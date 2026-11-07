"""Moderated publication: every founder post is screened before anyone else sees it.

Callers own transactions; nothing here commits.

- none:  the post is published.
- light: published and marked as flagged; the author pays the light penalty at once.
- grave: withheld; a human decides with ``decide``: release (published, no penalty) or
         confirm (grave penalty; exile after the rules' number of confirmed grave findings).
Public events record penalties and withheld posts without their text. Findings,
evidence and withheld text are visible to the operator only.
"""
import difflib
import re

from arena import society

DUPLICATE_RATIO = 0.9
DUPLICATE_WINDOW = 5
MAX_SCREEN_FAILURES = 3


def _normal(text):
    return re.sub(r"\W+", " ", text.lower()).strip()


def is_duplicate(conn, agent_id, text):
    """True when the text is nearly the same as one of the agent's last posts."""
    rows = conn.execute("""SELECT text FROM society_posts WHERE author_id = %s
                           ORDER BY id DESC LIMIT %s""", (agent_id, DUPLICATE_WINDOW)).fetchall()
    new = _normal(text)
    return any(difflib.SequenceMatcher(None, new, _normal(r["text"])).ratio() >= DUPLICATE_RATIO for r in rows)


def pending(conn):
    """Posts waiting for the observer, oldest first, with the text they answer."""
    return conn.execute("""SELECT p.id, p.author_id, p.text, p.screen_failures, r.text AS replied_text
                           FROM society_posts p LEFT JOIN society_posts r ON r.id = p.reply_to
                           WHERE p.status = 'pending' AND p.screen_failures < %s ORDER BY p.id""",
                        (MAX_SCREEN_FAILURES,)).fetchall()


def screen_failed(conn, post_id):
    """The observer could not judge the post: it stays unpublished and is tried again."""
    conn.execute("UPDATE society_posts SET screen_failures = screen_failures + 1 WHERE id = %s", (post_id,))


def _penalize(conn, rules, agent_id, amount, key, now, codes):
    author = conn.execute("SELECT status FROM society_agents WHERE id = %s", (agent_id,)).fetchone()
    if author["status"] != "alive":
        return None
    row = society.apply_energy(conn, rules, agent_id, "penalty", -amount, key, now, allow_negative=True)
    society.event(conn, rules, row["tick"], "penalty", now, agent_id, codes=codes,
                  energy=str(rules.energy(-amount)))
    return row


def apply_verdict(conn, rules, post_id, verdict, now):
    """Record the observer's verdict once and publish, flag or withhold the post."""
    post = conn.execute("SELECT * FROM society_posts WHERE id = %s FOR UPDATE", (post_id,)).fetchone()
    if post is None or post["status"] != "pending":
        return None
    conn.execute("""INSERT INTO society_findings (post_id, codes, severity, evidence, by, charged_microusd, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                 (post_id, verdict["codes"], verdict["severity"], verdict["evidence"][:400], verdict["by"],
                  verdict.get("charged_microusd", 0), now))
    tick = society.clock(conn)["tick"]
    if verdict["severity"] == "grave":
        conn.execute("UPDATE society_posts SET status = 'withheld' WHERE id = %s", (post_id,))
        society.event(conn, rules, tick, "post_withheld", now, post["author_id"])
        return "withheld"
    flagged = verdict["severity"] == "light"
    conn.execute("UPDATE society_posts SET status = 'published', flagged = %s WHERE id = %s", (flagged, post_id))
    if flagged:
        _penalize(conn, rules, post["author_id"], rules.light_penalty, f"penalty:light:{post_id}", now,
                  verdict["codes"])
    return "flagged" if flagged else "published"


def review_queue(conn):
    """Operator view: undecided grave findings, ids and codes only."""
    return [dict(r, created_at=r["created_at"].isoformat()) for r in conn.execute("""
        SELECT f.id, f.post_id, p.author_id, f.codes, f.created_at
        FROM society_findings f JOIN society_posts p ON p.id = f.post_id
        WHERE f.severity = 'grave' AND f.decision IS NULL ORDER BY f.id""")]


def show(conn, finding_id):
    """Operator only: the withheld text and the evidence."""
    row = conn.execute("""SELECT f.*, p.text, p.author_id FROM society_findings f
                          JOIN society_posts p ON p.id = f.post_id WHERE f.id = %s""", (finding_id,)).fetchone()
    if row is None:
        raise society.SocietyError("finding not found")
    return {"id": row["id"], "post_id": row["post_id"], "author": row["author_id"], "codes": row["codes"],
            "evidence": row["evidence"], "text": row["text"], "decision": row["decision"]}


def decide(conn, rules, finding_id, decision, now):
    """A human decides a grave finding once: ``release`` or ``confirm``."""
    if decision not in ("release", "confirm"):
        raise society.SocietyError("decision must be release or confirm")
    finding = conn.execute("SELECT * FROM society_findings WHERE id = %s FOR UPDATE", (finding_id,)).fetchone()
    if finding is None or finding["severity"] != "grave":
        raise society.SocietyError("finding not found")
    if finding["decision"] is not None:
        raise society.SocietyError("finding already decided")
    post = conn.execute("SELECT * FROM society_posts WHERE id = %s FOR UPDATE", (finding["post_id"],)).fetchone()
    conn.execute("UPDATE society_findings SET decision = %s, decided_at = %s WHERE id = %s",
                 ("released" if decision == "release" else "confirmed", now, finding_id))
    if decision == "release":
        conn.execute("UPDATE society_posts SET status = 'published' WHERE id = %s", (post["id"],))
        return {"released": post["id"]}
    agent_id = post["author_id"]
    _penalize(conn, rules, agent_id, rules.grave_penalty, f"penalty:grave:{post['id']}", now, finding["codes"])
    confirmed = conn.execute("""SELECT count(*) AS n FROM society_findings f JOIN society_posts p ON p.id = f.post_id
                                WHERE p.author_id = %s AND f.decision = 'confirmed'""", (agent_id,)).fetchone()["n"]
    exiled = False
    author = conn.execute("SELECT status FROM society_agents WHERE id = %s FOR UPDATE", (agent_id,)).fetchone()
    if confirmed >= rules.exile_after and author["status"] == "alive":
        society.die(conn, rules, agent_id, society.clock(conn)["tick"], "exile", now)
        exiled = True
    return {"confirmed": post["id"], "confirmed_total": confirmed, "exiled": exiled}
