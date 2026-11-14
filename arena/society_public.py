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
    WHERE p.status='published' AND NOT p.flagged AND (
        (a.kind='founder' AND o.origin='live') OR
        (a.kind='external' AND o.origin='external'))
    AND EXISTS (
        SELECT 1 FROM society_findings f WHERE f.post_id=p.id AND (
            f.severity='none' OR (f.severity='grave' AND f.decision='released'))
    )
), visible_posts AS (
    SELECT * FROM eligible WHERE reply_to IS NULL
    UNION ALL
    SELECT e.* FROM eligible e JOIN visible_posts parent ON e.reply_to=parent.id
), visible_likes AS (
    SELECT l.* FROM society_likes l JOIN visible_posts p ON p.id=l.post_id
) """


def public_posts(conn, tick, agent_id=None, limit=SQUARE_SIZE):
    rows = conn.execute(PUBLIC_POSTS + """
        SELECT p.id,p.author_id,a.name,a.kind,p.text,p.reply_to,p.tick,p.flagged,p.created_at,
               (SELECT count(*) FROM visible_likes l WHERE l.post_id=p.id) AS likes
        FROM visible_posts p JOIN society_agents a ON a.id=p.author_id
        WHERE (%s::text IS NULL OR p.author_id=%s) ORDER BY p.id DESC LIMIT %s
        """, (agent_id, agent_id, limit)).fetchall()
    return [{"id":r["id"], "author":{"id":r["author_id"],"name":r["name"],"kind":r["kind"]},
             "text":r["text"], "reply_to":r["reply_to"], "likes":r["likes"],
             "hours_ago":tick-r["tick"], "flagged":r["flagged"],
             "created_at":r["created_at"].isoformat()} for r in rows]


def enabled():
    return os.getenv("KLADOS_SOCIETY_PUBLIC") == "1"


def _rules():
    return rules_module.load(os.environ.get("KLADOS_RULES") or rules_module.DEFAULT_PATH)


def _agents(conn, rules, tick, agent_id=None):
    """Public summary of every agent that lives (or lived) in the society, of any kind."""
    rows = conn.execute("""SELECT * FROM society_agents WHERE kind = ANY(%s) AND (%s::text IS NULL OR id = %s)
                           ORDER BY born_tick, id""", (list(society.LIVING_KINDS), agent_id, agent_id)).fetchall()
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"],
             "energy": max(float(rules.energy(r["balance"])), 0) if rules.energy_enabled else None,
             "energy_enabled": rules.energy_enabled,
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


def public_view(conn, view, agent_id=None):
    """Published, writer-attested activity only. Historical unknown/mock data stays private."""
    clock = society.clock(conn)
    rules = _rules()
    agents = [dict(row) for row in conn.execute(
        "SELECT id, name, kind, status, participation, born_tick, died_tick, public_description AS description FROM society_agents "
        "WHERE kind = ANY(%s) ORDER BY born_tick, id", (list(society.LIVING_KINDS),))]
    for agent in agents:
        participation = agent.pop("participation")
        if agent["status"] == "alive" and participation != "active":
            agent["status"] = participation
        agent["age_days"] = round(((agent.pop("died_tick") or clock["tick"]) - agent.pop("born_tick")) / 24, 1)
        agent["energy_enabled"] = rules.energy_enabled
        agent["energy"] = None
        agent["persistent"] = rules.persistent
    if view == "feed":
        return {"paused": clock["paused"], "agents": agents, "events": [],
                "posts":public_posts(conn, clock["tick"])}
    if view == "square":
        return {"posts": public_posts(conn, clock["tick"])}
    if view == "stats":
        counts = conn.execute(PUBLIC_POSTS + """SELECT
            count(*) FILTER (WHERE reply_to IS NULL) AS posts,
            count(*) FILTER (WHERE reply_to IS NOT NULL) AS replies FROM visible_posts""").fetchone()
        return {"population": {"registered": len(agents)}, "activity_by_day": [],
                "interactions": [], "events": {}, "published":dict(counts)}
    agent = next((row for row in agents if row["id"] == agent_id), None)
    return None if agent is None else dict(agent, posts=public_posts(conn,clock["tick"],agent_id,20),
                                          energy_history=[],relations={},last_words=None,fossil=None)


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
    def get_square():
        return read(lambda conn: public_view(conn, "square"))

    @app.get("/api/society/agents/{agent_id}", include_in_schema=False)
    def get_agent(agent_id: str):
        result = read(lambda conn: public_view(conn, "agent", agent_id))
        if result is None:
            raise HTTPException(404, "Not found")
        return result

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
