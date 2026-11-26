"""Read-only bounded context, privacy and pending Constitution action format gate."""
import copy
import json
import unittest
from unittest.mock import patch
from arena import institution_context
from tee_arena.society import prompts
from tee_arena.gateway import ModelPrice, cost_microusd, input_token_allowance


class Rows:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0]


class SyntheticContext:
    """Tests query bounds and ownership rather than inventing an inference result."""
    def __init__(self): self.queries = []
    def execute(self, query, params=()):
        self.queries.append((query, params))
        if 'society_clock' in query: return Rows([{'tick': 20}])
        assert query.lstrip().startswith('SELECT')
        if 'FROM society_organizations o' in query:
            return Rows([dict(id=n, name=f'Organization {n}', charter='A voluntary association.', join_policy='open', your_role='member') for n in range(3)])
        if 'FROM society_proposals p' in query:
            return Rows([dict(id=n, org_id=1, kind='resolution', text='Consider a voluntary practice.', options=['yes', 'no'], closes_tick=21, already_voted=False) for n in range(5)])
        if 'FROM society_commitments c' in query:
            return Rows([dict(id=n, author_id=params[0], counterparty_id='birch', status='open', due_tick=22, text='An optional commitment.') for n in range(5)])
        if 'FROM society_cases c' in query:
            return Rows([dict(id=n, forum_org_id=1, filed_by='alder', subject_ids=['birch'], status='open', claim='A disputed understanding.') for n in range(3)])
        if 'FROM society_trust WHERE from_id' in query:
            assert params == ('alder',)
            return Rows([dict(agent_id='birch', stance='trust', reason='My private reasoning.', tick=19)])
        if 'FROM society_trust WHERE to_id' in query:
            assert params == ('alder',)
            assert 'reason' not in query
            return Rows([dict(agent_id='cedar', stance='uncertain', tick=19)])
        raise AssertionError(query)


class ContextTests(unittest.TestCase):
    def test_bounds_ownership_and_private_reason_direction(self):
        conn = SyntheticContext()
        context = institution_context.build(conn, 'alder')
        self.assertEqual([len(context[k]) for k in ('organizations','proposals','commitments','cases')], [3,5,5,3])
        self.assertIn('reason', context['personal_trust']['outgoing'][0])
        self.assertNotIn('reason', context['personal_trust']['incoming'][0])
        self.assertTrue(all('LIMIT' in q for q, _ in conn.queries if 'society_clock' not in q))
        self.assertTrue(all(q.lstrip().startswith('SELECT') for q, _ in conn.queries))

    def test_prompt_is_additive_v6_format_stays_pending(self):
        context = {'state_version': 2, 'you': {'name':'Alder','strategy':'Unchanged'},
                   'institutions': institution_context.build(SyntheticContext(), 'alder',20)}
        before = copy.deepcopy(context)
        system, messages = prompts.turn(context)
        self.assertIn(prompts.INSTITUTION_GUIDANCE, system)
        self.assertNotIn(prompts.INSTITUTION_FORMAT, system)
        self.assertIn('My private reasoning.', messages[0]['content'])
        self.assertEqual(context,before)
        with patch.object(prompts.constitution,'VERSION',7):
            self.assertIn(prompts.INSTITUTION_FORMAT,prompts.turn(context)[0])
        self.assertIn(prompts.INSTITUTION_GUIDANCE,prompts.reflection(context)[0])

    def test_synthetic_cost_report_is_not_a_600_token_activation_gate(self):
        baseline = {'state_version':2,'you':{'name':'Alder'},'activity_settings':{}}
        enriched = dict(baseline,institutions=institution_context.build(SyntheticContext(),'alder',20))
        old = input_token_allowance(*prompts.turn(baseline))
        new = input_token_allowance(*prompts.turn(enriched))
        delta = new-old
        prices = json.loads(__import__('pathlib').Path('config/society-models.json').read_text())
        price = ModelPrice(**prices['openrouter:openai/gpt-6-luna'])
        daily = cost_microusd(price, delta*4*24,0)
        print(f'Institution context synthetic delta={delta} input allowance tokens; four founders/day=${daily/1_000_000:.6f}; 600 target is informational')
        self.assertGreater(delta,0)
        self.assertLess(delta,15000)  # deterministic bounded envelope, not a product quota


class MockInstitutionTests(unittest.TestCase):
    def test_explicit_fixture_uses_actual_adapter_without_changing_default(self):
        from types import SimpleNamespace
        from arena.mock_provider import MockProvider
        from tee_arena.society import decisions
        state = {'you': {'name': 'Alder', 'age': {'days': 0, 'hours': 1}},
                 'your_recent_posts': [], 'public_square': [], 'today': {}, 'activity_settings': {}}
        _, messages = prompts.turn(state)
        request = SimpleNamespace(messages=messages, model='synthetic')
        fixtures = [
            {'action': 'org_join', 'org_id': 1},
            {'action': 'vote', 'proposal_id': 1, 'choice': 'yes'},
            {'action': 'trust', 'agent_id': 'birch', 'stance': 'trust', 'reason': 'My private assessment.'},
        ]
        provider = MockProvider(institution_actions=fixtures)
        for fixture in fixtures:
            result = decisions.parse_batch(provider.complete(request).text)
            self.assertEqual(result['actions'][0], fixture)
        baseline = decisions.parse_batch(MockProvider().complete(request).text)
        self.assertEqual(baseline['actions'][0]['action'], 'post')
        self.assertNotIn('institution', json.dumps(baseline))
