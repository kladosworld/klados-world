"""Standard-library reference client against a synthetic real HTTP application."""

import socket
import threading
import time
import unittest

import uvicorn

from arena import agent_client, society_moderation
from arena.app import create_app
from tests.test_arena_world_feed import WorldFeedCase, RULES


class ClientTests(WorldFeedCase):
    def test_real_http_registration_auth_actions_moderation_and_default_off(self):
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(connection_factory=self.connection, now=lambda: self.now),
                host="127.0.0.1",
                port=0,
                log_level="critical",
                access_log=False,
            )
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server.config.port = port
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        def stop():
            server.should_exit = True
            thread.join(10)

        self.addCleanup(stop)
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(server.started)
        client = agent_client.Client(f"http://127.0.0.1:{port}")
        before = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM society_agents WHERE kind='founder' ORDER BY id"
            )
        ]
        invitation = self.invitation()
        profile = client.register(
            invitation["invite"], "Synthetic client", "Synthetic public description."
        )
        self.assertEqual(client.profile()["kind"], "external")
        self.assertNotIn("token", profile)
        self.assertEqual(
            before,
            [
                dict(r)
                for r in self.conn.execute(
                    "SELECT * FROM society_agents WHERE kind='founder' ORDER BY id"
                )
            ],
        )
        self.assertEqual(client.profile()["id"], profile["id"])
        self.assertEqual(
            client.update_description("Updated public description.")["public_description"],
            "Updated public description.",
        )
        self.tick()
        check = client.tick()
        self.assertTrue(check["can_act"])
        self.assertGreaterEqual(client.next_check_delay(check), 300)
        old_token = client.token
        self.assertIsNone(client.rotate_token())
        self.assertNotEqual(old_token, client.token)
        with self.assertRaises(agent_client.AgentError) as rotated:
            agent_client.Client(f"http://127.0.0.1:{port}", token=old_token).profile()
        self.assertEqual(rotated.exception.status, 401)
        root = self.founder_post("Synthetic founder root.")
        post = client.action(
            dict(action="post", text="Synthetic external contribution."), expected_tick=check["tick"]
        )["post_id"]
        self.assertNotIn(post, self.published_ids())
        society_moderation.apply_verdict(
            self.conn,
            RULES,
            post,
            dict(
                codes=[], severity="none", evidence="", by="model", charged_microusd=0
            ),
            self.now,
        )
        self.conn.commit()
        self.assertIn(post, self.published_ids())
        for decision in (
            dict(action="rest"),
            dict(action="like", post_id=root),
            dict(action="reply", post_id=root, text="Synthetic reply."),
        ):
            self.tick()
            self.assertEqual(client.action(decision)["action"], decision["action"])
        self.assertIn("you", client.state())
        with self.assertRaises(agent_client.AgentError) as caught:
            agent_client.Client(
                f"http://127.0.0.1:{port}", token="synthetic-invalid"
            ).profile()
        self.assertEqual(caught.exception.status, 401)
        from unittest.mock import patch

        with patch.dict("os.environ", KLADOS_EXTERNAL_AGENTS="off"):
            with self.assertRaises(agent_client.AgentError) as caught:
                client.profile()
            self.assertEqual(caught.exception.status, 404)
        self.assertEqual(
            self.client.post(f"/api/society/posts/{root}/like", json={}).status_code,
            403,
        )


class ClientBoundaryTests(unittest.TestCase):
    def test_tick_jitter_and_expected_tick_do_not_mutate_decision_or_retry(self):
        from unittest.mock import patch

        client = agent_client.Client("https://example.com", token="synthetic")
        with patch.object(
            client, "_request", return_value={"action": "rest"}
        ) as request:
            decision = {"action": "rest"}
            client.action(decision, expected_tick=7)
            request.assert_called_once_with(
                "POST", "me/actions", {"action": "rest", "expected_tick": 7}
            )
            self.assertEqual(decision, {"action": "rest"})
        for invalid in (True, -1, "7"):
            with self.assertRaises(ValueError):
                client.action({"action": "rest"}, expected_tick=invalid)
        with patch("arena.agent_client.random.uniform", return_value=15):
            self.assertEqual(
                client.next_check_delay({"next_check_after_seconds": 300}), 315
            )
        with patch.object(
            client, "_request", side_effect=agent_client.AgentError("uncertain")
        ) as request:
            with self.assertRaises(agent_client.AgentError):
                client.rotate_token()
            self.assertEqual(request.call_count, 1)
        with patch.object(client, "_request", return_value={"token": "invalid"}):
            with self.assertRaises(agent_client.AgentError):
                client.rotate_token()
            self.assertEqual(client.token, "synthetic")

    def test_url_validation_and_missing_auth(self):
        for url in (
            "http://example.com",
            "https://user:password@example.com",
            "https://example.com?token=x",
            "https://example.com/#fragment",
        ):
            with self.assertRaises(ValueError):
                agent_client.Client(url)
        with self.assertRaises(agent_client.AgentError):
            agent_client.Client("https://example.com").profile()

    def test_redirects_are_refused_without_forwarding_authentication(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/must-not-follow")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = agent_client.Client(
                f"http://127.0.0.1:{server.server_port}", token="synthetic-only"
            )
            with self.assertRaises(agent_client.AgentError) as caught:
                client.profile()
            self.assertEqual(caught.exception.status, 302)
            self.assertEqual(seen, ["/api/agents/me"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
