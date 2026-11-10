-- Add an opt-in identity boundary without rewriting existing society data.
ALTER TABLE society_agents DROP CONSTRAINT society_agents_kind_check;
ALTER TABLE society_agents DROP CONSTRAINT society_agents_check1;
ALTER TABLE society_agents ADD CONSTRAINT society_agents_kind_check
    CHECK (kind IN ('founder', 'stone', 'external'));
ALTER TABLE society_agents ADD CONSTRAINT society_agents_living_check
    CHECK (kind IN ('founder', 'external') OR
           (balance = 0 AND turn_minute IS NULL AND status = 'alive'));

CREATE TABLE agent_invitations (
    id text PRIMARY KEY,
    token_digest text NOT NULL UNIQUE CHECK (token_digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    revoked_at timestamptz,
    CHECK (expires_at > created_at)
);
CREATE TABLE agent_tokens (
    token_digest text PRIMARY KEY CHECK (token_digest ~ '^[0-9a-f]{64}$'),
    agent_id text NOT NULL UNIQUE REFERENCES society_agents(id),
    created_at timestamptz NOT NULL,
    revoked_at timestamptz,
    suspended boolean NOT NULL DEFAULT false,
    request_window timestamptz NOT NULL,
    request_count integer NOT NULL DEFAULT 0 CHECK (request_count BETWEEN 0 AND 60)
);
