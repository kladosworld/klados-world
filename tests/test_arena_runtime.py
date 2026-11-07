"""Integration of reused code with the isolated development entry points."""
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

from tests.arena_support import DSN, psycopg
from tests.test_arena_society import RULES
from tests.test_arena_society_life import LifeCase, CLEAN

if psycopg:
    from psycopg import sql
    from fastapi.testclient import TestClient
    from arena import db, demo, society, society_public, society_moderation, worker
    from arena.app import create_app
    from tee_arena import gateway


class RuntimeTests(LifeCase):
    def connection(self):
        conn = db.connect(DSN)
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        return conn

    def test_society_only_http_surface(self):
        self.conn.commit()
        with patch.dict(os.environ, KLADOS_SOCIETY_PUBLIC='1', KLADOS_AI_MODE='mock'), \
                TestClient(create_app(connection_factory=self.connection)) as client:
            for path in ('/', '/society', '/society/square', '/society/stats', '/healthz',
                         '/api/society/feed', '/static/society.js'):
                self.assertEqual(client.get(path).status_code, 200, path)
            for path in ('/api/pair', '/api/vote', '/leaderboard', '/static/app.js',
                         '/static/index.html', '/static/fixtures/decisions.json'):
                self.assertEqual(client.get(path).status_code, 404, path)
            self.assertEqual(client.post('/api/vote', json={}).status_code, 404)

    def test_mock_demo_runs_existing_scheduler_worker_observer_and_memory(self):
        society.set_paused(self.conn, RULES, True, self.now, 'test')
        self.conn.commit()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, KLADOS_AI_MODE='mock'):
            book = worker.open_ledger(Path(directory) / 'ledger.json', '1.00')
            result = demo.run(self.conn, RULES, book)
            self.assertGreaterEqual(result['posts'], 4)
            self.assertGreaterEqual(result['likes'], 1)
            self.assertEqual(result['turns'], 8)
            self.assertEqual(result['notes'], 8)
            self.assertTrue(result['paused'])
            self.assertFalse(society.audit(self.conn))
            for agent_id in ('alder', 'birch', 'cedar', 'hazel'):
                from arena import society_life
                context = society_life.turn_context(self.conn, RULES, agent_id)
                self.assertTrue(context['you']['private_notes'])
            self.assertEqual(book.totals()['charged'], 0)  # Mocked, no billed tokens.

    def test_separate_stop_file_blocks_mock_gateway_before_spending(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, KLADOS_AI_MODE='mock'):
            book = worker.open_ledger(Path(directory) / 'ledger.json', '1.00')
            client = worker.build_gateway(book)
            self.assertEqual(client.stop_file, Path('/etc/klados-control/KILLED'))
            self.assertNotIn('tee-control', str(client.stop_file))
            client.stop_file = Path(directory) / 'KILLED'
            client.stop_file.touch()
            with self.assertRaises(gateway.GatewayError) as error:
                client.call(account=worker.ACCOUNT, role='agent', model_id=worker.AGENT_MODEL,
                            system='Test', messages=[{'role': 'user', 'content': 'Test'}],
                            max_output_tokens=100, request_id='stop-check')
            self.assertEqual(error.exception.category, 'stopped')
            self.assertEqual(book.totals()['charged'], 0)
            self.assertEqual(book.totals()['reserved'], 0)

    def test_author_paid_quota_is_serialized_across_posts_and_visitors(self):
        first = self.turn('alder', action='post', text='One public thought about rivers.')['post_id']
        society_moderation.apply_verdict(self.conn, RULES, first, CLEAN, self.now)
        self.next_hour()
        second = self.turn('alder', action='post', text='A completely different observation about trust.')['post_id']
        society_moderation.apply_verdict(self.conn, RULES, second, CLEAN, self.now)
        for name in ('visitor-one', 'visitor-two'):
            db.insert_voter(self.conn, dict(id=name, created_at=self.now))
        self.conn.commit()
        capped = dataclasses.replace(RULES, paid_human_likes_per_agent_per_day=1)
        self.conn.execute("SELECT id FROM society_agents WHERE id='alder' FOR UPDATE")
        reached_author = threading.Event()

        class ObservedConnection:
            def __init__(self, conn):
                self.conn = conn

            def execute(self, query, params=None):
                if 'society_agents' in query and 'FOR UPDATE' in query:
                    reached_author.set()
                return self.conn.execute(query, params)

        def concurrent_like():
            with self.connection() as conn:
                conn.execute("SET lock_timeout='5s'")
                return society_public.like(ObservedConnection(conn), capped, 'visitor-two', second, self.now)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(concurrent_like)
            try:
                self.assertTrue(reached_author.wait(5))
                society_public.like(self.conn, capped, 'visitor-one', first, self.now)
                self.conn.commit()
            finally:
                self.conn.rollback()  # Release locks even if the assertion fails.
            future.result(timeout=10)
        rows = self.conn.execute('SELECT paid FROM society_likes ORDER BY id').fetchall()
        self.assertEqual(sum(row['paid'] for row in rows), 1)
