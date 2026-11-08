from datetime import timedelta
from pathlib import Path
import tempfile
import os
from unittest.mock import patch

from tests.arena_support import DSN, psycopg
from tests.test_arena_society import RULES
from tests.test_arena_society_life import CLEAN, FOUNDERS, WorkerCase

if psycopg:
    from psycopg import sql
    from arena import db, scheduler, society, society_moderation, society_report


class SchedulerTests(WorkerCase):
    script = {name: '{"action": "rest"}' for name in ("Alder", "Birch", "Cedar", "Hazel")}

    def setUp(self):
        super().setUp()
        self.run_worker = self.make(dict(self.script))
        self.gateway = self.run_worker.gateway

    def step(self, at):
        return scheduler.step(self.conn, RULES, self.gateway, self.gateway, at)

    def test_invalid_purpose_config_cannot_advance_time_or_call_a_provider(self):
        before = society.clock(self.conn)["tick"]
        with patch.dict(os.environ, KLADOS_SHARED_PURPOSE="unavailable-test-v1"):
            with self.assertRaises(scheduler.worker.shared_purpose.PurposeConfigurationError):
                self.step(self.now + timedelta(hours=6))
        self.assertEqual(society.clock(self.conn)["tick"], before)
        self.assertEqual(self.provider.requests, [])

    def test_one_tick_per_hour_and_no_turn_twice_even_after_a_restart(self):
        start = self.now + timedelta(minutes=59)
        first = self.step(start)  # the hour already ticked in setUp: no new tick, the turns run
        self.assertEqual((first["tick"], first.get("ticked"), first["turns"]), (1, None, 4))
        again = self.step(start + timedelta(seconds=30))  # a restarted scheduler, same hour
        self.assertEqual((again["tick"], again["turns"], again["failed"]), (1, 0, 0))
        later = self.step(self.now + timedelta(hours=1))
        self.assertEqual((later["tick"], later["ticked"]), (2, True))
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_turns").fetchone()["n"],
                         4 + later["turns"])

    def test_a_paused_society_does_nothing(self):
        society.set_paused(self.conn, RULES, True, self.now, "operator")
        result = self.step(self.now + timedelta(hours=5))
        self.assertEqual(result, {"tick": 1, "paused": True})
        self.assertEqual(len(self.provider.requests), 0)

    def test_only_one_scheduler_can_hold_the_lock(self):
        def connect():
            conn = db.connect(DSN)
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn
        first, second = connect(), connect()
        try:
            self.assertTrue(first.execute("SELECT pg_try_advisory_lock(%s) AS ok", (scheduler.LOCK,)).fetchone()["ok"])
            self.assertFalse(second.execute("SELECT pg_try_advisory_lock(%s) AS ok", (scheduler.LOCK,)).fetchone()["ok"])
        finally:
            first.close()
            second.close()


class ReportTests(WorkerCase):
    def test_the_report_holds_aggregates_and_nothing_private(self):
        self.publish(self.turn("alder", action="post", text="I will publish a table tomorrow.",
                               note="PRIVATE-NOTE-ALDER", reason="PRIVATE-REASON-ALDER")["post_id"])
        withheld = self.turn("birch", action="post", text="WITHHELD-TEXT-BIRCH")["post_id"]
        society_moderation.apply_verdict(self.conn, RULES, withheld,
                                         dict(CLEAN, codes=["OUT_OF_BAND"], severity="grave", evidence="EVIDENCE-X"),
                                         self.now)
        self.conn.execute("""INSERT INTO society_strategies (agent_id, version, text, reason, tick, created_at)
                             VALUES ('cedar', 1, 'PRIVATE-STRATEGY-CEDAR', 'PRIVATE-WHY', 1, %s)""", (self.now,))
        run = self.make({"Alder": '{"action": "rest"}'})
        text = society_report.build(self.conn, RULES, run.gateway.ledger, self.now)
        for secret in ("PRIVATE-NOTE-ALDER", "PRIVATE-REASON-ALDER", "WITHHELD-TEXT-BIRCH", "EVIDENCE-X",
                       "PRIVATE-STRATEGY-CEDAR", "PRIVATE-WHY", "I will publish a table"):
            self.assertNotIn(secret, text)
        for heading in ("## Energie", "## Activitate", "## Diversitate", "## Reflecții", "## Moderare", "## Erori",
                        "## Costuri", "## Confidențialitate"):
            self.assertIn(heading, text)
        self.assertIn("reținute: 1", text)
        self.assertIn("Postări cu angajamente", text)
        self.assertIn("(id-uri: 1)", text)

    def test_a_leak_of_another_founders_private_text_is_counted(self):
        self.turn("alder", action="rest", note="Hazel is the weakest; strike her alliance first.")
        self.next_hour()
        leaked = self.turn("birch", action="post", text="Overheard: Hazel is the weakest; strike her alliance first.")
        self.publish(leaked["post_id"])
        self.assertEqual(society_report._leaks(self.conn), 1)
