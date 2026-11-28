"""Wall-clock social dormancy; orthogonal to administrative/security controls.

No death, account replacement, identity reset, inference or automatic admin
reactivation. A dormant leader stays a member and does not regain the role merely
by returning to activity.
"""
from datetime import timedelta

from arena import activity_limits

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


def touch(conn, agent_id, now):
    """Successful cleared activity may wake only auto-dormant participation."""
    from arena import founder_status
    return founder_status.touch(conn, agent_id, now)


def refresh(conn, now, *, limit=32):
    from arena import operation_locks
    operation_locks.institutions(conn)
    from arena import founder_status
    if type(limit) is not int or not 1 <= limit <= 128:
        raise ValueError('Invalid maintenance batch size')
    changed = 0
    for agent in conn.execute("SELECT a.id,a.kind,a.created_at FROM society_agents a LEFT JOIN society_maintenance_checks c ON c.agent_id=a.id WHERE a.kind IN ('founder','external') AND a.status='alive' AND a.participation IN ('active','dormant') ORDER BY c.checked_at NULLS FIRST,a.id LIMIT %s", (limit,)).fetchall():
        identifier = agent['id']
        conn.execute('SELECT pg_advisory_xact_lock(%s,hashtext(%s))',
                     (activity_limits.LOCK_NAMESPACE, identifier))
        value = founder_status.details(conn, identifier)
        current = conn.execute('SELECT * FROM society_agent_lifecycle WHERE agent_id=%s FOR UPDATE', (identifier,)).fetchone()
        last = _latest(conn, identifier) or agent['created_at']
        if current is not None:
            last = max(last, current['last_active_at'])
        locked = (not value['history_consistent'] or value['participation'] in
                  ('inactive','retired','suspended','revoked') or
                  (value['participation']=='dormant' and value['source']!='auto'))
        old_state = current['state'] if current else 'active'
        if locked:
            # Social refresh never rewrites an operator/security lock, even after
            # a delayed post clears or an old durable decision is recovered.
            state = old_state
        else:
            state = 'dormant' if now-last >= INACTIVE_AFTER else 'active'
            if agent['kind']=='founder':
                changed += founder_status.automatic(conn, identifier, state, now)
            elif state=='dormant' and old_state!='dormant':
                # External agents retain active API participation while socially
                # dormant; their independently operated client can return by acting.
                founder_status._release_leadership(conn,identifier,'dormant','auto',now)
                changed += 1
            elif state != old_state:
                changed += 1
        conn.execute("""INSERT INTO society_agent_lifecycle(agent_id,last_active_at,state,changed_at)
            VALUES (%s,%s,%s,%s) ON CONFLICT(agent_id) DO UPDATE SET
            last_active_at=GREATEST(society_agent_lifecycle.last_active_at,excluded.last_active_at),
            state=excluded.state,changed_at=CASE WHEN society_agent_lifecycle.state<>excluded.state
                THEN excluded.changed_at ELSE society_agent_lifecycle.changed_at END""",
            (identifier,last,state,now))
        conn.execute("INSERT INTO society_maintenance_checks(agent_id,checked_at) VALUES (%s,%s) ON CONFLICT(agent_id) DO UPDATE SET checked_at=excluded.checked_at", (identifier,now))
    return {'changed': changed}
