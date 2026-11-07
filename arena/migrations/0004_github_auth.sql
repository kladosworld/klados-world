-- Minimal voter identity; raw provider ids and profile data are never stored.
ALTER TABLE voters ADD COLUMN provider text NOT NULL DEFAULT 'dev';
ALTER TABLE voters ADD COLUMN provider_id_hash text NULL;
ALTER TABLE voters ADD CONSTRAINT voter_provider_identity CHECK (
    (provider = 'dev' AND provider_id_hash IS NULL) OR
    (provider = 'github' AND provider_id_hash ~ '^[0-9a-f]{64}$' AND provider_id_hash IS NOT NULL));
CREATE UNIQUE INDEX voters_provider_identity_idx ON voters(provider, provider_id_hash);
CREATE TABLE oauth_flows (
    state_digest text PRIMARY KEY,
    browser_digest text NOT NULL,
    pkce_verifier text,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    code_digest text UNIQUE
);
CREATE TABLE voter_sessions (
    token_digest text PRIMARY KEY,
    voter_id text NOT NULL REFERENCES voters(id),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    adult_confirmed boolean NOT NULL DEFAULT false
);
CREATE INDEX voter_sessions_expiry_idx ON voter_sessions(expires_at);
