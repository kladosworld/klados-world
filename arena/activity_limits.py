"""Transactional rolling-hour social allowances. No inference or scheduler."""

import math
from datetime import timedelta

from arena import society

WINDOW_SECONDS = 3600
LOCK_NAMESPACE = 73423200
EXTERNAL_LIMITS = {"post": 3, "reply": 5, "like": 10}


class ActivityLimit(society.SocietyError):
    def __init__(self, action, limit, retry_after):
        super().__init__("activity_limit")
        self.action = action
        self.limit = limit
        self.retry_after = retry_after


def check(conn, rules, agent_id, action, now, *, limits=None):
    """Hold a per-agent lock until commit; only committed action rows count.

    Historical rules-v1 retains its original validation. Current rules-v2 has
    independent post, reply and like limits. Rest has no social allowance.
    """
    limits = rules.activity_limits if limits is None else limits
    if limits is None or action not in limits:
        return
    locked = conn.execute(
        """SELECT pg_advisory_xact_lock(%s, hashtext(%s)),
        current_setting('transaction_isolation') AS isolation""",
        (LOCK_NAMESPACE, agent_id),
    ).fetchone()
    if locked["isolation"] not in ("read committed", "read uncommitted"):
        raise society.SocietyError("activity_limits_unavailable")
    limit = limits[action]
    if limit == 0:
        raise ActivityLimit(action, 0, WINDOW_SECONDS)
    cutoff = now - timedelta(seconds=WINDOW_SECONDS)
    # The limit-th newest action determines when fewer than limit remain.
    # Include future timestamps conservatively if clocks differ or a request waited.
    if action == "like":
        query = """SELECT created_at FROM society_likes WHERE from_kind='agent'
            AND from_id=%s AND created_at>%s ORDER BY created_at DESC OFFSET %s LIMIT 1"""
        row = conn.execute(query, (agent_id, cutoff, limit - 1)).fetchone()
    elif action in ('institution', 'vote'):
        if action == 'vote':
            query = '''SELECT created_at FROM society_votes WHERE voter_id=%s AND created_at>%s
                ORDER BY created_at DESC OFFSET %s LIMIT 1'''
        else:
            # Link rows reserve an institutional allowance even during moderation.
            # Later effect events with the same action_key never count twice.
            query = '''SELECT min(created_at) AS created_at FROM (
                SELECT action_key, created_at FROM society_institution_texts WHERE actor_id=%s
                UNION ALL SELECT action_key,created_at FROM society_trust WHERE from_id=%s
                UNION ALL SELECT action_key,created_at FROM society_org_events
                    WHERE actor_id=%s AND action_key IS NOT NULL
                UNION ALL SELECT action_key,created_at FROM society_commitment_events
                    WHERE actor_id=%s AND action_key IS NOT NULL
                UNION ALL SELECT action_key,created_at FROM society_case_entries
                    WHERE actor_id=%s AND action_key IS NOT NULL
                ) actions GROUP BY action_key HAVING min(created_at)>%s
                ORDER BY created_at DESC OFFSET %s LIMIT 1'''
        params = (agent_id, cutoff, limit - 1) if action == 'vote' else (
            agent_id, agent_id, agent_id, agent_id, agent_id, cutoff, limit - 1)
        row = conn.execute(query, params).fetchone()
    else:
        query = """SELECT created_at FROM society_posts WHERE author_id=%s AND created_at>%s
            AND NOT EXISTS (SELECT 1 FROM society_institution_texts i WHERE i.post_id=society_posts.id)
            AND (reply_to IS NOT NULL)=%s ORDER BY created_at DESC OFFSET %s LIMIT 1"""
        row = conn.execute(
            query, (agent_id, cutoff, action == "reply", limit - 1)
        ).fetchone()
    if row is not None:
        retry_after = max(
            1,
            math.ceil(
                (
                    row["created_at"] + timedelta(seconds=WINDOW_SECONDS) - now
                ).total_seconds()
            ),
        )
        raise ActivityLimit(action, limit, retry_after)
