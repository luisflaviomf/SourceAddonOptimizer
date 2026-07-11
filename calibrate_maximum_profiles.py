from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
import tempfile
from pathlib import Path

from maximum_optimizer.visual_validation import REQUIRED_METRICS


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ROLES = ("roundtrip", "known_good", "known_bad")


def _finite_nonnegative(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {label}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"invalid {label}")
    return number


def _expand_path(raw: object, base: Path) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError("metric path must be a non-empty string")
    missing = sorted(
        {name for name in _ENV_PATTERN.findall(raw) if not os.environ.get(name)}
    )
    if missing:
        raise ValueError(f"missing environment variable: {', '.join(missing)}")
    expanded = _ENV_PATTERN.sub(lambda match: os.environ[match.group(1)], raw)
    if "$" in expanded:
        raise ValueError(f"unexpanded environment expression in path: {raw}")
    path = Path(expanded)
    return path if path.is_absolute() else (base / path).resolve()


def _finite_metrics(raw_bytes: bytes, label: str) -> dict[str, float]:
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid metrics JSON for {label}") from exc
    values = payload.get("metrics") if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        raise ValueError(f"metrics object is missing for {label}")
    result = {}
    for metric in REQUIRED_METRICS:
        value = values.get(metric)
        try:
            result[metric] = _finite_nonnegative(value, f"or missing {metric} for {label}")
        except ValueError as exc:
            raise ValueError(f"invalid or missing {metric} for {label}") from exc
    return result


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate a percentile without values")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _hard_floors(payload: dict) -> dict[str, float]:
    raw = payload.get("hard_floors")
    if not isinstance(raw, dict) or set(raw) != set(REQUIRED_METRICS):
        raise ValueError("hard_floors must contain every required metric")
    floors = {}
    for metric in REQUIRED_METRICS:
        value = raw[metric]
        floors[metric] = _finite_nonnegative(value, f"hard floor for {metric}")
    return floors


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_raw = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_raw)
    try:
        content = json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def calibrate_profile(corpus_path: Path, partition: str, out_path: Path) -> dict:
    corpus_path = Path(corpus_path)
    try:
        corpus_payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid calibration corpus: {corpus_path}") from exc
    if (
        not isinstance(corpus_payload, dict)
        or type(corpus_payload.get("schema")) is not int
        or corpus_payload.get("schema") != 1
    ):
        raise ValueError("calibration corpus schema must be 1")
    families_raw = corpus_payload.get("families")
    if not isinstance(families_raw, list):
        raise ValueError("calibration corpus families must be a list")
    selected = [
        family
        for family in families_raw
        if isinstance(family, dict) and family.get("partition") == partition
    ]
    ids = [family.get("id") for family in selected]
    if (
        len(selected) < 20
        or any(not isinstance(family_id, str) or not family_id for family_id in ids)
        or len(set(ids)) < 20
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("calibration requires at least 20 unique family IDs")
    selected.sort(key=lambda family: family["id"])
    floors = _hard_floors(corpus_payload)
    logical = {
        "schema": 1,
        "partition": partition,
        "hard_floors": floors,
        "families": selected,
    }
    digest = hashlib.sha256(
        json.dumps(logical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    loaded: dict[str, dict[str, dict[str, float]]] = {}
    for family in selected:
        family_id = family["id"]
        if not isinstance(family.get("model_rel"), str) or not family["model_rel"]:
            raise ValueError(f"model_rel is required for {family_id}")
        _expand_path(family.get("addon_root"), corpus_path.parent)
        metrics_paths = family.get("metrics")
        if not isinstance(metrics_paths, dict) or set(metrics_paths) != set(_ROLES):
            raise ValueError(f"all metric roles are required for {family_id}")
        loaded[family_id] = {}
        for role in _ROLES:
            path = _expand_path(metrics_paths[role], corpus_path.parent)
            try:
                raw_bytes = path.read_bytes()
            except OSError as exc:
                raise ValueError(f"metrics file is missing for {family_id}/{role}: {path}") from exc
            digest.update(f"\0{family_id}\0{role}\0".encode("utf-8"))
            digest.update(raw_bytes)
            loaded[family_id][role] = _finite_metrics(
                raw_bytes, f"{family_id}/{role}"
            )

    limits = {}
    diagnostics = {}
    for metric in REQUIRED_METRICS:
        values = [loaded[family["id"]]["roundtrip"][metric] for family in selected]
        median = statistics.median(values)
        mad = statistics.median(abs(value - median) for value in values)
        p99 = _percentile(values, 0.99)
        limit = max(floors[metric], p99 + 3.0 * mad)
        if not math.isfinite(limit):
            raise ValueError(f"non-finite calibrated limit for {metric}")
        limits[metric] = limit
        diagnostics[metric] = {
            "hard_floor": floors[metric],
            "median": median,
            "mad": mad,
            "p99": p99,
        }

    for family in selected:
        family_id = family["id"]
        good = loaded[family_id]["known_good"]
        exceeded_good = [metric for metric in REQUIRED_METRICS if good[metric] > limits[metric]]
        if exceeded_good:
            raise ValueError(
                f"known_good {family_id} exceeds calibrated limits: {', '.join(exceeded_good)}"
            )
        bad = loaded[family_id]["known_bad"]
        if not any(bad[metric] > limits[metric] for metric in REQUIRED_METRICS):
            raise ValueError(f"known_bad {family_id} does not exceed any calibrated limit")

    profile = {
        "schema": 1,
        "version": "maximum-experimental-v1",
        "calibrated": True,
        "corpus_hash": digest.hexdigest(),
        "limits": limits,
        "family_count": len(selected),
        "partition": partition,
        "calibration": diagnostics,
    }
    _atomic_json(Path(out_path), profile)
    return profile


def _parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Calibrate Maximum visual fidelity limits")
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--out", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        profile = calibrate_profile(args.corpus, args.partition, args.out)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(
        f"[OK] Calibrated {profile['family_count']} families: "
        f"{args.out} ({profile['corpus_hash']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
