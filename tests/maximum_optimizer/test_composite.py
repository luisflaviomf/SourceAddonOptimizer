from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from maximum_optimizer.domain import (
    ArtifactStat,
    CandidateEvaluation,
    CandidateSpec,
    ChangedSourceProof,
    CompositionProof,
    CompositeRecipe,
    CompiledSizeSnapshot,
    FocusRegionResult,
    FocusTarget,
    FocusedEvidenceRef,
    RecoverySourceSnapshot,
    SourceFileProof,
    SourceOverlay,
    SourceTreeManifest,
    StructuralAuthorizationEvidence,
    ValidationResult,
)
from maximum_optimizer.composite import (
    build_source_tree_manifest,
    build_recovery_source_snapshot,
    candidate_spec_sha256,
    compose_candidate_sources,
    focused_recovery_recipe,
    optimizer_contract_sha256,
    revalidate_recovery_snapshot,
    select_recovery_overlays,
    validate_composition_proof,
)
from maximum_optimizer.candidates import CandidateBuild
from maximum_optimizer.focused_regions import FocusSelection
from maximum_optimizer.qc_graph import parse_qc_graph


H = {letter: letter * 64 for letter in "abcdef0123456789"}


def seal(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def manifest() -> SourceTreeManifest:
    files = (
        SourceFileProof("body.smd", "visual-source", "body.smd", 4, H["a"]),
        SourceFileProof("main.qc", "qc", "main.qc", 3, H["b"]),
    )
    payload = {
        "schema": 1,
        "root_identity": "candidate-source-v1",
        "files": [
            {
                "file_identity": item.file_identity,
                "kind": item.kind,
                "relative_path": item.relative_path,
                "size": item.size,
                "sha256": item.sha256,
            }
            for item in files
        ],
        "total_files": 2,
        "total_bytes": 7,
    }
    return SourceTreeManifest(1, "candidate-source-v1", files, 2, 7, seal(payload))


def snapshot(root: Path) -> RecoverySourceSnapshot:
    source_manifest = manifest()
    refs = (FocusedEvidenceRef("r-" + H["1"], H["2"]),)
    payload = {
        "schema": 1,
        "kind": "candidate",
        "family_id": H["3"],
        "family_input_sha256": H["4"],
        "optimizer_contract_sha256": H["5"],
        "whole_profile_sha256": H["6"],
        "focused_profile_sha256": H["7"],
        "dependency_proof_sha256": H["8"],
        "candidate_id": "candidate-a",
        "candidate_cache_digest": H["9"],
        "source_manifest": {
            "schema": source_manifest.schema,
            "root_identity": source_manifest.root_identity,
            "files": [{
                "file_identity": item.file_identity,
                "kind": item.kind,
                "relative_path": item.relative_path,
                "size": item.size,
                "sha256": item.sha256,
            } for item in source_manifest.files],
            "total_files": source_manifest.total_files,
            "total_bytes": source_manifest.total_bytes,
            "digest": source_manifest.digest,
        },
        "focused_evidence": [
            {"region_key": refs[0].region_key, "evidence_sha256": refs[0].evidence_sha256}
        ],
    }
    return RecoverySourceSnapshot(
        1, "candidate", H["3"], H["4"], H["5"], H["6"], H["7"], H["8"],
        "candidate-a", H["9"], root, source_manifest, refs, seal(payload),
    )


def recipe(root: Path) -> CompositeRecipe:
    donor = snapshot(root)
    optimizer_contract = seal({
        "engine": "blender", "target_error": 0.0,
        "repair_profile": "blender-adaptive-v1", "strategy": "blender-adaptive-v1",
        "update_vertices": True, "transfer": "blender-native-v1",
    })
    overlay = SourceOverlay(
        "body.smd", "donor", "r-" + H["1"], H["a"], H["c"], 5,
        donor.snapshot_sha256, donor.candidate_id, donor.candidate_cache_digest, 0.7,
        donor.focused_evidence, None,
    )
    payload = {
        "schema": 1,
        "kind": "focused-recovery-v1",
        "family_id": H["3"],
        "family_input_sha256": H["4"],
        "base_candidate_id": "base",
        "base_spec_sha256": H["d"],
        "base_cache_digest": H["e"],
        "base_source_manifest_sha256": manifest().digest,
        "optimizer_contract_sha256": optimizer_contract,
        "whole_profile_sha256": H["6"],
        "focused_profile_sha256": H["7"],
        "dependency_proof_sha256": H["8"],
        "round_index": 0,
        "direct_ratio": None,
        "overlays": [{
            "source_identity": overlay.source_identity,
            "mode": overlay.mode,
            "motivating_region_key": overlay.motivating_region_key,
            "base_source_sha256": overlay.base_source_sha256,
            "replacement_sha256": overlay.replacement_sha256,
            "replacement_size": overlay.replacement_size,
            "replacement_snapshot_sha256": overlay.replacement_snapshot_sha256,
            "replacement_candidate_id": overlay.replacement_candidate_id,
            "replacement_cache_digest": overlay.replacement_cache_digest,
            "effective_ratio": overlay.effective_ratio,
            "focused_evidence": [{
                "region_key": refs.region_key, "evidence_sha256": refs.evidence_sha256
            } for refs in overlay.focused_evidence],
            "reason": overlay.reason,
        }],
        "selector_version": "surface-risk-top-k-v1",
        "prefilter_version": None,
    }
    return CompositeRecipe(
        1, "focused-recovery-v1", H["3"], H["4"], "base", H["d"], H["e"],
        manifest().digest, optimizer_contract, H["6"], H["7"], H["8"], 0, None,
        (overlay,), "surface-risk-top-k-v1", None, seal(payload),
    )


class CompositeTypedContractTests(unittest.TestCase):
    def test_structural_authorization_is_byte_bound_and_sealed(self) -> None:
        validation = ValidationResult(True)
        payload = {
            "candidate_cache_digest": H["1"], "composition_evidence_sha256": H["2"],
            "compile_manifest_sha256": H["3"], "fingerprint_sha256": H["4"],
            "validation": {"passed": True, "failures": [], "metrics": {}, "worst_scope": ""},
        }
        valid = StructuralAuthorizationEvidence(
            H["1"], H["2"], H["3"], H["4"], validation, seal(payload)
        )
        with self.assertRaises(ValueError):
            replace(valid, compile_manifest_sha256=H["5"])

    def test_composition_proof_seals_exact_changed_sources(self) -> None:
        changed = ChangedSourceProof(
            "body.smd", "body.smd", 4, H["a"], 5, H["c"], H["d"], H["e"]
        )
        payload = {
            "schema": 1, "recipe_sha256": H["1"], "base_manifest_sha256": H["2"],
            "composed_manifest_sha256": H["3"],
            "changed_sources": [{
                "source_identity": "body.smd", "relative_path": "body.smd",
                "before_size": 4, "before_sha256": H["a"], "after_size": 5,
                "after_sha256": H["c"], "overlay_sha256": H["d"],
                "replacement_snapshot_sha256": H["e"],
            }],
        }
        valid = CompositionProof(1, H["1"], H["2"], H["3"], (changed,), seal(payload))
        with self.assertRaises(ValueError):
            replace(valid, composed_manifest_sha256=H["4"])

    def test_manifest_is_exact_canonical_and_sealed(self) -> None:
        valid = manifest()
        self.assertEqual(valid.total_bytes, 7)
        with self.assertRaises(ValueError):
            replace(valid, total_bytes=8)
        with self.assertRaises(ValueError):
            replace(valid, files=tuple(reversed(valid.files)))
        with self.assertRaises(ValueError):
            replace(valid, files=valid.files + (
                SourceFileProof("BODY.SMD", "visual-source", "other.smd", 1, H["c"]),
            ))

    def test_snapshot_seal_excludes_runtime_root_but_binds_all_logical_fields(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            valid = snapshot(Path(first))
            moved = replace(valid, source_root=Path(second))
            self.assertEqual(moved.snapshot_sha256, valid.snapshot_sha256)
            with self.assertRaises(ValueError):
                replace(valid, dependency_proof_sha256=H["0"])
            with self.assertRaises(ValueError):
                replace(valid, kind="original")

    def test_recipe_is_cumulative_canonical_bounded_and_in_candidate_cache_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            valid = recipe(Path(root))
            spec = CandidateSpec(
                "recovery-" + valid.recipe_sha256, "blender", 0.4, 0.0,
                "blender-adaptive-v1", strategy="blender-adaptive-v1",
                transfer="blender-native-v1", composite_recipe=valid,
            )
            self.assertEqual(spec.cache_payload()["composite_recipe"]["recipe_sha256"], valid.recipe_sha256)
            changed_overlay = replace(valid.overlays[0], replacement_size=6)
            with self.assertRaises(ValueError):
                replace(valid, overlays=(changed_overlay,))
            with self.assertRaises(ValueError):
                replace(valid, overlays=valid.overlays * 5)

    def test_overlay_mode_matrix_rejects_donor_without_motivating_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            valid = recipe(Path(root)).overlays[0]
            with self.assertRaises(ValueError):
                replace(valid, focused_evidence=())
            with self.assertRaises(ValueError):
                replace(valid, effective_ratio=True)
            with self.assertRaises(ValueError):
                replace(valid, reason="donors-exhausted-v1")


class SourceSnapshotTests(unittest.TestCase):
    def test_unreferenced_original_clone_cannot_collide_with_active_optimized_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "body.smd").write_bytes(b"original")
            (root / "main.qc").write_text('$body body "body.smd"\n', encoding="utf-8")
            (root / "output").mkdir()
            (root / "output" / "body_OPT.smd").write_bytes(b"optimized")
            (root / "main_OPT.qc").write_text(
                '$body body "output/body_OPT.smd"\n', encoding="utf-8"
            )
            graph = parse_qc_graph(root / "main_OPT.qc", root)
            current = build_source_tree_manifest(root, graph, "candidate-source-v1", None)
            self.assertIn("body.smd", {item.file_identity for item in current.files})
            self.assertIn("auxiliary/body.smd", {item.file_identity for item in current.files})

    def test_snapshot_factory_seals_logical_manifest_but_not_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "main.qc").write_text('$body body "body.smd"\n', encoding="utf-8")
            (root / "body.smd").write_bytes(b"body")
            graph = parse_qc_graph(root / "main.qc", root)
            tree = build_source_tree_manifest(root, graph, "candidate-source-v1", None)
            made = build_recovery_source_snapshot(
                kind="candidate", family_id=H["3"], family_input_sha256=H["4"],
                optimizer_contract_sha256=H["5"], whole_profile_sha256=H["6"],
                focused_profile_sha256=H["7"], dependency_proof_sha256=H["8"],
                candidate_id="candidate", candidate_cache_digest=H["9"],
                source_root=root, source_manifest=tree, focused_evidence=(),
            )
            self.assertEqual(made.source_root, root)
            self.assertEqual(replace(made, source_root=root.parent).snapshot_sha256, made.snapshot_sha256)

    def test_manifest_rejects_mocked_reparse_ancestor_before_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "main.qc").write_text('$body body "body.smd"\n', encoding="utf-8")
            (root / "body.smd").write_bytes(b"body")
            graph = parse_qc_graph(root / "main.qc", root)
            with mock.patch("maximum_optimizer.composite._has_reparse_ancestor", return_value=True):
                with self.assertRaisesRegex(ValueError, "reparse ancestor"):
                    build_source_tree_manifest(root, graph, "candidate-source-v1", None)

    def test_manifest_propagates_validated_handle_swap_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "main.qc").write_text('$body body "body.smd"\n', encoding="utf-8")
            (root / "body.smd").write_bytes(b"body")
            graph = parse_qc_graph(root / "main.qc", root)
            with mock.patch(
                "maximum_optimizer.composite._file_proof",
                side_effect=ValueError("material file changed while hashing"),
            ):
                with self.assertRaisesRegex(ValueError, "changed while hashing"):
                    build_source_tree_manifest(root, graph, "candidate-source-v1", None)

    def test_candidate_build_retains_typed_source_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            retained = snapshot(root)
            spec = CandidateSpec("ordinary", "blender", 0.5, 1.0, "standard")
            build = CandidateBuild(spec, root, root / "main.qc", root / "compiled", {}, {}, (), retained)
            self.assertIs(build.source_snapshot, retained)

    def test_manifest_inventories_complete_graph_tree_and_revalidation_detects_stale_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "main.qc").write_text('$body body "body.smd"\n', encoding="utf-8")
            (root / "body.smd").write_bytes(b"body")
            (root / "compile-options.txt").write_bytes(b"aux")
            graph = parse_qc_graph(root / "main.qc", root)
            current = build_source_tree_manifest(root, graph, "candidate-source-v1", None)
            self.assertEqual(
                [(item.file_identity, item.kind) for item in current.files],
                [("auxiliary/compile-options.txt", "auxiliary"), ("body.smd", "visual-source"), ("main.qc", "qc")],
            )
            refs: tuple[FocusedEvidenceRef, ...] = ()
            payload = {
                "schema": 1, "kind": "candidate", "family_id": H["3"],
                "family_input_sha256": H["4"], "optimizer_contract_sha256": H["5"],
                "whole_profile_sha256": H["6"], "focused_profile_sha256": H["7"],
                "dependency_proof_sha256": H["8"], "candidate_id": "candidate-a",
                "candidate_cache_digest": H["9"],
                "source_manifest": {
                    "schema": current.schema, "root_identity": current.root_identity,
                    "files": [{
                        "file_identity": item.file_identity, "kind": item.kind,
                        "relative_path": item.relative_path, "size": item.size,
                        "sha256": item.sha256,
                    } for item in current.files],
                    "total_files": current.total_files, "total_bytes": current.total_bytes,
                    "digest": current.digest,
                },
                "focused_evidence": [],
            }
            published = RecoverySourceSnapshot(
                1, "candidate", H["3"], H["4"], H["5"], H["6"], H["7"], H["8"],
                "candidate-a", H["9"], root, current, refs, seal(payload),
            )
            self.assertEqual(revalidate_recovery_snapshot(published, None), current)
            (root / "body.smd").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                revalidate_recovery_snapshot(published, None)


class DonorSelectionTests(unittest.TestCase):
    def test_recipe_factory_seals_complete_base_and_cumulative_overlay_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            base, base_snapshot = self._candidate(root / "base", "base", 0.4, b"base", focused_pass=False)
            donor, donor_snapshot = self._candidate(root / "donor", "donor", 0.6, b"donor", focused_pass=True)
            target = base.focused_by_region["r-" + H["1"]].target
            selection = FocusSelection(H["2"], (target,), (target,))
            overlays = select_recovery_overlays(
                base, selection, (base, donor), {"base": base_snapshot, "donor": donor_snapshot},
                base_snapshot, None, frozenset(), 0,
            )
            made = focused_recovery_recipe(
                base.spec, base_snapshot, overlays, round_index=0,
                selector_version="surface-risk-top-k-v1",
            )
            self.assertEqual(made.base_spec_sha256, candidate_spec_sha256(base.spec))
            self.assertEqual(made.overlays, overlays)
            self.assertEqual(made.base_cache_digest, base_snapshot.candidate_cache_digest)

    def _candidate(
        self, root: Path, candidate_id: str, ratio: float, body: bytes, *, focused_pass: bool
    ) -> tuple[CandidateEvaluation, RecoverySourceSnapshot]:
        root.mkdir()
        (root / "main.qc").write_text('$body body "body.smd"\n', encoding="utf-8")
        (root / "body.smd").write_bytes(body)
        graph = parse_qc_graph(root / "main.qc", root)
        tree = build_source_tree_manifest(root, graph, "candidate-source-v1", None)
        spec = CandidateSpec(
            candidate_id, "blender", ratio, 0.0, "blender-adaptive-v1",
            strategy="blender-adaptive-v1", transfer="blender-native-v1",
        )
        target = FocusTarget(
            0, "r-" + H["1"], "body.smd", 0, "default", (), 0, "bind",
            1.0, 1.0, 1.0, 1.0, H["2"],
        )
        evidence = H["a"] if candidate_id == "base" else seal(candidate_id)
        focus = FocusRegionResult(target, ValidationResult(focused_pass), evidence, False)
        whole = ValidationResult(True, metrics={"fidelity_score": 1.0})
        aggregate = whole if focused_pass else ValidationResult(False)
        size = CompiledSizeSnapshot(root, len(body), {".mdl": len(body)}, {}, (
            ArtifactStat("models/car.mdl", ".mdl", len(body)),
        ))
        evaluation = CandidateEvaluation(
            spec, size, ValidationResult(True), aggregate, root, whole,
            {target.region_key: focus},
        )
        refs = (FocusedEvidenceRef(target.region_key, evidence),) if focused_pass else ()
        cache_digest = seal("cache-" + candidate_id)
        contract = optimizer_contract_sha256(spec)
        manifest_payload = {
            "schema": tree.schema, "root_identity": tree.root_identity,
            "files": [{
                "file_identity": item.file_identity, "kind": item.kind,
                "relative_path": item.relative_path, "size": item.size, "sha256": item.sha256,
            } for item in tree.files], "total_files": tree.total_files,
            "total_bytes": tree.total_bytes, "digest": tree.digest,
        }
        payload = {
            "schema": 1, "kind": "candidate", "family_id": H["3"],
            "family_input_sha256": H["4"], "optimizer_contract_sha256": contract,
            "whole_profile_sha256": H["6"], "focused_profile_sha256": H["7"],
            "dependency_proof_sha256": H["8"], "candidate_id": candidate_id,
            "candidate_cache_digest": cache_digest, "source_manifest": manifest_payload,
            "focused_evidence": [
                {"region_key": item.region_key, "evidence_sha256": item.evidence_sha256}
                for item in refs
            ],
        }
        snapshot_value = RecoverySourceSnapshot(
            1, "candidate", H["3"], H["4"], contract, H["6"], H["7"], H["8"],
            candidate_id, cache_digest, root, tree, refs, seal(payload),
        )
        return evaluation, snapshot_value

    def test_least_less_aggressive_passing_donor_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            base, base_snapshot = self._candidate(
                root / "base", "base", 0.4, b"base", focused_pass=False
            )
            donor_70, snap_70 = self._candidate(
                root / "d70", "d70", 0.7, b"donor70", focused_pass=True
            )
            donor_60, snap_60 = self._candidate(
                root / "d60", "d60", 0.6, b"donor60", focused_pass=True
            )
            target = base.focused_by_region["r-" + H["1"]].target
            selection = FocusSelection(H["2"], (target,), (target,))
            overlays = select_recovery_overlays(
                base, selection, (base, donor_70, donor_60),
                {"base": base_snapshot, "d70": snap_70, "d60": snap_60},
                base_snapshot, None, frozenset(), 0,
            )
            self.assertEqual(len(overlays), 1)
            self.assertEqual(overlays[0].replacement_candidate_id, "d60")
            donated = next(item for item in snap_60.source_manifest.files if item.file_identity == "body.smd")
            self.assertEqual(overlays[0].replacement_sha256, donated.sha256)

    def test_selector_uses_only_retained_seals_and_does_not_touch_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            base, base_snapshot = self._candidate(root / "base", "base", 0.4, b"base", focused_pass=False)
            donor, donor_snapshot = self._candidate(root / "donor", "donor", 0.6, b"donor", focused_pass=True)
            target = base.focused_by_region["r-" + H["1"]].target
            selection = FocusSelection(H["2"], (target,), (target,))
            with mock.patch(
                "maximum_optimizer.composite.revalidate_recovery_snapshot",
                side_effect=AssertionError("selector reopened bytes before reservation"),
            ):
                overlays = select_recovery_overlays(
                    base, selection, (base, donor),
                    {"base": base_snapshot, "donor": donor_snapshot},
                    base_snapshot, None, frozenset(), 0,
                )
            self.assertEqual(overlays[0].replacement_candidate_id, "donor")

    def test_composition_changes_only_declared_source_and_proves_current_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            base, base_snapshot = self._candidate(
                root / "base", "base", 0.4, b"base", focused_pass=False
            )
            donor, donor_snapshot = self._candidate(
                root / "donor", "donor", 0.6, b"donor", focused_pass=True
            )
            target = base.focused_by_region["r-" + H["1"]].target
            selection = FocusSelection(H["2"], (target,), (target,))
            overlays = select_recovery_overlays(
                base, selection, (base, donor), {"base": base_snapshot, "donor": donor_snapshot},
                base_snapshot, None, frozenset(), 0,
            )
            recipe_payload = {
                "schema": 1, "kind": "focused-recovery-v1", "family_id": H["3"],
                "family_input_sha256": H["4"], "base_candidate_id": "base",
                "base_spec_sha256": candidate_spec_sha256(base.spec),
                "base_cache_digest": base_snapshot.candidate_cache_digest,
                "base_source_manifest_sha256": base_snapshot.source_manifest.digest,
                "optimizer_contract_sha256": optimizer_contract_sha256(base.spec),
                "whole_profile_sha256": H["6"], "focused_profile_sha256": H["7"],
                "dependency_proof_sha256": H["8"], "round_index": 0,
                "direct_ratio": None,
                "overlays": [{
                    "source_identity": item.source_identity, "mode": item.mode,
                    "motivating_region_key": item.motivating_region_key,
                    "base_source_sha256": item.base_source_sha256,
                    "replacement_sha256": item.replacement_sha256,
                    "replacement_size": item.replacement_size,
                    "replacement_snapshot_sha256": item.replacement_snapshot_sha256,
                    "replacement_candidate_id": item.replacement_candidate_id,
                    "replacement_cache_digest": item.replacement_cache_digest,
                    "effective_ratio": item.effective_ratio,
                    "focused_evidence": [{
                        "region_key": ref.region_key, "evidence_sha256": ref.evidence_sha256
                    } for ref in item.focused_evidence], "reason": item.reason,
                } for item in overlays],
                "selector_version": "surface-risk-top-k-v1", "prefilter_version": None,
            }
            composite_recipe = CompositeRecipe(
                1, "focused-recovery-v1", H["3"], H["4"], "base",
                candidate_spec_sha256(base.spec), base_snapshot.candidate_cache_digest,
                base_snapshot.source_manifest.digest, optimizer_contract_sha256(base.spec),
                H["6"], H["7"], H["8"], 0, None, overlays,
                "surface-risk-top-k-v1", None, seal(recipe_payload),
            )
            base_build = CandidateBuild(
                base.spec, root / "base-work", root / "base" / "main.qc", root / "compiled",
                {}, {}, (), base_snapshot,
            )
            workspace = root / "composed"
            composed = compose_candidate_sources(
                base_build, composite_recipe,
                {donor_snapshot.snapshot_sha256: donor_snapshot}, workspace, None,
            )
            self.assertEqual((workspace / "src" / "body.smd").read_bytes(), b"donor")
            self.assertEqual((workspace / "src" / "main.qc").read_bytes(), (root / "base" / "main.qc").read_bytes())
            self.assertEqual(
                validate_composition_proof(
                    composite_recipe, {donor_snapshot.snapshot_sha256: donor_snapshot},
                    root / "base", workspace / "src", None,
                ),
                composed.composition,
            )
            (workspace / "src" / "main.qc").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                validate_composition_proof(
                    composite_recipe, {donor_snapshot.snapshot_sha256: donor_snapshot},
                    root / "base", workspace / "src", None,
                )


if __name__ == "__main__":
    unittest.main()
