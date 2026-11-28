from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

from arena import activity_limits, institutions, lifecycle, operation_locks, society
from tee_arena.society import rules
from tests.arena_support import DatabaseCase, DSN
from arena import db
from psycopg import sql

NOW = datetime(2026, 11, 28, tzinfo=timezone.utc)
RULES = rules.load()


class LockOrderTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        society.found(self.conn, RULES, NOW)
        society.set_paused(self.conn, RULES, False, NOW, 'synthetic')
        self.conn.commit()

    def connection(self):
        conn = db.connect(DSN)
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        conn.execute("SET lock_timeout='3s'")
        return conn

    def test_external_actor_and_maintenance_do_not_invert_clock_and_actor(self):
        ready = threading.Event()
        operation_locks.social(self.conn)
        self.conn.execute('SELECT pg_advisory_xact_lock(%s,hashtext(%s))',
                          (activity_limits.LOCK_NAMESPACE, 'alder'))

        def maintain():
            with self.connection() as conn:
                institutions.close_due(conn, RULES, NOW)
                ready.set()
                return lifecycle.refresh(conn, NOW)

        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(maintain)
            try:
                self.assertTrue(ready.wait(2))
                # Former inversion: maintenance locked clock then waited for this actor;
                # external action held actor then waited for a conflicting clock share.
                self.assertFalse(self.conn.execute('SELECT paused FROM society_clock').fetchone()['paused'])
            finally:
                self.conn.commit()
            self.assertEqual(future.result(timeout=4)['changed'], 0)

    def test_pause_waits_for_inflight_social_commit(self):
        entered = threading.Event()
        operation_locks.social(self.conn)

        def pause():
            with self.connection() as conn:
                entered.set()
                society.set_paused(conn, RULES, True, NOW + timedelta(minutes=1), 'synthetic')

        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(pause)
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(future.done())
            finally:
                self.conn.commit()
            future.result(timeout=4)
        self.assertTrue(society.clock(self.conn)['paused'])

    def test_ordinary_social_leases_are_concurrent(self):
        operation_locks.social(self.conn)
        with self.connection() as other:
            other.execute("SET LOCAL lock_timeout='100ms'")
            operation_locks.social(other)
        self.conn.commit()
