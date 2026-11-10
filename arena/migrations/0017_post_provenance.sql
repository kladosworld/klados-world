-- Preserve existing rows and leave historical provenance unknown. No backfill.
CREATE TABLE society_post_origins (
    post_id integer PRIMARY KEY REFERENCES society_posts(id),
    origin text NOT NULL CHECK (origin IN ('mock', 'live', 'external')),
    created_at timestamptz NOT NULL
);
