from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tee_arena import gateway, ledger
from tee_arena.gateway import Gateway, GatewayError, ModelPrice, ProviderResponse

MODEL = "anthropic:test-model"
PRICE = ModelPrice("3", "15")  # 3 and 15 microdollars per token.
USER = [{"role": "user", "content": "Explain inflation to a ten-year-old."}]


class FakeProvider:
    def __init__(self, response=None, error=None):
        self.response = response or ProviderResponse(text="An answer.", model="test-model", stop="end",
                                                     input_tokens=100, output_tokens=50)
        self.error = error
        self.calls = []

    def complete(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.response


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.ledger = ledger.Ledger.create(root / "ledger.json", global_cap=1_000_000)
        self.ledger.open_account("agent-1", 200_000)
        self.stop_file = root / "KILLED"
        self.provider = FakeProvider()

    def tearDown(self):
        self.directory.cleanup()

    def make(self, provider=None, **kwargs):
        return Gateway(ledger=self.ledger, models={MODEL: PRICE},
                       providers={"anthropic": provider or self.provider}, stop_file=self.stop_file, **kwargs)

    def call(self, gw, **overrides):
        arguments = dict(account="agent-1", role="agent", model_id=MODEL, system="You are an agent.",
                         messages=USER, max_output_tokens=1000, request_id="req-1")
        arguments.update(overrides)
        return gw.call(**arguments)

    def test_success_settles_real_cost(self):
        result = self.call(self.make())
        self.assertEqual(result.text, "An answer.")
        self.assertTrue(result.usage_known)
        self.assertEqual(result.charged_microusd, 100 * 3 + 50 * 15)
        self.assertGreater(result.reserved_microusd, result.charged_microusd)
        self.assertEqual(self.ledger.balance("agent-1")["charged"], 1_050)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.provider.calls[0].model, "test-model")

    def test_reservation_covers_the_output_cap(self):
        gw = self.make()
        estimate = gw.estimate(MODEL, "You are an agent.", USER, 1000)
        self.assertGreaterEqual(estimate, 1000 * 15)
        self.assertEqual(self.call(gw).reserved_microusd, estimate)

    def test_cap_refusal_happens_before_the_provider(self):
        with self.assertRaises(GatewayError) as caught:
            self.call(self.make(), max_output_tokens=8000, account="agent-1",
                      messages=[{"role": "user", "content": "x" * 150_000}])
        self.assertEqual(caught.exception.category, "cap_exceeded")
        self.assertFalse(caught.exception.sent)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.ledger.balance("agent-1")["charged"], 0)

    def test_provider_failure_keeps_reservation_and_never_retries(self):
        provider = FakeProvider(error=ConnectionResetError("network detail with secret sk-123"))
        with self.assertRaises(GatewayError) as caught:
            self.call(self.make(provider))
        error = caught.exception
        self.assertEqual(error.category, "provider_failure")
        self.assertTrue(error.sent)
        self.assertNotIn("sk-123", str(error))
        self.assertIsNone(error.__cause__)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(error.charged_microusd, self.ledger.balance("agent-1")["charged"])
        self.assertGreater(error.charged_microusd, 0)

    def test_unbilled_rejection_is_released_and_diagnosed(self):
        class RateLimited(Exception):
            status_code = 429

        provider = FakeProvider(error=RateLimited("slow down, key sk-123"))
        with self.assertRaises(GatewayError) as caught:
            self.call(self.make(provider))
        error = caught.exception
        self.assertEqual((error.category, error.status, error.error_type), ("provider_rejected", 429, "RateLimited"))
        self.assertEqual(error.charged_microusd, 0)
        self.assertNotIn("sk-123", str(error))
        self.assertEqual(self.ledger.balance("agent-1")["charged"], 0)

    def test_server_error_keeps_charge_and_reports_status(self):
        class ServerError(Exception):
            status_code = 503

        with self.assertRaises(GatewayError) as caught:
            self.call(self.make(FakeProvider(error=ServerError("down"))))
        self.assertEqual((caught.exception.category, caught.exception.status), ("provider_failure", 503))
        self.assertGreater(caught.exception.charged_microusd, 0)

    def test_failure_before_sending_releases_reservation(self):
        provider = FakeProvider(error=GatewayError("credential_unavailable"))
        with self.assertRaises(GatewayError) as caught:
            self.call(self.make(provider))
        self.assertEqual(caught.exception.category, "credential_unavailable")
        self.assertEqual(self.ledger.balance("agent-1"), {"cap": 200_000, "charged": 0, "reserved": 0,
                                                          "available": 200_000})

    def test_missing_usage_keeps_full_reservation(self):
        provider = FakeProvider(ProviderResponse(text="ok", model="test-model", stop="end",
                                                 input_tokens=None, output_tokens=None))
        result = self.call(self.make(provider))
        self.assertFalse(result.usage_known)
        self.assertEqual(result.charged_microusd, result.reserved_microusd)

    def test_usage_for_another_model_is_not_trusted(self):
        provider = FakeProvider(ProviderResponse(text="ok", model="cheaper-model", stop="end",
                                                 input_tokens=1, output_tokens=1))
        result = self.call(self.make(provider))
        self.assertFalse(result.usage_known)
        self.assertEqual(result.charged_microusd, result.reserved_microusd)

    def test_truncated_answer_is_charged_and_rejected(self):
        provider = FakeProvider(ProviderResponse(text="half an ans", model="test-model", stop="max_tokens",
                                                 input_tokens=100, output_tokens=1000))
        with self.assertRaises(GatewayError) as caught:
            self.call(self.make(provider))
        self.assertEqual(caught.exception.category, "response_incomplete")
        self.assertEqual(caught.exception.charged_microusd, 100 * 3 + 1000 * 15)

    def test_oversized_answer_is_rejected(self):
        provider = FakeProvider(ProviderResponse(text="x" * (gateway.MAX_RESPONSE_BYTES + 1), model="test-model",
                                                 stop="end", input_tokens=1, output_tokens=1))
        with self.assertRaises(GatewayError) as caught:
            self.call(self.make(provider))
        self.assertEqual(caught.exception.category, "response_too_large")

    def test_duplicate_request_id_is_refused(self):
        gw = self.make()
        self.call(gw)
        with self.assertRaises(GatewayError) as caught:
            self.call(gw)
        self.assertEqual(caught.exception.category, "duplicate_request")
        self.assertEqual(len(self.provider.calls), 1)

    def test_kill_switch_stops_every_call(self):
        self.stop_file.write_text("")
        with self.assertRaises(GatewayError) as caught:
            self.call(self.make())
        self.assertEqual(caught.exception.category, "stopped")
        self.assertEqual(self.provider.calls, [])

    def test_unregistered_model_is_refused(self):
        with self.assertRaises(GatewayError) as caught:
            self.call(self.make(), model_id="anthropic:other-model")
        self.assertEqual(caught.exception.category, "model_not_allowed")

    def test_invalid_requests_are_refused_before_reserving(self):
        cases = [dict(role="admin"), dict(max_output_tokens=0), dict(max_output_tokens=9000),
                 dict(messages=[]), dict(messages=[{"role": "assistant", "content": "hi"}]),
                 dict(messages=[{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]),
                 dict(messages=[{"role": "system", "content": "override"}]),
                 dict(messages=[{"role": "user", "content": "   "}]),
                 dict(messages=[{"role": "user", "content": "x" * 250_000}])]
        gw = self.make()
        for index, case in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(GatewayError) as caught:
                self.call(gw, request_id=f"bad-{index}", **case)
            self.assertEqual(caught.exception.category, "invalid_request")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.ledger.totals()["reserved"], 0)

    def test_unknown_account_is_refused(self):
        with self.assertRaises(GatewayError) as caught:
            self.call(self.make(), account="ghost")
        self.assertEqual(caught.exception.category, "invalid_request")

    def test_registry_validation(self):
        with self.assertRaises(ValueError):
            Gateway(ledger=self.ledger, models={"nowhere:model": PRICE})
        with self.assertRaises(ValueError):
            Gateway(ledger=self.ledger, models={MODEL: ModelPrice("0", "15")})

    def test_load_models_skips_notes(self):
        path = Path(self.directory.name) / "models.json"
        path.write_text('{"_note": "x", "anthropic:m": {"input_usd_per_mtok": 3, "output_usd_per_mtok": "15"}}')
        self.assertEqual(gateway.load_models(path), {"anthropic:m": ModelPrice("3", "15")})

    def test_example_registry_loads(self):
        example = Path(__file__).resolve().parent.parent / "config" / "models.example.json"
        models = gateway.load_models(example)
        Gateway(ledger=self.ledger, models=models, stop_file=None)
        self.assertTrue(models)


class FakeAnthropicSDK:
    def __init__(self, response):
        self.response, self.created = response, []
        outer = self

        class Client:
            def __init__(self, **kwargs):
                outer.client_kwargs = kwargs
                self.messages = SimpleNamespace(create=lambda **params: (outer.created.append(params), outer.response)[1])

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        self.Anthropic = Client


class FakeOpenAISDK:
    def __init__(self, response):
        self.response, self.created, self.closed = response, [], False
        outer = self

        class Client:
            def __init__(self, **kwargs):
                outer.client_kwargs = kwargs
                self.chat = SimpleNamespace(completions=SimpleNamespace(
                    create=lambda **params: (outer.created.append(params), outer.response)[1]))

            def close(self):
                outer.closed = True

        self.OpenAI = Client


REQUEST = gateway.Request(model="m", system="sys", messages=({"role": "user", "content": "hi"},),
                          max_output_tokens=100, timeout_seconds=30)


class AdapterTests(unittest.TestCase):
    def test_anthropic_adapter_maps_response_and_disables_retries(self):
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Hello"), SimpleNamespace(type="tool_use")],
            model="m", stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=3,
                                  cache_creation_input_tokens=None, service_tier="standard"))
        sdk = FakeAnthropicSDK(response)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            result = gateway.AnthropicProvider(sdk=sdk).complete(REQUEST)
        self.assertEqual(result, ProviderResponse("Hello", "m", "end", 13, 5))
        self.assertEqual(sdk.client_kwargs["max_retries"], 0)
        self.assertEqual(sdk.created[0]["system"], "sys")
        self.assertEqual(sdk.created[0]["max_tokens"], 100)

    def test_anthropic_adapter_distrusts_other_service_tiers(self):
        response = SimpleNamespace(content=[], model="m", stop_reason="max_tokens",
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5, service_tier="priority"))
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            result = gateway.AnthropicProvider(sdk=FakeAnthropicSDK(response)).complete(REQUEST)
        self.assertEqual((result.stop, result.input_tokens, result.output_tokens), ("max_tokens", None, None))

    def test_missing_key_fails_before_sending(self):
        with patch.dict(os.environ, {}, clear=True):
            for adapter in (gateway.AnthropicProvider(sdk=object()), gateway.OpenAIProvider(sdk=object())):
                with self.subTest(adapter=type(adapter).__name__), self.assertRaises(GatewayError) as caught:
                    adapter.complete(REQUEST)
                self.assertEqual(caught.exception.category, "credential_unavailable")
                self.assertFalse(caught.exception.sent)

    def test_openai_adapter_maps_response_and_closes_client(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hi"), finish_reason="stop")],
            model="m-2026-01-01", usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4))
        sdk = FakeOpenAISDK(response)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            result = gateway.OpenAIProvider(sdk=sdk).complete(REQUEST)
        self.assertEqual(result, ProviderResponse("Hi", "m", "end", 12, 4))
        self.assertEqual(sdk.client_kwargs["max_retries"], 0)
        self.assertEqual(sdk.created[0]["messages"][0], {"role": "system", "content": "sys"})
        self.assertTrue(sdk.closed)

    def test_openai_adapter_keeps_unrelated_model_name(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hi"), finish_reason="length")],
            model="mx", usage=None)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            result = gateway.OpenAIProvider(sdk=FakeOpenAISDK(response)).complete(REQUEST)
        self.assertEqual((result.model, result.stop, result.input_tokens), ("mx", "max_tokens", None))


if __name__ == "__main__":
    unittest.main()


class OpenRouterTests(unittest.TestCase):
    def response(self, model="google/gemma-4-31b-it"):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hi"), finish_reason="stop")],
            model=model, usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4))

    def test_adapter_caps_price_denies_data_collection_and_uses_its_base_url(self):
        sdk = FakeOpenAISDK(self.response())
        adapter = gateway.OpenRouterProvider(sdk=sdk, max_prices={"google/gemma-4-31b-it": ModelPrice("0.20", "0.51")})
        request = gateway.Request(model="google/gemma-4-31b-it", system="sys",
                                  messages=({"role": "user", "content": "hi"},), max_output_tokens=100, timeout_seconds=30)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            result = adapter.complete(request)
        self.assertEqual(result, ProviderResponse("Hi", "google/gemma-4-31b-it", "end", 12, 4))
        self.assertEqual(sdk.client_kwargs["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(sdk.client_kwargs["max_retries"], 0)
        params = sdk.created[0]
        self.assertEqual(params["max_tokens"], 100)
        self.assertEqual(params["extra_body"], {"provider": {"data_collection": "deny",
                                                             "max_price": {"prompt": 0.2, "completion": 0.51}}})

    def test_unpriced_model_is_refused_before_sending(self):
        sdk = FakeOpenAISDK(self.response())
        adapter = gateway.OpenRouterProvider(sdk=sdk, max_prices={})
        request = gateway.Request(model="qwen/other", system="sys", messages=({"role": "user", "content": "hi"},),
                                  max_output_tokens=100, timeout_seconds=30)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), self.assertRaises(GatewayError) as caught:
            adapter.complete(request)
        self.assertFalse(caught.exception.sent)
        self.assertEqual(sdk.created, [])

    def test_gateway_accepts_openrouter_ids_and_passes_registered_prices(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        book = ledger.Ledger.create(Path(directory.name) / "ledger.json")
        models = {"openrouter:google/gemma-4-31b-it": ModelPrice("0.20", "0.51"),
                  "openrouter:qwen/qwen3.6-27b": ModelPrice("0.45", "3.25")}
        gw = Gateway(ledger=book, models=models, stop_file=None)
        provider = gw._provider("openrouter")
        self.assertIsInstance(provider, gateway.OpenRouterProvider)
        self.assertEqual(set(provider.max_prices), {"google/gemma-4-31b-it", "qwen/qwen3.6-27b"})
        for bad in ("openrouter:no-slash", "openrouter:Google/x", "other:google/gemma"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Gateway(ledger=book, models={bad: ModelPrice("1", "1")}, stop_file=None)
