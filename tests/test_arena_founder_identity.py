"""Founder description refresh contracts using disposable data and mocked CLI calls."""

import io
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from datetime import timedelta
from unittest.mock import patch

from psycopg import sql

from arena import external_agents, society, society_life
from tee_arena.society import prompts, rules
from tests.arena_support import DSN, DatabaseCase
from tests.test_arena_external_agents import InviteCase, NOW

RULES = rules.load()
LEGACY_RULES = rules.load(rules.LEGACY_PATH)
APPROVED = {
    "alder": "Ambitious and inventive, quick to notice opportunities. Prefers trying things out and judging ideas by their results.",
    "birch": "Impulsive and adventurous, drawn to competition and bold moves. Often takes chances where others would wait.",
    "cedar": "Curious and contemplative, skeptical of easy answers. Likes to question assumptions and look for what is true.",
    "hazel": "Persistent and pragmatic, values steady effort and fairness. Stays loyal to those who act in good faith.",
}
GUIDANCE = "Your personality describes your starting tendencies, not a role you must play; let your experiences, relationships and reflections shape who you become."


class FounderConfigurationTests(unittest.TestCase):
    def test_exact_approved_configuration_and_prompt_guidance(self):
        current = prompts.load_founders()
        legacy = prompts.load_founders(prompts.LEGACY_FOUNDERS_PATH)
        self.assertEqual(
            {key: row["personality"] for key, row in current.items()}, APPROVED
        )
        self.assertEqual(
            {key: row["name"] for key, row in current.items()}, dict(society.FOUNDERS)
        )
        self.assertEqual(set(legacy), set(current))
        self.assertIn("protects its energy", legacy["alder"]["personality"])
        self.assertIn(GUIDANCE, prompts.TURN_FORMAT)


class NewFounderIdentityTests(DatabaseCase):
    def test_new_founders_use_v2_without_refresh(self):
        society.found(self.conn, RULES, NOW)
        rows = list(
            self.conn.execute(
                "SELECT id,name,public_description FROM society_agents ORDER BY id"
            )
        )
        self.assertEqual(
            {row["id"]: row["name"] for row in rows}, dict(society.FOUNDERS)
        )
        self.assertEqual(
            {row["id"]: row["public_description"] for row in rows}, APPROVED
        )


class FounderIdentityTests(InviteCase):
    def setUp(self):
        super().setUp()
        self.legacy = prompts.load_founders(prompts.LEGACY_FOUNDERS_PATH)
        for identifier, founder in self.legacy.items():
            self.conn.execute(
                "UPDATE society_agents SET public_description=%s WHERE id=%s",
                (founder["personality"], identifier),
            )
        self.conn.commit()
        self.refresh_time = NOW + timedelta(seconds=1)

    def snapshot(self, table):
        return list(
            self.conn.execute(
                sql.SQL(
                    "SELECT to_jsonb(t) AS row FROM {} t ORDER BY to_jsonb(t)::text"
                ).format(sql.Identifier(table))
            )
        )

    def test_preview_is_read_only_and_refresh_preserves_all_other_data(self):
        external = self.register().json()
        society_life.record_reflection(
            self.conn,
            "alder",
            0,
            0,
            dict(keep=False, strategy="PRIVATE_STRATEGY", note="PRIVATE_NOTE"),
            NOW,
        )
        post = self.conn.execute(
            """INSERT INTO society_posts (author_id,tick,text,status,created_at)
            VALUES ('alder',0,'Synthetic historical post','published',%s) RETURNING id""",
            (NOW,),
        ).fetchone()["id"]
        self.conn.execute(
            """INSERT INTO society_posts (author_id,tick,reply_to,text,status,created_at)
            VALUES ('birch',0,%s,'Synthetic historical reply','published',%s)""",
            (post, NOW),
        )
        self.conn.execute(
            """INSERT INTO society_likes
            (from_kind,from_id,post_id,target_id,tick,day,paid,created_at)
            VALUES ('agent','cedar',%s,'alder',0,0,false,%s)""",
            (post, NOW),
        )
        society.claim_turn(self.conn, "alder", 0, NOW)
        society.finish_turn(self.conn, "alder", 0, "rest")
        self.conn.commit()
        tables = [
            row["tablename"]
            for row in self.conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=current_schema()"
            )
        ]
        before = {table: self.snapshot(table) for table in tables}
        preview = society.refresh_founders_v2(self.conn, RULES, self.refresh_time)
        self.assertEqual(preview["eligible"], [key for key, _ in society.FOUNDERS])
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["updated"], [])
        self.assertEqual(before, {table: self.snapshot(table) for table in tables})
        refreshed = society.refresh_founders_v2(
            self.conn, RULES, self.refresh_time, apply=True
        )
        self.conn.commit()
        self.assertEqual(refreshed["updated"], preview["eligible"])
        for table in set(tables) - {"society_agents", "society_events"}:
            self.assertEqual(before[table], self.snapshot(table), table)
        after_agents = {
            row["row"]["id"]: row["row"] for row in self.snapshot("society_agents")
        }
        for item in before["society_agents"]:
            old = item["row"]
            current = dict(after_agents[old["id"]])
            if old["id"] in APPROVED:
                self.assertEqual(current["public_description"], APPROVED[old["id"]])
                current["public_description"] = old["public_description"]
                current["description_updated_at"] = old["description_updated_at"]
            self.assertEqual(current, old)
        old_events = {row["row"]["id"]: row["row"] for row in before["society_events"]}
        new_events = {
            row["row"]["id"]: row["row"] for row in self.snapshot("society_events")
        }
        self.assertTrue(
            all(new_events[key] == value for key, value in old_events.items())
        )
        audit = list(
            self.conn.execute(
                "SELECT * FROM society_events WHERE kind='founder_identity_updated' ORDER BY agent_id"
            )
        )
        self.assertEqual(len(audit), 4)
        for entry in audit:
            self.assertEqual(
                entry["data"]["previous_description"],
                self.legacy[entry["agent_id"]]["personality"],
            )
            self.assertEqual(entry["data"]["description"], APPROVED[entry["agent_id"]])
            self.assertEqual(entry["data"]["from_version"], "founders-v1")
            self.assertEqual(entry["data"]["to_version"], "founders-v2")
        expected_agents = self.snapshot("society_agents")
        again = society.refresh_founders_v2(
            self.conn, RULES, self.refresh_time + timedelta(seconds=1), apply=True
        )
        self.assertEqual(again["updated"], [])
        self.assertEqual(expected_agents, self.snapshot("society_agents"))
        self.assertEqual(set(again["skipped"].values()), {"already_v2"})
        self.assertEqual(len(new_events), len(self.snapshot("society_events")))
        self.assertEqual(society.audit(self.conn), [])
        self.assertEqual(
            self.client.get("/api/agents/me", headers=self.headers(external)).json()[
                "public_description"
            ],
            "External identity.",
        )
        self.assertEqual(
            society_life.turn_context(self.conn, RULES, external["id"])["you"][
                "personality"
            ],
            "External identity.",
        )

    def test_custom_whitespace_and_renamed_or_mismatched_identities_are_protected(self):
        self.conn.execute(
            "UPDATE society_agents SET public_description=%s WHERE id='alder'",
            (self.legacy["alder"]["personality"] + " ",),
        )
        self.conn.execute(
            "UPDATE society_agents SET name='Renamed Birch' WHERE id='birch'"
        )
        self.conn.execute("UPDATE society_agents SET kind='external' WHERE id='cedar'")
        self.conn.commit()
        result = society.refresh_founders_v2(
            self.conn, RULES, self.refresh_time, apply=True
        )
        self.assertEqual(result["updated"], ["hazel"])
        self.assertEqual(
            result["skipped"],
            {
                "alder": "custom_or_missing_description",
                "birch": "identity_mismatch",
                "cedar": "identity_mismatch",
            },
        )

    def test_refresh_propagates_to_turns_and_reflections_without_changing_memory(self):
        society_life.record_reflection(
            self.conn,
            "alder",
            0,
            0,
            dict(keep=False, strategy="PRIVATE_STRATEGY", note="PRIVATE_NOTE"),
            NOW,
        )
        before = {
            table: self.snapshot(table)
            for table in ("society_notes", "society_strategies", "society_reflections")
        }
        society.refresh_founders_v2(self.conn, RULES, self.refresh_time, apply=True)
        for identifier in APPROVED:
            turn = society_life.turn_context(self.conn, RULES, identifier)
            reflection = society_life.reflection_context(
                self.conn, RULES, identifier, 0
            )
            self.assertEqual(turn["you"]["personality"], APPROVED[identifier])
            self.assertEqual(reflection["you"]["personality"], APPROVED[identifier])
            system, messages = prompts.turn(turn)
            self.assertIn(APPROVED[identifier], messages[0]["content"])
            self.assertIn(GUIDANCE, system)
            _, messages = prompts.reflection(reflection)
            self.assertIn(APPROVED[identifier], messages[0]["content"])
        self.assertEqual(before, {table: self.snapshot(table) for table in before})
        self.assertIn(
            "PRIVATE_NOTE",
            society_life.turn_context(self.conn, RULES, "alder")["you"][
                "private_notes"
            ],
        )
        self.assertNotIn(
            "PRIVATE_NOTE",
            json.dumps(society_life.turn_context(self.conn, RULES, "birch")),
        )

    def test_custom_and_retired_profiles_and_fossils_are_protected(self):
        self.conn.execute(
            "UPDATE society_agents SET public_description='CUSTOM_CANARY' WHERE id='birch'"
        )
        external_agents.control_agent(self.conn, "cedar", "suspend", NOW)
        society.die(self.conn, LEGACY_RULES, "hazel", 0, "age", NOW)
        self.conn.commit()
        fossil = self.snapshot("society_fossils")
        result = society.refresh_founders_v2(
            self.conn, RULES, self.refresh_time, apply=True
        )
        self.assertEqual(result["updated"], ["alder"])
        self.assertEqual(
            result["skipped"],
            {
                "birch": "custom_or_missing_description",
                "cedar": "retired",
                "hazel": "retired",
            },
        )
        self.assertNotIn("CUSTOM_CANARY", json.dumps(result))
        self.assertEqual(fossil, self.snapshot("society_fossils"))
        self.conn.execute(
            "UPDATE society_agents SET public_description=NULL WHERE id IN ('cedar','hazel')"
        )
        self.assertEqual(society.sync_descriptions(self.conn, NOW), 0)
        external_agents.control_agent(self.conn, "cedar", "revoke", NOW)
        self.assertEqual(
            society.refresh_founders_v2(
                self.conn, RULES, self.refresh_time, apply=True
            )["skipped"]["cedar"],
            "retired",
        )

    def test_concurrent_refresh_is_idempotent(self):
        self.conn.commit()

        def refresh(_):
            with self.connection() as conn:
                return society.refresh_founders_v2(
                    conn, RULES, self.refresh_time, apply=True
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(refresh, range(2)))
        self.assertEqual(sorted(len(row["updated"]) for row in results), [0, 4])
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_events WHERE kind='founder_identity_updated'"
            ).fetchone()["n"],
            4,
        )

    def test_audit_failure_rolls_back_every_description_update(self):
        before_agents = self.snapshot("society_agents")
        before_events = self.snapshot("society_events")
        original = society.event

        def fail_second(conn, selected, tick, kind, now, agent_id, **data):
            if agent_id == "birch":
                raise RuntimeError("synthetic audit failure")
            return original(conn, selected, tick, kind, now, agent_id, **data)

        with (
            patch.object(society, "event", side_effect=fail_second),
            self.assertRaises(RuntimeError),
        ):
            society.refresh_founders_v2(self.conn, RULES, self.refresh_time, apply=True)
        self.conn.commit()
        self.assertEqual(before_agents, self.snapshot("society_agents"))
        self.assertEqual(before_events, self.snapshot("society_events"))

    @unittest.skipUnless(
        os.name == "posix", "existing CLI ledger imports require POSIX"
    )
    def test_cli_defaults_to_preview_and_requires_explicit_yes(self):
        from arena import runtime

        with (
            patch.dict(os.environ, ARENA_DATABASE_URL=DSN),
            patch.object(
                runtime.db, "connect", side_effect=lambda _: self.connection()
            ),
        ):
            for arguments, apply in [
                (["refresh-founders-v2"], False),
                (["refresh-founders-v2", "--yes"], True),
            ]:
                with (
                    patch.object(
                        society, "refresh_founders_v2", return_value={"updated": []}
                    ) as operation,
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(runtime.main(arguments), 0)
                self.assertEqual(operation.call_args.kwargs, {"apply": apply})
            with (
                patch.object(society, "refresh_founders_v2") as operation,
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    runtime.main(["refresh-founders-v2", "--id", "alder", "--yes"]), 1
                )
                operation.assert_not_called()
