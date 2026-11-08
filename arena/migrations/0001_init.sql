-- Challenge, Answer and Vote contracts in docs/CONTRACTS.md.
CREATE TABLE voters (
    id text PRIMARY KEY,
    created_at timestamptz NOT NULL
);
CREATE TABLE challenges (
    id text PRIMARY KEY CHECK (id ~ '^ch-[0-9]{4,}$'),
    kind text NOT NULL CHECK (kind IN ('open', 'verifiable')),
    category text NOT NULL CHECK (category IN ('explanation', 'planning', 'writing',
        'reasoning', 'logic', 'math', 'knowledge', 'code_reading')),
    prompt text NOT NULL CHECK (length(prompt) BETWEEN 1 AND 4000 AND btrim(prompt) <> ''),
    checker jsonb,
    max_output_tokens integer NOT NULL CHECK (max_output_tokens BETWEEN 100 AND 2000),
    language text NOT NULL CHECK (language IN ('en', 'ro')),
    source text NOT NULL CHECK (source IN ('team', 'public')),
    status text NOT NULL CHECK (status IN ('draft', 'approved', 'retired')),
    notes text,
    submitted_by text REFERENCES voters(id),
    CHECK ((kind = 'open' AND checker IS NULL) OR
        (kind = 'verifiable' AND checker IS NOT NULL AND jsonb_typeof(checker) = 'object'
         AND coalesce(checker->>'type' IN ('exact', 'numeric', 'choice', 'regex'), false)))
);
CREATE TABLE answers (
    id text PRIMARY KEY,
    challenge_id text NOT NULL REFERENCES challenges(id),
    agent_id text NOT NULL,
    generation integer NOT NULL CHECK (generation >= 0),
    genome_version text NOT NULL,
    constitution_fingerprint text NOT NULL CHECK (constitution_fingerprint ~ '^[0-9a-f]{64}$'),
    text text NOT NULL,
    charged_microusd bigint NOT NULL CHECK (charged_microusd >= 0),
    created_at timestamptz NOT NULL,
    UNIQUE (id, challenge_id, agent_id)
);
CREATE TABLE pairings (
    id text PRIMARY KEY,
    voter_id text NOT NULL REFERENCES voters(id),
    challenge_id text NOT NULL REFERENCES challenges(id),
    left_answer_id text NOT NULL,
    right_answer_id text NOT NULL,
    left_agent_id text NOT NULL,
    right_agent_id text NOT NULL,
    created_at timestamptz NOT NULL,
    CHECK (left_agent_id <> right_agent_id),
    FOREIGN KEY (left_answer_id, challenge_id, left_agent_id)
        REFERENCES answers(id, challenge_id, agent_id),
    FOREIGN KEY (right_answer_id, challenge_id, right_agent_id)
        REFERENCES answers(id, challenge_id, agent_id),
    UNIQUE (id, voter_id, challenge_id, left_answer_id, right_answer_id)
);
CREATE TABLE votes (
    id text PRIMARY KEY,
    pairing_id text NOT NULL UNIQUE,
    voter_id text NOT NULL,
    challenge_id text NOT NULL,
    left_answer_id text NOT NULL,
    right_answer_id text NOT NULL,
    winner text NOT NULL CHECK (winner IN ('left', 'right', 'tie')),
    weight double precision NOT NULL CHECK (weight > 0 AND weight <= 1),
    created_at timestamptz NOT NULL,
    FOREIGN KEY (pairing_id, voter_id, challenge_id, left_answer_id, right_answer_id)
        REFERENCES pairings(id, voter_id, challenge_id, left_answer_id, right_answer_id)
);
CREATE INDEX answers_challenge_idx ON answers(challenge_id);
CREATE INDEX pairings_left_idx ON pairings(left_answer_id);
CREATE INDEX pairings_right_idx ON pairings(right_answer_id);
CREATE INDEX votes_voter_time_idx ON votes(voter_id, created_at);

-- Enforce own-challenge exclusion even for direct SQL callers. Lock the challenge
-- row so a concurrent submitter update cannot invalidate the pairing invariant.
CREATE FUNCTION guard_pairing() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_id text;
BEGIN
    SELECT submitted_by INTO owner_id FROM challenges WHERE id = NEW.challenge_id FOR SHARE;
    IF owner_id = NEW.voter_id THEN
        RAISE EXCEPTION 'voter submitted this challenge' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER pairing_owner_guard BEFORE INSERT OR UPDATE ON pairings
    FOR EACH ROW EXECUTE FUNCTION guard_pairing();
CREATE FUNCTION guard_challenge_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM pairings WHERE challenge_id = NEW.id AND voter_id = NEW.submitted_by) THEN
        RAISE EXCEPTION 'challenge submitter has a pairing' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER challenge_owner_guard BEFORE UPDATE OF submitted_by ON challenges
    FOR EACH ROW EXECUTE FUNCTION guard_challenge_owner();
