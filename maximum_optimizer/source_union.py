from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, UnidentifiedImageError

from .composite import (
    AdaptiveDirectSourceUnionRecord,
    AdaptiveDirectSourceUnionTarget,
    build_adaptive_direct_source_union_record,
    build_adaptive_direct_source_union_target,
    revalidate_direct_source_snapshot,
)
from .domain import (
    AdaptiveDirectCoverageManifest,
    AdaptiveDirectCoverageSourceProof,
    DirectSourceSnapshot,
    ValidationResult,
)
from .focused_cache import (
    RenderFileProof,
    _assert_safe_tree,
    _file_proof,
    _has_reparse_ancestor,
    _is_reparse,
    _read_regular_no_follow,
)
from .processes import ProcessCancelledError
from .visual_validation import FidelityProfile


_HASH = re.compile(r"[0-9a-f]{64}")
_CAMERAS = tuple(f"camera-{index:02d}" for index in range(8))
_PASSES = ("clay", "textured")
_MAX_IMAGES_PER_SOURCE = 64
_MAX_IMAGES_PER_CANDIDATE = 512
_MAX_RENDER_BYTES = 512 * 1024 * 1024
_MAX_IMAGE_PIXELS = 16 * 1024 * 1024


@dataclass(frozen=True)
class SourceUnionMaterialBinding:
    material_region_key: str
    material_contract_sha256: str

    def __post_init__(self) -> None:
        if type(self.material_region_key) is not str or not self.material_region_key:
            raise ValueError("source-union material region is invalid")
        if _HASH.fullmatch(self.material_contract_sha256 or "") is None:
            raise ValueError("source-union material contract is invalid")


@dataclass(frozen=True)
class SourceUnionMaskObservation:
    side: str
    component_key: str
    pose_key: str
    camera_key: str
    visible_mask_pixels: int

    def __post_init__(self) -> None:
        if self.side not in {"reference", "candidate"}:
            raise ValueError("source-union observation side is invalid")
        if any(type(value) is not str or not value for value in (
            self.component_key, self.pose_key, self.camera_key,
        )):
            raise ValueError("source-union observation identity is invalid")
        if self.camera_key not in _CAMERAS:
            raise ValueError("source-union observation camera is invalid")
        if type(self.visible_mask_pixels) is not int or self.visible_mask_pixels < 0:
            raise ValueError("source-union observation pixels are invalid")


@dataclass(frozen=True)
class SourceUnionRenderOutput:
    root: Path
    observations: tuple[SourceUnionMaskObservation, ...]

    def __post_init__(self) -> None:
        root = Path(os.path.abspath(self.root))
        observations = tuple(self.observations)
        if not root.is_absolute():
            raise ValueError("source-union render root must be absolute")
        if any(not isinstance(item, SourceUnionMaskObservation) for item in observations):
            raise TypeError("source-union observations are invalid")
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "observations", observations)


@dataclass(frozen=True)
class SourceUnionRenderRequest:
    target: AdaptiveDirectSourceUnionTarget
    reference_source: Path
    candidate_source: Path
    dependency_proof_sha256: str
    material_bindings: tuple[SourceUnionMaterialBinding, ...]
    cameras: tuple[str, ...] = _CAMERAS
    render_passes: tuple[str, ...] = _PASSES

    def __post_init__(self) -> None:
        if not isinstance(self.target, AdaptiveDirectSourceUnionTarget):
            raise TypeError("source-union render target is invalid")
        reference = Path(self.reference_source)
        candidate = Path(self.candidate_source)
        if not reference.is_absolute() or not candidate.is_absolute():
            raise ValueError("source-union render sources must be absolute")
        if _HASH.fullmatch(self.dependency_proof_sha256 or "") is None:
            raise ValueError("source-union dependency proof is invalid")
        bindings = tuple(self.material_bindings)
        if any(not isinstance(item, SourceUnionMaterialBinding) for item in bindings):
            raise TypeError("source-union material bindings are invalid")
        if self.cameras != _CAMERAS or self.render_passes != _PASSES:
            raise ValueError("source-union camera/pass matrix is invalid")
        object.__setattr__(self, "reference_source", reference)
        object.__setattr__(self, "candidate_source", candidate)
        object.__setattr__(self, "material_bindings", bindings)


SourceUnionRenderer = Callable[
    [SourceUnionRenderRequest, Path, threading.Event | None], SourceUnionRenderOutput
]
SourceUnionComparator = Callable[[Path, Path, FidelityProfile], ValidationResult]


def _cancel(cancel_event: threading.Event | None, message: str) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCancelledError(message)


def _overlaps(first: Path, second: Path) -> bool:
    first_text = os.path.normcase(os.path.abspath(first))
    second_text = os.path.normcase(os.path.abspath(second))
    try:
        common = os.path.commonpath((first_text, second_text))
    except ValueError:
        return False
    return common in {first_text, second_text}


def _remove_owned_tree_no_follow(root: Path) -> None:
    if not os.path.lexists(root):
        return

    def remove(path: Path) -> None:
        info = os.lstat(path)
        if _is_reparse(path) or stat.S_ISLNK(info.st_mode):
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
        else:
            path.unlink()

    remove(Path(root))


def _quarantine_cleanup(workspace: Path) -> None:
    quarantine = workspace.with_name(
        f".{workspace.name}.source-union-cleanup-{uuid.uuid4().hex}"
    )
    os.replace(workspace, quarantine)
    _remove_owned_tree_no_follow(quarantine)


def _expected_paths(target: AdaptiveDirectSourceUnionTarget) -> tuple[str, ...]:
    return tuple(sorted(
        f"source-union/{target.union_key}/{side}/{pose}/{render_pass}/{camera}.png"
        for side in ("candidate", "reference")
        for pose in target.pose_keys
        for render_pass in _PASSES
        for camera in _CAMERAS
    ))


def _render_manifest(
    root: Path,
    target: AdaptiveDirectSourceUnionTarget,
    cancel_event: threading.Event | None,
) -> tuple[RenderFileProof, ...]:
    expected = _expected_paths(target)
    paths = _assert_safe_tree(
        root, cancel_event, set(expected), max_files=_MAX_IMAGES_PER_SOURCE,
        max_bytes=_MAX_RENDER_BYTES,
    )
    proofs = []
    for path in paths:
        _cancel(cancel_event, "cancelled during source-union manifest")
        relative = path.relative_to(root).as_posix()
        payload = _read_regular_no_follow(
            path, cancel_event, contained_root=root, max_bytes=_MAX_RENDER_BYTES,
        )
        size, digest = _file_proof(
            path, cancel_event, contained_root=root, max_bytes=len(payload),
        )
        if (size, digest) != (len(payload), hashlib.sha256(payload).hexdigest()):
            raise ValueError("source-union image changed during proof")
        try:
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != "PNG":
                    raise ValueError("source-union image content is not PNG")
                width, height = image.size
                if width * height > _MAX_IMAGE_PIXELS:
                    raise ValueError("source-union image exceeds pixel bound")
                image.load()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise ValueError("source-union image is invalid") from exc
        parts = relative.split("/")
        if len(parts) != 6 or parts[0] != "source-union" or parts[1] != target.union_key:
            raise ValueError("source-union image path is invalid")
        proofs.append(RenderFileProof(parts[2], "image", relative, size, digest, width, height))
    proofs_tuple = tuple(sorted(proofs, key=lambda item: item.path))
    if tuple(item.path for item in proofs_tuple) != expected:
        raise ValueError("source-union image matrix differs")
    return proofs_tuple


def _visibility(
    target: AdaptiveDirectSourceUnionTarget,
    observations: tuple[SourceUnionMaskObservation, ...],
) -> tuple[tuple, ...]:
    expected = tuple(
        (side, component, pose, camera)
        for side in ("candidate", "reference")
        for component in target.component_keys
        for pose in target.pose_keys
        for camera in _CAMERAS
    )
    indexed = {}
    for item in observations:
        key = (item.side, item.component_key, item.pose_key, item.camera_key)
        if key in indexed:
            raise ValueError("source-union visibility observation is duplicated")
        indexed[key] = item.visible_mask_pixels
    if set(indexed) != set(expected) or len(indexed) != len(expected):
        raise ValueError("source-union visibility observations are not exact")
    result = []
    for component in target.component_keys:
        for pose in target.pose_keys:
            preceding = []
            for camera in _CAMERAS:
                reference = indexed[("reference", component, pose, camera)]
                candidate = indexed[("candidate", component, pose, camera)]
                if reference > 0 and candidate > 0:
                    result.append((
                        component, pose, camera, reference, candidate, tuple(preceding),
                    ))
                    break
                preceding.append((camera, reference, candidate))
            else:
                raise ValueError("source-union component is never bilaterally visible")
    return tuple(result)


def validate_adaptive_direct_source_union(
    *,
    coverage: AdaptiveDirectCoverageManifest,
    source_proof: AdaptiveDirectCoverageSourceProof,
    snapshot: DirectSourceSnapshot,
    workspace: Path,
    dependency_proof_sha256: str,
    material_bindings: tuple[SourceUnionMaterialBinding, ...],
    profile: FidelityProfile,
    renderer: SourceUnionRenderer,
    comparator: SourceUnionComparator,
    cancel_event: threading.Event | None = None,
) -> AdaptiveDirectSourceUnionRecord:
    if not isinstance(coverage, AdaptiveDirectCoverageManifest):
        raise TypeError("source-union coverage is invalid")
    if not isinstance(source_proof, AdaptiveDirectCoverageSourceProof):
        raise TypeError("source-union source proof is invalid")
    if not isinstance(snapshot, DirectSourceSnapshot) or not isinstance(profile, FidelityProfile):
        raise TypeError("source-union snapshot/profile is invalid")
    if not callable(renderer) or not callable(comparator):
        raise TypeError("source-union renderer/comparator is invalid")
    current = tuple(item for item in coverage.sources if item.source_identity == source_proof.source_identity)
    eligible = tuple(item for item in coverage.sources if item.eligibility_kind == "eligible-exact-v1")
    if len(current) != 1 or current[0] != source_proof or source_proof.eligibility_kind != "eligible-exact-v1":
        raise ValueError("source-union source is not one exact eligible coverage member")
    if coverage.maximum_candidate_images > _MAX_IMAGES_PER_CANDIDATE:
        raise ValueError("source-union candidate image bound exceeded")
    request = snapshot.request
    if any((
        request.coverage_manifest_sha256 != coverage.coverage_manifest_sha256,
        request.source_coverage_sha256 != source_proof.source_coverage_sha256,
        request.source_identity != source_proof.source_identity,
        request.source_size != source_proof.source_size,
        request.source_sha256 != source_proof.source_sha256,
        request.base_candidate_id != coverage.base_candidate_id,
        request.base_spec_sha256 != coverage.base_spec_sha256,
        request.base_cache_digest != coverage.base_cache_digest,
        request.base_source_manifest_sha256 != coverage.base_source_manifest_sha256,
        request.base_source_snapshot_sha256 != coverage.base_source_snapshot_sha256,
        request.dependency_proof_sha256 != dependency_proof_sha256,
    )):
        raise ValueError("source-union request/coverage/dependency binding differs")
    bindings = tuple(material_bindings)
    keys = tuple(item.material_region_key for item in bindings if isinstance(item, SourceUnionMaterialBinding))
    if (
        len(keys) != len(bindings)
        or keys != source_proof.material_region_keys
        or len({key.casefold() for key in keys}) != len(keys)
    ):
        raise ValueError("source-union material regions differ from coverage")
    material_contract = source_proof.witnesses[0].material_contract_sha256
    if any(item.material_contract_sha256 != material_contract for item in bindings):
        raise ValueError("source-union material contract differs from coverage")
    target = build_adaptive_direct_source_union_target(
        source_proof=source_proof,
        coverage_manifest_sha256=coverage.coverage_manifest_sha256,
    )
    if target.image_count > _MAX_IMAGES_PER_SOURCE or target.image_count * len(eligible) > _MAX_IMAGES_PER_CANDIDATE:
        raise ValueError("source-union render bounds exceeded")
    revalidate_direct_source_snapshot(snapshot, cancel_event)
    reference_source = Path(snapshot.input_source_root) / Path(*request.source_relative_path.split("/"))
    candidate_source = Path(snapshot.source_root) / Path(*snapshot.output_relative_path.split("/"))
    workspace = Path(os.path.abspath(workspace))
    if os.path.lexists(workspace):
        raise ValueError("source-union workspace must not exist")
    if _has_reparse_ancestor(workspace.parent):
        raise ValueError("source-union workspace parent has a reparse ancestor")
    if _overlaps(workspace, snapshot.input_source_root) or _overlaps(workspace, snapshot.source_root):
        raise ValueError("source-union workspace overlaps source input")
    render_request = SourceUnionRenderRequest(
        target, reference_source, candidate_source, dependency_proof_sha256, bindings,
    )
    owned_workspace = False
    try:
        workspace.mkdir(parents=False, exist_ok=False)
        owned_workspace = True
        _cancel(cancel_event, "cancelled before source-union render")
        rendered = renderer(render_request, workspace, cancel_event)
        if not isinstance(rendered, SourceUnionRenderOutput):
            raise TypeError("source-union renderer returned invalid output")
        _cancel(cancel_event, "cancelled after source-union render")
        try:
            rendered.root.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("source-union render root escapes workspace") from exc
        if _is_reparse(rendered.root) or _has_reparse_ancestor(rendered.root):
            raise ValueError("source-union render root has a reparse point")
        files = _render_manifest(rendered.root, target, cancel_event)
        visibility = _visibility(target, rendered.observations)
        reference_dir = rendered.root / "source-union" / target.union_key / "reference"
        candidate_dir = rendered.root / "source-union" / target.union_key / "candidate"
        _cancel(cancel_event, "cancelled before source-union comparison")
        validation = comparator(reference_dir, candidate_dir, profile)
        if not isinstance(validation, ValidationResult):
            raise TypeError("source-union comparator returned invalid validation")
        _cancel(cancel_event, "cancelled after source-union comparison")
        if _render_manifest(rendered.root, target, cancel_event) != files:
            raise ValueError("source-union render bytes changed after comparison")
        revalidate_direct_source_snapshot(snapshot, cancel_event)
        return build_adaptive_direct_source_union_record(
            target=target, validation=validation, files=files, visibility=visibility,
        )
    except BaseException:
        if owned_workspace and os.path.lexists(workspace):
            _quarantine_cleanup(workspace)
        raise
