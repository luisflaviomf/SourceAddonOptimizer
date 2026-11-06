from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True, order=True)
class SmdInfluence:
    bone: int
    weight: float


@dataclass(frozen=True)
class SmdVertex:
    primary_bone: int
    position: tuple[float, float, float]
    normal: tuple[float, float, float]
    uv: tuple[float, float]
    influences: tuple[SmdInfluence, ...]


@dataclass(frozen=True)
class SmdTriangle:
    material: str
    vertices: tuple[SmdVertex, SmdVertex, SmdVertex]
    source_ordinal: int


@dataclass(frozen=True)
class SmdDocument:
    header_lines: tuple[str, ...]
    triangles: tuple[SmdTriangle, ...]


def _finite_floats(tokens: list[str], indices: Iterable[int], label: str) -> tuple[float, ...]:
    try:
        values = tuple(float(tokens[index]) for index in indices)
    except (IndexError, ValueError) as exc:
        raise ValueError(f"SMD {label} is invalid") from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"SMD {label} contains a non-finite value")
    return values


def _parse_vertex(raw: str) -> SmdVertex:
    tokens = raw.split()
    if len(tokens) < 9:
        raise ValueError("SMD triangle vertex is truncated")
    try:
        primary_bone = int(tokens[0])
    except ValueError as exc:
        raise ValueError("SMD primary bone is invalid") from exc
    position = _finite_floats(tokens, (1, 2, 3), "position")
    normal = _finite_floats(tokens, (4, 5, 6), "normal")
    uv = _finite_floats(tokens, (7, 8), "UV")
    influences: list[SmdInfluence] = []
    if len(tokens) == 9:
        influences.append(SmdInfluence(primary_bone, 1.0))
    else:
        try:
            count = int(tokens[9])
        except ValueError as exc:
            raise ValueError("SMD influence count is invalid") from exc
        if count < 0 or len(tokens) != 10 + count * 2:
            raise ValueError("SMD influence list is truncated")
        if count == 0:
            influences.append(SmdInfluence(primary_bone, 1.0))
        for index in range(count):
            try:
                bone = int(tokens[10 + index * 2])
                weight = float(tokens[11 + index * 2])
            except ValueError as exc:
                raise ValueError("SMD influence is invalid") from exc
            if bone < 0 or not math.isfinite(weight) or weight < 0.0:
                raise ValueError("SMD influence is invalid")
            influences.append(SmdInfluence(bone, weight))
    if not influences or sum(value.weight for value in influences) <= 1e-12:
        raise ValueError("SMD vertex has no positive skin weight")
    return SmdVertex(
        primary_bone,
        position,  # type: ignore[arg-type]
        normal,  # type: ignore[arg-type]
        uv,  # type: ignore[arg-type]
        tuple(influences),
    )


def parse_smd(text: str) -> SmdDocument:
    if type(text) is not str:
        raise TypeError("SMD text must be a string")
    lines = text.splitlines()
    try:
        triangles_line = next(index for index, line in enumerate(lines) if line.strip().casefold() == "triangles")
    except StopIteration as exc:
        raise ValueError("SMD has no triangles section") from exc
    header = tuple(lines[:triangles_line])
    triangles: list[SmdTriangle] = []
    index = triangles_line + 1
    while index < len(lines):
        material = lines[index].strip()
        if material.casefold() == "end":
            if not triangles:
                raise ValueError("SMD triangles section is empty")
            if any(line.strip() for line in lines[index + 1 :]):
                raise ValueError("SMD has content after triangles end")
            return SmdDocument(header, tuple(triangles))
        if not material:
            raise ValueError("SMD triangle material is empty")
        if index + 3 >= len(lines):
            raise ValueError("SMD triangles section ended mid-triangle")
        vertex_lines = lines[index + 1 : index + 4]
        if any(line.strip().casefold() == "end" for line in vertex_lines):
            raise ValueError("SMD triangles section ended mid-triangle")
        vertices = tuple(_parse_vertex(line) for line in vertex_lines)
        triangles.append(
            SmdTriangle(material, vertices, len(triangles))  # type: ignore[arg-type]
        )
        index += 4
    raise ValueError("SMD triangles section ended without end marker")


def _source_influences(vertex: SmdVertex) -> tuple[SmdInfluence, ...]:
    combined: dict[int, float] = {}
    for influence in vertex.influences:
        if influence.weight > 0.0:
            combined[influence.bone] = combined.get(influence.bone, 0.0) + influence.weight
    selected = sorted(combined.items(), key=lambda item: (-item[1], item[0]))[:3]
    total = sum(weight for _bone, weight in selected)
    if total <= 1e-12:
        raise ValueError("SMD vertex has no exportable skin weight")
    return tuple(SmdInfluence(bone, weight / total) for bone, weight in selected)


def _number(value: float) -> str:
    if value == 0.0:
        return "0"
    return format(float(value), ".9g")


def _serialize_vertex(vertex: SmdVertex) -> str:
    values = [
        str(vertex.primary_bone),
        *(_number(value) for value in vertex.position),
        *(_number(value) for value in vertex.normal),
        *(_number(value) for value in vertex.uv),
    ]
    influences = _source_influences(vertex)
    if len(influences) == 1 and influences[0].bone == vertex.primary_bone and abs(influences[0].weight - 1.0) <= 1e-9:
        return " ".join(values)
    values.append(str(len(influences)))
    for influence in influences:
        values.extend((str(influence.bone), _number(influence.weight)))
    return " ".join(values)


def serialize_smd(document: SmdDocument) -> str:
    lines = [*document.header_lines, "triangles"]
    for triangle in document.triangles:
        lines.append(triangle.material)
        lines.extend(_serialize_vertex(vertex) for vertex in triangle.vertices)
    lines.append("end")
    return "\n".join(lines) + "\n"
