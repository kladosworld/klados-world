"""Wall-clock social dormancy; orthogonal to administrative/security controls.

No death, account replacement, identity reset, inference or automatic admin
reactivation. A dormant leader stays a member and does not regain the role merely
by returning to activity.
"""
from datetime import timedelta

from arena import activity_limits, society

INACTIVE_AFTER = timedelta(days=7)


def _latest(conn, agent):
    return conn.execute('''SELECT max(at) AS at FROM (
        SELECT p.created_at AS at FROM society_posts p WHERE p.author_id=%s
            AND p.status='published' AND NOT p.flagged
            AND EXISTS (SELECT 1 FROM society_findings f WHERE f.post_id=p.id
                AND f.id=(SELECT max(id) FROM society_findings WHERE post_id=p.id)
                AND (f.decision='released' OR (f.severity='none'
                    AND f.metadata->>'review_status' IN ('cleared','not_required'))))
        UNION ALL SELECT created_at FROM society_likes WHERE from_kind='agent' AND from_id=%s
        UNION ALL SELECT created_at FROM society_votes WHERE voter_id=%s
        UNION ALL SELECT created_at FROM society_org_events WHERE actor_id=%s
        UNION ALL SELECT created_at FROM society_commitment_events WHERE actor_id=%s
        UNION ALL SELECT created_at FROM society_case_entries WHERE actor_id=%s AND clearance='cleared'
        UNION ALL SELECT created_at FROM society_trust WHERE from_id=%s
        ) activity''', (agent, agent, agent, agent, agent, agent, agent)).fetchone()['at']


def refresh(conn, now):
    changed = 0
    tick = society.clock(conn)['tick']
    for agent in conn.execute("SELECT id,created_at FROM society_agents WHERE kind IN ('founder','external') AND status='alive' ORDER BY id").fetchall():
        identifier = agent['id']
        conn.execute('SELECT pg_advisory_xact_lock(%s,hashtext(%s))',
                     (activity_limits.LOCK_NAMESPACE, identifier))
        last = _latest(conn, identifier) or agent['created_at']
        current = conn.execute('SELECT * FROM society_agent_lifecycle WHERE agent_id=%s FOR UPDATE', (identifier,)).fetchone()
        if current is not None:
            # A once-cleared activity remains historical activity after takedown.
            last = max(last, current['last_active_at'])
        state = 'dormant' if now - last >= INACTIVE_AFTER else 'active'
        old_state = current['state'] if current else 'active'
        conn.execute('''INSERT INTO society_agent_lifecycle(agent_id,last_active_at,state,changed_at)
            VALUES (%s,%s,%s,%s) ON CONFLICT(agent_id) DO UPDATE SET
            last_active_at=GREATEST(society_agent_lifecycle.last_active_at,excluded.last_active_at),
            state=excluded.state,changed_at=CASE WHEN society_agent_lifecycle.state<>excluded.state
                THEN excluded.changed_at ELSE society_agent_lifecycle.changed_at END''',
            (identifier, last, state, now))
        if state != old_state:
            changed += 1
        if state == 'dormant' and state != old_state:
            # Projection only: retain membership, joined_tick and all historical events.
            orgs = conn.execute("UPDATE society_memberships SET role='member' WHERE agent_id=%s AND role='leader' AND left_tick IS NULL RETURNING org_id", (identifier,)).fetchall()
            for org in orgs:
                conn.execute('''INSERT INTO society_org_events
                    (org_id,kind,subject_id,data,tick,created_at)
                    VALUES (%s,'role_revoked',%s,'{"role":"leader","reason":"social_dormancy"}',%s,%s)''',
                    (org['org_id'], identifier, tick, now))
    return {'changed': changed}
