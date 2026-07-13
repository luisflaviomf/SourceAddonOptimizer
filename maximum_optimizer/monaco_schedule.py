from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Callable

from .composite import adaptive_direct_recipe, candidate_spec_sha256, optimizer_contract_sha256
from .domain import (
    AdaptiveDirectCoverageManifest,
    CandidateSpec,
    DirectSourceBuildRequest,
    DirectSourceSnapshot,
    SourceOverlay,
    direct_source_request_payload,
    direct_source_snapshot_payload,
)
from .reporting import canonical_json


MONACO_DIRECT_RATIOS = (0.50, 0.45, 0.40, 0.35)


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MonacoRatioReservation:
    ordinal: int
    ratio: float
    token: str

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or not 0 <= self.ordinal < len(MONACO_DIRECT_RATIOS):
            raise ValueError("Monaco reservation ordinal is invalid")
        if self.ratio != MONACO_DIRECT_RATIOS[self.ordinal]:
            raise ValueError("Monaco reservation ratio is not contiguous")
        if type(self.token) is not str or not self.token:
            raise ValueError("Monaco reservation token is invalid")


@dataclass(frozen=True)
class MonacoScheduledCandidate:
    reservation: MonacoRatioReservation
    ratio: float
    requests: tuple[DirectSourceBuildRequest, ...]
    snapshots: tuple[DirectSourceSnapshot, ...]
    request_set_sha256: str
    snapshot_set_sha256: str
    spec: CandidateSpec

    def __post_init__(self) -> None:
        if self.ratio != self.reservation.ratio:
            raise ValueError("Monaco scheduled ratio differs from reservation")
        object.__setattr__(self, "requests", tuple(self.requests))
        object.__setattr__(self, "snapshots", tuple(self.snapshots))


def _canonical_ratio_inputs(
    *, base_spec: CandidateSpec, coverage: AdaptiveDirectCoverageManifest,
    ratio: float, raw: object,
) -> tuple[tuple[DirectSourceBuildRequest, ...], tuple[DirectSourceSnapshot, ...]]:
    if type(raw) not in (tuple, list) or len(raw) != 2:
        raise ValueError("Monaco ratio accessor must return requests and snapshots")
    requests = tuple(raw[0])
    snapshots = tuple(raw[1])
    if any(not isinstance(item, DirectSourceBuildRequest) for item in requests):
        raise ValueError("Monaco direct requests are invalid")
    if any(not isinstance(item, DirectSourceSnapshot) for item in snapshots):
        raise ValueError("Monaco direct snapshots are invalid")
    requests = tuple(sorted(requests, key=lambda item: (item.source_identity.casefold(), item.source_identity)))
    snapshots = tuple(sorted(snapshots, key=lambda item: (item.request.source_identity.casefold(), item.request.source_identity)))
    eligible = tuple(item for item in coverage.sources if item.eligibility_kind == "eligible-exact-v1")
    expected_identities = tuple(item.source_identity for item in eligible)
    request_identities = tuple(item.source_identity for item in requests)
    snapshot_identities = tuple(item.request.source_identity for item in snapshots)
    if request_identities != expected_identities or snapshot_identities != expected_identities:
        raise ValueError("Monaco ratio inputs differ from complete eligible source set")
    if len({item.casefold() for item in request_identities}) != len(request_identities):
        raise ValueError("Monaco ratio source identities are duplicated")
    optimizer = optimizer_contract_sha256(base_spec)
    first = requests[0]
    sources = {item.source_identity: item for item in eligible}
    for request, snapshot in zip(requests, snapshots):
        source = sources[request.source_identity]
        if any((
            request.family_id != coverage.family_id,
            request.family_input_sha256 != coverage.family_input_sha256,
            request.base_candidate_id != coverage.base_candidate_id,
            request.base_spec_sha256 != coverage.base_spec_sha256,
            request.base_cache_digest != coverage.base_cache_digest,
            request.base_source_manifest_sha256 != coverage.base_source_manifest_sha256,
            request.base_source_snapshot_sha256 != coverage.base_source_snapshot_sha256,
            request.coverage_manifest_sha256 != coverage.coverage_manifest_sha256,
            request.source_coverage_sha256 != source.source_coverage_sha256,
            request.source_size != source.source_size,
            request.source_sha256 != source.source_sha256,
            request.source_relative_path != source.source_identity,
            request.optimizer_contract_sha256 != optimizer,
            request.whole_profile_sha256 != first.whole_profile_sha256,
            request.focused_profile_sha256 != first.focused_profile_sha256,
            request.dependency_proof_sha256 != first.dependency_proof_sha256,
            request.direct_ratio != ratio,
            snapshot.request != request,
        )):
            raise ValueError("Monaco ratio request/snapshot binding mismatch")
    return requests, snapshots


def _scheduled_candidate(
    *, base_spec: CandidateSpec, coverage: AdaptiveDirectCoverageManifest,
    reservation: MonacoRatioReservation, raw: object,
) -> MonacoScheduledCandidate:
    ratio = reservation.ratio
    requests, snapshots = _canonical_ratio_inputs(
        base_spec=base_spec, coverage=coverage, ratio=ratio, raw=raw,
    )
    request_set_sha256 = _digest({
        "schema": 1, "kind": "adaptive-direct-request-set-v1",
        "coverage_manifest_sha256": coverage.coverage_manifest_sha256,
        "ratio": ratio,
        "requests": [direct_source_request_payload(item) for item in requests],
    })
    snapshot_set_sha256 = _digest({
        "schema": 1, "kind": "adaptive-direct-snapshot-set-v1",
        "coverage_manifest_sha256": coverage.coverage_manifest_sha256,
        "ratio": ratio,
        "snapshots": [direct_source_snapshot_payload(item) for item in snapshots],
    })
    overlays = tuple(SourceOverlay(
        source_identity=request.source_identity,
        mode="direct-position",
        motivating_region_key=None,
        base_source_sha256=request.source_sha256,
        replacement_sha256=snapshot.output_sha256,
        replacement_size=snapshot.output_size,
        replacement_snapshot_sha256=snapshot.snapshot_sha256,
        replacement_candidate_id=snapshot.direct_candidate_id,
        replacement_cache_digest=snapshot.direct_cache_digest,
        effective_ratio=ratio,
        focused_evidence=(),
        reason="approved-direct-position-v1",
    ) for request, snapshot in zip(requests, snapshots))
    first = requests[0]
    recipe = adaptive_direct_recipe(
        family_id=coverage.family_id,
        family_input_sha256=coverage.family_input_sha256,
        base_candidate_id=coverage.base_candidate_id,
        base_spec_sha256=coverage.base_spec_sha256,
        base_cache_digest=coverage.base_cache_digest,
        base_source_manifest_sha256=coverage.base_source_manifest_sha256,
        base_source_snapshot_sha256=coverage.base_source_snapshot_sha256,
        optimizer_contract_sha256=first.optimizer_contract_sha256,
        whole_profile_sha256=first.whole_profile_sha256,
        focused_profile_sha256=first.focused_profile_sha256,
        dependency_proof_sha256=first.dependency_proof_sha256,
        coverage_manifest_sha256=coverage.coverage_manifest_sha256,
        direct_request_set_sha256=request_set_sha256,
        direct_snapshot_set_sha256=snapshot_set_sha256,
        direct_ratio=ratio,
        overlays=overlays,
    )
    spec = CandidateSpec(
        "adaptive-direct-" + recipe.recipe_sha256,
        base_spec.engine, ratio, base_spec.target_error, base_spec.repair_profile,
        (), strategy=base_spec.strategy, update_vertices=base_spec.update_vertices,
        transfer=base_spec.transfer, composite_recipe=recipe,
    )
    return MonacoScheduledCandidate(
        reservation, ratio, requests, snapshots,
        request_set_sha256, snapshot_set_sha256, spec,
    )


def build_monaco_schedule(
    *, base_spec: CandidateSpec, coverage: AdaptiveDirectCoverageManifest,
    remaining_candidates: int,
    reserve: Callable[[int, float], str],
    access_ratio: Callable[[float], object],
) -> tuple[MonacoScheduledCandidate, ...]:
    if not isinstance(base_spec, CandidateSpec) or base_spec.composite_recipe is not None or base_spec.strategy != "blender-adaptive-v1":
        raise ValueError("Monaco schedule base spec is not ordinary blender-adaptive")
    if not isinstance(coverage, AdaptiveDirectCoverageManifest):
        raise TypeError("Monaco schedule coverage is invalid")
    if base_spec.candidate_id != coverage.base_candidate_id or candidate_spec_sha256(base_spec) != coverage.base_spec_sha256:
        raise ValueError("Monaco schedule base spec differs from coverage preflight")
    if type(remaining_candidates) is not int or remaining_candidates < 0:
        raise ValueError("Monaco remaining budget is invalid")
    if not callable(reserve) or not callable(access_ratio):
        raise TypeError("Monaco scheduling callbacks are invalid")
    prefix = MONACO_DIRECT_RATIOS[:min(remaining_candidates, len(MONACO_DIRECT_RATIOS))]
    reservations = tuple(
        MonacoRatioReservation(index, ratio, reserve(index, ratio))
        for index, ratio in enumerate(prefix)
    )
    return tuple(_scheduled_candidate(
        base_spec=base_spec, coverage=coverage, reservation=item,
        raw=access_ratio(item.ratio),
    ) for item in reservations)
