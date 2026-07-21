from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .contracts import MaximumProfile, RegionBudget


_PROFILE_FIELDS = {
    "schema",
    "version",
    "calibrated",
    "seed_ratio",
    "max_simplifier_evaluations",
    "near_limit_fraction",
    "sample_count",
    "silhouette_resolution",
    "limits",
}
_LIMIT_FIELDS = set(RegionBudget.__dataclass_fields__)


def _finite_ratio(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be finite")
    result = float(value)
    if not math.isfinite(result) or not 0.0 < result <= 1.0:
        raise ValueError(f"{label} must be finite and in (0, 1]")
    return result


def load_profile(path: Path) -> MaximumProfile:
    profile_path = Path(path)
    raw = profile_path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("profile is not valid UTF-8 JSON") from exc
    if type(payload) is not dict or set(payload) != _PROFILE_FIELDS:
        raise ValueError("profile fields do not match schema")
    limits = payload["limits"]
    if type(limits) is not dict or set(limits) != _LIMIT_FIELDS:
        raise ValueError("profile limit fields do not match schema")
    if payload["schema"] != 1 or payload["version"] != "maximum-adaptive-v2":
        raise ValueError("unsupported profile schema or version")
    if type(payload["calibrated"]) is not bool:
        raise ValueError("profile calibrated flag must be boolean")
    if type(payload["max_simplifier_evaluations"]) is not int or payload["max_simplifier_evaluations"] != 3:
        raise ValueError("profile must allow exactly three simplifier evaluations")
    if type(payload["sample_count"]) is not int or payload["sample_count"] < 256:
        raise ValueError("profile sample count is invalid")
    silhouette_resolution = payload["silhouette_resolution"]
    if (
        type(silhouette_resolution) is not int
        or not 256 <= silhouette_resolution <= 1024
        or silhouette_resolution & (silhouette_resolution - 1)
    ):
        raise ValueError("profile silhouette resolution must be a power of two in [256, 1024]")
    return MaximumProfile(
        schema=1,
        version="maximum-adaptive-v2",
        calibrated=payload["calibrated"],
        seed_ratio=_finite_ratio(payload["seed_ratio"], "seed ratio"),
        max_simplifier_evaluations=3,
        near_limit_fraction=_finite_ratio(payload["near_limit_fraction"], "near-limit fraction"),
        sample_count=payload["sample_count"],
        silhouette_resolution=silhouette_resolution,
        limits=RegionBudget(**limits),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
