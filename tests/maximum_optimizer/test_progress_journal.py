from __future__ import annotations

import threading
import unittest

from maximum_optimizer.progress_journal import DurableProgressJournal


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class DurableProgressJournalTests(unittest.TestCase):
    def test_coalesces_and_force_flushes(self):
        writes: list[dict[str, int]] = []
        clock = FakeClock()
        journal = DurableProgressJournal(writes.append, min_interval=0.25, clock=clock)

        self.assertTrue(journal.publish(lambda: {"n": 1}))
        self.assertFalse(journal.publish(lambda: {"n": 2}))
        self.assertTrue(journal.publish(lambda: {"n": 3}, force=True))

        self.assertEqual(writes, [{"n": 1}, {"n": 3}])

    def test_elapsed_interval_flushes_and_suppressed_snapshot_is_not_built(self):
        writes: list[dict[str, int]] = []
        built: list[int] = []
        clock = FakeClock()
        journal = DurableProgressJournal(writes.append, min_interval=0.25, clock=clock)

        journal.publish(lambda: {"n": 1})
        self.assertFalse(journal.publish(lambda: built.append(2) or {"n": 2}))
        clock.advance(0.25)
        self.assertTrue(journal.publish(lambda: built.append(3) or {"n": 3}))

        self.assertEqual(built, [3])
        self.assertEqual(writes, [{"n": 1}, {"n": 3}])

    def test_concurrent_first_publish_writes_once(self):
        writes: list[dict[str, int]] = []
        journal = DurableProgressJournal(writes.append, min_interval=60.0, clock=lambda: 1.0)
        barrier = threading.Barrier(8)
        results: list[bool] = []
        lock = threading.Lock()

        def publish(value: int) -> None:
            barrier.wait()
            result = journal.publish(lambda: {"n": value})
            with lock:
                results.append(result)

        threads = [threading.Thread(target=publish, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(writes), 1)

    def test_failed_write_is_not_counted_as_a_flush(self):
        calls = 0

        def writer(payload: dict[str, int]) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("disk unavailable")

        journal = DurableProgressJournal(writer, min_interval=60.0, clock=lambda: 1.0)

        with self.assertRaisesRegex(OSError, "disk unavailable"):
            journal.publish(lambda: {"n": 1})
        self.assertTrue(journal.publish(lambda: {"n": 2}))
        self.assertEqual(calls, 2)

    def test_rejects_invalid_constructor_and_publish_inputs(self):
        for invalid in (-1, float("inf"), float("nan"), True):
            with self.subTest(min_interval=invalid), self.assertRaises((TypeError, ValueError)):
                DurableProgressJournal(lambda _payload: None, min_interval=invalid)
        with self.assertRaises(TypeError):
            DurableProgressJournal(None, min_interval=1.0)
        journal = DurableProgressJournal(lambda _payload: None, min_interval=1.0)
        with self.assertRaises(TypeError):
            journal.publish(None)
        with self.assertRaises(TypeError):
            journal.publish(lambda: {}, force=1)


if __name__ == "__main__":
    unittest.main()
