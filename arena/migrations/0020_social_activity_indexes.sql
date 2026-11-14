-- Index existing committed records. No counters, backfill or historical rewrites.
CREATE INDEX society_posts_agent_activity ON society_posts (author_id, created_at);
CREATE INDEX society_likes_agent_activity ON society_likes (from_id, created_at)
    WHERE from_kind = 'agent';
