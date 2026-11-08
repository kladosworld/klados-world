-- Klados v2 society core: clock, agents, energy journal, turns, events.
-- Energy is integer units (rules: units_per_energy). The journal is append-only and
-- every entry has a unique key, so a retried operation can never apply twice.
CREATE TABLE society_clock (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    tick bigint NOT NULL DEFAULT 0 CHECK (tick >= 0),
    paused boolean NOT NULL DEFAULT true,
    rules_version text NOT NULL CHECK (rules_version ~ '^rules-v[0-9]+(\.[0-9]+)?$'),
    last_tick_at timestamptz,
    updated_at timestamptz NOT NULL
);
CREATE TABLE society_agents (
    id text PRIMARY KEY CHECK (id ~ '^[a-z][a-z0-9-]{1,39}$'),
    name text NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 40 AND btrim(name) = name),
    kind text NOT NULL CHECK (kind IN ('founder', 'stone')),
    born_tick bigint NOT NULL CHECK (born_tick >= 0),
    turn_minute integer CHECK (turn_minute BETWEEN 0 AND 59),
    status text NOT NULL DEFAULT 'alive' CHECK (status IN ('alive', 'dead')),
    balance bigint NOT NULL DEFAULT 0,
    died_tick bigint,
    death_cause text CHECK (death_cause IN ('energy', 'age', 'exile')),
    created_at timestamptz NOT NULL,
    CHECK ((status = 'dead') = (died_tick IS NOT NULL AND death_cause IS NOT NULL)),
    CHECK (kind = 'founder' OR (balance = 0 AND turn_minute IS NULL AND status = 'alive'))
);
CREATE TABLE energy_entries (
    id bigserial PRIMARY KEY,
    agent_id text NOT NULL REFERENCES society_agents(id),
    key text NOT NULL UNIQUE CHECK (length(key) BETWEEN 3 AND 160),
    kind text NOT NULL CHECK (kind IN ('birth', 'living', 'action', 'clip', 'prize', 'penalty', 'refund')),
    requested bigint NOT NULL,
    amount bigint NOT NULL,
    balance_after bigint NOT NULL,
    tick bigint NOT NULL CHECK (tick >= 0),
    rules_version text NOT NULL,
    created_at timestamptz NOT NULL,
    CHECK ((requested >= 0 AND amount BETWEEN 0 AND requested) OR (requested < 0 AND amount = requested))
);
CREATE INDEX energy_entries_agent ON energy_entries (agent_id, id);
CREATE TABLE society_turns (
    agent_id text NOT NULL REFERENCES society_agents(id),
    tick bigint NOT NULL CHECK (tick >= 0),
    status text NOT NULL DEFAULT 'claimed' CHECK (status IN ('claimed', 'done', 'failed')),
    action text,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (agent_id, tick)
);
CREATE TABLE society_events (
    id bigserial PRIMARY KEY,
    tick bigint NOT NULL CHECK (tick >= 0),
    kind text NOT NULL CHECK (kind ~ '^[a-z_]{2,40}$'),
    agent_id text REFERENCES society_agents(id),
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    rules_version text NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE INDEX society_events_tick ON society_events (tick, id);
