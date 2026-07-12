from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

EngineName = Literal["fidelity", "blender", "meshoptimizer"]
FamilyStatus = Literal["optimized", "preserved", "failed", "cancelled"]


def _deep_freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ArtifactStat:
    relative_path: str
    kind: str
    size_bytes: int
    lod_vertices: tuple[int, ...] = ()


@dataclass(frozen=True)
class CompiledSizeSnapshot:
    root: Path
    total_bytes: int
    bytes_by_kind: Mapping[str, int]
    vertices_by_lod: Mapping[int, int]
    artifacts: tuple[ArtifactStat, ...]

    def __post_init__(self) -> None:
        if self.total_bytes != sum(item.size_bytes for item in self.artifacts):
            raise ValueError("total_bytes must equal artifact bytes")
        object.__setattr__(self, "bytes_by_kind", MappingProxyType(dict(self.bytes_by_kind)))
        object.__setattr__(self, "vertices_by_lod", MappingProxyType(dict(self.vertices_by_lod)))

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "total_bytes": self.total_bytes,
            "bytes_by_kind": dict(self.bytes_by_kind),
            "vertices_by_lod": dict(self.vertices_by_lod),
            "artifacts": tuple(asdict(item) for item in self.artifacts),
        }


@dataclass(frozen=True)
class StructuralFingerprint:
    model_name: str
    bodygroups: tuple[str, ...]
    materials: tuple[str, ...]
    skin_families: tuple[tuple[str, ...], ...]
    bones: tuple[str, ...]
    bone_parents: tuple[tuple[str, str], ...]
    attachments: tuple[str, ...]
    hitboxes: tuple[str, ...]
    sequences: tuple[str, ...]
    mesh_files: tuple[str, ...]
    lod_mesh_files: tuple[str, ...]
    physics_mesh: str | None


@dataclass(frozen=True)
class FamilyManifest:
    family_id: str
    model_rel: str
    source_dir: Path
    original_models_dir: Path
    fingerprint: StructuralFingerprint
    input_hash: str
    required_artifact_kinds: tuple[str, ...]


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    engine: EngineName
    target_ratio: float
    target_error: float
    repair_profile: str
    region_overrides: tuple[tuple[str, float], ...] = ()
    strategy: str = "legacy-v1"
    update_vertices: bool = True
    transfer: str = "projection-v1"

    def __post_init__(self) -> None:
        if self.strategy == "blender-adaptive-v1" and (
            self.engine != "blender"
            or self.update_vertices is not True
            or self.transfer != "blender-native-v1"
            or self.repair_profile != "blender-adaptive-v1"
            or self.target_error != 0.0
        ):
            raise ValueError("Blender adaptive strategy contract is immutable")
        direct_strategies = {"meshopt-direct-v1", "meshopt-direct-position-v1"}
        direct_selected = (
            self.strategy in direct_strategies
            or self.update_vertices is False
            or self.transfer == "direct-v1"
        )
        if direct_selected and (
            self.engine != "meshoptimizer"
            or self.strategy not in direct_strategies
            or self.update_vertices is not False
            or self.transfer != "direct-v1"
            or self.repair_profile != self.strategy
        ):
            raise ValueError("meshopt direct strategy contract is immutable")

    def cache_payload(self) -> dict:
        payload = asdict(self)
        payload["region_overrides"] = [
            {"region_key": region_key, "ratio": ratio}
            for region_key, ratio in self.region_overrides
        ]
        return payload


@dataclass(frozen=True)
class GateFailure:
    gate: str
    scope: str
    measured: float | str | None
    limit: float | str | None
    message: str


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    failures: tuple[GateFailure, ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)
    worst_scope: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))


@dataclass(frozen=True)
class FocusedRegionPolicy:
    schema: int
    selector: str
    top_k: int
    max_whole_states: int = 16
    max_recovery_rounds: int = 3

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1:
            raise ValueError("focused policy schema must be 1")
        if self.selector != "surface-risk-top-k-v1":
            raise ValueError("focused policy selector is invalid")
        if type(self.top_k) is not int or not 1 <= self.top_k <= 4:
            raise ValueError("focused policy top_k must be an integer from 1 through 4")
        if type(self.max_whole_states) is not int or self.max_whole_states != 16:
            raise ValueError("focused policy whole-state bound must be 16")
        if type(self.max_recovery_rounds) is not int or self.max_recovery_rounds != 3:
            raise ValueError("focused policy recovery bound must be 3")


@dataclass(frozen=True)
class WholeStateEvidence:
    state_index: int
    state_name: str
    bodygroups: tuple[tuple[str, int], ...]
    lod_index: int
    poses: tuple[str, ...]
    source_pairs: tuple[tuple[str, str, str], ...]
    reference_manifest: str
    reference_manifest_sha256: str
    candidate_manifest: str
    candidate_manifest_sha256: str
    geometry_rows: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "bodygroups", tuple(tuple(item) for item in self.bodygroups))
        object.__setattr__(self, "poses", tuple(self.poses))
        object.__setattr__(self, "source_pairs", tuple(tuple(item) for item in self.source_pairs))
        object.__setattr__(
            self, "geometry_rows", tuple(_deep_freeze(dict(row)) for row in self.geometry_rows)
        )


@dataclass(frozen=True)
class FocusTarget:
    rank: int
    region_key: str
    source_identity: str
    state_index: int
    state_name: str
    bodygroups: tuple[tuple[str, int], ...]
    lod_index: int
    anchor_pose: str
    surface_bidirectional_p95: float
    surface_max: float
    normalized_p95: float
    normalized_max: float
    selector_input_sha256: str


@dataclass(frozen=True)
class FocusRegionResult:
    target: FocusTarget
    validation: ValidationResult
    evidence_sha256: str
    cache_hit: bool


@dataclass(frozen=True)
class FocusedGateResult:
    validation: ValidationResult
    targets: tuple[FocusTarget, ...]
    regions: Mapping[str, FocusRegionResult]
    evidence_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "targets", tuple(self.targets))
        object.__setattr__(self, "regions", _deep_freeze(dict(self.regions)))


@dataclass(frozen=True)
class CandidateEvaluation:
    spec: CandidateSpec
    size: CompiledSizeSnapshot
    structural: ValidationResult
    visual: ValidationResult
    compiled_models_dir: Path

    @property
    def passed(self) -> bool:
        return self.structural.passed and self.visual.passed


@dataclass(frozen=True)
class CandidateAttempt:
    spec: CandidateSpec
    status: Literal["generated", "compile_failed", "evaluated", "cancelled"]
    evaluation: CandidateEvaluation | None
    error: str = ""


@dataclass(frozen=True)
class SearchBudget:
    max_candidates: int
    min_ratio_step: float
    min_marginal_saving: float

    @classmethod
    def experimental_default(cls) -> "SearchBudget":
        return cls(max_candidates=18, min_ratio_step=0.025, min_marginal_saving=0.005)


@dataclass(frozen=True)
class FamilyOutcome:
    manifest: FamilyManifest
    status: FamilyStatus
    selected: CandidateEvaluation | None
    attempted: tuple[CandidateAttempt, ...]
    reason: str
