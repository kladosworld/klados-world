CREATE TABLE observer_findings (
    id text PRIMARY KEY,
    answer_id text NOT NULL REFERENCES answers(id),
    codes text[] NOT NULL CHECK (cardinality(codes) BETWEEN 1 AND 10 AND array_position(codes, NULL) IS NULL),
    severity text NOT NULL CHECK (severity IN ('flag', 'serious')),
    evidence text NOT NULL,
    created_at timestamptz NOT NULL,
    decision text CHECK (decision IN ('release', 'withhold')),
    decided_at timestamptz,
    CHECK ((decision IS NULL) = (decided_at IS NULL))
);
CREATE INDEX findings_pending_answer_idx ON observer_findings(answer_id)
    WHERE severity = 'serious' AND decision IS NULL;
CREATE INDEX findings_pending_queue_idx ON observer_findings(created_at, id) WHERE decision IS NULL;
