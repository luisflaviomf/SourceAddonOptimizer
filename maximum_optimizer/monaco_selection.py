from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Callable, Mapping, Sequence

from .composite import (
    build_adaptive_direct_coverage_manifest,
    candidate_spec_sha256,
)
from .domain import (
    AdaptiveCandidateMetricsProof,
    AdaptiveDirectCoverageManifest,
    AdaptiveDirectStateInventory,
    CandidateEvaluation,
    FocusedEvidenceRef,
    SourceFileProof,
)
from .reporting import canonical_json


_SHA256 = re.compile(r"[0-9a-f]{64}")


def _seal(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _activation_payload(
    *,
    schema: int,
    candidate_id: str,
    candidate_cache_digest: str,
    base_spec_sha256: str,
    source_manifest_sha256: str,
    source_snapshot_sha256: str,
    metrics_evidence_sha256: str,
    state_inventory_sha256: str,
    focused_evidence: tuple[FocusedEvidenceRef, ...],
) -> dict[str, object]:
    return {
        "schema": schema,
        "candidate_id": candidate_id,
        "candidate_cache_digest": candidate_cache_digest,
        "base_spec_sha256": base_spec_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "source_snapshot_sha256": source_snapshot_sha256,
        "metrics_evidence_sha256": metrics_evidence_sha256,
        "state_inventory_sha256": state_inventory_sha256,
        "focused_evidence": [
            {"region_key": item.region_key, "evidence_sha256": item.evidence_sha256}
            for item in focused_evidence
        ],
    }


@dataclass(frozen=True)
class RetainedMonacoBaseProof:
    schema: int
    candidate_id: str
    candidate_cache_digest: str
    base_spec_sha256: str
    source_manifest_sha256: str
    source_snapshot_sha256: str
    metrics: AdaptiveCandidateMetricsProof
    state_inventory: AdaptiveDirectStateInventory
    focused_evidence: tuple[FocusedEvidenceRef, ...]
    activation_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 3:
            raise ValueError("Monaco retained proof requires schema 3")
        if not isinstance(self.metrics, AdaptiveCandidateMetricsProof):
            raise TypeError("Monaco retained metrics proof is invalid")
        if not isinstance(self.state_inventory, AdaptiveDirectStateInventory):
            raise TypeError("Monaco retained state inventory is invalid")
        focused = tuple(self.focused_evidence)
        focused_keys = [(item.region_key.casefold(), item.region_key) for item in focused if isinstance(item, FocusedEvidenceRef)]
        if not focused or len(focused_keys) != len(focused) or focused_keys != sorted(focused_keys) or len({item[0] for item in focused_keys}) != len(focused):
            raise ValueError("Monaco retained focused evidence is not complete canonical")
        if (
            self.metrics.candidate_id != self.candidate_id
            or self.metrics.candidate_cache_digest != self.candidate_cache_digest
            or self.metrics.base_spec_sha256 != self.base_spec_sha256
            or self.metrics.source_manifest_sha256 != self.source_manifest_sha256
            or self.metrics.source_snapshot_sha256 != self.source_snapshot_sha256
            or self.state_inventory.base_candidate_id != self.candidate_id
            or self.state_inventory.base_cache_digest != self.candidate_cache_digest
            or self.state_inventory.base_spec_sha256 != self.base_spec_sha256
            or self.state_inventory.base_source_manifest_sha256
            != self.source_manifest_sha256
            or self.state_inventory.base_source_snapshot_sha256
            != self.source_snapshot_sha256
            or self.metrics.family_id != self.state_inventory.family_id
            or self.metrics.family_input_sha256
            != self.state_inventory.family_input_sha256
            or tuple(item.source_identity for item in self.metrics.sources)
            != self.state_inventory.complete_source_identities
        ):
            raise ValueError("Monaco retained proof bindings are stale")
        for value in (
            self.candidate_cache_digest,
            self.base_spec_sha256,
            self.source_manifest_sha256,
            self.source_snapshot_sha256,
            self.activation_sha256,
        ):
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise ValueError("Monaco retained proof hash is invalid")
        payload = _activation_payload(
            schema=self.schema,
            candidate_id=self.candidate_id,
            candidate_cache_digest=self.candidate_cache_digest,
            base_spec_sha256=self.base_spec_sha256,
            source_manifest_sha256=self.source_manifest_sha256,
            source_snapshot_sha256=self.source_snapshot_sha256,
            metrics_evidence_sha256=self.metrics.evidence_sha256,
            state_inventory_sha256=self.state_inventory.state_inventory_sha256,
            focused_evidence=focused,
        )
        if self.activation_sha256 != _seal(payload):
            raise ValueError("Monaco retained proof seal mismatch")
        object.__setattr__(self, "focused_evidence", focused)


def build_retained_monaco_base_proof(
    metrics: AdaptiveCandidateMetricsProof,
    state_inventory: AdaptiveDirectStateInventory,
    focused_evidence: Sequence[FocusedEvidenceRef],
) -> RetainedMonacoBaseProof:
    if not isinstance(metrics, AdaptiveCandidateMetricsProof):
        raise TypeError("Monaco metrics proof is invalid")
    if not isinstance(state_inventory, AdaptiveDirectStateInventory):
        raise TypeError("Monaco state inventory is invalid")
    values = {
        "schema": 3,
        "candidate_id": metrics.candidate_id,
        "candidate_cache_digest": metrics.candidate_cache_digest,
        "base_spec_sha256": metrics.base_spec_sha256,
        "source_manifest_sha256": metrics.source_manifest_sha256,
        "source_snapshot_sha256": metrics.source_snapshot_sha256,
        "metrics": metrics,
        "state_inventory": state_inventory,
        "focused_evidence": tuple(focused_evidence),
    }
    values["activation_sha256"] = _seal(_activation_payload(
        schema=3,
        candidate_id=metrics.candidate_id,
        candidate_cache_digest=metrics.candidate_cache_digest,
        base_spec_sha256=metrics.base_spec_sha256,
        source_manifest_sha256=metrics.source_manifest_sha256,
        source_snapshot_sha256=metrics.source_snapshot_sha256,
        metrics_evidence_sha256=metrics.evidence_sha256,
        state_inventory_sha256=state_inventory.state_inventory_sha256,
        focused_evidence=tuple(focused_evidence),
    ))
    return RetainedMonacoBaseProof(**values)


def select_monaco_base(
    evaluations: Sequence[CandidateEvaluation],
    retained_proofs: Mapping[str, RetainedMonacoBaseProof],
) -> CandidateEvaluation | None:
    eligible: list[CandidateEvaluation] = []
    for evaluation in evaluations:
        if not isinstance(evaluation, CandidateEvaluation):
            continue
        spec = evaluation.spec
        proof = retained_proofs.get(spec.candidate_id)
        if not isinstance(proof, RetainedMonacoBaseProof):
            continue
        if (
            proof.schema != 3
            or proof.candidate_id != spec.candidate_id
            or proof.base_spec_sha256 != candidate_spec_sha256(spec)
            or spec.engine != "blender"
            or spec.strategy != "blender-adaptive-v1"
            or spec.composite_recipe is not None
            or not evaluation.structural.passed
            or not evaluation.whole_visual.passed
            or not evaluation.visual.passed
            or not evaluation.focused_by_region
            or tuple(sorted(evaluation.focused_by_region)) != tuple(item.region_key for item in proof.focused_evidence)
            or any(evaluation.focused_by_region[item.region_key].evidence_sha256 != item.evidence_sha256 for item in proof.focused_evidence)
            or not all(
                item.validation.passed
                for item in evaluation.focused_by_region.values()
            )
        ):
            continue
        eligible.append(evaluation)
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda item: (item.size.total_bytes, item.spec.candidate_id),
    )


@dataclass(frozen=True)
class ExactFallbackSelection:
    source_identities: tuple[str, ...]
    coverage: AdaptiveDirectCoverageManifest

    def __post_init__(self) -> None:
        if not isinstance(self.coverage, AdaptiveDirectCoverageManifest):
            raise TypeError("Monaco coverage preflight is invalid")
        if self.source_identities != tuple(
            item.source_identity
            for item in self.coverage.sources
            if item.eligibility_kind == "eligible-exact-v1"
        ):
            raise ValueError("Monaco fallback selection differs from coverage")


def select_exact_fallback_sources(
    base_proof: RetainedMonacoBaseProof,
    metrics: AdaptiveCandidateMetricsProof,
    state_inventory: AdaptiveDirectStateInventory,
    current_source_proofs: Sequence[SourceFileProof],
    *,
    coverage_factory: Callable[..., AdaptiveDirectCoverageManifest]
    = build_adaptive_direct_coverage_manifest,
    reservation_callback: Callable[..., object] | None = None,
    direct_io_callback: Callable[..., object] | None = None,
) -> ExactFallbackSelection | None:
    # These callbacks belong to the later coordinator. Accepting them here lets tests
    # prove selection and preflight never reserve budget or begin direct I/O.
    _ = reservation_callback, direct_io_callback
    if not isinstance(base_proof, RetainedMonacoBaseProof):
        raise TypeError("Monaco retained base proof is invalid")
    if metrics != base_proof.metrics or state_inventory != base_proof.state_inventory:
        raise ValueError("Monaco fallback inputs differ from retained proof")
    eligible = tuple(
        item for item in metrics.sources if item.kind == "eligible-exact-v1"
    )
    if not eligible:
        return None
    if len(eligible) > 8:
        raise ValueError("Monaco exact fallback exceeds eight eligible sources")

    current = tuple(current_source_proofs)
    if any(not isinstance(item, SourceFileProof) for item in current):
        raise TypeError("Monaco current source proof is invalid")
    current = tuple(sorted(
        current, key=lambda item: (item.file_identity.casefold(), item.file_identity)
    ))
    identities = tuple(item.source_identity for item in metrics.sources)
    if (
        tuple(item.file_identity for item in current) != identities
        or len({item.file_identity.casefold() for item in current}) != len(current)
        or any(item.kind != "visual-source" for item in current)
    ):
        raise ValueError("Monaco current source inventory is incomplete")
    for metric, proof in zip(metrics.sources, current):
        if (proof.size, proof.sha256) != (
            metric.output_size, metric.output_sha256,
        ):
            raise ValueError("Monaco current source bytes are stale")

    coverage = coverage_factory(
        metrics_proof=metrics,
        state_inventory=state_inventory,
        family_id=state_inventory.family_id,
        family_input_sha256=state_inventory.family_input_sha256,
        base_candidate_id=state_inventory.base_candidate_id,
        base_spec_sha256=state_inventory.base_spec_sha256,
        base_cache_digest=state_inventory.base_cache_digest,
        base_source_manifest_sha256=state_inventory.base_source_manifest_sha256,
        base_source_snapshot_sha256=state_inventory.base_source_snapshot_sha256,
    )
    if not isinstance(coverage, AdaptiveDirectCoverageManifest):
        raise TypeError("Monaco coverage factory returned invalid proof")
    selected = tuple(item.source_identity for item in eligible)
    return ExactFallbackSelection(selected, coverage)
