from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import maximum_optimizer.composite as composite_module
import maximum_optimizer.production_authority_bundle as bundle_module
from maximum_optimizer.candidates import CandidateBuild
from maximum_optimizer.composite import (
    build_recovery_source_snapshot,
    build_source_tree_manifest,
    optimizer_contract_sha256,
)
from maximum_optimizer.domain import (
    ArtifactStat,
    CandidateEvaluation,
    CandidateSpec,
    CompiledSizeSnapshot,
    FamilyManifest,
    FocusRegionResult,
    FocusedEvidenceRef,
    GateFailure,
    StructuralFingerprint,
    ValidationResult,
)
from maximum_optimizer.production_authority_bundle import (
    build_production_adaptive_authority_bundle,
)
from maximum_optimizer.qc_graph import parse_qc_graph
from tests.maximum_optimizer.test_orchestrator import _focus_target
from tests.maximum_optimizer.test_task6_contracts import H


def _smd(material: str = "paint") -> bytes:
    return (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\n' + material + '\n'
        '0 0 0 0 0 0 1 0 0\n0 1 0 0 0 0 1 1 0\n'
        '0 0 1 0 0 0 1 0 1\nend\n'
    ).encode("utf-8")


def _animation_smd(*, frames: tuple[int, ...] = (0, 12), bone: str = "root") -> bytes:
    rows = [
        "version 1", "nodes", f'0 "{bone}" -1', "end", "skeleton",
    ]
    for frame in frames:
        rows.extend((f"time {frame}", "0 0 0 0 0 0 0"))
    rows.extend(("end", ""))
    return "\n".join(rows).encode("utf-8")


class BundleFixture:
    def __init__(
        self, root: Path, *, visual_count: int = 2,
        cdmaterials: tuple[str, ...] = ("vehicles",),
        include_animation: bool = False,
        original_animations: tuple[tuple[str, bytes], ...] | None = None,
        candidate_animations: tuple[tuple[str, bytes], ...] | None = None,
        original_sequence_prefix: str = "drive",
        candidate_sequence_prefix: str = "drive",
    ) -> None:
        self.root = root
        self.original_root = root / "original"
        self.candidate_root = root / "candidate"
        self.output_root = self.candidate_root / "output"
        self.compiled_root = root / "compiled"
        self.material_root = root / "materials"
        self.original_root.mkdir()
        self.output_root.mkdir(parents=True)
        self.compiled_root.mkdir()
        (self.material_root / "vehicles").mkdir(parents=True)
        (self.material_root / "textures").mkdir()
        (self.material_root / "vehicles" / "paint.vmt").write_text(
            'VertexLitGeneric { "$basetexture" "textures/paint" }',
            encoding="utf-8",
        )
        (self.material_root / "textures" / "paint.vtf").write_bytes(b"texture")

        original_lines = [
            '$modelname "models/test.mdl"',
            *(f'$cdmaterials "{item}"' for item in cdmaterials),
        ]
        candidate_lines = list(original_lines)
        if include_animation:
            originals = (
                (("anim.smd", _animation_smd()),)
                if original_animations is None else original_animations
            )
            candidates = originals if candidate_animations is None else candidate_animations
            for index, (name, payload) in enumerate(originals):
                original_lines.append(f'$sequence "{original_sequence_prefix}{index}" "{name}"')
                (self.original_root / name).write_bytes(payload)
            for index, (name, payload) in enumerate(candidates):
                candidate_lines.append(f'$sequence "{candidate_sequence_prefix}{index}" "{name}"')
                (self.candidate_root / name).write_bytes(payload)
        for index in range(visual_count):
            name = f"part{index:02d}.smd"
            output = f"output/part{index:02d}_opt.smd"
            (self.original_root / name).write_bytes(_smd())
            (self.candidate_root / output).write_bytes(_smd())
            original_lines.append(f'$body "part{index:02d}" "{name}"')
            candidate_lines.append(f'$body "part{index:02d}" "{output}"')
        self.original_qc = self.original_root / "main.qc"
        self.candidate_qc = self.candidate_root / "main_OPT.qc"
        self.original_qc.write_text("\n".join(original_lines) + "\n", encoding="utf-8")
        self.candidate_qc.write_text("\n".join(candidate_lines) + "\n", encoding="utf-8")

        self.spec = CandidateSpec(
            "adaptive-r040", "blender", 0.4, 0.0, "blender-adaptive-v1",
            strategy="blender-adaptive-v1", transfer="blender-native-v1",
        )
        self.cache_digest = H["c"]
        self.manifest = FamilyManifest(
            H["a"], "models/test.mdl", self.original_root, root / "models",
            StructuralFingerprint(
                "models/test.mdl", (), ("paint",), (("paint",),), ("root",),
                (), (), (), (), tuple(f"part{i:02d}.smd" for i in range(visual_count)),
                (), None,
            ),
            H["b"], (".mdl",),
        )

        original_graph = parse_qc_graph(self.original_qc, self.original_root)
        provenance = []
        for reference in original_graph.references:
            if reference.role != "visual":
                continue
            provenance.append({
                "graph_file": reference.graph_file.relative_to(self.original_root).as_posix(),
                "directive": reference.directive,
                "line": reference.line,
                "logical_path": reference.logical_path,
                "role": "visual",
            })
        metrics = {
            "schema_version": 1,
            "candidate_id": self.spec.candidate_id,
            "engine": "blender",
            "strategy": self.spec.strategy,
            "files": [{
                "adaptive_exact_preservation": {
                    "schema": 1,
                    "source_identity": f"part{index:02d}.smd",
                    "kind": "eligible-exact-v1",
                    "preserved_exact": True,
                    "reason": "ratio-preserved-exact-v1",
                },
            } for index in range(visual_count)],
            "provenance": provenance,
        }
        self.metrics_path = self.candidate_root / "candidate_metrics.json"
        self.metrics_path.write_text(
            json.dumps(metrics, sort_keys=True), encoding="utf-8",
        )
        candidate_graph = parse_qc_graph(self.candidate_qc, self.candidate_root)
        candidate_manifest = build_source_tree_manifest(
            self.candidate_root, candidate_graph, "candidate-source-v1", None,
        )
        target = _focus_target()
        self.focused = (FocusedEvidenceRef(target.region_key, H["d"]),)
        self.candidate_snapshot = build_recovery_source_snapshot(
            kind="candidate", family_id=self.manifest.family_id,
            family_input_sha256=self.manifest.input_hash,
            optimizer_contract_sha256=optimizer_contract_sha256(self.spec),
            whole_profile_sha256=H["1"], focused_profile_sha256=H["2"],
            dependency_proof_sha256=H["3"], candidate_id=self.spec.candidate_id,
            candidate_cache_digest=self.cache_digest,
            source_root=self.candidate_root, source_manifest=candidate_manifest,
            focused_evidence=self.focused,
        )
        compiled = b"compiled model"
        (self.compiled_root / "test.mdl").write_bytes(compiled)
        size = CompiledSizeSnapshot(
            self.compiled_root, len(compiled), {"mdl": len(compiled)}, {},
            (ArtifactStat("test.mdl", ".mdl", len(compiled)),),
        )
        region = FocusRegionResult(
            target, ValidationResult(True), self.focused[0].evidence_sha256, False,
        )
        self.evaluation = CandidateEvaluation(
            self.spec, size, ValidationResult(True), ValidationResult(True),
            self.compiled_root, ValidationResult(True), {target.region_key: region},
        )
        self.build = CandidateBuild(
            self.spec, root / "workspace", self.candidate_qc, self.compiled_root,
            {}, {"test.mdl": "candidate-compile"}, (), self.candidate_snapshot,
        )

    def build_bundle(self):
        return build_production_adaptive_authority_bundle(
            manifest=self.manifest, evaluation=self.evaluation, build=self.build,
            candidate_cache_digest=self.cache_digest,
            material_roots=(self.material_root,), cancel_event=threading.Event(),
        )


class ProductionAuthorityBundleTests(unittest.TestCase):
    def test_builds_complete_bind_only_authority_and_exposes_source_union_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary))
            bundle = fixture.build_bundle()

            self.assertEqual(bundle.original_snapshot.kind, "original")
            self.assertEqual(bundle.metrics, bundle.retained_base.metrics)
            self.assertEqual(bundle.state_inventory, bundle.retained_base.state_inventory)
            self.assertEqual(bundle.selection.source_identities, ("part00.smd", "part01.smd"))
            self.assertEqual(
                bundle.selection.coverage.coverage_manifest_sha256,
                bundle.coverage.coverage_manifest_sha256,
            )
            self.assertEqual(tuple(bundle.component_manifests), bundle.selection.source_identities)
            self.assertEqual(tuple(bundle.material_contracts), bundle.selection.source_identities)
            self.assertEqual(bundle.material_roots, (fixture.material_root.resolve(),))
            self.assertTrue(all(row.pose_keys == ("bind",) for row in bundle.state_inventory.rows))
            self.assertTrue(all(
                tuple(binding.search_paths for binding in contract.bindings)
                == (("vehicles",),)
                for contract in bundle.material_contracts.values()
            ))
            with self.assertRaises(TypeError):
                bundle.component_manifests["forged.smd"] = next(
                    iter(bundle.component_manifests.values())
                )

    def test_rejects_more_than_eight_eligible_sources_before_qc_smd_or_material_io(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), visual_count=9)
            (fixture.material_root / "vehicles" / "paint.vmt").unlink()
            source_reads = []
            real_hash = composite_module._hash_current_file

            def recording_hash(path, *args, **kwargs):
                if Path(path).suffix.casefold() in {".qc", ".smd"}:
                    source_reads.append(Path(path).name)
                return real_hash(path, *args, **kwargs)

            with self.assertRaisesRegex(ValueError, "eight"):
                with patch.object(composite_module, "_hash_current_file", recording_hash):
                    fixture.build_bundle()
            self.assertEqual(source_reads, [])

    def test_rejects_current_candidate_bytes_that_differ_from_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary))
            fixture.metrics_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "snapshot|bytes"):
                fixture.build_bundle()

    def test_rejects_unapproved_or_nonordinary_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary))
            failed = CandidateEvaluation(
                fixture.spec, fixture.evaluation.size, ValidationResult(False),
                ValidationResult(True), fixture.compiled_root,
                ValidationResult(True), fixture.evaluation.focused_by_region,
            )
            with self.assertRaisesRegex(ValueError, "approved"):
                build_production_adaptive_authority_bundle(
                    manifest=fixture.manifest, evaluation=failed, build=fixture.build,
                    candidate_cache_digest=fixture.cache_digest,
                    material_roots=(fixture.material_root,),
                )

    def test_rejects_candidate_cache_digest_not_supplied_by_the_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary))
            with self.assertRaisesRegex(ValueError, "cache"):
                build_production_adaptive_authority_bundle(
                    manifest=fixture.manifest, evaluation=fixture.evaluation,
                    build=fixture.build, candidate_cache_digest=H["9"],
                    material_roots=(fixture.material_root,),
                )

    def test_rejects_incoherent_validation_results_for_every_ordinary_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary))
            incoherent = ValidationResult(True, (
                GateFailure("visual", "whole", 1.0, 0.0, "failed but marked passed"),
            ))
            target = next(iter(fixture.evaluation.focused_by_region.values())).target
            focused = FocusRegionResult(target, incoherent, H["d"], False)
            variants = {
                "structural": replace(fixture.evaluation, structural=incoherent),
                "visual": replace(fixture.evaluation, visual=incoherent),
                "whole": replace(fixture.evaluation, whole_visual=incoherent),
                "focused": replace(
                    fixture.evaluation,
                    focused_by_region={target.region_key: focused},
                ),
            }
            for label, evaluation in variants.items():
                with self.subTest(label=label), self.assertRaisesRegex(
                    ValueError, "incoherent"
                ):
                    build_production_adaptive_authority_bundle(
                        manifest=fixture.manifest, evaluation=evaluation,
                        build=fixture.build,
                        candidate_cache_digest=fixture.cache_digest,
                        material_roots=(fixture.material_root,),
                    )

    def test_rejects_original_qc_change_between_graph_parse_and_manifest_seal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary))
            (fixture.material_root / "other").mkdir()
            (fixture.material_root / "other" / "paint.vmt").write_text(
                'VertexLitGeneric { "$basetexture" "textures/paint" }',
                encoding="utf-8",
            )
            real_builder = bundle_module.build_source_tree_manifest

            def mutate_then_build(root, graph, identity, event):
                if identity == "original-source-v1":
                    fixture.original_qc.write_text(
                        fixture.original_qc.read_text(encoding="utf-8").replace(
                            '$cdmaterials "vehicles"', '$cdmaterials "other"'
                        ),
                        encoding="utf-8",
                    )
                return real_builder(root, graph, identity, event)

            with patch.object(
                bundle_module, "build_source_tree_manifest", mutate_then_build
            ), self.assertRaisesRegex(ValueError, "QC graph.*changed|same window"):
                fixture.build_bundle()

    def test_revalidates_material_dependencies_after_final_snapshot_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary))
            real_revalidate = bundle_module.revalidate_recovery_snapshot
            calls = 0

            def mutate_after_final_snapshot(snapshot, event):
                nonlocal calls
                result = real_revalidate(snapshot, event)
                calls += 1
                if calls == 4:
                    (fixture.material_root / "vehicles" / "paint.vmt").write_text(
                        'VertexLitGeneric { "$basetexture" "textures/changed" }',
                        encoding="utf-8",
                    )
                return result

            with patch.object(
                bundle_module, "revalidate_recovery_snapshot",
                mutate_after_final_snapshot,
            ), self.assertRaisesRegex(ValueError, "material contract"):
                fixture.build_bundle()

    def test_rejects_duplicate_resolved_material_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary))
            with self.assertRaisesRegex(ValueError, "roots.*duplicated|canonical unique"):
                build_production_adaptive_authority_bundle(
                    manifest=fixture.manifest, evaluation=fixture.evaluation,
                    build=fixture.build,
                    candidate_cache_digest=fixture.cache_digest,
                    material_roots=(fixture.material_root, fixture.material_root),
                )

    def test_rejects_cdmaterials_duplicated_after_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(
                Path(temporary), cdmaterials=("vehicles", "VeHiClEs/"),
            )
            with self.assertRaisesRegex(ValueError, "cdmaterials.*duplicated"):
                fixture.build_bundle()

    def test_builds_exact_paired_animation_authority_for_all_compatible_visual_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), include_animation=True)
            bundle = fixture.build_bundle()
            self.assertEqual(tuple(bundle.animation_pairs), ("part00.smd", "part01.smd"))
            self.assertEqual(tuple(bundle.pose_bindings), ("part00.smd", "part01.smd"))
            self.assertTrue(all(row.pose_keys == ("bind", "animation") for row in bundle.state_inventory.rows))
            for bindings in bundle.pose_bindings.values():
                self.assertEqual(tuple(item.pose_key for item in bindings), ("bind", "animation"))
                self.assertEqual(bindings[1].frame, 12)
            with self.assertRaises((TypeError, ValueError)):
                replace(bundle, animation_anchor=object())
            with self.assertRaises((TypeError, ValueError)):
                replace(bundle, animation_pairs={key: object() for key in bundle.animation_pairs})
            with self.assertRaises((TypeError, ValueError)):
                replace(bundle, pose_bindings={key: (value[0],) for key, value in bundle.pose_bindings.items()})

    def test_animation_authority_rejects_divergence_missing_duplicate_and_incompatible_sources(self) -> None:
        cases = {
            "divergent": dict(
                original_animations=(("before.smd", _animation_smd()),),
                candidate_animations=(("after.smd", _animation_smd(frames=(0, 9))),),
            ),
            "missing": dict(
                original_animations=(("before.smd", _animation_smd()),),
                candidate_animations=(),
            ),
            "duplicate": dict(
                original_animations=(("before.smd", _animation_smd()),),
                candidate_animations=(
                    ("after-a.smd", _animation_smd()),
                    ("after-b.smd", _animation_smd()),
                ),
            ),
            "incompatible": dict(
                original_animations=(("before.smd", _animation_smd(bone="other")),),
                candidate_animations=(("after.smd", _animation_smd(bone="other")),),
            ),
            "occurrence-semantics": dict(
                original_sequence_prefix="drive",
                candidate_sequence_prefix="renamed",
            ),
        }
        for label, arguments in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                fixture = BundleFixture(Path(temporary), include_animation=True, **arguments)
                with self.assertRaisesRegex(ValueError, "animation"):
                    fixture.build_bundle()

    def test_animation_anchor_is_deterministic_from_qc_occurrence_not_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(
                Path(temporary), include_animation=True,
                original_animations=(
                    ("z-last-name.smd", _animation_smd(frames=(0, 7))),
                    ("a-first-name.smd", _animation_smd(frames=(0, 19))),
                ),
                candidate_animations=(
                    ("candidate-unrelated-name.smd", _animation_smd(frames=(0, 7))),
                    ("another-name.smd", _animation_smd(frames=(0, 19))),
                ),
            )
            bundle = fixture.build_bundle()
            self.assertEqual(bundle.animation_anchor.occurrence_index, 0)
            self.assertEqual(bundle.animation_anchor.representative_frame, 7)
            self.assertTrue(all(
                bindings[1].frame == 7 for bindings in bundle.pose_bindings.values()
            ))

    def test_animation_authority_revalidates_after_inventory_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), include_animation=True)
            real_build = bundle_module.build_production_adaptive_direct_state_inventory

            def mutate_after_inventory(*args, **kwargs):
                value = real_build(*args, **kwargs)
                (fixture.original_root / "anim.smd").write_bytes(
                    _animation_smd(frames=(0, 99))
                )
                return value

            with patch.object(
                bundle_module, "build_production_adaptive_direct_state_inventory",
                side_effect=mutate_after_inventory,
            ), self.assertRaisesRegex(ValueError, "snapshot|animation"):
                fixture.build_bundle()

    def test_animation_authority_revalidates_after_dependency_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), include_animation=True)
            real_revalidate = bundle_module._revalidate_dependencies

            def mutate_after_dependencies(*args, **kwargs):
                value = real_revalidate(*args, **kwargs)
                (fixture.candidate_root / "anim.smd").write_bytes(
                    _animation_smd(frames=(0, 99))
                )
                return value

            with patch.object(
                bundle_module, "_revalidate_dependencies",
                side_effect=mutate_after_dependencies,
            ), self.assertRaisesRegex(ValueError, "snapshot|animation"):
                fixture.build_bundle()


if __name__ == "__main__":
    unittest.main()
