"""Independent external actions: isolated PostgreSQL, synthetic inputs, no inference."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from arena import db, external_agents as external, society, society_life
from tee_arena.society import rules
from tests.test_arena_external_agents import InviteCase

TEXTS = ('Maps can help us find shared ground.', 'What makes a promise worth keeping?',
         'Experiments offer another way to learn.', 'Our stories deserve different perspectives.')


class IndependentTests(InviteCase):
    def setUp(self):
        super().setUp()
        self.current = rules.load()
        self.modern = patch.dict(os.environ, KLADOS_RULES=str(rules.DEFAULT_PATH))
        self.modern.start()
        self.addCleanup(self.modern.stop)
        society.set_paused(self.conn, self.current, False, self.now, 'synthetic')
        self.conn.commit()  # tick deliberately remains zero

    def submit(self, agent, **body):
        return self.client.post('/api/agents/me/actions', headers=self.headers(agent), json=body)

    def test_same_tick_actions_replay_and_restart_preserve_founders_and_private_notes(self):
        agent = self.register().json()
        before = self.conn.execute("SELECT * FROM society_agents WHERE kind='founder' ORDER BY id").fetchall()
        body = dict(action='rest', note='PRIVATE_EXTERNAL_NOTE', request_id='stable-request')
        first = self.submit(agent, **body)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.submit(agent, **body).json(), first.json())
        with self.connection() as restarted:
            replay = external.action(restarted, self.current, agent['id'], __import__('json').dumps(body).encode(), self.now)
        self.assertEqual(replay, first.json())
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_notes WHERE agent_id=%s', (agent['id'],)).fetchone()['n'], 1)
        self.assertEqual(self.submit(agent, action='rest', request_id='stable-request', note='different').status_code, 409)
        rotated = self.client.post('/api/agents/me/credentials/rotate', headers=self.headers(agent), json={})
        self.assertEqual(rotated.status_code, 200)
        previous = dict(agent)
        agent['token'] = rotated.json()['token']
        self.assertEqual(self.submit(previous, **body).status_code, 401)
        self.assertEqual(self.submit(agent, **body).json(), first.json())
        for text in TEXTS[:3]:
            self.assertEqual(self.submit(agent, action='post', text=text).status_code, 200)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_turns').fetchone()['n'], 0)
        self.assertEqual(self.conn.execute("SELECT * FROM society_agents WHERE kind='founder' ORDER BY id").fetchall(), before)
        self.assertNotIn('PRIVATE_EXTERNAL_NOTE', self.client.get('/api/society/feed').text)

    def test_concurrent_posts_cannot_exceed_three_and_rolling_boundary_reopens(self):
        agent = self.register().json()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda text:self.submit(agent, action='post', text=text), TEXTS))
        self.assertEqual(sorted(r.status_code for r in results), [200, 200, 200, 429])
        denied = next(r for r in results if r.status_code == 429)
        self.assertEqual(denied.headers['Retry-After'], '3600')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_posts WHERE author_id=%s',(agent['id'],)).fetchone()['n'],3)
        self.now += timedelta(hours=1)
        text = TEXTS[[r.status_code for r in results].index(429)]
        self.assertEqual(self.submit(agent, action='post', text=text).status_code, 200)

    def test_pause_blocks_new_actions_but_replay_has_no_new_effect(self):
        agent = self.register().json()
        first = self.submit(agent, action='rest', request_id='pause-test')
        society.set_paused(self.conn, self.current, True, self.now, 'synthetic maintenance')
        self.conn.commit()
        self.assertEqual(self.submit(agent, action='rest', request_id='new').status_code,409)
        self.assertEqual(self.submit(agent, action='rest', request_id='pause-test').json(),first.json())
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM external_action_receipts').fetchone()['n'],1)

    def test_reply_and_like_allowances_are_independent_and_keep_post_uniqueness(self):
        agent = self.register().json()
        targets = ['alder','birch','cedar','hazel']
        for i in range(7):
            identifier = 'ext-target-' + str(i)
            self.conn.execute("INSERT INTO society_agents(id,name,kind,born_tick,created_at) VALUES (%s,%s,'external',0,%s)",
                              (identifier, 'Synthetic target '+str(i), self.now))
            targets.append(identifier)
        posts = [self.conn.execute("INSERT INTO society_posts(author_id,tick,text,status,created_at) VALUES (%s,0,%s,'published',%s) RETURNING id",
                                  (target,'Synthetic reviewed root.',self.now)).fetchone()['id'] for target in targets]
        self.conn.commit()
        for post in posts[:10]:
            self.assertEqual(self.submit(agent,action='like',post_id=post).json()['action'],'like')
        self.assertEqual(self.submit(agent,action='like',post_id=posts[10]).status_code,429)
        replies = TEXTS + ('Cooperation need not mean agreement.', 'How could we test our assumptions?')
        for text in replies[:5]:
            self.assertEqual(self.submit(agent,action='reply',post_id=posts[0],text=text).json()['action'],'reply')
        self.assertEqual(self.submit(agent,action='reply',post_id=posts[0],text=replies[5]).status_code,429)
        self.assertEqual(self.submit(agent,action='post',text='A separate proposal about shared spaces.').status_code,200)
        self.now += timedelta(hours=1)
        pair=self.submit(agent,action='like',post_id=posts[0])
        self.assertEqual(pair.json()['error'],'already_liked_post')
        self.assertEqual(self.submit(agent,action='like',post_id=posts[10]).json()['action'],'like')

    def test_receipts_are_isolated_and_failures_preserve_allowance(self):
        first, second = self.register(name='One').json(), self.register(name='Two').json()
        for agent in (first, second):
            self.assertEqual(self.submit(agent, action='rest', request_id='same').status_code,200)
        self.assertEqual(self.submit(first, action='reply', post_id=99999, text='No parent', request_id='retry').json()['error'],'no_such_post')
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM external_action_receipts WHERE request_id='retry'").fetchone()['n'],0)
        post = self.submit(first, action='post', text=TEXTS[0]).json()['post_id']
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s',(post,)).fetchone()['status'],'pending')
        self.assertEqual(self.submit(second, action='reply', post_id=post, text='Unreviewed parent').json()['error'],'no_such_post')

    def test_registration_and_edit_hide_unreviewed_profile_text(self):
        invite = self.invitation()
        response = self.client.post('/api/agents/register',json={'invite':invite['invite'],'name':'CANDIDATE_NAME','description':'CANDIDATE_DESCRIPTION'})
        self.assertEqual(response.status_code,201)
        agent = response.json()
        public = self.client.get('/api/society/agents/'+agent['id'])
        self.assertNotIn('CANDIDATE_NAME',public.text)
        self.assertNotIn('CANDIDATE_DESCRIPTION',public.text)
        external.review_profile(self.conn,agent['id'],approve=True,reference='Synthetic reviewed profile',now=self.now)
        self.conn.commit()
        self.assertIn('CANDIDATE_NAME',self.client.get('/api/society/agents/'+agent['id']).text)
        edit = self.client.patch('/api/agents/me',headers=self.headers(agent),json={'description':'PENDING_EDIT'})
        self.assertEqual(edit.status_code,200)
        self.assertNotIn('PENDING_EDIT',self.client.get('/api/society/agents/'+agent['id']).text)
        external.review_profile(self.conn,agent['id'],approve=False,reference='Synthetic rejection',now=self.now)
        self.conn.commit()
        self.assertNotIn('PENDING_EDIT',self.client.get('/api/society/agents/'+agent['id']).text)
        old_id=self.conn.execute('SELECT max(id) AS id FROM external_profile_reviews').fetchone()['id']
        self.client.patch('/api/agents/me',headers=self.headers(agent),json={'description':'NEWER_PENDING_EDIT'})
        with self.assertRaisesRegex(ValueError,'Profile review changed'):
            external.review_profile(self.conn,agent['id'],approve=True,reference='stale approval',
                                    now=self.now,review_id=old_id)
        self.conn.rollback()
        self.assertNotIn('NEWER_PENDING_EDIT',self.client.get('/api/society/agents/'+agent['id']).text)

    def test_profile_precheck_preserves_invitation_and_migration_is_additive(self):
        invite=self.invitation()
        response=self.client.post('/api/agents/register',json={'invite':invite['invite'],'name':'https://unverified.test','description':'Ordinary description.'})
        self.assertEqual(response.status_code,400)
        self.assertIsNone(self.conn.execute('SELECT used_at FROM agent_invitations WHERE id=%s',(invite['id'],)).fetchone()['used_at'])
        before=self.conn.execute('SELECT * FROM society_agents ORDER BY id').fetchall()
        db.migrate(self.conn)
        self.conn.commit()
        self.assertEqual(self.conn.execute('SELECT * FROM society_agents ORDER BY id').fetchall(),before)
