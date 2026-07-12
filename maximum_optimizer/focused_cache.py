from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import threading
import tempfile
import uuid
from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType

from maximum_optimizer.calibration_evidence import (
    TRUSTED_CALIBRATION_EVIDENCE_V3_SHA256,
)
from maximum_optimizer.domain import (
    FocusedRegionPolicy,
    FocusRegionResult,
    FocusTarget,
    GateFailure,
    ValidationResult,
)
from maximum_optimizer.reporting import canonical_json, canonical_payload, deep_freeze
from maximum_optimizer.regions import RegionDescriptor
from maximum_optimizer.processes import ProcessCancelledError
from maximum_optimizer.visual_validation import (
    EXPECTED_ANGLES,
    EXPECTED_PASSES,
    REQUIRED_METRICS,
    FidelityProfile,
    compare_render_sets,
)


_HASH = re.compile(r"^[0-9a-f]{64}$")
_REGION_KEY = re.compile(r"^r-[0-9a-f]{64}$")
_MAX_MATERIAL_FILES = 4096
_MAX_MATERIAL_BYTES = 2 * 1024 ** 3
_IO_CHUNK_SIZE = 1024 * 1024
_TOP_FIELDS = {
    "schema", "family_input_sha256", "candidate_cache_digest", "source_pairs",
    "region_descriptor", "target", "state", "region_manifest_sha256",
    "configuration_manifest_sha256", "whole_profile", "focused_profile",
    "trusted_evidence_v3_sha256", "selector_version", "renderer_version",
    "dependency_proof_sha256", "material_proof", "expected",
}


class _MaterialByteLimitError(ValueError):
    pass


def _exact(value: object, fields: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return value


def _hash(value: object, label: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} is invalid")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} is invalid")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise ValueError(f"{label} is invalid")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} is invalid")
    return number


def _relative(value: object, label: str) -> str:
    from pathlib import PurePosixPath, PureWindowsPath

    if type(value) is not str or not value or "\\" in value:
        raise ValueError(f"{label} is not a canonical relative path")
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if path.is_absolute() or windows.is_absolute() or windows.drive or ".." in path.parts:
        raise ValueError(f"{label} is not a canonical relative path")
    canonical = PurePosixPath(*(part for part in path.parts if part not in ("", "."))).as_posix()
    if canonical != value or canonical in ("", "."):
        raise ValueError(f"{label} is not a canonical relative path")
    return canonical


def _validate_profile(value: object, label: str) -> None:
    item = _exact(value, {"version", "corpus_hash", "profile_file_sha256", "limits"}, label)
    _text(item["version"], f"{label} version")
    _hash(item["corpus_hash"], f"{label} corpus")
    _hash(item["profile_file_sha256"], f"{label} file")
    limits = item["limits"]
    if type(limits) is not dict or set(limits) != set(REQUIRED_METRICS):
        raise ValueError(f"{label} limits are invalid")
    for name, number in limits.items():
        _number(number, f"{label} {name}")


def _validate_material_proof(value: object) -> None:
    fields = {
        "schema", "cacheable", "reason", "roots", "requests", "files",
        "resolutions", "total_files", "total_bytes", "digest",
    }
    proof = _exact(value, fields, "material proof")
    if proof["schema"] != 1 or proof["cacheable"] is not True or proof["reason"] != "ok":
        raise ValueError("material proof is not cacheable schema 1")
    _integer(proof["total_files"], "material file count")
    _integer(proof["total_bytes"], "material bytes")
    if proof["total_files"] > 4096 or proof["total_bytes"] > 2 * 1024 ** 3:
        raise ValueError("material proof exceeds bounds")
    roots = proof["roots"]
    requests = proof["requests"]
    files = proof["files"]
    resolutions = proof["resolutions"]
    if not all(type(items) is list for items in (roots, requests, files, resolutions)):
        raise ValueError("material proof lists are invalid")
    if not roots or not requests or len(files) != proof["total_files"]:
        raise ValueError("material proof cardinality is invalid")
    for index, root in enumerate(roots):
        root = _exact(root, {"root_index", "root_identity", "inventory_sha256"}, "material root")
        if root["root_index"] != index:
            raise ValueError("material roots are not canonical")
        _text(root["root_identity"], "material root identity")
        _hash(root["inventory_sha256"], "material inventory")
    for index, request in enumerate(requests):
        request = _exact(request, {"request_index", "material_identity", "search_paths"}, "material request")
        if request["request_index"] != index:
            raise ValueError("material requests are not canonical")
        _relative(request["material_identity"], "material identity")
        if type(request["search_paths"]) is not list or any(
            _relative(path, "material search path") != path for path in request["search_paths"]
        ):
            raise ValueError("material search paths are invalid")
    file_keys = []
    byte_count = 0
    for file in files:
        file = _exact(file, {"root_index", "path", "kind", "size", "sha256"}, "material file")
        root_index = _integer(file["root_index"], "material file root")
        if root_index >= len(roots) or file["kind"] not in {"vmt", "vtf"}:
            raise ValueError("material file is invalid")
        relative = _relative(file["path"], "material file path")
        size = _integer(file["size"], "material file size")
        _hash(file["sha256"], "material file hash")
        file_keys.append((root_index, relative.casefold(), relative))
        byte_count += size
    if file_keys != sorted(file_keys) or len({(a, b) for a, b, _ in file_keys}) != len(file_keys):
        raise ValueError("material files are not canonical")
    if byte_count != proof["total_bytes"]:
        raise ValueError("material byte total is invalid")
    file_by_key = {
        (item["root_index"], item["path"].casefold()): item for item in files
    }
    for root in roots:
        inventory_files = tuple(
            item for item in files if item["root_index"] == root["root_index"]
        )
        expected_inventory = hashlib.sha256(canonical_json({
            "root_index": root["root_index"],
            "root_identity": root["root_identity"],
            "files": inventory_files,
        }).encode("utf-8")).hexdigest()
        if root["inventory_sha256"] != expected_inventory:
            raise ValueError("material root inventory hash is invalid")
    for index, resolution in enumerate(resolutions):
        resolution = _exact(resolution, {
            "request_index", "material_identity", "state", "root_index",
            "search_path_index", "vmt_path", "vmt_sha256", "vtf_root_index",
            "vtf_path", "vtf_sha256", "shader", "texture_directive",
            "duplicate_root_directives",
        }, "material resolution")
        if resolution["request_index"] != index or index >= len(requests):
            raise ValueError("material resolutions are not canonical")
        if resolution["material_identity"] != requests[index]["material_identity"]:
            raise ValueError("material resolution identity mismatch")
        if type(resolution["duplicate_root_directives"]) is not list:
            raise ValueError("duplicate directive audit is invalid")
        directives = resolution["duplicate_root_directives"]
        directive_names = []
        for audit in directives:
            audit = _exact(audit, {"directive", "ignored_values"}, "duplicate directive")
            directive = _text(audit["directive"], "duplicate directive name")
            if directive != directive.casefold() or type(audit["ignored_values"]) is not list or not audit["ignored_values"] or any(type(item) is not str for item in audit["ignored_values"]):
                raise ValueError("duplicate directive audit is invalid")
            directive_names.append(directive)
        if directive_names != sorted(set(directive_names)):
            raise ValueError("duplicate directive audit is not canonical")
        if resolution["state"] == "resolved":
            for field in ("root_index", "search_path_index", "vtf_root_index"):
                _integer(resolution[field], f"material resolution {field}")
            for field in ("vmt_path", "vtf_path"):
                _relative(resolution[field], f"material resolution {field}")
            for field in ("vmt_sha256", "vtf_sha256"):
                _hash(resolution[field], f"material resolution {field}")
            _text(resolution["shader"], "material shader")
            _text(resolution["texture_directive"], "material directive")
            if resolution["root_index"] >= len(roots) or resolution["vtf_root_index"] >= len(roots):
                raise ValueError("material resolution root index is invalid")
            if resolution["search_path_index"] >= max(1, len(requests[index]["search_paths"])):
                raise ValueError("material resolution search path index is invalid")
            vmt = file_by_key.get((resolution["root_index"], resolution["vmt_path"].casefold()))
            vtf = file_by_key.get((resolution["vtf_root_index"], resolution["vtf_path"].casefold()))
            if (
                vmt is None or vmt["kind"] != "vmt" or vmt["sha256"] != resolution["vmt_sha256"]
                or vtf is None or vtf["kind"] != "vtf" or vtf["sha256"] != resolution["vtf_sha256"]
            ):
                raise ValueError("material resolution does not match file inventory")
        elif resolution["state"] == "missing":
            optional = (
                "root_index", "search_path_index", "vmt_path", "vmt_sha256",
                "vtf_root_index", "vtf_path", "vtf_sha256", "shader",
                "texture_directive",
            )
            if any(resolution[field] is not None for field in optional) or resolution["duplicate_root_directives"]:
                raise ValueError("missing material resolution must have null details")
        else:
            raise ValueError("material resolution state is invalid")
    if len(resolutions) != len(requests):
        raise ValueError("material resolution cardinality is invalid")
    supplied = _hash(proof["digest"], "material proof digest")
    unsealed = dict(proof)
    del unsealed["digest"]
    actual = hashlib.sha256(canonical_json(unsealed).encode("utf-8")).hexdigest()
    if supplied != actual:
        raise ValueError("material proof digest mismatch")


def _validate_cache_payload(value: object) -> dict:
    payload = _exact(value, _TOP_FIELDS, "focus cache context")
    if payload["schema"] != 1:
        raise ValueError("focus cache schema must be 1")
    for field in (
        "family_input_sha256", "candidate_cache_digest", "region_manifest_sha256",
        "configuration_manifest_sha256", "dependency_proof_sha256",
    ):
        _hash(payload[field], field)
    if payload["trusted_evidence_v3_sha256"] != TRUSTED_CALIBRATION_EVIDENCE_V3_SHA256:
        raise ValueError("focus cache context does not use trusted evidence v3")
    _text(payload["selector_version"], "selector version")
    _text(payload["renderer_version"], "renderer version")
    pairs = payload["source_pairs"]
    if type(pairs) is not list or not pairs:
        raise ValueError("source pairs are invalid")
    canonical_pairs = []
    for pair in pairs:
        if type(pair) is not list or len(pair) != 3:
            raise ValueError("source pair is invalid")
        canonical_pairs.append((_relative(pair[0], "source identity"), _hash(pair[1], "original source"), _hash(pair[2], "candidate source")))
    if canonical_pairs != sorted(canonical_pairs) or len({item[0].casefold() for item in canonical_pairs}) != len(canonical_pairs):
        raise ValueError("source pairs are not canonical")
    descriptor = _exact(payload["region_descriptor"], {
        "descriptor_schema", "local_ordinal", "materials", "object_name", "source_identity",
    }, "region descriptor")
    if descriptor["descriptor_schema"] != "maximum-region-descriptor-v1":
        raise ValueError("region descriptor schema is invalid")
    _integer(descriptor["local_ordinal"], "region ordinal")
    _text(descriptor["object_name"], "region object")
    _relative(descriptor["source_identity"], "region source")
    if type(descriptor["materials"]) is not list or descriptor["materials"] != sorted(set(descriptor["materials"])):
        raise ValueError("region materials are not canonical")
    target = _exact(payload["target"], {
        "rank", "region_key", "source_identity", "state_index", "state_name",
        "bodygroups", "lod_index", "anchor_pose", "surface_bidirectional_p95",
        "surface_max", "normalized_p95", "normalized_max", "selector_input_sha256",
    }, "focus target")
    _integer(target["rank"], "target rank")
    if type(target["region_key"]) is not str or _REGION_KEY.fullmatch(target["region_key"]) is None:
        raise ValueError("target region key is invalid")
    _relative(target["source_identity"], "target source")
    _integer(target["state_index"], "target state")
    _text(target["state_name"], "target state name")
    _integer(target["lod_index"], "target LOD")
    _text(target["anchor_pose"], "target pose")
    for field in ("surface_bidirectional_p95", "surface_max", "normalized_p95", "normalized_max"):
        _number(target[field], field)
    _hash(target["selector_input_sha256"], "selector input")
    state = _exact(payload["state"], {
        "state_index", "state_name", "bodygroups", "lod_index", "poses",
        "selected_pose", "selected_frame", "animation_state", "animation_sha256",
    }, "focus state")
    for field in ("state_index", "lod_index", "selected_frame"):
        _integer(state[field], f"state {field}")
    _text(state["state_name"], "state name")
    bodygroups = state["bodygroups"]
    if type(bodygroups) is not list or any(type(item) is not list or len(item) != 2 or type(item[0]) is not str or type(item[1]) is not int for item in bodygroups):
        raise ValueError("state bodygroups are invalid")
    if bodygroups != sorted(bodygroups, key=lambda item: (item[0].casefold(), item[0])):
        raise ValueError("state bodygroups are not canonical")
    poses = state["poses"]
    if type(poses) is not list or not poses or len(poses) > 2 or poses[0] != "bind" or len(set(poses)) != len(poses):
        raise ValueError("state poses are invalid")
    if state["selected_pose"] not in poses:
        raise ValueError("selected pose is invalid")
    if state["animation_state"] == "none":
        if state["animation_sha256"] is not None:
            raise ValueError("none animation cannot have a hash")
    elif state["animation_state"] == "source":
        _hash(state["animation_sha256"], "animation source")
    else:
        raise ValueError("animation state is invalid")
    if target["state_index"] != state["state_index"] or target["state_name"] != state["state_name"] or target["lod_index"] != state["lod_index"] or target["bodygroups"] != bodygroups:
        raise ValueError("target and state differ")
    if target["region_key"] != payload["expected"]["region_key"] or target["source_identity"] != descriptor["source_identity"]:
        raise ValueError("target context differs")
    _validate_profile(payload["whole_profile"], "whole profile")
    _validate_profile(payload["focused_profile"], "focused profile")
    _validate_material_proof(payload["material_proof"])
    expected = _exact(payload["expected"], {
        "region_key", "poses", "passes", "angles", "width", "height",
        "reference_count", "candidate_count",
    }, "expected matrix")
    if expected["region_key"] != target["region_key"] or expected["poses"] != poses:
        raise ValueError("expected matrix target differs")
    if tuple(expected["passes"]) != EXPECTED_PASSES or tuple(expected["angles"]) != EXPECTED_ANGLES:
        raise ValueError("expected render matrix is invalid")
    _integer(expected["width"], "expected width", minimum=1)
    _integer(expected["height"], "expected height", minimum=1)
    count = len(poses) * len(EXPECTED_PASSES) * len(EXPECTED_ANGLES)
    if expected["reference_count"] != count or expected["candidate_count"] != count:
        raise ValueError("expected cardinality is invalid")
    return payload


@dataclass(frozen=True)
class FocusCacheKey:
    digest: str

    def __post_init__(self) -> None:
        _hash(self.digest, "focus cache digest")

    @classmethod
    def build(cls, payload: Mapping[str, object]) -> "FocusCacheKey":
        if not isinstance(payload, Mapping):
            raise TypeError("focus cache payload must be a mapping")
        validated = _validate_cache_payload(json.loads(canonical_json(payload)))
        return cls(hashlib.sha256(canonical_json(validated).encode("utf-8")).hexdigest())


@dataclass(frozen=True)
class DuplicateDirectiveProof:
    directive: str
    ignored_values: tuple[str, ...]


@dataclass(frozen=True)
class MaterialRootProof:
    root_index: int
    root_identity: str
    inventory_sha256: str | None


@dataclass(frozen=True)
class MaterialRequestProof:
    request_index: int
    material_identity: str
    search_paths: tuple[str, ...]


@dataclass(frozen=True)
class MaterialFileProof:
    root_index: int
    path: str
    kind: str
    size: int
    sha256: str


@dataclass(frozen=True)
class MaterialRequestResolution:
    request_index: int
    material_identity: str
    state: str
    root_index: int | None
    search_path_index: int | None
    vmt_path: str | None
    vmt_sha256: str | None
    vtf_root_index: int | None
    vtf_path: str | None
    vtf_sha256: str | None
    shader: str | None
    texture_directive: str | None
    duplicate_root_directives: tuple[DuplicateDirectiveProof, ...]


@dataclass(frozen=True)
class MaterialResolutionProof:
    schema: int
    cacheable: bool
    reason: str
    roots: tuple[MaterialRootProof, ...]
    requests: tuple[MaterialRequestProof, ...]
    files: tuple[MaterialFileProof, ...]
    resolutions: tuple[MaterialRequestResolution, ...]
    total_files: int
    total_bytes: int
    digest: str


@dataclass(frozen=True)
class FocusRenderDirectories:
    reference: Path
    candidate: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference", Path(self.reference))
        object.__setattr__(self, "candidate", Path(self.candidate))


@dataclass(frozen=True)
class RenderFileProof:
    side: str
    kind: str
    path: str
    size: int
    sha256: str
    width: int | None
    height: int | None

    def __post_init__(self) -> None:
        if self.side not in {"reference", "candidate"}:
            raise ValueError("render file side is invalid")
        if self.kind not in {"manifest", "image"}:
            raise ValueError("render file kind is invalid")
        _relative(self.path, "render file path")
        _integer(self.size, "render file size")
        _hash(self.sha256, "render file hash")
        if self.kind == "manifest":
            if self.path != "render_manifest.json" or self.width is not None or self.height is not None:
                raise ValueError("render manifest proof is invalid")
        elif (
            type(self.width) is not int or self.width < 1
            or type(self.height) is not int or self.height < 1
            or not self.path.casefold().endswith(".png")
        ):
            raise ValueError("render image proof is invalid")


@dataclass(frozen=True)
class FocusCacheMetadata:
    schema: int
    context: Mapping[str, object]
    target: Mapping[str, object]
    expected: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.schema != 1:
            raise ValueError("focus cache metadata schema must be 1")
        copied_context = json.loads(canonical_json(self.context))
        _validate_cache_payload(copied_context)
        copied_target = json.loads(canonical_json(self.target))
        copied_expected = json.loads(canonical_json(self.expected))
        if copied_target != copied_context["target"] or copied_expected != copied_context["expected"]:
            raise ValueError("focus cache metadata differs from context")
        object.__setattr__(self, "context", deep_freeze(copied_context))
        object.__setattr__(self, "target", deep_freeze(copied_target))
        object.__setattr__(self, "expected", deep_freeze(copied_expected))


@dataclass(frozen=True)
class FocusExpectedMatrix:
    region_key: str
    poses: tuple[str, ...]
    passes: tuple[str, ...]
    angles: tuple[str, ...]
    width: int
    height: int
    reference_count: int
    candidate_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "poses", tuple(self.poses))
        object.__setattr__(self, "passes", tuple(self.passes))
        object.__setattr__(self, "angles", tuple(self.angles))


@dataclass(frozen=True)
class FocusProfileProof:
    version: str
    corpus_hash: str
    profile_file_sha256: str
    limits: Mapping[str, float]

    def __post_init__(self) -> None:
        copied = json.loads(canonical_json(self.limits))
        _validate_profile({
            "version": self.version,
            "corpus_hash": self.corpus_hash,
            "profile_file_sha256": self.profile_file_sha256,
            "limits": copied,
        }, "focus profile proof")
        object.__setattr__(self, "limits", deep_freeze(copied))


@dataclass(frozen=True)
class FocusStateProof:
    state_index: int
    state_name: str
    bodygroups: tuple[tuple[str, int], ...]
    lod_index: int
    poses: tuple[str, ...]
    selected_pose: str
    selected_frame: int
    animation_state: str
    animation_sha256: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "bodygroups", tuple(tuple(item) for item in self.bodygroups))
        object.__setattr__(self, "poses", tuple(self.poses))


@dataclass(frozen=True)
class FocusCacheContext:
    schema: int
    family_input_sha256: str
    candidate_cache_digest: str
    source_pairs: tuple[tuple[str, str, str], ...]
    region_descriptor: RegionDescriptor
    target: FocusTarget
    state: FocusStateProof
    region_manifest_sha256: str
    configuration_manifest_sha256: str
    whole_profile: FocusProfileProof
    focused_profile: FocusProfileProof
    trusted_evidence_v3_sha256: str
    selector_version: str
    renderer_version: str
    dependency_proof_sha256: str
    material_proof: object
    expected: FocusExpectedMatrix

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_pairs", tuple(tuple(item) for item in self.source_pairs))
        if not isinstance(self.region_descriptor, RegionDescriptor):
            raise TypeError("focus cache region descriptor is invalid")
        if not isinstance(self.target, FocusTarget) or not isinstance(self.state, FocusStateProof):
            raise TypeError("focus cache target/state is invalid")
        if not isinstance(self.whole_profile, FocusProfileProof) or not isinstance(self.focused_profile, FocusProfileProof):
            raise TypeError("focus cache profile proofs are invalid")
        if not isinstance(self.expected, FocusExpectedMatrix):
            raise TypeError("focus cache expected matrix is invalid")
        payload = self._mutable_payload()
        _validate_cache_payload(payload)
        object.__setattr__(self, "material_proof", deep_freeze(payload["material_proof"]))

    def _mutable_payload(self) -> dict[str, object]:
        return json.loads(canonical_json({
            "schema": self.schema,
            "family_input_sha256": self.family_input_sha256,
            "candidate_cache_digest": self.candidate_cache_digest,
            "source_pairs": self.source_pairs,
            "region_descriptor": self.region_descriptor.canonical_payload(),
            "target": self.target,
            "state": self.state,
            "region_manifest_sha256": self.region_manifest_sha256,
            "configuration_manifest_sha256": self.configuration_manifest_sha256,
            "whole_profile": self.whole_profile,
            "focused_profile": self.focused_profile,
            "trusted_evidence_v3_sha256": self.trusted_evidence_v3_sha256,
            "selector_version": self.selector_version,
            "renderer_version": self.renderer_version,
            "dependency_proof_sha256": self.dependency_proof_sha256,
            "material_proof": self.material_proof,
            "expected": self.expected,
        }))

    def to_payload(self) -> Mapping[str, object]:
        return deep_freeze(self._mutable_payload())


@dataclass(frozen=True)
class FocusedEvidenceContext:
    schema: int
    family_id: str
    candidate_id: str
    policy: FocusedRegionPolicy
    whole_profile: Mapping[str, object]
    focused_profile: Mapping[str, object]
    trusted_evidence_v3_sha256: str
    dependency_proof_sha256: str
    material_proofs: Mapping[str, Mapping[str, object]]

    def __post_init__(self) -> None:
        if self.schema != 1:
            raise ValueError("focused evidence context schema must be 1")
        _text(self.family_id, "focused evidence family")
        _text(self.candidate_id, "focused evidence candidate")
        if not isinstance(self.policy, FocusedRegionPolicy):
            raise TypeError("focused evidence policy is invalid")
        whole = json.loads(canonical_json(self.whole_profile))
        focused = json.loads(canonical_json(self.focused_profile))
        _validate_profile(whole, "whole profile")
        _validate_profile(focused, "focused profile")
        if self.trusted_evidence_v3_sha256 != TRUSTED_CALIBRATION_EVIDENCE_V3_SHA256:
            raise ValueError("focused evidence does not use trusted evidence v3")
        _hash(self.dependency_proof_sha256, "focused dependency proof")
        if not isinstance(self.material_proofs, Mapping) or not self.material_proofs:
            raise ValueError("focused material proofs are required")
        material: dict[str, object] = {}
        for key, proof in sorted(self.material_proofs.items()):
            if type(key) is not str or _REGION_KEY.fullmatch(key) is None:
                raise ValueError("focused material proof key is invalid")
            copied = json.loads(canonical_json(proof))
            _validate_material_proof(copied)
            material[key] = copied
        object.__setattr__(self, "whole_profile", deep_freeze(whole))
        object.__setattr__(self, "focused_profile", deep_freeze(focused))
        object.__setattr__(self, "material_proofs", deep_freeze(material))


@dataclass(frozen=True)
class FocusedRenderEvidence:
    target: FocusTarget
    terminal_status: str
    expected: Mapping[str, object]
    reference_manifest: str
    reference_manifest_sha256: str
    candidate_manifest: str
    candidate_manifest_sha256: str
    files: tuple[RenderFileProof, ...]
    material_proof_sha256: str
    validation: ValidationResult
    cache_hit: bool
    evidence_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected", deep_freeze(json.loads(canonical_json(self.expected))))
        object.__setattr__(self, "files", tuple(self.files))


def _cancel(cancel_event: threading.Event | None, message: str) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCancelledError(message)


def _is_reparse(path: Path) -> bool:
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
    )


def _has_reparse_ancestor(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    return any(_is_reparse(item) for item in (*reversed(absolute.parents), absolute))


def _opened_file_identity(info) -> tuple[int, int, int, int]:
    return (
        int(info.st_dev), int(info.st_ino), int(info.st_size),
        int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1e9))),
    )


def _open_regular_no_follow(path: Path, *, contained_root: Path | None = None):
    path = Path(path)
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION),
        ]
        kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ]
        kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        handle = kernel32.CreateFileW(
            str(path), 0x80000000, 0x00000007, None, 3,
            0x00200000 | 0x08000000, None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (0, -1, invalid):
            raise OSError(ctypes.get_last_error(), f"cannot open file without following links: {path}")
        try:
            information = BY_HANDLE_FILE_INFORMATION()
            if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
                raise OSError(ctypes.get_last_error(), f"cannot inspect opened file: {path}")
            if information.dwFileAttributes & (0x400 | 0x10):
                raise ValueError(f"opened file is a reparse point or directory: {path}")
            if contained_root is not None:
                buffer = ctypes.create_unicode_buffer(32768)
                length = kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
                if not length or length >= len(buffer):
                    raise OSError(ctypes.get_last_error(), f"cannot resolve opened file handle: {path}")
                final_name = buffer.value
                if final_name.startswith("\\\\?\\UNC\\"):
                    final_name = "\\\\" + final_name[8:]
                elif final_name.startswith("\\\\?\\"):
                    final_name = final_name[4:]
                final_path = Path(final_name)
                root = Path(contained_root).resolve(strict=True)
                try:
                    final_path.relative_to(root)
                except ValueError as exc:
                    raise ValueError("opened file handle escapes contained root") from exc
            descriptor = msvcrt.open_osfhandle(
                int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0)
            )
            handle = None
            stream = os.fdopen(descriptor, "rb", closefd=True)
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                stream.close()
                raise ValueError("opened handle is not a regular file")
            return stream, _opened_file_identity(info)
        finally:
            if handle not in (None, 0, -1, invalid):
                kernel32.CloseHandle(handle)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("platform cannot open files without following links")
    descriptor = os.open(path, flags | nofollow)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("opened handle is not a regular file")
        if contained_root is not None:
            proc = Path(f"/proc/self/fd/{descriptor}")
            if not proc.exists():
                raise ValueError("platform cannot prove opened handle containment")
            final_path = proc.resolve(strict=True)
            try:
                final_path.relative_to(Path(contained_root).resolve(strict=True))
            except ValueError as exc:
                raise ValueError("opened file handle escapes contained root") from exc
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        return stream, _opened_file_identity(info)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _file_proof(
    path: Path,
    cancel_event: threading.Event | None = None,
    *,
    chunk_size: int = _IO_CHUNK_SIZE,
    max_bytes: int | None = None,
    contained_root: Path | None = None,
    capture_stream=None,
) -> tuple[int, str]:
    _cancel(cancel_event, "cancelled before material hash")
    digest = hashlib.sha256()
    size = 0
    stream, before_identity = _open_regular_no_follow(
        Path(path), contained_root=contained_root
    )
    if max_bytes is not None and before_identity[2] > max_bytes:
        stream.close()
        raise _MaterialByteLimitError("file exceeds byte limit")
    with stream:
        while True:
            _cancel(cancel_event, "cancelled during material hash")
            if max_bytes is not None and size >= max_bytes:
                break
            request_size = chunk_size
            if max_bytes is not None:
                request_size = min(request_size, max_bytes - size)
            block = stream.read(request_size)
            if not block:
                break
            digest.update(block)
            if capture_stream is not None:
                capture_stream.write(block)
            size += len(block)
        after_identity = _opened_file_identity(os.fstat(stream.fileno()))
    if max_bytes is not None and after_identity[2] > max_bytes:
        raise _MaterialByteLimitError("file exceeds byte limit")
    if before_identity != after_identity or size != after_identity[2]:
        raise ValueError("material file changed while hashing")
    if capture_stream is not None:
        capture_stream.flush()
        capture_stream.seek(0)
    _cancel(cancel_event, "cancelled after material hash")
    return size, digest.hexdigest()


def _material_digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _uncacheable_material_proof(
    reason: str,
    roots: tuple[tuple[str, Path], ...],
    requests: tuple[MaterialRequestProof, ...],
    total_files: int,
    total_bytes: int,
) -> MaterialResolutionProof:
    root_proofs = tuple(
        MaterialRootProof(index, identity, None)
        for index, (identity, _path) in enumerate(roots)
    )
    unsealed = {
        "schema": 1,
        "cacheable": False,
        "reason": reason,
        "roots": root_proofs,
        "requests": requests,
        "files": (),
        "resolutions": (),
        "total_files": total_files,
        "total_bytes": total_bytes,
    }
    return MaterialResolutionProof(
        1, False, reason, root_proofs, requests, (), (), total_files, total_bytes,
        _material_digest(unsealed),
    )


def material_resolution_proof(
    roots,
    requests,
    cancel_event: threading.Event | None,
) -> MaterialResolutionProof:
    _cancel(cancel_event, "cancelled before material proof")
    if isinstance(roots, (str, bytes)) or not roots:
        raise ValueError("material roots are required")
    canonical_roots: list[tuple[str, Path]] = []
    root_identities: set[str] = set()
    for raw in roots:
        if type(raw) not in (tuple, list) or len(raw) != 2:
            raise ValueError("material root must be an identity/path pair")
        identity = _text(raw[0], "material root identity")
        if identity in root_identities:
            raise ValueError("material root identity is duplicated")
        root_identities.add(identity)
        canonical_roots.append((identity, Path(raw[1])))
    canonical_roots_tuple = tuple(canonical_roots)
    request_proofs: list[MaterialRequestProof] = []
    for index, raw in enumerate(requests):
        request = _exact(raw, {"material_identity", "search_paths"}, "material request")
        identity = _relative(request["material_identity"], "material identity")
        raw_paths = request["search_paths"]
        if type(raw_paths) not in (tuple, list):
            raise ValueError("material search paths are invalid")
        paths = tuple(_relative(item, "material search path") for item in raw_paths)
        if len(set(path.casefold() for path in paths)) != len(paths):
            raise ValueError("material search paths are duplicated")
        request_proofs.append(MaterialRequestProof(index, identity, paths))
    if not request_proofs:
        raise ValueError("material requests are required")
    request_tuple = tuple(request_proofs)

    candidates: list[tuple[int, Path, str, str, int]] = []
    discovered_bytes = 0
    try:
        for root_index, (_identity, root) in enumerate(canonical_roots_tuple):
            _cancel(cancel_event, "cancelled during material traversal")
            if _has_reparse_ancestor(root) or not root.is_dir():
                return _uncacheable_material_proof(
                    "unsafe-tree", canonical_roots_tuple, request_tuple, 0, 0
                )
            for directory, directory_names, file_names in os.walk(root, followlinks=False):
                _cancel(cancel_event, "cancelled during material traversal")
                parent = Path(directory)
                directory_names.sort(key=str.casefold)
                for name in directory_names:
                    if _is_reparse(parent / name):
                        return _uncacheable_material_proof(
                            "unsafe-tree", canonical_roots_tuple, request_tuple, 0, 0
                        )
                for name in sorted(file_names, key=str.casefold):
                    path = parent / name
                    suffix = path.suffix.casefold().lstrip(".")
                    if suffix not in {"vmt", "vtf"}:
                        continue
                    if len(candidates) >= _MAX_MATERIAL_FILES:
                        return _uncacheable_material_proof(
                            "file-limit", canonical_roots_tuple, request_tuple,
                            len(candidates), discovered_bytes,
                        )
                    info = path.lstat()
                    if _is_reparse(path) or not stat.S_ISREG(info.st_mode):
                        return _uncacheable_material_proof(
                            "unsafe-tree", canonical_roots_tuple, request_tuple, 0, 0
                        )
                    if discovered_bytes + info.st_size > _MAX_MATERIAL_BYTES:
                        return _uncacheable_material_proof(
                            "byte-limit", canonical_roots_tuple, request_tuple,
                            len(candidates), discovered_bytes,
                        )
                    relative = path.relative_to(root).as_posix()
                    candidates.append((root_index, path, relative.casefold(), relative, info.st_size))
                    discovered_bytes += info.st_size
    except OSError:
        return _uncacheable_material_proof(
            "io-error", canonical_roots_tuple, request_tuple, 0, 0
        )
    candidates.sort(key=lambda item: (item[0], item[2], item[3]))
    if len({(item[0], item[2]) for item in candidates}) != len(candidates):
        return _uncacheable_material_proof(
            "unsafe-tree", canonical_roots_tuple, request_tuple, 0, 0
        )
    candidate_by_key = {
        (item[0], item[2]): item for item in candidates
    }
    selected_vmt: dict[int, tuple[int, int, str] | None] = {}
    selected_vmt_keys: set[tuple[int, str]] = set()
    for request in request_tuple:
        identity = request.material_identity
        if identity.casefold().endswith(".vmt"):
            identity = identity[:-4]
        normalized = PurePosixPath(identity)
        search_candidates = (
            tuple((PurePosixPath(path) / normalized).as_posix() for path in request.search_paths)
            if request.search_paths and len(normalized.parts) == 1
            else (normalized.as_posix(),)
        )
        selected = None
        for root_index in range(len(canonical_roots_tuple)):
            for search_index, relative in enumerate(search_candidates):
                key = (root_index, f"{relative}.vmt".casefold())
                file = candidate_by_key.get(key)
                if file is not None:
                    selected = (root_index, search_index, file[3])
                    selected_vmt_keys.add((root_index, file[3].casefold()))
                    break
            if selected is not None:
                break
        selected_vmt[request.request_index] = selected
    files: list[MaterialFileProof] = []
    captured_vmt = {}
    total_bytes = 0
    for root_index, path, _folded, relative, stat_size in candidates:
        _cancel(cancel_event, "cancelled during material inventory")
        if len(files) >= _MAX_MATERIAL_FILES:
            return _uncacheable_material_proof(
                "file-limit", canonical_roots_tuple, request_tuple,
                len(files), total_bytes,
            )
        if total_bytes + stat_size > _MAX_MATERIAL_BYTES:
            return _uncacheable_material_proof(
                "byte-limit", canonical_roots_tuple, request_tuple,
                len(files), total_bytes,
            )
        try:
            capture = None
            if (root_index, relative.casefold()) in selected_vmt_keys:
                capture = tempfile.SpooledTemporaryFile(max_size=_IO_CHUNK_SIZE, mode="w+b")
            size, digest = _file_proof(
                path, cancel_event,
                max_bytes=_MAX_MATERIAL_BYTES - total_bytes,
                contained_root=canonical_roots_tuple[root_index][1],
                capture_stream=capture,
            )
            if capture is not None:
                captured_vmt[(root_index, relative.casefold())] = capture
        except _MaterialByteLimitError:
            if capture is not None:
                capture.close()
            for stream in captured_vmt.values():
                stream.close()
            return _uncacheable_material_proof(
                "byte-limit", canonical_roots_tuple, request_tuple,
                len(files), total_bytes,
            )
        except OSError:
            if capture is not None:
                capture.close()
            for stream in captured_vmt.values():
                stream.close()
            return _uncacheable_material_proof(
                "io-error", canonical_roots_tuple, request_tuple,
                len(files), total_bytes,
            )
        except ValueError:
            if capture is not None:
                capture.close()
            for stream in captured_vmt.values():
                stream.close()
            return _uncacheable_material_proof(
                "unsafe-tree", canonical_roots_tuple, request_tuple,
                len(files), total_bytes,
            )
        except ProcessCancelledError:
            if capture is not None:
                capture.close()
            for stream in captured_vmt.values():
                stream.close()
            raise
        files.append(MaterialFileProof(root_index, relative, path.suffix.casefold()[1:], size, digest))
        total_bytes += size

    file_map = {(item.root_index, item.path.casefold()): item for item in files}
    resolutions: list[MaterialRequestResolution] = []
    import render_previews
    for request in request_tuple:
        try:
            _cancel(cancel_event, "cancelled during material resolution")
        except ProcessCancelledError:
            for stream in captured_vmt.values():
                stream.close()
            raise
        selected = selected_vmt[request.request_index]
        if selected is None:
            resolutions.append(MaterialRequestResolution(
                request.request_index, request.material_identity, "missing",
                None, None, None, None, None, None, None, None, None, (),
            ))
            continue
        vmt_root, search_index, vmt_relative = selected
        stream = captured_vmt[(vmt_root, vmt_relative.casefold())]
        stream.seek(0)
        vmt_text = stream.read().decode("utf-8", errors="replace")
        parsed = render_previews._parse_vmt_root(vmt_text)
        texture_reference = render_previews._source_texture_reference(vmt_text)
        if parsed is None or texture_reference is None:
            resolutions.append(MaterialRequestResolution(
                request.request_index, request.material_identity, "missing",
                None, None, None, None, None, None, None, None, None, (),
            ))
            continue
        shader, texture_directive, base_texture, _uses_alpha = texture_reference
        normalized_texture = base_texture.replace("\\", "/").strip()
        texture_path = PurePosixPath(normalized_texture)
        if (
            not normalized_texture or texture_path.is_absolute()
            or PureWindowsPath(normalized_texture).is_absolute()
            or PureWindowsPath(normalized_texture).drive
            or ".." in texture_path.parts
        ):
            resolutions.append(MaterialRequestResolution(
                request.request_index, request.material_identity, "missing",
                None, None, None, None, None, None, None, None, None, (),
            ))
            continue
        vtf_file = None
        for root_index in range(len(canonical_roots_tuple)):
            candidate = file_map.get((root_index, f"{texture_path.as_posix()}.vtf".casefold()))
            if candidate is not None and candidate.kind == "vtf":
                vtf_file = candidate
                break
        if vtf_file is None:
            resolutions.append(MaterialRequestResolution(
                request.request_index, request.material_identity, "missing",
                None, None, None, None, None, None, None, None, None, (),
            ))
            continue
        vtf_root = vtf_file.root_index
        vtf_relative = vtf_file.path
        vmt = file_map[(vmt_root, vmt_relative.casefold())]
        vtf = vtf_file
        duplicates = tuple(
            DuplicateDirectiveProof(directive, tuple(values))
            for directive, values in sorted(parsed[2].items())
        )
        resolutions.append(MaterialRequestResolution(
            request.request_index, request.material_identity, "resolved",
            vmt_root, search_index, vmt_relative, vmt.sha256,
            vtf_root, vtf_relative, vtf.sha256, shader,
            texture_directive, duplicates,
        ))
    for stream in captured_vmt.values():
        stream.close()
    root_proofs = []
    for root_index, (identity, _root) in enumerate(canonical_roots_tuple):
        inventory = tuple(item for item in files if item.root_index == root_index)
        root_proofs.append(MaterialRootProof(root_index, identity, _material_digest({
            "root_index": root_index, "root_identity": identity, "files": inventory,
        })))
    unsealed = {
        "schema": 1, "cacheable": True, "reason": "ok",
        "roots": tuple(root_proofs), "requests": request_tuple,
        "files": tuple(files), "resolutions": tuple(resolutions),
        "total_files": len(files), "total_bytes": total_bytes,
    }
    return MaterialResolutionProof(
        1, True, "ok", tuple(root_proofs), request_tuple, tuple(files),
        tuple(resolutions), len(files), total_bytes, _material_digest(unsealed),
    )


def _write_json_fsync(path: Path, payload: object) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical_json(payload))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _assert_safe_tree(root: Path) -> None:
    root = Path(root)
    if _is_reparse(root) or not root.is_dir():
        raise ValueError(f"render tree is a reparse or missing directory: {root}")
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in directory_names:
            child = parent / name
            if _is_reparse(child):
                raise ValueError(f"render tree contains a reparse directory: {child}")
        for name in file_names:
            child = parent / name
            if _is_reparse(child) or not stat.S_ISREG(child.lstat().st_mode):
                raise ValueError(f"render tree contains a reparse or special file: {child}")


def _render_file_manifest(
    directories: FocusRenderDirectories,
    cancel_event: threading.Event | None,
) -> tuple[RenderFileProof, ...]:
    from PIL import Image, UnidentifiedImageError

    result: list[RenderFileProof] = []
    folded: set[tuple[str, str]] = set()
    for side, root in (("reference", directories.reference), ("candidate", directories.candidate)):
        _cancel(cancel_event, "cancelled before render manifest")
        _assert_safe_tree(root)
        for path in sorted(
            (item for item in root.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(root).as_posix(),
        ):
            _cancel(cancel_event, "cancelled during render manifest")
            relative = path.relative_to(root).as_posix()
            key = (side, relative.casefold())
            if key in folded:
                raise ValueError("render tree contains case-colliding paths")
            folded.add(key)
            size, digest = _file_proof(path, cancel_event, contained_root=root)
            if relative == "render_manifest.json":
                kind, width, height = "manifest", None, None
            elif relative.casefold().endswith(".png"):
                try:
                    with Image.open(path) as image:
                        image.load()
                        width, height = image.size
                except (OSError, ValueError, UnidentifiedImageError) as exc:
                    raise ValueError("render image is corrupt") from exc
                kind = "image"
            else:
                raise ValueError("render tree contains an unexpected file")
            result.append(RenderFileProof(side, kind, relative, size, digest, width, height))
    return tuple(result)


def _validate_render_files(
    files: tuple[RenderFileProof, ...],
    expected: Mapping[str, object],
) -> None:
    canonical = tuple(sorted(files, key=lambda item: (
        0 if item.side == "reference" else 1, item.path.casefold(), item.path,
    )))
    if files != canonical:
        raise ValueError("render file proofs are not canonical")
    for side, count_field in (("reference", "reference_count"), ("candidate", "candidate_count")):
        side_files = tuple(item for item in files if item.side == side)
        manifests = tuple(item for item in side_files if item.kind == "manifest")
        images = tuple(item for item in side_files if item.kind == "image")
        if len(manifests) != 1 or len(images) != expected[count_field]:
            raise ValueError("render file cardinality differs from expected matrix")
        if any(item.width != expected["width"] or item.height != expected["height"] for item in images):
            raise ValueError("render image dimensions differ from expected matrix")


def _copy_file_no_follow(
    source: Path,
    destination: Path,
    cancel_event: threading.Event | None,
    *,
    contained_root: Path | None = None,
) -> None:
    _cancel(cancel_event, "cancelled before cache file copy")
    size = 0
    reader, before_identity = _open_regular_no_follow(
        source, contained_root=contained_root
    )
    with reader, destination.open("xb") as writer:
        while True:
            _cancel(cancel_event, "cancelled during cache file copy")
            block = reader.read(_IO_CHUNK_SIZE)
            if not block:
                break
            writer.write(block)
            size += len(block)
        writer.flush()
        os.fsync(writer.fileno())
        after_identity = _opened_file_identity(os.fstat(reader.fileno()))
    if (
        before_identity != after_identity
        or size != after_identity[2]
    ):
        raise ValueError("cache copy source changed while reading")


def _copy_render_tree(
    source: FocusRenderDirectories,
    destination: FocusRenderDirectories,
    cancel_event: threading.Event | None,
) -> None:
    for source_root, destination_root in (
        (source.reference, destination.reference),
        (source.candidate, destination.candidate),
    ):
        _assert_safe_tree(source_root)
        destination_root.mkdir(parents=True, exist_ok=False)
        for directory, directory_names, file_names in os.walk(source_root, followlinks=False):
            _cancel(cancel_event, "cancelled during cache tree copy")
            parent = Path(directory)
            target_parent = destination_root / parent.relative_to(source_root)
            for name in sorted(directory_names):
                child = parent / name
                if _is_reparse(child):
                    raise ValueError("cache copy source contains a reparse directory")
                (target_parent / name).mkdir()
            for name in sorted(file_names):
                _copy_file_no_follow(
                    parent / name, target_parent / name, cancel_event,
                    contained_root=source_root,
                )
            _fsync_directory(target_parent)


def _marker_files(files: tuple[RenderFileProof, ...]) -> list[dict[str, object]]:
    return [
        {
            "path": f"{item.side}/{item.path}",
            "size": item.size,
            "sha256": item.sha256,
        }
        for item in files
    ]


def _metadata_payload(metadata: FocusCacheMetadata) -> dict[str, object]:
    return {
        "schema": metadata.schema,
        "context": canonical_payload(metadata.context),
        "target": canonical_payload(metadata.target),
        "expected": canonical_payload(metadata.expected),
    }


def _remove_owned_tree(path: Path, parent: Path) -> None:
    if path.parent.resolve(strict=True) != parent.resolve(strict=True):
        raise ValueError("cache cleanup path escapes its parent")
    if _is_reparse(path):
        raise ValueError("refusing to remove cache reparse point")
    if path.exists():
        _assert_safe_tree(path)
        shutil.rmtree(path)


def _path_identity(path: Path) -> tuple[int, int]:
    info = Path(path).lstat()
    return int(info.st_dev), int(info.st_ino)


class FocusedRenderCache:
    def __init__(self, root: os.PathLike[str] | str):
        self.root = Path(root)

    def _entry(self, key: FocusCacheKey) -> Path:
        return self.root / key.digest

    def _validate_entry(
        self,
        key: FocusCacheKey,
        cancel_event: threading.Event | None,
    ) -> tuple[FocusCacheMetadata, tuple[RenderFileProof, ...]] | None:
        final = self._entry(key)
        try:
            if _is_reparse(final) or not final.is_dir():
                return None
            if set(item.name for item in final.iterdir()) != {"complete.json", "metadata.json", "payload"}:
                return None
            if any(_is_reparse(path) for path in (final / "complete.json", final / "metadata.json", final / "payload")):
                return None
            if set(item.name for item in (final / "payload").iterdir()) != {"reference", "candidate"}:
                return None
            marker = json.loads((final / "complete.json").read_text(encoding="utf-8"))
            if type(marker) is not dict or set(marker) != {
                "schema", "key_digest", "metadata_sha256", "expected_file_count", "files",
            }:
                return None
            if marker["schema"] != 1 or marker["key_digest"] != key.digest:
                return None
            metadata_bytes = (final / "metadata.json").read_bytes()
            if hashlib.sha256(metadata_bytes).hexdigest() != marker["metadata_sha256"]:
                return None
            raw_metadata = json.loads(metadata_bytes.decode("utf-8"))
            if type(raw_metadata) is not dict or set(raw_metadata) != {"schema", "context", "target", "expected"}:
                return None
            metadata = FocusCacheMetadata(
                raw_metadata["schema"], raw_metadata["context"],
                raw_metadata["target"], raw_metadata["expected"],
            )
            if FocusCacheKey.build(metadata.context) != key:
                return None
            directories = FocusRenderDirectories(
                final / "payload/reference", final / "payload/candidate"
            )
            actual = _render_file_manifest(directories, cancel_event)
            _validate_render_files(actual, metadata.expected)
            if marker["expected_file_count"] != len(actual) or marker["files"] != _marker_files(actual):
                return None
            return metadata, actual
        except ProcessCancelledError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def store(
        self,
        key: FocusCacheKey,
        source: FocusRenderDirectories,
        metadata: FocusCacheMetadata,
        expected_files,
        cancel_event: threading.Event | None,
    ) -> FocusRenderDirectories:
        if not isinstance(key, FocusCacheKey) or not isinstance(source, FocusRenderDirectories) or not isinstance(metadata, FocusCacheMetadata):
            raise TypeError("focused cache arguments are invalid")
        if FocusCacheKey.build(metadata.context) != key:
            raise ValueError("focused cache metadata does not match key")
        _cancel(cancel_event, "cancelled before focus cache store")
        expected_tuple = tuple(expected_files)
        if any(not isinstance(item, RenderFileProof) for item in expected_tuple):
            raise TypeError("expected render files are invalid")
        _validate_render_files(expected_tuple, metadata.expected)
        actual_source = _render_file_manifest(source, cancel_event)
        if actual_source != expected_tuple:
            raise ValueError("fresh render files differ from expected proof")
        if _has_reparse_ancestor(self.root.parent):
            raise ValueError("focused cache root has a reparse ancestor")
        self.root.mkdir(parents=True, exist_ok=True)
        if _is_reparse(self.root) or not self.root.is_dir():
            raise ValueError("focused cache root is unsafe")
        existing = self._validate_entry(key, cancel_event)
        if existing is not None:
            final = self._entry(key)
            return FocusRenderDirectories(final / "payload/reference", final / "payload/candidate")
        staging = self.root / f"{key.digest}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
        staging.mkdir()
        quarantine: Path | None = None
        try:
            staged_dirs = FocusRenderDirectories(
                staging / "payload/reference", staging / "payload/candidate"
            )
            (staging / "payload").mkdir()
            _copy_render_tree(source, staged_dirs, cancel_event)
            copied = _render_file_manifest(staged_dirs, cancel_event)
            if copied != expected_tuple:
                raise ValueError("copied render files differ from expected proof")
            metadata_path = staging / "metadata.json"
            _write_json_fsync(metadata_path, _metadata_payload(metadata))
            metadata_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
            _cancel(cancel_event, "cancelled before focus cache marker")
            _write_json_fsync(staging / "complete.json", {
                "schema": 1,
                "key_digest": key.digest,
                "metadata_sha256": metadata_hash,
                "expected_file_count": len(copied),
                "files": _marker_files(copied),
            })
            _fsync_directory(staging / "payload")
            _fsync_directory(staging)
            _cancel(cancel_event, "cancelled before focus cache promotion")
            final = self._entry(key)
            staging_identity = _path_identity(staging)
            concurrent = self._validate_entry(key, cancel_event)
            if concurrent is not None:
                _remove_owned_tree(staging, self.root)
                return FocusRenderDirectories(
                    final / "payload/reference", final / "payload/candidate"
                )
            if os.path.lexists(final):
                if _is_reparse(final):
                    raise ValueError("focused cache final is a reparse point")
                quarantine = self.root / f"{key.digest}.quarantine-{uuid.uuid4().hex}"
                os.replace(final, quarantine)
                os.replace(quarantine, final)
                if self._validate_entry(key, cancel_event) is not None:
                    _remove_owned_tree(staging, self.root)
                    return FocusRenderDirectories(
                        final / "payload/reference", final / "payload/candidate"
                    )
                os.replace(final, quarantine)
            try:
                os.replace(staging, final)
                if _path_identity(final) != staging_identity:
                    if self._validate_entry(key, None) is not None:
                        _cancel(cancel_event, "cancelled after concurrent cache winner")
                        if quarantine is not None:
                            try:
                                _remove_owned_tree(quarantine, self.root)
                            except (OSError, ValueError):
                                pass
                            quarantine = None
                        return FocusRenderDirectories(
                            final / "payload/reference", final / "payload/candidate"
                        )
                    raise ValueError("focus cache destination changed during promotion")
                _cancel(cancel_event, "cancelled after focus cache promotion")
                _fsync_directory(self.root)
            except BaseException:
                final_exists = os.path.lexists(final) and not _is_reparse(final)
                final_is_ours = final_exists and _path_identity(final) == staging_identity
                if final_is_ours:
                    _remove_owned_tree(final, self.root)
                    final_exists = False
                concurrent_valid = final_exists and self._validate_entry(key, None) is not None
                if quarantine is not None:
                    if not final_exists:
                        os.replace(quarantine, final)
                        quarantine = None
                        _fsync_directory(self.root)
                    elif concurrent_valid:
                        try:
                            _remove_owned_tree(quarantine, self.root)
                            quarantine = None
                        except (OSError, ValueError):
                            pass
                raise
            if quarantine is not None:
                try:
                    _remove_owned_tree(quarantine, self.root)
                except (OSError, ValueError):
                    pass
            return FocusRenderDirectories(final / "payload/reference", final / "payload/candidate")
        except BaseException:
            if staging.exists() and not _is_reparse(staging):
                try:
                    _remove_owned_tree(staging, self.root)
                except (OSError, ValueError):
                    pass
            raise

    def lookup(
        self,
        key: FocusCacheKey,
        snapshot_root: os.PathLike[str] | str,
        cancel_event: threading.Event | None,
    ) -> FocusRenderDirectories | None:
        validated = self._validate_entry(key, cancel_event)
        if validated is None:
            return None
        metadata, expected_files = validated
        snapshot = Path(snapshot_root)
        if os.path.lexists(snapshot):
            raise ValueError("focused cache snapshot destination must not exist")
        if _has_reparse_ancestor(snapshot.parent):
            raise ValueError("focused cache snapshot has a reparse ancestor")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.mkdir()
        try:
            final = self._entry(key)
            source = FocusRenderDirectories(
                final / "payload/reference", final / "payload/candidate"
            )
            destination = FocusRenderDirectories(
                snapshot / "reference", snapshot / "candidate"
            )
            _copy_render_tree(source, destination, cancel_event)
            copied = _render_file_manifest(destination, cancel_event)
            _validate_render_files(copied, metadata.expected)
            if copied != expected_files:
                raise ValueError("focused cache changed while materializing snapshot")
            return destination
        except ProcessCancelledError:
            try:
                _remove_owned_tree(snapshot, snapshot.parent)
            except (OSError, ValueError):
                pass
            raise
        except (OSError, ValueError):
            try:
                _remove_owned_tree(snapshot, snapshot.parent)
            except (OSError, ValueError):
                pass
            return None

    def invalidate(self, key: FocusCacheKey) -> bool:
        final = self._entry(key)
        if _is_reparse(final):
            raise ValueError("focused cache entry is a reparse point")
        if not final.exists():
            return False
        if final.parent.resolve(strict=True) != self.root.resolve(strict=True):
            raise ValueError("focused cache entry escapes root")
        _remove_owned_tree(final, self.root)
        return True


def _validate_validation(value: ValidationResult, terminal_status: str) -> None:
    if not isinstance(value, ValidationResult) or type(value.passed) is not bool:
        raise ValueError("focused validation result is invalid")
    if terminal_status not in {"passed", "failed", "cancelled"}:
        raise ValueError("focused terminal status is invalid")
    expected_passed = terminal_status == "passed"
    if value.passed != expected_passed:
        raise ValueError("focused terminal status and validation differ")
    if value.passed != (len(value.failures) == 0):
        raise ValueError("focused validation pass/failure fields are incoherent")
    if any(not isinstance(item, GateFailure) for item in value.failures):
        raise ValueError("focused validation failure is invalid")
    for name, number in value.metrics.items():
        if type(name) is not str or isinstance(number, bool) or type(number) not in (int, float):
            raise ValueError("focused validation metric is invalid")
        if not math.isfinite(float(number)) or float(number) < 0:
            raise ValueError("focused validation metric is invalid")
    if type(value.worst_scope) is not str:
        raise ValueError("focused validation worst scope is invalid")


def _record_payload(
    record: FocusedRenderEvidence,
    *,
    include_cache: bool,
    include_seal: bool,
) -> dict[str, object]:
    payload = {
        "target": record.target,
        "terminal_status": record.terminal_status,
        "expected": record.expected,
        "reference_manifest": record.reference_manifest,
        "reference_manifest_sha256": record.reference_manifest_sha256,
        "candidate_manifest": record.candidate_manifest,
        "candidate_manifest_sha256": record.candidate_manifest_sha256,
        "files": record.files,
        "material_proof_sha256": record.material_proof_sha256,
        "validation": record.validation,
    }
    if include_cache:
        payload["cache_hit"] = record.cache_hit
    if include_seal:
        payload["evidence_sha256"] = record.evidence_sha256
    return canonical_payload(payload)


def _record_digest(record: FocusedRenderEvidence) -> str:
    return hashlib.sha256(
        canonical_json(_record_payload(record, include_cache=True, include_seal=False)).encode("utf-8")
    ).hexdigest()


def _validate_focused_render_record(
    record: FocusedRenderEvidence,
    *,
    verify_seal: bool,
) -> None:
    if not isinstance(record, FocusedRenderEvidence) or not isinstance(record.target, FocusTarget):
        raise ValueError("focused render record type is invalid")
    expected = json.loads(canonical_json(record.expected))
    expected = _exact(expected, {
        "region_key", "poses", "passes", "angles", "width", "height",
        "reference_count", "candidate_count",
    }, "focused record expected matrix")
    if expected["region_key"] != record.target.region_key:
        raise ValueError("focused record expected region differs from target")
    poses = expected["poses"]
    if (
        type(poses) is not list or not poses or len(poses) > 2
        or poses[0] != "bind" or len(set(poses)) != len(poses)
        or record.target.anchor_pose not in poses
    ):
        raise ValueError("focused record poses are invalid")
    if tuple(expected["passes"]) != EXPECTED_PASSES or tuple(expected["angles"]) != EXPECTED_ANGLES:
        raise ValueError("focused record matrix is invalid")
    _integer(expected["width"], "focused record width", minimum=1)
    _integer(expected["height"], "focused record height", minimum=1)
    count = len(poses) * len(EXPECTED_PASSES) * len(EXPECTED_ANGLES)
    if expected["reference_count"] != count or expected["candidate_count"] != count:
        raise ValueError("focused record cardinality is invalid")
    if type(record.cache_hit) is not bool:
        raise ValueError("focused record cache diagnostic is invalid")
    _hash(record.material_proof_sha256, "focused record material proof")
    _hash(record.reference_manifest_sha256, "focused reference manifest")
    _hash(record.candidate_manifest_sha256, "focused candidate manifest")
    _relative(record.reference_manifest, "focused reference manifest path")
    _relative(record.candidate_manifest, "focused candidate manifest path")
    files = tuple(record.files)
    if any(not isinstance(item, RenderFileProof) for item in files):
        raise ValueError("focused record file proof is invalid")
    _validate_render_files(files, expected)
    reference = next(
        (item for item in files if item.side == "reference" and item.kind == "manifest"),
        None,
    )
    candidate = next(
        (item for item in files if item.side == "candidate" and item.kind == "manifest"),
        None,
    )
    if (
        reference is None or candidate is None
        or record.reference_manifest != f"reference/{reference.path}"
        or record.candidate_manifest != f"candidate/{candidate.path}"
        or record.reference_manifest_sha256 != reference.sha256
        or record.candidate_manifest_sha256 != candidate.sha256
    ):
        raise ValueError("focused record manifest proof is inconsistent")
    _validate_validation(record.validation, record.terminal_status)
    if verify_seal:
        _hash(record.evidence_sha256, "focused record evidence")
        if _record_digest(record) != record.evidence_sha256:
            raise ValueError("focused record seal is invalid")


def build_focused_render_evidence(
    target: FocusTarget,
    validation: ValidationResult,
    expected: Mapping[str, object],
    files,
    material_proof_sha256: str,
    cache_hit: bool,
) -> FocusedRenderEvidence:
    if not isinstance(target, FocusTarget):
        raise TypeError("focused evidence target is invalid")
    expected_copy = json.loads(canonical_json(expected))
    exact_expected = _exact(expected_copy, {
        "region_key", "poses", "passes", "angles", "width", "height",
        "reference_count", "candidate_count",
    }, "focused expected matrix")
    if exact_expected["region_key"] != target.region_key:
        raise ValueError("focused expected matrix targets another region")
    file_tuple = tuple(files)
    if any(not isinstance(item, RenderFileProof) for item in file_tuple):
        raise TypeError("focused render file proof is invalid")
    _validate_render_files(file_tuple, exact_expected)
    _hash(material_proof_sha256, "focused material proof")
    if type(cache_hit) is not bool:
        raise ValueError("focused cache diagnostic is invalid")
    terminal = "passed" if validation.passed else "failed"
    _validate_validation(validation, terminal)
    reference_manifest = next(
        (item for item in file_tuple if item.side == "reference" and item.kind == "manifest"),
        None,
    )
    candidate_manifest = next(
        (item for item in file_tuple if item.side == "candidate" and item.kind == "manifest"),
        None,
    )
    if reference_manifest is None or candidate_manifest is None:
        raise ValueError("focused render manifests are missing")
    unsealed = FocusedRenderEvidence(
        target, terminal, exact_expected,
        f"reference/{reference_manifest.path}", reference_manifest.sha256,
        f"candidate/{candidate_manifest.path}", candidate_manifest.sha256,
        file_tuple, material_proof_sha256, validation, cache_hit, "0" * 64,
    )
    _validate_focused_render_record(unsealed, verify_seal=False)
    result = FocusedRenderEvidence(
        target, terminal, exact_expected,
        unsealed.reference_manifest, unsealed.reference_manifest_sha256,
        unsealed.candidate_manifest, unsealed.candidate_manifest_sha256,
        file_tuple, material_proof_sha256, validation, cache_hit,
        _record_digest(unsealed),
    )
    _validate_focused_render_record(result, verify_seal=True)
    return result


def validate_focused_target(
    cache: FocusedRenderCache,
    key: FocusCacheKey,
    target: FocusTarget,
    profile: FidelityProfile,
    render_fresh,
    snapshot_root: os.PathLike[str] | str,
    metadata: FocusCacheMetadata,
    expected_files,
    cancel_event: threading.Event | None,
    *,
    comparator=compare_render_sets,
) -> tuple[FocusRegionResult, FocusedRenderEvidence]:
    if not isinstance(cache, FocusedRenderCache) or not isinstance(profile, FidelityProfile):
        raise TypeError("focused validation arguments are invalid")
    _cancel(cancel_event, "cancelled before focused cache lookup")
    directories = cache.lookup(key, snapshot_root, cancel_event)
    cache_hit = directories is not None
    if directories is None:
        _cancel(cancel_event, "cancelled before focused render")
        directories = render_fresh()
        if not isinstance(directories, FocusRenderDirectories):
            raise TypeError("focused renderer returned invalid directories")
        _cancel(cancel_event, "cancelled after focused render")
        try:
            cache.store(key, directories, metadata, expected_files, cancel_event)
        except ProcessCancelledError:
            raise
        except (OSError, ValueError):
            pass
    _cancel(cancel_event, "cancelled before focused comparison")
    validation = comparator(directories.reference, directories.candidate, profile)
    if not isinstance(validation, ValidationResult):
        raise TypeError("focused comparator returned an invalid result")
    _cancel(cancel_event, "cancelled after focused comparison")
    files = _render_file_manifest(directories, cancel_event)
    material_digest = str(metadata.context["material_proof"]["digest"])
    record = build_focused_render_evidence(
        target, validation, metadata.expected, files, material_digest, cache_hit,
    )
    return FocusRegionResult(target, validation, record.evidence_sha256, cache_hit), record


def _context_payload(context: FocusedEvidenceContext) -> dict[str, object]:
    return canonical_payload({
        "schema": context.schema,
        "policy": context.policy,
        "whole_profile": context.whole_profile,
        "focused_profile": context.focused_profile,
        "trusted_evidence_v3_sha256": context.trusted_evidence_v3_sha256,
        "dependency_proof_sha256": context.dependency_proof_sha256,
        "material_proofs": context.material_proofs,
    })


def focused_gate_evidence_payload(
    context: FocusedEvidenceContext,
    selection,
    records,
    recoveries=(),
) -> Mapping[str, object]:
    from maximum_optimizer.focused_regions import FocusSelection

    if not isinstance(context, FocusedEvidenceContext) or not isinstance(selection, FocusSelection):
        raise TypeError("focused gate evidence arguments are invalid")
    if tuple(recoveries):
        raise ValueError("focused evidence schema 1 does not accept recovery records")
    eligible = tuple(selection.eligible_ranking)
    selected = tuple(selection.selected)
    if not eligible or tuple(item.rank for item in eligible) != tuple(range(len(eligible))):
        raise ValueError("focused eligible ranking is invalid")
    if len({item.region_key for item in eligible}) != len(eligible):
        raise ValueError("focused eligible ranking contains duplicate regions")
    if any(
        not isinstance(item, FocusTarget)
        or item.selector_input_sha256 != selection.selector_input_sha256
        for item in eligible
    ):
        raise ValueError("focused eligible ranking selector proof is invalid")
    expected_selected = eligible[:min(context.policy.top_k, len(eligible))]
    if selected != expected_selected:
        raise ValueError("focused selected prefix is invalid")
    record_tuple = tuple(records)
    if len(record_tuple) != len(selected):
        raise ValueError("focused record cardinality differs from selected targets")
    if any(not isinstance(record, FocusedRenderEvidence) for record in record_tuple):
        raise TypeError("focused record is invalid")
    if tuple(record.target for record in record_tuple) != selected:
        raise ValueError("focused record targets differ from selection")
    if set(context.material_proofs) != {item.region_key for item in selected}:
        raise ValueError("focused material proof cardinality differs from selection")
    for record in record_tuple:
        _validate_focused_render_record(record, verify_seal=True)
        proof = context.material_proofs[record.target.region_key]
        if record.material_proof_sha256 != proof["digest"]:
            raise ValueError("focused record material proof differs from context")
        _relative(record.reference_manifest, "focused reference manifest")
        _relative(record.candidate_manifest, "focused candidate manifest")
        if not record.reference_manifest.startswith("reference/") or not record.candidate_manifest.startswith("candidate/"):
            raise ValueError("focused manifest side is invalid")

    context_payload = _context_payload(context)
    selection_payload = canonical_payload(selection)
    authorization_records = tuple(
        _record_payload(record, include_cache=False, include_seal=False)
        for record in record_tuple
    )
    authorization_payload = {
        "schema": 1,
        "family_id": context.family_id,
        "candidate_id": context.candidate_id,
        "context": context_payload,
        "selection": selection_payload,
        "records": authorization_records,
        "recoveries": (),
    }
    authorization_sha256 = hashlib.sha256(
        canonical_json(authorization_payload).encode("utf-8")
    ).hexdigest()
    outer = {
        "schema": 1,
        "family_id": context.family_id,
        "candidate_id": context.candidate_id,
        "context": context_payload,
        "selection": selection_payload,
        "records": tuple(
            _record_payload(record, include_cache=True, include_seal=True)
            for record in record_tuple
        ),
        "recoveries": (),
        "authorization_sha256": authorization_sha256,
    }
    outer["evidence_sha256"] = hashlib.sha256(
        canonical_json(outer).encode("utf-8")
    ).hexdigest()
    return deep_freeze(outer)
