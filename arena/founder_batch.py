"""Durable founder decisions. One paid decision, one atomic batch with per-item savepoints."""
from psycopg.types.json import Jsonb

from arena import society, society_life


def save(conn, agent_id, tick, decision, settings, charged, origin):
    private = {'batch_version': 1, 'phase': 'ready', 'decision': decision,
               'settings': settings, 'origin': origin}
    changed = conn.execute('''UPDATE society_turns SET private=%s, charged_microusd=%s
        WHERE agent_id=%s AND tick=%s AND status='claimed' AND private='{}'::jsonb
        RETURNING agent_id''', (Jsonb(private), charged, agent_id, tick)).fetchone()
    if changed is None:
        raise society.SocietyError('turn_not_claimed')


def execute(conn, rules, agent_id, tick, now):
    """Caller commits outcomes and effects together. Never calls an inference provider."""
    clock = society.clock(conn, lock=True)
    turn = conn.execute('''SELECT * FROM society_turns WHERE agent_id=%s AND tick=%s
        FOR UPDATE''', (agent_id, tick)).fetchone()
    if turn is None or turn['status'] != 'claimed' or turn['private'].get('phase') != 'ready':
        return None
    private = dict(turn['private'])
    if clock['paused']:
        return None
    if clock['tick'] != tick:
        private.update(phase='expired', error='tick_expired')
        conn.execute('''UPDATE society_turns SET status='failed', action='rest', private=%s
            WHERE agent_id=%s AND tick=%s''', (Jsonb(private), agent_id, tick))
        return 'failed'
    limits = private['settings']['values']
    try:
        society_life._alive_agent(conn, agent_id, acting=True)
    except society.SocietyError:
        private.update(phase='expired', error='participation_unavailable')
        conn.execute('''UPDATE society_turns SET status='failed', action='rest', private=%s
            WHERE agent_id=%s AND tick=%s''', (Jsonb(private), agent_id, tick))
        return 'failed'
    outcomes = []
    for index, action in enumerate(private['decision']['actions']):
        if 'invalid' in action:
            outcomes.append({'action': 'rest', 'error': 'reply_' + action['invalid']})
        else:
            outcomes.append(society_life.apply_action(conn, rules, agent_id, tick, action, now,
                action_key=f'turn:{agent_id}:{tick}:{index}', post_origin=private['origin'],
                limits={name: limits[name] for name in ('post', 'reply', 'like', 'institution', 'vote')
                        if name in limits}))
    # A single envelope note, not one copy for each action. Verify participation even for []/notes.
    note = private['decision'].get('note')
    if note:
        conn.execute('INSERT INTO society_notes (agent_id,tick,text,created_at) VALUES (%s,%s,%s,%s)',
                     (agent_id, tick, note, now))
    private.update(phase='done', outcomes=outcomes)
    conn.execute('''UPDATE society_turns SET status='done', action=%s, private=%s
        WHERE agent_id=%s AND tick=%s''',
        ('batch' if outcomes else 'rest', Jsonb(private), agent_id, tick))
    return 'done'


def recoverable(conn):
    return [(row['agent_id'], row['tick']) for row in conn.execute('''SELECT agent_id,tick
        FROM society_turns WHERE status='claimed' AND private->>'phase'='ready'
        ORDER BY tick,agent_id''')]
