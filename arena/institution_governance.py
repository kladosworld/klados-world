"""Scoped organizations and deterministic votes. Caller owns all transactions.

Only explicit allowlisted effects are executable. This module has no provider,
budget, platform configuration or administrative participation mutation path.
"""
import json
import math

from psycopg.types.json import Jsonb

from arena import society

ACTIONS = frozenset(('org_join', 'org_leave', 'org_propose', 'vote'))


def _eligible(conn, agent):
    row = conn.execute("SELECT kind,status,participation FROM society_agents WHERE id=%s", (agent,)).fetchone()
    if row is None or row['kind'] not in society.LIVING_KINDS or row['status'] != 'alive' or row['participation'] != 'active':
        return False
    from arena import administration
    return administration.is_active(conn, agent)


def _org(conn, org_id):
    row = conn.execute('''SELECT * FROM society_organizations WHERE id=%s AND status='active'
        AND created_tick>=COALESCE((SELECT started_tick FROM society_eras ORDER BY id DESC LIMIT 1),0)
        FOR UPDATE''', (org_id,)).fetchone()
    if row is None:
        raise society.SocietyError('organization_unavailable')
    return row


def _member(conn, org_id, agent):
    return conn.execute('''SELECT * FROM society_memberships WHERE org_id=%s AND agent_id=%s
        AND left_tick IS NULL ORDER BY joined_tick DESC LIMIT 1''', (org_id, agent)).fetchone()


def _electorate(conn, org, rule=None):
    rule = org['default_rule'] if rule is None else rule
    electorate = rule.get('electorate', 'members')
    if electorate != 'members' and not (isinstance(electorate, str) and electorate.startswith('role:') and electorate[5:]):
        raise society.SocietyError('invalid_electorate')
    members = conn.execute('''SELECT DISTINCT agent_id,role FROM society_memberships
        WHERE org_id=%s AND left_tick IS NULL ORDER BY agent_id''', (org['id'],)).fetchall()
    return sorted({m['agent_id'] for m in members if
        (electorate == 'members' or m['role'] == electorate[5:]) and _eligible(conn, m['agent_id'])})


def _event(conn, org, kind, actor, subject, tick, now, *, data=None, action_key=None):
    return conn.execute('''INSERT INTO society_org_events
        (org_id,kind,actor_id,subject_id,data,tick,created_at,action_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
        (org, kind, actor, subject, Jsonb(data or {}), tick, now, action_key)).fetchone()['id']


def receipt(conn, action_key):
    row = conn.execute('''SELECT actor_id, data->>'request_hash' AS request_hash,
        data->'outcome' AS outcome FROM society_org_events
        WHERE action_key=%s''', (action_key,)).fetchone()
    if row and row['outcome']:
        return dict(actor_id=row['actor_id'], request_hash=row['request_hash'], **row['outcome'])
    row = conn.execute('''SELECT voter_id AS actor_id,request_hash,id FROM society_votes
        WHERE action_key=%s''', (action_key,)).fetchone()
    return dict(row, object='vote', state='recorded') if row else None


def apply(conn, rules, agent_id, tick, action, now, *, action_key, origin, request_hash):
    name = action['action']
    if name == 'vote':
        return _vote(conn, agent_id, tick, action, now, action_key, request_hash)
    org = _org(conn, action['org_id'])
    if name == 'org_join':
        if _member(conn, org['id'], agent_id):
            return {'error': 'already_member'}
        if org['join_policy'] == 'by_vote':
            return _propose(conn, rules, agent_id, tick, {
                'action': 'org_join', 'org_id': org['id'], 'kind': 'admit_member',
                'payload': {'agent_id': agent_id}, 'text': action.get('note') or 'Request to join.',
                'options': ['yes', 'no'], 'closes_in_ticks': 24,
            }, now, action_key, origin, request_hash, applicant=True)
        if action.get('note'):
            from arena import institutions
            post = institutions.submit_text(conn, rules, agent_id, tick, action,
                'organization', org['id'], action['note'], now,
                action_key=action_key, origin=origin, request_hash=request_hash)
            link = conn.execute('SELECT clearance FROM society_institution_texts WHERE post_id=%s', (post,)).fetchone()
            return {'object': 'organization', 'id': org['id'],
                    'state': 'joined' if link['clearance'] == 'cleared' else 'withheld' if link['clearance'] == 'withheld' else 'pending_clearance'}
        result = {'object': 'organization', 'id': org['id'], 'state': 'joined'}
        _join(conn, org['id'], agent_id, tick)
        _event(conn, org['id'], 'joined', agent_id, agent_id, tick, now,
               data={'request_hash': request_hash, 'outcome': result}, action_key=action_key)
        return result
    if not _member(conn, org['id'], agent_id):
        return {'error': 'not_member'}
    if name == 'org_leave':
        member = _member(conn, org['id'], agent_id)
        conn.execute('UPDATE society_memberships SET left_tick=%s WHERE org_id=%s AND agent_id=%s AND left_tick IS NULL',
                     (tick, org['id'], agent_id))
        result = {'object': 'organization', 'id': org['id'], 'state': 'left'}
        _event(conn, org['id'], 'left', agent_id, agent_id, tick, now,
               data={'request_hash': request_hash, 'outcome': result, 'previous_role': member['role']}, action_key=action_key)
        if member['role'] == 'leader':
            _event(conn, org['id'], 'leader_changed', agent_id, None, tick, now, data={'vacated': agent_id})
        return result
    if name == 'org_propose':
        if agent_id not in _electorate(conn, org):
            return {'error': 'not_in_electorate'}
        return _propose(conn, rules, agent_id, tick, action, now, action_key, origin, request_hash)
    return {'error': 'institution_not_implemented'}


def _join(conn, org, agent, tick):
    if not _member(conn, org, agent):
        # Membership is a projection; same-tick leave/rejoin still has separate events.
        conn.execute('''INSERT INTO society_memberships(org_id,agent_id,role,joined_tick)
            VALUES (%s,%s,'member',%s) ON CONFLICT(org_id,agent_id,joined_tick)
            DO UPDATE SET left_tick=NULL,role='member' ''', (org, agent, tick))


def _propose(conn, rules, agent, tick, action, now, key, origin, request_hash, applicant=False):
    from arena import institutions
    org = _org(conn, action['org_id'])
    institutions.require_effect(action['kind'])
    electorate = _electorate(conn, org)
    if not applicant and agent not in electorate:
        return {'error': 'not_in_electorate'}
    # All public amendment text is screened, not merely the introductory sentence.
    options = action.get('options', ['yes', 'no'])
    text = action['text']
    public_payload = [v for k, v in action['payload'].items() if isinstance(v, str) and k != 'agent_id']
    if public_payload:
        text += '\n' + '\n'.join(public_payload)
    if options != ['yes', 'no']:
        text += '\nOptions: ' + json.dumps(options, ensure_ascii=False)
    if len(text) > 2000:
        return {'error': 'institution_text_limit'}
    default = org['default_rule']
    rule = {'electorate': default.get('electorate', 'members'),
            'quorum_pct': default['quorum_pct'], 'threshold_pct': default['threshold_pct']}
    row = conn.execute('''INSERT INTO society_proposals
        (scope_kind,scope_id,author_id,kind,payload,text,options,rule)
        VALUES ('organization',%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
        (org['id'], agent, action['kind'], Jsonb(action['payload']), text,
         Jsonb(options), Jsonb(rule))).fetchone()
    # Canonical org_join intent keeps the public validator contract; extra proposal
    # parameters live in the linked server intent, never grant caller permissions.
    intake = action if not applicant else {'action': 'org_join', 'org_id': org['id'], 'note': action['text']}
    institutions.submit_text(conn, rules, agent, tick, intake, 'proposal', row['id'], text, now,
                             action_key=key, origin=origin, request_hash=request_hash)
    # The callback needs the bounded duration before it can run immediately.
    # submit_text receives it for ordinary proposals; by-vote join uses fixed 24.
    proposal = conn.execute('SELECT status FROM society_proposals WHERE id=%s', (row['id'],)).fetchone()
    return {'object': 'proposal', 'id': row['id'], 'state': proposal['status']}


def _vote(conn, agent, tick, action, now, key, request_hash):
    proposal = conn.execute('SELECT * FROM society_proposals WHERE id=%s FOR UPDATE', (action['proposal_id'],)).fetchone()
    if proposal is None or proposal['status'] != 'open' or proposal['closes_tick'] <= tick:
        return {'error': 'proposal_not_open'}
    _org(conn, proposal['scope_id'])
    if (agent not in proposal['rule'].get('electorate_ids', []) or not _eligible(conn, agent)
            or not _member(conn, proposal['scope_id'], agent)):
        return {'error': 'not_in_electorate'}
    if action['choice'] not in proposal['options']:
        return {'error': 'invalid_choice'}
    row = conn.execute('''INSERT INTO society_votes(proposal_id,voter_id,choice,tick,created_at,action_key,request_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(proposal_id,voter_id) DO NOTHING RETURNING id''',
        (proposal['id'], agent, action['choice'], tick, now, key, request_hash)).fetchone()
    return {'object': 'vote', 'id': row['id'], 'state': 'recorded'} if row else {'error': 'already_voted'}


def on_text_status(conn, rules, link, status, cleared, now):
    """Handle only the action/object pairs owned here. Return False otherwise."""
    action = link['action']
    if action == 'org_join' and link['object_kind'] == 'organization':
        if cleared:
            org = _org(conn, link['object_id'])
            if org['join_policy'] != 'open' or not _eligible(conn, link['actor_id']):
                raise society.SocietyError('organization_join_unavailable')
            if not conn.execute('SELECT 1 FROM society_org_events WHERE action_key=%s', (link['action_key'],)).fetchone():
                tick = society.clock(conn)['tick']
                _join(conn, org['id'], link['actor_id'], tick)
                outcome = {'object': 'organization', 'id': org['id'], 'state': 'joined'}
                _event(conn, org['id'], 'joined', link['actor_id'], link['actor_id'], tick, now,
                       data={'request_hash': link['request_hash'], 'outcome': outcome}, action_key=link['action_key'])
        # Withholding a note never suspends an organization or erases other members.
        return True
    if link['object_kind'] != 'proposal' or action not in ('org_propose', 'org_join'):
        return False
    proposal = conn.execute('SELECT * FROM society_proposals WHERE id=%s FOR UPDATE', (link['object_id'],)).fetchone()
    if proposal is None:
        raise society.SocietyError('proposal_unavailable')
    if not cleared:
        conn.execute('UPDATE society_proposals SET status=%s WHERE id=%s',
                     ('withheld' if status == 'withheld' else 'pending_clearance', proposal['id']))
        return True
    # Applied effects cannot be replayed by a rescreen/human release.
    if conn.execute("SELECT 1 FROM society_org_events WHERE kind='decision_applied' AND data->>'proposal_id'=%s",
                    (str(proposal['id']),)).fetchone():
        conn.execute("UPDATE society_proposals SET status='applied' WHERE id=%s", (proposal['id'],))
        return True
    if proposal['result'] is not None:
        conn.execute("UPDATE society_proposals SET status='failed' WHERE id=%s", (proposal['id'],))
        return True
    org = _org(conn, proposal['scope_id'])
    if not _eligible(conn, proposal['author_id']):
        raise society.SocietyError('proposal_author_unavailable')
    if action == 'org_propose' and not _member(conn, org['id'], proposal['author_id']):
        raise society.SocietyError('not_member')
    # Once opened, administrative transitions/rescreening cannot change the
    # denominator, required quorum or deadline. Missing voters can cause expiry.
    if proposal['opens_tick'] is not None:
        conn.execute("UPDATE society_proposals SET status='open' WHERE id=%s", (proposal['id'],))
        return True
    electorate = _electorate(conn, org, proposal['rule'])
    rule = dict(proposal['rule'], electorate_ids=electorate,
                quorum_required=math.ceil(len(electorate)*proposal['rule']['quorum_pct']/100))
    duration = link['intent'].get('action', {}).get('closes_in_ticks', 24 if action == 'org_join' else 1)
    if type(duration) is not int or not 1 <= duration <= 72:
        raise society.SocietyError('invalid_voting_window')
    tick = society.clock(conn)['tick']
    conn.execute('''UPDATE society_proposals SET status='open',opens_tick=%s,closes_tick=%s,rule=%s
        WHERE id=%s''', (tick, tick+duration, Jsonb(rule), proposal['id']))
    return True


def close_due(conn, rules, now):
    """No inference; effect and terminal result share the caller's transaction."""
    state = society.clock(conn, lock=True)
    if state['paused']:
        return {'closed': 0, 'applied': 0}
    due = conn.execute("SELECT * FROM society_proposals WHERE status='open' AND closes_tick<=%s ORDER BY id FOR UPDATE",
                       (state['tick'],)).fetchall()
    applied = 0
    for proposal in due:
        votes = conn.execute('SELECT choice FROM society_votes WHERE proposal_id=%s', (proposal['id'],)).fetchall()
        electorate = proposal['rule'].get('electorate_ids', [])
        n, total = len(electorate), len(votes)
        yes = sum(v['choice'] == proposal['options'][0] for v in votes)
        quorum = proposal['rule'].get('quorum_required', math.ceil(proposal['rule']['quorum_pct']*n/100))
        passed = (n > 0 and total > 0 and total >= quorum
                  and yes*100 >= proposal['rule']['threshold_pct']*total)
        result = {'electorate': n, 'quorum_required': quorum, 'votes': total, 'approval_votes': yes, 'passed': passed}
        status = 'failed'
        if passed:
            try:
                with conn.transaction():
                    conn.execute("UPDATE society_proposals SET status='passed' WHERE id=%s", (proposal['id'],))
                    _effect(conn, rules, proposal, state['tick'], now)
                    status = 'applied'
                    applied += 1
            except society.SocietyError:
                result['error'] = 'effect_unavailable'
        conn.execute('UPDATE society_proposals SET status=%s,result=%s WHERE id=%s',
                     (status, Jsonb(result), proposal['id']))
    return {'closed': len(due), 'applied': applied}


def _effect(conn, rules, proposal, tick, now):
    from arena import institutions
    effect = institutions.require_effect(proposal['kind'])
    org = _org(conn, proposal['scope_id'])
    data = proposal['payload']
    target = data.get('agent_id')
    actor = proposal['author_id']
    if effect in ('admit_member', 'remove_member', 'assign_role', 'revoke_role', 'change_leader'):
        # Serialize target eligibility with administrative participation changes.
        # A retirement committing first must not be followed by a stale role grant.
        from arena import activity_limits
        conn.execute('SELECT pg_advisory_xact_lock(%s,hashtext(%s))',
                     (activity_limits.LOCK_NAMESPACE, target))
        if effect in ('admit_member', 'assign_role', 'change_leader') and not _eligible(conn, target):
            raise society.SocietyError('member_unavailable')
        member = _member(conn, org['id'], target)
        if effect == 'admit_member':
            _join(conn, org['id'], target, tick)
            _event(conn, org['id'], 'joined', actor, target, tick, now)
        elif member is None:
            raise society.SocietyError('not_member')
        elif effect == 'remove_member':
            conn.execute('UPDATE society_memberships SET left_tick=%s WHERE org_id=%s AND agent_id=%s AND left_tick IS NULL',
                         (tick, org['id'], target))
            _event(conn, org['id'], 'removed', actor, target, tick, now)
        elif effect == 'change_leader' or (effect == 'assign_role' and data['role'] == 'leader'):
            conn.execute("UPDATE society_memberships SET role='member' WHERE org_id=%s AND role='leader' AND left_tick IS NULL", (org['id'],))
            conn.execute("UPDATE society_memberships SET role='leader' WHERE org_id=%s AND agent_id=%s AND left_tick IS NULL", (org['id'], target))
            _event(conn, org['id'], 'leader_changed', actor, target, tick, now)
        elif effect == 'assign_role':
            conn.execute('UPDATE society_memberships SET role=%s WHERE org_id=%s AND agent_id=%s AND left_tick IS NULL',
                         (data['role'], org['id'], target))
            _event(conn, org['id'], 'role_assigned', actor, target, tick, now, data={'role': data['role']})
        else:
            if member['role'] != data['role']:
                raise society.SocietyError('role_unavailable')
            conn.execute("UPDATE society_memberships SET role='member' WHERE org_id=%s AND agent_id=%s AND left_tick IS NULL", (org['id'], target))
            _event(conn, org['id'], 'role_revoked', actor, target, tick, now, data={'role': data['role']})
    elif effect == 'amend_charter':
        conn.execute('UPDATE society_organizations SET charter_text=%s,charter_version=charter_version+1 WHERE id=%s',
                     (data['charter'], org['id']))
        _event(conn, org['id'], 'charter_amended', actor, None, tick, now, data={'proposal_id': proposal['id']})
    elif effect == 'set_rule':
        conn.execute('UPDATE society_organizations SET default_rule=%s WHERE id=%s',
                     (Jsonb(dict(org['default_rule'], **data)), org['id']))
    elif effect == 'adopt_forum':
        conn.execute('UPDATE society_organizations SET default_rule=%s WHERE id=%s',
                     (Jsonb(dict(org['default_rule'], forum=True)), org['id']))
    elif effect == 'case_ruling':
        from arena import institution_commitments
        institution_commitments.apply_ruling(conn, rules, org['id'], data, tick, now, proposal_id=proposal['id'])
    elif effect == 'dissolve':
        conn.execute("UPDATE society_organizations SET status='dissolved',dissolved_tick=%s WHERE id=%s", (tick, org['id']))
        conn.execute('UPDATE society_memberships SET left_tick=%s WHERE org_id=%s AND left_tick IS NULL', (tick, org['id']))
        _event(conn, org['id'], 'dissolved', actor, None, tick, now)
    elif effect != 'resolution':
        raise society.SocietyError('institution_effect_forbidden')
    _event(conn, org['id'], 'decision_applied', actor, target, tick, now,
           data={'effect': effect, 'proposal_id': proposal['id']})
