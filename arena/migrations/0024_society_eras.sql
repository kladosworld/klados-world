CREATE TABLE society_eras (
    id integer PRIMARY KEY CHECK (id IN (1,2)),
    started_at timestamptz NOT NULL,
    started_tick bigint NOT NULL CHECK (started_tick >= 0),
    purpose text,
    purpose_sha256 text,
    reference text,
    approved_by text,
    CHECK (id=1 OR (purpose='building-a-society-v3' AND length(purpose_sha256)=64
                   AND length(reference)>0 AND length(approved_by)>0))
);
-- No seed rows: absence of an era record means Era 1.
