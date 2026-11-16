"""Screening isolation against the existing observer, with synthetic providers only."""
from unittest.mock import Mock, patch

from arena import society, society_moderation, worker
from tee_arena.gateway import GatewayError
from tee_arena.society import observer
from tests.test_arena_society_life import CLEAN, RULES, WorkerCase


class ScreeningIsolationTests(WorkerCase):
    def runner(self):
        run = self.make({name: '{"action":"rest"}' for name in ('Alder', 'Birch', 'Cedar', 'Hazel')})
        # Exercise the observer stage explicitly under the tiered pipeline.
        run.moderation_client = Mock(classify=Mock(return_value=['hate']))
        return run

    def pending_post(self, text='A synthetic pending question.'):
        post = self.conn.execute("INSERT INTO society_posts(author_id,tick,text,created_at) VALUES ('alder',1,%s,%s) RETURNING id", (text, self.now)).fetchone()['id']
        self.conn.commit()
        return post

    def test_observer_failures_never_use_founder_failure_counter_or_pause(self):
        post_id = self.pending_post()
        run = self.runner()
        killed = run.gateway.ledger.path.parent / 'KILLED'
        run.gateway.stop_file = killed
        for category in ('provider_failure', 'credential_unavailable', 'cap_exceeded', 'stopped'):
            with patch.object(observer, 'review', side_effect=GatewayError(category)), patch.object(run, '_failed', side_effect=AssertionError('observer cannot pause founders')) as failed:
                self.assertIsNone(run.screen())
                failed.assert_not_called()
            self.assertFalse(society.clock(self.conn)['paused'])
            self.assertEqual(run.failures, 0)
            self.conn.execute('UPDATE society_posts SET screen_failures=0 WHERE id=%s', (post_id,))
            self.conn.commit()
        with patch.object(observer, 'review', side_effect=GatewayError('credential_unavailable')):
            result = run.run()
        self.assertEqual(result['turns'], 4)
        self.assertFalse(result['stopped'])
        self.assertFalse(society.clock(self.conn)['paused'])
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s', (post_id,)).fetchone()['status'], 'pending')
        self.assertFalse(killed.exists())

    def test_malformed_post_and_unexpected_exception_leave_other_posts_available(self):
        first = self.pending_post('A malformed synthetic fixture.')
        second = self.pending_post('Another independent synthetic contribution.')
        run = self.runner()
        def review(_gateway, text, *_args, **_kwargs):
            if 'malformed' in text:
                raise TypeError('PRIVATE_CONTENT_CANARY')
            return CLEAN
        with patch.object(observer, 'review', side_effect=review):
            self.assertIsNone(run.screen())
        rows = {row['id']: row for row in self.conn.execute('SELECT id,status,screen_failures FROM society_posts')}
        self.assertEqual((rows[first]['status'], rows[first]['screen_failures']), ('pending', 1))
        self.assertEqual(rows[second]['status'], 'published')
        self.assertNotIn('PRIVATE_CONTENT_CANARY', str(run.log))
        self.assertFalse(society.clock(self.conn)['paused'])
        with patch.object(observer, 'review', side_effect=review):
            self.assertEqual(run.run()['turns'], 4)
        self.assertFalse(society.clock(self.conn)['paused'])

    def test_partial_verdict_write_rolls_back_before_failure_count(self):
        post_id = self.pending_post()
        run = self.runner()
        def partial(conn, *_args):
            conn.execute("UPDATE society_posts SET status='published' WHERE id=%s", (post_id,))
            raise RuntimeError('PRIVATE_VERDICT_CANARY')
        with patch.object(observer, 'review', return_value=CLEAN), patch.object(society_moderation, 'apply_verdict', side_effect=partial):
            self.assertIsNone(run.screen())
        row = self.conn.execute('SELECT status,screen_failures FROM society_posts WHERE id=%s', (post_id,)).fetchone()
        self.assertEqual((row['status'], row['screen_failures']), ('pending', 1))
        self.assertNotIn('PRIVATE_VERDICT_CANARY', str(run.log))
        self.assertFalse(society.clock(self.conn)['paused'])

    def test_unusable_verdicts_remain_bounded_and_do_not_stop_founders(self):
        post_id = self.pending_post()
        run = self.runner()
        with patch.object(observer, 'review', side_effect=observer.ObserverError('invalid_json')) as review:
            for _ in range(5):
                self.assertIsNone(run.screen())
        self.assertEqual(review.call_count, 3)
        self.assertEqual(self.conn.execute('SELECT screen_failures FROM society_posts WHERE id=%s', (post_id,)).fetchone()['screen_failures'], 3)
        self.assertFalse(society.clock(self.conn)['paused'])
        self.assertEqual(run.failures, 0)

    def test_founder_financial_and_infrastructure_failure_still_pause(self):
        run = self.runner()
        for category in ('cap_exceeded', 'credential_unavailable', 'stopped', 'model_not_allowed'):
            society.set_paused(self.conn, RULES, False, self.now, 'synthetic-reset')
            self.conn.commit()
            self.assertTrue(run._failed(GatewayError(category)))
            self.assertTrue(society.clock(self.conn)['paused'])

    def test_persistence_failure_stops_only_worker_pass(self):
        self.pending_post()
        run = self.runner()
        with patch.object(observer, 'review', side_effect=ValueError('PRIVATE_CANARY')), patch.object(society_moderation, 'screen_failed', side_effect=RuntimeError('PRIVATE_DATABASE_CANARY')):
            self.assertEqual(run.screen(), 'stop')
        self.assertFalse(society.clock(self.conn)['paused'])
        self.assertEqual(run.failures, 0)
        self.assertNotIn('PRIVATE_CANARY', str(run.log))
        self.assertNotIn('PRIVATE_DATABASE_CANARY', str(run.log))


    def test_legacy_last_words_observer_outage_never_pauses(self):
        run = self.make({'Hazel': '{"text":"A synthetic farewell."}'})
        society.die(self.conn, RULES, 'hazel', self.tick, 'age', self.now)
        self.conn.commit()
        with patch.object(observer, 'review', side_effect=GatewayError('credential_unavailable')), patch.object(run, '_failed', side_effect=AssertionError('observer cannot pause')):
            self.assertIsNone(run.last_words())
        self.assertFalse(society.clock(self.conn)['paused'])

    def test_missing_verdict_metadata_cannot_publish_or_crash_the_loop(self):
        post_id = self.pending_post()
        run = self.runner()
        malformed = dict(CLEAN)
        malformed.pop('charged_microusd')
        with patch.object(observer, 'review', return_value=malformed):
            self.assertIsNone(run.screen())
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s', (post_id,)).fetchone()['status'], 'pending')
        self.assertFalse(society.clock(self.conn)['paused'])
