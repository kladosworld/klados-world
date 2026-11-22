"""Offline hosted-classifier response contracts; no real key or network request."""
import os
import unittest
from unittest.mock import Mock, patch

import httpx

from arena import moderation


class ClassifierResponseTests(unittest.TestCase):
    def payload(self, score=0, flagged=False):
        scores = dict.fromkeys(moderation.CATEGORIES, 0.0)
        scores['hate'] = score
        flags = dict.fromkeys(moderation.CATEGORIES, False)
        flags['hate'] = flagged
        return {'results': [{'flagged': flagged, 'categories': flags, 'category_scores': scores}]}

    def classify(self, payload, status=200):
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.post.return_value = httpx.Response(status, json=payload)
        with patch.dict(os.environ, OPENAI_MODERATION_API_KEY='synthetic-only'), \
                patch.object(httpx, 'Client', return_value=client) as factory:
            result = moderation.OpenAIModeration().classify('Synthetic text only.', moderation.load_config())
        self.assertFalse(factory.call_args.kwargs['follow_redirects'])
        self.assertFalse(factory.call_args.kwargs['trust_env'])
        client.post.assert_called_once()
        self.assertEqual(client.post.call_args.kwargs['json'],
                         {'model': 'omni-moderation-latest', 'input': 'Synthetic text only.'})
        return result

    def test_complete_clean_response_and_threshold_results(self):
        for score, flagged, expected in ((0, False, []), (0.199, False, []),
                                        (0.2, False, ['hate']), (0.7, False, ['hate']),
                                        (0, True, ['hate'])):
            with self.subTest(score=score, flagged=flagged):
                self.assertEqual(self.classify(self.payload(score, flagged)), expected)

    def test_missing_unknown_and_contradictory_response_never_approves(self):
        missing = self.payload()
        del missing['results'][0]['category_scores']['hate']
        unknown = self.payload()
        unknown['results'][0]['category_scores']['unexpected'] = 0
        contradictory = self.payload()
        contradictory['results'][0]['flagged'] = True
        for payload in ({}, {'results': []}, missing, unknown, contradictory):
            with self.subTest(payload=payload), self.assertRaises(moderation.ModerationError):
                self.classify(payload)

    def test_bad_score_types_and_bounds_never_approve(self):
        for score in (-0.1, 1.1, True, '0.1', None):
            with self.subTest(score=score), self.assertRaises(moderation.ModerationError):
                self.classify(self.payload(score))

    def test_transport_status_is_not_approval(self):
        for status in (302, 401, 429, 503):
            with self.subTest(status=status), self.assertRaises(moderation.ModerationError):
                self.classify(self.payload(), status)
