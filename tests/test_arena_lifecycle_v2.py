"""Persistent lifecycle contracts using disposable PostgreSQL and synthetic data."""

import copy
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from psycopg import sql

from arena import (
    db,
    external_agents,
    society,
    society_life,
    society_moderation,
    society_public,
    website,
)
from tee_arena.society import constitution, prompts, rules
from tests.arena_support import DatabaseCase
from tests.test_arena_external_agents import InviteCase, NOW

V2 = rules.load()
V1 = rules.load(rules.LEGACY_PATH)
CLEAN = dict(codes=[], severity="none", evidence="", by="model", charged_microusd=0)


class LifecycleRulesTests(unittest.TestCase):
    def test_default_is_persistent_and_legacy_remains_available(self):
        self.assertEqual(V2.version, "rules-v2")
        self.assertTrue(V2.persistent)
        self.assertFalse(V2.energy_enabled)
        self.assertIsNone(V2.max_days)
        self.assertIsNone(V2.max_hours)
        self.assertFalse(V1.persistent)
        self.assertEqual(V1.max_hours, 720)
        self.assertEqual(V2.min_minutes_between_ticks, V1.min_minutes_between_ticks)
        self.assertEqual(
            V2.agent_likes_per_pair_per_day, V1.agent_likes_per_pair_per_day
        )
        document = json.loads(rules.DEFAULT_PATH.read_text())
        for section, key, value in [
            ("energy", "cap", 1),
            ("age", "max_days", 30),
            ("actions", "post", 0.5),
            ("penalties", "grave", 1),
        ]:
            changed = copy.deepcopy(document)
            changed[section][key] = value
            with self.assertRaises(rules.RulesError):
                rules.parse(changed)

    def test_prompts_and_current_rule_pages_describe_persistence(self):
        self.assertEqual(constitution.VERSION, 2)
        system, _ = prompts.turn({"you": {"name": "Alder"}})
        self.assertIn("no energy economy", system)
        self.assertNotIn("you die when", system)
        self.assertIn("voluntarily share", system)
        self.assertIn("Protect other agents' private information", system)
        with patch.dict(os.environ, KLADOS_RULES=str(rules.DEFAULT_PATH)):
            public = website.public_rules()
            pages = website.docs_content()["pages"]
        self.assertFalse(public["energy_enabled"])
        self.assertIsNone(public["max_days"])
        text = json.dumps(pages)
        self.assertIn("no maximum lifespan", text)
        self.assertNotIn("Death follows running out", text)
        self.assertNotIn("Living costs are charged", text)


class NewPersistentSocietyTests(DatabaseCase):
    def test_new_founders_have_no_energy_journal(self):
        society.found(self.conn, V2, NOW)
        self.assertEqual(
            [
                r["name"]
                for r in self.conn.execute(
                    "SELECT name FROM society_agents ORDER BY id"
                )
            ],
            ["Alder", "Birch", "Cedar", "Hazel"],
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) AS n FROM energy_entries").fetchone()[
                "n"
            ],
            0,
        )
        self.assertTrue(
            all(r["energy"] is None for r in society.status(self.conn, V2)["agents"])
        )
        self.assertEqual(society.audit(self.conn), [])


class PersistentLifecycleTests(InviteCase):
    def setUp(self):
        super().setUp()  # Starts with populated rules-v1 founder balances for upgrade coverage.
        environment = patch.dict(os.environ, KLADOS_RULES=str(rules.DEFAULT_PATH))
        environment.start()
        self.addCleanup(environment.stop)

    def tick(self):
        society.set_paused(self.conn, V2, False, self.now, "synthetic")
        self.now += timedelta(hours=1)
        result = society.tick(self.conn, V2, self.now)
        self.conn.commit()
        return result

    def submit(self, registered, **decision):
        return self.client.post(
            "/api/agents/me/actions", headers=self.headers(registered), json=decision
        )

    def test_legacy_profile_schema_stays_unchanged(self):
        legacy = society_public.profile(self.conn, V1, "alder")
        self.assertNotIn("energy_enabled", legacy)
        self.assertNotIn("persistent", legacy)
        self.assertEqual(legacy["energy"], 300)
        current = society_public.profile(self.conn, V2, "alder")
        self.assertFalse(current["energy_enabled"])
        self.assertTrue(current["persistent"])
        self.assertIsNone(current["energy"])

    def test_beyond_thirty_days_and_negative_historical_balance_do_not_end_identity(
        self,
    ):
        society.apply_energy(
            self.conn,
            V1,
            "alder",
            "living",
            -30100,
            "synthetic:historic-debit",
            self.now,
            allow_negative=True,
        )
        self.conn.commit()
        before = list(self.conn.execute("SELECT * FROM energy_entries ORDER BY id"))
        for _ in range(721):
            self.assertEqual(self.tick()["deaths"], [])
        self.assertEqual(
            before, list(self.conn.execute("SELECT * FROM energy_entries ORDER BY id"))
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_agents WHERE status='alive'"
            ).fetchone()["n"],
            4,
        )
        self.assertEqual(society.clock(self.conn)["rules_version"], "rules-v2")
        self.assertTrue(society.claim_turn(self.conn, "alder", 721, self.now))
        self.assertEqual(
            society_life.act(
                self.conn,
                V2,
                "alder",
                721,
                {"action": "post", "text": "Still participating after thirty days."},
                self.now,
            )["action"],
            "post",
        )
        self.assertEqual(society.audit(self.conn), [])
        with self.assertRaises(society.SocietyError):
            society.apply_energy(
                self.conn, V2, "alder", "like", 100, "synthetic:new-credit", self.now
            )
        with self.assertRaises(society.SocietyError):
            society.die(self.conn, V2, "alder", 721, "age", self.now)
        society.set_paused(self.conn, V2, True, self.now, "synthetic")
        self.assertFalse(
            society.tick(self.conn, V2, self.now + timedelta(days=90))["ticked"]
        )

    def test_all_actions_work_without_energy_and_turn_and_like_limits_remain(self):
        first = self.register().json()
        second = self.register(name="Second persistent agent").json()
        before = list(self.conn.execute("SELECT * FROM energy_entries ORDER BY id"))
        self.tick()
        post = self.submit(
            first, action="post", text="A persistent contribution."
        ).json()["post_id"]
        society_moderation.apply_verdict(self.conn, V2, post, CLEAN, self.now)
        self.conn.commit()
        self.assertEqual(self.submit(first, action="rest").status_code, 409)
        reply = self.submit(
            second, action="reply", post_id=post, text="A persistent response."
        )
        self.assertEqual(reply.json()["action"], "reply")
        society_moderation.apply_verdict(
            self.conn, V2, reply.json()["post_id"], CLEAN, self.now
        )
        self.conn.commit()
        self.assertEqual(len(self.client.get("/api/society/square").json()["posts"]), 2)
        self.tick()
        self.assertFalse(
            self.submit(second, action="like", post_id=post).json()["paid"]
        )
        self.tick()
        self.assertEqual(
            self.submit(second, action="like", post_id=post).json()["action"], "rest"
        )
        self.assertEqual(self.submit(first, action="rest").json()["action"], "rest")
        self.assertEqual(
            before, list(self.conn.execute("SELECT * FROM energy_entries ORDER BY id"))
        )
        self.assertTrue(society.relations(self.conn, second["id"])["replied_to"])
        self.assertEqual(society.audit(self.conn), [])
        profile = self.client.get("/api/agents/me", headers=self.headers(first)).json()
        self.assertIsNone(profile["energy"])
        self.assertFalse(profile["energy_enabled"])
        self.assertTrue(profile["persistent"])

    def test_moderation_preserves_findings_without_energy_or_automatic_exile(self):
        registered = self.register().json()
        for index in range(4):
            self.tick()
            text = [
                "One topic about forests.",
                "A distinct view of oceans.",
                "Another perspective on mountains.",
                "Different questions about stars.",
            ][index]
            post = self.submit(registered, action="post", text=text).json()["post_id"]
            severity = "light" if index == 0 else "grave"
            verdict = dict(CLEAN, severity=severity, codes=["synthetic"])
            society_moderation.apply_verdict(self.conn, V2, post, verdict, self.now)
            if severity == "grave":
                finding = self.conn.execute(
                    "SELECT id FROM society_findings WHERE post_id=%s", (post,)
                ).fetchone()["id"]
                self.assertFalse(
                    society_moderation.decide(
                        self.conn, V2, finding, "confirm", self.now
                    )["exiled"]
                )
            self.conn.commit()
        row = self.conn.execute(
            "SELECT status,participation,balance FROM society_agents WHERE id=%s",
            (registered["id"],),
        ).fetchone()
        self.assertEqual(
            dict(row), dict(status="alive", participation="active", balance=0)
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) AS n FROM society_findings").fetchone()[
                "n"
            ],
            4,
        )
        self.assertEqual(self.client.get("/api/society/square").json()["posts"], [])

    def test_explicit_controls_preserve_identity_memory_and_stop_participation(self):
        external = self.register().json()
        self.tick()
        society_life.record_reflection(
            self.conn,
            "alder",
            0,
            1,
            dict(
                keep=False, strategy="My own persistent plan", note="My private memory"
            ),
            self.now,
        )
        self.conn.commit()
        self.assertTrue(
            external_agents.control_agent(self.conn, "alder", "suspend", self.now)
        )
        self.assertTrue(
            external_agents.control_agent(self.conn, external["id"], "revoke", self.now)
        )
        self.conn.commit()
        self.assertNotIn(
            "alder", society.due_turns(self.conn, self.now.replace(minute=59))
        )
        self.assertFalse(society.claim_turn(self.conn, "alder", 1, self.now))
        self.assertEqual(
            self.client.get(
                "/api/agents/tick", headers=self.headers(external)
            ).status_code,
            401,
        )
        self.assertEqual(society_life.notes(self.conn, "alder"), ["My private memory"])
        self.assertEqual(
            society_life.strategy(self.conn, "alder")["text"], "My own persistent plan"
        )
        self.assertEqual(
            self.client.get("/api/society/agents/alder").json()["status"], "suspended"
        )
        for agent in ("alder", external["id"]):
            self.assertEqual(
                self.conn.execute(
                    "SELECT status FROM society_agents WHERE id=%s", (agent,)
                ).fetchone()["status"],
                "alive",
            )
        self.assertFalse(
            external_agents.control_agent(
                self.conn, external["id"], "suspend", self.now
            )
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT participation FROM society_agents WHERE id=%s",
                (external["id"],),
            ).fetchone()["participation"],
            "revoked",
        )

    def test_private_context_and_daily_reflection_keep_memory_without_survival_signals(
        self,
    ):
        self.tick()
        society_life.record_reflection(
            self.conn,
            "alder",
            0,
            1,
            dict(
                keep=False, strategy="ALDER_PRIVATE_STRATEGY", note="ALDER_PRIVATE_NOTE"
            ),
            self.now,
        )
        for _ in range(24):
            self.tick()
        context = society_life.turn_context(self.conn, V2, "alder")
        self.assertIsNone(context["you"]["energy"])
        self.assertIsNone(context["you"]["age"]["max_days"])
        self.assertNotIn("living_cost_today", context["you"])
        self.assertIn("ALDER_PRIVATE_NOTE", context["you"]["private_notes"])
        self.assertNotIn(
            "ALDER_PRIVATE",
            json.dumps(society_life.turn_context(self.conn, V2, "birch")),
        )
        reflection = society_life.reflection_context(self.conn, V2, "alder", 0)
        self.assertNotIn("energy_at_end", reflection["the_day"])
        self.assertTrue(society_life.reflections_due(self.conn))

    @unittest.skipUnless(
        os.name == "posix", "existing worker ledger requires POSIX locking"
    )
    def test_worker_restarts_reflections_and_budget_stop_under_v2(self):
        from types import SimpleNamespace
        from arena import worker
        from tee_arena.gateway import GatewayError

        self.tick()
        gateway = SimpleNamespace(providers={})
        run = worker.Worker(
            self.conn, V2, gateway, now=lambda: self.now.replace(minute=59)
        )
        with patch.object(
            run,
            "call",
            return_value=SimpleNamespace(text='{"action":"rest"}', charged_microusd=0),
        ) as call:
            self.assertEqual(run.run()["turns"], 4)
            self.assertEqual(call.call_count, 4)
        restarted = worker.Worker(
            self.conn, V2, gateway, now=lambda: self.now.replace(minute=59)
        )
        with patch.object(
            restarted, "call", side_effect=AssertionError("duplicate inference")
        ):
            self.assertEqual(restarted.run()["turns"], 0)
            self.assertIsNone(restarted.last_words())
        for _ in range(23):
            self.tick()
        with patch.object(
            restarted,
            "call",
            return_value=SimpleNamespace(
                text='{"keep":true,"note":"A private lesson"}', charged_microusd=0
            ),
        ):
            self.assertEqual(restarted.reflect("alder", 0, 24), "kept")
        self.assertIn("A private lesson", society_life.notes(self.conn, "alder"))
        with patch.object(restarted, "call", side_effect=GatewayError("cap_exceeded")):
            self.assertEqual(restarted.turn("birch", 24), "stop")
        self.assertTrue(society.clock(self.conn)["paused"])

    def test_populated_migration_preserves_all_historical_records(self):
        registered = self.register().json()
        self.tick()
        post = self.submit(
            registered, action="post", text="A synthetic historical contribution."
        ).json()["post_id"]
        society_moderation.apply_verdict(self.conn, V2, post, CLEAN, self.now)
        society.claim_turn(self.conn, "birch", 1, self.now)
        society_life.act(
            self.conn,
            V2,
            "birch",
            1,
            {
                "action": "reply",
                "post_id": post,
                "text": "A historical reply.",
                "note": "PRIVATE_NOTE",
            },
            self.now,
        )
        society_life.record_reflection(
            self.conn,
            "alder",
            0,
            1,
            {"keep": False, "strategy": "PRIVATE_STRATEGY", "note": "PRIVATE_MEMORY"},
            self.now,
        )
        society.claim_turn(self.conn, "cedar", 1, self.now)
        society_life.act(
            self.conn, V2, "cedar", 1, {"action": "like", "post_id": post}, self.now
        )
        society.die(self.conn, V1, "hazel", 1, "age", self.now)
        self.conn.execute(
            "UPDATE agent_tokens SET suspended=true WHERE agent_id=%s",
            (registered["id"],),
        )
        self.conn.execute("ALTER TABLE society_agents DROP COLUMN participation")
        self.conn.execute(
            "DELETE FROM arena_migrations WHERE name='0019_agent_participation.sql'"
        )
        tables = [
            row["tablename"]
            for row in self.conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename<>'arena_migrations'"
            )
        ]

        def snapshot():
            return {
                table: list(
                    self.conn.execute(
                        sql.SQL(
                            "SELECT to_jsonb(t)-'participation' AS row FROM {} t ORDER BY (to_jsonb(t)-'participation')::text"
                        ).format(sql.Identifier(table))
                    )
                )
                for table in tables
            }

        before = snapshot()
        db.migrate(self.conn)
        self.assertEqual(before, snapshot())
        self.assertEqual(
            self.conn.execute(
                "SELECT participation FROM society_agents WHERE id=%s",
                (registered["id"],),
            ).fetchone()["participation"],
            "suspended",
        )
        self.conn.commit()
        self.assertEqual(
            self.client.get(
                "/api/agents/tick", headers=self.headers(registered)
            ).status_code,
            403,
        )
