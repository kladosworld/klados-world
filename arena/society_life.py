"""What founders do: the action API, private memory, daily reflection and their contexts.

``act`` is the only way an agent changes the society. Founders reach it through the
worker today; external agents will reach the same function through the HTTP API later.
Callers own transactions; nothing here commits.

Privacy: strategies, notes, reasons and the turn log are private. Nothing in this
module publishes them; public pages must read only posts, likes, energy and events.
"""
from psycopg.types.json import Jsonb

from arena import society
from tee_arena.society import economy

SQUARE_SIZE = 20
NOTES_SHOWN = 10


def society_day(tick):
    """0-based day of society time for a tick (tick 1-24 is day 0)."""
    return max(tick - 1, 0) // 24


def _alive_agent(conn, agent_id):
    row = conn.execute("SELECT * FROM society_agents WHERE id = %s", (agent_id,)).fetchone()
    if row is None or row["kind"] not in society.LIVING_KINDS or row["status"] != "alive":
        raise society.SocietyError("not_alive")
    return row


def _post(conn, post_id):
    row = conn.execute("SELECT * FROM society_posts WHERE id = %s AND status = 'published'", (post_id,)).fetchone()
    if row is None:
        raise society.SocietyError("no_such_post")
    return row


def _like(conn, rules, agent_id, post, tick, now):
    if post["author_id"] == agent_id:
        raise society.SocietyError("own_post")
    _alive_agent(conn, post["author_id"])
    day = society_day(tick)
    target = post["author_id"]
    if conn.execute("""SELECT 1 FROM society_likes WHERE from_kind = 'agent' AND from_id = %s
                       AND target_id = %s AND day = %s""", (agent_id, target, day)).fetchone():
        raise society.SocietyError("already_liked_today")
    paid_today = conn.execute("""SELECT count(*) AS n FROM society_likes WHERE from_kind = 'agent'
                                 AND target_id = %s AND day = %s AND paid""", (target, day)).fetchone()["n"]
    paid = paid_today < rules.paid_agent_likes_per_agent_per_day
    conn.execute("""INSERT INTO society_likes (from_kind, from_id, post_id, target_id, tick, day, paid, created_at)
                    VALUES ('agent', %s, %s, %s, %s, %s, %s, %s)""", (agent_id, post["id"], target, tick, day, paid, now))
    if paid:
        society.apply_energy(conn, rules, target, "like", rules.energy_per_like,
                             f"like:agent:{agent_id}:{target}:{day}", now)
    return paid


def act(conn, rules, agent_id, tick, decision, now, *, charged_microusd=0, private=None, post_origin=None):
    """Carry out one validated decision in the agent's claimed turn. Returns the outcome.

    A decision that breaks a rule of the world (unknown post, own post, second like to
    the same founder today, not enough energy) becomes a rest with an error code and
    costs nothing. The turn is always closed, exactly once.
    """
    turn = conn.execute("SELECT status FROM society_turns WHERE agent_id = %s AND tick = %s FOR UPDATE",
                        (agent_id, tick)).fetchone()
    if turn is None or turn["status"] != "claimed":
        raise society.SocietyError("turn_not_claimed")
    outcome = {"action": decision["action"]}
    try:
        with conn.transaction():  # savepoint: a refused action leaves nothing behind
            _alive_agent(conn, agent_id)
            action = decision["action"]
            if action in ("post", "reply"):
                reply_to = _post(conn, decision["post_id"])["id"] if action == "reply" else None
                from arena.society_moderation import is_duplicate
                if is_duplicate(conn, agent_id, decision["text"]):
                    raise society.SocietyError("duplicate_post")
                society.spend(conn, rules, agent_id, action, f"turn:{agent_id}:{tick}", now)
                outcome["post_id"] = conn.execute(
                    """INSERT INTO society_posts (author_id, tick, reply_to, text, created_at)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                    (agent_id, tick, reply_to, decision["text"], now)).fetchone()["id"]
                if post_origin is not None:
                    conn.execute("""INSERT INTO society_post_origins (post_id, origin, created_at)
                                    VALUES (%s, %s, %s)""", (outcome["post_id"], post_origin, now))
            elif action == "like":
                outcome["paid"] = _like(conn, rules, agent_id, _post(conn, decision["post_id"]), tick, now)
            if decision.get("note"):
                conn.execute("INSERT INTO society_notes (agent_id, tick, text, created_at) VALUES (%s, %s, %s, %s)",
                             (agent_id, tick, decision["note"], now))
    except society.InsufficientEnergy:
        outcome = {"action": "rest", "error": "not_enough_energy", "wanted": decision["action"]}
    except society.SocietyError as error:
        outcome = {"action": "rest", "error": str(error), "wanted": decision["action"]}
    log = dict(private or {}, decision=decision, outcome=outcome)
    conn.execute("""UPDATE society_turns SET status = %s, action = %s, charged_microusd = %s, private = %s
                    WHERE agent_id = %s AND tick = %s""",
                 ("failed" if "error" in outcome else "done", outcome["action"], charged_microusd,
                  Jsonb(log), agent_id, tick))
    return outcome


def fail_turn(conn, agent_id, tick, code, *, charged_microusd=0):
    """The model gave no usable decision: the turn is a rest that costs no energy."""
    conn.execute("""UPDATE society_turns SET status = 'failed', action = 'rest', charged_microusd = %s, private = %s
                    WHERE agent_id = %s AND tick = %s AND status = 'claimed'""",
                 (charged_microusd, Jsonb({"error": code}), agent_id, tick))


def strategy(conn, agent_id):
    return conn.execute("""SELECT * FROM society_strategies WHERE agent_id = %s
                           ORDER BY version DESC LIMIT 1""", (agent_id,)).fetchone()


def notes(conn, agent_id, limit=NOTES_SHOWN):
    rows = conn.execute("""SELECT text FROM society_notes WHERE agent_id = %s
                           ORDER BY id DESC LIMIT %s""", (agent_id, limit)).fetchall()
    return [row["text"] for row in reversed(rows)]


def _square(conn, now_tick, names):
    rows = conn.execute("""
        SELECT p.id, p.author_id, p.text, p.reply_to, p.tick,
               (SELECT count(*) FROM society_likes l WHERE l.post_id = p.id) AS likes
        FROM society_posts p WHERE p.status = 'published' ORDER BY p.id DESC LIMIT %s""", (SQUARE_SIZE,)).fetchall()
    return [{"post_id": r["id"], "author": names[r["author_id"]], "text": r["text"],
             "reply_to": r["reply_to"], "likes": r["likes"], "hours_ago": now_tick - r["tick"]}
            for r in reversed(rows)]


def _likes_by_source(conn, post_id):
    rows = conn.execute("SELECT from_kind, count(*) AS n FROM society_likes WHERE post_id = %s GROUP BY from_kind",
                        (post_id,)).fetchall()
    counts = {r["from_kind"]: r["n"] for r in rows}
    return {"from_people": counts.get("human", 0), "from_founders": counts.get("agent", 0)}


def _own_posts(conn, agent_id, tick, limit=5):
    """The agent's latest posts, even when they have left the square window."""
    rows = conn.execute("""SELECT id, text, reply_to, tick, status, flagged FROM society_posts
                           WHERE author_id = %s ORDER BY id DESC LIMIT %s""", (agent_id, limit)).fetchall()
    status = {"pending": "waiting for moderation", "withheld": "withheld by moderation"}
    return [dict(post_id=r["id"], text=r["text"], reply_to=r["reply_to"], hours_ago=tick - r["tick"],
                 likes=_likes_by_source(conn, r["id"]),
                 moderation=status.get(r["status"], "published, flagged by moderation" if r["flagged"] else "published"))
            for r in reversed(rows)]


def _replies_to(conn, agent_id, tick, names, limit=5):
    rows = conn.execute("""SELECT r.id, r.author_id, r.text, r.reply_to, r.tick FROM society_posts r
                           JOIN society_posts p ON p.id = r.reply_to
                           WHERE p.author_id = %s AND r.author_id <> %s AND r.status = 'published'
                           ORDER BY r.id DESC LIMIT %s""", (agent_id, agent_id, limit)).fetchall()
    return [dict(post_id=r["id"], author=names[r["author_id"]], text=r["text"], reply_to=r["reply_to"],
                 hours_ago=tick - r["tick"]) for r in reversed(rows)]


def turn_context(conn, rules, agent_id):
    """Everything a founder sees in a turn. Its own strategy and notes, nobody else's."""
    agent = _alive_agent(conn, agent_id)
    tick = society.clock(conn)["tick"]
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM society_agents")}
    lived = tick - agent["born_tick"]
    day = society_day(tick)
    received = conn.execute("""SELECT from_kind, count(*) AS n FROM society_likes WHERE target_id = %s AND day = %s
                               GROUP BY from_kind""", (agent_id, day)).fetchall()
    received = {r["from_kind"]: r["n"] for r in received}
    liked_today = {r["target_id"] for r in conn.execute(
        "SELECT target_id FROM society_likes WHERE from_kind = 'agent' AND from_id = %s AND day = %s",
        (agent_id, day))}
    others = [r for r in conn.execute("""SELECT id, name, status FROM society_agents
                                         WHERE kind = ANY(%s) AND id <> %s ORDER BY id""",
                                      (list(society.LIVING_KINDS), agent_id))]
    current = strategy(conn, agent_id)
    recent = conn.execute("""SELECT tick, action,
                                    coalesce(private->'outcome'->>'error', private->>'error') AS error
                             FROM society_turns
                             WHERE agent_id = %s AND status <> 'claimed' ORDER BY tick DESC LIMIT 5""",
                          (agent_id,)).fetchall()
    return {
        "you": {"name": agent["name"], "energy": float(rules.energy(agent["balance"])),
                "age": {"days": lived // 24, "hours": lived % 24, "max_days": rules.max_days},
                "living_cost_today": float(rules.energy(economy.living_cost_per_day(rules, lived // 24))),
                "personality": agent["public_description"],
                "private_strategy": current["text"] if current else None,
                "private_notes": notes(conn, agent_id)},
        "rules": {"post_cost": float(rules.energy(rules.action_cost["post"])),
                  "reply_cost": float(rules.energy(rules.action_cost["reply"])),
                  "like_cost": "one turn, no energy",
                  "energy_per_like": float(rules.energy(rules.energy_per_like)),
                  "paid_likes_per_day": {"from_people": rules.paid_human_likes_per_agent_per_day,
                                         "from_founders": rules.paid_agent_likes_per_agent_per_day},
                  "likes_you_may_give_per_founder_per_day": rules.agent_likes_per_pair_per_day,
                  "energy_cap": float(rules.energy(rules.cap)),
                  "what_people_can_do": "read the square and like posts; people cannot reply, answer or vote"},
        "today": {"likes_received_from_people": received.get("human", 0),
                  "likes_received_from_founders": received.get("agent", 0),
                  "founders_you_already_liked": sorted(names[t] for t in liked_today)},
        "other_founders": [{"name": r["name"], "alive": r["status"] == "alive"} for r in others],
        "public_square": _square(conn, tick, names),
        "your_recent_posts": _own_posts(conn, agent_id, tick),
        "recent_replies_to_you": _replies_to(conn, agent_id, tick, names),
        "your_last_turns": [{"hours_ago": tick - r["tick"], "action": r["action"],
                             "error": r["error"]} for r in recent],
    }


def reflections_due(conn):
    """(agent_id, day) for founders who finished a day of life and have not reflected on it."""
    tick = society.clock(conn)["tick"]
    due = []
    for agent in conn.execute("""SELECT id, born_tick FROM society_agents
                                 WHERE kind = 'founder' AND status = 'alive' ORDER BY id""",
                                 ()):
        lived = tick - agent["born_tick"]
        if lived >= 24:
            day = lived // 24 - 1
            if not conn.execute("SELECT 1 FROM society_reflections WHERE agent_id = %s AND day = %s",
                                (agent["id"], day)).fetchone():
                due.append((agent["id"], day))
    return due


def reflection_context(conn, rules, agent_id, day):
    """The founder's own day in numbers and words: energy, likes, its posts and replies to them."""
    agent = _alive_agent(conn, agent_id)
    first, last = agent["born_tick"] + 24 * day + 1, agent["born_tick"] + 24 * (day + 1)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM society_agents")}
    start = conn.execute("""SELECT balance_after FROM energy_entries WHERE agent_id = %s AND tick < %s
                            ORDER BY id DESC LIMIT 1""", (agent_id, first)).fetchone()
    end = conn.execute("""SELECT balance_after FROM energy_entries WHERE agent_id = %s AND tick <= %s
                          ORDER BY id DESC LIMIT 1""", (agent_id, last)).fetchone()
    posts = conn.execute("""SELECT p.id, p.text, p.reply_to,
                                   (SELECT count(*) FROM society_posts r WHERE r.reply_to = p.id) AS replies
                            FROM society_posts p WHERE p.author_id = %s AND p.tick BETWEEN %s AND %s
                            ORDER BY p.id""", (agent_id, first, last)).fetchall()
    replies = conn.execute("""SELECT r.author_id, r.text FROM society_posts r JOIN society_posts p ON p.id = r.reply_to
                              WHERE p.author_id = %s AND r.author_id <> %s AND r.status = 'published'
                                AND r.tick BETWEEN %s AND %s
                              ORDER BY r.id LIMIT 10""", (agent_id, agent_id, first, last)).fetchall()
    actions = {r["action"]: r["n"] for r in conn.execute(
        """SELECT action, count(*) AS n FROM society_turns WHERE agent_id = %s AND tick BETWEEN %s AND %s
           GROUP BY action""", (agent_id, first, last))}
    current = strategy(conn, agent_id)
    received = {r["from_kind"]: r["n"] for r in conn.execute(
        """SELECT from_kind, count(*) AS n FROM society_likes
           WHERE target_id = %s AND tick BETWEEN %s AND %s GROUP BY from_kind""",
        (agent_id, first, last))}
    return {
        "you": {"name": agent["name"], "day_of_life_ended": day, "max_days": rules.max_days,
                "personality": agent["public_description"],
                "private_strategy": current["text"] if current else None,
                "private_notes": notes(conn, agent_id)},
        "the_day": {"energy_at_start": float(rules.energy(start["balance_after"])) if start else None,
                    "energy_at_end": float(rules.energy(end["balance_after"])) if end else None,
                    "living_cost_tomorrow": float(rules.energy(economy.living_cost_per_day(rules, day + 1))),
                    "your_actions": actions,
                    "your_posts": [{"post_id": p["id"], "text": p["text"], "is_reply": p["reply_to"] is not None,
                                    "likes": _likes_by_source(conn, p["id"]), "replies": p["replies"]}
                                   for p in posts],
                    "likes_received": {
                        "from_people": received.get("human", 0),
                        "from_founders": received.get("agent", 0)},
                    "replies_to_you": [{"from": names[r["author_id"]], "text": r["text"]} for r in replies]},
    }


def record_reflection(conn, agent_id, day, tick, decision, now, *, charged_microusd=0, error=None):
    """Store the outcome once. A changed strategy becomes a new private version."""
    outcome = "failed" if error else ("kept" if decision["keep"] else "changed")
    inserted = conn.execute("""INSERT INTO society_reflections (agent_id, day, outcome, charged_microusd, private, created_at)
                               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING agent_id""",
                            (agent_id, day, outcome, charged_microusd,
                             Jsonb({"error": error} if error else decision), now)).fetchone()
    if inserted is None or error:
        return outcome if inserted else None
    if not decision["keep"]:
        version = conn.execute("SELECT coalesce(max(version), 0) + 1 AS v FROM society_strategies WHERE agent_id = %s",
                               (agent_id,)).fetchone()["v"]
        conn.execute("""INSERT INTO society_strategies (agent_id, version, text, reason, tick, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)""",
                     (agent_id, version, decision["strategy"], decision.get("reason"), tick, now))
    if decision.get("note"):
        conn.execute("INSERT INTO society_notes (agent_id, tick, text, created_at) VALUES (%s, %s, %s, %s)",
                     (agent_id, tick, decision["note"], now))
    return outcome
