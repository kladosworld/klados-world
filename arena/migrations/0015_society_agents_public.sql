-- Public identity in the database (not in a config file), so any agent can have one.
-- Separate from the private strategy and notes. Updating it later is a plain UPDATE
-- that also sets description_updated_at; no endpoint does that in MVP v1.
ALTER TABLE society_agents
    ADD COLUMN public_description text CHECK (length(public_description) BETWEEN 1 AND 600),
    ADD COLUMN description_updated_at timestamptz;

-- One fossil per dead agent: a snapshot of its public identity and observable life,
-- created at death even when last words are missing or withheld.
CREATE TABLE society_fossils (
    agent_id text PRIMARY KEY REFERENCES society_agents(id),
    name text NOT NULL,
    kind text NOT NULL,
    public_description text,
    born_tick bigint NOT NULL,
    died_tick bigint NOT NULL,
    death_cause text NOT NULL CHECK (death_cause IN ('energy', 'age', 'exile')),
    energy_history jsonb NOT NULL,
    counts jsonb NOT NULL,
    relations jsonb NOT NULL,
    rules_version text NOT NULL,
    last_words_status text NOT NULL DEFAULT 'pending'
        CHECK (last_words_status IN ('pending', 'published', 'withheld', 'missing')),
    last_words text CHECK (length(last_words) BETWEEN 1 AND 280),
    last_words_attempts integer NOT NULL DEFAULT 0 CHECK (last_words_attempts >= 0),
    withheld_last_words text CHECK (length(withheld_last_words) BETWEEN 1 AND 280),
    created_at timestamptz NOT NULL,
    CHECK ((last_words_status = 'published') = (last_words IS NOT NULL)),
    CHECK (withheld_last_words IS NULL OR last_words_status = 'withheld')
);
