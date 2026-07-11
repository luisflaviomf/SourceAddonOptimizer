from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import batch_optimize_maximum as maximum


class MaximumBlenderPureTests(unittest.TestCase):
    def test_module_imports_without_blender_and_parses_strict_candidate(self) -> None:
        self.assertIsNone(maximum.bpy)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.json"
            candidate.write_text(
                json.dumps(
                    {
                        "candidate_id": "meshopt-r035",
                        "engine": "meshoptimizer",
                        "ratio": 0.35,
                        "target_error": 0.01,
                        "update_vertices": True,
                        "region_overrides": {"thin": 0.7},
                    }
                ),
                encoding="utf-8",
            )
            settings = maximum.parse_args(
                [str(root), "--candidate-json", str(candidate), "--meshopt-dll", str(root / "bridge.dll")]
            )
        self.assertEqual(settings.candidate.candidate_id, "meshopt-r035")
        self.assertEqual(settings.candidate.ratio, 0.35)
        self.assertEqual(dict(settings.candidate.region_overrides), {"thin": 0.7})

    def test_candidate_rejects_unknown_fields_bool_numbers_and_traversal(self) -> None:
        valid = {
            "candidate_id": "safe-id",
            "engine": "meshoptimizer",
            "ratio": 0.5,
            "target_error": 0.01,
            "update_vertices": False,
            "region_overrides": {},
        }
        mutations = (
            {**valid, "surprise": 1},
            {**valid, "ratio": True},
            {**valid, "target_error": float("inf")},
            {**valid, "candidate_id": "../escape"},
            {**valid, "engine": "blender"},
            {**valid, "region_overrides": {"thin": 0.0}},
        )
        for payload in mutations:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    maximum.load_candidate_payload(payload)

    def test_geometry_classification_uses_shape_not_name(self) -> None:
        thin_vertices = ((0, 0, 0), (10, 0, 0), (10, 1, 0), (0, 1, 0))
        triangles = ((0, 1, 2), (0, 2, 3))
        a = maximum.classify_geometry(thin_vertices, triangles)
        b = maximum.classify_geometry(thin_vertices, triangles)
        self.assertEqual(a, b)
        self.assertTrue(a.is_thin)

        cube = (
            (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
            (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
        )
        cube_triangles = ((0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6))
        self.assertFalse(maximum.classify_geometry(cube, cube_triangles).is_thin)

    def test_policy_locks_topological_border_and_protects_material_boundary(self) -> None:
        vertices = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))
        triangles = ((0, 1, 2), (0, 2, 3))
        policy = maximum.derive_simplification_policy(
            vertices,
            triangles,
            material_ids=(0, 1),
            skin_weights=((1, 0, 0, 0),) * 4,
        )
        self.assertTrue(all(flag & maximum.LOCK for flag in policy.vertex_flags))
        self.assertTrue(policy.vertex_flags[0] & maximum.PROTECT)
        self.assertTrue(policy.vertex_flags[2] & maximum.PROTECT)
        self.assertTrue(policy.meshopt_options & maximum.SIMPLIFY_REGULARIZE_LIGHT)

    def test_weight_repair_clamps_normalizes_and_rejects_zero_sum(self) -> None:
        self.assertEqual(maximum.repair_weights((2.0, -1.0, 1.0, 0.0)), (0.5, 0.0, 0.5, 0.0))
        with self.assertRaisesRegex(ValueError, "zero-sum"):
            maximum.repair_weights((0.0, -1.0, 0.0, 0.0))

    def test_qc_rewrite_is_token_aware_and_writes_opt_name(self) -> None:
        original = (
            '$modelname "cars/a.mdl"\n$body body "body.smd"\n'
            '$collisionmodel "physics.smd"\n$sequence idle "idle.smd"\n'
        )
        rewritten = maximum.rewrite_qc_references(
            original,
            {"body.smd": "output/body_opt.smd", "physics.smd": "output/physics_opt.smd"},
        )
        self.assertIn('$body body "output/body_opt.smd"', rewritten)
        self.assertIn('$collisionmodel "output/physics_opt.smd"', rewritten)
        self.assertIn('$sequence idle "idle.smd"', rewritten)
        self.assertIn('$modelname "cars/a.mdl"', rewritten)

    def test_geometry_references_cover_body_model_and_collision_without_sequences(self) -> None:
        qc = (
            '$body body "body.smd"\n$model view "view.smd"\n'
            '$collisionmodel "physics.smd"\n$collisionjoints "joints.smd"\n'
            '$sequence idle "animation.smd"\n'
        )
        self.assertEqual(
            maximum.geometry_references(qc),
            ("body.smd", "view.smd", "physics.smd", "joints.smd"),
        )

    def test_geometry_policy_rejects_out_of_range_topology(self) -> None:
        with self.assertRaises(ValueError):
            maximum.derive_simplification_policy(
                ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                ((0, 1, 99),),
                material_ids=(0,),
                skin_weights=None,
            )

    def test_smd_audit_counts_triangles_materials_and_bones(self) -> None:
        smd = """version 1
nodes
0 \"root\" -1
end
skeleton
time 0
0 0 0 0 0 0 0
end
triangles
metal
0 0 0 0 0 0 1 0 0
0 1 0 0 0 0 1 1 0
0 0 1 0 0 0 1 0 1
end
"""
        audit = maximum.audit_smd_text(smd)
        self.assertEqual(audit.triangle_count, 1)
        self.assertEqual(audit.materials, ("metal",))
        self.assertEqual(audit.bones, (0,))


if __name__ == "__main__":
    unittest.main()
