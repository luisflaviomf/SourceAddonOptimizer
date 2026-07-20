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

from maximum_optimizer.domain import GateFailure, ValidationResult, require_canonical_relative


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
    "surface_correspondence": "material-bone-multinormal-near-coincident-surface-v6",
    "uv_distance": "periodic-unit-torus-v1",
    "skinning_correspondence": (
        "dominant-bone-partitioned-stable-topology-v2"
    ),
}
SUPPORTED_MATERIAL_SHADERS = frozenset({
    "vertexlitgeneric", "lightmappedgeneric", "unlitgeneric", "refract",
})
MATERIAL_RESOLUTION_RULE = "materials-root-order-then-qc-search-order-v1"
MATERIAL_EVIDENCE_FIELDS = frozenset({
    "material_identity", "resolution_rule", "root_index", "search_path_index",
    "vtf_root_index", "vmt_sha256", "vtf_sha256", "shader",
    "texture_directive", "uses_texture_alpha",
    "duplicate_root_directives",
})


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


def _material_availability(entry: dict) -> tuple[bool, tuple[str, ...]] | None:
    texture_missing = entry.get("texture_missing")
    missing_materials = entry.get("missing_materials")
    if (
        type(texture_missing) is not bool
        or type(missing_materials) is not list
        or any(type(value) is not str or not value for value in missing_materials)
        or len(set(missing_materials)) != len(missing_materials)
        or texture_missing != bool(missing_materials)
    ):
        return None
    return texture_missing, tuple(missing_materials)


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


def _validate_resolved_materials(
    indexed: dict[tuple[str, str, str], dict],
    label: str,
    failures: list[GateFailure],
) -> None:
    consistent_evidence: dict[str, dict] = {}
    for key, entry in indexed.items():
        scope = _scope(key)
        availability = _material_availability(entry)
        if availability is None or (key[0] != "textured" and availability[0]):
            failures.append(_failure(
                "invalid_material_evidence", f"{label}/{scope}",
                "texture availability evidence is invalid",
            ))
        raw = entry.get("resolved_materials")
        if type(raw) is not list:
            failures.append(_failure(
                "invalid_material_evidence", f"{label}/{scope}",
                "resolved_materials must be an explicit list",
            ))
            continue
        identities: set[str] = set()
        for position, evidence in enumerate(raw):
            evidence_scope = f"{label}/{scope}/{position}"
            valid = type(evidence) is dict and set(evidence) == MATERIAL_EVIDENCE_FIELDS
            if valid:
                shader = evidence["shader"]
                directive = evidence["texture_directive"]
                expected_directive = (
                    "$refracttinttexture" if shader == "refract" else "$basetexture"
                )
                identity = evidence["material_identity"]
                duplicate_audit = evidence["duplicate_root_directives"]
                duplicate_valid = (
                    type(duplicate_audit) is list
                    and all(
                        type(item) is dict
                        and set(item) == {"directive", "ignored_values"}
                        and type(item["directive"]) is str
                        and bool(item["directive"])
                        and item["directive"] == item["directive"].casefold()
                        and type(item["ignored_values"]) is list
                        and bool(item["ignored_values"])
                        and all(type(value) is str for value in item["ignored_values"])
                        for item in duplicate_audit
                    )
                    and [item["directive"] for item in duplicate_audit]
                    == sorted(
                        {item["directive"] for item in duplicate_audit},
                        key=str.casefold,
                    )
                )
                valid = (
                    type(identity) is str and bool(identity)
                    and identity not in identities
                    and evidence["resolution_rule"] == MATERIAL_RESOLUTION_RULE
                    and all(
                        type(evidence[field]) is int and evidence[field] >= 0
                        for field in ("root_index", "search_path_index", "vtf_root_index")
                    )
                    and all(
                        type(evidence[field]) is str
                        and re.fullmatch(r"[0-9a-f]{64}", evidence[field]) is not None
                        for field in ("vmt_sha256", "vtf_sha256")
                    )
                    and shader in SUPPORTED_MATERIAL_SHADERS
                    and directive == expected_directive
                    and type(evidence["uses_texture_alpha"]) is bool
                    and (shader != "refract" or evidence["uses_texture_alpha"] is True)
                    and duplicate_valid
                )
            if not valid:
                failures.append(_failure(
                    "invalid_material_evidence", evidence_scope,
                    "resolved material evidence is invalid",
                ))
            else:
                identities.add(evidence["material_identity"])
                previous = consistent_evidence.setdefault(
                    evidence["material_identity"], evidence
                )
                if previous != evidence:
                    failures.append(_failure(
                        "material_evidence_inconsistent", evidence_scope,
                        "resolved material evidence changes across render entries",
                    ))


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


@dataclass(frozen=True)
class SourceUnionComparisonContract:
    target_sha256: str
    source_identity: str
    source_coverage_sha256: str
    reference_source_sha256: str
    candidate_source_sha256: str
    material_contract_sha256: str
    pose_frames: tuple[tuple[str, int], ...]
    union_key: str
    contract_sha256: str

    def __post_init__(self) -> None:
        hashes = (
            self.target_sha256, self.source_coverage_sha256,
            self.reference_source_sha256, self.candidate_source_sha256,
            self.material_contract_sha256,
        )
        if any(re.fullmatch(r"[0-9a-f]{64}", value or "") is None for value in hashes):
            raise ValueError("source-union comparison hash is invalid")
        if type(self.source_identity) is not str or not self.source_identity:
            raise ValueError("source-union comparison source is invalid")
        require_canonical_relative(self.source_identity, "source-union comparison source")
        poses = tuple(tuple(item) for item in self.pose_frames)
        if poses != (("bind", 0),) and not (
            len(poses) == 2 and poses[0] == ("bind", 0)
            and poses[1][0] == "animation"
            and type(poses[1][1]) is int and poses[1][1] > 0
        ):
            raise ValueError("source-union comparison pose contract is invalid")
        if not re.fullmatch(r"source-union-[0-9a-f]{32}", self.union_key or ""):
            raise ValueError("source-union comparison union key is invalid")
        if self.union_key != "source-union-" + self.source_coverage_sha256[:32]:
            raise ValueError("source-union comparison key differs from source coverage")
        expected = hashlib.sha256(json.dumps({
            "target_sha256": self.target_sha256,
            "source_identity": self.source_identity,
            "source_coverage_sha256": self.source_coverage_sha256,
            "reference_source_sha256": self.reference_source_sha256,
            "candidate_source_sha256": self.candidate_source_sha256,
            "material_contract_sha256": self.material_contract_sha256,
            "pose_frames": [list(item) for item in poses],
            "union_key": self.union_key,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if self.contract_sha256 != expected:
            raise ValueError("source-union comparison contract seal mismatch")
        object.__setattr__(self, "pose_frames", poses)

    @classmethod
    def create(cls, **values) -> "SourceUnionComparisonContract":
        provisional = cls.__new__(cls)
        for key, value in {**values, "contract_sha256": "0" * 64}.items():
            object.__setattr__(provisional, key, value)
        poses = tuple(tuple(item) for item in values["pose_frames"])
        digest = hashlib.sha256(json.dumps({
            **{key: values[key] for key in (
                "target_sha256", "source_identity", "source_coverage_sha256",
                "reference_source_sha256", "candidate_source_sha256",
                "material_contract_sha256", "union_key",
            )},
            "pose_frames": [list(item) for item in poses],
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return cls(**values, contract_sha256=digest)


def _load_source_union_manifest(
    root: Path, label: str, contract: SourceUnionComparisonContract,
    failures: list[GateFailure],
) -> dict | None:
    manifest = _load_manifest(root, label, failures)
    fields = {
        "schema", "kind", "side", "contract_sha256", "target_sha256",
        "source_identity", "source_coverage_sha256", "source_sha256",
        "material_contract_sha256", "expected", "entries", "geometry",
        "geometry_audit", "geometry_audit_algorithm",
    }
    if manifest is None:
        return None
    expected_source = (
        contract.reference_source_sha256 if label == "reference"
        else contract.candidate_source_sha256
    )
    if set(manifest) != fields or any((
        manifest.get("kind") != "adaptive-direct-source-union-render-v1",
        manifest.get("side") != label,
        manifest.get("contract_sha256") != contract.contract_sha256,
        manifest.get("target_sha256") != contract.target_sha256,
        manifest.get("source_identity") != contract.source_identity,
        manifest.get("source_coverage_sha256") != contract.source_coverage_sha256,
        manifest.get("source_sha256") != expected_source,
        manifest.get("material_contract_sha256") != contract.material_contract_sha256,
    )):
        failures.append(_failure("invalid_manifest", label, "source-union manifest binding is invalid"))
        return None
    entry_fields = {
        "pass", "pose", "angle", "image", "sha256", "texture_missing",
        "missing_materials", "resolved_materials",
    }
    for entry in manifest["entries"]:
        if (
            type(entry) is not dict or set(entry) != entry_fields
            or _material_availability(entry) is None
            or type(entry.get("resolved_materials")) is not list
        ):
            failures.append(_failure("invalid_manifest", label, "source-union entry schema is invalid"))
            return None
    geometry_fields = {"scope", "pose", "region_missing", *GEOMETRY_METRICS}
    geometry = manifest.get("geometry")
    if type(geometry) is not list or any(
        type(item) is not dict or set(item) != geometry_fields for item in geometry
    ):
        failures.append(_failure("invalid_manifest", label, "source-union geometry schema is invalid"))
        return None
    expected = manifest.get("expected")
    if type(expected) is not dict or set(expected) != {
        "passes", "angles", "poses", "pose_frames", "regions",
    }:
        failures.append(_failure("invalid_manifest", label, "source-union expected schema is invalid"))
        return None
    return manifest


def compare_source_union_render_sets(
    reference_dir: Path,
    candidate_dir: Path,
    profile: FidelityProfile,
    *,
    expected_contract: SourceUnionComparisonContract,
) -> ValidationResult:
    if not isinstance(profile, FidelityProfile) or not isinstance(
        expected_contract, SourceUnionComparisonContract
    ):
        raise TypeError("source-union comparator inputs are invalid")
    reference_dir = Path(reference_dir); candidate_dir = Path(candidate_dir)
    failures: list[GateFailure] = []
    maxima = {metric: 0.0 for metric in REQUIRED_METRICS}
    observations: list[tuple[float, str]] = []
    reference = _load_source_union_manifest(
        reference_dir, "reference", expected_contract, failures
    )
    candidate = _load_source_union_manifest(
        candidate_dir, "candidate", expected_contract, failures
    )
    required_expected = {
        "passes": list(EXPECTED_PASSES), "angles": list(EXPECTED_ANGLES),
        "poses": [item[0] for item in expected_contract.pose_frames],
        "pose_frames": dict(expected_contract.pose_frames),
        "regions": [expected_contract.union_key],
    }
    for label, manifest in (("reference", reference), ("candidate", candidate)):
        if manifest is None:
            continue
        if manifest.get("expected") != required_expected:
            failures.append(_failure("expected_mismatch", label, "source-union expected contract differs"))
        for entry in manifest.get("entries", ()):
            key = _entry_key(entry)
            if key is not None and entry.get("image") != f"{key[0]}/{key[1]}/{key[2]}.png":
                failures.append(_failure("invalid_entry", f"{label}/{_scope(key)}", "source-union image path is not canonical"))
    return _compare_bound_render_manifests(
        reference_dir, candidate_dir, profile, reference, candidate, failures,
        require_legacy_configuration=False,
    )
def _compare_bound_render_manifests(
    reference_dir: Path,
    candidate_dir: Path,
    profile: FidelityProfile,
    reference_manifest: dict | None,
    candidate_manifest: dict | None,
    failures: list[GateFailure],
    *,
    require_legacy_configuration: bool,
) -> ValidationResult:
    if not isinstance(profile, FidelityProfile):
        raise TypeError("profile must be a FidelityProfile")
    reference_dir = Path(reference_dir)
    candidate_dir = Path(candidate_dir)
    maxima = {metric: 0.0 for metric in REQUIRED_METRICS}
    observations: list[tuple[float, str]] = []
    if reference_manifest is not None and candidate_manifest is not None:
        if require_legacy_configuration:
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
        _validate_resolved_materials(reference_entries, "reference", failures)
        _validate_resolved_materials(candidate_entries, "candidate", failures)
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
                if reference_entry.get("resolved_materials") != candidate_entry.get("resolved_materials"):
                    failures.append(_failure(
                        "material_evidence_mismatch", scope,
                        "reference and candidate material evidence differ",
                    ))
                reference_availability = _material_availability(reference_entry)
                candidate_availability = _material_availability(candidate_entry)
                if reference_availability != candidate_availability:
                    failures.append(_failure(
                        "texture_missing", scope,
                        "reference and candidate unresolved material sets differ",
                    ))
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


def compare_render_sets(
    reference_dir: Path,
    candidate_dir: Path,
    profile: FidelityProfile,
) -> ValidationResult:
    if not isinstance(profile, FidelityProfile):
        raise TypeError("profile must be a FidelityProfile")
    reference_dir = Path(reference_dir); candidate_dir = Path(candidate_dir)
    failures: list[GateFailure] = []
    reference = _load_manifest(reference_dir, "reference", failures)
    candidate = _load_manifest(candidate_dir, "candidate", failures)
    return _compare_bound_render_manifests(
        reference_dir, candidate_dir, profile, reference, candidate, failures,
        require_legacy_configuration=True,
    )
