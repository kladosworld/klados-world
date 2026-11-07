import dataclasses
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_arena_society import RULES, SocietyCase

try:
    from arena import society, society_life, society_moderation, worker
except ImportError:  # psycopg missing: the cases are skipped anyway
    society = society_life = society_moderation = worker = None
from tee_arena import ledger as ledger_module
from tee_arena.gateway import Gateway, GatewayError, ModelPrice, ProviderResponse
from tee_arena.society import decisions, prompts

FOUNDERS = prompts.load_founders()
MODEL = "openrouter:anthropic/claude-haiku-5.5"
OBSERVER = "openrouter:openai/gpt-6-luna"
CLEAN = {"codes": [], "severity": "none", "evidence": "", "by": "model", "charged_microusd": 0}


def decide(**fields):
    return decisions.parse_turn(json.dumps(fields))


class LifeCase(SocietyCase):
    def setUp(self):
        super().setUp()
        self.hours(1)
        self.tick = society.clock(self.conn)["tick"]

    def turn(self, agent_id, **fields):
        self.assertTrue(society.claim_turn(self.conn, agent_id, self.tick, self.now))
        return society_life.act(self.conn, RULES, agent_id, self.tick, decide(**fields), self.now)

    def publish(self, post_id):
        self.assertEqual(society_moderation.apply_verdict(self.conn, RULES, post_id, CLEAN, self.now), "published")
        return post_id

    def next_hour(self):
        self.hours(1)
        self.tick = society.clock(self.conn)["tick"]


class ActTests(LifeCase):
    def test_post_costs_half_an_energy_and_keeps_the_note_private(self):
        before = self.agent("alder")["balance"]
        outcome = self.turn("alder", action="post", text="First light in Klados.", note="Birch talks a lot")
        self.assertEqual(outcome["action"], "post")
        self.assertEqual(self.agent("alder")["balance"], before - 50)
        post = self.conn.execute("SELECT * FROM society_posts").fetchone()
        self.assertEqual((post["author_id"], post["status"]), ("alder", "pending"))
        self.assertEqual(society_life.notes(self.conn, "alder"), ["Birch talks a lot"])
        self.assertEqual(society_life.notes(self.conn, "birch"), [])
        turn = self.conn.execute("SELECT * FROM society_turns WHERE agent_id = 'alder'").fetchone()
        self.assertEqual((turn["status"], turn["action"]), ("done", "post"))

    def test_a_turn_acts_once(self):
        self.turn("alder", action="rest")
        with self.assertRaises(society.SocietyError):
            society_life.act(self.conn, RULES, "alder", self.tick, decide(action="rest"), self.now)
        with self.assertRaises(society.SocietyError):  # never claimed
            society_life.act(self.conn, RULES, "birch", self.tick, decide(action="rest"), self.now)

    def test_impossible_actions_become_free_rests(self):
        before = self.agent("birch")["balance"]
        outcome = self.turn("birch", action="reply", post_id=999, text="Hello?")
        self.assertEqual(outcome, {"action": "rest", "error": "no_such_post", "wanted": "reply"})
        self.assertEqual(self.agent("birch")["balance"], before)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_posts").fetchone()["n"], 0)

    def test_likes_pay_once_per_pair_per_day_and_never_for_own_posts(self):
        post_id = self.publish(self.turn("alder", action="post", text="Who else is here?")["post_id"])
        before = self.agent("alder")["balance"]
        self.assertTrue(self.turn("birch", action="like", post_id=post_id)["paid"])
        self.assertEqual(self.agent("alder")["balance"], before + 100)
        self.next_hour()
        self.assertEqual(self.turn("alder", action="like", post_id=post_id)["error"], "own_post")
        self.assertEqual(self.turn("birch", action="like", post_id=post_id)["error"], "already_liked_today")
        self.assertEqual(self.agent("alder")["balance"], before + 100 - 50)  # one more hour of living cost

    def test_paid_likes_stop_at_the_daily_cap(self):
        rules = dataclasses.replace(RULES, paid_agent_likes_per_agent_per_day=1)
        post_id = self.publish(self.turn("alder", action="post", text="Cap test.")["post_id"])
        for agent_id, expected in (("birch", True), ("cedar", False)):
            society.claim_turn(self.conn, agent_id, self.tick, self.now)
            outcome = society_life.act(self.conn, rules, agent_id, self.tick,
                                       decide(action="like", post_id=post_id), self.now)
            self.assertEqual(outcome["paid"], expected)
        self.assertEqual(society.audit(self.conn), [])

    def test_without_energy_an_action_is_a_free_rest(self):
        society.apply_energy(self.conn, RULES, "hazel", "penalty", -self.agent("hazel")["balance"] + 20,
                             "drain:hazel", self.now)
        outcome = self.turn("hazel", action="post", text="Help")
        self.assertEqual(outcome["error"], "not_enough_energy")
        self.assertEqual(self.agent("hazel")["balance"], 20)


class ContextTests(LifeCase):
    def test_recent_turns_distinguish_chosen_rest_from_failed_calls_and_refused_actions(self):
        self.turn("alder", action="rest")
        self.next_hour()
        self.assertTrue(society.claim_turn(self.conn, "alder", self.tick, self.now))
        society_life.fail_turn(self.conn, "alder", self.tick, "reply_invalid_json")
        self.next_hour()
        self.turn("alder", action="reply", post_id=999, text="Hello?")
        recent = society_life.turn_context(self.conn, RULES, "alder")["your_last_turns"]
        self.assertEqual([(r["action"], r["error"]) for r in recent],
                         [("rest", "no_such_post"), ("rest", "reply_invalid_json"), ("rest", None)])

    def test_a_founder_sees_only_its_own_private_memory(self):
        self.publish(self.turn("alder", action="post", text="Hello.", note="alder secret")["post_id"])
        self.conn.execute("""INSERT INTO society_strategies (agent_id, version, text, tick, created_at)
                             VALUES ('birch', 1, 'birch secret plan', 1, %s)""", (self.now,))
        context = society_life.turn_context(self.conn, RULES, "cedar")
        text = json.dumps(context)
        self.assertNotIn("alder secret", text)
        self.assertNotIn("birch secret plan", text)
        self.assertEqual(context["public_square"][0]["author"], "Alder")
        self.assertEqual(context["you"]["energy"], 299.5)
        self.assertIn("cannot reply", context["rules"]["what_people_can_do"])
        alder = society_life.turn_context(self.conn, RULES, "alder")
        self.assertEqual(alder["your_recent_posts"][0]["likes"], {"from_people": 0, "from_founders": 0})
        own = society_life.turn_context(self.conn, RULES, "birch")
        self.assertEqual(own["you"]["private_strategy"], "birch secret plan")
        self.assertIn(FOUNDERS["birch"]["personality"], json.dumps(own, ensure_ascii=False))


    def test_unscreened_and_withheld_posts_are_invisible_to_other_founders(self):
        pending_id = self.turn("alder", action="post", text="Not screened yet.")["post_id"]
        self.assertEqual(society_life.turn_context(self.conn, RULES, "birch")["public_square"], [])
        own = society_life.turn_context(self.conn, RULES, "alder")["your_recent_posts"][0]
        self.assertEqual(own["moderation"], "waiting for moderation")
        self.assertEqual(self.turn("birch", action="like", post_id=pending_id)["error"], "no_such_post")

    def test_near_duplicate_posts_are_refused_for_free(self):
        self.turn("alder", action="post", text="Reliability beats noise, every single day.")
        self.next_hour()
        before = self.agent("alder")["balance"]
        outcome = self.turn("alder", action="post", text="Reliability beats noise every single day!")
        self.assertEqual(outcome["error"], "duplicate_post")
        self.assertEqual(self.agent("alder")["balance"], before)


class ReflectionTests(LifeCase):
    def test_daily_likes_count_receipt_time_including_older_posts_and_exclude_later_likes(self):
        post = self.publish(self.turn("alder", action="post", text="A thought from yesterday.")["post_id"])
        self.turn("birch", action="like", post_id=post)
        self.hours(24)
        self.tick = society.clock(self.conn)["tick"]
        self.turn("cedar", action="like", post_id=post)
        self.hours(23)
        day0 = society_life.reflection_context(self.conn, RULES, "alder", 0)["the_day"]
        day1 = society_life.reflection_context(self.conn, RULES, "alder", 1)["the_day"]
        self.assertEqual(day0["likes_received"], {"from_people": 0, "from_founders": 1})
        self.assertEqual(day1["likes_received"], {"from_people": 0, "from_founders": 1})
        self.assertEqual(day1["your_posts"], [])

    def test_one_reflection_per_day_with_versioned_strategy(self):
        self.assertEqual(society_life.reflections_due(self.conn), [])
        self.hours(23)
        due = society_life.reflections_due(self.conn)
        self.assertEqual(due, [(a, 0) for a in ("alder", "birch", "cedar", "hazel")])
        context = society_life.reflection_context(self.conn, RULES, "alder", 0)
        self.assertEqual((context["the_day"]["energy_at_start"], context["the_day"]["energy_at_end"]), (300, 288))
        tick = society.clock(self.conn)["tick"]
        change = decisions.parse_reflection('{"keep": false, "strategy": "Speak rarely, reply well.", "reason": "quiet day"}')
        self.assertEqual(society_life.record_reflection(self.conn, "alder", 0, tick, change, self.now), "changed")
        self.assertIsNone(society_life.record_reflection(self.conn, "alder", 0, tick, change, self.now))
        keep = decisions.parse_reflection('{"keep": true}')
        self.assertEqual(society_life.record_reflection(self.conn, "birch", 0, tick, keep, self.now), "kept")
        self.assertEqual(society_life.strategy(self.conn, "alder")["version"], 1)
        self.assertIsNone(society_life.strategy(self.conn, "birch"))
        self.assertEqual([a for a, _ in society_life.reflections_due(self.conn)], ["cedar", "hazel"])


class ScriptedProvider:
    """Replies by agent name from a script; a callable entry raises or returns text."""

    def __init__(self, script):
        self.script, self.requests = script, []

    def complete(self, request):
        self.requests.append(request)
        state = request.messages[0]["content"]
        if "<post>" in state:  # the observer
            reply = self.script.get("observer", '{"codes": [], "evidence": ""}')
            text = reply(request) if callable(reply) else reply
            return ProviderResponse(text=text, model=request.model, stop="end", input_tokens=600, output_tokens=40)
        name = json.loads(state.split("<klados_state>\n", 1)[1].rsplit("\n</klados_state>", 1)[0])["you"]["name"]
        reply = self.script[name]
        text = reply(request) if callable(reply) else reply
        return ProviderResponse(text=text, model=request.model, stop="end", input_tokens=1500, output_tokens=80)


class WorkerCase(LifeCase):
    def make(self, script, cap=5_000_000):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        book = ledger_module.Ledger.create(Path(directory.name) / "ledger.json", global_cap=cap)
        book.open_account(worker.ACCOUNT, cap)
        book.open_account(worker.OBSERVER_ACCOUNT, cap)
        self.provider = ScriptedProvider(script)
        models = {MODEL: ModelPrice("0.10", "0.50"), OBSERVER: ModelPrice("0.10", "0.50")}
        gateway = Gateway(ledger=book, models=models, providers={"openrouter": self.provider}, stop_file=None)
        return worker.Worker(self.conn, RULES, gateway, observer_gateway=gateway, sleep=lambda s: None,
                             now=lambda: self.now.replace(minute=59))



class WorkerTests(WorkerCase):
    def test_reflection_retry_cap_refusal_pauses_immediately(self):
        run = self.make({"Alder": '{"action": "rest"}'})
        self.hours(23)
        with patch.object(run, "call", side_effect=[SimpleNamespace(text="thinking", charged_microusd=7),
                                                    GatewayError("cap_exceeded")]):
            self.assertEqual(run.reflect("alder", 0, 24), "stop")
        self.assertTrue(society.clock(self.conn)["paused"])
        row = self.conn.execute("SELECT * FROM society_reflections WHERE agent_id='alder'").fetchone()
        self.assertEqual(row["charged_microusd"], 7)
        self.assertEqual(row["private"]["error"], "call_cap_exceeded")

    def test_reflection_retry_provider_failure_counts_charge_and_failure_threshold(self):
        run = self.make({"Alder": '{"action": "rest"}'})
        run.max_failures = 1
        self.hours(23)
        error = GatewayError("provider_failure", sent=True, charged_microusd=11)
        with patch.object(run, "call", side_effect=[SimpleNamespace(text="thinking", charged_microusd=7), error]):
            self.assertEqual(run.reflect("alder", 0, 24), "stop")
        self.assertTrue(society.clock(self.conn)["paused"])
        row = self.conn.execute("SELECT * FROM society_reflections WHERE agent_id='alder'").fetchone()
        self.assertEqual(row["charged_microusd"], 18)
        self.assertEqual(row["private"]["error"], "call_provider_failure")

    def test_founders_decide_and_unusable_replies_are_rests(self):
        script = {"Alder": '{"action": "post", "text": "I will speak rarely.", "reason": "save energy"}',
                  "Birch": "Let me think about it...",
                  "Cedar": '{"action": "rest", "note": "watch first"}',
                  "Hazel": '{"action": "like", "post_id": 1}'}
        run = self.make(script)
        self.assertEqual(run.run(), {"turns": 3, "failed": 1, "reflections": 0, "stopped": False})
        rows = {r["agent_id"]: r for r in self.conn.execute("SELECT * FROM society_turns")}
        self.assertEqual(rows["birch"]["private"], {"error": "reply_invalid_json"})
        self.assertGreater(rows["alder"]["charged_microusd"], 0)
        self.assertEqual(rows["alder"]["private"]["decision"]["reason"], "save energy")
        self.assertTrue(rows["hazel"]["private"]["outcome"]["paid"])  # Alder posted first in the hour
        self.assertEqual(run.run()["turns"], 0)  # the hour's turns are spent
        self.assertEqual(self.provider.requests[0].model, "anthropic/claude-haiku-5.5")

    def test_repeated_failures_pause_the_society(self):
        def broken(request):
            raise RuntimeError("upstream down")
        run = self.make({name: broken for name in ("Alder", "Birch", "Cedar", "Hazel")})
        self.assertTrue(run.run()["stopped"])
        self.assertTrue(society.clock(self.conn)["paused"])
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_turns").fetchone()["n"], 3)
        self.assertEqual(self.events("paused")[-1]["data"]["reason"], "worker: provider_failure")

    def test_an_unusable_reflection_is_tried_once_more(self):
        replies = iter(["thinking...", '{"keep": true}'])
        script = {name: (lambda request: next(replies) if "day_of_life_ended" in request.messages[0]["content"]
                         else '{"action": "rest"}') for name in ("Alder",)}
        script.update({name: '{"action": "rest"}' for name in ("Birch", "Cedar", "Hazel")})
        run = self.make(script)
        run.run()
        self.hours(23)
        for name in ("Birch", "Cedar", "Hazel"):
            script[name] = lambda request: ('{"keep": true}' if "day_of_life_ended" in request.messages[0]["content"]
                                            else '{"action": "rest"}')
        self.assertEqual(run.run()["reflections"], 4)
        row = self.conn.execute("SELECT * FROM society_reflections WHERE agent_id = 'alder'").fetchone()
        self.assertEqual(row["outcome"], "kept")

    def test_cut_off_replies_are_rests_not_outages(self):
        def cut(request):
            return "{"
        provider_script = {name: '{"action": "rest"}' for name in ("Alder", "Birch", "Cedar", "Hazel")}
        run = self.make(provider_script)
        original = self.provider.complete

        def incomplete(request):
            response = original(request)
            return ProviderResponse(text=response.text, model=response.model, stop="max_tokens",
                                    input_tokens=1500, output_tokens=1200)
        self.provider.complete = incomplete
        summary = run.run()
        self.assertEqual((summary["failed"], summary["stopped"]), (4, False))
        self.assertFalse(society.clock(self.conn)["paused"])
        self.assertEqual({r["private"]["error"] for r in self.conn.execute("SELECT private FROM society_turns")},
                         {"reply_response_incomplete"})

    def test_a_spent_cap_pauses_at_once(self):
        run = self.make({name: '{"action": "rest"}' for name in ("Alder", "Birch", "Cedar", "Hazel")}, cap=10)
        self.assertTrue(run.run()["stopped"])
        self.assertEqual(self.events("paused")[-1]["data"]["reason"], "worker: cap_exceeded")

    def test_daily_reflection_changes_strategy_and_announces_only_the_fact(self):
        script = {name: '{"action": "rest"}' for name in ("Alder", "Birch", "Cedar", "Hazel")}
        run = self.make(script)
        run.run()
        self.hours(23)
        reflection = '{"keep": false, "strategy": "Reply to whoever is ignored.", "reason": "nobody talked"}'
        for name in script:
            script[name] = lambda request, r=reflection: (r if "day_of_life_ended" in request.messages[0]["content"]
                                                          else '{"action": "rest"}')
        summary = run.run()
        self.assertEqual(summary["reflections"], 4)
        self.assertEqual(society_life.strategy(self.conn, "cedar")["text"], "Reply to whoever is ignored.")
        changed = self.events("strategy_changed")
        self.assertEqual(len(changed), 4)
        self.assertEqual(changed[0]["data"], {})
