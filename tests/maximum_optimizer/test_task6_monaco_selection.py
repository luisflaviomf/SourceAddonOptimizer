from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest
from unittest import mock

from maximum_optimizer.composite import (
    build_adaptive_candidate_metrics_proof,
    build_adaptive_direct_state_inventory,
    candidate_spec_sha256,
)
from maximum_optimizer.domain import (
    ArtifactStat,
    CandidateEvaluation,
    CandidateSpec,
    CompiledSizeSnapshot,
    FocusRegionResult,
    FocusedEvidenceRef,
    SourceFileProof,
    ValidationResult,
)
from maximum_optimizer.monaco_selection import (
    RetainedMonacoBaseProof,
    build_retained_monaco_base_proof,
    select_exact_fallback_sources,
    select_monaco_base,
)
from tests.maximum_optimizer.test_orchestrator import _focus_target
from tests.maximum_optimizer.test_task6_contracts import (
    H,
    coverage_source,
    inventory_for_coverage,
    metrics_for_coverage,
    source_metrics,
)


def _spec(candidate_id: str) -> CandidateSpec:
    return CandidateSpec(
        candidate_id, "blender", 0.5, 0.0, "blender-adaptive-v1",
        strategy="blender-adaptive-v1", transfer="blender-native-v1",
    )


def _typed_inputs(spec: CandidateSpec, sources):
    old_metrics = metrics_for_coverage(sources)
    metrics = build_adaptive_candidate_metrics_proof(
        family_id=H["0"], family_input_sha256=H["1"],
        candidate_id=spec.candidate_id, candidate_cache_digest=H["3"],
        base_spec_sha256=candidate_spec_sha256(spec),
        source_manifest_sha256=H["4"], source_snapshot_sha256=H["5"],
        original_graph_sha256=H["6"], candidate_graph_sha256=H["7"],
        raw_metrics_sha256=H["8"], sources=old_metrics.sources,
    )
    old_inventory = inventory_for_coverage(sources)
    inventory = build_adaptive_direct_state_inventory(
        family_id=H["0"], family_input_sha256=H["1"],
        base_candidate_id=spec.candidate_id,
        base_spec_sha256=candidate_spec_sha256(spec), base_cache_digest=H["3"],
        base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
        complete_source_identities=old_inventory.complete_source_identities,
        rows=old_inventory.rows,
    )
    return metrics, inventory


def _evaluation(spec: CandidateSpec, size: int, *, structural=True, whole=True, focused=True):
    snapshot = CompiledSizeSnapshot(
        Path("compiled"), size, {"mdl": size}, {},
        (ArtifactStat("model.mdl", "mdl", size),),
    )
    target = _focus_target()
    region = FocusRegionResult(target, ValidationResult(focused), H["a"], False)
    return CandidateEvaluation(
        spec, snapshot, ValidationResult(structural), ValidationResult(whole and focused),
        Path("compiled"), ValidationResult(whole), {target.region_key: region},
    )


def _proof(spec: CandidateSpec, sources=None) -> RetainedMonacoBaseProof:
    sources = (coverage_source(),) if sources is None else tuple(sources)
    metrics, inventory = _typed_inputs(spec, sources)
    return build_retained_monaco_base_proof(
        metrics, inventory, (FocusedEvidenceRef(_focus_target().region_key, H["a"]),)
    )


def _current_sources(proof: RetainedMonacoBaseProof):
    return tuple(
        SourceFileProof(
            item.source_identity, "visual-source", item.output_relative_path,
            item.output_size, item.output_sha256,
        )
        for item in reversed(proof.metrics.sources)
    )


class MonacoBaseSelectionTests(unittest.TestCase):
    def test_selects_smallest_passing_ordinary_retained_base_then_id(self) -> None:
        first, second = _spec("base-a"), _spec("base-b")
        evaluations = (
            _evaluation(first, 60), _evaluation(second, 60),
            _evaluation(_spec("larger"), 80),
        )
        proofs = {
            "larger": _proof(_spec("larger")),
            "base-b": _proof(second),
            "base-a": _proof(first),
        }

        selected = select_monaco_base(tuple(reversed(evaluations)), proofs)

        self.assertEqual(selected.spec.candidate_id, "base-a")

    def test_rejects_failed_gates_unretained_stale_and_non_schema3_proofs(self) -> None:
        spec = _spec("base")
        valid = _proof(spec)
        cases = (
            _evaluation(spec, 10, structural=False),
            _evaluation(spec, 10, whole=False),
            _evaluation(spec, 10, focused=False),
        )
        for evaluation in cases:
            with self.subTest(evaluation=evaluation):
                self.assertIsNone(
                    select_monaco_base((evaluation,), {"base": valid})
                )
        self.assertIsNone(select_monaco_base((_evaluation(spec, 10),), {}))
        with self.assertRaises(ValueError):
            replace(valid, candidate_cache_digest=H["f"])
        self.assertIsNone(select_monaco_base(
            (_evaluation(spec, 10),), {"base": _proof(_spec("other"))}
        ))
        for schema in (1, 2):
            fake = mock.Mock(schema=schema)
            self.assertIsNone(select_monaco_base((_evaluation(spec, 10),), {"base": fake}))

    def test_filename_and_environment_tokens_never_activate(self) -> None:
        legacy = CandidateSpec("monaco-schema3-env", "blender", 0.5, 0.01, "legacy")
        self.assertIsNone(select_monaco_base((_evaluation(legacy, 1),), {
            legacy.candidate_id: _proof(_spec(legacy.candidate_id)),
        }))


class ExactFallbackSelectionTests(unittest.TestCase):
    def test_selects_complete_exact_union_stably_and_builds_preflight_once(self) -> None:
        sources = (coverage_source("body.smd"), coverage_source("wheel.smd"))
        proof = _proof(_spec("base"), sources)
        factory = mock.Mock(wraps=__import__(
            "maximum_optimizer.composite", fromlist=["build_adaptive_direct_coverage_manifest"]
        ).build_adaptive_direct_coverage_manifest)

        result = select_exact_fallback_sources(
            proof, proof.metrics, proof.state_inventory,
            _current_sources(proof), coverage_factory=factory,
        )

        self.assertEqual(result.source_identities, ("body.smd", "wheel.smd"))
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(result.coverage.complete_source_identities, result.source_identities)

    def test_zero_or_nine_eligible_reject_before_preflight_or_callbacks(self) -> None:
        zero_source = coverage_source("changed.smd")
        eligible = tuple(coverage_source(f"source-{index:02d}.smd") for index in range(9))
        factory = mock.Mock(side_effect=AssertionError("preflight called"))
        callback = mock.Mock(side_effect=AssertionError("callback called"))
        class UntouchedInventory:
            def __iter__(self):
                callback("source-inventory-open")
                raise AssertionError("source inventory touched")

        zero_spec = _spec("base")
        zero_inventory = _typed_inputs(zero_spec, (zero_source,))[1]
        zero_metrics = build_adaptive_candidate_metrics_proof(
            family_id=H["0"], family_input_sha256=H["1"], candidate_id="base",
            candidate_cache_digest=H["3"],
            base_spec_sha256=candidate_spec_sha256(zero_spec),
            source_manifest_sha256=H["4"], source_snapshot_sha256=H["5"],
            original_graph_sha256=H["6"], candidate_graph_sha256=H["7"],
            raw_metrics_sha256=H["8"],
            sources=(source_metrics("changed.smd", eligible=False),),
        )
        zero_proof = build_retained_monaco_base_proof(
            zero_metrics, zero_inventory,
            (FocusedEvidenceRef(_focus_target().region_key, H["a"]),),
        )
        self.assertIsNone(select_exact_fallback_sources(
            zero_proof, zero_metrics, zero_proof.state_inventory,
            UntouchedInventory(), coverage_factory=factory,
            reservation_callback=callback, direct_io_callback=callback,
        ))

        nine_proof = _proof(_spec("base"), eligible)
        with self.assertRaises(ValueError):
            select_exact_fallback_sources(
                nine_proof, nine_proof.metrics, nine_proof.state_inventory,
                UntouchedInventory(), coverage_factory=factory,
                reservation_callback=callback, direct_io_callback=callback,
            )
        factory.assert_not_called()
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
