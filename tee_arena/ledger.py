"""Energy accounting, as integer microdollar reservations.

Follows the reservation pattern of the operational ledger in TEE-internal: reserve a
conservative estimate before a model call, settle it afterwards, and keep the full
reservation when the real usage is missing or the call may have been billed.

- Every account (an agent, or a role such as ``observer``) has a cap for the period
  it was opened for. An optional global cap bounds all accounts together.
- Events are append-only. Balances are derived from them, never stored.
- A reservation that would exceed any cap is refused before anything is spent.
- A request id can be reserved only once, so a failed call is never replayed under
  the same id by accident.

All amounts are integers in microdollars (1 USD = 1,000,000).
"""
from datetime import datetime, timezone
from pathlib import Path
import re
from uuid import uuid4

from tee_arena import store

FORMAT = "tee-arena-ledger-v1"
ACCOUNT = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,79}")
REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}")
KINDS = ("open", "reserve", "settle", "release", "budget", "cap")
MAX_MICROUSD = 1_000_000_000_000  # 1,000,000 USD: far above any sane cap; catches unit errors.


class LedgerError(ValueError):
    """Invalid ledger operation; nothing was written."""


class UnknownAccount(LedgerError):
    """The caller selected an account that does not exist."""


class CapExceeded(LedgerError):
    """The reservation would exceed an account or global cap; nothing was written."""

    def __init__(self, scope, available, requested):
        self.scope, self.available, self.requested = scope, available, requested
        super().__init__(f"{scope} cap exceeded: {requested} requested, {available} available (microusd).")


class DuplicateRequest(LedgerError):
    """This request id was already reserved; calls are never replayed."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _amount(value, *, allow_zero=False):
    if type(value) is not int or value < (0 if allow_zero else 1) or value > MAX_MICROUSD:
        raise LedgerError("Amounts must be positive integer microdollars.")
    return value


def _account(value):
    if not isinstance(value, str) or not ACCOUNT.fullmatch(value):
        raise LedgerError("Invalid account name.")
    return value


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise LedgerError("Invalid UTC accounting timestamp.") from None


def _month(value):
    return _timestamp(value).strftime("%Y-%m")


def _checked_clock(document, at):
    now = _timestamp(at)
    if any(_timestamp(event.get("at")) > now for event in document["events"]):
        raise LedgerError("Accounting clock is behind the event history.")
    return _month(at)


def _active_budget(summary, month):
    eligible = [event for event in summary["budgets"] if event["month"] <= month]
    return max(enumerate(eligible), key=lambda item: (item[1]["month"], item[0]))[1] if eligible else None


def summarize(document):
    """Derive lifetime and calendar-month use without rewriting event history."""
    accounts, reservations, requests, months, budgets = {}, {}, set(), {}, []
    global_cap, last_cap = document["global_cap"], None
    for event in document["events"]:
        kind = event["kind"]
        if kind == "open":
            accounts[event["account"]] = {"cap": event["microusd"], "charged": 0, "reserved": 0}
        elif kind == "budget":
            budgets.append(event)
        elif kind == "cap":
            if event["account"] == "global":
                global_cap = event["microusd"]
            else:
                accounts[event["account"]]["cap"] = event["microusd"]
            last_cap = event
        elif kind == "reserve":
            month = _month(event["at"])
            monthly = months.setdefault(month, {"charged": 0, "reserved": 0})
            monthly["reserved"] += event["microusd"]
            accounts[event["account"]]["reserved"] += event["microusd"]
            reservations[event["reservation"]] = dict(event, open=True, month=month)
            requests.add(event["request_id"])
        else:
            reservation = reservations[event["reservation"]]
            account = accounts[reservation["account"]]
            account["reserved"] -= reservation["microusd"]
            account["charged"] += event["microusd"]
            monthly = months[reservation["month"]]
            monthly["reserved"] -= reservation["microusd"]
            monthly["charged"] += event["microusd"]
            reservation["open"] = False
    return {"accounts": accounts, "reservations": reservations, "requests": requests,
            "charged": sum(a["charged"] for a in accounts.values()),
            "reserved": sum(a["reserved"] for a in accounts.values()),
            "global_cap": global_cap, "months": months, "budgets": budgets, "last_cap": last_cap}


def _approval(event):
    for key in ("approved_by", "reference"):
        value = event.get(key)
        if (not isinstance(value, str) or not value.strip() or len(value) > 200
                or any(ord(char) < 32 for char in value)):
            raise LedgerError("Budget changes require an approval reference and actor label.")
    month = event.get("month")
    if not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month) or month.startswith("0000"):
        raise LedgerError("Invalid effective month.")
    _timestamp(event.get("at"))


def validate(document):
    """Reject any document whose event stream is not internally consistent."""
    if (not isinstance(document, dict) or document.get("format") != FORMAT
            or "global_cap" not in document or not isinstance(document.get("events"), list)):
        raise LedgerError("Not a TEE Arena ledger.")
    global_cap = document.get("global_cap")
    if global_cap is not None:
        _amount(global_cap)
    ids, accounts, reservations, requests = set(), set(), {}, set()
    for event in document["events"]:
        if not isinstance(event, dict) or event.get("kind") not in KINDS or event.get("id") in ids:
            raise LedgerError("Invalid or duplicate ledger event.")
        ids.add(event["id"])
        if event["kind"] in ("budget", "cap"):
            _approval(event)
            _amount(event.get("microusd"), allow_zero=True)
            if event["kind"] == "budget":
                warning = event.get("warning_microusd")
                _amount(warning, allow_zero=True)
                if warning > event["microusd"]:
                    raise LedgerError("Warning exceeds monthly ceiling.")
            elif event.get("account") != "global" and event.get("account") not in accounts:
                raise LedgerError("Unknown cap scope.")
        elif event["kind"] == "open":
            _account(event.get("account"))
            if event["account"] in accounts:
                raise LedgerError("Account opened twice.")
            _amount(event.get("microusd"), allow_zero=True)
            if "reference" in event or "approved_by" in event:
                _approval(event)
            accounts.add(event["account"])
        elif event["kind"] == "reserve":
            if event.get("account") not in accounts or event.get("reservation") in reservations:
                raise LedgerError("Reservation on an unknown account, or reused reservation id.")
            if not isinstance(event.get("request_id"), str) or event["request_id"] in requests:
                raise LedgerError("Missing or duplicate request id.")
            _amount(event.get("microusd"))
            _timestamp(event.get("at"))
            reservations[event["reservation"]] = "open"
            requests.add(event["request_id"])
        else:
            if reservations.get(event.get("reservation")) != "open":
                raise LedgerError("Settlement of an unknown or closed reservation.")
            _amount(event.get("microusd"), allow_zero=True)
            reservations[event["reservation"]] = "closed"
    summary = summarize(document)
    for name, account in summary["accounts"].items():
        if account["reserved"] < 0:
            raise LedgerError(f"Negative reservations on {name}.")


class Ledger:
    """One ledger document on disk. Safe for concurrent use across processes."""

    def __init__(self, path):
        self.path = Path(path).resolve()

    @classmethod
    def create(cls, path, *, global_cap=None):
        document = {"format": FORMAT, "global_cap": global_cap, "events": []}
        store.create(path, document, validate=validate)
        return cls(path)

    def _append(self, build):
        """Run ``build(document, summary)`` under the lock; it returns the new event."""
        result = {}

        def change(document):
            validate(document)
            at = _now()
            event = build(document, summarize(document), at)
            event.update(id="event:" + uuid4().hex, at=at)
            document["events"].append(event)
            result.update(event)

        store.update(self.path, change, validate=validate)
        return result

    def open_account(self, account, cap_microusd, *, note=""):
        _account(account)
        _amount(cap_microusd)

        def build(document, summary, at):
            if account in summary["accounts"]:
                raise LedgerError("Account already open.")
            return {"kind": "open", "account": account, "microusd": cap_microusd, "note": str(note)[:500]}

        self._append(build)

    def reserve(self, account, microusd, request_id, *, note=""):
        """Reserve ``microusd`` for one request. Returns the reservation id."""
        _amount(microusd)
        if not isinstance(request_id, str) or not REQUEST.fullmatch(request_id):
            raise LedgerError("Invalid request id.")

        def build(document, summary, at):
            if account not in summary["accounts"]:
                raise UnknownAccount("Unknown account.")
            if request_id in summary["requests"]:
                raise DuplicateRequest(f"Request {request_id} was already reserved.")
            state = summary["accounts"][account]
            available = state["cap"] - state["charged"] - state["reserved"]
            if microusd > available:
                raise CapExceeded(account, max(0, available), microusd)
            if summary["global_cap"] is not None:
                remaining = summary["global_cap"] - summary["charged"] - summary["reserved"]
                if microusd > remaining:
                    raise CapExceeded("global", max(0, remaining), microusd)
            month = _checked_clock(document, at)
            budget = _active_budget(summary, month)
            if budget is not None:
                use = summary["months"].get(month, {"charged": 0, "reserved": 0})
                remaining = budget["microusd"] - use["charged"] - use["reserved"]
                if microusd > remaining:
                    raise CapExceeded("monthly", max(0, remaining), microusd)
            return {"kind": "reserve", "account": account, "microusd": microusd, "request_id": request_id,
                    "reservation": "reservation:" + uuid4().hex, "note": str(note)[:500]}

        return self._append(build)["reservation"]

    def settle(self, reservation, actual_microusd=None, *, note=""):
        """Close a reservation with the real cost.

        ``None`` means the real cost is unknown: the full reservation is charged. A
        real cost above the reservation is charged in full and flagged as an overrun.
        """
        if actual_microusd is not None:
            _amount(actual_microusd, allow_zero=True)

        def build(document, summary, at):
            held = summary["reservations"].get(reservation)
            if held is None or not held["open"]:
                raise LedgerError("Unknown or already closed reservation.")
            charge = held["microusd"] if actual_microusd is None else actual_microusd
            return {"kind": "settle", "reservation": reservation, "microusd": charge,
                    "usage_known": actual_microusd is not None,
                    "overrun": actual_microusd is not None and actual_microusd > held["microusd"],
                    "note": str(note)[:500]}

        return self._append(build)["microusd"]

    def release(self, reservation, *, note=""):
        """Close a reservation at zero cost. Only for requests never sent."""

        def build(document, summary, at):
            held = summary["reservations"].get(reservation)
            if held is None or not held["open"]:
                raise LedgerError("Unknown or already closed reservation.")
            return {"kind": "release", "reservation": reservation, "microusd": 0, "note": str(note)[:500]}

        self._append(build)

    def balance(self, account):
        """Return ``{"cap", "charged", "reserved", "available"}`` for one account."""
        _, document = store.read(self.path)
        validate(document)
        state = summarize(document)["accounts"].get(account)
        if state is None:
            raise LedgerError("Unknown account.")
        return dict(state, available=state["cap"] - state["charged"] - state["reserved"])

    def totals(self):
        _, document = store.read(self.path)
        validate(document)
        summary = summarize(document)
        return {"global_cap": summary["global_cap"], "charged": summary["charged"], "reserved": summary["reserved"]}


    def set_budget(self, microusd, *, reference, approved_by, warning_microusd=None,
                   apply=False, approved=False):
        """Preview by default. Appending policy requires explicit operator approval."""
        _amount(microusd, allow_zero=True)
        warning = microusd * 7 // 10 if warning_microusd is None else warning_microusd
        def build(document, summary, at):
            month = _checked_clock(document, at)
            event = dict(kind="budget", microusd=microusd, warning_microusd=warning,
                         month=month, reference=reference, approved_by=approved_by, at=at)
            _approval(event)
            _amount(warning, allow_zero=True)
            if warning > microusd:
                raise LedgerError("Warning exceeds monthly ceiling.")
            current = _active_budget(summary, month)
            increasing = current is None or microusd > current["microusd"] or warning > current["warning_microusd"]
            if apply and increasing and approved is not True:
                raise LedgerError("Budget increase requires explicit approval.")
            return event
        return self._policy_change(build, apply)

    def raise_cap(self, microusd, *, reference, approved_by, account="global", apply=False, approved=False):
        """Raise a specific lifetime ceiling without replacing any historical event."""
        _amount(microusd)
        def build(document, summary, at):
            month = _checked_clock(document, at)
            if account != "global" and account not in summary["accounts"]:
                raise LedgerError("Unknown cap scope.")
            current = summary["global_cap"] if account == "global" else summary["accounts"][account]["cap"]
            if current is None or microusd <= current:
                raise LedgerError("New lifetime ceiling must exceed its current value.")
            if apply and approved is not True:
                raise LedgerError("Cap increase requires explicit approval.")
            event = dict(kind="cap", account=account, microusd=microusd, month=month,
                         reference=reference, approved_by=approved_by, at=at)
            _approval(event)
            return event
        return self._policy_change(build, apply)

    def lower_cap(self, microusd, *, reference, approved_by, account="global", apply=False, approved=False):
        """Lower a ceiling atomically, never below charged plus committed reservations."""
        _amount(microusd, allow_zero=True)
        def build(document, summary, at):
            month = _checked_clock(document, at)
            if account != "global" and account not in summary["accounts"]:
                raise LedgerError("Unknown cap scope.")
            state = summary if account == "global" else summary["accounts"][account]
            current = summary["global_cap"] if account == "global" else state["cap"]
            if current is not None and microusd >= current:
                raise LedgerError("New lifetime ceiling must be lower than its current value.")
            if microusd < state["charged"] + state["reserved"]:
                raise LedgerError("Ceiling cannot be below recorded spending and reservations.")
            if apply and approved is not True:
                raise LedgerError("Cap change requires explicit approval.")
            event = dict(kind="cap", account=account, microusd=microusd, month=month,
                         reference=reference, approved_by=approved_by, at=at)
            _approval(event)
            return event
        return self._policy_change(build, apply)

    def approved_open_account(self, account, microusd, *, reference, approved_by, apply=False, approved=False):
        """Open an audited sub-limit. This never increases the global ceiling."""
        _account(account)
        _amount(microusd, allow_zero=True)
        def build(document, summary, at):
            if account == "global" or account in summary["accounts"]:
                raise LedgerError("Account is reserved or already open.")
            if apply and approved is not True:
                raise LedgerError("Opening an account requires explicit approval.")
            event = dict(kind="open", account=account, microusd=microusd,
                         month=_checked_clock(document, at), reference=reference,
                         approved_by=approved_by, at=at)
            _approval(event)
            return event
        return self._policy_change(build, apply)

    def _policy_change(self, build, apply):
        if type(apply) is not bool:
            raise LedgerError("Apply must be an explicit boolean.")
        if apply:
            return self._append(build)
        # Previews use the same validation and calculations, without writing or creating a ledger.
        _, document = store.read(self.path)
        validate(document)
        return dict(build(document, summarize(document), _now()), preview=True)

    def budget_status(self):
        """Sanitized aggregate contract for the continuous-operation guard."""
        _, document = store.read(self.path)
        validate(document)
        at = _now()
        month = _checked_clock(document, at)
        summary = summarize(document)
        budget = _active_budget(summary, month)
        use = summary["months"].get(month, {"charged": 0, "reserved": 0})
        ceiling = budget["microusd"] if budget else None
        remaining = None if ceiling is None else max(0, ceiling - use["charged"] - use["reserved"])
        def policy(event):
            return {key: event[key] for key in ("id", "kind", "at", "month", "microusd", "reference", "approved_by", "account") if key in event} if event else None
        accounts = {name: {"cap_microusd": state["cap"],
                           "charged_microusd": state["charged"],
                           "reserved_microusd": state["reserved"]}
                    for name, state in summary["accounts"].items()}
        return {"schema": 1, "as_of": at, "month": month, "monthly_cap_microusd": ceiling,
                "accounts": accounts, "events": len(document["events"]),
                "last_event_id": document["events"][-1]["id"] if document["events"] else None,
                "charged_microusd": use["charged"], "reserved_microusd": use["reserved"],
                "available_microusd": remaining,
                "warning_microusd": budget["warning_microusd"] if budget else None,
                "warning": bool(budget and use["charged"] + use["reserved"] >= budget["warning_microusd"]),
                "lifetime_cap_microusd": summary["global_cap"],
                "lifetime_charged_microusd": summary["charged"],
                "lifetime_reserved_microusd": summary["reserved"],
                "lifetime_available_microusd": None if summary["global_cap"] is None else max(0, summary["global_cap"] - summary["charged"] - summary["reserved"]),
                "pending_by_month": {period: values["reserved"] for period, values in summary["months"].items() if values["reserved"]},
                "last_budget": policy(budget), "last_cap": policy(summary["last_cap"])}
