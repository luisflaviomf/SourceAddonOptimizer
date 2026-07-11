from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

EngineName = Literal["fidelity", "blender", "meshoptimizer"]
FamilyStatus = Literal["optimized", "preserved", "failed", "cancelled"]


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
    bytes_by_kind: dict[str, int]
    vertices_by_lod: dict[int, int]
    artifacts: tuple[ArtifactStat, ...]

    def __post_init__(self) -> None:
        if self.total_bytes != sum(item.size_bytes for item in self.artifacts):
            raise ValueError("total_bytes must equal artifact bytes")

    def to_dict(self) -> dict:
        return {**asdict(self), "root": str(self.root)}


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

    def cache_payload(self) -> dict:
        return asdict(self)


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
    metrics: dict[str, float] = field(default_factory=dict)
    worst_scope: str = ""


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
