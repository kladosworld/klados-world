-- Forward-only queue lookup indexes. No rows or historical findings are rewritten.
CREATE INDEX society_pending_review ON society_posts (id) WHERE status='pending';
CREATE INDEX society_optional_review ON society_findings (post_id,id)
    WHERE metadata->>'async_pending'='true';
CREATE INDEX society_moderation_attempts ON society_events ((data->>'post_id'),kind,id)
    WHERE kind IN ('risk_hold','moderation_outage','moderation_retry','moderation_contested');
