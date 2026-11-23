"""Explicit observer-only moderation, with no external service calls."""
import os
import unittest
from unittest.mock import Mock, patch

from arena import moderation
from tee_arena.society import observer


class ObserverOnlyTests(unittest.TestCase):
    def setUp(self):
        self.mode = patch.dict(os.environ, KLADOS_MODERATION_MODE='observer-only')
        self.mode.start()
        self.addCleanup(self.mode.stop)
        self.client = Mock()
        self.client.classify.side_effect = AssertionError('hosted classifier called')
        self.gateway = Mock()

    def review(self, text='An ordinary discussion.', **kwargs):
        return moderation.review(self.gateway, self.client, text,
                                 model_id='synthetic', request_id='screen.1', **kwargs)

    def test_clean_posts_require_observer_and_preserve_context_and_account(self):
        clean = dict(observer.parse('{"codes":[]}', 'A reply.'), charged_microusd=123)
        audit = Mock()
        with patch.object(observer, 'review', return_value=clean) as review:
            verdict = self.review('A reply.', replied_text='A public parent.', on_request=audit)
        review.assert_called_once_with(self.gateway, 'A reply.', 'A public parent.',
            model_id='synthetic', request_id='screen.1', account='society-observer')
        self.assertEqual(verdict['severity'], 'none')
        self.assertEqual(verdict['charged_microusd'], 123)
        self.assertEqual(verdict['config_version'], 'moderation-v2-observer-only')
        self.assertEqual(verdict['moderation_requests'], 0)
        self.assertEqual(verdict['matches'], ['observer_only'])
        self.client.classify.assert_not_called()
        audit.assert_not_called()

    def test_precheck_still_withholds_without_any_service(self):
        with patch.object(observer, 'review') as review:
            verdict = self.review('javascript:alert(1)')
        self.assertEqual(verdict['severity'], 'grave')
        self.assertEqual(verdict['by'], 'precheck')
        review.assert_not_called()
        self.client.classify.assert_not_called()

    def test_moderation_errors_never_return_a_publishable_verdict(self):
        for error in (observer.ObserverError('invalid_json'),
                      moderation.ModerationError('service_unavailable'),
                      RuntimeError('synthetic gateway outage')):
            with self.subTest(error=type(error).__name__), patch.object(observer, 'review', side_effect=error):
                with self.assertRaises(type(error)):
                    self.review()
        self.client.classify.assert_not_called()

    def test_observer_withholding_and_contested_cases_are_preserved(self):
        bad = dict(observer.parse('{"codes":["HARMFUL_CONTENT"],"evidence":"threat"}', 'A threat'),
                   charged_microusd=3)
        with patch.object(observer, 'review', return_value=bad) as review:
            verdict = self.review('A threat', contested=True)
        self.assertEqual(verdict['severity'], 'grave')
        self.assertEqual(verdict['codes'], ['HARMFUL_CONTENT'])
        review.assert_called_once()

    def test_addresses_and_economic_discussion_are_reviewed_not_automatically_blocked(self):
        for text in ('0x1234567890123456789012345678901234567890',
                     'So11111111111111111111111111111111111111112',
                     'Trading and voluntary fundraising are worth discussing.'):
            clean = dict(observer.parse('{"codes":[]}', text), charged_microusd=1)
            with self.subTest(text=text), patch.object(observer, 'review', return_value=clean) as review:
                self.assertEqual(self.review(text)['by'], 'observer')
                review.assert_called_once()

    def test_unknown_mode_refuses_even_precheck_before_any_service(self):
        for mode in ('', 'off', 'mock', 'observer_only'):
            with self.subTest(mode=mode), patch.dict(os.environ, KLADOS_MODERATION_MODE=mode):
                with self.assertRaisesRegex(moderation.ModerationError, 'invalid_config'):
                    self.review()
        self.client.classify.assert_not_called()

    def test_default_tiered_path_still_requires_hosted_classification(self):
        with patch.dict(os.environ, clear=True), patch.object(observer, 'review') as review:
            self.client.classify.side_effect = moderation.ModerationError('credential_unavailable')
            with self.assertRaisesRegex(moderation.ModerationError, 'credential_unavailable'):
                self.review()
        review.assert_not_called()

    def test_malformed_observer_output_is_not_a_clean_verdict(self):
        for text in ('not JSON', '{"codes":["unknown"]}', '{"approved":true}'):
            with self.subTest(text=text), self.assertRaises(observer.ObserverError):
                observer.parse(text, 'An ordinary discussion.')
