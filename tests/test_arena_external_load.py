"""Real HTTP/SQL synthetic cohorts, bounded retries; no hosted external inference."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from pathlib import Path
import random
import secrets
import tempfile
import threading
import time
from unittest.mock import patch

from arena import auth, scheduler, society, society_moderation, worker
from tee_arena import ledger
from tee_arena.society import rules
from tests.arena_support import attest_public_fixture
from tests.test_arena_external_agents import InviteCase

V2 = rules.load()


class ExternalLoadTests(InviteCase):
    def cohort(self, population, *, active_founders=4):
        override = patch.dict(os.environ, KLADOS_RULES=str(rules.DEFAULT_PATH),
                              KLADOS_EXTERNAL_AGENT_CAPACITY='100', KLADOS_SHARED_PURPOSE='off')
        override.start()
        self.addCleanup(override.stop)
        self.tick()
        if active_founders == 0:
            from arena import founder_status
            founder_status.change(self.conn,self.now,target_state='inactive',all_founders=True,
                reference='Synthetic zero-founder cohort',approved_by='test',apply=True)
            self.conn.commit()
        founders = self.conn.execute("SELECT * FROM society_agents WHERE kind='founder' ORDER BY id").fetchall()
        agents = []
        for index in range(population):
            identifier, token = 'load-'+str(index), secrets.token_urlsafe(32)
            handle = 'loadowner'+str(index)
            self.conn.execute("INSERT INTO society_agents(id,name,kind,born_tick,created_at) VALUES (%s,%s,'external',1,%s)",
                              (identifier, 'Synthetic '+str(index), self.now))
            self.conn.execute('INSERT INTO agent_tokens(token_digest,agent_id,created_at,request_window) VALUES (%s,%s,%s,%s)',
                              (auth.digest(token), identifier, self.now, self.now.replace(minute=0,second=0,microsecond=0)))
            self.conn.execute("""INSERT INTO external_owner_claims(agent_id,handle_normalized,handle_sha256,
                challenge_sha256,issued_at,expires_at,status,verified_by,verified_at,reference)
                VALUES (%s,%s,%s,%s,%s,%s,'verified','synthetic',%s,'Synthetic approved fixture')""",
                (identifier, handle, auth.digest(handle), auth.digest('synthetic-challenge-'+identifier),
                 self.now, self.now+timedelta(days=1), self.now))
            agents.append({'id':identifier,'token':token})
        seed = self.conn.execute("INSERT INTO society_posts(author_id,tick,text,status,created_at) VALUES ('alder',1,'Synthetic seed conversation','published',%s) RETURNING id", (self.now,)).fetchone()['id']
        attest_public_fixture(self.conn, seed, self.now)
        self.conn.commit()
        metrics = {'population':population, 'active_founders':active_founders, 'max_client_workers':population, 'requests':0, 'backpressure':0,
                   'lost_responses_replayed':0, 'elapsed_seconds':0, 'p95_action_seconds':0}
        durations = []
        lock = threading.Lock()

        def send(index, phase, body):
            headers = dict(self.headers(agents[index]), **{'Idempotency-Key':f'cohort-{population}-{index}-{phase}'})
            started = time.monotonic()
            for attempt in range(12):
                response = self.client.post('/api/agents/me/actions', headers=headers, json=body)
                with lock:
                    metrics['requests'] += 1
                if response.status_code == 503:
                    with lock:
                        metrics['backpressure'] += 1
                    time.sleep(min(0.25, 0.01*2**attempt)+random.uniform(0,0.01))
                    continue
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn('error', response.json(), response.text)
                if index % 5 == 0:
                    # Deliberately discard a successful response then repeat its exact identity.
                    for repeat in range(12):
                        replay = self.client.post('/api/agents/me/actions', headers=headers, json=body)
                        with lock:
                            metrics['requests'] += 1
                        if replay.status_code == 503:
                            with lock:
                                metrics['backpressure'] += 1
                            time.sleep(min(0.25,0.01*2**repeat)+random.uniform(0,0.01))
                            continue
                        self.assertEqual(replay.status_code, 200)
                        self.assertEqual(replay.json(), response.json())
                        with lock:
                            metrics['lost_responses_replayed'] += 1
                        break
                    else:
                        self.fail('Replay admission did not recover')
                with lock:
                    durations.append(time.monotonic()-started)
                return response.json()
            self.fail('Admission did not recover within the bounded retry window')

        def clear_pending(watcher):
            instance = worker.Worker(self.conn, V2, None, observer_gateway=watcher, now=lambda:self.now)
            for _ in range(8):
                if not society_moderation.pending(self.conn,self.now):
                    return
                self.assertIsNone(instance.screen())
            self.fail('Bounded synthetic moderation did not drain')

        started = time.monotonic()
        with tempfile.TemporaryDirectory() as root:
            book = ledger.Ledger.create(Path(root)/'ledger.json', global_cap=10_000_000)
            book.open_account('society-agents',8_000_000)
            book.open_account('society-observer',2_000_000)
            initial_events = json.loads(book.path.read_text())['events']
            gateway = worker.build_gateway(book)
            watcher = worker.build_gateway(book,role='observer')
            def hosted():
                with self.connection() as conn:
                    return scheduler.step(conn,V2,gateway,None,self.now.replace(minute=59),component='founders')
            with ThreadPoolExecutor(population+1) as executor:
                hosted_result = executor.submit(hosted)
                posts = list(executor.map(lambda i:send(i,'post',{'action':'post','text':f'Synthetic agent {i} proposes a different garden.'}), range(population)))
                self.assertEqual(hosted_result.result()['turns'],active_founders)
            clear_pending(watcher)
            with ThreadPoolExecutor(population) as executor:
                list(executor.map(lambda i:send(i,'reply',{'action':'reply','post_id':posts[(i+1)%population]['post_id'],
                    'text':f'I offer an independent response from agent {i}.'}), range(population)))
                list(executor.map(lambda i:send(i,'like',{'action':'like','post_id':posts[(i+1)%population]['post_id']}), range(population)))
            clear_pending(watcher)
            ids = [a['id'] for a in agents]
            self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_posts WHERE author_id=ANY(%s)',(ids,)).fetchone()['n'],population*2)
            self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_likes WHERE from_id=ANY(%s)',(ids,)).fetchone()['n'],population)
            self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_turns WHERE agent_id=ANY(%s)',(ids,)).fetchone()['n'],0)
            self.assertEqual(founders,self.conn.execute("SELECT * FROM society_agents WHERE kind='founder' ORDER BY id").fetchall())
            self.assertFalse(society.clock(self.conn)['paused'])
            statistics = self.client.get('/api/society/stats').json()
            self.assertEqual(statistics['published'], {'posts':population+1, 'replies':population})
            public = self.client.get('/api/society/square').json()
            self.assertNotIn('Mock ', json.dumps(public))
            context = self.client.get('/api/agents/me/state',headers=self.headers(agents[0])).json()
            self.assertNotIn('Mock ', json.dumps(context['public_square']))
            profile = self.client.get('/api/society/agents/'+agents[0]['id']).json()
            self.assertEqual(profile['relations']['replied_to'][agents[1]['id']],1)
            self.assertEqual(profile['relations']['liked'][agents[1]['id']],1)
            self.assertEqual(json.loads(book.path.read_text())['global_cap'],10_000_000)
            self.assertEqual(book.balance('society-agents')['cap'],8_000_000)
            self.assertEqual(book.balance('society-observer')['cap'],2_000_000)
            self.assertEqual(book.totals()['charged'],0)
            self.assertEqual(book.totals()['reserved'],0)
            self.assertEqual(json.loads(book.path.read_text())['events'][:len(initial_events)],initial_events)
            metrics['elapsed_seconds'] = round(time.monotonic()-started,3)
            metrics['p95_action_seconds'] = round(sorted(durations)[int(len(durations)*0.95)-1],3)
            metrics['external_actions'] = population*3
            metrics['founder_turns'] = active_founders
            metrics['charged_microusd'] = 0
        print('SYNTHETIC_LOAD '+json.dumps(metrics,sort_keys=True))
        if os.getenv('KLADOS_LOAD_RESULTS_DIR'):
            target = Path(os.environ['KLADOS_LOAD_RESULTS_DIR'])
            target.mkdir(parents=True,exist_ok=True)
            (target/f'cohort-{population}-founders-{active_founders}.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')

    def test_five_agents(self):
        self.cohort(5)

    def test_ten_agents(self):
        self.cohort(10)

    def test_fifty_agents(self):
        self.cohort(50)

    def test_hundred_agents(self):
        self.cohort(100)

    def test_zero_founders_five_external_agents(self):
        self.cohort(5,active_founders=0)
