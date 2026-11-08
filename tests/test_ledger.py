from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from tee_arena import ledger, store


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "ledger.json"
        self.ledger = ledger.Ledger.create(self.path, global_cap=10_000)
        self.ledger.open_account("agent-1", 5_000)
        self.ledger.open_account("agent-2", 5_000)

    def tearDown(self):
        self.directory.cleanup()

    def test_reserve_then_settle_charges_actual_cost(self):
        reservation = self.ledger.reserve("agent-1", 1_000, "req-1")
        self.assertEqual(self.ledger.balance("agent-1")["available"], 4_000)
        self.assertEqual(self.ledger.settle(reservation, 300), 300)
        self.assertEqual(self.ledger.balance("agent-1"),
                         {"cap": 5_000, "charged": 300, "reserved": 0, "available": 4_700})

    def test_unknown_usage_keeps_full_reservation(self):
        reservation = self.ledger.reserve("agent-1", 1_000, "req-1")
        self.assertEqual(self.ledger.settle(reservation, None), 1_000)
        self.assertEqual(self.ledger.balance("agent-1")["charged"], 1_000)

    def test_release_returns_everything(self):
        reservation = self.ledger.reserve("agent-1", 1_000, "req-1")
        self.ledger.release(reservation)
        self.assertEqual(self.ledger.balance("agent-1")["available"], 5_000)

    def test_overrun_is_charged_and_flagged(self):
        reservation = self.ledger.reserve("agent-1", 100, "req-1")
        self.assertEqual(self.ledger.settle(reservation, 250), 250)
        _, document = store.read(self.path)
        self.assertTrue(document["events"][-1]["overrun"])

    def test_account_cap_refuses_and_writes_nothing(self):
        before = store.read(self.path)[0]
        with self.assertRaises(ledger.CapExceeded) as caught:
            self.ledger.reserve("agent-1", 5_001, "req-1")
        self.assertEqual(caught.exception.scope, "agent-1")
        self.assertEqual(store.read(self.path)[0], before)

    def test_global_cap_applies_across_accounts(self):
        ledger_path = Path(self.directory.name) / "tight.json"
        tight = ledger.Ledger.create(ledger_path, global_cap=1_500)
        tight.open_account("agent-1", 1_000)
        tight.open_account("agent-2", 1_000)
        tight.reserve("agent-1", 1_000, "req-1")
        with self.assertRaises(ledger.CapExceeded) as caught:
            tight.reserve("agent-2", 600, "req-2")
        self.assertEqual(caught.exception.scope, "global")
        tight.reserve("agent-2", 500, "req-3")

    def test_request_id_is_reserved_only_once(self):
        reservation = self.ledger.reserve("agent-1", 100, "req-1")
        self.ledger.release(reservation)
        with self.assertRaises(ledger.DuplicateRequest):
            self.ledger.reserve("agent-1", 100, "req-1")
        with self.assertRaises(ledger.DuplicateRequest):
            self.ledger.reserve("agent-2", 100, "req-1")

    def test_reservation_closes_only_once(self):
        reservation = self.ledger.reserve("agent-1", 100, "req-1")
        self.ledger.settle(reservation, 50)
        with self.assertRaises(ledger.LedgerError):
            self.ledger.settle(reservation, 50)
        with self.assertRaises(ledger.LedgerError):
            self.ledger.release(reservation)

    def test_invalid_amounts_and_names_are_refused(self):
        for amount in (0, -1, 1.5, "100", True):
            with self.subTest(amount=amount), self.assertRaises(ledger.LedgerError):
                self.ledger.reserve("agent-1", amount, f"req-{amount}")
        with self.assertRaises(ledger.LedgerError):
            self.ledger.open_account("Agent With Spaces", 100)
        with self.assertRaises(ledger.LedgerError):
            self.ledger.reserve("unknown", 100, "req-x")
        with self.assertRaises(ledger.LedgerError):
            self.ledger.open_account("agent-1", 100)

    def test_tampered_event_stream_is_rejected(self):
        reservation = self.ledger.reserve("agent-1", 100, "req-1")
        self.ledger.settle(reservation, 50)
        _, document = store.read(self.path)
        document["events"].append(dict(document["events"][-1], id="event:copy"))  # Second settlement.
        with self.assertRaises(ledger.LedgerError):
            ledger.validate(document)

    def test_concurrent_reservations_never_exceed_cap(self):
        def attempt(index):
            try:
                self.ledger.reserve("agent-1", 700, f"req-{index}")
                return True
            except ledger.CapExceeded:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            granted = sum(pool.map(attempt, range(20)))
        self.assertEqual(granted, 7)  # 7 x 700 = 4,900 <= 5,000 < 8 x 700.
        self.assertLessEqual(self.ledger.balance("agent-1")["reserved"], 5_000)

    def test_totals(self):
        self.ledger.settle(self.ledger.reserve("agent-1", 100, "req-1"), 40)
        self.ledger.reserve("agent-2", 200, "req-2")
        self.assertEqual(self.ledger.totals(), {"global_cap": 10_000, "charged": 40, "reserved": 200})


if __name__ == "__main__":
    unittest.main()
