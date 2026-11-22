"""Moderation failures stay with the post; founder, budget and infrastructure failures still pause."""
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from tests.test_arena_society import RULES
from tests.test_arena_society_fossils import FossilTests
from tests.test_arena_society_life import CLEAN, WorkerCase
from tests.arena_support import DSN, psycopg

if psycopg:
    from arena import society, society_moderation
from tee_arena import store
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
    def make(self, *args, **kwargs):
        run = super().make(*args, **kwargs)
        # These retained regressions exercise the Observer through the tiered path.
        run.moderation_client = Mock(classify=Mock(return_value=['hate']))
        return run

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
        for category in ("credential_unavailable", "stopped", "model_not_allowed"):
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

    # Request ids after an outage, with the real gateway and ledger (Technical Lead review on #39).

    def ledger_requests(self, run, prefix="klados.observe."):
        _, document = store.read(run.gateway.ledger.path)
        kinds = {}
        for event in document["events"]:
            if event["kind"] == "reserve":
                kinds[event["reservation"]] = [event["request_id"], "open"]
            elif event["kind"] in ("settle", "release"):
                kinds[event["reservation"]][1] = event["kind"]
        return [tuple(v) for v in kinds.values() if v[0].startswith(prefix)]

    def test_missing_key_released_then_restored_screens_without_duplicate_or_retry_loss(self):
        post_id = self.pending_post()
        state = {"key": False}

        def observer_reply(request):
            if not state["key"]:  # what OpenRouterProvider raises before sending without a key
                raise GatewayError("credential_unavailable")
            return '{"codes": [], "evidence": ""}'
        run = self.make({"observer": observer_reply, **{n: REST for n in ("Alder", "Birch", "Cedar", "Hazel")}})
        for _ in range(3):  # three passes while the key is missing
            self.assertIsNone(run.screen())
        self.assertEqual(self.posts()[post_id], ("pending", 0))  # no retry lost
        self.assertEqual(self.ledger_requests(run), [
            (f"klados.observe.{post_id}.0", "release"),
            (f"klados.observe.{post_id}.0.o1", "release"),
            (f"klados.observe.{post_id}.0.o2", "release")])
        state["key"] = True  # key restored
        self.assertIsNone(run.screen())
        self.assertEqual(self.posts()[post_id], ("published", 0))
        self.assertEqual(self.ledger_requests(run)[-1], (f"klados.observe.{post_id}.0.o3", "settle"))
        self.assertNotIn("duplicate_request", str(run.log))
        self.assertEqual(run.failures, 0)
        self.assertFalse(society.clock(self.conn)["paused"])
        outages = self.events("moderation_outage")
        self.assertEqual([(e["agent_id"], e["data"]) for e in outages],
                         [(None, {"post_id": post_id, "category": "credential_unavailable"})] * 3)

    def test_every_release_before_send_outage_gets_a_new_id(self):
        class Rejected(Exception):
            def __init__(self, status):
                super().__init__("synthetic")
                self.status_code = status
        cases = [("credential_unavailable", lambda: GatewayError("credential_unavailable")),
                 ("sdk_unavailable", lambda: GatewayError("sdk_unavailable"))]
        cases += [(f"http_{status}", lambda status=status: Rejected(status)) for status in (401, 402, 403, 429)]
        for name, failure in cases:
            with self.subTest(name=name):
                self.conn.execute("UPDATE society_posts SET status = 'published' WHERE status = 'pending'")
                self.conn.commit()
                post_id = self.pending_post()
                state = {"down": True}

                def observer_reply(request, failure=failure, state=state):
                    if state["down"]:
                        raise failure()
                    return '{"codes": [], "evidence": ""}'
                run = self.make({"observer": observer_reply, **{n: REST for n in ("Alder", "Birch", "Cedar", "Hazel")}})
                with patch("time.sleep"):  # 429 is first retried by the observer itself
                    self.assertIsNone(run.screen())
                    self.assertIsNone(run.screen())
                    state["down"] = False
                    self.assertIsNone(run.screen())
                self.assertEqual(self.posts()[post_id], ("published", 0))
                requests = self.ledger_requests(run)
                per_pass = 4 if name == "http_429" else 1
                self.assertEqual([r[1] for r in requests], ["release"] * 2 * per_pass + ["settle"])
                self.assertEqual(len({r[0] for r in requests}), len(requests))  # every id used once
                self.assertNotIn("duplicate_request", str(run.log))
                self.assertFalse(society.clock(self.conn)["paused"])

    def test_a_paid_attempt_is_never_repeated_under_the_same_id(self):
        post_id = self.pending_post()
        run = self.make({n: REST for n in ("Alder", "Birch", "Cedar", "Hazel")})
        book = run.gateway.ledger  # a crash after a paid call: the id is reserved and settled
        book.settle(book.reserve("society-observer", 100, f"klados.observe.{post_id}.0"), 90)
        before = len(self.provider.requests)
        self.assertIsNone(run.screen())
        self.assertEqual(len(self.provider.requests), before)  # no second paid call for that id
        self.assertEqual(self.posts()[post_id], ("pending", 1))  # a post failure: the id advances
        self.assertIn({"post": post_id, "error": "observer call_duplicate_request"}, run.log)
        self.assertIsNone(run.screen())
        self.assertEqual(self.posts()[post_id], ("published", 1))
        self.assertEqual(self.ledger_requests(run)[-1], (f"klados.observe.{post_id}.1", "settle"))

    def test_post_specific_rejections_still_cost_a_retry(self):
        class Rejected(Exception):
            status_code = 413
        post_id = self.pending_post()

        def observer_reply(request):
            raise Rejected("synthetic")
        run = self.make({"observer": observer_reply, **{n: REST for n in ("Alder", "Birch", "Cedar", "Hazel")}})
        self.assertIsNone(run.screen())
        self.assertEqual(self.posts()[post_id], ("pending", 1))
        self.assertEqual(self.events("moderation_outage"), [])

    # Founder inference, budget and infrastructure failures keep their existing safety pause.

    def test_founder_failures_still_pause_after_observer_failures(self):
        def broken(request):
            raise RuntimeError("founder upstream down")
        run = self.make({name: broken for name in ("Alder", "Birch", "Cedar", "Hazel")})
        with patch.object(observer, "review", side_effect=GatewayError("provider_failure", sent=True)):
            self.assertTrue(run.run()["stopped"])
        self.assertTrue(society.clock(self.conn)["paused"])
        self.assertEqual(self.events("paused")[-1]["data"]["reason"], "worker: provider_failure")

    def test_founder_credential_and_stop_file_still_pause_at_once(self):
        for category in ("credential_unavailable", "stopped", "model_not_allowed"):
            with self.subTest(category=category):
                society.set_paused(self.conn, RULES, False, self.now, "test")
                self.conn.commit()
                run = self.make({name: REST for name in ("Alder", "Birch", "Cedar", "Hazel")})
                self.next_hour()
                with patch.object(run, "call", side_effect=GatewayError(category)):
                    self.assertTrue(run.run()["stopped"])
                self.assertTrue(society.clock(self.conn)["paused"])
                self.assertEqual(self.events("paused")[-1]["data"]["reason"], f"worker: {category}")

    def test_a_spent_global_cap_is_scoped_financial_rest(self):
        run = self.make({name: REST for name in ("Alder", "Birch", "Cedar", "Hazel")}, cap=10)
        self.assertFalse(run.run()["stopped"])
        self.assertFalse(society.clock(self.conn)["paused"])
        self.assertEqual(len(self.provider.requests), 0)

    def test_a_real_stop_file_still_pauses(self):
        run = self.make({name: REST for name in ("Alder", "Birch", "Cedar", "Hazel")})
        with tempfile.TemporaryDirectory() as directory:
            stop = Path(directory) / "KILLED"
            stop.touch()
            run.gateway.stop_file = stop
            self.assertTrue(run.run()["stopped"])
        self.assertEqual(self.events("paused")[-1]["data"]["reason"], "worker: stopped")


    def test_observer_reservation_outage_keeps_retries_and_founder_accounting_pause(self):
        first = self.pending_post()
        second = self.pending_post("Another pending synthetic contribution.")
        run = self.resting()
        book = run.gateway.ledger
        before = book.path.read_bytes()
        with patch.object(book, "reserve", side_effect=OSError("PRIVATE_LEDGER_CANARY")):
            with patch.object(run, "_failed", side_effect=AssertionError("observer cannot pause founders")):
                for _ in range(4):
                    self.assertIsNone(run.screen())
                    self.assertEqual(self.posts(), {first: ("pending", 0), second: ("pending", 0)})
                    self.assertFalse(society.clock(self.conn)["paused"])
                    self.assertEqual(run.failures, 0)
            self.assertEqual(len(self.provider.requests), 0)
            self.assertEqual(book.path.read_bytes(), before)
            self.assertEqual(len(self.events("moderation_outage")), 4)
            # The same accounting failure on the founder path still pauses immediately.
            self.assertTrue(run.run()["stopped"])
        self.assertTrue(society.clock(self.conn)["paused"])
        self.assertEqual(self.events("paused")[-1]["data"]["reason"], "worker: accounting_unavailable")
        self.assertEqual(self.posts(), {first: ("pending", 0), second: ("pending", 0)})
        self.assertNotIn("PRIVATE_LEDGER_CANARY", str(run.log))

    def test_observer_failed_settlement_preserves_pending_posts_and_open_reservation(self):
        first = self.pending_post()
        second = self.pending_post("Another pending synthetic contribution.")
        run = self.resting()
        book = run.gateway.ledger
        with patch.object(book, "settle", side_effect=OSError("PRIVATE_SETTLEMENT_CANARY")), \
                patch.object(run, "_failed", side_effect=AssertionError("observer cannot pause founders")):
            self.assertIsNone(run.screen())
        self.assertEqual(self.posts(), {first: ("pending", 0), second: ("pending", 0)})
        self.assertEqual(len(self.provider.requests), 1)
        self.assertFalse(society.clock(self.conn)["paused"])
        self.assertEqual(run.failures, 0)
        self.assertEqual(book.totals()["charged"], 0)
        self.assertGreater(book.totals()["reserved"], 0)
        self.assertEqual(self.ledger_requests(run), [(f"klados.observe.{first}.0", "open")])
        # Reloading retains the conservative hold. No automatic release or reconciliation.
        from tee_arena.ledger import Ledger
        self.assertEqual(Ledger(book.path).totals(), book.totals())
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_findings").fetchone()["n"], 0)
        self.assertEqual(self.events("moderation_outage")[-1]["data"],
                         {"post_id": first, "category": "accounting_unavailable"})
        self.assertNotIn("PRIVATE_SETTLEMENT_CANARY", str(run.log))
    def test_generation_and_outage_identity_survive_worker_restart(self):
        post_id = self.pending_post()
        script = {name: REST for name in ("Alder", "Birch", "Cedar", "Hazel")}
        run = self.make(script)
        with patch.object(observer, "review", side_effect=GatewayError("credential_unavailable")):
            run.screen()
        self.assertEqual(self.posts()[post_id], ("pending", 0))
        self.conn.execute("UPDATE society_posts SET screen_failures=3 WHERE id=%s", (post_id,))
        society_moderation.retry_screening(self.conn, RULES, post_id, self.now)
        self.conn.commit()
        post = next(p for p in society_moderation.pending(self.conn) if p["id"] == post_id)
        generation = post["screening_generation"]
        self.assertGreater(generation, 0)
        from arena import worker
        restarted = worker.Worker(self.conn, RULES, run.gateway, observer_gateway=run.observer_gateway,
                                  moderation_client=run.moderation_client, now=lambda: self.now)
        expected = f"klados.observe.{post_id}.g{generation}.0.o1"
        self.assertEqual(restarted._observe_request_id(post), expected)
        with patch.object(observer, "review", return_value=CLEAN.copy()) as review:
            restarted.screen()
        self.assertEqual(review.call_args.kwargs["request_id"], expected)
        self.assertEqual(self.posts()[post_id], ("published", 0))
        self.assertFalse(society.clock(self.conn)["paused"])

    def test_classifier_outage_preserves_post_retries_and_durable_audit(self):
        from arena import moderation
        post_id = self.pending_post()
        run = self.make({name: REST for name in ("Alder", "Birch", "Cedar", "Hazel")})
        for code in ("credential_unavailable", "service_unavailable"):
            run.moderation_client.classify.side_effect = moderation.ModerationError(code)
            with patch.object(observer, "review") as review:
                self.assertIsNone(run.screen())
                review.assert_not_called()
            self.assertEqual(self.posts()[post_id], ("pending", 0))
            self.assertEqual(run.failures, 0)
            self.assertFalse(society.clock(self.conn)["paused"])
        self.assertEqual(len(self.events("moderation_request")), 2)
        self.assertEqual(len(self.events("moderation_outage")), 2)
        run.moderation_client.classify.side_effect = None
        run.moderation_client.classify.return_value = []
        run.screen()
        self.assertEqual(self.posts()[post_id], ("published", 0))


    def test_invalid_config_repeated_outages_preserve_posts_and_founder_state(self):
        from arena import moderation
        first = self.pending_post()
        second = self.pending_post()
        run = self.make({name: REST for name in ("Alder", "Birch", "Cedar", "Hazel")})
        founders = self.conn.execute("SELECT * FROM society_agents ORDER BY id").fetchall()
        clock = dict(society.clock(self.conn))
        before_ledger = run.gateway.ledger.path.read_bytes()
        with patch.object(moderation, "load_config", side_effect=moderation.ModerationError("invalid_config")), \
                patch.object(observer, "review") as review, \
                patch.object(run, "_failed", side_effect=AssertionError("classifier cannot pause founders")):
            for _ in range(4):
                self.assertIsNone(run.screen())
                self.assertEqual(self.posts(), {first: ("pending", 0), second: ("pending", 0)})
                self.assertEqual(run.failures, 0)
            review.assert_not_called()
        self.assertEqual(run.moderation_client.classify.call_count, 0)
        self.assertEqual(self.events("moderation_request"), [])
        outages = self.events("moderation_outage")
        self.assertEqual([e["data"] for e in outages],
                         [{"post_id": first, "category": "moderation_invalid_config"}] * 4)
        # Read independently to prove outage evidence survived committed transactions.
        with psycopg.connect(DSN) as check:
            query = psycopg.sql.SQL("SELECT count(*) FROM {}.society_events WHERE kind='moderation_outage'").format(
                psycopg.sql.Identifier(self.schema))
            self.assertEqual(check.execute(query).fetchone()[0], 4)
        self.assertEqual(self.conn.execute("SELECT * FROM society_agents ORDER BY id").fetchall(), founders)
        self.assertEqual(dict(society.clock(self.conn)), clock)
        self.assertEqual(run.gateway.ledger.path.read_bytes(), before_ledger)
        with patch.object(observer, "review", return_value=CLEAN.copy()):
            run.screen()
        self.assertEqual(self.posts(), {first: ("published", 0), second: ("published", 0)})


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
