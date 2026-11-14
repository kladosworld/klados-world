"""Transactional rolling-hour social allowances. No inference or scheduler."""

import math
from datetime import timedelta

from arena import society

WINDOW_SECONDS = 3600
LOCK_NAMESPACE = 73423200


class ActivityLimit(society.SocietyError):
    def __init__(self, action, limit, retry_after):
        super().__init__("activity_limit")
        self.action = action
        self.limit = limit
        self.retry_after = retry_after


def check(conn, rules, agent_id, action, now):
    """Hold a per-agent lock until commit; only committed action rows count.

    Historical rules-v1 retains its original validation. Current rules-v2 has
    independent post, reply and like limits. Rest has no social allowance.
    """
    if rules.activity_limits is None or action not in rules.activity_limits:
        return
    locked = conn.execute(
        """SELECT pg_advisory_xact_lock(%s, hashtext(%s)),
        current_setting('transaction_isolation') AS isolation""",
        (LOCK_NAMESPACE, agent_id),
    ).fetchone()
    if locked["isolation"] not in ("read committed", "read uncommitted"):
        raise society.SocietyError("activity_limits_unavailable")
    limit = rules.activity_limits[action]
    cutoff = now - timedelta(seconds=WINDOW_SECONDS)
    # The limit-th newest action determines when fewer than limit remain.
    # Include future timestamps conservatively if clocks differ or a request waited.
    if action == "like":
        query = """SELECT created_at FROM society_likes WHERE from_kind='agent'
            AND from_id=%s AND created_at>%s ORDER BY created_at DESC OFFSET %s LIMIT 1"""
        row = conn.execute(query, (agent_id, cutoff, limit - 1)).fetchone()
    else:
        query = """SELECT created_at FROM society_posts WHERE author_id=%s AND created_at>%s
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
