from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Literal

from .contracts import SizeAccounting


def comparable_model_bytes(root: Path) -> SizeAccounting:
    comparable = 0
    dx80 = 0
    for path in sorted(Path(root).rglob("*"), key=lambda value: value.as_posix().casefold()):
        if not path.is_file():
            continue
        size = path.stat().st_size
        if path.name.casefold().endswith(".dx80.vtx"):
            dx80 += size
        else:
            comparable += size
    return SizeAccounting(comparable, dx80)


def reduction_percent(original: SizeAccounting, final: SizeAccounting) -> float:
    if original.comparable_bytes <= 0:
        return 0.0
    return (original.comparable_bytes - final.comparable_bytes) * 100.0 / original.comparable_bytes


@dataclass(frozen=True)
class RegionStatusCounts:
    aggressive: int
    lighter: int
    normal_fallback: int
    original_fallback: int
    ambiguous: int
    failed: int


@dataclass(frozen=True)
class ComparableSizeReport:
    original_comparable: int
    final_comparable: int
    saved_comparable: int
    dx80_removed: int


@dataclass(frozen=True)
class StageTiming:
    stage: str
    wall_seconds: float
    cpu_seconds: float
    peak_working_set_bytes: int


@dataclass(frozen=True)
class RegionReport:
    key: str
    source: str
    material: str
    representation: str
    original_triangles: int
    normal_triangles: int
    selected_triangles: int
    selected_vertices: int
    risk_score: float
    target_ratio: float
    simplifier_evaluations: int
    cache_hits: int
    targeted_render: bool
    failed_gates: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class MaximumRunReport:
    family_status: Literal["optimized", "preserved", "failed", "cancelled"]
    regions: RegionStatusCounts
    sizes: ComparableSizeReport
    full_family_renders: int
    targeted_renders: int
    studiomdl_compiles: int
    simplifier_evaluations: int
    cache_hits: int
    original_triangles: int
    normal_triangles: int
    final_triangles: int
    stages: tuple[StageTiming, ...]
    region_details: tuple[RegionReport, ...]
    failures: tuple[str, ...]
    profile_sha256: str
    profile_version: str
    report_path: Path


def _json_value(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def maximum_report_payload(report: MaximumRunReport) -> dict[str, object]:
    payload = _json_value(asdict(report))
    payload["schema"] = 1
    payload["profile"] = {
        "version": report.profile_version,
        "sha256": report.profile_sha256,
    }
    payload.pop("profile_version", None)
    payload.pop("profile_sha256", None)
    return payload


def write_atomic_maximum_report(report: MaximumRunReport) -> None:
    path = Path(report.report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    encoded = json.dumps(
        maximum_report_payload(report),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    try:
        temporary.write_text(encoded, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
