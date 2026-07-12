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
    {"dynamic_model_load", "rendering", "bodygroups_skins", "animation", "physics", "damage"}
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


def _artifact_payload(item: ArtifactDeclaration) -> dict[str, Any]:
    return {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}


def _manifest_digest(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def candidate_manifest_for_corpus(corpus: Corpus) -> dict[str, Any]:
    if not corpus.pressure_ids or len(corpus.pressure_ids) != len(set(corpus.pressure_ids)):
        raise Dx90PolicyError("candidate manifest requires unique pressure families")
    families: list[dict[str, Any]] = []
    for family_id in corpus.pressure_ids:
        family = corpus.family(family_id)
        try:
            original = tuple(family.baselines["original"])
        except KeyError as exc:
            raise Dx90PolicyError(f"{family_id} lacks original baseline declarations") from exc
        kept = tuple(item for item in original if not item.path.lower().endswith(".dx80.vtx"))
        omitted = tuple(item for item in original if item.path.lower().endswith(".dx80.vtx"))
        if len(omitted) != 1 or not {".mdl", ".vvd", ".dx90.vtx"}.issubset(
            {compiled_kind(item.path) for item in kept}
        ):
            raise Dx90PolicyError(f"{family_id} manifest lacks required sidecars or exact DX80 omission")
        source_payload = sorted((_artifact_payload(item) for item in original), key=lambda item: item["path"])
        family_payload = {
            "family_id": family.id,
            "compiled_stem": family.compiled_stem,
            "kept": [_artifact_payload(item) for item in kept],
            "omitted_dx80": _artifact_payload(omitted[0]),
            "source_manifest_sha256": _manifest_digest({"artifacts": source_payload}),
            "source_bytes": sum(item.size_bytes for item in original),
            "candidate_bytes": sum(item.size_bytes for item in kept),
            "saved_dx80_bytes": omitted[0].size_bytes,
        }
        families.append(family_payload)
    body = {
        "schema_version": 1,
        "corpus_id": corpus.corpus_id,
        "family_ids": list(corpus.pressure_ids),
        "families": families,
    }
    return {**body, "sha256": _manifest_digest(body)}


def _parse_candidate_manifest(raw: object) -> dict[str, Any]:
    expected = {"schema_version", "corpus_id", "family_ids", "families", "sha256"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise Dx90PolicyError("candidate manifest keys are invalid")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise Dx90PolicyError("candidate manifest schema_version is invalid")
    if not isinstance(raw["corpus_id"], str) or not raw["corpus_id"]:
        raise Dx90PolicyError("candidate manifest corpus_id is invalid")
    if not isinstance(raw["family_ids"], list) or not raw["family_ids"] or any(
        not isinstance(item, str) or not item for item in raw["family_ids"]
    ) or len(raw["family_ids"]) != len(set(raw["family_ids"])):
        raise Dx90PolicyError("candidate manifest family_ids are invalid")
    if not isinstance(raw["families"], list) or len(raw["families"]) != len(raw["family_ids"]):
        raise Dx90PolicyError("candidate manifest families are incomplete")
    family_expected = {
        "family_id", "compiled_stem", "kept", "omitted_dx80", "source_manifest_sha256",
        "source_bytes", "candidate_bytes", "saved_dx80_bytes",
    }
    parsed_families: list[dict[str, Any]] = []
    for index, item in enumerate(raw["families"]):
        if not isinstance(item, dict) or set(item) != family_expected or item["family_id"] != raw["family_ids"][index]:
            raise Dx90PolicyError("candidate manifest family keys/order are invalid")
        if not isinstance(item["compiled_stem"], str) or not item["compiled_stem"]:
            raise Dx90PolicyError("candidate manifest compiled_stem is invalid")
        if not isinstance(item["kept"], list) or not item["kept"]:
            raise Dx90PolicyError("candidate manifest kept artifacts are invalid")
        kept = tuple(ArtifactDeclaration.parse(value, "candidate manifest kept artifact") for value in item["kept"])
        omitted = ArtifactDeclaration.parse(item["omitted_dx80"], "candidate manifest omitted DX80")
        if not omitted.path.lower().endswith(".dx80.vtx") or any(
            value.path.lower().endswith(".dx80.vtx") for value in kept
        ):
            raise Dx90PolicyError("candidate manifest must omit only exact DX80")
        if len({value.path for value in kept}) != len(kept) or omitted.path in {value.path for value in kept}:
            raise Dx90PolicyError("candidate manifest artifact paths are duplicated")
        stem = item["compiled_stem"].lower()
        for value in (*kept, omitted):
            kind = compiled_kind(value.path)
            if not kind or value.path.lower()[:-len(kind)] != stem:
                raise Dx90PolicyError("candidate manifest artifact does not belong to exact compiled stem")
        if not {".mdl", ".vvd", ".dx90.vtx"}.issubset({compiled_kind(value.path) for value in kept}):
            raise Dx90PolicyError("candidate manifest lacks required sidecars")
        for field in ("source_bytes", "candidate_bytes", "saved_dx80_bytes"):
            if type(item[field]) is not int or item[field] < 0:
                raise Dx90PolicyError(f"candidate manifest {field} must be an exact non-negative integer")
        source_bytes = sum(value.size_bytes for value in kept) + omitted.size_bytes
        candidate_bytes = sum(value.size_bytes for value in kept)
        if (item["source_bytes"], item["candidate_bytes"], item["saved_dx80_bytes"]) != (
            source_bytes, candidate_bytes, omitted.size_bytes
        ):
            raise Dx90PolicyError("candidate manifest byte accounting mismatch")
        source_payload = sorted(
            (_artifact_payload(value) for value in (*kept, omitted)), key=lambda value: value["path"]
        )
        if item["source_manifest_sha256"] != _manifest_digest({"artifacts": source_payload}):
            raise Dx90PolicyError("candidate manifest source digest mismatch")
        parsed_families.append(item)
    body = {key: raw[key] for key in ("schema_version", "corpus_id", "family_ids", "families")}
    if not _is_sha256(raw["sha256"]) or raw["sha256"] != _manifest_digest(body):
        raise Dx90PolicyError("candidate manifest digest mismatch")
    return {**body, "sha256": raw["sha256"], "families": parsed_families}


@dataclass(frozen=True)
class RuntimeEvidence:
    schema_version: int
    status: str
    target: str
    corpus_id: str
    family_ids: tuple[str, ...]
    candidate_manifest_sha256: str
    engine_name: str | None
    engine_executable_sha256: str | None
    engine_build_id: str | None
    engine_build_sha256: str | None
    log_sha256: str | None
    capabilities: tuple[str, ...]
    reason: str | None
    manual_procedure: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise Dx90PolicyError("unsupported runtime evidence schema_version")
        if self.target != GMOD_DYNAMIC_RUNTIME_TARGET:
            raise Dx90PolicyError("runtime evidence target must be gmod_dynamic_runtime")
        if not isinstance(self.corpus_id, str) or not self.corpus_id or not isinstance(self.family_ids, tuple) or not self.family_ids:
            raise Dx90PolicyError("runtime evidence requires corpus and family binding")
        if any(not isinstance(item, str) or not item for item in self.family_ids) or len(self.family_ids) != len(set(self.family_ids)):
            raise Dx90PolicyError("runtime evidence family binding is invalid")
        if not _is_sha256(self.candidate_manifest_sha256):
            raise Dx90PolicyError("runtime evidence requires candidate manifest SHA-256")
        if not isinstance(self.capabilities, tuple) or not isinstance(self.manual_procedure, tuple):
            raise Dx90PolicyError("runtime evidence arrays must be immutable tuples")
        if self.status == "pending":
            if any(value is not None for value in (
                self.engine_name, self.engine_executable_sha256, self.engine_build_id,
                self.engine_build_sha256, self.log_sha256,
            )) or self.capabilities:
                raise Dx90PolicyError("pending runtime evidence cannot contain proof fields")
            if not isinstance(self.reason, str) or not self.reason.strip() or not self.manual_procedure or any(
                not isinstance(step, str) or not step.strip() for step in self.manual_procedure
            ):
                raise Dx90PolicyError("pending runtime evidence requires reason and exact manual procedure")
            procedure = " ".join(self.manual_procedure).lower()
            if "render" not in procedure or "damage" not in procedure:
                raise Dx90PolicyError("pending manual procedure must include rendering and damage validation")
        elif self.status == "proven":
            if self.engine_name != "Garry's Mod":
                raise Dx90PolicyError("only Garry's Mod runtime evidence can mark DX90-only proven")
            if not all(_is_sha256(value) for value in (
                self.engine_executable_sha256, self.engine_build_sha256, self.log_sha256
            )):
                raise Dx90PolicyError("runtime executable, build and log require lowercase SHA-256 hashes")
            if not isinstance(self.engine_build_id, str) or not self.engine_build_id.strip() or len(self.engine_build_id) > 128:
                raise Dx90PolicyError("runtime evidence requires a bounded Garry's Mod build id")
            if set(self.capabilities) != REQUIRED_RUNTIME_CAPABILITIES or len(self.capabilities) != len(REQUIRED_RUNTIME_CAPABILITIES):
                raise Dx90PolicyError(f"runtime capabilities must be exactly {sorted(REQUIRED_RUNTIME_CAPABILITIES)}")
            if self.reason is not None or self.manual_procedure:
                raise Dx90PolicyError("proven runtime evidence cannot contain pending fields")
        else:
            raise Dx90PolicyError("runtime evidence status must be pending or proven")

    @property
    def runtime_proven(self) -> bool:
        return self.status == "proven"

    @classmethod
    def pending(cls, *, corpus_id: str, family_ids: Sequence[str], candidate_manifest_sha256: str,
                reason: str, manual_procedure: Sequence[str]) -> "RuntimeEvidence":
        return cls(1, "pending", GMOD_DYNAMIC_RUNTIME_TARGET, corpus_id, tuple(family_ids),
                   candidate_manifest_sha256, None, None, None, None, None, (), reason, tuple(manual_procedure))

    @classmethod
    def proven(cls, *, corpus_id: str, family_ids: Sequence[str], candidate_manifest_sha256: str,
               engine_name: str, engine_executable_sha256: str, engine_build_id: str,
               engine_build_sha256: str, log_sha256: str, capabilities: Sequence[str]) -> "RuntimeEvidence":
        return cls(1, "proven", GMOD_DYNAMIC_RUNTIME_TARGET, corpus_id, tuple(family_ids),
                   candidate_manifest_sha256, engine_name, engine_executable_sha256, engine_build_id,
                   engine_build_sha256, log_sha256, tuple(capabilities), None, ())

    @classmethod
    def from_dict(cls, raw: object) -> "RuntimeEvidence":
        expected = {
            "schema_version", "status", "target", "corpus_id", "family_ids", "candidate_manifest_sha256",
            "engine_name", "engine_executable_sha256", "engine_build_id", "engine_build_sha256",
            "log_sha256", "capabilities", "reason", "manual_procedure",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise Dx90PolicyError(f"runtime evidence keys must be exactly {sorted(expected)}")
        if not isinstance(raw["family_ids"], list) or not isinstance(raw["capabilities"], list) or not isinstance(raw["manual_procedure"], list):
            raise Dx90PolicyError("runtime evidence arrays are invalid")
        return cls(
            raw["schema_version"], raw["status"], raw["target"], raw["corpus_id"], tuple(raw["family_ids"]),
            raw["candidate_manifest_sha256"], raw["engine_name"], raw["engine_executable_sha256"],
            raw["engine_build_id"], raw["engine_build_sha256"], raw["log_sha256"],
            tuple(raw["capabilities"]), raw["reason"], tuple(raw["manual_procedure"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "status": self.status, "target": self.target,
            "corpus_id": self.corpus_id, "family_ids": list(self.family_ids),
            "candidate_manifest_sha256": self.candidate_manifest_sha256,
            "engine_name": self.engine_name, "engine_executable_sha256": self.engine_executable_sha256,
            "engine_build_id": self.engine_build_id, "engine_build_sha256": self.engine_build_sha256,
            "log_sha256": self.log_sha256, "capabilities": list(self.capabilities),
            "reason": self.reason, "manual_procedure": list(self.manual_procedure),
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
    manifest = _parse_candidate_manifest(candidate_manifest_for_corpus(corpus))
    # Never trust an object merely because its type matches: force the strict canonical schema path.
    runtime_evidence = RuntimeEvidence.from_dict(runtime_evidence.to_dict())
    if (
        runtime_evidence.target != GMOD_DYNAMIC_RUNTIME_TARGET
        or runtime_evidence.corpus_id != corpus.corpus_id
        or runtime_evidence.family_ids != corpus.pressure_ids
        or runtime_evidence.candidate_manifest_sha256 != manifest["sha256"]
    ):
        raise Dx90PolicyError("runtime evidence is unrelated to the exact candidate manifest/corpus/families")
    runtime_payload = runtime_evidence.to_dict()
    runtime_digest = _manifest_digest(runtime_payload)
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
        for family_index, family_id in enumerate(corpus.pressure_ids):
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
            family_manifest = manifest["families"][family_index]
            if (
                [_artifact_payload(item) for item in candidate.artifacts] != family_manifest["kept"]
                or candidate.omitted_paths != (family_manifest["omitted_dx80"]["path"],)
                or (candidate.source_bytes, candidate.candidate_bytes, candidate.saved_bytes) != (
                    family_manifest["source_bytes"], family_manifest["candidate_bytes"],
                    family_manifest["saved_dx80_bytes"],
                )
            ):
                raise Dx90PolicyError(f"built candidate differs from frozen manifest for {family_id}")
            source_hash = family_manifest["source_manifest_sha256"]
            provenance = {
                "tool": {},
                "scripts": {"maximum_optimizer/dx90_optional.py": script_hash},
                "settings": {
                    "target": GMOD_DYNAMIC_RUNTIME_TARGET,
                    "omission": ".dx80.vtx",
                    "runtime_status": runtime_evidence.status,
                    "candidate_manifest_sha256": manifest["sha256"],
                    "runtime_evidence_sha256": runtime_digest,
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
    payload = {
        "schema_version": 1,
        "corpus_id": corpus.corpus_id,
        "lane": DX90_OPTIONAL_LANE,
        "target": GMOD_DYNAMIC_RUNTIME_TARGET,
        "candidate_manifest": manifest,
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
        "runtime_evidence": runtime_payload,
        "runtime_evidence_sha256": runtime_digest,
    }
    parse_dx90_optional_experiment(payload)
    return payload


def parse_dx90_optional_experiment(raw: object) -> dict[str, Any]:
    expected = {
        "schema_version", "corpus_id", "lane", "target", "candidate_manifest", "records",
        "summary", "accounting", "runtime_evidence", "runtime_evidence_sha256",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise Dx90PolicyError(f"DX90 experiment keys must be exactly {sorted(expected)}")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise Dx90PolicyError("DX90 experiment schema_version is invalid")
    if raw["lane"] != DX90_OPTIONAL_LANE or raw["target"] != GMOD_DYNAMIC_RUNTIME_TARGET:
        raise Dx90PolicyError("DX90 experiment lane/target is invalid")
    manifest = _parse_candidate_manifest(raw["candidate_manifest"])
    if raw["corpus_id"] != manifest["corpus_id"]:
        raise Dx90PolicyError("DX90 experiment corpus differs from candidate manifest")
    evidence = RuntimeEvidence.from_dict(raw["runtime_evidence"])
    evidence_digest = _manifest_digest(evidence.to_dict())
    if not _is_sha256(raw["runtime_evidence_sha256"]) or raw["runtime_evidence_sha256"] != evidence_digest:
        raise Dx90PolicyError("runtime evidence digest mismatch")
    if (
        evidence.corpus_id != manifest["corpus_id"]
        or evidence.family_ids != tuple(manifest["family_ids"])
        or evidence.candidate_manifest_sha256 != manifest["sha256"]
    ):
        raise Dx90PolicyError("runtime evidence is unrelated to candidate manifest")
    if not isinstance(raw["records"], list) or len(raw["records"]) != len(manifest["families"]):
        raise Dx90PolicyError("DX90 experiment records are incomplete")
    records = tuple(BenchmarkRecord.from_dict(item) for item in raw["records"])
    for record, family in zip(records, manifest["families"], strict=True):
        if record.corpus_id != manifest["corpus_id"] or record.family_id != family["family_id"] or record.lane != DX90_OPTIONAL_LANE:
            raise Dx90PolicyError("DX90 experiment record identity/order mismatch")
        expected_artifacts: dict[str, int] = {}
        for item in family["kept"]:
            kind = compiled_kind(item["path"])
            expected_artifacts[kind] = expected_artifacts.get(kind, 0) + item["size_bytes"]
        if dict(record.artifacts) != expected_artifacts:
            raise Dx90PolicyError("DX90 experiment record differs from per-path manifest")
        settings = record.provenance.get("settings", {})
        if set(settings) != {
            "target", "omission", "runtime_status", "candidate_manifest_sha256", "runtime_evidence_sha256"
        } or settings.get("target") != GMOD_DYNAMIC_RUNTIME_TARGET or settings.get("omission") != ".dx80.vtx" or (
            settings.get("candidate_manifest_sha256") != manifest["sha256"]
            or settings.get("runtime_evidence_sha256") != evidence_digest
            or settings.get("runtime_status") != evidence.status
        ):
            raise Dx90PolicyError("DX90 experiment provenance is not bound to evidence/manifest")
        scripts = record.provenance.get("scripts", {})
        if record.provenance.get("tool") != {} or set(scripts) != {"maximum_optimizer/dx90_optional.py"}:
            raise Dx90PolicyError("DX90 experiment tool/script provenance is invalid")
        script_hash = scripts.get("maximum_optimizer/dx90_optional.py")
        if not _is_sha256(script_hash):
            raise Dx90PolicyError("DX90 experiment script provenance is invalid")
        expected_cache = build_cache_key(
            source=family["source_manifest_sha256"], tools={},
            scripts={"maximum_optimizer/dx90_optional.py": script_hash}, settings=settings,
        )
        if record.cache_key != expected_cache:
            raise Dx90PolicyError("DX90 experiment cache key is not bound to evidence/manifest")
        expected_runtime = "pass" if evidence.runtime_proven else "not_run"
        if record.gates != {"structural": "pass", "visual": "not_run", "runtime": expected_runtime}:
            raise Dx90PolicyError("DX90 experiment gates disagree with runtime evidence")
    if not isinstance(raw["accounting"], dict) or set(raw["accounting"]) != {
        "source_bytes", "candidate_bytes", "saved_dx80_bytes"
    } or any(type(value) is not int or value < 0 for value in raw["accounting"].values()):
        raise Dx90PolicyError("DX90 experiment accounting is invalid")
    expected_accounting = {
        "source_bytes": sum(item["source_bytes"] for item in manifest["families"]),
        "candidate_bytes": sum(item["candidate_bytes"] for item in manifest["families"]),
        "saved_dx80_bytes": sum(item["saved_dx80_bytes"] for item in manifest["families"]),
    }
    if raw["accounting"] != expected_accounting:
        raise Dx90PolicyError("DX90 experiment accounting differs from candidate manifest")
    expected_summary = summarize_records(
        records, expected_family_ids=tuple(manifest["family_ids"]), required_lanes=(DX90_OPTIONAL_LANE,)
    )
    if raw["summary"] != expected_summary:
        raise Dx90PolicyError("DX90 experiment summary differs from records")
    return {
        "candidate_manifest_sha256": manifest["sha256"],
        "runtime_evidence_sha256": evidence_digest,
        "records": records,
        "runtime_evidence": evidence,
    }


def write_dx90_optional_experiment(output: Path, payload: Mapping[str, Any]) -> None:
    parse_dx90_optional_experiment(dict(payload))
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
