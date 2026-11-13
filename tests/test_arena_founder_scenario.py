"""Synthetic engine-level 72-tick scenario; no provider or money-ledger calls."""
import json
from collections import Counter
from unittest.mock import patch

from arena import external_agents, society, society_life, society_moderation, society_public
from tee_arena.society import decisions
from tests.founder_scenario import scenario_turn
from tests.test_arena_society import RULES, SocietyCase


class FounderScenarioTests(SocietyCase):
    def test_seventy_two_ticks_cover_all_actions_and_preserve_the_energy_economy(self):
        identities = list(self.conn.execute('SELECT id,name,public_description FROM society_agents ORDER BY id'))
        activity = {identifier: Counter() for identifier, _ in society.FOUNDERS}
        for tick in range(1, 73):
            self.assertTrue(self.hours(1)[0]['ticked'])
            for identifier, _ in society.FOUNDERS:
                context = society_life.turn_context(self.conn, RULES, identifier)
                decision = decisions.parse_turn(json.dumps(scenario_turn(context)))
                self.assertTrue(society.claim_turn(self.conn, identifier, tick, self.now))
                outcome = society_life.act(self.conn, RULES, identifier, tick, decision, self.now, post_origin='mock')
                self.assertNotIn('error', outcome)
                self.assertEqual(outcome['action'], decision['action'])
                activity[identifier][outcome['action']] += 1
                if outcome['action'] == 'like':
                    self.assertTrue(outcome['paid'])
                if 'post_id' in outcome:
                    verdict = dict(codes=[], severity='none', evidence='', by='model', charged_microusd=0)
                    self.assertEqual(society_moderation.apply_verdict(self.conn, RULES, outcome['post_id'], verdict, self.now), 'published')
                self.assertFalse(society.claim_turn(self.conn, identifier, tick, self.now))
            for identifier, day in society_life.reflections_due(self.conn):
                self.assertEqual(society_life.record_reflection(self.conn, identifier, day, tick, {'keep': True}, self.now), 'kept')
            self.assertEqual(society.audit(self.conn), [])
            self.assertTrue(all(0 <= row['balance'] <= RULES.cap for row in self.conn.execute('SELECT balance FROM society_agents')))
        self.assertEqual(identities, list(self.conn.execute('SELECT id,name,public_description FROM society_agents ORDER BY id')))
        expected = Counter(post=18, reply=18, like=9, rest=27)
        for identifier, _ in society.FOUNDERS:
            self.assertEqual(activity[identifier], expected)
            self.assertEqual(self.agent(identifier)['balance'], 25500)
            self.assertEqual(self.agent(identifier)['status'], 'alive')
            for relation, count in (('replied_to', 6), ('replies_from', 6), ('liked', 3), ('liked_by', 3)):
                self.assertEqual(society.relations(self.conn, identifier)[relation], {other: count for other, _ in society.FOUNDERS if other != identifier})
            self.assertEqual(society_public.public_view(self.conn, 'agent', identifier)['posts'], [])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_turns').fetchone()['n'], 288)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_turns WHERE status<>'done'").fetchone()['n'], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM energy_entries').fetchone()['n'], 472)
        self.assertEqual(list(self.conn.execute('SELECT key FROM energy_entries GROUP BY key HAVING count(*)>1')), [])
        self.assertEqual(list(self.conn.execute("SELECT from_id,target_id,day FROM society_likes WHERE from_kind='agent' GROUP BY from_id,target_id,day HAVING count(*)>1")), [])
        self.assertEqual(list(self.conn.execute('SELECT target_id,day FROM society_likes WHERE paid GROUP BY target_id,day HAVING count(*)>3')), [])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_reflections').fetchone()['n'], 12)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_agents WHERE kind='external'").fetchone()['n'], 0)
        self.assertEqual(self.conn.execute('SELECT sum(charged_microusd) AS n FROM society_turns').fetchone()['n'], 0)
        self.assertEqual(society_public.public_view(self.conn, 'square')['posts'], [])
        with patch.dict('os.environ', KLADOS_EXTERNAL_AGENTS='off'):
            self.assertFalse(external_agents.enabled())

    def test_scenario_rests_when_context_has_no_root_or_the_target_was_liked_today(self):
        self.hours(4)
        context = society_life.turn_context(self.conn, RULES, 'alder')
        self.assertEqual(scenario_turn(context), {'action': 'rest'})
        context['public_square'] = [dict(author='Birch', post_id=12, reply_to=None)]
        context['today']['founders_you_already_liked'] = ['Birch']
        self.assertEqual(scenario_turn(context), {'action': 'rest'})
        context['today']['founders_you_already_liked'] = []
        self.assertEqual(scenario_turn(context), {'action': 'like', 'post_id': 12})
