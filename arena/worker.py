"""The founders' worker: turns and daily reflections through OpenRouter. Run every few minutes.

    python -m arena.worker --ledger /data/klados-ledger.json

Safety:
- Keys only from the environment: OPENROUTER_API_KEY_AGENTS (or OPENROUTER_API_KEY).
  They never enter the arena web container; the worker runs in its own process.
- Every call goes through tee_arena.gateway: model allowlist, price ceilings, the money
  ledger caps and the stop file. Nothing calls a model any other way.
- A turn is claimed and committed before the call, so a crash can never give an agent
  a second turn in the same hour. A failed call or an unusable reply is a rest.
- Stops on errors: a spent cap, a missing key or the stop file pauses the society at
  once; ``max_failures`` failed calls in a row pause it too. A paused society does not
  age, so nobody dies of an outage.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

from arena import db, society, society_death, society_life, society_moderation
from tee_arena import gateway as gateway_module, ledger as ledger_module
from tee_arena.society import decisions, observer, prompts, rules as rules_module

ROOT = Path(__file__).resolve().parent.parent
MODELS_PATH = ROOT / "config" / "society-models.json"
AGENT_MODEL = "openrouter:anthropic/claude-haiku-5.5"
OBSERVER_MODEL = "openrouter:openai/gpt-6-luna"  # another family than the agents
ACCOUNT = "society-agents"
OBSERVER_ACCOUNT = "society-observer"
MAX_OUTPUT_TOKENS = 1200  # the model may spend part of it reasoning before the JSON
PAUSE_AT_ONCE = ("cap_exceeded", "credential_unavailable", "stopped", "model_not_allowed")
# The provider answered, but the reply cannot be used: a rest, not an outage.
BAD_REPLY = ("response_incomplete", "response_invalid", "response_too_large")


class Worker:
    def __init__(self, conn, rules, gateway, *, observer_gateway=None, model_id=AGENT_MODEL,
                 account=ACCOUNT, max_failures=3, delays=(10, 20, 40), sleep=time.sleep, now=None):
        self.conn, self.rules, self.gateway = conn, rules, gateway
        self.observer_gateway = observer_gateway  # None: posts stay unpublished
        self.model_id, self.account, self.max_failures = model_id, account, max_failures
        self.delays, self.sleep = delays, sleep
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.failures = 0
        self.log = []

    def call(self, system, messages, request_id):
        """One model call with retries on HTTP 429, under new request ids."""
        for attempt in range(len(self.delays) + 1):
            try:
                return self.gateway.call(account=self.account, role="agent", model_id=self.model_id, system=system,
                                         messages=messages, max_output_tokens=MAX_OUTPUT_TOKENS,
                                         request_id=request_id + (f":a{attempt}" if attempt else ""))
            except gateway_module.GatewayError as error:
                if error.category == "provider_rejected" and error.status == 429 and attempt < len(self.delays):
                    self.sleep(self.delays[attempt])
                    continue
                raise

    def _failed(self, error):
        """Count a failed call; pause the society when it is no longer safe to go on."""
        if error.category in BAD_REPLY:
            return False
        self.failures += 1
        if error.category in PAUSE_AT_ONCE or self.failures >= self.max_failures:
            society.set_paused(self.conn, self.rules, True, self.now(), f"worker: {error.category}")
            self.conn.commit()
            return True
        return False

    def turn(self, agent_id, tick):
        if not society.claim_turn(self.conn, agent_id, tick, self.now()):
            return None
        self.conn.commit()  # the turn is spent even if everything below fails
        context = society_life.turn_context(self.conn, self.rules, agent_id)
        system, messages = prompts.turn(context)
        try:
            result = self.call(system, messages, f"klados.turn.{agent_id}.{tick}")
        except gateway_module.GatewayError as error:
            code = ("reply_" if error.category in BAD_REPLY else "call_") + error.category
            society_life.fail_turn(self.conn, agent_id, tick, code, charged_microusd=error.charged_microusd)
            self.conn.commit()
            self.log.append({"agent": agent_id, "tick": tick, "error": code})
            return "stop" if self._failed(error) else "failed"
        self.failures = 0
        try:
            decision = decisions.parse_turn(result.text)
        except decisions.DecisionError as error:
            society_life.fail_turn(self.conn, agent_id, tick, "reply_" + error.code,
                                   charged_microusd=result.charged_microusd)
            self.conn.commit()
            self.log.append({"agent": agent_id, "tick": tick, "error": "reply_" + error.code})
            return "failed"
        outcome = society_life.act(self.conn, self.rules, agent_id, tick, decision, self.now(),
                                   charged_microusd=result.charged_microusd)
        self.conn.commit()
        self.log.append({"agent": agent_id, "tick": tick, "context": context, "decision": decision,
                         "outcome": outcome, "charged_microusd": result.charged_microusd})
        if "post_id" in outcome and self.screen() == "stop":
            return "stop"
        return "done"

    def screen(self):
        """Run the observer on every unpublished post. Returns "stop" if the society was paused."""
        if self.observer_gateway is None:
            return None
        for post in society_moderation.pending(self.conn):
            try:
                verdict = observer.review(self.observer_gateway, post["text"], post["replied_text"],
                                          model_id=OBSERVER_MODEL, account=OBSERVER_ACCOUNT,
                                          request_id=f"klados.observe.{post['id']}.{post['screen_failures']}")
            except gateway_module.GatewayError as error:
                society_moderation.screen_failed(self.conn, post["id"])
                self.conn.commit()
                self.log.append({"post": post["id"], "error": "observer call_" + error.category})
                if self._failed(error):
                    return "stop"
                continue
            except observer.ObserverError as error:
                society_moderation.screen_failed(self.conn, post["id"])
                self.conn.commit()
                self.log.append({"post": post["id"], "error": "observer reply_" + error.code,
                                 "charged_microusd": getattr(error, "charged_microusd", 0)})
                continue
            outcome = society_moderation.apply_verdict(self.conn, self.rules, post["id"], verdict, self.now())
            self.conn.commit()
            self.log.append({"post": post["id"], "moderation": outcome, "codes": verdict["codes"],
                             "by": verdict["by"], "charged_microusd": verdict["charged_microusd"]})
        return None

    def reflect(self, agent_id, day, tick):
        context = society_life.reflection_context(self.conn, self.rules, agent_id, day)
        system, messages = prompts.reflection(context)
        try:
            result = self.call(system, messages, f"klados.reflect.{agent_id}.{day}")
        except gateway_module.GatewayError as error:
            society_life.record_reflection(self.conn, agent_id, day, tick, None, self.now(),
                                           charged_microusd=error.charged_microusd, error="call_" + error.category)
            self.conn.commit()
            self.log.append({"agent": agent_id, "day": day, "tick": tick, "error": "reflection call_" + error.category})
            return "stop" if self._failed(error) else "failed"
        self.failures = 0
        charged = result.charged_microusd
        try:
            decision = decisions.parse_reflection(result.text)
        except decisions.DecisionError:
            try:  # one more try: a lost reflection costs the agent a whole day of learning
                result = self.call(system, messages, f"klados.reflect.{agent_id}.{day}.retry")
                charged += result.charged_microusd
                decision = decisions.parse_reflection(result.text)
            except (decisions.DecisionError, gateway_module.GatewayError) as error:
                if isinstance(error, gateway_module.GatewayError):
                    charged += error.charged_microusd
                code = "reply_" + getattr(error, "code", None) if isinstance(error, decisions.DecisionError) \
                    else "call_" + error.category
                society_life.record_reflection(self.conn, agent_id, day, tick, None, self.now(),
                                               charged_microusd=charged, error=code)
                self.conn.commit()
                self.log.append({"agent": agent_id, "day": day, "tick": tick, "error": "reflection " + code})
                if isinstance(error, gateway_module.GatewayError) and self._failed(error):
                    return "stop"
                return "failed"
        outcome = society_life.record_reflection(self.conn, agent_id, day, tick, decision, self.now(),
                                                 charged_microusd=charged)
        if outcome == "changed":  # public: the fact that it changed, never the strategy
            society.event(self.conn, self.rules, tick, "strategy_changed", self.now(), agent_id)
        self.conn.commit()
        self.log.append({"agent": agent_id, "day": day, "reflection": context, "decision": decision,
                         "outcome": outcome, "charged_microusd": charged})
        return outcome

    def last_words(self):
        """One free last message for each agent that has died. Returns "stop" if the society was paused."""
        if self.observer_gateway is None:  # nothing may be published unscreened
            return None
        for fossil in society_death.pending(self.conn):
            agent_id = fossil["agent_id"]
            system, messages = prompts.last_words(society_death.context(self.conn, self.rules, agent_id))
            request = f"klados.lastwords.{agent_id}.{fossil['last_words_attempts']}"
            try:
                result = self.call(system, messages, request)
                text = decisions.parse_last_words(result.text)
                verdict = observer.review(self.observer_gateway, text, model_id=OBSERVER_MODEL,
                                          account=OBSERVER_ACCOUNT, request_id=request + ".observe")
            except gateway_module.GatewayError as error:
                society_death.failed(self.conn, agent_id)
                self.conn.commit()
                self.log.append({"agent": agent_id, "error": "last words call_" + error.category})
                if self._failed(error):
                    return "stop"
                continue
            except (decisions.DecisionError, observer.ObserverError) as error:
                society_death.failed(self.conn, agent_id)
                self.conn.commit()
                self.log.append({"agent": agent_id, "error": "last words reply_" + error.code})
                continue
            self.failures = 0
            outcome = society_death.record(self.conn, self.rules, agent_id, text, verdict, self.now())
            self.conn.commit()
            self.log.append({"agent": agent_id, "last_words": outcome, "codes": verdict["codes"]})
        return None

    def run(self):
        """One pass: every due turn, then every due reflection. Returns a summary."""
        summary = {"turns": 0, "failed": 0, "reflections": 0, "stopped": False}
        if society.clock(self.conn)["paused"]:
            return dict(summary, stopped=True)
        if self.screen() == "stop":  # posts left from an earlier pass
            return dict(summary, stopped=True)
        tick = society.clock(self.conn)["tick"]
        for agent_id in society.due_turns(self.conn, self.now()):
            result = self.turn(agent_id, tick)
            if result == "stop":
                return dict(summary, stopped=True)
            if result is not None:
                summary["turns" if result == "done" else "failed"] += 1
        for agent_id, day in society_life.reflections_due(self.conn):
            result = self.reflect(agent_id, day, tick)
            if result == "stop":
                return dict(summary, stopped=True)
            summary["reflections"] += result in ("kept", "changed")
        if self.last_words() == "stop":
            return dict(summary, stopped=True)
        return summary


def build_gateway(ledger, models_path=MODELS_PATH, environ=os.environ, role="agents"):
    """A gateway that can call only the society's models, with the role's own key.

    ``role`` is "agents" or "observer": OPENROUTER_API_KEY_AGENTS / _OBSERVER, else the
    shared OPENROUTER_API_KEY. Separate keys show each role's spending at OpenRouter.
    """
    if role not in ("agents", "observer"):
        raise ValueError("role must be agents or observer")
    models = gateway_module.load_models(models_path)
    mode = environ.get("KLADOS_AI_MODE", "live")
    if mode == "mock":
        from arena.mock_provider import MockProvider
        return gateway_module.Gateway(ledger=ledger, models=models,
                                      providers={"openrouter": MockProvider()})
    if mode != "live":
        raise ValueError("KLADOS_AI_MODE must be mock or live")
    own = f"OPENROUTER_API_KEY_{role.upper()}"
    variable = own if environ.get(own) else "OPENROUTER_API_KEY"
    prices = {m.split(":", 1)[1]: p for m, p in models.items() if m.startswith("openrouter:")}
    provider = gateway_module.OpenRouterProvider(api_key_variable=variable, max_prices=prices)
    return gateway_module.Gateway(ledger=ledger, models=models, providers={"openrouter": provider})


def open_ledger(path, cap_usd=None):
    """The money ledger for the founders' calls; created once with an explicit cap."""
    path = Path(path)
    if path.exists():
        return ledger_module.Ledger(path)
    if cap_usd is None:
        raise SystemExit("error: the ledger does not exist; create it once with --create-cap-usd")
    cap = int(round(float(cap_usd) * 1_000_000))
    book = ledger_module.Ledger.create(path, global_cap=cap)
    book.open_account(ACCOUNT, cap, note="Klados MVP v1 founders")
    book.open_account(OBSERVER_ACCOUNT, cap, note="Klados MVP v1 observer")
    return book


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the founders' due turns and reflections once.")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--create-cap-usd", help="Create the ledger with this total cap (first run only)")
    args = parser.parse_args(argv)
    dsn = os.environ.get("ARENA_DATABASE_URL")
    if not dsn:
        print("error: ARENA_DATABASE_URL is required", file=sys.stderr)
        return 1
    rules = rules_module.load(os.environ.get("KLADOS_RULES") or rules_module.DEFAULT_PATH)
    book = open_ledger(args.ledger, args.create_cap_usd)
    gateway, watcher = build_gateway(book), build_gateway(book, role="observer")
    with db.connect(dsn) as conn:
        summary = Worker(conn, rules, gateway, observer_gateway=watcher).run()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
