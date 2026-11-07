-- Planned times inform public counts only; explicit closure still gates ratings.
CREATE TABLE generation_schedule (
    generation integer PRIMARY KEY CHECK (generation >= 0),
    opens_at timestamptz NOT NULL,
    closes_at timestamptz NOT NULL,
    CHECK (closes_at > opens_at)
);
