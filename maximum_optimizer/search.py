from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Literal

from .cache import FileRegionCache
from .contracts import ValidationDecision
from .regions import SmdRegion


Representation = Literal["aggressive", "lighter", "normal", "original"]


@dataclass(frozen=True)
class RegionRequest:
    original: SmdRegion
    normal: SmdRegion
    classified_ratio: float
    normal_validation: ValidationDecision
    original_triangle_count: int
    normal_triangle_count: int
    source_sha256: str
    profile_sha256: str
    attribute_contract_sha256: str
    engine_version: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.classified_ratio) or not 0.0 < self.classified_ratio <= 1.0:
            raise ValueError("classified ratio must be in (0, 1]")
        if self.original_triangle_count <= 0 or not 0 < self.normal_triangle_count <= self.original_triangle_count:
            raise ValueError("region triangle counts are invalid")
        for value in (self.source_sha256, self.profile_sha256, self.attribute_contract_sha256):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError("region request hash is invalid")
        if self.engine_version <= 0:
            raise ValueError("engine version is invalid")


@dataclass(frozen=True)
class RegionDecision:
    selected: SmdRegion
    representation: Representation
    ratio: float
    evaluations: int
    cache_hits: int
    attempted_ratios: tuple[float, ...]
    validation: ValidationDecision
    reason: str


def candidate_ratios(request: RegionRequest) -> tuple[float, ...]:
    target = request.classified_ratio
    if request.normal_validation.passed:
        if request.normal_validation.margin_fraction < 0.15:
            return ()
        normal_ratio = request.normal_triangle_count / request.original_triangle_count
        values = (target, (target + normal_ratio) / 2.0)
        return tuple(round(value, 12) for value in values if value < normal_ratio - 0.02)[:2]
    values = (target, (target + 1.0) / 2.0, 0.85)
    return tuple(dict.fromkeys(round(value, 12) for value in values))[:3]


def _cache_key(request: RegionRequest, ratio: float, base: str) -> str:
    payload = {
        "attribute_contract_sha256": request.attribute_contract_sha256,
        "base": base,
        "engine_version": request.engine_version,
        "profile_sha256": request.profile_sha256,
        "ratio": format(ratio, ".12g"),
        "region_key": request.original.key.value,
        "source_sha256": request.source_sha256,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def optimize_region(
    request: RegionRequest,
    simplify: Callable[[SmdRegion, float], SmdRegion],
    validate: Callable[[SmdRegion], ValidationDecision],
    cache: FileRegionCache,
) -> RegionDecision:
    ratios = candidate_ratios(request)
    if not ratios:
        return RegionDecision(
            request.normal,
            "normal",
            request.normal_triangle_count / request.original_triangle_count,
            0,
            0,
            (),
            request.normal_validation,
            "normal candidate is already near a fidelity limit",
        )

    base = request.normal if request.normal_validation.passed else request.original
    base_name = "normal" if request.normal_validation.passed else "original"
    evaluations = 0
    cache_hits = 0
    simplifier_errors = 0
    attempted: list[float] = []
    for ratio in ratios:
        attempted.append(ratio)
        key = _cache_key(request, ratio, base_name)
        candidate = cache.get(key)
        if candidate is None:
            evaluations += 1
            try:
                candidate = simplify(base, ratio)
            except Exception:
                simplifier_errors += 1
                continue
            cache.put(key, candidate)
        else:
            cache_hits += 1
        if len(candidate.triangles) >= len(base.triangles):
            continue
        decision = validate(candidate)
        if decision.passed:
            return RegionDecision(
                candidate,
                "aggressive" if request.normal_validation.passed else "lighter",
                ratio,
                evaluations,
                cache_hits,
                tuple(attempted),
                decision,
                "regional candidate passed",
            )

    if request.normal_validation.passed:
        error_suffix = f"; {simplifier_errors} simplifier errors" if simplifier_errors else ""
        return RegionDecision(
            request.normal,
            "normal",
            request.normal_triangle_count / request.original_triangle_count,
            evaluations,
            cache_hits,
            tuple(attempted),
            request.normal_validation,
            "aggressive regional candidates failed; kept normal region" + error_suffix,
        )
    error_suffix = f"; {simplifier_errors} simplifier errors" if simplifier_errors else ""
    return RegionDecision(
        request.original,
        "original",
        1.0,
        evaluations,
        cache_hits,
        tuple(attempted),
        ValidationDecision(True, (), 1.0),
        "all simplified regional candidates failed; restored original region" + error_suffix,
    )
