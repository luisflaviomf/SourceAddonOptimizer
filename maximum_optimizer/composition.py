from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from types import MappingProxyType

from .regions import SmdRegion
from .smd import SmdDocument, parse_smd, serialize_smd


@dataclass(frozen=True)
class RegionReplacement:
    source_path: PurePosixPath
    original: SmdRegion
    selected: SmdRegion

    def __post_init__(self) -> None:
        path = PurePosixPath(self.source_path)
        if path.is_absolute() or ".." in path.parts or path.suffix.casefold() != ".smd":
            raise ValueError("replacement source must be a relative SMD path")
        if self.original.material.casefold() != self.selected.material.casefold():
            raise ValueError("regional replacement must preserve material identity")
        object.__setattr__(self, "source_path", path)


@dataclass(frozen=True)
class CompositionResult:
    root: Path
    sources: Mapping[PurePosixPath, Path]
    replaced_regions: int


@dataclass(frozen=True)
class IsolationResult:
    reverted_sources: tuple[str, ...]
    compile_count: int


def _compose_document(document: SmdDocument, replacements: tuple[RegionReplacement, ...]) -> SmdDocument:
    insertion_by_ordinal: dict[int, tuple] = {}
    covered: set[int] = set()
    for replacement in replacements:
        ordinals = replacement.original.triangle_ordinals
        if not ordinals or any(ordinal < 0 or ordinal >= len(document.triangles) for ordinal in ordinals):
            raise ValueError("replacement triangle ordinal is outside the source")
        if covered.intersection(ordinals):
            raise ValueError("regional replacements overlap")
        actual = tuple(document.triangles[ordinal] for ordinal in ordinals)
        if actual != replacement.original.triangles:
            raise ValueError("replacement was derived from a different source revision")
        covered.update(ordinals)
        insertion_by_ordinal[min(ordinals)] = replacement.selected.triangles

    triangles = []
    for ordinal, triangle in enumerate(document.triangles):
        selected = insertion_by_ordinal.get(ordinal)
        if selected is not None:
            triangles.extend(selected)
        if ordinal not in covered:
            triangles.append(triangle)
    if not triangles:
        raise ValueError("composition removed all source triangles")
    return SmdDocument(document.header_lines, tuple(triangles))


def compose_source_tree(
    original_tree: Path,
    normal_tree: Path,
    replacements: tuple[RegionReplacement, ...],
    output_tree: Path,
    *,
    normal_source_fallbacks: tuple[PurePosixPath, ...] = (),
) -> CompositionResult:
    original_root = Path(original_tree).resolve()
    normal_root = Path(normal_tree).resolve()
    output_root = Path(output_tree).resolve()
    if not original_root.is_dir() or not normal_root.is_dir():
        raise FileNotFoundError("original and normal source trees are required")
    if output_root.exists():
        raise FileExistsError(output_root)
    output_root.parent.mkdir(parents=True, exist_ok=True)

    by_source: dict[PurePosixPath, list[RegionReplacement]] = {}
    for replacement in replacements:
        by_source.setdefault(replacement.source_path, []).append(replacement)
    fallback_sources = tuple(sorted(set(normal_source_fallbacks), key=lambda path: path.as_posix().casefold()))
    if any(path in by_source for path in fallback_sources):
        raise ValueError("a source cannot have regional and whole-source replacements")
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_root.name}.", dir=output_root.parent)).resolve()
    try:
        shutil.copytree(original_root, temporary, dirs_exist_ok=True)
        sources: dict[PurePosixPath, Path] = {}
        for relative in fallback_sources:
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("normal fallback source must be relative")
            normal_source = normal_root / Path(*relative.parts)
            if not normal_source.is_file():
                raise FileNotFoundError(relative.as_posix())
            destination = temporary / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(normal_source, destination)
            sources[relative] = output_root / Path(*relative.parts)
        for relative, source_replacements in sorted(by_source.items(), key=lambda item: item[0].as_posix().casefold()):
            original_source = original_root / Path(*relative.parts)
            normal_source = normal_root / Path(*relative.parts)
            if not original_source.is_file() or not normal_source.is_file():
                raise FileNotFoundError(relative.as_posix())
            document = parse_smd(original_source.read_text(encoding="utf-8", errors="strict"))
            composed = _compose_document(document, tuple(source_replacements))
            destination = temporary / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(serialize_smd(composed), encoding="utf-8", newline="\n")
            sources[relative] = output_root / Path(*relative.parts)
        os.replace(temporary, output_root)
        return CompositionResult(output_root, MappingProxyType(sources), len(replacements))
    except Exception:
        if temporary.exists() and temporary.parent == output_root.parent:
            shutil.rmtree(temporary)
        raise


def isolate_compile_failure(
    changed_sources: tuple[str, ...],
    compile_active: Callable[[tuple[str, ...]], bool],
) -> IsolationResult:
    remaining = tuple(sorted(set(changed_sources), key=str.casefold))
    if not remaining:
        return IsolationResult((), 0)
    compiles = 0
    while len(remaining) > 1:
        midpoint = len(remaining) // 2
        left = remaining[:midpoint]
        right = remaining[midpoint:]
        passed = compile_active(left)
        if type(passed) is not bool:
            raise TypeError("compile callback must return bool")
        compiles += 1
        remaining = right if passed else left
    passed = compile_active(remaining)
    if type(passed) is not bool:
        raise TypeError("compile callback must return bool")
    compiles += 1
    if passed:
        raise RuntimeError("compile failure could not be isolated to one source")
    return IsolationResult(remaining, compiles)
