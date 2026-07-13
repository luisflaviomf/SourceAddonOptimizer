from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .candidates import CandidateBuild, CandidateTools
from .composite import ComposedSourceTree, build_source_tree_manifest
from .domain import CandidateSpec, CompileFileProof, FamilyManifest
from .focused_cache import _file_proof, _has_reparse_ancestor, _is_reparse
from .processes import ProcessCancelledError, run_process
from .qc_graph import parse_qc_graph


_HASH = re.compile(r"[0-9a-f]{64}")


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

    def __post_init__(self) -> None:
        if type(self.pose_key) is not str or not self.pose_key or type(self.frame) is not int or self.frame < 0:
            raise ValueError("source-union pose binding identity is invalid")
        values = (
            self.animation_before, self.animation_after,
            self.animation_before_sha256, self.animation_after_sha256,
        )
        if self.pose_key == "bind":
            if self.frame != 0 or any(value is not None for value in values):
                raise ValueError("bind pose cannot carry animation state")
            return
        if any(value is None for value in values):
            raise ValueError("anchor pose requires paired animation proofs")
        before = Path(self.animation_before)
        after = Path(self.animation_after)
        if not before.is_absolute() or not after.is_absolute():
            raise ValueError("anchor animation paths must be absolute")
        if any(_HASH.fullmatch(value or "") is None for value in (
            self.animation_before_sha256, self.animation_after_sha256,
        )):
            raise ValueError("anchor animation hashes are invalid")
        object.__setattr__(self, "animation_before", before)
        object.__setattr__(self, "animation_after", after)

    @classmethod
    def bind(cls) -> "SourceUnionPoseBinding": return cls("bind", 0, None, None, None, None)

    @classmethod
    def anchor(
        cls, pose_key: str, frame: int, before: Path, after: Path,
        before_sha256: str, after_sha256: str,
    ) -> "SourceUnionPoseBinding":
        return cls(pose_key, frame, Path(before), Path(after), before_sha256, after_sha256)

    def revalidate(self, cancel_event: threading.Event | None) -> None:
        if self.pose_key == "bind":
            return
        for path, expected in (
            (self.animation_before, self.animation_before_sha256),
            (self.animation_after, self.animation_after_sha256),
        ):
            _cancel(cancel_event, "cancelled during source-union animation validation")
            if _has_reparse_ancestor(path):
                raise ValueError("source-union animation has reparse ancestry")
            _size, digest = _file_proof(path, cancel_event, contained_root=path.parent)
            if digest != expected:
                raise ValueError("source-union animation proof is stale")


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
    poses = tuple(token.strip() for token in str(args.poses or "").split(",") if token.strip())
    if not 1 <= len(poses) <= 2 or poses[0] != "bind:0":
        raise ValueError("source-union poses must be bind plus optional anchor")


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
    build: CandidateBuild
    compile_files: tuple[CompileFileProof, ...]
    composition_evidence_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.build, CandidateBuild) or not self.compile_files:
            raise TypeError("adaptive-direct compile result is invalid")
        if any(not isinstance(item, CompileFileProof) for item in self.compile_files):
            raise TypeError("adaptive-direct compile proofs are invalid")
        if _HASH.fullmatch(self.composition_evidence_sha256 or "") is None:
            raise ValueError("adaptive-direct composition evidence is invalid")


def _cleanup_compile_owned(workspace: Path) -> None:
    from .candidates import _quarantine_and_remove_owned_direct_tree
    for name in ("compiled", "logs"):
        path = workspace / name
        if os.path.lexists(path):
            _quarantine_and_remove_owned_direct_tree(path)


def _reject_compile_extras(manifest: FamilyManifest, build: CandidateBuild) -> None:
    from .orchestrator import _is_exact_family_artifact
    root = build.compiled_models_dir
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        if any(_is_reparse(parent / name) for name in directory_names):
            raise ValueError("adaptive-direct compile contains reparse directory")
        for name in file_names:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if _is_reparse(path) or not _is_exact_family_artifact(relative, manifest.model_rel):
                raise ValueError("adaptive-direct compile contains undeclared artifact")


class ProductionAdapters:
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
            or composed.composition.kind != recipe.kind
            or composed.composition.recipe_sha256 != recipe.recipe_sha256
        ):
            raise ValueError("adaptive-direct compile composition binding differs")
        workspace = Path(composed.workspace)
        source_root = workspace / "src"
        if not workspace.is_absolute() or not source_root.is_dir():
            raise ValueError("adaptive-direct composition workspace is invalid")
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
            _reject_compile_extras(manifest, build)
            proofs = _current_recovery_compile_files(manifest, build, cancel_event)
            if _current_recovery_compile_files(manifest, build, cancel_event) != proofs:
                raise ValueError("adaptive-direct compiled bytes changed during proof")
            return AdaptiveDirectCompileResult(
                build, proofs, composed.composition.evidence_sha256
            )
        except BaseException:
            _cleanup_compile_owned(workspace)
            raise
