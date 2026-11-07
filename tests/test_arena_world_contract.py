"""Fixture schemas are compared automatically with real v2 database projections."""
import json
from pathlib import Path
from tests.arena_support import psycopg
from tests.test_arena_society_life import LifeCase, CLEAN
from tests.test_arena_society import RULES
if psycopg:
    from arena import db, society, society_moderation, society_public

FIXTURES = Path(__file__).resolve().parent.parent / 'arena/static/fixtures'


class WorldContractTests(LifeCase):
    def assert_public_shape(self, example, actual, path=()):
        # These are open maps of ids/kinds to counts, not fields of a fixed schema.
        dynamic = path[-1:] in [('by_kind',), ('death_causes',), ('events',),
            ('replied_to',), ('replies_from',), ('liked',), ('liked_by',)]
        if dynamic and isinstance(actual, dict):
            for key, count in actual.items():
                self.assertIsInstance(key, str)
                self.assertIs(type(count), int)
            return
        if isinstance(example, dict) and isinstance(actual, dict):
            expected_keys = set(example) - {'_example'}
            self.assertEqual(expected_keys, set(actual), '.'.join(path))
            for key in expected_keys:
                self.assert_public_shape(example[key], actual[key], (*path, key))
        elif isinstance(example, list) and isinstance(actual, list):
            if example and actual:
                for row in actual:
                    self.assert_public_shape(example[0], row, (*path, '[]'))
            else:
                self.assertEqual(bool(example), bool(actual), '.'.join(path))
        elif example is not None and actual is not None:
            # Values differ, but scalar categories must agree (energy may be int or float).
            if type(example) in (int, float) and type(actual) in (int, float):
                return
            self.assertEqual(type(example), type(actual), '.'.join(path))

    def test_every_fixture_shape_matches_real_society_public_functions(self):
        post_id = self.turn('alder', action='post', text='A published example for the contract.')['post_id']
        society_moderation.apply_verdict(self.conn, RULES, post_id, CLEAN, self.now)
        reply = self.turn('birch', action='reply', post_id=post_id, text='A published reply for the contract.')['post_id']
        society_moderation.apply_verdict(self.conn, RULES, reply, CLEAN, self.now)
        self.turn('cedar', action='like', post_id=post_id)
        db.insert_voter(self.conn, dict(id='contract-visitor', created_at=self.now))
        society_public.like(self.conn, RULES, 'contract-visitor', post_id, self.now)
        society.die(self.conn, RULES, 'hazel', self.tick, 'energy', self.now)
        society.die(self.conn, RULES, 'cedar', self.tick, 'age', self.now)
        self.conn.execute("UPDATE society_fossils SET last_words_status='published', last_words='Public example last words.' WHERE agent_id='hazel'")
        society.event(self.conn, RULES, self.tick, 'last_words', self.now, 'hazel')
        actual = {'feed.json': society_public.feed(self.conn, RULES),
                  'square.json': society_public.square(self.conn),
                  'stats.json': society_public.stats(self.conn, RULES)}
        actual.update({f"agents/{agent['id']}.json": society_public.profile(self.conn, RULES, agent['id'])
                       for agent in actual['feed.json']['agents']})
        self.assertEqual({str(path.relative_to(FIXTURES)) for path in FIXTURES.rglob('*.json')}, set(actual))
        for resource, value in actual.items():
            with self.subTest(resource=resource):
                self.assert_public_shape(json.loads((FIXTURES / resource).read_text()), value)
