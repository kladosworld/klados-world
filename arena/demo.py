"""Explicit simulated-time activity on a paused, mock-only development society."""
from datetime import datetime, timedelta, timezone
import os

from arena import scheduler, society, worker
from tee_arena.gateway import DEFAULT_STOP_FILE


def run(conn, rules, ledger):
    if DEFAULT_STOP_FILE.exists():
        raise ValueError('Emergency stop is set')
    if os.getenv('KLADOS_AI_MODE') != 'mock':
        raise ValueError('Demo requires KLADOS_AI_MODE=mock')
    if not conn.execute('SELECT pg_try_advisory_lock(%s) AS ok', (scheduler.LOCK,)).fetchone()['ok']:
        raise ValueError('Stop the worker before running the demo')
    try:
        conn.commit()
        state = society.clock(conn)
        if not state['paused']:
            raise ValueError('Pause the society before running the demo')
        start = datetime.now(timezone.utc).replace(minute=59, second=0, microsecond=0)
        society.set_paused(conn, rules, False, start, 'mock-demo')
        conn.commit()
        agents = worker.build_gateway(ledger)
        watcher = worker.build_gateway(ledger, role='observer')
        for hour in (1, 2):
            scheduler.step(conn, rules, agents, watcher, start + timedelta(hours=hour))
        society.set_paused(conn, rules, True, start + timedelta(hours=2), 'mock-demo-complete')
        conn.commit()
        counts = conn.execute("""SELECT
            (SELECT count(*) FROM society_posts WHERE status='published') AS posts,
            (SELECT count(*) FROM society_likes) AS likes,
            (SELECT count(*) FROM society_notes) AS notes,
            (SELECT count(*) FROM society_turns WHERE status='done') AS turns""").fetchone()
        if society.audit(conn):
            raise ValueError('Energy journal mismatch')
        return dict(counts, tick=society.clock(conn)['tick'], paused=True, ai_mode='mock')
    finally:
        conn.rollback()
        conn.execute('SELECT pg_advisory_unlock(%s)', (scheduler.LOCK,))
        conn.commit()
