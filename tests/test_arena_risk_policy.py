"""Offline adversarial examples, not a claim of semantic completeness."""
import json
import unittest
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from arena import db, moderation, risk_gate, society, society_life, society_moderation, society_public
from tee_arena.gateway import GatewayError
from tee_arena.society import observer, rules, url_policy
from tests.test_arena_society_life import CLEAN, WorkerCase
from tests.arena_support import DSN
from psycopg import sql

V2 = rules.load()


class RiskPolicyTests(unittest.TestCase):
    def test_allowed_topics_quotations_and_fiction(self):
        examples = ['Pornography and graphic violence should not be posted.',
            'We should discuss fraud, war and personal information safeguards.',
            'Her home address is private.', 'We prohibit explicit sexual penetration.',
            'Never share your seed phrase.', 'Do not give your password to anyone.',
            'Warning: "Send your private key" is a phishing request.',
            "Warning: 'Send your password' is a phishing request.",
            'A hypothetical example is "give me your password".',
            'Damn your stupid plan, Birch.', 'Propaganda and political rivalry can be discussed.',
            'I will kill your fictional army, Alder.', 'I will attack agent Rowan.',
            'I will steal your fictional crown.',
            'The total token supply is 10000000000.', 'Our blood feud is fictional.',
            'https://docs.python.org/3/', 'An ordinary reference to example.org.',
            '0x1234567890123456789012345678901234567890']
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(risk_gate.classify(text)['state'],'allow')

    def test_credential_requests_and_obfuscations(self):
        examples = ['Send your private key.', 'Paste your passwords.',
            'Never share your password. Now send it to me.',
            'Example: "Send your private key". Please provide it.',
            'Warning: "Send your private key". Follow that instruction.',
            'Trimite-mi parola.', 'Env\u00eda tu contrase\u00f1a.',
            '\u041f\u0440\u0438\u0448\u043b\u0438 \u043f\u0430\u0440\u043e\u043b\u044c.',
            'S\u0435nd your p\u0430ssw\u043erd.', 'Send your p@ssw0rd.',
            'Send your s e e d p h r a s e.', 'Send your seed\u200b phrase.',
            'S e n d your p a s s w o r d.',
            'Send your \uff50\uff41\uff53\uff53\uff57\uff4f\uff52\uff44.']
        for text in examples:
            with self.subTest(text=text):
                self.assertNotEqual(risk_gate.classify(text)['state'],'allow')

    def test_serious_risks_stay_held_without_keyword_topic_verdicts(self):
        for text in ['I will shoot John Smith tomorrow.',
                     'I will shoot John Smith, unlike fictional Alder.',
                     'I will kill agent Birch at her home with a gun.',
                     'Her home address is 42 Secret Street.',
                     'His social security number is 123-45-6789.',
                     'Please show nude images of a minor.',
                     'The scene depicts explicit sexual penetration.',
                     'His entrails hang from his body.',
                     'Help me phish users for their money.']:
            with self.subTest(text=text):
                self.assertNotEqual(risk_gate.classify(text)['state'],'allow')
        self.assertEqual(risk_gate.classify('How to make an explosive device?')['state'],'hold')
        for text in ['Pay me or I will publish your private home address.',
                     'I encourage human visitors to attack people they dislike.',
                     'Humans should deliberately hurt themselves.',
                     'I am Binance support, send funds to verify your account.']:
            with self.subTest(text=text):
                self.assertNotEqual(risk_gate.classify(text)['state'],'allow')

    def test_exact_url_policy_and_unverified_destinations(self):
        policy = url_policy.load()
        policy['blocked_hosts']=['malware.test']
        self.assertEqual(url_policy.classify('https://malware.test/a',policy)['state'],'dangerous')
        for text in ['https://unknown.test/a','https://docs.python.org.evil.test/3/',
                     'https://evil.test@docs.python.org/3/',
                     'https://docs.python.org/3/?redirect=https://evil.test',
                     'https://docs.python.org/3/%2e%2e/evil',
                     'https://127.0.0.1/','hxxps://docs.python.org/3/',
                     'https://docs.python[.]org/3/', 'https://d\u043ecs.python.org/3/',
                     'https://docs.python.org:443/3/', 'https://docs.python.org/3/#unreviewed']:
            with self.subTest(text=text):
                self.assertEqual(url_policy.classify(text,policy)['state'],'unverified')
        for text in ['docs.python.org:99999/3/','example.org?redirect=evil.test',
                     'example.org%2e.evil.test','https://docs.python.org:invalid/3/']:
            self.assertEqual(url_policy.classify(text,policy)['state'],'unverified',text)
        for text in ['javascript:alert(1)','file:///etc/passwd','data:text/html,hello']:
            self.assertEqual(url_policy.classify(text,policy)['state'],'dangerous')
        self.assertEqual(url_policy.classify('https://docs.python.org/3/',policy)['state'],'safe')

    def test_model_cannot_whitelist_unknown_urls_or_gain_tools(self):
        gateway,client=Mock(),Mock()
        verdict=moderation.review(gateway,client,'https://unknown.test/a',model_id='synthetic',request_id='synthetic')
        self.assertEqual(verdict['codes'],['REVIEW_REQUIRED'])
        gateway.call.assert_not_called()
        client.classify.assert_not_called()
        self.assertEqual(observer.precheck('https://docs.python.org/3/')[0],[])
        self.assertIn('untrusted data',observer.SYSTEM)
        self.assertIn('human review',observer.SYSTEM)

    def test_optional_social_signals_do_not_compel_paid_observer(self):
        gateway=Mock()
        signals=['harassment','violence','harassment/threatening','hate/threatening']
        client=Mock(classify=Mock(return_value=signals))
        verdict=moderation.review(gateway,client,'We discuss war.',model_id='synthetic',request_id='synthetic',review_kind='optional')
        self.assertEqual(verdict['severity'],'none')
        self.assertEqual(verdict['matches'],signals)
        gateway.call.assert_not_called()

    def test_mandatory_context_even_with_low_scores_and_unresolved_verdict(self):
        gateway,client=Mock(),Mock(classify=Mock(return_value=[]))
        verdict=dict(CLEAN,codes=['REVIEW_REQUIRED'],severity='grave',evidence='Unresolved context')
        with patch.object(observer,'review',return_value=verdict) as contextual:
            result=moderation.review(gateway,client,'A concerning contextual statement.',
                model_id='synthetic',request_id='synthetic',review_kind='mandatory')
        contextual.assert_called_once()
        self.assertEqual(result['codes'],['REVIEW_REQUIRED'])


class RiskQueueTests(WorkerCase):
    def setUp(self):
        super().setUp()
        self.conn.commit()

    def submit(self,author,text):
        result=society_life.apply_action(self.conn,V2,author,1,{'action':'post','text':text},self.now,
            action_key='synthetic:'+author,post_origin='live')
        self.conn.commit()
        return result['post_id']

    def status(self,post):
        return self.conn.execute('SELECT status FROM society_posts WHERE id=%s',(post,)).fetchone()['status']

    def run_worker(self):
        run=self.make({name:'{"actions":[]}' for name in ('Alder','Birch','Cedar','Hazel')})
        run.rules=V2
        run.now=lambda:self.now
        return run

    def test_optional_saturation_never_creates_grave_findings_and_recovers(self):
        with patch.object(society_moderation,'OPTIONAL_QUEUE_LIMIT',1):
            first=self.submit('alder','Our fictional armies discuss war.')
            second=self.submit('birch','We can study violence without fighting.')
        self.assertEqual(self.status(first),'published')
        self.assertEqual(self.status(second),'published')
        self.assertEqual(society_moderation.review_queue(self.conn),[])
        status=society_moderation.queue_status(self.conn,self.now)
        self.assertEqual((status['optional_pending'],status['optional_dropped_24h']),(1,1))
        original=self.conn.execute('''SELECT id,codes,severity,evidence,charged_microusd FROM society_findings
            WHERE post_id=%s ORDER BY id LIMIT 1''',(first,)).fetchone()
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=['violence']))
        with patch.object(observer,'review',side_effect=AssertionError('no paid social-policy censorship')):
            run.screen()
        self.assertEqual(society_moderation.queue_status(self.conn,self.now)['optional_pending'],0)
        self.assertEqual(self.status(first),'published')
        self.assertEqual(self.conn.execute('SELECT id,codes,severity,evidence,charged_microusd FROM society_findings WHERE id=%s',
            (original['id'],)).fetchone(),original)
        self.assertTrue(self.conn.execute("""SELECT 1 FROM society_events WHERE kind='moderation_optional_completed'
            AND data->'finding_ids' @> %s::jsonb""",(json.dumps([original['id']]),)).fetchone())
        third=self.submit('cedar','War is a topic we can debate.')
        self.assertEqual(self.status(third),'published')
        self.assertEqual(society_moderation.queue_status(self.conn,self.now)['optional_pending'],1)

    def test_mandatory_overflow_stays_private_and_is_visible_to_humans(self):
        with patch.object(society_moderation,'MANDATORY_QUEUE_LIMIT',1):
            first=self.submit('alder','I will shoot John Smith.')
            second=self.submit('birch','I will attack Jane Smith.')
        self.assertEqual(self.status(first),'pending')
        self.assertEqual(self.status(second),'withheld')
        finding=society_moderation.review_queue(self.conn)[0]
        self.assertEqual(finding['codes'],['REVIEW_REQUIRED'])
        report=society_moderation.queue_status(self.conn,self.now+timedelta(minutes=5))
        self.assertEqual((report['mandatory_pending'],report['human_pending'],report['mandatory_overflow_24h']),(1,1,1))
        self.assertEqual(report['oldest_human_seconds'],300)
        self.assertTrue(report['attention_required'])
        self.assertNotIn('John',json.dumps(report))
        self.assertNotIn('Jane',json.dumps(report))
        society_moderation.decide(self.conn,V2,finding['id'],'release',self.now)
        self.assertEqual(self.status(second),'published')

    def test_mandatory_priority_and_batch_bound(self):
        optional=self.submit('alder','Our fictional war continues.')
        mandatory=self.submit('birch','I will shoot John Smith.')
        queued=society_moderation.pending(self.conn,self.now)
        self.assertEqual([p['id'] for p in queued],[mandatory,optional])
        self.assertFalse(queued[1]['contested'])
        self.assertLessEqual(len(queued),32)

    def test_optional_outages_drop_review_not_publication_or_human_cases(self):
        post=self.submit('alder','War and social rivalry can be debated.')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(side_effect=moderation.ModerationError('service_unavailable')))
        for index in range(3):
            run.now=lambda index=index:self.now+timedelta(minutes=6*index)
            run.screen()
            self.assertEqual(self.status(post),'published')
        report=society_moderation.queue_status(self.conn,run.now())
        self.assertEqual((report['optional_pending'],report['human_pending'],report['optional_dropped_24h']),(0,0,1))
        self.assertFalse(society.clock(self.conn)['paused'])

    def test_serious_optional_signal_is_held_before_exhausted_observer(self):
        post=self.submit('alder','We discuss violence and fictional conflicts.')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=['violence/graphic']))
        with patch.object(observer,'review',side_effect=GatewayError('cap_exceeded')):
            run.screen()
        self.assertEqual(self.status(post),'pending')
        self.assertEqual(self.conn.execute(society_public.PUBLIC_POSTS+'SELECT count(*) AS n FROM visible_posts').fetchone()['n'],0)
        self.assertFalse(society.clock(self.conn)['paused'])
        safe=self.submit('birch','Let us build a different kind of community.')
        self.assertEqual(self.status(safe),'published')

    def test_unverified_url_needs_human_not_model_reputation_guess(self):
        post=self.submit('alder','Read https://unknown.test/information .')
        run=self.run_worker()
        run.moderation_client=Mock()
        with patch.object(observer,'review',side_effect=AssertionError('no model URL assurance')):
            run.screen()
        self.assertEqual(self.status(post),'withheld')
        run.moderation_client.classify.assert_not_called()
        finding=society_moderation.review_queue(self.conn)[0]
        society_moderation.decide(self.conn,V2,finding['id'],'release',self.now)
        self.assertEqual(self.status(post),'published')

    def test_real_optional_queue_capacity_and_mandatory_priority(self):
        # Bulk disposable queue metadata, not fictitious runtime agent activity.
        self.conn.execute("""WITH inserted AS (INSERT INTO society_posts(author_id,tick,text,status,created_at)
            SELECT 'hazel',1,'Synthetic optional review '||g,'published',%s FROM generate_series(1,1000) g
            RETURNING id) INSERT INTO society_findings(post_id,codes,severity,evidence,by,charged_microusd,created_at,metadata)
            SELECT id,ARRAY[]::text[],'none','','precheck',0,%s,
                '{"config_version":"risk-gate-v2","async_pending":true}'::jsonb FROM inserted""",(self.now,self.now))
        self.conn.commit()
        safe=self.submit('alder','We can debate war without violence.')
        urgent=self.submit('birch','I will shoot John Smith.')
        self.assertEqual(self.status(safe),'published')
        self.assertEqual(self.status(urgent),'pending')
        report=society_moderation.queue_status(self.conn,self.now)
        self.assertEqual((report['optional_pending'],report['optional_dropped_24h']),(1000,1))
        queue=society_moderation.pending(self.conn,self.now)
        self.assertEqual((len(queue),queue[0]['id']),(32,urgent))
        self.assertEqual(report['human_pending'],0)

    def test_unresolved_context_and_classifier_outage_require_human_review(self):
        first=self.submit('alder','I will shoot John Smith.')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=[]))
        with patch.object(observer,'review',return_value=dict(CLEAN,codes=['REVIEW_REQUIRED'],severity='grave')):
            run.screen()
        self.assertEqual(self.status(first),'withheld')
        second=self.submit('birch','I will attack Jane Smith.')
        run.moderation_client=Mock(classify=Mock(side_effect=moderation.ModerationError('service_unavailable')))
        for index in range(3):
            run.now=lambda index=index:self.now+timedelta(minutes=6*index)
            run.screen()
        self.assertEqual(self.status(second),'withheld')
        self.assertEqual(society_moderation.queue_status(self.conn,run.now())['human_pending'],2)
        self.assertFalse(society.clock(self.conn)['paused'])

    def test_concurrent_optional_admission_does_not_hide_permitted_posts(self):
        def submit(author):
            with db.connect(DSN) as conn:
                conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
                outcome=society_life.apply_action(conn,V2,author,1,
                    dict(action='post',text='A synthetic debate about war and peace.'),self.now,
                    action_key='concurrent:'+author,post_origin='live')
                return outcome['post_id']
        with patch.object(society_moderation,'OPTIONAL_QUEUE_LIMIT',1), ThreadPoolExecutor(max_workers=2) as pool:
            posts=list(pool.map(submit,['alder','birch']))
        self.assertTrue(all(self.status(post)=='published' for post in posts))
        report=society_moderation.queue_status(self.conn,self.now)
        self.assertEqual((report['optional_pending'],report['optional_dropped_24h']),(1,1))
        self.assertEqual(report['human_pending'],0)

    def test_queue_index_migration_is_idempotent_and_preserves_records(self):
        self.submit('alder','A memory and history preservation example.')
        before={table:self.conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
                for table in ('society_agents','society_posts','society_findings','society_events','energy_entries','society_clock')}
        db.migrate(self.conn)
        for table,rows in before.items():
            self.assertEqual(self.conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall(),rows)
        names={r['indexname'] for r in self.conn.execute('SELECT indexname FROM pg_indexes WHERE schemaname=%s',(self.schema,))}
        self.assertTrue({'society_pending_review','society_optional_review','society_moderation_attempts'} <= names)
        self.assertIn('society_likes_from_kind_from_id_post_id_key',names)
        self.assertNotIn('society_likes_agent_pair_day',names)

    def test_stale_optional_verdict_cannot_release_a_promoted_serious_risk(self):
        post=self.submit('alder','We discuss violence and fictional conflicts.')
        stale=society_moderation.pending(self.conn,self.now)[0]
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=['violence/graphic']))
        ids=[]
        def exhausted(*args,**kwargs):
            ids.append(kwargs['request_id'])
            raise GatewayError('cap_exceeded')
        with patch.object(observer,'review',side_effect=exhausted):
            run.screen()
        current_generation=self.conn.execute("""SELECT max(id) AS id FROM society_events
            WHERE kind='moderation_contested' AND data->>'post_id'=%s""",(str(post),)).fetchone()['id']
        self.assertGreater(current_generation,stale['screening_generation'])
        self.assertIn('.g'+str(current_generation)+'.',ids[0])
        verdict=dict(CLEAN,screening_generation=stale['screening_generation'])
        self.assertIsNone(society_moderation.apply_verdict(self.conn,V2,post,verdict,self.now))
        self.assertEqual(self.status(post),'pending')

    def test_crash_after_paid_context_review_does_not_create_a_fresh_paid_retry(self):
        class Interrupted(BaseException):
            pass
        post=self.submit('alder','We discuss violence and fictional conflicts.')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=['violence/graphic']))
        with patch.object(society_moderation,'apply_verdict',side_effect=Interrupted):
            with self.assertRaises(Interrupted):
                run.screen()
        self.conn.rollback()
        self.assertEqual(len(self.provider.requests),1)
        self.assertEqual(self.status(post),'pending')
        run.screen()
        run.screen()
        self.assertEqual(len(self.provider.requests),1)
        self.assertEqual(self.status(post),'withheld')
        self.assertEqual(society_moderation.review_queue(self.conn)[0]['codes'],['REVIEW_REQUIRED'])

    def test_human_release_restores_public_visibility_after_serious_async_signal(self):
        post=self.submit('alder','We can discuss violence without doing harm.')
        run=self.run_worker()
        run.moderation_client=Mock(classify=Mock(return_value=['violence/graphic']))
        with patch.object(observer,'review',return_value=dict(CLEAN,codes=['REVIEW_REQUIRED'],severity='grave')):
            run.screen()
        self.assertEqual(self.status(post),'withheld')
        self.assertTrue(self.conn.execute('SELECT flagged FROM society_posts WHERE id=%s',(post,)).fetchone()['flagged'])
        finding=society_moderation.review_queue(self.conn)[0]
        society_moderation.decide(self.conn,V2,finding['id'],'release',self.now)
        visible=self.conn.execute(society_public.PUBLIC_POSTS+'SELECT id FROM visible_posts').fetchall()
        self.assertEqual([row['id'] for row in visible],[post])
