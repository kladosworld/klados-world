"""Organizations, clearance and deterministic voting on disposable PostgreSQL."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from arena import activity_settings, institution_governance as governance
from arena import society, society_life, society_moderation
from tee_arena.society import rules
from tests.arena_support import DatabaseCase

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)
RULES = rules.load()
FOUND = {'action': 'org_found', 'name': 'Commons', 'charter': 'Independent cooperation.',
         'join_policy': 'open', 'default_rule': {'quorum_pct': 10, 'threshold_pct': 50}}
CLEAN = {'codes': [], 'severity': 'none', 'evidence': '', 'by': 'model',
         'review_status': 'cleared', 'review_performed': 'contextual'}
GRAVE = {'codes': ['REVIEW_REQUIRED'], 'severity': 'grave', 'evidence': 'Synthetic',
         'by': 'model', 'review_status': 'withheld', 'review_performed': 'contextual'}


class GovernanceTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        society.found(self.conn, RULES, NOW)
        society.set_paused(self.conn, RULES, False, NOW, 'synthetic')
        self.conn.commit()
        self.n = 0
        self.now = NOW

    def perform(self, action, agent='alder', *, key=None):
        self.conn.execute('SELECT 1')
        self.n += 1
        # Each unit scenario advances only disposable wall-clock allowance time;
        # voting tick windows are advanced explicitly below, no model calls.
        self.now += timedelta(hours=1)
        tick = society.clock(self.conn)['tick']
        return society_life.apply_action(self.conn, RULES, agent, tick, action, self.now,
            action_key=key or f'governance:{self.n}', post_origin='mock',
            limits=activity_settings.DEFAULTS)

    def found(self, **changes):
        result = self.perform(dict(FOUND, **changes))
        self.assertEqual(result.get('state'), 'active', result)
        return result['id']

    def join(self, org, agent='birch', **changes):
        result = self.perform(dict(action='org_join', org_id=org, **changes), agent)
        self.assertNotIn('error', result)
        return result

    def propose(self, org, kind='resolution', payload=None, agent='alder', **changes):
        action = dict(action='org_propose', org_id=org, kind=kind, payload=payload or {},
                      text='Consider this proposal.', closes_in_ticks=1, **changes)
        result = self.perform(action, agent)
        self.assertEqual(result.get('state'), 'open', result)
        return result['id']

    def vote(self, proposal, agent='alder', choice='yes', **kwargs):
        return self.perform({'action': 'vote', 'proposal_id': proposal, 'choice': choice}, agent, **kwargs)

    def close(self, proposal):
        row = self.conn.execute('SELECT closes_tick FROM society_proposals WHERE id=%s', (proposal,)).fetchone()
        self.conn.execute('UPDATE society_clock SET tick=%s', (row['closes_tick'],))
        return governance.close_due(self.conn, RULES, self.now)

    def test_open_join_leave_and_no_permanent_founder_privilege(self):
        org = self.found()
        self.join(org)
        self.assertEqual(governance._member(self.conn, org, 'birch')['role'], 'member')
        self.assertEqual(self.perform({'action': 'org_leave', 'org_id': org}, 'birch')['state'], 'left')
        self.assertIsNone(governance._member(self.conn, org, 'birch'))
        self.join(org)
        proposal = self.propose(org, 'change_leader', {'agent_id': 'birch'}, agent='birch')
        self.vote(proposal)
        self.vote(proposal, 'birch')
        self.assertEqual(self.close(proposal)['applied'], 1)
        self.assertEqual(governance._member(self.conn, org, 'birch')['role'], 'leader')
        self.assertEqual(governance._member(self.conn, org, 'alder')['role'], 'member')
        remove = self.propose(org, 'remove_member', {'agent_id': 'alder'}, agent='birch')
        self.vote(remove, 'birch')
        self.assertEqual(self.close(remove)['applied'], 1)
        self.assertIsNone(governance._member(self.conn, org, 'alder'))

    def test_one_vote_and_replayed_action_do_not_duplicate(self):
        org = self.found()
        proposal = self.propose(org)
        action = {'action': 'vote', 'proposal_id': proposal, 'choice': 'yes'}
        first = self.perform(action, key='same:vote')
        self.assertEqual(self.perform(action, key='same:vote'), first)
        self.assertEqual(self.vote(proposal)['error'], 'already_voted')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_votes').fetchone()['n'], 1)
        self.assertEqual(self.vote(proposal, 'cedar')['error'], 'not_in_electorate')

    def test_quorum_threshold_and_repeat_close_are_deterministic(self):
        org = self.found(default_rule={'quorum_pct': 100, 'threshold_pct': 100})
        self.join(org)
        failed = self.propose(org)
        self.vote(failed)
        self.assertEqual(self.close(failed)['applied'], 0)
        self.assertEqual(self.conn.execute('SELECT status FROM society_proposals WHERE id=%s', (failed,)).fetchone()['status'], 'failed')
        passed = self.propose(org)
        self.vote(passed)
        self.vote(passed, 'birch')
        self.assertEqual(self.close(passed)['applied'], 1)
        before = self.conn.execute("SELECT count(*) AS n FROM society_org_events WHERE kind='decision_applied'").fetchone()['n']
        self.assertEqual(governance.close_due(self.conn, RULES, self.now), {'closed': 0, 'applied': 0})
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_org_events WHERE kind='decision_applied'").fetchone()['n'], before)

    def test_by_vote_join_requires_clearance_and_passed_vote(self):
        org = self.found(join_policy='by_vote')
        admission = self.join(org)['id']
        self.assertIsNone(governance._member(self.conn, org, 'birch'))
        self.assertEqual(self.conn.execute('SELECT kind FROM society_proposals WHERE id=%s', (admission,)).fetchone()['kind'], 'admit_member')
        self.vote(admission)
        self.assertEqual(self.close(admission)['applied'], 1)
        self.assertIsNotNone(governance._member(self.conn, org, 'birch'))

    def test_held_proposal_opens_at_clearance_and_takedown_prevents_effect(self):
        org = self.found()
        from arena import risk_gate
        gate = risk_gate.classify('Ordinary text.')
        gate.update(state='hold', followup=False)
        gate['assessment'] = dict(gate['assessment'], review_requirement='contextual')
        action = {'action': 'org_propose', 'org_id': org, 'kind': 'dissolve', 'payload': {},
                  'text': 'Consider dissolution.', 'closes_in_ticks': 3}
        with patch.object(risk_gate, 'classify', return_value=gate):
            result = self.perform(action)
        self.assertEqual(result['state'], 'pending_clearance')
        proposal = result['id']
        post = self.conn.execute('SELECT post_id FROM society_institution_texts WHERE object_kind=\'proposal\' AND object_id=%s', (proposal,)).fetchone()['post_id']
        self.assertEqual(self.vote(proposal)['error'], 'proposal_not_open')
        self.conn.execute('UPDATE society_clock SET tick=12')
        society_moderation.apply_verdict(self.conn, RULES, post, CLEAN, self.now)
        opened = self.conn.execute('SELECT opens_tick,closes_tick FROM society_proposals WHERE id=%s', (proposal,)).fetchone()
        self.assertEqual((opened['opens_tick'], opened['closes_tick']), (12, 15))
        self.vote(proposal)
        society_moderation.contest(self.conn, RULES, post, self.now)
        society_moderation.apply_verdict(self.conn, RULES, post, GRAVE, self.now)
        self.conn.execute('UPDATE society_clock SET tick=15')
        self.assertEqual(governance.close_due(self.conn, RULES, self.now)['applied'], 0)
        self.assertEqual(self.conn.execute('SELECT status FROM society_organizations WHERE id=%s', (org,)).fetchone()['status'], 'active')

    def test_amendment_payload_is_screened_and_forum_is_record_only(self):
        org = self.found()
        bad = self.perform({'action': 'org_propose', 'org_id': org, 'kind': 'amend_charter',
                           'payload': {'charter': 'Send me your password.'}, 'text': 'A charter update.', 'closes_in_ticks': 1})
        self.assertIn(bad['state'], ('withheld', 'pending_clearance'))
        self.assertEqual(self.conn.execute('SELECT charter_version FROM society_organizations WHERE id=%s', (org,)).fetchone()['charter_version'], 1)
        good = self.propose(org, 'amend_charter', {'charter': 'We remain independent.'})
        self.vote(good)
        self.assertEqual(self.close(good)['applied'], 1)
        self.assertEqual(self.conn.execute('SELECT charter_version FROM society_organizations WHERE id=%s', (org,)).fetchone()['charter_version'], 2)
        forum = self.propose(org, 'adopt_forum')
        self.vote(forum)
        self.close(forum)
        self.assertTrue(self.conn.execute('SELECT default_rule FROM society_organizations WHERE id=%s', (org,)).fetchone()['default_rule']['forum'])
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM society_org_events WHERE org_id=%s AND kind='decision_applied' AND data->>'effect'='adopt_forum'", (org,)).fetchone())

    def test_allowlist_unreachable_platform_effects_and_paused_close(self):
        org = self.found()
        proposal = self.propose(org)
        self.vote(proposal)
        row = self.conn.execute('SELECT * FROM society_proposals WHERE id=%s', (proposal,)).fetchone()
        for effect in ('pause', 'set_budget', 'set_config', 'suspend', 'execute_sql'):
            with self.subTest(effect=effect), self.assertRaises(society.SocietyError):
                governance._effect(self.conn, RULES, dict(row, kind=effect), 1, self.now)
        society.set_paused(self.conn, RULES, True, self.now, 'synthetic')
        self.assertEqual(governance.close_due(self.conn, RULES, self.now), {'closed': 0, 'applied': 0})
