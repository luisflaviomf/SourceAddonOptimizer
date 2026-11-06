from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest import mock

from maximum_optimizer import metrics
from maximum_optimizer.silhouette_backend import (
    SilhouetteBackend,
    configure_silhouette_backend,
    current_silhouette_backend,
    reset_silhouette_backend,
)
from maximum_optimizer.silhouette_native import NativeSilhouettePackage
from tests.maximum_optimizer.test_metrics import CONTRACT, make_disc


PACKAGE = NativeSilhouettePackage(
    Path(r"C:\validated-tools\_internal\maximum_optimizer\native\bin\win-x64\meshopt_bridge.dll"),
    "0" * 64,
    1,
    "1.0.0",
    "maximum-silhouette-raw-v1-20260722",
    "x64",
)


class _FailingKernel:
    api_version = "1.0.0"
    build_id = "maximum-silhouette-raw-v1-20260722"
    architecture = "x64"

    def __init__(self) -> None:
        self.calls = 0

    def measure(self, *_args, **_kwargs):
        self.calls += 1
        raise RuntimeError("injected native failure after preparation")


class SilhouetteBackendTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_silhouette_backend()

    def test_load_failure_is_process_sticky_and_never_retries(self) -> None:
        attempts = 0

        def fail_factory(_package):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("bad ABI")

        backend = SilhouetteBackend(PACKAGE, kernel_factory=fail_factory)
        self.assertFalse(backend.initialize())
        self.assertFalse(backend.initialize())
        self.assertEqual(backend.state, "legacy")
        self.assertEqual(attempts, 1)
        self.assertIn("bad ABI", backend.snapshot()["fallback_reason"])

    def test_retired_environment_variable_and_adjacent_dll_are_ignored(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"MAXIMUM_SILHOUETTE_EXPERIMENT_DLL": str(Path.cwd() / "meshopt_bridge.dll")},
            clear=False,
        ):
            reference = metrics.prepare_region_reference(make_disc(12), CONTRACT)
        self.assertIsNone(reference.silhouette_mask_batch)
        self.assertEqual(current_silhouette_backend().state, "legacy")

    def test_failure_after_raw_preparation_recomputes_the_complete_exact_result(self) -> None:
        original = make_disc(64)
        candidate = make_disc(7)

        reset_silhouette_backend()
        legacy_reference = metrics.prepare_region_reference(original, CONTRACT)
        expected = metrics.measure_region_prepared(legacy_reference, candidate)

        kernel = _FailingKernel()
        configure_silhouette_backend(PACKAGE, kernel_factory=lambda _package: kernel)
        backend = current_silhouette_backend()
        self.assertTrue(backend.initialize())
        native_reference = metrics.prepare_region_reference(original, CONTRACT)
        self.assertIsNotNone(native_reference.silhouette_mask_batch)

        actual = metrics.measure_region_prepared(native_reference, candidate)

        self.assertEqual(actual, expected)
        self.assertEqual(kernel.calls, 1)
        self.assertEqual(backend.state, "legacy")
        later_reference = metrics.prepare_region_reference(original, CONTRACT)
        self.assertIsNone(later_reference.silhouette_mask_batch)
        metrics.measure_region_prepared(later_reference, candidate)
        self.assertEqual(kernel.calls, 1)
        snapshot = backend.snapshot()
        self.assertTrue(snapshot["fallback"])
        self.assertEqual(snapshot["fallback_stage"], "measure")
        self.assertIn("injected native failure", snapshot["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
