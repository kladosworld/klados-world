"""The society's clock and worker in one long-running process (internal test deployment).

Every minute: tick when an hour has passed since the last tick, run the due turns,
reflections and moderation, and write the aggregate report every 24 hours.

Safety:
- One scheduler at a time: a PostgreSQL advisory lock held for the whole run. A
  second copy exits at once, so turns can never run twice in parallel.
- Restarts are safe: time is the database clock, a turn is claimed before its call,
  a tick never runs twice within the rules' minimum gap, and missed hours are not
  replayed (an outage ages nobody).
- A paused society does nothing. The worker pauses it on a spent cap, a missing key,
  the stop file, or repeated provider failures.
- Logs carry counts and fixed error labels only, never agent text, reasons or keys.
"""
import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import signal
import sys
import time

from arena import db, society, society_report, worker
from tee_arena.society import rules as rules_module

LOCK = 73423189
TICK_EVERY = timedelta(hours=1)
REPORT_EVERY = timedelta(hours=24)


def log(message):
    print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), message, flush=True)


def tick_due(state, now):
    return not state["paused"] and (state["last_tick_at"] is None or now - state["last_tick_at"] >= TICK_EVERY)


def step(conn, rules, agents, watcher, now):
    """One pass. Returns a summary of counts (safe to log)."""
    state = society.clock(conn)
    result = {"tick": state["tick"], "paused": state["paused"]}
    if state["paused"]:
        return result
    if tick_due(state, now):
        ticked = society.tick(conn, rules, now)
        conn.commit()
        result.update(tick=ticked["tick"], ticked=ticked["ticked"], deaths=len(ticked.get("deaths", [])))
    summary = worker.Worker(conn, rules, agents, observer_gateway=watcher, now=lambda: now).run()
    result.update({k: v for k, v in summary.items()})
    return result


def write_report(conn, rules, ledger, directory, now):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"report-{now.strftime('%Y%m%dT%H%MZ')}.md"
    path.write_text(society_report.build(conn, rules, ledger, now), encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the society clock and worker until stopped.")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--reports", required=True)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--init-ledger-usd", help="Create the ledger with this total cap, then exit (setup only)")
    parser.add_argument("--report-now", action="store_true", help="Write one report now, then exit")
    args = parser.parse_args(argv)
    if args.init_ledger_usd:
        if Path(args.ledger).exists():
            log("error: the ledger already exists; it is never recreated")
            return 1
        worker.open_ledger(args.ledger, args.init_ledger_usd)
        log(f"ledger created with a total cap of ${float(args.init_ledger_usd):.2f}")
        return 0
    dsn = os.environ.get("ARENA_DATABASE_URL")
    if not dsn:
        log("error: ARENA_DATABASE_URL is required")
        return 1
    if os.environ.get("KLADOS_SOCIETY_PUBLIC") == "1":
        log("error: the internal test must run with the public API off")
        return 1
    rules = rules_module.load(os.environ.get("KLADOS_RULES") or rules_module.DEFAULT_PATH)
    ledger = worker.open_ledger(args.ledger)  # created once by the setup step, never here
    agents, watcher = worker.build_gateway(ledger), worker.build_gateway(ledger, role="observer")
    if args.report_now:
        with db.connect(dsn) as conn:
            log(f"report written: {write_report(conn, rules, ledger, args.reports, datetime.now(timezone.utc)).name}")
        return 0
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    with db.connect(dsn) as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK,)).fetchone()["ok"]:
            log("error: another scheduler is running")
            return 1
        conn.commit()
        next_report = datetime.now(timezone.utc) + REPORT_EVERY
        log("scheduler started")
        failures = 0
        while not stopping:
            now = datetime.now(timezone.utc)
            try:
                summary = step(conn, rules, agents, watcher, now)
                if summary.get("ticked") or summary.get("turns") or summary.get("failed") or summary.get("stopped"):
                    log("step " + " ".join(f"{k}={v}" for k, v in sorted(summary.items())))
                if now >= next_report:
                    log(f"report written: {write_report(conn, rules, ledger, args.reports, now).name}")
                    next_report = now + REPORT_EVERY
                conn.commit()
                failures = 0
            except Exception as error:  # never log the exception text: it may hold agent text
                conn.rollback()
                failures += 1
                log(f"error: step failed ({type(error).__name__}), attempt {failures}")
                if failures >= 5:
                    log("error: five failed steps in a row; stopping, the society stays as it is")
                    return 1
            for _ in range(args.interval):
                if stopping:
                    break
                time.sleep(1)
        log("scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
