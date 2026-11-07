-- MVP v1 moderation: one observer finding per post, human decisions on grave findings.
CREATE TABLE society_findings (
    id bigserial PRIMARY KEY,
    post_id bigint NOT NULL UNIQUE REFERENCES society_posts(id),
    codes text[] NOT NULL,
    severity text NOT NULL CHECK (severity IN ('none', 'light', 'grave')),
    evidence text NOT NULL DEFAULT '' CHECK (length(evidence) <= 400),
    by text NOT NULL CHECK (by IN ('precheck', 'model')),
    charged_microusd bigint NOT NULL DEFAULT 0 CHECK (charged_microusd >= 0),
    decision text CHECK (decision IN ('confirmed', 'released')),
    decided_at timestamptz,
    created_at timestamptz NOT NULL,
    CHECK ((decision IS NULL) = (decided_at IS NULL)),
    CHECK (decision IS NULL OR severity = 'grave')
);
ALTER TABLE society_posts ADD COLUMN flagged boolean NOT NULL DEFAULT false;
ALTER TABLE society_posts ADD COLUMN screen_failures integer NOT NULL DEFAULT 0 CHECK (screen_failures >= 0);
