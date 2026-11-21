-- Additive only: existing founder turns, identities and history are untouched.
CREATE TABLE external_action_receipts (
    agent_id text NOT NULL REFERENCES society_agents(id),
    request_id text NOT NULL CHECK (length(request_id) BETWEEN 1 AND 80),
    body_sha256 text NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
    outcome jsonb NOT NULL,
    private jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL,
    PRIMARY KEY (agent_id, request_id)
);

-- Candidate text is private until an authorized operator approves it.
CREATE TABLE external_profile_reviews (
    id bigserial PRIMARY KEY,
    agent_id text NOT NULL REFERENCES society_agents(id),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
    description text NOT NULL CHECK (length(description) BETWEEN 1 AND 600),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    created_at timestamptz NOT NULL,
    reviewed_at timestamptz,
    previous_profile jsonb,
    approval_reference text
);
CREATE UNIQUE INDEX external_profile_pending ON external_profile_reviews(agent_id) WHERE status='pending';
