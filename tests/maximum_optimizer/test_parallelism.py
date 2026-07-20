import unittest

from maximum_optimizer.parallelism import (
    GIB,
    MemorySnapshot,
    current_memory_job_limit,
    resolve_maximum_parallelism,
)


class MaximumParallelismTests(unittest.TestCase):
    def test_auto_targets_half_of_twenty_processors(self) -> None:
        plan = resolve_maximum_parallelism(
            0,
            logical_processors=20,
            memory=MemorySnapshot(32 * GIB, 16 * GIB),
        )

        self.assertEqual((plan.cpu_target_jobs, plan.effective_jobs), (10, 10))
        self.assertEqual(plan.blender_threads, 1)
        self.assertFalse(plan.memory_throttled)

    def test_auto_throttles_when_only_eight_gib_are_available(self) -> None:
        plan = resolve_maximum_parallelism(
            0,
            logical_processors=20,
            memory=MemorySnapshot(32 * GIB, 8 * GIB),
        )

        self.assertEqual((plan.cpu_target_jobs, plan.effective_jobs), (10, 6))
        self.assertTrue(plan.memory_throttled)

    def test_unknown_memory_caps_auto_but_serial_keeps_native_blender_threads(self) -> None:
        auto = resolve_maximum_parallelism(
            0, logical_processors=20, memory=None
        )
        serial = resolve_maximum_parallelism(
            1, logical_processors=20, memory=None
        )

        self.assertEqual((auto.effective_jobs, auto.blender_threads), (4, 1))
        self.assertEqual((serial.effective_jobs, serial.blender_threads), (1, 0))

    def test_dynamic_guard_does_not_return_zero_slots(self) -> None:
        plan = resolve_maximum_parallelism(
            8,
            logical_processors=20,
            memory=MemorySnapshot(32 * GIB, 16 * GIB),
        )

        self.assertEqual(
            current_memory_job_limit(
                plan, MemorySnapshot(32 * GIB, 4 * GIB)
            ),
            1,
        )

    def test_explicit_jobs_are_bounded_by_logical_processors(self) -> None:
        plan = resolve_maximum_parallelism(
            99,
            logical_processors=12,
            memory=MemorySnapshot(32 * GIB, 32 * GIB),
        )

        self.assertEqual(plan.cpu_target_jobs, 12)
        self.assertEqual(plan.effective_jobs, 12)

    def test_rejects_boolean_and_negative_job_requests(self) -> None:
        for value in (True, -1):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    resolve_maximum_parallelism(value)


if __name__ == "__main__":
    unittest.main()
