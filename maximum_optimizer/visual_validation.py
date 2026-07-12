from __future__ import annotations

import hashlib
import json
import math
import re
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
EXPECTED_PASSES = ("textured", "clay")
EXPECTED_ANGLES = ("front", "back", "left", "right", "top", "bottom", "iso1", "iso2")
GEOMETRY_AUDIT_ALGORITHM = {
    "name": "relative-cross-area-squared-v1",
    "relative_area_squared_epsilon": 1e-24,
    "max_filtered_fraction": 0.05,
}


@dataclass(frozen=True)
class FidelityProfile:
    schema: int
    version: str
    calibrated: bool
    corpus_hash: str
    limits: Mapping[str, float]

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1:
            raise ValueError("unsupported fidelity profile schema")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("fidelity profile version is required")
        if self.calibrated is not True:
            raise ValueError("fidelity profile must be calibrated")
        if (
            not isinstance(self.corpus_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.corpus_hash) is None
        ):
            raise ValueError("fidelity profile corpus_hash is required")
        if not isinstance(self.limits, Mapping):
            raise ValueError("fidelity profile limits must be an object")
        copied: dict[str, float] = {}
        if set(self.limits) != set(REQUIRED_METRICS):
            raise ValueError("fidelity profile must contain every required metric")
        for metric in REQUIRED_METRICS:
            value = self.limits[metric]
            number = _finite_nonnegative(value)
            if number is None:
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
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema")) is not int
        or payload.get("schema") != 1
    ):
        failures.append(_failure("invalid_manifest", label, f"{label} render manifest schema is invalid"))
        return None
    if not isinstance(payload.get("entries"), list):
        failures.append(_failure("invalid_manifest", label, f"{label} render entries are invalid"))
        return None
    return payload


def _validate_expected(
    manifest: dict, label: str, failures: list[GateFailure]
) -> dict[str, object] | None:
    raw = manifest.get("expected")
    if not isinstance(raw, dict):
        failures.append(_failure("invalid_expected", label, "expected render matrix is missing"))
        return None
    result: dict[str, object] = {}
    for field in ("passes", "angles", "poses", "regions"):
        values = raw.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
            or len(set(values)) != len(values)
        ):
            failures.append(
                _failure("invalid_expected", f"{label}/{field}", f"expected {field} is invalid")
            )
            return None
        result[field] = tuple(values)
    if result["passes"] != EXPECTED_PASSES or result["angles"] != EXPECTED_ANGLES:
        failures.append(
            _failure(
                "invalid_expected",
                label,
                "expected passes/angles do not match the mandatory Maximum matrix",
            )
        )
        return None
    if "bind" not in result["poses"]:
        failures.append(_failure("invalid_expected", label, "expected poses must include bind"))
        return None
    pose_frames = raw.get("pose_frames")
    if (
        not isinstance(pose_frames, dict)
        or set(pose_frames) != set(result["poses"])
        or any(
            type(frame) is not int or frame < 0
            for frame in pose_frames.values()
        )
    ):
        failures.append(
            _failure(
                "invalid_expected",
                f"{label}/pose_frames",
                "pose_frames must map every declared pose to a non-negative integer frame",
            )
        )
        return None
    result["pose_frames"] = {
        pose: pose_frames[pose] for pose in result["poses"]
    }
    return result


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


def _validate_entry_matrix(
    indexed: dict[tuple[str, str, str], dict],
    expected: dict[str, object],
    label: str,
    failures: list[GateFailure],
) -> None:
    required = {
        (render_pass, pose, angle)
        for render_pass in expected["passes"]
        for pose in expected["poses"]
        for angle in expected["angles"]
    }
    actual = set(indexed)
    if actual != required:
        failures.append(
            _failure(
                "entry_matrix",
                label,
                f"render matrix differs: missing={sorted(required - actual)!r}, extra={sorted(actual - required)!r}",
            )
        )


def _finite_nonnegative(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _validate_geometry(
    manifest: dict,
    expected: dict[str, object],
    label: str,
    failures: list[GateFailure],
) -> dict[tuple[str, str], dict]:
    raw = manifest.get("geometry")
    if not isinstance(raw, list):
        failures.append(_failure("invalid_geometry", label, "geometry entries are invalid"))
        return {}
    indexed: dict[tuple[str, str], dict] = {}
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict):
            failures.append(
                _failure("invalid_geometry", f"{label}/{position}", "geometry entry is invalid")
            )
            continue
        region, pose = entry.get("scope"), entry.get("pose")
        if not isinstance(region, str) or not region or not isinstance(pose, str) or not pose:
            failures.append(
                _failure("invalid_geometry", f"{label}/{position}", "geometry scope/pose is invalid")
            )
            continue
        key = (region, pose)
        if key in indexed:
            failures.append(
                _failure("duplicate_geometry", f"{region}/{pose}", "geometry entry is duplicated")
            )
            continue
        indexed[key] = entry
        region_missing = entry.get("region_missing")
        if type(region_missing) is not bool:
            failures.append(
                _failure(
                    "invalid_geometry",
                    f"{region}/{pose}",
                    "region_missing must be an explicit boolean",
                )
            )
        elif region_missing:
            failures.append(
                _failure("region_missing", f"{region}/{pose}", f"{label} geometry region is missing")
            )
        for metric in GEOMETRY_METRICS:
            if _finite_nonnegative(entry.get(metric)) is None:
                failures.append(
                    _failure(
                        "invalid_geometry",
                        f"{region}/{pose}",
                        f"geometry metric {metric} is invalid",
                    )
                )
    required = {
        (region, pose)
        for region in expected["regions"]
        for pose in expected["poses"]
    }
    if set(indexed) != required:
        failures.append(
            _failure(
                "geometry_matrix",
                label,
                f"geometry matrix differs: missing={sorted(required - set(indexed))!r}, extra={sorted(set(indexed) - required)!r}",
            )
        )
    return indexed


def _validate_configuration(
    manifest: dict, label: str, failures: list[GateFailure]
) -> dict | None:
    payload = manifest.get("configuration")
    if type(payload) is not dict or set(payload) != {"schema", "name", "bodygroups", "lod_index", "source_pairs"}:
        failures.append(_failure("invalid_configuration", label, "render configuration is invalid"))
        return None
    bodygroups = payload.get("bodygroups")
    pairs = payload.get("source_pairs")
    if (
        payload.get("schema") != 1
        or type(payload.get("name")) is not str
        or not payload["name"]
        or type(payload.get("lod_index")) is not int
        or payload["lod_index"] < 0
        or type(bodygroups) is not dict
        or any(
            type(name) is not str or not name or type(index) is not int or index < 0
            for name, index in bodygroups.items()
        )
        or type(pairs) is not list
        or not pairs
        or any(
            type(pair) is not dict
            or set(pair) != {"source_identity", "reference_sha256", "candidate_sha256"}
            or type(pair["source_identity"]) is not str
            or not pair["source_identity"]
            or any(
                type(pair.get(field)) is not str
                or re.fullmatch(r"[0-9a-f]{64}", pair[field]) is None
                for field in ("reference_sha256", "candidate_sha256")
            )
            for pair in pairs
        )
    ):
        failures.append(_failure("invalid_configuration", label, "render configuration values are invalid"))
        return None
    return payload


def _validate_geometry_audit(
    manifest: dict, expected: dict[str, object], label: str, failures: list[GateFailure]
) -> None:
    if manifest.get("geometry_audit_algorithm") != GEOMETRY_AUDIT_ALGORITHM:
        failures.append(_failure("invalid_geometry_audit", label, "geometry audit algorithm is invalid"))
        return
    payload = manifest.get("geometry_audit")
    required = {
        f"{region}/{pose}"
        for region in expected["regions"]
        for pose in expected["poses"]
    }
    if type(payload) is not dict or set(payload) != required:
        failures.append(_failure("invalid_geometry_audit", label, "geometry audit matrix is invalid"))
        return
    for scope, audit in payload.items():
        valid = type(audit) is dict and set(audit) == {
            "input_triangles", "kept_triangles", "filtered_degenerate_triangles",
            "filtered_indices_sha256",
        }
        if valid:
            counts = tuple(audit[field] for field in (
                "input_triangles", "kept_triangles", "filtered_degenerate_triangles"
            ))
            valid = (
                all(type(value) is int and value >= 0 for value in counts)
                and counts[1] + counts[2] == counts[0]
                and counts[1] > 0
                and counts[2] / counts[0] <= GEOMETRY_AUDIT_ALGORITHM["max_filtered_fraction"]
                and type(audit["filtered_indices_sha256"]) is str
                and re.fullmatch(r"[0-9a-f]{64}", audit["filtered_indices_sha256"]) is not None
            )
        if not valid:
            failures.append(_failure("invalid_geometry_audit", f"{label}/{scope}", "geometry audit entry is invalid"))


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
    # Edge extraction intentionally thresholds coverage at 0.5 (128/255).
    return image.getchannel("A").point(lambda value: 255 if value >= 128 else 0, mode="1")


def _mask_counts(first: Image.Image, second: Image.Image) -> tuple[int, int]:
    intersection = ImageChops.logical_and(first, second)
    union = ImageChops.logical_or(first, second)
    return sum(intersection.get_flattened_data()), sum(union.get_flattened_data())


def _silhouette_error(reference: Image.Image, candidate: Image.Image) -> float:
    reference_alpha = reference.getchannel("A").get_flattened_data()
    candidate_alpha = candidate.getchannel("A").get_flattened_data()
    intersection = sum(min(first, second) for first, second in zip(reference_alpha, candidate_alpha))
    union = sum(max(first, second) for first, second in zip(reference_alpha, candidate_alpha))
    return 0.0 if union == 0 else 1.0 - intersection / union


def _linear_channel(value: int) -> float:
    normalized = value / 255.0
    if normalized <= 0.04045:
        return normalized / 12.92
    return ((normalized + 0.055) / 1.055) ** 2.4


def _rgb_mae(reference: Image.Image, candidate: Image.Image) -> float:
    reference_pixels = reference.get_flattened_data()
    candidate_pixels = candidate.get_flattened_data()
    union_weights = [
        max(ref_pixel[3], candidate_pixel[3]) / 255.0
        for ref_pixel, candidate_pixel in zip(reference_pixels, candidate_pixels)
    ]
    total_weight = sum(union_weights)
    if total_weight == 0:
        return 0.0
    total = 0.0
    for weight, ref_pixel, candidate_pixel in zip(
        union_weights,
        reference_pixels,
        candidate_pixels,
    ):
        if weight:
            total += weight * sum(
                abs(_linear_channel(ref_pixel[index]) - _linear_channel(candidate_pixel[index]))
                for index in range(3)
            )
    return total / (total_weight * 3)


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
        reference_configuration = _validate_configuration(reference_manifest, "reference", failures)
        candidate_configuration = _validate_configuration(candidate_manifest, "candidate", failures)
        if (
            reference_configuration is not None
            and candidate_configuration is not None
            and reference_configuration != candidate_configuration
        ):
            failures.append(_failure(
                "configuration_mismatch", "manifest",
                "reference and candidate bodygroup/LOD configurations differ",
            ))
        reference_expected = _validate_expected(reference_manifest, "reference", failures)
        candidate_expected = _validate_expected(candidate_manifest, "candidate", failures)
        if (
            reference_expected is not None
            and candidate_expected is not None
            and reference_expected != candidate_expected
        ):
            failures.append(
                _failure(
                    "expected_mismatch",
                    "manifest",
                    "reference and candidate expected matrices differ",
                )
            )
        reference_entries = _index_entries(reference_manifest, "reference", failures)
        candidate_entries = _index_entries(candidate_manifest, "candidate", failures)
        if reference_expected is not None:
            _validate_entry_matrix(reference_entries, reference_expected, "reference", failures)
            _validate_geometry(reference_manifest, reference_expected, "reference", failures)
            _validate_geometry_audit(reference_manifest, reference_expected, "reference", failures)
        candidate_geometry = {}
        if candidate_expected is not None:
            _validate_entry_matrix(candidate_entries, candidate_expected, "candidate", failures)
            candidate_geometry = _validate_geometry(
                candidate_manifest, candidate_expected, "candidate", failures
            )
            _validate_geometry_audit(candidate_manifest, candidate_expected, "candidate", failures)
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

        for (region, pose), entry in sorted(candidate_geometry.items()):
            scope = f"{region}/{pose}"
            for metric in GEOMETRY_METRICS:
                number = _finite_nonnegative(entry.get(metric))
                if number is not None:
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
