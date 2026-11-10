"""Read-only analysis of exported pilot evidence. Outputs allowlisted aggregates."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

FOUNDERS = ("alder", "birch", "cedar", "hazel")
MAX_FILE_BYTES = 64 * 1024 * 1024


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid count")
    return value


def read_document(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Invalid input file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Input too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Invalid document")
    return result


def ledger_window(before, after):
    """Inspect an append-only ledger snapshot pair without changing or settling it."""
    if before.get("format") != "tee-arena-ledger-v1" or after.get("format") != before["format"]:
        raise ValueError("Invalid ledger format")
    first, last = before["events"], after["events"]
    if not isinstance(first, list) or not isinstance(last, list) or last[: len(first)] != first:
        raise ValueError("Ledger prefix changed")
    reservations, settled = {}, set()
    requests, charged, released, unknown = {}, 0, 0, 0
    for index, event in enumerate(last):
        if index == len(first) and set(reservations) - settled:
            raise ValueError("Pilot starts with an unsettled request")
        kind = event["kind"]
        amount = integer(event["microusd"])
        if kind == "open":
            continue
        reservation = event["reservation"]
        if kind == "reserve":
            if reservation in reservations:
                raise ValueError("Duplicate reservation")
            reservations[reservation] = (index, event["request_id"], amount)
        elif kind in ("settle", "release"):
            if reservation not in reservations or reservation in settled:
                raise ValueError("Invalid settlement")
            settled.add(reservation)
            original_index, request, reserved = reservations[reservation]
            if index >= len(first):
                # A pre-existing in-flight request cannot be assigned to this pilot safely.
                if original_index < len(first):
                    raise ValueError("Pilot starts with an unsettled request")
                if request in requests:
                    raise ValueError("Duplicate request")
                requests[request] = amount if kind == "settle" else 0
                charged += amount if kind == "settle" else 0
                released += kind == "release"
                if kind == "settle":
                    unknown += event.get("usage_known") is not True
        else:
            raise ValueError("Invalid event kind")
    if any(
        index < len(first) and key not in settled for key, (index, _, _) in reservations.items()
    ):
        raise ValueError("Pilot starts with an unsettled request")
    pending = sum(
        1
        for key, (index, _, _) in reservations.items()
        if index >= len(first) and key not in settled
    )
    return dict(
        charged_microusd=charged,
        pending_requests=pending,
        settled_requests=len(requests),
        released_requests=released,
        unknown_usage_requests=unknown,
    ), requests


def analyze(evidence, *, before=None, after=None, provider=None):
    if type(evidence.get("schema_version")) is not int or evidence["schema_version"] != 1:
        raise ValueError("Invalid evidence schema")
    start, end = integer(evidence["start_tick"]), integer(evidence["end_tick"])
    if end < start:
        raise ValueError("Invalid tick range")
    rows = {
        agent: dict(turns=0, actions=Counter(), failed=0, reflections=Counter(), charged_microusd=0)
        for agent in FOUNDERS
    }
    seen = set()
    for turn in evidence["turns"]:
        agent, tick = turn["agent_id"], integer(turn["tick"])
        if agent not in rows or not start <= tick <= end or (agent, tick) in seen:
            raise ValueError("Invalid turn coverage")
        seen.add((agent, tick))
        action, status = turn["action"], turn["status"]
        if action not in ("post", "reply", "like", "rest", None) or status not in (
            "claimed",
            "done",
            "failed",
        ):
            raise ValueError("Invalid turn")
        row = rows[agent]
        row["turns"] += 1
        row["actions"][action or "unfinished"] += 1
        row["failed"] += status == "failed"
        row["charged_microusd"] += integer(turn.get("charged_microusd", 0))
    reflected = set()
    for reflection in evidence["reflections"]:
        agent, day, outcome = (
            reflection["agent_id"],
            integer(reflection["day"]),
            reflection["outcome"],
        )
        if (
            agent not in rows
            or (agent, day) in reflected
            or outcome not in ("kept", "changed", "failed")
        ):
            raise ValueError("Invalid reflection")
        reflected.add((agent, day))
        rows[agent]["reflections"][outcome] += 1
        rows[agent]["charged_microusd"] += integer(reflection.get("charged_microusd", 0))
    verdicts, observer_cost = Counter(), 0
    for finding in evidence["findings"]:
        severity = finding["severity"]
        if severity not in ("none", "light", "grave"):
            raise ValueError("Invalid verdict")
        verdicts[severity] += 1
        observer_cost += integer(finding.get("charged_microusd", 0))
    expected = (end - start + 1) * len(FOUNDERS)
    result = {
        "schema_version": 1,
        "ticks": {"start": start, "end": end},
        "founders": rows,
        "coverage": {
            "turns": len(seen),
            "expected_turns": expected,
            "missing_turns": expected - len(seen),
        },
        "observer": {"verdicts": dict(verdicts), "charged_microusd": observer_cost},
        "reconciliation": {"status": "unavailable"},
    }
    if (before is None) != (after is None):
        raise ValueError("Both ledger snapshots are required")
    if before is not None:
        ledger, requests = ledger_window(before, after)
        recorded = observer_cost + sum(row["charged_microusd"] for row in rows.values())
        result["ledger"] = ledger
        result["reconciliation"] = {
            "status": "incomplete" if ledger["pending_requests"] else "compared",
            "recorded_microusd": recorded,
            "ledger_minus_recorded_microusd": ledger["charged_microusd"] - recorded,
            "provider_status": "unavailable",
        }
        if provider is not None:
            if type(provider.get("schema_version")) is not int or provider["schema_version"] != 1:
                raise ValueError("Invalid provider schema")
            usage = {}
            for row in provider["requests"]:
                identifier = row["request_id"]
                if identifier in usage:
                    raise ValueError("Duplicate provider request")
                usage[identifier] = integer(row["charged_microusd"])
            matched = set(requests) & set(usage)
            missing = len(set(requests) - set(usage))
            unmatched = len(set(usage) - set(requests))
            result["reconciliation"].update(
                provider_status="compared" if not (missing or unmatched) else "incomplete",
                provider_missing_requests=missing,
                provider_unmatched_requests=unmatched,
                ledger_minus_provider_microusd=sum(requests[k] - usage[k] for k in matched),
            )
    elif provider is not None:
        raise ValueError("Provider comparison requires ledger snapshots")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--ledger-before")
    parser.add_argument("--ledger-after")
    parser.add_argument("--provider-usage")
    args = parser.parse_args(argv)
    try:
        result = analyze(
            read_document(args.evidence),
            before=read_document(args.ledger_before) if args.ledger_before else None,
            after=read_document(args.ledger_after) if args.ledger_after else None,
            provider=read_document(args.provider_usage) if args.provider_usage else None,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError):
        print("Analysis failed: invalid or incomplete input.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
