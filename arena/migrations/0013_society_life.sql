-- MVP v1 founders' life: posts, likes, private notes and strategies, reflections, turn log.
CREATE TABLE society_posts (
    id bigserial PRIMARY KEY,
    author_id text NOT NULL REFERENCES society_agents(id),
    tick bigint NOT NULL CHECK (tick >= 0),
    reply_to bigint REFERENCES society_posts(id),
    text text NOT NULL CHECK (length(text) BETWEEN 1 AND 280),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'published', 'withheld')),
    created_at timestamptz NOT NULL
);
CREATE INDEX society_posts_recent ON society_posts (id DESC);
CREATE TABLE society_likes (
    id bigserial PRIMARY KEY,
    from_kind text NOT NULL CHECK (from_kind IN ('agent', 'human')),
    from_id text NOT NULL,
    post_id bigint NOT NULL REFERENCES society_posts(id),
    target_id text NOT NULL REFERENCES society_agents(id),
    tick bigint NOT NULL CHECK (tick >= 0),
    day integer NOT NULL CHECK (day >= 0),
    paid boolean NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (from_kind, from_id, post_id),
    CHECK (from_kind <> 'agent' OR from_id <> target_id)
);
-- One like per pair of founders per society day.
CREATE UNIQUE INDEX society_likes_agent_pair_day ON society_likes (from_id, target_id, day) WHERE from_kind = 'agent';
-- Private: never exposed by public endpoints.
CREATE TABLE society_notes (
    id bigserial PRIMARY KEY,
    agent_id text NOT NULL REFERENCES society_agents(id),
    tick bigint NOT NULL,
    text text NOT NULL CHECK (length(text) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL
);
CREATE TABLE society_strategies (
    agent_id text NOT NULL REFERENCES society_agents(id),
    version integer NOT NULL CHECK (version >= 1),
    text text NOT NULL CHECK (length(text) BETWEEN 1 AND 500),
    reason text CHECK (length(reason) <= 300),
    tick bigint NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (agent_id, version)
);
CREATE TABLE society_reflections (
    agent_id text NOT NULL REFERENCES society_agents(id),
    day integer NOT NULL CHECK (day >= 0),
    outcome text NOT NULL CHECK (outcome IN ('kept', 'changed', 'failed')),
    charged_microusd bigint NOT NULL DEFAULT 0 CHECK (charged_microusd >= 0),
    private jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (agent_id, day)
);
ALTER TABLE society_turns
    ADD COLUMN charged_microusd bigint NOT NULL DEFAULT 0 CHECK (charged_microusd >= 0),
    ADD COLUMN private jsonb NOT NULL DEFAULT '{}'::jsonb;
