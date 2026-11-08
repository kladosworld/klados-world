-- Explicit publication boundary; never infer closure from the newest answers.
CREATE TABLE generation_closures (
    generation integer PRIMARY KEY CHECK (generation >= 0),
    closed_at timestamptz NOT NULL
);
