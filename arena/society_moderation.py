"""Founder publication follows a local safety gate, with selective later review.

Low-risk founder posts publish immediately. Mandatory serious-risk cases remain
private; optional reviews may inspect already-published material. Verdicts can
publish, flag or withhold; audited human release/confirm resolves held cases.
Current persistent rules do not apply energy penalties or automatic exile. Legacy
records/paths remain for compatibility. External submissions retain their separate
prepublication path. Findings and evidence are operator-only. Callers own commits.
"""
import difflib
import re
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from arena import society

DUPLICATE_RATIO = 0.9
DUPLICATE_WINDOW = 5
MAX_SCREEN_FAILURES = 3
OPTIONAL_QUEUE_LIMIT = 1000
MANDATORY_QUEUE_LIMIT = 1000


def _normal(text):
    return re.sub(r"\W+", " ", text.lower()).strip()


def is_duplicate(conn, agent_id, text):
    """True when the text is nearly the same as one of the agent's last posts."""
    rows = conn.execute("""SELECT text FROM society_posts WHERE author_id = %s
                           ORDER BY id DESC LIMIT %s""", (agent_id, DUPLICATE_WINDOW)).fetchall()
    new = _normal(text)
    return any(difflib.SequenceMatcher(None, new, _normal(r["text"])).ratio() >= DUPLICATE_RATIO for r in rows)


def pending(conn, now=None):
    """Eligible mandatory holds first, then optional published reviews; at most 32."""
    return conn.execute("""WITH review_ids AS (
                           SELECT id FROM society_posts WHERE status='pending'
                           UNION ALL SELECT p.id FROM society_findings gate JOIN society_posts p ON p.id=gate.post_id
                           WHERE p.status='published' AND gate.metadata->>'async_pending'='true'
                           AND gate.id=(SELECT max(id) FROM society_findings WHERE post_id=p.id))
                           SELECT p.id, p.author_id, p.text, p.status AS review_status, p.screen_failures, r.text AS replied_text,
                           coalesce((SELECT max(e.id) FROM society_events e
                             WHERE e.kind IN ('moderation_contested','moderation_retry')
                               AND e.data->>'post_id'=p.id::text),0) AS screening_generation,
                           EXISTS(SELECT 1 FROM society_events e WHERE e.kind='moderation_contested'
                               AND e.data->>'post_id'=p.id::text) AS contested
                           FROM review_ids selected JOIN society_posts p ON p.id=selected.id
                           LEFT JOIN society_posts r ON r.id = p.reply_to
                           WHERE p.screen_failures < %s AND NOT EXISTS (
                             SELECT 1 FROM society_events e WHERE e.kind='moderation_outage'
                             AND e.data->>'post_id'=p.id::text AND e.created_at > %s
                             AND (EXISTS (SELECT 1 FROM society_events h WHERE h.kind='risk_hold'
                                  AND h.data->>'post_id'=p.id::text)
                                  OR EXISTS (SELECT 1 FROM society_findings f WHERE f.post_id=p.id
                                  AND f.metadata->>'config_version' LIKE 'risk-gate-v%%')))
                           ORDER BY (p.status='pending') DESC,p.id LIMIT 32""",
                        (MAX_SCREEN_FAILURES, (now or datetime.now(timezone.utc))-timedelta(minutes=5))).fetchall()


def screen_failed(conn, post_id):
    """Count a bounded review failure; caller preserves the case's publication priority."""
    conn.execute("UPDATE society_posts SET screen_failures = screen_failures + 1 WHERE id = %s", (post_id,))


def _penalize(conn, rules, agent_id, amount, key, now, codes):
    if not rules.energy_enabled:
        return None
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
    async_review = post is not None and post['status'] == 'published' and conn.execute(
        """SELECT 1 FROM society_findings WHERE post_id=%s AND metadata->>'async_pending'='true'
        AND id=(SELECT max(id) FROM society_findings WHERE post_id=%s)""", (post_id, post_id)).fetchone()
    if post is None or (post["status"] != "pending" and not async_review):
        return None
    if "screening_generation" in verdict:
        generation = conn.execute("""SELECT coalesce(max(id),0) AS generation FROM society_events
            WHERE kind IN ('moderation_contested','moderation_retry') AND data->>'post_id'=%s""",
            (str(post_id),)).fetchone()["generation"]
        if generation != verdict["screening_generation"]:
            return None
    # Only queue state is mutable. Findings/codes/evidence stay intact, and selection
    # plus completion remain in the append-only event log.
    closed = conn.execute("""UPDATE society_findings SET metadata=metadata || '{"async_pending":false}'::jsonb
        WHERE post_id=%s AND metadata->>'async_pending'='true' RETURNING id""",(post_id,)).fetchall()
    if closed:
        society.event(conn,rules,society.clock(conn)['tick'],'moderation_optional_completed',now,
                      post_id=post_id,finding_ids=[row['id'] for row in closed])
    conn.execute("""INSERT INTO society_findings (post_id, codes, severity, evidence, by, charged_microusd, created_at, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                 (post_id, verdict["codes"], verdict["severity"], verdict["evidence"][:400], verdict["by"],
                  verdict.get("charged_microusd", 0), now,
                  Jsonb({key: verdict[key] for key in ("config_version", "matches", "moderation_requests", "screening_generation", "sexual_signal")
                         if key in verdict})))
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
        WHERE f.severity = 'grave' AND f.decision IS NULL AND p.status='withheld'
          AND f.id=(SELECT max(latest.id) FROM society_findings latest WHERE latest.post_id=f.post_id)
        ORDER BY f.id""")]


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
    # A rescreen may finish while this decision waits for the post lock.
    latest = conn.execute("SELECT max(id) AS id FROM society_findings WHERE post_id=%s",
                          (finding["post_id"],)).fetchone()["id"]
    if finding["id"] != latest:
        raise society.SocietyError("finding superseded")
    if post["status"] != "withheld":
        raise society.SocietyError("post is awaiting rescreen")
    conn.execute("UPDATE society_findings SET decision = %s, decided_at = %s WHERE id = %s",
                 ("released" if decision == "release" else "confirmed", now, finding_id))
    if decision == "release":
        conn.execute("UPDATE society_posts SET status = 'published', flagged=false WHERE id = %s", (post["id"],))
        return {"released": post["id"]}
    agent_id = post["author_id"]
    _penalize(conn, rules, agent_id, rules.grave_penalty, f"penalty:grave:{post['id']}", now, finding["codes"])
    confirmed = conn.execute("""SELECT count(*) AS n FROM society_findings f JOIN society_posts p ON p.id = f.post_id
                                WHERE p.author_id = %s AND f.decision = 'confirmed'""", (agent_id,)).fetchone()["n"]
    exiled = False
    author = conn.execute("SELECT status FROM society_agents WHERE id = %s FOR UPDATE", (agent_id,)).fetchone()
    if not rules.persistent and confirmed >= rules.exile_after and author["status"] == "alive":
        society.die(conn, rules, agent_id, society.clock(conn)["tick"], "exile", now)
        exiled = True
    return {"confirmed": post["id"], "confirmed_total": confirmed, "exiled": exiled}


def contest(conn, rules, post_id, now):
    """Explicit operator rescreen. Prior findings remain immutable and non-public."""
    post = conn.execute("SELECT * FROM society_posts WHERE id=%s FOR UPDATE", (post_id,)).fetchone()
    if post is None or post["status"] == "pending":
        raise society.SocietyError("post unavailable for rescreen")
    conn.execute("UPDATE society_posts SET status='pending', flagged=true, screen_failures=0 WHERE id=%s", (post_id,))
    society.event(conn, rules, society.clock(conn)["tick"], "moderation_contested", now,
                  post["author_id"], post_id=post_id)
    return {"rescreen": post_id}


def retry_screening(conn, rules, post_id, now):
    """An explicit retry starts a new audit generation without deleting findings or charges."""
    post = conn.execute("SELECT * FROM society_posts WHERE id=%s FOR UPDATE", (post_id,)).fetchone()
    if post is None or post["status"] != "pending" or post["screen_failures"] < MAX_SCREEN_FAILURES:
        raise society.SocietyError("post is not awaiting screening recovery")
    conn.execute("UPDATE society_posts SET screen_failures=0 WHERE id=%s", (post_id,))
    society.event(conn, rules, society.clock(conn)["tick"], "moderation_retry", now,
                  post["author_id"], post_id=post_id)
    return {"retry_screening": post_id}


def initial_gate(conn, rules, post_id, text, now):
    """Founder-only publication policy; identity/provenance still use trusted callers."""
    from arena import risk_gate
    gate = risk_gate.classify(text)
    # Serialize bounded queue admission; safe, non-selected posts never wait on a provider.
    followup = gate['followup']
    if followup or gate['state'] == 'hold':
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (73423205,))
        mandatory = conn.execute("""SELECT count(*) AS n FROM (SELECT id FROM society_posts
            WHERE status='pending' AND id<>%s LIMIT %s) active""",(post_id,MANDATORY_QUEUE_LIMIT)).fetchone()['n']
        optional = conn.execute("""SELECT count(*) AS n FROM (SELECT f.id FROM society_findings f
            JOIN society_posts p ON p.id=f.post_id WHERE p.status='published' AND p.id<>%s
            AND f.metadata->>'async_pending'='true' AND f.id=(SELECT max(id) FROM society_findings WHERE post_id=p.id)
            LIMIT %s) active""",(post_id,OPTIONAL_QUEUE_LIMIT)).fetchone()['n']
        queued = dict(mandatory=mandatory,optional=optional)
        if followup and queued['optional'] >= OPTIONAL_QUEUE_LIMIT:
            followup = False
            society.event(conn,rules,society.clock(conn)['tick'],'moderation_optional_dropped',now,
                          post_id=post_id,reason='queue_capacity')
        if gate['state']=='hold' and queued['mandatory'] >= MANDATORY_QUEUE_LIMIT:
            society.event(conn,rules,society.clock(conn)['tick'],'moderation_backpressure',now,
                          post_id=post_id,priority='mandatory')
            escalate(conn,rules,post_id,now)
            return
    if gate['state'] == 'hold':
        society.event(conn, rules, society.clock(conn)['tick'], 'risk_hold', now, post_id=post_id)
        return
    verdict = dict(codes=gate['codes'], severity='none' if gate['state']=='allow' else 'grave',
                   evidence=gate['evidence'], by='precheck', config_version=risk_gate.VERSION)
    apply_verdict(conn, rules, post_id, verdict, now)
    if followup:
        conn.execute("""UPDATE society_findings SET metadata=metadata || %s
            WHERE id=(SELECT max(id) FROM society_findings WHERE post_id=%s)""",
            (Jsonb(dict(async_pending=True, selection_signals=risk_gate.review_signals(text))), post_id))
        society.event(conn,rules,society.clock(conn)['tick'],'moderation_optional_selected',now,post_id=post_id)


def escalate(conn, rules, post_id, now):
    """Exhausted review stays withheld for the existing human release/confirm queue."""
    post = conn.execute('SELECT status FROM society_posts WHERE id=%s FOR UPDATE',(post_id,)).fetchone()
    if post and post['status']=='published':
        result = apply_verdict(conn,rules,post_id,
            dict(codes=[],severity='none',evidence='',by='precheck',config_version='risk-gate-v2',
                 matches=['optional_review_dropped']),now)
        society.event(conn,rules,society.clock(conn)['tick'],'moderation_optional_dropped',now,
                      post_id=post_id,reason='review_unavailable')
        return result
    result = apply_verdict(conn, rules, post_id,
        dict(codes=['REVIEW_REQUIRED'], severity='grave', evidence='Unresolved risk; human review required',
             by='precheck', config_version='risk-gate-v2'), now)
    if result:
        society.event(conn,rules,society.clock(conn)['tick'],'moderation_human_required',now,post_id=post_id)
    return result


def risk_review(conn, post_id):
    return bool(conn.execute("""SELECT 1 WHERE EXISTS (SELECT 1 FROM society_events
        WHERE kind='risk_hold' AND data->>'post_id'=%s) OR EXISTS (
        SELECT 1 FROM society_findings WHERE post_id=%s AND metadata->>'config_version' LIKE 'risk-gate-v%%')""",
        (str(post_id), post_id)).fetchone())


def queue_status(conn, now):
    """Operator-only metadata: never texts, evidence, names or provider credentials."""
    row = conn.execute("""SELECT
        count(*) FILTER (WHERE p.status='pending') AS mandatory_pending,
        min(p.created_at) FILTER (WHERE p.status='pending') AS oldest_pending,
        count(*) FILTER (WHERE p.status='published' AND EXISTS (SELECT 1 FROM society_findings f
            WHERE f.post_id=p.id AND f.metadata->>'async_pending'='true'
            AND f.id=(SELECT max(id) FROM society_findings WHERE post_id=p.id))) AS optional_pending,
        count(*) FILTER (WHERE p.status='withheld' AND EXISTS (SELECT 1 FROM society_findings f
            WHERE f.post_id=p.id AND f.severity='grave' AND f.decision IS NULL
            AND f.id=(SELECT max(id) FROM society_findings WHERE post_id=p.id))) AS human_pending,
        min(p.created_at) FILTER (WHERE p.status='withheld' AND EXISTS (SELECT 1 FROM society_findings f
            WHERE f.post_id=p.id AND f.severity='grave' AND f.decision IS NULL
            AND f.id=(SELECT max(id) FROM society_findings WHERE post_id=p.id))) AS oldest_human
        FROM society_posts p""").fetchone()
    dropped = conn.execute("""SELECT count(*) FILTER (WHERE kind='moderation_optional_dropped') AS optional_dropped,
        count(*) FILTER (WHERE kind='moderation_backpressure') AS mandatory_overflow
        FROM society_events WHERE created_at >= %s""", (now-timedelta(hours=24),)).fetchone()
    return dict(mandatory_pending=row['mandatory_pending'],optional_pending=row['optional_pending'],
        human_pending=row['human_pending'],optional_dropped_24h=dropped['optional_dropped'],
        mandatory_overflow_24h=dropped['mandatory_overflow'],
        oldest_pending_seconds=max(0,int((now-row['oldest_pending']).total_seconds())) if row['oldest_pending'] else None,
        oldest_human_seconds=max(0,int((now-row['oldest_human']).total_seconds())) if row['oldest_human'] else None,
        attention_required=row['human_pending']>0 or row['mandatory_pending']>0,
        batch_limit=32,optional_limit=OPTIONAL_QUEUE_LIMIT,mandatory_limit=MANDATORY_QUEUE_LIMIT)
