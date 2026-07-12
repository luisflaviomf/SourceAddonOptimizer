from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from unittest import mock
from types import SimpleNamespace

import batch_optimize_maximum as maximum
from maximum_optimizer.domain import CandidateSpec
from maximum_optimizer.qc_graph import parse_qc_graph, rewritten_qc_graph_texts
from maximum_optimizer.regions import (
    build_region_manifest,
    load_region_manifest_payload,
    parse_region_scope,
    resolve_region_assignments,
    source_material_slot_identities,
)
from maximum_optimizer.mesh_attributes import (
    build_wedge_mesh, interpolate_influences, recombine_full_attribute_vertices,
)


class MaximumBlenderPureTests(unittest.TestCase):
    def test_round_export_runtime_failure_writes_exact_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "wheel.smd"
            destination = root / "output" / "wheel_opt.smd"
            payload = b"version 1\r\ntriangles\r\nend\r\n\xff"
            source.write_bytes(payload)
            candidate = maximum.CandidateConfig(
                "round",
                "blender",
                0.35,
                0.0,
                True,
                (),
                strategy="round-planar-priority-v1",
                transfer="blender-native-v1",
            )

            reason = maximum._write_round_export_fallback(
                source, destination, candidate, RuntimeError("export failed")
            )

            self.assertEqual(reason, "export failed")
            self.assertEqual(destination.read_bytes(), payload)

    def test_round_planar_candidate_is_explicit_opt_in_blender_contract(self) -> None:
        payload = {
            "candidate_id": "round-planar-priority-r035",
            "engine": "blender",
            "ratio": 0.35,
            "target_error": 0.0,
            "update_vertices": True,
            "region_overrides": [],
            "strategy": "round-planar-priority-v1",
            "transfer": "blender-native-v1",
        }

        candidate = maximum.load_candidate_payload(payload)

        self.assertEqual(candidate.strategy, "round-planar-priority-v1")
        self.assertEqual(candidate.ratio, 0.35)

    def test_round_planar_modifiers_dissolve_before_priority_collapse(self) -> None:
        applied = []

        class Modifiers(list):
            def new(self, *, name, type):
                modifier = SimpleNamespace(name=name, type=type)
                self.append(modifier)
                return modifier

            def find(self, name):
                return next((index for index, item in enumerate(self) if item.name == name), -1)

            def move(self, source, target):
                self.insert(target, self.pop(source))

        class Group:
            name = "__maximum_round_priority_v1__"
            index = 4

            def __init__(self):
                self.assignments = []

            def add(self, indices, weight, mode):
                self.assignments.append((tuple(indices), weight, mode))

        class Groups:
            def __init__(self):
                self.group = None

            def get(self, name):
                return self.group if self.group and self.group.name == name else None

            def new(self, *, name):
                self.group = Group()
                self.group.name = name
                return self.group

            def remove(self, group):
                self.group = None

        obj = SimpleNamespace(
            modifiers=Modifiers(),
            vertex_groups=Groups(),
            data=SimpleNamespace(vertices=tuple(
                SimpleNamespace(
                    index=index,
                    co=(float(index), 0.0, 0.0),
                    groups=(SimpleNamespace(group=4, weight=1.0),)
                    if index in {0, 2, 4} else (),
                )
                for index in range(5)
            ), loop_triangles=[SimpleNamespace(vertices=(0, 1, 2))] * 12),
        )
        obj.data.calc_loop_triangles = lambda: None

        def apply(*, modifier):
            item = next(item for item in obj.modifiers if item.name == modifier)
            applied.append(item)
            obj.modifiers.remove(item)
            if item.name == "MaximumRoundPlanar":
                obj.data.loop_triangles = [SimpleNamespace(vertices=(0, 1, 2))] * 8

        fake_bpy = SimpleNamespace(
            context=SimpleNamespace(
                view_layer=SimpleNamespace(objects=SimpleNamespace(active=None)),
            ),
            ops=SimpleNamespace(object=SimpleNamespace(modifier_apply=apply)),
        )
        obj.select_set = lambda selected: None

        with mock.patch.object(maximum, "bpy", fake_bpy), mock.patch.object(
            maximum,
            "_verify_round_priority_survival",
            wraps=maximum._verify_round_priority_survival,
        ) as verify_priority:
            evidence = maximum._apply_round_planar_modifiers(
                obj, ratio=0.35, priority_vertices=(0, 2, 4), planar_angle_degrees=1.0
            )

        self.assertEqual(verify_priority.call_count, 2)
        self.assertEqual([item.type for item in applied], ["DECIMATE", "DECIMATE"])
        planar, collapse = applied
        self.assertEqual(planar.decimate_type, "DISSOLVE")
        self.assertFalse(planar.use_dissolve_boundaries)
        self.assertEqual(planar.delimit, {"UV", "SHARP", "NORMAL", "MATERIAL", "SEAM"})
        self.assertAlmostEqual(planar.angle_limit, math.radians(1.0))
        self.assertEqual(collapse.decimate_type, "COLLAPSE")
        self.assertEqual(collapse.ratio, 0.35)
        self.assertTrue(collapse.use_collapse_triangulate)
        self.assertEqual(collapse.vertex_group, "__maximum_round_priority_v1__")
        self.assertTrue(collapse.invert_vertex_group)
        self.assertEqual(obj.vertex_groups.group, None)
        identity = maximum._round_priority_identities(
            tuple(vertex.co for vertex in obj.data.vertices), (0, 2, 4)
        )["sha256"]
        self.assertEqual(evidence, {
            "planar_angle_degrees": 1.0,
            "planar_triangles_after": 8,
            "priority_vertices_requested": 3,
            "priority_vertices_survived": 3,
            "priority_geometric_vertices_requested": 3,
            "priority_geometric_vertices_survived_planar": 3,
            "priority_geometric_vertices_survived_collapse": 3,
            "priority_identity_sha256_requested": identity,
            "priority_identity_sha256_planar": identity,
            "priority_identity_sha256_collapse": identity,
            "boundary_vertices_requested": 0,
            "boundary_edges_requested": 0,
            "boundary_vertices_survived_planar": 0,
            "boundary_edges_survived_planar": 0,
            "boundary_vertices_survived_collapse": 0,
            "boundary_edges_survived_collapse": 0,
        })

    def test_round_priority_group_must_survive_planar_with_nonempty_assignments(self) -> None:
        group = SimpleNamespace(index=4)
        obj = SimpleNamespace(data=SimpleNamespace(vertices=(
            SimpleNamespace(groups=(SimpleNamespace(group=4, weight=1.0),)),
            SimpleNamespace(groups=(SimpleNamespace(group=2, weight=1.0),)),
            SimpleNamespace(groups=(SimpleNamespace(group=4, weight=0.5),)),
        )))

        self.assertEqual(maximum._surviving_priority_vertices(obj, group), (0, 2))
        obj.data.vertices = (
            SimpleNamespace(groups=(SimpleNamespace(group=4, weight=0.0),)),
        )
        with self.assertRaisesRegex(maximum.SmdAuditValidationError, "did not survive"):
            maximum._surviving_priority_vertices(obj, group)

    def test_round_boundary_survival_requires_every_position_and_boundary_edge(self) -> None:
        positions = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                     (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
        triangles = ((0, 1, 2), (0, 2, 3))
        boundary_edges = ((0, 1), (1, 2), (2, 3), (0, 3))

        evidence = maximum._verify_round_boundary_survival(
            positions, boundary_edges, positions, triangles
        )

        self.assertEqual(evidence, {"boundary_vertices": 4, "boundary_edges": 4})
        with self.assertRaisesRegex(maximum.SmdAuditValidationError, "boundary edge"):
            maximum._verify_round_boundary_survival(
                positions, boundary_edges, positions, triangles[:1]
            )
        moved = positions[:-1] + ((0.01, 1.0, 0.0),)
        with self.assertRaisesRegex(maximum.SmdAuditValidationError, "boundary vertex"):
            maximum._verify_round_boundary_survival(
                positions, boundary_edges, moved, triangles
            )

    def test_round_priority_survival_requires_every_canonical_geometric_identity(self) -> None:
        positions = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                     (1.0, 1.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0))
        priority = (0, 1, 2, 3, 4)

        requested = maximum._round_priority_identities(positions, priority)
        survived = maximum._verify_round_priority_survival(
            requested, positions
        )

        self.assertEqual(requested["count"], 4)
        self.assertEqual(survived["count"], 4)
        self.assertEqual(survived["sha256"], requested["sha256"])
        drifted = positions[:-2] + ((0.0, 1.0 + 1e-6, 0.0), positions[-1])
        self.assertEqual(
            maximum._verify_round_priority_survival(requested, drifted)["sha256"],
            requested["sha256"],
        )
        with self.assertRaisesRegex(maximum.SmdAuditValidationError, "priority vertex"):
            maximum._verify_round_priority_survival(requested, positions[:3])
        distinct = maximum._round_priority_identities(
            ((0.0, 0.0, 0.0), (3e-6, 0.0, 0.0)), (0, 1)
        )
        with self.assertRaisesRegex(maximum.SmdAuditValidationError, "priority vertex"):
            maximum._verify_round_priority_survival(
                distinct, ((1.5e-6, 0.0, 0.0),)
            )

    def test_round_evidence_records_admission_planar_and_priority_counts(self) -> None:
        from maximum_optimizer.round_planar_priority import (
            RoundBoundaryLoopAudit,
            RoundComponentAudit,
            RoundComponentDecision,
        )

        payload = maximum._round_evidence_payload(
            RoundComponentDecision(
                True,
                "eligible",
                2,
                (1, 3, 5),
                components=(RoundComponentAudit(
                    index=0,
                    canonical_vertices=16,
                    source_vertices=32,
                    triangles=24,
                    area_fraction=1.0,
                    extent_fraction=1.0,
                    significant=True,
                    eligible=True,
                    reason="eligible",
                    axis=2,
                    priority_vertices=3,
                    boundary_edges=24,
                    boundary_loops=(RoundBoundaryLoopAudit(
                        vertices=24,
                        axial_span=1e-6,
                        angular_bins_occupied=16,
                        radial_cv=0.001,
                        center_offset_fraction=0.001,
                        edge_length_cv=0.002,
                        outer_radius_fraction=0.99,
                    ),),
                ),),
            ),
            {
                "planar_angle_degrees": 1.0,
                "planar_triangles_after": 40,
                "priority_vertices_requested": 3,
                "priority_vertices_survived": 3,
                "priority_geometric_vertices_requested": 3,
                "priority_geometric_vertices_survived_planar": 3,
                "priority_geometric_vertices_survived_collapse": 3,
                "priority_identity_sha256_requested": "identity",
                "priority_identity_sha256_planar": "identity",
                "priority_identity_sha256_collapse": "identity",
                "boundary_vertices_requested": 24,
                "boundary_edges_requested": 24,
                "boundary_vertices_survived_planar": 24,
                "boundary_edges_survived_planar": 24,
                "boundary_vertices_survived_collapse": 24,
                "boundary_edges_survived_collapse": 24,
            },
        )

        self.assertEqual(payload, {
            "round_admission": "eligible",
            "round_axis": 2,
            "round_position_tolerance": 2e-6,
            "round_components": [{
                "index": 0,
                "canonical_vertices": 16,
                "source_vertices": 32,
                "triangles": 24,
                "area_fraction": 1.0,
                "extent_fraction": 1.0,
                "significant": True,
                "eligible": True,
                "reason": "eligible",
                "axis": 2,
                "priority_vertices": 3,
                "boundary_edges": 24,
                "boundary_loops": [{
                    "vertices": 24,
                    "axial_span": 1e-6,
                    "angular_bins_occupied": 16,
                    "radial_cv": 0.001,
                    "center_offset_fraction": 0.001,
                    "edge_length_cv": 0.002,
                    "outer_radius_fraction": 0.99,
                }],
            }],
            "round_planar_angle_degrees": 1.0,
            "round_planar_triangles_after": 40,
            "round_priority_vertices_requested": 3,
            "round_priority_vertices_survived": 3,
            "round_priority_geometric_vertices_requested": 3,
            "round_priority_geometric_vertices_survived_planar": 3,
            "round_priority_geometric_vertices_survived_collapse": 3,
            "round_priority_identity_sha256_requested": "identity",
            "round_priority_identity_sha256_planar": "identity",
            "round_priority_identity_sha256_collapse": "identity",
            "round_boundary_vertices_requested": 24,
            "round_boundary_edges_requested": 24,
            "round_boundary_vertices_survived_planar": 24,
            "round_boundary_edges_survived_planar": 24,
            "round_boundary_vertices_survived_collapse": 24,
            "round_boundary_edges_survived_collapse": 24,
        })

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

    def test_output_guard_rejects_mocked_reparse_component_before_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "junction").mkdir()
            with mock.patch(
                "batch_optimize_maximum._path_is_reparse_point",
                side_effect=lambda path: Path(path).name == "junction",
            ):
                with self.assertRaisesRegex(ValueError, "reparse"):
                    maximum.safe_output_path(root, root / "junction" / "out.json")

    @unittest.skipUnless(os.name == "nt", "Windows junction semantics")
    def test_output_guard_rejects_real_windows_junction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            junction = root / "junction"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                capture_output=True,
                text=True,
                shell=False,
            )
            if created.returncode != 0:
                self.skipTest(f"junction creation unavailable: {created.stderr or created.stdout}")
            try:
                with self.assertRaisesRegex(ValueError, "reparse"):
                    maximum.atomic_write_bytes(root, junction / "escape.bin", b"bad")
                self.assertFalse((outside / "escape.bin").exists())
            finally:
                if junction.exists():
                    os.rmdir(junction)

    def test_parse_args_rejects_lexical_root_before_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.json"
            candidate.write_text(json.dumps({
                "candidate_id": "root-guard", "engine": "meshoptimizer", "ratio": 0.5,
                "target_error": 0.1, "update_vertices": True, "region_overrides": [],
            }), encoding="utf-8")
            with mock.patch(
                "batch_optimize_maximum._path_is_reparse_point",
                side_effect=lambda path: Path(path) == root,
            ):
                with self.assertRaisesRegex(ValueError, "reparse"):
                    maximum.parse_args([
                        str(root), "--candidate-json", str(candidate),
                        "--meshopt-dll", str(root / "bridge.dll"),
                    ])

    @unittest.skipUnless(os.name == "nt", "Windows root junction semantics")
    def test_parse_args_rejects_real_root_junction_without_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside = base / "outside"
            outside.mkdir()
            candidate = base / "candidate.json"
            candidate.write_text(json.dumps({
                "candidate_id": "root-junction", "engine": "meshoptimizer", "ratio": 0.5,
                "target_error": 0.1, "update_vertices": True, "region_overrides": [],
            }), encoding="utf-8")
            junction = base / "root-junction"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                capture_output=True, text=True, shell=False,
            )
            if created.returncode != 0:
                self.skipTest(f"junction creation unavailable: {created.stderr or created.stdout}")
            try:
                with self.assertRaisesRegex(ValueError, "reparse"):
                    maximum.parse_args([
                        str(junction), "--candidate-json", str(candidate),
                        "--meshopt-dll", str(base / "bridge.dll"),
                    ])
                self.assertFalse((outside / "candidate_metrics.json").exists())
            finally:
                if junction.exists():
                    os.rmdir(junction)

    def test_material_slot_identity_preserves_legitimate_suffix_and_ignores_datablock_rename(self) -> None:
        identities = source_material_slot_identities(
            "cars/body.smd",
            ("Paint.001", "glass/Ç"),
            ("Paint.042", "glass.009"),
        )
        self.assertEqual(identities, ("slot:0:paint.001", "slot:1:glass/ç"))

        class Material:
            def __init__(self, name):
                self.name = name

        class Data:
            materials = (Material("Paint.999"), Material("glass.999"))

        class Obj:
            name = "Body"
            data = Data()

        observation = maximum._object_region_observations(
            "cars/body.smd", (Obj(),), ("Paint.001", "glass/Ç")
        )
        self.assertEqual(observation[0][2], identities)

    def test_material_slot_identity_rejects_ambiguous_partial_mapping(self) -> None:
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            source_material_slot_identities(
                "cars/body.smd", ("paint", "paint.001"), ("paint.999",)
            )
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

    def test_lossless_recombine_uses_exact_float_signatures(self) -> None:
        result = recombine_full_attribute_vertices(
            (0, 2, 2, 1, 2, 2), (0, 0),
            ((0.0, 0.0, 0.0), (1e-10, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0.0, 0.0, 1.0),) * 3,
            ((0.0, 0.0),) * 3,
            ((('root', 1.0),),) * 3,
        )
        self.assertNotEqual(result.indices[0], result.indices[3])

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
            self.assertEqual(
                [
                    (group.name, tuple(choice.logical_path if choice else None for choice in group.choices))
                    for group in graph.bodygroups
                ],
                [("panels", ("panel one.smd",))],
            )
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

    def test_qc_graph_preserves_zero_based_bodygroup_choices_including_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("body.smd", "closed.smd", "open.smd"):
                (root / name).write_text("mesh", encoding="utf-8")
            qc = root / "car.qc"
            qc.write_text(
                '$body body "body.smd"\n'
                '$bodygroup hood { blank studio "closed.smd" studio "open.smd" }\n',
                encoding="utf-8",
            )

            graph = parse_qc_graph(qc, root)

            self.assertEqual(len(graph.bodygroups), 1)
            group = graph.bodygroups[0]
            self.assertEqual(group.name, "hood")
            self.assertEqual(group.group, "car.qc:2")
            self.assertEqual(
                tuple(choice.logical_path if choice else None for choice in group.choices),
                (None, "closed.smd", "open.smd"),
            )
            for malformed in (
                '$bodygroup hood extra { studio "open.smd" }\n',
                '$bodygroup hood { studio studio "open.smd" }\n',
                '$bodygroup hood { blank "open.smd" }\n',
                '$bodygroup hood { studio "open.smd" "closed.smd" }\n',
            ):
                with self.subTest(malformed=malformed):
                    qc.write_text(malformed, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "bodygroup"):
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
        observations = (
            ("parts/body.smd", "Body", ("vehicles\\paint/azul", "Vidro/Ç")),
            ("parts/body.smd", "Antenna", ("metal",)),
        )
        manifest = build_region_manifest(observations)
        assignments = resolve_region_assignments(manifest, observations)
        body_key = assignments[observations[0]]
        antenna_key = assignments[observations[1]]
        self.assertRegex(body_key, r"^r-[0-9a-f]{64}$")
        self.assertRegex(antenna_key, r"^r-[0-9a-f]{64}$")
        with self.assertRaises(TypeError):
            manifest.by_key[body_key] = manifest.entries[0]  # type: ignore[index]
        payload = {
            "candidate_id": "regional",
            "engine": "meshoptimizer",
            "ratio": 0.25,
            "target_error": 0.01,
            "update_vertices": True,
            "region_overrides": [{"region_key": antenna_key, "ratio": 0.7}],
            "strategy": "meshopt-project-v1",
            "transfer": "projection-v1",
        }
        candidate = maximum.load_candidate_payload(payload)
        ratios = maximum.resolve_region_ratios(
            manifest, observations, candidate.region_overrides, candidate.ratio
        )
        self.assertEqual(ratios[observations[0]], 0.25)
        self.assertEqual(ratios[observations[1]], 0.7)
        self.assertEqual(parse_region_scope(f"{antenna_key}/bind"), (antenna_key, "bind"))

        round_trip = load_region_manifest_payload(manifest.to_payload())
        self.assertEqual(round_trip.entries, manifest.entries)

    def test_search_candidate_cache_payload_is_directly_consumable(self) -> None:
        region_key = "r-" + "a" * 64
        spec = CandidateSpec(
            "regional", "meshoptimizer", 0.25, 0.01, "meshopt-direct-v1",
            ((region_key, 0.5),),
            strategy="meshopt-direct-v1", update_vertices=False, transfer="direct-v1",
        )
        candidate = maximum.load_candidate_payload(spec.cache_payload())
        self.assertEqual(candidate.ratio, 0.25)
        self.assertFalse(candidate.update_vertices)
        self.assertEqual(candidate.strategy, "meshopt-direct-v1")
        self.assertEqual(candidate.transfer, "direct-v1")
        self.assertEqual(candidate.region_overrides, ((region_key, 0.5),))

    def test_direct_candidate_requires_known_complete_strategy(self) -> None:
        valid = {
            "candidate_id": "meshopt-direct-r055",
            "engine": "meshoptimizer",
            "target_ratio": 0.55,
            "target_error": 0.01,
            "repair_profile": "meshopt-direct-v1",
            "region_overrides": [],
            "strategy": "meshopt-direct-v1",
            "update_vertices": False,
            "transfer": "direct-v1",
        }
        for missing in ("strategy", "update_vertices", "transfer"):
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "missing or unknown"):
                maximum.load_candidate_payload({key: value for key, value in valid.items() if key != missing})
        for mutation in (
            {**valid, "strategy": "unknown"},
            {**valid, "update_vertices": True},
            {**valid, "transfer": "project-v1"},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                maximum.load_candidate_payload(mutation)

    def test_direct_multi_object_source_occurrence_is_allowed_for_explicit_corner_mapping(self) -> None:
        candidate = maximum.CandidateConfig(
            "direct", "meshoptimizer", 0.5, 0.01, False, (),
            strategy="meshopt-direct-v1", transfer="direct-v1",
        )
        maximum.require_direct_single_object(candidate, (object(), object()))
        maximum.require_direct_single_object(candidate, (object(),))
        with self.assertRaisesRegex(RuntimeError, "at least one"):
            maximum.require_direct_single_object(candidate, ())

    def test_unknown_or_ambiguous_region_override_fails(self) -> None:
        observations = (("body.smd", "Body", ("paint",)),)
        manifest = build_region_manifest(observations)
        with self.assertRaisesRegex(ValueError, "unknown region"):
            maximum.resolve_region_ratios(
                manifest, observations, (("r-" + "f" * 64, 0.8),), 0.3
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_region_manifest(observations + observations)

    def test_region_manifest_rejects_hash_collision_missing_unknown_and_old_schema(self) -> None:
        observations = (
            ("a/body.smd", "Body.001", ("mat/ç",)),
            ("a/body.smd", "Body.002", ("mat/ç",)),
        )
        with self.assertRaisesRegex(ValueError, "collision"):
            build_region_manifest(observations, hasher=lambda _payload: "0" * 64)
        manifest = build_region_manifest(observations)
        with self.assertRaisesRegex(ValueError, "missing"):
            resolve_region_assignments(manifest, observations[:1])
        with self.assertRaisesRegex(ValueError, "unknown"):
            resolve_region_assignments(
                manifest, observations + (("other.smd", "Other", ("mat",)),)
            )
        with self.assertRaisesRegex(ValueError, "schema"):
            load_region_manifest_payload({"schema_version": 0, "regions": []})
        noncanonical = manifest.to_payload()
        noncanonical["regions"][0]["descriptor"]["materials"][0] = "MAT\\Ç"
        with self.assertRaisesRegex(ValueError, "canonical"):
            load_region_manifest_payload(noncanonical)

    def test_two_object_region_override_changes_only_target_simplify_options(self) -> None:
        class Material:
            def __init__(self, name: str) -> None:
                self.name = name

        class Data:
            def __init__(self, material: str) -> None:
                self.materials = (Material(material),)

        class Obj:
            def __init__(self, name: str, material: str) -> None:
                self.name = name
                self.data = Data(material)

        objects = (Obj("Body", "paint"), Obj("Antenna", "metal"))
        observations = (
            ("car/body.smd", "Body", ("paint",)),
            ("car/body.smd", "Antenna", ("metal",)),
        )
        manifest = build_region_manifest(observations)
        assignments = resolve_region_assignments(manifest, observations)
        target_key = assignments[observations[1]]
        candidate = maximum.CandidateConfig(
            "regional", "meshoptimizer", 0.2, 0.01, True, ((target_key, 0.85),)
        )
        policy = maximum.SimplificationPolicy(
            (0,), 0, maximum.GeometryClass(False, (1.0, 1.0, 1.0), 0)
        )
        captured = {}

        def fake_optimizer(obj, config, ratio):
            captured[obj.name] = maximum.make_simplify_options(config, ratio, policy)
            return {"achieved_ratio": 0.99}

        metrics = maximum.optimize_region_objects(
            "car/body.smd", objects, candidate, manifest, optimizer=fake_optimizer
        )
        self.assertEqual(captured["Body"].target_ratio, 0.2)
        self.assertEqual(captured["Antenna"].target_ratio, 0.85)
        self.assertEqual({item["achieved_ratio"] for item in metrics}, {0.99})

    def test_blender_candidate_dispatches_native_decimator_per_stable_region(self) -> None:
        class Material:
            name = "paint"
        class Data:
            materials = (Material(),)
        class Obj:
            name = "Body"
            data = Data()

        observations = (("car/body.smd", "Body", ("paint",)),)
        manifest = build_region_manifest(observations)
        key = manifest.entries[0].key
        candidate = maximum.CandidateConfig(
            "adaptive", "blender", 0.3, 0.0, True, ((key, 0.65),),
            strategy="blender-adaptive-v1", transfer="blender-native-v1",
        )
        with patch.object(maximum, "_optimize_blender_object", return_value={"achieved_ratio": 0.6}) as decimate:
            metrics = maximum.optimize_region_objects(
                "car/body.smd", (Obj(),), candidate, manifest
            )

        decimate.assert_called_once_with(unittest.mock.ANY, candidate, 0.65)
        self.assertEqual(metrics[0]["region_key"], key)

    def test_projection_material_plan_rejects_mixed_and_out_of_range_mappings(self) -> None:
        self.assertEqual(
            maximum.projection_vertex_materials(4, (0, 1, 2, 0, 2, 3), (1, 1), (0, 1), 2),
            {0: 1, 1: 1, 2: 1, 3: 1},
        )
        with self.assertRaisesRegex(ValueError, "mixed incompatible material"):
            maximum.projection_vertex_materials(4, (0, 1, 2, 0, 2, 3), (0, 1), (0, 1), 2)
        with self.assertRaisesRegex(ValueError, "out of range"):
            maximum.projection_vertex_materials(3, (0, 1, 2), (2,), (0, 1), 2)
        with self.assertRaisesRegex(ValueError, "no compatible source"):
            maximum.projection_vertex_materials(3, (0, 1, 2), (1,), (0,), 2)

    def test_projection_uses_region_material_bvh_when_global_nearest_is_wrong(self) -> None:
        positions = (
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            (0.0, 0.0, 0.01), (1.0, 0.0, 0.01), (0.0, 1.0, 0.01),
        )
        triangles = ((0, 1, 2), (3, 4, 5))

        class FakeBvh:
            def __init__(self, source_positions, polygons):
                self.source_positions = source_positions
                self.polygons = polygons

            def find_nearest(self, _point):
                triangle = self.polygons[0]
                z = self.source_positions[triangle[0]][2]
                return ((0.2, 0.2, z), None, 0, abs(0.009 - z))

        buckets = maximum.build_region_material_bvhs(
            positions,
            triangles,
            (0, 1),
            vector_factory=lambda value: value,
            bvh_factory=lambda verts, polygons: FakeBvh(verts, polygons),
        )
        projected, face_index, _distance = maximum.find_compatible_projection(
            buckets, 0, (0.2, 0.2, 0.009)
        )
        self.assertEqual(projected, (0.2, 0.2, 0.0))
        self.assertEqual(face_index, 0)
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
                        "region_overrides": [{"region_key": "r-" + "1" * 64, "ratio": 0.7}],
                        "strategy": "meshopt-project-v1",
                        "transfer": "projection-v1",
                    }
                ),
                encoding="utf-8",
            )
            settings = maximum.parse_args(
                [str(root), "--candidate-json", str(candidate), "--meshopt-dll", str(root / "bridge.dll")]
            )
        self.assertEqual(settings.candidate.candidate_id, "meshopt-r035")
        self.assertEqual(settings.candidate.ratio, 0.35)
        self.assertEqual(settings.candidate.region_overrides, (("r-" + "1" * 64, 0.7),))

    def test_parses_immutable_compiler_aware_blender_candidate(self) -> None:
        payload = {
            "candidate_id": "blender-adaptive-r040",
            "engine": "blender",
            "ratio": 0.4,
            "target_error": 0.0,
            "update_vertices": True,
            "region_overrides": [{"region_key": "r-" + "a" * 64, "ratio": 0.7}],
            "strategy": "blender-adaptive-v1",
            "transfer": "blender-native-v1",
        }

        candidate = maximum.load_candidate_payload(payload)

        self.assertEqual(candidate.engine, "blender")
        self.assertEqual(candidate.strategy, "blender-adaptive-v1")
        self.assertEqual(candidate.region_overrides, (("r-" + "a" * 64, 0.7),))

    def test_parses_explicit_importance_map_research_candidate(self) -> None:
        payload = {
            "candidate_id": "blender-importance-r030",
            "engine": "blender", "ratio": 0.3, "target_error": 0.0,
            "update_vertices": True, "region_overrides": [],
            "strategy": "blender-importance-map-v1",
            "transfer": "blender-native-v1",
        }

        candidate = maximum.load_candidate_payload(payload)

        self.assertEqual(candidate.strategy, "blender-importance-map-v1")
        self.assertEqual(candidate.ratio, 0.3)

    def test_blender_candidate_parse_does_not_require_meshoptimizer_dll(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.json"
            candidate.write_text(json.dumps({
                "candidate_id": "blender-adaptive-r040", "engine": "blender",
                "ratio": 0.4, "target_error": 0.0, "update_vertices": True,
                "region_overrides": [], "strategy": "blender-adaptive-v1",
                "transfer": "blender-native-v1",
            }), encoding="utf-8")
            settings = maximum.parse_args([str(root), "--candidate-json", str(candidate)])
        self.assertIsNone(settings.meshopt_dll)

    def test_blender_strategy_contract_rejects_projection_or_nonzero_error(self) -> None:
        base = {
            "candidate_id": "blender-adaptive-r040",
            "engine": "blender",
            "ratio": 0.4,
            "target_error": 0.0,
            "update_vertices": True,
            "region_overrides": [],
            "strategy": "blender-adaptive-v1",
            "transfer": "blender-native-v1",
        }
        for mutation in (
            {**base, "transfer": "projection-v1"},
            {**base, "update_vertices": False},
            {**base, "target_error": 0.01},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                maximum.load_candidate_payload(mutation)

    def test_candidate_rejects_unknown_fields_bool_numbers_and_traversal(self) -> None:
        valid = {
            "candidate_id": "safe-id",
            "engine": "meshoptimizer",
            "ratio": 0.5,
            "target_error": 0.01,
            "update_vertices": False,
            "region_overrides": [],
            "strategy": "meshopt-direct-v1",
            "transfer": "direct-v1",
        }
        mutations = (
            {**valid, "surprise": 1},
            {**valid, "ratio": True},
            {**valid, "target_error": float("inf")},
            {**valid, "candidate_id": "../escape"},
            {**valid, "engine": "blender"},
            {**valid, "region_overrides": [{"region_key": "r-" + "b" * 64, "ratio": 0.0}]},
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
