-- Arena lifecycle metadata; historical answers, pairings and votes remain intact.
ALTER TABLE answers ADD COLUMN withheld_at timestamptz NULL;
ALTER TABLE pairings ADD COLUMN expired_at timestamptz NULL;
