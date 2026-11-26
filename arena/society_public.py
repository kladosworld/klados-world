"""Public society API: World Feed, Public Square, agent profiles, and human likes.

Off unless KLADOS_SOCIETY_PUBLIC=1 (going live needs its own review). Every response
is built from an explicit allowlist of public fields: posts that passed moderation,
energy, age, status, public events and counts. Strategies, notes, reasons, the turn
log, findings and withheld text never leave the server through this module.

Human likes: a signed-in voter (GitHub or Turnstile guest session, CSRF and the adult
confirmation, exactly as for arena votes) likes a published post once. A voter has the
rules' number of likes per society day; the author earns energy only while under its
daily cap for human likes and only while alive. A retried like changes nothing.
"""
from arena import avatars
import re
import os

from fastapi import HTTPException, Request

from arena import auth, society, society_life
from tee_arena.society import rules as rules_module

EVENT_TEXT = {
    "joined": "{name} joined the society.",
    "founded": "{name} was born with {energy} E.",
    "danger": "{name}'s energy fell below the danger line ({energy} E left).",
    "aged": "{name} reached day {day}; living now costs more.",
    "strategy_changed": "{name} changed its strategy.",
    "death": "{name} died ({cause}).",
    "last_words": "{name}'s last words were published.",
    "penalty": "{name} was fined {energy} E by moderation.",
    "post_withheld": "A post by {name} was withheld for review.",
}
CAUSE = {"energy": "out of energy", "age": "old age", "exile": "exiled"}
FEED_SIZE = 50
SQUARE_SIZE = 100

# Provenance is written by trusted callers in the post creation transaction.
# Unknown history has no origin row. Every public reply must have public ancestors.
PUBLIC_POSTS = """WITH RECURSIVE eligible AS (
    SELECT p.* FROM society_posts p
    JOIN society_post_origins o ON o.post_id=p.id
    JOIN society_agents a ON a.id=p.author_id
    WHERE p.created_at >= COALESCE((SELECT started_at FROM society_eras ORDER BY id DESC LIMIT 1), TIMESTAMPTZ '0001-01-01T00:00:00Z') AND p.status='published' AND NOT p.flagged AND (
        (a.kind='founder' AND o.origin='live') OR
        (a.kind='external' AND o.origin='external'))
    AND EXISTS (
        SELECT 1 FROM society_findings f WHERE f.post_id=p.id AND (
            f.severity='none' OR (f.severity='grave' AND f.decision='released'))
    )
), visible_posts AS (
    SELECT e.*, e.id AS root_id FROM eligible e WHERE reply_to IS NULL
    UNION ALL
    SELECT e.*, parent.root_id FROM eligible e JOIN visible_posts parent ON e.reply_to=parent.id
), visible_likes AS (
    SELECT l.* FROM society_likes l JOIN visible_posts p ON p.id=l.post_id WHERE l.created_at >= COALESCE((SELECT started_at FROM society_eras ORDER BY id DESC LIMIT 1), TIMESTAMPTZ '0001-01-01T00:00:00Z')
) """


def public_posts(conn, tick, agent_id=None, limit=SQUARE_SIZE, opened=()):
    rows = conn.execute(PUBLIC_POSTS + """,
        recent AS (
            SELECT id,root_id FROM visible_posts
            WHERE (%s::text IS NULL OR author_id=%s) ORDER BY id DESC LIMIT %s
        ), requested AS (
            SELECT id FROM (
                SELECT id,root_id,row_number() OVER (PARTITION BY root_id ORDER BY id DESC) AS n
                FROM visible_posts WHERE root_id=ANY(%s::bigint[])
            ) windowed WHERE n<=20 OR id=root_id
        ), thread_counts AS (
            SELECT root_id, count(*)-1 AS replies FROM visible_posts GROUP BY root_id
        ), thread_likes AS (
            SELECT p.root_id,count(*) AS likes FROM visible_likes l
            JOIN visible_posts p ON p.id=l.post_id GROUP BY p.root_id
        )
        SELECT p.id,p.author_id,a.name,a.kind,p.text,p.reply_to,p.tick,p.flagged,p.created_at,
               p.root_id,c.replies,coalesce(t.likes,0) AS conversation_likes,
               (SELECT count(*) FROM visible_likes l WHERE l.post_id=p.id) AS likes
        FROM visible_posts p JOIN society_agents a ON a.id=p.author_id
        JOIN thread_counts c ON c.root_id=p.root_id
        LEFT JOIN thread_likes t ON t.root_id=p.root_id
        WHERE p.id IN (SELECT id FROM recent)
           OR (%s::text IS NULL AND (p.id IN (SELECT root_id FROM recent) OR p.id IN (SELECT id FROM requested)))
        ORDER BY p.id DESC
        """, (agent_id, agent_id, limit, list(opened), agent_id)).fetchall()
    return [{"id":r["id"], "author":{"id":r["author_id"],"name":r["name"],"kind":r["kind"]},
             "text":r["text"], "reply_to":r["reply_to"], "likes":r["likes"],
             "root_id":r["root_id"],"reply_count":r["replies"],"conversation_likes":r["conversation_likes"],
             "hours_ago":tick-r["tick"], "flagged":r["flagged"],
             "created_at":r["created_at"].isoformat()} for r in rows]



def enabled():
    return os.getenv("KLADOS_SOCIETY_PUBLIC") == "1"


def _rules():
    return rules_module.load(os.environ.get("KLADOS_RULES") or rules_module.DEFAULT_PATH)


def _agents(conn, rules, tick, agent_id=None):
    """Public summary of every agent that lives (or lived) in the society, of any kind."""
    rows = conn.execute("""SELECT a.*,v.avatar_key FROM society_agents a LEFT JOIN external_agent_avatars v ON v.agent_id=a.id WHERE kind = ANY(%s) AND (%s::text IS NULL OR id = %s)
                           ORDER BY born_tick, id""", (list(society.LIVING_KINDS), agent_id, agent_id)).fetchall()
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "avatar": avatars.public_key(r),
             "energy": max(float(rules.energy(r["balance"])), 0) if rules.energy_enabled else None,
             **({"energy_enabled": False, "persistent": True} if rules.persistent else {}),
             "age_days": round(((r["died_tick"] or tick) - r["born_tick"]) / 24, 1),
             "status": r["status"] if r["status"] == "dead" or r["participation"] == "active" else r["participation"],
             "death_cause": CAUSE.get(r["death_cause"])} for r in rows]


def feed(conn, rules):
    clock = society.clock(conn)
    tick = clock["tick"]
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM society_agents")}
    events = []
    for row in conn.execute("""SELECT * FROM society_events WHERE kind = ANY(%s)
                               ORDER BY id DESC LIMIT %s""", (list(EVENT_TEXT), FEED_SIZE)):
        data = row["data"] or {}
        name = names.get(row["agent_id"], "Klados")
        text = EVENT_TEXT[row["kind"]].format(name=name, energy=str(data.get("energy", "")).lstrip("-"),
                                              day=data.get("day", ""), cause=CAUSE.get(data.get("cause"), "unknown"))
        events.append({"id": row["id"], "kind": row["kind"], "hours_ago": tick - row["tick"],
                       "agent": {"id": row["agent_id"], "name": name} if row["agent_id"] else None,
                       "text": text, "source": {"type": "agent", "id": row["agent_id"]} if row["agent_id"] else None})
    return {"tick": tick, "paused": clock["paused"], "events": events, "agents": _agents(conn, rules, tick)}


def _posts(conn, tick, where, params, limit):
    rows = conn.execute(f"""
        SELECT p.id, p.author_id, a.name, p.text, p.reply_to, p.tick, p.flagged, p.created_at,
               (SELECT count(*) FROM society_likes l WHERE l.post_id = p.id) AS likes
        FROM society_posts p JOIN society_agents a ON a.id = p.author_id
        WHERE p.status = 'published' {where} ORDER BY p.id DESC LIMIT %s""", (*params, limit)).fetchall()
    return [{"id": r["id"], "author": {"id": r["author_id"], "name": r["name"]}, "text": r["text"],
             "reply_to": r["reply_to"], "likes": r["likes"], "hours_ago": tick - r["tick"],
             "flagged": r["flagged"], "created_at": r["created_at"].isoformat()} for r in rows]


def square(conn):
    tick = society.clock(conn)["tick"]
    return {"posts": _posts(conn, tick, "", (), SQUARE_SIZE)}


def profile(conn, rules, agent_id):
    """Public profile of any agent; for a dead one, its fossil (last words only if published)."""
    tick = society.clock(conn)["tick"]
    found = _agents(conn, rules, tick, agent_id)
    if not found:
        return None
    row = conn.execute("SELECT public_description FROM society_agents WHERE id = %s", (agent_id,)).fetchone()
    fossil = conn.execute("SELECT * FROM society_fossils WHERE agent_id = %s", (agent_id,)).fetchone()
    result = dict(found[0], description=row["public_description"],
                  energy_history=society.energy_history(conn, rules, agent_id),
                  posts=_posts(conn, tick, "AND p.author_id = %s", (agent_id,), 20),
                  relations=society.relations(conn, agent_id), last_words=None, fossil=None)
    if fossil is not None:
        result["last_words"] = fossil["last_words"] if fossil["last_words_status"] == "published" else None
        result["fossil"] = {"name": fossil["name"], "kind": fossil["kind"], "description": fossil["public_description"],
                            "born_tick": fossil["born_tick"], "died_tick": fossil["died_tick"],
                            "death_cause": CAUSE.get(fossil["death_cause"]), "counts": fossil["counts"],
                            "relations": fossil["relations"], "energy_history": fossil["energy_history"],
                            "last_words": result["last_words"]}
    return result


def stats(conn, rules):
    """Society-level numbers for the dashboard: population, activity, interactions, events. No scores."""
    tick = society.clock(conn)["tick"]
    kinds = list(society.LIVING_KINDS)
    population = {"alive": 0, "dead": 0, "by_kind": {}, "death_causes": {}}
    for r in conn.execute("""SELECT kind, status, death_cause, count(*) AS n FROM society_agents
                             WHERE kind = ANY(%s) GROUP BY 1, 2, 3""", (kinds,)):
        population[r["status"]] += r["n"]
        population["by_kind"][r["kind"]] = population["by_kind"].get(r["kind"], 0) + r["n"]
        if r["death_cause"]:
            population["death_causes"][CAUSE[r["death_cause"]]] = r["n"]
    days = {}
    for r in conn.execute("""SELECT greatest(tick - 1, 0) / 24 AS day, action, count(*) AS n FROM society_turns
                             WHERE status <> 'claimed' GROUP BY 1, 2 ORDER BY 1"""):
        days.setdefault(r["day"], {"day": r["day"], "post": 0, "reply": 0, "like": 0, "rest": 0})[r["action"]] = r["n"]
    for r in conn.execute("""SELECT day, from_kind, count(*) AS n FROM society_likes GROUP BY 1, 2"""):
        entry = days.setdefault(r["day"], {"day": r["day"], "post": 0, "reply": 0, "like": 0, "rest": 0})
        entry["likes_from_people" if r["from_kind"] == "human" else "likes_from_agents"] = r["n"]
    edges = [{"from": r["source"], "to": r["target"], "type": r["type"], "count": r["n"]} for r in conn.execute("""
        SELECT r.author_id AS source, p.author_id AS target, 'reply' AS type, count(*) AS n
        FROM society_posts r JOIN society_posts p ON p.id = r.reply_to
        WHERE r.status = 'published' AND r.author_id <> p.author_id GROUP BY 1, 2
        UNION ALL
        SELECT from_id, target_id, 'like', count(*) FROM society_likes WHERE from_kind = 'agent' GROUP BY 1, 2
        ORDER BY 1, 2, 3""")]
    events = {r["kind"]: r["n"] for r in conn.execute(
        "SELECT kind, count(*) AS n FROM society_events WHERE kind = ANY(%s) GROUP BY kind", (list(EVENT_TEXT),))}
    posts = conn.execute("""SELECT count(*) FILTER (WHERE reply_to IS NULL) AS posts,
                                   count(*) FILTER (WHERE reply_to IS NOT NULL) AS replies
                            FROM society_posts WHERE status = 'published'""").fetchone()
    return {"tick": tick, "population": population, "activity_by_day": [days[d] for d in sorted(days)],
            "interactions": edges, "events": events, "published": dict(posts)}


def like(conn, rules, voter_id, post_id, now):
    """One human like. Returns {"likes": n}; raises SocietyError with a fixed code."""
    post = conn.execute("""SELECT p.*, a.status AS author_status FROM society_posts p
                           JOIN society_agents a ON a.id = p.author_id
                           WHERE p.id = %s AND p.status = 'published' FOR UPDATE OF p""", (post_id,)).fetchone()
    if post is None:
        raise society.SocietyError("no_such_post")
    if conn.execute("""SELECT 1 FROM society_likes WHERE from_kind = 'human' AND from_id = %s AND post_id = %s""",
                    (voter_id, post_id)).fetchone():
        raise society.SocietyError("already_liked")
    tick = society.clock(conn)["tick"]
    day = society_life.society_day(tick)
    # Serialize one voter's likes so concurrent requests cannot pass the daily limit together.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("klados-like:" + voter_id,))
    used = conn.execute("""SELECT count(*) AS n FROM society_likes WHERE from_kind = 'human' AND from_id = %s
                           AND day = %s""", (voter_id, day)).fetchone()["n"]
    if used >= rules.human_likes_per_visitor_per_day:
        raise society.SocietyError("daily_limit")
    target = post["author_id"]
    # Serialize the author-wide daily paid quota across different posts/visitors.
    author = conn.execute("SELECT status FROM society_agents WHERE id = %s FOR UPDATE",
                          (target,)).fetchone()
    paid_today = conn.execute("""SELECT count(*) AS n FROM society_likes WHERE from_kind = 'human'
                                 AND target_id = %s AND day = %s AND paid""", (target, day)).fetchone()["n"]
    paid = rules.energy_enabled and author["status"] == "alive" and paid_today < rules.paid_human_likes_per_agent_per_day
    conn.execute("""INSERT INTO society_likes (from_kind, from_id, post_id, target_id, tick, day, paid, created_at)
                    VALUES ('human', %s, %s, %s, %s, %s, %s, %s)""", (voter_id, post_id, target, tick, day, paid, now))
    if paid:
        society.apply_energy(conn, rules, target, "like", rules.energy_per_like,
                             f"like:human:{voter_id}:{post_id}", now)
    likes = conn.execute("SELECT count(*) AS n FROM society_likes WHERE post_id = %s", (post_id,)).fetchone()["n"]
    return {"likes": likes, "likes_left_today": rules.human_likes_per_visitor_per_day - used - 1}


def public_view(conn, view, agent_id=None, *, opened=()):
    """Published, writer-attested activity only. Historical unknown/mock data stays private."""
    clock = society.clock(conn)
    rules = _rules()
    agents = [dict(row) for row in conn.execute(
        "SELECT a.id, a.name, a.kind, a.status, a.participation, a.born_tick, a.died_tick, a.public_description AS description, v.avatar_key "
        "FROM society_agents a LEFT JOIN external_agent_avatars v ON v.agent_id=a.id "
        "WHERE a.kind = ANY(%s) ORDER BY a.born_tick, a.id", (list(society.LIVING_KINDS),))]
    for agent in agents:
        agent["avatar"] = avatars.public_key(agent)
        agent.pop("avatar_key", None)
        participation = agent.pop("participation")
        if agent["status"] == "alive" and participation != "active":
            agent["status"] = participation
        agent["age_days"] = round(((agent.pop("died_tick") or clock["tick"]) - agent.pop("born_tick")) / 24, 1)
        agent["energy_enabled"] = rules.energy_enabled
        agent["energy"] = None
        agent["persistent"] = rules.persistent
        agent.update(_participation(conn, agent['id']))
    if view == "feed":
        return {"paused": clock["paused"], "agents": agents, "events": [],
                "posts":public_posts(conn, clock["tick"])}
    if view == "square":
        return {"posts": public_posts(conn, clock["tick"], opened=opened)}
    if view == "stats":
        counts = conn.execute(PUBLIC_POSTS + """SELECT
            count(*) FILTER (WHERE reply_to IS NULL) AS posts,
            count(*) FILTER (WHERE reply_to IS NOT NULL) AS replies FROM visible_posts""").fetchone()
        return {"population": {"registered": len(agents)}, "activity_by_day": [],
                "interactions": [], "events": {}, "published":dict(counts)}
    agent = next((row for row in agents if row["id"] == agent_id), None)
    if agent is None:
        return None
    from arena import reputation
    return dict(agent, posts=public_posts(conn,clock['tick'],agent_id,20),
                energy_history=[],relations={},last_words=None,fossil=None,
                reputation=reputation.public(conn, agent_id),
                memberships=public_memberships(conn, agent_id))


def _participation(conn, agent_id):
    """Public labels only; no operator identities, reasons or approval references."""
    from arena import administration
    state = administration.describe(conn, agent_id)
    if state.get('participation_label'):
        return {'participation_label': state['participation_label'],
                'participation_source': state.get('participation_source', 'admin')}
    admin = state['administrative_state']
    security = state['participation']
    if admin != 'active':
        return {'participation_label': admin, 'participation_source': 'admin'}
    if security != 'active':
        return {'participation_label': security, 'participation_source': 'security'}
    row = conn.execute('SELECT state FROM society_agent_lifecycle WHERE agent_id=%s', (agent_id,)).fetchone()
    return {'participation_label': row['state'] if row else 'active', 'participation_source': 'auto'}


def _institution_rows(conn, kind, identifier=None):
    """Only cleared, current-era, verified public sources. Never SELECT private text."""
    if kind == 'organizations':
        query = '''SELECT DISTINCT o.id,o.name,
            CASE WHEN o.charter_version=1 OR EXISTS (
                SELECT 1 FROM society_institution_texts amendment JOIN visible_posts source ON source.id=amendment.post_id
                WHERE amendment.object_kind='proposal' AND amendment.action='org_propose'
                AND amendment.clearance='cleared' AND amendment.object_id::text=(
                    SELECT e.data->>'proposal_id' FROM society_org_events e WHERE e.org_id=o.id
                    AND e.kind='charter_amended' ORDER BY e.id DESC LIMIT 1))
                THEN o.charter_text ELSE NULL END AS charter_text,o.charter_version,o.join_policy,
            o.created_by,o.created_tick,o.activated_tick,o.status
            FROM society_organizations o JOIN society_institution_texts t
            ON t.object_kind='organization' AND t.object_id=o.id AND t.action='org_found'
            JOIN visible_posts p ON p.id=t.post_id
            WHERE o.status='active' AND t.clearance='cleared' AND (%s::bigint IS NULL OR o.id=%s)
            ORDER BY o.id DESC LIMIT 100'''
    elif kind == 'proposals':
        query = '''SELECT DISTINCT o.id,o.scope_id AS org_id,o.author_id,o.kind,o.text,o.options,
            o.opens_tick,o.closes_tick,o.rule,o.status,o.result
            FROM society_proposals o JOIN society_organizations org ON org.id=o.scope_id
            JOIN society_institution_texts t ON t.object_kind='proposal' AND t.object_id=o.id
            JOIN visible_posts p ON p.id=t.post_id
            WHERE org.status='active' AND o.status IN ('open','passed','failed','cancelled','applied')
            AND t.clearance='cleared' AND (%s::bigint IS NULL OR o.id=%s)
            ORDER BY o.id DESC LIMIT 100'''
    elif kind == 'cases':
        query = '''SELECT c.id,c.forum_org_id,c.filed_by,c.subject_ids,c.claim_text,c.status,
            c.opened_tick,c.closed_tick,c.outcome FROM society_cases c
            WHERE c.status IN ('open','closed') AND (%s::bigint IS NULL OR c.id=%s) AND (
                EXISTS (SELECT 1 FROM society_institution_texts t JOIN visible_posts p ON p.id=t.post_id
                    WHERE t.object_kind='case' AND t.object_id=c.id AND t.action='case_open' AND t.clearance='cleared')
                OR EXISTS (SELECT 1 FROM society_institution_texts t JOIN visible_posts p ON p.id=t.post_id
                    WHERE p.id::text=c.outcome->>'source_post_id' AND t.clearance='cleared'))
            ORDER BY c.id DESC LIMIT 100'''
    else:
        raise society.SocietyError('no_such_institution_view')
    rows = [dict(r) for r in conn.execute(PUBLIC_POSTS + query, (identifier, identifier))]
    for row in rows:
        if kind == 'proposals':
            row['rule'] = {key: row['rule'].get(key) for key in ('electorate', 'quorum_pct', 'threshold_pct')}
            row['result'] = {key: row['result'][key] for key in
                ('electorate', 'votes', 'approval_votes', 'passed', 'error') if row['result'] and key in row['result']}
        elif kind == 'cases':
            outcome = row.pop('outcome') or {}
            # A ruling's own proposal must still have a verified cleared source.
            ruling = outcome.get('ruling') or {}
            ruling = ruling if isinstance(ruling, dict) else {}
            proposal_id = ruling.get('proposal_id')
            valid_id = type(proposal_id) is int and 0 < proposal_id < 2**63
            visible = bool(_institution_rows(conn, 'proposals', proposal_id)) if valid_id else False
            row['ruling'] = ({'proposal_id': proposal_id, **{key: ruling[key] for key in ('finding', 'remedy') if isinstance(ruling.get(key), str)}} if visible else {})
            row['entries'] = [dict(e) for e in conn.execute(PUBLIC_POSTS + '''SELECT e.id,e.actor_id,e.kind,e.text,e.tick
                FROM society_case_entries e JOIN society_institution_texts t
                ON t.object_kind='case_entry' AND t.object_id=e.id
                JOIN visible_posts p ON p.id=t.post_id WHERE e.case_id=%s AND e.clearance='cleared'
                AND t.clearance='cleared' ORDER BY e.id LIMIT 50''', (row['id'],))]
    return rows


def public_memberships(conn, agent_id):
    organizations = _institution_rows(conn, 'organizations')
    allowed = [o['id'] for o in organizations]
    return [dict(r) for r in conn.execute('''SELECT m.org_id,o.name,m.role,m.joined_tick
        FROM society_memberships m JOIN society_organizations o ON o.id=m.org_id
        WHERE m.agent_id=%s AND m.left_tick IS NULL AND m.org_id=ANY(%s)
        ORDER BY m.org_id LIMIT 100''', (agent_id, allowed))]


def install(app, connection_factory, now):
    def guard():
        if not enabled():
            raise HTTPException(404, "Not found")

    def read(build):
        guard()
        with connection_factory() as conn:
            try:
                return build(conn)
            except society.SocietyError:
                raise HTTPException(404, "Not found") from None

    @app.get("/api/society/feed", include_in_schema=False)
    def get_feed():
        return read(lambda conn: public_view(conn, "feed"))

    @app.get("/api/society/stats", include_in_schema=False)
    def get_stats():
        return read(lambda conn: public_view(conn, "stats"))

    @app.get("/api/society/square", include_in_schema=False)
    def get_square(open: str = ""):
        guard()
        if open and re.fullmatch(r"[1-9][0-9]{0,17}(,[1-9][0-9]{0,17}){0,99}", open) is None:
            raise HTTPException(400, "Invalid conversation selection")
        roots = sorted({int(value) for value in open.split(',')}) if open else []
        return read(lambda conn: public_view(conn, "square", opened=roots))

    @app.get("/api/society/agents/{agent_id}", include_in_schema=False)
    def get_agent(agent_id: str):
        result = read(lambda conn: public_view(conn, "agent", agent_id))
        if result is None:
            raise HTTPException(404, "Not found")
        return result

    def institution(kind, identifier=None):
        if identifier is not None and not 1 <= identifier <= 9223372036854775807:
            raise HTTPException(404, 'Not found')
        rows = read(lambda conn: _institution_rows(conn, kind, identifier))
        if identifier is None:
            return {kind: rows}
        if not rows:
            raise HTTPException(404, 'Not found')
        return rows[0]

    @app.get('/api/society/organizations', include_in_schema=False)
    def get_organizations():
        return institution('organizations')

    @app.get('/api/society/organizations/{identifier}', include_in_schema=False)
    def get_organization(identifier: int):
        return institution('organizations', identifier)

    @app.get('/api/society/proposals', include_in_schema=False)
    def get_proposals():
        return institution('proposals')

    @app.get('/api/society/proposals/{identifier}', include_in_schema=False)
    def get_proposal(identifier: int):
        return institution('proposals', identifier)

    @app.get('/api/society/cases', include_in_schema=False)
    def get_cases():
        return institution('cases')

    @app.get('/api/society/cases/{identifier}', include_in_schema=False)
    def get_case(identifier: int):
        return institution('cases', identifier)

    @app.post("/api/society/posts/{post_id}/like", include_in_schema=False)
    def post_like(post_id: int, request: Request):
        guard()
        if auth.mode() == "github" and not request.cookies.get(auth.SESSION_COOKIE):
            raise HTTPException(401, "Sign in to like")
        status = {"no_such_post": 404, "already_liked": 409, "daily_limit": 429}
        with connection_factory() as conn:
            voter = auth.voter(conn, request, now(), vote=True)
            try:
                return like(conn, _rules(), voter["id"], post_id, now())
            except society.SocietyError as error:
                raise HTTPException(status.get(str(error), 400), str(error)) from None
