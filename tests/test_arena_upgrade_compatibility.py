"""Populated-society compatibility across idempotent migrations and purpose toggles."""
import json
import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.test_arena_society_life import LifeCase, RULES


class PersistedUpgradeTests(LifeCase):
    def snapshot(self):
        from psycopg import sql
        tables = self.conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE 'society_%' ORDER BY tablename").fetchall()
        return {row['tablename']: [item['value'] for item in self.conn.execute(
            sql.SQL('SELECT to_jsonb(t) AS value FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(row['tablename'])))]
            for row in tables}

    def test_populated_society_and_public_contract_survive_migrate_and_purpose_off_on_off(self):
        from arena import db, society, society_life, scheduler
        from arena.app import create_app
        from psycopg import sql
        from tests.arena_support import DSN
        from tee_arena.society import prompts, shared_purpose

        post = self.publish(self.turn('alder', action='post', text='A proposal open to disagreement.',
                                     note='PRIVATE_ALDER_NOTE', reason='PRIVATE_ALDER_REASON')['post_id'])
        reply = self.publish(self.turn('birch', action='reply', post_id=post,
                                      text='I disagree with that proposal.', note='PRIVATE_BIRCH_NOTE')['post_id'])
        self.turn('cedar', action='like', post_id=post)
        self.turn('hazel', action='rest', note='PRIVATE_HAZEL_NOTE')
        society_life.record_reflection(self.conn, 'alder', 0, self.tick,
            {'keep':False, 'strategy':'PRIVATE_ALDER_STRATEGY', 'reason':'PRIVATE_STRATEGY_REASON'}, self.now)
        society.set_paused(self.conn, RULES, True, self.now, 'PRIVATE_PAUSE_REASON')
        self.conn.commit()
        before = self.snapshot()
        self.assertTrue(before['society_notes'])
        self.assertTrue(before['society_strategies'])
        self.assertTrue(before['society_likes'])
        self.assertEqual(society.relations(self.conn,'alder')['replies_from'],{'birch':1})
        self.assertEqual(society.relations(self.conn,'alder')['liked_by'],{'cedar':1})

        def connect():
            connection = db.connect(DSN)
            connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            return connection

        class ForbiddenGateway:
            def call(self, **kwargs):
                raise AssertionError('Paused compatibility checks must make no inference request')

        original = prompts.turn(society_life.turn_context(self.conn,RULES,'alder'))
        for selection in ('off','building-a-society-v1','off'):
            with self.subTest(purpose=selection), patch.dict(os.environ,
                    {'KLADOS_SHARED_PURPOSE':selection,'KLADOS_SOCIETY_PUBLIC':'1','KLADOS_AI_MODE':'mock'}):
                db.migrate(self.conn)
                self.conn.commit()
                purpose = shared_purpose.from_environ()
                composed = prompts.turn(society_life.turn_context(self.conn,RULES,'alder'),purpose=purpose)
                self.assertEqual(composed[1],original[1])
                self.assertEqual(composed[0],(purpose.prefix() if purpose else '')+original[0])
                summary = scheduler.step(self.conn,RULES,ForbiddenGateway(),ForbiddenGateway(),self.now)
                self.assertTrue(summary['paused'])
                with TestClient(create_app(connection_factory=connect)) as client:
                    feed = client.get('/api/society/feed')
                    self.assertEqual(feed.status_code,200)
                    self.assertTrue(feed.json()['paused'])
                    self.assertEqual([a['name'] for a in feed.json()['agents']],['Alder','Birch','Cedar','Hazel'])
                    square = client.get('/api/society/square').json()['posts']
                    self.assertEqual({p['id'] for p in square},{post,reply})
                    self.assertEqual(next(p for p in square if p['id']==post)['likes'],1)
                    self.assertEqual(next(p for p in square if p['id']==reply)['reply_to'],post)
                    self.assertTrue(all('created_at' in p for p in square))
                    public = [feed.json(),square]
                    for identifier in ('alder','birch','cedar','hazel'):
                        response=client.get('/api/society/agents/'+identifier)
                        self.assertEqual(response.status_code,200)
                        public.append(response.json())
                    stats=client.get('/api/society/stats')
                    self.assertEqual(stats.status_code,200)
                    public.append(stats.json())
                    self.assertNotIn('PRIVATE_',json.dumps(public))
                self.assertEqual(society.audit(self.conn),[])
                self.assertEqual(self.snapshot(),before)
