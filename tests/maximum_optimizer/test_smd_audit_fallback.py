from __future__ import annotations

import unittest
from unittest.mock import patch

import batch_optimize_maximum as maximum


class SmdAuditFallbackTests(unittest.TestCase):
    def base(self) -> maximum.SmdAudit:
        return maximum.SmdAudit(
            1, ("mat",), (0,), ((0, "root", -1),), (0,), (0, 1, 0, 1), 3,
            ((0,),), 1, 1, 1,
        )

    def test_every_smd_audit_gate_raises_the_typed_validation_error(self) -> None:
        base = self.base()
        changes = (
            {"materials": ()},
            {"bone_nodes": ((0, "renamed", -1),)},
            {"influence_bones": ()},
            {"influence_sets": ()},
            {"uv_seam_positions": 0},
            {"hard_normal_positions": 0},
            {"position_normal_keys": 2},
            {"uv_bounds": None},
        )
        for change in changes:
            with self.subTest(change=change):
                changed = maximum.SmdAudit(**{**base.__dict__, **change})
                with self.assertRaises(maximum.SmdAuditValidationError):
                    maximum.validate_smd_audits(base, changed)

    def test_exact_fallback_accepts_typed_audit_error_but_not_internal_runtime_error(self) -> None:
        self.assertTrue(maximum.allows_exact_fallback(
            maximum.SmdAuditValidationError("export changed SMD bone names/order/hierarchy")
        ))
        self.assertFalse(maximum.allows_exact_fallback(RuntimeError("Blender import exploded")))

    def test_direct_position_fallback_is_limited_to_known_per_source_failures(self) -> None:
        strategy = "meshopt-direct-position-v1"
        for error in (
            maximum.SmdAuditValidationError("export lost SMD material-bone influence pairs"),
            RuntimeError("meshoptimizer did not reduce this mesh"),
            ValueError("corner normal is invalid"),
            ValueError("normal must be non-zero"),
            ValueError("direct SMD triangle is degenerate"),
        ):
            with self.subTest(error=str(error)):
                self.assertTrue(maximum.allows_strategy_exact_fallback(
                    error, strategy=strategy,
                ))
        self.assertFalse(maximum.allows_strategy_exact_fallback(
            RuntimeError("meshoptimizer internal state corrupted"),
            strategy=strategy,
        ))

    def test_preexisting_incomplete_normals_are_allowed_but_new_loss_is_rejected(self) -> None:
        incomplete = maximum.SmdAudit(
            2, ("mat",), (0,), ((0, "root", -1),), (0,), None, 3,
            ((0,),), 0, 0, 1,
        )
        maximum.validate_smd_audits(incomplete, incomplete)
        degraded = maximum.SmdAudit(**{
            **incomplete.__dict__, "triangle_count": 3, "finite_normal_count": 3,
        })
        with self.assertRaisesRegex(maximum.SmdAuditValidationError, "coverage"):
            maximum.validate_smd_audits(incomplete, degraded)

    def test_triangle_reduction_cannot_mask_worse_finite_normal_coverage(self) -> None:
        source = maximum.SmdAudit(**{
            **self.base().__dict__, "triangle_count": 4, "finite_normal_count": 6,
        })
        equal_coverage = maximum.SmdAudit(**{
            **source.__dict__, "triangle_count": 2, "finite_normal_count": 3,
        })
        maximum.validate_smd_audits(source, equal_coverage)

        worse_coverage = maximum.SmdAudit(**{
            **equal_coverage.__dict__, "finite_normal_count": 2,
        })
        with self.assertRaisesRegex(maximum.SmdAuditValidationError, "coverage"):
            maximum.validate_smd_audits(source, worse_coverage)

    def test_candidate_with_zero_valid_normals_fails_when_source_has_any(self) -> None:
        source = maximum.SmdAudit(**{
            **self.base().__dict__, "triangle_count": 2, "finite_normal_count": 1,
        })
        candidate = maximum.SmdAudit(**{
            **source.__dict__, "triangle_count": 1, "finite_normal_count": 0,
        })
        with self.assertRaisesRegex(maximum.SmdAuditValidationError, "zero valid normals"):
            maximum.validate_smd_audits(source, candidate)

    def test_only_exported_smd_parse_errors_are_typed_for_exact_fallback(self) -> None:
        malformed = "triangles\nmat\n0 0 0 0 broken 0 1 0 0\nend\n"
        with self.assertRaises(ValueError):
            maximum.audit_smd_text(malformed)
        with self.assertRaises(maximum.SmdAuditValidationError) as raised:
            maximum.audit_exported_smd_text(malformed)
        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertTrue(maximum.allows_exact_fallback(raised.exception))

        with patch.object(maximum, "audit_smd_text", side_effect=RuntimeError("internal")):
            with self.assertRaisesRegex(RuntimeError, "internal"):
                maximum.audit_exported_smd_text("ignored")


if __name__ == "__main__":
    unittest.main()
