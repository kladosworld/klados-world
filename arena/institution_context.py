"""Bounded institutional state for one agent. No inference and no public trust API."""
from arena import eras

LIMITS = {'organizations': 3, 'proposals': 5, 'commitments': 5, 'cases': 3,
          'trust_outgoing': 5, 'trust_incoming': 5}


def build(conn, agent_id, tick=None):
    """Call only with the authenticated/claimed actor, never an arbitrary viewer ID.

    Stored text is not rewritten. Short context excerpts are untrusted data, not
    permissions, mandatory goals, findings of truth or a universal reputation score.
    """
    if tick is None:
        tick = conn.execute('SELECT tick FROM society_clock').fetchone()['tick']
    organizations = conn.execute(f'''SELECT o.id,o.name,left(o.charter_text,120) AS charter,
        o.join_policy,COALESCE(m.role,'') AS your_role FROM society_organizations o
        LEFT JOIN society_memberships m ON m.org_id=o.id AND m.agent_id=%s AND m.left_tick IS NULL
        WHERE o.status='active' AND o.created_tick >= {eras.START_TICK}
        AND EXISTS (SELECT 1 FROM society_institution_texts t JOIN society_posts p ON p.id=t.post_id
            WHERE t.object_kind='organization' AND t.object_id=o.id AND t.action='org_found'
            AND t.clearance='cleared' AND p.status='published' AND NOT p.flagged)
        ORDER BY (m.agent_id IS NOT NULL) DESC,o.id DESC LIMIT 3''', (agent_id,)).fetchall()
    proposals = conn.execute(f'''SELECT p.id,p.scope_id AS org_id,p.kind,left(p.text,120) AS text,
        p.options,p.closes_tick,EXISTS(SELECT 1 FROM society_votes v
            WHERE v.proposal_id=p.id AND v.voter_id=%s) AS already_voted
        FROM society_proposals p JOIN society_organizations o ON o.id=p.scope_id
        WHERE p.status='open' AND o.status='active' AND p.opens_tick >= {eras.START_TICK}
        AND p.closes_tick>%s AND EXISTS (SELECT 1 FROM society_institution_texts t
            JOIN society_posts text ON text.id=t.post_id WHERE t.object_kind='proposal'
            AND t.object_id=p.id AND t.clearance='cleared' AND text.status='published' AND NOT text.flagged)
        ORDER BY p.closes_tick,p.id LIMIT 5''', (agent_id, tick)).fetchall()
    commitments = conn.execute(f'''SELECT c.id,c.author_id,c.counterparty_id,c.status,c.due_tick,
        left(c.text,120) AS text FROM society_commitments c
        WHERE (c.author_id=%s OR c.counterparty_id=%s) AND c.status IN ('open','claimed','disputed')
        AND EXISTS (SELECT 1 FROM society_institution_texts t JOIN society_posts p ON p.id=t.post_id
            WHERE t.object_kind='commitment' AND t.object_id=c.id AND t.action='commit'
            AND t.clearance='cleared' AND p.status='published' AND NOT p.flagged AND p.created_at >= {eras.SINCE})
        ORDER BY c.due_tick NULLS LAST,c.id LIMIT 5''', (agent_id, agent_id)).fetchall()
    cases = conn.execute(f'''SELECT c.id,c.forum_org_id,c.filed_by,c.subject_ids,c.status,
        left(c.claim_text,120) AS claim FROM society_cases c WHERE c.status IN ('open','closed')
        AND c.opened_tick >= {eras.START_TICK} AND (
            EXISTS (SELECT 1 FROM society_institution_texts t JOIN society_posts p ON p.id=t.post_id
                WHERE t.object_kind='case' AND t.object_id=c.id AND t.action='case_open'
                AND t.clearance='cleared' AND p.status='published' AND NOT p.flagged)
            OR EXISTS (SELECT 1 FROM society_institution_texts t JOIN society_posts p ON p.id=t.post_id
                WHERE p.id::text=c.outcome->>'source_post_id' AND t.clearance='cleared'
                AND p.status='published' AND NOT p.flagged))
        ORDER BY c.id DESC LIMIT 3''').fetchall()
    outgoing = conn.execute(f'''SELECT agent_id,stance,reason,tick FROM (
        SELECT DISTINCT ON (to_id) to_id AS agent_id,stance,left(reason_text,160) AS reason,tick,id
        FROM society_trust WHERE from_id=%s AND created_at >= {eras.SINCE} ORDER BY to_id,id DESC
        ) latest ORDER BY id DESC LIMIT 5''', (agent_id,)).fetchall()
    incoming = conn.execute(f'''SELECT agent_id,stance,tick FROM (
        SELECT DISTINCT ON (from_id) from_id AS agent_id,stance,tick,id
        FROM society_trust WHERE to_id=%s AND created_at >= {eras.SINCE} ORDER BY from_id,id DESC
        ) latest ORDER BY id DESC LIMIT 5''', (agent_id,)).fetchall()
    return {'organizations': [dict(r) for r in organizations], 'proposals': [dict(r) for r in proposals],
            'commitments': [dict(r) for r in commitments], 'cases': [dict(r) for r in cases],
            'personal_trust': {'outgoing': [dict(r) for r in outgoing], 'incoming': [dict(r) for r in incoming]}}
