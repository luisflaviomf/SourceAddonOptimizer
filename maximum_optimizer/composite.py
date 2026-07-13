from __future__ import annotations

import hashlib
import os
import stat
import threading
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, TypeAlias

from .domain import (
    AdaptiveCandidateMetricsProof,
    AdaptiveDirectCoverageManifest,
    AdaptiveDirectCoverageOccurrenceProof,
    AdaptiveDirectCoverageSourceProof,
    AdaptiveDirectStateInventory,
    AdaptiveDirectStateInventoryRow,
    CandidateEvaluation,
    ChangedSourceProof,
    CandidateSpec,
    CompositeRecipe,
    ComposedSourceTree,
    CompositionProof,
    DirectPrefilterProof,
    DirectSourceBuildRequest,
    DirectSourceSnapshot,
    FocusedEvidenceRef,
    GateFailure,
    RecoverySourceSnapshot,
    SourceFileProof,
    SourceOverlay,
    SourceTreeManifest,
    ValidationResult,
    composite_recipe_payload,
    changed_source_proof_payload,
    composition_proof_payload,
    adaptive_candidate_metrics_payload,
    adaptive_direct_coverage_manifest_payload,
    adaptive_direct_state_inventory_payload,
    adaptive_direct_state_inventory_row_payload,
    direct_prefilter_payload,
    direct_candidate_id,
    direct_cache_digest,
    direct_source_request_payload,
    direct_source_snapshot_from_payload,
    direct_source_snapshot_payload,
    validation_result_payload,
    require_canonical_relative,
    source_overlay_payload,
    source_tree_manifest_payload,
)
from .processes import ProcessCancelledError
from .focused_cache import (
    RenderFileProof,
    _copy_file_no_follow,
    _file_proof,
    _read_regular_no_follow,
    _has_reparse_ancestor,
    _is_reparse as _focused_is_reparse,
)
from .qc_graph import QcGraph, parse_qc_graph
from .reporting import canonical_json
from .smd_contract import direct_smd_material_counts, prefilter_direct_degenerate_smd


_MAX_SOURCE_FILES = 4096
_MAX_SOURCE_BYTES = 2 * 1024 ** 3
_CHUNK = 1024 * 1024
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_ZERO_HASH = "0" * 64

CompositionSnapshot: TypeAlias = RecoverySourceSnapshot | DirectSourceSnapshot


def _pure_seal(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _unsealed(cls, **values):
    result = object.__new__(cls)
    for name, value in values.items(): object.__setattr__(result, name, value)
    return result


def build_adaptive_candidate_metrics_proof(**values) -> AdaptiveCandidateMetricsProof:
    sources = tuple(values.pop("sources"))
    raw = dict(
        schema=1, strategy="blender-adaptive-v1", sources=sources,
        evidence_sha256=_ZERO_HASH, **values,
    )
    provisional = _unsealed(AdaptiveCandidateMetricsProof, **raw); raw["evidence_sha256"] = _pure_seal(adaptive_candidate_metrics_payload(provisional, include_seal=False)); return AdaptiveCandidateMetricsProof(**raw)


def build_adaptive_direct_coverage_manifest(**values) -> AdaptiveDirectCoverageManifest:
    metrics = values.pop("metrics_proof")
    inventory = values.pop("state_inventory")
    if not isinstance(metrics, AdaptiveCandidateMetricsProof) or not isinstance(inventory, AdaptiveDirectStateInventory):
        raise TypeError("coverage requires typed metrics and state inventory")
    bindings = ("family_id", "family_input_sha256", "base_spec_sha256")
    if any(getattr(metrics, name) != getattr(inventory, name) for name in bindings): raise ValueError("coverage metrics/inventory binding mismatch")
    if metrics.candidate_id != inventory.base_candidate_id or metrics.candidate_cache_digest != inventory.base_cache_digest or metrics.source_manifest_sha256 != inventory.base_source_manifest_sha256 or metrics.source_snapshot_sha256 != inventory.base_source_snapshot_sha256: raise ValueError("coverage base metrics/inventory mismatch")
    for name in ("family_id", "family_input_sha256", "base_candidate_id", "base_spec_sha256", "base_cache_digest", "base_source_manifest_sha256", "base_source_snapshot_sha256"):
        if values.get(name) != getattr(inventory, name): raise ValueError("coverage caller binding differs from typed inventory")
    identities = inventory.complete_source_identities
    if tuple(item.source_identity for item in metrics.sources) != identities: raise ValueError("coverage metrics/inventory source union mismatch")
    rows_by_source = {identity: [] for identity in identities}
    for row in inventory.rows: rows_by_source[row.source_identity].append(row)
    sources = []
    for metric in metrics.sources:
        rows = tuple(rows_by_source[metric.source_identity])
        metric_occurrences = tuple((item.graph_relative_path, item.directive, item.line, item.logical_path) for item in metric.occurrences)
        row_occurrences = tuple((item.graph_relative_path, item.directive, item.line, item.source_identity) for item in rows)
        if metric_occurrences != row_occurrences: raise ValueError("coverage state inventory differs from complete metric occurrences")
        if any(row.source_size != metric.source_size or row.source_sha256 != metric.source_sha256 for row in rows): raise ValueError("coverage state inventory source bytes mismatch")
        witnesses = tuple(AdaptiveDirectCoverageOccurrenceProof.create(
            occurrence_key=row.occurrence_key, source_identity=row.source_identity,
            graph_relative_path=row.graph_relative_path, directive=row.directive, line=row.line,
            state_key=row.state_key, bodygroup_key=row.bodygroup_key, lod_key=row.lod_key,
            skin_key=row.skin_key, source_size=row.source_size, source_sha256=row.source_sha256,
            component_manifest_sha256=row.component_manifest_sha256,
            material_contract_sha256=row.material_contract_sha256,
            skeleton_contract_sha256=row.skeleton_contract_sha256,
            pose_contract_sha256=row.pose_contract_sha256,
            equivalence_class_sha256=row.equivalence_class_sha256,
        ) for row in rows)
        def one(field):
            result = {getattr(row, field) for row in rows}
            if len(result) != 1: raise ValueError("coverage inventory dependency splits source equivalence")
            return next(iter(result))
        components = tuple(sorted({key for row in rows for key in row.component_keys}, key=lambda key: (key.casefold(), key)))
        materials = tuple(sorted({key for row in rows for key in row.material_region_keys}, key=lambda key: (key.casefold(), key)))
        poses = one("pose_keys")
        sources.append(AdaptiveDirectCoverageSourceProof.create(
            source_identity=metric.source_identity, eligibility_kind=metric.kind,
            source_size=metric.source_size, source_sha256=metric.source_sha256,
            occurrence_keys=tuple(item.occurrence_key for item in witnesses),
            state_keys=tuple(sorted({item.state_key for item in rows}, key=lambda key: (key.casefold(), key))),
            component_keys=components, material_region_keys=materials,
            skeleton_contract_sha256=one("skeleton_contract_sha256"), pose_keys=poses,
            equivalence_class_sha256=one("equivalence_class_sha256"),
            metrics_sha256=metric.metrics_sha256,
            state_inventory_sha256=inventory.state_inventory_sha256, witnesses=witnesses,
        ))
    sources = tuple(sources)
    eligible = tuple(item for item in sources if item.eligibility_kind == "eligible-exact-v1")
    raw = dict(
        schema=1, complete_source_identities=identities, sources=sources,
        metrics_evidence_sha256=metrics.evidence_sha256,
        state_inventory_sha256=inventory.state_inventory_sha256,
        metrics_proof=metrics, state_inventory=inventory,
        occurrence_count=sum(len(item.witnesses) for item in sources),
        component_count=sum(len(item.component_keys) for item in sources),
        state_count=sum(len(item.state_keys) for item in sources),
        maximum_candidate_images=sum(32 * len(item.pose_keys) for item in eligible),
        coverage_manifest_sha256=_ZERO_HASH, **values,
    )
    provisional = _unsealed(AdaptiveDirectCoverageManifest, **raw); raw["coverage_manifest_sha256"] = _pure_seal(adaptive_direct_coverage_manifest_payload(provisional, include_seal=False)); return AdaptiveDirectCoverageManifest(**raw)


def build_adaptive_direct_state_inventory_row(**values) -> AdaptiveDirectStateInventoryRow:
    raw = dict(row_sha256=_ZERO_HASH, **values)
    provisional = _unsealed(AdaptiveDirectStateInventoryRow, **raw); raw["row_sha256"] = _pure_seal(adaptive_direct_state_inventory_row_payload(provisional, include_seal=False)); return AdaptiveDirectStateInventoryRow(**raw)


def build_adaptive_direct_state_inventory(*, rows, complete_source_identities, **values) -> AdaptiveDirectStateInventory:
    raw = dict(schema=1, rows=tuple(rows), complete_source_identities=tuple(complete_source_identities), state_inventory_sha256=_ZERO_HASH, **values)
    provisional = _unsealed(AdaptiveDirectStateInventory, **raw); raw["state_inventory_sha256"] = _pure_seal(adaptive_direct_state_inventory_payload(provisional, include_seal=False)); return AdaptiveDirectStateInventory(**raw)


def build_direct_prefilter_proof(*, source_triangle_count: int, triangles: tuple) -> DirectPrefilterProof:
    triangles = tuple(triangles)
    raw = dict(
        schema=1, strategy="direct-degenerate-prefilter-v1", cross_squared_threshold=1e-30,
        source_triangle_count=source_triangle_count, dropped_count=len(triangles),
        dropped_fraction=len(triangles) / source_triangle_count, triangles=triangles,
        applied=True, evidence_sha256=_ZERO_HASH,
    )
    provisional = _unsealed(DirectPrefilterProof, **raw); raw["evidence_sha256"] = _pure_seal(direct_prefilter_payload(provisional, include_seal=False)); return DirectPrefilterProof(**raw)


def build_direct_source_request(**values) -> DirectSourceBuildRequest:
    raw = dict(
        schema=1, base_strategy="blender-adaptive-v1",
        strategy="meshopt-direct-position-v1", transfer="direct-position-v1",
        prefilter_version="direct-degenerate-prefilter-v1", request_sha256=_ZERO_HASH,
        **values,
    )
    provisional = _unsealed(DirectSourceBuildRequest, **raw); raw["request_sha256"] = _pure_seal(direct_source_request_payload(provisional, include_seal=False)); return DirectSourceBuildRequest(**raw)


def build_direct_source_snapshot(
    *, cancel_event: threading.Event | None = None, **values
) -> DirectSourceSnapshot:
    request = values["request"]
    raw = dict(
        schema=1, fallback_reason=None, preserved_exact=False,
        reason="approved-direct-position-v1", snapshot_sha256=_ZERO_HASH,
        direct_candidate_id=direct_candidate_id(request),
        direct_cache_digest=direct_cache_digest(request), **values,
    )
    provisional = _unsealed(DirectSourceSnapshot, **raw)
    raw["snapshot_sha256"] = _pure_seal(
        direct_source_snapshot_payload(provisional, include_seal=False)
    )
    return revalidate_direct_source_snapshot(
        DirectSourceSnapshot(**raw), cancel_event
    )


def adaptive_direct_recipe(*, overlays, **values) -> CompositeRecipe:
    raw = dict(
        schema=1, kind="adaptive-direct-fallback-v1", round_index=0,
        overlays=tuple(overlays), selector_version="monaco-terminal-v1",
        prefilter_version="direct-degenerate-prefilter-v1",
        base_strategy="blender-adaptive-v1",
        direct_strategy="meshopt-direct-position-v1",
        direct_transfer="direct-position-v1", recipe_sha256=_ZERO_HASH, **values,
    )
    provisional = _unsealed(CompositeRecipe, **raw)
    raw["recipe_sha256"] = _pure_seal(composite_recipe_payload(provisional, include_seal=False))
    return CompositeRecipe(**raw)


@dataclass(frozen=True)
class AdaptiveDirectVisibilityProof:
    component_key: str
    pose_key: str
    camera_key: str
    reference_visible_mask_pixels: int
    candidate_visible_mask_pixels: int
    preceding_camera_mask_pixels: tuple[tuple[str, int, int], ...]
    evidence_sha256: str

    def __post_init__(self) -> None:
        if any(type(item) is not str or not item for item in (self.component_key, self.pose_key, self.camera_key)):
            raise ValueError("source-union visibility identity is invalid")
        if any(type(item) is not int or item < 1 for item in (self.reference_visible_mask_pixels, self.candidate_visible_mask_pixels)):
            raise ValueError("source-union visibility pixels are invalid")
        cameras = tuple(f"camera-{index:02d}" for index in range(8))
        if self.camera_key not in cameras: raise ValueError("source-union visibility camera is invalid")
        preceding = tuple(tuple(item) for item in self.preceding_camera_mask_pixels)
        selected_index = cameras.index(self.camera_key)
        if len(preceding) != selected_index or tuple(item[0] for item in preceding if len(item) == 3) != cameras[:selected_index]: raise ValueError("source-union preceding camera proof is incomplete")
        if any(len(item) != 3 or type(item[1]) is not int or type(item[2]) is not int or item[1] < 0 or item[2] < 0 or (item[1] > 0 and item[2] > 0) for item in preceding): raise ValueError("source-union preceding camera was already bilaterally visible")
        if self.evidence_sha256 != _pure_seal(_visibility_payload(self, False)):
            raise ValueError("source-union visibility seal mismatch")
        object.__setattr__(self, "preceding_camera_mask_pixels", preceding)


def _visibility_payload(value: AdaptiveDirectVisibilityProof, include_seal: bool = True) -> dict[str, object]:
    payload = {"component_key": value.component_key, "pose_key": value.pose_key, "camera_key": value.camera_key, "reference_visible_mask_pixels": value.reference_visible_mask_pixels, "candidate_visible_mask_pixels": value.candidate_visible_mask_pixels, "preceding_camera_mask_pixels": [{"camera_key": item[0], "reference_visible_mask_pixels": item[1], "candidate_visible_mask_pixels": item[2]} for item in value.preceding_camera_mask_pixels]}
    if include_seal: payload["evidence_sha256"] = value.evidence_sha256
    return payload


adaptive_direct_visibility_payload = _visibility_payload


def adaptive_direct_visibility_from_payload(value: object) -> AdaptiveDirectVisibilityProof:
    fields = {"component_key", "pose_key", "camera_key", "reference_visible_mask_pixels", "candidate_visible_mask_pixels", "preceding_camera_mask_pixels", "evidence_sha256"}
    if type(value) is not dict or set(value) != fields or type(value["preceding_camera_mask_pixels"]) is not list: raise ValueError("source-union visibility payload fields are invalid")
    preceding = []
    for raw in value["preceding_camera_mask_pixels"]:
        if type(raw) is not dict or set(raw) != {"camera_key", "reference_visible_mask_pixels", "candidate_visible_mask_pixels"}: raise ValueError("source-union preceding camera payload is invalid")
        preceding.append((raw["camera_key"], raw["reference_visible_mask_pixels"], raw["candidate_visible_mask_pixels"]))
    copied = dict(value); copied["preceding_camera_mask_pixels"] = tuple(preceding)
    return AdaptiveDirectVisibilityProof(**copied)


@dataclass(frozen=True)
class AdaptiveDirectSourceUnionTarget:
    source_identity: str
    union_key: str
    coverage_manifest_sha256: str
    source_coverage_sha256: str
    component_keys: tuple[str, ...]
    material_region_keys: tuple[str, ...]
    pose_keys: tuple[str, ...]
    image_count: int
    target_sha256: str

    def __post_init__(self) -> None:
        require_canonical_relative(self.source_identity, "source-union identity")
        expected_key = f"source-union-{self.source_coverage_sha256[:32]}"
        if self.union_key != expected_key or not re.fullmatch(r"source-union-[0-9a-f]{32}", self.union_key): raise ValueError("source-union key is invalid")
        for value in (self.coverage_manifest_sha256, self.source_coverage_sha256):
            if not re.fullmatch(r"[0-9a-f]{64}", value): raise ValueError("source-union hash is invalid")
        components = tuple(self.component_keys); materials = tuple(self.material_region_keys); poses = tuple(self.pose_keys)
        if not components or len(components) > 256 or any(type(item) is not str or not item for item in components) or components != tuple(sorted(components, key=lambda item: (item.casefold(), item))) or len({item.casefold() for item in components}) != len(components): raise ValueError("source-union components are invalid")
        if not materials or len(materials) > 256 or any(type(item) is not str or not item for item in materials) or materials != tuple(sorted(materials, key=lambda item: (item.casefold(), item))) or len({item.casefold() for item in materials}) != len(materials): raise ValueError("source-union materials are invalid")
        if not 1 <= len(poses) <= 2 or any(type(item) is not str or not item for item in poses) or poses[0] != "bind" or len({item.casefold() for item in poses}) != len(poses): raise ValueError("source-union poses are invalid")
        if type(self.image_count) is not int or self.image_count != 32 * len(poses) or self.image_count > 64: raise ValueError("source-union image count is invalid")
        if self.target_sha256 != _pure_seal(_union_target_payload(self, False)): raise ValueError("source-union target seal mismatch")


def _union_target_payload(value: AdaptiveDirectSourceUnionTarget, include_seal: bool = True) -> dict[str, object]:
    payload = {"source_identity": value.source_identity, "union_key": value.union_key, "coverage_manifest_sha256": value.coverage_manifest_sha256, "source_coverage_sha256": value.source_coverage_sha256, "component_keys": list(value.component_keys), "material_region_keys": list(value.material_region_keys), "pose_keys": list(value.pose_keys), "image_count": value.image_count}
    if include_seal: payload["target_sha256"] = value.target_sha256
    return payload


adaptive_direct_source_union_target_payload = _union_target_payload


def adaptive_direct_source_union_target_from_payload(value: object) -> AdaptiveDirectSourceUnionTarget:
    fields = {"source_identity", "union_key", "coverage_manifest_sha256", "source_coverage_sha256", "component_keys", "material_region_keys", "pose_keys", "image_count", "target_sha256"}
    if type(value) is not dict or set(value) != fields or any(type(value[name]) is not list for name in ("component_keys", "material_region_keys", "pose_keys")): raise ValueError("source-union target payload fields are invalid")
    copied = dict(value)
    for name in ("component_keys", "material_region_keys", "pose_keys"): copied[name] = tuple(copied[name])
    return AdaptiveDirectSourceUnionTarget(**copied)


def build_adaptive_direct_source_union_target(*, source_proof, coverage_manifest_sha256) -> AdaptiveDirectSourceUnionTarget:
    if not isinstance(source_proof, AdaptiveDirectCoverageSourceProof): raise TypeError("source-union target requires typed coverage source proof")
    source_coverage = source_proof.source_coverage_sha256
    poses = source_proof.pose_keys; components = source_proof.component_keys; materials = source_proof.material_region_keys
    raw = dict(source_identity=source_proof.source_identity, coverage_manifest_sha256=coverage_manifest_sha256, source_coverage_sha256=source_coverage, union_key=f"source-union-{source_coverage[:32]}", component_keys=components, material_region_keys=materials, pose_keys=poses, image_count=32 * len(poses), target_sha256=_ZERO_HASH)
    provisional = _unsealed(AdaptiveDirectSourceUnionTarget, **raw); raw["target_sha256"] = _pure_seal(_union_target_payload(provisional, False)); return AdaptiveDirectSourceUnionTarget(**raw)


def _render_payload(value: RenderFileProof) -> dict[str, object]:
    return {"side": value.side, "kind": value.kind, "path": value.path, "size": value.size, "sha256": value.sha256, "width": value.width, "height": value.height}


@dataclass(frozen=True)
class AdaptiveDirectSourceUnionRecord:
    target: AdaptiveDirectSourceUnionTarget
    validation: ValidationResult
    files: tuple[RenderFileProof, ...]
    visibility: tuple[AdaptiveDirectVisibilityProof, ...]
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, AdaptiveDirectSourceUnionTarget) or not isinstance(self.validation, ValidationResult): raise TypeError("source-union record identity is invalid")
        files = tuple(self.files); visibility = tuple(self.visibility)
        cameras = tuple(f"camera-{index:02d}" for index in range(8)); passes = ("clay", "textured")
        expected = {f"source-union/{self.target.union_key}/{side}/{pose}/{render_pass}/{camera}.png" for side in ("candidate", "reference") for pose in self.target.pose_keys for render_pass in passes for camera in cameras}
        if len(files) != self.target.image_count or any(not isinstance(item, RenderFileProof) or item.kind != "image" for item in files) or {item.path for item in files} != expected or tuple(item.path for item in files) != tuple(sorted(item.path for item in files)):
            raise ValueError("source-union files are not the exact canonical matrix")
        expected_visibility = {(component, pose) for component in self.target.component_keys for pose in self.target.pose_keys}
        actual_visibility = {(item.component_key, item.pose_key) for item in visibility if isinstance(item, AdaptiveDirectVisibilityProof)}
        if len(visibility) != len(expected_visibility) or actual_visibility != expected_visibility or tuple((item.component_key, item.pose_key) for item in visibility) != tuple(sorted(actual_visibility)):
            raise ValueError("source-union visibility is not exact")
        if self.evidence_sha256 != _pure_seal(_union_record_payload(self, False)): raise ValueError("source-union record seal mismatch")
        object.__setattr__(self, "files", files); object.__setattr__(self, "visibility", visibility)


def _union_record_payload(value: AdaptiveDirectSourceUnionRecord, include_seal: bool = True) -> dict[str, object]:
    payload = {"target": _union_target_payload(value.target), "validation": validation_result_payload(value.validation), "files": [_render_payload(item) for item in value.files], "visibility": [_visibility_payload(item) for item in value.visibility]}
    if include_seal: payload["evidence_sha256"] = value.evidence_sha256
    return payload


adaptive_direct_source_union_record_payload = _union_record_payload


def adaptive_direct_source_union_record_from_payload(value: object) -> AdaptiveDirectSourceUnionRecord:
    fields = {"target", "validation", "files", "visibility", "evidence_sha256"}
    if type(value) is not dict or set(value) != fields or type(value["files"]) is not list or type(value["visibility"]) is not list: raise ValueError("source-union record payload fields are invalid")
    validation = value["validation"]
    if type(validation) is not dict or set(validation) != {"passed", "failures", "metrics", "worst_scope"} or type(validation["failures"]) is not list or type(validation["metrics"]) is not dict: raise ValueError("source-union validation payload is invalid")
    failures = []
    for raw in validation["failures"]:
        if type(raw) is not dict or set(raw) != {"gate", "scope", "measured", "limit", "message"}: raise ValueError("source-union failure payload is invalid")
        failures.append(GateFailure(**raw))
    validation_value = ValidationResult(validation["passed"], tuple(failures), validation["metrics"], validation["worst_scope"])
    files = []
    for raw in value["files"]:
        if type(raw) is not dict or set(raw) != {"side", "kind", "path", "size", "sha256", "width", "height"}: raise ValueError("source-union render payload is invalid")
        files.append(RenderFileProof(**raw))
    return AdaptiveDirectSourceUnionRecord(
        adaptive_direct_source_union_target_from_payload(value["target"]), validation_value,
        tuple(files), tuple(adaptive_direct_visibility_from_payload(item) for item in value["visibility"]),
        value["evidence_sha256"],
    )


def build_adaptive_direct_source_union_record(*, target, validation, files, visibility) -> AdaptiveDirectSourceUnionRecord:
    visibility_proofs = []
    for item in visibility:
        if type(item) not in (tuple, list) or len(item) not in {5, 6}: raise ValueError("source-union visibility input is invalid")
        component, pose, camera, reference_pixels, candidate_pixels = item[:5]
        preceding = () if len(item) == 5 else tuple(item[5])
        raw = dict(component_key=component, pose_key=pose, camera_key=camera, reference_visible_mask_pixels=reference_pixels, candidate_visible_mask_pixels=candidate_pixels, preceding_camera_mask_pixels=preceding, evidence_sha256=_ZERO_HASH)
        provisional = _unsealed(AdaptiveDirectVisibilityProof, **raw); raw["evidence_sha256"] = _pure_seal(_visibility_payload(provisional, False)); visibility_proofs.append(AdaptiveDirectVisibilityProof(**raw))
    raw = dict(target=target, validation=validation, files=tuple(files), visibility=tuple(visibility_proofs), evidence_sha256=_ZERO_HASH)
    provisional = _unsealed(AdaptiveDirectSourceUnionRecord, **raw); raw["evidence_sha256"] = _pure_seal(_union_record_payload(provisional, False)); return AdaptiveDirectSourceUnionRecord(**raw)


def _cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCancelledError("source recovery cancelled")


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE)


def _contained(path: Path, root: Path) -> str:
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root))
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("source path escapes source root") from exc
    if not relative or relative == "." or "\\" in relative or ".." in relative.split("/"):
        raise ValueError("source path is not canonical")
    return relative


def _safe_tree_files(root: Path, cancel_event: threading.Event | None) -> tuple[Path, ...]:
    root = Path(os.path.abspath(root))
    if _has_reparse_ancestor(root):
        raise ValueError("source root has a reparse ancestor")
    root_info = os.lstat(root)
    if not stat.S_ISDIR(root_info.st_mode) or _is_reparse(root_info):
        raise ValueError("source root must be a regular non-reparse directory")
    pending = [root]
    files: list[Path] = []
    while pending:
        _cancel(cancel_event)
        directory = pending.pop()
        directory_info = os.lstat(directory)
        if not stat.S_ISDIR(directory_info.st_mode) or _is_reparse(directory_info):
            raise ValueError("source tree contains an unsafe directory")
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda item: (item.name.casefold(), item.name))
        for entry in ordered:
            _cancel(cancel_event)
            path = Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            if _is_reparse(info) or stat.S_ISLNK(info.st_mode):
                raise ValueError("source tree contains a reparse point or symlink")
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                files.append(path)
                if len(files) > _MAX_SOURCE_FILES:
                    raise ValueError("source tree exceeds file bound")
            else:
                raise ValueError("source tree contains a special file")
    files.sort(key=lambda item: (_contained(item, root).casefold(), _contained(item, root)))
    relatives = [_contained(item, root) for item in files]
    if len({item.casefold() for item in relatives}) != len(relatives):
        raise ValueError("source tree contains a case-colliding path")
    return tuple(files)


def _hash_current_file(
    path: Path,
    root: Path,
    cancel_event: threading.Event | None,
    *,
    max_bytes: int,
) -> tuple[int, str]:
    path = Path(path)
    return _file_proof(
        path, cancel_event, max_bytes=max_bytes,
        contained_root=root,
    )


def revalidate_direct_source_snapshot(
    snapshot: DirectSourceSnapshot,
    cancel_event: threading.Event | None = None,
) -> DirectSourceSnapshot:
    if not isinstance(snapshot, DirectSourceSnapshot):
        raise TypeError("direct source snapshot is invalid")
    if direct_source_snapshot_from_payload(
        direct_source_snapshot_payload(snapshot), source_root=snapshot.source_root,
        input_source_root=snapshot.input_source_root,
    ) != snapshot:
        raise ValueError("direct snapshot typed payload is not self-consistent")
    root = Path(os.path.abspath(snapshot.source_root))
    try:
        files = _safe_tree_files(root, cancel_event)
    except OSError as exc:
        raise ValueError("direct snapshot root is unavailable") from exc
    if len(files) != 1:
        raise ValueError("direct snapshot must contain exactly one regular file")
    output = files[0]
    if _contained(output, root) != snapshot.output_relative_path:
        raise ValueError("direct snapshot output differs from the declared canonical path")
    try:
        size, digest = _hash_current_file(
            output,
            root,
            cancel_event,
            max_bytes=_MAX_SOURCE_BYTES,
        )
    except OSError as exc:
        raise ValueError("direct snapshot output is unavailable") from exc
    if (size, digest) != (snapshot.output_size, snapshot.output_sha256):
        raise ValueError("direct snapshot current bytes differ from declared size or hash")
    output_bytes = _read_regular_no_follow(
        output, cancel_event, contained_root=root, max_bytes=_MAX_SOURCE_BYTES,
    )
    if (len(output_bytes), hashlib.sha256(output_bytes).hexdigest()) != (size, digest):
        raise ValueError("direct snapshot output changed during semantic validation")
    try:
        material_counts = direct_smd_material_counts(output_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("direct snapshot output is not one complete valid SMD") from exc
    if material_counts != tuple(
        (item.material, item.triangles_after) for item in snapshot.material_triangles
    ):
        raise ValueError("direct snapshot current SMD differs from material ratio evidence")
    input_root = Path(os.path.abspath(snapshot.input_source_root))
    input_path = input_root.joinpath(*Path(snapshot.request.source_relative_path).parts)
    try:
        input_bytes = _read_regular_no_follow(
            input_path, cancel_event, contained_root=input_root,
            max_bytes=_MAX_SOURCE_BYTES,
        )
    except OSError as exc:
        raise ValueError("direct snapshot current input is unavailable") from exc
    if (
        len(input_bytes), hashlib.sha256(input_bytes).hexdigest()
    ) != (snapshot.request.source_size, snapshot.request.source_sha256):
        raise ValueError("direct snapshot current input differs from request")
    try:
        input_text = input_bytes.decode("utf-8")
        from .candidates import (
            _direct_input_material_proofs, _direct_prefilter_proof,
            _validate_direct_smd_output,
        )
        prefilter = prefilter_direct_degenerate_smd(input_text)
        if _direct_prefilter_proof(input_text) != snapshot.request.expected_prefilter:
            raise ValueError("direct snapshot current prefilter differs from request")
        if _direct_input_material_proofs(prefilter.filtered_text) != snapshot.request.expected_materials:
            raise ValueError("direct snapshot current material inventory differs from request")
        before, after, materials = _validate_direct_smd_output(
            prefilter.filtered_text, output_bytes.decode("utf-8"),
            snapshot.request.direct_ratio,
        )
    except (UnicodeDecodeError, ValueError, RuntimeError) as exc:
        raise ValueError("direct snapshot current input/output provenance is invalid") from exc
    if (
        before != snapshot.triangles_before or after != snapshot.triangles_after
        or materials != snapshot.material_triangles
    ):
        raise ValueError("direct snapshot current provenance differs from sealed evidence")
    if _safe_tree_files(root, cancel_event) != files:
        raise ValueError("direct snapshot file inventory changed during validation")
    return snapshot


def _manifest_digest(
    root_identity: str, files: tuple[SourceFileProof, ...], total_bytes: int
) -> str:
    payload = {
        "schema": 1,
        "root_identity": root_identity,
        "files": [{
            "file_identity": item.file_identity,
            "kind": item.kind,
            "relative_path": item.relative_path,
            "size": item.size,
            "sha256": item.sha256,
        } for item in files],
        "total_files": len(files),
        "total_bytes": total_bytes,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_source_tree_manifest(
    root: Path,
    graph: QcGraph,
    root_identity: str,
    cancel_event: threading.Event | None,
) -> SourceTreeManifest:
    root = Path(os.path.abspath(root))
    if not isinstance(graph, QcGraph) or Path(os.path.abspath(graph.family_root)) != root:
        raise ValueError("QC graph does not belong to source root")
    qc_paths = {Path(os.path.abspath(item.path)) for item in graph.files}
    roles: dict[Path, set[str]] = {}
    identities: dict[Path, set[str]] = {}
    for reference in graph.references:
        resolved = Path(os.path.abspath(reference.source_path))
        _contained(resolved, root)
        roles.setdefault(resolved, set()).add(reference.role)
        relative = _contained(resolved, root)
        parts = list(Path(*relative.split("/")).parts)
        if reference.role == "visual":
            if len(parts) >= 2 and parts[-2].casefold() == "output":
                parts.pop(-2)
            elif parts and parts[0].casefold() == "output":
                parts.pop(0)
            leaf = Path(parts[-1])
            stem = re.sub(r"_(?:optimized|opt)$", "", leaf.stem, flags=re.IGNORECASE)
            parts[-1] = stem + leaf.suffix.casefold()
        identity = "/".join(part.casefold() for part in parts)
        identities.setdefault(resolved, set()).add(identity)
    if any(len(values) != 1 for values in identities.values()):
        raise ValueError("QC graph source has ambiguous logical identities")
    flattened = [next(iter(values)) for values in identities.values()]
    if len(set(flattened)) != len(flattened):
        raise ValueError("QC graph source identities collide")
    for qc in qc_paths:
        _contained(qc, root)
    proofs: list[SourceFileProof] = []
    total_bytes = 0
    for path in _safe_tree_files(root, cancel_event):
        relative = _contained(path, root)
        size, digest = _hash_current_file(
            path, root, cancel_event, max_bytes=_MAX_SOURCE_BYTES - total_bytes
        )
        total_bytes += size
        if total_bytes > _MAX_SOURCE_BYTES:
            raise ValueError("source tree exceeds byte bound")
        resolved = Path(os.path.abspath(path))
        if resolved in qc_paths:
            kind = "qc"
        else:
            source_roles = roles.get(resolved, set())
            if any(role in {"collision", "physics"} for role in source_roles):
                kind = "physics-source"
            elif "animation" in source_roles:
                kind = "animation-source"
            elif "visual" in source_roles:
                kind = "visual-source"
            else:
                kind = "auxiliary"
        identity = next(iter(identities[resolved])) if resolved in identities else (
            relative if kind == "qc" else f"auxiliary/{relative}"
        )
        proofs.append(SourceFileProof(identity, kind, relative, size, digest))
    proofs_tuple = tuple(sorted(
        proofs, key=lambda item: (item.file_identity.casefold(), item.file_identity)
    ))
    return SourceTreeManifest(
        1, root_identity, proofs_tuple, len(proofs_tuple), total_bytes,
        _manifest_digest(root_identity, proofs_tuple, total_bytes),
    )


def revalidate_recovery_snapshot(
    snapshot: RecoverySourceSnapshot,
    cancel_event: threading.Event | None,
) -> SourceTreeManifest:
    if not isinstance(snapshot, RecoverySourceSnapshot):
        raise TypeError("recovery snapshot is invalid")
    root = Path(snapshot.source_root)
    current_paths = _safe_tree_files(root, cancel_event)
    by_relative = {item.relative_path: item for item in snapshot.source_manifest.files}
    current_by_relative = {_contained(path, root): path for path in current_paths}
    if set(current_by_relative) != set(by_relative):
        raise ValueError("recovery snapshot tree cardinality or path set changed")
    current: list[SourceFileProof] = []
    total = 0
    for expected in snapshot.source_manifest.files:
        relative = expected.relative_path
        path = current_by_relative[relative]
        expected = by_relative[relative]
        size, digest = _hash_current_file(
            path, root, cancel_event, max_bytes=_MAX_SOURCE_BYTES - total
        )
        total += size
        current.append(SourceFileProof(
            expected.file_identity, expected.kind, relative, size, digest
        ))
    files = tuple(current)
    manifest = SourceTreeManifest(
        1, snapshot.source_manifest.root_identity, files, len(files), total,
        _manifest_digest(snapshot.source_manifest.root_identity, files, total),
    )
    if manifest != snapshot.source_manifest:
        raise ValueError("recovery snapshot bytes no longer match sealed manifest")
    return manifest


def build_recovery_source_snapshot(
    *,
    kind: str,
    family_id: str,
    family_input_sha256: str,
    optimizer_contract_sha256: str,
    whole_profile_sha256: str,
    focused_profile_sha256: str,
    dependency_proof_sha256: str,
    candidate_id: str | None,
    candidate_cache_digest: str | None,
    source_root: Path,
    source_manifest: SourceTreeManifest,
    focused_evidence,
) -> RecoverySourceSnapshot:
    refs = tuple(focused_evidence)
    payload = {
        "schema": 1, "kind": kind, "family_id": family_id,
        "family_input_sha256": family_input_sha256,
        "optimizer_contract_sha256": optimizer_contract_sha256,
        "whole_profile_sha256": whole_profile_sha256,
        "focused_profile_sha256": focused_profile_sha256,
        "dependency_proof_sha256": dependency_proof_sha256,
        "candidate_id": candidate_id,
        "candidate_cache_digest": candidate_cache_digest,
        "source_manifest": source_tree_manifest_payload(source_manifest),
        "focused_evidence": [
            {"region_key": item.region_key, "evidence_sha256": item.evidence_sha256}
            for item in refs
        ],
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return RecoverySourceSnapshot(
        1, kind, family_id, family_input_sha256, optimizer_contract_sha256,
        whole_profile_sha256, focused_profile_sha256, dependency_proof_sha256,
        candidate_id, candidate_cache_digest, Path(source_root), source_manifest,
        refs, digest,
    )


def optimizer_contract_sha256(spec: CandidateSpec) -> str:
    if not isinstance(spec, CandidateSpec):
        raise TypeError("optimizer contract requires a candidate spec")
    payload = {
        "engine": spec.engine,
        "target_error": spec.target_error,
        "repair_profile": spec.repair_profile,
        "strategy": spec.strategy,
        "update_vertices": spec.update_vertices,
        "transfer": spec.transfer,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def candidate_spec_sha256(spec: CandidateSpec) -> str:
    if not isinstance(spec, CandidateSpec):
        raise TypeError("candidate spec proof requires a candidate spec")
    return hashlib.sha256(canonical_json(spec.cache_payload()).encode("utf-8")).hexdigest()


def _effective_ratio(spec: CandidateSpec, region_key: str) -> float:
    overrides = dict(spec.region_overrides)
    value = overrides.get(region_key, spec.target_ratio)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("candidate effective ratio is invalid")
    result = float(value)
    if not 0 < result <= 1:
        raise ValueError("candidate effective ratio is invalid")
    return result


def _file_by_identity(snapshot: RecoverySourceSnapshot, identity: str) -> SourceFileProof | None:
    return next(
        (item for item in snapshot.source_manifest.files if item.file_identity == identity),
        None,
    )


def _snapshot_contract_matches(
    candidate: RecoverySourceSnapshot,
    base: RecoverySourceSnapshot,
) -> bool:
    return all((
        candidate.family_id == base.family_id,
        candidate.family_input_sha256 == base.family_input_sha256,
        candidate.optimizer_contract_sha256 == base.optimizer_contract_sha256,
        candidate.whole_profile_sha256 == base.whole_profile_sha256,
        candidate.focused_profile_sha256 == base.focused_profile_sha256,
        candidate.dependency_proof_sha256 == base.dependency_proof_sha256,
    ))


def _overlay_digest(overlay: SourceOverlay) -> str:
    return hashlib.sha256(
        canonical_json(source_overlay_payload(overlay)).encode("utf-8")
    ).hexdigest()


def select_recovery_overlays(
    failed: CandidateEvaluation,
    selection: object,
    evaluations: object,
    snapshots_by_candidate: Mapping[str, RecoverySourceSnapshot],
    original_snapshot: RecoverySourceSnapshot,
    current_recipe: CompositeRecipe | None,
    attempted_overlay_sha256: object,
    round_index: int,
) -> tuple[SourceOverlay, ...]:
    if not isinstance(failed, CandidateEvaluation):
        raise TypeError("failed recovery evaluation is invalid")
    if not failed.structural.passed or not failed.whole_visual.passed:
        raise ValueError(
            "focused recovery base must pass structural and whole-visual gates"
        )
    if type(round_index) is not int or not 0 <= round_index < 3:
        raise ValueError("recovery round index is invalid")
    selected = tuple(getattr(selection, "selected", ()))
    if not selected or len(selected) > 4:
        raise ValueError("recovery focus selection is invalid")
    evaluations_tuple = tuple(evaluations)
    if len(evaluations_tuple) > 4096 or any(
        not isinstance(item, CandidateEvaluation) for item in evaluations_tuple
    ):
        raise ValueError("recovery evaluations are invalid")
    attempted = frozenset(attempted_overlay_sha256)
    if any(type(item) is not str for item in attempted):
        raise ValueError("attempted overlay hashes are invalid")
    base_candidate_id = (
        failed.spec.candidate_id
        if current_recipe is None else current_recipe.base_candidate_id
    )
    base_snapshot = snapshots_by_candidate.get(base_candidate_id)
    if base_snapshot is None or base_snapshot.kind != "candidate":
        raise ValueError("failed candidate source snapshot is unavailable")
    if base_snapshot.optimizer_contract_sha256 != optimizer_contract_sha256(failed.spec):
        raise ValueError("failed candidate optimizer contract mismatch")
    existing = {
        item.source_identity: item for item in (() if current_recipe is None else current_recipe.overlays)
    }
    failures = []
    for target in selected:
        result = failed.focused_by_region.get(target.region_key)
        if result is None or result.target != target:
            raise ValueError("failed evaluation does not cover selected focus")
        if not result.validation.passed:
            failures.append(target)
    if not failures:
        return ()
    failures.sort(key=lambda item: (item.source_identity.casefold(), item.source_identity, item.region_key))
    changed = False
    for target in failures:
        identity = target.source_identity
        if identity in existing and existing[identity].mode == "exact-original":
            continue
        base_file = _file_by_identity(base_snapshot, identity)
        if base_file is None:
            raise ValueError("failed focus source is absent from base snapshot")
        current_overlay = existing.get(identity)
        current_hash = (
            base_file.sha256 if current_overlay is None else current_overlay.replacement_sha256
        )
        current_ratio = (
            _effective_ratio(failed.spec, target.region_key)
            if current_overlay is None else current_overlay.effective_ratio
        )
        if current_ratio is None:
            continue
        ordered: list[tuple[float, str, str, CandidateEvaluation, RecoverySourceSnapshot]] = []
        for evaluation in evaluations_tuple:
            snapshot = snapshots_by_candidate.get(evaluation.spec.candidate_id)
            if snapshot is None or snapshot.kind != "candidate":
                continue
            ratio = _effective_ratio(evaluation.spec, target.region_key)
            if ratio <= current_ratio:
                continue
            ordered.append((ratio, evaluation.spec.candidate_id, snapshot.snapshot_sha256, evaluation, snapshot))
        ordered.sort(key=lambda item: (item[0], item[1], item[2]))
        replacement: SourceOverlay | None = None
        for ratio, candidate_id, _snapshot_hash, evaluation, snapshot in ordered[:8]:
            if candidate_id == failed.spec.candidate_id or evaluation.spec.composite_recipe is not None:
                continue
            if not evaluation.structural.passed or not evaluation.whole_visual.passed:
                continue
            if snapshot.candidate_id != candidate_id or snapshot.optimizer_contract_sha256 != optimizer_contract_sha256(evaluation.spec):
                continue
            if not _snapshot_contract_matches(snapshot, base_snapshot):
                continue
            focused = evaluation.focused_by_region.get(target.region_key)
            evidence_by_region = {item.region_key: item.evidence_sha256 for item in snapshot.focused_evidence}
            if (
                focused is None or focused.target != target or not focused.validation.passed
                or evidence_by_region.get(target.region_key) != focused.evidence_sha256
            ):
                continue
            donor_file = _file_by_identity(snapshot, identity)
            if donor_file is None or donor_file.sha256 == current_hash:
                continue
            proposal = SourceOverlay(
                identity, "donor", target.region_key, base_file.sha256,
                donor_file.sha256, donor_file.size, snapshot.snapshot_sha256,
                candidate_id, snapshot.candidate_cache_digest, ratio,
                (FocusedEvidenceRef(target.region_key, focused.evidence_sha256),), None,
            )
            if _overlay_digest(proposal) in attempted:
                continue
            replacement = proposal
            break
        if replacement is None:
            if original_snapshot.kind != "original" or not _snapshot_contract_matches(original_snapshot, base_snapshot):
                raise ValueError("authoritative original snapshot contract mismatch")
            original_file = _file_by_identity(original_snapshot, identity)
            if original_file is None:
                raise ValueError("failed focus source is absent from original snapshot")
            if original_file.sha256 == current_hash:
                continue
            proposal = SourceOverlay(
                identity, "exact-original", target.region_key, base_file.sha256,
                original_file.sha256, original_file.size, original_snapshot.snapshot_sha256,
                None, None, None, (), "donors-exhausted-v1",
            )
            if _overlay_digest(proposal) in attempted:
                continue
            replacement = proposal
        existing[identity] = replacement
        changed = True
        if len(existing) > 4:
            raise ValueError("recovery exceeds changed-source bound")
        # One reserved round advances exactly one canonical source. Remaining
        # failed sources are handled by later cumulative recipes.
        break
    if not changed:
        return ()
    return tuple(sorted(existing.values(), key=lambda item: (item.source_identity.casefold(), item.source_identity)))


def recovery_candidate_spec(base_spec: CandidateSpec, recipe: CompositeRecipe) -> CandidateSpec:
    if not isinstance(base_spec, CandidateSpec) or not isinstance(recipe, CompositeRecipe):
        raise TypeError("recovery candidate contract is invalid")
    if base_spec.composite_recipe is not None:
        raise ValueError("recovery base must be an ordinary candidate")
    if recipe.kind != "focused-recovery-v1":
        raise ValueError("Task 5 recovery requires focused-recovery-v1")
    if recipe.base_candidate_id != base_spec.candidate_id:
        raise ValueError("recovery recipe base candidate mismatch")
    if recipe.base_spec_sha256 != candidate_spec_sha256(base_spec):
        raise ValueError("recovery recipe base spec mismatch")
    if recipe.optimizer_contract_sha256 != optimizer_contract_sha256(base_spec):
        raise ValueError("recovery recipe optimizer contract mismatch")
    return CandidateSpec(
        "recovery-" + recipe.recipe_sha256,
        base_spec.engine,
        base_spec.target_ratio,
        base_spec.target_error,
        base_spec.repair_profile,
        base_spec.region_overrides,
        strategy=base_spec.strategy,
        update_vertices=base_spec.update_vertices,
        transfer=base_spec.transfer,
        composite_recipe=recipe,
    )


def focused_recovery_recipe(
    base_spec: CandidateSpec,
    base_snapshot: RecoverySourceSnapshot,
    overlays,
    *,
    round_index: int,
    selector_version: str,
) -> CompositeRecipe:
    if not isinstance(base_spec, CandidateSpec) or base_spec.composite_recipe is not None:
        raise ValueError("focused recovery recipe requires an ordinary base spec")
    if not isinstance(base_snapshot, RecoverySourceSnapshot) or base_snapshot.kind != "candidate":
        raise ValueError("focused recovery recipe requires a candidate base snapshot")
    if (
        base_snapshot.candidate_id != base_spec.candidate_id
        or base_snapshot.optimizer_contract_sha256 != optimizer_contract_sha256(base_spec)
    ):
        raise ValueError("focused recovery base snapshot does not match spec")
    overlay_tuple = tuple(overlays)
    payload = {
        "schema": 1, "kind": "focused-recovery-v1",
        "family_id": base_snapshot.family_id,
        "family_input_sha256": base_snapshot.family_input_sha256,
        "base_candidate_id": base_spec.candidate_id,
        "base_spec_sha256": candidate_spec_sha256(base_spec),
        "base_cache_digest": base_snapshot.candidate_cache_digest,
        "base_source_manifest_sha256": base_snapshot.source_manifest.digest,
        "optimizer_contract_sha256": base_snapshot.optimizer_contract_sha256,
        "whole_profile_sha256": base_snapshot.whole_profile_sha256,
        "focused_profile_sha256": base_snapshot.focused_profile_sha256,
        "dependency_proof_sha256": base_snapshot.dependency_proof_sha256,
        "round_index": round_index, "direct_ratio": None,
        "overlays": [source_overlay_payload(item) for item in overlay_tuple],
        "selector_version": selector_version, "prefilter_version": None,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return CompositeRecipe(
        1, "focused-recovery-v1", base_snapshot.family_id,
        base_snapshot.family_input_sha256, base_spec.candidate_id,
        candidate_spec_sha256(base_spec), base_snapshot.candidate_cache_digest,
        base_snapshot.source_manifest.digest, base_snapshot.optimizer_contract_sha256,
        base_snapshot.whole_profile_sha256, base_snapshot.focused_profile_sha256,
        base_snapshot.dependency_proof_sha256, round_index, None, overlay_tuple,
        selector_version, None, digest,
    )


def _overlaps(first: Path, second: Path) -> bool:
    first_text = os.path.normcase(os.path.abspath(first))
    second_text = os.path.normcase(os.path.abspath(second))
    try:
        common = os.path.commonpath((first_text, second_text))
    except ValueError:
        return False
    return common in {first_text, second_text}


def _remove_owned_composition_tree_no_follow(root: Path) -> None:
    root = Path(root)
    if not os.path.lexists(root):
        return

    def remove(path: Path) -> None:
        info = os.lstat(path)
        if _focused_is_reparse(path) or stat.S_ISLNK(info.st_mode):
            try:
                path.unlink()
            except (IsADirectoryError, PermissionError):
                os.rmdir(path)
            return
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as scan:
                entries = tuple(scan)
            for entry in entries:
                remove(Path(entry.path))
            os.rmdir(path)
            return
        path.unlink()

    remove(root)


def _quarantine_composition_workspace(workspace: Path) -> None:
    quarantine = workspace.with_name(
        f".{workspace.name}.composition-cleanup-{uuid.uuid4().hex}"
    )
    os.replace(workspace, quarantine)
    _remove_owned_composition_tree_no_follow(quarantine)


def _direct_set_digest(kind: str, coverage: str, ratio: float, values) -> str:
    if kind == "request":
        payloads = [direct_source_request_payload(item) for item in values]
        set_kind = "adaptive-direct-request-set-v1"
        key = "requests"
    elif kind == "snapshot":
        payloads = [direct_source_snapshot_payload(item) for item in values]
        set_kind = "adaptive-direct-snapshot-set-v1"
        key = "snapshots"
    else:
        raise ValueError("direct set kind is invalid")
    return _pure_seal({
        "schema": 1, "kind": set_kind,
        "coverage_manifest_sha256": coverage, "ratio": ratio, key: payloads,
    })


def _direct_snapshot_for_overlay(
    recipe: CompositeRecipe,
    base_snapshot: RecoverySourceSnapshot,
    overlay: SourceOverlay,
    snapshot: CompositionSnapshot,
    cancel_event: threading.Event | None,
) -> DirectSourceSnapshot:
    if overlay.mode != "direct-position" or recipe.kind != "adaptive-direct-fallback-v1":
        raise ValueError("direct snapshot dispatch is invalid")
    if not isinstance(snapshot, DirectSourceSnapshot):
        raise TypeError("direct-position overlay requires a direct source snapshot")
    revalidate_direct_source_snapshot(snapshot, cancel_event)
    request = snapshot.request
    base_file = _file_by_identity(base_snapshot, overlay.source_identity)
    if base_file is None or base_file.kind != "visual-source":
        raise ValueError("direct overlay does not target one base visual source")
    if any((
        request.family_id != recipe.family_id,
        request.family_input_sha256 != recipe.family_input_sha256,
        request.base_candidate_id != recipe.base_candidate_id,
        request.base_spec_sha256 != recipe.base_spec_sha256,
        request.base_cache_digest != recipe.base_cache_digest,
        request.base_source_manifest_sha256 != recipe.base_source_manifest_sha256,
        request.base_source_snapshot_sha256 != recipe.base_source_snapshot_sha256,
        request.coverage_manifest_sha256 != recipe.coverage_manifest_sha256,
        request.optimizer_contract_sha256 != recipe.optimizer_contract_sha256,
        request.whole_profile_sha256 != recipe.whole_profile_sha256,
        request.focused_profile_sha256 != recipe.focused_profile_sha256,
        request.dependency_proof_sha256 != recipe.dependency_proof_sha256,
        request.base_strategy != recipe.base_strategy,
        request.strategy != recipe.direct_strategy,
        request.transfer != recipe.direct_transfer,
        request.prefilter_version != recipe.prefilter_version,
        request.direct_ratio != recipe.direct_ratio,
        request.source_identity != overlay.source_identity,
        request.source_relative_path != base_file.relative_path,
        request.source_size != base_file.size,
        request.source_sha256 != base_file.sha256,
        overlay.base_source_sha256 != base_file.sha256,
        overlay.replacement_sha256 != snapshot.output_sha256,
        overlay.replacement_size != snapshot.output_size,
        overlay.replacement_snapshot_sha256 != snapshot.snapshot_sha256,
        overlay.replacement_candidate_id != snapshot.direct_candidate_id,
        overlay.replacement_cache_digest != snapshot.direct_cache_digest,
        overlay.effective_ratio != request.direct_ratio,
    )):
        raise ValueError("direct-position overlay differs from sealed request/snapshot/base matrix")
    return snapshot


def _resolve_recipe_snapshots(
    recipe: CompositeRecipe,
    base_snapshot: RecoverySourceSnapshot,
    snapshots_by_sha256: Mapping[str, CompositionSnapshot],
    cancel_event: threading.Event | None,
    coverage_manifest: AdaptiveDirectCoverageManifest | None = None,
) -> dict[str, CompositionSnapshot]:
    resolved: dict[str, CompositionSnapshot] = {}
    direct_requests = []
    direct_snapshots = []
    for overlay in recipe.overlays:
        snapshot = snapshots_by_sha256.get(overlay.replacement_snapshot_sha256)
        if snapshot is None:
            raise ValueError("composition replacement snapshot is unavailable")
        if recipe.kind == "focused-recovery-v1":
            if overlay.mode not in {"donor", "exact-original"}:
                raise ValueError("focused recovery overlay dispatch is invalid")
            if not isinstance(snapshot, RecoverySourceSnapshot):
                raise TypeError("recovery overlay requires a recovery source snapshot")
            revalidate_recovery_snapshot(snapshot, cancel_event)
        elif recipe.kind == "adaptive-direct-fallback-v1":
            snapshot = _direct_snapshot_for_overlay(
                recipe, base_snapshot, overlay, snapshot, cancel_event
            )
            direct_requests.append(snapshot.request)
            direct_snapshots.append(snapshot)
        else:
            raise ValueError("composition recipe kind is invalid")
        resolved[overlay.replacement_snapshot_sha256] = snapshot
    if recipe.kind == "adaptive-direct-fallback-v1":
        if not isinstance(coverage_manifest, AdaptiveDirectCoverageManifest):
            raise ValueError("adaptive direct composition requires typed coverage")
        if any((
            coverage_manifest.coverage_manifest_sha256 != recipe.coverage_manifest_sha256,
            coverage_manifest.family_id != recipe.family_id,
            coverage_manifest.family_input_sha256 != recipe.family_input_sha256,
            coverage_manifest.base_candidate_id != recipe.base_candidate_id,
            coverage_manifest.base_spec_sha256 != recipe.base_spec_sha256,
            coverage_manifest.base_cache_digest != recipe.base_cache_digest,
            coverage_manifest.base_source_manifest_sha256 != recipe.base_source_manifest_sha256,
            coverage_manifest.base_source_snapshot_sha256 != recipe.base_source_snapshot_sha256,
            base_snapshot.snapshot_sha256 != recipe.base_source_snapshot_sha256,
        )):
            raise ValueError("adaptive direct coverage/base binding differs from recipe")
        eligible = tuple(
            item for item in coverage_manifest.sources
            if item.eligibility_kind == "eligible-exact-v1"
        )
        eligible_identities = tuple(item.source_identity for item in eligible)
        request_identities = tuple(item.source_identity for item in direct_requests)
        snapshot_identities = tuple(item.request.source_identity for item in direct_snapshots)
        overlay_identities = tuple(item.source_identity for item in recipe.overlays)
        if (
            not eligible_identities
            or overlay_identities != eligible_identities
            or request_identities != eligible_identities
            or snapshot_identities != eligible_identities
        ):
            raise ValueError("adaptive direct composition differs from complete eligible coverage")
        for source, request in zip(eligible, direct_requests):
            if any((
                request.source_coverage_sha256 != source.source_coverage_sha256,
                request.source_size != source.source_size,
                request.source_sha256 != source.source_sha256,
            )):
                raise ValueError("direct request differs from typed source coverage")
        if _direct_set_digest(
            "request", recipe.coverage_manifest_sha256, recipe.direct_ratio,
            direct_requests,
        ) != recipe.direct_request_set_sha256:
            raise ValueError("direct composition request set differs from recipe")
        if _direct_set_digest(
            "snapshot", recipe.coverage_manifest_sha256, recipe.direct_ratio,
            direct_snapshots,
        ) != recipe.direct_snapshot_set_sha256:
            raise ValueError("direct composition snapshot set differs from recipe")
    return resolved


def _inventory_with_kinds(
    root: Path,
    root_identity: str,
    kinds: Mapping[str, str],
    cancel_event: threading.Event | None,
) -> SourceTreeManifest:
    root = Path(os.path.abspath(root))
    proofs: list[SourceFileProof] = []
    total = 0
    for path in _safe_tree_files(root, cancel_event):
        relative = _contained(path, root)
        kind = kinds.get(relative)
        if kind is None:
            kind = "qc" if relative.casefold().endswith(".qc") else "auxiliary"
        size, digest = _file_proof(
            path, cancel_event, max_bytes=_MAX_SOURCE_BYTES - total, contained_root=root
        )
        total += size
        if total > _MAX_SOURCE_BYTES:
            raise ValueError("source tree exceeds byte bound")
        proofs.append(SourceFileProof(relative, kind, relative, size, digest))
    files = tuple(proofs)
    return SourceTreeManifest(
        1, root_identity, files, len(files), total,
        _manifest_digest(root_identity, files, total),
    )


def _matching_graph_manifest(
    root: Path,
    expected_digest: str,
    root_identity: str,
    cancel_event: threading.Event | None,
) -> tuple[Path, QcGraph, SourceTreeManifest]:
    root = Path(os.path.abspath(root))
    matches: list[tuple[Path, QcGraph, SourceTreeManifest]] = []
    for path in _safe_tree_files(root, cancel_event):
        if path.suffix.casefold() != ".qc":
            continue
        try:
            graph = parse_qc_graph(path, root)
            manifest = build_source_tree_manifest(root, graph, root_identity, cancel_event)
        except (OSError, UnicodeError, ValueError):
            continue
        if manifest.digest == expected_digest:
            matches.append((path, graph, manifest))
    if len(matches) != 1:
        raise ValueError("source tree does not have one exact QC graph manifest")
    return matches[0]


def _composition_proof(
    recipe: CompositeRecipe,
    base_manifest: SourceTreeManifest,
    composed_manifest: SourceTreeManifest,
    snapshots_by_sha256: Mapping[str, CompositionSnapshot],
) -> CompositionProof:
    base_by_id = {item.file_identity: item for item in base_manifest.files}
    composed_by_id = {item.file_identity: item for item in composed_manifest.files}
    if set(base_by_id) != set(composed_by_id):
        raise ValueError("composition added or removed a source file")
    overlays = {item.source_identity: item for item in recipe.overlays}
    changed: list[ChangedSourceProof] = []
    for identity, before in base_by_id.items():
        after = composed_by_id[identity]
        overlay = overlays.get(identity)
        if overlay is None:
            if before.relative_path != after.relative_path or before.size != after.size or before.sha256 != after.sha256 or before.kind != after.kind:
                raise ValueError("composition changed an undeclared source")
            continue
        if before.sha256 != overlay.base_source_sha256:
            raise ValueError("overlay base hash does not match immutable base")
        if after.sha256 != overlay.replacement_sha256 or after.size != overlay.replacement_size:
            raise ValueError("composed replacement bytes do not match overlay")
        snapshot = snapshots_by_sha256.get(overlay.replacement_snapshot_sha256)
        if snapshot is None or snapshot.snapshot_sha256 != overlay.replacement_snapshot_sha256:
            raise ValueError("overlay replacement snapshot is unavailable")
        snapshot_refs = {}
        if isinstance(snapshot, RecoverySourceSnapshot):
            if any((
                snapshot.family_id != recipe.family_id,
                snapshot.family_input_sha256 != recipe.family_input_sha256,
                snapshot.optimizer_contract_sha256 != recipe.optimizer_contract_sha256,
                snapshot.whole_profile_sha256 != recipe.whole_profile_sha256,
                snapshot.focused_profile_sha256 != recipe.focused_profile_sha256,
                snapshot.dependency_proof_sha256 != recipe.dependency_proof_sha256,
            )):
                raise ValueError("overlay replacement snapshot crosses recipe contract")
            snapshot_refs = {
                item.region_key: item.evidence_sha256 for item in snapshot.focused_evidence
            }
        overlay_refs = {
            item.region_key: item.evidence_sha256 for item in overlay.focused_evidence
        }
        if overlay.mode == "donor":
            if (
                snapshot.kind != "candidate"
                or snapshot.candidate_id != overlay.replacement_candidate_id
                or snapshot.candidate_cache_digest != overlay.replacement_cache_digest
                or not overlay_refs
                or any(snapshot_refs.get(key) != value for key, value in overlay_refs.items())
            ):
                raise ValueError("donor overlay does not match candidate snapshot evidence")
        elif overlay.mode == "exact-original":
            if snapshot.kind != "original":
                raise ValueError("exact-original overlay does not use original snapshot")
        elif overlay.mode == "direct-position":
            if (
                not isinstance(snapshot, DirectSourceSnapshot)
                or snapshot.direct_candidate_id != overlay.replacement_candidate_id
                or snapshot.direct_cache_digest != overlay.replacement_cache_digest
            ):
                raise ValueError("direct-position overlay does not match direct snapshot")
        if isinstance(snapshot, DirectSourceSnapshot):
            replacement_size = snapshot.output_size
            replacement_hash = snapshot.output_sha256
        else:
            replacement = _file_by_identity(snapshot, identity)
            if replacement is None:
                raise ValueError("sealed replacement source is unavailable")
            replacement_size = replacement.size
            replacement_hash = replacement.sha256
        if replacement_hash != after.sha256 or replacement_size != after.size:
            raise ValueError("composed bytes do not match sealed replacement snapshot")
        changed.append(ChangedSourceProof(
            identity, after.relative_path, before.size, before.sha256,
            after.size, after.sha256, _overlay_digest(overlay), snapshot.snapshot_sha256,
        ))
    if set(overlays) != {item.source_identity for item in changed}:
        raise ValueError("composition did not prove every overlay")
    changed_tuple = tuple(sorted(changed, key=lambda item: (item.source_identity.casefold(), item.source_identity)))
    payload = {
        "schema": 1, "kind": recipe.kind, "recipe_sha256": recipe.recipe_sha256,
        "base_manifest_sha256": base_manifest.digest,
        "composed_manifest_sha256": composed_manifest.digest,
        "changed_sources": [changed_source_proof_payload(item) for item in changed_tuple],
    }
    evidence = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return CompositionProof(
        1, recipe.recipe_sha256, base_manifest.digest, composed_manifest.digest,
        changed_tuple, evidence, recipe.kind,
    )


def validate_composition_proof(
    recipe: CompositeRecipe,
    snapshots_by_sha256: Mapping[str, CompositionSnapshot],
    base_root: Path,
    composed_root: Path,
    cancel_event: threading.Event | None,
    *,
    coverage_manifest: AdaptiveDirectCoverageManifest | None = None,
    base_snapshot: RecoverySourceSnapshot | None = None,
) -> CompositionProof:
    if not isinstance(recipe, CompositeRecipe):
        raise TypeError("composition recipe is invalid")
    base_root = Path(os.path.abspath(base_root))
    composed_root = Path(os.path.abspath(composed_root))
    if _overlaps(base_root, composed_root):
        raise ValueError("composition roots overlap")
    base_qc, _base_graph, base_manifest = _matching_graph_manifest(
        base_root, recipe.base_source_manifest_sha256,
        "candidate-source-v1", cancel_event,
    )
    if recipe.kind == "adaptive-direct-fallback-v1":
        if not isinstance(base_snapshot, RecoverySourceSnapshot):
            raise ValueError("adaptive direct proof requires authoritative base snapshot")
        if Path(os.path.abspath(base_snapshot.source_root)) != base_root:
            raise ValueError("adaptive direct base snapshot root differs")
        revalidate_recovery_snapshot(base_snapshot, cancel_event)
        if base_snapshot.source_manifest != base_manifest:
            raise ValueError("adaptive direct base snapshot manifest differs")
    else:
        base_snapshot = build_recovery_source_snapshot(
            kind="candidate", family_id=recipe.family_id,
            family_input_sha256=recipe.family_input_sha256,
            optimizer_contract_sha256=recipe.optimizer_contract_sha256,
            whole_profile_sha256=recipe.whole_profile_sha256,
            focused_profile_sha256=recipe.focused_profile_sha256,
            dependency_proof_sha256=recipe.dependency_proof_sha256,
            candidate_id=recipe.base_candidate_id,
            candidate_cache_digest=recipe.base_cache_digest,
            source_root=base_root, source_manifest=base_manifest, focused_evidence=(),
        )
    resolved = _resolve_recipe_snapshots(
        recipe, base_snapshot, snapshots_by_sha256, cancel_event, coverage_manifest
    )
    qc_relative = base_qc.relative_to(base_root)
    composed_graph = parse_qc_graph(composed_root / qc_relative, composed_root)
    composed_manifest = build_source_tree_manifest(
        composed_root, composed_graph, "composite-source-v1", cancel_event
    )
    return _composition_proof(recipe, base_manifest, composed_manifest, resolved)


def compose_candidate_sources(
    base_build: object,
    recipe: CompositeRecipe,
    snapshots_by_sha256: Mapping[str, CompositionSnapshot],
    workspace: Path,
    cancel_event: threading.Event | None,
    *,
    coverage_manifest: AdaptiveDirectCoverageManifest | None = None,
) -> ComposedSourceTree:
    from .candidates import CandidateBuild

    if not isinstance(base_build, CandidateBuild) or not isinstance(recipe, CompositeRecipe):
        raise TypeError("composition input is invalid")
    base_snapshot = base_build.source_snapshot
    if base_snapshot is None or base_snapshot.kind != "candidate":
        raise ValueError("composition base has no candidate snapshot")
    if recipe.base_candidate_id != base_build.spec.candidate_id:
        raise ValueError("composition recipe base candidate mismatch")
    if recipe.base_spec_sha256 != candidate_spec_sha256(base_build.spec):
        raise ValueError("composition recipe base spec mismatch")
    if recipe.base_cache_digest != base_snapshot.candidate_cache_digest:
        raise ValueError("composition recipe base cache mismatch")
    if recipe.base_source_manifest_sha256 != base_snapshot.source_manifest.digest:
        raise ValueError("composition recipe base source mismatch")
    if recipe.kind == "adaptive-direct-fallback-v1" and (
        recipe.base_source_snapshot_sha256 != base_snapshot.snapshot_sha256
    ):
        raise ValueError("composition recipe base snapshot mismatch")
    if recipe.optimizer_contract_sha256 != optimizer_contract_sha256(base_build.spec):
        raise ValueError("composition recipe optimizer mismatch")
    if any((
        recipe.family_id != base_snapshot.family_id,
        recipe.family_input_sha256 != base_snapshot.family_input_sha256,
        recipe.optimizer_contract_sha256 != base_snapshot.optimizer_contract_sha256,
        recipe.whole_profile_sha256 != base_snapshot.whole_profile_sha256,
        recipe.focused_profile_sha256 != base_snapshot.focused_profile_sha256,
        recipe.dependency_proof_sha256 != base_snapshot.dependency_proof_sha256,
    )):
        raise ValueError("composition recipe crosses immutable base contract")
    base_root = Path(base_snapshot.source_root)
    workspace = Path(os.path.abspath(workspace))
    if os.path.lexists(workspace):
        raise ValueError("composition workspace must not exist")
    if _overlaps(workspace, base_root) or _overlaps(workspace, base_build.compiled_models_dir):
        raise ValueError("composition workspace overlaps an input")
    if _has_reparse_ancestor(workspace.parent):
        raise ValueError("composition workspace parent has a reparse ancestor")
    # This function is called only after the orchestrator atomically reserves both
    # the recovery-round and candidate slots. Reopening current bytes starts here.
    revalidate_recovery_snapshot(base_snapshot, cancel_event)
    resolved = _resolve_recipe_snapshots(
        recipe, base_snapshot, snapshots_by_sha256, cancel_event, coverage_manifest
    )
    for snapshot in resolved.values():
        if isinstance(snapshot, DirectSourceSnapshot) and (
            _overlaps(workspace, snapshot.source_root)
            or _overlaps(workspace, snapshot.input_source_root)
        ):
            raise ValueError("composition workspace overlaps a direct snapshot input")
    source_root = workspace / "src"
    try:
        workspace.mkdir(parents=False, exist_ok=False)
        source_root.mkdir()
        for proof in base_snapshot.source_manifest.files:
            _cancel(cancel_event)
            source = base_root / Path(*proof.relative_path.split("/"))
            destination = source_root / Path(*proof.relative_path.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            _copy_file_no_follow(source, destination, cancel_event, contained_root=base_root)
        for overlay in recipe.overlays:
            snapshot = resolved[overlay.replacement_snapshot_sha256]
            base_file = _file_by_identity(base_snapshot, overlay.source_identity)
            if base_file is None:
                raise ValueError("composition source identity is unavailable")
            if isinstance(snapshot, DirectSourceSnapshot):
                replacement_size = snapshot.output_size
                replacement_hash = snapshot.output_sha256
                replacement_path = snapshot.output_relative_path
                replacement_root = Path(snapshot.source_root)
            else:
                replacement = _file_by_identity(snapshot, overlay.source_identity)
                if replacement is None:
                    raise ValueError("composition replacement identity is unavailable")
                replacement_size = replacement.size
                replacement_hash = replacement.sha256
                replacement_path = replacement.relative_path
                replacement_root = Path(snapshot.source_root)
            if base_file.sha256 != overlay.base_source_sha256 or replacement_hash != overlay.replacement_sha256 or replacement_size != overlay.replacement_size:
                raise ValueError("composition overlay proof does not match snapshots")
            source = replacement_root / Path(*replacement_path.split("/"))
            destination = source_root / Path(*base_file.relative_path.split("/"))
            temporary = destination.with_name(destination.name + ".replacement.tmp")
            _copy_file_no_follow(
                source, temporary, cancel_event, contained_root=replacement_root
            )
            os.replace(temporary, destination)
        optimized_relative = Path(base_build.optimized_qc).relative_to(base_root)
        optimized_qc = source_root / optimized_relative
        composed_graph = parse_qc_graph(optimized_qc, source_root)
        composed_manifest = build_source_tree_manifest(
            source_root, composed_graph, "composite-source-v1", cancel_event
        )
        composition = _composition_proof(
            recipe, base_snapshot.source_manifest, composed_manifest, resolved
        )
        if not optimized_qc.is_file() or _focused_is_reparse(optimized_qc):
            raise ValueError("composed optimized QC is unavailable")
        return ComposedSourceTree(workspace, optimized_qc, composed_manifest, composition)
    except BaseException:
        if os.path.lexists(workspace):
            _quarantine_composition_workspace(workspace)
        raise
