from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .benchmarking import (
    ArtifactDeclaration,
    BenchmarkRecord,
    Corpus,
    CorpusError,
    build_cache_key,
    canonical_json_bytes,
    compiled_kind,
    find_stem_sidecars,
    safe_existing_root,
    summarize_records,
    verify_declared_sidecars,
)


DX90_OPTIONAL_LANE = "dx90_optional"
GMOD_DYNAMIC_RUNTIME_TARGET = "gmod_dynamic_runtime"
REQUIRED_RUNTIME_CAPABILITIES = frozenset(
    {"bodygroups", "animation", "physics", "dynamic_model_load"}
)
_SHA256_LENGTH = 64


class Dx90PolicyError(ValueError):
    pass


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _policy_error(exc: Exception) -> Dx90PolicyError:
    return Dx90PolicyError(str(exc))


@dataclass(frozen=True)
class Dx90CandidateResult:
    lane: str
    target: str
    artifacts: tuple[ArtifactDeclaration, ...]
    omitted_paths: tuple[str, ...]
    source_bytes: int
    candidate_bytes: int
    saved_bytes: int


def _validate_source(
    root: Path, compiled_stem: str, declarations: Sequence[ArtifactDeclaration]
) -> Path:
    paths = {item.path.lower() for item in declarations}
    required = (
        any(path.endswith(".mdl") for path in paths),
        any(path.endswith(".vvd") for path in paths),
        any(path.endswith(".dx90.vtx") for path in paths),
    )
    if not all(required):
        raise Dx90PolicyError("DX90 optional policy requires .mdl, .vvd and .dx90.vtx sidecars")
    dx80 = [item for item in declarations if item.path.lower().endswith(".dx80.vtx")]
    if len(dx80) != 1:
        raise Dx90PolicyError("DX90 optional policy requires exactly one .dx80.vtx to omit")
    try:
        safe = safe_existing_root(root.resolve(strict=True), "DX90 optional source root")
        actual = {path.relative_to(safe).as_posix() for path in find_stem_sidecars(safe, compiled_stem)}
        expected = {item.path for item in declarations}
        if len(expected) != len(declarations) or actual != expected:
            raise Dx90PolicyError(
                f"source sidecar set mismatch: expected {sorted(expected)}, got {sorted(actual)}"
            )
        verify_declared_sidecars(safe, compiled_stem, declarations, "DX90 optional source")
    except CorpusError as exc:
        raise _policy_error(exc) from exc
    return safe


def validate_dx90_optional_candidate(
    candidate_root: Path,
    compiled_stem: str,
    declarations: Sequence[ArtifactDeclaration],
) -> None:
    if any(item.path.lower().endswith(".dx80.vtx") for item in declarations):
        raise Dx90PolicyError("DX90 optional candidate declarations must omit .dx80.vtx")
    try:
        safe = safe_existing_root(candidate_root.resolve(strict=True), "DX90 optional candidate root")
        verify_declared_sidecars(safe, compiled_stem, declarations, "DX90 optional candidate")
    except CorpusError as exc:
        raise _policy_error(exc) from exc


def build_dx90_optional_candidate(
    *,
    source_root: Path,
    candidate_root: Path,
    compiled_stem: str,
    declarations: Sequence[ArtifactDeclaration],
    target: str | None,
) -> Dx90CandidateResult:
    if target != GMOD_DYNAMIC_RUNTIME_TARGET:
        raise Dx90PolicyError(
            "DX80 omission is permitted only for the explicit gmod_dynamic_runtime target"
        )
    source = _validate_source(source_root, compiled_stem, declarations)
    destination = Path(os.path.abspath(candidate_root))
    try:
        parent = safe_existing_root(destination.parent.resolve(strict=True), "DX90 candidate parent")
    except CorpusError as exc:
        raise _policy_error(exc) from exc
    if destination.parent.resolve(strict=True) != parent:
        raise Dx90PolicyError("DX90 candidate parent is unsafe")
    if os.path.lexists(destination):
        raise Dx90PolicyError(f"immutable DX90 candidate root already exists: {destination}")

    omitted = tuple(item for item in declarations if item.path.lower().endswith(".dx80.vtx"))
    kept = tuple(item for item in declarations if item not in omitted)
    destination.mkdir()
    try:
        for item in kept:
            source_path = source / Path(item.path)
            target_path = destination / Path(item.path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
        validate_dx90_optional_candidate(destination, compiled_stem, kept)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

    source_bytes = sum(item.size_bytes for item in declarations)
    candidate_bytes = sum(item.size_bytes for item in kept)
    saved_bytes = sum(item.size_bytes for item in omitted)
    if source_bytes - candidate_bytes != saved_bytes:
        shutil.rmtree(destination, ignore_errors=True)
        raise Dx90PolicyError("DX90 optional byte accounting mismatch")
    return Dx90CandidateResult(
        lane=DX90_OPTIONAL_LANE,
        target=target,
        artifacts=kept,
        omitted_paths=tuple(item.path for item in omitted),
        source_bytes=source_bytes,
        candidate_bytes=candidate_bytes,
        saved_bytes=saved_bytes,
    )


@dataclass(frozen=True)
class RuntimeEvidence:
    schema_version: int
    status: str
    target: str
    tool_name: str | None
    tool_sha256: str | None
    build_id: str | None
    log_sha256: str | None
    capabilities: tuple[str, ...]
    reason: str | None
    manual_procedure: tuple[str, ...]

    @property
    def runtime_proven(self) -> bool:
        return self.status == "proven"

    @classmethod
    def pending(cls, *, reason: str, manual_procedure: Sequence[str]) -> "RuntimeEvidence":
        if not isinstance(reason, str) or not reason.strip():
            raise Dx90PolicyError("pending runtime evidence requires a reason")
        steps = tuple(manual_procedure)
        if not steps or any(not isinstance(step, str) or not step.strip() for step in steps):
            raise Dx90PolicyError("pending runtime evidence requires an exact manual procedure")
        return cls(1, "pending", GMOD_DYNAMIC_RUNTIME_TARGET, None, None, None, None, (), reason, steps)

    @classmethod
    def proven(
        cls,
        *,
        tool_name: str,
        tool_sha256: str,
        build_id: str,
        log_sha256: str,
        capabilities: Sequence[str],
    ) -> "RuntimeEvidence":
        if tool_name != "Garry's Mod":
            raise Dx90PolicyError("only Garry's Mod runtime evidence can mark DX90-only proven")
        if not _is_sha256(tool_sha256) or not _is_sha256(log_sha256):
            raise Dx90PolicyError("runtime tool and log require lowercase SHA-256 hashes")
        if not isinstance(build_id, str) or not build_id.strip() or len(build_id) > 128:
            raise Dx90PolicyError("runtime evidence requires a bounded Garry's Mod build id")
        capability_tuple = tuple(capabilities)
        if set(capability_tuple) != REQUIRED_RUNTIME_CAPABILITIES or len(capability_tuple) != len(REQUIRED_RUNTIME_CAPABILITIES):
            raise Dx90PolicyError(
                f"runtime capabilities must be exactly {sorted(REQUIRED_RUNTIME_CAPABILITIES)}"
            )
        return cls(
            1,
            "proven",
            GMOD_DYNAMIC_RUNTIME_TARGET,
            tool_name,
            tool_sha256,
            build_id,
            log_sha256,
            capability_tuple,
            None,
            (),
        )

    @classmethod
    def from_dict(cls, raw: object) -> "RuntimeEvidence":
        expected = {
            "schema_version", "status", "target", "tool_name", "tool_sha256", "build_id",
            "log_sha256", "capabilities", "reason", "manual_procedure",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            actual = sorted(raw) if isinstance(raw, dict) else []
            raise Dx90PolicyError(f"runtime evidence keys must be exactly {sorted(expected)}; got {actual}")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise Dx90PolicyError("unsupported runtime evidence schema_version")
        if raw["target"] != GMOD_DYNAMIC_RUNTIME_TARGET:
            raise Dx90PolicyError("runtime evidence target must be gmod_dynamic_runtime")
        if not isinstance(raw["capabilities"], list) or not isinstance(raw["manual_procedure"], list):
            raise Dx90PolicyError("runtime evidence arrays are invalid")
        if raw["status"] == "pending":
            if any(raw[field] is not None for field in ("tool_name", "tool_sha256", "build_id", "log_sha256")) or raw["capabilities"]:
                raise Dx90PolicyError("pending runtime evidence cannot contain proof fields")
            return cls.pending(reason=raw["reason"], manual_procedure=raw["manual_procedure"])
        if raw["status"] == "proven":
            if raw["reason"] is not None or raw["manual_procedure"]:
                raise Dx90PolicyError("proven runtime evidence cannot contain pending fields")
            return cls.proven(
                tool_name=raw["tool_name"],
                tool_sha256=raw["tool_sha256"],
                build_id=raw["build_id"],
                log_sha256=raw["log_sha256"],
                capabilities=raw["capabilities"],
            )
        raise Dx90PolicyError("runtime evidence status must be pending or proven")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "target": self.target,
            "tool_name": self.tool_name,
            "tool_sha256": self.tool_sha256,
            "build_id": self.build_id,
            "log_sha256": self.log_sha256,
            "capabilities": list(self.capabilities),
            "reason": self.reason,
            "manual_procedure": list(self.manual_procedure),
        }


def build_dx90_optional_experiment(
    *,
    corpus: Corpus,
    candidate_root: Path,
    runtime_evidence: RuntimeEvidence,
    script_hash: str,
) -> dict[str, Any]:
    if not _is_sha256(script_hash):
        raise Dx90PolicyError("experiment script_hash must be a lowercase SHA-256 digest")
    if runtime_evidence.target != GMOD_DYNAMIC_RUNTIME_TARGET:
        raise Dx90PolicyError("runtime evidence target does not match DX90 optional policy")
    try:
        original_root = safe_existing_root(corpus.roots["original"], "DX90 experiment original root")
        parent = safe_existing_root(candidate_root.parent.resolve(strict=True), "DX90 experiment parent")
    except (KeyError, CorpusError) as exc:
        raise _policy_error(exc) from exc
    destination = Path(os.path.abspath(candidate_root))
    if destination.parent.resolve(strict=True) != parent:
        raise Dx90PolicyError("DX90 experiment root is unsafe")
    if os.path.lexists(destination):
        raise Dx90PolicyError(f"immutable DX90 experiment root already exists: {destination}")
    destination.mkdir()

    records: list[BenchmarkRecord] = []
    source_bytes = 0
    candidate_bytes = 0
    saved_bytes = 0
    try:
        for family_id in corpus.pressure_ids:
            family = corpus.family(family_id)
            try:
                declarations = family.baselines["original"]
            except KeyError as exc:
                raise Dx90PolicyError(f"{family_id} lacks original baseline declarations") from exc
            candidate = build_dx90_optional_candidate(
                source_root=original_root,
                candidate_root=destination / family_id,
                compiled_stem=family.compiled_stem,
                declarations=declarations,
                target=GMOD_DYNAMIC_RUNTIME_TARGET,
            )
            source_manifest = [
                {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
                for item in declarations
            ]
            source_hash = hashlib.sha256(canonical_json_bytes(source_manifest)).hexdigest()
            provenance = {
                "tool": {},
                "scripts": {"maximum_optimizer/dx90_optional.py": script_hash},
                "settings": {
                    "target": GMOD_DYNAMIC_RUNTIME_TARGET,
                    "omission": ".dx80.vtx",
                    "runtime_status": runtime_evidence.status,
                },
            }
            artifact_bytes: dict[str, int] = {}
            for item in candidate.artifacts:
                kind = compiled_kind(item.path)
                artifact_bytes[kind] = artifact_bytes.get(kind, 0) + item.size_bytes
            cache_key = build_cache_key(
                source=source_hash,
                tools={},
                scripts={"maximum_optimizer/dx90_optional.py": script_hash},
                settings=provenance["settings"],
            )
            records.append(BenchmarkRecord.create(
                corpus_id=corpus.corpus_id,
                family_id=family.id,
                lane=DX90_OPTIONAL_LANE,
                strategy="omit-dx80-gmod-dynamic-runtime",
                cache_key=cache_key,
                artifacts=artifact_bytes,
                provenance=provenance,
                gates={
                    "structural": "pass",
                    "visual": "not_run",
                    "runtime": "pass" if runtime_evidence.runtime_proven else "not_run",
                },
            ))
            source_bytes += candidate.source_bytes
            candidate_bytes += candidate.candidate_bytes
            saved_bytes += candidate.saved_bytes
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    if source_bytes - candidate_bytes != saved_bytes:
        shutil.rmtree(destination, ignore_errors=True)
        raise Dx90PolicyError("DX90 pressure experiment byte accounting mismatch")
    return {
        "schema_version": 1,
        "corpus_id": corpus.corpus_id,
        "lane": DX90_OPTIONAL_LANE,
        "target": GMOD_DYNAMIC_RUNTIME_TARGET,
        "records": [record.to_dict() for record in records],
        "summary": summarize_records(
            records,
            expected_family_ids=corpus.pressure_ids,
            required_lanes=(DX90_OPTIONAL_LANE,),
        ),
        "accounting": {
            "source_bytes": source_bytes,
            "candidate_bytes": candidate_bytes,
            "saved_dx80_bytes": saved_bytes,
        },
        "runtime_evidence": runtime_evidence.to_dict(),
    }


def write_dx90_optional_experiment(output: Path, payload: Mapping[str, Any]) -> None:
    destination = Path(os.path.abspath(output))
    try:
        parent = safe_existing_root(destination.parent.resolve(strict=True), "DX90 evidence parent")
    except CorpusError as exc:
        raise _policy_error(exc) from exc
    if destination.parent.resolve(strict=True) != parent:
        raise Dx90PolicyError("DX90 evidence output parent is unsafe")
    if os.path.lexists(destination):
        raise Dx90PolicyError(f"immutable DX90 evidence output already exists: {destination}")
    try:
        destination.write_bytes(canonical_json_bytes(payload))
    except (OSError, TypeError, ValueError) as exc:
        if os.path.lexists(destination):
            destination.unlink()
        raise Dx90PolicyError(f"cannot write canonical DX90 evidence: {exc}") from exc
