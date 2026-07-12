from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Sequence


POSITION_SERIALIZATION_TOLERANCE = 2e-6


@dataclass(frozen=True)
class SmdCorner:
    line_index: int
    tokens: tuple[str, ...]
    spans: tuple[tuple[int, int], ...]
    position: tuple[float, float, float]
    normal: tuple[float, float, float]
    uv: tuple[float, float]


@dataclass(frozen=True)
class SmdTriangle:
    material: str
    corners: tuple[SmdCorner, SmdCorner, SmdCorner]


@dataclass(frozen=True)
class ParsedSmd:
    lines: tuple[str, ...]
    triangles: tuple[SmdTriangle, ...]


def _corner(line_index: int, raw: str) -> SmdCorner:
    matches = tuple(re.finditer(r"\S+", raw))
    tokens = tuple(match.group(0) for match in matches)
    if len(tokens) < 9 or not tokens[0].lstrip("-").isdigit():
        raise ValueError(f"SMD corner line {line_index + 1} is invalid")
    try:
        position = tuple(float(tokens[index]) for index in (1, 2, 3))
        normal = tuple(float(tokens[index]) for index in (4, 5, 6))
        uv = tuple(float(tokens[index]) for index in (7, 8))
    except ValueError as exc:
        raise ValueError(f"SMD corner line {line_index + 1} contains non-numeric payload") from exc
    if not all(math.isfinite(value) for value in (*position, *normal, *uv)):
        raise ValueError(f"SMD corner line {line_index + 1} contains non-finite payload")
    if len(tokens) > 9:
        try:
            links = int(tokens[9])
        except ValueError as exc:
            raise ValueError(f"SMD corner line {line_index + 1} has invalid links") from exc
        if links < 0 or len(tokens) != 10 + links * 2:
            raise ValueError(f"SMD corner line {line_index + 1} has truncated links")
    return SmdCorner(
        line_index,
        tokens,
        tuple((match.start(), match.end()) for match in matches),
        position,  # type: ignore[arg-type]
        normal,  # type: ignore[arg-type]
        uv,  # type: ignore[arg-type]
    )


def parse_smd_triangles(text: str) -> ParsedSmd:
    lines = tuple(text.splitlines(keepends=True))
    section = False
    expect_material = False
    material = ""
    corners: list[SmdCorner] = []
    triangles: list[SmdTriangle] = []
    for index, raw in enumerate(lines):
        line = raw.strip()
        folded = line.casefold()
        if not section:
            if folded == "triangles":
                section, expect_material = True, True
            continue
        if folded == "end":
            if corners:
                raise ValueError("SMD triangles section ended mid-triangle")
            break
        if expect_material:
            if not line:
                raise ValueError("SMD triangle material is empty")
            material, expect_material = line, False
            continue
        corners.append(_corner(index, raw))
        if len(corners) == 3:
            triangles.append(SmdTriangle(material, tuple(corners)))  # type: ignore[arg-type]
            corners, expect_material = [], True
    if not section or not triangles:
        raise ValueError("SMD has no triangle records")
    return ParsedSmd(lines, tuple(triangles))


def _numeric_token_equal(first: str, second: str) -> bool:
    try:
        return float(first) == float(second)
    except ValueError:
        return first == second


def validate_fixed_topology_smd(
    original_text: str,
    exported_text: str,
    *,
    position_tolerance: float = POSITION_SERIALIZATION_TOLERANCE,
) -> tuple[ParsedSmd, ParsedSmd]:
    """Require exact ordered triangles and every non-normal corner value.

    Position components alone allow the documented Source Tools float32 last-place
    serialization equivalence. Materials, UVs, bone ids, weights, link order and all
    other non-normal values must remain numerically identical at the same ordinal.
    """
    if not math.isfinite(position_tolerance) or position_tolerance < 0.0:
        raise ValueError("position tolerance must be finite and non-negative")
    original = parse_smd_triangles(original_text)
    exported = parse_smd_triangles(exported_text)
    if len(original.triangles) != len(exported.triangles):
        raise RuntimeError("fixed-topology SMD triangle count changed")
    for triangle_index, (before, after) in enumerate(zip(original.triangles, exported.triangles)):
        if before.material != after.material:
            raise RuntimeError(f"fixed-topology SMD triangle order/material changed at {triangle_index}")
        for corner_index, (source, candidate) in enumerate(zip(before.corners, after.corners)):
            ordinal = f"triangle {triangle_index} corner {corner_index}"
            if len(source.tokens) != len(candidate.tokens):
                raise RuntimeError(f"fixed-topology SMD corner payload width changed at {ordinal}")
            if source.tokens[0] != candidate.tokens[0]:
                raise RuntimeError(f"fixed-topology SMD bone id changed at {ordinal}")
            if any(abs(a - b) > position_tolerance for a, b in zip(source.position, candidate.position)):
                raise RuntimeError(f"fixed-topology SMD position changed beyond serialization equivalence at {ordinal}")
            if source.uv != candidate.uv:
                raise RuntimeError(f"fixed-topology SMD UV changed at {ordinal}")
            for token_index in range(7, len(source.tokens)):
                if not _numeric_token_equal(source.tokens[token_index], candidate.tokens[token_index]):
                    label = "weight/bone payload" if token_index >= 9 else "UV payload"
                    raise RuntimeError(f"fixed-topology SMD {label} changed at {ordinal}")
    return original, exported


def restore_ordered_smd_normals(
    original_text: str,
    exported_text: str,
    *,
    max_distance: float = 2e-2,
    position_tolerance: float = POSITION_SERIALIZATION_TOLERANCE,
) -> str:
    if not math.isfinite(max_distance) or max_distance <= 0.0:
        raise ValueError("normal restore tolerance must be finite and positive")
    original, exported = validate_fixed_topology_smd(
        original_text, exported_text, position_tolerance=position_tolerance
    )
    output = list(exported.lines)
    for triangle_index, (before, after) in enumerate(zip(original.triangles, exported.triangles)):
        for corner_index, (source, candidate) in enumerate(zip(before.corners, after.corners)):
            distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(source.normal, candidate.normal)))
            if distance > max_distance:
                raise RuntimeError(
                    "exported SMD corresponding corner normal is outside original identity tolerance: "
                    f"triangle={triangle_index} corner={corner_index} distance={distance:.9g} "
                    f"limit={max_distance:.9g}"
                )
            raw = output[candidate.line_index]
            for token_index in (6, 5, 4):
                start, end = candidate.spans[token_index]
                raw = raw[:start] + source.tokens[token_index] + raw[end:]
            output[candidate.line_index] = raw
    restored = "".join(output)
    restored_original, restored_candidate = validate_fixed_topology_smd(
        original_text, restored, position_tolerance=position_tolerance
    )
    intended = tuple(
        corner.normal for triangle in restored_original.triangles for corner in triangle.corners
    )
    actual = tuple(
        corner.normal for triangle in restored_candidate.triangles for corner in triangle.corners
    )
    if actual != intended:
        raise RuntimeError("restored SMD normal ordered correspondence differs from original")
    return restored


def restore_direct_smd_normals(
    original_text: str, exported_text: str, source_corner_ordinals: Sequence[int],
    *, position_tolerance: float = POSITION_SERIALIZATION_TOLERANCE,
) -> str:
    """Restore normals through explicit no-update wedge provenance after topology change."""
    original = parse_smd_triangles(original_text)
    exported = parse_smd_triangles(exported_text)
    source_corners = tuple(c for tri in original.triangles for c in tri.corners)
    if len(source_corner_ordinals) != len(exported.triangles) * 3:
        raise RuntimeError("direct provenance corner count differs from export")
    expected = []
    for offset in range(0, len(source_corner_ordinals), 3):
        ordinals = tuple(source_corner_ordinals[offset:offset + 3])
        if len(set(ordinals)) != 3 or any(i < 0 or i >= len(source_corners) for i in ordinals):
            raise RuntimeError("direct provenance contains invalid or duplicate corners")
        material = original.triangles[ordinals[0] // 3].material
        if any(original.triangles[i // 3].material != material for i in ordinals):
            raise RuntimeError("direct provenance crosses material ownership")
        expected.append((material, tuple(source_corners[i] for i in ordinals)))
    by_material_expected: dict[str, list[tuple[SmdCorner, ...]]] = {}
    by_material_exported: dict[str, list[SmdTriangle]] = {}
    for material, corners in expected:
        by_material_expected.setdefault(material, []).append(corners)
    for triangle in exported.triangles:
        by_material_exported.setdefault(triangle.material, []).append(triangle)
    if set(by_material_expected) != set(by_material_exported):
        raise RuntimeError("direct provenance material set differs from export")
    output = list(exported.lines)
    used_lines: set[int] = set()
    for material in sorted(by_material_expected):
        before_rows, after_rows = by_material_expected[material], by_material_exported[material]
        if len(before_rows) != len(after_rows):
            raise RuntimeError("direct provenance material triangle count differs from export")
        for before, after in zip(before_rows, after_rows):
            for source, candidate in zip(before, after.corners):
                if candidate.line_index in used_lines:
                    raise RuntimeError("direct provenance is not bijective")
                used_lines.add(candidate.line_index)
                if source.tokens[0] != candidate.tokens[0] or source.uv != candidate.uv:
                    raise RuntimeError("direct provenance bone or UV payload differs")
                if any(abs(a-b) > position_tolerance for a,b in zip(source.position, candidate.position)):
                    raise RuntimeError("direct provenance position differs")
                if len(source.tokens) != len(candidate.tokens) or any(
                    not _numeric_token_equal(source.tokens[i], candidate.tokens[i])
                    for i in range(9, len(source.tokens))
                ):
                    raise RuntimeError("direct provenance weight payload differs")
                raw = output[candidate.line_index]
                for token_index in (6, 5, 4):
                    start, end = candidate.spans[token_index]
                    raw = raw[:start] + source.tokens[token_index] + raw[end:]
                output[candidate.line_index] = raw
    if len(used_lines) != len(source_corner_ordinals):
        raise RuntimeError("direct provenance is not a complete bijection")
    return "".join(output)
