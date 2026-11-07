"""Real OpenAI SDK wire contract, intercepted by httpx; never sends network traffic."""
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from openai import OpenAI

from arena import worker
from tee_arena.gateway import ModelPrice, OpenRouterProvider, Request
from tee_arena.society import decisions, observer


class LiveCompatibilityTests(unittest.TestCase):
    def complete(self, model, text, *, status=200, body=None):
        self.requests = []

        def handle(request):
            self.requests.append(request)
            return httpx.Response(status, json=body if body is not None else {
                "id": "synthetic-response", "object": "chat.completion", "created": 0,
                "model": model, "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            })

        sdk = SimpleNamespace(OpenAI=lambda **kwargs: OpenAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle))))
        adapter = OpenRouterProvider(sdk=sdk, api_key_variable="OPENROUTER_API_KEY_AGENTS",
                                     max_prices={model: ModelPrice("0.10", "0.50")})
        request = Request(model, "Synthetic system", ({"role": "user", "content": "Synthetic state"},),
                          1200, 30)
        with patch.dict(os.environ, OPENROUTER_API_KEY_AGENTS="synthetic-not-a-real-key"):
            return adapter.complete(request)

    def test_all_four_actions_and_observer_json_cross_the_real_sdk(self):
        for decision in ({"action": "post", "text": "An idea."},
                         {"action": "reply", "post_id": 1, "text": "A reply."},
                         {"action": "like", "post_id": 1}, {"action": "rest"}):
            with self.subTest(action=decision["action"]):
                result = self.complete(worker.AGENT_MODEL.split(":", 1)[1], json.dumps(decision))
                self.assertEqual(decisions.parse_turn(result.text)["action"], decision["action"])
                self.assertEqual((result.stop, result.input_tokens, result.output_tokens), ("end", 100, 30))
                self.assertEqual(len(self.requests), 1)
                request = self.requests[0]
                self.assertEqual(str(request.url), "https://openrouter.ai/api/v1/chat/completions")
                wire = json.loads(request.content)
                self.assertEqual(wire["max_tokens"], 1200)
                self.assertNotIn("tools", wire)
                self.assertEqual(wire["provider"], {"data_collection": "deny",
                    "max_price": {"prompt": 0.1, "completion": 0.5}})
        result = self.complete(worker.OBSERVER_MODEL.split(":", 1)[1], '{"codes":[],"evidence":""}')
        self.assertEqual(observer.parse(result.text, "An idea.")["severity"], "none")

    def test_sdk_does_not_retry_rejected_requests(self):
        for status in (401, 402, 429, 500):
            with self.subTest(status=status), self.assertRaises(Exception) as caught:
                self.complete(worker.AGENT_MODEL.split(":", 1)[1], "", status=status,
                              body={"error": {"message": "synthetic rejection", "code": status}})
            self.assertEqual(caught.exception.status_code, status)
            self.assertEqual(len(self.requests), 1)

    def test_live_role_keys_are_separate_and_mock_never_constructs_live_adapter(self):
        book = Mock()
        for role in ("agents", "observer"):
            client = worker.build_gateway(book, environ={"KLADOS_AI_MODE": "live",
                "OPENROUTER_API_KEY_AGENTS": "synthetic-agent",
                "OPENROUTER_API_KEY_OBSERVER": "synthetic-observer"}, role=role)
            self.assertEqual(client.providers["openrouter"].api_key_variable,
                             "OPENROUTER_API_KEY_" + role.upper())
        with patch("arena.worker.gateway_module.OpenRouterProvider", side_effect=AssertionError("live adapter")):
            worker.build_gateway(book, environ={"KLADOS_AI_MODE": "mock"})
        with self.assertRaises(ValueError):
            worker.build_gateway(book, environ={"KLADOS_AI_MODE": "invalid"})
