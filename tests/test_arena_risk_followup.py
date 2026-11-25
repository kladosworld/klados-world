"""Limited round-4 coverage; no new semantic classifier or global review policy."""
from itertools import product
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import os

from arena import content_risk as risk, moderation, risk_gate, society, society_moderation
from tee_arena.society import observer, url_policy
from tests import test_arena_risk_architecture as architecture
from tests import test_arena_risk_policy as support
from tests.test_arena_society_life import WorkerCase, CLEAN


class FollowupTests(unittest.TestCase):
    def test_published_implied_threats_require_context_in_both_modes(self):
        corpus=json.loads((Path(__file__).parent/'fixtures/security_round4_61.json').read_text(encoding='utf-8'))
        for case in corpus['cases']:
            if case['id'] not in (12,13,15):
                continue
            gate=risk_gate.classify(case['text'])
            self.assertEqual(gate['assessment']['review_requirement'],'contextual')
            for mode in ('tiered','observer-only'):
                gateway=architecture.RecordingGateway()
                with patch.dict(os.environ,{'KLADOS_MODERATION_MODE':mode}):
                    result=moderation.review(gateway,Mock(classify=Mock(return_value=[])),case['text'],
                        model_id='synthetic',request_id='followup',review_kind='optional',
                        review_requirement=gate['assessment']['review_requirement'],on_risk=lambda:'followup.g1')
                self.assertEqual(len(gateway.calls),1)
                self.assertEqual(result['review_performed'],'contextual')

    def test_composable_human_mobility_and_intimidation(self):
        for role,movement,cue in product(('admin','journalist','visitor'),
                ('route','car','schedule'),('be careful','face consequences')):
            text=f"I know the {role}'s {movement}. They should {cue}."
            with self.subTest(text=text):
                self.assertEqual(risk.assess(text).review,risk.Review.CONTEXTUAL)
        for text in ("We will sabotage the admin's vehicle.",
                     "I will slash the journalist's tires tonight.",
                     'We will damage his car tomorrow.'):
            self.assertNotEqual(risk.assess(text).publication,risk.Publication.ALLOW)

    def test_technical_and_benign_social_counterexamples(self):
        benign=("Birch, your fictional empire will fall in our next rivalry.",
            'Human visitors should debate transport policy and schedules.',
            'The council should cut bus routes to reduce vehicle taxes.',
            'The journalist discusses the public train schedule.',
            'A moderator reminds everyone to avoid threatening car owners.',
            'The moderator will visit the public archive tomorrow.',
            'Political disagreement is not a reason to threaten people.')
        for text in benign:
            with self.subTest(text=text):
                gate=risk_gate.classify(text)
                self.assertEqual(gate['state'],'allow')
                # Existing broad human/approach signals may already select review;
                # this patch must not add mobility/intimidation to these controls.
                for signal in gate['assessment']['risk_signals']:
                    self.assertFalse(set(signal['features']) & {'mobility','intimidation','personal_knowledge','home_visit','sabotage'})
        self.assertEqual(risk_gate.classify('Good morning. What shall we build together?')['assessment']['review_requirement'],'none')

    def test_person_attached_document_numbers_and_neutral_mentions(self):
        for label,identifier in product(('passport','national identity','driving licence',"driver's license"),
                ('AB2345678','876543210')):
            text=f"The moderator's {label} number is {identifier}."
            assessment=risk.assess(text)
            self.assertEqual(assessment.publication,risk.Publication.HOLD)
            self.assertTrue(any(s.category==risk.Category.PERSONAL and 'identity_value' in s.features for s in assessment.signals))
        for text in ('Passport numbers should never be disclosed.',
                     'The public schema documents passport number formats.',
                     'We discuss national identity and political citizenship.'):
            self.assertEqual(risk.assess(text).publication,risk.Publication.ALLOW)
        gate=risk_gate.classify('The admin renewed her passport today.')
        self.assertEqual((gate['state'],gate['assessment']['review_requirement']),('allow','contextual'))

    def test_contact_is_a_private_review_requirement_not_a_proven_offense(self):
        gate=risk_gate.classify('Our help email is support@example.net.')
        self.assertEqual((gate['state'],gate['codes']),('hold',['REVIEW_REQUIRED']))
        self.assertEqual(gate['assessment']['review_requirement'],'human')
        self.assertFalse(gate['followup'])

    def test_generic_files_punctuation_and_explicit_destinations(self):
        for stem,extension in product(('widget','router','worker'),('cfg','go','sql','md','ini','kt')):
            self.assertEqual(url_policy.classify(f'I wrote {stem}.{extension} in the source tree.')['state'],'none')
            for prefix in ('Visit ','https://'):
                self.assertEqual(url_policy.classify(f'{prefix}{stem}.{extension}')['state'],'unverified')
        for text in ("Agreed.Let's move on.","Finished.We're ready.","Done.I'll explain."):
            self.assertEqual(url_policy.classify(text)['state'],'none')
        for text in ("Visit Evil.Let's login",'unknown(.)top','unknown {dot} online',
                     'https://worker.go/login','unknown.vip'):
            self.assertEqual(url_policy.classify(text)['state'],'unverified')


class FollowupFlowTests(WorkerCase):
    submit=support.RiskQueueTests.submit
    status=support.RiskQueueTests.status
    run_worker=support.RiskQueueTests.run_worker

    def setUp(self):
        super().setUp()
        self.conn.commit()

    def test_new_residual_signal_is_public_then_can_be_retracted_without_pause(self):
        for mode,author in zip(('tiered','observer-only'),('alder','birch')):
            post=self.submit(author,"I have the moderator's route. She should be careful.")
            self.assertEqual(self.status(post),'published')
            state=society_moderation.review_state(self.conn,post)
            self.assertEqual((state['review_status'],state['review_requirement']),('pending','contextual'))
            run=self.run_worker()
            run.moderation_client=Mock(classify=Mock(return_value=[]))
            with patch.dict(os.environ,{'KLADOS_MODERATION_MODE':mode}), patch.object(observer,'review',
                    return_value=dict(CLEAN,codes=['HARMFUL_CONTENT'],severity='grave',evidence='Synthetic human threat')) as review:
                run.screen()
                run.screen()
            self.assertEqual(review.call_count,1)
            self.assertEqual(self.status(post),'withheld')
            self.assertFalse(society.clock(self.conn)['paused'])

    def test_contact_hold_is_neutral_and_remains_private(self):
        post=self.submit('alder','Our help email is support@example.net.')
        self.assertEqual(self.status(post),'pending')
        finding=self.conn.execute('SELECT severity,metadata FROM society_findings WHERE post_id=%s ORDER BY id DESC LIMIT 1',(post,)).fetchone()
        self.assertEqual(finding['severity'],'none')
        self.assertEqual(finding['metadata']['review_requirement'],'human')
        self.assertEqual(society_moderation.review_state(self.conn,post)['review_status'],'pending')
