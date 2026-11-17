-- Preserve old findings while recording the deciding tier and reviewed policy version.
ALTER TABLE society_findings DROP CONSTRAINT society_findings_by_check;
ALTER TABLE society_findings ADD CONSTRAINT society_findings_by_check
    CHECK (by IN ('precheck', 'model', 'rules', 'moderation', 'observer'));
ALTER TABLE society_findings ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE society_findings DROP CONSTRAINT society_findings_post_id_key;
CREATE INDEX society_findings_post_history ON society_findings(post_id, id);
