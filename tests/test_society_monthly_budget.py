"""Monthly inference accounting using synthetic ledger files and mocked clocks/providers."""
from multiprocessing import get_context
import copy
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from tee_arena import ledger, store
from tee_arena.gateway import Gateway, GatewayError, ModelPrice, ProviderResponse

NOVEMBER = '2026-11-30T23:59:00+00:00'
DECEMBER = '2026-12-01T00:00:00+00:00'


def reserve_process(path, index):
    try:
        with patch.object(ledger, '_now', return_value=NOVEMBER):
            ledger.Ledger(path).reserve('society-agents', 300, 'process.' + str(index))
        return True
    except ledger.CapExceeded:
        return False


class MonthlyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'ledger.json'
        self.time = patch.object(ledger, '_now', return_value=NOVEMBER)
        self.clock = self.time.start()
        self.addCleanup(self.time.stop)
        self.book = ledger.Ledger.create(self.path, global_cap=30_000_000)
        self.book.open_account('society-agents', 30_000_000)
        self.book.open_account('society-observer', 30_000_000)

    def policy(self, value=1000, **changes):
        return self.book.set_budget(value, reference='synthetic-approval', approved_by='test-operator',
                                    apply=True, approved=True, **changes)

    def test_old_ledger_and_one_dollar_lifetime_cap_remain_effective(self):
        old = ledger.Ledger.create(Path(self.directory.name) / 'old.json', global_cap=1_000_000)
        old.open_account('society-agents', 1_000_000)
        old.reserve('society-agents', 1_000_000, 'old-request')
        original = store.read(old.path)[1]['events']
        old.set_budget(10_000_000, reference='test', approved_by='test', apply=True, approved=True)
        self.assertEqual(store.read(old.path)[1]['events'][:2], original)
        before = old.path.read_bytes()
        with self.assertRaises(ledger.CapExceeded):
            old.reserve('society-agents', 1, 'another')
        self.assertEqual(old.path.read_bytes(), before)
        self.assertEqual(old.totals()['global_cap'], 1_000_000)

    def test_reporting_empty_history_has_explicit_cursor_and_no_writes(self):
        empty = ledger.Ledger.create(Path(self.directory.name) / 'empty.json', global_cap=1_000_000)
        before = empty.path.read_bytes()
        status = empty.budget_status()
        self.assertEqual(status['schema'], 1)
        self.assertEqual(status['accounts'], {})
        self.assertEqual(status['events'], 0)
        self.assertIsNone(status['last_event_id'])
        self.assertIsNone(status['monthly_cap_microusd'])
        self.assertEqual(status['lifetime_cap_microusd'], 1_000_000)
        self.assertEqual(empty.path.read_bytes(), before)

    def test_reporting_classifies_policy_events_and_effective_account_caps(self):
        hold = self.book.reserve('society-agents', 600, 'report.settled')
        self.book.settle(hold, 400)
        released = self.book.reserve('society-observer', 200, 'report.released')
        self.book.release(released)
        self.book.reserve('society-observer', 100, 'report.pending')
        self.policy(10_000_000)
        self.book.raise_cap(31_000_000, reference='synthetic-cap-approval', approved_by='test',
                            account='society-observer', apply=True, approved=True)
        before = self.path.read_bytes()
        document = store.read(self.path)[1]
        status = self.book.budget_status()
        self.assertEqual(status['events'], len(document['events']))
        self.assertEqual(status['last_event_id'], document['events'][-1]['id'])
        self.assertEqual(status['accounts'], {
            'society-agents': {'cap_microusd': 30_000_000, 'charged_microusd': 400, 'reserved_microusd': 0},
            'society-observer': {'cap_microusd': 31_000_000, 'charged_microusd': 0, 'reserved_microusd': 100}})
        self.assertEqual(status['lifetime_charged_microusd'], 400)
        self.assertEqual(status['lifetime_reserved_microusd'], 100)
        self.assertEqual(status['charged_microusd'], 400)
        self.assertEqual(status['reserved_microusd'], 100)
        self.assertEqual(status['lifetime_cap_microusd'], 30_000_000)
        self.assertEqual(ledger.Ledger(self.path).budget_status(), status)
        self.assertEqual(self.path.read_bytes(), before)
        self.book.reserve('society-agents', 1, 'report.next')
        following = self.book.budget_status()
        self.assertEqual(following['events'], status['events'] + 1)
        self.assertNotEqual(following['last_event_id'], status['last_event_id'])

    def test_reporting_refuses_unknown_or_corrupt_events_without_rewriting(self):
        original = self.path.read_bytes()
        document = store.read(self.path)[1]
        document['events'][-1]['kind'] = 'synthetic-unknown'
        for content in (json.dumps(document).encode(), b'{broken'):
            self.path.write_bytes(content)
            with self.assertRaises((ledger.LedgerError, json.JSONDecodeError)):
                self.book.budget_status()
            self.assertEqual(self.path.read_bytes(), content)
        self.path.write_bytes(original)

    def test_guard_snapshot_reconciles_reservation_months_and_lifetime_accounts(self):
        book = ledger.Ledger.create(Path(self.directory.name) / 'guard.json', global_cap=1_000_000)
        book.open_account('society-agents', 1_000_000)
        book.open_account('society-observer', 1_000_000)
        book.set_budget(10_000_000, reference='synthetic-policy', approved_by='test',
                        warning_microusd=7_000_000, apply=True, approved=True)
        late = book.reserve('society-agents', 600, 'guard.november-late')
        book.reserve('society-agents', 200, 'guard.november-open')
        self.clock.return_value = DECEMBER
        book.settle(late, 400)
        settled = book.reserve('society-observer', 100, 'guard.december-settled')
        book.settle(settled, 80)
        book.reserve('society-observer', 120, 'guard.december-open')
        before = book.path.read_bytes()
        status = book.budget_status()
        self.assertEqual(status['month'], '2026-12')
        self.assertEqual((status['charged_microusd'], status['reserved_microusd']), (80, 120))
        self.assertEqual(status['pending_by_month'], {'2026-11': 200, '2026-12': 120})
        self.assertEqual(status['accounts'], {
            'society-agents': {'cap_microusd': 1_000_000, 'charged_microusd': 400, 'reserved_microusd': 200},
            'society-observer': {'cap_microusd': 1_000_000, 'charged_microusd': 80, 'reserved_microusd': 120}})
        self.assertEqual(status['lifetime_cap_microusd'], 1_000_000)
        self.assertEqual(status['lifetime_charged_microusd'], 480)
        self.assertEqual(status['lifetime_reserved_microusd'], 320)
        self.assertEqual(status['lifetime_available_microusd'], 999_200)
        self.assertEqual(status['available_microusd'], 9_999_800)
        # Generating a newer report is not new ledger or provider activity.
        self.clock.return_value = '2026-12-01T00:05:00+00:00'
        following = ledger.Ledger(book.path).budget_status()
        self.assertNotEqual(following['as_of'], status['as_of'])
        self.assertEqual({k: v for k, v in following.items() if k != 'as_of'},
                         {k: v for k, v in status.items() if k != 'as_of'})
        self.assertEqual(book.path.read_bytes(), before)

    def test_guard_report_keeps_tighter_account_cap_and_refused_request_cursor(self):
        book = ledger.Ledger.create(Path(self.directory.name) / 'tight.json', global_cap=1_000_000)
        book.open_account('society-agents', 100)
        book.open_account('society-observer', 1_000_000)
        book.set_budget(10_000_000, reference='synthetic-policy', approved_by='test', apply=True, approved=True)
        book.reserve('society-agents', 100, 'guard.account-full')
        status = book.budget_status()
        before = book.path.read_bytes()
        with self.assertRaises(ledger.CapExceeded) as failure:
            book.reserve('society-agents', 1, 'guard.refused')
        self.assertEqual(failure.exception.scope, 'society-agents')
        self.assertGreater(status['available_microusd'], 0)
        self.assertGreater(status['lifetime_available_microusd'], 0)
        self.assertEqual(status['accounts']['society-agents']['cap_microusd'], 100)
        self.assertEqual(status['accounts']['society-agents']['reserved_microusd'], 100)
        self.assertEqual(book.budget_status(), status)
        self.assertEqual(book.path.read_bytes(), before)

    def test_guard_cli_failed_snapshot_emits_no_json_or_sensitive_diagnostics(self):
        from arena import runtime
        for content in (b'{PRIVATE_CONTENT_CANARY', b'{"format":"unknown","events":[]}'):
            self.path.write_bytes(content)
            with patch.object(runtime.db, 'connect', side_effect=AssertionError('no database')), \
                    redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(runtime.main(['budget', 'show', '--ledger', str(self.path)]), 1)
            self.assertEqual(output.getvalue(), '')
            self.assertNotIn('PRIVATE_CONTENT_CANARY', errors.getvalue())
            self.assertNotIn(str(self.path), errors.getvalue())
            self.assertEqual(self.path.read_bytes(), content)

    def test_maximum_reservation_warning_and_no_partial_write(self):
        self.policy()
        hold = self.book.reserve('society-agents', 700, 'turn')
        status = self.book.budget_status()
        self.assertTrue(status['warning'])
        self.assertEqual(status['warning_microusd'], 700)
        before = self.path.read_bytes()
        with self.assertRaises(ledger.CapExceeded) as caught:
            self.book.reserve('society-observer', 301, 'moderation')
        self.assertEqual(caught.exception.scope, 'monthly')
        self.assertEqual(self.path.read_bytes(), before)
        self.book.settle(hold, 400)
        self.book.reserve('society-observer', 600, 'exact-fit')
        self.assertEqual(self.book.budget_status()['available_microusd'], 0)

    def test_rollover_keeps_late_settlement_in_reservation_month_and_no_unused_rollover(self):
        self.policy()
        hold = self.book.reserve('society-agents', 600, 'november')
        self.clock.return_value = DECEMBER
        self.assertEqual(self.book.budget_status()['charged_microusd'], 0)
        self.assertEqual(self.book.budget_status()['reserved_microusd'], 0)
        self.assertEqual(self.book.budget_status()['available_microusd'], 1000)
        self.assertEqual(self.book.budget_status()['pending_by_month'], {'2026-11': 600})
        self.book.reserve('society-observer', 1000, 'december')
        self.book.settle(hold, 400)
        document = store.read(self.path)[1]
        self.assertEqual(ledger.summarize(document)['months']['2026-11'], {'charged': 400, 'reserved': 0})
        self.assertEqual(ledger.summarize(document)['months']['2026-12'], {'charged': 0, 'reserved': 1000})
        self.assertEqual(ledger.Ledger(self.path).budget_status(), self.book.budget_status())

    def test_year_rollover_leap_day_unknown_usage_and_release(self):
        self.policy()
        for stamp in ('2026-12-31T23:59:59+00:00', '2027-01-01T00:00:00+00:00',
                      '2028-02-29T23:59:59+00:00', '2028-03-01T00:00:00+00:00'):
            self.clock.return_value = stamp
            hold = self.book.reserve('society-agents', 100, stamp.replace('+', 'Z'))
            self.book.settle(hold, None)
            released = self.book.reserve('society-observer', 50, 'release.' + stamp.replace('+', 'Z'))
            self.book.release(released)
            status = self.book.budget_status()
            self.assertEqual(status['month'], stamp[:7])
            self.assertEqual(status['charged_microusd'], 100)
            self.assertEqual(status['reserved_microusd'], 0)

    def test_concurrent_reservations_and_policy_changes_share_lock(self):
        self.policy()
        def attempt(index):
            try:
                self.book.reserve('society-agents' if index % 2 else 'society-observer', 300, 'request.' + str(index))
                return True
            except ledger.CapExceeded:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(12))), 3)
        self.assertEqual(self.book.budget_status()['reserved_microusd'], 900)

    def test_policy_preview_approval_decrease_cap_audit_and_history(self):
        before = self.path.read_bytes()
        self.assertTrue(self.book.set_budget(1000, reference='test', approved_by='operator')['preview'])
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ledger.LedgerError):
            self.book.set_budget(1000, reference='test', approved_by='operator', apply=True)
        self.policy()
        original = copy.deepcopy(store.read(self.path)[1]['events'])
        for reference in ('', ' ', '\n'):
            with self.assertRaises(ledger.LedgerError):
                self.book.set_budget(2000, reference=reference, approved_by='operator', apply=True, approved=True)
        with self.assertRaises(ledger.LedgerError):
            self.book.set_budget(2000, reference='test', approved_by='operator', apply=True)
        self.book.set_budget(500, reference='decrease', approved_by='operator', apply=True)
        self.assertEqual(store.read(self.path)[1]['events'][:len(original)], original)
        self.assertEqual(self.book.budget_status()['monthly_cap_microusd'], 500)
        before = self.path.read_bytes()
        self.assertTrue(self.book.raise_cap(40_000_000, reference='test', approved_by='operator')['preview'])
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ledger.LedgerError):
            self.book.raise_cap(40_000_000, reference='test', approved_by='operator', apply=True)
        self.book.raise_cap(40_000_000, reference='separate-cap-approval', approved_by='operator', apply=True, approved=True)
        self.assertEqual(self.book.totals()['global_cap'], 40_000_000)
        self.assertEqual(self.book.budget_status()['last_cap']['reference'], 'separate-cap-approval')
        self.book.raise_cap(35_000_000, account='society-agents', reference='account-approval', approved_by='operator', apply=True, approved=True)
        self.assertEqual(self.book.balance('society-agents')['cap'], 35_000_000)

    def test_bad_clock_invalid_timestamps_and_malformed_policy_fail_closed(self):
        self.policy()
        before = self.path.read_bytes()
        self.clock.return_value = '2026-11-30T23:58:59+00:00'
        with self.assertRaises(ledger.LedgerError):
            self.book.reserve('society-agents', 1, 'backwards')
        self.assertEqual(self.path.read_bytes(), before)
        document = store.read(self.path)[1]
        for changes in ({'microusd': -1}, {'month': '2026-13'}, {'reference': ''},
                        {'warning_microusd': 1001}, {'at': 'invalid'}, {'approved_by': None}):
            changed = copy.deepcopy(document)
            changed['events'][-1].update(changes)
            with self.assertRaises(ledger.LedgerError):
                ledger.validate(changed)

    def test_gateway_never_sends_if_budget_or_accounting_unavailable(self):
        self.policy(0)
        provider = Mock()
        gateway = Gateway(ledger=self.book, models={'openai:synthetic': ModelPrice('1', '1')},
                          providers={'openai': provider}, stop_file=None)
        request = dict(account='society-agents', role='agent', model_id='openai:synthetic',
                       system='synthetic', messages=[{'role': 'user', 'content': 'synthetic'}],
                       max_output_tokens=100, request_id='synthetic')
        with self.assertRaises(GatewayError) as caught:
            gateway.call(**request)
        self.assertEqual(caught.exception.category, 'cap_exceeded')
        provider.complete.assert_not_called()
        self.path.write_text('{broken')
        with self.assertRaises(GatewayError) as caught:
            gateway.call(**request)
        self.assertEqual(caught.exception.category, 'accounting_unavailable')
        provider.complete.assert_not_called()

    def test_settlement_write_failure_retains_reservation_and_stops(self):
        self.policy(10_000)
        provider = Mock()
        provider.complete.return_value = ProviderResponse(text='{}', model='synthetic', stop='end', input_tokens=1, output_tokens=1)
        gateway = Gateway(ledger=self.book, models={'openai:synthetic': ModelPrice('1', '1')}, providers={'openai': provider}, stop_file=None)
        with patch.object(self.book, 'settle', side_effect=OSError('sensitive-path')):
            with self.assertRaises(GatewayError) as caught:
                gateway.call(account='society-agents', role='agent', model_id='openai:synthetic', system='',
                             messages=[{'role': 'user', 'content': 'synthetic'}], max_output_tokens=100, request_id='held')
        self.assertEqual(caught.exception.category, 'accounting_unavailable')
        self.assertTrue(caught.exception.sent)
        self.assertNotIn('sensitive-path', str(caught.exception))
        self.assertGreater(self.book.budget_status()['reserved_microusd'], 0)

    def test_cli_preview_explicit_apply_status_and_no_database(self):
        from arena import runtime
        base = ['--ledger', str(self.path), '--usd', '10', '--warning-usd', '7', '--reference', 'synthetic-approval']
        with patch.object(runtime.db, 'connect', side_effect=AssertionError('no database')), redirect_stdout(io.StringIO()) as output:
            before = self.path.read_bytes()
            self.assertEqual(runtime.main(['budget', 'set', *base]), 0)
            self.assertTrue(json.loads(output.getvalue())['preview'])
            self.assertEqual(self.path.read_bytes(), before)
            output.seek(0); output.truncate()
            self.assertEqual(runtime.main(['budget', 'set', *base, '--yes']), 0)
            output.seek(0); output.truncate()
            self.assertEqual(runtime.main(['budget', 'show', '--ledger', str(self.path)]), 0)
            status = json.loads(output.getvalue())
            self.assertEqual(status['monthly_cap_microusd'], 10_000_000)
            self.assertEqual(status['warning_microusd'], 7_000_000)
        before = self.path.read_bytes()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(runtime.main(['cap', 'raise', '--ledger', str(self.path), '--usd', '40', '--yes']), 1)
        self.assertEqual(self.path.read_bytes(), before)


    @unittest.skipUnless(os.name == 'posix', 'native process locks require Linux')
    def test_process_reservations_share_atomic_ceiling(self):
        self.policy()
        with ProcessPoolExecutor(max_workers=4, mp_context=get_context('spawn')) as pool:
            granted = list(pool.map(reserve_process, [str(self.path)] * 12, range(12)))
        self.assertEqual(sum(granted), 3)
        self.assertEqual(self.book.budget_status()['reserved_microusd'], 900)

    def test_worker_refuses_paid_work_without_pausing_society_on_accounting_failure(self):
        from arena import worker
        run = worker.Worker(Mock(), Mock(), Mock())
        with patch.object(worker.society, 'set_paused') as pause:
            self.assertFalse(run._failed(GatewayError('accounting_unavailable')))
        pause.assert_not_called()

    def test_retroactive_current_month_policy_counts_existing_history(self):
        hold = self.book.reserve('society-agents', 600, 'before-policy')
        self.book.settle(hold, 500)
        original = copy.deepcopy(store.read(self.path)[1]['events'])
        before = self.path.read_bytes()
        with self.assertRaises(ledger.LedgerError):
            self.policy(400)
        self.assertEqual(self.path.read_bytes(), before)
        self.policy(500)  # exact committed-use floor preserves all prior charges
        status = self.book.budget_status()
        self.assertEqual(status['charged_microusd'], 500)
        self.assertEqual(status['available_microusd'], 0)
        self.assertEqual(store.read(self.path)[1]['events'][:len(original)], original)
        with self.assertRaises(ledger.CapExceeded):
            self.book.reserve('society-observer', 1, 'after-policy')
