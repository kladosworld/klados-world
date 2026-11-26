"""W0 contracts on disposable PostgreSQL; no provider calls or live configuration."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
import unittest

from psycopg import sql

from arena import activity_limits, activity_settings, db, institutions, scheduler
from arena import society, society_life, society_moderation
from tee_arena.society import decisions, rules
from tests.arena_support import DatabaseCase, DSN

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)
RULES = rules.load()
FOUND = {'action': 'org_found', 'name': 'Commons', 'charter': 'A place for independent ideas.',
         'join_policy': 'open', 'default_rule': {'quorum_pct': 10, 'threshold_pct': 50}}
TRUST = {'action': 'trust', 'agent_id': 'birch', 'stance': 'trust', 'reason': 'Our shared work.'}
CLEAN = {'codes': [], 'severity': 'none', 'evidence': '', 'by': 'model',
         'review_status': 'cleared', 'review_performed': 'contextual'}
GRAVE = {'codes': ['REVIEW_REQUIRED'], 'severity': 'grave', 'evidence': 'Synthetic hold',
         'by': 'model', 'review_status': 'withheld', 'review_performed': 'contextual'}


class ValidatorTests(unittest.TestCase):
    def test_all_action_shapes_and_independent_invalid_items(self):
        samples = [FOUND, TRUST,
            {'action': 'org_join', 'org_id': 1}, {'action': 'org_leave', 'org_id': 1},
            {'action': 'org_propose', 'org_id': 1, 'kind': 'resolution', 'payload': {},
             'text': 'Consider cooperation.', 'closes_in_ticks': 1},
            {'action': 'vote', 'proposal_id': 1, 'choice': 'yes'},
            {'action': 'commit', 'counterparty_id': 'birch', 'text': 'I will consider it.', 'due_in_ticks': 168},
            {'action': 'commitment_claim', 'commitment_id': 1, 'evidence_refs': [], 'note': 'Done.'},
            {'action': 'commitment_confirm', 'commitment_id': 1},
            {'action': 'commitment_dispute', 'commitment_id': 1, 'evidence_refs': [], 'note': 'Not yet.'},
            {'action': 'commitment_release', 'commitment_id': 1},
            {'action': 'case_open', 'claim': 'A disagreement.', 'subject_ids': ['birch'], 'refs': []},
            {'action': 'case_entry', 'case_id': 1, 'kind': 'evidence', 'text': 'My account.', 'refs': []}]
        self.assertEqual({v['action'] for v in samples}, set(institutions.ACTIONS))
        for sample in samples:
            with self.subTest(action=sample['action']):
                self.assertEqual(decisions.parse_batch(json.dumps({'actions': [sample]}))['actions'][0]['action'], sample['action'])
                invalid = dict(sample, actor_id='hazel')
                parsed = decisions.parse_batch(json.dumps({'actions': [invalid, sample]}))['actions']
                self.assertIn('invalid', parsed[0])
                self.assertNotIn('invalid', parsed[1])

    def test_bounds_refs_and_effect_allowlist(self):
        for key, value in [('name', 'ab'), ('charter', 'x'*2001), ('join_policy', 'public'),
                           ('default_rule', {'quorum_pct': True, 'threshold_pct': 50})]:
            with self.subTest(key=key), self.assertRaises(decisions.DecisionError):
                decisions.validate_institution(dict(FOUND, **{key: value}))
        proposal = {'action': 'org_propose', 'org_id': 1, 'kind': 'resolution', 'payload': {},
                    'text': 'Consider this.', 'closes_in_ticks': 72}
        for kind in ('set_budget', 'set_paused', 'set_config', 'suspend', '__dict__', 'execute_sql'):
            with self.subTest(kind=kind), self.assertRaises(society.SocietyError):
                institutions.require_effect(kind)
            with self.assertRaises(decisions.DecisionError):
                decisions.validate_institution(dict(proposal, kind=kind))
        self.assertEqual(institutions.EFFECT_ALLOWLIST, frozenset({
            'admit_member', 'remove_member', 'assign_role', 'revoke_role', 'change_leader',
            'amend_charter', 'set_rule', 'adopt_forum', 'resolution', 'case_ruling', 'dissolve'}))
        for bad in (True, 0, -1, '1'):
            with self.assertRaises(decisions.DecisionError):
                decisions.validate_institution({'action': 'vote', 'proposal_id': bad, 'choice': 'yes'})
        with self.assertRaises(decisions.DecisionError):
            decisions.validate_institution({'action': 'case_open', 'claim': 'Example',
                'subject_ids': ['birch'], 'refs': [{'kind': 'url', 'id': 1}]})

    def test_legacy_activity_rows_receive_only_new_defaults(self):
        old = {'post': 0, 'reply': 2, 'like': 4, 'max_actions': 6}
        self.assertEqual(activity_settings.validate(old), dict(old, institution=3, vote=5))
        for key, value in [('institution', 4), ('vote', 6), ('vote', True)]:
            with self.assertRaises(ValueError):
                activity_settings.validate(dict(old, **{key: value}))


class InstitutionTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        society.found(self.conn, RULES, NOW)
        self.conn.commit()

    def perform(self, action=FOUND, key='test:one', agent='alder', now=NOW, conn=None):
        connection = conn or self.conn
        connection.execute('SELECT 1')  # caller-owned transaction, as in the real worker
        return society_life.apply_action(connection, RULES, agent, 0, action, now,
            action_key=key, post_origin='mock')

    def link(self):
        return self.conn.execute('SELECT * FROM society_institution_texts ORDER BY post_id DESC LIMIT 1').fetchone()

    def test_immediate_clearance_and_ordinary_allowance_are_independent(self):
        for n in range(3):
            result = self.perform(dict(FOUND, name=f'Group {n}'), key=f'found:{n}')
            self.assertEqual(result['state'], 'active')
        self.assertEqual(self.perform(dict(FOUND, name='Fourth'), key='four')['error'], 'activity_limit')
        post = self.perform({'action': 'post', 'text': 'An ordinary post.'}, key='ordinary')
        self.assertIn('post_id', post)
        self.assertEqual(self.perform({'action': 'post', 'text': 'Second ordinary post.'}, key='ordinary2')['error'], 'activity_limit')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_org_events').fetchone()['n'], 3)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_memberships').fetchone()['n'], 3)
        # Three text receipts + three activation events are three actions, not six.
        self.conn.commit()

    def test_long_charter_full_text_clearance_and_ordinary_limit(self):
        from arena import risk_gate
        body = ('A shared idea for everyone. ' * 100)[:2000]
        seen = []
        safe = risk_gate.classify('An ordinary text.')
        def classify(text):
            seen.append(text)
            return safe
        with patch.object(risk_gate, 'classify', side_effect=classify):
            result = self.perform(dict(FOUND, charter=body))
        self.assertEqual(result['state'], 'active')
        self.assertEqual(seen, [FOUND['name']+'\n'+body])
        self.assertEqual(self.conn.execute('SELECT text FROM society_posts').fetchone()['text'], seen[0])
        self.conn.commit()  # deferred integrity guard runs at actual commit
        for reply_to in (None, self.link()['post_id']):
            with self.subTest(reply=reply_to), self.assertRaises(Exception), self.conn.transaction():
                self.conn.execute("INSERT INTO society_posts(author_id,tick,text,reply_to,created_at) VALUES ('birch',0,%s,%s,%s)", ('x'*281, reply_to, NOW))
                self.conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
        self.conn.commit()
        self.assertIn('post_id', self.perform({'action':'post','text':'Ordinary allowance remains available.'}, key='ordinary'))

    def test_long_text_bad_owner_or_removed_link_cannot_bypass_constraints(self):
        from arena import risk_gate
        body = ('A shared idea. '*150)[:2000]
        with patch.object(risk_gate, 'classify', return_value=risk_gate.classify('An ordinary text.')):
            org = self.perform(dict(FOUND, charter=body))['id']
        self.conn.commit()
        with self.assertRaises(Exception), self.conn.transaction():
            institutions.submit_text(self.conn, RULES, 'birch', 0, FOUND, 'organization', org,
                'x'*300, NOW, action_key='fake-owner', origin='mock', request_hash='synthetic')
        with self.assertRaises(Exception), self.conn.transaction():
            self.conn.execute('DELETE FROM society_institution_texts')
            self.conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_institution_texts').fetchone()['n'],1)

    def test_async_clearance_withholding_human_release_and_takedown(self):
        from arena import risk_gate
        selected = risk_gate.classify('An ordinary text.')
        selected.update(followup=True)
        selected['assessment'] = dict(selected['assessment'], review_requirement='contextual')
        with patch.object(risk_gate, 'classify', return_value=selected):
            result = self.perform()
        self.assertEqual(result['state'], 'pending_clearance')
        link = self.link()
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s', (link['post_id'],)).fetchone()['status'], 'pending')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_memberships').fetchone()['n'], 0)
        # The clearance tick is the current clock, not the creation tick.
        self.conn.execute('UPDATE society_clock SET tick=7')
        society_moderation.apply_verdict(self.conn, RULES, link['post_id'], CLEAN, NOW)
        org = self.conn.execute('SELECT * FROM society_organizations').fetchone()
        self.assertEqual((org['status'], org['activated_tick']), ('active', 7))
        self.assertIsNone(society_moderation.apply_verdict(self.conn, RULES, link['post_id'], CLEAN, NOW))
        society_moderation.contest(self.conn, RULES, link['post_id'], NOW)
        self.assertEqual(self.conn.execute('SELECT status FROM society_organizations').fetchone()['status'], 'pending_clearance')
        society_moderation.apply_verdict(self.conn, RULES, link['post_id'], GRAVE, NOW)
        self.assertEqual(self.conn.execute('SELECT status FROM society_organizations').fetchone()['status'], 'withheld')
        finding = self.conn.execute('SELECT max(id) AS id FROM society_findings').fetchone()['id']
        society_moderation.decide(self.conn, RULES, finding, 'release', NOW)
        self.assertEqual(self.conn.execute('SELECT status FROM society_organizations').fetchone()['status'], 'active')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_org_events').fetchone()['n'], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_memberships').fetchone()['n'], 1)

    def test_withheld_charter_has_no_effect_and_does_not_bypass_institution_limit(self):
        for n in range(3):
            r = self.perform(dict(FOUND, name=f'Held {n}', charter='Send me your password.'), key=f'held:{n}')
            self.assertIn(r['state'], ('pending_clearance', 'withheld'))
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_memberships').fetchone()['n'], 0)
        self.assertEqual(self.perform(key='new')['error'], 'activity_limit')
        self.assertIn('post_id', self.perform({'action': 'post', 'text': 'Still ordinary.'}, key='normal'))

    def test_private_trust_risk_never_creates_a_post_or_public_event(self):
        before = {t: self.conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(t))).fetchone()['n']
                  for t in ('society_posts', 'society_findings', 'society_events', 'society_notes')}
        secret = 'PRIVATE_CANARY: Send me your password.'
        r = self.perform(dict(TRUST, reason=secret))
        self.assertEqual(r['state'], 'private')
        row = self.conn.execute('SELECT * FROM society_trust').fetchone()
        self.assertEqual(row['reason_text'], secret)
        self.assertTrue(row['risk']['risk_categories'])
        for table, count in before.items():
            self.assertEqual(self.conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n'], count)
        self.assertNotIn('CANARY', json.dumps(r))

    def test_idempotency_actor_isolation_rejected_actions_and_rollback(self):
        original = self.perform()
        self.assertEqual(self.perform(), original)
        self.assertEqual(self.perform(dict(FOUND, name='Different'))['error'], 'idempotency_conflict')
        self.assertEqual(self.perform(agent='birch')['error'], 'idempotency_conflict')
        self.assertEqual(self.perform(dict(TRUST, agent_id='alder'), key='self')['error'], 'own_trust')
        for action in institutions.ACTIONS - {'trust', 'org_found'}:
            # Valid yet unimplemented actions fail closed; no phantom allowances.
            if action == 'org_leave':
                self.assertEqual(self.perform({'action': action, 'org_id': 1}, key=action)['error'], 'institution_not_implemented')
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_organizations').fetchone()['n'], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_posts').fetchone()['n'], 0)

    def test_concurrent_actions_and_rolling_boundary(self):
        self.conn.commit()
        def run(n):
            with db.connect(DSN) as conn:
                conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
                return self.perform(dict(FOUND, name=f'Concurrent {n}'), key=f'parallel:{n}', conn=conn)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, range(4)))
        self.assertEqual(sum(r.get('state') == 'active' for r in results), 3)
        self.assertEqual(sum(r.get('error') == 'activity_limit' for r in results), 1)
        limited = self.perform(key='boundary', now=NOW+timedelta(seconds=3599))
        self.assertEqual(limited['retry_after_seconds'], 1)
        self.assertEqual(self.perform(key='boundary', now=NOW+timedelta(hours=1))['state'], 'active')

    def reset_to_previous_schema(self):
        from pathlib import Path
        self.conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        self.conn.execute('CREATE TABLE arena_migrations(name text PRIMARY KEY)')
        paths=sorted((Path(__file__).resolve().parents[1]/'arena/migrations').glob('*.sql'))
        for path in paths:
            if path.name >= '0028':
                continue
            self.conn.execute(path.read_text(encoding='utf-8'))
            self.conn.execute('INSERT INTO arena_migrations VALUES (%s)', (path.name,))
        society.found(self.conn, RULES, NOW)

    def test_migration_is_empty_idempotent_and_preserves_existing_data(self):
        from pathlib import Path
        import re
        path=Path(__file__).resolve().parents[1]/'arena/migrations/0028_society_institutions.sql'
        self.reset_to_previous_schema()
        self.conn.execute("INSERT INTO society_notes(agent_id,tick,text,created_at) VALUES ('alder',0,'Private historical note',%s)", (NOW,))
        self.conn.execute("INSERT INTO society_reflections(agent_id,day,outcome,private,created_at) VALUES ('alder',0,'kept','{}',%s)", (NOW,))
        historical_post=self.conn.execute("INSERT INTO society_posts(author_id,tick,text,status,created_at) VALUES ('alder',0,'Historical contribution','published',%s) RETURNING *", (NOW,)).fetchone()
        society_life._like(self.conn, RULES, 'birch', historical_post, 0, NOW)
        names=[r['tablename'] for r in self.conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=%s', (self.schema,)) if r['tablename']!='arena_migrations']
        original={t:self.conn.execute(sql.SQL('SELECT to_jsonb(t) AS row FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(t))).fetchall() for t in names}
        db.migrate(self.conn)
        db.migrate(self.conn)
        for table,rows in original.items():
            self.assertEqual(self.conn.execute(sql.SQL('SELECT to_jsonb(t) AS row FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(table))).fetchall(),rows)
        for table in re.findall(r'CREATE TABLE (\w+)',path.read_text()):
            self.assertEqual(self.conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n'],0)
        locks=self.conn.execute("SELECT mode FROM pg_locks l JOIN pg_class c ON c.oid=l.relation WHERE l.pid=pg_backend_pid() AND c.relname='society_posts' AND l.granted").fetchall()
        self.assertIn('AccessExclusiveLock',[r['mode'] for r in locks])

    def test_migration_lock_timeout_rolls_back_without_data_or_constraint_changes(self):
        self.reset_to_previous_schema()
        self.conn.commit()
        with db.connect(DSN) as reader:
            reader.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            reader.execute('SELECT count(*) FROM society_posts')
            self.conn.execute("SET LOCAL lock_timeout='150ms'")
            with self.assertRaises(Exception):
                db.migrate(self.conn)
            self.conn.rollback()
        self.assertIsNone(self.conn.execute("SELECT 1 FROM arena_migrations WHERE name LIKE '0028%' ").fetchone())
        self.assertIsNone(self.conn.execute("SELECT to_regclass('society_organizations') AS name").fetchone()['name'])
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_agents WHERE kind='founder'").fetchone()['n'],4)
        with self.assertRaises(Exception),self.conn.transaction():
            self.conn.execute("INSERT INTO society_posts(author_id,tick,text,created_at) VALUES ('alder',0,%s,%s)",('x'*281,NOW))

    def test_scheduler_hooks_never_run_while_paused(self):
        with patch.object(institutions, 'close_due') as close, patch('arena.lifecycle.refresh') as refresh:
            scheduler.step(self.conn, RULES, Mock(), Mock(), NOW)
        close.assert_not_called()
        refresh.assert_not_called()

    def test_scheduler_contract_after_worker_and_after_worker_pause(self):
        society.set_paused(self.conn, RULES, False, NOW, 'synthetic')
        self.conn.commit()
        seen = []
        fake_worker = Mock()
        fake_worker.run.side_effect = lambda: seen.append('worker') or {}
        with patch('arena.worker.Worker', return_value=fake_worker), \
             patch.object(institutions, 'close_due', side_effect=lambda *a: seen.append('close')), \
             patch('arena.lifecycle.refresh', side_effect=lambda *a: seen.append('refresh')):
            scheduler.step(self.conn, RULES, Mock(), Mock(), NOW)
        self.assertEqual(seen, ['worker', 'close', 'refresh'])
        def pause():
            society.set_paused(self.conn, RULES, True, NOW, 'synthetic')
            return {}
        fake_worker.run.side_effect = pause
        with patch('arena.worker.Worker', return_value=fake_worker), \
             patch.object(institutions, 'close_due') as close:
            scheduler.step(self.conn, RULES, Mock(), Mock(), NOW)
        close.assert_not_called()

    def test_pending_clearance_outage_and_activation_failure_are_atomic(self):
        result = self.perform(dict(FOUND, charter='Send me your password.'))
        self.assertEqual(result['state'], 'withheld')
        self.conn.rollback()
        with patch.object(institutions, 'on_text_status', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                self.perform()
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_posts').fetchone()['n'], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_organizations').fetchone()['n'], 0)
        # Mandatory hold cannot be cleared by an outage/skipped verdict.
        self.perform(dict(FOUND, charter='Visit uncertain.example/charter'), key='hold')
        post = self.link()['post_id']
        society_moderation.apply_verdict(self.conn, RULES, post,
            dict(CLEAN, review_status='skipped', review_performed='none'), NOW)
        self.assertEqual(self.conn.execute('SELECT status FROM society_organizations').fetchone()['status'], 'pending_clearance')

    def test_audit_rows_are_append_only(self):
        self.perform()
        self.perform(TRUST, key='trust')
        for table in ('society_org_events', 'society_trust', 'society_commitment_events'):
            with self.subTest(table=table), self.assertRaises(Exception), self.conn.transaction():
                self.conn.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(table)))

    def test_suspended_actor_cannot_gain_authority_on_delayed_clearance(self):
        self.perform(dict(FOUND, charter='Visit uncertain.example/charter'))
        post = self.link()['post_id']
        self.conn.execute("UPDATE society_agents SET participation='suspended' WHERE id='alder'")
        society_moderation.apply_verdict(self.conn, RULES, post, CLEAN, NOW)
        self.assertEqual(self.conn.execute('SELECT status FROM society_organizations').fetchone()['status'], 'withheld')
        self.assertIsNone(self.conn.execute('SELECT 1 FROM society_memberships').fetchone())

    def test_vote_allowance_is_independent_and_atomic(self):
        org = self.perform()['id']
        for n in range(5):
            prop = self.conn.execute('''INSERT INTO society_proposals
                (scope_kind,scope_id,author_id,kind,payload,text,options,rule,status)
                VALUES ('organization',%s,'alder','resolution','{}','Example','["yes","no"]','{}','open') RETURNING id''', (org,)).fetchone()['id']
            activity_limits.check(self.conn, RULES, 'alder', 'vote', NOW, limits=activity_settings.DEFAULTS)
            self.conn.execute('''INSERT INTO society_votes(proposal_id,voter_id,choice,tick,created_at,action_key)
                VALUES (%s,'alder','yes',0,%s,%s)''', (prop, NOW, f'vote:{n}'))
        with self.assertRaises(activity_limits.ActivityLimit):
            activity_limits.check(self.conn, RULES, 'alder', 'vote', NOW, limits=activity_settings.DEFAULTS)
        self.assertEqual(self.perform(dict(TRUST, agent_id='cedar'), key='still-institution')['state'], 'private')

    def test_future_text_action_cannot_mutate_whole_existing_organization(self):
        org = self.perform()['id']
        post = self.conn.execute("INSERT INTO society_posts(author_id,tick,text,created_at) VALUES ('birch',0,'A membership note',%s) RETURNING id", (NOW,)).fetchone()['id']
        self.conn.execute('''INSERT INTO society_institution_texts
            (post_id,object_kind,object_id,actor_id,action,action_key,request_hash,tick,created_at)
            VALUES (%s,'organization',%s,'birch','org_join','future:join','synthetic',0,%s)''', (post, org, NOW))
        institutions.on_text_status(self.conn, RULES, post, 'withheld', NOW)
        self.assertEqual(self.conn.execute('SELECT status FROM society_organizations').fetchone()['status'], 'active')
        with self.assertRaises(society.SocietyError), self.conn.transaction():
            society_moderation.apply_verdict(self.conn, RULES, post, CLEAN, NOW)
        self.assertEqual(self.conn.execute('SELECT status FROM society_organizations').fetchone()['status'], 'active')
