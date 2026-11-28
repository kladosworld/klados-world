"""Free/paid component isolation, actual scheduler and persistent synthetic founders."""
from datetime import timedelta
from pathlib import Path
import tempfile
from unittest.mock import patch

from arena import founder_status, lifecycle, scheduler, society, worker, db
from tee_arena import paid_control
from tee_arena.society import rules
from tests.arena_support import DSN
from tests.test_arena_society_life import WorkerCase
from tests.test_arena_external_agents import InviteCase

V2 = rules.load()


class ComponentIsolationTests(WorkerCase):
    def test_paid_stop_is_financial_rest_for_four_founders_and_free_tick_continues(self):
        run = self.make({name:'{"actions":[]}' for name in ('Alder','Birch','Cedar','Hazel')})
        run.rules = V2
        run.gateway.paid_authorizer = lambda **kwargs: paid_control.Decision(False, 'paid_stop')
        raw = run.gateway.ledger.path.read_bytes()
        result = run.run(screening=False)
        self.assertEqual(result['financial_rests'], 4)
        self.assertFalse(society.clock(self.conn)['paused'])
        self.assertEqual(len(self.provider.requests), 0)
        self.assertEqual(run.gateway.ledger.path.read_bytes(), raw)
        self.assertTrue(all(r['status']=='done' for r in self.conn.execute('SELECT status FROM society_turns')))
        run.gateway.paid_authorizer = lambda **kwargs: paid_control.Decision(True)
        with patch.dict('os.environ', KLADOS_SHARED_PURPOSE='off'):
            resumed = scheduler.step(self.conn, V2, run.gateway, None, (self.now+timedelta(hours=1)).replace(minute=59))
        self.assertEqual(resumed['turns'], 4)
        self.assertEqual(len(self.provider.requests), 4)

    def test_one_founder_exception_does_not_stop_other_founders(self):
        run = self.make({name:'{"actions":[]}' for name in ('Alder','Birch','Cedar','Hazel')})
        run.rules = V2
        original = run.turn
        before = self.conn.execute('SELECT id,name,public_description FROM society_agents ORDER BY id').fetchall()
        def turn(agent, tick):
            if agent == 'alder':
                society.claim_turn(self.conn, agent, tick, self.now)
                self.conn.commit()
                raise ValueError('PRIVATE_MEMORY_CANARY')
            return original(agent, tick)
        with patch.object(run, 'turn', side_effect=turn):
            result = run.run(screening=False)
        self.assertEqual((result['turns'], result['failed']), (3, 1))
        self.assertFalse(society.clock(self.conn)['paused'])
        self.assertNotIn('PRIVATE_MEMORY_CANARY', str(run.log))
        self.assertEqual(before, self.conn.execute('SELECT id,name,public_description FROM society_agents ORDER BY id').fetchall())

    def test_zero_founders_maintenance_does_not_touch_gateway_or_hosted_purpose(self):
        founder_status.change(self.conn, self.now, target_state='inactive', all_founders=True,
                              reference='Synthetic', approved_by='test', apply=True)
        self.conn.commit()
        class Forbidden:
            def call(self, **kwargs):
                raise AssertionError('No paid dependency in maintenance')
        with patch('arena.worker.shared_purpose.from_environ', side_effect=AssertionError('No prompt dependency')):
            result = scheduler.step(self.conn, V2, Forbidden(), Forbidden(), self.now+timedelta(hours=1), component='maintenance')
        self.assertTrue(result['ticked'])
        self.assertFalse(result['paused'])
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_agents WHERE kind='founder'").fetchone()['n'], 4)

    def test_pause_between_due_list_and_turn_cannot_start_a_new_paid_call(self):
        run = self.make({'Alder':'{"actions":[]}'})
        run.rules = V2
        society.set_paused(self.conn,V2,True,self.now,'Synthetic operator pause')
        self.conn.commit()
        self.assertIsNone(run.turn('alder',society.clock(self.conn)['tick']))
        self.assertEqual(len(self.provider.requests),0)

    def test_split_singletons_preserve_exclusive_era_and_all_worker_exclusion(self):
        connections = []
        try:
            def connection():
                conn = db.connect(DSN)
                connections.append(conn)
                return conn
            maintenance = connection()
            founders = connection()
            duplicate = connection()
            all_worker = connection()
            era = connection()
            self.assertTrue(scheduler.acquire_component(maintenance, 'maintenance'))
            self.assertTrue(scheduler.acquire_component(founders, 'founders'))
            self.assertFalse(scheduler.acquire_component(duplicate, 'founders'))
            self.assertFalse(scheduler.acquire_component(all_worker, 'all'))
            self.assertFalse(era.execute('SELECT pg_try_advisory_xact_lock(%s) AS ok', (scheduler.LOCK,)).fetchone()['ok'])
        finally:
            for conn in connections:
                conn.close()

    def test_lifecycle_batches_make_fair_progress(self):
        for n in range(70):
            self.conn.execute("INSERT INTO society_agents(id,name,kind,born_tick,created_at) VALUES (%s,%s,'external',0,%s)",
                               ('synthetic-'+str(n), 'Synthetic '+str(n), self.now))
        self.conn.commit()
        for index in range(3):
            lifecycle.refresh(self.conn, self.now+timedelta(seconds=index), limit=32)
            self.conn.commit()
            count = self.conn.execute('SELECT count(*) AS n FROM society_maintenance_checks').fetchone()['n']
            self.assertEqual(count, min(32*(index+1), 74))

    def test_database_reconnect_does_not_repeat_committed_founder_calls(self):
        run = self.make({name:'{"actions":[]}' for name in ('Alder','Birch','Cedar','Hazel')})
        run.rules = V2
        self.conn.commit()
        stopped = []
        opened = []
        at = self.now.replace(minute=59)
        original = scheduler.step
        def connection(dsn):
            from psycopg import sql
            conn = db.connect(dsn)
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            opened.append(conn)
            return conn
        def step(conn, *args, **kwargs):
            result = original(conn, *args, **kwargs)
            conn.commit()
            if len(opened) == 1:
                pid = conn.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
                self.conn.execute('SELECT pg_terminate_backend(%s)', (pid,))
                self.conn.commit()
                conn.execute('SELECT 1')  # Force a real broken connection exception.
            else:
                stopped.append(True)
            return result
        with patch('os.environ', dict(__import__('os').environ, KLADOS_SHARED_PURPOSE='off')):
            with patch('arena.scheduler.step', side_effect=step):
                result = scheduler.serve(DSN, V2, run.gateway, None, component='founders',
                    interval=1, stopping=stopped, connection_factory=connection,
                    sleep=lambda _:None, now=lambda:at)
        self.assertEqual(result, 0)
        self.assertEqual(len(opened), 2)
        self.assertEqual(len(self.provider.requests), 4)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_turns').fetchone()['n'], 4)
        self.assertFalse(society.clock(self.conn)['paused'])



class EmergencyIsolationTests(InviteCase):
    def test_killed_blocks_new_social_actions_tick_and_resume_but_not_reads(self):
        agent = self.register().json()
        self.tick()
        tick = society.clock(self.conn)['tick']
        with tempfile.TemporaryDirectory() as root, patch.object(paid_control, 'DIRECTORY', Path(root)):
            (Path(root)/'KILLED').touch()
            response = self.client.post('/api/agents/me/actions', headers=self.headers(agent), json={'action':'rest'})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(self.client.get('/api/agents/me', headers=self.headers(agent)).status_code, 200)
            self.assertFalse(society.tick(self.conn, V2, self.now+timedelta(hours=1))['ticked'])
            with self.assertRaises(society.SocietyError):
                society.set_paused(self.conn, V2, False, self.now, 'Synthetic')
        self.assertEqual(society.clock(self.conn)['tick'], tick)

    def test_external_unbilled_outage_cycle_recovers_without_human_retry_penalty(self):
        from unittest.mock import Mock
        from arena import society_moderation
        agent = self.register().json()
        self.tick()
        post_id = self.client.post('/api/agents/me/actions', headers=self.headers(agent),
            json={'action':'post', 'text':'A synthetic recoverable contribution.'}).json()['post_id']
        instance = worker.Worker(self.conn, V2, None, observer_gateway=Mock(), moderation_client=Mock(), now=lambda:self.now)
        for _ in range(4):
            post = next(p for p in society_moderation.pending(self.conn,self.now) if p['id']==post_id)
            self.assertTrue(instance._observer_outage(post,'paid_operations_suspended'))
            self.now += timedelta(minutes=6)
        self.assertEqual(self.conn.execute('SELECT status,screen_failures FROM society_posts WHERE id=%s',(post_id,)).fetchone(),
                         {'status':'pending','screen_failures':0})
        verdict = dict(codes=[],severity='none',evidence='',by='model',charged_microusd=0,
                       review_status='cleared',review_performed='classifier')
        with patch('arena.moderation.review', return_value=verdict) as review:
            self.assertIsNone(instance.screen())
        review.assert_called_once()
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s',(post_id,)).fetchone()['status'],'published')
        self.assertFalse(society.clock(self.conn)['paused'])
