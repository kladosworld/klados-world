"""Dynamic ceilings use the existing atomic ledger; synthetic files only."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import tempfile
import unittest

from tee_arena import ledger, store
from tests.test_arena_society_life import WorkerCase


def race(path, operation):
    book=ledger.Ledger(path)
    try:
        if operation=='lower':
            book.lower_cap(500, reference='test', approved_by='test', account='founders', apply=True, approved=True)
        else:
            book.reserve('founders',400,'race-'+operation)
        return True
    except ledger.LedgerError:
        return False


class BudgetChangesTests(unittest.TestCase):
    def test_lower_preserves_reservations_spending_history_and_global_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            book=ledger.Ledger.create(Path(directory)/'ledger.json',global_cap=10_000_000)
            book.open_account('founders',2000)
            first=book.reserve('founders',200,'paid');book.settle(first,150)
            held=book.reserve('founders',300,'pending')
            before=book.path.read_bytes()
            for scope in ('founders','global'):
                with self.assertRaises(ledger.LedgerError):
                    book.lower_cap(449,reference='test',approved_by='test',account=scope,apply=True,approved=True)
                self.assertEqual(book.path.read_bytes(),before)
            preview=book.lower_cap(450,reference='test',approved_by='test',account='founders')
            self.assertTrue(preview['preview']);self.assertEqual(book.path.read_bytes(),before)
            history=store.read(book.path)[1]['events']
            book.lower_cap(450,reference='test',approved_by='test',account='founders',apply=True,approved=True)
            self.assertEqual(store.read(book.path)[1]['events'][:-1],history)
            self.assertEqual(book.totals()['global_cap'],10_000_000)
            with self.assertRaises(ledger.CapExceeded):book.reserve('founders',1,'refused')
            book.release(held)
            book.reserve('founders',1,'new-after-release')
            with self.assertRaises(ledger.DuplicateRequest):book.reserve('founders',1,'paid')

    def test_open_is_approved_audited_and_does_not_expand_global_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            book=ledger.Ledger.create(Path(directory)/'ledger.json',global_cap=10_000_000)
            before=book.path.read_bytes()
            self.assertTrue(book.approved_open_account('new',0,reference='test',approved_by='test')['preview'])
            with self.assertRaises(ledger.LedgerError):book.approved_open_account('new',100,reference='test',approved_by='test',apply=True)
            self.assertEqual(book.path.read_bytes(),before)
            event=book.approved_open_account('new',100,reference='test',approved_by='test',apply=True,approved=True)
            self.assertEqual(event['reference'],'test')
            for name in ('new','global'):
                with self.assertRaises(ledger.LedgerError):book.approved_open_account(name,100,reference='test',approved_by='test',apply=True,approved=True)
            self.assertEqual(book.totals()['global_cap'],10_000_000)
            book.lower_cap(0,account='new',reference='test',approved_by='test',apply=True,approved=True)
            with self.assertRaises(ledger.CapExceeded):book.reserve('new',1,'blocked')

    def test_process_race_never_lowers_below_committed_use(self):
        with tempfile.TemporaryDirectory() as directory:
            book=ledger.Ledger.create(Path(directory)/'ledger.json',global_cap=10_000_000)
            book.open_account('founders',2000)
            with ProcessPoolExecutor(max_workers=3) as pool:
                list(pool.map(race,[str(book.path)]*3,['lower','one','two']))
            state=book.balance('founders')
            self.assertLessEqual(state['charged']+state['reserved'],state['cap'])
            ledger.validate(store.read(book.path)[1])


class FinancialRestTests(WorkerCase):
    def test_same_worker_uses_new_limits_on_next_tick_without_restart(self):
        run=self.make({name:'{"action":"rest"}' for name in ('Alder','Birch','Cedar','Hazel')},cap=10)
        self.assertFalse(run.run()['stopped'])
        self.assertFalse(self.provider.requests)
        self.assertEqual(run.failures,0)
        book=run.gateway.ledger
        for scope in ('global','society-agents','society-observer'):
            book.raise_cap(5_000_000,account=scope,reference='synthetic',approved_by='test',apply=True,approved=True)
        self.hours(1)
        self.assertFalse(run.run()['stopped'])
        self.assertEqual(len(self.provider.requests),4)
        self.assertFalse(__import__('arena.society',fromlist=['clock']).clock(self.conn)['paused'])
