"""Transactional commitments and advisory cases; no inference or platform powers."""
from psycopg.types.json import Jsonb

from arena import eras, society

ACTIONS = frozenset({'commit', 'commitment_claim', 'commitment_confirm',
                     'commitment_dispute', 'commitment_release', 'case_open', 'case_entry'})


def _error(code):
    raise society.SocietyError(code)


def _agent(conn, identifier):
    row = conn.execute('SELECT id,kind FROM society_agents WHERE id=%s', (identifier,)).fetchone()
    if row is None or row['kind'] not in society.LIVING_KINDS:
        _error('no_such_agent')


def _membership(conn, org_id, agent):
    return conn.execute('''SELECT 1 FROM society_memberships m JOIN society_organizations o
        ON o.id=m.org_id WHERE m.org_id=%s AND m.agent_id=%s AND m.left_tick IS NULL
        AND o.status='active' ''', (org_id, agent)).fetchone() is not None


def forum_active(conn, org_id):
    return conn.execute('''SELECT 1 FROM society_organizations o WHERE o.id=%s AND o.status='active'
        AND EXISTS (SELECT 1 FROM society_org_events e WHERE e.org_id=o.id
            AND e.kind='decision_applied' AND e.data->>'effect'='adopt_forum')''', (org_id,)).fetchone() is not None


def _commitment(conn, identifier, actor):
    row = conn.execute('SELECT * FROM society_commitments WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
    if (row is None or actor not in (row['author_id'], row['counterparty_id'])
            or row['status'] in ('pending_clearance', 'withheld')):
        _error('commitment_unavailable')
    return row


def _case(conn, identifier, actor):
    row = conn.execute('SELECT * FROM society_cases WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
    if row is None or row['status'] != 'open':
        _error('case_unavailable')
    if actor not in [row['filed_by'], *row['subject_ids']] and not (
            row['forum_org_id'] and forum_active(conn, row['forum_org_id'])
            and _membership(conn, row['forum_org_id'], actor)):
        _error('case_not_authorized')
    return row


def _refs(conn, actor, refs):
    """IDs alone are not authorization: every referenced object must be visible."""
    for ref in refs:
        kind, identifier = ref['kind'], ref['id']
        if kind == 'post':
            visible = conn.execute(f'''SELECT 1 FROM society_posts WHERE id=%s
                AND status='published' AND NOT flagged AND created_at >= {eras.SINCE}''', (identifier,)).fetchone()
        elif kind == 'commitment':
            visible = conn.execute('''SELECT 1 FROM society_commitments WHERE id=%s
                AND status NOT IN ('pending_clearance','withheld')
                AND %s IN (author_id,counterparty_id)''', (identifier, actor)).fetchone()
        elif kind == 'proposal':
            visible = conn.execute('''SELECT 1 FROM society_proposals WHERE id=%s
                AND status NOT IN ('pending_clearance','withheld')''', (identifier,)).fetchone()
        elif kind == 'vote':
            visible = conn.execute('''SELECT 1 FROM society_votes v JOIN society_proposals p
                ON p.id=v.proposal_id WHERE v.id=%s AND p.status NOT IN ('pending_clearance','withheld')''', (identifier,)).fetchone()
        elif kind == 'org_event':
            visible = conn.execute('''SELECT 1 FROM society_org_events e JOIN society_organizations o
                ON o.id=e.org_id WHERE e.id=%s AND o.status='active' ''', (identifier,)).fetchone()
        else:
            visible = None
        if not visible:
            _error('reference_unavailable')


def _text(conn, rules, agent, tick, action, kind, identifier, text, now, key, origin, request_hash):
    from arena import institutions
    return institutions.submit_text(conn, rules, agent, tick, action, kind, identifier, text, now,
        action_key=key, origin=origin, request_hash=request_hash)


def _event(conn, identifier, kind, actor, tick, now, key=None, request_hash=None, outcome=None, refs=()):
    conn.execute('''INSERT INTO society_commitment_events
        (commitment_id,kind,actor_id,evidence,tick,created_at,action_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s)''', (identifier, kind, actor,
        Jsonb({'request_hash': request_hash, 'outcome': outcome, 'refs': list(refs)}), tick, now, key))


def receipt(conn, key):
    row = conn.execute('''SELECT actor_id,evidence->>'request_hash' AS request_hash,
        commitment_id AS id,evidence->'outcome' AS outcome FROM society_commitment_events
        WHERE action_key=%s''', (key,)).fetchone()
    if row is None:
        return None
    outcome = row['outcome'] or {'object': 'commitment', 'id': row['id'], 'state': 'open'}
    return {'actor_id': row['actor_id'], 'request_hash': row['request_hash'], **outcome}


def apply(conn, rules, agent, tick, action, now, *, action_key, origin, request_hash):
    name = action['action']
    if name == 'commit':
        target = action['counterparty_id']
        if target == agent:
            _error('own_commitment')
        _agent(conn, target)
        org_id = action.get('org_id')
        if org_id is not None and not _membership(conn, org_id, agent):
            _error('organization_not_authorized')
        identifier = conn.execute('''INSERT INTO society_commitments
            (author_id,counterparty_id,org_id,text) VALUES (%s,%s,%s,%s) RETURNING id''',
            (agent, target, org_id, action['text'])).fetchone()['id']
        _text(conn, rules, agent, tick, action, 'commitment', identifier, action['text'], now,
              action_key, origin, request_hash)
        state = conn.execute('SELECT status FROM society_commitments WHERE id=%s', (identifier,)).fetchone()['status']
        return {'object': 'commitment', 'id': identifier, 'state': state}
    if name.startswith('commitment_'):
        row = _commitment(conn, action['commitment_id'], agent)
        identifier = row['id']
        if name == 'commitment_claim':
            if agent != row['author_id'] or row['status'] != 'open':
                _error('commitment_not_authorized')
        elif name == 'commitment_confirm':
            if agent != row['counterparty_id'] or row['status'] != 'claimed':
                _error('commitment_not_authorized')
            outcome = {'object': 'commitment', 'id': identifier, 'state': 'confirmed'}
            _event(conn, identifier, 'confirmed', agent, tick, now, action_key, request_hash, outcome)
            conn.execute("UPDATE society_commitments SET status='confirmed' WHERE id=%s", (identifier,))
            return outcome
        elif name == 'commitment_release':
            if agent != row['counterparty_id'] or row['status'] not in ('open', 'claimed', 'disputed'):
                _error('commitment_not_authorized')
            outcome = {'object': 'commitment', 'id': identifier, 'state': 'released'}
            _event(conn, identifier, 'released', agent, tick, now, action_key, request_hash, outcome)
            conn.execute("UPDATE society_commitments SET status='released' WHERE id=%s", (identifier,))
            return outcome
        elif row['status'] not in ('open', 'claimed'):
            _error('commitment_not_authorized')
        _refs(conn, agent, action['evidence_refs'])
        if action.get('forum_org_id') and not forum_active(conn, action['forum_org_id']):
            _error('forum_unavailable')
        _text(conn, rules, agent, tick, action, 'commitment', identifier, action['note'], now,
              action_key, origin, request_hash)
        state = conn.execute('SELECT status FROM society_commitments WHERE id=%s', (identifier,)).fetchone()['status']
        return {'object': 'commitment', 'id': identifier, 'state': state}
    if name == 'case_open':
        for subject in action['subject_ids']:
            _agent(conn, subject)
        if action.get('forum_org_id') and not forum_active(conn, action['forum_org_id']):
            _error('forum_unavailable')
        _refs(conn, agent, action['refs'])
        identifier = conn.execute('''INSERT INTO society_cases(forum_org_id,filed_by,subject_ids,claim_text)
            VALUES (%s,%s,%s,%s) RETURNING id''',
            (action.get('forum_org_id'), agent, action['subject_ids'], action['claim'])).fetchone()['id']
        _text(conn, rules, agent, tick, action, 'case', identifier, action['claim'], now,
              action_key, origin, request_hash)
        state = conn.execute('SELECT status FROM society_cases WHERE id=%s', (identifier,)).fetchone()['status']
        return {'object': 'case', 'id': identifier, 'state': state}
    if name == 'case_entry':
        _case(conn, action['case_id'], agent)
        _refs(conn, agent, action['refs'])
        identifier = conn.execute('''INSERT INTO society_case_entries
            (case_id,actor_id,kind,text,refs,tick,created_at,action_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
            (action['case_id'], agent, action['kind'], action['text'], Jsonb(action['refs']), tick, now, action_key)).fetchone()['id']
        _text(conn, rules, agent, tick, action, 'case_entry', identifier, action['text'], now,
              action_key, origin, request_hash)
        state = conn.execute('SELECT clearance FROM society_case_entries WHERE id=%s', (identifier,)).fetchone()['clearance']
        return {'object': 'case_entry', 'id': identifier, 'state': state}
    _error('institution_not_implemented')


def _rebuild_commitment(conn, identifier, tick, override=None):
    links = conn.execute('''SELECT * FROM society_institution_texts
        WHERE object_kind='commitment' AND object_id=%s ORDER BY post_id''', (identifier,)).fetchall()
    cleared = {r['action_key']: (override[1] if override and r['post_id'] == override[0]
                               else r['clearance'] == 'cleared') for r in links}
    original = next((r for r in links if r['action'] == 'commit'), None)
    if original is None or not cleared.get(original['action_key']):
        state = 'withheld' if original and original['clearance'] == 'withheld' else 'pending_clearance'
        if override and original and original['post_id'] == override[0]:
            state = 'withheld' if not override[1] else state
        conn.execute('UPDATE society_commitments SET status=%s WHERE id=%s', (state, identifier))
        return
    state = 'open'
    for event in conn.execute('SELECT * FROM society_commitment_events WHERE commitment_id=%s ORDER BY id', (identifier,)):
        if event['action_key'] in cleared and not cleared[event['action_key']]:
            continue
        if event['kind'] == 'claimed_fulfilled' and state == 'open':
            state = 'claimed'
        elif event['kind'] == 'confirmed':
            # Explicit counterparty confirmation is a standing fact, not an
            # implicit assertion that the claim's prose remains publicly available.
            state = 'confirmed'
        elif event['kind'] == 'disputed' and state in ('open', 'claimed'):
            state = 'disputed'
        elif event['kind'] == 'released':
            state = 'released'
        elif event['kind'] == 'expired' and state in ('open', 'claimed'):
            state = 'expired'
    conn.execute('UPDATE society_commitments SET status=%s WHERE id=%s', (state, identifier))


def on_text_status(conn, rules, link, status, cleared, now):
    tick = society.clock(conn)['tick']
    action = link['intent'].get('action', {})
    name, identifier = link['action'], link['object_id']
    if name == 'commit':
        row = conn.execute('SELECT * FROM society_commitments WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
        if cleared:
            if row['due_tick'] is None:
                conn.execute('UPDATE society_commitments SET due_tick=%s WHERE id=%s',
                             (tick + action['due_in_ticks'], identifier))
            if not conn.execute('SELECT 1 FROM society_commitment_events WHERE action_key=%s', (link['action_key'],)).fetchone():
                _event(conn, identifier, 'created', link['actor_id'], tick, now, link['action_key'], link['request_hash'])
        _rebuild_commitment(conn, identifier, tick, (link['post_id'], cleared))
    elif name in ('commitment_claim', 'commitment_dispute'):
        row = conn.execute('SELECT * FROM society_commitments WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
        if cleared and not conn.execute('SELECT 1 FROM society_commitment_events WHERE action_key=%s', (link['action_key'],)).fetchone():
            expected = 'open' if name == 'commitment_claim' else None
            if row['status'] in ('pending_clearance', 'withheld', 'confirmed', 'released', 'expired') or (expected and row['status'] != expected):
                _error('commitment_state_changed')
            kind = 'claimed_fulfilled' if name == 'commitment_claim' else 'disputed'
            _event(conn, identifier, kind, link['actor_id'], tick, now, link['action_key'], link['request_hash'],
                   refs=action['evidence_refs'])
            if name == 'commitment_dispute' and action.get('forum_org_id'):
                if not forum_active(conn, action['forum_org_id']):
                    _error('forum_unavailable')
                case = conn.execute('''INSERT INTO society_cases
                    (forum_org_id,filed_by,subject_ids,claim_text,status,opened_tick,outcome)
                    VALUES (%s,%s,%s,%s,'open',%s,%s) RETURNING id''',
                    (action['forum_org_id'], link['actor_id'],
                     [r for r in (row['author_id'], row['counterparty_id']) if r != link['actor_id']],
                     action['note'], tick, Jsonb({'source_commitment': identifier, 'source_post_id': link['post_id']}))).fetchone()['id']
                conn.execute('''INSERT INTO society_case_entries(case_id,actor_id,kind,text,refs,clearance,tick,created_at)
                    VALUES (%s,%s,'statement',%s,%s,'cleared',%s,%s)''',
                    (case, link['actor_id'], action['note'], Jsonb(action['evidence_refs']), tick, now))
        _rebuild_commitment(conn, identifier, tick, (link['post_id'], cleared))
    elif name == 'case_open':
        row = conn.execute('SELECT * FROM society_cases WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
        state = 'closed' if cleared and (row['outcome'] or {}).get('ruling') else 'open' if cleared else 'withheld' if status == 'withheld' else 'pending_clearance'
        conn.execute('UPDATE society_cases SET status=%s,opened_tick=COALESCE(opened_tick,%s) WHERE id=%s',
                     (state, tick if cleared else None, identifier))
    elif name == 'case_entry':
        row = conn.execute('SELECT case_id FROM society_case_entries WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
        if cleared:
            _case(conn, row['case_id'], link['actor_id'])
        conn.execute('UPDATE society_case_entries SET clearance=%s WHERE id=%s',
                     ('cleared' if cleared else 'withheld' if status == 'withheld' else 'pending', identifier))


def apply_ruling(conn, rules, forum_org_id, payload, tick, now, *, proposal_id):
    """Advisory finding only: never changes platform rights or forces compliance."""
    if not forum_active(conn, forum_org_id):
        _error('forum_unavailable')
    row = conn.execute('SELECT * FROM society_cases WHERE id=%s FOR UPDATE', (payload['case_id'],)).fetchone()
    if row is None or row['status'] != 'open' or row['forum_org_id'] != forum_org_id:
        _error('case_outside_forum')
    finding = payload['finding']
    remedy = payload['remedy']
    if not isinstance(finding, str) or not isinstance(remedy, str) or len(finding) > 280 or len(remedy) > 280:
        _error('invalid_case_ruling')
    outcome = dict(row['outcome'] or {}, ruling={'proposal_id': proposal_id, 'finding': finding, 'remedy': remedy},
                   accepted_by=[])  # No public consent action exists; never infer consent.
    conn.execute("UPDATE society_cases SET status='closed',outcome=%s,closed_tick=%s WHERE id=%s",
                 (Jsonb(outcome), tick, row['id']))
    return {'case_id': row['id'], 'state': 'closed'}


def close_due(conn, rules, now):
    tick = society.clock(conn)['tick']
    rows = conn.execute("SELECT id FROM society_commitments WHERE status IN ('open','claimed') AND due_tick<=%s ORDER BY id FOR UPDATE", (tick,)).fetchall()
    for row in rows:
        _event(conn, row['id'], 'expired', None, tick, now)
        conn.execute("UPDATE society_commitments SET status='expired' WHERE id=%s", (row['id'],))
    return {'expired': len(rows)}
