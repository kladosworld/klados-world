"""Last words: one free message from an agent that has died, screened like any post.

The fossil already exists (written at death by ``society.die``); this module only
fills its last words. Outcomes, recorded once:
- published: approved by the observer (a light finding still publishes);
- withheld:  a grave finding; the text is kept for the operator, never published;
- missing:   no usable message after ``MAX_ATTEMPTS`` tries.
Callers own transactions; nothing here commits.
"""
from arena import society

MAX_ATTEMPTS = 3


def pending(conn):
    return conn.execute("""SELECT * FROM society_fossils WHERE last_words_status = 'pending'
                           AND last_words_attempts < %s ORDER BY died_tick, agent_id""", (MAX_ATTEMPTS,)).fetchall()


def context(conn, rules, agent_id):
    """What the dying agent sees: its public life, and its own strategy and notes (its private self)."""
    from arena import society_life
    fossil = conn.execute("SELECT * FROM society_fossils WHERE agent_id = %s", (agent_id,)).fetchone()
    posts = conn.execute("""SELECT text FROM society_posts WHERE author_id = %s AND status = 'published'
                            ORDER BY id DESC LIMIT 5""", (agent_id,)).fetchall()
    own = society_life.strategy(conn, agent_id)
    return {"you": {"name": fossil["name"], "personality": fossil["public_description"],
                    "private_strategy": own["text"] if own else None,
                    "private_notes": society_life.notes(conn, agent_id)},
            "your_death": {"cause": {"energy": "your energy ran out", "age": "you reached the maximum age",
                                     "exile": "you were exiled after repeated grave violations"}[fossil["death_cause"]],
                           "days_lived": round((fossil["died_tick"] - fossil["born_tick"]) / 24, 1)},
            "your_life": dict(fossil["counts"], last_posts=[p["text"] for p in reversed(posts)])}


def failed(conn, agent_id):
    """One unusable attempt; after the last one the fossil keeps no last words."""
    row = conn.execute("""UPDATE society_fossils SET last_words_attempts = last_words_attempts + 1
                          WHERE agent_id = %s AND last_words_status = 'pending'
                          RETURNING last_words_attempts""", (agent_id,)).fetchone()
    if row and row["last_words_attempts"] >= MAX_ATTEMPTS:
        conn.execute("UPDATE society_fossils SET last_words_status = 'missing' WHERE agent_id = %s", (agent_id,))
        return "missing"
    return "retry"


def record(conn, rules, agent_id, text, verdict, now):
    """Store the screened last words once. Returns the outcome."""
    fossil = conn.execute("SELECT * FROM society_fossils WHERE agent_id = %s FOR UPDATE", (agent_id,)).fetchone()
    if fossil is None or fossil["last_words_status"] != "pending":
        return None
    tick = society.clock(conn)["tick"]
    if verdict["severity"] == "grave":
        conn.execute("""UPDATE society_fossils SET last_words_status = 'withheld', withheld_last_words = %s,
                        last_words_attempts = last_words_attempts + 1 WHERE agent_id = %s""", (text, agent_id))
        return "withheld"
    conn.execute("""UPDATE society_fossils SET last_words_status = 'published', last_words = %s,
                    last_words_attempts = last_words_attempts + 1 WHERE agent_id = %s""", (text, agent_id))
    society.event(conn, rules, tick, "last_words", now, agent_id)
    return "published"
