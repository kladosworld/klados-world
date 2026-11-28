"""Paid-control contract vectors and strict reservation/settlement isolation."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest

from tee_arena import gateway, ledger, paid_control

NOW = datetime(2026, 11, 28, 7, tzinfo=timezone.utc)
LEASE = b'contract=1\nlease_id=0123456789abcdef0123456789abcdef\nverified_at=2026-11-28T07:00:00Z\nvalid_until=2026-11-28T07:15:00Z\nhistory_n=42\n'


class PaidControlVectors(unittest.TestCase):
    def test_only_complete_current_utc_contract_is_accepted(self):
        self.assertTrue(paid_control.parse_lease(LEASE, now=NOW).allowed)
        self.assertTrue(paid_control.parse_lease(LEASE+b'future_key=ignored\n', now=NOW).allowed)
        for bad in (
            b'', LEASE.replace(b'contract=1', b'contract=2'),
            LEASE.replace(b'valid_until=', b'missing_until='),
            LEASE.replace(b'07:15:00Z', b'07:15:00+00:00'),
            LEASE.replace(b'07:15:00Z', b'07:15:00+02:00'),
            LEASE.replace(b'07:15:00Z', b'07:21:00Z'),
            LEASE.replace(b'history_n=42', b'history_n=-1'),
            LEASE+b'contract=1\n', LEASE+b'x='+b'a'*512,
            LEASE.replace(b'2026-11-28', b'2026-99-99'),
            LEASE+b'badline', LEASE+b'x=\xff',
        ):
            with self.subTest(vector=bad[:30]):
                self.assertFalse(paid_control.parse_lease(bad, now=NOW).allowed)
        self.assertEqual(paid_control.parse_lease(LEASE, now=NOW+timedelta(minutes=15)).reason, 'lease_expired')
        self.assertEqual(paid_control.parse_lease(LEASE, now=NOW-timedelta(seconds=1)).reason, 'lease_future')

    def test_provider_timeout_margin_and_compatible_version(self):
        self.assertTrue(paid_control.parse_lease(LEASE,now=NOW+timedelta(minutes=13)).allowed)
        self.assertFalse(paid_control.parse_lease(LEASE,now=NOW+timedelta(minutes=13,seconds=1)).allowed)
        self.assertTrue(paid_control.parse_lease(LEASE,now=NOW+timedelta(minutes=9),min_remaining=360).allowed)
        self.assertFalse(paid_control.parse_lease(LEASE,now=NOW+timedelta(minutes=9,seconds=1),min_remaining=360).allowed)
        self.assertEqual(paid_control.parse_lease(LEASE.replace(b'contract=1',b'contract=2'),now=NOW).reason,'lease_incompatible')
        self.assertFalse(paid_control.parse_lease(LEASE.replace(b'lease_id=',b'unknown='),now=NOW).allowed)

    def test_mock_needs_no_lease_but_emergency_stop_still_applies(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(paid_control.authorize(directory=root, mock=True).allowed)
            (Path(root)/'KILLED').touch()
            self.assertFalse(paid_control.authorize(directory=root, mock=True).allowed)

    @unittest.skipUnless(os.name == 'posix', 'Native POSIX ownership/no-follow reader')
    def test_reader_rejects_symlinks_stops_and_nonregular_or_insecure_files(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            path = directory/'FINANCIAL_LEASE'
            path.write_bytes(LEASE)
            path.chmod(0o644)
            def read():
                return paid_control.authorize(directory=root, now=NOW, expected_uid=os.getuid())
            self.assertTrue(read().allowed)
            (directory/'PAID_STOP').touch()
            self.assertEqual(read().reason, 'paid_stop')
            (directory/'PAID_STOP').unlink()
            path.chmod(0o666)
            self.assertFalse(read().allowed)
            path.unlink()
            target = directory/'original'
            target.write_bytes(LEASE)
            path.symlink_to(target)
            self.assertFalse(read().allowed)
            path.unlink()
            os.mkfifo(path)
            self.assertFalse(read().allowed)


class ReservationGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.book = ledger.Ledger.create(Path(self.temp.name)/'ledger.json', global_cap=10_000_000)
        self.book.open_account('society-agents', 8_000_000)
        self.book.open_account('society-observer', 2_000_000)
        self.calls = []
        outer = self
        class Provider:
            def complete(self, request):
                outer.calls.append(request)
                return gateway.ProviderResponse(text='OK', input_tokens=1, output_tokens=1, model=request.model, stop='end')
        self.client = gateway.Gateway(self.book, {'openai:synthetic-model': gateway.ModelPrice('1','1')},
                                      providers={'openai':Provider()}, stop_file=None)

    def call(self, key='test:request'):
        return self.client.call(account='society-agents', role='agent', model_id='openai:synthetic-model',
            system='Synthetic', messages=[{'role':'user','content':'Synthetic'}],
            max_output_tokens=10, request_id=key)

    def test_paid_stop_prevents_reserve_but_never_blocks_settle_or_release(self):
        held = self.book.reserve('society-agents', 10, 'old:pending')
        raw = self.book.path.read_bytes()
        self.client.paid_authorizer = lambda **kwargs: paid_control.Decision(False, 'paid_stop')
        with self.assertRaises(gateway.GatewayError) as caught:
            self.call()
        self.assertEqual(caught.exception.category, 'paid_operations_suspended')
        self.assertEqual(self.book.path.read_bytes(), raw)
        self.assertEqual(self.calls, [])
        self.book.settle(held, 2)
        self.client.paid_authorizer = lambda **kwargs: paid_control.Decision(True)
        self.call('renewed:lease')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.book.totals()['reserved'], 0)

    def test_short_lease_is_refused_inside_reservation_without_new_event(self):
        self.client.paid_authorizer = lambda **kwargs: paid_control.Decision(False, 'lease_expired')
        before = self.book.path.read_bytes()
        with self.assertRaises(gateway.GatewayError):
            self.call()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.book.path.read_bytes(),before)

    def test_authorizer_errors_are_sanitized_and_never_authorize_spend(self):
        def broken(**kwargs):
            raise OSError('PRIVATE_CONFIGURATION')
        self.client.paid_authorizer = broken
        with self.assertRaises(gateway.GatewayError) as caught:
            self.call()
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(self.calls, [])

    def test_stop_published_under_writer_lock_precedes_waiting_reservation(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from tee_arena import store
        started = threading.Event()
        allowed = [True]
        self.client.paid_authorizer = lambda **kwargs: paid_control.Decision(allowed[0], None if allowed[0] else 'paid_stop', 'a'*32 if allowed[0] else None)
        def call():
            started.set()
            return self.call()
        before = self.book.path.read_bytes()
        with ThreadPoolExecutor(1) as pool:
            with store.lock(self.book.path):
                future = pool.submit(call)
                self.assertTrue(started.wait(2))
                allowed[0] = False  # Synthetic guard publishes while owning identical lock.
            with self.assertRaises(gateway.GatewayError) as caught:
                future.result(timeout=3)
        self.assertEqual(caught.exception.category,'paid_operations_suspended')
        self.assertEqual(self.calls,[])
        self.assertEqual(self.book.path.read_bytes(),before)

    def test_reserve_records_lease_and_historical_events_remain_valid(self):
        import json
        lease = '0123456789abcdef0123456789abcdef'
        self.client.paid_authorizer = lambda **kwargs: paid_control.Decision(True,lease_id=lease)
        self.call()
        document = json.loads(self.book.path.read_text())
        reserved = next(e for e in document['events'] if e['kind']=='reserve')
        self.assertEqual(reserved['lease'],lease)
        ledger.validate(document)
        reserved.pop('lease')
        ledger.validate(document)  # Legacy events do not acquire invented provenance.
        reserved['lease']='invalid'
        with self.assertRaises(ledger.LedgerError):
            ledger.validate(document)
