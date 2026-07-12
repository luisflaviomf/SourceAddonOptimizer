from __future__ import annotations

import unittest

from maximum_optimizer.compiler_aware import (
    CompiledCostCalibration,
    FamilyCandidate,
    compiler_proxy_bytes,
    select_family_winner,
    require_triangular_mesh,
    engine_evidence,
    preserve_whole_source,
    exact_source_payload,
    allows_exact_fallback,
    allows_strategy_exact_fallback,
    provenance_status,
    move_modifier_first,
)


class CompilerAwareTests(unittest.TestCase):
    def test_round_research_runtime_failure_is_exact_fallback_only_for_that_strategy(self) -> None:
        error = RuntimeError("Blender modifier exploded")

        self.assertTrue(
            allows_strategy_exact_fallback(error, strategy="round-planar-priority-v1")
        )
        self.assertFalse(
            allows_strategy_exact_fallback(error, strategy="blender-adaptive-v1")
        )
        raw = b"version 1\r\ntriangles\r\nend\r\n\xff"
        self.assertIs(exact_source_payload(raw), raw)

    def test_proxy_weights_compiled_vertices_more_than_triangles(self) -> None:
        self.assertEqual(compiler_proxy_bytes(100, 200), 10_600)
        self.assertLess(compiler_proxy_bytes(99, 205), compiler_proxy_bytes(100, 200))

    def test_calibration_requires_snapshots_and_bounds_proxy_error(self) -> None:
        calibration = CompiledCostCalibration.from_snapshots(
            ((100, 200, 10_600), (200, 400, 21_200))
        )
        self.assertEqual(calibration.max_absolute_error_bytes, 0)
        with self.assertRaisesRegex(ValueError, "two snapshots"):
            CompiledCostCalibration.from_snapshots(((100, 200, 10_600),))

    def test_winner_is_strictly_smaller_than_baseline_and_gate_eligible(self) -> None:
        candidates = (
            FamilyCandidate("b050", 1_000, True, True, True),
            FamilyCandidate("smaller-unverified", 800, True, False, False),
            FamilyCandidate("larger-pass", 1_010, True, True, True),
            FamilyCandidate("adaptive", 900, True, True, True),
        )
        self.assertEqual(select_family_winner(1_000, candidates).strategy, "adaptive")

    def test_no_winner_when_only_unverified_or_regressing_candidates_exist(self) -> None:
        candidates = (
            FamilyCandidate("unverified", 700, True, False, False),
            FamilyCandidate("regression", 1_001, True, True, True),
        )
        self.assertIsNone(select_family_winner(1_000, candidates))

    def test_post_modifier_mesh_must_be_triangle_exact_before_smoothing(self) -> None:
        require_triangular_mesh(12, 12)
        with self.assertRaisesRegex(RuntimeError, "triangulated"):
            require_triangular_mesh(11, 12)

    def test_engine_evidence_never_mislabels_blender_as_meshoptimizer(self) -> None:
        self.assertEqual(
            engine_evidence("blender", "blender-adaptive-v1", (5, 0, 1)),
            {"engine": "blender", "engine_version": "5.0.1", "strategy": "blender-adaptive-v1"},
        )

    def test_exact_preservation_requires_every_region_at_one(self) -> None:
        self.assertTrue(preserve_whole_source((1.0, 1.0)))
        self.assertFalse(preserve_whole_source((1.0, 0.4)))
        self.assertFalse(preserve_whole_source(()))

    def test_exact_source_payload_does_not_normalize_crlf(self) -> None:
        raw = b"version 1\r\nnodes\r\nend\r\n"
        self.assertIs(exact_source_payload(raw), raw)

    def test_exact_fallback_is_limited_to_known_appearance_failures(self) -> None:
        self.assertTrue(allows_exact_fallback("normal must be non-zero"))
        self.assertTrue(allows_exact_fallback("export lost all hard-normal seam evidence"))
        self.assertFalse(allows_exact_fallback("cannot map material"))

    def test_fallback_provenance_is_disclosed_as_preserved(self) -> None:
        self.assertEqual(
            provenance_status("normal must be non-zero", strategy="blender-adaptive-v1"),
            ("preserved", "exact-source-fallback-v1: normal must be non-zero"),
        )
        self.assertEqual(
            provenance_status(None, strategy="blender-adaptive-v1"),
            ("optimized", "blender-adaptive-v1"),
        )
        self.assertEqual(
            provenance_status(None, strategy="blender-importance-map-v1"),
            ("optimized", "blender-importance-map-v1"),
        )
        self.assertEqual(
            provenance_status(None, strategy="round-planar-priority-v1"),
            ("optimized", "round-planar-priority-v1"),
        )
        with self.assertRaisesRegex(ValueError, "strategy"):
            provenance_status(None, strategy="made-up")

    def test_decimate_modifier_is_moved_before_imported_armature(self) -> None:
        class Modifier:
            def __init__(self, name): self.name = name
        class Modifiers(list):
            def find(self, name): return next(i for i, item in enumerate(self) if item.name == name)
            def move(self, source, target): self.insert(target, self.pop(source))
        armature, decimate = Modifier("Armature"), Modifier("MaximumCompilerAware")
        modifiers = Modifiers((armature, decimate))
        move_modifier_first(modifiers, decimate)
        self.assertIs(modifiers[0], decimate)


if __name__ == "__main__":
    unittest.main()
