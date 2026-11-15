"""Moderation failures stay with the post; founder, budget and infrastructure failures still pause."""
from pathlib import Path
import tempfile
from unittest.mock import patch

from tests.test_arena_society import RULES
from tests.test_arena_society_fossils import FossilTests
from tests.test_arena_society_life import CLEAN, WorkerCase
from tests.arena_support import psycopg

if psycopg:
    from arena import society, society_moderation
from tee_arena.gateway import GatewayError
from tee_arena.society import observer

REST = '{"action": "rest"}'


def posting(**overrides):
    script = {"Alder": '{"action": "post", "text": "A synthetic idea about maps."}',
              "Birch": '{"action": "post", "text": "A synthetic reply about rivers."}',
              "Cedar": REST, "Hazel": REST}
    script.update(overrides)
    return script


class ModerationIsolationTests(WorkerCase):
    def posts(self):
        return {r["id"]: (r["status"], r["screen_failures"])
                for r in self.conn.execute("SELECT id, status, screen_failures FROM society_posts ORDER BY id")}

    def assert_running(self, summary):
        self.assertFalse(summary["stopped"])
        self.assertFalse(society.clock(self.conn)["paused"])
        self.assertEqual(self.events("paused"), [])

    def test_observer_provider_failures_never_pause_and_founders_keep_acting(self):
        def down(request):
            raise RuntimeError("observer upstream down")
        run = self.make(posting(observer=down))
        summary = run.run()
        self.assert_running(summary)
        self.assertEqual(summary["turns"], 4)  # every founder acted although the observer is down
        # screen() runs after each posting turn: post 1 was tried twice in this pass, post 2 once
        self.assertEqual(self.posts(), {1: ("pending", 2), 2: ("pending", 1)})
        self.assertEqual(run.failures, 0)  # observer failures never count toward the founder pause
        for _ in range(4):  # later passes retry up to the limit, still without a pause
            self.assert_running(run.run())
        self.assertEqual(self.posts(), {1: ("pending", 3), 2: ("pending", 3)})

    def test_observer_outage_ends_screening_for_the_pass_without_charging_the_posts(self):
        for category in ("cap_exceeded", "credential_unavailable", "stopped", "model_not_allowed"):
            with self.subTest(category=category):
                self.conn.execute("DELETE FROM society_posts WHERE status='pending'")
                self.conn.commit()
                self.next_hour()
                run = self.make(posting())
                with patch.object(observer, "review", side_effect=GatewayError(category)) as review:
                    summary = run.run()
                self.assert_running(summary)
                self.assertEqual(summary["turns"], 4)
                self.assertTrue(all(v == ("pending", 0) for v in self.posts().values()))
                self.assertIn({"moderation": "halted_for_this_pass", "reason": category}, run.log)
                self.assertLessEqual(review.call_count, 3)  # at most once per screening pass, then halted

    def test_three_failed_screenings_in_a_pass_halt_only_screening(self):
        run = self.make(posting())
        error = GatewayError("provider_failure", sent=True, charged_microusd=5)
        with patch.object(run, "turn", return_value="done"):
            for agent in ("alder", "birch", "cedar", "hazel"):
                self.turn(agent, action="post", text=f"Synthetic post by {agent}.")
            self.conn.commit()
            with patch.object(observer, "review", side_effect=error) as review:
                self.assert_running(run.run())
        self.assertEqual(review.call_count, 3)
        self.assertEqual(sorted(v[1] for v in self.posts().values()), [0, 1, 1, 1])

    def test_an_unexpected_screening_exception_stays_with_its_post(self):
        run = self.make(posting())
        real = society_moderation.apply_verdict

        def poison(conn, rules, post_id, verdict, now):
            if post_id == 1:
                conn.execute("UPDATE society_posts SET flagged = true WHERE id = 1")  # rolled back
                raise KeyError("synthetic bug")
            return real(conn, rules, post_id, verdict, now)
        with patch.object(society_moderation, "apply_verdict", side_effect=poison):
            summary = run.run()
        self.assert_running(summary)
        self.assertEqual(self.posts(), {1: ("pending", 2), 2: ("published", 0)})
        self.assertFalse(self.conn.execute("SELECT flagged FROM society_posts WHERE id=1").fetchone()["flagged"])
        self.assertIn({"post": 1, "error": "screening_unexpected"}, run.log)
        with patch.object(observer, "review", side_effect=ValueError("synthetic parser bug")):
            self.assert_running(run.run())
        self.assertEqual(self.posts()[1], ("pending", 3))

    # Safeguards and diagnostics (adapted from klados-world#38).

    def pending_post(self, text="A synthetic pending question."):
        post = self.conn.execute("""INSERT INTO society_posts (author_id, tick, text, created_at)
                                    VALUES ('alder', 1, %s, %s) RETURNING id""", (text, self.now)).fetchone()["id"]
        self.conn.commit()
        return post

    def resting(self):
        return self.make({name: REST for name in ("Alder", "Birch", "Cedar", "Hazel")})

    def test_observer_errors_never_reach_the_founder_safety_path(self):
        post_id = self.pending_post()
        run = self.resting()
        for category in ("provider_failure", "credential_unavailable", "cap_exceeded", "stopped",
                         "model_not_allowed", "response_invalid"):
            with self.subTest(category=category):
                with patch.object(observer, "review", side_effect=GatewayError(category)), \
                        patch.object(run, "_failed", side_effect=AssertionError("observer cannot pause")) as failed:
                    self.assertIsNone(run.screen())
                failed.assert_not_called()
                self.assertFalse(society.clock(self.conn)["paused"])
                self.assertEqual(run.failures, 0)
        self.assertEqual(self.posts()[post_id], ("pending", 2))  # provider_failure, response_invalid only

    def test_private_text_never_reaches_the_log(self):
        first = self.pending_post("A malformed synthetic fixture.")
        second = self.pending_post("Another independent synthetic contribution.")
        run = self.resting()

        def review(_gateway, text, *_args, **_kwargs):
            if "malformed" in text:
                raise TypeError("PRIVATE_CONTENT_CANARY")
            return dict(CLEAN)
        with patch.object(observer, "review", side_effect=review):
            self.assertIsNone(run.screen())
        with patch.object(observer, "review", side_effect=observer.ObserverError("PRIVATE_CODE_CANARY")):
            self.assertIsNone(run.screen())
        self.assertEqual(self.posts(), {first: ("pending", 2), second: ("published", 0)})
        self.assertIn({"post": first, "error": "observer reply_invalid_response", "charged_microusd": 0}, run.log)
        self.assertNotIn("CANARY", str(run.log))
        self.assertFalse(society.clock(self.conn)["paused"])

    def test_a_partial_verdict_write_is_rolled_back_before_the_failure_count(self):
        post_id = self.pending_post()
        run = self.resting()

        def partial(conn, *_args):
            conn.execute("UPDATE society_posts SET status = 'published' WHERE id = %s", (post_id,))
            raise RuntimeError("PRIVATE_VERDICT_CANARY")
        with patch.object(observer, "review", return_value=dict(CLEAN)), \
                patch.object(society_moderation, "apply_verdict", side_effect=partial):
            self.assertIsNone(run.screen())
        self.assertEqual(self.posts()[post_id], ("pending", 1))
        self.assertNotIn("CANARY", str(run.log))

    def test_missing_verdict_metadata_cannot_publish_or_crash_the_loop(self):
        post_id = self.pending_post()
        later = self.pending_post("A second synthetic contribution.")
        run = self.resting()
        malformed = dict(CLEAN)
        malformed.pop("charged_microusd")
        with patch.object(observer, "review", side_effect=[malformed, dict(CLEAN)]):
            self.assertIsNone(run.screen())
        self.assertEqual(self.posts(), {post_id: ("pending", 1), later: ("published", 0)})
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_findings WHERE post_id = %s",
                                           (post_id,)).fetchone()["n"], 0)

    def test_unusable_verdicts_stay_bounded(self):
        post_id = self.pending_post()
        run = self.resting()
        with patch.object(observer, "review", side_effect=observer.ObserverError("invalid_json")) as review:
            for _ in range(5):
                self.assertIsNone(run.screen())
        self.assertEqual(review.call_count, 3)
        self.assertEqual(self.posts()[post_id], ("pending", 3))
        self.assertEqual(run.failures, 0)

    def test_a_persistence_failure_ends_only_the_pass(self):
        self.pending_post()
        run = self.resting()
        with patch.object(observer, "review", side_effect=ValueError("PRIVATE_CANARY")), \
                patch.object(society_moderation, "screen_failed", side_effect=RuntimeError("PRIVATE_DATABASE_CANARY")):
            self.assertEqual(run.screen(), "stop")
            summary = run.run()
        self.assertTrue(summary["stopped"])
        self.assertEqual(summary["turns"], 0)
        self.assertFalse(society.clock(self.conn)["paused"])
        self.assertEqual(self.events("paused"), [])
        self.assertEqual(run.failures, 0)
        self.assertIn({"post": 1, "error": "screening_persistence_failure"}, run.log)
        self.assertNotIn("CANARY", str(run.log))
        self.assertEqual(run.run()["turns"], 4)  # once the database records again, the next pass runs

    # Founder inference, budget and infrastructure failures keep their existing safety pause.

    def test_founder_failures_still_pause_after_observer_failures(self):
        def broken(request):
            raise RuntimeError("founder upstream down")
        run = self.make({name: broken for name in ("Alder", "Birch", "Cedar", "Hazel")})
        with patch.object(observer, "review", side_effect=GatewayError("provider_failure", sent=True)):
            self.assertTrue(run.run()["stopped"])
        self.assertTrue(society.clock(self.conn)["paused"])
        self.assertEqual(self.events("paused")[-1]["data"]["reason"], "worker: provider_failure")

    def test_founder_budget_credential_and_stop_file_still_pause_at_once(self):
        for category in ("cap_exceeded", "credential_unavailable", "stopped", "model_not_allowed"):
            with self.subTest(category=category):
                society.set_paused(self.conn, RULES, False, self.now, "test")
                self.conn.commit()
                run = self.make({name: REST for name in ("Alder", "Birch", "Cedar", "Hazel")})
                self.next_hour()
                with patch.object(run, "call", side_effect=GatewayError(category)):
                    self.assertTrue(run.run()["stopped"])
                self.assertTrue(society.clock(self.conn)["paused"])
                self.assertEqual(self.events("paused")[-1]["data"]["reason"], f"worker: {category}")

    def test_a_spent_global_cap_still_pauses_with_a_real_ledger(self):
        run = self.make({name: REST for name in ("Alder", "Birch", "Cedar", "Hazel")}, cap=10)
        self.assertTrue(run.run()["stopped"])
        self.assertEqual(self.events("paused")[-1]["data"]["reason"], "worker: cap_exceeded")

    def test_a_real_stop_file_still_pauses(self):
        run = self.make({name: REST for name in ("Alder", "Birch", "Cedar", "Hazel")})
        with tempfile.TemporaryDirectory() as directory:
            stop = Path(directory) / "KILLED"
            stop.touch()
            run.gateway.stop_file = stop
            self.assertTrue(run.run()["stopped"])
        self.assertEqual(self.events("paused")[-1]["data"]["reason"], "worker: stopped")


class LastWordsIsolationTests(WorkerCase):
    # Helpers only; inheriting FossilTests would run its tests twice.
    starve, fossil, last_words_script = FossilTests.starve, FossilTests.fossil, FossilTests.last_words_script

    def test_observer_failure_on_last_words_never_pauses(self):
        for error in (GatewayError("cap_exceeded"), GatewayError("provider_failure", sent=True),
                      observer.ObserverError("response_invalid"), RuntimeError("synthetic")):
            with self.subTest(error=type(error).__name__):
                run = self.last_words_script('{"text": "Measure what you value, friends."}')
                if self.fossil("hazel") is None:
                    self.starve("hazel")
                self.conn.execute("UPDATE society_fossils SET last_words_attempts = 0, last_words_status = 'pending'")
                self.conn.commit()
                with patch.object(observer, "review", side_effect=error), \
                        patch.object(run, "_failed", side_effect=AssertionError("observer cannot pause")):
                    self.assertFalse(run.run()["stopped"])
                self.assertFalse(society.clock(self.conn)["paused"])
                fossil = self.fossil("hazel")
                self.assertEqual((fossil["last_words_status"], fossil["last_words_attempts"]), ("pending", 1))

    def test_founder_failure_generating_last_words_still_counts(self):
        run = self.last_words_script('{"text": "unused"}')
        self.starve("hazel")
        run.max_failures = 1
        with patch.object(run, "call", side_effect=GatewayError("provider_failure", sent=True)):
            self.assertTrue(run.run()["stopped"])
        self.assertTrue(society.clock(self.conn)["paused"])
