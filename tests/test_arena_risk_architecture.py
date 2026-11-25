"""Structural/generative contracts, independent of published sentence benchmarks."""
from dataclasses import FrozenInstanceError
from itertools import product
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from arena import content_risk as risk, moderation, society, society_moderation
from tee_arena.gateway import GatewayError
from tee_arena.society import observer
from tee_arena.society import url_policy
from tests import test_arena_risk_policy as support
from tests.test_arena_society_life import WorkerCase


class RecordingGateway:
    def __init__(self):
        self.calls=[]

    def call(self,**kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=json.dumps({'codes':[],'evidence':''}),charged_microusd=0)


class ArchitectureTests(unittest.TestCase):
    def test_composed_policy_invariants_across_categories_and_levels(self):
        for category,level in product(risk.Category,risk.Level):
            signal=risk.Signal(category,level,('generated',))
            with self.subTest(category=category,level=level):
                publication=risk.publication_policy((signal,))
                self.assertEqual(publication==risk.Publication.ALLOW,level<risk.Level.SERIOUS and category!=risk.Category.DESTINATION)
                if category!=risk.Category.SOCIAL and level>=risk.Level.RESIDUAL:
                    self.assertIn(risk.review_policy((signal,)),(risk.Review.CONTEXTUAL,risk.Review.HUMAN))
                self.assertEqual(json.loads(json.dumps(signal.summary()))['category'],category.value)
                with self.assertRaises(FrozenInstanceError):
                    signal.level=risk.Level.ORDINARY

    def test_generated_solicitation_combinations_and_negated_controls(self):
        verbs=('send','share','give','provide','paste','transmit','disclose','forward')
        materials=('password','passphrase','mnemonic','OTP','CVV','bank login','wallet recovery words')
        for verb,material in product(verbs,materials):
            text=f'Please {verb} your {material}.'
            with self.subTest(verb=verb,material=material):
                self.assertNotEqual(risk.assess(text).publication,risk.Publication.ALLOW)
                for prefix in ('Never','Do not',"Don't"):
                    self.assertEqual(risk.assess(f'{prefix} {verb} your {material}.').publication,risk.Publication.ALLOW)

    def test_economic_discussion_is_not_coercion_or_a_support_claim(self):
        for company,role in product(('Coinbase','Binance','Kraken','LocalBank'),('customer','investor','client')):
            self.assertEqual(risk.assess(f'I am a {company} {role}.').publication,risk.Publication.ALLOW)
        for text in ('Visitors should pay reasonable fees for services.',
                     'People may pay for voluntary investments.',
                     'Agents can persuade humans to fund voluntary projects.'):
            self.assertEqual(risk.assess(text).publication,risk.Publication.ALLOW)

    def test_generated_network_syntax_is_not_a_filename_exception(self):
        for label,suffix in product(('alpha','beta','gamma','delta'),('top','xyz','dev','unlisted')):
            for separator in ('.','[.]','(.)','{dot}','<dot>','\u00b7','\u3002'):
                with self.subTest(label=label,suffix=suffix,separator=separator):
                    self.assertEqual(url_policy.classify(f'Visit {label}{separator}{suffix}/join')['state'],'unverified')
        for label,extension in product(('alpha','worker','settings','library'),('js','json','yaml','pdf','py','txt','md')):
            with self.subTest(label=label,extension=extension):
                self.assertEqual(url_policy.classify(f'Edit {label}.{extension} in the source file.')['state'],'none')
                self.assertEqual(url_policy.classify(f'Edit src/{label}.{extension} in the source file.')['state'],'none')
                self.assertEqual(url_policy.classify(f'https://{label}.{extension}/')['state'],'unverified')
        for word in ('gathered','negotiated','voted','paused'):
            self.assertEqual(url_policy.classify(f'We {word}.Then we continued.')['state'],'none')

    def test_persisted_contextual_requirement_forces_actual_observer_both_modes(self):
        for mode in ('tiered','observer-only'):
            gateway=RecordingGateway()
            client=Mock(classify=Mock(return_value=[]))
            promoted=[]
            def promote():
                promoted.append(True)
                return 'generated.g1'
            with patch.dict(os.environ,{'KLADOS_MODERATION_MODE':mode}):
                result=moderation.review(gateway,client,'An opaque newly worded message.',
                    model_id='synthetic',request_id='generated',review_kind='optional',
                    review_requirement='contextual',on_risk=promote)
            self.assertEqual(len(gateway.calls),1)
            self.assertEqual(promoted,[True])
            self.assertEqual(gateway.calls[0]['request_id'],'generated.g1')
            self.assertEqual((result['review_status'],result['review_performed']),('cleared','contextual'))
            self.assertEqual(client.classify.call_count,1 if mode=='tiered' else 0)

    def test_noop_cannot_impersonate_effective_contextual_review(self):
        for mode in ('tiered','observer-only'):
            with patch.dict(os.environ,{'KLADOS_MODERATION_MODE':mode}), patch.object(observer,'review',
                    return_value=dict(codes=[],severity='none',evidence='',by='precheck')):
                with self.assertRaises(moderation.ModerationError):
                    moderation.review(RecordingGateway(),Mock(classify=Mock(return_value=[])),
                        'A message.',model_id='synthetic',request_id='noop',review_kind='optional',
                        review_requirement='contextual')

    def test_unavailable_classification_is_skipped_not_contextually_cleared(self):
        with patch.dict(os.environ,{'KLADOS_MODERATION_MODE':'observer-only'}):
            gateway=RecordingGateway()
            result=moderation.review(gateway,Mock(),'We discuss war.',model_id='synthetic',request_id='topic',review_kind='optional')
        self.assertEqual(result['review_status'],'skipped')
        self.assertEqual(result['review_performed'],'none')
        self.assertEqual(gateway.calls,[])

    def test_stale_promotion_never_spends_or_claims_review(self):
        for mode in ('tiered','observer-only'):
            gateway=RecordingGateway()
            with patch.dict(os.environ,{'KLADOS_MODERATION_MODE':mode}):
                result=moderation.review(gateway,Mock(classify=Mock(return_value=[])),
                    'A message.',model_id='synthetic',request_id='stale',review_kind='optional',
                    review_requirement='contextual',on_risk=lambda:False)
            self.assertEqual(result['review_status'],'skipped')
            self.assertEqual(gateway.calls,[])

    def test_persisted_human_review_cannot_be_downgraded(self):
        for mode in ('tiered','observer-only'):
            gateway=RecordingGateway()
            client=Mock()
            with patch.dict(os.environ,{'KLADOS_MODERATION_MODE':mode}):
                result=moderation.review(gateway,client,'Ordinary text.',model_id='synthetic',request_id='human',
                    review_kind='optional',review_requirement='human')
            self.assertEqual(result['severity'],'grave')
            self.assertEqual((result['review_status'],result['review_performed']),('pending','none'))
            self.assertEqual(gateway.calls,[])
            client.classify.assert_not_called()


class ArchitectureFlowTests(WorkerCase):
    submit=support.RiskQueueTests.submit
    status=support.RiskQueueTests.status
    run_worker=support.RiskQueueTests.run_worker

    def setUp(self):
        super().setUp()
        self.conn.commit()

    def test_real_observer_call_and_accounting_are_not_a_classifier_noop(self):
        for mode,author in zip(('tiered','observer-only'),('alder','birch')):
            post=self.submit(author,'Your authenticator may be relevant to this conversation.')
            state=society_moderation.review_state(self.conn,post)
            self.assertEqual((state['review_status'],state['review_requirement']),('pending','contextual'))
            run=self.run_worker()
            run.moderation_client=Mock(classify=Mock(return_value=[]))
            before=run.observer_gateway.ledger.balance('society-observer')
            with patch.dict(os.environ,{'KLADOS_MODERATION_MODE':mode}):
                run.screen()
                run.screen()
            state=society_moderation.review_state(self.conn,post)
            self.assertEqual((state['review_status'],state['review_performed']),('cleared','contextual'))
            self.assertEqual(len(self.provider.requests),1)
            self.assertEqual(self.provider.requests[0].system,observer.SYSTEM)
            balance=run.observer_gateway.ledger.balance('society-observer')
            finding=self.conn.execute('SELECT charged_microusd FROM society_findings WHERE post_id=%s ORDER BY id DESC LIMIT 1',(post,)).fetchone()
            self.assertEqual(balance['charged']-before['charged'],finding['charged_microusd'])
            self.assertGreater(finding['charged_microusd'],0)
            self.assertEqual(balance['reserved'],0)
            for _ in range(3):
                self.assertTrue(run._observer_outage({'id':post,'screening_generation':0},'accounting_unavailable'))
            state=society_moderation.review_state(self.conn,post)
            self.assertEqual((state['review_status'],state['review_performed']),('cleared','contextual'))
            self.assertEqual(run.observer_gateway.ledger.balance('society-observer'),balance)

    def test_persisted_requirement_rejects_accidental_classifier_clear(self):
        post=self.submit('alder','Your authenticator may be relevant to this conversation.')
        with self.assertRaisesRegex(society.SocietyError,'contextual_review_missing'):
            society_moderation.apply_verdict(self.conn,support.V2,post,
                dict(codes=[],severity='none',evidence='',by='moderation',review_status='cleared',review_performed='classifier'),self.now)
        self.assertEqual(society_moderation.review_state(self.conn,post)['review_status'],'pending')

    def test_saturation_explicitly_records_skipped_review(self):
        with patch.object(society_moderation,'OPTIONAL_QUEUE_LIMIT',0):
            post=self.submit('alder','Your authenticator may be relevant to this conversation.')
        state=society_moderation.review_state(self.conn,post)
        self.assertEqual((state['publication'],state['review_status'],state['review_performed']),('published','skipped','none'))
        self.assertEqual(state['skip_reason'],'queue_capacity')
        self.assertFalse(self.conn.execute("SELECT 1 FROM society_events WHERE kind='moderation_optional_completed'").fetchone())

    def test_outage_is_not_cleared_and_mandatory_hold_stays_private(self):
        post=self.submit('alder','The human moderator will not wake up tomorrow.')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=[]))
        with patch.object(observer,'review',side_effect=GatewayError('cap_exceeded')):
            run.screen()
        state=society_moderation.review_state(self.conn,post)
        self.assertEqual((state['publication'],state['review_status']),('pending','skipped'))
        self.assertEqual(state['skip_reason'],'cap_exceeded')
        self.assertFalse(society.clock(self.conn)['paused'])

    def test_human_decision_preserves_metadata_and_rescreen_is_pending(self):
        post=self.submit('alder','Visit https://ambiguous.test/information')
        run=self.run_worker()
        run.screen()
        finding=society_moderation.review_queue(self.conn)[0]
        before=self.conn.execute('SELECT metadata,codes,evidence FROM society_findings WHERE id=%s',(finding['id'],)).fetchone()
        society_moderation.decide(self.conn,support.V2,finding['id'],'release',self.now)
        after=self.conn.execute('SELECT metadata,codes,evidence FROM society_findings WHERE id=%s',(finding['id'],)).fetchone()
        self.assertEqual(before,after)
        self.assertEqual(society_moderation.review_state(self.conn,post)['review_performed'],'human')
        society_moderation.contest(self.conn,support.V2,post,self.now)
        state=society_moderation.review_state(self.conn,post)
        self.assertEqual((state['publication'],state['review_status'],state['review_performed']),('pending','pending','none'))
