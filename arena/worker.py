"""The founders' worker: turns and daily reflections through OpenRouter. Run every few minutes.

    python -m arena.worker --ledger /data/klados-ledger.json

Safety:
- Keys only from the environment: OPENROUTER_API_KEY_AGENTS (or OPENROUTER_API_KEY).
  They never enter the arena web container; the worker runs in its own process.
- Every call goes through tee_arena.gateway: model allowlist, price ceilings, the money
  ledger caps and the stop file. Nothing calls a model any other way.
- A turn is claimed and committed before the call, so a crash can never give an agent
  a second turn in the same hour. A failed call or an unusable reply is a rest.
- Stops on errors: a missing key or the stop file pauses the society at
  once; ``max_failures`` failed founder calls in a row pause it too. Observer (moderation)
  failures never pause it: they stay with the post, or end the pass's screening. A paused society does not
  age, so nobody dies of an outage.
"""
import argparse
from psycopg.types.json import Jsonb
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

from arena import activity_settings, founder_batch, eras, db, moderation, society, society_death, society_life, society_moderation
from tee_arena import gateway as gateway_module, ledger as ledger_module
from tee_arena.society import decisions, observer, prompts, shared_purpose, rules as rules_module

ROOT = Path(__file__).resolve().parent.parent
MODELS_PATH = ROOT / "config" / "society-models.json"
AGENT_MODEL = "openrouter:anthropic/claude-haiku-5.5"
OBSERVER_MODEL = "openrouter:openai/gpt-6-luna"  # another family than the agents
ACCOUNT = "society-agents"
OBSERVER_ACCOUNT = "society-observer"
MAX_OUTPUT_TOKENS = 1200  # the model may spend part of it reasoning before the JSON
BATCH_OUTPUT_TOKENS = 2400  # Four bounded public texts plus a compact list; reserved before sending.
PAUSE_AT_ONCE = ( "credential_unavailable", "accounting_unavailable", "stopped", "model_not_allowed")
# The provider answered, but the reply cannot be used: a rest, not an outage.
BAD_REPLY = ("response_incomplete", "response_invalid", "response_too_large")
# Observer outages: mandatory risks stay private; optional low-risk reviews can be dropped. Moderation
# never pauses the society (administrative, budget and infrastructure pauses come from founder calls).
MODERATION_OUTAGE = ("cap_exceeded", "credential_unavailable", "accounting_unavailable", "sdk_unavailable", "stopped", "model_not_allowed")
# Unbilled provider refusals that concern the observer as a whole (key, credit, permission, rate).
OBSERVER_WIDE_STATUSES = (401, 402, 403, 429)
OBSERVER_CODES = ("invalid_json", "schema", "unknown_code")


def _observer_wide(error):
    """An outage of the observer itself, not a problem of the post being screened."""
    return error.category in MODERATION_OUTAGE or (
        error.category == "provider_rejected" and error.status in OBSERVER_WIDE_STATUSES)


def _observer_code(error):
    """Only the observer's fixed codes reach the log, never text derived from a post."""
    return error.code if error.code in OBSERVER_CODES else "invalid_response"


class Worker:
    def __init__(self, conn, rules, gateway, *, observer_gateway=None, moderation_client=None, model_id=AGENT_MODEL,
                 account=ACCOUNT, max_failures=3, delays=(10, 20, 40), sleep=time.sleep, now=None,
                 purpose=None):
        if purpose is not None and not isinstance(purpose, shared_purpose.Purpose):
            raise shared_purpose.PurposeConfigurationError("invalid_purpose")
        self.purpose = purpose
        self.conn, self.rules, self.gateway = conn, rules, gateway
        self.moderation_client = moderation_client
        self.observer_gateway = observer_gateway  # None: mandatory holds stay private; local safe publication continues.
        self.model_id, self.account, self.max_failures = model_id, account, max_failures
        self.delays, self.sleep = delays, sleep
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.failures = 0
        self.log = []

    def call(self, system, messages, request_id, *, max_output_tokens=MAX_OUTPUT_TOKENS):
        """One model call with retries on HTTP 429, under new request ids."""
        for attempt in range(len(self.delays) + 1):
            try:
                return self.gateway.call(account=self.account, role="agent", model_id=self.model_id, system=system,
                                         messages=messages, max_output_tokens=max_output_tokens,
                                         request_id=request_id + (f":a{attempt}" if attempt else ""))
            except gateway_module.GatewayError as error:
                if error.category == "provider_rejected" and error.status == 429 and attempt < len(self.delays):
                    self.sleep(self.delays[attempt])
                    continue
                raise

    def _failed(self, error):
        """Count a failed call; pause the society when it is no longer safe to go on."""
        if error.category in BAD_REPLY or error.category == "cap_exceeded":
            return False
        self.failures += 1
        if error.category in PAUSE_AT_ONCE or self.failures >= self.max_failures:
            society.set_paused(self.conn, self.rules, True, self.now(), f"worker: {error.category}")
            self.conn.commit()
            return True
        return False

    def turn(self, agent_id, tick):
        eras.require_purpose(self.conn, self.purpose)
        if not society.claim_turn(self.conn, agent_id, tick, self.now()):
            return None
        self.conn.commit()  # the turn is spent even if everything below fails
        try:
            context = society_life.turn_context(self.conn, self.rules, agent_id)
            settings = activity_settings.effective(self.conn, tick) if self.rules.persistent else None
            if settings is not None:
                context['activity_settings'] = settings
                context['rules']['activity_limits_per_hour'] = {
                    key: settings['values'][key] for key in ('post', 'reply', 'like')}
                context['rules'].pop('likes_you_may_give_per_agent_per_day', None)
        except society.SocietyError:
            society_life.fail_turn(self.conn, agent_id, tick, "participation_unavailable")
            self.conn.commit()
            return "failed"
        system, messages = prompts.turn(context, purpose=self.purpose)
        try:
            options = {'max_output_tokens': BATCH_OUTPUT_TOKENS} if settings is not None else {}
            result = self.call(system, messages, f"klados.turn.{agent_id}.{tick}", **options)
        except gateway_module.GatewayError as error:
            code = "budget_exhausted" if error.category == "cap_exceeded" else ("reply_" if error.category in BAD_REPLY else "call_") + error.category
            society_life.fail_turn(self.conn, agent_id, tick, code, charged_microusd=error.charged_microusd)
            self.conn.commit()
            self.log.append({"agent": agent_id, "tick": tick, "error": code})
            if error.category == "cap_exceeded":
                return "financial_rest"
            return "stop" if self._failed(error) else "failed"
        self.failures = 0
        try:
            decision = (decisions.parse_batch(result.text, settings['values']['max_actions'])
                        if settings is not None else decisions.parse_turn(result.text))
        except decisions.DecisionError as error:
            society_life.fail_turn(self.conn, agent_id, tick, "reply_" + error.code,
                                   charged_microusd=result.charged_microusd)
            self.conn.commit()
            self.log.append({"agent": agent_id, "tick": tick, "error": "reply_" + error.code})
            return "failed"
        from arena.mock_provider import MockProvider
        provider = self.gateway.providers.get(self.model_id.split(":", 1)[0])
        origin = ("live" if isinstance(provider, gateway_module.OpenRouterProvider) else
                  "mock" if isinstance(provider, MockProvider) else None)
        if settings is not None:
            founder_batch.save(self.conn, agent_id, tick, decision, settings, result.charged_microusd, origin)
            self.conn.commit()  # Durable decision; a restart never needs to ask the model again.
            outcome = founder_batch.execute(self.conn, self.rules, agent_id, tick, self.now())
            self.conn.commit()
            return outcome
        outcome = society_life.act(self.conn, self.rules, agent_id, tick, decision, self.now(),
                                   charged_microusd=result.charged_microusd, post_origin=origin)
        self.conn.commit()
        self.log.append({"agent": agent_id, "tick": tick, "context": context, "decision": decision,
                         "outcome": outcome, "charged_microusd": result.charged_microusd})
        if "post_id" in outcome and self.screen() == "stop":
            return "stop"
        return "done"

    def screen(self):
        """Review bounded mandatory/optional queues. Never pauses the society.

        Moderation failures are isolated: mandatory risks stay private; optional work
        may be dropped without hiding low-risk content. Failures increment the bounded
        ``screen_failures`` count, and an observer outage
        (spent observer cap, missing key, stop file, disallowed model, or repeated failures) only
        ends this pass's screening without charging the posts a retry; it is recorded as a
        ``moderation_outage`` event, so the next attempt uses a new ledger request id. Only founder inference
        reaches ``_failed`` and its safety pause. Returns "stop" only when a failed screening
        cannot be recorded (database): the pass ends, the society clock is not touched.
        """
        if self.observer_gateway is None:
            return None
        failures = 0
        for post in society_moderation.pending(self.conn, self.now()):
            def record_request(version):
                society.event(self.conn, self.rules, society.clock(self.conn)["tick"],
                              "moderation_request", self.now(), post["author_id"],
                              post_id=post["id"], config_version=version)
                self.conn.commit()

            def hold_signal():
                # A classifier or selected contextual signal changes priority before an
                # Observer call; an exhausted account must not leave that risk public.
                current = self.conn.execute('SELECT status FROM society_posts WHERE id=%s FOR UPDATE',
                                            (post['id'],)).fetchone()
                generation = self.conn.execute("""SELECT coalesce(max(id),0) AS generation FROM society_events
                    WHERE kind IN ('moderation_contested','moderation_retry') AND data->>'post_id'=%s""",
                    (str(post['id']),)).fetchone()['generation']
                if generation != post.get('screening_generation',0):
                    return False
                if not current or current['status']!='published':
                    return False
                self.conn.execute("UPDATE society_posts SET status='pending',flagged=true WHERE id=%s",(post['id'],))
                from arena.institutions import on_text_status
                on_text_status(self.conn, self.rules, post['id'], 'pending', self.now())
                society.event(self.conn,self.rules,society.clock(self.conn)['tick'],'moderation_contested',self.now(),
                              post_id=post['id'],source='contextual_review_signal')
                post['screening_generation'] = self.conn.execute("""SELECT max(id) AS generation FROM society_events
                    WHERE kind='moderation_contested' AND data->>'post_id'=%s""",(str(post['id']),)).fetchone()['generation']
                society.event(self.conn,self.rules,society.clock(self.conn)['tick'],'risk_hold',self.now(),
                              post_id=post['id'],source='contextual_review_signal')
                self.conn.commit()
                return self._observe_request_id(post)

            kind = None
            try:
                request_id = self._observe_request_id(post)
                client = self.moderation_client or moderation.client_for_gateway(self.observer_gateway)
                if society_moderation.risk_review(self.conn,post['id']):
                    kind = 'optional' if post.get('review_status')=='published' else 'mandatory'
                # The classifier audit is committed before submission. Verdict application
                # and its metadata remain atomic; any failed review is rolled back below.
                verdict = moderation.review(self.observer_gateway, client, post["text"], post["replied_text"],
                    model_id=OBSERVER_MODEL, account=OBSERVER_ACCOUNT, contested=post["contested"],
                    on_request=record_request, request_id=request_id, review_kind=kind, on_risk=hold_signal,
                    review_requirement=post.get('review_requirement'))
                verdict["screening_generation"] = post["screening_generation"]
                with self.conn.transaction():
                    outcome = society_moderation.apply_verdict(self.conn, self.rules, post["id"], verdict, self.now())
                    if kind=='optional' and verdict.get('matches')==['optional_review_not_available'] and outcome:
                        society.event(self.conn,self.rules,society.clock(self.conn)['tick'],'moderation_optional_dropped',
                                      self.now(),post_id=post['id'],reason='optional_mode_unavailable')
                    record = {"post": post["id"], "moderation": outcome, "codes": verdict["codes"],
                              "by": verdict["by"], "charged_microusd": verdict["charged_microusd"]}
                self.conn.commit()
            except gateway_module.GatewayError as error:
                self.conn.rollback()
                self.log.append({"post": post["id"], "error": "observer call_" + error.category})
                outage = _observer_wide(error)
                if error.category=='duplicate_request' and kind is not None:
                    # A prior paid attempt has an uncertain durable outcome. Never
                    # invent a fresh paid ID to recover it: human review or optional drop.
                    try:
                        society_moderation.escalate(self.conn,self.rules,post['id'],self.now())
                        self.conn.commit()
                    except Exception:
                        self.conn.rollback()
                        self.log.append({'post':post['id'],'error':'screening_persistence_failure'})
                        return 'stop'
                    continue
                # An outage is not the post's fault: it keeps its retries, and the next attempt
                # gets a new request id (the ledger never accepts a used one, even if released).
                if not (self._observer_outage(post, error.category) if outage else self._screen_failed(post["id"])):
                    return "stop"
                failures += error.category not in BAD_REPLY
                if outage or failures >= self.max_failures:
                    self.log.append({"moderation": "halted_for_this_pass", "reason": error.category})
                    return None
                continue
            except moderation.ModerationError as error:
                self.conn.rollback()
                code = str(error) if str(error) in ("invalid_config", "invalid_response", "service_unavailable", "credential_unavailable") else "invalid_response"
                self.log.append({"post": post["id"], "error": "moderation_" + code})
                if code in ("service_unavailable", "credential_unavailable", "invalid_config"):
                    if not self._observer_outage(post, "moderation_" + code):
                        return "stop"
                    return None
                if not self._screen_failed(post["id"]):
                    return "stop"
                continue
            except observer.ObserverError as error:
                self.conn.rollback()
                self.log.append({"post": post["id"], "error": "observer reply_" + _observer_code(error),
                                 "charged_microusd": getattr(error, "charged_microusd", 0)})
                if not self._screen_failed(post["id"]):
                    return "stop"
                continue
            except Exception:  # one post must never stop the society; never log its text
                self.conn.rollback()
                self.log.append({"post": post["id"], "error": "screening_unexpected"})
                if not self._screen_failed(post["id"]):
                    return "stop"
                continue
            self.log.append(record)
        return None

    def _observe_request_id(self, post):
        """One ledger request id per screening attempt, stable across a crash and restart.

        Every failed attempt advances it: a post failure through ``screen_failures``, an observer
        outage through a recorded ``moderation_outage`` event. A crash after a paid call reuses the
        id, so the ledger refuses a second paid call (``duplicate_request`` is a post failure).
        """
        outages = self.conn.execute(
            "SELECT count(*) AS n FROM society_events WHERE kind = 'moderation_outage' AND data->>'post_id' = %s",
            (str(post["id"]),)).fetchone()["n"]
        suffix = f".o{outages}" if outages else ""
        generation = post.get("screening_generation", 0)
        prefix = f"klados.observe.{post['id']}"
        if generation:
            prefix += f".g{generation}"
        return f"{prefix}.{post['screen_failures']}{suffix}"

    def _observer_outage(self, post, category):
        """Record an observer outage for the post (no retry penalty). False if it cannot be recorded."""
        try:
            with self.conn.transaction():
                society.event(self.conn, self.rules, society.clock(self.conn)["tick"], "moderation_outage",
                              self.now(), post_id=post["id"], category=category)
                changed=self.conn.execute('''UPDATE society_findings SET metadata=metadata || %s
                    WHERE id=(SELECT max(id) FROM society_findings WHERE post_id=%s)
                    AND (metadata->>'async_pending'='true' OR EXISTS(SELECT 1 FROM society_posts p WHERE p.id=post_id AND p.status='pending')) RETURNING id''',
                    (Jsonb(dict(review_status='skipped',review_performed='none',review_skip_reason=category)),post['id'])).fetchone()
                if changed and society_moderation.risk_review(self.conn, post['id']):
                    count = self.conn.execute("""SELECT count(*) AS n FROM society_events
                        WHERE kind='moderation_outage' AND data->>'post_id'=%s AND id>%s""",
                        (str(post['id']),post.get('screening_generation',0))).fetchone()['n']
                    if count >= society_moderation.MAX_SCREEN_FAILURES:
                        society_moderation.escalate(self.conn, self.rules, post['id'], self.now())
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            self.log.append({"post": post["id"], "error": "screening_persistence_failure"})
            return False

    def _screen_failed(self, post_id):
        """Record one failed screening. False when the database cannot record it."""
        try:
            with self.conn.transaction():
                self.conn.execute('''UPDATE society_findings SET metadata=metadata || %s
                    WHERE id=(SELECT max(id) FROM society_findings WHERE post_id=%s)
                    AND (metadata->>'async_pending'='true' OR EXISTS(SELECT 1 FROM society_posts p WHERE p.id=post_id AND p.status='pending'))''',
                    (Jsonb(dict(review_status='skipped',review_performed='none',review_skip_reason='review_failed')),post_id))
                society_moderation.screen_failed(self.conn, post_id)
                row = self.conn.execute('SELECT screen_failures FROM society_posts WHERE id=%s', (post_id,)).fetchone()
                if (row and row['screen_failures'] >= society_moderation.MAX_SCREEN_FAILURES
                        and society_moderation.risk_review(self.conn, post_id)):
                    society_moderation.escalate(self.conn, self.rules, post_id, self.now())
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            self.log.append({"post": post_id, "error": "screening_persistence_failure"})
            return False

    def reflect(self, agent_id, day, tick):
        eras.require_purpose(self.conn, self.purpose)
        from arena import administration
        if not administration.is_active(self.conn, agent_id, lock=True):
            return None
        try:
            context = society_life.reflection_context(self.conn, self.rules, agent_id, day)
        except society.SocietyError:
            return None
        system, messages = prompts.reflection(context, purpose=self.purpose)
        try:
            result = self.call(system, messages, f"klados.reflect.{agent_id}.{day}")
        except gateway_module.GatewayError as error:
            society_life.record_reflection(self.conn, agent_id, day, tick, None, self.now(),
                                           charged_microusd=error.charged_microusd, error="budget_exhausted" if error.category == "cap_exceeded" else "call_" + error.category)
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
                code = "reply_" + error.code if isinstance(error, decisions.DecisionError) else (
                    "budget_exhausted" if error.category == "cap_exceeded" else "call_" + error.category)
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
        if self.rules.persistent:
            return None
        if self.observer_gateway is None:  # nothing may be published unscreened
            return None
        for fossil in society_death.pending(self.conn):
            agent_id = fossil["agent_id"]
            system, messages = prompts.last_words(society_death.context(self.conn, self.rules, agent_id),
                                                 purpose=self.purpose)
            request = f"klados.lastwords.{agent_id}.{fossil['last_words_attempts']}"
            try:
                result = self.call(system, messages, request)
                text = decisions.parse_last_words(result.text)
            except gateway_module.GatewayError as error:
                society_death.failed(self.conn, agent_id)
                self.conn.commit()
                self.log.append({"agent": agent_id, "error": "last words call_" + error.category})
                if self._failed(error):
                    return "stop"
                continue
            except decisions.DecisionError as error:
                society_death.failed(self.conn, agent_id)
                self.conn.commit()
                self.log.append({"agent": agent_id, "error": "last words reply_" + error.code})
                continue
            self.failures = 0
            try:  # moderation of the last words: a failure stays with this fossil, never a pause
                with self.conn.transaction():
                    verdict = observer.review(self.observer_gateway, text, model_id=OBSERVER_MODEL,
                                              account=OBSERVER_ACCOUNT, request_id=request + ".observe")
                    outcome = society_death.record(self.conn, self.rules, agent_id, text, verdict, self.now())
                    record = {"agent": agent_id, "last_words": outcome, "codes": verdict["codes"]}
                self.conn.commit()
            except Exception as error:
                code = (error.category if isinstance(error, gateway_module.GatewayError) else
                        _observer_code(error) if isinstance(error, observer.ObserverError) else "unexpected")
                self.log.append({"agent": agent_id, "error": "last words observer_" + code})
                with self.conn.transaction():
                    society_death.failed(self.conn, agent_id)
                self.conn.commit()
                continue
            self.log.append(record)
        return None

    def run(self):
        """One pass: every due turn, then every due reflection. Returns a summary."""
        summary = {"turns": 0, "failed": 0, "reflections": 0, "stopped": False}
        if society.clock(self.conn)["paused"]:
            return dict(summary, stopped=True)
        if self.screen() == "stop":  # posts left from an earlier pass
            return dict(summary, stopped=True)
        tick = society.clock(self.conn)["tick"]
        if self.rules.persistent:
            for agent_id, previous_tick in founder_batch.recoverable(self.conn):
                recovered = founder_batch.execute(self.conn, self.rules, agent_id, previous_tick, self.now())
                self.conn.commit()
                if recovered is not None:
                    summary['turns' if recovered == 'done' else 'failed'] += 1
        for agent_id in society.due_turns(self.conn, self.now()):
            result = self.turn(agent_id, tick)
            if result == "stop":
                return dict(summary, stopped=True)
            if result == "financial_rest":
                summary["financial_rests"] = summary.get("financial_rests", 0) + 1
            elif result is not None:
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
    mode = environ.get("KLADOS_AI_MODE")
    if mode == "mock":
        from arena.mock_provider import MockProvider
        return gateway_module.Gateway(ledger=ledger, models=models,
                                      providers={"openrouter": MockProvider()})
    if mode != "live":
        raise ValueError("KLADOS_AI_MODE must be explicitly set to mock or live")
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
    purpose = shared_purpose.from_environ()
    book = open_ledger(args.ledger, args.create_cap_usd)
    gateway, watcher = build_gateway(book), build_gateway(book, role="observer")
    with db.connect(dsn) as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (eras.SCHEDULER_LOCK,)).fetchone()["ok"]:
            raise society.SocietyError("another_worker_is_running")
        if not society.clock(conn)["paused"]:
            eras.require_purpose(conn, purpose)
        summary = Worker(conn, rules, gateway, observer_gateway=watcher, purpose=purpose).run()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
