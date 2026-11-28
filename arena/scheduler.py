"""The society's clock and worker in one long-running process (internal test deployment).

Every minute: tick when an hour has passed since the last tick, run the due turns,
reflections and moderation, and write the aggregate report every 24 hours.

Safety:
- One scheduler at a time: a PostgreSQL advisory lock held for the whole run. A
  second copy exits at once, so turns can never run twice in parallel.
- Restarts are safe: time is the database clock, a turn is claimed before its call,
  a tick never runs twice within the rules' minimum gap, and missed hours are not
  replayed (an outage ages nobody).
- Social pause and KILLED stop social activity. Paid control/budget failures cause
  financial rest; provider/credential failures stop only hosted inference. Free
  maintenance may run as a separate singleton component without a ledger or key.
- Logs carry counts and fixed error labels only, never agent text, reasons or keys.
"""
import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import signal
import sys
import time
from psycopg import OperationalError, InterfaceError
from psycopg.errors import QueryCanceled, LockNotAvailable, DeadlockDetected, SerializationFailure

from arena import db, society, society_report, worker, eras, institutions, lifecycle
from tee_arena.society import rules as rules_module
from tee_arena import paid_control

LOCK = 73423189
TICK_EVERY = timedelta(hours=1)
REPORT_EVERY = timedelta(hours=24)


def log(message):
    print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), message, flush=True)


def tick_due(state, now):
    return not state["paused"] and (state["last_tick_at"] is None or now - state["last_tick_at"] >= TICK_EVERY)


COMPONENT_LOCKS = {'maintenance': 73423310, 'founders': 73423311, 'moderation': 73423312}


def acquire_component(conn, component):
    if component == 'all':
        return conn.execute('SELECT pg_try_advisory_lock(%s) AS ok', (LOCK,)).fetchone()['ok']
    if component not in COMPONENT_LOCKS:
        raise ValueError('Invalid scheduler component')
    # Shared era lease prevents an era transition while any split component runs.
    if not conn.execute('SELECT pg_try_advisory_lock_shared(%s) AS ok', (LOCK,)).fetchone()['ok']:
        return False
    acquired = conn.execute('SELECT pg_try_advisory_lock(%s) AS ok',
                         (COMPONENT_LOCKS[component],)).fetchone()['ok']
    if not acquired:
        conn.execute('SELECT pg_advisory_unlock_shared(%s)', (LOCK,))
    return acquired


def step(conn, rules, agents, watcher, now, *, component='all'):
    """One bounded free/paid component pass. No component can activate the society."""
    if component not in ('all', *COMPONENT_LOCKS):
        raise ValueError('Invalid scheduler component')
    state = society.clock(conn)
    result = {'tick': state['tick'], 'paused': state['paused']}
    if state['paused']:
        return result
    if paid_control.killed():
        return dict(result, stopped=True)
    if component in ('all', 'maintenance'):
        if tick_due(state, now):
            ticked = society.tick(conn, rules, now)
            conn.commit()
            result.update(tick=ticked['tick'], ticked=ticked['ticked'], deaths=len(ticked.get('deaths', [])))
        institutions.close_due(conn, rules, now)
        conn.commit()
        lifecycle.refresh(conn, now)
        conn.commit()
    if component == 'maintenance':
        return result
    if component == 'moderation':
        screening = worker.Worker(conn, rules, None, observer_gateway=watcher, now=lambda: now).screen()
        return dict(result, moderation_stopped=screening == 'stop')
    try:
        purpose = worker.shared_purpose.from_environ()
        eras.require_purpose(conn, purpose)
    except (worker.shared_purpose.PurposeConfigurationError, society.SocietyError):
        conn.rollback()
        return dict(result, founder_configuration_unavailable=True)
    summary = worker.Worker(conn, rules, agents, observer_gateway=watcher, now=lambda: now,
                            purpose=purpose).run(screening=component == 'all')
    result.update(summary)
    return result


def write_report(conn, rules, ledger, directory, now):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"report-{now.strftime('%Y%m%dT%H%MZ')}.md"
    path.write_text(society_report.build(conn, rules, ledger, now), encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the society clock and worker until stopped.")
    parser.add_argument("--ledger")
    parser.add_argument("--reports")
    parser.add_argument("--component", choices=("all", *COMPONENT_LOCKS), default="all")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--init-ledger-usd", help="Create the ledger with this total cap, then exit (setup only)")
    parser.add_argument("--report-now", action="store_true", help="Write one report now, then exit")
    args = parser.parse_args(argv)
    if not 1 <= args.interval <= 3600:
        parser.error('--interval must be between 1 and 3600 seconds')
    if (args.component != 'maintenance' or args.init_ledger_usd or args.report_now) and not args.ledger:
        parser.error('--ledger is required for paid components or financial reporting')
    if args.report_now and not args.reports:
        parser.error('--reports is required with --report-now')
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
    # Constructing Ledger never creates, resets or validates a document. An absent/
    # unreadable ledger refuses paid reserves without preventing free maintenance.
    ledger = worker.ledger_module.Ledger(args.ledger) if args.ledger else None
    agents = worker.build_gateway(ledger) if args.component in ('all', 'founders') else None
    watcher = worker.build_gateway(ledger, role='observer') if args.component in ('all', 'moderation') else None
    if args.report_now:
        with db.connect(dsn) as conn:
            log(f"report written: {write_report(conn, rules, ledger, args.reports, datetime.now(timezone.utc)).name}")
        return 0
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    return serve(dsn, rules, agents, watcher, component=args.component, interval=args.interval,
                 ledger=ledger, reports=args.reports, stopping=stopping)


def serve(dsn, rules, agents, watcher, *, component, interval, ledger=None, reports=None,
          stopping=None, connection_factory=db.connect, sleep=time.sleep, now=None):
    """Reconnect physical sessions; durable turn/receipt identities remain authoritative."""
    stopping = [] if stopping is None else stopping
    now = now or (lambda: datetime.now(timezone.utc))
    next_report = now() + REPORT_EVERY
    def wait(seconds):
        for _ in range(seconds):
            if stopping:
                break
            sleep(1)
    while not stopping:
        try:
            with connection_factory(dsn) as conn:
                conn.execute("SET lock_timeout='5s'; SET statement_timeout='30s'")
                if not acquire_component(conn, component):
                    log('error: another scheduler component is running')
                    return 1
                conn.commit()
                failures = 0
                log('scheduler component started: ' + component)
                while not stopping:
                    timestamp = now()
                    try:
                        summary = step(conn, rules, agents, watcher, timestamp, component=component)
                        if summary.get('ticked') or summary.get('turns') or summary.get('failed') or summary.get('financial_rests') or summary.get('stopped'):
                            log('step ' + ' '.join(f'{k}={v}' for k,v in sorted(summary.items())))
                        if reports and ledger is not None and timestamp >= next_report:
                            try:
                                log(f'report written: {write_report(conn,rules,ledger,reports,timestamp).name}')
                                next_report = timestamp + REPORT_EVERY
                            except (worker.ledger_module.LedgerError, OSError, ValueError):
                                log('warning: financial report unavailable; free maintenance continues')
                                next_report = timestamp + timedelta(minutes=5)
                        conn.commit()
                        failures = 0
                    except (OperationalError, InterfaceError):
                        raise
                    except (QueryCanceled, LockNotAvailable, DeadlockDetected, SerializationFailure):
                        conn.rollback()
                        log('warning: database contention; deferred component pass')
                    except Exception as error:
                        conn.rollback()
                        failures += 1
                        log(f'error: component step failed ({type(error).__name__}), attempt {failures}')
                        if failures >= 5:
                            log('error: five failed steps; stopping affected component')
                            return 1
                    wait(interval)
        except (OperationalError, InterfaceError, db.DatabaseBusy):
            # The disconnected server session releases its singleton/era lease.
            # Reacquire on a NEW connection; never reuse an aborted/broken session.
            log('warning: database unavailable; reconnecting component')
            wait(min(interval,30))
    log('scheduler component stopped: ' + component)
    return 0



if __name__ == "__main__":
    sys.exit(main())
