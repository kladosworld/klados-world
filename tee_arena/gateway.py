"""The only path from the arena to AI models.

Every model call goes through ``Gateway.call``:

1. The request is checked: allowed role, registered model, bounded output.
2. A conservative cost estimate is reserved in the ledger. If that would exceed the
   account's cap or the global cap, the call is refused before any network request.
3. The provider is called exactly once. There are no automatic retries.
4. The reservation is settled with the real cost when the provider reports usage for
   the requested model, and kept in full otherwise.

Starting points in TEE-internal: the provider call in ``creative.py`` (no retries,
injectable provider, usage read from the response envelope) and
``operational.estimate()`` (conservative integer microdollar estimate).

Errors carry a fixed category only, never provider messages, request text or keys.
Provider adapters import their SDK and read their API key only when a call is made.
"""
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
import json
import math
import os
from pathlib import Path
import re
import time

from tee_arena import ledger as ledger_module

ROLES = ("agent", "mutator", "observer", "utility")
MODEL_ID = re.compile(r"(anthropic|openai):[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
                      r"|openrouter:[a-z0-9][a-z0-9._-]{0,49}/[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
MAX_OUTPUT_TOKENS = 8000
MAX_RESPONSE_BYTES = 64_000
MAX_REQUEST_BYTES = 200_000
INPUT_OVERHEAD_TOKENS = 512
DEFAULT_STOP_FILE = Path("/etc/klados-control/KILLED")  # Klados only: `touch` it to refuse every model call.

CATEGORIES = ("invalid_request", "model_not_allowed", "stopped", "cap_exceeded", "duplicate_request",
              "credential_unavailable", "sdk_unavailable", "provider_failure", "response_incomplete",
              "response_too_large", "response_invalid", "provider_rejected")
# Client errors the providers reject before doing any work and do not bill: bad
# request, authentication, payment required, permission, not found, conflict, too
# large, unprocessable, rate limited. Anything else after sending keeps its charge.
UNBILLED_STATUSES = frozenset((400, 401, 402, 403, 404, 409, 413, 422, 429))


class GatewayError(RuntimeError):
    """A refused or failed call. ``category`` is one of ``CATEGORIES``.

    ``sent`` tells whether a request may have reached the provider (and may be
    billed). ``charged_microusd`` is what the ledger kept for it.
    """

    def __init__(self, category, *, sent=False, charged_microusd=0, status=None, error_type=None):
        if category not in CATEGORIES:
            category = "provider_failure"
        self.category, self.sent, self.charged_microusd = category, sent, charged_microusd
        # Safe diagnostics only: an HTTP status and an exception class name, never
        # a message, which could echo request text or credentials.
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        self.error_type = error_type if isinstance(error_type, str) and error_type.isidentifier() else None
        super().__init__(f"Model call {'failed' if sent else 'refused'}: {category}.")


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens, as decimal strings (for example ``"3"`` and ``"15"``)."""
    input_usd_per_mtok: str
    output_usd_per_mtok: str

    def rates(self):
        values = [Decimal(self.input_usd_per_mtok), Decimal(self.output_usd_per_mtok)]
        if any(not v.is_finite() or v <= 0 or v > 1000 for v in values):
            raise ValueError("Model prices must be positive and at most 1000 USD per million tokens.")
        return values


def cost_microusd(price, input_tokens, output_tokens):
    """Exact cost, rounded up to a whole microdollar.

    USD per million tokens equals microdollars per token, so no other scaling applies.
    """
    rate_in, rate_out = price.rates()
    return int((input_tokens * rate_in + output_tokens * rate_out).to_integral_value(rounding=ROUND_CEILING))


def input_token_allowance(system, messages):
    """Conservative input size: one token per two UTF-8 bytes, plus fixed overhead.

    Real tokenizers average three to four bytes per token for English text, so this
    overestimates on purpose. The difference is returned when the call settles.
    """
    size = len(system.encode("utf-8")) + sum(len(m["content"].encode("utf-8")) for m in messages)
    return math.ceil(size / 2) + INPUT_OVERHEAD_TOKENS


@dataclass(frozen=True)
class Request:
    model: str          # provider-specific name, without the "provider:" prefix
    system: str
    messages: tuple     # ({"role": "user" | "assistant", "content": str}, ...)
    max_output_tokens: int
    timeout_seconds: int


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    model: str                 # the model the provider says answered
    stop: str                  # "end", "max_tokens" or "other"
    input_tokens: int | None   # None when the provider did not report usage
    output_tokens: int | None


@dataclass(frozen=True)
class Result:
    text: str
    model: str
    request_id: str
    reserved_microusd: int
    charged_microusd: int
    usage_known: bool
    elapsed_ms: int


def _check_messages(messages):
    if not isinstance(messages, (list, tuple)) or not 1 <= len(messages) <= 50:
        raise GatewayError("invalid_request")
    checked = []
    for index, message in enumerate(messages):
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in ("user", "assistant") or not isinstance(message["content"], str)
                or not message["content"].strip()):
            raise GatewayError("invalid_request")
        if (index % 2 == 0) != (message["role"] == "user"):
            raise GatewayError("invalid_request")  # Must alternate, starting and ending with the user.
        checked.append({"role": message["role"], "content": message["content"]})
    if checked[-1]["role"] != "user":
        raise GatewayError("invalid_request")
    return tuple(checked)


@dataclass
class Gateway:
    """Route model calls through caps, a ledger and a fixed set of providers.

    ``models`` maps ``"provider:model"`` ids to their price. Only these models can
    be called. ``providers`` maps a provider name to an adapter with a
    ``complete(Request) -> ProviderResponse`` method; tests inject fakes here.
    """
    ledger: ledger_module.Ledger
    models: dict
    providers: dict = field(default_factory=dict)
    stop_file: Path | None = DEFAULT_STOP_FILE
    timeout_seconds: int = 60

    def __post_init__(self):
        for model_id, price in self.models.items():
            if not MODEL_ID.fullmatch(model_id) or not isinstance(price, ModelPrice):
                raise ValueError(f"Invalid model registration: {model_id!r}")
            price.rates()
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 300:
            raise ValueError("Timeout must be 1-300 seconds.")

    def _provider(self, name):
        if name not in self.providers:
            if name == "openrouter":
                # Cap each model at its registered price, so the ledger's estimate is
                # always an upper bound on what OpenRouter's chosen provider charges.
                prices = {m.split(":", 1)[1]: p for m, p in self.models.items() if m.startswith("openrouter:")}
                self.providers[name] = OpenRouterProvider(max_prices=prices)
            else:
                self.providers[name] = {"anthropic": AnthropicProvider, "openai": OpenAIProvider}[name]()
        return self.providers[name]

    def estimate(self, model_id, system, messages, max_output_tokens):
        """The amount that would be reserved for this call, in microdollars."""
        if model_id not in self.models:
            raise GatewayError("model_not_allowed")
        messages = _check_messages(messages)
        return cost_microusd(self.models[model_id], input_token_allowance(system, messages), max_output_tokens)

    def call(self, *, account, role, model_id, system, messages, max_output_tokens, request_id):
        if role not in ROLES or not isinstance(system, str):
            raise GatewayError("invalid_request")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise GatewayError("invalid_request")
        if model_id not in self.models:
            raise GatewayError("model_not_allowed")
        messages = _check_messages(messages)
        if len(json.dumps({"s": system, "m": messages}).encode("utf-8")) > MAX_REQUEST_BYTES:
            raise GatewayError("invalid_request")
        if self.stop_file is not None and self.stop_file.exists():
            raise GatewayError("stopped")

        price = self.models[model_id]
        provider_name, model = model_id.split(":", 1)
        reserved = cost_microusd(price, input_token_allowance(system, messages), max_output_tokens)
        try:
            reservation = self.ledger.reserve(account, reserved, request_id, note=f"{role} {model_id}")
        except ledger_module.CapExceeded:
            raise GatewayError("cap_exceeded") from None
        except ledger_module.DuplicateRequest:
            raise GatewayError("duplicate_request") from None
        except ledger_module.LedgerError:
            raise GatewayError("invalid_request") from None

        request = Request(model=model, system=system, messages=messages,
                          max_output_tokens=max_output_tokens, timeout_seconds=self.timeout_seconds)
        try:
            provider = self._provider(provider_name)
        except GatewayError as error:
            self.ledger.release(reservation, note=f"not sent: {error.category}")
            raise
        started = time.monotonic()
        try:
            response = provider.complete(request)
        except GatewayError as error:
            if not error.sent:  # The adapter stopped before sending anything.
                self.ledger.release(reservation, note=f"not sent: {error.category}")
                raise
            charged = self.ledger.settle(reservation, None, note=f"failed after sending: {error.category}")
            raise GatewayError(error.category, sent=True, charged_microusd=charged) from None
        except Exception as error:
            status = getattr(error, "status_code", None)
            status = status if type(status) is int else None
            error_type = type(error).__name__
            if status in UNBILLED_STATUSES:
                self.ledger.release(reservation, note=f"rejected by provider: HTTP {status}")
                raise GatewayError("provider_rejected", sent=True, status=status, error_type=error_type) from None
            # The request may have reached the provider and may be billed. Keep the
            # whole reservation and never retry.
            charged = self.ledger.settle(reservation, None, note=f"failed after sending: {error_type}")
            raise GatewayError("provider_failure", sent=True, charged_microusd=charged,
                               status=status, error_type=error_type) from None
        elapsed_ms = int((time.monotonic() - started) * 1000)

        usage_known = (isinstance(response, ProviderResponse) and response.model == model
                       and type(response.input_tokens) is int and type(response.output_tokens) is int
                       and response.input_tokens >= 0 and response.output_tokens >= 0)
        actual = cost_microusd(price, response.input_tokens, response.output_tokens) if usage_known else None
        charged = self.ledger.settle(reservation, actual, note=f"{role} {model_id}")

        if not isinstance(response, ProviderResponse) or not isinstance(response.text, str):
            raise GatewayError("response_invalid", sent=True, charged_microusd=charged)
        if response.stop != "end":
            raise GatewayError("response_incomplete", sent=True, charged_microusd=charged)
        if len(response.text.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise GatewayError("response_too_large", sent=True, charged_microusd=charged)
        return Result(text=response.text, model=model_id, request_id=request_id, reserved_microusd=reserved,
                      charged_microusd=charged, usage_known=usage_known, elapsed_ms=elapsed_ms)


def _api_key(variable):
    key = os.environ.get(variable)
    if not key:
        raise GatewayError("credential_unavailable")
    return key


class AnthropicProvider:
    """Anthropic Messages API. Not yet exercised against the live API.

    ``sdk`` is the ``anthropic`` module; tests pass a fake one.
    """

    def __init__(self, sdk=None, api_key_variable="ANTHROPIC_API_KEY"):
        self.sdk, self.api_key_variable = sdk, api_key_variable

    def complete(self, request):
        key = _api_key(self.api_key_variable)
        sdk = self.sdk
        if sdk is None:
            try:
                import anthropic as sdk
            except ImportError:
                raise GatewayError("sdk_unavailable") from None
        with sdk.Anthropic(api_key=key, max_retries=0, timeout=request.timeout_seconds) as client:
            response = client.messages.create(model=request.model, max_tokens=request.max_output_tokens,
                                              system=request.system, messages=list(request.messages))
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        usage = getattr(response, "usage", None)
        input_tokens = output_tokens = None
        if usage is not None and getattr(usage, "service_tier", None) in (None, "standard"):
            parts = [getattr(usage, "input_tokens", None), getattr(usage, "cache_creation_input_tokens", None) or 0,
                     getattr(usage, "cache_read_input_tokens", None) or 0]
            if all(type(p) is int for p in parts):
                # Cached tokens are billed at different rates; counting them all at the
                # full input rate overestimates, which is the safe direction.
                input_tokens = sum(parts)
            if type(getattr(usage, "output_tokens", None)) is int:
                output_tokens = usage.output_tokens
        stop = {"end_turn": "end", "stop_sequence": "end", "max_tokens": "max_tokens"}.get(
            getattr(response, "stop_reason", None), "other")
        return ProviderResponse(text=text, model=getattr(response, "model", None), stop=stop,
                                input_tokens=input_tokens, output_tokens=output_tokens)


class OpenAIProvider:
    """OpenAI Chat Completions API. Not yet exercised against the live API.

    ``sdk`` is the ``openai`` module; tests pass a fake one. The provider reports
    a dated model name (for example ``name-2026-01-01``); a response counts as the
    requested model when its name starts with the requested name.
    """

    base_url = None
    token_parameter = "max_completion_tokens"

    def __init__(self, sdk=None, api_key_variable="OPENAI_API_KEY"):
        self.sdk, self.api_key_variable = sdk, api_key_variable

    def extra_body(self, request):
        return None

    def complete(self, request):
        key = _api_key(self.api_key_variable)
        sdk = self.sdk
        if sdk is None:
            try:
                import openai as sdk
            except ImportError:
                raise GatewayError("sdk_unavailable") from None
        options = dict(api_key=key, max_retries=0, timeout=request.timeout_seconds)
        if self.base_url:
            options["base_url"] = self.base_url
        client = sdk.OpenAI(**options)
        try:
            messages = [{"role": "system", "content": request.system}, *request.messages]
            params = {"model": request.model, "messages": messages, self.token_parameter: request.max_output_tokens}
            extra = self.extra_body(request)
            if extra:
                params["extra_body"] = extra
            response = client.chat.completions.create(**params)
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                close()
        choice = response.choices[0]
        text = choice.message.content or ""
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
        answered = getattr(response, "model", None)
        model = request.model if isinstance(answered, str) and (
            answered == request.model or answered.startswith(request.model + "-")) else answered
        stop = {"stop": "end", "length": "max_tokens"}.get(getattr(choice, "finish_reason", None), "other")
        return ProviderResponse(text=text, model=model, stop=stop,
                                input_tokens=input_tokens if type(input_tokens) is int else None,
                                output_tokens=output_tokens if type(output_tokens) is int else None)


class OpenRouterProvider(OpenAIProvider):
    """Open-weight models (Gemma, Qwen and others) through OpenRouter's
    OpenAI-compatible API. Not yet exercised against the live API.

    OpenRouter routes each request to one of several providers whose prices differ.
    ``max_prices`` caps every model at its registered price (``provider.max_price``),
    so the ledger, which charges the registered price, never undercounts. Providers
    that keep request data for training are excluded (``data_collection: deny``).
    """
    base_url = "https://openrouter.ai/api/v1"
    token_parameter = "max_tokens"

    def __init__(self, sdk=None, api_key_variable="OPENROUTER_API_KEY", max_prices=None):
        super().__init__(sdk=sdk, api_key_variable=api_key_variable)
        self.max_prices = dict(max_prices or {})

    def extra_body(self, request):
        price = self.max_prices.get(request.model)
        if price is None:
            raise GatewayError("model_not_allowed")
        rate_in, rate_out = price.rates()
        return {"provider": {"data_collection": "deny",
                             "max_price": {"prompt": float(rate_in), "completion": float(rate_out)}}}


def load_models(path):
    """Read a model registry: ``{"provider:model": {"input_usd_per_mtok": "3", "output_usd_per_mtok": "15"}}``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Model registry must be a JSON object.")
    return {model_id: ModelPrice(**{k: str(v) for k, v in prices.items()})
            for model_id, prices in data.items() if not model_id.startswith("_")}
