from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping

from .companion_contract import normalize_virtual_path
from .qc_graph import QcOccurrence, scan_qc_file_occurrences, tokenize_qc_line


@dataclass(frozen=True)
class QcRuntimeBinding:
    model_path: str | None
    bodygroup: tuple[str, int] | None
    skin_rows: tuple[tuple[str, ...], ...]
    material_directories: tuple[PurePosixPath, ...]


@dataclass(frozen=True)
class QcRuntimeBindings:
    by_occurrence: Mapping[QcOccurrence, QcRuntimeBinding]


def _model_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    if not normalized.casefold().startswith("models/"):
        normalized = "models/" + normalized
    if not normalized.casefold().endswith(".mdl"):
        normalized += ".mdl"
    result = normalize_virtual_path(normalized)
    if not result.endswith(".mdl"):
        raise ValueError("QC model path must identify an MDL")
    return result


def _bodygroup_choices(lines: list[str]) -> dict[int, tuple[str, int]]:
    choices: dict[int, tuple[str, int]] = {}
    pending: tuple[str, int] | None = None
    active: tuple[str, int] | None = None
    choice_index = 0
    brace_depth = 0
    for line_number, raw in enumerate(lines, start=1):
        values = tokenize_qc_line(raw)
        folded = values[0].casefold() if values else ""
        if folded == "$bodygroup":
            pending = (values[1], brace_depth) if len(values) >= 2 else None
            active = None
            choice_index = 0
        if pending is not None and "{" in raw:
            active = pending
            pending = None
            choice_index = 0
        elif pending is not None and values and folded != "$bodygroup":
            pending = None

        if active is not None and brace_depth > active[1]:
            if folded == "studio" and len(values) >= 2:
                choices[line_number] = (active[0], choice_index)
                choice_index += 1
            elif folded == "blank":
                choice_index += 1

        brace_depth += raw.count("{") - raw.count("}")
        if active is not None and brace_depth <= active[1] and "}" in raw:
            active = None
    return choices


def _texturegroup_rows(lines: list[str]) -> tuple[tuple[str, ...], ...]:
    groups: list[tuple[tuple[str, ...], ...]] = []
    pending_depth: int | None = None
    active_depth: int | None = None
    rows: list[tuple[str, ...]] = []
    brace_depth = 0
    for raw in lines:
        values = tokenize_qc_line(raw)
        folded = values[0].casefold() if values else ""
        if folded == "$texturegroup":
            pending_depth = brace_depth if len(values) >= 2 else None
            active_depth = None
            rows = []
        if pending_depth is not None and "{" in raw:
            active_depth = pending_depth
            pending_depth = None
        elif pending_depth is not None and values and folded != "$texturegroup":
            pending_depth = None

        if active_depth is not None and brace_depth > active_depth and "{" in raw and "}" in raw:
            row = tuple(
                value.replace("\\", "/").casefold()
                for value in values
                if value not in {"{", "}"}
            )
            if row:
                rows.append(row)

        brace_depth += raw.count("{") - raw.count("}")
        if active_depth is not None and brace_depth <= active_depth and "}" in raw:
            if rows and len({len(row) for row in rows}) == 1:
                groups.append(tuple(rows))
            active_depth = None
    return groups[0] if len(groups) == 1 else ()


def parse_qc_runtime_bindings(root: Path) -> QcRuntimeBindings:
    source_root = Path(root).resolve()
    bindings: dict[QcOccurrence, QcRuntimeBinding] = {}
    for qc_path in sorted(source_root.rglob("*.qc"), key=lambda path: path.as_posix().casefold()):
        try:
            lines = qc_path.read_text(encoding="utf-8", errors="strict").splitlines()
            occurrences = scan_qc_file_occurrences(source_root, qc_path)
        except (OSError, UnicodeError, ValueError):
            continue

        model_paths: set[str] = set()
        modelname_count = 0
        for raw in lines:
            values = tokenize_qc_line(raw)
            if not values or values[0].casefold() != "$modelname":
                continue
            modelname_count += 1
            if len(values) < 2:
                continue
            try:
                model_paths.add(_model_path(values[1]))
            except ValueError:
                pass
        model_path = next(iter(model_paths)) if modelname_count == 1 and len(model_paths) == 1 else None
        choices = _bodygroup_choices(lines)
        skin_rows = _texturegroup_rows(lines)
        for occurrence in occurrences:
            bodygroup = choices.get(occurrence.line) if occurrence.directive == "$bodygroup/studio" else None
            bindings[occurrence] = QcRuntimeBinding(
                model_path=model_path,
                bodygroup=bodygroup,
                skin_rows=skin_rows,
                material_directories=occurrence.material_directories,
            )
    return QcRuntimeBindings(MappingProxyType(dict(sorted(bindings.items()))))
