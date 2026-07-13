from __future__ import annotations

import os
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .candidates import CandidateBuild, CandidateTools
from .composite import (
    AdaptiveDirectSourceUnionRecord, ComposedSourceTree,
    build_adaptive_direct_source_union_target,
    build_source_tree_manifest, candidate_spec_sha256,
    revalidate_direct_source_snapshot, revalidate_recovery_snapshot,
)
from .domain import (
    AdaptiveDirectCoverageManifest, AdaptiveDirectCoverageSourceProof,
    CandidateSpec, CompileFileProof,
    DirectSourceSnapshot, FamilyManifest,
)
from .focused_cache import (
    _assert_safe_tree, _copy_file_no_follow, _file_proof, _has_reparse_ancestor,
    _is_reparse, _read_regular_no_follow, _write_json_fsync,
)
from .processes import ProcessCancelledError, run_process
from .qc_graph import parse_qc_graph
from .reporting import canonical_json
from .source_union import (
    SourceUnionMaterialBinding, SourceUnionMaskObservation,
    SourceUnionWorkspaceLease,
    SourceUnionRenderOutput, _quarantine_cleanup_if_owned,
    _workspace_root_identity,
    validate_adaptive_direct_source_union,
)
from .source_components import (
    SourceComponentManifest, build_source_component_transfer,
    current_filtered_source_component_bytes, require_current_source_component_transfer,
    source_component_manifest_payload, source_component_transfer_payload,
)
from .source_materials import (
    PrivateSourceUnionMaterialLease,
    SourceUnionMaterialContract,
    SourceUnionMaterialOwnershipConflict,
    materialize_private_source_union_material_roots,
    require_current_private_source_union_material_lease,
    require_current_source_union_material_contract,
    source_union_material_contract_payload,
    source_union_material_render_evidence,
)
from .visual_validation import (
    FidelityProfile, SourceUnionComparisonContract,
    compare_source_union_render_sets,
)


_HASH = re.compile(r"[0-9a-f]{64}")
_ANGLES = ("front", "back", "left", "right", "top", "bottom", "iso1", "iso2")
_CAMERAS = tuple(f"camera-{index:02d}" for index in range(8))


def _material_tree_cleanup_is_authorized(
    material_tree: Path, material_lease: object,
) -> bool:
    if type(material_lease) is not PrivateSourceUnionMaterialLease:
        return not os.path.lexists(material_tree)
    try:
        require_current_private_source_union_material_lease(
            material_lease, material_tree, None,
        )
    except (OSError, TypeError, ValueError):
        return False
    return True


def _cancel(event: threading.Event | None, message: str) -> None:
    if event is not None and event.is_set():
        raise ProcessCancelledError(message)


@dataclass(frozen=True)
class SourceUnionPoseBinding:
    pose_key: str
    frame: int
    animation_before: Path | None
    animation_after: Path | None
    animation_before_sha256: str | None
    animation_after_sha256: str | None
    pose_contract_sha256: str

    def __post_init__(self) -> None:
        if type(self.pose_key) is not str or not self.pose_key or type(self.frame) is not int or self.frame < 0:
            raise ValueError("source-union pose binding identity is invalid")
        if _HASH.fullmatch(self.pose_contract_sha256 or "") is None:
            raise ValueError("source-union pose contract is invalid")
        values = (
            self.animation_before, self.animation_after,
            self.animation_before_sha256, self.animation_after_sha256,
        )
        if self.pose_key == "bind":
            if self.frame != 0 or any(value is not None for value in values):
                raise ValueError("bind pose cannot carry animation state")
            return
        raise ValueError("source-union anchor unavailable without sealed preflight proof")

    @classmethod
    def bind(cls, pose_contract_sha256: str) -> "SourceUnionPoseBinding":
        return cls("bind", 0, None, None, None, None, pose_contract_sha256)

    @classmethod
    def anchor(
        cls, pose_key: str, frame: int, before: Path, after: Path,
        before_sha256: str, after_sha256: str, pose_contract_sha256: str,
    ) -> "SourceUnionPoseBinding":
        raise ValueError("source-union anchor unavailable without sealed preflight proof")

    def revalidate(self, cancel_event: threading.Event | None) -> None:
        if self.pose_key == "bind":
            return
        raise ValueError("source-union anchor unavailable without sealed preflight proof")


def validate_source_union_pose_bindings(
    bindings: tuple[SourceUnionPoseBinding, ...],
    pose_keys: tuple[str, ...],
    pose_contract_sha256: str,
    cancel_event: threading.Event | None,
) -> None:
    values = tuple(bindings)
    if (
        len(values) != 1
        or any(not isinstance(item, SourceUnionPoseBinding) for item in values)
        or tuple(item.pose_key for item in values) != tuple(pose_keys)
        or tuple(pose_keys) != ("bind",)
        or values[0].pose_key != "bind"
        or any(item.pose_contract_sha256 != pose_contract_sha256 for item in values)
    ):
        raise ValueError("source-union pose bindings differ from target/pose contract")
    for item in values:
        item.revalidate(cancel_event)


def validate_source_union_cli_contract(args) -> None:
    enabled = getattr(args, "source_union_contract", None)
    visibility = getattr(args, "source_union_visibility_out", None)
    if bool(enabled) != bool(visibility):
        raise ValueError("source-union contract and visibility output must be paired")
    if not enabled:
        raise ValueError("source-union mode is not enabled")
    if any((
        getattr(args, "configuration_manifest", None) is not None,
        getattr(args, "focus_region", None) is not None,
        bool(getattr(args, "aggregate_regions", False)),
    )):
        raise ValueError("source-union mode cannot carry state/focus selectors")
    if args.passes != "textured,clay":
        raise ValueError("source-union pass command is not canonical")
    if args.angles != "front,back,left,right,top,bottom,iso1,iso2":
        raise ValueError("source-union angle command is not canonical")
    raw_poses = tuple(token.strip() for token in str(args.poses or "").split(",") if token.strip())
    parsed = []
    for token in raw_poses:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+):(\d+)", token)
        if match is None:
            raise ValueError("source-union pose command is malformed")
        parsed.append((match.group(1), int(match.group(2))))
    if (
        parsed != [("bind", 0)]
    ):
        raise ValueError("source-union E2A accepts bind:0 only")


def _current_composed_manifest(composed: ComposedSourceTree, event) -> None:
    root = composed.workspace / "src"
    if _has_reparse_ancestor(root) or _has_reparse_ancestor(composed.optimized_qc):
        raise ValueError("adaptive-direct composed source has reparse ancestry")
    graph = parse_qc_graph(composed.optimized_qc, root)
    current = build_source_tree_manifest(root, graph, "composite-source-v1", event)
    if current != composed.source_manifest or current.digest != composed.composition.composed_manifest_sha256:
        raise ValueError("adaptive-direct composed source changed before compile")


@dataclass(frozen=True)
class AdaptiveDirectCompileResult:
    schema: int
    build: CandidateBuild
    compile_files: tuple[CompileFileProof, ...]
    composition_evidence_sha256: str
    result_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.schema) is not int or self.schema != 1
            or not isinstance(self.build, CandidateBuild) or not self.compile_files
        ):
            raise TypeError("adaptive-direct compile result is invalid")
        files = tuple(self.compile_files)
        if any(not isinstance(item, CompileFileProof) for item in files):
            raise TypeError("adaptive-direct compile proofs are invalid")
        if any(_HASH.fullmatch(value or "") is None for value in (
            self.composition_evidence_sha256, self.result_sha256,
        )):
            raise ValueError("adaptive-direct composition evidence is invalid")
        if hashlib.sha256(canonical_json(self._unsigned_payload()).encode()).hexdigest() != self.result_sha256:
            raise ValueError("adaptive-direct compile result seal is invalid")
        object.__setattr__(self, "compile_files", files)

    def _unsigned_payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "candidate_spec_sha256": candidate_spec_sha256(self.build.spec),
            "compile_files": [{
                "relative_path": item.relative_path, "kind": item.kind,
                "size": item.size, "sha256": item.sha256,
            } for item in self.compile_files],
            "composition_evidence_sha256": self.composition_evidence_sha256,
        }

    @classmethod
    def create(
        cls, build: CandidateBuild, compile_files: tuple[CompileFileProof, ...],
        composition_evidence_sha256: str,
    ) -> "AdaptiveDirectCompileResult":
        files = tuple(compile_files)
        provisional = object.__new__(cls)
        for name, value in (
            ("schema", 1), ("build", build), ("compile_files", files),
            ("composition_evidence_sha256", composition_evidence_sha256),
            ("result_sha256", "0" * 64),
        ):
            object.__setattr__(provisional, name, value)
        seal = hashlib.sha256(
            canonical_json(provisional._unsigned_payload()).encode()
        ).hexdigest()
        return cls(1, build, files, composition_evidence_sha256, seal)


@dataclass(frozen=True)
class SourceUnionRenderTools:
    blender_exe: Path
    renderer_script: Path
    renderer_sha256: str
    materials_roots: tuple[Path, ...] = ()
    vtfcmd: Path | None = None
    texture_cache: Path | None = None
    dependency_digest_provider: object = None

    def __post_init__(self) -> None:
        blender = Path(self.blender_exe).resolve()
        renderer = Path(self.renderer_script).resolve()
        roots = tuple(Path(item).resolve() for item in self.materials_roots)
        if any(not path.is_file() or _has_reparse_ancestor(path) for path in (blender, renderer)):
            raise ValueError("source-union Blender/renderer is unavailable or unsafe")
        if _HASH.fullmatch(self.renderer_sha256 or "") is None:
            raise ValueError("source-union renderer hash is invalid")
        if any(not root.is_dir() or _has_reparse_ancestor(root) for root in roots):
            raise ValueError("source-union materials root is unavailable or unsafe")
        if not callable(self.dependency_digest_provider):
            raise TypeError("source-union dependency digest provider is required")
        object.__setattr__(self, "blender_exe", blender)
        object.__setattr__(self, "renderer_script", renderer)
        object.__setattr__(self, "materials_roots", roots)
        if self.vtfcmd is not None:
            vtfcmd = Path(self.vtfcmd).resolve()
            if not vtfcmd.is_file() or _has_reparse_ancestor(vtfcmd):
                raise ValueError("source-union VTFCmd is unavailable or unsafe")
            object.__setattr__(self, "vtfcmd", vtfcmd)
        if self.texture_cache is not None:
            cache = Path(self.texture_cache).resolve()
            parent = cache if cache.exists() else cache.parent
            if not parent.is_dir() or _has_reparse_ancestor(parent):
                raise ValueError("source-union texture cache is unsafe")
            object.__setattr__(self, "texture_cache", cache)


def _cleanup_compile_owned(workspace: Path) -> None:
    from .candidates import _quarantine_and_remove_owned_direct_tree
    for name in ("compiled", "logs"):
        path = workspace / name
        if os.path.lexists(path):
            _quarantine_and_remove_owned_direct_tree(path)


def _write_private_bytes_fsync(path: Path, payload: bytes) -> None:
    if type(payload) is not bytes or os.path.lexists(path):
        raise ValueError("source-union private output must be fresh bytes")
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _assert_source_union_workspace_root(
    workspace: Path, *, authorized: bool,
) -> None:
    expected = {
        "inputs", "control", "raw", "material-roots",
        "source-union-render.log",
    }
    if authorized:
        expected.add("authorized")
    if _has_reparse_ancestor(workspace):
        raise ValueError("source-union workspace root has reparse ancestry")
    try:
        entries = tuple(os.scandir(workspace))
    except OSError as exc:
        raise ValueError("source-union workspace root is unavailable") from exc
    if {item.name for item in entries} != expected:
        raise ValueError("source-union workspace root tree is not exact")
    for entry in entries:
        path = Path(entry.path)
        if _is_reparse(path):
            raise ValueError("source-union workspace root contains a reparse point")
        if entry.name == "source-union-render.log":
            if not entry.is_file(follow_symlinks=False):
                raise ValueError("source-union render log is not a regular file")
        elif not entry.is_dir(follow_symlinks=False):
            raise ValueError("source-union workspace root child is not a directory")


def _parse_source_union_visibility(path: Path, target, event) -> tuple[SourceUnionMaskObservation, ...]:
    try:
        payload = json.loads(_read_regular_no_follow(
            path, event, contained_root=path.parent, max_bytes=16 * 1024 * 1024,
        ).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("source-union visibility JSON is unavailable") from exc
    fields = {
        "schema", "kind", "target_sha256", "source_identity",
        "source_coverage_sha256", "cameras", "pose_keys", "observations",
        "evidence_sha256",
    }
    if type(payload) is not dict or set(payload) != fields:
        raise ValueError("source-union visibility schema is invalid")
    evidence = payload["evidence_sha256"]
    unsigned = dict(payload); unsigned.pop("evidence_sha256")
    expected_digest = hashlib.sha256(canonical_json(unsigned).encode()).hexdigest()
    if any((
        type(payload["schema"]) is not int or payload["schema"] != 1,
        payload["kind"] != "adaptive-direct-source-union-visibility-v1",
        payload["target_sha256"] != target.target_sha256,
        payload["source_identity"] != target.source_identity,
        payload["source_coverage_sha256"] != target.source_coverage_sha256,
        payload["cameras"] != list(_CAMERAS),
        payload["pose_keys"] != list(target.pose_keys),
        evidence != expected_digest,
        type(payload["observations"]) is not list,
    )):
        raise ValueError("source-union visibility binding/seal differs")
    expected_keys = tuple(
        (side, component, pose, camera)
        for side in ("candidate", "reference")
        for component in target.component_keys
        for pose in target.pose_keys
        for camera in _CAMERAS
    )
    result = []
    for raw, key in zip(payload["observations"], expected_keys):
        if type(raw) is not dict or set(raw) != {
            "side", "component_key", "pose_key", "camera_key", "visible_mask_pixels",
        }:
            raise ValueError("source-union visibility observation schema is invalid")
        item = SourceUnionMaskObservation(
            raw["side"], raw["component_key"], raw["pose_key"],
            raw["camera_key"], raw["visible_mask_pixels"],
        )
        if (item.side, item.component_key, item.pose_key, item.camera_key) != key:
            raise ValueError("source-union visibility observation order differs")
        result.append(item)
    if len(result) != len(expected_keys) or len(payload["observations"]) != len(expected_keys):
        raise ValueError("source-union visibility cardinality differs")
    return tuple(result)


def _raw_mapping(target):
    for raw_side, side in (("original", "reference"), ("optimized", "candidate")):
        for pose in target.pose_keys:
            for render_pass in ("textured", "clay"):
                for index, angle in enumerate(_ANGLES):
                    yield (
                        raw_side, side, f"{render_pass}/{pose}/{angle}.png",
                        f"source-union/{target.union_key}/{side}/{pose}/{render_pass}/camera-{index:02d}.png",
                    )


def _raw_manifest(root: Path, side: str, event) -> dict:
    path = root / "render_manifest.json"
    try:
        payload = json.loads(_read_regular_no_follow(
            path, event, contained_root=root, max_bytes=16 * 1024 * 1024,
        ).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"source-union {side} raw manifest is invalid") from exc
    if type(payload) is not dict:
        raise ValueError(f"source-union {side} raw manifest is invalid")
    return payload


def _assert_exact_source_union_raw(raw: Path, target, event) -> None:
    expected = {
        f"{raw_side}/render_manifest.json"
        for raw_side in ("original", "optimized")
    } | {
        f"{raw_side}/{source}"
        for raw_side, _side, source, _destination in _raw_mapping(target)
    }
    _assert_safe_tree(raw, event, expected, max_files=len(expected))


def _assert_source_union_raw_material_evidence(
    raw: Path, expected_evidence: tuple[dict[str, object], ...], event,
) -> None:
    expected = list(expected_evidence)
    for raw_side, label in (("original", "reference"), ("optimized", "candidate")):
        manifest = _raw_manifest(raw / raw_side, label, event)
        entries = manifest.get("entries")
        if type(entries) is not list or len(entries) != 16:
            raise ValueError("source-union raw material entry cardinality differs")
        for entry in entries:
            if type(entry) is not dict or entry.get("resolved_materials") != (
                expected if entry.get("pass") == "textured" else []
            ):
                raise ValueError("source-union raw material evidence differs")


def _normalize_source_union_raw(raw: Path, authorized: Path, target, comparison, event) -> None:
    _assert_exact_source_union_raw(raw, target, event)
    manifests = {
        "original": _raw_manifest(raw / "original", "reference", event),
        "optimized": _raw_manifest(raw / "optimized", "candidate", event),
    }
    for raw_side in ("original", "optimized"):
        expected = {"render_manifest.json"} | {
            source for side, _auth, source, _dest in _raw_mapping(target) if side == raw_side
        }
        _assert_safe_tree(raw / raw_side, event, expected, max_files=65)
    indexed = {}
    for raw_side, manifest in manifests.items():
        if type(manifest) is not dict or type(manifest.get("entries")) is not list:
            raise ValueError("source-union raw entries are invalid")
        indexed[raw_side] = {
            entry.get("image"): entry for entry in manifest["entries"] if type(entry) is dict
        }
    for raw_side, _side, source_relative, destination_relative in _raw_mapping(target):
        entry = indexed[raw_side].get(source_relative)
        if entry is None or entry.get("sha256") is None:
            raise ValueError("source-union raw/authorized mapping is incomplete")
        source = raw / raw_side / Path(*source_relative.split("/"))
        destination = authorized / Path(*destination_relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_file_no_follow(source, destination, event, contained_root=raw / raw_side)
        _size, digest = _file_proof(destination, event, contained_root=authorized)
        if digest != entry["sha256"]:
            raise ValueError("source-union authorized PNG differs from raw manifest")
    _prove_source_union_bijection(raw, authorized, target, comparison, event)


def _prove_source_union_bijection(raw: Path, authorized: Path, target, comparison, event) -> None:
    _assert_exact_source_union_raw(raw, target, event)
    expected_authorized = {destination for *_rest, destination in _raw_mapping(target)}
    _assert_safe_tree(authorized, event, expected_authorized, max_files=64)
    manifests = {
        "original": _raw_manifest(raw / "original", "reference", event),
        "optimized": _raw_manifest(raw / "optimized", "candidate", event),
    }
    for raw_side, _side, source_relative, destination_relative in _raw_mapping(target):
        entries = manifests[raw_side].get("entries", ())
        matches = tuple(item for item in entries if type(item) is dict and item.get("image") == source_relative)
        if len(matches) != 1:
            raise ValueError("source-union raw entry mapping is not bijective")
        source = raw / raw_side / Path(*source_relative.split("/"))
        destination = authorized / Path(*destination_relative.split("/"))
        source_size, source_hash = _file_proof(source, event, contained_root=raw / raw_side)
        dest_size, dest_hash = _file_proof(destination, event, contained_root=authorized)
        if (source_size, source_hash) != (dest_size, dest_hash) or source_hash != matches[0].get("sha256"):
            raise ValueError("source-union raw/authorized bytes differ")


def _reject_compile_extras(manifest: FamilyManifest, build: CandidateBuild) -> None:
    from .orchestrator import _is_exact_family_artifact
    root = build.compiled_models_dir
    if _has_reparse_ancestor(root):
        raise ValueError("adaptive-direct compiled root has reparse ancestry")
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        if any(_is_reparse(parent / name) for name in directory_names):
            raise ValueError("adaptive-direct compile contains reparse directory")
        for name in file_names:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if _is_reparse(path) or not _is_exact_family_artifact(relative, manifest.model_rel):
                raise ValueError("adaptive-direct compile contains undeclared artifact")


class AdaptiveDirectProductionBoundary:
    def __init__(self, *, process_runner=run_process) -> None:
        if not callable(process_runner):
            raise TypeError("production process runner is invalid")
        self._process_runner = process_runner

    def compile_adaptive_direct_candidate(
        self, *, manifest: FamilyManifest, spec: CandidateSpec,
        composed: ComposedSourceTree, tools: CandidateTools,
        cancel_event: threading.Event,
    ) -> AdaptiveDirectCompileResult:
        if not isinstance(manifest, FamilyManifest) or not isinstance(spec, CandidateSpec):
            raise TypeError("adaptive-direct compile inputs are invalid")
        if not isinstance(composed, ComposedSourceTree) or not isinstance(tools, CandidateTools):
            raise TypeError("adaptive-direct compile runtime is invalid")
        recipe = spec.composite_recipe
        if (
            recipe is None or recipe.kind != "adaptive-direct-fallback-v1"
            or recipe.round_index != 0
            or manifest.family_id != recipe.family_id
            or manifest.input_hash != recipe.family_input_sha256
            or composed.composition.kind != recipe.kind
            or composed.composition.recipe_sha256 != recipe.recipe_sha256
        ):
            raise ValueError("adaptive-direct compile composition binding differs")
        workspace = Path(composed.workspace)
        source_root = workspace / "src"
        if not workspace.is_absolute() or not source_root.is_dir():
            raise ValueError("adaptive-direct composition workspace is invalid")
        if _has_reparse_ancestor(manifest.source_dir):
            raise ValueError("adaptive-direct original source root has reparse ancestry")
        try:
            composed.optimized_qc.relative_to(source_root)
        except ValueError as exc:
            raise ValueError("adaptive-direct optimized QC escapes source root") from exc
        if (
            composed.optimized_qc.suffix.casefold() != ".qc"
            or not composed.optimized_qc.is_file()
            or _has_reparse_ancestor(composed.optimized_qc)
        ):
            raise ValueError("adaptive-direct optimized QC is invalid")
        for path in (tools.python_exe, tools.studiomdl_exe, tools.repo_root / "batch_compile_opt_qc.py"):
            if not path.is_file() or _has_reparse_ancestor(path):
                raise ValueError("adaptive-direct compiler tool is unavailable or unsafe")
        _cancel(cancel_event, "cancelled before adaptive-direct compile")
        if os.path.lexists(workspace / "compiled") or os.path.lexists(workspace / "logs"):
            _cleanup_compile_owned(workspace)
            raise ValueError("adaptive-direct compile roots must be fresh")
        _current_composed_manifest(composed, cancel_event)
        from .orchestrator import (
            _compile_composed_candidate, _current_recovery_compile_files,
            _matching_qcs, _validate_complete_recovery_graph_pairing,
        )
        original_qcs = _matching_qcs(manifest.source_dir, manifest.model_rel, optimized=False)
        if len(original_qcs) != 1:
            raise ValueError("adaptive-direct original QC graph is missing or ambiguous")
        _validate_complete_recovery_graph_pairing(
            parse_qc_graph(original_qcs[0], manifest.source_dir),
            parse_qc_graph(composed.optimized_qc, source_root),
        )
        try:
            build = _compile_composed_candidate(
                manifest, spec, workspace, composed.optimized_qc, tools,
                cancel_event, process_runner=self._process_runner,
            )
            _cancel(cancel_event, "cancelled after adaptive-direct compile")
            _current_composed_manifest(composed, cancel_event)
            if _has_reparse_ancestor(workspace / "compiled") or _has_reparse_ancestor(workspace / "logs"):
                raise ValueError("adaptive-direct compile output has reparse ancestry")
            _reject_compile_extras(manifest, build)
            proofs = _current_recovery_compile_files(manifest, build, cancel_event)
            if _current_recovery_compile_files(manifest, build, cancel_event) != proofs:
                raise ValueError("adaptive-direct compiled bytes changed during proof")
            _current_composed_manifest(composed, cancel_event)
            return AdaptiveDirectCompileResult.create(
                build, proofs, composed.composition.evidence_sha256
            )
        except BaseException:
            _cleanup_compile_owned(workspace)
            raise

    def render_adaptive_direct_source_union(
        self, *, manifest: FamilyManifest, base_build: CandidateBuild,
        candidate_compile: AdaptiveDirectCompileResult,
        coverage: AdaptiveDirectCoverageManifest,
        source_proof: AdaptiveDirectCoverageSourceProof,
        snapshot: DirectSourceSnapshot, profile: FidelityProfile,
        component_manifest: SourceComponentManifest,
        material_contract: SourceUnionMaterialContract,
        tools: SourceUnionRenderTools, workspace: Path,
        cancel_event: threading.Event,
    ) -> AdaptiveDirectSourceUnionRecord:
        if not all((
            isinstance(manifest, FamilyManifest), isinstance(base_build, CandidateBuild),
            isinstance(candidate_compile, AdaptiveDirectCompileResult),
            isinstance(coverage, AdaptiveDirectCoverageManifest),
            isinstance(source_proof, AdaptiveDirectCoverageSourceProof),
            isinstance(snapshot, DirectSourceSnapshot), isinstance(profile, FidelityProfile),
            isinstance(tools, SourceUnionRenderTools),
            isinstance(component_manifest, SourceComponentManifest),
            isinstance(material_contract, SourceUnionMaterialContract),
        )):
            raise TypeError("adaptive-direct source-union production inputs are invalid")
        candidate_build = candidate_compile.build
        recipe = candidate_build.spec.composite_recipe
        base_snapshot = base_build.source_snapshot
        if recipe is None or recipe.kind != "adaptive-direct-fallback-v1" or base_snapshot is None:
            raise ValueError("source-union candidate/base binding is unavailable")
        expected_candidate_spec = CandidateSpec(
            "recovery-" + recipe.recipe_sha256,
            base_build.spec.engine,
            recipe.direct_ratio,
            base_build.spec.target_error,
            base_build.spec.repair_profile,
            (),
            strategy=base_build.spec.strategy,
            update_vertices=base_build.spec.update_vertices,
            transfer=base_build.spec.transfer,
            composite_recipe=recipe,
        )
        overlays = tuple(item for item in recipe.overlays if item.source_identity == source_proof.source_identity)
        if len(overlays) != 1 or any((
            base_build.spec.composite_recipe is not None,
            base_snapshot.kind != "candidate",
            recipe.base_candidate_id != base_build.spec.candidate_id,
            recipe.base_spec_sha256 != candidate_spec_sha256(base_build.spec),
            recipe.base_cache_digest != base_snapshot.candidate_cache_digest,
            recipe.base_source_manifest_sha256 != base_snapshot.source_manifest.digest,
            recipe.base_source_snapshot_sha256 != base_snapshot.snapshot_sha256,
            recipe.family_id != base_snapshot.family_id,
            recipe.family_input_sha256 != base_snapshot.family_input_sha256,
            recipe.optimizer_contract_sha256 != base_snapshot.optimizer_contract_sha256,
            recipe.whole_profile_sha256 != base_snapshot.whole_profile_sha256,
            recipe.focused_profile_sha256 != base_snapshot.focused_profile_sha256,
            recipe.dependency_proof_sha256 != base_snapshot.dependency_proof_sha256,
            candidate_build.spec != expected_candidate_spec,
            manifest.family_id != recipe.family_id,
            manifest.input_hash != recipe.family_input_sha256,
            coverage.family_id != recipe.family_id,
            coverage.family_input_sha256 != recipe.family_input_sha256,
            coverage.base_candidate_id != recipe.base_candidate_id,
            coverage.base_spec_sha256 != recipe.base_spec_sha256,
            coverage.base_cache_digest != recipe.base_cache_digest,
            coverage.base_source_manifest_sha256 != recipe.base_source_manifest_sha256,
            coverage.coverage_manifest_sha256 != recipe.coverage_manifest_sha256,
            coverage.base_source_snapshot_sha256 != base_snapshot.snapshot_sha256,
            snapshot.request.source_coverage_sha256 != source_proof.source_coverage_sha256,
            snapshot.snapshot_sha256 != overlays[0].replacement_snapshot_sha256,
            component_manifest.source_sha256 != snapshot.request.source_sha256,
            component_manifest.triangle_count != snapshot.triangles_before,
            component_manifest.component_manifest_sha256
            != source_proof.witnesses[0].component_manifest_sha256,
            tuple(item.component_key for item in component_manifest.components)
            != source_proof.component_keys,
        )):
            raise ValueError("source-union production binding differs")
        reference_source = Path(snapshot.input_source_root) / Path(
            *snapshot.request.source_relative_path.split("/")
        )
        candidate_source = Path(snapshot.source_root) / Path(
            *snapshot.output_relative_path.split("/")
        )

        def validate_current_inputs(event):
            from .orchestrator import _current_recovery_compile_files
            revalidate_recovery_snapshot(base_snapshot, event)
            revalidate_direct_source_snapshot(snapshot, event)
            if _has_reparse_ancestor(candidate_build.compiled_models_dir):
                raise ValueError("source-union compiled root has reparse ancestry")
            current_compile = _current_recovery_compile_files(manifest, candidate_build, event)
            if current_compile != candidate_compile.compile_files or _current_recovery_compile_files(
                manifest, candidate_build, event
            ) != current_compile:
                raise ValueError("source-union compiled candidate proof is stale")
            _renderer_size, renderer_digest = _file_proof(
                tools.renderer_script, event, contained_root=tools.renderer_script.parent,
            )
            if renderer_digest != tools.renderer_sha256:
                raise ValueError("source-union renderer script is stale")
            if tools.dependency_digest_provider(event) != snapshot.request.dependency_proof_sha256:
                raise ValueError("source-union runtime dependency proof is stale")
            source_bytes = _read_regular_no_follow(
                reference_source, event, contained_root=snapshot.input_source_root,
            )
            candidate_bytes = _read_regular_no_follow(
                candidate_source, event, contained_root=snapshot.source_root,
            )
            if (
                hashlib.sha256(source_bytes).hexdigest() != snapshot.request.source_sha256
                or hashlib.sha256(candidate_bytes).hexdigest() != snapshot.output_sha256
            ):
                raise ValueError("source-union source bytes are stale")
            filtered_bytes = current_filtered_source_component_bytes(
                component_manifest, source_bytes
            )
            transfer = build_source_component_transfer(
                component_manifest, source_bytes, candidate_bytes
            )
            require_current_source_component_transfer(
                transfer, component_manifest, source_bytes, candidate_bytes
            )
            if {
                item.component_key for item in transfer.triangles
            } != {
                item.component_key for item in component_manifest.components
            }:
                raise ValueError(
                    "source-union candidate dropped an entire source component"
                )
            return source_bytes, filtered_bytes, candidate_bytes, transfer

        _source_bytes, filtered_reference_bytes, candidate_bytes, component_transfer = (
            validate_current_inputs(cancel_event)
        )
        if any((
            material_contract.source_identity != source_proof.source_identity,
            material_contract.filtered_source_sha256
            != component_manifest.filtered_source_sha256,
            tuple(item.material_region_key for item in material_contract.bindings)
            != source_proof.material_region_keys,
            len(material_contract.roots) != len(tools.materials_roots),
            any(
                witness.material_contract_sha256
                != material_contract.material_contract_sha256
                for witness in source_proof.witnesses
            ),
        )):
            raise ValueError("source-union runtime material binding differs")
        require_current_source_union_material_contract(
            material_contract, filtered_source_bytes=filtered_reference_bytes,
            roots=tools.materials_roots, cancel_event=cancel_event,
        )
        target = build_adaptive_direct_source_union_target(
            source_proof=source_proof,
            coverage_manifest_sha256=coverage.coverage_manifest_sha256,
        )
        pose_contract = source_proof.witnesses[0].pose_contract_sha256
        pose_bindings = (SourceUnionPoseBinding.bind(pose_contract),)
        validate_source_union_pose_bindings(
            pose_bindings, target.pose_keys, pose_contract, cancel_event
        )
        material_contract_sha256 = material_contract.material_contract_sha256
        material_bindings = tuple(
            SourceUnionMaterialBinding(key, material_contract_sha256)
            for key in source_proof.material_region_keys
        )
        comparison = SourceUnionComparisonContract.create(
            target_sha256=target.target_sha256,
            source_identity=target.source_identity,
            source_coverage_sha256=target.source_coverage_sha256,
            reference_source_sha256=component_manifest.filtered_source_sha256,
            candidate_source_sha256=snapshot.output_sha256,
            material_contract_sha256=material_contract_sha256,
            pose_frames=(("bind", 0),), union_key=target.union_key,
        )
        holder: dict[str, object] = {}
        material_tree = workspace / "material-roots"
        expected_material_paths = {
            f"root-{item.root_index:03d}/{item.path}"
            for item in material_contract.files
        }

        def validate_current_materials(event) -> tuple[dict[str, object], ...]:
            material_lease = holder.get("private_material_lease")
            if (
                type(material_lease) is not PrivateSourceUnionMaterialLease
                or len(material_lease.roots) != len(material_contract.roots)
            ):
                raise ValueError("source-union private material roots are unavailable")
            try:
                require_current_private_source_union_material_lease(
                    material_lease, material_tree, event,
                )
            except SourceUnionMaterialOwnershipConflict as exc:
                ownership_lease.preserve_unowned_descendant()
                raise ValueError(
                    "source-union private material ownership changed"
                ) from exc
            private_roots = material_lease.roots
            _assert_safe_tree(
                material_tree, event, expected_material_paths,
                max_files=512, max_bytes=512 * 1024 * 1024,
            )
            original_authorization = require_current_source_union_material_contract(
                material_contract, filtered_source_bytes=filtered_reference_bytes,
                roots=tools.materials_roots, cancel_event=event,
            )
            private_authorization = require_current_source_union_material_contract(
                material_contract, filtered_source_bytes=filtered_reference_bytes,
                roots=private_roots, cancel_event=event,
            )
            if (
                original_authorization.material_contract_sha256
                != material_contract_sha256
                or private_authorization.material_contract_sha256
                != material_contract_sha256
            ):
                raise ValueError("source-union current material hash differs")
            original_evidence = source_union_material_render_evidence(
                original_authorization
            )
            private_evidence = source_union_material_render_evidence(
                private_authorization
            )
            if original_evidence != private_evidence:
                raise ValueError("source-union private material evidence differs")
            expected = holder.get("material_evidence")
            if expected is not None and original_evidence != expected:
                raise ValueError("source-union current material evidence changed")
            return original_evidence

        def render_fresh(request, output_root: Path, event):
            inputs = output_root / "inputs"; control = output_root / "control"
            raw = output_root / "raw"; authorized = output_root / "authorized"
            for path in (inputs, control): path.mkdir()
            reference_input = inputs / "reference.smd"
            candidate_input = inputs / "candidate.smd"
            renderer_input = inputs / "render_previews.py"
            current_source, current_filtered, current_candidate, current_transfer = (
                validate_current_inputs(event)
            )
            if (
                current_filtered != filtered_reference_bytes
                or current_candidate != candidate_bytes
                or current_transfer != component_transfer
            ):
                raise ValueError("source-union current inputs changed before render")
            require_current_source_union_material_contract(
                material_contract, filtered_source_bytes=current_filtered,
                roots=tools.materials_roots, cancel_event=event,
            )
            try:
                material_lease = materialize_private_source_union_material_roots(
                    material_contract, tools.materials_roots, material_tree, event,
                    filtered_source_bytes=current_filtered,
                )
            except SourceUnionMaterialOwnershipConflict as exc:
                ownership_lease.preserve_unowned_descendant()
                if exc.original_cause is not None:
                    raise exc.original_cause from exc
                raise
            if type(material_lease) is not PrivateSourceUnionMaterialLease:
                raise TypeError("source-union private material lease is invalid")
            private_material_roots = material_lease.roots
            holder["private_material_lease"] = material_lease
            holder["material_tree_identity"] = material_lease.destination_identity
            holder["private_material_roots"] = private_material_roots
            holder["private_material_root_identities"] = material_lease.root_identities
            ownership_lease.install_cleanup_guard(lambda: (
                _material_tree_cleanup_is_authorized(
                    material_tree, holder.get("private_material_lease"),
                )
            ))
            material_evidence = validate_current_materials(event)
            holder["material_evidence"] = material_evidence
            _write_private_bytes_fsync(reference_input, current_filtered)
            _write_private_bytes_fsync(candidate_input, current_candidate)
            _copy_file_no_follow(tools.renderer_script, renderer_input, event, contained_root=tools.renderer_script.parent)
            for path, expected in (
                (reference_input, comparison.reference_source_sha256),
                (candidate_input, comparison.candidate_source_sha256),
                (renderer_input, tools.renderer_sha256),
            ):
                _size, digest = _file_proof(path, event, contained_root=inputs)
                if digest != expected: raise ValueError("source-union private input differs")
            contract_path = control / "source-union-contract.json"
            visibility_path = control / "source-union-visibility.json"
            _write_json_fsync(contract_path, {
                "schema": 1, "kind": "adaptive-direct-source-union-render-v1",
                "target_sha256": target.target_sha256,
                "comparison_contract": {
                    "contract_sha256": comparison.contract_sha256,
                    "target_sha256": comparison.target_sha256,
                    "source_identity": comparison.source_identity,
                    "source_coverage_sha256": comparison.source_coverage_sha256,
                    "reference_source_sha256": comparison.reference_source_sha256,
                    "candidate_source_sha256": comparison.candidate_source_sha256,
                    "material_contract_sha256": comparison.material_contract_sha256,
                    "pose_frames": [list(item) for item in comparison.pose_frames],
                    "union_key": comparison.union_key,
                },
                "source_identity": target.source_identity,
                "source_coverage_sha256": target.source_coverage_sha256,
                "component_keys": list(target.component_keys),
                "component_manifest": source_component_manifest_payload(component_manifest),
                "candidate_component_transfer": source_component_transfer_payload(
                    component_transfer
                ),
                "material_region_keys": list(target.material_region_keys),
                "material_contract_sha256": material_contract_sha256,
                "material_contract": source_union_material_contract_payload(
                    material_contract
                ),
                "material_render_evidence": list(material_evidence),
                "pose_frames": {"bind": 0}, "angles": list(_ANGLES),
                "cameras": list(_CAMERAS), "renderer_sha256": tools.renderer_sha256,
            })
            contract_size, contract_digest = _file_proof(
                contract_path, event, contained_root=control
            )
            render_log = output_root / "source-union-render.log"
            _write_private_bytes_fsync(render_log, b"")
            private_texture_cache = output_root / "texture-cache"
            private_texture_cache.mkdir()
            command = [
                str(tools.blender_exe), "--background", "--python", str(renderer_input), "--",
                "--before", str(reference_input), "--after", str(candidate_input),
                "--out", str(raw), "--size", "512", "--angles", ",".join(_ANGLES),
                "--passes", "textured,clay", "--poses", "bind:0",
                "--source-union-contract", str(contract_path),
                "--source-union-visibility-out", str(visibility_path),
            ]
            for root in private_material_roots:
                command.extend(("--materials-root", str(root)))
            if tools.vtfcmd is not None: command.extend(("--vtfcmd", str(tools.vtfcmd)))
            command.extend(("--texture-cache", str(private_texture_cache)))
            if tools.dependency_digest_provider(event) != snapshot.request.dependency_proof_sha256:
                raise ValueError("source-union dependency changed before Blender")
            validate_current_materials(event)
            try:
                process = self._process_runner(
                    tuple(command), tools.renderer_script.parent,
                    output_root / "source-union-render.log", event,
                )
            except BaseException:
                validate_current_materials(event)
                raise
            validate_current_materials(event)
            if process.returncode != 0:
                raise ValueError("source-union Blender process failed")
            cache_error = None
            try:
                _assert_safe_tree(
                    private_texture_cache, event, expected_paths=None,
                    max_files=4096, max_bytes=2 * 1024 ** 3,
                )
            except BaseException as exc:
                cache_error = exc
            finally:
                if os.path.lexists(private_texture_cache):
                    from .candidates import _quarantine_and_remove_owned_direct_tree
                    _quarantine_and_remove_owned_direct_tree(private_texture_cache)
            if cache_error is not None:
                if isinstance(cache_error, ProcessCancelledError):
                    raise cache_error
                raise ValueError("source-union private texture cache is unsafe") from cache_error
            if tools.dependency_digest_provider(event) != snapshot.request.dependency_proof_sha256:
                raise ValueError("source-union dependency changed during Blender")
            validate_current_materials(event)
            if any(_has_reparse_ancestor(path) for path in (inputs, control, raw)):
                raise ValueError("source-union process output has reparse ancestry")
            if os.path.lexists(authorized):
                raise ValueError("source-union authorized root was precreated by renderer")
            _assert_source_union_workspace_root(output_root, authorized=False)
            _assert_safe_tree(
                inputs, event,
                {"reference.smd", "candidate.smd", "render_previews.py"},
                max_files=3,
            )
            _assert_safe_tree(
                control, event,
                {"source-union-contract.json", "source-union-visibility.json"},
                max_files=2,
            )
            _assert_exact_source_union_raw(raw, target, event)
            _assert_source_union_raw_material_evidence(
                raw, material_evidence, event
            )
            for path, expected in (
                (reference_input, comparison.reference_source_sha256),
                (candidate_input, comparison.candidate_source_sha256),
                (renderer_input, tools.renderer_sha256),
            ):
                _size, digest = _file_proof(path, event, contained_root=inputs)
                if digest != expected: raise ValueError("source-union private input changed during Blender")
            if (
                _read_regular_no_follow(reference_input, event, contained_root=inputs)
                != filtered_reference_bytes
                or _read_regular_no_follow(candidate_input, event, contained_root=inputs)
                != candidate_bytes
            ):
                raise ValueError("source-union private component inputs differ")
            _post_source, post_filtered, post_candidate, post_transfer = validate_current_inputs(event)
            if (
                post_filtered != filtered_reference_bytes
                or post_candidate != candidate_bytes
                or post_transfer != component_transfer
            ):
                raise ValueError("source-union component provenance changed during Blender")
            if _file_proof(contract_path, event, contained_root=control) != (
                contract_size, contract_digest
            ):
                raise ValueError("source-union control contract changed during Blender")
            authorized.mkdir()
            _assert_source_union_workspace_root(output_root, authorized=True)
            observations = _parse_source_union_visibility(
                visibility_path, target, event
            )
            _normalize_source_union_raw(raw, authorized, target, comparison, event)
            holder.update({
                "raw": raw, "authorized": authorized, "inputs": inputs,
                "control": control, "contract_path": contract_path,
                "contract_proof": (contract_size, contract_digest),
                "visibility_path": visibility_path,
                "observations": observations,
            })
            return SourceUnionRenderOutput(authorized, observations)

        def compare_authorized(reference_dir: Path, candidate_dir: Path, expected_profile):
            raw = holder.get("raw"); authorized = holder.get("authorized")
            if not isinstance(raw, Path) or not isinstance(authorized, Path) or any((
                reference_dir != authorized / "source-union" / target.union_key / "reference",
                candidate_dir != authorized / "source-union" / target.union_key / "candidate",
            )):
                raise ValueError("source-union comparator directories differ")
            current_material_evidence = validate_current_materials(cancel_event)
            _assert_source_union_raw_material_evidence(
                raw, current_material_evidence, cancel_event
            )
            _prove_source_union_bijection(raw, authorized, target, comparison, cancel_event)
            manifest_proofs = tuple(
                _file_proof(
                    raw / side / "render_manifest.json", cancel_event,
                    contained_root=raw / side,
                )
                for side in ("original", "optimized")
            )
            holder["raw_manifest_proofs"] = manifest_proofs
            try:
                result = compare_source_union_render_sets(
                    raw / "original", raw / "optimized", expected_profile,
                    expected_contract=comparison,
                )
            except BaseException:
                try:
                    validate_current_materials(None)
                except BaseException:
                    pass
                raise
            if tuple(
                _file_proof(
                    raw / side / "render_manifest.json", cancel_event,
                    contained_root=raw / side,
                )
                for side in ("original", "optimized")
            ) != manifest_proofs:
                raise ValueError("source-union raw manifests changed during comparison")
            _prove_source_union_bijection(raw, authorized, target, comparison, cancel_event)
            if validate_current_materials(cancel_event) != current_material_evidence:
                raise ValueError("source-union materials changed during comparison")
            _assert_source_union_raw_material_evidence(
                raw, current_material_evidence, cancel_event
            )
            return result

        def validate_render_workspace_current(event) -> None:
            raw = holder.get("raw"); authorized = holder.get("authorized")
            inputs = holder.get("inputs"); control = holder.get("control")
            contract_path = holder.get("contract_path")
            visibility_path = holder.get("visibility_path")
            if any(not isinstance(path, Path) for path in (
                raw, authorized, inputs, control, contract_path, visibility_path,
            )):
                raise ValueError("source-union workspace proof is unavailable")
            assert isinstance(raw, Path) and isinstance(authorized, Path)
            assert isinstance(inputs, Path) and isinstance(control, Path)
            assert isinstance(contract_path, Path) and isinstance(visibility_path, Path)
            _assert_safe_tree(
                inputs, event,
                {"reference.smd", "candidate.smd", "render_previews.py"},
                max_files=3,
            )
            _assert_safe_tree(
                control, event,
                {"source-union-contract.json", "source-union-visibility.json"},
                max_files=2,
            )
            if (
                _read_regular_no_follow(inputs / "reference.smd", event, contained_root=inputs)
                != filtered_reference_bytes
                or _read_regular_no_follow(inputs / "candidate.smd", event, contained_root=inputs)
                != candidate_bytes
                or _file_proof(
                    inputs / "render_previews.py", event, contained_root=inputs
                )[1] != tools.renderer_sha256
                or _file_proof(contract_path, event, contained_root=control)
                != holder.get("contract_proof")
                or _parse_source_union_visibility(visibility_path, target, event)
                != holder.get("observations")
            ):
                raise ValueError("source-union private/control proof changed")
            _prove_source_union_bijection(
                raw, authorized, target, comparison, event
            )
            manifest_proofs = holder.get("raw_manifest_proofs")
            if (
                type(manifest_proofs) is not tuple
                or tuple(
                    _file_proof(
                        raw / side / "render_manifest.json", event,
                        contained_root=raw / side,
                    )
                    for side in ("original", "optimized")
                ) != manifest_proofs
            ):
                raise ValueError("source-union final raw manifests changed")
            current_material_evidence = validate_current_materials(event)
            if current_material_evidence != holder.get("material_evidence"):
                raise ValueError("source-union final material evidence differs")
            _assert_source_union_raw_material_evidence(
                raw, current_material_evidence, event
            )
            _assert_source_union_workspace_root(workspace, authorized=True)

        ownership_lease = SourceUnionWorkspaceLease()
        try:
            record = validate_adaptive_direct_source_union(
                coverage=coverage, source_proof=source_proof, snapshot=snapshot,
                workspace=workspace,
                dependency_proof_sha256=snapshot.request.dependency_proof_sha256,
                material_bindings=material_bindings, profile=profile,
                renderer=render_fresh, comparator=compare_authorized,
                cancel_event=cancel_event,
                ownership_lease=ownership_lease,
            )
            _final_source, final_filtered, final_candidate, final_transfer = (
                validate_current_inputs(cancel_event)
            )
            if (
                final_filtered != filtered_reference_bytes
                or final_candidate != candidate_bytes
                or final_transfer != component_transfer
            ):
                raise ValueError("source-union inputs changed before record return")
            validate_render_workspace_current(cancel_event)
            return record
        except BaseException:
            if ownership_lease.authorize_cleanup():
                _quarantine_cleanup_if_owned(workspace, ownership_lease.identity)
            raise
