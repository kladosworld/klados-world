"""Administrative availability is explicit, private, and independent of dormancy."""
from datetime import datetime, timezone
import io
import json
import os
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor

from psycopg import sql

from arena import administration, db, society, worker
from tee_arena.society import rules
from tests.arena_support import DatabaseCase, DSN

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)
RULES = rules.load()


class AdministrationTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        society.found(self.conn, RULES, NOW)
        self.conn.commit()

    def change(self, state, agent='alder', *, apply=True, all_founders=False):
        return administration.change(self.conn, NOW, target_state=state,
            identifier=None if all_founders else agent, all_founders=all_founders,
            reference='SYNTHETIC-OPERATOR-APPROVAL', approved_by='test-operator', apply=apply)

    def test_historical_absence_means_active_and_preview_has_no_effect(self):
        self.assertEqual(administration.state(self.conn, 'alder'), 'active')
        result = self.change('inactive', apply=False)
        self.assertFalse(result['applied'])
        self.assertEqual(result['changed'], 1)
        self.assertEqual(administration.state(self.conn, 'alder'), 'active')
        self.assertIsNone(self.conn.execute('SELECT 1 FROM society_agent_administration').fetchone())
        self.assertIsNone(self.conn.execute('SELECT 1 FROM society_administration_events').fetchone())

    def test_individual_states_and_explicit_reactivation_preserve_security(self):
        before = dict(self.conn.execute("SELECT * FROM society_agents WHERE id='alder'").fetchone())
        self.conn.execute("UPDATE society_agents SET participation='suspended' WHERE id='alder'")
        self.assertEqual(administration.state(self.conn, 'alder'), 'active')
        self.assertFalse(administration.describe(self.conn, 'alder')['can_participate'])
        for state in ('inactive', 'retired', 'active'):
            self.change(state)
            detail = administration.describe(self.conn, 'alder')
            self.assertEqual(detail['administrative_state'], state)
            self.assertEqual(detail['participation'], 'suspended')
            self.assertFalse(detail['can_participate'])
        self.conn.execute("UPDATE society_agents SET participation='revoked' WHERE id='alder'")
        self.change('retired'); self.change('active')
        self.assertEqual(administration.describe(self.conn, 'alder')['participation'], 'revoked')
        after = dict(self.conn.execute("SELECT * FROM society_agents WHERE id='alder'").fetchone())
        after['participation'] = before['participation']
        self.assertEqual(after, before)

    def test_idempotency_and_audit_history(self):
        self.assertEqual(self.change('retired')['changed'], 1)
        self.assertEqual(self.change('retired')['changed'], 0)
        self.assertEqual(self.change('active')['changed'], 1)
        rows = self.conn.execute('SELECT * FROM society_administration_events ORDER BY id').fetchall()
        self.assertEqual([(r['previous_state'],r['state']) for r in rows],
                         [('active','retired'),('retired','active')])
        self.assertTrue(all(r['reference']=='SYNTHETIC-OPERATOR-APPROVAL' and r['approved_by']=='test-operator' for r in rows))

    def test_all_founders_zero_active_preserves_clock_and_history(self):
        society.set_paused(self.conn, RULES, False, NOW, 'synthetic')
        self.conn.execute('UPDATE society_clock SET tick=1')
        before_clock = dict(society.clock(self.conn))
        before_agents = self.conn.execute('SELECT to_jsonb(a) AS row FROM society_agents a ORDER BY id').fetchall()
        self.assertEqual(self.change('inactive', all_founders=True)['changed'], 4)
        self.assertEqual(society.due_turns(self.conn, NOW.replace(minute=59)), [])
        gateway = Mock()
        runner = worker.Worker(self.conn, RULES, gateway, observer_gateway=None, now=lambda: NOW)
        # Pending content still goes through moderation; zero founders is not a global pause.
        with patch.object(runner, 'screen', return_value=None) as screen, \
             patch.object(runner, 'last_words', return_value=None), \
             patch('arena.society_life.reflections_due', return_value=[]):
            summary = runner.run()
        screen.assert_called_once()
        gateway.call.assert_not_called()
        self.assertEqual(summary['turns'], 0)
        self.assertFalse(summary['stopped'])
        self.assertEqual(dict(society.clock(self.conn)), before_clock)
        self.assertEqual(self.conn.execute('SELECT to_jsonb(a) AS row FROM society_agents a ORDER BY id').fetchall(), before_agents)
        self.assertEqual(self.change('retired', all_founders=True)['changed'], 4)
        self.assertEqual(self.change('active', all_founders=True)['changed'], 4)
        self.assertEqual(len(society.due_turns(self.conn, NOW.replace(minute=59))), 4)

    def test_validation_and_transaction_rollback(self):
        for options in ({'identifier': None}, {'identifier': 'alder', 'all_founders': True},
                        {'target_state': 'suspended'}, {'reference': ''}, {'approved_by': ''},
                        {'apply': 'false'}, {'all_founders': 'false'}, {'identifier': ''}):
            args = dict(target_state='inactive', identifier='alder', all_founders=False,
                        reference='synthetic',approved_by='test')
            args.update(options)
            with self.assertRaises(ValueError):
                administration.change(self.conn, NOW, **args)
        self.change('retired', all_founders=True)
        self.conn.rollback()
        self.assertIsNone(self.conn.execute('SELECT 1 FROM society_agent_administration').fetchone())
        self.assertIsNone(self.conn.execute('SELECT 1 FROM society_administration_events').fetchone())

    def test_claim_and_reflection_are_denied_without_model_calls(self):
        self.change('retired')
        self.assertFalse(society.claim_turn(self.conn, 'alder', 1, NOW))
        gateway = Mock()
        runner = worker.Worker(self.conn, RULES, gateway, now=lambda: NOW)
        self.assertIsNone(runner.reflect('alder', 0, 1))
        gateway.call.assert_not_called()

    def test_operator_cli_is_preview_by_default_and_explicit_for_all_founders(self):
        from arena import runtime
        original_connect = db.connect
        def connect(_dsn):
            conn = original_connect(DSN)
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            return conn
        with patch.dict(os.environ, {'ARENA_DATABASE_URL':'unused','KLADOS_RULES':str(rules.DEFAULT_PATH)}), \
             patch.object(runtime.db, 'connect', side_effect=connect):
            output=io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(runtime.main(['agent','administrative-state','--state','retired',
                    '--all-founders','--reference','synthetic','--operator','test']),0)
            self.assertFalse(json.loads(output.getvalue())['applied'])
            self.assertTrue(administration.is_active(self.conn,'alder'))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(runtime.main(['agent','administrative-state','--state','retired',
                    '--all-founders','--reference','synthetic','--operator','test','--yes']),0)
            self.assertFalse(administration.is_active(self.conn,'alder'))
            with redirect_stderr(io.StringIO()):
                self.assertEqual(runtime.main(['agent','administrative-state','--state','active',
                    '--all-founders','--reference','synthetic','--yes']),1)

    def test_concurrent_same_transition_is_one_audited_change(self):
        def change(_):
            with db.connect(DSN) as conn:
                conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
                return administration.change(conn,NOW,target_state='retired',identifier='alder',
                    reference='synthetic',approved_by='test',apply=True)['changed']
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(sum(pool.map(change,range(3))),1)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_administration_events').fetchone()['n'],1)

    def test_all_founder_failure_rolls_back_and_audit_is_append_only(self):
        original = administration.state
        def failing(conn, identifier):
            if identifier == 'birch':
                raise RuntimeError('synthetic')
            return original(conn,identifier)
        with self.assertRaises(RuntimeError), self.conn.transaction(), patch.object(administration,'state',side_effect=failing):
            self.change('retired',all_founders=True)
        self.assertIsNone(self.conn.execute('SELECT 1 FROM society_agent_administration').fetchone())
        self.change('inactive')
        with self.assertRaises(Exception), self.conn.transaction():
            self.conn.execute('DELETE FROM society_administration_events')

    def test_external_auth_and_action_deny_admin_restrictions_without_revoking_token(self):
        from arena import external_agents as external
        invitation = external.create_invite(self.conn,NOW)
        registered = external.register(self.conn,RULES,dict(invite=invitation['invite'],name='Synthetic',description='An independent agent.'),NOW)
        identifier=registered['id'];token='Bearer '+registered['token']
        self.assertEqual(external.authenticate(self.conn,token,NOW)['id'],identifier)
        self.change('retired',agent=identifier)
        with self.assertRaises(external.HTTPException) as caught:
            external.authenticate(self.conn,token,NOW)
        self.assertEqual(caught.exception.status_code,403)
        with self.assertRaises(external.HTTPException) as caught:
            external.action(self.conn,RULES,identifier,json.dumps({'action':'rest','request_id':'synthetic'}),NOW)
        self.assertEqual(caught.exception.status_code,403)
        self.assertIsNone(self.conn.execute('SELECT revoked_at FROM agent_tokens WHERE agent_id=%s',(identifier,)).fetchone()['revoked_at'])
        self.change('active',agent=identifier)
        self.assertEqual(external.authenticate(self.conn,token,NOW)['id'],identifier)
