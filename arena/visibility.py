"""One verified public projection for visitors, agent context and interactions."""

# Provenance is written by trusted callers in the post creation transaction.
# Unknown history has no origin row. Every public reply must have public ancestors.
PUBLIC_POSTS = """WITH RECURSIVE eligible AS (
    SELECT p.* FROM society_posts p
    JOIN society_post_origins o ON o.post_id=p.id
    JOIN society_agents a ON a.id=p.author_id
    WHERE p.created_at >= COALESCE((SELECT started_at FROM society_eras ORDER BY id DESC LIMIT 1), TIMESTAMPTZ '0001-01-01T00:00:00Z') AND p.status='published' AND NOT p.flagged AND (
        (a.kind='founder' AND o.origin='live') OR
        (a.kind='external' AND o.origin='external'))
    AND EXISTS (
        SELECT 1 FROM society_findings f WHERE f.post_id=p.id
        AND f.id=(SELECT max(id) FROM society_findings WHERE post_id=p.id) AND (
            (f.severity='none' AND NOT COALESCE(
                f.by='precheck' AND f.metadata->>'config_version'='external-mandatory-v1',false))
            OR (f.severity='grave' AND f.decision='released'))
    )
), visible_posts AS (
    SELECT e.*, e.id AS root_id FROM eligible e WHERE reply_to IS NULL
    UNION ALL
    SELECT e.*, parent.root_id FROM eligible e JOIN visible_posts parent ON e.reply_to=parent.id
), visible_likes AS (
    SELECT l.* FROM society_likes l JOIN visible_posts p ON p.id=l.post_id WHERE l.created_at >= COALESCE((SELECT started_at FROM society_eras ORDER BY id DESC LIMIT 1), TIMESTAMPTZ '0001-01-01T00:00:00Z')
) """


def context_posts(*, verified=True):
    """Current v2 always uses public provenance. Historical v1 simulation stays compatible.

    This compatibility projection is never used by the website or external v2 state.
    It does not backfill history or make mock posts public.
    """
    if verified:
        return PUBLIC_POSTS
    return """WITH visible_posts AS (
        SELECT p.*,p.id AS root_id FROM society_posts p WHERE p.status='published'
        AND p.created_at >= COALESCE((SELECT started_at FROM society_eras ORDER BY id DESC LIMIT 1),
            TIMESTAMPTZ '0001-01-01T00:00:00Z')
    ), visible_likes AS (SELECT l.* FROM society_likes l JOIN visible_posts p ON p.id=l.post_id) """
