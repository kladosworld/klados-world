"""Current turn facts do not rewrite persistent identity or historical memory."""

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from psycopg import sql

from arena import society_life
from tee_arena.society import constitution, prompts, rules, shared_purpose
from tests.test_arena_external_agents import InviteCase


def payload(messages):
    return json.loads(messages[0]["content"].split("<klados_state>\n", 1)[1].split("\n</klados_state>", 1)[0])


class OperationalFactCompositionTests(unittest.TestCase):
    def test_additive_facts_preserve_context_and_shared_purpose(self):
        context = {"state_version": 2, "you": {"personality": "CUSTOM_IDENTITY", "private_notes": ["Historical energy memory"]}}
        original = deepcopy(context)
        system, messages = prompts.turn(context)
        composed = payload(messages)
        facts = composed.pop("operational_facts")
        self.assertEqual(composed, original)
        self.assertEqual(context, original)
        self.assertIn("cannot currently like posts or submit replies", facts["human_visitors"])
        self.assertIn("memories of earlier rules, not current operational rules", facts["lifecycle"])
        self.assertIn("disagree and revise their beliefs naturally", facts["autonomy"])
        purpose = shared_purpose.Purpose("synthetic-test-v1", "Synthetic purpose")
        enabled, enabled_messages = prompts.turn(context, purpose=purpose)
        self.assertEqual(enabled_messages, messages)
        self.assertEqual(enabled, purpose.prefix() + system)
        self.assertTrue(system.startswith(constitution.TEXT))
        reflection, reflection_messages = prompts.reflection(context)
        self.assertEqual(payload(reflection_messages)["operational_facts"], facts)
        self.assertEqual(payload(reflection_messages)["you"], original["you"])
        selected_reflection, selected_messages = prompts.reflection(context, purpose=purpose)
        self.assertEqual(selected_messages, reflection_messages)
        self.assertEqual(selected_reflection, purpose.prefix() + reflection)
        self.assertEqual(context, original)

    def test_legacy_turn_context_remains_unchanged(self):
        context = {"you": {"energy": 250, "private_notes": ["Historical rules-v1 note"]}}
        self.assertEqual(payload(prompts.turn(context)[1]), context)
        self.assertEqual(payload(prompts.reflection(context)[1]), context)


class FounderOperationalContextTests(InviteCase):
    def snapshot(self):
        tables = self.conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename").fetchall()
        return {row["tablename"]: list(self.conn.execute(sql.SQL(
            "SELECT to_jsonb(t) AS row FROM {} t ORDER BY to_jsonb(t)::text"
        ).format(sql.Identifier(row["tablename"])))) for row in tables}

    def test_each_founder_receives_current_facts_without_rewriting_any_row(self):
        founders = ("alder", "birch", "cedar", "hazel")
        for identifier in founders:
            society_life.record_reflection(self.conn, identifier, 0, 0, {
                "keep": False, "strategy": f"PRIVATE_{identifier}_STRATEGY: remember survival through energy",
                "note": f"PRIVATE_{identifier}_NOTE: earlier energy rules",
            }, self.now)
        self.conn.commit()
        before = self.snapshot()
        for identifier in founders:
            contexts = [society_life.turn_context(self.conn, rules.load(), identifier),
                        society_life.reflection_context(self.conn, rules.load(), identifier, 0)]
            for compose, context in zip((prompts.turn, prompts.reflection), contexts, strict=True):
                original = deepcopy(context)
                _, messages = compose(context)
                received = payload(messages)
                facts = received.pop("operational_facts")
                self.assertEqual(received, original)
                self.assertEqual(context, original)
                self.assertIn("no energy-based survival or death mechanic", facts["lifecycle"])
                self.assertIn(f"PRIVATE_{identifier}_STRATEGY", messages[0]["content"])
                self.assertIn(f"PRIVATE_{identifier}_NOTE", messages[0]["content"])
                for other in set(founders) - {identifier}:
                    self.assertNotIn(f"PRIVATE_{other}_", messages[0]["content"])
        self.assertEqual(self.snapshot(), before)

    def test_worker_sends_facts_once_per_founder_without_refreshing_identity(self):
        from arena import society, worker

        self.tick()
        tick = society.clock(self.conn)["tick"]
        tables = {"society_agents", "society_notes", "society_strategies", "society_reflections", "energy_entries"}
        before = {name: rows for name, rows in self.snapshot().items() if name in tables}
        run = worker.Worker(self.conn, rules.load(), SimpleNamespace(providers={}), now=lambda: self.now)
        with patch.object(run, "call", return_value=SimpleNamespace(text='{"action":"rest"}', charged_microusd=0)) as call:
            for identifier in ("alder", "birch", "cedar", "hazel"):
                self.assertEqual(run.turn(identifier, tick), "done")
                received = payload(call.call_args.args[1])
                self.assertIn("cannot currently like posts or submit replies", received["operational_facts"]["human_visitors"])
                self.assertIsNone(run.turn(identifier, tick))
            self.assertEqual(call.call_count, 4)
        self.assertEqual({name: rows for name, rows in self.snapshot().items() if name in tables}, before)

    def test_worker_reflections_receive_facts_without_extra_calls_or_strategy_rewrites(self):
        from arena import society, worker

        society.set_paused(self.conn, rules.load(), False, self.now, 'synthetic')
        self.conn.commit()
        tables = {"society_agents", "society_notes", "society_strategies", "energy_entries"}
        before = {name: rows for name, rows in self.snapshot().items() if name in tables}
        run = worker.Worker(self.conn, rules.load(), SimpleNamespace(providers={}), now=lambda: self.now)
        with patch.object(run, "call", return_value=SimpleNamespace(text='{"keep":true}', charged_microusd=0)) as call:
            for identifier in ("alder", "birch", "cedar", "hazel"):
                self.assertEqual(run.reflect(identifier, 0, 24), "kept")
                received = payload(call.call_args.args[1])
                self.assertIn("cannot currently like posts or submit replies", received["operational_facts"]["human_visitors"])
                self.assertIn("memories of earlier rules", received["operational_facts"]["lifecycle"])
            self.assertEqual(call.call_count, 4)
        self.assertEqual({name: rows for name, rows in self.snapshot().items() if name in tables}, before)
