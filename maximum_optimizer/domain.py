from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
import re
from types import MappingProxyType
from typing import Literal

EngineName = Literal["fidelity", "blender", "meshoptimizer"]
FamilyStatus = Literal["optimized", "preserved", "failed", "cancelled"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REGION_KEY_RE = re.compile(r"^r-[0-9a-f]{64}$")
_SOURCE_FILE_LIMIT = 4096
_SOURCE_BYTE_LIMIT = 2 * 1024 ** 3
_COMPILE_FILE_LIMIT = 64
_COMPILE_BYTE_LIMIT = 2 * 1024 ** 3
_ADAPTIVE_SOURCE_LIMIT = 8
_ADAPTIVE_OCCURRENCE_LIMIT = 4096
_ADAPTIVE_COMPONENT_LIMIT = 256
_ADAPTIVE_MATERIAL_LIMIT = 256
_ADAPTIVE_STATE_LIMIT = 16
_ADAPTIVE_POSE_LIMIT = 2
_COVERAGE_OCCURRENCE_FIELDS = {
    "occurrence_key", "source_identity", "graph_relative_path", "directive", "line",
    "state_key", "bodygroup_key", "lod_key", "skin_key", "source_size", "source_sha256",
    "component_manifest_sha256", "material_contract_sha256", "skeleton_contract_sha256",
    "pose_contract_sha256", "equivalence_class_sha256", "status", "evidence_sha256",
}


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _require_text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} is invalid")
    return value


def _require_relative(value: object, label: str) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise ValueError(f"{label} is not a canonical relative path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise ValueError(f"{label} is not a canonical relative path")
    canonical = PurePosixPath(*(part for part in posix.parts if part not in ("", "."))).as_posix()
    if canonical != value or canonical in ("", "."):
        raise ValueError(f"{label} is not a canonical relative path")
    return value


def require_canonical_relative(value: object, label: str) -> str:
    """Shared strict POSIX/Windows-safe relative identity validator."""
    return _require_relative(value, label)


def _require_size(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} is invalid")
    return value


def _require_ratio(value: object, label: str) -> float:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise ValueError(f"{label} is invalid")
    result = float(value)
    if not math.isfinite(result) or not 0 < result <= 1:
        raise ValueError(f"{label} is invalid")
    return result


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _seal(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _unsealed(cls, **values):
    result = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result


def _canonical_text_tuple(
    values: object, label: str, *, limit: int | None = None, required: bool = True
) -> tuple[str, ...]:
    result = tuple(values) if isinstance(values, (tuple, list)) else ()
    if (required and not result) or any(type(item) is not str or not item for item in result):
        raise ValueError(f"{label} is invalid")
    if result != tuple(sorted(result, key=lambda item: (item.casefold(), item))):
        raise ValueError(f"{label} is not canonical")
    if len({item.casefold() for item in result}) != len(result):
        raise ValueError(f"{label} is not unique")
    if limit is not None and len(result) > limit:
        raise ValueError(f"{label} exceeds bound")
    return result


def _focused_ref_payload(value: "FocusedEvidenceRef") -> dict[str, object]:
    return {"region_key": value.region_key, "evidence_sha256": value.evidence_sha256}


@dataclass(frozen=True)
class SourceFileProof:
    file_identity: str
    kind: Literal["qc", "visual-source", "animation-source", "physics-source", "auxiliary"]
    relative_path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        _require_relative(self.file_identity, "source file identity")
        if self.kind not in {"qc", "visual-source", "animation-source", "physics-source", "auxiliary"}:
            raise ValueError("source file kind is invalid")
        _require_relative(self.relative_path, "source file path")
        _require_size(self.size, "source file size")
        _require_sha256(self.sha256, "source file hash")


def _source_file_payload(value: SourceFileProof) -> dict[str, object]:
    return {
        "file_identity": value.file_identity, "kind": value.kind,
        "relative_path": value.relative_path, "size": value.size, "sha256": value.sha256,
    }


@dataclass(frozen=True)
class SourceTreeManifest:
    schema: int
    root_identity: str
    files: tuple[SourceFileProof, ...]
    total_files: int
    total_bytes: int
    digest: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1:
            raise ValueError("source manifest schema must be 1")
        _require_text(self.root_identity, "source manifest root identity")
        files = tuple(self.files)
        if any(not isinstance(item, SourceFileProof) for item in files):
            raise TypeError("source manifest files are invalid")
        keys = [(item.file_identity.casefold(), item.file_identity) for item in files]
        paths = [item.relative_path.casefold() for item in files]
        if keys != sorted(keys) or len({key[0] for key in keys}) != len(keys) or len(set(paths)) != len(paths):
            raise ValueError("source manifest files are not canonical and unique")
        if len(files) > _SOURCE_FILE_LIMIT:
            raise ValueError("source manifest exceeds file bound")
        if type(self.total_files) is not int or self.total_files != len(files):
            raise ValueError("source manifest file total mismatch")
        byte_total = sum(item.size for item in files)
        if type(self.total_bytes) is not int or self.total_bytes != byte_total or byte_total > _SOURCE_BYTE_LIMIT:
            raise ValueError("source manifest byte total mismatch or bound exceeded")
        payload = {
            "schema": self.schema, "root_identity": self.root_identity,
            "files": [_source_file_payload(item) for item in files],
            "total_files": self.total_files, "total_bytes": self.total_bytes,
        }
        if _require_sha256(self.digest, "source manifest digest") != _seal(payload):
            raise ValueError("source manifest digest mismatch")
        object.__setattr__(self, "files", files)


def source_tree_manifest_payload(value: SourceTreeManifest) -> dict[str, object]:
    return {
        "schema": value.schema, "root_identity": value.root_identity,
        "files": [_source_file_payload(item) for item in value.files],
        "total_files": value.total_files, "total_bytes": value.total_bytes,
        "digest": value.digest,
    }


@dataclass(frozen=True)
class FocusedEvidenceRef:
    region_key: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        if type(self.region_key) is not str or _REGION_KEY_RE.fullmatch(self.region_key) is None:
            raise ValueError("focused evidence region key is invalid")
        _require_sha256(self.evidence_sha256, "focused evidence hash")


@dataclass(frozen=True)
class RecoverySourceSnapshot:
    schema: int
    kind: Literal["candidate", "original"]
    family_id: str
    family_input_sha256: str
    optimizer_contract_sha256: str
    whole_profile_sha256: str
    focused_profile_sha256: str
    dependency_proof_sha256: str
    candidate_id: str | None
    candidate_cache_digest: str | None
    source_root: Path
    source_manifest: SourceTreeManifest
    focused_evidence: tuple[FocusedEvidenceRef, ...]
    snapshot_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1 or self.kind not in {"candidate", "original"}:
            raise ValueError("recovery snapshot schema or kind is invalid")
        for label, value in (
            ("family id", self.family_id), ("family input", self.family_input_sha256),
            ("optimizer contract", self.optimizer_contract_sha256),
            ("whole profile", self.whole_profile_sha256),
            ("focused profile", self.focused_profile_sha256),
            ("dependency proof", self.dependency_proof_sha256),
        ):
            _require_sha256(value, label)
        root = Path(self.source_root)
        if not root.is_absolute():
            raise ValueError("recovery snapshot source root must be absolute")
        if not isinstance(self.source_manifest, SourceTreeManifest):
            raise TypeError("recovery snapshot source manifest is invalid")
        refs = tuple(self.focused_evidence)
        ref_keys = [(item.region_key.casefold(), item.region_key) for item in refs]
        if any(not isinstance(item, FocusedEvidenceRef) for item in refs) or ref_keys != sorted(ref_keys) or len({key[0] for key in ref_keys}) != len(ref_keys):
            raise ValueError("recovery snapshot focused evidence is not canonical")
        if self.kind == "candidate":
            _require_text(self.candidate_id, "snapshot candidate id")
            _require_sha256(self.candidate_cache_digest, "snapshot candidate cache digest")
        elif self.candidate_id is not None or self.candidate_cache_digest is not None or refs:
            raise ValueError("original snapshot forbids candidate identity and focused evidence")
        payload = recovery_source_snapshot_payload(self, include_seal=False)
        if _require_sha256(self.snapshot_sha256, "recovery snapshot hash") != _seal(payload):
            raise ValueError("recovery snapshot hash mismatch")
        object.__setattr__(self, "source_root", root)
        object.__setattr__(self, "focused_evidence", refs)


def recovery_source_snapshot_payload(value: RecoverySourceSnapshot, *, include_seal: bool = True) -> dict[str, object]:
    payload = {
        "schema": value.schema, "kind": value.kind, "family_id": value.family_id,
        "family_input_sha256": value.family_input_sha256,
        "optimizer_contract_sha256": value.optimizer_contract_sha256,
        "whole_profile_sha256": value.whole_profile_sha256,
        "focused_profile_sha256": value.focused_profile_sha256,
        "dependency_proof_sha256": value.dependency_proof_sha256,
        "candidate_id": value.candidate_id, "candidate_cache_digest": value.candidate_cache_digest,
        "source_manifest": source_tree_manifest_payload(value.source_manifest),
        "focused_evidence": [_focused_ref_payload(item) for item in value.focused_evidence],
    }
    if include_seal:
        payload["snapshot_sha256"] = value.snapshot_sha256
    return payload


def recovery_source_snapshot_from_payload(
    value: object, *, source_root: Path
) -> RecoverySourceSnapshot:
    if type(value) is not dict or set(value) != {
        "schema", "kind", "family_id", "family_input_sha256",
        "optimizer_contract_sha256", "whole_profile_sha256",
        "focused_profile_sha256", "dependency_proof_sha256", "candidate_id",
        "candidate_cache_digest", "source_manifest", "focused_evidence",
        "snapshot_sha256",
    }:
        raise ValueError("recovery snapshot payload fields are invalid")
    manifest_raw = value["source_manifest"]
    if type(manifest_raw) is not dict or set(manifest_raw) != {
        "schema", "root_identity", "files", "total_files", "total_bytes", "digest",
    } or type(manifest_raw["files"]) is not list:
        raise ValueError("recovery source manifest payload is invalid")
    files = []
    for raw in manifest_raw["files"]:
        if type(raw) is not dict or set(raw) != {
            "file_identity", "kind", "relative_path", "size", "sha256",
        }:
            raise ValueError("recovery source file payload is invalid")
        files.append(SourceFileProof(**raw))
    manifest = SourceTreeManifest(
        manifest_raw["schema"], manifest_raw["root_identity"], tuple(files),
        manifest_raw["total_files"], manifest_raw["total_bytes"], manifest_raw["digest"],
    )
    refs_raw = value["focused_evidence"]
    if type(refs_raw) is not list:
        raise ValueError("recovery focused evidence payload is invalid")
    refs = []
    for raw in refs_raw:
        if type(raw) is not dict or set(raw) != {"region_key", "evidence_sha256"}:
            raise ValueError("recovery focused evidence ref is invalid")
        refs.append(FocusedEvidenceRef(**raw))
    return RecoverySourceSnapshot(
        value["schema"], value["kind"], value["family_id"],
        value["family_input_sha256"], value["optimizer_contract_sha256"],
        value["whole_profile_sha256"], value["focused_profile_sha256"],
        value["dependency_proof_sha256"], value["candidate_id"],
        value["candidate_cache_digest"], Path(source_root), manifest, tuple(refs),
        value["snapshot_sha256"],
    )


@dataclass(frozen=True)
class SourceOverlay:
    source_identity: str
    mode: Literal["donor", "exact-original", "direct-position"]
    motivating_region_key: str | None
    base_source_sha256: str
    replacement_sha256: str
    replacement_size: int
    replacement_snapshot_sha256: str
    replacement_candidate_id: str | None
    replacement_cache_digest: str | None
    effective_ratio: float | None
    focused_evidence: tuple[FocusedEvidenceRef, ...]
    reason: str | None

    def __post_init__(self) -> None:
        _require_relative(self.source_identity, "overlay source identity")
        _require_sha256(self.base_source_sha256, "overlay base hash")
        _require_sha256(self.replacement_sha256, "overlay replacement hash")
        _require_size(self.replacement_size, "overlay replacement size")
        _require_sha256(self.replacement_snapshot_sha256, "overlay snapshot hash")
        refs = tuple(self.focused_evidence)
        keys = [(item.region_key.casefold(), item.region_key) for item in refs]
        if any(not isinstance(item, FocusedEvidenceRef) for item in refs) or keys != sorted(keys) or len({key[0] for key in keys}) != len(keys):
            raise ValueError("overlay focused evidence is not canonical")
        if self.mode == "donor":
            _require_text(self.replacement_candidate_id, "donor candidate id")
            _require_sha256(self.replacement_cache_digest, "donor cache digest")
            _require_ratio(self.effective_ratio, "donor effective ratio")
            if self.motivating_region_key is None or _REGION_KEY_RE.fullmatch(self.motivating_region_key) is None or not refs or self.motivating_region_key not in {item.region_key for item in refs} or self.reason is not None:
                raise ValueError("donor overlay mode matrix is invalid")
        elif self.mode == "exact-original":
            if self.motivating_region_key is None or _REGION_KEY_RE.fullmatch(self.motivating_region_key) is None or self.replacement_candidate_id is not None or self.replacement_cache_digest is not None or self.effective_ratio is not None or refs or self.reason != "donors-exhausted-v1":
                raise ValueError("exact-original overlay mode matrix is invalid")
        elif self.mode == "direct-position":
            _require_text(self.replacement_candidate_id, "direct candidate id")
            _require_sha256(self.replacement_cache_digest, "direct cache digest")
            _require_ratio(self.effective_ratio, "direct ratio")
            if self.motivating_region_key is not None or refs or self.reason != "approved-direct-position-v1":
                raise ValueError("direct-position overlay mode matrix is invalid")
        else:
            raise ValueError("overlay mode is invalid")
        object.__setattr__(self, "focused_evidence", refs)


def source_overlay_payload(value: SourceOverlay) -> dict[str, object]:
    return {
        "source_identity": value.source_identity, "mode": value.mode,
        "motivating_region_key": value.motivating_region_key,
        "base_source_sha256": value.base_source_sha256,
        "replacement_sha256": value.replacement_sha256,
        "replacement_size": value.replacement_size,
        "replacement_snapshot_sha256": value.replacement_snapshot_sha256,
        "replacement_candidate_id": value.replacement_candidate_id,
        "replacement_cache_digest": value.replacement_cache_digest,
        "effective_ratio": value.effective_ratio,
        "focused_evidence": [_focused_ref_payload(item) for item in value.focused_evidence],
        "reason": value.reason,
    }


@dataclass(frozen=True)
class CompositeRecipe:
    schema: int
    kind: Literal["focused-recovery-v1", "adaptive-direct-fallback-v1"]
    family_id: str
    family_input_sha256: str
    base_candidate_id: str
    base_spec_sha256: str
    base_cache_digest: str
    base_source_manifest_sha256: str
    optimizer_contract_sha256: str
    whole_profile_sha256: str
    focused_profile_sha256: str
    dependency_proof_sha256: str
    round_index: int
    direct_ratio: float | None
    overlays: tuple[SourceOverlay, ...]
    selector_version: str
    prefilter_version: str | None
    recipe_sha256: str
    base_source_snapshot_sha256: str | None = None
    coverage_manifest_sha256: str | None = None
    direct_request_set_sha256: str | None = None
    direct_snapshot_set_sha256: str | None = None
    base_strategy: str | None = None
    direct_strategy: str | None = None
    direct_transfer: str | None = None

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1 or self.kind not in {"focused-recovery-v1", "adaptive-direct-fallback-v1"}:
            raise ValueError("composite recipe schema or kind is invalid")
        _require_sha256(self.family_id, "recipe family id")
        for label, value in (
            ("family input", self.family_input_sha256), ("base spec", self.base_spec_sha256),
            ("base cache", self.base_cache_digest), ("base source manifest", self.base_source_manifest_sha256),
            ("optimizer contract", self.optimizer_contract_sha256), ("whole profile", self.whole_profile_sha256),
            ("focused profile", self.focused_profile_sha256), ("dependency proof", self.dependency_proof_sha256),
        ):
            _require_sha256(value, label)
        _require_text(self.base_candidate_id, "base candidate id")
        _require_text(self.selector_version, "recipe selector version")
        if type(self.round_index) is not int or not 0 <= self.round_index < 3:
            raise ValueError("recipe round index is invalid")
        overlays = tuple(self.overlays)
        keys = [(item.source_identity.casefold(), item.source_identity) for item in overlays]
        overlay_limit = 4 if self.kind == "focused-recovery-v1" else 8
        if not overlays or len(overlays) > overlay_limit or any(not isinstance(item, SourceOverlay) for item in overlays) or keys != sorted(keys) or len({key[0] for key in keys}) != len(keys):
            raise ValueError("recipe overlays are not canonical or exceed bound")
        if self.kind == "focused-recovery-v1":
            if self.direct_ratio is not None or self.prefilter_version is not None or any(item.mode == "direct-position" for item in overlays) or any(value is not None for value in (self.base_source_snapshot_sha256, self.coverage_manifest_sha256, self.direct_request_set_sha256, self.direct_snapshot_set_sha256, self.base_strategy, self.direct_strategy, self.direct_transfer)):
                raise ValueError("focused recovery recipe mode matrix is invalid")
        else:
            ratio = _require_ratio(self.direct_ratio, "recipe direct ratio")
            for label, value in (("base source snapshot", self.base_source_snapshot_sha256), ("coverage manifest", self.coverage_manifest_sha256), ("direct request set", self.direct_request_set_sha256), ("direct snapshot set", self.direct_snapshot_set_sha256)):
                _require_sha256(value, label)
            if self.round_index != 0 or self.prefilter_version != "direct-degenerate-prefilter-v1" or self.base_strategy != "blender-adaptive-v1" or self.direct_strategy != "meshopt-direct-position-v1" or self.direct_transfer != "direct-position-v1" or any(item.mode != "direct-position" or item.effective_ratio != ratio for item in overlays):
                raise ValueError("adaptive direct recipe mode matrix is invalid")
        if _require_sha256(self.recipe_sha256, "recipe hash") != _seal(composite_recipe_payload(self, include_seal=False)):
            raise ValueError("recipe hash mismatch")
        object.__setattr__(self, "overlays", overlays)


def composite_recipe_payload(value: CompositeRecipe, *, include_seal: bool = True) -> dict[str, object]:
    payload = {
        "schema": value.schema, "kind": value.kind, "family_id": value.family_id,
        "family_input_sha256": value.family_input_sha256,
        "base_candidate_id": value.base_candidate_id, "base_spec_sha256": value.base_spec_sha256,
        "base_cache_digest": value.base_cache_digest,
        "base_source_manifest_sha256": value.base_source_manifest_sha256,
        "optimizer_contract_sha256": value.optimizer_contract_sha256,
        "whole_profile_sha256": value.whole_profile_sha256,
        "focused_profile_sha256": value.focused_profile_sha256,
        "dependency_proof_sha256": value.dependency_proof_sha256,
        "round_index": value.round_index, "direct_ratio": value.direct_ratio,
        "overlays": [source_overlay_payload(item) for item in value.overlays],
        "selector_version": value.selector_version, "prefilter_version": value.prefilter_version,
    }
    if value.kind == "adaptive-direct-fallback-v1":
        payload.update({
            "base_source_snapshot_sha256": value.base_source_snapshot_sha256,
            "coverage_manifest_sha256": value.coverage_manifest_sha256,
            "direct_request_set_sha256": value.direct_request_set_sha256,
            "direct_snapshot_set_sha256": value.direct_snapshot_set_sha256,
            "base_strategy": value.base_strategy,
            "direct_strategy": value.direct_strategy,
            "direct_transfer": value.direct_transfer,
        })
    if include_seal:
        payload["recipe_sha256"] = value.recipe_sha256
    return payload


def composite_recipe_from_payload(value: object) -> CompositeRecipe:
    common_fields = {
        "schema", "kind", "family_id", "family_input_sha256", "base_candidate_id",
        "base_spec_sha256", "base_cache_digest", "base_source_manifest_sha256",
        "optimizer_contract_sha256", "whole_profile_sha256", "focused_profile_sha256",
        "dependency_proof_sha256", "round_index", "direct_ratio", "overlays",
        "selector_version", "prefilter_version", "recipe_sha256",
    }
    adaptive_fields = {"base_source_snapshot_sha256", "coverage_manifest_sha256", "direct_request_set_sha256", "direct_snapshot_set_sha256", "base_strategy", "direct_strategy", "direct_transfer"}
    if type(value) is not dict or value.get("kind") not in {"focused-recovery-v1", "adaptive-direct-fallback-v1"}:
        raise ValueError("composite recipe payload fields are invalid")
    fields = common_fields | (adaptive_fields if value["kind"] == "adaptive-direct-fallback-v1" else set())
    if set(value) != fields or type(value["overlays"]) is not list:
        raise ValueError("composite recipe payload fields are invalid")
    overlays = []
    for raw in value["overlays"]:
        overlay_fields = {
            "source_identity", "mode", "motivating_region_key", "base_source_sha256",
            "replacement_sha256", "replacement_size", "replacement_snapshot_sha256",
            "replacement_candidate_id", "replacement_cache_digest", "effective_ratio",
            "focused_evidence", "reason",
        }
        if type(raw) is not dict or set(raw) != overlay_fields or type(raw["focused_evidence"]) is not list:
            raise ValueError("source overlay payload fields are invalid")
        refs = []
        for ref in raw["focused_evidence"]:
            if type(ref) is not dict or set(ref) != {"region_key", "evidence_sha256"}:
                raise ValueError("source overlay focused evidence is invalid")
            refs.append(FocusedEvidenceRef(**ref))
        copied = dict(raw)
        del copied["focused_evidence"]
        overlays.append(SourceOverlay(**copied, focused_evidence=tuple(refs)))
    copied = dict(value)
    del copied["overlays"]
    return CompositeRecipe(**copied, overlays=tuple(overlays))


@dataclass(frozen=True)
class AdaptiveGraphOccurrenceProof:
    graph_relative_path: str
    directive: str
    line: int
    logical_path: str
    role: Literal["visual"]

    def __post_init__(self) -> None:
        _require_relative(self.graph_relative_path, "adaptive graph path")
        _require_text(self.directive, "adaptive directive")
        if type(self.line) is not int or self.line < 1:
            raise ValueError("adaptive graph line is invalid")
        _require_relative(self.logical_path, "adaptive logical path")
        if self.role != "visual":
            raise ValueError("adaptive graph role is invalid")


def adaptive_graph_occurrence_payload(value: AdaptiveGraphOccurrenceProof) -> dict[str, object]:
    return {
        "graph_relative_path": value.graph_relative_path, "directive": value.directive,
        "line": value.line, "logical_path": value.logical_path, "role": value.role,
    }


def _validate_adaptive_occurrences(
    values: object, source_identity: str
) -> tuple[AdaptiveGraphOccurrenceProof, ...]:
    result = tuple(values) if isinstance(values, (tuple, list)) else ()
    keys = [(item.graph_relative_path.casefold(), item.directive, item.line, item.logical_path.casefold()) for item in result if isinstance(item, AdaptiveGraphOccurrenceProof)]
    if not result or len(result) > _ADAPTIVE_OCCURRENCE_LIMIT or len(keys) != len(result):
        raise ValueError("adaptive occurrences are invalid or exceed bound")
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise ValueError("adaptive occurrences are not canonical and unique")
    if any(item.logical_path.casefold() != source_identity.casefold() for item in result):
        raise ValueError("adaptive occurrence source mismatch")
    return result


def _adaptive_source_payload(value: object, *, include_seal: bool = True) -> dict[str, object]:
    payload = {
        "kind": value.kind, "source_identity": value.source_identity,
        "source_relative_path": value.source_relative_path, "source_size": value.source_size,
        "source_sha256": value.source_sha256, "output_relative_path": value.output_relative_path,
        "output_size": value.output_size, "output_sha256": value.output_sha256,
        "preserved_exact": value.preserved_exact,
        "occurrences": [adaptive_graph_occurrence_payload(item) for item in value.occurrences],
    }
    if value.kind == "eligible-exact-v1":
        payload["eligibility_reason"] = value.eligibility_reason
    else:
        payload["ineligibility_reason"] = value.ineligibility_reason
    if include_seal:
        payload["metrics_sha256"] = value.metrics_sha256
    return payload


@dataclass(frozen=True)
class EligibleAdaptiveSourceProof:
    kind: Literal["eligible-exact-v1"]
    source_identity: str
    source_relative_path: str
    source_size: int
    source_sha256: str
    output_relative_path: str
    output_size: int
    output_sha256: str
    preserved_exact: Literal[True]
    eligibility_reason: Literal["ratio-preserved-exact-v1", "approved-exact-source-fallback-v1"]
    occurrences: tuple[AdaptiveGraphOccurrenceProof, ...]
    metrics_sha256: str

    def __post_init__(self) -> None:
        if self.kind != "eligible-exact-v1" or self.preserved_exact is not True:
            raise ValueError("eligible adaptive source discriminator is invalid")
        _require_relative(self.source_identity, "adaptive source identity")
        _require_relative(self.source_relative_path, "adaptive source path")
        _require_relative(self.output_relative_path, "adaptive output path")
        _require_size(self.source_size, "adaptive source size")
        _require_size(self.output_size, "adaptive output size")
        _require_sha256(self.source_sha256, "adaptive source hash")
        _require_sha256(self.output_sha256, "adaptive output hash")
        if self.source_size != self.output_size or self.source_sha256 != self.output_sha256:
            raise ValueError("eligible adaptive source is not byte exact")
        if self.eligibility_reason not in {"ratio-preserved-exact-v1", "approved-exact-source-fallback-v1"}:
            raise ValueError("adaptive eligibility reason is invalid")
        occurrences = _validate_adaptive_occurrences(self.occurrences, self.source_identity)
        if _require_sha256(self.metrics_sha256, "adaptive metrics hash") != _seal(_adaptive_source_payload(self, include_seal=False)):
            raise ValueError("adaptive source metrics seal mismatch")
        object.__setattr__(self, "occurrences", occurrences)

    @classmethod
    def create(cls, **values) -> "EligibleAdaptiveSourceProof":
        raw = dict(kind="eligible-exact-v1", preserved_exact=True, metrics_sha256=H_EMPTY, **values)
        provisional = _unsealed(cls, **raw)
        raw["metrics_sha256"] = _seal(_adaptive_source_payload(provisional, include_seal=False))
        return cls(**raw)


@dataclass(frozen=True)
class IneligibleAdaptiveSourceProof:
    kind: Literal["ineligible-changed-v1"]
    source_identity: str
    source_relative_path: str
    source_size: int
    source_sha256: str
    output_relative_path: str
    output_size: int
    output_sha256: str
    preserved_exact: Literal[False]
    ineligibility_reason: Literal["adaptive-output-changed-v1"]
    occurrences: tuple[AdaptiveGraphOccurrenceProof, ...]
    metrics_sha256: str

    def __post_init__(self) -> None:
        if self.kind != "ineligible-changed-v1" or self.preserved_exact is not False or self.ineligibility_reason != "adaptive-output-changed-v1":
            raise ValueError("ineligible adaptive source discriminator is invalid")
        _require_relative(self.source_identity, "adaptive source identity")
        _require_relative(self.source_relative_path, "adaptive source path")
        _require_relative(self.output_relative_path, "adaptive output path")
        _require_size(self.source_size, "adaptive source size")
        _require_size(self.output_size, "adaptive output size")
        _require_sha256(self.source_sha256, "adaptive source hash")
        _require_sha256(self.output_sha256, "adaptive output hash")
        if self.source_size == self.output_size and self.source_sha256 == self.output_sha256:
            raise ValueError("ineligible adaptive source did not change")
        occurrences = _validate_adaptive_occurrences(self.occurrences, self.source_identity)
        if _require_sha256(self.metrics_sha256, "adaptive metrics hash") != _seal(_adaptive_source_payload(self, include_seal=False)):
            raise ValueError("adaptive source metrics seal mismatch")
        object.__setattr__(self, "occurrences", occurrences)

    @classmethod
    def create(cls, **values) -> "IneligibleAdaptiveSourceProof":
        raw = dict(kind="ineligible-changed-v1", preserved_exact=False, metrics_sha256=H_EMPTY, **values)
        provisional = _unsealed(cls, **raw)
        raw["metrics_sha256"] = _seal(_adaptive_source_payload(provisional, include_seal=False))
        return cls(**raw)


AdaptiveSourceMetricsProof = EligibleAdaptiveSourceProof | IneligibleAdaptiveSourceProof
H_EMPTY = "0" * 64


@dataclass(frozen=True)
class AdaptiveCandidateMetricsProof:
    schema: Literal[1]
    family_id: str
    family_input_sha256: str
    candidate_id: str
    candidate_cache_digest: str
    strategy: Literal["blender-adaptive-v1"]
    base_spec_sha256: str
    source_manifest_sha256: str
    source_snapshot_sha256: str
    original_graph_sha256: str
    candidate_graph_sha256: str
    raw_metrics_sha256: str
    sources: tuple[AdaptiveSourceMetricsProof, ...]
    evidence_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1 or self.strategy != "blender-adaptive-v1":
            raise ValueError("adaptive candidate metrics identity is invalid")
        for label, value in (("family id", self.family_id), ("family input", self.family_input_sha256), ("candidate cache", self.candidate_cache_digest), ("base spec", self.base_spec_sha256), ("source manifest", self.source_manifest_sha256), ("source snapshot", self.source_snapshot_sha256), ("original graph", self.original_graph_sha256), ("candidate graph", self.candidate_graph_sha256), ("raw metrics", self.raw_metrics_sha256)):
            _require_sha256(value, label)
        _require_text(self.candidate_id, "adaptive candidate id")
        sources = tuple(self.sources)
        keys = [(item.source_identity.casefold(), item.source_identity) for item in sources if isinstance(item, (EligibleAdaptiveSourceProof, IneligibleAdaptiveSourceProof))]
        if not sources or len(keys) != len(sources) or keys != sorted(keys) or len({key[0] for key in keys}) != len(keys):
            raise ValueError("adaptive metrics sources are not complete canonical union")
        if _require_sha256(self.evidence_sha256, "adaptive metrics evidence") != _seal(adaptive_candidate_metrics_payload(self, include_seal=False)):
            raise ValueError("adaptive candidate metrics seal mismatch")
        object.__setattr__(self, "sources", sources)


def adaptive_candidate_metrics_payload(value: AdaptiveCandidateMetricsProof, *, include_seal: bool = True) -> dict[str, object]:
    payload = {
        "schema": value.schema, "family_id": value.family_id,
        "family_input_sha256": value.family_input_sha256, "candidate_id": value.candidate_id,
        "candidate_cache_digest": value.candidate_cache_digest, "strategy": value.strategy,
        "base_spec_sha256": value.base_spec_sha256,
        "source_manifest_sha256": value.source_manifest_sha256,
        "source_snapshot_sha256": value.source_snapshot_sha256,
        "original_graph_sha256": value.original_graph_sha256,
        "candidate_graph_sha256": value.candidate_graph_sha256,
        "raw_metrics_sha256": value.raw_metrics_sha256,
        "sources": [_adaptive_source_payload(item) for item in value.sources],
    }
    if include_seal:
        payload["evidence_sha256"] = value.evidence_sha256
    return payload


def _adaptive_source_from_payload(raw: object) -> AdaptiveSourceMetricsProof:
    if type(raw) is not dict or raw.get("kind") not in {"eligible-exact-v1", "ineligible-changed-v1"} or type(raw.get("occurrences")) is not list:
        raise ValueError("adaptive source metrics payload is invalid")
    eligible = raw["kind"] == "eligible-exact-v1"
    fields = {"kind", "source_identity", "source_relative_path", "source_size", "source_sha256", "output_relative_path", "output_size", "output_sha256", "preserved_exact", "occurrences", "metrics_sha256", "eligibility_reason" if eligible else "ineligibility_reason"}
    if set(raw) != fields:
        raise ValueError("adaptive source metrics fields are invalid")
    occurrences = []
    for item in raw["occurrences"]:
        if type(item) is not dict or set(item) != {"graph_relative_path", "directive", "line", "logical_path", "role"}:
            raise ValueError("adaptive occurrence payload is invalid")
        occurrences.append(AdaptiveGraphOccurrenceProof(**item))
    copied = dict(raw); copied["occurrences"] = tuple(occurrences)
    return (EligibleAdaptiveSourceProof if eligible else IneligibleAdaptiveSourceProof)(**copied)


def adaptive_candidate_metrics_from_payload(value: object) -> AdaptiveCandidateMetricsProof:
    fields = {"schema", "family_id", "family_input_sha256", "candidate_id", "candidate_cache_digest", "strategy", "base_spec_sha256", "source_manifest_sha256", "source_snapshot_sha256", "original_graph_sha256", "candidate_graph_sha256", "raw_metrics_sha256", "sources", "evidence_sha256"}
    if type(value) is not dict or set(value) != fields or type(value["sources"]) is not list:
        raise ValueError("adaptive candidate metrics payload fields are invalid")
    copied = dict(value); copied["sources"] = tuple(_adaptive_source_from_payload(item) for item in value["sources"])
    return AdaptiveCandidateMetricsProof(**copied)


def adaptive_metric_occurrence_key(
    graph_relative_path: str, directive: str, line: int, logical_path: str,
) -> str:
    payload = {
        "graph_relative_path": graph_relative_path,
        "directive": directive, "line": line, "logical_path": logical_path,
        "role": "visual",
    }
    return "metric-occ-" + _seal(payload)


@dataclass(frozen=True)
class AdaptiveDirectMetricOccurrenceClassification:
    metric_occurrence_key: str
    source_identity: str
    graph_relative_path: str
    directive: str
    line: int
    logical_path: str
    role: Literal["visual"]
    classification: Literal[
        "active-renderable-v1", "lod-original-selector-nonrenderable-v1"
    ]
    active_row_occurrence_keys: tuple[str, ...]
    classification_sha256: str

    def __post_init__(self) -> None:
        _require_relative(self.source_identity, "classified metric source identity")
        _require_relative(self.graph_relative_path, "classified metric graph path")
        _require_relative(self.logical_path, "classified metric logical path")
        _require_text(self.directive, "classified metric directive")
        if type(self.line) is not int or self.line < 1 or self.role != "visual":
            raise ValueError("classified metric occurrence provenance is invalid")
        expected_key = adaptive_metric_occurrence_key(
            self.graph_relative_path, self.directive, self.line, self.logical_path,
        )
        if self.metric_occurrence_key != expected_key:
            raise ValueError("classified metric occurrence key differs from provenance")
        active_keys = tuple(self.active_row_occurrence_keys)
        if (
            len(active_keys) > _ADAPTIVE_OCCURRENCE_LIMIT
            or any(type(item) is not str or not item for item in active_keys)
            or active_keys != tuple(sorted(active_keys, key=lambda item: (item.casefold(), item)))
            or len({item.casefold() for item in active_keys}) != len(active_keys)
        ):
            raise ValueError("classified metric active row mapping is invalid")
        if self.classification == "active-renderable-v1":
            if not active_keys:
                raise ValueError("active metric occurrence has no rendered state rows")
        elif self.classification == "lod-original-selector-nonrenderable-v1":
            if active_keys or self.directive != "$lod/replacemodel":
                raise ValueError("LOD selector classification is renderable or has wrong directive")
        else:
            raise ValueError("metric occurrence classification is invalid")
        if _require_sha256(
            self.classification_sha256, "metric occurrence classification seal"
        ) != _seal(adaptive_direct_metric_occurrence_payload(self, include_seal=False)):
            raise ValueError("metric occurrence classification seal mismatch")
        object.__setattr__(self, "active_row_occurrence_keys", active_keys)


def adaptive_direct_metric_occurrence_payload(
    value: AdaptiveDirectMetricOccurrenceClassification, *, include_seal: bool = True,
) -> dict[str, object]:
    payload = {
        "metric_occurrence_key": value.metric_occurrence_key,
        "source_identity": value.source_identity,
        "graph_relative_path": value.graph_relative_path,
        "directive": value.directive, "line": value.line,
        "logical_path": value.logical_path, "role": value.role,
        "classification": value.classification,
        "active_row_occurrence_keys": list(value.active_row_occurrence_keys),
    }
    if include_seal:
        payload["classification_sha256"] = value.classification_sha256
    return payload


@dataclass(frozen=True)
class AdaptiveDirectStateInventoryRow:
    occurrence_key: str
    source_identity: str
    graph_relative_path: str
    directive: str
    line: int
    state_key: str
    bodygroup_key: str
    lod_key: str
    skin_key: str
    source_size: int
    source_sha256: str
    component_keys: tuple[str, ...]
    material_region_keys: tuple[str, ...]
    skeleton_contract_sha256: str
    pose_keys: tuple[str, ...]
    component_manifest_sha256: str
    material_contract_sha256: str
    pose_contract_sha256: str
    equivalence_class_sha256: str
    row_sha256: str

    def __post_init__(self) -> None:
        for label, value in (("inventory occurrence key", self.occurrence_key), ("inventory directive", self.directive), ("inventory state", self.state_key), ("inventory bodygroup", self.bodygroup_key), ("inventory lod", self.lod_key), ("inventory skin", self.skin_key)):
            _require_text(value, label)
        _require_relative(self.source_identity, "inventory source identity")
        _require_relative(self.graph_relative_path, "inventory graph path")
        if type(self.line) is not int or self.line < 1: raise ValueError("inventory graph line is invalid")
        _require_size(self.source_size, "inventory source size"); _require_sha256(self.source_sha256, "inventory source hash")
        components = _canonical_text_tuple(self.component_keys, "inventory components", limit=_ADAPTIVE_COMPONENT_LIMIT)
        materials = _canonical_text_tuple(self.material_region_keys, "inventory materials", limit=_ADAPTIVE_MATERIAL_LIMIT)
        poses = tuple(self.pose_keys)
        if not 1 <= len(poses) <= 2 or any(type(item) is not str or not item for item in poses) or poses[0] != "bind" or len({item.casefold() for item in poses}) != len(poses): raise ValueError("inventory poses are invalid")
        for label, value in (("inventory skeleton", self.skeleton_contract_sha256), ("inventory component manifest", self.component_manifest_sha256), ("inventory material contract", self.material_contract_sha256), ("inventory pose contract", self.pose_contract_sha256), ("inventory equivalence class", self.equivalence_class_sha256)):
            _require_sha256(value, label)
        if _require_sha256(self.row_sha256, "inventory row hash") != _seal(adaptive_direct_state_inventory_row_payload(self, include_seal=False)): raise ValueError("inventory row seal mismatch")
        object.__setattr__(self, "component_keys", components); object.__setattr__(self, "material_region_keys", materials); object.__setattr__(self, "pose_keys", poses)


def adaptive_direct_state_inventory_row_payload(value: AdaptiveDirectStateInventoryRow, *, include_seal: bool = True) -> dict[str, object]:
    payload = {name: getattr(value, name) for name in ("occurrence_key", "source_identity", "graph_relative_path", "directive", "line", "state_key", "bodygroup_key", "lod_key", "skin_key", "source_size", "source_sha256", "skeleton_contract_sha256", "component_manifest_sha256", "material_contract_sha256", "pose_contract_sha256", "equivalence_class_sha256")}
    payload["component_keys"] = list(value.component_keys); payload["material_region_keys"] = list(value.material_region_keys); payload["pose_keys"] = list(value.pose_keys)
    if include_seal: payload["row_sha256"] = value.row_sha256
    return payload


@dataclass(frozen=True)
class AdaptiveDirectStateInventory:
    schema: Literal[1]
    family_id: str
    family_input_sha256: str
    base_candidate_id: str
    base_spec_sha256: str
    base_cache_digest: str
    base_source_manifest_sha256: str
    base_source_snapshot_sha256: str
    complete_source_identities: tuple[str, ...]
    rows: tuple[AdaptiveDirectStateInventoryRow, ...]
    metric_occurrences: tuple[AdaptiveDirectMetricOccurrenceClassification, ...]
    state_inventory_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1: raise ValueError("state inventory schema is invalid")
        for label, value in (("inventory family", self.family_id), ("inventory family input", self.family_input_sha256), ("inventory base spec", self.base_spec_sha256), ("inventory base cache", self.base_cache_digest), ("inventory source manifest", self.base_source_manifest_sha256), ("inventory source snapshot", self.base_source_snapshot_sha256)): _require_sha256(value, label)
        _require_text(self.base_candidate_id, "inventory base candidate")
        identities = _canonical_text_tuple(self.complete_source_identities, "inventory source identities")
        rows = tuple(self.rows)
        metric_occurrences = tuple(self.metric_occurrences)
        keys = [(item.occurrence_key.casefold(), item.occurrence_key) for item in rows if isinstance(item, AdaptiveDirectStateInventoryRow)]
        if not rows or len(rows) > _ADAPTIVE_OCCURRENCE_LIMIT or len(keys) != len(rows) or keys != sorted(keys) or len({key[0] for key in keys}) != len(keys): raise ValueError("state inventory rows are not canonical or exceed bound")
        metric_keys = tuple(
            (item.metric_occurrence_key.casefold(), item.metric_occurrence_key)
            for item in metric_occurrences
            if isinstance(item, AdaptiveDirectMetricOccurrenceClassification)
        )
        if (
            not metric_occurrences or len(metric_occurrences) > _ADAPTIVE_OCCURRENCE_LIMIT
            or len(metric_keys) != len(metric_occurrences)
            or metric_keys != tuple(sorted(metric_keys))
            or len({item[0] for item in metric_keys}) != len(metric_keys)
        ):
            raise ValueError("state inventory metric classifications are not canonical")
        if {item.source_identity for item in metric_occurrences} != set(identities):
            raise ValueError("state inventory classified metric source coverage mismatch")
        rows_by_key = {item.occurrence_key: item for item in rows}
        mapped = tuple(
            key for item in metric_occurrences for key in item.active_row_occurrence_keys
        )
        if len(mapped) != len(set(mapped)) or set(mapped) != set(rows_by_key):
            raise ValueError("state inventory active row/classification mapping is incomplete")
        for item in metric_occurrences:
            for row_key in item.active_row_occurrence_keys:
                row = rows_by_key[row_key]
                if (
                    row.source_identity != item.source_identity
                    or row.graph_relative_path != item.graph_relative_path
                    or row.directive != item.directive or row.line != item.line
                ):
                    raise ValueError("state inventory row differs from classified metric provenance")
        if _require_sha256(self.state_inventory_sha256, "state inventory hash") != _seal(adaptive_direct_state_inventory_payload(self, include_seal=False)): raise ValueError("state inventory seal mismatch")
        object.__setattr__(self, "complete_source_identities", identities); object.__setattr__(self, "rows", rows); object.__setattr__(self, "metric_occurrences", metric_occurrences)


def adaptive_direct_state_inventory_payload(value: AdaptiveDirectStateInventory, *, include_seal: bool = True) -> dict[str, object]:
    payload = {"schema": value.schema, "family_id": value.family_id, "family_input_sha256": value.family_input_sha256, "base_candidate_id": value.base_candidate_id, "base_spec_sha256": value.base_spec_sha256, "base_cache_digest": value.base_cache_digest, "base_source_manifest_sha256": value.base_source_manifest_sha256, "base_source_snapshot_sha256": value.base_source_snapshot_sha256, "complete_source_identities": list(value.complete_source_identities), "rows": [adaptive_direct_state_inventory_row_payload(item) for item in value.rows], "metric_occurrences": [adaptive_direct_metric_occurrence_payload(item) for item in value.metric_occurrences]}
    if include_seal: payload["state_inventory_sha256"] = value.state_inventory_sha256
    return payload


def adaptive_direct_state_inventory_row_from_payload(value: object) -> AdaptiveDirectStateInventoryRow:
    fields = {"occurrence_key", "source_identity", "graph_relative_path", "directive", "line", "state_key", "bodygroup_key", "lod_key", "skin_key", "source_size", "source_sha256", "component_keys", "material_region_keys", "skeleton_contract_sha256", "pose_keys", "component_manifest_sha256", "material_contract_sha256", "pose_contract_sha256", "equivalence_class_sha256", "row_sha256"}
    if type(value) is not dict or set(value) != fields or any(type(value[name]) is not list for name in ("component_keys", "material_region_keys", "pose_keys")): raise ValueError("state inventory row payload fields are invalid")
    copied = dict(value)
    for name in ("component_keys", "material_region_keys", "pose_keys"): copied[name] = tuple(copied[name])
    return AdaptiveDirectStateInventoryRow(**copied)


def adaptive_direct_metric_occurrence_from_payload(
    value: object,
) -> AdaptiveDirectMetricOccurrenceClassification:
    fields = {
        "metric_occurrence_key", "source_identity", "graph_relative_path",
        "directive", "line", "logical_path", "role", "classification",
        "active_row_occurrence_keys", "classification_sha256",
    }
    if (
        type(value) is not dict or set(value) != fields
        or type(value["active_row_occurrence_keys"]) is not list
    ):
        raise ValueError("metric occurrence classification payload fields are invalid")
    copied = dict(value)
    copied["active_row_occurrence_keys"] = tuple(copied["active_row_occurrence_keys"])
    return AdaptiveDirectMetricOccurrenceClassification(**copied)


def adaptive_direct_state_inventory_from_payload(value: object) -> AdaptiveDirectStateInventory:
    fields = {"schema", "family_id", "family_input_sha256", "base_candidate_id", "base_spec_sha256", "base_cache_digest", "base_source_manifest_sha256", "base_source_snapshot_sha256", "complete_source_identities", "rows", "metric_occurrences", "state_inventory_sha256"}
    if type(value) is not dict or set(value) != fields or type(value["complete_source_identities"]) is not list or type(value["rows"]) is not list or type(value["metric_occurrences"]) is not list: raise ValueError("state inventory payload fields are invalid")
    copied = dict(value); copied["complete_source_identities"] = tuple(copied["complete_source_identities"]); copied["rows"] = tuple(adaptive_direct_state_inventory_row_from_payload(item) for item in copied["rows"]); copied["metric_occurrences"] = tuple(adaptive_direct_metric_occurrence_from_payload(item) for item in copied["metric_occurrences"])
    return AdaptiveDirectStateInventory(**copied)


@dataclass(frozen=True)
class AdaptiveDirectCoverageOccurrenceProof:
    occurrence_key: str
    source_identity: str
    graph_relative_path: str
    directive: str
    line: int
    state_key: str
    bodygroup_key: str
    lod_key: str
    skin_key: str
    source_size: int
    source_sha256: str
    component_manifest_sha256: str
    material_contract_sha256: str
    skeleton_contract_sha256: str
    pose_contract_sha256: str
    equivalence_class_sha256: str
    status: Literal["covered-by-source-union-v1"]
    evidence_sha256: str

    def __post_init__(self) -> None:
        for label, value in (("occurrence key", self.occurrence_key), ("directive", self.directive), ("state key", self.state_key), ("bodygroup key", self.bodygroup_key), ("lod key", self.lod_key), ("skin key", self.skin_key)):
            _require_text(value, label)
        _require_relative(self.source_identity, "coverage source identity")
        _require_relative(self.graph_relative_path, "coverage graph path")
        if type(self.line) is not int or self.line < 1:
            raise ValueError("coverage line is invalid")
        _require_size(self.source_size, "coverage source size")
        for label, value in (("source hash", self.source_sha256), ("component manifest", self.component_manifest_sha256), ("material contract", self.material_contract_sha256), ("skeleton contract", self.skeleton_contract_sha256), ("pose contract", self.pose_contract_sha256), ("equivalence class", self.equivalence_class_sha256)):
            _require_sha256(value, label)
        if self.status != "covered-by-source-union-v1":
            raise ValueError("coverage occurrence status is invalid")
        if _require_sha256(self.evidence_sha256, "coverage occurrence evidence") != _seal(adaptive_direct_coverage_occurrence_payload(self, include_seal=False)):
            raise ValueError("coverage occurrence seal mismatch")

    @classmethod
    def create(cls, **values) -> "AdaptiveDirectCoverageOccurrenceProof":
        raw = dict(status="covered-by-source-union-v1", evidence_sha256=H_EMPTY, **values)
        provisional = _unsealed(cls, **raw)
        raw["evidence_sha256"] = _seal(adaptive_direct_coverage_occurrence_payload(provisional, include_seal=False))
        return cls(**raw)


AdaptiveDirectCoverageOccurrenceWitness = AdaptiveDirectCoverageOccurrenceProof


def adaptive_direct_coverage_occurrence_payload(value: AdaptiveDirectCoverageOccurrenceProof, *, include_seal: bool = True) -> dict[str, object]:
    payload = {name: getattr(value, name) for name in (
        "occurrence_key", "source_identity", "graph_relative_path", "directive", "line",
        "state_key", "bodygroup_key", "lod_key", "skin_key", "source_size", "source_sha256",
        "component_manifest_sha256", "material_contract_sha256", "skeleton_contract_sha256",
        "pose_contract_sha256", "equivalence_class_sha256", "status",
    )}
    if include_seal: payload["evidence_sha256"] = value.evidence_sha256
    return payload


@dataclass(frozen=True)
class AdaptiveDirectCoverageSourceProof:
    source_identity: str
    eligibility_kind: Literal["eligible-exact-v1", "ineligible-changed-v1"]
    source_size: int
    source_sha256: str
    occurrence_keys: tuple[str, ...]
    state_keys: tuple[str, ...]
    component_keys: tuple[str, ...]
    material_region_keys: tuple[str, ...]
    skeleton_contract_sha256: str
    pose_keys: tuple[str, ...]
    equivalence_class_sha256: str
    metrics_sha256: str
    state_inventory_sha256: str
    witnesses: tuple[AdaptiveDirectCoverageOccurrenceProof, ...]
    source_coverage_sha256: str

    def __post_init__(self) -> None:
        _require_relative(self.source_identity, "coverage source identity")
        if self.eligibility_kind not in {"eligible-exact-v1", "ineligible-changed-v1"}:
            raise ValueError("coverage eligibility kind is invalid")
        _require_size(self.source_size, "coverage source size")
        _require_sha256(self.source_sha256, "coverage source hash")
        occurrence_keys = _canonical_text_tuple(self.occurrence_keys, "coverage occurrence keys", limit=_ADAPTIVE_OCCURRENCE_LIMIT)
        states = _canonical_text_tuple(self.state_keys, "coverage states", limit=_ADAPTIVE_STATE_LIMIT)
        components = _canonical_text_tuple(self.component_keys, "coverage components", limit=_ADAPTIVE_COMPONENT_LIMIT)
        materials = _canonical_text_tuple(self.material_region_keys, "coverage materials", limit=_ADAPTIVE_MATERIAL_LIMIT)
        poses = tuple(self.pose_keys)
        if not 1 <= len(poses) <= _ADAPTIVE_POSE_LIMIT or poses[0] != "bind" or len({item.casefold() for item in poses if type(item) is str}) != len(poses) or any(type(item) is not str or not item for item in poses):
            raise ValueError("coverage poses are invalid")
        _require_sha256(self.skeleton_contract_sha256, "coverage skeleton contract")
        _require_sha256(self.equivalence_class_sha256, "coverage equivalence class")
        _require_sha256(self.metrics_sha256, "coverage source metrics")
        _require_sha256(self.state_inventory_sha256, "coverage state inventory")
        witnesses = tuple(self.witnesses)
        if not witnesses or len(witnesses) > _ADAPTIVE_OCCURRENCE_LIMIT or any(not isinstance(item, AdaptiveDirectCoverageOccurrenceProof) for item in witnesses):
            raise ValueError("coverage witnesses are invalid or exceed bound")
        witness_keys = tuple(item.occurrence_key for item in witnesses)
        if witness_keys != occurrence_keys or tuple(sorted(witnesses, key=lambda item: (item.occurrence_key.casefold(), item.occurrence_key))) != witnesses:
            raise ValueError("coverage witness inventory mismatch")
        if {item.state_key for item in witnesses} != set(states):
            raise ValueError("coverage state inventory mismatch")
        if any(item.source_identity != self.source_identity or item.source_size != self.source_size or item.source_sha256 != self.source_sha256 or item.skeleton_contract_sha256 != self.skeleton_contract_sha256 or item.equivalence_class_sha256 != self.equivalence_class_sha256 for item in witnesses):
            raise ValueError("coverage equivalence witness mismatch")
        for field_name in ("component_manifest_sha256", "material_contract_sha256", "pose_contract_sha256"):
            if len({getattr(item, field_name) for item in witnesses}) != 1:
                raise ValueError("coverage dependency difference splits equivalence class")
        if _require_sha256(self.source_coverage_sha256, "source coverage hash") != _seal(adaptive_direct_coverage_source_payload(self, include_seal=False)):
            raise ValueError("source coverage seal mismatch")
        for name, value in (("occurrence_keys", occurrence_keys), ("state_keys", states), ("component_keys", components), ("material_region_keys", materials), ("pose_keys", poses), ("witnesses", witnesses)):
            object.__setattr__(self, name, value)

    @classmethod
    def create(cls, **values) -> "AdaptiveDirectCoverageSourceProof":
        raw = dict(source_coverage_sha256=H_EMPTY, **values)
        provisional = _unsealed(cls, **raw)
        raw["source_coverage_sha256"] = _seal(adaptive_direct_coverage_source_payload(provisional, include_seal=False))
        return cls(**raw)


def adaptive_direct_coverage_source_payload(value: AdaptiveDirectCoverageSourceProof, *, include_seal: bool = True) -> dict[str, object]:
    payload = {
        "source_identity": value.source_identity, "eligibility_kind": value.eligibility_kind,
        "source_size": value.source_size, "source_sha256": value.source_sha256,
        "occurrence_keys": list(value.occurrence_keys), "state_keys": list(value.state_keys),
        "component_keys": list(value.component_keys), "material_region_keys": list(value.material_region_keys),
        "skeleton_contract_sha256": value.skeleton_contract_sha256, "pose_keys": list(value.pose_keys),
        "equivalence_class_sha256": value.equivalence_class_sha256,
        "metrics_sha256": value.metrics_sha256,
        "state_inventory_sha256": value.state_inventory_sha256,
        "witnesses": [adaptive_direct_coverage_occurrence_payload(item) for item in value.witnesses],
    }
    if include_seal: payload["source_coverage_sha256"] = value.source_coverage_sha256
    return payload


@dataclass(frozen=True)
class AdaptiveDirectCoverageManifest:
    schema: Literal[1]
    family_id: str
    family_input_sha256: str
    base_candidate_id: str
    base_spec_sha256: str
    base_cache_digest: str
    base_source_manifest_sha256: str
    base_source_snapshot_sha256: str
    metrics_evidence_sha256: str
    state_inventory_sha256: str
    metrics_proof: AdaptiveCandidateMetricsProof
    state_inventory: AdaptiveDirectStateInventory
    complete_source_identities: tuple[str, ...]
    sources: tuple[AdaptiveDirectCoverageSourceProof, ...]
    occurrence_count: int
    component_count: int
    state_count: int
    maximum_candidate_images: int
    coverage_manifest_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1: raise ValueError("coverage manifest schema is invalid")
        for label, value in (("family id", self.family_id), ("family input", self.family_input_sha256), ("base spec", self.base_spec_sha256), ("base cache", self.base_cache_digest), ("base source manifest", self.base_source_manifest_sha256), ("base source snapshot", self.base_source_snapshot_sha256)):
            _require_sha256(value, label)
        _require_sha256(self.metrics_evidence_sha256, "coverage metrics evidence")
        _require_sha256(self.state_inventory_sha256, "coverage state inventory")
        if not isinstance(self.metrics_proof, AdaptiveCandidateMetricsProof) or self.metrics_proof.evidence_sha256 != self.metrics_evidence_sha256:
            raise ValueError("coverage typed metrics binding mismatch")
        if not isinstance(self.state_inventory, AdaptiveDirectStateInventory) or self.state_inventory.state_inventory_sha256 != self.state_inventory_sha256:
            raise ValueError("coverage typed state inventory binding mismatch")
        if any((self.metrics_proof.family_id != self.family_id, self.metrics_proof.family_input_sha256 != self.family_input_sha256, self.metrics_proof.candidate_id != self.base_candidate_id, self.metrics_proof.base_spec_sha256 != self.base_spec_sha256, self.metrics_proof.candidate_cache_digest != self.base_cache_digest, self.metrics_proof.source_manifest_sha256 != self.base_source_manifest_sha256, self.metrics_proof.source_snapshot_sha256 != self.base_source_snapshot_sha256)):
            raise ValueError("coverage typed metrics base mismatch")
        if any((self.state_inventory.family_id != self.family_id, self.state_inventory.family_input_sha256 != self.family_input_sha256, self.state_inventory.base_candidate_id != self.base_candidate_id, self.state_inventory.base_spec_sha256 != self.base_spec_sha256, self.state_inventory.base_cache_digest != self.base_cache_digest, self.state_inventory.base_source_manifest_sha256 != self.base_source_manifest_sha256, self.state_inventory.base_source_snapshot_sha256 != self.base_source_snapshot_sha256)):
            raise ValueError("coverage typed inventory base mismatch")
        _require_text(self.base_candidate_id, "coverage base candidate")
        identities = _canonical_text_tuple(self.complete_source_identities, "complete source identities")
        sources = tuple(self.sources)
        if any(not isinstance(item, AdaptiveDirectCoverageSourceProof) for item in sources) or tuple(item.source_identity for item in sources) != identities:
            raise ValueError("coverage source inventory mismatch")
        if any(item.state_inventory_sha256 != self.state_inventory_sha256 for item in sources):
            raise ValueError("coverage source state inventory binding mismatch")
        metrics_by_source = {item.source_identity: item for item in self.metrics_proof.sources}
        classifications_by_source = {identity: [] for identity in identities}
        for item in self.state_inventory.metric_occurrences:
            classifications_by_source[item.source_identity].append(item)
        rows_by_source = {identity: [] for identity in identities}
        for row in self.state_inventory.rows: rows_by_source[row.source_identity].append(row)
        for source in sources:
            metric = metrics_by_source.get(source.source_identity)
            rows = tuple(rows_by_source.get(source.source_identity, ()))
            if metric is None or source.metrics_sha256 != metric.metrics_sha256 or source.eligibility_kind != metric.kind or source.source_size != metric.source_size or source.source_sha256 != metric.source_sha256:
                raise ValueError("coverage source differs from typed metrics")
            metric_occurrences = {
                (item.graph_relative_path, item.directive, item.line, item.logical_path)
                for item in metric.occurrences
            }
            classified_occurrences = {
                (item.graph_relative_path, item.directive, item.line, item.logical_path)
                for item in classifications_by_source[source.source_identity]
            }
            if (
                len(metric_occurrences) != len(metric.occurrences)
                or len(classified_occurrences)
                != len(classifications_by_source[source.source_identity])
                or metric_occurrences != classified_occurrences
            ):
                raise ValueError("coverage classified occurrences differ from typed metrics")
            witness_rows = tuple((item.occurrence_key, item.source_identity, item.graph_relative_path, item.directive, item.line, item.state_key, item.bodygroup_key, item.lod_key, item.skin_key, item.source_size, item.source_sha256, item.component_manifest_sha256, item.material_contract_sha256, item.skeleton_contract_sha256, item.pose_contract_sha256, item.equivalence_class_sha256) for item in source.witnesses)
            inventory_rows = tuple((item.occurrence_key, item.source_identity, item.graph_relative_path, item.directive, item.line, item.state_key, item.bodygroup_key, item.lod_key, item.skin_key, item.source_size, item.source_sha256, item.component_manifest_sha256, item.material_contract_sha256, item.skeleton_contract_sha256, item.pose_contract_sha256, item.equivalence_class_sha256) for item in rows)
            if witness_rows != inventory_rows:
                raise ValueError("coverage witnesses differ from typed state inventory")
            expected_components = tuple(sorted({key for row in rows for key in row.component_keys}, key=lambda key: (key.casefold(), key)))
            expected_materials = tuple(sorted({key for row in rows for key in row.material_region_keys}, key=lambda key: (key.casefold(), key)))
            if source.component_keys != expected_components or source.material_region_keys != expected_materials or any(row.pose_keys != source.pose_keys for row in rows):
                raise ValueError("coverage source regions or poses differ from typed state inventory")
        eligible = tuple(item for item in sources if item.eligibility_kind == "eligible-exact-v1")
        if not 1 <= len(eligible) <= _ADAPTIVE_SOURCE_LIMIT:
            raise ValueError("coverage eligible source count is outside 1..8")
        occurrence_count = sum(len(item.witnesses) for item in sources)
        component_count = sum(len(item.component_keys) for item in sources)
        state_count = sum(len(item.state_keys) for item in sources)
        maximum_images = sum(32 * len(item.pose_keys) for item in eligible)
        totals = (self.occurrence_count, self.component_count, self.state_count, self.maximum_candidate_images)
        if any(type(item) is not int for item in totals) or occurrence_count > _ADAPTIVE_OCCURRENCE_LIMIT or totals != (occurrence_count, component_count, state_count, maximum_images) or maximum_images > 512:
            raise ValueError("coverage manifest totals mismatch or exceed bound")
        if _require_sha256(self.coverage_manifest_sha256, "coverage manifest hash") != _seal(adaptive_direct_coverage_manifest_payload(self, include_seal=False)):
            raise ValueError("coverage manifest seal mismatch")
        object.__setattr__(self, "complete_source_identities", identities); object.__setattr__(self, "sources", sources)


def adaptive_direct_coverage_manifest_payload(value: AdaptiveDirectCoverageManifest, *, include_seal: bool = True) -> dict[str, object]:
    payload = {
        "schema": value.schema, "family_id": value.family_id, "family_input_sha256": value.family_input_sha256,
        "base_candidate_id": value.base_candidate_id, "base_spec_sha256": value.base_spec_sha256,
        "base_cache_digest": value.base_cache_digest, "base_source_manifest_sha256": value.base_source_manifest_sha256,
        "base_source_snapshot_sha256": value.base_source_snapshot_sha256,
        "metrics_evidence_sha256": value.metrics_evidence_sha256,
        "state_inventory_sha256": value.state_inventory_sha256,
        "metrics_proof": adaptive_candidate_metrics_payload(value.metrics_proof),
        "state_inventory": adaptive_direct_state_inventory_payload(value.state_inventory),
        "complete_source_identities": list(value.complete_source_identities),
        "sources": [adaptive_direct_coverage_source_payload(item) for item in value.sources],
        "occurrence_count": value.occurrence_count, "component_count": value.component_count,
        "state_count": value.state_count, "maximum_candidate_images": value.maximum_candidate_images,
    }
    if include_seal: payload["coverage_manifest_sha256"] = value.coverage_manifest_sha256
    return payload


def _coverage_occurrence_from_payload(raw: object) -> AdaptiveDirectCoverageOccurrenceProof:
    if type(raw) is not dict or set(raw) != _COVERAGE_OCCURRENCE_FIELDS: raise ValueError("coverage occurrence payload fields are invalid")
    return AdaptiveDirectCoverageOccurrenceProof(**raw)


def _coverage_source_from_payload(raw: object) -> AdaptiveDirectCoverageSourceProof:
    fields = {"source_identity", "eligibility_kind", "source_size", "source_sha256", "occurrence_keys", "state_keys", "component_keys", "material_region_keys", "skeleton_contract_sha256", "pose_keys", "equivalence_class_sha256", "metrics_sha256", "state_inventory_sha256", "witnesses", "source_coverage_sha256"}
    if type(raw) is not dict or set(raw) != fields or any(type(raw[name]) is not list for name in ("occurrence_keys", "state_keys", "component_keys", "material_region_keys", "pose_keys", "witnesses")): raise ValueError("coverage source payload fields are invalid")
    copied = dict(raw)
    for name in ("occurrence_keys", "state_keys", "component_keys", "material_region_keys", "pose_keys"): copied[name] = tuple(copied[name])
    copied["witnesses"] = tuple(_coverage_occurrence_from_payload(item) for item in copied["witnesses"])
    return AdaptiveDirectCoverageSourceProof(**copied)


def adaptive_direct_coverage_manifest_from_payload(value: object) -> AdaptiveDirectCoverageManifest:
    fields = {"schema", "family_id", "family_input_sha256", "base_candidate_id", "base_spec_sha256", "base_cache_digest", "base_source_manifest_sha256", "base_source_snapshot_sha256", "metrics_evidence_sha256", "state_inventory_sha256", "metrics_proof", "state_inventory", "complete_source_identities", "sources", "occurrence_count", "component_count", "state_count", "maximum_candidate_images", "coverage_manifest_sha256"}
    if type(value) is not dict or set(value) != fields or type(value["complete_source_identities"]) is not list or type(value["sources"]) is not list: raise ValueError("coverage manifest payload fields are invalid")
    copied = dict(value); copied["complete_source_identities"] = tuple(copied["complete_source_identities"]); copied["sources"] = tuple(_coverage_source_from_payload(item) for item in copied["sources"]); copied["metrics_proof"] = adaptive_candidate_metrics_from_payload(copied["metrics_proof"]); copied["state_inventory"] = adaptive_direct_state_inventory_from_payload(copied["state_inventory"])
    return AdaptiveDirectCoverageManifest(**copied)


@dataclass(frozen=True)
class DirectDroppedTriangleProof:
    ordinal: int
    material: str
    primary_bones: tuple[str, ...]
    reason: Literal["cross-squared-at-most-1e-30"]
    source_sha256: str

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0: raise ValueError("direct triangle ordinal is invalid")
        _require_text(self.material, "direct triangle material")
        bones = _canonical_text_tuple(self.primary_bones, "direct triangle bones")
        if self.reason != "cross-squared-at-most-1e-30": raise ValueError("direct triangle reason is invalid")
        _require_sha256(self.source_sha256, "direct triangle source hash")
        object.__setattr__(self, "primary_bones", bones)


def direct_dropped_triangle_payload(value: DirectDroppedTriangleProof) -> dict[str, object]:
    return {"ordinal": value.ordinal, "material": value.material, "primary_bones": list(value.primary_bones), "reason": value.reason, "source_sha256": value.source_sha256}


@dataclass(frozen=True)
class DirectPrefilterProof:
    schema: Literal[1]
    strategy: Literal["direct-degenerate-prefilter-v1"]
    cross_squared_threshold: float
    source_triangle_count: int
    dropped_count: int
    dropped_fraction: float
    triangles: tuple[DirectDroppedTriangleProof, ...]
    applied: Literal[True]
    evidence_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1 or self.strategy != "direct-degenerate-prefilter-v1" or self.cross_squared_threshold != 1e-30 or self.applied is not True: raise ValueError("direct prefilter identity is invalid")
        if type(self.source_triangle_count) is not int or self.source_triangle_count < 1: raise ValueError("direct source triangle count is invalid")
        triangles = tuple(self.triangles)
        ordinals = tuple(item.ordinal for item in triangles if isinstance(item, DirectDroppedTriangleProof))
        if len(ordinals) != len(triangles) or ordinals != tuple(sorted(ordinals)) or len(set(ordinals)) != len(ordinals) or any(item >= self.source_triangle_count for item in ordinals): raise ValueError("direct dropped triangles are not canonical")
        expected_fraction = len(triangles) / self.source_triangle_count
        if type(self.dropped_count) is not int or self.dropped_count != len(triangles) or len(triangles) > self.source_triangle_count or type(self.dropped_fraction) is not float or self.dropped_fraction != expected_fraction: raise ValueError("direct prefilter totals mismatch")
        if _require_sha256(self.evidence_sha256, "direct prefilter evidence") != _seal(direct_prefilter_payload(self, include_seal=False)): raise ValueError("direct prefilter seal mismatch")
        object.__setattr__(self, "triangles", triangles)


def direct_prefilter_payload(value: DirectPrefilterProof, *, include_seal: bool = True) -> dict[str, object]:
    payload = {"schema": value.schema, "strategy": value.strategy, "cross_squared_threshold": value.cross_squared_threshold, "source_triangle_count": value.source_triangle_count, "dropped_count": value.dropped_count, "dropped_fraction": value.dropped_fraction, "triangles": [direct_dropped_triangle_payload(item) for item in value.triangles], "applied": value.applied}
    if include_seal: payload["evidence_sha256"] = value.evidence_sha256
    return payload


def direct_prefilter_from_payload(value: object) -> DirectPrefilterProof:
    fields = {"schema", "strategy", "cross_squared_threshold", "source_triangle_count", "dropped_count", "dropped_fraction", "triangles", "applied", "evidence_sha256"}
    if type(value) is not dict or set(value) != fields or type(value["triangles"]) is not list: raise ValueError("direct prefilter payload fields are invalid")
    triangles = []
    for raw in value["triangles"]:
        if type(raw) is not dict or set(raw) != {"ordinal", "material", "primary_bones", "reason", "source_sha256"} or type(raw["primary_bones"]) is not list: raise ValueError("direct dropped triangle payload is invalid")
        copied = dict(raw); copied["primary_bones"] = tuple(copied["primary_bones"]); triangles.append(DirectDroppedTriangleProof(**copied))
    copied = dict(value); copied["triangles"] = tuple(triangles); return DirectPrefilterProof(**copied)


@dataclass(frozen=True)
class DirectInputMaterialProof:
    ordinal: int
    material: str
    triangles_before: int
    source_sha256: str

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("direct input material ordinal is invalid")
        _require_text(self.material, "direct input material")
        if "\n" in self.material or "\r" in self.material:
            raise ValueError("direct input material is not one canonical line")
        if type(self.triangles_before) is not int or self.triangles_before < 1:
            raise ValueError("direct input material triangle total is invalid")
        _require_sha256(self.source_sha256, "direct input material source hash")


def direct_input_material_payload(value: DirectInputMaterialProof) -> dict[str, object]:
    return {
        "ordinal": value.ordinal, "material": value.material,
        "triangles_before": value.triangles_before,
        "source_sha256": value.source_sha256,
    }


@dataclass(frozen=True)
class DirectSourceBuildRequest:
    schema: Literal[1]
    family_id: str
    family_input_sha256: str
    base_candidate_id: str
    base_spec_sha256: str
    base_cache_digest: str
    base_source_manifest_sha256: str
    base_source_snapshot_sha256: str
    base_strategy: Literal["blender-adaptive-v1"]
    coverage_manifest_sha256: str
    source_coverage_sha256: str
    optimizer_contract_sha256: str
    whole_profile_sha256: str
    focused_profile_sha256: str
    dependency_proof_sha256: str
    source_identity: str
    source_relative_path: str
    source_size: int
    source_sha256: str
    direct_ratio: float
    strategy: Literal["meshopt-direct-position-v1"]
    transfer: Literal["direct-position-v1"]
    prefilter_version: Literal["direct-degenerate-prefilter-v1"]
    expected_prefilter: DirectPrefilterProof
    expected_materials: tuple[DirectInputMaterialProof, ...]
    request_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1 or self.base_strategy != "blender-adaptive-v1" or self.strategy != "meshopt-direct-position-v1" or self.transfer != "direct-position-v1" or self.prefilter_version != "direct-degenerate-prefilter-v1": raise ValueError("direct request identity is invalid")
        for label, value in (("family id", self.family_id), ("family input", self.family_input_sha256), ("base spec", self.base_spec_sha256), ("base cache", self.base_cache_digest), ("base source manifest", self.base_source_manifest_sha256), ("base source snapshot", self.base_source_snapshot_sha256), ("coverage manifest", self.coverage_manifest_sha256), ("source coverage", self.source_coverage_sha256), ("optimizer contract", self.optimizer_contract_sha256), ("whole profile", self.whole_profile_sha256), ("focused profile", self.focused_profile_sha256), ("dependency proof", self.dependency_proof_sha256), ("source hash", self.source_sha256)): _require_sha256(value, label)
        _require_text(self.base_candidate_id, "direct base candidate")
        _require_relative(self.source_identity, "direct source identity"); _require_relative(self.source_relative_path, "direct source path"); _require_size(self.source_size, "direct source size"); _require_ratio(self.direct_ratio, "direct ratio")
        if not isinstance(self.expected_prefilter, DirectPrefilterProof): raise TypeError("direct expected prefilter is invalid")
        materials = tuple(self.expected_materials)
        post_prefilter_count = self.expected_prefilter.source_triangle_count - self.expected_prefilter.dropped_count
        if (
            not materials
            or any(not isinstance(item, DirectInputMaterialProof) for item in materials)
            or tuple(item.ordinal for item in materials) != tuple(range(len(materials)))
            or len({item.material.casefold() for item in materials}) != len(materials)
            or sum(item.triangles_before for item in materials) != post_prefilter_count
        ):
            raise ValueError("direct expected material inventory is invalid")
        if _require_sha256(self.request_sha256, "direct request hash") != _seal(direct_source_request_payload(self, include_seal=False)): raise ValueError("direct request seal mismatch")
        object.__setattr__(self, "expected_materials", materials)


def direct_source_request_payload(value: DirectSourceBuildRequest, *, include_seal: bool = True) -> dict[str, object]:
    payload = {name: getattr(value, name) for name in ("schema", "family_id", "family_input_sha256", "base_candidate_id", "base_spec_sha256", "base_cache_digest", "base_source_manifest_sha256", "base_source_snapshot_sha256", "base_strategy", "coverage_manifest_sha256", "source_coverage_sha256", "optimizer_contract_sha256", "whole_profile_sha256", "focused_profile_sha256", "dependency_proof_sha256", "source_identity", "source_relative_path", "source_size", "source_sha256", "direct_ratio", "strategy", "transfer", "prefilter_version")}
    payload["expected_prefilter"] = direct_prefilter_payload(value.expected_prefilter)
    payload["expected_materials"] = [direct_input_material_payload(item) for item in value.expected_materials]
    if include_seal: payload["request_sha256"] = value.request_sha256
    return payload


def direct_source_request_from_payload(value: object) -> DirectSourceBuildRequest:
    fields = {"schema", "family_id", "family_input_sha256", "base_candidate_id", "base_spec_sha256", "base_cache_digest", "base_source_manifest_sha256", "base_source_snapshot_sha256", "base_strategy", "coverage_manifest_sha256", "source_coverage_sha256", "optimizer_contract_sha256", "whole_profile_sha256", "focused_profile_sha256", "dependency_proof_sha256", "source_identity", "source_relative_path", "source_size", "source_sha256", "direct_ratio", "strategy", "transfer", "prefilter_version", "expected_prefilter", "expected_materials", "request_sha256"}
    if type(value) is not dict or set(value) != fields or type(value["expected_materials"]) is not list: raise ValueError("direct request payload fields are invalid")
    material_fields = {"ordinal", "material", "triangles_before", "source_sha256"}
    materials = []
    for item in value["expected_materials"]:
        if type(item) is not dict or set(item) != material_fields:
            raise ValueError("direct input material payload is invalid")
        materials.append(DirectInputMaterialProof(**item))
    copied = dict(value); copied["expected_prefilter"] = direct_prefilter_from_payload(copied["expected_prefilter"]); copied["expected_materials"] = tuple(materials); return DirectSourceBuildRequest(**copied)


@dataclass(frozen=True)
class DirectMaterialTriangleProof:
    ordinal: int
    material: str
    triangles_before: int
    target_triangles: int
    triangles_after: int

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("direct material ordinal is invalid")
        _require_text(self.material, "direct material")
        if "\n" in self.material or "\r" in self.material:
            raise ValueError("direct material is not one canonical line")
        if (
            type(self.triangles_before) is not int or self.triangles_before < 1
            or type(self.target_triangles) is not int or self.target_triangles < 1
            or type(self.triangles_after) is not int or self.triangles_after < 1
            or self.triangles_after > self.target_triangles
            or self.target_triangles > self.triangles_before
        ):
            raise ValueError("direct material triangle totals are invalid")


def direct_material_triangle_payload(value: DirectMaterialTriangleProof) -> dict[str, object]:
    return {
        "ordinal": value.ordinal, "material": value.material,
        "triangles_before": value.triangles_before,
        "target_triangles": value.target_triangles,
        "triangles_after": value.triangles_after,
    }


@dataclass(frozen=True)
class DirectSourceSnapshot:
    schema: Literal[1]
    request: DirectSourceBuildRequest
    direct_candidate_id: str
    direct_cache_digest: str
    input_source_root: Path
    source_root: Path
    output_relative_path: str
    output_size: int
    output_sha256: str
    triangles_before: int
    triangles_after: int
    material_triangles: tuple[DirectMaterialTriangleProof, ...]
    prefilter: DirectPrefilterProof
    fallback_reason: None
    preserved_exact: Literal[False]
    reason: Literal["approved-direct-position-v1"]
    snapshot_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1 or not isinstance(self.request, DirectSourceBuildRequest): raise ValueError("direct snapshot identity is invalid")
        if self.direct_candidate_id != direct_candidate_id(self.request) or self.direct_cache_digest != direct_cache_digest(self.request):
            raise ValueError("direct snapshot derived identity mismatch")
        input_root = Path(self.input_source_root)
        root = Path(self.source_root)
        if not input_root.is_absolute() or not root.is_absolute(): raise ValueError("direct snapshot roots must be absolute")
        _require_relative(self.output_relative_path, "direct output path"); _require_size(self.output_size, "direct output size"); _require_sha256(self.output_sha256, "direct output hash")
        if not self.output_relative_path.casefold().endswith(".smd"): raise ValueError("direct output must be an SMD")
        post_prefilter_count = self.request.expected_prefilter.source_triangle_count - self.request.expected_prefilter.dropped_count
        if self.output_sha256 == self.request.source_sha256 or type(self.triangles_before) is not int or type(self.triangles_after) is not int or self.triangles_before != post_prefilter_count or not 0 < self.triangles_after < self.triangles_before: raise ValueError("direct snapshot did not prove strict post-prefilter reduction")
        materials = tuple(self.material_triangles)
        if (
            not materials
            or any(not isinstance(item, DirectMaterialTriangleProof) for item in materials)
            or tuple(item.ordinal for item in materials) != tuple(range(len(materials)))
            or len({item.material.casefold() for item in materials}) != len(materials)
            or sum(item.triangles_before for item in materials) != self.triangles_before
            or sum(item.triangles_after for item in materials) != self.triangles_after
            or any(
                item.target_triangles != max(1, math.floor(item.triangles_before * self.request.direct_ratio))
                for item in materials
            )
        ):
            raise ValueError("direct snapshot material ratio evidence is invalid")
        if tuple(
            (item.ordinal, item.material, item.triangles_before)
            for item in materials
        ) != tuple(
            (item.ordinal, item.material, item.triangles_before)
            for item in self.request.expected_materials
        ):
            raise ValueError("direct snapshot material evidence differs from request input")
        if self.prefilter != self.request.expected_prefilter or self.fallback_reason is not None or self.preserved_exact is not False or self.reason != "approved-direct-position-v1": raise ValueError("direct snapshot result matrix is invalid")
        if _require_sha256(self.snapshot_sha256, "direct snapshot hash") != _seal(direct_source_snapshot_payload(self, include_seal=False)): raise ValueError("direct snapshot seal mismatch")
        object.__setattr__(self, "source_root", root)
        object.__setattr__(self, "input_source_root", input_root)
        object.__setattr__(self, "material_triangles", materials)


def direct_candidate_id(request: DirectSourceBuildRequest) -> str:
    if not isinstance(request, DirectSourceBuildRequest): raise TypeError("direct request is invalid")
    return f"direct-source-{request.request_sha256[:32]}"


def direct_cache_digest(request: DirectSourceBuildRequest) -> str:
    if not isinstance(request, DirectSourceBuildRequest): raise TypeError("direct request is invalid")
    return _seal({"schema": 1, "kind": "direct-source-v1", "request_sha256": request.request_sha256})


def direct_source_snapshot_payload(value: DirectSourceSnapshot, *, include_seal: bool = True) -> dict[str, object]:
    payload = {"schema": value.schema, "request": direct_source_request_payload(value.request), "direct_candidate_id": value.direct_candidate_id, "direct_cache_digest": value.direct_cache_digest, "output_relative_path": value.output_relative_path, "output_size": value.output_size, "output_sha256": value.output_sha256, "triangles_before": value.triangles_before, "triangles_after": value.triangles_after, "material_triangles": [direct_material_triangle_payload(item) for item in value.material_triangles], "prefilter": direct_prefilter_payload(value.prefilter), "fallback_reason": value.fallback_reason, "preserved_exact": value.preserved_exact, "reason": value.reason}
    if include_seal: payload["snapshot_sha256"] = value.snapshot_sha256
    return payload


def direct_source_snapshot_from_payload(
    value: object, *, source_root: Path, input_source_root: Path,
) -> DirectSourceSnapshot:
    fields = {"schema", "request", "direct_candidate_id", "direct_cache_digest", "output_relative_path", "output_size", "output_sha256", "triangles_before", "triangles_after", "material_triangles", "prefilter", "fallback_reason", "preserved_exact", "reason", "snapshot_sha256"}
    if type(value) is not dict or set(value) != fields or type(value["material_triangles"]) is not list: raise ValueError("direct snapshot payload fields are invalid")
    material_fields = {"ordinal", "material", "triangles_before", "target_triangles", "triangles_after"}
    materials = []
    for item in value["material_triangles"]:
        if type(item) is not dict or set(item) != material_fields:
            raise ValueError("direct material triangle payload is invalid")
        materials.append(DirectMaterialTriangleProof(**item))
    copied = dict(value)
    copied["request"] = direct_source_request_from_payload(copied["request"])
    copied["prefilter"] = direct_prefilter_from_payload(copied["prefilter"])
    copied["material_triangles"] = tuple(materials)
    return DirectSourceSnapshot(
        source_root=source_root, input_source_root=input_source_root, **copied
    )


@dataclass(frozen=True)
class ChangedSourceProof:
    source_identity: str
    relative_path: str
    before_size: int
    before_sha256: str
    after_size: int
    after_sha256: str
    overlay_sha256: str
    replacement_snapshot_sha256: str

    def __post_init__(self) -> None:
        _require_relative(self.source_identity, "changed source identity")
        _require_relative(self.relative_path, "changed source path")
        _require_size(self.before_size, "changed source before size")
        _require_size(self.after_size, "changed source after size")
        for label, value in (
            ("before hash", self.before_sha256), ("after hash", self.after_sha256),
            ("overlay hash", self.overlay_sha256),
            ("replacement snapshot hash", self.replacement_snapshot_sha256),
        ):
            _require_sha256(value, label)
        if self.before_sha256 == self.after_sha256:
            raise ValueError("changed source proof must prove changed bytes")


def changed_source_proof_payload(value: ChangedSourceProof) -> dict[str, object]:
    return {
        "source_identity": value.source_identity, "relative_path": value.relative_path,
        "before_size": value.before_size, "before_sha256": value.before_sha256,
        "after_size": value.after_size, "after_sha256": value.after_sha256,
        "overlay_sha256": value.overlay_sha256,
        "replacement_snapshot_sha256": value.replacement_snapshot_sha256,
    }


@dataclass(frozen=True)
class CompileFileProof:
    relative_path: str
    kind: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        _require_relative(self.relative_path, "compile file path")
        _require_text(self.kind, "compile file kind")
        _require_size(self.size, "compile file size")
        _require_sha256(self.sha256, "compile file hash")


def compile_file_proof_payload(value: CompileFileProof) -> dict[str, object]:
    return {
        "relative_path": value.relative_path, "kind": value.kind,
        "size": value.size, "sha256": value.sha256,
    }


def validate_compile_file_proofs(values: tuple[CompileFileProof, ...]) -> tuple[CompileFileProof, ...]:
    proofs = tuple(values)
    keys = [(item.relative_path.casefold(), item.relative_path) for item in proofs]
    if any(not isinstance(item, CompileFileProof) for item in proofs) or keys != sorted(keys) or len({key[0] for key in keys}) != len(keys):
        raise ValueError("compile files are not canonical and unique")
    if len(proofs) > _COMPILE_FILE_LIMIT or sum(item.size for item in proofs) > _COMPILE_BYTE_LIMIT:
        raise ValueError("compile files exceed bounds")
    return proofs


@dataclass(frozen=True)
class CompositionProof:
    schema: int
    recipe_sha256: str
    base_manifest_sha256: str
    composed_manifest_sha256: str
    changed_sources: tuple[ChangedSourceProof, ...]
    evidence_sha256: str
    kind: Literal["focused-recovery-v1", "adaptive-direct-fallback-v1"] = "focused-recovery-v1"

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1:
            raise ValueError("composition proof schema must be 1")
        for label, value in (
            ("recipe", self.recipe_sha256), ("base manifest", self.base_manifest_sha256),
            ("composed manifest", self.composed_manifest_sha256),
        ):
            _require_sha256(value, label)
        if self.kind not in {"focused-recovery-v1", "adaptive-direct-fallback-v1"}:
            raise ValueError("composition proof kind is invalid")
        changed = tuple(self.changed_sources)
        keys = [(item.source_identity.casefold(), item.source_identity) for item in changed]
        changed_limit = 4 if self.kind == "focused-recovery-v1" else 8
        if not changed or len(changed) > changed_limit or any(not isinstance(item, ChangedSourceProof) for item in changed) or keys != sorted(keys) or len({key[0] for key in keys}) != len(keys):
            raise ValueError("composition changed sources are not canonical")
        if _require_sha256(self.evidence_sha256, "composition evidence") != _seal(composition_proof_payload(self, include_seal=False)):
            raise ValueError("composition proof seal mismatch")
        object.__setattr__(self, "changed_sources", changed)

    @classmethod
    def create(cls, kind: str, recipe_sha256: str, base_manifest_sha256: str, composed_manifest_sha256: str, changed_sources: tuple[ChangedSourceProof, ...]) -> "CompositionProof":
        raw = dict(schema=1, kind=kind, recipe_sha256=recipe_sha256,
                   base_manifest_sha256=base_manifest_sha256,
                   composed_manifest_sha256=composed_manifest_sha256,
                   changed_sources=tuple(changed_sources), evidence_sha256=H_EMPTY)
        provisional = _unsealed(cls, **raw)
        raw["evidence_sha256"] = _seal(composition_proof_payload(provisional, include_seal=False))
        return cls(**raw)


def composition_proof_payload(value: CompositionProof, *, include_seal: bool = True) -> dict[str, object]:
    payload = {
        "schema": value.schema, "kind": value.kind, "recipe_sha256": value.recipe_sha256,
        "base_manifest_sha256": value.base_manifest_sha256,
        "composed_manifest_sha256": value.composed_manifest_sha256,
        "changed_sources": [changed_source_proof_payload(item) for item in value.changed_sources],
    }
    if include_seal:
        payload["evidence_sha256"] = value.evidence_sha256
    return payload


def composition_proof_from_payload(value: object) -> CompositionProof:
    fields = {"schema", "kind", "recipe_sha256", "base_manifest_sha256", "composed_manifest_sha256", "changed_sources", "evidence_sha256"}
    if type(value) is not dict or set(value) != fields or type(value["changed_sources"]) is not list:
        raise ValueError("composition proof payload fields are invalid")
    changed = []
    for raw in value["changed_sources"]:
        if type(raw) is not dict or set(raw) != {"source_identity", "relative_path", "before_size", "before_sha256", "after_size", "after_sha256", "overlay_sha256", "replacement_snapshot_sha256"}:
            raise ValueError("changed source proof payload fields are invalid")
        changed.append(ChangedSourceProof(**raw))
    copied = dict(value); copied["changed_sources"] = tuple(changed)
    return CompositionProof(**copied)


@dataclass(frozen=True)
class ComposedSourceTree:
    workspace: Path
    optimized_qc: Path
    source_manifest: SourceTreeManifest
    composition: CompositionProof

    def __post_init__(self) -> None:
        workspace = Path(self.workspace)
        qc = Path(self.optimized_qc)
        if not workspace.is_absolute() or not qc.is_absolute():
            raise ValueError("composed source paths must be absolute")
        try:
            qc.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("composed optimized QC escapes workspace") from exc
        if not isinstance(self.source_manifest, SourceTreeManifest) or not isinstance(self.composition, CompositionProof):
            raise TypeError("composed source proofs are invalid")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "optimized_qc", qc)


def _deep_freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ArtifactStat:
    relative_path: str
    kind: str
    size_bytes: int
    lod_vertices: tuple[int, ...] = ()


@dataclass(frozen=True)
class CompiledSizeSnapshot:
    root: Path
    total_bytes: int
    bytes_by_kind: Mapping[str, int]
    vertices_by_lod: Mapping[int, int]
    artifacts: tuple[ArtifactStat, ...]

    def __post_init__(self) -> None:
        if self.total_bytes != sum(item.size_bytes for item in self.artifacts):
            raise ValueError("total_bytes must equal artifact bytes")
        object.__setattr__(self, "bytes_by_kind", MappingProxyType(dict(self.bytes_by_kind)))
        object.__setattr__(self, "vertices_by_lod", MappingProxyType(dict(self.vertices_by_lod)))

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "total_bytes": self.total_bytes,
            "bytes_by_kind": dict(self.bytes_by_kind),
            "vertices_by_lod": dict(self.vertices_by_lod),
            "artifacts": tuple(asdict(item) for item in self.artifacts),
        }


@dataclass(frozen=True)
class StructuralFingerprint:
    model_name: str
    bodygroups: tuple[str, ...]
    materials: tuple[str, ...]
    skin_families: tuple[tuple[str, ...], ...]
    bones: tuple[str, ...]
    bone_parents: tuple[tuple[str, str], ...]
    attachments: tuple[str, ...]
    hitboxes: tuple[str, ...]
    sequences: tuple[str, ...]
    mesh_files: tuple[str, ...]
    lod_mesh_files: tuple[str, ...]
    physics_mesh: str | None


@dataclass(frozen=True)
class FamilyManifest:
    family_id: str
    model_rel: str
    source_dir: Path
    original_models_dir: Path
    fingerprint: StructuralFingerprint
    input_hash: str
    required_artifact_kinds: tuple[str, ...]


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    engine: EngineName
    target_ratio: float
    target_error: float
    repair_profile: str
    region_overrides: tuple[tuple[str, float], ...] = ()
    strategy: str = "legacy-v1"
    update_vertices: bool = True
    transfer: str = "projection-v1"
    composite_recipe: CompositeRecipe | None = None

    def __post_init__(self) -> None:
        if self.strategy == "blender-adaptive-v1" and (
            self.engine != "blender"
            or self.update_vertices is not True
            or self.transfer != "blender-native-v1"
            or self.repair_profile != "blender-adaptive-v1"
            or self.target_error != 0.0
        ):
            raise ValueError("Blender adaptive strategy contract is immutable")
        direct_strategies = {"meshopt-direct-v1", "meshopt-direct-position-v1"}
        direct_selected = (
            self.strategy in direct_strategies
            or self.update_vertices is False
            or self.transfer == "direct-v1"
        )
        if direct_selected and (
            self.engine != "meshoptimizer"
            or self.strategy not in direct_strategies
            or self.update_vertices is not False
            or self.transfer != "direct-v1"
            or self.repair_profile != self.strategy
        ):
            raise ValueError("meshopt direct strategy contract is immutable")
        if self.composite_recipe is not None and not isinstance(self.composite_recipe, CompositeRecipe):
            raise TypeError("candidate composite recipe is invalid")
        if self.composite_recipe is not None:
            if (
                self.composite_recipe.kind == "focused-recovery-v1"
                and self.candidate_id != "recovery-" + self.composite_recipe.recipe_sha256
            ):
                raise ValueError("focused recovery candidate id must derive from full recipe")
            if self.composite_recipe.kind == "adaptive-direct-fallback-v1" and (
                self.candidate_id != "recovery-" + self.composite_recipe.recipe_sha256
                or self.target_ratio != self.composite_recipe.direct_ratio
                or self.region_overrides
            ):
                raise ValueError("adaptive-direct candidate identity must derive from full global recipe")
            contract = {
                "engine": self.engine, "target_error": self.target_error,
                "repair_profile": self.repair_profile, "strategy": self.strategy,
                "update_vertices": self.update_vertices, "transfer": self.transfer,
            }
            if _seal(contract) != self.composite_recipe.optimizer_contract_sha256:
                raise ValueError("composite recipe optimizer contract differs from candidate")

    def cache_payload(self) -> dict:
        payload = asdict(self)
        payload["region_overrides"] = [
            {"region_key": region_key, "ratio": ratio}
            for region_key, ratio in self.region_overrides
        ]
        if self.composite_recipe is None:
            payload.pop("composite_recipe", None)
        else:
            payload["composite_recipe"] = composite_recipe_payload(self.composite_recipe)
        return payload


@dataclass(frozen=True)
class GateFailure:
    gate: str
    scope: str
    measured: float | str | None
    limit: float | str | None
    message: str


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    failures: tuple[GateFailure, ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)
    worst_scope: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))


def validation_result_payload(value: ValidationResult) -> dict[str, object]:
    if not isinstance(value, ValidationResult) or type(value.passed) is not bool:
        raise ValueError("validation result is invalid")
    if value.passed != (len(value.failures) == 0):
        raise ValueError("validation result is incoherent")
    failures = []
    for item in value.failures:
        if not isinstance(item, GateFailure):
            raise ValueError("validation failure is invalid")
        failures.append(asdict(item))
    metrics: dict[str, float] = {}
    for name, number in value.metrics.items():
        if type(name) is not str or not name or isinstance(number, bool) or type(number) not in (int, float) or not math.isfinite(float(number)):
            raise ValueError("validation metric is invalid")
        metrics[name] = float(number)
    if type(value.worst_scope) is not str:
        raise ValueError("validation worst scope is invalid")
    return {
        "passed": value.passed, "failures": failures,
        "metrics": metrics, "worst_scope": value.worst_scope,
    }


@dataclass(frozen=True)
class StructuralAuthorizationEvidence:
    candidate_cache_digest: str
    composition_evidence_sha256: str
    compile_manifest_sha256: str
    fingerprint_sha256: str
    validation: ValidationResult
    evidence_sha256: str

    def __post_init__(self) -> None:
        for label, value in (
            ("structural candidate cache", self.candidate_cache_digest),
            ("structural composition", self.composition_evidence_sha256),
            ("structural compile manifest", self.compile_manifest_sha256),
            ("structural fingerprint", self.fingerprint_sha256),
        ):
            _require_sha256(value, label)
        validation_result_payload(self.validation)
        if _require_sha256(self.evidence_sha256, "structural evidence") != _seal(structural_authorization_evidence_payload(self, include_seal=False)):
            raise ValueError("structural authorization seal mismatch")


def structural_authorization_evidence_payload(
    value: StructuralAuthorizationEvidence, *, include_seal: bool = True
) -> dict[str, object]:
    payload = {
        "candidate_cache_digest": value.candidate_cache_digest,
        "composition_evidence_sha256": value.composition_evidence_sha256,
        "compile_manifest_sha256": value.compile_manifest_sha256,
        "fingerprint_sha256": value.fingerprint_sha256,
        "validation": validation_result_payload(value.validation),
    }
    if include_seal:
        payload["evidence_sha256"] = value.evidence_sha256
    return payload


@dataclass(frozen=True)
class FocusedRegionPolicy:
    schema: int
    selector: str
    top_k: int
    max_whole_states: int = 16
    max_recovery_rounds: int = 3

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1:
            raise ValueError("focused policy schema must be 1")
        if self.selector != "surface-risk-top-k-v1":
            raise ValueError("focused policy selector is invalid")
        if type(self.top_k) is not int or not 1 <= self.top_k <= 4:
            raise ValueError("focused policy top_k must be an integer from 1 through 4")
        if type(self.max_whole_states) is not int or self.max_whole_states != 16:
            raise ValueError("focused policy whole-state bound must be 16")
        if type(self.max_recovery_rounds) is not int or self.max_recovery_rounds != 3:
            raise ValueError("focused policy recovery bound must be 3")


@dataclass(frozen=True)
class WholeStateEvidence:
    state_index: int
    state_name: str
    bodygroups: tuple[tuple[str, int], ...]
    lod_index: int
    poses: tuple[str, ...]
    source_pairs: tuple[tuple[str, str, str], ...]
    reference_manifest: str
    reference_manifest_sha256: str
    candidate_manifest: str
    candidate_manifest_sha256: str
    geometry_rows: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "bodygroups", tuple(tuple(item) for item in self.bodygroups))
        object.__setattr__(self, "poses", tuple(self.poses))
        object.__setattr__(self, "source_pairs", tuple(tuple(item) for item in self.source_pairs))
        object.__setattr__(
            self, "geometry_rows", tuple(_deep_freeze(dict(row)) for row in self.geometry_rows)
        )


@dataclass(frozen=True)
class FocusTarget:
    rank: int
    region_key: str
    source_identity: str
    state_index: int
    state_name: str
    bodygroups: tuple[tuple[str, int], ...]
    lod_index: int
    anchor_pose: str
    surface_bidirectional_p95: float
    surface_max: float
    normalized_p95: float
    normalized_max: float
    selector_input_sha256: str


@dataclass(frozen=True)
class FocusRegionResult:
    target: FocusTarget
    validation: ValidationResult
    evidence_sha256: str
    cache_hit: bool


@dataclass(frozen=True)
class FocusedGateResult:
    validation: ValidationResult
    targets: tuple[FocusTarget, ...]
    regions: Mapping[str, FocusRegionResult]
    evidence_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "targets", tuple(self.targets))
        object.__setattr__(self, "regions", _deep_freeze(dict(self.regions)))


@dataclass(frozen=True)
class CandidateEvaluation:
    spec: CandidateSpec
    size: CompiledSizeSnapshot
    structural: ValidationResult
    visual: ValidationResult
    compiled_models_dir: Path
    whole_visual: ValidationResult | None = None
    focused_by_region: Mapping[str, FocusRegionResult] = field(default_factory=dict)

    def __post_init__(self) -> None:
        whole = self.visual if self.whole_visual is None else self.whole_visual
        if not isinstance(whole, ValidationResult):
            raise TypeError("whole visual result is invalid")
        focused = dict(self.focused_by_region)
        if any(
            type(key) is not str
            or not isinstance(value, FocusRegionResult)
            or value.target.region_key != key
            for key, value in focused.items()
        ):
            raise ValueError("focused region results are invalid")
        object.__setattr__(self, "whole_visual", whole)
        object.__setattr__(self, "focused_by_region", _deep_freeze(focused))

    @property
    def passed(self) -> bool:
        return self.structural.passed and self.visual.passed


@dataclass(frozen=True)
class CandidateAttempt:
    spec: CandidateSpec
    status: Literal["generated", "compile_failed", "evaluated", "cancelled"]
    evaluation: CandidateEvaluation | None
    error: str = ""


@dataclass(frozen=True)
class SearchBudget:
    max_candidates: int
    min_ratio_step: float
    min_marginal_saving: float

    @classmethod
    def experimental_default(cls) -> "SearchBudget":
        return cls(max_candidates=18, min_ratio_step=0.025, min_marginal_saving=0.005)


@dataclass(frozen=True)
class FamilyOutcome:
    manifest: FamilyManifest
    status: FamilyStatus
    selected: CandidateEvaluation | None
    attempted: tuple[CandidateAttempt, ...]
    reason: str
