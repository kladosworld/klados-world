"""Synthetic invite API contracts. Database tests require disposable PostgreSQL."""

import io
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.errors import CheckViolation

from arena import db, society, society_life, society_moderation
from arena import external_agents as external
from arena.app import create_app, public_route
from tee_arena.society import rules as rules_module
from tests.arena_support import DSN, DatabaseCase

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)
RULES = rules_module.load(rules_module.LEGACY_PATH)


class ClosedSurfaceTests(unittest.TestCase):
    def test_capacity_configuration_is_bounded_and_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(external.agent_capacity(), 8)
        for value in ("1", "8", "100"):
            with patch.dict(os.environ, KLADOS_EXTERNAL_AGENT_CAPACITY=value):
                self.assertEqual(external.agent_capacity(), int(value))
        for value in ("", "0", "101", "-1", "1.0", " 8", "true", "999999"):
            with patch.dict(os.environ, KLADOS_EXTERNAL_AGENT_CAPACITY=value):
                with self.assertRaises(external.HTTPException) as caught:
                    external.agent_capacity()
                self.assertEqual(caught.exception.status_code, 503)

    def test_default_off_and_invalid_flags_are_unrouted_without_database_access(self):
        def forbidden():
            raise AssertionError("Closed surface must not access the database")

        for setting in ("off", "", "true", "INVITE"):
            with (
                self.subTest(setting=setting),
                patch.dict(os.environ, KLADOS_EXTERNAL_AGENTS=setting),
                TestClient(create_app(connection_factory=forbidden)) as client,
            ):
                for method, path in external.ROUTES:
                    self.assertFalse(public_route(method, path))
                    self.assertEqual(client.request(method, path, json={}).status_code, 404)

    def test_invite_exposes_only_exact_methods_and_never_a_management_endpoint(self):
        with patch.dict(os.environ, KLADOS_EXTERNAL_AGENTS="invite"):
            for method, path in external.ROUTES:
                self.assertTrue(public_route(method, path))
            for method, path in [
                ("POST", "/api/agents/invite"),
                ("DELETE", "/api/agents/me"),
                ("GET", "/api/agents/register"),
                ("GET", "/api/agents/me/notes"),
            ]:
                self.assertFalse(public_route(method, path))

    def test_body_limit_including_chunked_input_precedes_database_and_auth(self):
        def forbidden():
            raise AssertionError("Oversized body must not access the database")

        with (
            patch.dict(os.environ, KLADOS_EXTERNAL_AGENTS="invite"),
            TestClient(create_app(connection_factory=forbidden)) as client,
        ):
            for content in (b"x" * 8193, iter([b"x" * 4096, b"x" * 4097])):
                self.assertEqual(
                    client.post("/api/agents/register", content=content).status_code, 413
                )

    def test_identity_validation_and_no_arbitrary_profile_fields(self):
        valid = {"invite": "synthetic", "name": "Aster", "description": "A public description."}
        self.assertEqual(external.identity(valid), valid["description"])
        for changes in (
            {"name": " x "},
            {"name": "x" * 41},
            {"name": "A\nB"},
            {"description": "x" * 601},
            {"description": "x\x00"},
            {"kind": "founder"},
            {"invite": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(Exception):
                external.identity(dict(valid, **changes))
        with self.assertRaises(Exception):
            external.identity(
                {"description": "OK", "private_notes": "forbidden"}, description_only=True
            )


class InviteCase(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.now = NOW
        society.found(self.conn, RULES, self.now)
        self.conn.commit()
        self.environment = patch.dict(
            os.environ,
            KLADOS_EXTERNAL_AGENTS="invite",
            KLADOS_SOCIETY_PUBLIC="1",
            KLADOS_AI_MODE="mock",
            KLADOS_RULES=str(rules_module.LEGACY_PATH),
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(
            create_app(connection_factory=self.connection, now=lambda: self.now)
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def connection(self):
        conn = db.connect(DSN)
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        return conn

    def invitation(self):
        result = external.create_invite(self.conn, self.now)
        self.conn.commit()
        return result

    def register(self, invitation=None, name="Aster"):
        invitation = invitation or self.invitation()
        return self.client.post(
            "/api/agents/register",
            json={
                "invite": invitation["invite"],
                "name": name,
                "description": "External identity.",
            },
        )

    def headers(self, registration):
        return {"Authorization": "Bearer " + registration["token"]}

    def tick(self):
        society.set_paused(self.conn, RULES, False, self.now, "synthetic")
        self.now += timedelta(hours=1)
        society.tick(self.conn, RULES, self.now)
        self.conn.commit()


class InviteDatabaseTests(InviteCase):
    def test_hourly_retry_after_rounds_up_and_preserves_shared_limits(self):
        registered = self.register().json()
        headers = self.headers(registered)
        for _ in range(external.REQUESTS_PER_HOUR):
            self.assertEqual(
                self.client.get("/api/agents/me", headers=headers).status_code, 200
            )
        before = self.conn.execute(
            "SELECT *, xmin::text AS version FROM agent_tokens"
        ).fetchone()
        for elapsed, expected in [(0, "3600"), (1200.25, "2400"), (3599.5, "1")]:
            self.now = NOW + timedelta(seconds=elapsed)
            for method, path in [
                ("GET", "/api/agents/me"),
                ("GET", "/api/agents/tick"),
                ("POST", "/api/agents/me/credentials/rotate"),
            ]:
                response = self.client.request(method, path, headers=headers, json={})
                self.assertEqual(response.status_code, 429)
                self.assertEqual(
                    response.json(), {"detail": "Agent request limit reached"}
                )
                self.assertEqual(response.headers["Retry-After"], expected)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(
            before,
            self.conn.execute(
                "SELECT *, xmin::text AS version FROM agent_tokens"
            ).fetchone(),
        )
        self.now = NOW + timedelta(hours=1)
        self.assertEqual(
            self.client.get("/api/agents/tick", headers=headers).status_code, 200
        )
        early = self.client.get("/api/agents/tick", headers=headers)
        self.assertEqual(early.status_code, 429)
        self.assertEqual(early.headers["Retry-After"], "300")
        for _ in range(external.REQUESTS_PER_HOUR - 1):
            self.assertEqual(
                self.client.get("/api/agents/me", headers=headers).status_code, 200
            )
        self.assertEqual(
            self.client.get("/api/agents/me", headers=headers).status_code, 429
        )
        self.assertEqual(
            self.conn.execute("SELECT request_count FROM agent_tokens").fetchone()[
                "request_count"
            ],
            60,
        )

    def test_tick_advancing_while_action_waits_cannot_consume_later_turn(self):
        registered = self.register().json()
        self.tick()
        society.clock(self.conn, lock=True)
        waiting = threading.Event()
        original_clock = society.clock

        def clock(conn, *, lock=False):
            if lock:
                waiting.set()
            return original_clock(conn, lock=lock)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(society, "clock", side_effect=clock):
                future = pool.submit(
                    self.client.post,
                    "/api/agents/me/actions",
                    headers=self.headers(registered),
                    json={"action": "rest", "expected_tick": 1},
                )
                try:
                    self.assertTrue(waiting.wait(5))
                    self.now += timedelta(hours=1)
                    society.tick(self.conn, RULES, self.now)
                    self.conn.commit()
                finally:
                    self.conn.rollback()
                self.assertEqual(future.result(timeout=10).status_code, 409)
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_turns WHERE agent_id=%s",
                (registered["id"],),
            ).fetchone()["n"],
            0,
        )
        self.assertEqual(society.audit(self.conn), [])

    def test_polling_migration_preserves_existing_credentials_and_founders(self):
        registered = self.register().json()
        self.client.get("/api/agents/me", headers=self.headers(registered))
        before = list(self.conn.execute("SELECT * FROM society_agents ORDER BY id"))
        self.conn.execute("ALTER TABLE agent_tokens DROP COLUMN last_tick_check_at")
        self.conn.execute(
            "DELETE FROM arena_migrations WHERE name='0018_agent_tick_checks.sql'"
        )
        token = self.conn.execute("SELECT * FROM agent_tokens").fetchone()
        db.migrate(self.conn)
        self.assertEqual(
            before, list(self.conn.execute("SELECT * FROM society_agents ORDER BY id"))
        )
        after = self.conn.execute("SELECT * FROM agent_tokens").fetchone()
        self.assertIsNone(after.pop("last_tick_check_at"))
        self.assertEqual(token, after)
        self.conn.commit()
        self.assertEqual(
            self.client.get(
                "/api/agents/tick", headers=self.headers(registered)
            ).status_code,
            200,
        )

    def test_tick_is_lightweight_private_and_cooldown_rejects_without_writes(self):
        registered = self.register().json()
        headers = self.headers(registered)
        with patch.object(
            society_life,
            "turn_context",
            side_effect=AssertionError("private state loaded"),
        ):
            check = self.client.get("/api/agents/tick", headers=headers)
        self.assertEqual(check.status_code, 200)
        self.assertEqual(check.headers["Cache-Control"], "no-store")
        self.assertEqual(check.json()["reason"], "paused")
        self.assertFalse(check.json()["can_act"])
        self.assertIsNone(check.json()["next_tick_not_before"])
        self.assertEqual(check.json()["next_check_after_seconds"], 300)
        self.assertEqual(check.json()["limits"]["requests_per_hour"], 60)
        self.assertNotIn("you", check.json())
        before = self.conn.execute(
            "SELECT *, xmin::text AS version FROM agent_tokens"
        ).fetchone()
        denied = self.client.get("/api/agents/tick", headers=headers)
        self.assertEqual(denied.status_code, 429)
        self.assertEqual(denied.headers["Retry-After"], "300")
        self.assertEqual(
            before,
            self.conn.execute(
                "SELECT *, xmin::text AS version FROM agent_tokens"
            ).fetchone(),
        )
        self.tick()
        check = self.client.get("/api/agents/tick", headers=headers).json()
        self.assertTrue(check["can_act"])
        self.assertEqual(check["tick"], 1)
        self.assertIsNotNone(check["next_tick_not_before"])
        self.client.post(
            "/api/agents/me/actions", headers=headers, json={"action": "rest"}
        )
        self.now += timedelta(seconds=300)
        check = self.client.get("/api/agents/tick", headers=headers).json()
        self.assertFalse(check["can_act"])
        self.assertEqual(check["reason"], "turn_used")

    def test_tick_cooldown_is_per_agent_and_concurrent_checks_charge_once(self):
        first = self.register().json()
        second = self.register(name="Other poller").json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(
                pool.map(
                    lambda _: (
                        self.client.get(
                            "/api/agents/tick", headers=self.headers(first)
                        ).status_code
                    ),
                    range(2),
                )
            )
        self.assertEqual(sorted(codes), [200, 429])
        self.assertEqual(
            self.client.get(
                "/api/agents/tick", headers=self.headers(second)
            ).status_code,
            200,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT request_count FROM agent_tokens WHERE agent_id=%s",
                (first["id"],),
            ).fetchone()["request_count"],
            1,
        )

    def test_expected_tick_refuses_stale_and_invalid_values_without_consuming_turn(
        self,
    ):
        registered = self.register().json()
        headers = self.headers(registered)
        self.tick()
        balance = self.conn.execute(
            "SELECT balance FROM society_agents WHERE id=%s", (registered["id"],)
        ).fetchone()
        for value, status in [
            (0, 409),
            (2, 409),
            (True, 400),
            (-1, 400),
            ("1", 400),
            (None, 400),
        ]:
            response = self.client.post(
                "/api/agents/me/actions",
                headers=headers,
                json={
                    "action": "post",
                    "text": "Not submitted.",
                    "expected_tick": value,
                },
            )
            self.assertEqual(response.status_code, status)
        self.assertEqual(
            self.conn.execute(
                "SELECT balance FROM society_agents WHERE id=%s", (registered["id"],)
            ).fetchone(),
            balance,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_turns WHERE agent_id=%s",
                (registered["id"],),
            ).fetchone()["n"],
            0,
        )
        body = {
            "action": "post",
            "text": "A single guarded submission.",
            "expected_tick": 1,
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(
                pool.map(
                    lambda _: (
                        self.client.post(
                            "/api/agents/me/actions", headers=headers, json=body
                        ).status_code
                    ),
                    range(2),
                )
            )
        self.assertEqual(sorted(codes), [200, 409])
        self.tick()
        self.assertEqual(
            self.client.post(
                "/api/agents/me/actions", headers=headers, json=body
            ).status_code,
            409,
        )
        # Legacy clients can still submit without expected_tick.
        self.assertEqual(
            self.client.post(
                "/api/agents/me/actions", headers=headers, json={"action": "rest"}
            ).status_code,
            200,
        )
        self.assertEqual(society.audit(self.conn), [])

    def test_rotation_invalidates_old_token_and_preserves_limits_and_isolation(self):
        first = self.register().json()
        second = self.register(name="Other identity").json()
        headers = self.headers(first)
        self.assertEqual(
            self.client.get("/api/agents/tick", headers=headers).status_code, 200
        )
        rotated = self.client.post(
            "/api/agents/me/credentials/rotate", headers=headers, json={}
        )
        self.assertEqual(rotated.status_code, 200)
        self.assertEqual(rotated.headers["Cache-Control"], "no-store")
        self.assertNotEqual(rotated.json()["token"], first["token"])
        self.assertEqual(
            self.client.get("/api/agents/me", headers=headers).status_code, 401
        )
        new_headers = self.headers(rotated.json())
        self.assertEqual(
            self.client.get("/api/agents/tick", headers=new_headers).status_code, 429
        )
        self.assertEqual(
            self.client.get("/api/agents/me", headers=new_headers).json()["id"],
            first["id"],
        )
        self.assertEqual(
            self.client.get("/api/agents/me", headers=self.headers(second)).json()[
                "id"
            ],
            second["id"],
        )
        row = self.conn.execute(
            "SELECT * FROM agent_tokens WHERE agent_id=%s", (first["id"],)
        ).fetchone()
        self.assertEqual(row["request_count"], 3)
        self.assertNotIn(rotated.json()["token"], json.dumps(row, default=str))
        self.conn.execute(
            "UPDATE agent_tokens SET request_count=59 WHERE agent_id=%s", (first["id"],)
        )
        self.conn.commit()
        final = self.client.post(
            "/api/agents/me/credentials/rotate", headers=new_headers, json={}
        )
        self.assertEqual(final.status_code, 200)
        self.assertEqual(
            self.client.get(
                "/api/agents/me", headers=self.headers(final.json())
            ).status_code,
            429,
        )

    def test_concurrent_rotation_and_controls_cannot_be_bypassed(self):
        registered = self.register().json()
        headers = self.headers(registered)
        # Invalid rotation payload is charged but leaves the credential intact.
        self.assertEqual(
            self.client.post(
                "/api/agents/me/credentials/rotate",
                headers=headers,
                json={"agent_id": "alder"},
            ).status_code,
            400,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda _: self.client.post(
                        "/api/agents/me/credentials/rotate", headers=headers, json={}
                    ),
                    range(2),
                )
            )
        self.assertEqual(sorted(r.status_code for r in responses), [200, 401])
        headers = self.headers(
            next(r.json() for r in responses if r.status_code == 200)
        )
        external.control_agent(self.conn, registered["id"], "suspend", self.now)
        self.conn.commit()
        for path in ("/api/agents/tick", "/api/agents/me/credentials/rotate"):
            method = "POST" if path.endswith("rotate") else "GET"
            self.assertEqual(
                self.client.request(method, path, headers=headers, json={}).status_code,
                403,
            )
        external.control_agent(self.conn, registered["id"], "revoke", self.now)
        self.conn.commit()
        self.assertEqual(
            self.client.post(
                "/api/agents/me/credentials/rotate", headers=headers, json={}
            ).status_code,
            401,
        )

    def test_configured_capacity_is_atomic_and_invalid_setting_refuses_registration(
        self,
    ):
        with patch.dict(os.environ, KLADOS_EXTERNAL_AGENT_CAPACITY="1"):
            invitations = [self.invitation(), self.invitation()]
            with ThreadPoolExecutor(max_workers=2) as pool:
                codes = list(
                    pool.map(
                        lambda n: (
                            self.register(
                                invitations[n], name=f"Capacity {n}"
                            ).status_code
                        ),
                        range(2),
                    )
                )
            self.assertEqual(sorted(codes), [201, 409])
        with patch.dict(os.environ, KLADOS_EXTERNAL_AGENT_CAPACITY="100"):
            for index in range(9):
                self.assertEqual(
                    self.register(name=f"Expanded {index}").status_code, 201
                )
        invitation = self.invitation()
        with patch.dict(os.environ, KLADOS_EXTERNAL_AGENT_CAPACITY="101"):
            self.assertEqual(
                self.register(invitation, name="Config failure").status_code, 503
            )
        self.assertEqual(
            self.register(invitation, name="Config failure").status_code, 409
        )
        with patch.dict(os.environ, KLADOS_EXTERNAL_AGENT_CAPACITY="100"):
            self.assertEqual(
                self.register(invitation, name="Config failure").status_code, 201
            )

    def test_single_use_expiry_revocation_and_only_digests_persist(self):
        invitation = self.invitation()
        response = self.register(invitation)
        self.assertEqual(response.status_code, 201)
        registered = response.json()
        self.assertEqual(self.register(invitation, name="Other").status_code, 403)
        row = self.conn.execute("SELECT * FROM agent_tokens").fetchone()
        self.assertNotIn(registered["token"], json.dumps(row, default=str))
        self.assertEqual(row["token_digest"], external.auth.digest(registered["token"]))
        listed = external.list_invites(self.conn)
        self.assertNotIn(invitation["invite"], json.dumps(listed))
        self.assertNotIn("token_digest", json.dumps(listed))
        revoked = self.invitation()
        external.revoke_invite(self.conn, revoked["id"], self.now)
        self.conn.commit()
        self.assertEqual(self.register(revoked).status_code, 403)
        expired = self.invitation()
        self.now += timedelta(days=7)
        self.assertEqual(self.register(expired).status_code, 403)
        self.assertEqual(society.audit(self.conn), [])

    def test_invalid_invitation_is_refused_without_waiting_for_shared_locks(self):
        holder = self.connection()
        self.addCleanup(holder.close)
        holder.execute("SELECT * FROM society_clock FOR UPDATE")  # e.g. a tick in progress
        holder.execute("SELECT pg_advisory_xact_lock(%s)", (external.REGISTRATION_LOCK,))
        with patch.object(external.society, "clock", side_effect=AssertionError("clock locked")):
            response = self.client.post(
                "/api/agents/register",
                json={"invite": "x" * 43, "name": "Aster", "description": "External identity."},
            )
        self.assertEqual(response.status_code, 403)
        holder.rollback()
        self.assertEqual(self.register().status_code, 201)

    def test_invite_race_consumes_once_and_name_conflict_preserves_invitation(self):
        invitation = self.invitation()
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(
                pool.map(
                    lambda n: self.register(invitation, name="Agent " + str(n)).status_code,
                    range(2),
                )
            )
        self.assertEqual(sorted(codes), [201, 403])
        invitation = self.invitation()
        self.assertEqual(self.register(invitation, name="Alder").status_code, 409)
        self.assertEqual(self.register(invitation, name="Different").status_code, 201)

    def test_rate_limit_revocation_suspension_and_profile_boundaries(self):
        registered = self.register().json()
        headers = self.headers(registered)
        response = self.client.patch(
            "/api/agents/me", headers=headers, json={"description": "Updated."}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["public_description"], "Updated.")
        self.assertEqual(response.json()["kind"], "external")
        self.assertNotIn("token_digest", response.text)
        external.control_agent(self.conn, registered["id"], "suspend", self.now)
        self.conn.commit()
        self.assertEqual(self.client.get("/api/agents/me", headers=headers).status_code, 403)
        external.control_agent(self.conn, registered["id"], "revoke", self.now)
        self.conn.commit()
        self.assertEqual(self.client.get("/api/agents/me", headers=headers).status_code, 401)
        registered = self.register(name="Rate test").json()
        headers = self.headers(registered)
        for _ in range(60):
            self.assertEqual(self.client.get("/api/agents/me", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/agents/me", headers=headers).status_code, 429)
        self.now += timedelta(hours=1)
        self.assertEqual(self.client.get("/api/agents/me", headers=headers).status_code, 200)

    def test_paused_invalid_actions_and_pending_publication_use_existing_engine(self):
        registered = self.register().json()
        headers = self.headers(registered)
        self.assertEqual(
            self.client.post(
                "/api/agents/me/actions", headers=headers, json={"action": "rest"}
            ).status_code,
            409,
        )
        self.tick()
        response = self.client.post("/api/agents/me/actions", headers=headers, content=b"not JSON")
        self.assertEqual(response.json()["action"], "rest")
        self.assertEqual(
            self.client.post(
                "/api/agents/me/actions", headers=headers, json={"action": "post", "text": "Hello"}
            ).status_code,
            409,
        )
        self.now += timedelta(hours=1)
        society.tick(self.conn, RULES, self.now)
        self.conn.commit()
        response = self.client.post(
            "/api/agents/me/actions",
            headers=headers,
            json={
                "action": "post",
                "text": "An external proposal.",
                "note": "PRIVATE_EXTERNAL_NOTE",
            },
        )
        self.assertEqual(response.status_code, 200)
        post = response.json()["post_id"]
        self.assertEqual(
            self.conn.execute("SELECT status FROM society_posts WHERE id=%s", (post,)).fetchone()[
                "status"
            ],
            "pending",
        )
        verdict = dict(codes=[], severity="none", evidence="", by="model", charged_microusd=0)
        society_moderation.apply_verdict(self.conn, RULES, post, verdict, self.now)
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT status FROM society_posts WHERE id=%s", (post,)).fetchone()[
                "status"
            ],
            "published",
        )
        context = self.client.get("/api/agents/me/state", headers=headers)
        self.assertEqual(context.json()["you"]["private_notes"], ["PRIVATE_EXTERNAL_NOTE"])
        public = self.client.get("/api/society/agents/" + registered["id"])
        self.assertEqual(public.json()["kind"], "external")
        self.assertNotIn("PRIVATE_EXTERNAL_NOTE", public.text)
        self.assertEqual(society.audit(self.conn), [])

    def test_external_agents_age_but_are_never_due_for_worker_inference(self):
        registered = self.register().json()
        self.tick()
        self.assertNotIn(registered["id"], society.due_turns(self.conn, self.now))
        self.now += timedelta(hours=24)
        for _ in range(24):
            society.tick(self.conn, RULES, self.now)
            self.now += timedelta(hours=1)
        self.assertNotIn(registered["id"], [a for a, _ in society_life.reflections_due(self.conn)])
        external_agent = self.conn.execute(
            "SELECT * FROM society_agents WHERE id=%s", (registered["id"],)
        ).fetchone()
        self.assertLess(external_agent["balance"], RULES.founder_start)
        self.assertEqual(society.audit(self.conn), [])
        society.die(
            self.conn, RULES, registered["id"], society.clock(self.conn)["tick"], "exile", self.now
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT last_words_status FROM society_fossils WHERE agent_id=%s",
                (registered["id"],),
            ).fetchone()["last_words_status"],
            "missing",
        )

    def test_external_reply_like_and_private_state_remain_bound_to_identity(self):
        first = self.register(name="First").json()
        second = self.register(name="Second").json()
        self.tick()
        post = self.client.post(
            "/api/agents/me/actions",
            headers=self.headers(first),
            json={"action": "post", "text": "A synthetic idea.", "note": "FIRST_PRIVATE_NOTE"},
        ).json()["post_id"]
        society_moderation.apply_verdict(
            self.conn,
            RULES,
            post,
            dict(codes=[], severity="none", evidence="", by="model", charged_microusd=0),
            self.now,
        )
        self.conn.commit()
        response = self.client.post(
            "/api/agents/me/actions",
            headers=self.headers(second),
            json={"action": "reply", "post_id": post, "text": "A different idea."},
        )
        self.assertEqual(response.json()["action"], "reply")
        context = self.client.get("/api/agents/me/state", headers=self.headers(second))
        self.assertNotIn("FIRST_PRIVATE_NOTE", context.text)
        self.now += timedelta(hours=1)
        society.tick(self.conn, RULES, self.now)
        self.conn.commit()
        response = self.client.post(
            "/api/agents/me/actions",
            headers=self.headers(second),
            json={"action": "like", "post_id": post},
        )
        self.assertEqual(response.json()["action"], "like")
        self.assertEqual(society.audit(self.conn), [])

    def test_action_errors_keep_public_codes_and_never_expose_exception_messages(self):
        registered = self.register().json()
        for message, expected in [
            ("not_alive", "not_alive"),
            ("no_such_post", "no_such_post"),
            ("own_post", "own_post"),
            ("already_liked_today", "already_liked_today"),
            ("duplicate_post", "duplicate_post"),
            ("PRIVATE_CANARY /private/path SELECT secret FROM credentials", "action_refused"),
        ]:
            self.tick()
            with self.subTest(message=message), patch.object(
                society_life, "_alive_agent", side_effect=society.SocietyError(message)
            ):
                response = self.client.post(
                    "/api/agents/me/actions", headers=self.headers(registered),
                    json={"action":"post", "text":"Synthetic refusal test."},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"action":"rest", "error":expected, "wanted":"post"})
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertNotIn("PRIVATE_CANARY", response.text)
        self.assertEqual(society.audit(self.conn), [])

    def test_concurrent_actions_charge_exactly_one_turn_and_auth_errors_are_fixed(self):
        registered = self.register().json()
        self.tick()

        def submit(_):
            return self.client.post(
                "/api/agents/me/actions",
                headers=self.headers(registered),
                json={"action": "post", "text": "A single synthetic post."},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(submit, range(2))), [200, 409])
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_turns WHERE agent_id=%s", (registered["id"],)
            ).fetchone()["n"],
            1,
        )
        self.assertEqual(society.audit(self.conn), [])
        for header in ("Bearer wrong", "Bearer " + "x" * 43, "Basic synthetic"):
            response = self.client.get("/api/agents/me", headers={"Authorization": header})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"detail": "Invalid agent token"})
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_capacity_and_migration_preserve_existing_society_rows(self):
        before = [dict(r) for r in self.conn.execute("SELECT * FROM society_agents ORDER BY id")]
        db.migrate(self.conn)
        after = [dict(r) for r in self.conn.execute("SELECT * FROM society_agents ORDER BY id")]
        self.assertEqual(before, after)
        self.conn.commit()
        for index in range(8):
            self.assertEqual(self.register(name="Agent " + str(index)).status_code, 201)
        self.assertEqual(self.register(name="Ninth").status_code, 409)
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_agents WHERE kind='founder'"
            ).fetchone()["n"],
            4,
        )

    def test_upgrade_from_0015_keeps_populated_data_and_new_tables_start_empty(self):
        # Restore only this disposable schema to the exact pre-0016 constraints.
        self.conn.execute("DROP TABLE agent_tokens, agent_invitations")
        self.conn.execute("ALTER TABLE society_agents DROP CONSTRAINT society_agents_kind_check")
        self.conn.execute("ALTER TABLE society_agents DROP CONSTRAINT society_agents_living_check")
        self.conn.execute(
            "ALTER TABLE society_agents ADD CONSTRAINT society_agents_kind_check CHECK (kind IN ('founder','stone'))"
        )
        self.conn.execute(
            "ALTER TABLE society_agents ADD CONSTRAINT society_agents_check1 CHECK (kind='founder' OR (balance=0 AND turn_minute IS NULL AND status='alive'))"
        )
        self.conn.execute("DELETE FROM arena_migrations WHERE name='0016_external_agents.sql'")
        self.conn.execute(
            "INSERT INTO society_notes(agent_id,tick,text,created_at) VALUES ('alder',0,'PRIVATE_UPGRADE_CANARY',%s)",
            (self.now,),
        )
        self.conn.execute(
            "INSERT INTO society_posts(author_id,tick,text,status,created_at) VALUES ('birch',0,'Existing public thought.','published',%s)",
            (self.now,),
        )
        tables = [
            "society_agents",
            "society_clock",
            "society_notes",
            "society_posts",
            "energy_entries",
        ]

        def snapshot():
            return {
                name: [
                    r["value"]
                    for r in self.conn.execute(
                        sql.SQL(
                            "SELECT to_jsonb(t) AS value FROM {} t ORDER BY to_jsonb(t)::text"
                        ).format(sql.Identifier(name))
                    )
                ]
                for name in tables
            }

        before = snapshot()
        db.migrate(self.conn)
        db.migrate(self.conn)
        self.assertEqual(before, snapshot())
        self.assertEqual(
            self.conn.execute("SELECT count(*) AS n FROM agent_tokens").fetchone()["n"], 0
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) AS n FROM agent_invitations").fetchone()["n"], 0
        )
        with self.assertRaises(CheckViolation), self.conn.transaction():
            self.conn.execute("UPDATE society_agents SET status='dead' WHERE id='alder'")

    @unittest.skipUnless(os.name == "posix", "runtime CLI imports the existing POSIX ledger")
    def test_operator_cli_issues_lists_and_revokes_without_inference(self):
        from arena import runtime

        output = io.StringIO()
        real_connect = db.connect

        def cli_connection(_):
            conn = real_connect(DSN)
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn

        with (
            patch.object(runtime.db, "connect", side_effect=cli_connection),
            patch.dict(os.environ, ARENA_DATABASE_URL="synthetic-only"),
            patch.object(
                runtime.worker, "build_gateway", side_effect=AssertionError("No inference")
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(runtime.main(["invite", "create"]), 0)
            invitation = json.loads(output.getvalue())
            output.seek(0)
            output.truncate()
            self.assertEqual(runtime.main(["invite", "list"]), 0)
            self.assertNotIn(invitation["invite"], output.getvalue())
            output.seek(0)
            output.truncate()
            self.assertEqual(runtime.main(["invite", "revoke", "--id", invitation["id"]]), 0)
        self.assertEqual(self.register(invitation).status_code, 403)
