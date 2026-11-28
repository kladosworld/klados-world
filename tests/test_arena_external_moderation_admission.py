"""Bounded mandatory intake and recoverable external moderation, synthetic data only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock, patch

from arena import society_moderation, worker
from tee_arena import gateway
from tests.test_arena_world_feed import WorldFeedCase, RULES


class ExternalModerationAdmissionTests(WorldFeedCase):
    def submit(self, agent, text):
        return self.client.post('/api/agents/me/actions', headers=self.headers(agent),
                                json={'action': 'post', 'text': text})

    def test_concurrent_last_owner_slot_and_other_owner_fairness(self):
        first = self.register(name='First').json()
        second = self.register(name='Second').json()
        self.tick()
        with patch.object(society_moderation, 'EXTERNAL_OWNER_PENDING_LIMIT', 1):
            with ThreadPoolExecutor(2) as executor:
                responses = list(executor.map(lambda text: self.submit(first, text),
                    ['A question about maps.', 'An independent plan for a garden.']))
            self.assertEqual(sorted(r.status_code for r in responses), [200, 503])
            refused = next(r for r in responses if r.status_code == 503)
            self.assertEqual(refused.headers['retry-after'], '60')
            self.assertEqual(refused.json()['error'], 'moderation_queue_full')
            self.assertEqual(self.submit(second, 'A different owner can still speak.').status_code, 200)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_posts').fetchone()['n'], 2)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_turns').fetchone()['n'], 0)
        self.assertEqual(self.published_ids(), [])

    def test_queue_is_owner_interleaved_and_outages_are_recoverable(self):
        first = self.register(name='First').json()
        second = self.register(name='Second').json()
        self.tick()
        for text in ('A map might help us.', 'I prefer a garden.', 'What does fairness mean?'):
            self.assertEqual(self.submit(first, text).status_code, 200)
        self.assertEqual(self.submit(second, 'Another owner has a question.').status_code, 200)
        queued = society_moderation.pending(self.conn, self.now)
        self.assertEqual([p['author_id'] for p in queued[:2]], [first['id'], second['id']])
        post = queued[0]
        instance = worker.Worker(self.conn, RULES, Mock(), observer_gateway=Mock(), moderation_client=Mock(), now=lambda: self.now)
        before = instance._observe_request_id(post)
        self.assertTrue(instance._observer_outage(post, 'accounting_unavailable'))
        self.assertNotEqual(before, instance._observe_request_id(post))
        self.assertNotIn(post['id'], [r['id'] for r in society_moderation.pending(self.conn, self.now)])
        self.assertEqual(self.conn.execute('SELECT screen_failures FROM society_posts WHERE id=%s',
                                           (post['id'],)).fetchone()['screen_failures'], 0)
        self.now += timedelta(minutes=6)
        self.assertIn(post['id'], [r['id'] for r in society_moderation.pending(self.conn, self.now)])
        for _ in range(society_moderation.MAX_SCREEN_FAILURES):
            self.assertTrue(instance._screen_failed(post['id']))
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s',
                                           (post['id'],)).fetchone()['status'], 'withheld')
        self.assertTrue(society_moderation.review_queue(self.conn))
        self.assertNotIn(post['id'], self.published_ids())
        self.assertFalse(self.conn.execute('SELECT paused FROM society_clock').fetchone()['paused'])

    def test_budget_outage_never_publishes_external_content(self):
        agent = self.register().json()
        self.tick()
        post = self.submit(agent, 'A synthetic post awaiting review.').json()['post_id']
        instance = worker.Worker(self.conn, RULES, Mock(), observer_gateway=Mock(), moderation_client=Mock(), now=lambda: self.now)
        with patch('arena.moderation.review', side_effect=gateway.GatewayError('cap_exceeded')) as review:
            self.assertIsNone(instance.screen())
        review.assert_called_once()
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s',
                                           (post,)).fetchone()['status'], 'pending')
        self.assertEqual(self.published_ids(), [])
        self.assertFalse(self.conn.execute('SELECT paused FROM society_clock').fetchone()['paused'])

    def test_uncertain_paid_review_is_not_resubmitted_under_a_new_id(self):
        agent = self.register().json()
        self.tick()
        post = self.submit(agent, 'A result may be lost after payment.').json()['post_id']
        instance = worker.Worker(self.conn, RULES, Mock(), observer_gateway=Mock(), moderation_client=Mock(), now=lambda: self.now)
        with patch('arena.moderation.review', side_effect=gateway.GatewayError('accounting_unavailable', sent=True)) as review:
            self.assertIsNone(instance.screen())
            self.assertIsNone(instance.screen())
        self.assertEqual(review.call_count, 1)
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s',
                                           (post,)).fetchone()['status'], 'withheld')
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_events WHERE kind='moderation_outage'").fetchone()['n'], 0)
        self.assertTrue(society_moderation.review_queue(self.conn))
        self.assertFalse(self.conn.execute('SELECT paused FROM society_clock').fetchone()['paused'])

    def test_unresolved_human_cases_retain_owner_admission_slots(self):
        agent = self.register().json()
        self.tick()
        with patch.object(society_moderation,'EXTERNAL_OWNER_PENDING_LIMIT',1):
            post = self.submit(agent,'An unresolved synthetic case.').json()['post_id']
            society_moderation.escalate(self.conn,RULES,post,self.now)
            self.conn.commit()
            self.assertEqual(self.submit(agent,'Another unrelated proposal.').status_code,503)
            self.assertEqual(self.submit(agent,'Another unrelated proposal.').status_code,503)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_events WHERE kind='moderation_backpressure'").fetchone()['n'],1)
        finding = self.conn.execute('SELECT max(id) AS id FROM society_findings WHERE post_id=%s',(post,)).fetchone()['id']
        society_moderation.decide(self.conn,RULES,finding,'confirm',self.now)
        self.conn.commit()
        self.assertEqual(self.submit(agent,'A later independent question.').status_code,200)

    def test_explicit_noop_cannot_clear_mandatory_external_review(self):
        from arena import society
        agent = self.register().json()
        self.tick()
        post = self.submit(agent,'A post requiring actual review.').json()['post_id']
        with self.assertRaises(society.SocietyError):
            society_moderation.apply_verdict(self.conn,RULES,post,
                dict(codes=[],severity='none',evidence='',by='precheck',review_status='cleared',review_performed='none'),self.now)
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s',(post,)).fetchone()['status'],'pending')
        self.assertEqual(self.published_ids(),[])
