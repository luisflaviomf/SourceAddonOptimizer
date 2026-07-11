from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import batch_optimize_maximum as maximum
from maximum_optimizer.domain import CandidateSpec
from maximum_optimizer.qc_graph import parse_qc_graph, rewritten_qc_graph_texts
from maximum_optimizer.regions import parse_region_scope, region_keys
from maximum_optimizer.mesh_attributes import (
    build_wedge_mesh, interpolate_influences, recombine_full_attribute_vertices,
)


class MaximumBlenderPureTests(unittest.TestCase):
    def test_atomic_output_is_exclusive_cleans_failure_and_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "nested" / "candidate_metrics.json"
            maximum.atomic_write_bytes(root, target, b"first")
            self.assertEqual(target.read_bytes(), b"first")
            with mock.patch("batch_optimize_maximum.os.replace", side_effect=OSError("replace")):
                with self.assertRaises(OSError):
                    maximum.atomic_write_bytes(root, target, b"second")
            self.assertEqual(target.read_bytes(), b"first")
            self.assertEqual(tuple(target.parent.glob(".candidate_metrics.json.tmp-*")), ())
            with self.assertRaisesRegex(ValueError, "escapes"):
                maximum.atomic_write_bytes(root, root.parent / "escape.json", b"bad")

    def test_atomic_output_rejects_real_symlink_when_permitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            link = root / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "symlink"):
                maximum.atomic_write_bytes(root, link / "out.json", b"bad")
    def test_wedges_preserve_uv_hard_normal_material_and_bone_identity(self) -> None:
        positions = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0))
        triangles = ((0, 1, 2), (0, 2, 3))
        normals = ((0, 0, 1),) * 3 + ((0, 1, 0),) * 3
        uvs = ((0, 0), (1, 0), (0, 1), (0.5, 0.5), (0, 1), (1, 1))
        influences = (
            (("root", 1.0),), (("root", 1.0),), (("root", 1.0),), (("tip", 1.0),)
        )
        wedges = build_wedge_mesh(positions, triangles, normals, uvs, (0, 1), influences)
        first_shared = wedges.indices[0]
        second_shared = wedges.indices[3]
        self.assertNotEqual(first_shared, second_shared)
        self.assertNotEqual(wedges.uvs[first_shared], wedges.uvs[second_shared])
        self.assertNotEqual(wedges.normals[first_shared], wedges.normals[second_shared])
        self.assertEqual(wedges.material_ids, (0, 1))
        self.assertEqual(wedges.bone_names, ("root", "tip"))

    def test_lossless_recombine_merges_only_full_attribute_matches(self) -> None:
        result = recombine_full_attribute_vertices(
            (0, 1, 2, 3, 2, 1), (0, 0),
            ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0)),
            ((0, 0, 1),) * 4,
            ((0, 0), (1, 0), (0, 1), (0, 0)),
            ((('root', 1.0),),) * 4,
        )
        self.assertEqual(len(result.positions), 3)
        seam = recombine_full_attribute_vertices(
            (0, 1, 2, 3, 2, 1), (0, 0),
            ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0)),
            ((0, 0, 1),) * 4,
            ((0, 0), (1, 0), (0, 1), (0.5, 0.5)),
            ((('root', 1.0),),) * 4,
        )
        self.assertEqual(len(seam.positions), 4)

    def test_bone_interpolation_uses_ids_not_equal_weight_magnitudes(self) -> None:
        result = interpolate_influences(
            ((("left", 1.0),), (("right", 1.0),), (("center", 1.0),)),
            (0.25, 0.25, 0.5),
        )
        self.assertEqual(result, (("center", 0.5), ("left", 0.25), ("right", 0.25)))
    def test_qc_graph_covers_bodygroup_lod_include_and_preserves_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "parts").mkdir()
            for relative in ("body.smd", "physics.smd", "parts/panel one.smd", "parts/panel_lod.smd", "idle.smd"):
                path = root / relative
                path.write_text(f"source:{relative}\n", encoding="utf-8")
            include = root / "parts" / "common file.qci"
            include.write_text(
                '$bodygroup panels\n{\n studio "panel one.smd"\n}\n'
                '$lod 20\n{\n replacemodel "panel one.smd" "panel_lod.smd"\n}\n',
                encoding="utf-8",
            )
            qc = root / "main.qc"
            qc.write_text(
                '$body body "body.smd" // keep comment\n'
                '$include "parts/common file.qci"\n$include "parts/common file.qci"\n'
                '$collisionmodel "physics.smd"\n$sequence idle "idle.smd"\n',
                encoding="utf-8",
            )
            collision_hash = __import__("hashlib").sha256((root / "physics.smd").read_bytes()).hexdigest()
            graph = parse_qc_graph(qc, root)
            roles = [(ref.directive, ref.role, ref.logical_path) for ref in graph.references]
            self.assertIn(("$bodygroup/studio", "visual", "panel one.smd"), roles)
            self.assertIn(("$lod/replacemodel", "visual", "panel_lod.smd"), roles)
            self.assertIn(("$collisionmodel", "collision", "physics.smd"), roles)
            self.assertIn(("$sequence", "animation", "idle.smd"), roles)
            visual = {ref.source_path for ref in graph.references if ref.role == "visual"}
            outputs = {path: path.with_name(path.stem + "_optimized.smd") for path in visual}
            rewritten = rewritten_qc_graph_texts(graph, outputs)
            self.assertEqual(len(rewritten), 2)
            main_text = rewritten[root / "main_OPT.qc"]
            self.assertEqual(main_text.count('parts/common file_OPT.qci'), 2)
            self.assertIn('$collisionmodel "physics.smd"', main_text)
            self.assertEqual(__import__("hashlib").sha256((root / "physics.smd").read_bytes()).hexdigest(), collision_hash)
            self.assertIn("// keep comment", main_text)

    def test_bodygroup_only_graph_is_valid_and_unknown_source_directive_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "only.smd").write_text("mesh", encoding="utf-8")
            qc = root / "only.qc"
            qc.write_text('$bodygroup only\n{\n studio "only.smd"\n}\n', encoding="utf-8")
            graph = parse_qc_graph(qc, root)
            self.assertEqual([(ref.role, ref.logical_path) for ref in graph.references], [("visual", "only.smd")])
            qc.write_text('$mystery "only.smd"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown geometry"):
                parse_qc_graph(qc, root)
            qc.write_text(
                '$bodygroup only\n{\n studio "only.smd"\n mystery "only.smd"\n}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown bodygroup"):
                parse_qc_graph(qc, root)

    def test_qc_graph_rejects_include_cycle_and_dmx_before_blender(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.qc").write_text('$include "b.qci"\n', encoding="utf-8")
            (root / "b.qci").write_text('$include "a.qc"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cycle"):
                parse_qc_graph(root / "a.qc", root)
            (root / "mesh.dmx").write_text("dmx", encoding="utf-8")
            (root / "dmx.qc").write_text('$body body "mesh.dmx"\n', encoding="utf-8")
            graph = parse_qc_graph(root / "dmx.qc", root)
            with self.assertRaisesRegex(ValueError, "DMX"):
                maximum.reject_unsupported_dmx(graph)
    def test_region_schema_matches_renderer_and_targets_only_one_region(self) -> None:
        descriptions = (("Body", ("paint",)), ("Antenna", ("metal",)))
        keys = region_keys(descriptions)
        payload = {
            "candidate_id": "regional",
            "engine": "meshoptimizer",
            "ratio": 0.25,
            "target_error": 0.01,
            "update_vertices": True,
            "region_overrides": [{"region_key": keys[descriptions[1]], "ratio": 0.7}],
        }
        candidate = maximum.load_candidate_payload(payload)
        ratios = maximum.resolve_region_ratios(descriptions, candidate.region_overrides, candidate.ratio)
        self.assertEqual(ratios[descriptions[0]], 0.25)
        self.assertEqual(ratios[descriptions[1]], 0.7)
        self.assertEqual(parse_region_scope(f"{keys[descriptions[1]]}/bind"), (keys[descriptions[1]], "bind"))

    def test_search_candidate_cache_payload_is_directly_consumable(self) -> None:
        spec = CandidateSpec(
            "regional", "meshoptimizer", 0.25, 0.01, "transfer-v1",
            (("body|paint|0", 0.5),),
        )
        candidate = maximum.load_candidate_payload(spec.cache_payload())
        self.assertEqual(candidate.ratio, 0.25)
        self.assertTrue(candidate.update_vertices)
        self.assertEqual(candidate.region_overrides, (("body|paint|0", 0.5),))

    def test_unknown_or_ambiguous_region_override_fails(self) -> None:
        descriptions = (("Body", ("paint",)),)
        with self.assertRaisesRegex(ValueError, "unknown region"):
            maximum.resolve_region_ratios(descriptions, (("missing|none|0", 0.8),), 0.3)
        duplicate = (("Body", ("paint",)), ("Body", ("paint",)))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            maximum.resolve_region_ratios(duplicate, (), 0.3)
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
                        "region_overrides": [{"region_key": "body|paint|0", "ratio": 0.7}],
                    }
                ),
                encoding="utf-8",
            )
            settings = maximum.parse_args(
                [str(root), "--candidate-json", str(candidate), "--meshopt-dll", str(root / "bridge.dll")]
            )
        self.assertEqual(settings.candidate.candidate_id, "meshopt-r035")
        self.assertEqual(settings.candidate.ratio, 0.35)
        self.assertEqual(settings.candidate.region_overrides, (("body|paint|0", 0.7),))

    def test_candidate_rejects_unknown_fields_bool_numbers_and_traversal(self) -> None:
        valid = {
            "candidate_id": "safe-id",
            "engine": "meshoptimizer",
            "ratio": 0.5,
            "target_error": 0.01,
            "update_vertices": False,
            "region_overrides": [],
        }
        mutations = (
            {**valid, "surprise": 1},
            {**valid, "ratio": True},
            {**valid, "target_error": float("inf")},
            {**valid, "candidate_id": "../escape"},
            {**valid, "engine": "blender"},
            {**valid, "region_overrides": [{"region_key": "body|paint|0", "ratio": 0.0}]},
            {**valid, "region_overrides": [{"region_key": "bad", "ratio": 0.5}]},
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

    def test_large_object_local_antenna_vertices_receive_thin_feature_priority(self) -> None:
        vertices = (
            (0, 0, 0), (100, 0, 0), (0, 100, 0),
            (50, 50, 0), (50.2, 50, 0), (50, 50.2, 0), (50, 50, 4),
        )
        triangles = ((0, 1, 2), (3, 4, 6), (3, 6, 5))
        policy = maximum.derive_simplification_policy(
            vertices, triangles, material_ids=(0, 0, 0), skin_weights=None
        )
        self.assertTrue(all(policy.vertex_flags[index] & maximum.PRIORITY for index in (3, 4, 5, 6)))

    def test_weight_repair_clamps_normalizes_and_rejects_zero_sum(self) -> None:
        self.assertEqual(maximum.repair_weights((2.0, -1.0, 1.0, 0.0)), (0.5, 0.0, 0.5, 0.0))
        with self.assertRaisesRegex(ValueError, "zero-sum"):
            maximum.repair_weights((0.0, -1.0, 0.0, 0.0))

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

    def test_smd_audit_detects_lost_uv_hard_normal_and_influence_evidence(self) -> None:
        base = maximum.SmdAudit(
            1, ("mat",), (0,), ((0, "root", -1),), (0,), (0, 1, 0, 1), 3,
            ((0,),), 1, 1,
        )
        for changed, message in (
            (maximum.SmdAudit(**{**base.__dict__, "uv_seam_positions": 0}), "UV seam"),
            (maximum.SmdAudit(**{**base.__dict__, "hard_normal_positions": 0}), "hard-normal"),
            (maximum.SmdAudit(**{**base.__dict__, "influence_sets": ()}), "influence sets"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, message):
                    maximum.validate_smd_audits(base, changed)

    def test_smd_bone_restore_recovers_original_ids_order_and_links(self) -> None:
        original = 'version 1\nnodes\n0 "root" -1\n1 "tip" 0\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\n1 0 0 0 0 0 0\nend\n'
        exported = (
            'version 1\nnodes\n0 "tip" 1\n1 "root" -1\nend\nskeleton\ntime 0\n'
            '1 0 0 0 0 0 0\n0 0 0 0 0 0 0\nend\ntriangles\nmat\n'
            '0 0 0 0 0 0 1 0 0 1 0 1.0\n1 1 0 0 0 0 1 1 0\n1 0 1 0 0 0 1 0 1\nend\n'
        )
        restored = maximum.restore_smd_bone_identity(original, exported)
        audit = maximum.audit_smd_text(restored)
        self.assertEqual(audit.bone_nodes, ((0, "root", -1), (1, "tip", 0)))
        self.assertIn('1 0 0 0 0 0 1 0 0 1 1 1.0', restored)


if __name__ == "__main__":
    unittest.main()
