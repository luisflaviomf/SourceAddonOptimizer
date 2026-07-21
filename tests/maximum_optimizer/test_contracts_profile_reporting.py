from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from maximum_optimizer.contracts import RegionKey, ValidationDecision
from maximum_optimizer.profile import load_profile
from maximum_optimizer.reporting import comparable_model_bytes, reduction_percent


REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPO_ROOT / "maximum_optimizer" / "profiles" / "maximum-adaptive-v2.json"


class ContractTests(unittest.TestCase):
    def test_region_key_is_a_frozen_lowercase_sha256_identity(self) -> None:
        key = RegionKey("r-" + "a" * 64)

        self.assertEqual(key.value, "r-" + "a" * 64)
        with self.assertRaises(FrozenInstanceError):
            key.value = "r-" + "b" * 64  # type: ignore[misc]
        for invalid in ("a" * 64, "r-" + "A" * 64, "r-short"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                RegionKey(invalid)

    def test_validation_decision_requires_consistent_gates_and_margin(self) -> None:
        passed = ValidationDecision(True, (), 0.25)

        self.assertTrue(passed.passed)
        with self.assertRaises(ValueError):
            ValidationDecision(True, ("surface",), 0.25)
        with self.assertRaises(ValueError):
            ValidationDecision(False, (), 0.0)
        with self.assertRaises(ValueError):
            ValidationDecision(True, (), 1.1)


class ProfileTests(unittest.TestCase):
    def test_profile_is_strict_and_hash_sealed(self) -> None:
        profile = load_profile(PROFILE_PATH)

        self.assertEqual(profile.version, "maximum-adaptive-v2")
        self.assertFalse(profile.calibrated)
        self.assertEqual(profile.max_simplifier_evaluations, 3)
        self.assertLess(profile.limits.normal_p95_degrees, 10.0)
        self.assertLessEqual(profile.limits.silhouette_boundary_p95_px, 1.5)
        self.assertEqual(profile.sha256, hashlib.sha256(PROFILE_PATH.read_bytes()).hexdigest())

    def test_profile_rejects_unknown_fields_and_non_finite_limits(self) -> None:
        payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            unknown = root / "unknown.json"
            unknown.write_text(json.dumps({**payload, "extra": True}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fields"):
                load_profile(unknown)

            non_finite = root / "non-finite.json"
            payload["limits"]["surface_p95"] = float("nan")
            non_finite.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "finite"):
                load_profile(non_finite)


class SizeAccountingTests(unittest.TestCase):
    def test_dx80_is_reported_separately_and_never_counted_as_saving(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original = root / "original"
            final = root / "final"
            original.mkdir()
            final.mkdir()
            (original / "a.mdl").write_bytes(b"m" * 10)
            (original / "a.vvd").write_bytes(b"v" * 20)
            (original / "a.dx90.vtx").write_bytes(b"9" * 30)
            (original / "a.dx80.vtx").write_bytes(b"8" * 40)
            (final / "a.mdl").write_bytes(b"m" * 10)
            (final / "a.vvd").write_bytes(b"v" * 10)
            (final / "a.dx90.vtx").write_bytes(b"9" * 20)

            before = comparable_model_bytes(original)
            after = comparable_model_bytes(final)

        self.assertEqual(before.comparable_bytes, 60)
        self.assertEqual(before.dx80_bytes, 40)
        self.assertEqual(after.comparable_bytes, 40)
        self.assertEqual(after.dx80_bytes, 0)
        self.assertAlmostEqual(reduction_percent(before, after), 100.0 / 3.0)

    def test_empty_original_has_no_invented_reduction(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            before = comparable_model_bytes(root)
            after = comparable_model_bytes(root)

        self.assertEqual(reduction_percent(before, after), 0.0)


if __name__ == "__main__":
    unittest.main()
