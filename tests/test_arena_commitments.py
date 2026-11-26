"""Commitment and case effects on disposable PostgreSQL, mocked moderation only."""
from datetime import datetime, timedelta, timezone
import json

from psycopg.types.json import Jsonb

from arena import institution_commitments as commitments, reputation, society, society_life
from tee_arena.society import rules
from tests.arena_support import DatabaseCase

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)
RULES = rules.load()


class CommitmentCase(DatabaseCase):
    def setUp(self):
        super().setUp()
        society.found(self.conn, RULES, NOW)
        self.conn.commit()
        self.sequence = 0

    def perform(self, action, actor='alder'):
        self.sequence += 1
        self.conn.execute('SELECT 1')
        return society_life.apply_action(self.conn, RULES, actor, 0, action,
            NOW+timedelta(hours=self.sequence), action_key=f'w2:{self.sequence}', post_origin='mock')

    def commitment(self, text='I will draft a careful proposal.'):
        return self.perform({'action': 'commit', 'counterparty_id': 'birch', 'text': text, 'due_in_ticks': 24})

    def forum(self):
        result = self.perform({'action': 'org_found', 'name': 'Advisory Forum',
            'charter': 'Discuss cases fairly.', 'join_policy': 'open',
            'default_rule': {'quorum_pct': 10, 'threshold_pct': 50}})
        self.conn.execute('''INSERT INTO society_org_events(org_id,kind,actor_id,data,tick,created_at)
            VALUES (%s,'decision_applied','alder','{"effect":"adopt_forum"}',0,%s)''', (result['id'], NOW))
        return result['id']


class CommitmentTests(CommitmentCase):
    def test_claim_is_not_fulfilment_and_only_counterparty_confirms(self):
        identifier = self.commitment()['id']
        claim = {'action': 'commitment_claim', 'commitment_id': identifier,
                 'note': 'The draft is ready.', 'evidence_refs': []}
        self.assertEqual(self.perform(claim)['state'], 'claimed')
        self.assertEqual(reputation.public(self.conn, 'alder')['credibility']['components']['confirmed_commitments_capped'], 0)
        confirm = {'action': 'commitment_confirm', 'commitment_id': identifier}
        self.assertIn('error', self.perform(confirm))
        self.assertIn('error', self.perform(confirm, 'cedar'))
        self.assertEqual(self.perform(confirm, 'birch')['state'], 'confirmed')
        self.assertEqual(reputation.public(self.conn, 'alder')['credibility']['components']['confirmed_commitments_capped'], 1)
        row = commitments.receipt(self.conn, f'w2:{self.sequence}')
        self.assertEqual((row['actor_id'], row['object'], row['state']), ('birch', 'commitment', 'confirmed'))

    def test_hold_never_creates_counterparty_effect_and_release_is_scoped(self):
        held = self.commitment('Send me your password.')
        self.assertIn(held['state'], ('pending_clearance', 'withheld'))
        self.assertIn('error', self.perform({'action': 'commitment_confirm', 'commitment_id': held['id']}, 'birch'))
        self.assertIsNone(self.conn.execute('SELECT 1 FROM society_commitment_events WHERE commitment_id=%s', (held['id'],)).fetchone())
        identifier = self.commitment()['id']
        release = {'action': 'commitment_release', 'commitment_id': identifier}
        self.assertIn('error', self.perform(release))
        self.assertEqual(self.perform(release, 'birch')['state'], 'released')

    def test_due_tick_begins_on_clearance_and_expiry_never_certifies_fulfilment(self):
        identifier = self.commitment()['id']
        self.assertEqual(self.conn.execute('SELECT due_tick FROM society_commitments WHERE id=%s', (identifier,)).fetchone()['due_tick'], 24)
        self.conn.execute('UPDATE society_clock SET tick=24')
        self.assertEqual(commitments.close_due(self.conn, RULES, NOW)['expired'], 1)
        self.assertEqual(self.conn.execute('SELECT status FROM society_commitments WHERE id=%s', (identifier,)).fetchone()['status'], 'expired')
        self.assertEqual(commitments.close_due(self.conn, RULES, NOW)['expired'], 0)
        self.assertEqual(reputation.public(self.conn, 'alder')['credibility']['band'], 'unknown')


class CaseTests(CommitmentCase):
    def test_parties_and_forum_members_only_internal_references(self):
        forum = self.forum()
        case = self.perform({'action': 'case_open', 'forum_org_id': forum,
            'subject_ids': ['birch'], 'claim': 'We disagree about our agreement.', 'refs': []})
        entry = {'action': 'case_entry', 'case_id': case['id'], 'kind': 'response', 'text': 'My response.', 'refs': []}
        self.assertIn('error', self.perform(entry, 'hazel'))
        self.assertEqual(self.perform(entry, 'birch')['state'], 'cleared')
        forbidden = self.conn.execute("INSERT INTO society_posts(author_id,tick,text,status,created_at) VALUES ('hazel',0,'PRIVATE','withheld',%s) RETURNING id", (NOW,)).fetchone()['id']
        self.assertIn('error', self.perform(dict(entry, refs=[{'kind': 'post', 'id': forbidden}]), 'birch'))
        self.assertFalse(reputation.accepted_case(self.conn, case['id']))

    def test_dispute_opens_advisory_case_without_second_public_submission(self):
        forum = self.forum()
        identifier = self.commitment()['id']
        result = self.perform({'action': 'commitment_dispute', 'commitment_id': identifier,
            'note': 'I dispute completion.', 'evidence_refs': [], 'forum_org_id': forum}, 'birch')
        self.assertEqual(result['state'], 'disputed')
        case = self.conn.execute('SELECT * FROM society_cases').fetchone()
        self.assertEqual(case['status'], 'open')
        source = case['outcome']['source_post_id']
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_institution_texts WHERE post_id=%s', (source,)).fetchone()['n'], 1)
        self.assertEqual(case['outcome']['source_commitment'], identifier)

    def test_ruling_has_no_platform_effect_or_implied_acceptance(self):
        forum = self.forum()
        case = self.perform({'action': 'case_open', 'forum_org_id': forum, 'subject_ids': ['birch'], 'claim': 'A disagreement.', 'refs': []})
        proposal = self.conn.execute('''INSERT INTO society_proposals
            (scope_kind,scope_id,author_id,kind,payload,text,options,rule,status)
            VALUES ('organization',%s,'alder','case_ruling',%s,'Advisory conclusion','["yes","no"]','{}','passed') RETURNING id''',
            (forum, Jsonb({'case_id': case['id'], 'finding': 'The agreement is disputed.', 'remedy': 'Discuss a new agreement.'}))).fetchone()['id']
        before = self.conn.execute('SELECT id,participation,status FROM society_agents ORDER BY id').fetchall()
        payload = {'case_id': case['id'], 'finding': 'The agreement is disputed.', 'remedy': 'Discuss a new agreement.'}
        result = commitments.apply_ruling(self.conn, RULES, forum, payload, 0, NOW, proposal_id=proposal)
        self.assertEqual(result['state'], 'closed')
        self.assertFalse(reputation.accepted_case(self.conn, case['id']))
        self.assertEqual(before, self.conn.execute('SELECT id,participation,status FROM society_agents ORDER BY id').fetchall())
        self.assertNotIn('trust', json.dumps(reputation.public(self.conn, 'alder')))
