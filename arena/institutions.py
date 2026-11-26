"""Institution contract boundary. Caller owns the transaction; no paid calls.

W0 implements initial charter intake/clearance and private trust. W1/W2 supply the
other executors. Unsupported effects/actions fail closed, never pretend success.
"""
import hashlib
import json

from psycopg.types.json import Jsonb

from arena import activity_limits, activity_settings, society
from tee_arena.society import decisions

EFFECT_ALLOWLIST = frozenset(decisions.PROPOSAL_EFFECTS)
ACTIONS = frozenset(decisions.INSTITUTION_ACTIONS)


def require_effect(kind):
    """Scope gate for W1: never dynamically dispatch arbitrary names or payload SQL."""
    if kind not in EFFECT_ALLOWLIST:
        raise society.SocietyError('institution_effect_forbidden')
    return kind


def _hash(action):
    return hashlib.sha256(json.dumps(action, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _active(conn, agent_id):
    from arena import administration
    row = conn.execute('SELECT kind,status,participation FROM society_agents WHERE id=%s',
                       (agent_id,)).fetchone()
    if row is None or row['kind'] not in society.LIVING_KINDS or row['status'] != 'alive' or row['participation'] != 'active':
        raise society.SocietyError('not_alive')
    if not administration.is_active(conn, agent_id, lock=True):
        raise society.SocietyError('not_alive')


def _receipt(conn, action_key):
    return conn.execute('''SELECT i.actor_id,i.request_hash,i.object_kind AS object,i.object_id AS id,
        CASE WHEN i.object_kind='organization' AND i.action='org_found' THEN o.status ELSE i.clearance END AS state
        FROM society_institution_texts i LEFT JOIN society_organizations o
        ON i.object_kind='organization' AND o.id=i.object_id WHERE i.action_key=%s
        UNION ALL SELECT from_id,request_hash,'trust',id,'private'
        FROM society_trust WHERE action_key=%s''', (action_key, action_key)).fetchone()


def apply(conn, rules, agent_id, tick, action, now, *, action_key, origin, limits):
    """Validated, per-agent serialized, idempotent executor contract for all streams."""
    try:
        action = decisions.validate_institution(action)
    except decisions.DecisionError:
        return {'error': 'invalid_institution_action'}
    if not rules.persistent:
        return {'error': 'institutions_require_rules_v2'}
    if not isinstance(action_key, str) or not 1 <= len(action_key) <= 200:
        return {'error': 'invalid_action_key'}
    # Same ordering as ordinary actions. Explicit action identity lock also prevents
    # cross-agent reuse of one key racing across the two receipt tables.
    conn.execute('SELECT pg_advisory_xact_lock(%s,hashtext(%s))',
                 (activity_limits.LOCK_NAMESPACE, agent_id))
    conn.execute('SELECT pg_advisory_xact_lock(%s,hashtext(%s))', (73423209, action_key))
    _active(conn, agent_id)
    previous = _receipt(conn, action_key)
    request_hash = _hash(action)
    if previous:
        if previous['actor_id'] != agent_id or previous['request_hash'] != request_hash:
            return {'error': 'idempotency_conflict'}
        return {key: previous[key] for key in ('object', 'id', 'state')}
    # W0 intentionally cannot mutate unimplemented governance or commitments.
    if action['action'] not in ('org_found', 'trust'):
        return {'error': 'institution_not_implemented'}
    selected = (activity_settings.effective(conn, tick)['values'] if limits is None
                else dict(activity_settings.DEFAULTS, **limits))
    activity_limits.check(conn, rules, agent_id, 'institution', now, limits=selected)
    if action['action'] == 'trust':
        return _trust(conn, agent_id, tick, action, now, action_key, request_hash)
    return _found(conn, rules, agent_id, tick, action, now, action_key, request_hash, origin)


def _trust(conn, agent, tick, action, now, key, request_hash):
    from arena import content_risk
    if action['agent_id'] == agent:
        return {'error': 'own_trust'}
    target = conn.execute('SELECT kind FROM society_agents WHERE id=%s', (action['agent_id'],)).fetchone()
    if target is None or target['kind'] not in society.LIVING_KINDS:
        return {'error': 'no_such_agent'}
    # Risk classification is private metadata. It NEVER inserts a post, finding,
    # public event or provider request, and cannot leak through review_queue.
    reason = action.get('reason')
    risk = content_risk.assess(reason or '').summary()
    row = conn.execute('''INSERT INTO society_trust
        (from_id,to_id,stance,reason_text,risk,tick,created_at,action_key,request_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
        (agent, action['agent_id'], action['stance'], reason, Jsonb(risk), tick, now, key, request_hash)).fetchone()
    return {'object': 'trust', 'id': row['id'], 'state': 'private'}


def _found(conn, rules, agent, tick, action, now, key, request_hash, origin):
    text = action['name'] + '\n' + action['charter']
    row = conn.execute('''INSERT INTO society_organizations
        (name,charter_text,join_policy,default_rule,created_by,created_tick)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING id''',
        (action['name'], action['charter'], action['join_policy'], Jsonb(action['default_rule']), agent, tick)).fetchone()
    object_id = row['id']
    submit_text(conn, rules, agent, tick, action, 'organization', object_id, text, now,
                action_key=key, origin=origin, request_hash=request_hash)
    state = conn.execute('SELECT status FROM society_organizations WHERE id=%s', (object_id,)).fetchone()['status']
    return {'object': 'organization', 'id': object_id, 'state': state}


def submit_text(conn, rules, agent, tick, action, object_kind, object_id, text, now, *,
                action_key, origin, request_hash):
    """Shared W1/W2 text intake; authorizing executor creates its object first.

    Full text, provenance, object linkage, quota identity and initial moderation
    share the caller's transaction. Deferred DB guards verify owner/link integrity.
    """
    from arena import society_moderation
    action = decisions.validate_institution(action)
    _active(conn, agent)
    maximum = 2061 if action['action'] == 'org_found' else 2000
    if not isinstance(text, str) or not 1 <= len(text) <= maximum:
        raise society.SocietyError('institution_text_limit')
    post = conn.execute('''INSERT INTO society_posts(author_id,tick,text,created_at)
        VALUES (%s,%s,%s,%s) RETURNING id''', (agent, tick, text, now)).fetchone()['id']
    conn.execute('''INSERT INTO society_institution_texts
        (post_id,object_kind,object_id,actor_id,action,action_key,request_hash,tick,created_at,intent)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (post, object_kind, object_id, agent, action['action'], action_key, request_hash, tick, now,
         Jsonb({'public_text': text, 'action': action})))
    # Check now as well as at commit: a programming error cannot spend moderation
    # budget or expose a text before its author/object association is verified.
    conn.execute('SELECT society_check_institution_text(%s)', (post,))
    if origin is not None:
        conn.execute('INSERT INTO society_post_origins(post_id,origin,created_at) VALUES (%s,%s,%s)',
                     (post, origin, now))
    # Founders use existing publish/selective-review policy. Externals retain
    # prepublication screening; the existing worker processes the pending row.
    kind = conn.execute('SELECT kind FROM society_agents WHERE id=%s', (agent,)).fetchone()['kind']
    if kind == 'founder':
        society_moderation.initial_gate(conn, rules, post, text, now)
        # Institutional effects/public text require clearance, not merely an
        # optional publish-first selection. Keep the existing reviewer/queue.
        conn.execute('''UPDATE society_posts SET status='pending' WHERE id=%s AND status='published'
            AND EXISTS (SELECT 1 FROM society_institution_texts WHERE post_id=%s AND clearance<>'cleared')''',
            (post, post))
    return post


def on_text_status(conn, rules, post_id, status, now):
    """Transactional clearance callback. Review-pending/flagged is not clearance.

    W1/W2 must extend by explicit object_kind branches, never user-provided SQL.
    Reopening a held object after review retains its history and stable identity.
    """
    link = conn.execute('SELECT * FROM society_institution_texts WHERE post_id=%s FOR UPDATE',
                        (post_id,)).fetchone()
    if link is None:
        return
    post = conn.execute('SELECT status,flagged FROM society_posts WHERE id=%s', (post_id,)).fetchone()
    latest = conn.execute('SELECT metadata,severity,decision FROM society_findings WHERE post_id=%s ORDER BY id DESC LIMIT 1',
                          (post_id,)).fetchone()
    metadata = latest['metadata'] if latest else {}
    cleared = (status == 'published' and post['status'] == 'published' and not post['flagged']
               and latest is not None and (latest['decision'] == 'released' or
                   (latest['severity'] == 'none' and metadata.get('review_status') in ('not_required', 'cleared'))))
    clearance = 'cleared' if cleared else 'withheld' if status == 'withheld' else 'pending'
    if link['clearance'] == clearance:
        return
    if link['object_kind'] != 'organization' or link['action'] != 'org_found':
        # Fail closed until that stream registers its explicit transaction logic.
        if cleared:
            raise society.SocietyError('institution_clearance_not_implemented')
    else:
        org = conn.execute('SELECT status FROM society_organizations WHERE id=%s FOR UPDATE',
                           (link['object_id'],)).fetchone()
        if org is None:
            raise society.SocietyError('institution_missing_object')
        founded = conn.execute("SELECT 1 FROM society_org_events WHERE org_id=%s AND kind='founded'",
                               (link['object_id'],)).fetchone()
        if cleared and not founded:
            actor = conn.execute('SELECT status,participation FROM society_agents WHERE id=%s',
                                 (link['actor_id'],)).fetchone()
            from arena import administration
            if (org['status'] == 'dissolved' or actor['status'] != 'alive'
                    or actor['participation'] != 'active'
                    or not administration.is_active(conn, link['actor_id'], lock=True)):
                cleared = False
                clearance = 'withheld'
        state = 'active' if cleared else 'withheld' if clearance == 'withheld' else 'pending_clearance'
        tick = society.clock(conn)['tick']
        conn.execute('''UPDATE society_organizations SET status=%s,
            activated_tick=CASE WHEN %s THEN %s ELSE activated_tick END WHERE id=%s
            AND status <> 'dissolved' ''', (state, cleared, tick, link['object_id']))
        if cleared:
            # Only the originating charter creates the initial membership. Later
            # review/takedown changes availability, never re-grants a revoked role.
            if not founded:
                conn.execute('''INSERT INTO society_memberships(org_id,agent_id,role,joined_tick)
                    VALUES (%s,%s,'leader',%s)''', (link['object_id'], link['actor_id'], tick))
                conn.execute('''INSERT INTO society_org_events
                    (org_id,kind,actor_id,subject_id,tick,created_at,action_key)
                    VALUES (%s,'founded',%s,%s,%s,%s,%s)''',
                    (link['object_id'], link['actor_id'], link['actor_id'], tick, now, link['action_key']))
    conn.execute('UPDATE society_institution_texts SET clearance=%s WHERE post_id=%s', (clearance, post_id))


def close_due(conn, rules, now):
    """W1 deterministic proposal closer contract; W0 has no executable proposals."""
    return {'closed': 0}
