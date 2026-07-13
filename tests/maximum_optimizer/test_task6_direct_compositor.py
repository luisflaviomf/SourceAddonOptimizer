from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from maximum_optimizer.candidates import (
    CandidateBuild,
    _direct_input_material_proofs,
    _direct_prefilter_proof,
)
from maximum_optimizer.composite import (
    adaptive_direct_recipe,
    build_adaptive_candidate_metrics_proof,
    build_adaptive_direct_coverage_manifest,
    build_adaptive_direct_state_inventory,
    build_direct_source_request,
    build_direct_source_snapshot,
    build_recovery_source_snapshot,
    build_source_tree_manifest,
    candidate_spec_sha256,
    compose_candidate_sources,
    optimizer_contract_sha256,
    validate_composition_proof,
)
from maximum_optimizer.domain import (
    CandidateSpec,
    DirectMaterialTriangleProof,
    DirectSourceSnapshot,
    FocusedEvidenceRef,
    SourceOverlay,
)
from maximum_optimizer.monaco_schedule import _request_set_digest, _snapshot_set_digest
from maximum_optimizer.processes import ProcessCancelledError
from maximum_optimizer.qc_graph import parse_qc_graph
from maximum_optimizer.smd_contract import prefilter_direct_degenerate_smd
from tests.maximum_optimizer.test_task6_contracts import (
    coverage_source,
    inventory_for_coverage,
    metrics_for_coverage,
    source_metrics,
)


H = {letter: letter * 64 for letter in "0123456789abcdef"}


def _smd(triangles: int, material: str = "paint") -> bytes:
    rows = []
    for triangle in range(triangles):
        x = triangle * 2
        rows.append(
            f"{material}\n"
            f"0 {x} 0 0 0 0 1 0 0\n"
            f"0 {x + 1} 0 0 0 0 1 1 0\n"
            f"0 {x} 1 0 0 0 1 0 1\n"
        )
    return (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\n' + "".join(rows) + "end\n"
    ).encode("utf-8")


class DirectCompositorFixture:
    def __init__(self, root: Path, visual_count: int = 2, ratio: float = 0.5) -> None:
        self.root = root
        self.base_root = root / "base"
        self.base_root.mkdir()
        (self.base_root / "meshes").mkdir()
        (self.base_root / "physics").mkdir()
        (self.base_root / "anim").mkdir()
        (self.base_root / "notes").mkdir()
        self.visual_paths = tuple(
            f"meshes/part-{index:02d}.smd" for index in range(visual_count)
        )
        base_bytes = _smd(9)
        for relative in self.visual_paths:
            (self.base_root / relative).write_bytes(base_bytes)
        (self.base_root / "physics/collision.smd").write_bytes(_smd(2, "collision"))
        (self.base_root / "anim/idle.smd").write_bytes(_smd(2, "animation"))
        (self.base_root / "notes/readme.txt").write_bytes(b"immutable auxiliary\n")
        (self.base_root / "shared.qci").write_text(
            '$collisionmodel "physics/collision.smd"\n'
            '$sequence idle "anim/idle.smd"\n',
            encoding="utf-8",
        )
        body_lines = [f'$body part{index} "{relative}"' for index, relative in enumerate(self.visual_paths)]
        (self.base_root / "main.qc").write_text(
            '$modelname "task6.mdl"\n$include "shared.qci"\n'
            + "\n".join(body_lines) + "\n",
            encoding="utf-8",
        )
        graph = parse_qc_graph(self.base_root / "main.qc", self.base_root)
        manifest = build_source_tree_manifest(
            self.base_root, graph, "candidate-source-v1", None
        )
        self.base_spec = CandidateSpec(
            "base", "blender", 0.8, 0.0, "blender-adaptive-v1",
            strategy="blender-adaptive-v1", transfer="blender-native-v1",
        )
        self.base_snapshot = build_recovery_source_snapshot(
            kind="candidate", family_id=H["0"], family_input_sha256=H["1"],
            optimizer_contract_sha256=optimizer_contract_sha256(self.base_spec),
            whole_profile_sha256=H["6"], focused_profile_sha256=H["7"],
            dependency_proof_sha256=H["8"], candidate_id="base",
            candidate_cache_digest=H["3"], source_root=self.base_root,
            source_manifest=manifest, focused_evidence=(),
        )
        compiled = root / "compiled"
        compiled.mkdir()
        self.base_build = CandidateBuild(
            self.base_spec, root / "base-build", self.base_root / "main.qc",
            compiled, {}, {}, (), self.base_snapshot,
        )
        by_identity = {
            item.file_identity: item for item in manifest.files
            if item.kind == "visual-source"
        }
        coverage_sources = tuple(coverage_source(
            identity, source_size=proof.size, source_sha256=proof.sha256,
        ) for identity, proof in sorted(by_identity.items()))
        old_metrics = metrics_for_coverage(coverage_sources)
        old_inventory = inventory_for_coverage(coverage_sources)
        spec_sha256 = candidate_spec_sha256(self.base_spec)
        metrics = build_adaptive_candidate_metrics_proof(
            family_id=H["0"], family_input_sha256=H["1"], candidate_id="base",
            candidate_cache_digest=H["3"], base_spec_sha256=spec_sha256,
            source_manifest_sha256=manifest.digest,
            source_snapshot_sha256=self.base_snapshot.snapshot_sha256,
            original_graph_sha256=H["a"], candidate_graph_sha256=H["b"],
            raw_metrics_sha256=H["c"], sources=old_metrics.sources,
        )
        inventory = build_adaptive_direct_state_inventory(
            family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
            base_spec_sha256=spec_sha256, base_cache_digest=H["3"],
            base_source_manifest_sha256=manifest.digest,
            base_source_snapshot_sha256=self.base_snapshot.snapshot_sha256,
            complete_source_identities=old_inventory.complete_source_identities,
            rows=old_inventory.rows,
        )
        self.coverage = build_adaptive_direct_coverage_manifest(
            family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
            base_spec_sha256=spec_sha256, base_cache_digest=H["3"],
            base_source_manifest_sha256=manifest.digest,
            base_source_snapshot_sha256=self.base_snapshot.snapshot_sha256,
            metrics_proof=metrics, state_inventory=inventory,
        )
        coverage_by_identity = {
            item.source_identity: item for item in self.coverage.sources
        }
        requests = []
        snapshots = []
        for ordinal, identity in enumerate(sorted(by_identity)):
            proof = by_identity[identity]
            text = (self.base_root / proof.relative_path).read_text(encoding="utf-8")
            filtered = prefilter_direct_degenerate_smd(text).filtered_text
            request = build_direct_source_request(
                family_id=H["0"], family_input_sha256=H["1"],
                base_candidate_id="base", base_spec_sha256=candidate_spec_sha256(self.base_spec),
                base_cache_digest=H["3"], base_source_manifest_sha256=manifest.digest,
                base_source_snapshot_sha256=self.base_snapshot.snapshot_sha256,
                coverage_manifest_sha256=self.coverage.coverage_manifest_sha256,
                source_coverage_sha256=coverage_by_identity[identity].source_coverage_sha256,
                optimizer_contract_sha256=optimizer_contract_sha256(self.base_spec),
                whole_profile_sha256=H["6"], focused_profile_sha256=H["7"],
                dependency_proof_sha256=H["8"], source_identity=identity,
                source_relative_path=proof.relative_path, source_size=proof.size,
                source_sha256=proof.sha256, direct_ratio=ratio,
                expected_prefilter=_direct_prefilter_proof(text),
                expected_materials=_direct_input_material_proofs(filtered),
            )
            output = _smd(max(1, int(9 * ratio)))
            direct_root = root / f"direct-{ordinal:02d}"
            direct_root.mkdir()
            (direct_root / "output.smd").write_bytes(output)
            after = max(1, int(9 * ratio))
            snapshot = build_direct_source_snapshot(
                request=request, input_source_root=self.base_root,
                source_root=direct_root, output_relative_path="output.smd",
                output_size=len(output), output_sha256=hashlib.sha256(output).hexdigest(),
                triangles_before=9, triangles_after=after,
                material_triangles=(DirectMaterialTriangleProof(0, "paint", 9, after, after),),
                prefilter=request.expected_prefilter,
            )
            requests.append(request)
            snapshots.append(snapshot)
        self.requests = tuple(requests)
        self.snapshots = tuple(snapshots)
        overlays = tuple(SourceOverlay(
            request.source_identity, "direct-position", None, request.source_sha256,
            snapshot.output_sha256, snapshot.output_size, snapshot.snapshot_sha256,
            snapshot.direct_candidate_id, snapshot.direct_cache_digest, ratio, (),
            "approved-direct-position-v1",
        ) for request, snapshot in zip(self.requests, self.snapshots))
        self.recipe = adaptive_direct_recipe(
            family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
            base_spec_sha256=candidate_spec_sha256(self.base_spec), base_cache_digest=H["3"],
            base_source_manifest_sha256=manifest.digest,
            base_source_snapshot_sha256=self.base_snapshot.snapshot_sha256,
            optimizer_contract_sha256=optimizer_contract_sha256(self.base_spec),
            whole_profile_sha256=H["6"], focused_profile_sha256=H["7"],
            dependency_proof_sha256=H["8"],
            coverage_manifest_sha256=self.coverage.coverage_manifest_sha256,
            direct_request_set_sha256=_request_set_digest(
                self.coverage.coverage_manifest_sha256, ratio, self.requests
            ),
            direct_snapshot_set_sha256=_snapshot_set_digest(
                self.coverage.coverage_manifest_sha256, ratio, self.snapshots
            ),
            direct_ratio=ratio, overlays=overlays,
        )

    @property
    def resolver(self) -> dict[str, DirectSourceSnapshot]:
        return {item.snapshot_sha256: item for item in self.snapshots}


def _alternate_coverage(
    fixture: DirectCompositorFixture,
    seeds,
    *,
    ineligible: frozenset[str] = frozenset(),
):
    seeds = tuple(sorted(
        seeds, key=lambda item: (item.source_identity.casefold(), item.source_identity)
    ))
    old_metrics = metrics_for_coverage(seeds)
    metric_sources = tuple(
        source_metrics(
            seed.source_identity, eligible=False,
            source_size=seed.source_size, source_sha256=seed.source_sha256,
        ) if seed.source_identity in ineligible else metric
        for seed, metric in zip(seeds, old_metrics.sources)
    )
    old_inventory = inventory_for_coverage(seeds)
    metrics = build_adaptive_candidate_metrics_proof(
        family_id=fixture.base_snapshot.family_id,
        family_input_sha256=fixture.base_snapshot.family_input_sha256,
        candidate_id=fixture.base_spec.candidate_id,
        candidate_cache_digest=fixture.base_snapshot.candidate_cache_digest,
        base_spec_sha256=candidate_spec_sha256(fixture.base_spec),
        source_manifest_sha256=fixture.base_snapshot.source_manifest.digest,
        source_snapshot_sha256=fixture.base_snapshot.snapshot_sha256,
        original_graph_sha256=H["a"], candidate_graph_sha256=H["b"],
        raw_metrics_sha256=H["c"], sources=metric_sources,
    )
    inventory = build_adaptive_direct_state_inventory(
        family_id=fixture.base_snapshot.family_id,
        family_input_sha256=fixture.base_snapshot.family_input_sha256,
        base_candidate_id=fixture.base_spec.candidate_id,
        base_spec_sha256=candidate_spec_sha256(fixture.base_spec),
        base_cache_digest=fixture.base_snapshot.candidate_cache_digest,
        base_source_manifest_sha256=fixture.base_snapshot.source_manifest.digest,
        base_source_snapshot_sha256=fixture.base_snapshot.snapshot_sha256,
        complete_source_identities=old_inventory.complete_source_identities,
        rows=old_inventory.rows,
    )
    return build_adaptive_direct_coverage_manifest(
        family_id=fixture.base_snapshot.family_id,
        family_input_sha256=fixture.base_snapshot.family_input_sha256,
        base_candidate_id=fixture.base_spec.candidate_id,
        base_spec_sha256=candidate_spec_sha256(fixture.base_spec),
        base_cache_digest=fixture.base_snapshot.candidate_cache_digest,
        base_source_manifest_sha256=fixture.base_snapshot.source_manifest.digest,
        base_source_snapshot_sha256=fixture.base_snapshot.snapshot_sha256,
        metrics_proof=metrics, state_inventory=inventory,
    )


class AdaptiveDirectCompositorTests(unittest.TestCase):
    def test_rejects_truncated_extra_zero_eligible_and_ineligible_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = DirectCompositorFixture(Path(temporary).resolve())
            seeds = tuple(coverage_source(
                request.source_identity, source_size=request.source_size,
                source_sha256=request.source_sha256,
            ) for request in fixture.requests)
            extra = coverage_source(
                "meshes/extra.smd", source_size=fixture.requests[0].source_size,
                source_sha256=fixture.requests[0].source_sha256,
            )
            with self.assertRaises(ValueError):
                _alternate_coverage(
                    fixture, seeds,
                    ineligible=frozenset(item.source_identity for item in seeds),
                )
            variants = {
                "truncated": _alternate_coverage(fixture, seeds[:1]),
                "extra": _alternate_coverage(fixture, seeds + (extra,)),
                "ineligible-member": _alternate_coverage(
                    fixture, seeds, ineligible=frozenset((seeds[-1].source_identity,))
                ),
            }
            for name, coverage in variants.items():
                workspace = fixture.root / name
                with self.subTest(name=name), self.assertRaises(ValueError):
                    compose_candidate_sources(
                        fixture.base_build, fixture.recipe, fixture.resolver,
                        workspace, None, coverage_manifest=coverage,
                    )
                self.assertFalse(workspace.exists())

    def test_requires_typed_complete_coverage_and_authoritative_base_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = DirectCompositorFixture(Path(temporary).resolve())
            with self.assertRaises(ValueError):
                compose_candidate_sources(
                    fixture.base_build, fixture.recipe, fixture.resolver,
                    fixture.root / "missing-coverage", None,
                )
            borrowed_snapshot = build_recovery_source_snapshot(
                kind="candidate", family_id=fixture.base_snapshot.family_id,
                family_input_sha256=fixture.base_snapshot.family_input_sha256,
                optimizer_contract_sha256=fixture.base_snapshot.optimizer_contract_sha256,
                whole_profile_sha256=fixture.base_snapshot.whole_profile_sha256,
                focused_profile_sha256=fixture.base_snapshot.focused_profile_sha256,
                dependency_proof_sha256=fixture.base_snapshot.dependency_proof_sha256,
                candidate_id=fixture.base_snapshot.candidate_id,
                candidate_cache_digest=fixture.base_snapshot.candidate_cache_digest,
                source_root=fixture.base_root,
                source_manifest=fixture.base_snapshot.source_manifest,
                focused_evidence=(FocusedEvidenceRef("r-" + H["d"], H["e"]),),
            )
            borrowed_build = replace(fixture.base_build, source_snapshot=borrowed_snapshot)
            with self.assertRaises(ValueError):
                compose_candidate_sources(
                    borrowed_build, fixture.recipe, fixture.resolver,
                    fixture.root / "borrowed-base", None,
                    coverage_manifest=fixture.coverage,
                )

    def test_composes_direct_outputs_onto_exact_base_paths_and_preserves_full_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = DirectCompositorFixture(Path(temporary).resolve())
            result = compose_candidate_sources(
                fixture.base_build, fixture.recipe, fixture.resolver,
                fixture.root / "composed", None, coverage_manifest=fixture.coverage,
            )
            self.assertEqual(result.composition.kind, "adaptive-direct-fallback-v1")
            self.assertEqual(
                tuple(item.source_identity for item in result.composition.changed_sources),
                tuple(item.source_identity for item in fixture.requests),
            )
            for request, snapshot in zip(fixture.requests, fixture.snapshots):
                self.assertEqual(
                    (result.workspace / "src" / request.source_relative_path).read_bytes(),
                    (snapshot.source_root / "output.smd").read_bytes(),
                )
            for relative in (
                "main.qc", "shared.qci", "physics/collision.smd",
                "anim/idle.smd", "notes/readme.txt",
            ):
                self.assertEqual(
                    (result.workspace / "src" / relative).read_bytes(),
                    (fixture.base_root / relative).read_bytes(),
                )
            proof = validate_composition_proof(
                fixture.recipe, fixture.resolver, fixture.base_root,
                result.workspace / "src", None, coverage_manifest=fixture.coverage,
                base_snapshot=fixture.base_snapshot,
            )
            self.assertEqual(proof, result.composition)

    def test_rejects_cross_mode_snapshot_before_creating_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = DirectCompositorFixture(Path(temporary).resolve())
            workspace = fixture.root / "cross-mode"
            resolver = dict(fixture.resolver)
            resolver[fixture.snapshots[0].snapshot_sha256] = fixture.base_snapshot
            with self.assertRaises((TypeError, ValueError)):
                compose_candidate_sources(
                    fixture.base_build, fixture.recipe, resolver, workspace, None,
                    coverage_manifest=fixture.coverage,
                )
            self.assertFalse(workspace.exists())

    def test_rejects_stale_direct_input_and_output_without_partial_tree(self) -> None:
        for mutate_input in (True, False):
            with self.subTest(mutate_input=mutate_input), tempfile.TemporaryDirectory() as temporary:
                fixture = DirectCompositorFixture(Path(temporary).resolve())
                if mutate_input:
                    request = fixture.requests[0]
                    (fixture.base_root / request.source_relative_path).write_bytes(b"stale input")
                else:
                    (fixture.snapshots[0].source_root / "output.smd").write_bytes(b"stale output")
                workspace = fixture.root / "stale"
                with self.assertRaises(ValueError):
                    compose_candidate_sources(
                        fixture.base_build, fixture.recipe, fixture.resolver, workspace, None,
                        coverage_manifest=fixture.coverage,
                    )
                self.assertFalse(workspace.exists())

    def test_rejects_undeclared_qc_collision_animation_and_auxiliary_mutations(self) -> None:
        for relative in (
            "main.qc", "shared.qci", "physics/collision.smd",
            "anim/idle.smd", "notes/readme.txt",
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                fixture = DirectCompositorFixture(Path(temporary).resolve())
                composed = fixture.root / "manual"
                composed.mkdir()
                import shutil
                shutil.copytree(fixture.base_root, composed / "src")
                for request, snapshot in zip(fixture.requests, fixture.snapshots):
                    (composed / "src" / request.source_relative_path).write_bytes(
                        (snapshot.source_root / "output.smd").read_bytes()
                    )

    def test_rejects_semantically_valid_qc_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = DirectCompositorFixture(Path(temporary).resolve())
            composed = fixture.root / "manual"
            import shutil
            shutil.copytree(fixture.base_root, composed)
            for request, snapshot in zip(fixture.requests, fixture.snapshots):
                (composed / request.source_relative_path).write_bytes(
                    (snapshot.source_root / "output.smd").read_bytes()
                )
            with (composed / "main.qc").open("a", encoding="utf-8") as stream:
                stream.write("// semantically inert rewrite\n")
            with self.assertRaises(ValueError):
                validate_composition_proof(
                    fixture.recipe, fixture.resolver, fixture.base_root,
                    composed, None, coverage_manifest=fixture.coverage,
                    base_snapshot=fixture.base_snapshot,
                )
                (composed / "src" / relative).write_bytes(b"undeclared mutation")
                with self.assertRaises((ValueError, UnicodeError)):
                    validate_composition_proof(
                        fixture.recipe, fixture.resolver, fixture.base_root,
                        composed / "src", None, coverage_manifest=fixture.coverage,
                        base_snapshot=fixture.base_snapshot,
                    )

    def test_cancellation_during_copy_leaves_no_partial_composed_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = DirectCompositorFixture(Path(temporary).resolve(), visual_count=8)
            event = threading.Event()
            event.set()
            workspace = fixture.root / "cancelled"
            with self.assertRaises(ProcessCancelledError):
                compose_candidate_sources(
                    fixture.base_build, fixture.recipe, fixture.resolver, workspace, event,
                    coverage_manifest=fixture.coverage,
                )
            self.assertFalse(workspace.exists())

    def test_cancellation_at_base_and_overlay_copy_phases_is_atomic(self) -> None:
        import maximum_optimizer.composite as composite_module

        for phase in ("base", "overlay"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                fixture = DirectCompositorFixture(Path(temporary).resolve())
                event = threading.Event()
                original_copy = composite_module._copy_file_no_follow
                base_file_count = fixture.base_snapshot.source_manifest.total_files
                trigger = 1 if phase == "base" else base_file_count + 1
                calls = 0

                def cancelling_copy(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    result = original_copy(*args, **kwargs)
                    if calls == trigger:
                        event.set()
                    return result

                workspace = fixture.root / f"cancel-{phase}"
                with mock.patch(
                    "maximum_optimizer.composite._copy_file_no_follow",
                    side_effect=cancelling_copy,
                ):
                    with self.assertRaises(ProcessCancelledError):
                        compose_candidate_sources(
                            fixture.base_build, fixture.recipe, fixture.resolver,
                            workspace, event, coverage_manifest=fixture.coverage,
                        )
                self.assertFalse(workspace.exists())
                self.assertEqual(tuple(fixture.root.glob(".*.composition-cleanup-*")), ())

    def test_rejects_snapshot_alias_from_another_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "first").mkdir()
            (root / "second").mkdir()
            fixture = DirectCompositorFixture(root / "first", ratio=0.5)
            other = DirectCompositorFixture(root / "second", ratio=0.4)
            resolver = dict(fixture.resolver)
            resolver[fixture.snapshots[0].snapshot_sha256] = other.snapshots[0]
            workspace = root / "cross-ratio"
            with self.assertRaises(ValueError):
                compose_candidate_sources(
                    fixture.base_build, fixture.recipe, resolver, workspace, None,
                    coverage_manifest=fixture.coverage,
                )
            self.assertFalse(workspace.exists())

    def test_rejects_workspace_overlapping_direct_input_or_output_roots(self) -> None:
        for root_kind in ("input", "output"):
            with self.subTest(root_kind=root_kind), tempfile.TemporaryDirectory() as temporary:
                fixture = DirectCompositorFixture(Path(temporary).resolve())
                parent = (
                    fixture.snapshots[0].input_source_root
                    if root_kind == "input" else fixture.snapshots[0].source_root
                )
                workspace = parent / "nested-composition"
                with self.assertRaises(ValueError):
                    compose_candidate_sources(
                        fixture.base_build, fixture.recipe, fixture.resolver,
                        workspace, None, coverage_manifest=fixture.coverage,
                    )
                self.assertFalse(workspace.exists())

    def test_eight_sources_are_deterministic_and_nine_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = DirectCompositorFixture(Path(first).resolve(), visual_count=8)
            two = DirectCompositorFixture(Path(second).resolve(), visual_count=8)
            shuffled = dict(reversed(tuple(two.resolver.items())))
            result_one = compose_candidate_sources(
                one.base_build, one.recipe, one.resolver, one.root / "result", None,
                coverage_manifest=one.coverage,
            )
            result_two = compose_candidate_sources(
                two.base_build, two.recipe, shuffled, two.root / "result", None,
                coverage_manifest=two.coverage,
            )
            self.assertEqual(result_one.composition.evidence_sha256, result_two.composition.evidence_sha256)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                DirectCompositorFixture(Path(temporary).resolve(), visual_count=9)


if __name__ == "__main__":
    unittest.main()
