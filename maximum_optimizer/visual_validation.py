from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from PIL import Image, ImageChops, ImageFilter, UnidentifiedImageError

from maximum_optimizer.domain import GateFailure, ValidationResult


IMAGE_METRICS = ("silhouette_iou", "rgb_mae", "edge_error")
GEOMETRY_METRICS = (
    "surface_bidirectional_p95",
    "surface_max",
    "normal_angle_p95",
    "uv_error_p95",
    "skinning_error_p95",
)
REQUIRED_METRICS = IMAGE_METRICS + GEOMETRY_METRICS


@dataclass(frozen=True)
class FidelityProfile:
    schema: int
    version: str
    calibrated: bool
    corpus_hash: str
    limits: Mapping[str, float]

    def __post_init__(self) -> None:
        if self.schema != 1:
            raise ValueError("unsupported fidelity profile schema")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("fidelity profile version is required")
        if self.calibrated is not True:
            raise ValueError("fidelity profile must be calibrated")
        if not isinstance(self.corpus_hash, str) or not self.corpus_hash:
            raise ValueError("fidelity profile corpus_hash is required")
        if not isinstance(self.limits, Mapping):
            raise ValueError("fidelity profile limits must be an object")
        copied: dict[str, float] = {}
        if set(self.limits) != set(REQUIRED_METRICS):
            raise ValueError("fidelity profile must contain every required metric")
        for metric in REQUIRED_METRICS:
            value = self.limits[metric]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"invalid limit for {metric}")
            number = float(value)
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"invalid limit for {metric}")
            copied[metric] = number
        object.__setattr__(self, "limits", MappingProxyType(copied))


def load_profile(path: Path) -> FidelityProfile:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid fidelity profile: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("fidelity profile must be a JSON object")
    try:
        return FidelityProfile(
            schema=payload.get("schema"),
            version=payload.get("version"),
            calibrated=payload.get("calibrated"),
            corpus_hash=payload.get("corpus_hash"),
            limits=payload.get("limits"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid fidelity profile: {exc}") from exc


def _failure(gate: str, scope: str, message: str) -> GateFailure:
    return GateFailure(gate, scope, None, None, message)


def _load_manifest(root: Path, label: str, failures: list[GateFailure]) -> dict | None:
    path = root / "render_manifest.json"
    if not path.is_file():
        failures.append(_failure("missing_manifest", label, f"{label} render manifest is missing"))
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        failures.append(_failure("corrupt_manifest", label, f"{label} render manifest is corrupt"))
        return None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        failures.append(_failure("invalid_manifest", label, f"{label} render manifest schema is invalid"))
        return None
    if not isinstance(payload.get("entries"), list):
        failures.append(_failure("invalid_manifest", label, f"{label} render entries are invalid"))
        return None
    return payload


def _entry_key(entry: object) -> tuple[str, str, str] | None:
    if not isinstance(entry, dict):
        return None
    values = (entry.get("pass"), entry.get("pose"), entry.get("angle"))
    if not all(isinstance(value, str) and value for value in values):
        return None
    return values


def _index_entries(
    manifest: dict,
    label: str,
    failures: list[GateFailure],
) -> dict[tuple[str, str, str], dict]:
    indexed: dict[tuple[str, str, str], dict] = {}
    for position, entry in enumerate(manifest["entries"]):
        key = _entry_key(entry)
        if key is None:
            failures.append(
                _failure("invalid_entry", f"{label}/{position}", "render entry is invalid")
            )
            continue
        if key in indexed:
            failures.append(
                _failure("duplicate_entry", "/".join(key), "render entry is duplicated")
            )
            continue
        indexed[key] = entry
    return indexed


def _scope(key: tuple[str, str, str]) -> str:
    render_pass, pose, angle = key
    if pose == "bind":
        return f"{render_pass}/{angle}"
    return f"{render_pass}/{pose}/{angle}"


def _safe_image_path(root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _load_verified_image(
    root: Path,
    entry: dict,
    label: str,
    scope: str,
    failures: list[GateFailure],
) -> Image.Image | None:
    path = _safe_image_path(root, entry.get("image"))
    if path is None:
        failures.append(_failure("invalid_image_path", scope, f"{label} image path is invalid"))
        return None
    if not path.is_file():
        failures.append(_failure("missing_image", scope, f"{label} image is missing"))
        return None
    try:
        content = path.read_bytes()
    except OSError:
        failures.append(_failure("missing_image", scope, f"{label} image cannot be read"))
        return None
    expected_hash = entry.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash != expected_hash:
        failures.append(_failure("hash_mismatch", scope, f"{label} image hash does not match"))
        return None
    try:
        with Image.open(path) as source:
            source.load()
            return source.convert("RGBA")
    except (OSError, ValueError, UnidentifiedImageError):
        failures.append(_failure("corrupt_image", scope, f"{label} image is not decodable"))
        return None


def _alpha_mask(image: Image.Image) -> Image.Image:
    return image.getchannel("A").point(lambda value: 255 if value else 0, mode="1")


def _mask_counts(first: Image.Image, second: Image.Image) -> tuple[int, int]:
    intersection = ImageChops.logical_and(first, second)
    union = ImageChops.logical_or(first, second)
    return sum(intersection.get_flattened_data()), sum(union.get_flattened_data())


def _silhouette_error(reference: Image.Image, candidate: Image.Image) -> float:
    intersection, union = _mask_counts(_alpha_mask(reference), _alpha_mask(candidate))
    return 0.0 if union == 0 else 1.0 - intersection / union


def _linear_channel(value: int) -> float:
    normalized = value / 255.0
    if normalized <= 0.04045:
        return normalized / 12.92
    return ((normalized + 0.055) / 1.055) ** 2.4


def _rgb_mae(reference: Image.Image, candidate: Image.Image) -> float:
    ref_mask = _alpha_mask(reference)
    candidate_mask = _alpha_mask(candidate)
    union = ImageChops.logical_or(ref_mask, candidate_mask)
    active = [bool(value) for value in union.get_flattened_data()]
    active_count = sum(active)
    if active_count == 0:
        return 0.0
    total = 0.0
    for enabled, ref_pixel, candidate_pixel in zip(
        active,
        reference.get_flattened_data(),
        candidate.get_flattened_data(),
    ):
        if enabled:
            total += sum(
                abs(_linear_channel(ref_pixel[index]) - _linear_channel(candidate_pixel[index]))
                for index in range(3)
            )
    return total / (active_count * 3)


def _dilated_boundary(mask: Image.Image) -> Image.Image:
    grayscale = mask.convert("L")
    eroded = grayscale.filter(ImageFilter.MinFilter(3))
    boundary = ImageChops.subtract(grayscale, eroded)
    return boundary.filter(ImageFilter.MaxFilter(3)).point(
        lambda value: 255 if value else 0, mode="1"
    )


def _edge_error(reference: Image.Image, candidate: Image.Image) -> float:
    ref_edge = _dilated_boundary(_alpha_mask(reference))
    candidate_edge = _dilated_boundary(_alpha_mask(candidate))
    intersection, union = _mask_counts(ref_edge, candidate_edge)
    return 0.0 if union == 0 else (union - intersection) / union


def _image_metrics(reference: Image.Image, candidate: Image.Image) -> dict[str, float]:
    return {
        "silhouette_iou": _silhouette_error(reference, candidate),
        "rgb_mae": _rgb_mae(reference, candidate),
        "edge_error": _edge_error(reference, candidate),
    }


def _gate(
    metric: str,
    scope: str,
    value: float,
    limit: float,
    failures: list[GateFailure],
) -> None:
    if value > limit:
        failures.append(
            GateFailure(
                metric,
                scope,
                value,
                limit,
                f"{metric} exceeded at {scope}",
            )
        )


def _ratio(value: float, limit: float) -> float:
    if limit == 0:
        return 0.0 if value == 0 else math.inf
    return value / limit


def compare_render_sets(
    reference_dir: Path,
    candidate_dir: Path,
    profile: FidelityProfile,
) -> ValidationResult:
    if not isinstance(profile, FidelityProfile):
        raise TypeError("profile must be a FidelityProfile")
    reference_dir = Path(reference_dir)
    candidate_dir = Path(candidate_dir)
    failures: list[GateFailure] = []
    maxima = {metric: 0.0 for metric in REQUIRED_METRICS}
    observations: list[tuple[float, str]] = []

    reference_manifest = _load_manifest(reference_dir, "reference", failures)
    candidate_manifest = _load_manifest(candidate_dir, "candidate", failures)
    if reference_manifest is not None and candidate_manifest is not None:
        reference_entries = _index_entries(reference_manifest, "reference", failures)
        candidate_entries = _index_entries(candidate_manifest, "candidate", failures)
        reference_keys = set(reference_entries)
        candidate_keys = set(candidate_entries)
        if reference_keys != candidate_keys:
            missing = sorted(reference_keys - candidate_keys)
            extra = sorted(candidate_keys - reference_keys)
            failures.append(
                _failure(
                    "entry_mismatch",
                    "manifest",
                    f"render entry sets differ: missing={missing!r}, extra={extra!r}",
                )
            )

        for label, root, indexed, unmatched in (
            (
                "reference",
                reference_dir,
                reference_entries,
                reference_keys - candidate_keys,
            ),
            (
                "candidate",
                candidate_dir,
                candidate_entries,
                candidate_keys - reference_keys,
            ),
        ):
            for key in sorted(unmatched):
                scope = _scope(key)
                entry = indexed[key]
                if key[0] == "textured" and entry.get("texture_missing") is not False:
                    failures.append(
                        _failure(
                            "texture_missing",
                            scope,
                            f"{label} textured render has missing texture material",
                        )
                    )
                _load_verified_image(root, entry, label, scope, failures)

        for key in sorted(reference_keys & candidate_keys):
            scope = _scope(key)
            reference_entry = reference_entries[key]
            candidate_entry = candidate_entries[key]
            if key[0] == "textured":
                for label, entry in (
                    ("reference", reference_entry),
                    ("candidate", candidate_entry),
                ):
                    if entry.get("texture_missing") is not False:
                        failures.append(
                            _failure(
                                "texture_missing",
                                scope,
                                f"{label} textured render has missing texture material",
                            )
                        )
            reference_image = _load_verified_image(
                reference_dir, reference_entry, "reference", scope, failures
            )
            candidate_image = _load_verified_image(
                candidate_dir, candidate_entry, "candidate", scope, failures
            )
            if reference_image is None or candidate_image is None:
                continue
            if reference_image.size != candidate_image.size:
                failures.append(
                    _failure("image_size", scope, "render image dimensions do not match")
                )
                continue
            for metric, value in _image_metrics(reference_image, candidate_image).items():
                maxima[metric] = max(maxima[metric], value)
                limit = profile.limits[metric]
                _gate(metric, scope, value, limit, failures)
                observations.append((_ratio(value, limit), scope))

        geometry = candidate_manifest.get("geometry")
        if not isinstance(geometry, list):
            failures.append(
                _failure("invalid_geometry", "candidate", "candidate geometry entries are invalid")
            )
        else:
            for position, entry in enumerate(geometry):
                if not isinstance(entry, dict):
                    failures.append(
                        _failure("invalid_geometry", str(position), "geometry entry is invalid")
                    )
                    continue
                region = entry.get("scope")
                pose = entry.get("pose")
                if not isinstance(region, str) or not region or not isinstance(pose, str) or not pose:
                    failures.append(
                        _failure("invalid_geometry", str(position), "geometry scope/pose is invalid")
                    )
                    continue
                scope = f"{region}/{pose}"
                for metric in GEOMETRY_METRICS:
                    value = entry.get(metric)
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                        or float(value) < 0
                    ):
                        failures.append(
                            _failure(
                                "invalid_geometry",
                                scope,
                                f"geometry metric {metric} is invalid",
                            )
                        )
                        continue
                    number = float(value)
                    maxima[metric] = max(maxima[metric], number)
                    limit = profile.limits[metric]
                    _gate(metric, scope, number, limit, failures)
                    observations.append((_ratio(number, limit), scope))

    infrastructure_failure = next(
        (failure for failure in failures if failure.gate not in REQUIRED_METRICS), None
    )
    if infrastructure_failure is not None:
        observations.append((math.inf, infrastructure_failure.scope))
    max_ratio, worst_scope = max(observations, default=(0.0, ""), key=lambda item: item[0])
    metrics = dict(maxima)
    metrics["fidelity_score"] = max(0.0, 1.0 - max_ratio)
    return ValidationResult(
        passed=not failures,
        failures=tuple(failures),
        metrics=metrics,
        worst_scope=worst_scope,
    )
