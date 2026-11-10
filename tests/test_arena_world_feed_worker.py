"""Provenance and observer integration with scripted gateways on native POSIX."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arena import society, society_public
from tests.test_arena_world_feed import RULES, WorldFeedCase


@unittest.skipUnless(os.name == "posix", "worker uses the existing POSIX ledger")
class WorldFeedWorkerTests(WorldFeedCase):
    def test_actual_writer_modes_and_external_observer_path_without_paid_inference(
        self,
    ):
        from arena import worker
        from tests.test_arena_society_life import ScriptedProvider

        registered = self.register().json()
        self.tick()
        with tempfile.TemporaryDirectory() as directory:
            book = worker.open_ledger(Path(directory) / "ledger.json", "1.00")
            mock = worker.build_gateway(book, environ={"KLADOS_AI_MODE": "mock"})
            mock.stop_file = None
            worker.Worker(
                self.conn,
                RULES,
                mock,
                observer_gateway=mock,
                now=lambda: self.now.replace(minute=59),
            ).run()
            self.assertEqual(
                society_public.public_view(self.conn, "square")["posts"], []
            )
            self.tick()
            gateway = worker.build_gateway(book, environ={"KLADOS_AI_MODE": "live"})
            scripted = ScriptedProvider(
                {
                    "Alder": '{"action":"post","text":"Synthetic live-writer fixture."}',
                    "Birch": '{"action":"rest"}',
                    "Cedar": '{"action":"rest"}',
                    "Hazel": '{"action":"rest"}',
                }
            )
            gateway.providers = {"openrouter": scripted}
            gateway.stop_file = None
            run = worker.Worker(
                self.conn,
                RULES,
                gateway,
                observer_gateway=gateway,
                now=lambda: self.now.replace(minute=59),
            )
            with patch.object(
                worker.gateway_module, "OpenRouterProvider", ScriptedProvider
            ):
                self.assertEqual(run.run()["turns"], 4)
            root = society_public.public_view(self.conn, "square")["posts"][0]["id"]
            agent_calls = sum(
                "<klados_state>" in r.messages[0]["content"] for r in scripted.requests
            )
            self.assertEqual(agent_calls, 4)
            reply = self.client.post(
                "/api/agents/me/actions",
                headers=self.headers(registered),
                json={
                    "action": "reply",
                    "post_id": root,
                    "text": "Synthetic observer integration reply.",
                },
            ).json()["post_id"]
            self.assertNotIn(reply, self.published_ids())
            run.screen()
            self.assertIn(reply, self.published_ids())
            self.assertEqual(
                sum(
                    "<klados_state>" in r.messages[0]["content"]
                    for r in scripted.requests
                ),
                agent_calls,
            )
            self.assertEqual(society.audit(self.conn), [])
