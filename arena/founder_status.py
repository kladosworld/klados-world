"""Audited participation controls; no parallel administrative projection.

Callers own transactions and operator authentication. No identity rewrite,
security bypass, paid inference, clock reset, or society activation.
"""
from uuid import uuid4

from arena import activity_limits, society

STATES = ('active', 'inactive', 'dormant', 'retired')
SECURITY_STATES = ('suspended', 'revoked')


def _lock(conn, identifier):
    from arena import operation_locks
    operation_locks.social(conn)
    conn.execute('SELECT pg_advisory_xact_lock(%s,hashtext(%s))',
                 (activity_limits.LOCK_NAMESPACE, identifier))


def details(conn, identifier, *, lock=False):
    if lock:
        _lock(conn, identifier)
    row = conn.execute('SELECT id,kind,status,participation,created_at FROM society_agents WHERE id=%s',
                       (identifier,)).fetchone()
    if row is None or row['kind'] not in society.LIVING_KINDS:
        raise society.SocietyError('no_such_agent')
    event = conn.execute('''SELECT to_state,source,created_at FROM society_participation_events
        WHERE agent_id=%s ORDER BY id DESC LIMIT 1''', (identifier,)).fetchone()
    consistent = event is None or event['to_state'] == row['participation']
    source = event['source'] if event else 'auto' if row['participation'] == 'active' else 'admin'
    can_act = (row['status'] == 'alive' and consistent and
               (row['participation'] == 'active' or
                (row['kind'] == 'founder' and row['participation'] == 'dormant' and source == 'auto')))
    label_source = 'security' if row['participation'] in SECURITY_STATES else source
    return {'agent_id': identifier, 'kind': row['kind'], 'lifecycle_status': row['status'],
            'participation': row['participation'], 'source': source,
            'since': (event['created_at'] if event else row['created_at']).isoformat(),
            'participation_label': row['participation'], 'participation_source': label_source,
            'history_consistent': consistent, 'can_participate': can_act}


def may_act(conn, identifier, *, lock=False):
    return details(conn, identifier, lock=lock)['can_participate']


def status(conn):
    values = [details(conn, row['id']) for row in conn.execute(
        "SELECT id FROM society_agents WHERE kind='founder' ORDER BY id")]
    return {'founders': values, 'counts': {state: sum(r['participation'] == state for r in values)
            for state in (*STATES, *SECURITY_STATES)}}


def history(conn, identifier=None):
    if identifier is not None and details(conn, identifier)['kind'] != 'founder':
        raise society.SocietyError('no_such_founder')
    rows = conn.execute('''SELECT e.* FROM society_participation_events e
        JOIN society_agents a ON a.id=e.agent_id WHERE a.kind='founder'
        AND (%s::text IS NULL OR e.agent_id=%s) ORDER BY e.id''', (identifier, identifier)).fetchall()
    return [dict(row, created_at=row['created_at'].isoformat()) for row in rows]


def _approval(reference, approved_by):
    for value, maximum in ((reference, 200), (approved_by, 100)):
        if (not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError('Explicit operator and approval reference required')


def _release_leadership(conn, identifier, state, source, now):
    tick = society.clock(conn)['tick']
    rows = conn.execute('''UPDATE society_memberships SET role='member'
        WHERE agent_id=%s AND role='leader' AND left_tick IS NULL RETURNING org_id''',
        (identifier,)).fetchall()
    for row in rows:
        conn.execute('''INSERT INTO society_org_events
            (org_id,kind,subject_id,data,tick,created_at)
            VALUES (%s,'role_revoked',%s,jsonb_build_object('role','leader',
                'reason','participation_change','state',%s::text,'source',%s::text),%s,%s)''',
            (row['org_id'], identifier, state, source, tick, now))


def _record(conn, identifier, old, new, source, now, *, reference, approved_by, batch_id=None):
    conn.execute('UPDATE society_agents SET participation=%s WHERE id=%s', (new, identifier))
    conn.execute('''INSERT INTO society_participation_events
        (agent_id,from_state,to_state,source,reference,approved_by,batch_id,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
        (identifier, old, new, source, reference, approved_by, batch_id, now))
    if new in ('dormant', 'retired'):
        _release_leadership(conn, identifier, new, source, now)


def _change(conn, now, *, target_state, identifier=None, all_founders=False,
            reference, approved_by, apply=False, founders_only=True):
    from arena import operation_locks
    operation_locks.institutions(conn)
    if (type(all_founders) is not bool or type(apply) is not bool or target_state not in STATES
            or (identifier is not None and (not isinstance(identifier, str)
                or not identifier.strip() or len(identifier) > 80))
            or (identifier is not None) == all_founders):
        raise ValueError('Choose one founder or all founders and a valid participation state')
    _approval(reference, approved_by)
    now = society._utc(now)
    # Read without locking the clock: action paths use different agent/clock
    # lock order. Administrative changes do not modify society time.
    if conn.execute('SELECT 1 FROM society_clock').fetchone() is None:
        raise society.SocietyError('society_not_initialized')
    identifiers = ([row['id'] for row in conn.execute(
        "SELECT id FROM society_agents WHERE kind='founder' ORDER BY id")]
        if all_founders else [identifier])
    batch_id = uuid4().hex if all_founders else None
    changes = []
    with conn.transaction():
        if target_state != 'retired' and not founders_only:
            from arena import external_agents
            for agent in identifiers:
                external_agents.check_reactivation(conn, agent)
        for agent in identifiers:
            _lock(conn, agent)
            row = conn.execute('SELECT kind,status,participation FROM society_agents WHERE id=%s FOR UPDATE',
                               (agent,)).fetchone()
            if row is None or row['kind'] not in society.LIVING_KINDS or (founders_only and row['kind'] != 'founder'):
                raise society.SocietyError('no_such_founder' if founders_only else 'no_such_agent')
            if row['participation'] in SECURITY_STATES:
                raise society.SocietyError('security_participation_requires_existing_control')
            if row['status'] != 'alive':
                raise society.SocietyError('historical_agent_not_reactivatable')
            previous = details(conn, agent)
            changed = (row['participation'] != target_state or
                       previous['source'] != 'admin')
            if apply and changed:
                _record(conn, agent, row['participation'], target_state, 'admin', now,
                        reference=reference.strip(), approved_by=approved_by.strip(), batch_id=batch_id)
            changes.append({'agent_id': agent, 'previous_state': row['participation'],
                            'administrative_state': target_state, 'participation': target_state,
                            'source': 'admin', 'changed': changed})
    return {'applied': apply, 'target_state': target_state, 'agents': changes,
            'changed': sum(r['changed'] for r in changes), 'batch_id': batch_id,
            'reference': reference.strip(), 'approved_by': approved_by.strip()}


def change(conn, now, **kwargs):
    return _change(conn, now, founders_only=True, **kwargs)


def automatic(conn, identifier, target_state, now):
    """Founder lifecycle-only transition. Explicit administrative locks win."""
    from arena import operation_locks
    operation_locks.social(conn)
    if target_state == 'dormant':
        operation_locks.institutions(conn)
    if target_state not in ('active', 'dormant'):
        raise ValueError('invalid automatic participation state')
    _lock(conn, identifier)
    row = conn.execute('SELECT kind,participation,status FROM society_agents WHERE id=%s FOR UPDATE',
                       (identifier,)).fetchone()
    value = details(conn, identifier)
    if (row['kind'] != 'founder' or row['status'] != 'alive' or not value['history_consistent']
            or row['participation'] in ('inactive', 'retired', *SECURITY_STATES)
            or (row['participation'] == 'dormant' and value['source'] != 'auto')):
        return False
    if row['participation'] == target_state:
        return False
    _record(conn, identifier, row['participation'], target_state, 'auto', society._utc(now),
            reference='social-lifecycle', approved_by='system')
    return True


def touch(conn, identifier, now):
    """Called only for cleared successful activity within its transaction."""
    value = details(conn, identifier, lock=True)
    if (not value['history_consistent'] or value['participation'] in ('inactive', 'retired', *SECURITY_STATES)
            or (value['participation'] == 'dormant' and value['source'] != 'auto')):
        return False
    changed = automatic(conn, identifier, 'active', now)
    conn.execute('''INSERT INTO society_agent_lifecycle(agent_id,last_active_at,state,changed_at)
        VALUES (%s,%s,'active',%s) ON CONFLICT(agent_id) DO UPDATE SET
        last_active_at=GREATEST(society_agent_lifecycle.last_active_at,excluded.last_active_at),
        state='active',changed_at=CASE WHEN society_agent_lifecycle.state<>'active'
            THEN excluded.changed_at ELSE society_agent_lifecycle.changed_at END''', (identifier, now, now))
    return changed
