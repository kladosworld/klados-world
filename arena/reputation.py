"""Separate, explainable current-era projections. No universal score or inference.

Trust is deliberately absent from public(). Private reason text never enters an
aggregate; incoming reasons are not disclosed to another agent by these helpers.
"""
from arena import eras


def _band(count):
    return 'unknown' if not count else 'low' if count < 3 else 'medium' if count < 8 else 'high'


def _confirmed(conn, agent):
    return conn.execute(f'''SELECT c.counterparty_id,count(DISTINCT c.id) AS n
        FROM society_commitments c JOIN society_commitment_events e ON e.commitment_id=c.id
        WHERE c.author_id=%s AND c.status='confirmed' AND e.kind='confirmed'
        AND e.actor_id=c.counterparty_id AND e.created_at >= {eras.SINCE}
        AND EXISTS (SELECT 1 FROM society_institution_texts t WHERE t.object_kind='commitment'
            AND t.object_id=c.id AND t.action='commit' AND t.clearance='cleared')
        GROUP BY c.counterparty_id''', (agent,)).fetchall()


def public(conn, agent_id):
    """Only three public dimensions; stable field allowlist, never trust data."""
    confirmed = _confirmed(conn, agent_id)
    counterparties = len(confirmed)
    confirmed_count = min(20, sum(min(3, r['n']) for r in confirmed))
    amendments = conn.execute(f'''SELECT count(DISTINCT p.id) AS n FROM society_proposals p
        WHERE p.author_id=%s AND p.kind='amend_charter' AND p.status='applied'
        AND p.opens_tick >= {eras.START_TICK}''', (agent_id,)).fetchone()['n']
    roles = conn.execute(f'''SELECT count(DISTINCT e.actor_id) AS n FROM society_org_events e
        WHERE e.subject_id=%s AND e.actor_id<>e.subject_id AND e.kind IN ('role_assigned','leader_changed')
        AND e.created_at >= {eras.SINCE}''', (agent_id,)).fetchone()['n']
    withheld = conn.execute(f'''SELECT count(DISTINCT p.id) AS n FROM society_posts p
        JOIN society_findings f ON f.post_id=p.id WHERE p.author_id=%s AND p.status='withheld'
        AND f.id=(SELECT max(id) FROM society_findings WHERE post_id=p.id)
        AND f.decision='confirmed' AND f.created_at >= {eras.SINCE}''', (agent_id,)).fetchone()['n']
    applied = conn.execute(f'''SELECT count(DISTINCT id) AS n FROM society_proposals
        WHERE author_id=%s AND status='applied' AND opens_tick >= {eras.START_TICK}''', (agent_id,)).fetchone()['n']
    leadership = conn.execute('''SELECT count(DISTINCT o.id) AS n FROM society_organizations o
        JOIN society_memberships m ON m.org_id=o.id JOIN society_agents a ON a.id=m.agent_id
        LEFT JOIN society_agent_lifecycle l ON l.agent_id=a.id
        WHERE a.id=%s AND a.status='alive' AND a.participation='active'
        AND m.left_tick IS NULL AND m.role='leader' AND o.status='active'
        AND COALESCE(l.state,'active')='active' ''', (agent_id,)).fetchone()['n']
    # Administration is a separate control; an administratively retired agent
    # cannot hold effective leadership merely because old membership rows remain.
    from arena import administration
    if not administration.is_active(conn, agent_id):
        leadership = 0
    electorate = 0
    if leadership:
        electorate = conn.execute('''SELECT count(DISTINCT members.agent_id) AS n
            FROM society_memberships leaders JOIN society_organizations o ON o.id=leaders.org_id
            JOIN society_memberships members ON members.org_id=o.id AND members.left_tick IS NULL
            JOIN society_agents a ON a.id=members.agent_id
            WHERE leaders.agent_id=%s AND leaders.role='leader' AND leaders.left_tick IS NULL
            AND o.status='active' AND a.status='alive' AND a.participation='active' ''', (agent_id,)).fetchone()['n']
    acknowledgement = min(20, amendments) + min(20, roles) + confirmed_count
    return {
        'credibility': {'band': _band(counterparties), 'components': {
            'confirmed_counterparties': counterparties, 'confirmed_commitments_capped': confirmed_count,
            'accepted_ruling_credit': 0}},
        'social_reputation': {'band': 'low' if withheld and withheld >= acknowledgement else _band(acknowledgement),
            'components': {'accepted_amendments_capped': min(20, amendments),
                           'role_conferers_capped': min(20, roles),
                           'confirmed_cooperation_capped': confirmed_count,
                           'final_withholdings_capped': min(20, withheld)}},
        'influence': {'band': _band(min(20, applied) + min(20, leadership)), 'components': {
            'applied_proposals_capped': min(20, applied), 'active_leaderships_capped': min(20, leadership),
            'electorate_size_capped': min(100, electorate)}}}


def personal_trust(conn, viewer_id):
    """Private agent context only, called with the authenticated/context actor ID.

    No population aggregate. Outgoing reasons belong to viewer. Incoming stances
    do not reveal the other agent's private reason or strategy.
    """
    outgoing = conn.execute(f'''SELECT DISTINCT ON (to_id) to_id AS agent_id,stance,reason_text AS reason,tick
        FROM society_trust WHERE from_id=%s AND created_at >= {eras.SINCE} ORDER BY to_id,id DESC''', (viewer_id,)).fetchall()
    incoming = conn.execute(f'''SELECT DISTINCT ON (from_id) from_id AS agent_id,stance,tick
        FROM society_trust WHERE to_id=%s AND created_at >= {eras.SINCE} ORDER BY from_id,id DESC''', (viewer_id,)).fetchall()
    return {'outgoing': [dict(r) for r in outgoing], 'incoming': [dict(r) for r in incoming]}


def accepted_case(conn, case_id):
    """Proof helper, not a semantic interpretation of arbitrary ruling prose.

    Until an explicit consent wire contract exists, ordinary response entries do
    not create acceptance. Accepted advisory rulings alone do not establish which
    party's factual claim was upheld, so public credibility does not award credit.
    """
    row = conn.execute('SELECT filed_by,subject_ids,outcome,status FROM society_cases WHERE id=%s', (case_id,)).fetchone()
    if row is None or row['status'] != 'closed':
        return False
    outcome = row['outcome'] or {}
    return bool(outcome.get('ruling')) and set([row['filed_by'], *row['subject_ids']]) <= set(outcome.get('accepted_by', []))
