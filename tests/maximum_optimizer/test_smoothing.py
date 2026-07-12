from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest

import batch_optimize_maximum as maximum
from maximum_optimizer.smd_contract import validate_fixed_topology_smd
from maximum_optimizer.smoothing import (
    canonicalize_export_normals, canonicalize_normals_by_identity, reconstruct_smoothing,
)


class SmoothingReconstructionTests(unittest.TestCase):
    @staticmethod
    def _two_triangle_smd() -> str:
        return """version 1
nodes
0 \"root\" -1
1 \"tip\" 0
end
skeleton
time 0
0 0 0 0 0 0 0
1 0 0 0 0 0 0
end
triangles
paint
0 0 0 0 0 0 1 0 0 1 0 1.000000
0 1 0 0 0 0 1 1 0 1 0 1.000000
0 0 1 0 0 0 1 0 1 1 0 1.000000
glass
1 0 0 0 0 1 0 0.5 0.5 1 1 1.000000
1 0 1 0 0 1 0 0 1 1 1 1.000000
1 0 0 1 0 1 0 1 1 1 1 1.000000
end
"""

    def test_fixed_topology_contract_rejects_triangle_reorder_and_winding(self) -> None:
        original = self._two_triangle_smd()
        first, second = original.split("paint\n", 1)[1].split("glass\n", 1)
        header = original.split("paint\n", 1)[0]
        reordered = header + "glass\n" + second.rsplit("end\n", 1)[0] + "paint\n" + first + "end\n"
        with self.assertRaisesRegex(RuntimeError, "triangle order|material"):
            validate_fixed_topology_smd(original, reordered)

        lines = original.splitlines(keepends=True)
        start = lines.index("paint\n") + 1
        lines[start], lines[start + 1] = lines[start + 1], lines[start]
        with self.assertRaisesRegex(RuntimeError, "corner order|winding|payload|position"):
            validate_fixed_topology_smd(original, "".join(lines))

    def test_fixed_topology_contract_rejects_position_uv_bone_and_weight_replacement(self) -> None:
        original = self._two_triangle_smd()
        replacements = (
            ("0 0 0 0 0 0 1 0 0", "0 0.01 0 0 0 0 1 0 0"),
            ("0 0 0 0 0 0 1 0 0", "0 0 0 0 0 0 1 0.25 0"),
            ("1 0 0 0 0 1 0 0.5 0.5", "0 0 0 0 0 1 0 0.5 0.5"),
            ("1 1 1.000000", "1 1 0.500000"),
        )
        for before, after in replacements:
            with self.subTest(after=after):
                with self.assertRaisesRegex(RuntimeError, "payload|position|UV|bone|weight"):
                    validate_fixed_topology_smd(original, original.replace(before, after, 1))

    def test_normal_restore_is_ordinal_and_rejects_true_normal_collapse(self) -> None:
        original = self._two_triangle_smd()
        collapsed = original.replace("0 1 0 0.5 0.5", "0 0 1 0.5 0.5", 1)
        with self.assertRaisesRegex(RuntimeError, "normal.*tolerance"):
            maximum.restore_smd_normal_identity(original, collapsed)

    def test_export_normal_canonicalization_merges_only_source_precision_equals(self) -> None:
        result = canonicalize_export_normals(
            ((0.0, 0.70710676, 0.70710676), (0.0, 0.70710679, 0.70710679), (0, 0, 1))
        )
        self.assertEqual(result[0], result[1])
        self.assertNotEqual(result[1], result[2])

    def test_source_normal_identity_removes_only_importer_copy_drift(self) -> None:
        normals = ((0, 0.70710, 0.70711), (0, 0.70712, 0.70709), (0, 1, 0))
        result = canonicalize_normals_by_identity(normals, ("curve", "curve", "crease"))
        self.assertEqual(result[0], result[1])
        self.assertEqual(result[0], normals[0])
        self.assertEqual(result[2], normals[2])

    def test_smd_gate_rejects_position_normal_key_amplification(self) -> None:
        base = maximum.SmdAudit(
            1, ("mat",), (0,), ((0, "root", -1),), (0,), (0, 1, 0, 1), 3,
            ((0,),), 0, 1, position_normal_keys=4,
        )
        amplified = maximum.SmdAudit(**{**base.__dict__, "position_normal_keys": 5})
        with self.assertRaisesRegex(RuntimeError, "position.normal"):
            maximum.validate_smd_audits(base, amplified)

    def test_smd_normal_restore_removes_blender_fan_drift_and_keeps_crease(self) -> None:
        original = """version 1
nodes
0 \"root\" -1
end
skeleton
time 0
0 0 0 0 0 0 0
end
triangles
mat
0 1 2 3 0.530481 -0.002487 -0.847693 0 0
0 4 5 6 0 0 1 1 0
0 7 8 9 0 0 1 0 1
mat
0 1 2 3 0 -1 0 0 0
0 10 11 12 0 1 0 1 0
0 13 14 15 0 1 0 0 1
end
"""
        exported = original.replace(
            "0.530481 -0.002487 -0.847693", "0.530474 -0.002493 -0.847698"
        ).replace("0 -1 0 0 0", "0.000001 -0.999999 0.000002 0 0")

        restored = maximum.restore_smd_normal_identity(original, exported)

        self.assertIn("0.530481 -0.002487 -0.847693", restored)
        self.assertIn("0 -1 0", restored)
        self.assertEqual(
            maximum.audit_smd_text(restored).position_normal_keys,
            maximum.audit_smd_text(original).position_normal_keys,
        )

    def test_smd_normal_restore_keeps_close_distinct_positions_one_to_one(self) -> None:
        original = """version 1
nodes
0 \"root\" -1
end
skeleton
time 0
0 0 0 0 0 0 0
end
triangles
mat
0 1.000001 2 3 0 0 1 0 0
0 4 5 6 0 0 1 1 0
0 7 8 9 0 0 1 0 1
mat
0 1.000002 2 3 0 1 0 0 0
0 10 11 12 0 1 0 1 0
0 13 14 15 0 1 0 0 1
end
"""
        restored = maximum.restore_smd_normal_identity(original, original)
        self.assertEqual(
            tuple(corner.normal for triangle in validate_fixed_topology_smd(original, restored)[1].triangles for corner in triangle.corners),
            tuple(corner.normal for triangle in validate_fixed_topology_smd(original, restored)[0].triangles for corner in triangle.corners),
        )

    def test_triangulated_flat_island_uses_custom_normals_without_global_smoothing(self) -> None:
        result = reconstruct_smoothing(
            positions=((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)),
            faces=((0, 1, 2), (0, 2, 3)),
            loop_normals=((0, 0, 1),) * 6,
        )

        self.assertEqual(result.smooth_faces, (True, True))
        self.assertNotIn((0, 2), result.sharp_edges)
        self.assertEqual(result.loop_normals, ((0.0, 0.0, 1.0),) * 6)

        isolated = reconstruct_smoothing(
            ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            ((0, 1, 2),),
            ((0, 0, 1),) * 3,
        )
        # Blender's Source exporter only consumes custom split normals on smooth
        # polygons. The intended face normal keeps this isolated face visually flat.
        self.assertEqual(isolated.smooth_faces, (True,))

    def test_curved_island_is_smooth_and_keeps_its_intended_loop_normals(self) -> None:
        positions = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))
        normal = 1.0 / math.sqrt(2.0)
        normals = ((0, -normal, normal), (0, 0, 1), (0, 0, 1), (0, normal, normal))

        result = reconstruct_smoothing(
            positions,
            ((0, 1, 2), (0, 2, 3)),
            (normals[0], normals[1], normals[2], normals[0], normals[2], normals[3]),
        )

        self.assertEqual(result.smooth_faces, (True, True))
        self.assertNotIn((0, 2), result.sharp_edges)
        self.assertEqual(result.loop_normals[0], normals[0])
        self.assertEqual(result.loop_normals[-1], normals[3])

    def test_true_crease_can_use_shared_indices_and_distinct_corner_normals(self) -> None:
        positions = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
        result = reconstruct_smoothing(
            positions,
            ((0, 1, 2), (0, 3, 1)),
            ((0, 0, 1),) * 3 + ((0, 1, 0),) * 3,
        )
        self.assertIn((0, 1), result.sharp_edges)
        self.assertEqual(result.loop_normals[:3], ((0.0, 0.0, 1.0),) * 3)
        self.assertEqual(result.loop_normals[3:], ((0.0, 1.0, 0.0),) * 3)

    def test_true_normal_crease_is_sharp_at_both_topological_copies(self) -> None:
        positions = (
            (0, 0, 0), (1, 0, 0), (0, 1, 0),
            (0, 0, 0), (1, 0, 0), (0, 0, 1),
        )
        normals = ((0, 0, 1),) * 3 + ((0, 1, 0),) * 3

        result = reconstruct_smoothing(positions, ((0, 1, 2), (3, 5, 4)), normals)

        self.assertEqual(result.smooth_faces, (True, True))
        self.assertIn((0, 1), result.sharp_edges)
        self.assertIn((3, 4), result.sharp_edges)

    def test_open_and_non_manifold_edges_are_sharp_fail_closed(self) -> None:
        open_result = reconstruct_smoothing(
            ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            ((0, 1, 2),),
            ((0, 0, 1),) * 3,
        )
        self.assertEqual(open_result.sharp_edges, frozenset({(0, 1), (0, 2), (1, 2)}))

        positions = (
            (0, 0, 0), (1, 0, 0), (0, 1, 0),
            (0, 0, 0), (1, 0, 0), (0, 0, 1),
            (0, 0, 0), (1, 0, 0), (0, -1, 0),
        )
        result = reconstruct_smoothing(
            positions,
            ((0, 1, 2), (3, 5, 4), (6, 7, 8)),
            ((0, 0, 1),) * 3 + ((0, 1, 0),) * 3 + ((0, 0, -1),) * 3,
        )
        self.assertTrue({(0, 1), (3, 4), (6, 7)}.issubset(result.sharp_edges))

    def test_preexisting_degenerate_triangle_is_preserved_and_fail_closed(self) -> None:
        result = reconstruct_smoothing(
            ((0, 0, 0), (1, 0, 0), (1, 0, 0)),
            ((0, 1, 2),),
            ((0, 0, 1),) * 3,
        )
        self.assertEqual(result.loop_normals, ((0.0, 0.0, 1.0),) * 3)
        self.assertTrue(result.sharp_edges)

    def test_material_or_uv_seam_with_equal_normals_is_not_a_false_crease(self) -> None:
        positions = (
            (0, 0, 0), (1, 0, 0), (0, 1, 0),
            (0, 0, 0), (0, 1, 0), (-1, 0, 0),
        )
        normals = ((0, 0, 1),) * 6

        result = reconstruct_smoothing(positions, ((0, 1, 2), (3, 4, 5)), normals)

        self.assertNotIn((0, 2), result.sharp_edges)
        self.assertNotIn((3, 4), result.sharp_edges)
        self.assertEqual(result.smooth_faces, (True, True))

    def test_reconstruction_is_deterministic_and_does_not_rewrite_payloads(self) -> None:
        positions = ((0, 0, 0), (1, 0, 0), (0, 1, 0))
        faces = ((0, 1, 2),)
        normals = ((0, 0, 1),) * 3
        uvs = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
        materials = (7,)
        skin = ((('root', 1.0),),) * 3

        first = reconstruct_smoothing(positions, faces, normals)
        second = reconstruct_smoothing(positions, faces, normals)

        self.assertEqual(first, second)
        self.assertEqual(positions, ((0, 0, 0), (1, 0, 0), (0, 1, 0)))
        self.assertEqual(faces, ((0, 1, 2),))
        self.assertEqual(uvs, ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)))
        self.assertEqual(materials, (7,))
        self.assertEqual(skin, ((('root', 1.0),),) * 3)


class BlenderFiveSmoothingSmokeTests(unittest.TestCase):
    def test_headless_blender_reproduces_flat_default_then_applies_split_normals(self) -> None:
        blender = os.environ.get("BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe")
        if not Path(blender).is_file():
            self.skipTest("Blender 5 executable is unavailable")
        script = Path(__file__).with_name("blender_smoothing_smoke.py")
        completed = subprocess.run(
            [blender, "--background", "--python", str(script), "--", str(Path.cwd())],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        marker = next(
            (line.removeprefix("SMOOTHING_SMOKE ") for line in completed.stdout.splitlines()
             if line.startswith("SMOOTHING_SMOKE ")),
            None,
        )
        self.assertIsNotNone(marker, completed.stdout)
        evidence = json.loads(marker)
        self.assertEqual(evidence["version"], [5, 0, 1])
        self.assertEqual(evidence["flat_default"], [False, False])
        self.assertEqual(evidence["smooth_after"], [True, True])
        self.assertTrue(evidence["has_custom_normals"])
        self.assertLessEqual(evidence["max_normal_error"], 1e-4)
        self.assertEqual(evidence["exported_triangles"], 2)
        self.assertLessEqual(evidence["exported_position_normal_keys"], evidence["intended_position_normal_keys"])
        self.assertLessEqual(evidence["exported_hard_normal_positions"], evidence["intended_hard_normal_positions"])
        self.assertTrue(evidence["payload_unchanged"])


if __name__ == "__main__":
    unittest.main()
