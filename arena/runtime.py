"""Small development CLI; no VPS, production or TEE controls."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from arena import db, demo, scheduler, society, society_moderation, worker
from tee_arena.gateway import DEFAULT_STOP_FILE
from tee_arena.society import rules as rules_module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['bootstrap', 'demo', 'status', 'audit', 'pause',
                                          'resume', 'emergency', 'clear-stop', 'worker-health',
                                          'review', 'show', 'decide', 'invite', 'agent',
                                          'refresh-founders-v2'])
    parser.add_argument('operation', nargs='?', choices=['create', 'list', 'revoke', 'suspend'])
    parser.add_argument('--id', dest='identifier')
    parser.add_argument('--ledger', default='/data/ledger.json')
    parser.add_argument('--cap-usd', default='1.00')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--finding', type=int)
    parser.add_argument('--decision', choices=['release', 'confirm'])
    args = parser.parse_args(argv)
    try:
        if args.command == 'emergency':
            DEFAULT_STOP_FILE.touch()
        if args.command == 'clear-stop':
            if not args.yes:
                raise ValueError('clear-stop requires --yes; resuming is a separate command')
            DEFAULT_STOP_FILE.unlink(missing_ok=True)
            print(json.dumps({'stopped': False, 'resumed': False}))
            return 0
        rules = rules_module.load(os.environ.get('KLADOS_RULES') or rules_module.DEFAULT_PATH)
        with db.connect(os.environ['ARENA_DATABASE_URL']) as conn:
            now = datetime.now(timezone.utc)
            if args.command in ('invite', 'agent'):
                from arena import external_agents
                if args.command == 'invite' and args.operation == 'create':
                    result = external_agents.create_invite(conn, now)
                elif args.command == 'invite' and args.operation == 'list':
                    result = external_agents.list_invites(conn)
                elif args.operation in ('revoke', 'suspend') and args.identifier:
                    if args.command == 'invite' and args.operation == 'revoke':
                        result = {'changed': external_agents.revoke_invite(conn, args.identifier, now)}
                    elif args.command == 'agent':
                        result = {'changed': external_agents.control_agent(conn, args.identifier, args.operation, now)}
                    else:
                        raise ValueError('Invalid operator action')
                else:
                    raise ValueError('Invalid operator action')
            elif args.command == 'refresh-founders-v2':
                if args.operation is not None or args.identifier is not None:
                    raise ValueError('refresh-founders-v2 takes no operation or identity argument')
                result = society.refresh_founders_v2(conn, rules, now, apply=args.yes)
            elif args.command == 'bootstrap':
                db.migrate(conn)
                state = conn.execute('SELECT 1 FROM society_clock').fetchone()
                if state is None:
                    society.found(conn, rules, now)
                society.sync_descriptions(conn, now)
                # One initializer at a time; existing ledgers are never reset.
                conn.execute('SELECT pg_advisory_xact_lock(%s)', (73423190,))
                worker.open_ledger(Path(args.ledger), args.cap_usd)
                result = society.status(conn, rules)
            elif args.command == 'demo':
                result = demo.run(conn, rules, worker.open_ledger(args.ledger))
            elif args.command in ('pause', 'resume', 'emergency'):
                if args.command == 'resume' and (not args.yes or DEFAULT_STOP_FILE.exists()):
                    raise ValueError('resume requires --yes and no emergency stop file')
                result = {'changed': society.set_paused(conn, rules, args.command != 'resume',
                                                        now, 'development-operator')}
            elif args.command == 'worker-health':
                row = conn.execute("""SELECT count(*) AS n FROM pg_locks
                    WHERE locktype='advisory' AND objid=%s AND classid=0 AND granted
                    AND database=(SELECT oid FROM pg_database WHERE datname=current_database())""",
                    (scheduler.LOCK,)).fetchone()
                if row['n'] != 1:
                    raise ValueError('Scheduler lock is not held')
                result = {'ready': True}
            elif args.command == 'audit':
                mismatches = society.audit(conn)
                if mismatches:
                    raise ValueError('Energy journal mismatch')
                result = {'ok': True}
            elif args.command == 'review':
                result = society_moderation.review_queue(conn)
            elif args.command in ('show', 'decide'):
                if args.finding is None:
                    raise ValueError('--finding is required')
                if args.command == 'show':
                    result = society_moderation.show(conn, args.finding)
                else:
                    if not args.yes or not args.decision:
                        raise ValueError('decide requires --yes and --decision')
                    result = society_moderation.decide(conn, rules, args.finding, args.decision, now)
            else:
                result = society.status(conn, rules)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        # Never print DSNs, provider responses, private memory or SQL diagnostics.
        print(f'Command failed ({type(error).__name__}); no private diagnostics emitted.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
