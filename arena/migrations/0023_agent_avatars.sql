-- Additive external profile metadata; founder identity rows remain byte-for-byte unchanged.
CREATE TABLE external_agent_avatars (
    agent_id text PRIMARY KEY REFERENCES society_agents(id),
    avatar_key text NOT NULL CHECK (avatar_key IN ('orbit','prism','wave','sprout','spark','grid'))
);
