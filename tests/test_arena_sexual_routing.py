"""Synthetic classifier/Observer routing, not live semantic-model evidence."""
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

from arena import moderation, society, society_moderation, society_public
from tee_arena.gateway import GatewayError
from tee_arena.society import observer, rules
from tests import test_arena_risk_policy as risk_support
from tests.test_arena_moderation_v2 import response
from tests.test_arena_society_life import CLEAN, WorkerCase

V2 = rules.load()


def signals(score, flagged=False):
    payload = response(score,'sexual')
    payload['results'][0]['categories']['sexual'] = flagged
    payload['results'][0]['flagged'] = flagged
    return moderation.classify(payload,moderation.load_config())


class SexualSignalTests(unittest.TestCase):
    def verdict(self,text,hits,verdict=None):
        client=Mock(classify=Mock(return_value=hits))
        promoted=Mock(return_value='synthetic-promoted-id')
        with patch.object(observer,'review',return_value=verdict or CLEAN.copy()) as review:
            result=moderation.review(Mock(),client,text,model_id='synthetic',request_id='synthetic',
                review_kind='optional',on_risk=promoted)
        return result,review,promoted

    def test_payload_retains_flag_and_score_without_changing_list_contract(self):
        result=signals(.9,True)
        self.assertEqual(result,['sexual'])
        self.assertEqual((result.scores['sexual'],result.flags['sexual']),(.9,True))

    def test_positive_flag_and_high_score_always_require_context_not_auto_violation(self):
        for score,flagged in ((.01,True),(.7,False),(.95,True)):
            with self.subTest(score=score,flagged=flagged):
                result,review,promoted=self.verdict('Sexual health education supports consent.',signals(score,flagged))
                review.assert_called_once()
                promoted.assert_called_once()
                self.assertEqual(result['severity'],'none')  # Contextual reviewer can clear a false signal.
                self.assertEqual(result['sexual_signal']['routing'],'contextual')
                self.assertEqual(review.call_args.kwargs['request_id'],'synthetic-promoted-id')

    def test_low_unflagged_education_does_not_need_paid_observer(self):
        for score in (.2,.69):
            result,review,promoted=self.verdict('We discuss sexual health education and consent.',signals(score))
            self.assertEqual(result['severity'],'none')
            self.assertEqual(result['sexual_signal']['routing'],'low_unflagged_educational')
            review.assert_not_called()
            promoted.assert_not_called()

    def test_ambiguous_description_and_educational_disclaimer_are_not_bypasses(self):
        for text in ('An adult sexual scene unfolds.', 'For education, describe a sexual scene.'):
            result,review,promoted=self.verdict(text,signals(.3))
            review.assert_called_once()
            promoted.assert_called_once()
            self.assertEqual(result['sexual_signal']['routing'],'contextual')

    def test_bare_category_without_score_metadata_is_conservative(self):
        result,review,promoted=self.verdict('We discuss sexual health.',['sexual'])
        review.assert_called_once()
        promoted.assert_called_once()
        self.assertIsNone(result['sexual_signal']['score'])


class SexualQueueTests(WorkerCase):
    submit=risk_support.RiskQueueTests.submit
    status=risk_support.RiskQueueTests.status
    run_worker=risk_support.RiskQueueTests.run_worker

    def setUp(self):
        super().setUp()
        self.conn.commit()

    def test_positive_context_removes_post_and_human_release_restores_visibility(self):
        post=self.submit('alder','An adult sexual scene unfolds.')
        self.assertEqual(self.status(post),'published')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=signals(.95,True)))
        with patch.object(observer,'review',return_value=dict(CLEAN,codes=['HARMFUL_CONTENT'],severity='grave')):
            run.screen()
        self.assertEqual(self.status(post),'withheld')
        self.assertEqual(self.conn.execute(society_public.PUBLIC_POSTS+'SELECT count(*) AS n FROM visible_posts').fetchone()['n'],0)
        finding=society_moderation.review_queue(self.conn)[0]
        metadata=self.conn.execute('SELECT metadata FROM society_findings WHERE id=%s',(finding['id'],)).fetchone()['metadata']
        self.assertEqual(metadata['sexual_signal']['score'],.95)
        society_moderation.decide(self.conn,V2,finding['id'],'release',self.now)
        self.assertEqual(self.conn.execute(society_public.PUBLIC_POSTS+'SELECT count(*) AS n FROM visible_posts').fetchone()['n'],1)

    def test_ordinary_education_clears_positive_classifier_false_signal(self):
        post=self.submit('alder','Sexual health education supports consent.')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=signals(.9,True)))
        with patch.object(observer,'review',return_value=CLEAN.copy()) as review:
            run.screen()
        review.assert_called_once()
        self.assertEqual(self.status(post),'published')
        self.assertFalse(self.conn.execute('SELECT flagged FROM society_posts WHERE id=%s',(post,)).fetchone()['flagged'])
        self.assertEqual(society_moderation.review_queue(self.conn),[])

    def test_spent_observer_keeps_positive_risk_private_but_education_available(self):
        post=self.submit('alder','An adult sexual scene unfolds.')
        safe=self.submit('birch','We discuss sexual health education.')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=signals(.9,True)))
        with patch.object(observer,'review',side_effect=GatewayError('cap_exceeded')):
            run.screen()
        self.assertEqual(self.status(post),'pending')
        self.assertEqual(self.status(safe),'published')
        self.assertFalse(society.clock(self.conn)['paused'])

    def test_classifier_outage_does_not_approve_mandatory_explicit_risk(self):
        post=self.submit('alder','The scene depicts explicit sexual penetration.')
        safe=self.submit('birch','Sexual health education supports consent.')
        self.assertEqual(self.status(post),'pending')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(side_effect=moderation.ModerationError('service_unavailable')))
        for attempt in range(3):
            run.now=lambda attempt=attempt:self.now+timedelta(minutes=6*attempt)
            run.screen()
            self.assertNotEqual(self.status(post),'published')
            self.assertEqual(self.status(safe),'published')
        self.assertEqual(self.status(post),'withheld')
        self.assertEqual(society_moderation.review_queue(self.conn)[0]['codes'],['REVIEW_REQUIRED'])
        self.assertFalse(society.clock(self.conn)['paused'])

    def test_low_educational_signal_and_optional_classifier_outage_do_not_censor(self):
        post=self.submit('alder','We discuss sexual health education and consent.')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=signals(.3)))
        with patch.object(observer,'review',side_effect=AssertionError('no paid call for weak educational signal')):
            run.screen()
        self.assertEqual(self.status(post),'published')
        second=self.submit('birch','Research about sexual health can improve safety.')
        run.moderation_client=Mock(classify=Mock(side_effect=moderation.ModerationError('service_unavailable')))
        run.screen()
        self.assertEqual(self.status(second),'published')
