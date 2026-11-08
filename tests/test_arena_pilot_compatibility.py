"""Matched disposable worlds; fake providers only, never the staging society."""
import unittest

from tests.arena_support import DSN, psycopg
from tests.test_arena_society_life import WorkerCase
from tee_arena.society import shared_purpose

if psycopg:
    from arena import society
    from tee_arena.gateway import input_token_allowance, cost_microusd, ModelPrice


@unittest.skipUnless(DSN and psycopg, "requires disposable PostgreSQL")
class PilotCompatibilityTests(unittest.TestCase):
    def world(self, purpose):
        case = WorkerCase(methodName="runTest")
        try:
            case.setUp()
            run = case.make({"Alder": '{"action":"post","text":"A testable idea."}',
                             "Birch": '{"action":"reply","post_id":1,"text":"A different view."}',
                             "Cedar": '{"action":"like","post_id":1}',
                             "Hazel": '{"action":"rest"}'}, purpose=purpose)
            before = [(r["id"], r["name"], r["public_description"]) for r in case.conn.execute(
                "SELECT id,name,public_description FROM society_agents ORDER BY id")]
            result = run.run()
            calls = list(case.provider.requests)
            after = [(r["id"], r["name"], r["public_description"]) for r in case.conn.execute(
                "SELECT id,name,public_description FROM society_agents ORDER BY id")]
            actions = [r["action"] for r in case.conn.execute("SELECT action FROM society_turns ORDER BY agent_id")]
            self.assertEqual(before, after)
            self.assertEqual(society.audit(case.conn), [])
            return result, calls, actions
        finally:
            case.doCleanups()

    def test_purpose_has_no_extra_calls_and_preserves_all_four_actions_and_output_caps(self):
        selected = shared_purpose.load_registry()["building-a-society-v1"]
        control, control_calls, control_actions = self.world(None)
        treatment, treatment_calls, treatment_actions = self.world(selected)
        self.assertEqual(control, treatment)
        self.assertEqual(control_actions, treatment_actions)
        self.assertEqual(set(control_actions), {"post", "reply", "like", "rest"})
        self.assertEqual(len(control_calls), 6)  # Four turns; two publication screens.
        self.assertEqual(len(control_calls), len(treatment_calls))
        for before, after in zip(control_calls, treatment_calls, strict=True):
            self.assertEqual(before.model, after.model)
            self.assertEqual(before.max_output_tokens, after.max_output_tokens)
            self.assertEqual(before.timeout_seconds, after.timeout_seconds)
            if "<klados_state>" in before.messages[0]["content"]:
                self.assertEqual(after.system, selected.prefix() + before.system)
                delta = input_token_allowance(after.system, after.messages) - input_token_allowance(before.system, before.messages)
                self.assertEqual(delta, 987)
                self.assertEqual(cost_microusd(ModelPrice("0.10", "0.50"), delta, 0), 99)
            else:
                self.assertEqual(before, after)  # Observer is not given the purpose.
