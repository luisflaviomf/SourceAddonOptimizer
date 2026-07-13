from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .benchmarking import (
    CORPUS_ID,
    FULL_FAMILY_IDS,
    PRESSURE_FAMILY_IDS,
    SCHEMA_VERSION,
    ArtifactDeclaration,
    CorpusError,
    _is_reparse,
    safe_existing_file,
    safe_existing_root,
    safe_join,
    sha256_file,
)


CALIBRATION_FAMILY_IDS = (
    "pontiac_transam_wheel",
    "dodge_charger",
    "toyota_supra",
    "nissan_skyline_gtr32",
    "dodge_monaco_police",
)
HOLDOUT_FAMILY_IDS = (
    "ford_fairlane",
    "vw_beetle",
    "vw_touareg",
    "ferrari_365_fullrig",
    "caterham_620r",
)
CANONICAL_LVS_FAMILY_IDS = CALIBRATION_FAMILY_IDS + HOLDOUT_FAMILY_IDS
_COPY_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class LvsSourceFamily:
    family_id: str
    source_qc: str
    source_files: tuple[ArtifactDeclaration, ...]


@dataclass(frozen=True)
class LvsSourceManifest:
    corpus_id: str
    families: tuple[LvsSourceFamily, ...]

    def family(self, family_id: str) -> LvsSourceFamily:
        matches = tuple(item for item in self.families if item.family_id == family_id)
        if len(matches) != 1:
            raise CorpusError(f"canonical LVS family is missing or duplicated: {family_id}")
        return matches[0]


@dataclass(frozen=True)
class PreparedLvsSourceRoot:
    source_root: Path
    calibration_family_ids: tuple[str, ...]
    holdout_family_ids: tuple[str, ...]
    file_count: int
    total_bytes: int


@dataclass
class _StagingOwnership:
    root_identity: tuple[int, int, int, int]
    descendants: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)


def _overlaps(first: Path, second: Path) -> bool:
    first_text = os.path.normcase(os.path.abspath(first))
    second_text = os.path.normcase(os.path.abspath(second))
    try:
        common = os.path.commonpath((first_text, second_text))
    except ValueError:
        return False
    return common in {first_text, second_text}


def _owned_identity(path: Path, *, directory: bool | None = None) -> tuple[int, int, int, int]:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise CorpusError(f"private staging ownership is unavailable: {path}: {exc}") from exc
    if _is_reparse(path) or stat.S_ISLNK(info.st_mode):
        raise CorpusError(f"private staging contains a symlink or reparse point: {path}")
    is_directory = stat.S_ISDIR(info.st_mode)
    is_file = stat.S_ISREG(info.st_mode)
    if not (is_directory or is_file) or (directory is True and not is_directory) or (
        directory is False and not is_file
    ):
        raise CorpusError(f"private staging object has an invalid type: {path}")
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))),
        int(stat.S_IFMT(info.st_mode)),
    )


def _exact_keys(raw: object, expected: set[str], context: str) -> dict:
    if not isinstance(raw, dict) or set(raw) != expected:
        actual = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
        raise CorpusError(f"{context} keys must be exactly {sorted(expected)}; got {actual}")
    return raw


def _validate_canonical_partition() -> None:
    if (
        len(CALIBRATION_FAMILY_IDS) != 5
        or len(HOLDOUT_FAMILY_IDS) != 5
        or len(set(CANONICAL_LVS_FAMILY_IDS)) != 10
        or set(CANONICAL_LVS_FAMILY_IDS) != set(FULL_FAMILY_IDS)
        or set(CALIBRATION_FAMILY_IDS) & set(HOLDOUT_FAMILY_IDS)
        or CALIBRATION_FAMILY_IDS == PRESSURE_FAMILY_IDS
    ):
        raise CorpusError("canonical calibration/holdout partition is invalid")


def load_lvs_source_manifest(corpus_path: Path) -> LvsSourceManifest:
    _validate_canonical_partition()
    path = safe_existing_file(Path(corpus_path).absolute(), "LVS corpus")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorpusError(f"cannot read LVS corpus: {exc}") from exc
    corpus = _exact_keys(
        raw,
        {"schema_version", "corpus_id", "roots", "partitions", "families"},
        "LVS corpus",
    )
    if corpus["schema_version"] != SCHEMA_VERSION or type(corpus["schema_version"]) is not int:
        raise CorpusError("LVS corpus schema_version is invalid")
    if corpus["corpus_id"] != CORPUS_ID:
        raise CorpusError("LVS corpus_id is invalid")
    roots = _exact_keys(
        corpus["roots"], {"source", "original", "blender", "fidelity"}, "LVS roots"
    )
    expected_envs = {
        "source": "LVS_SOURCE_ROOT",
        "original": "LVS_ORIGINAL_MODELS_ROOT",
        "blender": "LVS_BLENDER_MODELS_ROOT",
        "fidelity": "LVS_FIDELITY_MODELS_ROOT",
    }
    for name, expected_env in expected_envs.items():
        root = _exact_keys(roots[name], {"env"}, f"LVS {name} root")
        if root["env"] != expected_env:
            raise CorpusError(f"LVS {name} root environment binding is invalid")
    partitions = _exact_keys(corpus["partitions"], {"pressure", "full"}, "LVS partitions")
    if partitions["pressure"] != list(PRESSURE_FAMILY_IDS):
        raise CorpusError("LVS pressure partition drifted")
    if partitions["full"] != list(FULL_FAMILY_IDS):
        raise CorpusError("LVS full partition drifted")
    if not isinstance(corpus["families"], list):
        raise CorpusError("LVS families must be an array")
    raw_ids = tuple(
        item.get("id") if isinstance(item, dict) else None for item in corpus["families"]
    )
    if raw_ids != FULL_FAMILY_IDS:
        raise CorpusError(
            f"LVS family order/membership is invalid: expected {FULL_FAMILY_IDS}, got {raw_ids}"
        )
    parsed: dict[str, LvsSourceFamily] = {}
    for index, value in enumerate(corpus["families"]):
        family = _exact_keys(
            value,
            {
                "id", "display_name", "source_qc", "source_files",
                "compiled_stem", "baselines",
            },
            f"LVS family {index}",
        )
        if not isinstance(family["source_files"], list) or not family["source_files"]:
            raise CorpusError(f"LVS family {family['id']} source_files must be non-empty")
        declarations = tuple(
            ArtifactDeclaration.parse(item, f"LVS family {family['id']} source_files[{item_index}]")
            for item_index, item in enumerate(family["source_files"])
        )
        paths = tuple(item.path for item in declarations)
        if len(paths) != len(set(paths)):
            raise CorpusError(f"LVS family {family['id']} contains duplicate source paths")
        source_qc = family["source_qc"]
        if not isinstance(source_qc, str) or source_qc not in set(paths):
            raise CorpusError(f"LVS family {family['id']} source_qc is not declared")
        source_parent = PurePosixPath(source_qc).parent
        for declaration in declarations:
            try:
                PurePosixPath(declaration.path).relative_to(source_parent)
            except ValueError as exc:
                raise CorpusError(
                    f"LVS family {family['id']} source file escapes its QC directory"
                ) from exc
        parsed[family["id"]] = LvsSourceFamily(family["id"], source_qc, declarations)
    return LvsSourceManifest(
        CORPUS_ID, tuple(parsed[family_id] for family_id in CANONICAL_LVS_FAMILY_IDS)
    )


def _regular_file_identity(path: Path, context: str) -> os.stat_result:
    if _is_reparse(path):
        raise CorpusError(f"{context} contains a symlink or reparse point: {path}")
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise CorpusError(f"cannot inspect {context}: {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise CorpusError(f"{context} is not a regular file: {path}")
    return info


def _verify_declared_file(path: Path, declaration: ArtifactDeclaration, context: str) -> None:
    info = _regular_file_identity(path, context)
    if info.st_size != declaration.size_bytes:
        raise CorpusError(
            f"size mismatch for {context} {declaration.path}: "
            f"{info.st_size} != {declaration.size_bytes}"
        )
    digest = sha256_file(path)
    if digest != declaration.sha256:
        raise CorpusError(
            f"hash mismatch for {context} {declaration.path}: "
            f"{digest} != {declaration.sha256}"
        )


def _scan_regular_files(root: Path, context: str) -> set[str]:
    found: set[str] = set()
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in tuple(directories):
            child = current_path / name
            if _is_reparse(child):
                raise CorpusError(f"{context} contains a symlink or reparse point: {child}")
        for name in files:
            child = current_path / name
            _regular_file_identity(child, context)
            relative = child.relative_to(root).as_posix()
            folded = relative.casefold()
            if any(item.casefold() == folded for item in found):
                raise CorpusError(f"{context} contains a case-colliding path: {relative}")
            found.add(relative)
    return found


def _family_control_source_root(control_root: Path, family: LvsSourceFamily) -> Path:
    logical = f"{family.family_id}/workspace/{family.family_id}"
    root = safe_join(control_root, logical)
    if not root.is_dir():
        raise CorpusError(f"{family.family_id} control source root is not a directory")
    return safe_existing_root(root, f"{family.family_id} control source root")


def _verify_family_source_tree(root: Path, family: LvsSourceFamily) -> None:
    source_parent = PurePosixPath(family.source_qc).parent
    tree_root = safe_join(root, source_parent)
    if not tree_root.is_dir():
        raise CorpusError(f"{family.family_id} source tree is not a directory")
    expected = {item.path for item in family.source_files}
    actual = {
        (source_parent / PurePosixPath(relative)).as_posix()
        for relative in _scan_regular_files(tree_root, f"{family.family_id} source tree")
    }
    missing = expected - actual
    if missing:
        raise CorpusError(
            f"{family.family_id} declared source files are missing: {sorted(missing)}"
        )


def _copy_plan(
    manifest: LvsSourceManifest, control_root: Path
) -> tuple[tuple[str, ArtifactDeclaration, Path], ...]:
    planned: dict[str, tuple[str, ArtifactDeclaration, Path]] = {}
    for family_id in CANONICAL_LVS_FAMILY_IDS:
        family = manifest.family(family_id)
        root = _family_control_source_root(control_root, family)
        _verify_family_source_tree(root, family)
        for declaration in family.source_files:
            source = safe_join(root, declaration.path)
            _verify_declared_file(source, declaration, f"{family_id} control source")
            folded = declaration.path.casefold()
            previous = planned.get(folded)
            if previous is not None:
                previous_path, previous_declaration, _ = previous
                if (
                    previous_path != declaration.path
                    or previous_declaration.size_bytes != declaration.size_bytes
                    or previous_declaration.sha256 != declaration.sha256
                ):
                    raise CorpusError(
                        f"divergent collision for prepared source path: {declaration.path}"
                    )
                continue
            planned[folded] = (declaration.path, declaration, source)
    return tuple(sorted(planned.values(), key=lambda item: item[0].casefold()))


def _copy_file_no_follow(
    source: Path, destination: Path, declaration: ArtifactDeclaration
) -> None:
    before = _regular_file_identity(source, "copy source")
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
    )
    source_fd = os.open(source, source_flags)
    try:
        opened = os.fstat(source_fd)
        if (
            before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or before.st_size != opened.st_size
            or not stat.S_ISREG(opened.st_mode)
        ):
            raise CorpusError(f"copy source changed during open: {source}")
        destination_fd = os.open(destination, destination_flags, 0o600)
        digest = hashlib.sha256()
        copied = 0
        try:
            while True:
                chunk = os.read(source_fd, _COPY_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                copied += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise OSError("short write while preparing LVS source root")
                    view = view[written:]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)
    if copied != declaration.size_bytes or digest.hexdigest() != declaration.sha256:
        raise CorpusError(f"copy source changed while reading: {source}")


def _expected_union(
    manifest: LvsSourceManifest,
) -> dict[str, tuple[str, ArtifactDeclaration]]:
    expected: dict[str, tuple[str, ArtifactDeclaration]] = {}
    for family in manifest.families:
        for declaration in family.source_files:
            folded = declaration.path.casefold()
            previous = expected.get(folded)
            if previous is not None:
                previous_path, previous_declaration = previous
                if (
                    previous_path != declaration.path
                    or previous_declaration.size_bytes != declaration.size_bytes
                    or previous_declaration.sha256 != declaration.sha256
                ):
                    raise CorpusError(
                        f"divergent collision for prepared source path: {declaration.path}"
                    )
                continue
            expected[folded] = (declaration.path, declaration)
    return expected


def verify_prepared_lvs_source_root(
    manifest: LvsSourceManifest, source_root: Path
) -> PreparedLvsSourceRoot:
    _validate_canonical_partition()
    if not isinstance(manifest, LvsSourceManifest) or manifest.corpus_id != CORPUS_ID:
        raise TypeError("LVS source manifest is invalid")
    root = safe_existing_root(Path(source_root).absolute(), "prepared LVS source root")
    expected = _expected_union(manifest)
    actual_paths = _scan_regular_files(root, "prepared LVS source root")
    expected_paths = {item[0] for item in expected.values()}
    if actual_paths != expected_paths:
        raise CorpusError(
            "prepared LVS source file set mismatch: "
            f"expected {sorted(expected_paths)}, got {sorted(actual_paths)}"
        )
    total = 0
    for logical, declaration in expected.values():
        path = safe_join(root, logical)
        _verify_declared_file(path, declaration, "prepared LVS source")
        total += declaration.size_bytes
    return PreparedLvsSourceRoot(
        root,
        CALIBRATION_FAMILY_IDS,
        HOLDOUT_FAMILY_IDS,
        len(expected),
        total,
    )


def _require_owned_directory(
    staging: Path, path: Path, ownership: _StagingOwnership
) -> None:
    relative = path.relative_to(staging).as_posix()
    expected = ownership.root_identity if path == staging else ownership.descendants.get(relative)
    if expected is None or _owned_identity(path, directory=True) != expected:
        raise CorpusError(f"private staging directory ownership changed: {path}")


def _ensure_owned_parent_chain(
    staging: Path, parent: Path, ownership: _StagingOwnership
) -> None:
    try:
        relative = parent.relative_to(staging)
    except ValueError as exc:
        raise CorpusError("private staging destination escapes its root") from exc
    current = staging
    _require_owned_directory(staging, current, ownership)
    for part in relative.parts:
        current = current / part
        logical = current.relative_to(staging).as_posix()
        if os.path.lexists(current):
            if logical not in ownership.descendants:
                raise CorpusError(f"private staging parent ownership conflict: {current}")
            _require_owned_directory(staging, current, ownership)
            continue
        _require_owned_directory(staging, current.parent, ownership)
        current.mkdir(parents=False, exist_ok=False)
        ownership.descendants[logical] = _owned_identity(current, directory=True)
        _require_owned_directory(staging, current.parent, ownership)
        _require_owned_directory(staging, current, ownership)


def _validate_owned_staging(staging: Path, ownership: _StagingOwnership) -> bool:
    try:
        if _owned_identity(staging, directory=True) != ownership.root_identity:
            return False
        actual: dict[str, tuple[int, int, int, int]] = {}

        def scan(directory: Path) -> None:
            with os.scandir(directory) as entries:
                children = tuple(entries)
            for entry in children:
                path = Path(entry.path)
                relative = path.relative_to(staging).as_posix()
                expected = ownership.descendants.get(relative)
                if expected is None:
                    raise CorpusError("private staging contains an unowned descendant")
                identity = _owned_identity(path)
                if identity != expected:
                    raise CorpusError("private staging descendant ownership changed")
                actual[relative] = identity
                if stat.S_ISDIR(identity[3]):
                    scan(path)

        scan(staging)
        return actual == ownership.descendants
    except (CorpusError, OSError, ValueError):
        return False


def _remove_owned_staging_no_follow(
    root: Path, ownership: _StagingOwnership
) -> bool:
    if not _validate_owned_staging(root, ownership):
        return False
    try:
        for relative, expected in sorted(
            ownership.descendants.items(),
            key=lambda item: (len(PurePosixPath(item[0]).parts), item[0].casefold()),
            reverse=True,
        ):
            path = root.joinpath(*PurePosixPath(relative).parts)
            if _owned_identity(path) != expected:
                return False
            if stat.S_ISDIR(expected[3]):
                os.rmdir(path)
            else:
                path.unlink()
        if _owned_identity(root, directory=True) != ownership.root_identity:
            return False
        os.rmdir(root)
        return True
    except (CorpusError, OSError, ValueError):
        return False


def _remove_private_staging(
    staging: Path, ownership: _StagingOwnership | None
) -> bool:
    if not os.path.lexists(staging):
        return True
    if ownership is None or not _validate_owned_staging(staging, ownership):
        return False
    quarantine = staging.with_name(
        f".{staging.name}.cleanup-{secrets.token_hex(8)}"
    )
    if os.path.lexists(quarantine):
        return False
    try:
        os.rename(staging, quarantine)
    except OSError:
        return False
    if _owned_identity(quarantine, directory=True) != ownership.root_identity:
        return False
    return _remove_owned_staging_no_follow(quarantine, ownership)


def prepare_lvs_source_root(
    *,
    manifest: LvsSourceManifest,
    control_root: Path,
    output_root: Path,
) -> PreparedLvsSourceRoot:
    if not isinstance(manifest, LvsSourceManifest) or manifest.corpus_id != CORPUS_ID:
        raise TypeError("LVS source manifest is invalid")
    _validate_canonical_partition()
    control = safe_existing_root(Path(control_root).absolute(), "control root")
    output = Path(output_root).absolute()
    parent = safe_existing_root(output.parent, "prepared LVS source parent")
    if output.parent != parent:
        output = parent / output.name
    if _overlaps(output, control):
        raise CorpusError("prepared LVS output/staging cannot overlap the control root")
    family_roots = tuple(
        _family_control_source_root(control, manifest.family(family_id))
        for family_id in CANONICAL_LVS_FAMILY_IDS
    )
    if any(_overlaps(output, source_root) for source_root in family_roots):
        raise CorpusError("prepared LVS output/staging cannot overlap a family source root")
    if os.path.lexists(output):
        raise CorpusError(f"prepared LVS source root already exists: {output}")
    plan = _copy_plan(manifest, control)
    expected = _expected_union(manifest)
    if len(plan) != len(expected):
        raise CorpusError("prepared LVS source copy plan is incomplete")
    staging = parent / f".{output.name}.lvs-source-prep-{secrets.token_hex(8)}"
    if _overlaps(staging, control) or any(
        _overlaps(staging, source_root) for source_root in family_roots
    ):
        raise CorpusError("private staging cannot overlap control source roots")
    if os.path.lexists(staging):
        raise CorpusError(f"private staging already exists: {staging}")
    staging.mkdir(parents=False, exist_ok=False)
    ownership: _StagingOwnership | None = _StagingOwnership(
        _owned_identity(staging, directory=True)
    )
    published = False
    try:
        for logical, declaration, source in plan:
            destination = staging.joinpath(*PurePosixPath(logical).parts)
            _ensure_owned_parent_chain(staging, destination.parent, ownership)
            _require_owned_directory(staging, destination.parent, ownership)
            _copy_file_no_follow(source, destination, declaration)
            ownership.descendants[logical] = _owned_identity(destination, directory=False)
            _require_owned_directory(staging, destination.parent, ownership)
        if not _validate_owned_staging(staging, ownership):
            raise CorpusError("private staging ownership validation failed")
        verify_prepared_lvs_source_root(manifest, staging)
        if not _validate_owned_staging(staging, ownership):
            raise CorpusError("private staging ownership changed after validation")
        if os.path.lexists(output):
            raise CorpusError(f"prepared LVS source root appeared during publication: {output}")
        os.rename(staging, output)
        published = True
        prepared = verify_prepared_lvs_source_root(manifest, output)
    except BaseException:
        _remove_private_staging(output if published else staging, ownership)
        raise
    return prepared
