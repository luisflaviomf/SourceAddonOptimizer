from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest

import batch_optimize_maximum as maximum
from maximum_optimizer.smd_contract import restore_direct_smd_normals, validate_fixed_topology_smd
import maximum_optimizer.smd_contract as smd_contract
from maximum_optimizer.smoothing import (
    canonicalize_export_normals, canonicalize_normals_by_identity, reconstruct_smoothing,
)


class SmoothingReconstructionTests(unittest.TestCase):
    def test_direct_serializer_roundtrips_material_seams_hard_normals_and_weights(self):
        self.assertTrue(hasattr(smd_contract, "serialize_direct_smd"))
        original = (
            'version 1\nnodes\n0 "root" -1\n1 "wheel" 0\nend\n'
            'skeleton\ntime 0\n0 0 0 0 0 0 0\n1 0 0 0 0 0 0\nend\ntriangles\n'
            'old\n0 0 0 0 0 0 1 0 0\n0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\nend\n'
        )
        text = smd_contract.serialize_direct_smd(
            original,
            ((0.0, -0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0)),
            ((0.0, 0.0, 1.0),) * 3 + ((1.0, 0.0, 0.0),),
            ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.5, 0.5)),
            ((('root', 1.0),), (('root', 0.25), ('wheel', 0.75)), (('wheel', 1.0),), (('root', 1.0),)),
            (0, 1, 2, 3, 2, 1), ("paint", "glass"),
        )
        parsed = smd_contract.parse_smd_triangles(text)
        self.assertEqual(tuple(triangle.material for triangle in parsed.triangles), ("paint", "glass"))
        self.assertEqual(parsed.triangles[1].corners[0].normal, (1.0, 0.0, 0.0))
        self.assertNotIn("-0", text)
        self.assertIn("2 0 0.25 1 0.75", text)
        with self.assertRaisesRegex(ValueError, "degenerate"):
            smd_contract.serialize_direct_smd(
                original, ((0.0, 0.0, 0.0),) * 3, ((0.0, 0.0, 1.0),) * 3,
                ((0.0, 0.0),) * 3, ((('root', 1.0),),) * 3, (0, 1, 2), ("paint",),
            )

    def test_direct_corner_mapping_survives_permuted_import_order_and_hard_edges(self):
        self.assertTrue(hasattr(smd_contract, "map_imported_corners_to_smd"))
        original = self._two_triangle_smd()
        mapping = smd_contract.map_imported_corners_to_smd(
            original,
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
             (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            ((4, 5, 3), (2, 0, 1)),
            ((0.0, 1.0, 0.0),) * 3 + ((0.0, 0.0, 1.0),) * 3,
            ((0.0, 1.0), (1.0, 1.0), (0.5, 0.5), (0.0, 1.0), (0.0, 0.0), (1.0, 0.0)),
            (1, 0), ("paint", "glass"), ((('root', 1.0),),) * 3 + ((('tip', 1.0),),) * 3,
        )
        self.assertEqual(mapping, (4, 5, 3, 2, 0, 1))

    def test_direct_corner_mapping_accepts_only_cyclic_rotations_and_preserves_backface_orientation(self):
        original = self._one_triangle_smd()
        positions = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        uvs = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
        influences = ((('Root', 1.0),),) * 3
        for order in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
            with self.subTest(order=order):
                mapping = smd_contract.map_imported_corners_to_smd(
                    original, positions, (order,), ((0.0, 0.0, 1.0),) * 3,
                    tuple(uvs[index] for index in order), (0,), ("paint",), influences,
                )
                self.assertEqual(mapping, order)
                serialized = smd_contract.serialize_direct_smd(
                    original, tuple(positions[index] for index in order), ((0.0, 0.0, 1.0),) * 3,
                    tuple(uvs[index] for index in order), influences, (0, 1, 2), ("paint",),
                    source_corner_ordinals=mapping,
                )
                corners = smd_contract.parse_smd_triangles(serialized).triangles[0].corners
                ab = tuple(corners[1].position[i] - corners[0].position[i] for i in range(3))
                ac = tuple(corners[2].position[i] - corners[0].position[i] for i in range(3))
                self.assertGreater(ab[0] * ac[1] - ab[1] * ac[0], 0.0)
        with self.assertRaisesRegex(RuntimeError, "no source-corner mapping"):
            smd_contract.map_imported_corners_to_smd(
                original, positions, ((0, 2, 1),), ((0.0, 0.0, 1.0),) * 3,
                (uvs[0], uvs[2], uvs[1]), (0,), ("paint",), influences,
            )

    def test_direct_corner_mapping_zero_links_uses_primary_bone_and_bone_case_is_exact(self):
        original = self._one_triangle_smd(link_count=0)
        args = (
            original, ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)), ((0, 1, 2),),
            ((0.0, 0.0, 1.0),) * 3, ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
            (0,), ("paint",),
        )
        self.assertEqual(
            smd_contract.map_imported_corners_to_smd(*args, ((('Root', 1.0),),) * 3),
            (0, 1, 2),
        )
        with self.assertRaisesRegex(RuntimeError, "no source-corner mapping"):
            smd_contract.map_imported_corners_to_smd(*args, ((('root', 1.0),),) * 3)

    def test_direct_corner_mapping_rejects_normal_near_tie_and_has_15_degree_ceiling(self):
        self.assertAlmostEqual(
            smd_contract.IMPORT_NORMAL_DISAMBIGUATION_CEILING,
            2.0 * math.sin(math.radians(7.5)), places=12,
        )
        original = (
            'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\ntriangles\n'
            'paint\n0 0 0 0 0.00001 0 1 0 0\n0 1 0 0 0.00001 0 1 1 0\n0 0 1 0 0.00001 0 1 0 1\n'
            'paint\n0 0 0 0 -0.00001 0 1 0 0\n0 1 0 0 -0.00001 0 1 1 0\n0 0 1 0 -0.00001 0 1 0 1\nend\n'
        )
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            smd_contract.map_imported_corners_to_smd(
                original, ((0, 0, 0), (1, 0, 0), (0, 1, 0)), ((0, 1, 2),),
                ((0, 0, 1),) * 3, ((0, 0), (1, 0), (0, 1)),
                (0,), ("paint",), ((('root', 1.0),),) * 3,
            )

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

    @staticmethod
    def _one_triangle_smd(*, link_count: int = 1) -> str:
        suffix = "0" if link_count == 0 else "1 0 1.000000"
        return f'''version 1
nodes
0 "Root" -1
end
skeleton
time 0
0 0 0 0 0 0 0
end
triangles
paint
0 0 0 0 0 0 1 0 0 {suffix}
0 1 0 0 0 0 1 1 0 {suffix}
0 0 1 0 0 0 1 0 1 {suffix}
end
'''

    def test_fixed_topology_contract_rejects_triangle_reorder_and_winding(self) -> None:
        original = self._two_triangle_smd()
        first, second = original.split("paint\n", 1)[1].split("glass\n", 1)
        header = original.split("paint\n", 1)[0]
        reordered = header + "glass\n" + second.rsplit("end\n", 1)[0] + "paint\n" + first + "end\n"
        with self.assertRaisesRegex(RuntimeError, "triangle order|material"):
            validate_fixed_topology_smd(original, reordered)

    def test_direct_provenance_matches_exporter_reorder_without_material_zip(self) -> None:
        original = self._two_triangle_smd()
        parsed = original.split("triangles\n", 1)[1].rsplit("end\n", 1)[0]
        paint, glass = parsed.split("glass\n", 1)
        exported = original.split("triangles\n", 1)[0] + "triangles\nglass\n" + glass + "paint\n" + paint.split("paint\n", 1)[1] + "end\n"
        restored = restore_direct_smd_normals(original, exported, (0, 1, 2, 3, 4, 5))
        self.assertIn("glass\n", restored)
        with self.assertRaisesRegex(RuntimeError, "no matching"):
            restore_direct_smd_normals(original, exported.replace("0.5 0.5", "0.6 0.5"), (0, 1, 2, 3, 4, 5))

    def test_direct_provenance_rejects_duplicate_nonnormal_triangle_with_hard_normals(self) -> None:
        header = """version 1
nodes
0 "root" -1
end
skeleton
time 0
0 0 0 0 0 0 0
end
triangles
"""
        first = """paint
0 0 0 0 0 0 1 0 0
0 1 0 0 0 0 1 1 0
0 0 1 0 0 0 1 0 1
"""
        second = first.replace("0 0 1 0 0 0 1", "0 0 1 0 0 1 0").replace("0 1 0 0 0 0 1", "0 1 0 0 0 1 0").replace("0 0 0 0 0 0 1", "0 0 0 0 0 1 0")
        original = header + first + second + "end\n"
        exported = header + first + first + "end\n"
        with self.assertRaisesRegex(RuntimeError, "ambiguous across hard normals"):
            restore_direct_smd_normals(original, exported, (0, 1, 2, 3, 4, 5))

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
