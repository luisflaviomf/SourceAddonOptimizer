from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import time
from typing import Iterable, Literal, Sequence

from .compiled_validation import expected_compiled_family, validate_compiled_family
from .smd import parse_smd


Partition = Literal["development", "holdout"]


@dataclass(frozen=True)
class BenchmarkFamily:
    id: str
    partition: Partition
    include: tuple[str, ...]
    focus_regions: tuple[str, ...]
    file_count: int
    total_bytes: int
    tree_sha256: str


@dataclass(frozen=True)
class BenchmarkCorpus:
    schema: int
    families: tuple[BenchmarkFamily, ...]

    @property
    def development(self) -> tuple[BenchmarkFamily, ...]:
        return tuple(family for family in self.families if family.partition == "development")

    @property
    def holdout(self) -> tuple[BenchmarkFamily, ...]:
        return tuple(family for family in self.families if family.partition == "holdout")

    def partition(self, name: str) -> tuple[BenchmarkFamily, ...]:
        if name == "development":
            return self.development
        if name == "holdout":
            return self.holdout
        if name == "all":
            return self.families
        raise ValueError(f"unknown benchmark partition: {name}")


@dataclass(frozen=True)
class Lane:
    id: str
    optimizer_mode: str
    ratio: float | None
    implementation: Literal["current", "historical"]


LANES = (
    Lane("normal-safe", "normal", 0.75, "current"),
    Lane("maximum-8812c7a", "maximum", None, "historical"),
    Lane("maximum-adaptive-v2", "maximum", None, "current"),
)


@dataclass(frozen=True)
class ModelInventory:
    comparable_bytes: int
    dx80_bytes: int
    file_count: int
    mdl_count: int
    dx90_count: int
    dx80_count: int


@dataclass(frozen=True)
class ProcessMeasurement:
    exit_code: int
    wall_seconds: float
    cpu_seconds: float
    peak_working_set_bytes: int


@dataclass(frozen=True)
class FamilyResult:
    family_id: str
    partition: str
    lane: str
    status: str
    exit_code: int
    input_tree_sha256: str
    original_comparable: int
    final_comparable: int
    dx80_removed: int
    final_dx80_bytes: int
    wall_seconds: float
    cpu_seconds: float
    peak_working_set_bytes: int
    original_triangles: int
    final_triangles: int
    original_vertices: int
    final_vertices: int
    source_models: int
    optimized_models: int
    preserved_models: int
    failed_models: int
    dx90_count: int
    full_renders: int
    targeted_renders: int
    studiomdl_compiles: int
    simplifier_evaluations: int
    integrity_failures: tuple[str, ...]
    profile_sha256: str
    output_path: str
    work_path: str
    log_path: str
    stage_seconds: tuple[tuple[str, float], ...] = ()

    @classmethod
    def fixture(cls, family_id: str, *, original: int, final: int, dx80: int) -> "FamilyResult":
        return cls(
            family_id, "development", "fixture", "ok", 0, "0" * 64,
            original, final, dx80, 0, 1.0, 1.0, 1, 100, 50, 100, 50,
            1, 1, 0, 0, 1, 0, 0, 1, 1, (), "", "", "", "",
        )

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "FamilyResult":
        values = dict(payload)
        values["integrity_failures"] = tuple(str(value) for value in values.get("integrity_failures", ()))
        values["stage_seconds"] = tuple(
            (str(item[0]), float(item[1])) for item in values.get("stage_seconds", ())  # type: ignore[index]
        )
        allowed = {field.name for field in fields(cls)}
        return cls(**{name: value for name, value in values.items() if name in allowed})  # type: ignore[arg-type]


@dataclass(frozen=True)
class AggregateResult:
    lane: str
    family_count: int
    original_comparable: int
    final_comparable: int
    saved_comparable: int
    dx80_removed: int
    reduction_percent: float
    wall_seconds: float
    cpu_seconds: float
    peak_working_set_bytes: int
    original_triangles: int
    final_triangles: int
    original_vertices: int
    final_vertices: int
    optimized_models: int
    preserved_models: int
    failed_models: int
    full_renders: int
    targeted_renders: int
    studiomdl_compiles: int
    simplifier_evaluations: int
    integrity_failure_count: int


def _require_keys(payload: dict[str, object], expected: set[str], label: str) -> None:
    if set(payload) != expected:
        raise ValueError(f"{label} fields are invalid")


def load_corpus(path: Path) -> BenchmarkCorpus:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if type(payload) is not dict:
        raise ValueError("benchmark corpus must be an object")
    _require_keys(payload, {"schema", "families"}, "corpus")
    if payload["schema"] != 1 or type(payload["families"]) is not list:
        raise ValueError("benchmark corpus schema is invalid")
    families = []
    seen: set[str] = set()
    for raw in payload["families"]:
        if type(raw) is not dict:
            raise ValueError("benchmark family must be an object")
        _require_keys(
            raw,
            {"id", "partition", "include", "focus_regions", "file_count", "total_bytes", "tree_sha256"},
            "family",
        )
        family_id = raw["id"]
        partition = raw["partition"]
        includes = raw["include"]
        focus = raw["focus_regions"]
        sha256 = raw["tree_sha256"]
        if type(family_id) is not str or not family_id or family_id in seen:
            raise ValueError("benchmark family ID is invalid or duplicated")
        if partition not in ("development", "holdout"):
            raise ValueError("benchmark partition is invalid")
        if type(includes) is not list or not includes or any(type(value) is not str or not value for value in includes):
            raise ValueError("benchmark include list is invalid")
        if type(focus) is not list or not focus or any(type(value) is not str or not value for value in focus):
            raise ValueError("benchmark focus regions are invalid")
        if type(raw["file_count"]) is not int or int(raw["file_count"]) <= 0:
            raise ValueError("benchmark file count is invalid")
        if type(raw["total_bytes"]) is not int or int(raw["total_bytes"]) <= 0:
            raise ValueError("benchmark byte count is invalid")
        if type(sha256) is not str or len(sha256) != 64 or any(value not in "0123456789abcdef" for value in sha256):
            raise ValueError("benchmark tree SHA-256 is invalid")
        for include in includes:
            pure = PurePosixPath(include)
            if pure.is_absolute() or ".." in pure.parts or not include.startswith("models/"):
                raise ValueError("benchmark include path escapes models")
        seen.add(family_id)
        families.append(
            BenchmarkFamily(
                family_id,
                partition,  # type: ignore[arg-type]
                tuple(includes),
                tuple(focus),
                int(raw["file_count"]),
                int(raw["total_bytes"]),
                sha256,
            )
        )
    return BenchmarkCorpus(1, tuple(families))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_include(relative: str, include: str) -> bool:
    if include.endswith("/"):
        return relative.startswith(include)
    return relative == include or relative.startswith(include + ".")


def family_input_files(addon_root: Path, family: BenchmarkFamily) -> tuple[Path, ...]:
    root = Path(addon_root).resolve()
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if any(_matches_include(relative, include) for include in family.include):
            files.append(path)
    return tuple(sorted(files, key=lambda value: value.relative_to(root).as_posix().casefold()))


def tree_fingerprint(addon_root: Path, files: Iterable[Path]) -> tuple[int, int, str]:
    root = Path(addon_root).resolve()
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in files:
        resolved = Path(path).resolve()
        relative = resolved.relative_to(root).as_posix()
        size = resolved.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(resolved).encode("ascii"))
        digest.update(b"\n")
        count += 1
        total += size
    return count, total, digest.hexdigest()


def verify_family_input(addon_root: Path, family: BenchmarkFamily) -> tuple[Path, ...]:
    files = family_input_files(addon_root, family)
    count, total, sha256 = tree_fingerprint(addon_root, files)
    if count != family.file_count:
        raise ValueError(f"{family.id}: file count changed ({count} != {family.file_count})")
    if total != family.total_bytes:
        raise ValueError(f"{family.id}: byte count changed ({total} != {family.total_bytes})")
    if sha256 != family.tree_sha256:
        raise ValueError(f"{family.id}: tree SHA-256 changed ({sha256} != {family.tree_sha256})")
    return files


def copy_family_input(addon_root: Path, family: BenchmarkFamily, destination: Path) -> None:
    source = Path(addon_root).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    files = verify_family_input(source, family)
    for path in files:
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    addon_json = source / "addon.json"
    if addon_json.is_file():
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(addon_json, destination / "addon.json")
    materials_root = source / "materials"
    if materials_root.is_dir():
        for path in sorted(materials_root.rglob("*"), key=lambda value: value.as_posix().casefold()):
            if not path.is_file() or path.suffix.casefold() != ".vmt":
                continue
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _resolved_key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def assert_isolated_lane_paths(input_path: Path, work_path: Path, output_path: Path) -> None:
    paths = [Path(value).resolve() for value in (input_path, work_path, output_path)]
    keys = [_resolved_key(path) for path in paths]
    for index, first in enumerate(paths):
        for second in paths[index + 1 :]:
            first_key = _resolved_key(first)
            second_key = _resolved_key(second)
            try:
                common = os.path.commonpath((first_key, second_key))
            except ValueError:
                continue
            if common in (first_key, second_key):
                raise ValueError(f"benchmark lane paths overlap: {first} and {second}")
    if len(set(keys)) != len(keys):
        raise ValueError("benchmark lane paths overlap")


def scan_compiled_models(models_root: Path) -> ModelInventory:
    comparable = dx80 = count = mdl = dx90 = dx80_count = 0
    for path in sorted(Path(models_root).rglob("*"), key=lambda value: value.as_posix().casefold()):
        if not path.is_file():
            continue
        count += 1
        name = path.name.casefold()
        size = path.stat().st_size
        if name.endswith(".dx80.vtx"):
            dx80 += size
            dx80_count += 1
        else:
            comparable += size
        if name.endswith(".mdl"):
            mdl += 1
        if name.endswith(".dx90.vtx"):
            dx90 += 1
    return ModelInventory(comparable, dx80, count, mdl, dx90, dx80_count)


def aggregate_results(lane: str, families: Sequence[FamilyResult]) -> AggregateResult:
    original = sum(family.original_comparable for family in families)
    final = sum(family.final_comparable for family in families)
    saved = original - final
    return AggregateResult(
        lane,
        len(families),
        original,
        final,
        saved,
        sum(family.dx80_removed for family in families),
        saved * 100.0 / original if original else 0.0,
        sum(family.wall_seconds for family in families),
        sum(family.cpu_seconds for family in families),
        max((family.peak_working_set_bytes for family in families), default=0),
        sum(family.original_triangles for family in families),
        sum(family.final_triangles for family in families),
        sum(family.original_vertices for family in families),
        sum(family.final_vertices for family in families),
        sum(family.optimized_models for family in families),
        sum(family.preserved_models for family in families),
        sum(family.failed_models for family in families),
        sum(family.full_renders for family in families),
        sum(family.targeted_renders for family in families),
        sum(family.studiomdl_compiles for family in families),
        sum(family.simplifier_evaluations for family in families),
        sum(len(family.integrity_failures) for family in families),
    )


def build_lane_command(
    lane: Lane,
    *,
    repo_root: Path,
    addon_path: Path,
    work_path: Path,
    blender: Path,
    studiomdl: Path,
    framework_root: Path | None,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(repo_root) / "build_optimized_addon.py"),
        str(addon_path),
        "--suffix", "_benchmark_output",
        "--overwrite",
        "--work", str(work_path),
        "--overwrite-work",
        "--optimizer-mode", lane.optimizer_mode,
        "--blender", str(blender),
        "--studiomdl", str(studiomdl),
        "--format", "smd",
        "--merge", "0",
        "--autosmooth", "45",
        "--jobs", "2",
        "--decompile-jobs", "2",
        "--compile-jobs", "2",
        "--restore-skins",
        "--strict",
        "--single-addon-only",
    ]
    if lane.ratio is not None:
        command.extend(("--ratio", format(lane.ratio, ".8g")))
    if lane.id == "maximum-8812c7a":
        command.extend(("--maximum-jobs", "1", "--maximum-max-candidates", "18"))
    if lane.id == "maximum-adaptive-v2" and framework_root is not None:
        command.extend(("--maximum-framework-resolver", str(framework_root)))
    return command


def run_monitored(command: Sequence[str], *, cwd: Path, log_path: Path) -> ProcessMeasurement:
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - fallback for minimal benchmark hosts
        psutil = None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    peak = 0
    cpu = 0.0
    seen_cpu: dict[tuple[int, float], float] = {}
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        root_process = psutil.Process(process.pid) if psutil is not None else None
        assert process.stdout is not None
        while True:
            line = process.stdout.readline()
            if line:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
            if root_process is not None:
                candidates = []
                try:
                    candidates = [root_process, *root_process.children(recursive=True)]
                except psutil.Error:
                    pass
                working = 0
                for candidate in candidates:
                    try:
                        identity = (candidate.pid, candidate.create_time())
                        times = candidate.cpu_times()
                        seen_cpu[identity] = max(seen_cpu.get(identity, 0.0), times.user + times.system)
                        working += candidate.memory_info().rss
                    except psutil.Error:
                        continue
                peak = max(peak, working)
            if process.poll() is not None:
                remainder = process.stdout.read()
                if remainder:
                    log.write(remainder)
                    print(remainder, end="", flush=True)
                break
            if not line:
                time.sleep(0.05)
        exit_code = process.wait()
    if seen_cpu:
        cpu = sum(seen_cpu.values())
    return ProcessMeasurement(exit_code, time.perf_counter() - started, cpu, peak)


def _source_geometry(work_path: Path) -> tuple[int, int]:
    src = Path(work_path) / "src"
    original = final = 0
    if not src.is_dir():
        return 0, 0
    for path in src.rglob("*.smd"):
        try:
            triangles = len(parse_smd(path.read_text(encoding="utf-8", errors="replace")).triangles)
        except (OSError, ValueError):
            continue
        if triangles <= 0:
            continue
        relative_parts = {part.casefold() for part in path.relative_to(src).parts[:-1]}
        if "output" in relative_parts:
            final += triangles
        else:
            original += triangles
    return original, final


def _maximum_report(work_path: Path) -> dict[str, object]:
    preferred = Path(work_path) / "logs" / "maximum_adaptive_report.json"
    candidates = [preferred] if preferred.is_file() else []
    candidates.extend(
        path for path in Path(work_path).rglob("*.json")
        if "maximum" in path.name.casefold() and path not in candidates
    )
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if type(payload) is dict and (
                "original_triangles" in payload or "summary" in payload or "regions" in payload
            ):
                payload["_path"] = str(path)
                return payload
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _int_value(payload: dict[str, object], *keys: str) -> int:
    current: object = payload
    for key in keys:
        if type(current) is not dict or key not in current:
            return 0
        current = current[key]
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return 0
    return int(current)


def _profile_sha(payload: dict[str, object]) -> str:
    profile = payload.get("profile")
    if type(profile) is dict and type(profile.get("sha256")) is str:
        return str(profile["sha256"])
    value = payload.get("profile_sha256")
    return str(value) if type(value) is str else ""


def _model_integrity(original_models: Path, final_models: Path) -> tuple[int, int, int, int, tuple[str, ...]]:
    optimized = preserved = failed = original_vertices = final_vertices = 0
    failures: list[str] = []
    mdls = sorted(original_models.rglob("*.mdl"), key=lambda path: path.as_posix().casefold())
    for mdl in mdls:
        relative = PurePosixPath(mdl.relative_to(original_models).as_posix())
        try:
            expected = expected_compiled_family(original_models, relative)
            validation = validate_compiled_family(final_models, expected)
        except (OSError, ValueError) as exc:
            failed += 1
            failures.append(f"{relative}:{exc}")
            continue
        if not validation.passed:
            failed += 1
            failures.extend(f"{relative}:{value}" for value in validation.failures)
        try:
            from .compiled_validation import _read_vvd

            original_vertices += _read_vvd(mdl.with_suffix(".vvd"))[1]
            final_vertices += _read_vvd(final_models / Path(*relative.parts).with_suffix(".vvd"))[1]
        except (OSError, ValueError):
            failures.append(f"{relative}:vertex_inventory_failed")
        final_mdl = final_models / Path(*relative.parts)
        companions = (".mdl", ".vvd", ".dx90.vtx")
        same = True
        for suffix in companions:
            before = mdl.with_suffix(suffix)
            after = final_mdl.with_suffix(suffix)
            if not before.is_file() or not after.is_file() or sha256_file(before) != sha256_file(after):
                same = False
                break
        if same:
            preserved += 1
        else:
            optimized += 1
    return optimized, preserved, failed, original_vertices, final_vertices, tuple(dict.fromkeys(failures))


def analyze_family_result(
    family: BenchmarkFamily,
    lane: Lane,
    *,
    addon_path: Path,
    output_path: Path,
    work_path: Path,
    log_path: Path,
    measurement: ProcessMeasurement,
) -> FamilyResult:
    original_models = Path(addon_path) / "models"
    final_models = Path(output_path) / "models"
    original = scan_compiled_models(original_models)
    final = scan_compiled_models(final_models) if final_models.is_dir() else ModelInventory(0, 0, 0, 0, 0, 0)
    optimized, preserved, failed, original_vertices, final_vertices, failures = _model_integrity(
        original_models, final_models
    ) if final_models.is_dir() else (0, 0, original.mdl_count, 0, 0, ("missing_output_models",))
    if final.dx80_count:
        failures = tuple((*failures, f"final_dx80_count:{final.dx80_count}"))
    if final.dx90_count != original.mdl_count:
        failures = tuple((*failures, f"dx90_count:{final.dx90_count}/{original.mdl_count}"))
    original_triangles, final_triangles = _source_geometry(work_path)
    report = _maximum_report(work_path)
    if _int_value(report, "original_triangles"):
        original_triangles = _int_value(report, "original_triangles")
    if _int_value(report, "final_triangles"):
        final_triangles = _int_value(report, "final_triangles")
    regions = report.get("regions") if type(report.get("regions")) is dict else {}
    stage_seconds = []
    for stage in report.get("stages", ()) if type(report.get("stages")) is list else ():
        if type(stage) is dict and type(stage.get("stage")) is str and isinstance(stage.get("wall_seconds"), (int, float)):
            stage_seconds.append((str(stage["stage"]), float(stage["wall_seconds"])))
    status = "ok" if measurement.exit_code == 0 and not failures else "failed"
    return FamilyResult(
        family.id,
        family.partition,
        lane.id,
        status,
        measurement.exit_code,
        family.tree_sha256,
        original.comparable_bytes,
        final.comparable_bytes,
        original.dx80_bytes - final.dx80_bytes,
        final.dx80_bytes,
        measurement.wall_seconds,
        measurement.cpu_seconds,
        measurement.peak_working_set_bytes,
        original_triangles,
        final_triangles,
        original_vertices,
        final_vertices,
        original.mdl_count,
        optimized,
        preserved,
        failed,
        final.dx90_count,
        _int_value(report, "full_family_renders") or _int_value(report, "summary", "full_renders"),
        _int_value(report, "targeted_renders") or _int_value(report, "summary", "targeted_renders"),
        _int_value(report, "studiomdl_compiles") or _int_value(report, "summary", "compile_count"),
        _int_value(report, "simplifier_evaluations") or _int_value(report, "summary", "candidate_count"),
        failures,
        _profile_sha(report),
        str(Path(output_path).resolve()),
        str(Path(work_path).resolve()),
        str(Path(log_path).resolve()),
        tuple(stage_seconds),
    )


def write_json(path: Path, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def result_payload(result: FamilyResult) -> dict[str, object]:
    return asdict(result)


def aggregate_payload(result: AggregateResult) -> dict[str, object]:
    return asdict(result)


def load_family_results(root: Path, partition: str = "all") -> tuple[FamilyResult, ...]:
    results = []
    result_root = Path(root)
    for path in result_root.rglob("result.json"):
        relative = path.relative_to(result_root)
        if not relative.parts or relative.parts[0] not in {"development", "holdout"}:
            continue
        try:
            result = FamilyResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if partition == "all" or result.partition == partition:
            results.append(result)
    return tuple(sorted(results, key=lambda result: (result.family_id, result.lane)))
