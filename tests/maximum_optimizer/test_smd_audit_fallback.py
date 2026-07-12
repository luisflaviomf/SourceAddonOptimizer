from __future__ import annotations

import unittest

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

    def test_preexisting_incomplete_normals_are_allowed_but_new_loss_is_rejected(self) -> None:
        incomplete = maximum.SmdAudit(
            2, ("mat",), (0,), ((0, "root", -1),), (0,), None, 3,
            ((0,),), 0, 0, 1,
        )
        maximum.validate_smd_audits(incomplete, incomplete)
        degraded = maximum.SmdAudit(**{
            **incomplete.__dict__, "triangle_count": 3, "finite_normal_count": 3,
        })
        with self.assertRaisesRegex(maximum.SmdAuditValidationError, "incomplete"):
            maximum.validate_smd_audits(incomplete, degraded)


if __name__ == "__main__":
    unittest.main()
