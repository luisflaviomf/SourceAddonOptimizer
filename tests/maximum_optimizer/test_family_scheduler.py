from __future__ import annotations

import threading
import time
import unittest

from maximum_optimizer.family_scheduler import (
    FamilySchedulerUpdate,
    FamilyWorkItem,
    run_family_jobs,
)


class FamilySchedulerTests(unittest.TestCase):
    def test_bounds_active_jobs_and_returns_canonical_order(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def worker(value: int) -> int:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep((4 - value) * 0.01)
            with lock:
                active -= 1
            return value * 10

        items = tuple(FamilyWorkItem(index, index + 1, index) for index in range(4))

        self.assertEqual(
            run_family_jobs(items, max_workers=2, worker=worker),
            (0, 10, 20, 30),
        )
        self.assertEqual(peak, 2)

    def test_submits_largest_estimate_first_with_stable_ties(self):
        submitted: list[str] = []
        items = (
            FamilyWorkItem(0, 10, "a"),
            FamilyWorkItem(1, 30, "b"),
            FamilyWorkItem(2, 30, "c"),
        )

        result = run_family_jobs(
            items,
            max_workers=1,
            worker=lambda value: submitted.append(value) or value,
        )

        self.assertEqual(submitted, ["b", "c", "a"])
        self.assertEqual(result, ("a", "b", "c"))

    def test_dynamic_memory_limit_changes_new_submission_capacity(self):
        active = 0
        peak = 0
        completed = 0
        lock = threading.Lock()
        updates: list[FamilySchedulerUpdate] = []

        def memory_limit() -> int:
            with lock:
                return 1 if completed == 0 else 2

        def worker(value: int) -> int:
            nonlocal active, peak, completed
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
                completed += 1
            return value

        result = run_family_jobs(
            tuple(FamilyWorkItem(index, 4 - index, index) for index in range(4)),
            max_workers=3,
            worker=worker,
            memory_limit=memory_limit,
            on_update=updates.append,
        )

        self.assertEqual(result, (0, 1, 2, 3))
        self.assertEqual(peak, 2)
        self.assertTrue(any(update.memory_throttled for update in updates))
        self.assertTrue(all(update.capacity in {1, 2} for update in updates))
        self.assertEqual(updates[-1].completed, 4)
        self.assertEqual(updates[-1].active, 0)

    def test_cancellation_does_not_start_queued_items_and_drains_active_workers(self):
        cancel = threading.Event()
        release = threading.Event()
        both_started = threading.Event()
        started: list[int] = []
        finished: list[int] = []
        lock = threading.Lock()

        def worker(value: int) -> int:
            with lock:
                started.append(value)
                if len(started) == 2:
                    cancel.set()
                    both_started.set()
            release.wait(2)
            with lock:
                finished.append(value)
            return value

        def release_active() -> None:
            self.assertTrue(both_started.wait(2))
            release.set()

        releaser = threading.Thread(target=release_active)
        releaser.start()
        result = run_family_jobs(
            tuple(FamilyWorkItem(index, index + 1, index) for index in range(5)),
            max_workers=2,
            worker=worker,
            cancel_event=cancel,
        )
        releaser.join(2)

        self.assertFalse(releaser.is_alive())
        self.assertEqual(set(started), {3, 4})
        self.assertEqual(set(finished), {3, 4})
        self.assertEqual(result, (3, 4))

    def test_rejects_invalid_or_ambiguous_work_contracts(self):
        with self.assertRaisesRegex(ValueError, "max_workers"):
            run_family_jobs((), max_workers=0, worker=lambda value: value)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            run_family_jobs(
                (FamilyWorkItem(0, 1, "a"), FamilyWorkItem(0, 2, "b")),
                max_workers=1,
                worker=lambda value: value,
            )
        with self.assertRaisesRegex(ValueError, "memory"):
            run_family_jobs(
                (FamilyWorkItem(0, 1, "a"),),
                max_workers=1,
                worker=lambda value: value,
                memory_limit=lambda: 0,
            )

    def test_does_not_publish_duplicate_updates_while_worker_is_busy(self):
        updates: list[FamilySchedulerUpdate] = []

        run_family_jobs(
            (FamilyWorkItem(0, 1, "family"),),
            max_workers=1,
            worker=lambda value: time.sleep(0.3) or value,
            on_update=updates.append,
        )

        self.assertTrue(updates)
        self.assertTrue(all(
            current != previous
            for previous, current in zip(updates, updates[1:])
        ))


if __name__ == "__main__":
    unittest.main()
