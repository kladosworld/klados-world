"""Explicit administrative availability, separate from dormancy and security.

No startup refresh, automatic retirement, identity rewrite or society pause. The
operator CLI is private; no public or agent-authenticated mutation endpoint exists.
Callers own transactions. An absent projection means historically active.
"""
from arena import activity_limits, society

STATES = ('active', 'inactive', 'retired')


def state(conn, agent_id):
    row = conn.execute('SELECT state FROM society_agent_administration WHERE agent_id=%s',
                       (agent_id,)).fetchone()
    return row['state'] if row else 'active'


def is_active(conn, agent_id, *, lock=False):
    """Administrative gate only; never substitutes for security or lifecycle checks."""
    if lock:
        conn.execute('SELECT pg_advisory_xact_lock(%s,hashtext(%s))',
                     (activity_limits.LOCK_NAMESPACE, agent_id))
    return state(conn, agent_id) == 'active'


def describe(conn, agent_id):
    agent = conn.execute('SELECT id,kind,status,participation FROM society_agents WHERE id=%s',
                         (agent_id,)).fetchone()
    if agent is None or agent['kind'] not in society.LIVING_KINDS:
        raise society.SocietyError('no_such_agent')
    administrative_state = state(conn, agent_id)
    return dict(agent_id=agent_id, administrative_state=administrative_state,
                participation=agent['participation'], lifecycle_status=agent['status'],
                can_participate=(administrative_state == 'active' and agent['status'] == 'alive'
                                 and agent['participation'] == 'active'))


def change(conn, now, *, target_state, identifier=None, all_founders=False,
           reference, approved_by, apply=False):
    """Preview or atomic explicit transition; unchanged states produce no audit copy.

    Administrative reactivation never changes suspended/revoked participation,
    security tokens, historical death, dormant state, or past memories/roles.
    """
    if (type(all_founders) is not bool or type(apply) is not bool
            or target_state not in STATES or (identifier is not None and
                (not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 80))
            or (identifier is not None) == all_founders):
        raise ValueError('Choose one agent or all founders and a valid administrative state')
    if (not isinstance(reference, str) or not 1 <= len(reference.strip()) <= 200
            or not isinstance(approved_by, str) or not 1 <= len(approved_by.strip()) <= 100):
        raise ValueError('Explicit operator and approval reference required')
    now = society._utc(now)
    # No clock lock: external actions take agent -> clock, while founder batch
    # takes clock -> agent. This operation does not edit the clock, so taking it
    # would introduce a cross-path lock inversion. Agent locks serialize effects.
    if all_founders:
        agents = conn.execute("SELECT id FROM society_agents WHERE kind='founder' ORDER BY id").fetchall()
        identifiers = [row['id'] for row in agents]
    else:
        identifiers = [identifier]
    changes = []
    for agent_id in identifiers:
        conn.execute('SELECT pg_advisory_xact_lock(%s,hashtext(%s))',
                     (activity_limits.LOCK_NAMESPACE, agent_id))
        agent = conn.execute('SELECT id,kind FROM society_agents WHERE id=%s FOR UPDATE',
                             (agent_id,)).fetchone()
        if agent is None or agent['kind'] not in society.LIVING_KINDS:
            raise society.SocietyError('no_such_agent')
        previous = state(conn, agent_id)
        changed = previous != target_state
        if apply and changed:
            conn.execute('''INSERT INTO society_agent_administration(agent_id,state,changed_at)
                VALUES (%s,%s,%s) ON CONFLICT(agent_id) DO UPDATE
                SET state=EXCLUDED.state,changed_at=EXCLUDED.changed_at''',
                (agent_id, target_state, now))
            conn.execute('''INSERT INTO society_administration_events
                (agent_id,previous_state,state,reference,approved_by,created_at)
                VALUES (%s,%s,%s,%s,%s,%s)''',
                (agent_id, previous, target_state, reference.strip(), approved_by.strip(), now))
        changes.append(dict(agent_id=agent_id, previous_state=previous,
                            administrative_state=target_state, changed=changed))
    return dict(applied=apply, target_state=target_state, agents=changes,
                changed=sum(item['changed'] for item in changes),
                reference=reference.strip(), approved_by=approved_by.strip())
