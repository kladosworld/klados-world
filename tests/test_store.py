from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from tee_arena import store


def _increment_in_process(path, times):
    from tee_arena import store as child_store
    for _ in range(times):
        child_store.update(path, lambda doc: doc.__setitem__("count", doc["count"] + 1))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "agent.json"

    def tearDown(self):
        self.directory.cleanup()

    def test_create_then_read_round_trips(self):
        store.create(self.path, {"count": 0, "name": "agent-1"})
        raw, doc = store.read(self.path)
        self.assertEqual(doc, {"count": 0, "name": "agent-1"})
        self.assertEqual(json.loads(raw), doc)

    def test_create_never_overwrites(self):
        store.create(self.path, {"count": 0})
        with self.assertRaises(FileExistsError):
            store.create(self.path, {"count": 99})
        self.assertEqual(store.read(self.path)[1], {"count": 0})

    def test_only_json_objects_are_stored(self):
        with self.assertRaises(ValueError):
            store.create(self.path, ["not", "an", "object"])
        self.assertFalse(self.path.exists())

    def test_update_applies_change(self):
        store.create(self.path, {"count": 0})
        result = store.update(self.path, lambda doc: doc.__setitem__("count", 5))
        self.assertEqual(result, {"count": 5})
        self.assertEqual(store.read(self.path)[1], {"count": 5})

    def test_stale_snapshot_is_refused_and_nothing_written(self):
        store.create(self.path, {"count": 0})
        raw, _ = store.read(self.path)
        store.update(self.path, lambda doc: doc.__setitem__("count", 1))
        with self.assertRaises(store.Conflict):
            store.update(self.path, lambda doc: doc.__setitem__("count", 100), expected_raw=raw)
        self.assertEqual(store.read(self.path)[1], {"count": 1})

    def test_matching_snapshot_is_accepted(self):
        store.create(self.path, {"count": 0})
        raw, _ = store.read(self.path)
        store.update(self.path, lambda doc: doc.__setitem__("count", 1), expected_raw=raw)
        self.assertEqual(store.read(self.path)[1], {"count": 1})

    def test_failed_validation_leaves_document_unchanged(self):
        store.create(self.path, {"count": 0})

        def non_negative(doc):
            if doc["count"] < 0:
                raise ValueError("negative")

        with self.assertRaises(ValueError):
            store.update(self.path, lambda doc: doc.__setitem__("count", -1), validate=non_negative)
        self.assertEqual(store.read(self.path)[1], {"count": 0})

    def test_failing_change_leaves_document_unchanged(self):
        store.create(self.path, {"count": 0})

        def broken(doc):
            doc["count"] = 7
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            store.update(self.path, broken)
        self.assertEqual(store.read(self.path)[1], {"count": 0})

    def test_no_op_change_does_not_rewrite(self):
        store.create(self.path, {"count": 0})
        before = self.path.stat().st_mtime_ns
        store.update(self.path, lambda doc: None)
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_no_temporary_files_left_behind(self):
        store.create(self.path, {"count": 0})
        for value in range(5):
            store.update(self.path, lambda doc, v=value: doc.__setitem__("count", v))
        leftovers = [p.name for p in Path(self.directory.name).iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_concurrent_threads_lose_no_updates(self):
        store.create(self.path, {"count": 0})
        with ThreadPoolExecutor(max_workers=8) as pool:
            for future in [pool.submit(store.update, self.path, lambda doc: doc.__setitem__("count", doc["count"] + 1))
                           for _ in range(200)]:
                future.result()
        self.assertEqual(store.read(self.path)[1], {"count": 200})

    def test_concurrent_processes_lose_no_updates(self):
        store.create(self.path, {"count": 0})
        context = multiprocessing.get_context("spawn")
        workers = [context.Process(target=_increment_in_process, args=(str(self.path), 25)) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(store.read(self.path)[1], {"count": 100})


if __name__ == "__main__":
    unittest.main()
