"""Atomic social allowances with disposable data and no model calls."""

import dataclasses
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from arena import db, society, society_life
from tee_arena.society import rules
from tests.test_arena_external_agents import InviteCase, NOW

RULES = rules.load()
COMMENTS = [
    "A considered response about trust.",
    "I prefer a different balance.",
    "Perhaps we can explore this together.",
    "What would success look like?",
]


class SocialConfigurationTests(unittest.TestCase):
    def test_defaults_and_independent_configuration_are_validated(self):
        self.assertEqual(RULES.activity_limits, {"post": 1, "reply": 3, "like": 5})
        document = json.loads(rules.DEFAULT_PATH.read_text())
        document["activity_limits_per_hour"] = {"post": 2, "reply": 7, "like": 9}
        self.assertEqual(
            rules.parse(document).activity_limits, {"post": 2, "reply": 7, "like": 9}
        )
        for value in (0, -1, True, 0.5, "1", 10001):
            document["activity_limits_per_hour"]["post"] = value
            with self.assertRaises(rules.RulesError):
                rules.parse(document)

    def test_reference_client_keeps_only_sanitized_retry_metadata(self):
        import io
        from urllib.error import HTTPError
        from arena.agent_client import Client, AgentError

        client = Client("https://example.com", token="synthetic")
        for header, expected in [("12", 12), ("PRIVATE_CANARY", None), ("-1", None)]:
            error = HTTPError(
                "https://example.com",
                429,
                "PRIVATE_CANARY",
                {"Retry-After": header},
                io.BytesIO(b"PRIVATE_BODY"),
            )
            with patch.object(client._opener, "open", side_effect=error) as request:
                with self.assertRaises(AgentError) as caught:
                    client.action({"action": "rest"})
            self.assertEqual(caught.exception.status, 429)
            self.assertEqual(caught.exception.retry_after, expected)
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertEqual(request.call_count, 1)


class SocialActivityTests(InviteCase):
    def setUp(self):
        super().setUp()
        environment = patch.dict(os.environ, KLADOS_RULES=str(rules.DEFAULT_PATH))
        environment.start()
        self.addCleanup(environment.stop)

    def root(self, author="birch"):
        post = self.conn.execute(
            """INSERT INTO society_posts
            (author_id,tick,text,status,created_at) VALUES (%s,0,'Synthetic root','published',%s)
            RETURNING id""",
            (author, NOW - timedelta(hours=2)),
        ).fetchone()["id"]
        self.conn.commit()
        return post

    def perform(self, agent, tick, decision, *, now=NOW, selected=RULES, commit=True):
        # Distinct claimed turns isolate the allowance layer from the scheduler.
        # The HTTP test below separately enforces the real current-tick constraint.
        self.assertTrue(society.claim_turn(self.conn, agent, tick, now))
        outcome = society_life.act(self.conn, selected, agent, tick, decision, now)
        if commit:
            self.conn.commit()
        return outcome

    def test_rolling_boundary_and_fractional_retry(self):
        self.assertEqual(
            self.perform(
                "alder", 1, {"action": "post", "text": "A first contribution."}
            )["action"],
            "post",
        )
        refused = self.perform(
            "alder",
            2,
            {"action": "post", "text": "A distinct proposal."},
            now=NOW + timedelta(seconds=3599.1),
        )
        self.assertEqual(refused["error"], "activity_limit")
        self.assertEqual(refused["retry_after_seconds"], 1)
        accepted = self.perform(
            "alder",
            3,
            {"action": "post", "text": "A distinct proposal."},
            now=NOW + timedelta(seconds=3600),
        )
        self.assertEqual(accepted["action"], "post")
        self.assertEqual(society.audit(self.conn), [])

    def test_utc_hour_boundary_does_not_reset_a_rolling_allowance(self):
        first = NOW + timedelta(minutes=30)
        self.perform("alder", 1, {"action": "post", "text": COMMENTS[0]}, now=first)
        refused = self.perform(
            "alder",
            2,
            {"action": "post", "text": COMMENTS[1]},
            now=NOW + timedelta(hours=1),
        )
        self.assertEqual(refused["retry_after_seconds"], 1800)
        accepted = self.perform(
            "alder",
            3,
            {"action": "post", "text": COMMENTS[1]},
            now=NOW + timedelta(minutes=90),
        )
        self.assertEqual(accepted["action"], "post")

    def test_index_migration_preserves_committed_action_history(self):
        self.perform("alder", 1, {"action": "post", "text": COMMENTS[0]})
        before = list(self.conn.execute("SELECT * FROM society_posts ORDER BY id"))
        for name in ("society_posts_agent_activity", "society_likes_agent_activity"):
            self.conn.execute("DROP INDEX " + name)  # Fixed test-owned identifiers.
        self.conn.execute(
            "DELETE FROM arena_migrations WHERE name='0020_social_activity_indexes.sql'"
        )
        db.migrate(self.conn)
        self.assertEqual(
            before, list(self.conn.execute("SELECT * FROM society_posts ORDER BY id"))
        )
        self.conn.commit()
        result = self.perform("alder", 2, {"action": "post", "text": COMMENTS[1]})
        self.assertEqual(result["error"], "activity_limit")

    def test_comments_have_no_spacing_and_limits_are_independent_and_per_agent(self):
        root = self.root()
        for tick, text in enumerate(COMMENTS[:3], 1):
            self.assertEqual(
                self.perform(
                    "alder", tick, {"action": "reply", "post_id": root, "text": text}
                )["action"],
                "reply",
            )
        refused = self.perform(
            "alder", 4, {"action": "reply", "post_id": root, "text": COMMENTS[3]}
        )
        self.assertEqual(
            (refused["wanted"], refused["limit"], refused["retry_after_seconds"]),
            ("reply", 3, 3600),
        )
        self.assertEqual(
            self.perform("alder", 5, {"action": "post", "text": "A separate topic."})[
                "action"
            ],
            "post",
        )
        self.assertEqual(
            self.perform(
                "cedar", 1, {"action": "reply", "post_id": root, "text": COMMENTS[3]}
            )["action"],
            "reply",
        )
        self.assertEqual(self.perform("alder", 6, {"action": "rest"})["action"], "rest")

    def test_five_likes_have_no_spacing_and_rejected_duplicates_do_not_count(self):
        targets = ["birch", "cedar", "hazel"] + [
            self.register(name=f"Target {n}").json()["id"] for n in range(3)
        ]
        posts = [self.root(target) for target in targets]
        for tick, post in enumerate(posts[:5], 1):
            result = self.perform("alder", tick, {"action": "like", "post_id": post})
            self.assertEqual(result["action"], "like")
            self.assertFalse(result["paid"])
        result = self.perform("alder", 6, {"action": "like", "post_id": posts[5]})
        self.assertEqual((result["error"], result["limit"]), ("activity_limit", 5))
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_likes WHERE from_id='alder'"
            ).fetchone()["n"],
            5,
        )

    def test_rejected_and_rolled_back_actions_leave_allowances_available(self):
        root = self.root()
        for tick in range(1, 5):
            outcome = self.perform(
                "alder", tick, {"action": "reply", "post_id": -1, "text": COMMENTS[0]}
            )
            self.assertEqual(outcome["error"], "no_such_post")
        with self.assertRaises(RuntimeError):
            with self.conn.transaction():
                self.perform(
                    "alder",
                    5,
                    {"action": "post", "text": "A rolled back proposal."},
                    commit=False,
                )
                raise RuntimeError("synthetic rollback")
        self.assertEqual(
            self.perform(
                "alder", 6, {"action": "post", "text": "A committed proposal."}
            )["action"],
            "post",
        )
        for tick, text in enumerate(COMMENTS[:3], 7):
            self.assertEqual(
                self.perform(
                    "alder", tick, {"action": "reply", "post_id": root, "text": text}
                )["action"],
                "reply",
            )
        self.assertEqual(
            self.perform("cedar", 1, {"action": "like", "post_id": root})["action"],
            "like",
        )
        self.assertEqual(
            self.perform("cedar", 2, {"action": "like", "post_id": root})["error"],
            "already_liked_today",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_likes WHERE from_id='cedar'"
            ).fetchone()["n"],
            1,
        )

    def test_configured_limit_reduction_uses_the_blocking_record(self):
        selected = dataclasses.replace(
            RULES, activity_limits={"post": 3, "reply": 3, "like": 5}
        )
        for tick, text in enumerate(COMMENTS[:3], 1):
            self.perform(
                "alder",
                tick,
                {"action": "post", "text": text},
                now=NOW + timedelta(seconds=tick * 10),
                selected=selected,
            )
        reduced = dataclasses.replace(
            RULES, activity_limits={"post": 2, "reply": 3, "like": 5}
        )
        outcome = self.perform(
            "alder",
            4,
            {"action": "post", "text": COMMENTS[3]},
            now=NOW + timedelta(seconds=40),
            selected=reduced,
        )
        self.assertEqual(outcome["retry_after_seconds"], 3580)

    def test_concurrent_different_turns_cannot_overspend_allowances(self):
        root = self.root()
        barrier = threading.Barrier(4)

        def submit(index):
            with self.connection() as conn:
                self.assertTrue(society.claim_turn(conn, "alder", index + 1, NOW))
                barrier.wait(timeout=10)
                return society_life.act(
                    conn,
                    RULES,
                    "alder",
                    index + 1,
                    {"action": "reply", "post_id": root, "text": COMMENTS[index]},
                    NOW,
                )

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(submit, range(4)))
        self.assertEqual(sum(row["action"] == "reply" for row in outcomes), 3)
        self.assertEqual(
            sum(row.get("error") == "activity_limit" for row in outcomes), 1
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_posts WHERE author_id='alder'"
            ).fetchone()["n"],
            3,
        )

    def test_http_and_founders_share_limits_and_keep_tick_claims(self):
        registered = self.register().json()
        society.set_paused(self.conn, RULES, False, self.now, "synthetic")
        self.now += timedelta(hours=1)
        society.tick(self.conn, RULES, self.now)
        self.conn.commit()
        headers = self.headers(registered)
        path = "/api/agents/me/actions"
        self.assertEqual(
            self.client.post(
                path,
                headers=headers,
                json={"action": "post", "text": COMMENTS[0], "expected_tick": 1},
            ).status_code,
            200,
        )
        self.perform("alder", 1, {"action": "post", "text": COMMENTS[0]}, now=self.now)
        self.now += timedelta(minutes=55)
        society.tick(self.conn, RULES, self.now)
        self.conn.commit()
        refused = self.client.post(
            path,
            headers=headers,
            json={"action": "post", "text": COMMENTS[1], "expected_tick": 2},
        )
        self.assertEqual(refused.status_code, 429)
        self.assertEqual(refused.headers["Retry-After"], "300")
        self.assertEqual(refused.headers["Cache-Control"], "no-store")
        founder = self.perform(
            "alder", 2, {"action": "post", "text": COMMENTS[1]}, now=self.now
        )
        self.assertEqual(founder, refused.json())
        self.assertEqual(
            self.client.post(
                path, headers=headers, json={"action": "rest"}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_posts WHERE author_id=%s",
                (registered["id"],),
            ).fetchone()["n"],
            1,
        )
        check = self.client.get("/api/agents/tick", headers=headers).json()
        self.assertFalse(check["can_act"])
        self.assertEqual(
            check["limits"]["activity_limits_per_hour"], RULES.activity_limits
        )
        state = society_life.turn_context(self.conn, RULES, "alder")
        self.assertEqual(
            state["rules"]["activity_limits_per_hour"], RULES.activity_limits
        )
        self.assertIsNone(state["you"]["energy"])
        self.assertEqual(society.audit(self.conn), [])

    def test_snapshot_isolation_http_refusal_preserves_auth_accounting(self):
        from fastapi.testclient import TestClient
        from arena.app import create_app

        registered = self.register().json()
        society.set_paused(self.conn, RULES, False, self.now, "synthetic")
        self.now += timedelta(hours=1)
        society.tick(self.conn, RULES, self.now)
        self.conn.commit()

        def snapshot_connection():
            conn = self.connection()
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            return conn

        with TestClient(
            create_app(connection_factory=snapshot_connection, now=lambda: self.now)
        ) as client:
            response = client.post(
                "/api/agents/me/actions",
                headers=self.headers(registered),
                json={"action": "post", "text": "Never committed."},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "activity_limits_unavailable")
        self.assertEqual(
            self.conn.execute("SELECT request_count FROM agent_tokens").fetchone()[
                "request_count"
            ],
            1,
        )

    def test_snapshot_isolation_refuses_without_writing_an_action(self):
        with self.connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self.assertTrue(society.claim_turn(conn, "alder", 1, NOW))
            result = society_life.act(
                conn,
                RULES,
                "alder",
                1,
                {"action": "post", "text": "Must fail closed."},
                NOW,
            )
            self.assertEqual(result["error"], "activity_limits_unavailable")
        self.assertEqual(
            self.conn.execute("SELECT count(*) AS n FROM society_posts").fetchone()[
                "n"
            ],
            0,
        )
