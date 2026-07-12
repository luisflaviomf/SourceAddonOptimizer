from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

from maximum_optimizer.qc_graph import QcGraph
from maximum_optimizer.round_planar_priority import classify_round_component
from maximum_optimizer.smd_contract import parse_smd_triangles


ROUND_RIGID = "round-rigid-v1"
GENERAL_BODY_DETAIL = "general-body-detail-v1"


@dataclass(frozen=True)
class SourceFidelityAudit:
    source: str
    eligible: bool
    reason: str
    axis: int | None


@dataclass(frozen=True)
class FamilyFidelitySelection:
    profile_class: str
    reason: str
    sources: tuple[SourceFidelityAudit, ...]


def _corner_influences(tokens: tuple[str, ...]) -> tuple[tuple[int, float], ...]:
    try:
        parent = int(tokens[0])
    except (IndexError, ValueError) as exc:
        raise ValueError("invalid parent bone") from exc
    if len(tokens) == 9:
        return ((parent, 1.0),)
    try:
        count = int(tokens[9])
    except (IndexError, ValueError) as exc:
        raise ValueError("invalid influence count") from exc
    if count < 0 or len(tokens) != 10 + count * 2:
        raise ValueError("invalid influence payload")
    if count == 0:
        return ((parent, 1.0),)
    result = []
    for index in range(count):
        try:
            bone = int(tokens[10 + index * 2])
            weight = float(tokens[11 + index * 2])
        except (IndexError, ValueError) as exc:
            raise ValueError("invalid influence payload") from exc
        if not math.isfinite(weight):
            raise ValueError("non-finite influence weight")
        result.append((bone, weight))
    return tuple(result)


def _round_input(path: Path):
    parsed = parse_smd_triangles(path.read_text(encoding="utf-8-sig"))
    positions = []
    influences = []
    triangles = []
    for triangle in parsed.triangles:
        indices = []
        for corner in triangle.corners:
            indices.append(len(positions))
            positions.append(corner.position)
            influences.append(_corner_influences(corner.tokens))
        triangles.append(tuple(indices))
    if not positions or not triangles:
        raise ValueError("SMD has no round-classification geometry")
    return tuple(positions), tuple(triangles), tuple(influences)


def classify_original_family(graph: QcGraph) -> FamilyFidelitySelection:
    if not isinstance(graph, QcGraph):
        raise TypeError("graph must be a QcGraph")
    root = graph.family_root.resolve(strict=True)
    unique = {reference.source_path.resolve(strict=True) for reference in graph.references
              if reference.role == "visual"}
    sources = tuple(sorted(unique, key=lambda path: path.relative_to(root).as_posix().casefold()))
    if not sources:
        return FamilyFidelitySelection(GENERAL_BODY_DETAIL, "no-visual-sources", ())

    audits = []
    for source in sources:
        try:
            relative = source.relative_to(root).as_posix()
        except ValueError:
            relative = source.name
            audit = SourceFidelityAudit(relative, False, "source-outside-family", None)
        else:
            if source.suffix.casefold() != ".smd":
                audit = SourceFidelityAudit(
                    relative, False, f"unsupported-source-format:{source.suffix.casefold()}", None
                )
            else:
                try:
                    decision = classify_round_component(*_round_input(source))
                except (OSError, UnicodeError, ValueError) as exc:
                    audit = SourceFidelityAudit(
                        relative, False, f"invalid-smd:{type(exc).__name__}", None
                    )
                else:
                    audit = SourceFidelityAudit(
                        relative, decision.eligible, decision.reason, decision.axis
                    )
        audits.append(audit)

    rows = tuple(audits)
    failure = next((item for item in rows if not item.eligible), None)
    if failure is not None:
        return FamilyFidelitySelection(
            GENERAL_BODY_DETAIL, f"{failure.source}:{failure.reason}", rows
        )
    return FamilyFidelitySelection(ROUND_RIGID, "all-visual-sources-round-rigid", rows)
