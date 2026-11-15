"""Deterministic, agent-scoped spam controls derived from the existing audit stream."""
from datetime import datetime, timedelta, timezone
import math

from arena import society

DURATION = timedelta(hours=6)
POLICY = 'spam-v1'
EVENTS = ('spam_warning', 'spam_suspension')


def status(conn, agent_id, now):
    """Read a bounded restriction. Expiry needs no job, counter reset or credential write."""
    now = society._utc(now)
    row = conn.execute("""SELECT data FROM society_events WHERE agent_id=%s
                          AND kind='spam_suspension' ORDER BY id DESC LIMIT 1""", (agent_id,)).fetchone()
    if row is None:
        return None
    until = datetime.fromisoformat(row['data']['expires_at']).astimezone(timezone.utc)
    if until <= now:
        return None
    return {'reason': 'repeated_spam', 'expires_at': until.isoformat(),
            'retry_after_seconds': max(1, math.ceil((until - now).total_seconds()))}


def check(conn, agent_id, action, now):
    """Serialize social actions with offense recording; rest and private work stay available."""
    if action not in ('post', 'reply', 'like'):
        return
    conn.execute('SELECT id FROM society_agents WHERE id=%s FOR UPDATE', (agent_id,))
    restriction = status(conn, agent_id, now)
    if restriction is not None:
        raise Restricted(restriction)


class Restricted(society.SocietyError):
    def __init__(self, restriction):
        super().__init__('spam_suspended')
        self.restriction = restriction


def record_duplicate(conn, rules, agent_id, tick, text, now):
    """Only the validated duplicate-action path calls this, never an observer verdict.

    The claimed turn is the offense identity. A replay cannot add a warning or
    lengthen a suspension. A later, fresh warning/repeat cycle starts after expiry.
    """
    now = society._utc(now)
    agent = conn.execute('SELECT status,participation FROM society_agents WHERE id=%s FOR UPDATE', (agent_id,)).fetchone()
    if agent is None or agent['status'] != 'alive' or agent['participation'] != 'active':
        return None
    key = f'turn:{agent_id}:{tick}'
    previous = conn.execute("""SELECT kind,data FROM society_events WHERE agent_id=%s
        AND kind=ANY(%s) AND data->>'offense_key'=%s ORDER BY id DESC LIMIT 1""",
        (agent_id, list(EVENTS), key)).fetchone()
    if previous:
        return {'level': 'warning' if previous['kind'] == 'spam_warning' else 'suspended',
                'expires_at': previous['data'].get('expires_at')}
    turn = conn.execute('SELECT status FROM society_turns WHERE agent_id=%s AND tick=%s', (agent_id, tick)).fetchone()
    from arena.society_moderation import is_duplicate
    if turn is None or turn['status'] != 'claimed' or not is_duplicate(conn, agent_id, text):
        raise society.SocietyError('spam_offense_not_confirmed')
    restriction = status(conn, agent_id, now)
    if restriction is not None:
        return dict(restriction, level='suspended')
    last = conn.execute("""SELECT kind,data FROM society_events WHERE agent_id=%s
        AND kind=ANY(%s) ORDER BY id DESC LIMIT 1""", (agent_id, list(EVENTS))).fetchone()
    repeated = last is not None and last['kind'] == 'spam_warning'
    until = now + DURATION if repeated else None
    society.event(conn, rules, tick, 'spam_suspension' if repeated else 'spam_warning', now, agent_id,
                  policy_version=POLICY, reason='duplicate_post', evidence=text[:280],
                  offense_key=key, source='deterministic_duplicate', started_at=now.isoformat(),
                  expires_at=until.isoformat() if until else None)
    return {'level': 'suspended' if repeated else 'warning',
            'expires_at': until.isoformat() if until else None}
