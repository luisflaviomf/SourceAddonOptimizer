from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re


_TOKEN = re.compile(r'"([^"\r\n]*)"|(\S+)')


@dataclass(frozen=True, order=True)
class QcOccurrence:
    qc_path: PurePosixPath
    directive: str
    line: int
    source_path: PurePosixPath
    material_directories: tuple[PurePosixPath, ...] = ()


def _tokens(line: str) -> tuple[str, ...]:
    content = line.split("//", 1)[0]
    return tuple(match.group(1) if match.group(1) is not None else match.group(2) for match in _TOKEN.finditer(content))


def _source_path(root: Path, qc_path: Path, token: str) -> PurePosixPath:
    normalized = token.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.suffix.casefold() not in {".smd", ".dmx"}:
        path = path.with_suffix(".smd")
    candidate = (qc_path.parent / Path(*path.parts)).resolve()
    try:
        relative = candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"QC source escapes inventory root: {token}") from exc
    return PurePosixPath(relative.as_posix())


def scan_qc_occurrences(root: Path) -> tuple[QcOccurrence, ...]:
    source_root = Path(root).resolve()
    occurrences: list[QcOccurrence] = []
    for qc_path in sorted(source_root.rglob("*.qc"), key=lambda path: path.as_posix().casefold()):
        relative_qc = PurePosixPath(qc_path.relative_to(source_root).as_posix())
        lines = qc_path.read_text(encoding="utf-8", errors="strict").splitlines()
        material_directories: list[PurePosixPath] = []
        for raw in lines:
            values = _tokens(raw)
            if values and values[0].casefold() == "$cdmaterials" and len(values) >= 2:
                normalized = values[1].replace("\\", "/").strip("/")
                path = PurePosixPath(normalized)
                if normalized and not path.is_absolute() and ".." not in path.parts and path not in material_directories:
                    material_directories.append(path)
        bodygroup_depth: int | None = None
        brace_depth = 0
        for line_number, raw in enumerate(lines, start=1):
            values = _tokens(raw)
            folded = values[0].casefold() if values else ""
            if folded == "$bodygroup":
                bodygroup_depth = brace_depth
            if folded in {"$body", "$model"} and len(values) >= 3:
                occurrences.append(
                    QcOccurrence(
                        relative_qc,
                        folded,
                        line_number,
                        _source_path(source_root, qc_path, values[2]),
                        tuple(material_directories),
                    )
                )
            elif bodygroup_depth is not None and folded == "studio" and len(values) >= 2:
                occurrences.append(
                    QcOccurrence(
                        relative_qc,
                        "$bodygroup/studio",
                        line_number,
                        _source_path(source_root, qc_path, values[1]),
                        tuple(material_directories),
                    )
                )
            elif folded == "replacemodel" and len(values) >= 3:
                occurrences.append(
                    QcOccurrence(
                        relative_qc,
                        "$lod/replacemodel",
                        line_number,
                        _source_path(source_root, qc_path, values[2]),
                        tuple(material_directories),
                    )
                )
            brace_depth += raw.count("{") - raw.count("}")
            if bodygroup_depth is not None and brace_depth <= bodygroup_depth and "}" in raw:
                bodygroup_depth = None
    return tuple(sorted(occurrences))
