from __future__ import annotations

from dataclasses import dataclass
import math
import re


_REGION_KEY = re.compile(r"^r-[0-9a-f]{64}$")


@dataclass(frozen=True)
class RegionKey:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _REGION_KEY.fullmatch(self.value) is None:
            raise ValueError("invalid region key")


@dataclass(frozen=True)
class ValidationDecision:
    passed: bool
    failed_gates: tuple[str, ...]
    margin_fraction: float

    def __post_init__(self) -> None:
        gates = tuple(self.failed_gates)
        if type(self.passed) is not bool:
            raise ValueError("validation result must be boolean")
        if any(type(gate) is not str or not gate for gate in gates):
            raise ValueError("validation gates must be non-empty strings")
        if len(set(gates)) != len(gates):
            raise ValueError("validation gates must be unique")
        if self.passed == bool(gates):
            raise ValueError("validation result is inconsistent with failed gates")
        if not math.isfinite(self.margin_fraction) or not 0.0 <= self.margin_fraction <= 1.0:
            raise ValueError("validation margin must be finite and in [0, 1]")
        object.__setattr__(self, "failed_gates", gates)


@dataclass(frozen=True)
class RiskFeatures:
    curvature_p95_norm: float
    silhouette_fraction: float
    hard_boundary_density: float
    uv_seam_density: float
    material_semantic_risk: float
    skinning_risk: float
    visibility_confidence: float
    score: float
    target_ratio: float


@dataclass(frozen=True)
class RegionMetrics:
    surface_p95: float
    surface_max: float
    normal_p95_degrees: float
    normal_max_degrees: float
    silhouette_iou_loss: float
    silhouette_boundary_p95_px: float
    uv_p95: float
    material_boundary_p95_px: float
    skinning_p95: float
    skinning_max: float


@dataclass(frozen=True)
class RegionBudget:
    surface_p95: float
    surface_max: float
    normal_p95_degrees: float
    normal_max_degrees: float
    silhouette_iou_loss: float
    silhouette_boundary_p95_px: float
    uv_p95: float
    material_boundary_p95_px: float
    skinning_p95: float
    skinning_max: float

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"profile limit {name} must be finite")
            if float(value) <= 0.0:
                raise ValueError(f"profile limit {name} must be positive")
        if self.silhouette_iou_loss > 1.0:
            raise ValueError("silhouette IoU loss must not exceed 1")
        if self.normal_p95_degrees > 180.0 or self.normal_max_degrees > 180.0:
            raise ValueError("normal limits must not exceed 180 degrees")


@dataclass(frozen=True)
class MaximumProfile:
    schema: int
    version: str
    calibrated: bool
    seed_ratio: float
    max_simplifier_evaluations: int
    near_limit_fraction: float
    sample_count: int
    silhouette_resolution: int
    limits: RegionBudget
    sha256: str


@dataclass(frozen=True)
class SizeAccounting:
    comparable_bytes: int
    dx80_bytes: int
