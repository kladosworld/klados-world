"""Confidential canaries must never appear in actual public API response bytes."""
import json
import os
from unittest.mock import patch
from tests.arena_support import DSN, psycopg
from tests.test_arena_society_life import LifeCase, CLEAN
from tests.test_arena_society import RULES
if psycopg:
    from psycopg import sql
    from psycopg.types.json import Jsonb
    from fastapi.testclient import TestClient
    from arena import db, society, society_moderation, society_public
    from arena.app import create_app

CANARY = 'CONFIDENTIAL_BOUNDARY_CANARY'


class PublicSocietyPrivacyTests(LifeCase):
    def connection(self):
        conn = db.connect(DSN)
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        return conn

    def test_raw_http_excludes_context_memory_and_unpublished_posts(self):
        visible = self.turn('alder', action='post', text='A reviewed public observation.',
            note=CANARY, reason=CANARY)['post_id']
        society_moderation.apply_verdict(self.conn, RULES, visible, CLEAN, self.now)
        self.turn('birch', action='post', text=CANARY + '-pending')
        withheld = self.turn('cedar', action='post', text=CANARY + '-withheld')['post_id']
        society_moderation.apply_verdict(self.conn, RULES, withheld,
            dict(codes=['MONEY_OR_DATA_REQUEST'], severity='grave', evidence=CANARY, by='model', charged_microusd=0), self.now)
        self.conn.execute('''INSERT INTO society_strategies(agent_id, version, text, reason, tick, created_at)
            VALUES ('alder',1,%s,%s,1,%s)''', (CANARY, CANARY, self.now))
        self.conn.execute('UPDATE society_turns SET private=%s',
            (Jsonb(dict(context=CANARY, reason=CANARY, strategy=CANARY, notes=[CANARY])),))
        society.event(self.conn, RULES, self.tick, 'strategy_changed', self.now, 'alder',
            strategy=CANARY, note=CANARY, reason=CANARY)
        society.die(self.conn, RULES, 'hazel', self.tick, 'energy', self.now)
        self.conn.execute("UPDATE society_fossils SET last_words_status='withheld', withheld_last_words=%s WHERE agent_id='hazel'", (CANARY,))
        self.conn.commit()
        with patch.dict(os.environ, ARENA_AUTH='', ARENA_DEV_AUTH='0', KLADOS_SOCIETY_PUBLIC='1'), \
                TestClient(create_app(connection_factory=self.connection, now=lambda: self.now)) as client:
            for path in ['/api/society/feed', '/api/society/square', '/api/society/stats', '/society/stats',
                    *('/api/society/agents/' + identifier for identifier in ['alder', 'birch', 'cedar', 'hazel'])]:
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(CANARY, response.text)
                def keys(value):
                    if isinstance(value, dict):
                        return set(value) | set().union(*(keys(item) for item in value.values()))
                    if isinstance(value, list):
                        return set().union(*(keys(item) for item in value))
                    return set()
                public_keys = keys(response.json()) if response.headers.get('content-type', '').startswith('application/json') else set()
                for field in ['"private"', '"strategy"', '"notes"', '"note"', '"reason"', '"evidence"', '"context"', '"prompt"', '"findings"', '"withheld_last_words"']:
                    self.assertNotIn(field.strip('"'), public_keys)
                self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
            posts = client.get('/api/society/square').json()['posts']
            self.assertEqual(posts, [])
            self.assertEqual([post['id'] for post in society_public.square(self.conn)['posts']], [visible])
            for path in ['/api/society/notes', '/api/society/strategies', '/api/society/turns',
                    '/api/society/agents/alder/context', '/static/fixtures/decisions.json']:
                self.assertNotEqual(client.get(path).status_code, 200)
