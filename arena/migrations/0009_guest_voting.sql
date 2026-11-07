ALTER TABLE voters DROP CONSTRAINT voter_provider_identity;
ALTER TABLE voters ADD CONSTRAINT voter_provider_identity CHECK (
    (provider IN ('dev', 'guest') AND provider_id_hash IS NULL) OR
    (provider = 'github' AND provider_id_hash ~ '^[0-9a-f]{64}$' AND provider_id_hash IS NOT NULL));
CREATE TABLE guest_challenges (
    state_digest text PRIMARY KEY,
    browser_digest text NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    token_digest text UNIQUE
);
