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
        if not overlays or len(overlays) > 4 or any(not isinstance(item, SourceOverlay) for item in overlays) or keys != sorted(keys) or len({key[0] for key in keys}) != len(keys):
            raise ValueError("recipe overlays are not canonical or exceed bound")
        if self.kind == "focused-recovery-v1":
            if self.direct_ratio is not None or self.prefilter_version is not None or any(item.mode == "direct-position" for item in overlays):
                raise ValueError("focused recovery recipe mode matrix is invalid")
        else:
            ratio = _require_ratio(self.direct_ratio, "recipe direct ratio")
            if not self.prefilter_version or any(item.mode != "direct-position" or item.effective_ratio != ratio for item in overlays):
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
    if include_seal:
        payload["recipe_sha256"] = value.recipe_sha256
    return payload


def composite_recipe_from_payload(value: object) -> CompositeRecipe:
    fields = {
        "schema", "kind", "family_id", "family_input_sha256", "base_candidate_id",
        "base_spec_sha256", "base_cache_digest", "base_source_manifest_sha256",
        "optimizer_contract_sha256", "whole_profile_sha256", "focused_profile_sha256",
        "dependency_proof_sha256", "round_index", "direct_ratio", "overlays",
        "selector_version", "prefilter_version", "recipe_sha256",
    }
    if type(value) is not dict or set(value) != fields or type(value["overlays"]) is not list:
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

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1:
            raise ValueError("composition proof schema must be 1")
        for label, value in (
            ("recipe", self.recipe_sha256), ("base manifest", self.base_manifest_sha256),
            ("composed manifest", self.composed_manifest_sha256),
        ):
            _require_sha256(value, label)
        changed = tuple(self.changed_sources)
        keys = [(item.source_identity.casefold(), item.source_identity) for item in changed]
        if not changed or len(changed) > 4 or any(not isinstance(item, ChangedSourceProof) for item in changed) or keys != sorted(keys) or len({key[0] for key in keys}) != len(keys):
            raise ValueError("composition changed sources are not canonical")
        if _require_sha256(self.evidence_sha256, "composition evidence") != _seal(composition_proof_payload(self, include_seal=False)):
            raise ValueError("composition proof seal mismatch")
        object.__setattr__(self, "changed_sources", changed)


def composition_proof_payload(value: CompositionProof, *, include_seal: bool = True) -> dict[str, object]:
    payload = {
        "schema": value.schema, "recipe_sha256": value.recipe_sha256,
        "base_manifest_sha256": value.base_manifest_sha256,
        "composed_manifest_sha256": value.composed_manifest_sha256,
        "changed_sources": [changed_source_proof_payload(item) for item in value.changed_sources],
    }
    if include_seal:
        payload["evidence_sha256"] = value.evidence_sha256
    return payload


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
