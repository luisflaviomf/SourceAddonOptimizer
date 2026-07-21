from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath
import tempfile
import unittest

from maximum_optimizer.cache import FileRegionCache
from maximum_optimizer.contracts import ValidationDecision
from maximum_optimizer.qc_graph import QcOccurrence
from maximum_optimizer.regions import build_region_graph
from maximum_optimizer.search import RegionRequest, candidate_ratios, optimize_region
from maximum_optimizer.smd import parse_smd


FIXTURES = Path(__file__).resolve().parent / "fixtures"
OCCURRENCE = QcOccurrence(
    PurePosixPath("vehicle.qc"), "$body", 2, PurePosixPath("two_components.smd")
)
ORIGINAL = build_region_graph(
    parse_smd((FIXTURES / "two_components.smd").read_text(encoding="utf-8")),
    OCCURRENCE,
).regions[1]
NORMAL = build_region_graph(
    parse_smd((FIXTURES / "two_components_OPT.smd").read_text(encoding="utf-8")),
    OCCURRENCE,
).regions[1]
REDUCED = replace(NORMAL, triangles=NORMAL.triangles[:1])
PASS = ValidationDecision(True, (), 0.5)
NEAR_PASS = ValidationDecision(True, (), 0.10)
FAIL = ValidationDecision(False, ("silhouette",), 0.0)


def request(
    *,
    normal_validation: ValidationDecision,
    classified_ratio: float = 0.2,
    region_suffix: str = "body",
    engine_version: int = 10200,
) -> RegionRequest:
    return RegionRequest(
        original=ORIGINAL,
        normal=NORMAL,
        classified_ratio=classified_ratio,
        normal_validation=normal_validation,
        original_triangle_count=100,
        normal_triangle_count=35,
        source_sha256=("a" if region_suffix == "body" else "b") * 64,
        profile_sha256="c" * 64,
        attribute_contract_sha256="d" * 64,
        engine_version=engine_version,
    )


class CandidateScheduleTests(unittest.TestCase):
    def test_passing_normal_tests_only_more_aggressive_targets(self) -> None:
        self.assertEqual(candidate_ratios(request(normal_validation=PASS, classified_ratio=0.10)), (0.10, 0.225))

    def test_near_limit_normal_is_kept_without_simplifier_call(self) -> None:
        self.assertEqual(candidate_ratios(request(normal_validation=NEAR_PASS)), ())

    def test_failed_normal_has_exactly_three_monotonic_recovery_targets(self) -> None:
        self.assertEqual(candidate_ratios(request(normal_validation=FAIL)), (0.2, 0.6, 0.85))


class AdaptiveSearchTests(unittest.TestCase):
    def test_failed_region_never_exceeds_three_simplifier_calls(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as raw:
            decision = optimize_region(
                request(normal_validation=FAIL),
                lambda region, ratio: calls.append(ratio) or region,
                lambda candidate: FAIL,
                FileRegionCache(Path(raw)),
            )

        self.assertEqual(decision.representation, "original")
        self.assertEqual(calls, [0.2, 0.6, 0.85])
        self.assertEqual(decision.evaluations, 3)

    def test_simplifier_errors_are_local_and_respect_the_evaluation_bound(self) -> None:
        calls = []

        def fail_locally(_region, ratio):
            calls.append(ratio)
            raise RuntimeError("synthetic native failure")

        with tempfile.TemporaryDirectory() as raw:
            decision = optimize_region(
                request(normal_validation=FAIL),
                fail_locally,
                lambda _candidate: self.fail("failed candidates must not be validated"),
                FileRegionCache(Path(raw)),
            )

        self.assertEqual(decision.representation, "original")
        self.assertEqual(decision.evaluations, 3)
        self.assertEqual(calls, [0.2, 0.6, 0.85])
        self.assertIn("3 simplifier errors", decision.reason)

    def test_failed_wheel_does_not_revert_passing_body(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cache = FileRegionCache(Path(raw))
            body = optimize_region(
                request(normal_validation=PASS, region_suffix="body"),
                lambda _region, _ratio: REDUCED,
                lambda _candidate: PASS,
                cache,
            )
            wheel = optimize_region(
                request(normal_validation=FAIL, region_suffix="wheel"),
                lambda region, _ratio: region,
                lambda _candidate: FAIL,
                cache,
            )

        self.assertEqual(body.representation, "aggressive")
        self.assertEqual(wheel.representation, "original")
        self.assertIs(body.selected, REDUCED)
        self.assertIs(wheel.selected, ORIGINAL)

    def test_near_limit_normal_uses_zero_simplifier_evaluations(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            decision = optimize_region(
                request(normal_validation=NEAR_PASS),
                lambda _region, _ratio: self.fail("simplifier must not run"),
                lambda _candidate: self.fail("validator must not rerun"),
                FileRegionCache(Path(raw)),
            )

        self.assertEqual(decision.representation, "normal")
        self.assertEqual(decision.evaluations, 0)

    def test_no_op_simplifier_result_is_not_reported_as_aggressive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            decision = optimize_region(
                request(normal_validation=PASS),
                lambda _region, _ratio: NORMAL,
                lambda _candidate: PASS,
                FileRegionCache(Path(raw)),
            )

        self.assertEqual(decision.representation, "normal")
        self.assertIs(decision.selected, NORMAL)

    def test_cache_hit_skips_native_evaluation_and_is_profile_versioned(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as raw:
            cache = FileRegionCache(Path(raw))
            first = optimize_region(
                request(normal_validation=PASS),
                lambda _region, ratio: calls.append(ratio) or REDUCED,
                lambda _candidate: PASS,
                cache,
            )
            second = optimize_region(
                request(normal_validation=PASS),
                lambda _region, _ratio: self.fail("cache hit must skip simplifier"),
                lambda _candidate: PASS,
                cache,
            )
            changed_engine = optimize_region(
                request(normal_validation=PASS, engine_version=10201),
                lambda _region, ratio: calls.append(ratio) or REDUCED,
                lambda _candidate: PASS,
                cache,
            )
            temporary_files = tuple(Path(raw).rglob("*.tmp"))

        self.assertEqual(first.evaluations, 1)
        self.assertEqual(second.evaluations, 0)
        self.assertEqual(second.cache_hits, 1)
        self.assertEqual(changed_engine.evaluations, 1)
        self.assertEqual(calls, [0.2, 0.2])
        self.assertEqual(temporary_files, ())


if __name__ == "__main__":
    unittest.main()
