from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
CORPUS_ID = "lvs-models-v1"
FULL_FAMILY_IDS = (
    "dodge_charger",
    "dodge_monaco_police",
    "toyota_supra",
    "ford_fairlane",
    "nissan_skyline_gtr32",
    "vw_beetle",
    "vw_touareg",
    "ferrari_365_fullrig",
    "caterham_620r",
    "pontiac_transam_wheel",
)
PRESSURE_FAMILY_IDS = FULL_FAMILY_IDS[:5]
BASELINE_LANES = ("original", "control", "blender", "fidelity", "experiment")
COMPILED_KINDS = (".mdl", ".vvd", ".vtx", ".dx80.vtx", ".dx90.vtx", ".ani", ".phy")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_LOGICAL_ID = re.compile(r"[a-z][a-z0-9_]*")
_CORPUS_ID_RE = re.compile(r"[a-z][a-z0-9_-]*")
_STRATEGY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_REPARSE_POINT = 0x400


class CorpusError(ValueError):
    pass


def _exact_keys(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise CorpusError(f"{context} keys must be exactly {sorted(expected)}; got {sorted(actual)}")


def _exact_int(value: object, context: str, *, nonnegative: bool = False) -> int:
    if type(value) is not int or (nonnegative and value < 0):
        qualifier = "non-negative " if nonnegative else ""
        raise ValueError(f"{context} must be an exact {qualifier}integer")
    return value


def _finite_nonnegative(value: object, context: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{context} must be a finite non-negative number")
    return float(value)


def _logical_id(value: object, context: str, pattern: re.Pattern[str] = _LOGICAL_ID) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{context} must be a non-empty logical identifier")
    return value


def _relative_path(raw: object, context: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or "\\" in raw or re.match(r"^[A-Za-z]:", raw):
        raise CorpusError(f"{context} must be a non-empty POSIX logical relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise CorpusError(f"{context} must be a contained logical relative path")
    return path


def _is_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError as exc:
        raise CorpusError(f"cannot inspect path {path}: {exc}") from exc
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & _REPARSE_POINT)


def _safe_root(path: Path, context: str) -> Path:
    if not path.is_absolute() or not path.is_dir():
        raise CorpusError(f"{context} must expand to an existing absolute directory")
    current = path
    while True:
        if _is_reparse(current):
            raise CorpusError(f"{context} contains a symlink or reparse point: {current}")
        if current.parent == current:
            break
        current = current.parent
    return path.resolve(strict=True)


def safe_existing_root(path: Path, context: str) -> Path:
    return _safe_root(path, context)


def safe_existing_file(path: Path, context: str) -> Path:
    if not path.is_absolute() or not path.is_file() or _is_reparse(path):
        raise CorpusError(f"{context} must be an absolute regular non-reparse file")
    parent = _safe_root(path.parent, f"{context} parent")
    result = parent / path.name
    if _is_reparse(result):
        raise CorpusError(f"{context} must not be a symlink or reparse point")
    return result


def safe_join(root: Path, relative: str | PurePosixPath, *, must_exist: bool = True) -> Path:
    rel = _relative_path(str(relative), "artifact path")
    current = root
    for part in rel.parts:
        current = current / part
        if current.exists() or os.path.lexists(current):
            if _is_reparse(current):
                raise CorpusError(f"artifact path contains a symlink or reparse point: {current}")
        elif must_exist:
            raise CorpusError(f"artifact is missing: {current}")
    resolved = current.resolve(strict=must_exist)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CorpusError(f"artifact path escapes root: {relative}") from exc
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable_compiler_error(line: str) -> str:
    """Strip a compiler workspace path while preserving the QC line and diagnostic."""
    match = re.match(r"ERROR:\s+.*?\.qc\((\d+)\):\s+-\s+(.*)$", line, re.IGNORECASE)
    if match:
        return f"QC line {match.group(1)}: {match.group(2)}"
    if re.match(r"^[A-Za-z]:[\\/]", line) or "\\users\\" in line.lower():
        return "compiler error (machine path redacted)"
    return line.strip()


@dataclass(frozen=True)
class ArtifactDeclaration:
    path: str
    size_bytes: int
    sha256: str

    @classmethod
    def parse(cls, raw: object, context: str) -> "ArtifactDeclaration":
        if not isinstance(raw, dict):
            raise CorpusError(f"{context} must be an object")
        _exact_keys(raw, {"path", "size_bytes", "sha256"}, context)
        path = _relative_path(raw["path"], f"{context}.path").as_posix()
        size = raw["size_bytes"]
        digest = raw["sha256"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise CorpusError(f"{context}.size_bytes must be a non-negative integer")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise CorpusError(f"{context}.sha256 must be a lowercase SHA-256 digest")
        return cls(path, size, digest)


def _verify_declarations(root: Path, declarations: Sequence[ArtifactDeclaration], context: str) -> None:
    seen: set[str] = set()
    for declaration in declarations:
        if declaration.path in seen:
            raise CorpusError(f"duplicate {context} path: {declaration.path}")
        seen.add(declaration.path)
        path = safe_join(root, declaration.path)
        if not path.is_file():
            raise CorpusError(f"{context} is not a regular file: {declaration.path}")
        actual_size = path.stat().st_size
        if actual_size != declaration.size_bytes:
            raise CorpusError(f"size mismatch for {context} {declaration.path}: {actual_size} != {declaration.size_bytes}")
        actual_hash = sha256_file(path)
        if actual_hash != declaration.sha256:
            raise CorpusError(f"hash mismatch for {context} {declaration.path}: {actual_hash} != {declaration.sha256}")


def compiled_kind(path: str) -> str:
    lower = path.lower()
    for suffix in (".dx80.vtx", ".dx90.vtx"):
        if lower.endswith(suffix):
            return suffix
    suffix = PurePosixPath(lower).suffix
    return suffix if suffix in COMPILED_KINDS else ""


def _verify_exact_sidecars(root: Path, stem: str, declarations: Sequence[ArtifactDeclaration], context: str) -> None:
    stem_path = _relative_path(stem, f"{context}.compiled_stem")
    parent = safe_join(root, stem_path.parent, must_exist=True) if str(stem_path.parent) != "." else root
    expected = {item.path for item in declarations}
    actual: set[str] = set()
    for child in parent.iterdir():
        if child.is_dir():
            continue
        name = child.name
        prefix = stem_path.name.lower()
        if not name.lower().startswith(prefix + "."):
            continue
        rel = child.relative_to(root).as_posix()
        if compiled_kind(rel):
            actual.add(rel)
    if actual != expected:
        raise CorpusError(f"{context} sidecar set mismatch: expected {sorted(expected)}, got {sorted(actual)}")
    kinds = {compiled_kind(path) for path in actual}
    if not {".mdl", ".vvd", ".dx90.vtx"}.issubset(kinds):
        raise CorpusError(f"{context} lacks required .mdl/.vvd/.dx90.vtx sidecars")


def verify_declared_sidecars(
    root: Path, stem: str, declarations: Sequence[ArtifactDeclaration], context: str
) -> None:
    safe = _safe_root(root, f"{context} root")
    _verify_declarations(safe, declarations, context)
    _verify_exact_sidecars(safe, stem, declarations, context)


@dataclass(frozen=True)
class FamilySpec:
    id: str
    display_name: str
    source_qc: str
    source_files: tuple[ArtifactDeclaration, ...]
    compiled_stem: str
    baselines: Mapping[str, tuple[ArtifactDeclaration, ...]]


@dataclass(frozen=True)
class Corpus:
    schema_version: int
    corpus_id: str
    roots: Mapping[str, Path]
    pressure_ids: tuple[str, ...]
    full_ids: tuple[str, ...]
    families: tuple[FamilySpec, ...]

    def family(self, family_id: str) -> FamilySpec:
        return next(family for family in self.families if family.id == family_id)


def load_corpus(
    path: os.PathLike[str] | str,
    *,
    environ: Mapping[str, str] | None = None,
    expected_family_ids: Sequence[str] = FULL_FAMILY_IDS,
) -> Corpus:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorpusError(f"cannot read corpus: {exc}") from exc
    if not isinstance(raw, dict):
        raise CorpusError("corpus must be an object")
    _exact_keys(raw, {"schema_version", "corpus_id", "roots", "partitions", "families"}, "corpus")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
        raise CorpusError("unsupported corpus schema_version")
    if not isinstance(raw["corpus_id"], str) or raw["corpus_id"] != CORPUS_ID:
        raise CorpusError("unsupported corpus schema or id")
    env = os.environ if environ is None else environ
    if not isinstance(raw["roots"], dict) or not raw["roots"]:
        raise CorpusError("roots must be a non-empty object")
    roots: dict[str, Path] = {}
    for name, spec in raw["roots"].items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            raise CorpusError("root declarations must be objects")
        _exact_keys(spec, {"env"}, f"root {name}")
        env_name = spec["env"]
        if not isinstance(env_name, str) or _ENV_NAME.fullmatch(env_name) is None:
            raise CorpusError(f"root {name} env must be an environment variable name")
        value = env.get(env_name)
        if not value:
            raise CorpusError(f"required environment variable {env_name} is not set")
        roots[name] = _safe_root(Path(value).expanduser(), f"root {name} ({env_name})")
    for required in ("source", "original"):
        if required not in roots:
            raise CorpusError(f"required root is missing: {required}")

    partitions = raw["partitions"]
    if not isinstance(partitions, dict):
        raise CorpusError("partitions must be an object")
    _exact_keys(partitions, {"pressure", "full"}, "partitions")
    pressure = tuple(partitions["pressure"]) if isinstance(partitions["pressure"], list) else ()
    full = tuple(partitions["full"]) if isinstance(partitions["full"], list) else ()
    if any(not isinstance(item, str) or _LOGICAL_ID.fullmatch(item) is None for item in pressure + full):
        raise CorpusError("partition family IDs must be logical identifiers")
    expected = tuple(expected_family_ids)
    expected_pressure = expected[:5] if expected == FULL_FAMILY_IDS else expected
    if pressure != expected_pressure:
        raise CorpusError(f"pressure partition must be exactly {list(expected_pressure)}")
    if full != expected:
        raise CorpusError(f"full partition must be exactly {list(expected)}")

    family_raw = raw["families"]
    if not isinstance(family_raw, list) or [item.get("id") if isinstance(item, dict) else None for item in family_raw] != list(expected):
        raise CorpusError(f"families must be exactly the ordered full partition {list(expected)}")
    families: list[FamilySpec] = []
    for index, item in enumerate(family_raw):
        context = f"families[{index}]"
        _exact_keys(item, {"id", "display_name", "source_qc", "source_files", "compiled_stem", "baselines"}, context)
        if not all(isinstance(item[key], str) and item[key] for key in ("id", "display_name", "source_qc", "compiled_stem")):
            raise CorpusError(f"{context} string fields must be non-empty")
        if _LOGICAL_ID.fullmatch(item["id"]) is None:
            raise CorpusError(f"{context}.id must be a logical identifier")
        source_qc = _relative_path(item["source_qc"], f"{context}.source_qc").as_posix()
        compiled_stem = _relative_path(item["compiled_stem"], f"{context}.compiled_stem").as_posix()
        if not isinstance(item["source_files"], list) or not item["source_files"]:
            raise CorpusError(f"{context}.source_files must be a non-empty array")
        source_files = tuple(ArtifactDeclaration.parse(value, f"{context}.source_files") for value in item["source_files"])
        if source_qc not in {entry.path for entry in source_files}:
            raise CorpusError(f"{context}.source_qc must be present in source_files")
        _verify_declarations(roots["source"], source_files, f"{item['id']} source")
        source_parent = PurePosixPath(source_qc).parent
        source_directory = safe_join(roots["source"], source_parent) if str(source_parent) != "." else roots["source"]
        actual_source_paths: set[str] = set()
        for candidate in source_directory.rglob("*"):
            if _is_reparse(candidate):
                raise CorpusError(f"{context} source tree contains a symlink or reparse point: {candidate}")
            if candidate.is_file():
                actual_source_paths.add(candidate.relative_to(roots["source"]).as_posix())
        declared_source_paths = {entry.path for entry in source_files}
        if actual_source_paths != declared_source_paths:
            raise CorpusError(
                f"{context} source file set mismatch: expected {sorted(declared_source_paths)}, "
                f"got {sorted(actual_source_paths)}"
            )
        if not isinstance(item["baselines"], dict) or "original" not in item["baselines"]:
            raise CorpusError(f"{context}.baselines must contain original")
        baselines: dict[str, tuple[ArtifactDeclaration, ...]] = {}
        for lane, values in item["baselines"].items():
            if lane not in roots or lane not in BASELINE_LANES:
                raise CorpusError(f"{context} baseline {lane} has no declared compatible root")
            if not isinstance(values, list) or not values:
                raise CorpusError(f"{context} baseline {lane} must contain artifacts")
            declarations = tuple(ArtifactDeclaration.parse(value, f"{context}.baselines.{lane}") for value in values)
            _verify_declarations(roots[lane], declarations, f"{item['id']} {lane}")
            _verify_exact_sidecars(roots[lane], compiled_stem, declarations, f"{item['id']} {lane}")
            baselines[lane] = declarations
        families.append(FamilySpec(item["id"], item["display_name"], source_qc, source_files, compiled_stem, MappingProxyType(baselines)))
    return Corpus(SCHEMA_VERSION, CORPUS_ID, MappingProxyType(roots), pressure, full, tuple(families))


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def build_cache_key(*, source: str, tools: Mapping[str, str], scripts: Mapping[str, str], settings: Mapping[str, Any]) -> str:
    if not isinstance(source, str) or _SHA256.fullmatch(source) is None:
        raise ValueError("source must be a SHA-256 digest")
    for context, values in (("tools", tools), ("scripts", scripts)):
        if not isinstance(values, Mapping) or any(
            not isinstance(name, str) or not name or not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
            for name, digest in values.items()
        ):
            raise ValueError(f"{context} values must be SHA-256 digests")
    if not isinstance(settings, Mapping):
        raise TypeError("settings must be a JSON object")
    frozen_settings = _deep_freeze_json(settings, "settings")
    payload = {"schema_version": SCHEMA_VERSION, "source": source, "tools": dict(tools), "scripts": dict(scripts), "settings": _thaw(frozen_settings)}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _deep_freeze_json(value: Any, context: str, *, key_name: str = "") -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise TypeError(f"{context} JSON object keys must be non-empty strings")
            frozen[key] = _deep_freeze_json(item, f"{context}.{key}", key_name=key)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze_json(item, f"{context}[]") for item in value)
    if value is None or isinstance(value, (str, bool)) or type(value) is int:
        if isinstance(value, str):
            lowered = key_name.lower()
            if "sha256" in lowered or lowered.endswith("digest"):
                if _SHA256.fullmatch(value) is None:
                    raise ValueError(f"{context} digest must be lowercase SHA-256")
            if lowered.endswith("_path") or lowered == "path":
                _relative_path(value, context)
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{context} must be finite")
        return value
    raise TypeError(f"{context} contains a non-JSON value")


def _freeze_provenance(provenance: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(provenance, Mapping) or set(provenance) != {"tool", "scripts", "settings"} or not all(
        isinstance(provenance[key], Mapping) for key in ("tool", "scripts", "settings")
    ):
        raise ValueError("provenance must contain exactly tool/scripts/settings objects")
    scripts = provenance["scripts"]
    for name, digest in scripts.items():
        if not isinstance(name, str) or not name or not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError("provenance scripts values must be SHA-256 digests")
    return _deep_freeze_json(provenance, "provenance")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class BenchmarkRecord:
    schema_version: int
    corpus_id: str
    family_id: str
    lane: str
    strategy: str
    cache_key: str
    artifacts: Mapping[str, int]
    total_bytes: int
    geometry_comparable_bytes: int
    dx80_optional_bytes: int
    provenance: Mapping[str, Any]
    gates: Mapping[str, str]
    elapsed_seconds: float | None = None
    failure: str | None = None

    @classmethod
    def create(cls, *, corpus_id: str, family_id: str, lane: str, strategy: str, cache_key: str,
               artifacts: Mapping[str, int], provenance: Mapping[str, Any], gates: Mapping[str, str],
               elapsed_seconds: float | None = None, failure: str | None = None) -> "BenchmarkRecord":
        _logical_id(corpus_id, "corpus_id", _CORPUS_ID_RE)
        _logical_id(family_id, "family_id")
        _logical_id(strategy, "strategy", _STRATEGY_RE)
        if lane not in BASELINE_LANES:
            raise ValueError(f"unknown benchmark lane: {lane}")
        if not isinstance(cache_key, str) or _SHA256.fullmatch(cache_key) is None:
            raise ValueError("cache_key must be a SHA-256 digest")
        frozen_provenance = _freeze_provenance(provenance)
        elapsed = _finite_nonnegative(elapsed_seconds, "elapsed_seconds", allow_none=True)
        if failure is not None and not isinstance(failure, str):
            raise ValueError("failure must be null or a string")
        if not isinstance(artifacts, Mapping):
            raise TypeError("artifacts must be an object")
        clean: dict[str, int] = {}
        for kind, size in artifacts.items():
            if kind not in COMPILED_KINDS or not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError(f"invalid artifact byte decomposition: {kind}={size}")
            clean[kind] = size
        required_gates = {"structural", "visual", "runtime"}
        if not isinstance(gates, Mapping) or set(gates) != required_gates or any(
            not isinstance(value, str) or value not in {"pass", "fail", "not_run"} for value in gates.values()
        ):
            raise ValueError("gates must contain structural/visual/runtime with pass/fail/not_run")
        dx80 = clean.get(".dx80.vtx", 0)
        total = sum(clean.values())
        return cls(SCHEMA_VERSION, corpus_id, family_id, lane, strategy, cache_key,
                   _deep_freeze_json(dict(sorted(clean.items())), "artifacts"), total, total - dx80, dx80,
                   frozen_provenance, _deep_freeze_json(gates, "gates"), elapsed, failure)

    @classmethod
    def from_dict(cls, raw: object) -> "BenchmarkRecord":
        if not isinstance(raw, dict):
            raise ValueError("record must be an object")
        expected = {
            "schema_version", "corpus_id", "family_id", "lane", "strategy", "cache_key",
            "artifacts", "total_bytes", "geometry_comparable_bytes", "dx80_optional_bytes",
            "provenance", "gates", "elapsed_seconds", "failure",
        }
        if set(raw) != expected:
            raise ValueError(f"record keys must be exactly {sorted(expected)}")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported record schema_version")
        record = cls.create(
            corpus_id=raw["corpus_id"], family_id=raw["family_id"], lane=raw["lane"],
            strategy=raw["strategy"], cache_key=raw["cache_key"], artifacts=raw["artifacts"],
            provenance=raw["provenance"], gates=raw["gates"],
            elapsed_seconds=raw["elapsed_seconds"], failure=raw["failure"],
        )
        if (raw["total_bytes"], raw["geometry_comparable_bytes"], raw["dx80_optional_bytes"]) != (
            record.total_bytes, record.geometry_comparable_bytes, record.dx80_optional_bytes
        ):
            raise ValueError("record derived byte totals do not match artifacts")
        return record

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "corpus_id": self.corpus_id,
            "family_id": self.family_id, "lane": self.lane, "strategy": self.strategy,
            "cache_key": self.cache_key, "artifacts": _thaw(self.artifacts),
            "total_bytes": self.total_bytes, "geometry_comparable_bytes": self.geometry_comparable_bytes,
            "dx80_optional_bytes": self.dx80_optional_bytes, "provenance": _thaw(self.provenance),
            "gates": _thaw(self.gates), "elapsed_seconds": self.elapsed_seconds, "failure": self.failure,
        }


def _artifact_bytes(declarations: Iterable[ArtifactDeclaration | Mapping[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for raw in declarations:
        item = raw if isinstance(raw, ArtifactDeclaration) else ArtifactDeclaration.parse(dict(raw), "declared artifact")
        kind = compiled_kind(item.path)
        if not kind:
            raise CorpusError(f"not a compiled sidecar: {item.path}")
        result[kind] = result.get(kind, 0) + item.size_bytes
    return result


def import_compiled_baseline(*, corpus_id: str, family_id: str, lane: str, root: Path, compiled_stem: str,
                             declared_artifacts: Iterable[ArtifactDeclaration | Mapping[str, Any]],
                             provenance: Mapping[str, Any], source_hash: str) -> BenchmarkRecord:
    declarations = tuple(item if isinstance(item, ArtifactDeclaration) else ArtifactDeclaration.parse(dict(item), "declared artifact") for item in declared_artifacts)
    safe = _safe_root(root, f"{lane} root")
    _verify_declarations(safe, declarations, f"{family_id} {lane}")
    _verify_exact_sidecars(safe, compiled_stem, declarations, f"{family_id} {lane}")
    tools = {key: value for key, value in provenance.get("tool", {}).items() if isinstance(value, str) and _SHA256.fullmatch(value)}
    scripts = {key: value for key, value in provenance.get("scripts", {}).items() if isinstance(value, str) and _SHA256.fullmatch(value)}
    cache_key = build_cache_key(source=source_hash, tools=tools, scripts=scripts, settings={"lane": lane, **dict(provenance.get("settings", {}))})
    return BenchmarkRecord.create(corpus_id=corpus_id, family_id=family_id, lane=lane, strategy=lane,
                                  cache_key=cache_key, artifacts=_artifact_bytes(declarations), provenance=provenance,
                                  gates={"structural": "not_run", "visual": "not_run", "runtime": "not_run"})


def summarize_records(
    records: Iterable[BenchmarkRecord], *, expected_family_ids: Sequence[str] | None = None,
    required_lanes: Sequence[str] | None = None,
) -> dict[str, Any]:
    records = tuple(records)
    if not records:
        lanes_to_report = tuple(required_lanes or BASELINE_LANES)
        families = tuple(expected_family_ids or ())
    else:
        corpus_ids = {record.corpus_id for record in records}
        if len(corpus_ids) != 1:
            raise ValueError("all summary records must share one corpus")
        lanes_to_report = tuple(required_lanes or dict.fromkeys(record.lane for record in records))
        families = tuple(expected_family_ids or dict.fromkeys(record.family_id for record in records))
        seen: set[tuple[str, str]] = set()
        for record in records:
            key = (record.lane, record.family_id)
            if key in seen:
                raise ValueError(f"duplicate summary record for {record.lane}/{record.family_id}")
            seen.add(key)
        expected_pairs = {(lane, family) for lane in lanes_to_report for family in families}
        missing = expected_pairs - seen
        extra = seen - expected_pairs
        if missing:
            raise ValueError(f"missing summary records: {sorted(missing)}")
        if extra:
            raise ValueError(f"unexpected summary records: {sorted(extra)}")
    lanes: dict[str, Any] = {}
    per_extension: dict[str, dict[str, int]] = {}
    for lane in BASELINE_LANES:
        lane_records = tuple(record for record in records if record.lane == lane)
        geometry = [record.geometry_comparable_bytes for record in lane_records if record.failure is None]
        extension_totals: dict[str, int] = {}
        for record in lane_records:
            if record.failure is not None:
                continue
            for kind, size in record.artifacts.items():
                extension_totals[kind] = extension_totals.get(kind, 0) + size
        per_extension[lane] = dict(sorted(extension_totals.items()))
        lanes[lane] = {
            "record_count": len(lane_records),
            "failure_count": sum(record.failure is not None for record in lane_records),
            "failures": [{"family_id": record.family_id, "failure": record.failure} for record in lane_records if record.failure is not None],
            "median_geometry_comparable_bytes": statistics.median(geometry) if geometry else None,
            "worst_geometry_comparable_bytes": max(geometry) if geometry else None,
            "total_dx80_optional_bytes": sum(record.dx80_optional_bytes for record in lane_records if record.failure is None),
            "per_extension_bytes": per_extension[lane],
        }
    quality = "verified" if records and all(
        record.failure is None
        and {".mdl", ".vvd", ".dx90.vtx"}.issubset(record.artifacts)
        and all(value == "pass" for value in record.gates.values())
        for record in records
    ) else "unverified"
    return {"schema_version": SCHEMA_VERSION, "corpus_id": records[0].corpus_id if records else CORPUS_ID,
            "quality_status": quality, "quality_claim": None,
            "lanes": lanes, "dx80_optional": {lane: values["total_dx80_optional_bytes"] for lane, values in lanes.items()}}


def parse_control_results(raw: object, *, expected_family_ids: Sequence[str] = FULL_FAMILY_IDS) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, dict):
        raise ValueError("control evidence must be an object")
    if set(raw) != {"schema_version", "corpus_id", "results"}:
        raise ValueError("control evidence keys must be exactly schema_version/corpus_id/results")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
        raise ValueError("invalid control schema_version")
    if raw["corpus_id"] != CORPUS_ID or not isinstance(raw["corpus_id"], str):
        raise ValueError("invalid control corpus_id")
    if not isinstance(raw["results"], list):
        raise ValueError("control results must be an array")
    expected = tuple(expected_family_ids)
    ids: list[str] = []
    parsed: list[Mapping[str, Any]] = []
    required = {"family_id", "status", "returncode", "elapsed_seconds", "log", "autofixes", "source_mutated"}
    for index, item in enumerate(raw["results"]):
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError(f"control result {index} keys must be exactly {sorted(required)}")
        family_id = _logical_id(item["family_id"], f"control result {index} family_id")
        ids.append(family_id)
        if not isinstance(item["status"], str) or item["status"] not in {"compiled", "failed"}:
            raise ValueError(f"control result {index} status is invalid")
        returncode = _exact_int(item["returncode"], f"control result {index} returncode")
        if (item["status"] == "compiled") != (returncode == 0):
            raise ValueError(f"control result {index} status/returncode mismatch")
        _finite_nonnegative(item["elapsed_seconds"], f"control result {index} elapsed_seconds")
        _relative_path(item["log"], f"control result {index} log logical relative path")
        if item["autofixes"] is not False:
            raise ValueError(f"control result {index} autofixes must be exactly false")
        if item["source_mutated"] is not False:
            raise ValueError(f"control result {index} source_mutated must be exactly false")
        parsed.append(_deep_freeze_json(item, f"control result {index}"))
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate control family IDs")
    if set(ids) != set(expected):
        raise ValueError("control evidence must be complete for the expected families")
    if tuple(ids) != expected:
        raise ValueError("control family order must match the fixed corpus")
    return tuple(parsed)


class StrictControlRoundtripAdapter:
    """Compile an unchanged copied QC tree. It performs no QC/SMD rewrites or autofixes."""

    def __init__(self, studiomdl: Path, game_root: Path) -> None:
        self.studiomdl = _safe_root(studiomdl.parent, "StudioMDL parent") / studiomdl.name
        if not self.studiomdl.is_file() or _is_reparse(self.studiomdl):
            raise CorpusError("StudioMDL must be a regular non-reparse file")
        self.game_root = _safe_root(game_root, "control game root")

    def compile(self, *, source_root: Path, source_files: Sequence[ArtifactDeclaration], source_qc: str,
                run_root: Path, family_id: str) -> dict[str, Any]:
        run_root.mkdir(parents=True, exist_ok=True)
        safe_run = _safe_root(run_root, "control run root")
        workspace = safe_run / family_id
        if workspace.exists():
            raise CorpusError(f"immutable control workspace already exists: {workspace}")
        workspace.mkdir()
        started = time.monotonic()
        for declaration in source_files:
            source = safe_join(source_root, declaration.path)
            target = workspace / PurePosixPath(declaration.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            # A compiler is an external process: private copies are required even when
            # hardlinks are available, because a misbehaving tool must not mutate source.
            shutil.copy2(source, target)
        qc = safe_join(workspace, source_qc)
        completed = subprocess.run([str(self.studiomdl), "-game", str(self.game_root), str(qc)], cwd=qc.parent,
                                   capture_output=True, text=True, check=False)
        log = workspace / "studiomdl.log"
        log.write_text(completed.stdout + completed.stderr, encoding="utf-8", errors="replace")
        return {"status": "compiled" if completed.returncode == 0 else "failed", "returncode": completed.returncode,
                "elapsed_seconds": time.monotonic() - started, "log": log.relative_to(safe_run).as_posix(),
                "autofixes": False, "source_mutated": False}
