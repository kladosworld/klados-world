-- Preserve legacy lifecycle/audit records. Participation controls apply to all kinds.
ALTER TABLE society_agents ADD COLUMN participation text NOT NULL DEFAULT 'active'
    CHECK (participation IN ('active', 'suspended', 'revoked'));
UPDATE society_agents a SET participation = CASE WHEN t.revoked_at IS NOT NULL
    THEN 'revoked' ELSE 'suspended' END
FROM agent_tokens t WHERE t.agent_id=a.id AND (t.revoked_at IS NOT NULL OR t.suspended);
