from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import threading
from typing import Literal

from .candidates import _direct_input_material_proofs
from .direct_source_runner import (
    _DeadlineEvent,
    _abspath,
    _acquire_run_root,
    _bounded_artifact_proofs,
    _cancel,
    _directory_identity,
    _overlaps,
    _preserve_quarantine,
    _publish_no_replace,
    _regular_identity,
    _write_new,
)
from .domain import (
    DirectSourceBuildRequest,
    SourceFileProof,
    direct_source_request_from_payload,
    direct_source_request_payload,
)
from .focused_cache import (
    _copy_file_no_follow,
    _file_proof,
    _has_reparse_ancestor,
    _read_regular_no_follow,
)
from .processes import ProcessCancelledError, ProcessResult, run_process
from .visual_remapped_topology import (
    VisualRemappedTopologyProof,
    validate_visual_remapped_topology_smd,
    visual_remapped_topology_proof_from_payload,
    visual_remapped_topology_proof_payload,
)


ProcessRunner = Callable[
    [Sequence[str | Path], Path, Path, threading.Event], ProcessResult
]

_STRATEGY = "meshopt-remapped-visual-v1"
_TRANSFER = "visual-remapped-topology-v1"
_MAX_SOURCE_BYTES = 512 * 1024**2
_MAX_ARTIFACT_FILES = 64
_MAX_ARTIFACT_BYTES = 2 * 1024**3
_MAX_PROCESS_SECONDS = 30 * 60.0


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _is_sha(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and value == value.casefold()
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True)
class VisualRemappedSourceRequest:
    schema: Literal[1]
    source_request: DirectSourceBuildRequest
    input_size: int
    input_sha256: str
    requested_ratio: float
    strategy: Literal["meshopt-remapped-visual-v1"]
    transfer: Literal["visual-remapped-topology-v1"]
    quality_status: Literal["unverified"]
    authorizing: Literal[False]
    request_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.schema) is not int
            or self.schema != 1
            or not isinstance(self.source_request, DirectSourceBuildRequest)
            or type(self.input_size) is not int
            or self.input_size < 1
            or not _is_sha(self.input_sha256)
            or type(self.requested_ratio) is not float
            or not math.isfinite(self.requested_ratio)
            or self.requested_ratio != self.source_request.direct_ratio
            or self.strategy != _STRATEGY
            or self.transfer != _TRANSFER
            or self.quality_status != "unverified"
            or self.authorizing is not False
            or not _is_sha(self.request_sha256)
            or self.request_sha256 != _canonical_digest(
                visual_remapped_source_request_payload(self, include_seal=False)
            )
        ):
            raise ValueError("visual remapped source request is invalid")


def visual_remapped_source_request_payload(
    request: VisualRemappedSourceRequest, *, include_seal: bool = True,
) -> dict[str, object]:
    if not isinstance(request, VisualRemappedSourceRequest):
        raise TypeError("visual remapped source request is invalid")
    payload = {
        "schema": request.schema,
        "source_request": direct_source_request_payload(request.source_request),
        "input_size": request.input_size,
        "input_sha256": request.input_sha256,
        "requested_ratio": request.requested_ratio,
        "strategy": request.strategy,
        "transfer": request.transfer,
        "quality_status": request.quality_status,
        "authorizing": request.authorizing,
    }
    if include_seal:
        payload["request_sha256"] = request.request_sha256
    return payload


def visual_remapped_source_request_from_payload(
    value: object,
) -> VisualRemappedSourceRequest:
    fields = {
        "schema", "source_request", "input_size", "input_sha256",
        "requested_ratio", "strategy", "transfer", "quality_status",
        "authorizing", "request_sha256",
    }
    if type(value) is not dict or set(value) != fields:
        raise ValueError("visual remapped source request fields are invalid")
    copied = dict(value)
    copied["source_request"] = direct_source_request_from_payload(
        copied["source_request"]
    )
    return VisualRemappedSourceRequest(**copied)


def visual_remapped_source_request(
    source_request: DirectSourceBuildRequest, input_bytes: bytes,
) -> VisualRemappedSourceRequest:
    if not isinstance(source_request, DirectSourceBuildRequest):
        raise TypeError("visual remapped source base request is invalid")
    if type(input_bytes) is not bytes or not input_bytes:
        raise ValueError("visual remapped source input bytes are invalid")
    values = dict(
        schema=1,
        source_request=source_request,
        input_size=len(input_bytes),
        input_sha256=hashlib.sha256(input_bytes).hexdigest(),
        requested_ratio=float(source_request.direct_ratio),
        strategy=_STRATEGY,
        transfer=_TRANSFER,
        quality_status="unverified",
        authorizing=False,
        request_sha256="0" * 64,
    )
    provisional = VisualRemappedSourceRequest.__new__(VisualRemappedSourceRequest)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["request_sha256"] = _canonical_digest(
        visual_remapped_source_request_payload(provisional, include_seal=False)
    )
    return VisualRemappedSourceRequest(**values)


def visual_remapped_candidate_id(request: VisualRemappedSourceRequest) -> str:
    if not isinstance(request, VisualRemappedSourceRequest):
        raise TypeError("visual remapped source request is invalid")
    return f"visual-remapped-{request.request_sha256[:32]}"


def visual_remapped_cache_digest(request: VisualRemappedSourceRequest) -> str:
    if not isinstance(request, VisualRemappedSourceRequest):
        raise TypeError("visual remapped source request is invalid")
    return _canonical_digest({
        "schema": 1,
        "kind": "visual-remapped-source-v1",
        "request_sha256": request.request_sha256,
    })


@dataclass(frozen=True)
class VisualRemappedToolProof:
    role: Literal["batch-script", "blender", "meshoptimizer"]
    name: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            self.role not in {"batch-script", "blender", "meshoptimizer"}
            or type(self.name) is not str
            or not self.name
            or "/" in self.name
            or "\\" in self.name
            or type(self.size) is not int
            or self.size < 1
            or not _is_sha(self.sha256)
        ):
            raise ValueError("visual remapped tool proof is invalid")


def _tool_payload(tool: VisualRemappedToolProof) -> dict[str, object]:
    return {
        "role": tool.role,
        "name": tool.name,
        "size": tool.size,
        "sha256": tool.sha256,
    }


@dataclass(frozen=True)
class VisualRemappedToolchainProof:
    schema: Literal[1]
    tools: tuple[VisualRemappedToolProof, ...]
    toolchain_sha256: str

    def __post_init__(self) -> None:
        tools = tuple(self.tools)
        if (
            type(self.schema) is not int
            or self.schema != 1
            or any(not isinstance(item, VisualRemappedToolProof) for item in tools)
            or tuple(item.role for item in tools)
            != ("batch-script", "blender", "meshoptimizer")
            or not _is_sha(self.toolchain_sha256)
            or self.toolchain_sha256 != _canonical_digest({
                "schema": 1, "tools": [_tool_payload(item) for item in tools],
            })
        ):
            raise ValueError("visual remapped toolchain proof is invalid")
        object.__setattr__(self, "tools", tools)


def _toolchain_payload(value: VisualRemappedToolchainProof) -> dict[str, object]:
    return {
        "schema": value.schema,
        "tools": [_tool_payload(item) for item in value.tools],
        "toolchain_sha256": value.toolchain_sha256,
    }


def _toolchain_from_payload(value: object) -> VisualRemappedToolchainProof:
    if (
        type(value) is not dict
        or set(value) != {"schema", "tools", "toolchain_sha256"}
        or type(value["tools"]) is not list
    ):
        raise ValueError("visual remapped toolchain fields are invalid")
    tools = []
    for item in value["tools"]:
        if type(item) is not dict or set(item) != {"role", "name", "size", "sha256"}:
            raise ValueError("visual remapped tool fields are invalid")
        tools.append(VisualRemappedToolProof(**item))
    return VisualRemappedToolchainProof(
        schema=value["schema"], tools=tuple(tools),
        toolchain_sha256=value["toolchain_sha256"],
    )


def _artifact_payload(value: SourceFileProof) -> dict[str, object]:
    return {
        "file_identity": value.file_identity,
        "kind": value.kind,
        "relative_path": value.relative_path,
        "size": value.size,
        "sha256": value.sha256,
    }


@dataclass(frozen=True)
class VisualRemappedSourceEvidence:
    schema: Literal[1]
    request: VisualRemappedSourceRequest
    candidate_id: str
    cache_digest: str
    quality_status: Literal["unverified"]
    authorizing: Literal[False]
    source_size: int
    source_sha256: str
    output_size: int
    output_sha256: str
    requested_ratio: float
    achieved_ratio: float
    triangles_before: int
    triangles_after: int
    boundary_changed: bool
    retained_boundary_edges: int
    removed_boundary_edges: int
    added_boundary_edges: int
    topology: VisualRemappedTopologyProof
    artifacts: tuple[SourceFileProof, ...]
    toolchain: VisualRemappedToolchainProof
    engine_version: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.request, VisualRemappedSourceRequest)
            and self.cache_digest != visual_remapped_cache_digest(self.request)
        ):
            raise ValueError("visual remapped source evidence cache digest is stale")
        artifacts = tuple(self.artifacts)
        artifact_keys = tuple(item.relative_path for item in artifacts)
        artifact_by_path = {item.relative_path: item for item in artifacts}
        output_artifact = artifact_by_path.get("output/source_opt.smd")
        source_artifact = artifact_by_path.get("source.smd")
        if (
            type(self.schema) is not int
            or self.schema != 1
            or not isinstance(self.request, VisualRemappedSourceRequest)
            or self.candidate_id != visual_remapped_candidate_id(self.request)
            or self.cache_digest != visual_remapped_cache_digest(self.request)
            or self.quality_status != "unverified"
            or self.authorizing is not False
            or type(self.source_size) is not int
            or self.source_size != self.request.input_size
            or self.source_sha256 != self.request.input_sha256
            or type(self.output_size) is not int
            or self.output_size < 1
            or not _is_sha(self.output_sha256)
            or not isinstance(self.topology, VisualRemappedTopologyProof)
            or self.topology.strategy != _STRATEGY
            or self.topology.transfer != _TRANSFER
            or self.topology.quality_status != "unverified"
            or self.topology.source_sha256 != self.source_sha256
            or self.topology.output_sha256 != self.output_sha256
            or self.requested_ratio != self.request.requested_ratio
            or self.requested_ratio != self.topology.requested_ratio
            or self.achieved_ratio != self.topology.achieved_ratio
            or self.triangles_before != self.topology.triangles_before
            or self.triangles_after != self.topology.triangles_after
            or self.boundary_changed != self.topology.boundary_changed
            or self.retained_boundary_edges != self.topology.retained_boundary_edges
            or self.removed_boundary_edges != self.topology.removed_boundary_edges
            or self.added_boundary_edges != self.topology.added_boundary_edges
            or not artifacts
            or any(not isinstance(item, SourceFileProof) for item in artifacts)
            or artifact_keys != tuple(sorted(artifact_keys))
            or len(set(artifact_keys)) != len(artifact_keys)
            or len(artifacts) > _MAX_ARTIFACT_FILES
            or sum(item.size for item in artifacts) > _MAX_ARTIFACT_BYTES
            or source_artifact is None
            or (source_artifact.size, source_artifact.sha256)
            != (self.source_size, self.source_sha256)
            or output_artifact is None
            or (output_artifact.size, output_artifact.sha256)
            != (self.output_size, self.output_sha256)
            or not isinstance(self.toolchain, VisualRemappedToolchainProof)
            or type(self.engine_version) is not str
            or not self.engine_version
            or len(self.engine_version) > 128
            or "\n" in self.engine_version
            or "\r" in self.engine_version
            or not _is_sha(self.evidence_sha256)
            or self.evidence_sha256 != _canonical_digest(
                visual_remapped_source_evidence_payload(self, include_seal=False)
            )
        ):
            raise ValueError("visual remapped source evidence relationships are invalid")
        object.__setattr__(self, "artifacts", artifacts)


def visual_remapped_source_evidence_payload(
    evidence: VisualRemappedSourceEvidence, *, include_seal: bool = True,
) -> dict[str, object]:
    if not isinstance(evidence, VisualRemappedSourceEvidence):
        raise TypeError("visual remapped source evidence is invalid")
    payload = {
        "schema": evidence.schema,
        "request": visual_remapped_source_request_payload(evidence.request),
        "candidate_id": evidence.candidate_id,
        "cache_digest": evidence.cache_digest,
        "quality_status": evidence.quality_status,
        "authorizing": evidence.authorizing,
        "source_size": evidence.source_size,
        "source_sha256": evidence.source_sha256,
        "output_size": evidence.output_size,
        "output_sha256": evidence.output_sha256,
        "requested_ratio": evidence.requested_ratio,
        "achieved_ratio": evidence.achieved_ratio,
        "triangles_before": evidence.triangles_before,
        "triangles_after": evidence.triangles_after,
        "boundary_changed": evidence.boundary_changed,
        "retained_boundary_edges": evidence.retained_boundary_edges,
        "removed_boundary_edges": evidence.removed_boundary_edges,
        "added_boundary_edges": evidence.added_boundary_edges,
        "topology": visual_remapped_topology_proof_payload(evidence.topology),
        "artifacts": [_artifact_payload(item) for item in evidence.artifacts],
        "toolchain": _toolchain_payload(evidence.toolchain),
        "engine_version": evidence.engine_version,
    }
    if include_seal:
        payload["evidence_sha256"] = evidence.evidence_sha256
    return payload


def visual_remapped_source_evidence_from_payload(
    value: object,
) -> VisualRemappedSourceEvidence:
    fields = set(VisualRemappedSourceEvidence.__dataclass_fields__)
    if type(value) is not dict or set(value) != fields:
        raise ValueError("visual remapped source evidence fields are invalid")
    if type(value["artifacts"]) is not list:
        raise ValueError("visual remapped source artifact collection is invalid")
    artifact_fields = {"file_identity", "kind", "relative_path", "size", "sha256"}
    artifacts = []
    for item in value["artifacts"]:
        if type(item) is not dict or set(item) != artifact_fields:
            raise ValueError("visual remapped source artifact fields are invalid")
        artifacts.append(SourceFileProof(**item))
    copied = dict(value)
    copied["request"] = visual_remapped_source_request_from_payload(copied["request"])
    copied["topology"] = visual_remapped_topology_proof_from_payload(copied["topology"])
    copied["artifacts"] = tuple(artifacts)
    copied["toolchain"] = _toolchain_from_payload(copied["toolchain"])
    return VisualRemappedSourceEvidence(**copied)


@dataclass(frozen=True)
class VisualRemappedRunnerTools:
    blender_exe: Path
    repo_root: Path
    meshopt_dll: Path
    work_root: Path
    process_runner: ProcessRunner = field(default=run_process, repr=False, compare=False)
    max_source_bytes: int = _MAX_SOURCE_BYTES
    max_artifact_files: int = _MAX_ARTIFACT_FILES
    max_artifact_bytes: int = _MAX_ARTIFACT_BYTES
    max_process_seconds: float = _MAX_PROCESS_SECONDS

    def __post_init__(self) -> None:
        for name in ("blender_exe", "repo_root", "meshopt_dll", "work_root"):
            object.__setattr__(self, name, _abspath(getattr(self, name)))
        if not callable(self.process_runner):
            raise TypeError("visual remapped process runner is invalid")
        if (
            type(self.max_source_bytes) is not int
            or self.max_source_bytes < 1
            or type(self.max_artifact_files) is not int
            or self.max_artifact_files < 1
            or type(self.max_artifact_bytes) is not int
            or self.max_artifact_bytes < 1
            or type(self.max_process_seconds) not in (int, float)
            or not math.isfinite(float(self.max_process_seconds))
            or self.max_process_seconds <= 0
        ):
            raise ValueError("visual remapped runner budget is invalid")
        if (
            not self.repo_root.is_dir()
            or not self.work_root.is_dir()
            or _has_reparse_ancestor(self.repo_root)
            or _has_reparse_ancestor(self.work_root)
        ):
            raise ValueError("visual remapped runner roots are unavailable or unsafe")
        _directory_identity(self.work_root)
        _regular_identity(self.blender_exe)
        _regular_identity(self.meshopt_dll)
        _regular_identity(self.repo_root / "batch_optimize_maximum.py")


def _toolchain_proof(
    tools: VisualRemappedRunnerTools, event,
) -> VisualRemappedToolchainProof:
    paths = (
        ("batch-script", tools.repo_root / "batch_optimize_maximum.py", 8 * 1024**2),
        ("blender", tools.blender_exe, 1024**3),
        ("meshoptimizer", tools.meshopt_dll, 64 * 1024**2),
    )
    proofs = []
    for role, path, maximum in paths:
        size, digest = _file_proof(path, event, max_bytes=maximum)
        proofs.append(VisualRemappedToolProof(
            role=role, name=path.name, size=size, sha256=digest,
        ))
    payload = {"schema": 1, "tools": [_tool_payload(item) for item in proofs]}
    return VisualRemappedToolchainProof(
        schema=1, tools=tuple(proofs), toolchain_sha256=_canonical_digest(payload),
    )


def _validate_metrics(
    root: Path,
    request: VisualRemappedSourceRequest,
    topology: VisualRemappedTopologyProof,
    event,
    *,
    max_source_bytes: int,
) -> str:
    raw = _read_regular_no_follow(
        root / "candidate_metrics.json", event,
        contained_root=root, max_bytes=max_source_bytes,
    )
    try:
        metrics = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("visual remapped runner metrics are invalid JSON") from exc
    version = metrics.get("engine_version") if type(metrics) is dict else None
    if (
        type(metrics) is not dict
        or metrics.get("schema_version") != 1
        or metrics.get("candidate_id") != visual_remapped_candidate_id(request)
        or metrics.get("engine") != "meshoptimizer"
        or type(version) is not str
        or not version
        or len(version) > 128
        or metrics.get("triangles_before") != topology.triangles_before
        or metrics.get("triangles_after") != topology.triangles_after
    ):
        raise ValueError("visual remapped runner metrics identity or totals differ")
    files = metrics.get("files")
    if type(files) is not list or len(files) != 1 or type(files[0]) is not dict:
        raise ValueError("visual remapped runner metrics file coverage differs")
    item = files[0]
    objects = item.get("objects")
    source_value = item.get("source")
    output_value = item.get("output")
    if (
        type(source_value) is not str
        or type(output_value) is not str
        or Path(os.path.abspath(source_value)) != root / "source.smd"
        or Path(os.path.abspath(output_value)) != root / "output" / "source_opt.smd"
        or item.get("triangles_before") != topology.triangles_before
        or item.get("triangles_after") != topology.triangles_after
        or type(objects) is not list
        or not objects
        or any(
            type(value) is not dict
            or value.get("strategy") != _STRATEGY
            or value.get("transfer") != _TRANSFER
            for value in objects
        )
    ):
        raise ValueError("visual remapped runner per-file strategy evidence differs")
    provenance = metrics.get("provenance")
    if (
        type(provenance) is not list
        or len(provenance) != 1
        or type(provenance[0]) is not dict
    ):
        raise ValueError("visual remapped runner provenance coverage differs")
    record = provenance[0]
    if (
        record.get("graph_file") != "model.qc"
        or record.get("directive") != "$body"
        or record.get("logical_path") != "source.smd"
        or record.get("role") != "visual"
        or record.get("status") != "optimized"
        or record.get("source_sha256") != topology.source_sha256
        or record.get("output") != "output/source_opt.smd"
        or record.get("output_sha256") != topology.output_sha256
    ):
        raise ValueError("visual remapped runner provenance differs")
    manifest_size, manifest_hash = _file_proof(
        root / "maximum_region_manifest.json", event,
        contained_root=root, max_bytes=max_source_bytes,
    )
    if (
        manifest_size < 1
        or metrics.get("region_manifest") != "maximum_region_manifest.json"
        or metrics.get("region_manifest_sha256") != manifest_hash
    ):
        raise ValueError("visual remapped runner region manifest proof differs")
    return version


def _build_evidence(
    request: VisualRemappedSourceRequest,
    topology: VisualRemappedTopologyProof,
    artifacts: tuple[SourceFileProof, ...],
    toolchain: VisualRemappedToolchainProof,
    engine_version: str,
    output_size: int,
) -> VisualRemappedSourceEvidence:
    values = dict(
        schema=1,
        request=request,
        candidate_id=visual_remapped_candidate_id(request),
        cache_digest=visual_remapped_cache_digest(request),
        quality_status="unverified",
        authorizing=False,
        source_size=request.input_size,
        source_sha256=request.input_sha256,
        output_size=output_size,
        output_sha256=topology.output_sha256,
        requested_ratio=topology.requested_ratio,
        achieved_ratio=topology.achieved_ratio,
        triangles_before=topology.triangles_before,
        triangles_after=topology.triangles_after,
        boundary_changed=topology.boundary_changed,
        retained_boundary_edges=topology.retained_boundary_edges,
        removed_boundary_edges=topology.removed_boundary_edges,
        added_boundary_edges=topology.added_boundary_edges,
        topology=topology,
        artifacts=artifacts,
        toolchain=toolchain,
        engine_version=engine_version,
        evidence_sha256="0" * 64,
    )
    provisional = VisualRemappedSourceEvidence.__new__(VisualRemappedSourceEvidence)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["evidence_sha256"] = _canonical_digest(
        visual_remapped_source_evidence_payload(provisional, include_seal=False)
    )
    return VisualRemappedSourceEvidence(**values)


@dataclass(frozen=True)
class VisualRemappedRunResult:
    request_sha256: str
    run_root: Path
    process: ProcessResult
    evidence: VisualRemappedSourceEvidence

    def __post_init__(self) -> None:
        if (
            not _is_sha(self.request_sha256)
            or not Path(self.run_root).is_absolute()
            or not isinstance(self.process, ProcessResult)
            or not isinstance(self.evidence, VisualRemappedSourceEvidence)
            or self.request_sha256 != self.evidence.request.request_sha256
        ):
            raise ValueError("visual remapped run result is invalid")


class BlenderVisualRemappedSourceRunner:
    """Generate one structurally validated, explicitly unverified candidate."""

    def __init__(self, tools: VisualRemappedRunnerTools) -> None:
        if not isinstance(tools, VisualRemappedRunnerTools):
            raise TypeError("visual remapped runner tools are invalid")
        self.tools = tools

    def __call__(
        self,
        input_path: Path,
        output_path: Path,
        request: VisualRemappedSourceRequest,
        cancel_event: threading.Event | None,
    ) -> VisualRemappedRunResult:
        if not isinstance(request, VisualRemappedSourceRequest):
            raise TypeError("visual remapped source runner request is invalid")
        if request.strategy != _STRATEGY or request.transfer != _TRANSFER:
            raise ValueError("visual remapped source runner strategy is invalid")
        event = cancel_event or threading.Event()
        _cancel(event, "visual remapped source runner cancelled before preflight")
        source = _abspath(input_path)
        destination = _abspath(output_path)
        if (
            not source.is_file()
            or _has_reparse_ancestor(source)
            or not destination.parent.is_dir()
            or _has_reparse_ancestor(destination.parent)
            or os.path.lexists(destination)
            or _overlaps(self.tools.work_root, source)
            or _overlaps(self.tools.work_root, destination)
        ):
            raise ValueError("visual remapped runner input/output boundary is unsafe")
        source_bytes = _read_regular_no_follow(
            source, event, contained_root=source.parent,
            max_bytes=self.tools.max_source_bytes,
        )
        if (
            len(source_bytes) != request.input_size
            or hashlib.sha256(source_bytes).hexdigest() != request.input_sha256
        ):
            raise ValueError("visual remapped runner input byte proof differs")
        try:
            source_text = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("visual remapped runner input is not UTF-8") from exc
        if (
            _direct_input_material_proofs(source_text)
            != request.source_request.expected_materials
        ):
            raise ValueError("visual remapped runner input material proof differs")
        work_identity = _directory_identity(self.tools.work_root)
        initial_tools = _toolchain_proof(self.tools, event)
        run_root: Path | None = None
        ownership: tuple[int, int, int] | None = None
        try:
            _cancel(event, "visual remapped source runner cancelled before acquisition")
            if _directory_identity(self.tools.work_root) != work_identity:
                raise ValueError("visual remapped runner work root changed")
            run_root, ownership = _acquire_run_root(
                self.tools.work_root, request.request_sha256,
            )
            _copy_file_no_follow(
                source, run_root / "source.smd", event, contained_root=source.parent,
            )
            copied_source = _read_regular_no_follow(
                run_root / "source.smd", event, contained_root=run_root,
                max_bytes=self.tools.max_source_bytes,
            )
            if copied_source != source_bytes:
                raise ValueError("visual remapped runner source changed during snapshot")
            _write_new(
                run_root / "model.qc",
                b'$modelname "maximum/visual-remapped.mdl"\n$body "body" "source.smd"\n',
            )
            candidate = {
                "candidate_id": visual_remapped_candidate_id(request),
                "engine": "meshoptimizer",
                "ratio": request.requested_ratio,
                "target_error": 0.01,
                "update_vertices": False,
                "region_overrides": [],
                "strategy": _STRATEGY,
                "direct_degenerate_prefilter": "direct-degenerate-prefilter-v1",
                "transfer": _TRANSFER,
            }
            _write_new(
                run_root / "candidate.json",
                json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )
            command = (
                str(self.tools.blender_exe),
                "--background",
                "--python-exit-code",
                "1",
                "--python",
                str(self.tools.repo_root / "batch_optimize_maximum.py"),
                "--",
                str(run_root),
                "--candidate-json",
                str(run_root / "candidate.json"),
                "--meshopt-dll",
                str(self.tools.meshopt_dll),
            )
            deadline = _DeadlineEvent(event, float(self.tools.max_process_seconds))
            process = self.tools.process_runner(
                command, run_root, run_root / "blender.log", deadline,
            )
            if not isinstance(process, ProcessResult):
                raise TypeError("visual remapped runner process result is invalid")
            if process.command != command or Path(process.log_path) != run_root / "blender.log":
                raise ValueError("visual remapped runner process identity differs")
            if process.elapsed_seconds > self.tools.max_process_seconds:
                raise ProcessCancelledError("visual remapped runner time budget exceeded")
            _cancel(deadline, "visual remapped source runner cancelled after process")
            if process.returncode != 0:
                raise RuntimeError(
                    f"visual remapped Blender failed with exit code {process.returncode}"
                )
            if _directory_identity(run_root) != ownership:
                raise ValueError("visual remapped runner root changed during process")
            if _toolchain_proof(self.tools, event) != initial_tools:
                raise ValueError("visual remapped runner tool changed during process")
            optimized = run_root / "output" / "source_opt.smd"
            optimized_bytes = _read_regular_no_follow(
                optimized, event, contained_root=run_root,
                max_bytes=self.tools.max_source_bytes,
            )
            try:
                optimized_text = optimized_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("visual remapped runner output is not UTF-8") from exc
            topology = validate_visual_remapped_topology_smd(
                source_text, optimized_text, request.requested_ratio,
            )
            if (
                topology.source_sha256 != request.input_sha256
                or topology.output_sha256
                != hashlib.sha256(optimized_bytes).hexdigest()
            ):
                raise ValueError("visual remapped runner topology byte proof differs")
            engine_version = _validate_metrics(
                run_root, request, topology, event,
                max_source_bytes=self.tools.max_source_bytes,
            )
            artifacts = _bounded_artifact_proofs(
                run_root, event,
                max_files=self.tools.max_artifact_files,
                max_bytes=self.tools.max_artifact_bytes,
            )
            evidence = _build_evidence(
                request, topology, artifacts, initial_tools, engine_version,
                len(optimized_bytes),
            )
            if _directory_identity(run_root) != ownership:
                raise ValueError("visual remapped runner root changed before publication")
            if _bounded_artifact_proofs(
                run_root, event,
                max_files=self.tools.max_artifact_files,
                max_bytes=self.tools.max_artifact_bytes,
            ) != artifacts:
                raise ValueError("visual remapped runner artifacts changed before publication")
            if _toolchain_proof(self.tools, event) != initial_tools:
                raise ValueError("visual remapped runner tool changed before publication")
            _cancel(event, "visual remapped source runner cancelled before publication")
            _publish_no_replace(
                optimized, destination, event, source_root=run_root,
                expected_size=len(optimized_bytes),
                expected_sha256=topology.output_sha256,
            )
            return VisualRemappedRunResult(
                request_sha256=request.request_sha256,
                run_root=run_root,
                process=process,
                evidence=evidence,
            )
        except BaseException:
            if run_root is not None:
                _preserve_quarantine(run_root, ownership)
            raise
