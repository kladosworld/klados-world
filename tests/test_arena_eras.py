"""Era boundaries archive social context, never rewrite historical rows or money."""
import os
from datetime import timedelta
from unittest.mock import patch, Mock
from psycopg.types.json import Jsonb

from arena import eras, society, society_life, society_public, db
from tee_arena.society import rules as rules_module, shared_purpose
from tests.test_arena_external_agents import InviteCase

V2=rules_module.load()


class EraTests(InviteCase):
    def prepare(self):
        society.set_paused(self.conn,V2,True,self.now,'synthetic')
        self.conn.commit()
        # Registry byte integrity has its own tests/private-input review. This fixture
        # isolates the operator transaction without distributing private purpose text.
        selected=Mock(version=shared_purpose.EVOLUTION_VERSION)
        selected.fingerprint.return_value=shared_purpose.EVOLUTION_LAYER_SHA256
        mock=patch.object(shared_purpose,'validate_reviewed_registry',return_value={selected.version:selected})
        mock.start();self.addCleanup(mock.stop)
        mock=patch.object(shared_purpose,'load_registry',return_value={})
        mock.start();self.addCleanup(mock.stop)
        return selected

    def start(self,apply=True,**kw):
        with patch.dict(os.environ,KLADOS_EXTERNAL_AGENTS='off'):
            return eras.start(self.conn,V2,self.now,purpose=shared_purpose.EVOLUTION_VERSION,
                reference=kw.pop('reference','synthetic-approval'),approved_by='test',apply=apply,**kw)

    def test_preview_transition_idempotency_and_history_preservation(self):
        self.prepare()
        old=self.conn.execute("INSERT INTO society_posts(author_id,tick,text,status,created_at) VALUES ('alder',0,'OLD_SOCIAL_CANARY','published',%s) RETURNING id",(self.now,)).fetchone()['id']
        self.conn.execute("INSERT INTO society_notes(agent_id,tick,text,created_at) VALUES ('alder',0,'OLD_NOTE_CANARY',%s)",(self.now,))
        self.conn.execute("INSERT INTO society_strategies(agent_id,version,text,reason,tick,created_at) VALUES ('alder',1,'OLD_STRATEGY_CANARY','old',0,%s)",(self.now,))
        self.conn.execute("INSERT INTO society_likes(from_kind,from_id,post_id,target_id,tick,day,paid,created_at) VALUES ('agent','birch',%s,'alder',0,0,false,%s)",(old,self.now))
        self.conn.commit()
        before=list(self.conn.execute('SELECT to_jsonb(a) AS row FROM society_agents a ORDER BY id'))
        tick=society.clock(self.conn)['tick']
        self.assertTrue(self.start(False)['preview'])
        self.assertEqual(eras.current(self.conn)['id'],1)
        self.assertIn('OLD_NOTE_CANARY',society_life.notes(self.conn,'alder'))
        self.assertTrue(self.start()['changed']);self.conn.commit()
        self.assertFalse(self.start()['changed']);self.conn.commit()
        self.assertEqual(list(self.conn.execute('SELECT to_jsonb(a) AS row FROM society_agents a ORDER BY id')),before)
        self.assertEqual(society.clock(self.conn)['tick'],tick)
        self.assertEqual(self.conn.execute('SELECT text FROM society_posts WHERE id=%s',(old,)).fetchone()['text'],'OLD_SOCIAL_CANARY')
        self.assertEqual(society_life.notes(self.conn,'alder'),[])
        self.assertIsNone(society_life.strategy(self.conn,'alder'))
        context=society_life.turn_context(self.conn,V2,'alder')
        self.assertNotIn('CANARY',str(context))
        self.assertEqual(context['public_square'],[])
        self.assertTrue(all(not any(v for k,v in r.items() if k!='name') for r in context['relationships']))
        self.assertEqual(society_public.public_posts(self.conn,tick),[])
        reflection=society_life.reflection_context(self.conn,V2,'alder',0)
        self.assertNotIn('CANARY',str(reflection))
        with self.assertRaises(society.SocietyError):self.start(reference='different')
        self.conn.rollback()
        # Additive migrations are idempotent; Era 2 cannot be reset by an upgrade.
        db.migrate(self.conn);db.migrate(self.conn)
        self.assertEqual(eras.current(self.conn)['id'],2)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_eras').fetchone()['n'],2)

    def test_refusals_and_runtime_purpose_guard(self):
        selected=self.prepare()
        society.set_paused(self.conn,V2,False,self.now,'synthetic');self.conn.commit()
        with self.assertRaises(society.SocietyError):self.start()
        self.conn.rollback();society.set_paused(self.conn,V2,True,self.now,'synthetic');self.conn.commit()
        self.conn.execute("UPDATE society_agents SET public_description='custom' WHERE id='alder'");self.conn.commit()
        with self.assertRaises(society.SocietyError):self.start()
        self.conn.rollback()
        from tee_arena.society import prompts
        self.conn.execute("UPDATE society_agents SET public_description=%s WHERE id='alder'",(prompts.load_founders()['alder']['personality'],));self.conn.commit()
        self.conn.execute("INSERT INTO society_turns(agent_id,tick,created_at) VALUES ('alder',0,%s)",(self.now,));self.conn.commit()
        with self.assertRaises(society.SocietyError):self.start()
        self.conn.rollback();society.finish_turn(self.conn,'alder',0,'rest');self.conn.commit()
        self.start();self.conn.commit()
        with self.assertRaises(society.SocietyError):eras.require_purpose(self.conn,None)
        eras.require_purpose(self.conn,selected)
        with self.connection() as worker:
            worker.execute('SELECT pg_advisory_lock(%s)',(eras.SCHEDULER_LOCK,))
            with self.assertRaises(society.SocietyError):self.start()
            self.conn.rollback()
            worker.execute('SELECT pg_advisory_unlock(%s)',(eras.SCHEDULER_LOCK,))

    def test_new_external_activity_and_memory_use_current_era(self):
        registered=self.register().json()
        self.prepare();self.start();self.conn.commit()
        self.now=eras.current(self.conn)['started_at']+timedelta(seconds=1)
        society.set_paused(self.conn,V2,False,self.now,'synthetic');self.conn.commit()
        reply=self.client.post('/api/agents/me/actions',headers=self.headers(registered),json={
            'action':'post','text':'A new chapter contribution.','request_id':'era2-external'})
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertIn('post_id',reply.json())
        self.assertEqual(self.client.post('/api/agents/me/actions',headers=self.headers(registered),json={
            'action':'post','text':'A new chapter contribution.','request_id':'era2-external'}).json(),reply.json())
        self.conn.execute("INSERT INTO society_notes(agent_id,tick,text,created_at) VALUES ('alder',0,'NEW_NOTE',%s)",(self.now,));self.conn.commit()
        self.assertEqual(society_life.notes(self.conn,'alder'),['NEW_NOTE'])
        self.assertEqual(society.clock(self.conn)['tick'],0)
