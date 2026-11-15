"""Bounded deterministic spam sanctions in disposable data; no classifier authority."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from unittest.mock import patch

from arena import external_agents, society, society_life, society_moderation, spam_control
from tee_arena.society import rules
from tests.test_arena_external_agents import InviteCase

V2 = rules.load()
TEXT = 'A synthetic repeatable contribution about cooperation.'


class SpamTests(InviteCase):
    def setUp(self):
        super().setUp()
        environment = patch.dict(os.environ, KLADOS_RULES=str(rules.DEFAULT_PATH))
        environment.start()
        self.addCleanup(environment.stop)

    def seed_post(self, identifier='alder', text=TEXT):
        row = self.conn.execute("INSERT INTO society_posts(author_id,tick,text,status,created_at) VALUES (%s,0,%s,'published',%s) RETURNING id", (identifier,text,self.now-timedelta(hours=2))).fetchone()
        self.conn.commit()
        return row['id']

    def act(self, tick, identifier='alder', action='post', text=TEXT, **fields):
        self.assertTrue(society.claim_turn(self.conn, identifier, tick, self.now))
        decision = {'action': action, **fields}
        if action in ('post','reply'):
            decision['text'] = text
        result = society_life.act(self.conn,V2,identifier,tick,decision,self.now)
        self.conn.commit()
        return result

    def suspend(self, identifier='alder'):
        self.seed_post(identifier)
        warning = self.act(1, identifier)
        self.assertEqual(warning['spam_control']['level'],'warning')
        self.now += timedelta(hours=1)
        result = self.act(2, identifier)
        self.assertEqual(result['spam_control']['level'],'suspended')
        return self.now + timedelta(hours=6)

    def tick(self):
        society.set_paused(self.conn, V2, False, self.now, 'synthetic')
        self.now += timedelta(hours=1)
        result = society.tick(self.conn,V2,self.now)
        self.conn.commit()
        return result

    def test_warning_then_suspension_audit_identity_and_own_feedback(self):
        before = self.conn.execute("SELECT * FROM society_agents WHERE id='alder'").fetchone()
        until = self.suspend()
        events = self.conn.execute("SELECT kind,data,created_at FROM society_events WHERE agent_id='alder' AND kind=ANY(%s) ORDER BY id", (list(spam_control.EVENTS),)).fetchall()
        self.assertEqual([e['kind'] for e in events], ['spam_warning','spam_suspension'])
        data = events[1]['data']
        self.assertEqual(data['reason'],'duplicate_post')
        self.assertEqual(data['evidence'],TEXT)
        self.assertEqual(data['expires_at'],until.isoformat())
        self.assertEqual(data['started_at'],self.now.isoformat())
        self.assertEqual(self.conn.execute("SELECT * FROM society_agents WHERE id='alder'").fetchone(),before)
        own = society_life.turn_context(self.conn,V2,'alder',now=self.now)
        self.assertEqual(own['you']['spam_control']['level'],'suspended')
        other = society_life.turn_context(self.conn,V2,'birch',now=self.now)
        self.assertNotIn('spam_control',other['you'])
        self.assertTrue(society.clock(self.conn)['paused'])  # Fixture pause state is preserved.

    def test_all_social_actions_blocked_rest_and_memory_allowed_no_extension(self):
        until = self.suspend()
        root = self.seed_post('birch', 'A different public root.')
        history = self.conn.execute("SELECT * FROM society_events WHERE kind=ANY(%s) ORDER BY id", (list(spam_control.EVENTS),)).fetchall()
        for tick,action in enumerate(('post','reply','like'),start=3):
            result = self.act(tick,action=action,post_id=root)
            self.assertEqual(result['error'],'spam_suspended')
            self.assertEqual(result['expires_at'],until.isoformat())
        rest = self.act(6,action='rest',note='An own private memory during rest.')
        self.assertEqual(rest, {'action':'rest'})
        self.assertIn('An own private memory during rest.',society_life.notes(self.conn,'alder'))
        self.assertEqual(self.conn.execute("SELECT * FROM society_events WHERE kind=ANY(%s) ORDER BY id", (list(spam_control.EVENTS),)).fetchall(),history)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_posts WHERE author_id='alder'").fetchone()['n'],1)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_likes WHERE from_id='alder'").fetchone()['n'],0)

    def test_expiry_boundary_fresh_cycle_and_history_immutable(self):
        until = self.suspend()
        original = self.conn.execute("SELECT * FROM society_events WHERE kind=ANY(%s) ORDER BY id", (list(spam_control.EVENTS),)).fetchall()
        self.assertIsNotNone(spam_control.status(self.conn,'alder',until-timedelta(microseconds=1)))
        self.assertIsNone(spam_control.status(self.conn,'alder',until))
        self.now = until
        self.assertEqual(self.act(3)['spam_control']['level'],'warning')
        self.now += timedelta(hours=1)
        self.assertEqual(self.act(4)['spam_control']['level'],'suspended')
        updated = self.conn.execute("SELECT * FROM society_events WHERE kind=ANY(%s) ORDER BY id", (list(spam_control.EVENTS),)).fetchall()
        self.assertEqual(updated[:2],original)
        self.assertEqual(len(updated),4)
        self.assertEqual(spam_control.status(self.conn,'alder',self.now)['expires_at'],(self.now+timedelta(hours=6)).isoformat())

    def test_concurrent_idempotent_offenses_cannot_extend_suspension(self):
        self.seed_post()
        self.assertTrue(society.claim_turn(self.conn,'alder',1,self.now))
        self.assertTrue(society.claim_turn(self.conn,'alder',2,self.now))
        self.conn.commit()
        def offense(tick):
            with self.connection() as connection:
                return spam_control.record_duplicate(connection,V2,'alder',tick,TEXT,self.now)
        for tick,level in ((1,'warning'),(2,'suspended')):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(offense,[tick]*4))
            self.assertTrue(all(r['level']==level for r in results))
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_events WHERE agent_id='alder' AND kind=ANY(%s)", (list(spam_control.EVENTS),)).fetchone()['n'],2)

    def test_rollback_does_not_record_an_offense_and_unconfirmed_input_refused(self):
        self.seed_post()
        self.assertTrue(society.claim_turn(self.conn,'alder',1,self.now))
        spam_control.record_duplicate(self.conn,V2,'alder',1,TEXT,self.now)
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_events WHERE kind=ANY(%s)",(list(spam_control.EVENTS),)).fetchone()['n'],0)
        with self.assertRaises(society.SocietyError):
            spam_control.record_duplicate(self.conn,V2,'alder',2,'An unrelated original contribution.',self.now)

    def test_observer_spam_flags_do_not_create_suspensions(self):
        post_id = self.seed_post()
        self.conn.execute("UPDATE society_posts SET status='pending' WHERE id=%s",(post_id,))
        verdict = dict(codes=['SPAM'],severity='light',evidence='synthetic',by='observer',charged_microusd=0)
        society_moderation.apply_verdict(self.conn,V2,post_id,verdict,self.now)
        self.conn.commit()
        self.assertIsNone(spam_control.status(self.conn,'alder',self.now))
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_events WHERE kind=ANY(%s)",(list(spam_control.EVENTS),)).fetchone()['n'],0)

    def test_only_affected_founder_is_skipped_and_society_clock_stays_active(self):
        until = self.suspend()
        self.conn.execute("UPDATE society_clock SET tick=3,paused=false")
        self.conn.commit()
        due = society.due_turns(self.conn,self.now.replace(minute=59))
        self.assertNotIn('alder',due)
        self.assertEqual(set(due),{'birch','cedar','hazel'})
        self.assertFalse(society.clock(self.conn)['paused'])
        self.assertIn('alder',society.due_turns(self.conn,until.replace(minute=59)))

    def test_external_reads_rotation_action_limits_and_expiry(self):
        registered = self.register().json()
        identifier = registered['id']
        until = self.suspend(identifier)
        self.conn.execute("UPDATE society_clock SET tick=3,paused=false")
        self.conn.commit()
        headers = self.headers(registered)
        for route in ('/api/agents/me','/api/agents/me/state'):
            self.assertEqual(self.client.get(route,headers=headers).status_code,200)
        check = self.client.get('/api/agents/tick',headers=headers).json()
        self.assertFalse(check['can_act'])
        self.assertEqual(check['reason'],'spam_suspended')
        for action in ('post','reply','like'):
            response = self.client.post('/api/agents/me/actions',headers=headers,json={'action':action,'text':'A new thought.','post_id':1})
            self.assertEqual(response.status_code,403)
            self.assertEqual(response.headers['Retry-After'],'21600')
        self.assertIsNone(self.conn.execute('SELECT status FROM society_turns WHERE agent_id=%s AND tick=3',(identifier,)).fetchone())
        token = self.client.post('/api/agents/me/credentials/rotate',headers=headers,json={}).json()['token']
        headers = {'Authorization':'Bearer '+token}
        self.now = until
        response = self.client.post('/api/agents/me/actions',headers=headers,json={'action':'post','text':'A genuinely different observation about nature.'})
        self.assertEqual(response.status_code,200)
        self.assertIn('post_id',response.json())
        self.assertFalse(society.clock(self.conn)['paused'])

    def test_admin_revocation_and_suspension_are_not_cleared_at_expiry(self):
        registered = self.register().json()
        until = self.suspend(registered['id'])
        external_agents.control_agent(self.conn,registered['id'],'suspend',self.now)
        self.conn.commit()
        self.now = until
        self.assertEqual(self.client.get('/api/agents/me',headers=self.headers(registered)).status_code,403)
        external_agents.control_agent(self.conn,registered['id'],'revoke',self.now)
        self.conn.commit()
        self.assertEqual(self.client.get('/api/agents/me',headers=self.headers(registered)).status_code,401)


    def test_operator_history_is_bounded_read_only_and_evidence_stays_off_public_api(self):
        self.suspend()
        before = self.conn.execute("SELECT * FROM society_events ORDER BY id").fetchall()
        rows = spam_control.history(self.conn,'alder')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['data']['reason'],'duplicate_post')
        self.assertEqual(rows[0]['data']['evidence'],TEXT)
        self.assertEqual(self.conn.execute("SELECT * FROM society_events ORDER BY id").fetchall(),before)
        for route in ('/api/society/feed','/api/society/agents/alder'):
            response = self.client.get(route)
            self.assertEqual(response.status_code,200)
            self.assertNotIn('offense_key',response.text)
            self.assertNotIn('deterministic_duplicate',response.text)
        self.assertEqual(self.client.get('/api/agents/spam').status_code,404)
