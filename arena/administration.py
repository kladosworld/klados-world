"""Compatibility API for canonical participation; no administrative state table."""
from arena import founder_status

STATES = founder_status.STATES

def state(conn, agent_id):
    return founder_status.details(conn, agent_id)['participation']

def is_active(conn, agent_id, *, lock=False):
    return founder_status.may_act(conn, agent_id, lock=lock)

def describe(conn, agent_id):
    result = founder_status.details(conn, agent_id)
    # External social dormancy is a public label, not an authentication lock.
    if result['kind'] == 'external' and result['participation'] == 'active':
        social = conn.execute('SELECT state FROM society_agent_lifecycle WHERE agent_id=%s',
                              (agent_id,)).fetchone()
        if social and social['state'] == 'dormant':
            result = dict(result, participation_label='dormant', participation_source='auto')
    return dict(result, administrative_state=result['participation'])

def change(conn, now, **kwargs):
    # Caller is an existing private operator or authenticated-owner path. Security
    # suspension/revocation is never cleared by this compatibility operation.
    return founder_status._change(conn, now, founders_only=False, **kwargs)
