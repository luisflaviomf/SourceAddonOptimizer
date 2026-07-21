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
        bodygroup_depth: int | None = None
        brace_depth = 0
        for line_number, raw in enumerate(qc_path.read_text(encoding="utf-8", errors="strict").splitlines(), start=1):
            values = _tokens(raw)
            folded = values[0].casefold() if values else ""
            if folded == "$bodygroup":
                bodygroup_depth = brace_depth
            if folded in {"$body", "$model"} and len(values) >= 3:
                occurrences.append(
                    QcOccurrence(relative_qc, folded, line_number, _source_path(source_root, qc_path, values[2]))
                )
            elif bodygroup_depth is not None and folded == "studio" and len(values) >= 2:
                occurrences.append(
                    QcOccurrence(relative_qc, "$bodygroup/studio", line_number, _source_path(source_root, qc_path, values[1]))
                )
            elif folded == "replacemodel" and len(values) >= 3:
                occurrences.append(
                    QcOccurrence(relative_qc, "$lod/replacemodel", line_number, _source_path(source_root, qc_path, values[2]))
                )
            brace_depth += raw.count("{") - raw.count("}")
            if bodygroup_depth is not None and brace_depth <= bodygroup_depth and "}" in raw:
                bodygroup_depth = None
    return tuple(sorted(occurrences))
