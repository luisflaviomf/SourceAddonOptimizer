from __future__ import annotations

import unittest
import json
import os
from pathlib import Path
import subprocess

from maximum_optimizer.importance_map import MeshImportanceInput, build_importance_weights


class ImportanceMapTests(unittest.TestCase):
    def test_marks_projected_hulls_and_axis_extrema_deterministically(self) -> None:
        mesh = MeshImportanceInput(
            positions=((0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0), (1, 1, 0)),
            faces=((0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)),
            face_materials=(0, 0, 0, 0),
            corner_uvs=(((0, 0), (1, 0), (.5, .5)),) * 4,
            skin_signatures=((),) * 5,
        )

        result = build_importance_weights(mesh)

        self.assertEqual(result.protected_vertices, (0, 1, 2, 3))
        self.assertEqual(result.weights, (1.0, 1.0, 1.0, 1.0, 0.0))
        self.assertEqual(result.reasons[4], ())

    def test_marks_only_true_open_borders_not_uv_wedges_as_geometry_borders(self) -> None:
        mesh = MeshImportanceInput(
            positions=((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (.5, .5, 0)),
            faces=((0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)),
            face_materials=(0, 0, 0, 0),
            corner_uvs=(
                ((0, 0), (1, 0), (.5, .5)),
                ((1, 0), (1, 1), (.6, .5)),
                ((1, 1), (0, 1), (.5, .5)),
                ((0, 1), (0, 0), (.5, .5)),
            ),
            skin_signatures=((),) * 5,
        )

        result = build_importance_weights(mesh)

        self.assertIn("open-border", result.reasons[1])
        self.assertNotIn("open-border", result.reasons[4])
        self.assertIn("uv-boundary", result.reasons[4])
        self.assertEqual(result.weights[4], 0.5)

    def test_hard_creases_are_guidance_not_absolute_locks(self) -> None:
        mesh = MeshImportanceInput(
            positions=((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
            faces=((0, 1, 2), (1, 0, 3)),
            face_materials=(0, 0),
            corner_uvs=(((0, 0), (1, 0), (0, 1)), ((1, 0), (0, 0), (0, 1))),
            skin_signatures=((),) * 4,
        )
        result = build_importance_weights(mesh, crease_degrees=30.0)
        self.assertEqual(result.weights[0], 1.0)  # projected silhouette also protects it
        self.assertEqual(result.reason_weights["hard-crease"], 0.5)

    def test_marks_material_hard_crease_and_skin_transitions(self) -> None:
        mesh = MeshImportanceInput(
            positions=((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
            faces=((0, 1, 2), (1, 0, 3)),
            face_materials=(2, 3),
            corner_uvs=(((0, 0), (1, 0), (0, 1)), ((1, 0), (0, 0), (0, 1))),
            skin_signatures=(((0, 1.0),), ((0, .5), (1, .5)), ((0, 1.0),), ((1, 1.0),)),
        )

        result = build_importance_weights(mesh, crease_degrees=30.0)

        for vertex in (0, 1):
            self.assertIn("material-boundary", result.reasons[vertex])
            self.assertIn("hard-crease", result.reasons[vertex])
            self.assertIn("skin-transition", result.reasons[vertex])

    def test_rejects_malformed_corner_payload(self) -> None:
        mesh = MeshImportanceInput(
            positions=((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            faces=((0, 1, 2),),
            face_materials=(0,),
            corner_uvs=(((0, 0), (1, 0)),),
            skin_signatures=((),) * 3,
        )
        with self.assertRaisesRegex(ValueError, "corner UV"):
            build_importance_weights(mesh)


class BlenderImportanceGroupSemanticsTests(unittest.TestCase):
    def test_vertex_group_weights_protect_only_with_inversion(self) -> None:
        blender = os.environ.get("BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe")
        if not Path(blender).is_file():
            self.skipTest("Blender 5 executable is unavailable")
        script = Path(__file__).with_name("blender_importance_group_smoke.py")
        completed = subprocess.run(
            [blender, "--background", "--python", str(script), "--", str(Path.cwd())],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        marker = next(
            (line.removeprefix("IMPORTANCE_GROUP_SMOKE ") for line in completed.stdout.splitlines()
             if line.startswith("IMPORTANCE_GROUP_SMOKE ")),
            None,
        )
        self.assertIsNotNone(marker, completed.stdout)
        evidence = json.loads(marker)
        self.assertEqual(evidence["version"], [5, 0, 1])
        self.assertGreater(evidence["inverted"]["important_retained"],
                           evidence["not_inverted"]["important_retained"])
        self.assertGreaterEqual(evidence["inverted"]["important_retained_fraction"], 0.9)
        self.assertLess(evidence["inverted"]["triangles_after"], evidence["triangles_before"])
        self.assertEqual(evidence["integrated"]["strategy"], "blender-importance-map-v1")
        self.assertTrue(evidence["integrated"]["importance_group_inverted"])
        self.assertTrue(evidence["integrated"]["importance_group_removed"])
        self.assertLess(evidence["integrated"]["triangles_after"], evidence["triangles_before"])


if __name__ == "__main__":
    unittest.main()
