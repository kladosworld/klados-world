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
KINDS = ("open", "reserve", "settle", "release")
MAX_MICROUSD = 1_000_000_000_000  # 1,000,000 USD: far above any sane cap; catches unit errors.


class LedgerError(ValueError):
    """Invalid ledger operation; nothing was written."""


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


def summarize(document):
    """Derive caps, charges and open reservations from the event list."""
    accounts, reservations, requests = {}, {}, set()
    for event in document["events"]:
        kind = event["kind"]
        if kind == "open":
            accounts[event["account"]] = {"cap": event["microusd"], "charged": 0, "reserved": 0}
        elif kind == "reserve":
            account = accounts[event["account"]]
            account["reserved"] += event["microusd"]
            reservations[event["reservation"]] = dict(event, open=True)
            requests.add(event["request_id"])
        else:
            reservation = reservations[event["reservation"]]
            account = accounts[reservation["account"]]
            account["reserved"] -= reservation["microusd"]
            account["charged"] += event["microusd"]
            reservation["open"] = False
    total_charged = sum(a["charged"] for a in accounts.values())
    total_reserved = sum(a["reserved"] for a in accounts.values())
    return {"accounts": accounts, "reservations": reservations, "requests": requests,
            "charged": total_charged, "reserved": total_reserved}


def validate(document):
    """Reject any document whose event stream is not internally consistent."""
    if (not isinstance(document, dict) or document.get("format") != FORMAT
            or not isinstance(document.get("events"), list)):
        raise LedgerError("Not a TEE Arena ledger.")
    global_cap = document.get("global_cap")
    if global_cap is not None:
        _amount(global_cap)
    ids, accounts, reservations, requests = set(), set(), {}, set()
    for event in document["events"]:
        if not isinstance(event, dict) or event.get("kind") not in KINDS or event.get("id") in ids:
            raise LedgerError("Invalid or duplicate ledger event.")
        ids.add(event["id"])
        if event["kind"] == "open":
            _account(event.get("account"))
            if event["account"] in accounts:
                raise LedgerError("Account opened twice.")
            _amount(event.get("microusd"))
            accounts.add(event["account"])
        elif event["kind"] == "reserve":
            if event.get("account") not in accounts or event.get("reservation") in reservations:
                raise LedgerError("Reservation on an unknown account, or reused reservation id.")
            if not isinstance(event.get("request_id"), str) or event["request_id"] in requests:
                raise LedgerError("Missing or duplicate request id.")
            _amount(event.get("microusd"))
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
            event = build(document, summarize(document))
            event.update(id="event:" + uuid4().hex, at=_now())
            document["events"].append(event)
            result.update(event)

        store.update(self.path, change, validate=validate)
        return result

    def open_account(self, account, cap_microusd, *, note=""):
        _account(account)
        _amount(cap_microusd)

        def build(document, summary):
            if account in summary["accounts"]:
                raise LedgerError("Account already open.")
            return {"kind": "open", "account": account, "microusd": cap_microusd, "note": str(note)[:500]}

        self._append(build)

    def reserve(self, account, microusd, request_id, *, note=""):
        """Reserve ``microusd`` for one request. Returns the reservation id."""
        _amount(microusd)
        if not isinstance(request_id, str) or not REQUEST.fullmatch(request_id):
            raise LedgerError("Invalid request id.")

        def build(document, summary):
            if account not in summary["accounts"]:
                raise LedgerError("Unknown account.")
            if request_id in summary["requests"]:
                raise DuplicateRequest(f"Request {request_id} was already reserved.")
            state = summary["accounts"][account]
            available = state["cap"] - state["charged"] - state["reserved"]
            if microusd > available:
                raise CapExceeded(account, max(0, available), microusd)
            if document["global_cap"] is not None:
                remaining = document["global_cap"] - summary["charged"] - summary["reserved"]
                if microusd > remaining:
                    raise CapExceeded("global", max(0, remaining), microusd)
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

        def build(document, summary):
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

        def build(document, summary):
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
        return {"global_cap": document["global_cap"], "charged": summary["charged"], "reserved": summary["reserved"]}
