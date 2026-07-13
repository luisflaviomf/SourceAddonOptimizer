from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import maximum_optimizer.adaptive_state_inventory as inventory_module
from maximum_optimizer.adaptive_state_inventory import (
    AdaptiveStateSourceDependencies,
    build_production_adaptive_direct_state_inventory,
)
from maximum_optimizer.adaptive_metrics_factory import qc_graph_sha256
from maximum_optimizer.composite import (
    build_adaptive_candidate_metrics_proof,
    build_recovery_source_snapshot,
    build_source_tree_manifest,
    candidate_spec_sha256,
    optimizer_contract_sha256,
)
from maximum_optimizer.domain import (
    AdaptiveGraphOccurrenceProof,
    CandidateSpec,
    EligibleAdaptiveSourceProof,
    FamilyManifest,
    IneligibleAdaptiveSourceProof,
    StructuralFingerprint,
    adaptive_direct_state_inventory_from_payload,
    adaptive_direct_state_inventory_payload,
)
from maximum_optimizer.qc_graph import parse_qc_graph


H = {character: character * 64 for character in "0123456789abcdef"}


def _smd() -> bytes:
    return (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\nend\n'
    ).encode()


def _spec() -> CandidateSpec:
    return CandidateSpec(
        "adaptive-r040", "blender", 0.4, 0.0, "blender-adaptive-v1",
        strategy="blender-adaptive-v1", transfer="blender-native-v1",
    )


class InventoryFixture:
    def __init__(self, root: Path) -> None:
        self.original_root = root / "original"; self.original_root.mkdir(parents=True)
        self.candidate_root = root / "candidate"; (self.candidate_root / "output").mkdir(parents=True)
        for name in ("fixed.smd", "door.smd"):
            (self.original_root / name).write_bytes(_smd())
            (self.candidate_root / "output" / name.replace(".smd", "_opt.smd")).write_bytes(_smd())
        self.original_qc = self.original_root / "main.qc"
        self.original_qc.write_text(
            '$modelname "models/state.mdl"\n$body "fixed" "fixed.smd"\n'
            '$bodygroup "door"\n{\n blank\n studio "door.smd"\n}\n'
            '$texturegroup "skins"\n{\n { "paint" }\n { "paint_alt" }\n}\n',
            encoding="utf-8",
        )
        self.candidate_qc = self.candidate_root / "main_OPT.qc"
        self.candidate_qc.write_text(
            '$modelname "models/state.mdl"\n$body "fixed" "output/fixed_opt.smd"\n'
            '$bodygroup "door"\n{\n blank\n studio "output/door_opt.smd"\n}\n'
            '$texturegroup "skins"\n{\n { "paint" }\n { "paint_alt" }\n}\n',
            encoding="utf-8",
        )
        self.original_graph = parse_qc_graph(self.original_qc, self.original_root)
        self.candidate_graph = parse_qc_graph(self.candidate_qc, self.candidate_root)
        original_manifest = build_source_tree_manifest(
            self.original_root, self.original_graph, "original-source-v1", None,
        )
        candidate_manifest = build_source_tree_manifest(
            self.candidate_root, self.candidate_graph, "candidate-source-v1", None,
        )
        self.spec = _spec(); self.cache_digest = H["c"]
        fingerprint = StructuralFingerprint(
            "models/state.mdl", ("door",), ("paint",), (("paint",),),
            ("root",), (), (), (), (), ("fixed.smd", "door.smd"), (), None,
        )
        self.manifest = FamilyManifest(
            H["a"], "models/state.mdl", self.original_root, root / "models",
            fingerprint, H["b"], (".mdl",),
        )
        common = dict(
            family_id=self.manifest.family_id,
            family_input_sha256=self.manifest.input_hash,
            optimizer_contract_sha256=optimizer_contract_sha256(self.spec),
            whole_profile_sha256=H["1"], focused_profile_sha256=H["2"],
            dependency_proof_sha256=H["3"], focused_evidence=(),
        )
        self.original_snapshot = build_recovery_source_snapshot(
            kind="original", candidate_id=None, candidate_cache_digest=None,
            source_root=self.original_root, source_manifest=original_manifest, **common,
        )
        self.candidate_snapshot = build_recovery_source_snapshot(
            kind="candidate", candidate_id=self.spec.candidate_id,
            candidate_cache_digest=self.cache_digest, source_root=self.candidate_root,
            source_manifest=candidate_manifest, **common,
        )
        original_files = {
            item.file_identity: item for item in original_manifest.files
            if item.kind == "visual-source"
        }
        candidate_files = {
            item.file_identity: item for item in candidate_manifest.files
            if item.kind == "visual-source"
        }
        refs_by_identity = {identity: [] for identity in original_files}
        by_relative = {item.relative_path: item for item in original_files.values()}
        for ref in self.original_graph.references:
            if ref.role != "visual":
                continue
            proof = by_relative[ref.source_path.relative_to(self.original_root).as_posix()]
            refs_by_identity[proof.file_identity].append(AdaptiveGraphOccurrenceProof(
                ref.graph_file.relative_to(self.original_root).as_posix(), ref.directive,
                ref.line, proof.file_identity, "visual",
            ))
        metric_sources = []
        for identity in sorted(original_files):
            source = original_files[identity]; output = candidate_files[identity]
            metric_sources.append(EligibleAdaptiveSourceProof.create(
                source_identity=identity, source_relative_path=source.relative_path,
                source_size=source.size, source_sha256=source.sha256,
                output_relative_path=output.relative_path, output_size=output.size,
                output_sha256=output.sha256,
                eligibility_reason="ratio-preserved-exact-v1",
                occurrences=tuple(refs_by_identity[identity]),
            ))
        self.metrics = build_adaptive_candidate_metrics_proof(
            family_id=self.manifest.family_id,
            family_input_sha256=self.manifest.input_hash,
            candidate_id=self.spec.candidate_id,
            candidate_cache_digest=self.cache_digest,
            base_spec_sha256=candidate_spec_sha256(self.spec),
            source_manifest_sha256=candidate_manifest.digest,
            source_snapshot_sha256=self.candidate_snapshot.snapshot_sha256,
            original_graph_sha256=qc_graph_sha256(self.original_graph),
            candidate_graph_sha256=qc_graph_sha256(self.candidate_graph),
            raw_metrics_sha256=H["9"], sources=tuple(metric_sources),
        )
        self.dependencies = {
            identity: AdaptiveStateSourceDependencies.create(
                source_identity=identity, source_size=proof.size,
                source_sha256=proof.sha256, component_keys=("component-000",),
                material_region_keys=("material-000",),
                component_manifest_sha256=H["4"], material_contract_sha256=H["5"],
            )
            for identity, proof in original_files.items()
        }

    def build(self, *, dependencies=None):
        return build_production_adaptive_direct_state_inventory(
            manifest=self.manifest, spec=self.spec,
            candidate_cache_digest=self.cache_digest,
            original_snapshot=self.original_snapshot,
            candidate_snapshot=self.candidate_snapshot,
            metrics_proof=self.metrics,
            dependencies=self.dependencies if dependencies is None else dependencies,
            animation_pairs={}, cancel_event=threading.Event(),
        )


class AdaptiveStateInventoryFactoryTests(unittest.TestCase):
    def test_factory_rejects_candidate_skeleton_hierarchy_difference(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = InventoryFixture(Path(raw))
            candidate_path = fixture.candidate_root / "output" / "door_opt.smd"
            candidate_path.write_bytes(
                candidate_path.read_bytes().replace(b'0 "root" -1', b'0 "changed" -1')
            )
            candidate_graph = parse_qc_graph(
                fixture.candidate_qc, fixture.candidate_root,
            )
            candidate_manifest = build_source_tree_manifest(
                fixture.candidate_root, candidate_graph, "candidate-source-v1", None,
            )
            fixture.candidate_snapshot = build_recovery_source_snapshot(
                kind="candidate", candidate_id=fixture.spec.candidate_id,
                candidate_cache_digest=fixture.cache_digest,
                source_root=fixture.candidate_root, source_manifest=candidate_manifest,
                family_id=fixture.manifest.family_id,
                family_input_sha256=fixture.manifest.input_hash,
                optimizer_contract_sha256=optimizer_contract_sha256(fixture.spec),
                whole_profile_sha256=H["1"], focused_profile_sha256=H["2"],
                dependency_proof_sha256=H["3"], focused_evidence=(),
            )
            candidate_files = {
                item.file_identity: item for item in candidate_manifest.files
                if item.kind == "visual-source"
            }
            sources = []
            for metric in fixture.metrics.sources:
                output = candidate_files[metric.source_identity]
                common = dict(
                    source_identity=metric.source_identity,
                    source_relative_path=metric.source_relative_path,
                    source_size=metric.source_size, source_sha256=metric.source_sha256,
                    output_relative_path=output.relative_path,
                    output_size=output.size, output_sha256=output.sha256,
                    occurrences=metric.occurrences,
                )
                if metric.source_identity == "door.smd":
                    sources.append(IneligibleAdaptiveSourceProof.create(
                        ineligibility_reason="adaptive-output-changed-v1", **common,
                    ))
                else:
                    sources.append(EligibleAdaptiveSourceProof.create(
                        eligibility_reason="ratio-preserved-exact-v1", **common,
                    ))
            fixture.metrics = build_adaptive_candidate_metrics_proof(
                family_id=fixture.manifest.family_id,
                family_input_sha256=fixture.manifest.input_hash,
                candidate_id=fixture.spec.candidate_id,
                candidate_cache_digest=fixture.cache_digest,
                base_spec_sha256=candidate_spec_sha256(fixture.spec),
                source_manifest_sha256=candidate_manifest.digest,
                source_snapshot_sha256=fixture.candidate_snapshot.snapshot_sha256,
                original_graph_sha256=qc_graph_sha256(fixture.original_graph),
                candidate_graph_sha256=qc_graph_sha256(candidate_graph),
                raw_metrics_sha256=H["9"], sources=tuple(sources),
            )
            with self.assertRaisesRegex(ValueError, "skeleton|nodes|hierarchy|bind"):
                fixture.build()

    def test_dependency_contract_rejects_forged_seal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = InventoryFixture(Path(raw))
            dependency = fixture.dependencies["door.smd"]
            with self.assertRaisesRegex(ValueError, "seal"):
                replace(dependency, dependency_sha256=H["f"])

    def test_factory_emits_only_active_rows_for_complete_bodygroup_skin_product(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = InventoryFixture(Path(raw))
            inventory = fixture.build()
            self.assertEqual(adaptive_direct_state_inventory_from_payload(
                adaptive_direct_state_inventory_payload(inventory)
            ), inventory)
            self.assertEqual(inventory.complete_source_identities, ("door.smd", "fixed.smd"))
            self.assertEqual(len(inventory.rows), 6)
            self.assertEqual(len({item.state_key for item in inventory.rows}), 4)
            self.assertEqual(len({item.skin_key for item in inventory.rows}), 2)
            self.assertEqual(sum(item.source_identity == "fixed.smd" for item in inventory.rows), 4)
            self.assertEqual(sum(item.source_identity == "door.smd" for item in inventory.rows), 2)
            self.assertEqual(len({item.occurrence_key for item in inventory.rows}), 6)
            source_proofs = {
                item.file_identity: item for item in fixture.original_snapshot.source_manifest.files
                if item.kind == "visual-source"
            }
            for row in inventory.rows:
                proof = source_proofs[row.source_identity]
                dependency = fixture.dependencies[row.source_identity]
                self.assertEqual((row.source_size, row.source_sha256), (proof.size, proof.sha256))
                self.assertEqual(row.component_keys, dependency.component_keys)
                self.assertEqual(row.material_region_keys, dependency.material_region_keys)
                self.assertEqual(row.component_manifest_sha256, dependency.component_manifest_sha256)
                self.assertEqual(row.material_contract_sha256, dependency.material_contract_sha256)
                self.assertEqual(row.pose_keys, ("bind",))

    def test_factory_rejects_dependency_metrics_or_snapshot_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = InventoryFixture(Path(raw))
            missing = dict(fixture.dependencies); missing.pop("door.smd")
            with self.assertRaisesRegex(ValueError, "depend|union"):
                fixture.build(dependencies=missing)
            forged = dict(fixture.dependencies)
            current = forged["door.smd"]
            forged["door.smd"] = AdaptiveStateSourceDependencies.create(
                source_identity=current.source_identity,
                source_size=current.source_size, source_sha256=H["f"],
                component_keys=current.component_keys,
                material_region_keys=current.material_region_keys,
                component_manifest_sha256=current.component_manifest_sha256,
                material_contract_sha256=current.material_contract_sha256,
            )
            with self.assertRaisesRegex(ValueError, "depend|bytes"):
                fixture.build(dependencies=forged)

    def test_factory_final_revalidation_catches_source_mutation_during_row_sealing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = InventoryFixture(Path(raw))
            source = fixture.original_root / "fixed.smd"
            original = source.read_bytes()
            real_builder = inventory_module.build_adaptive_direct_state_inventory

            def mutate_then_build(*args, **kwargs):
                result = real_builder(*args, **kwargs)
                source.write_bytes(original + b"stale")
                return result

            try:
                with patch.object(
                    inventory_module, "build_adaptive_direct_state_inventory",
                    side_effect=mutate_then_build,
                ):
                    with self.assertRaisesRegex(ValueError, "snapshot|bytes|match"):
                        fixture.build()
            finally:
                source.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
