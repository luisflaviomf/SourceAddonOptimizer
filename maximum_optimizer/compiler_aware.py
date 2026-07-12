from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


COMPILED_VERTEX_BYTES = 82
COMPILED_TRIANGLE_BYTES = 12


class SmdAuditValidationError(RuntimeError):
    """A post-export SMD contract failure safe for whole-file exact fallback."""


def compiler_proxy_bytes(compiled_vertices: int, triangles: int) -> int:
    """Approximate two-VTX Source output; final ranking still uses real sidecars."""
    if type(compiled_vertices) is not int or compiled_vertices < 0:
        raise ValueError("compiled_vertices must be a non-negative integer")
    if type(triangles) is not int or triangles < 0:
        raise ValueError("triangles must be a non-negative integer")
    return COMPILED_VERTEX_BYTES * compiled_vertices + COMPILED_TRIANGLE_BYTES * triangles


def require_triangular_mesh(polygon_count: int, loop_triangle_count: int) -> None:
    if polygon_count != loop_triangle_count:
        raise RuntimeError(
            "Blender decimate output must be explicitly triangulated before smoothing"
        )


def engine_evidence(
    engine: str, strategy: str, blender_version: tuple[int, ...]
) -> dict[str, str]:
    if engine == "blender":
        version = ".".join(str(part) for part in blender_version)
    elif engine == "meshoptimizer":
        version = "1.2.0"
    else:
        raise ValueError("unsupported candidate engine")
    return {"engine": engine, "engine_version": version, "strategy": strategy}


def preserve_whole_source(region_ratios: Iterable[float]) -> bool:
    ratios = tuple(region_ratios)
    return bool(ratios) and all(ratio >= 0.999999 for ratio in ratios)


def exact_source_payload(raw: bytes) -> bytes:
    if not isinstance(raw, bytes):
        raise TypeError("exact source payload must be bytes")
    return raw


def allows_exact_fallback(error: object) -> bool:
    if isinstance(error, SmdAuditValidationError):
        return True
    message = str(error)
    return message in {
        "normal must be non-zero",
        "export lost all hard-normal seam evidence",
    }


def provenance_status(
    fallback_reason: str | None, *, strategy: str
) -> tuple[str, str]:
    if strategy not in {"blender-adaptive-v1", "blender-importance-map-v1"}:
        raise ValueError("unknown Blender research strategy")
    if fallback_reason is None:
        return "optimized", strategy
    return "preserved", f"exact-source-fallback-v1: {fallback_reason}"


def move_modifier_first(modifiers: object, modifier: object) -> None:
    index = modifiers.find(modifier.name)
    if type(index) is not int or index < 0:
        raise RuntimeError("decimate modifier is missing from Blender stack")
    if index:
        modifiers.move(index, 0)
    if modifiers.find(modifier.name) != 0:
        raise RuntimeError("decimate modifier must be first in Blender stack")


@dataclass(frozen=True)
class CompiledCostCalibration:
    snapshot_count: int
    max_absolute_error_bytes: int

    @classmethod
    def from_snapshots(
        cls, snapshots: Iterable[tuple[int, int, int]]
    ) -> "CompiledCostCalibration":
        values = tuple(snapshots)
        if len(values) < 2:
            raise ValueError("compiler calibration requires at least two snapshots")
        errors = tuple(
            abs(actual_bytes - compiler_proxy_bytes(vertices, triangles))
            for vertices, triangles, actual_bytes in values
        )
        return cls(len(values), max(errors))


@dataclass(frozen=True)
class FamilyCandidate:
    strategy: str
    geometry_comparable_bytes: int
    structural_passed: bool
    visual_passed: bool
    runtime_passed: bool

    @property
    def eligible(self) -> bool:
        return self.structural_passed and self.visual_passed and self.runtime_passed


def select_family_winner(
    baseline_geometry_bytes: int, candidates: Iterable[FamilyCandidate]
) -> FamilyCandidate | None:
    if type(baseline_geometry_bytes) is not int or baseline_geometry_bytes < 0:
        raise ValueError("baseline bytes must be a non-negative integer")
    eligible = tuple(
        candidate
        for candidate in candidates
        if candidate.eligible
        and candidate.geometry_comparable_bytes < baseline_geometry_bytes
    )
    if not eligible:
        return None
    return min(eligible, key=lambda item: (item.geometry_comparable_bytes, item.strategy))
