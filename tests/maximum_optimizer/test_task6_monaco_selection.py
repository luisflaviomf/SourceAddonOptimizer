from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from maximum_optimizer.candidates import CandidateBuild
from maximum_optimizer.composite import (
    build_adaptive_candidate_metrics_proof,
    build_adaptive_direct_state_inventory,
    build_adaptive_direct_state_inventory_row,
    build_recovery_source_snapshot,
    candidate_spec_sha256,
)
from maximum_optimizer.domain import (
    AdaptiveGraphOccurrenceProof,
    ArtifactStat,
    CandidateEvaluation,
    CandidateSpec,
    CompiledSizeSnapshot,
    EligibleAdaptiveSourceProof,
    FocusRegionResult,
    FocusedEvidenceRef,
    SourceFileProof,
    SourceTreeManifest,
    ValidationResult,
)
from maximum_optimizer.monaco_selection import (
    RetainedMonacoBaseProof,
    build_retained_monaco_base_proof,
    select_exact_fallback_sources,
    select_monaco_base,
)
from maximum_optimizer import monaco_selection
from maximum_optimizer.reporting import canonical_json
from tests.maximum_optimizer.test_orchestrator import _focus_target
from tests.maximum_optimizer.test_task6_contracts import H


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _spec(candidate_id: str) -> CandidateSpec:
    return CandidateSpec(
        candidate_id, "blender", 0.5, 0.0, "blender-adaptive-v1",
        strategy="blender-adaptive-v1", transfer="blender-native-v1",
    )


def _manifest(root_identity: str, files: tuple[SourceFileProof, ...]) -> SourceTreeManifest:
    files = tuple(sorted(files, key=lambda item: (item.file_identity.casefold(), item.file_identity)))
    total = sum(item.size for item in files)
    payload = {
        "schema": 1, "root_identity": root_identity,
        "files": [{
            "file_identity": item.file_identity, "kind": item.kind,
            "relative_path": item.relative_path, "size": item.size,
            "sha256": item.sha256,
        } for item in files],
        "total_files": len(files), "total_bytes": total,
    }
    return SourceTreeManifest(
        1, root_identity, files, len(files), total,
        hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
    )


def _source_snapshot(
    root: Path, spec: CandidateSpec | None, *, output: bool,
    candidate_relative: str = "output/body.smd",
):
    qc = b'$body "body" "body.smd"\n'
    mesh = b"same visual source bytes"
    relative = candidate_relative if output else "body.smd"
    (root / "main.qc").write_bytes(qc)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mesh)
    manifest = _manifest("candidate" if output else "original", (
        SourceFileProof("body.smd", "visual-source", relative, len(mesh), _sha(mesh)),
        SourceFileProof("main.qc", "qc", "main.qc", len(qc), _sha(qc)),
    ))
    focused = (FocusedEvidenceRef(_focus_target().region_key, H["a"]),) if spec else ()
    return build_recovery_source_snapshot(
        kind="candidate" if spec else "original",
        family_id=H["0"], family_input_sha256=H["1"],
        optimizer_contract_sha256=H["2"], whole_profile_sha256=H["3"],
        focused_profile_sha256=H["4"], dependency_proof_sha256=H["5"],
        candidate_id=spec.candidate_id if spec else None,
        candidate_cache_digest=H["6"] if spec else None,
        source_root=root, source_manifest=manifest, focused_evidence=focused,
    )


def _typed_inputs(spec: CandidateSpec, snapshot):
    source = next(item for item in snapshot.source_manifest.files if item.file_identity == "body.smd")
    occurrence = AdaptiveGraphOccurrenceProof("main.qc", "$body", 1, "body.smd", "visual")
    metric = EligibleAdaptiveSourceProof.create(
        source_identity="body.smd", source_relative_path="body.smd",
        source_size=source.size, source_sha256=source.sha256,
        output_relative_path="output/body.smd", output_size=source.size,
        output_sha256=source.sha256, occurrences=(occurrence,),
        eligibility_reason="ratio-preserved-exact-v1",
    )
    metrics = build_adaptive_candidate_metrics_proof(
        family_id=H["0"], family_input_sha256=H["1"],
        candidate_id=spec.candidate_id, candidate_cache_digest=H["6"],
        base_spec_sha256=candidate_spec_sha256(spec),
        source_manifest_sha256=snapshot.source_manifest.digest,
        source_snapshot_sha256=snapshot.snapshot_sha256,
        original_graph_sha256=H["7"], candidate_graph_sha256=H["8"],
        raw_metrics_sha256=H["9"], sources=(metric,),
    )
    row = build_adaptive_direct_state_inventory_row(
        occurrence_key="occ-body-0000", source_identity="body.smd",
        graph_relative_path="main.qc", directive="$body", line=1,
        state_key="default", bodygroup_key="body", lod_key="lod0", skin_key="skin0",
        source_size=source.size, source_sha256=source.sha256,
        component_keys=("component-000",), material_region_keys=("material-000",),
        skeleton_contract_sha256=H["a"], pose_keys=("bind",),
        component_manifest_sha256=H["b"], material_contract_sha256=H["c"],
        pose_contract_sha256=H["d"], equivalence_class_sha256=H["e"],
    )
    inventory = build_adaptive_direct_state_inventory(
        family_id=H["0"], family_input_sha256=H["1"],
        base_candidate_id=spec.candidate_id,
        base_spec_sha256=candidate_spec_sha256(spec), base_cache_digest=H["6"],
        base_source_manifest_sha256=snapshot.source_manifest.digest,
        base_source_snapshot_sha256=snapshot.snapshot_sha256,
        complete_source_identities=("body.smd",), rows=(row,),
    )
    return metrics, inventory


def _retained(
    root: Path, candidate_id: str = "base", compiled_bytes: bytes = b"compiled-model",
    *, candidate_relative: str = "output/body.smd",
):
    spec = _spec(candidate_id)
    source_root = root / f"{candidate_id}-source"
    source_root.mkdir()
    snapshot = _source_snapshot(
        source_root, spec, output=True, candidate_relative=candidate_relative,
    )
    compiled = root / f"{candidate_id}-compiled"
    compiled.mkdir()
    (compiled / "model.mdl").write_bytes(compiled_bytes)
    size = CompiledSizeSnapshot(
        compiled, len(compiled_bytes), {"mdl": len(compiled_bytes)}, {},
        (ArtifactStat("model.mdl", "mdl", len(compiled_bytes)),),
    )
    target = _focus_target()
    region = FocusRegionResult(target, ValidationResult(True), H["a"], False)
    evaluation = CandidateEvaluation(
        spec, size, ValidationResult(True), ValidationResult(True), compiled,
        ValidationResult(True), {target.region_key: region},
    )
    build = CandidateBuild(
        spec, root / f"{candidate_id}-workspace", source_root / "main.qc", compiled,
        {}, {"model.mdl": "candidate-compile"}, (), snapshot,
    )
    metrics, inventory = _typed_inputs(spec, snapshot)
    proof = build_retained_monaco_base_proof(
        evaluation, build, metrics, inventory,
        (FocusedEvidenceRef(target.region_key, H["a"]),),
    )
    return evaluation, build, proof


class MonacoBaseSelectionTests(unittest.TestCase):
    def test_compiled_inventory_rejects_file_count_byte_bound_and_reparse_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(65):
                (root / f"file-{index:02d}.mdl").write_bytes(b"x")
            with self.assertRaises(ValueError):
                monaco_selection._compiled_files(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            huge = root / "huge.mdl"
            with huge.open("wb") as stream:
                stream.truncate(2 * 1024 ** 3 + 1)
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
                with self.assertRaises(ValueError):
                    monaco_selection._compiled_files(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.mdl").write_bytes(b"x")
            with mock.patch(
                "maximum_optimizer.monaco_selection._has_reparse_ancestor",
                return_value=True,
            ):
                with self.assertRaises(ValueError):
                    monaco_selection._compiled_files(root)

    def test_selects_smallest_current_retained_build_then_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _retained(root, "base-a", b"123456")
            second = _retained(root, "base-b", b"123456")
            larger = _retained(root, "larger", b"123456789")
            selected = select_monaco_base(
                (larger[0], second[0], first[0]),
                {item[0].spec.candidate_id: item[2] for item in (first, second, larger)},
            )
            self.assertEqual(selected.spec.candidate_id, "base-a")

    def test_rejects_nonexistent_stale_or_borrowed_retained_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evaluation, build, proof = _retained(root)
            (build.compiled_models_dir / "model.mdl").write_bytes(b"mutated")
            self.assertIsNone(select_monaco_base((evaluation,), {"base": proof}))

            other_evaluation, _, other_proof = _retained(root, "other")
            self.assertIsNone(select_monaco_base((evaluation,), {"base": other_proof}))
            with self.assertRaises((ValueError, FileNotFoundError)):
                build_retained_monaco_base_proof(
                    replace(evaluation, compiled_models_dir=root / "missing"), build,
                    proof.metrics, proof.state_inventory, proof.focused_evidence,
                )
            self.assertIsNotNone(other_evaluation)

    def test_rejects_failed_visual_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evaluation, _, proof = _retained(Path(temporary))
            self.assertIsNone(select_monaco_base(
                (replace(evaluation, structural=ValidationResult(False)),), {"base": proof}
            ))


class ExactFallbackSelectionTests(unittest.TestCase):
    def test_uses_exact_original_and_current_base_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, proof = _retained(root)
            original_root = root / "original"
            original_root.mkdir()
            original = _source_snapshot(original_root, None, output=False)
            result = select_exact_fallback_sources(
                proof, proof.metrics, proof.state_inventory, original,
            )
            self.assertEqual(result.source_identities, ("body.smd",))

    def test_rejects_stale_original_or_forged_current_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, proof = _retained(root)
            original_root = root / "original"
            original_root.mkdir()
            original = _source_snapshot(original_root, None, output=False)
            (original_root / "body.smd").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                select_exact_fallback_sources(
                    proof, proof.metrics, proof.state_inventory, original,
                )

            _, _, forged = _retained(
                root, "forged", candidate_relative="forged/body.smd",
            )
            forged_original_root = root / "forged-original"
            forged_original_root.mkdir()
            forged_original = _source_snapshot(forged_original_root, None, output=False)
            with self.assertRaises(ValueError):
                select_exact_fallback_sources(
                    forged, forged.metrics, forged.state_inventory, forged_original,
                )

    def test_zero_or_over_eight_reject_before_snapshot_io(self) -> None:
        # Bounded preflight remains independent of filesystem access.
        sentinel = mock.Mock()
        with tempfile.TemporaryDirectory() as temporary:
            _, _, proof = _retained(Path(temporary))
            zero_metrics = SimpleNamespace(sources=())
            zero_proof = SimpleNamespace(metrics=zero_metrics, state_inventory=proof.state_inventory)
            self.assertIsNone(select_exact_fallback_sources(
                zero_proof, zero_metrics, proof.state_inventory, sentinel,
            ))
            nine_metrics = SimpleNamespace(
                sources=tuple(SimpleNamespace(kind="eligible-exact-v1") for _ in range(9))
            )
            with self.assertRaises(ValueError):
                select_exact_fallback_sources(
                    zero_proof, nine_metrics, proof.state_inventory, sentinel,
                )
            sentinel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
