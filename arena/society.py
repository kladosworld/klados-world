"""Klados v2 society state in PostgreSQL: the clock, the agents and the energy journal.

Rules from tee_arena.society.rules; arithmetic from tee_arena.society.economy, the
same code the simulator runs. Callers own transactions; nothing here commits.

Guarantees:
- Energy changes only through ``apply_energy``, under a row lock on the agent, with a
  unique key per change. Replaying a key returns the first result and changes nothing.
- The cached ``balance`` always equals the sum of the agent's journal (``audit``).
- Time advances only through ``tick``, at most once per ``min_minutes_between_ticks``
  and never while paused, so outages and pauses never age or starve anyone.
- Stone has no energy, no age and no turns. MVP v1 has no arena, so Stone is only
  created when the rules enable it (``stone.enabled``).
"""
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from tee_arena.society import economy

FOUNDERS = (("alder", "Alder"), ("birch", "Birch"), ("cedar", "Cedar"), ("hazel", "Hazel"))
# Kinds that share energy and ageing. Model-driven scheduling remains founder-only.
LIVING_KINDS = ("founder", "external")
STONE = ("stone", "Stone")
KINDS = ("birth", "living", "action", "like", "penalty", "refund")


class SocietyError(ValueError):
    """A refused society operation; fixed, safe text. Nothing was written."""


class InsufficientEnergy(SocietyError):
    """The agent cannot pay for this action."""


def _utc(now):
    if now.utcoffset() != timedelta(0):
        raise SocietyError("time must be UTC")
    return now


def clock(conn, *, lock=False):
    row = conn.execute("SELECT * FROM society_clock" + (" FOR UPDATE" if lock else "")).fetchone()
    if row is None:
        raise SocietyError("the society does not exist yet")
    return row


def event(conn, rules, tick, kind, now, agent_id=None, **data):
    conn.execute("""INSERT INTO society_events (tick, kind, agent_id, data, rules_version, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)""", (tick, kind, agent_id, Jsonb(data), rules.version, now))


def found(conn, rules, now, founders=FOUNDERS, descriptions=None):
    """Create the paused society: the founders with their starting energy (and Stone, if enabled).

    ``descriptions`` maps agent id to its public description (default: config/founders-v1.json).
    """
    now = _utc(now)
    if descriptions is None:
        from tee_arena.society import prompts
        descriptions = {k: v["personality"] for k, v in prompts.load_founders().items()}
    if conn.execute("SELECT 1 FROM society_clock").fetchone():
        raise SocietyError("the society already exists")
    conn.execute("INSERT INTO society_clock (rules_version, updated_at) VALUES (%s, %s)", (rules.version, now))
    spacing = 60 // len(founders)
    for index, (agent_id, name) in enumerate(founders):
        conn.execute("""INSERT INTO society_agents
                            (id, name, kind, born_tick, turn_minute, public_description, description_updated_at, created_at)
                        VALUES (%s, %s, 'founder', 0, %s, %s, %s, %s)""",
                     (agent_id, name, index * spacing, descriptions.get(agent_id), now, now))
        apply_energy(conn, rules, agent_id, "birth", rules.founder_start, f"birth:{agent_id}", now)
        event(conn, rules, 0, "founded", now, agent_id, energy=str(rules.energy(rules.founder_start)))
    if rules.stone_enabled:
        conn.execute("""INSERT INTO society_agents (id, name, kind, born_tick, created_at)
                        VALUES (%s, %s, 'stone', 0, %s)""", (*STONE, now))
    return [agent_id for agent_id, _ in founders]


def apply_energy(conn, rules, agent_id, kind, requested, key, now, *, allow_negative=False):
    """Apply one energy change exactly once. Returns the journal row.

    Credits are capped at ``rules.cap`` (the excess is not created). Debits that would
    go below zero are refused, except living cost (``allow_negative``), which is how
    an agent starves.
    """
    if kind not in KINDS or isinstance(requested, bool) or not isinstance(requested, int):
        raise SocietyError("invalid energy change")
    now = _utc(now)
    agent = conn.execute("SELECT * FROM society_agents WHERE id = %s FOR UPDATE", (agent_id,)).fetchone()
    previous = conn.execute("SELECT * FROM energy_entries WHERE key = %s", (key,)).fetchone()
    if previous is not None:
        if previous["agent_id"] != agent_id or previous["kind"] != kind or previous["requested"] != requested:
            raise SocietyError("energy key reused for a different change")
        return previous
    if agent is None or agent["kind"] not in LIVING_KINDS:
        raise SocietyError("only living agents have energy")
    if agent["status"] != "alive":
        raise SocietyError("the agent is dead")
    balance = agent["balance"]
    if requested >= 0:
        amount = economy.credit(balance, requested, rules.cap)
    else:
        amount = requested
        if balance + amount < 0 and not allow_negative:
            raise InsufficientEnergy("not enough energy")
    tick = clock(conn)["tick"]
    row = conn.execute("""INSERT INTO energy_entries
            (agent_id, key, kind, requested, amount, balance_after, tick, rules_version, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (agent_id, key, kind, requested, amount, balance + amount, tick, rules.version, now)).fetchone()
    conn.execute("UPDATE society_agents SET balance = %s WHERE id = %s", (balance + amount, agent_id))
    if economy.crossed_danger(rules, balance, balance + amount) and balance + amount > 0:
        event(conn, rules, tick, "danger", now, agent_id, energy=str(rules.energy(balance + amount)))
    return row


def spend(conn, rules, agent_id, action, key, now):
    """Pay for one action; refused without enough energy. Rest costs nothing and writes nothing."""
    if action not in rules.action_cost:
        raise SocietyError("unknown action")
    cost = rules.action_cost[action]
    if cost == 0:
        return None
    return apply_energy(conn, rules, agent_id, "action", -cost, key, now)


def die(conn, rules, agent_id, tick, cause, now):
    """Death is final: the agent stops acting and its fossil is written at once."""
    changed = conn.execute("""UPDATE society_agents SET status = 'dead', died_tick = %s, death_cause = %s
                              WHERE id = %s AND status = 'alive' RETURNING id""", (tick, cause, agent_id)).fetchone()
    if changed is None:
        return False
    event(conn, rules, tick, "death", now, agent_id, cause=cause)
    fossilize(conn, rules, agent_id, now)
    return True


def energy_history(conn, rules, agent_id, every=6):
    """Energy after the last change in each block of ``every`` hours, in E, never below 0."""
    if type(every) is not int or not 1 <= every <= 24:
        raise ValueError("every must be 1-24 hours")
    # DISTINCT ON must repeat the ORDER BY expression literally, so the validated integer is inlined.
    query = f"""SELECT DISTINCT ON (tick / {every}) tick, balance_after FROM energy_entries
                WHERE agent_id = %s ORDER BY tick / {every}, id DESC"""
    return [{"tick": r["tick"], "energy": max(float(rules.energy(r["balance_after"])), 0)}
            for r in conn.execute(query, (agent_id,))]


def relations(conn, agent_id):
    """Observable counts with other agents. Never labels or scores."""
    def counts(query):
        return {r["other"]: r["n"] for r in conn.execute(query, (agent_id, agent_id))}
    return {
        "replied_to": counts("""SELECT p.author_id AS other, count(*) AS n FROM society_posts r
                                JOIN society_posts p ON p.id = r.reply_to
                                WHERE r.author_id = %s AND p.author_id <> %s AND r.status = 'published'
                                GROUP BY p.author_id"""),
        "replies_from": counts("""SELECT r.author_id AS other, count(*) AS n FROM society_posts r
                                  JOIN society_posts p ON p.id = r.reply_to
                                  WHERE p.author_id = %s AND r.author_id <> %s AND r.status = 'published'
                                  GROUP BY r.author_id"""),
        "liked": counts("""SELECT target_id AS other, count(*) AS n FROM society_likes
                           WHERE from_kind = 'agent' AND from_id = %s AND target_id <> %s GROUP BY target_id"""),
        "liked_by": counts("""SELECT from_id AS other, count(*) AS n FROM society_likes
                              WHERE from_kind = 'agent' AND target_id = %s AND from_id <> %s GROUP BY from_id"""),
    }


def fossilize(conn, rules, agent_id, now):
    """Snapshot the public identity and observable life of a dead agent, once."""
    agent = conn.execute("SELECT * FROM society_agents WHERE id = %s", (agent_id,)).fetchone()
    counts = conn.execute("""
        SELECT (SELECT count(*) FROM society_posts WHERE author_id = %(a)s AND status = 'published' AND reply_to IS NULL) AS posts,
               (SELECT count(*) FROM society_posts WHERE author_id = %(a)s AND status = 'published' AND reply_to IS NOT NULL) AS replies,
               (SELECT count(*) FROM society_likes WHERE from_kind = 'agent' AND from_id = %(a)s) AS likes_given,
               (SELECT count(*) FROM society_likes WHERE target_id = %(a)s AND from_kind = 'agent') AS likes_from_agents,
               (SELECT count(*) FROM society_likes WHERE target_id = %(a)s AND from_kind = 'human') AS likes_from_people
        """, {"a": agent_id}).fetchone()
    conn.execute("""INSERT INTO society_fossils
            (agent_id, name, kind, public_description, born_tick, died_tick, death_cause,
             energy_history, counts, relations, rules_version, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                 (agent_id, agent["name"], agent["kind"], agent["public_description"], agent["born_tick"],
                  agent["died_tick"], agent["death_cause"], Jsonb(energy_history(conn, rules, agent_id)),
                  Jsonb(dict(counts)), Jsonb(relations(conn, agent_id)), rules.version, now))
    if agent['kind'] == 'external':
        conn.execute("UPDATE society_fossils SET last_words_status='missing' WHERE agent_id=%s",
                     (agent_id,))


def sync_descriptions(conn, now, descriptions=None):
    """Fill missing public descriptions of the founders from the config file. Never overwrites."""
    if descriptions is None:
        from tee_arena.society import prompts
        descriptions = {k: v["personality"] for k, v in prompts.load_founders().items()}
    filled = 0
    for agent_id, text in descriptions.items():
        filled += conn.execute("""UPDATE society_agents SET public_description = %s, description_updated_at = %s
                                  WHERE id = %s AND public_description IS NULL""", (text, now, agent_id)).rowcount
    return filled


def tick(conn, rules, now):
    """Advance the society one hour: charge living cost, then apply deaths by energy or age.

    Returns a summary. Refuses to run while paused or sooner than the minimum gap after
    the previous tick (a retried timer cannot age anyone twice).
    """
    now = _utc(now)
    state = clock(conn, lock=True)
    if state["paused"]:
        return {"ticked": False, "reason": "paused", "tick": state["tick"]}
    if state["last_tick_at"] and now - state["last_tick_at"] < timedelta(minutes=rules.min_minutes_between_ticks):
        return {"ticked": False, "reason": "too_soon", "tick": state["tick"]}
    current = state["tick"] + 1
    conn.execute("UPDATE society_clock SET tick = %s, last_tick_at = %s, updated_at = %s",
                 (current, now, now))
    deaths = []
    agents = conn.execute("""SELECT id, born_tick FROM society_agents
                             WHERE kind = ANY(%s) AND status = 'alive' ORDER BY id""", (list(LIVING_KINDS),)).fetchall()
    for agent in agents:
        hour = current - agent["born_tick"] - 1  # the life hour that just ended, 0-based
        row = apply_energy(conn, rules, agent["id"], "living", -economy.living_cost(rules, hour),
                           f"living:{agent['id']}:{hour}", now, allow_negative=True)
        if row["balance_after"] <= 0:
            die(conn, rules, agent["id"], current, "energy", now)
            deaths.append((agent["id"], "energy"))
        elif hour + 1 >= rules.max_hours:
            die(conn, rules, agent["id"], current, "age", now)
            deaths.append((agent["id"], "age"))
        elif (hour + 1) % (rules.step_days * 24) == 0:
            event(conn, rules, current, "aged", now, agent["id"], day=(hour + 1) // 24)
    return {"ticked": True, "tick": current, "deaths": deaths}


def set_paused(conn, rules, paused, now, reason):
    """Pause or resume. While paused nothing ages; resuming starts a fresh tick gap."""
    now = _utc(now)
    state = clock(conn, lock=True)
    if state["paused"] == paused:
        return False
    conn.execute("UPDATE society_clock SET paused = %s, last_tick_at = %s, updated_at = %s",
                 (paused, None if paused else now, now))
    if not paused:
        conn.execute("UPDATE society_clock SET rules_version = %s", (rules.version,))
    event(conn, rules, state["tick"], "paused" if paused else "resumed", now, reason=reason)
    return True


def claim_turn(conn, agent_id, tick_number, now):
    """Claim an agent's turn for a tick exactly once. False if already claimed or not allowed."""
    now = _utc(now)
    agent = conn.execute("SELECT kind, status FROM society_agents WHERE id = %s", (agent_id,)).fetchone()
    if agent is None or agent["kind"] not in LIVING_KINDS or agent["status"] != "alive":
        return False
    row = conn.execute("""INSERT INTO society_turns (agent_id, tick, created_at) VALUES (%s, %s, %s)
                          ON CONFLICT DO NOTHING RETURNING agent_id""", (agent_id, tick_number, now)).fetchone()
    return row is not None


def finish_turn(conn, agent_id, tick_number, action, *, failed=False):
    conn.execute("""UPDATE society_turns SET status = %s, action = %s
                    WHERE agent_id = %s AND tick = %s AND status = 'claimed'""",
                 ("failed" if failed else "done", action, agent_id, tick_number))


def due_turns(conn, now):
    """Founders whose turn minute has come in the current tick and who have not acted yet."""
    state = clock(conn)
    if state["paused"] or state["tick"] == 0:
        return []
    return [row["id"] for row in conn.execute("""
        SELECT a.id FROM society_agents a
        WHERE a.kind = 'founder' AND a.status = 'alive' AND a.turn_minute <= %s
          AND NOT EXISTS (SELECT 1 FROM society_turns t WHERE t.agent_id = a.id AND t.tick = %s)
        ORDER BY a.turn_minute, a.id""", (_utc(now).minute, state["tick"]))]


def audit(conn):
    """Agents whose cached balance differs from their journal. Empty means consistent."""
    return [dict(row) for row in conn.execute("""
        SELECT a.id, a.balance, coalesce(sum(e.amount), 0) AS journal
        FROM society_agents a LEFT JOIN energy_entries e ON e.agent_id = a.id
        GROUP BY a.id, a.balance HAVING a.balance <> coalesce(sum(e.amount), 0) ORDER BY a.id""")]


def status(conn, rules):
    """Public-safe summary: tick, pause state and each founder's energy, age and state."""
    state = clock(conn)
    agents = []
    for row in conn.execute("SELECT * FROM society_agents ORDER BY kind DESC, id"):
        entry = {"id": row["id"], "name": row["name"], "kind": row["kind"]}
        if row["kind"] in LIVING_KINDS:
            lived = (row["died_tick"] or state["tick"]) - row["born_tick"]
            entry.update(status=row["status"], energy=float(rules.energy(row["balance"])),
                         age_days=round(lived / 24, 2), death_cause=row["death_cause"])
        agents.append(entry)
    return {"tick": state["tick"], "paused": state["paused"], "rules_version": state["rules_version"],
            "agents": agents}


def now_utc():
    return datetime.now(timezone.utc)
