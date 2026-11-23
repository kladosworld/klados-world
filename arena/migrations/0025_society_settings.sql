-- Empty means reviewed defaults. Changes are append-only, never history rewrites.
CREATE TABLE society_settings (
    id bigserial PRIMARY KEY,
    scope text NOT NULL CHECK (scope = 'founders'),
    values jsonb NOT NULL,
    reference text NOT NULL CHECK (length(reference) BETWEEN 1 AND 200),
    approved_by text NOT NULL CHECK (length(approved_by) BETWEEN 1 AND 100),
    created_at timestamptz NOT NULL,
    effective_tick bigint NOT NULL CHECK (effective_tick > 0),
    supersedes bigint REFERENCES society_settings(id)
);
CREATE FUNCTION society_settings_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'society settings are append-only';
END $$;
CREATE TRIGGER society_settings_no_rewrite BEFORE UPDATE OR DELETE OR TRUNCATE
    ON society_settings FOR EACH STATEMENT EXECUTE FUNCTION society_settings_immutable();
