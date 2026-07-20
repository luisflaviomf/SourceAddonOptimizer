from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import struct
from typing import Sequence


POSITION_SERIALIZATION_TOLERANCE = 2e-6
# Source Tools recalculates smoothing on import. Accept at most one degree of normalized
# direction drift (chord length 2*sin(0.5 degree)); exact position/UV/skin and the
# ambiguity gate still identify the source corner.
IMPORT_NORMAL_EQUIVALENCE_TOLERANCE = 2.0 * math.sin(math.radians(0.5))
# Normal is used only to distinguish source rows that already have identical material,
# position, UV and skin. Reject an opposite-hemisphere association and near ties; the
# serializer always copies the exact source normal and never emits the imported value.
IMPORT_NORMAL_DISAMBIGUATION_CEILING = 2.0 * math.sin(math.radians(7.5))
# Blender exposes imported normals as float32. Require a winner by more than eight
# float32 ULPs at unit magnitude so importer rounding cannot reverse the choice,
# without rejecting distinct source normals that happen to be visually near-identical.
IMPORT_NORMAL_DISAMBIGUATION_MARGIN = 8.0 * (2.0 ** -23)


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


@dataclass(frozen=True)
class DirectDegeneratePrefilter:
    filtered_text: str
    dropped_source_triangles: tuple[int, ...]
    evidence: dict[str, object]


def _corner(
    line_index: int, raw: str, *, allow_invalid_normals: bool = False
) -> SmdCorner:
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
    if (
        not all(math.isfinite(value) for value in (*position, *uv))
        or (
            not allow_invalid_normals
            and not all(math.isfinite(value) for value in normal)
        )
    ):
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


def parse_smd_triangles(
    text: str, *, allow_invalid_normals: bool = False
) -> ParsedSmd:
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
        corners.append(
            _corner(index, raw, allow_invalid_normals=allow_invalid_normals)
        )
        if len(corners) == 3:
            triangles.append(SmdTriangle(material, tuple(corners)))  # type: ignore[arg-type]
            corners, expect_material = [], True
    if not section or not triangles:
        raise ValueError("SMD has no triangle records")
    return ParsedSmd(lines, tuple(triangles))


def direct_smd_material_counts(text: str) -> tuple[tuple[str, int], ...]:
    """Parse one complete SMD and return first-occurrence material order/counts."""
    parsed = parse_smd_triangles(text)
    last_corner = parsed.triangles[-1].corners[-1].line_index
    tail = parsed.lines[last_corner + 1:]
    if not tail or tail[0].strip().casefold() != "end" or any(line.strip() for line in tail[1:]):
        raise ValueError("direct SMD has trailing or malformed content after triangles end")
    order = tuple(dict.fromkeys(item.material for item in parsed.triangles))
    return tuple(
        (material, sum(item.material == material for item in parsed.triangles))
        for material in order
    )


def match_direct_output_triangle_ordinals(
    filtered_text: str, output_text: str,
) -> tuple[int, ...]:
    """Map each exact retained output triangle to its post-prefilter source ordinal."""
    source = parse_smd_triangles(filtered_text)
    output = parse_smd_triangles(output_text)
    direct_smd_material_counts(filtered_text)
    direct_smd_material_counts(output_text)
    source_cycles: dict[str, dict[tuple[tuple[str, ...], ...], int]] = {}
    for ordinal, triangle in enumerate(source.triangles):
        tokens = tuple(corner.tokens for corner in triangle.corners)
        values = source_cycles.setdefault(triangle.material, {})
        for offset in range(3):
            cycle = tokens[offset:] + tokens[:offset]
            if cycle in values:
                raise RuntimeError("direct source triangle cycle provenance is ambiguous")
            values[cycle] = ordinal
    result: list[int] = []
    used: set[int] = set()
    for triangle in output.triangles:
        tokens = tuple(corner.tokens for corner in triangle.corners)
        source_ordinal = source_cycles.get(triangle.material, {}).get(tokens)
        if source_ordinal is None:
            raise RuntimeError("direct output changed retained corner cycle or winding")
        if source_ordinal in used:
            raise RuntimeError("direct output duplicated a retained source triangle")
        used.add(source_ordinal)
        result.append(source_ordinal)
    return tuple(result)


def direct_smd_input_material_inventory(
    text: str,
) -> tuple[tuple[str, int, str], ...]:
    """Return post-prefilter material order, counts, and exact record-stream hashes."""
    parsed = parse_smd_triangles(text)
    direct_smd_material_counts(text)  # also enforces exact EOF
    order = tuple(dict.fromkeys(item.material for item in parsed.triangles))
    records: dict[str, list[bytes]] = {material: [] for material in order}
    for triangle in parsed.triangles:
        start = triangle.corners[0].line_index - 1
        end = triangle.corners[-1].line_index
        records[triangle.material].append(
            "".join(parsed.lines[start:end + 1]).encode("utf-8")
        )
    return tuple(
        (
            material, len(records[material]),
            hashlib.sha256(b"".join(records[material])).hexdigest(),
        )
        for material in order
    )


def prefilter_direct_degenerate_smd(
    text: str, *, cross_squared_threshold: float = 1e-30
) -> DirectDegeneratePrefilter:
    if (
        isinstance(cross_squared_threshold, bool)
        or not isinstance(cross_squared_threshold, (int, float))
        or not math.isfinite(float(cross_squared_threshold))
        or float(cross_squared_threshold) != 1e-30
    ):
        raise ValueError("direct degenerate threshold must be exactly 1e-30")
    parsed = parse_smd_triangles(text, allow_invalid_normals=True)
    node_names: dict[int, str] = {}
    in_nodes = False
    for raw in parsed.lines:
        folded = raw.strip().casefold()
        if folded == "nodes":
            in_nodes = True
            continue
        if in_nodes and folded == "end":
            break
        if in_nodes:
            match = re.match(r'^\s*(-?\d+)\s+"([^"]*)"\s+-?\d+', raw)
            if match:
                node_names[int(match.group(1))] = match.group(2)

    dropped: list[int] = []
    removed_lines: set[int] = set()
    records: list[dict[str, object]] = []
    for ordinal, triangle in enumerate(parsed.triangles):
        a, b, c = (corner.position for corner in triangle.corners)
        ab = tuple(b[axis] - a[axis] for axis in range(3))
        ac = tuple(c[axis] - a[axis] for axis in range(3))
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        cross_squared = sum(value * value for value in cross)
        invalid_normals = tuple(
            corner for corner in triangle.corners
            if (
                not all(math.isfinite(value) for value in corner.normal)
                or sum(value * value for value in corner.normal) <= 1e-24
            )
        )
        if cross_squared <= float(cross_squared_threshold):
            start = triangle.corners[0].line_index - 1
            end = triangle.corners[-1].line_index
            removed_lines.update(range(start, end + 1))
            raw_record = "".join(parsed.lines[start : end + 1]).encode("utf-8")
            dropped.append(ordinal)
            records.append({
                "ordinal": ordinal,
                "material": triangle.material,
                "primary_bones": sorted({
                    node_names.get(int(corner.tokens[0]), corner.tokens[0])
                    for corner in triangle.corners
                }, key=lambda value: (value.casefold(), value)),
                "reason": "cross-squared-at-most-1e-30",
                "source_sha256": hashlib.sha256(raw_record).hexdigest(),
            })
        elif invalid_normals:
            raise ValueError(
                f"invalid normal outside dropped triangle: {ordinal}"
            )
    filtered_text = "".join(
        raw for index, raw in enumerate(parsed.lines) if index not in removed_lines
    )
    evidence_without_hash: dict[str, object] = {
        "schema": 1,
        "strategy": "direct-degenerate-prefilter-v1",
        "cross_squared_threshold": float(cross_squared_threshold),
        "source_triangle_count": len(parsed.triangles),
        "dropped_count": len(dropped),
        "dropped_fraction": len(dropped) / len(parsed.triangles),
        "triangles": records,
    }
    encoded = (
        json.dumps(
            evidence_without_hash,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    evidence = {
        **evidence_without_hash,
        "evidence_sha256": hashlib.sha256(encoded).hexdigest(),
    }
    return DirectDegeneratePrefilter(
        filtered_text, tuple(dropped), evidence
    )


def _direct_float(value: float) -> str:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("direct SMD contains a non-finite float")
    if value == 0.0:
        return "0"
    return format(value, ".9g")


def _f32_word(value: float) -> int:
    word = struct.unpack("=I", struct.pack("=f", float(value)))[0]
    return 0 if word == 0x80000000 else word


def _influence_key(values: Sequence[tuple[str, float]]) -> tuple[tuple[str, int], ...]:
    combined: dict[str, float] = {}
    for name, weight in values:
        if type(name) is not str or not name or not math.isfinite(float(weight)):
            raise ValueError("corner influence is invalid")
        if float(weight) > 0.0:
            combined[name] = combined.get(name, 0.0) + float(weight)
    total = sum(combined.values())
    if not combined or total <= 1e-12:
        raise ValueError("corner influence is empty")
    return tuple(sorted((name, _f32_word(weight / total)) for name, weight in combined.items()))


def map_imported_corners_to_smd(
    original_text: str,
    positions: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    loop_normals: Sequence[Sequence[float]],
    loop_uvs: Sequence[Sequence[float]],
    material_ids: Sequence[int],
    material_names: Sequence[str],
    vertex_influences: Sequence[Sequence[tuple[str, float]]],
    *,
    excluded_source_triangles: frozenset[int] = frozenset(),
    dropped_source_triangles: frozenset[int] = frozenset(),
) -> tuple[int, ...]:
    """Map imported loops to source corners by exact float32 tuple identity, never by ordinal."""
    parsed = parse_smd_triangles(
        original_text, allow_invalid_normals=bool(dropped_source_triangles)
    )
    if any(
        type(index) is not int or index < 0 or index >= len(parsed.triangles)
        for index in dropped_source_triangles
    ):
        raise ValueError("dropped source triangle ordinal is invalid")
    excluded = frozenset(excluded_source_triangles | dropped_source_triangles)
    if len(loop_normals) != len(triangles) * 3 or len(loop_uvs) != len(triangles) * 3:
        raise ValueError("imported loop attribute counts are invalid")
    if len(material_ids) != len(triangles) or len(vertex_influences) != len(positions):
        raise ValueError("imported topology attribute counts are invalid")
    node_names: dict[int, str] = {}
    in_nodes = False
    for raw in parsed.lines:
        folded = raw.strip().casefold()
        if folded == "nodes":
            in_nodes = True
            continue
        if in_nodes and folded == "end":
            break
        if in_nodes:
            match = re.match(r'^\s*(-?\d+)\s+"([^"]*)"\s+-?\d+', raw)
            if match:
                node_names[int(match.group(1))] = match.group(2)

    def source_influences(corner: SmdCorner) -> tuple[tuple[str, float], ...]:
        if len(corner.tokens) > 9:
            count = int(corner.tokens[9])
            if count == 0:
                return ((node_names[int(corner.tokens[0])], 1.0),)
            values = tuple(
                (node_names[int(corner.tokens[10 + i * 2])], float(corner.tokens[11 + i * 2]))
                for i in range(count)
            )
        else:
            values = ((node_names[int(corner.tokens[0])], 1.0),)
        return values

    def identity(position, normal, uv, influences) -> tuple[object, ...]:
        if len(position) != 3 or len(normal) != 3 or len(uv) != 2:
            raise ValueError("corner tuple width is invalid")
        normal_length = math.sqrt(sum(float(value) ** 2 for value in normal))
        if not math.isfinite(normal_length) or normal_length <= 1e-12:
            raise ValueError("corner normal is invalid")
        return (
            tuple(_f32_word(value) for value in position),
            tuple(float(value) / normal_length for value in normal),
            tuple(_f32_word(value) for value in uv),
            _influence_key(influences),
        )

    def equivalent(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
        return (
            left[0] == right[0] and left[2:] == right[2:]
            and math.sqrt(sum((a - b) ** 2 for a, b in zip(left[1], right[1])))
            <= IMPORT_NORMAL_EQUIVALENCE_TOLERANCE
        )

    source_identities = tuple(
        None if index in dropped_source_triangles else tuple(
            identity(c.position, c.normal, c.uv, source_influences(c))
            for c in triangle.corners
        )
        for index, triangle in enumerate(parsed.triangles)
    )
    result: list[int] = []
    used: set[int] = set(excluded)
    orders = ((0, 1, 2), (1, 2, 0), (2, 0, 1))
    def coarse(value: tuple[object, ...]) -> tuple[object, ...]:
        return (value[0], value[2], value[3])

    source_lookup: dict[tuple[object, ...], list[tuple[int, tuple[int, int, int]]]] = {}
    for source_triangle_index, source_triangle in enumerate(parsed.triangles):
        if source_triangle_index in excluded:
            continue
        identities = source_identities[source_triangle_index]
        assert identities is not None
        for order in orders:
            key = (
                source_triangle.material.casefold(),
                *(coarse(identities[order[i]]) for i in range(3)),
            )
            source_lookup.setdefault(key, []).append((source_triangle_index, order))
    for triangle_index, triangle in enumerate(triangles):
        if len(triangle) != 3 or any(type(vertex) is not int or vertex < 0 or vertex >= len(positions) for vertex in triangle):
            raise ValueError("imported triangle is invalid")
        material_slot = material_ids[triangle_index]
        if type(material_slot) is not int or material_slot < 0 or material_slot >= len(material_names):
            raise ValueError("imported material slot is invalid")
        imported = tuple(
            identity(
                positions[vertex], loop_normals[triangle_index * 3 + corner],
                loop_uvs[triangle_index * 3 + corner], vertex_influences[vertex],
            )
            for corner, vertex in enumerate(triangle)
        )
        matches: list[tuple[int, tuple[int, int, int]]] = []
        key = (material_names[material_slot].casefold(), *(coarse(value) for value in imported))
        for candidate_source, order in source_lookup.get(key, ()):
            identities = source_identities[candidate_source]
            assert identities is not None
            if candidate_source not in used and all(
                equivalent(imported[i], identities[order[i]]) for i in range(3)
            ):
                matches.append((candidate_source, order))
        if not matches:
            available = tuple(
                (source, order) for source, order in source_lookup.get(key, ()) if source not in used
            )
            ranked = sorted(
                (
                    max(math.sqrt(sum((a-b)**2 for a,b in zip(imported[i][1], source_identities[source][order[i]][1]))) for i in range(3)),  # type: ignore[index]
                    source, order,
                )
                for source, order in available
            )
            if len(available) == 1:
                matches.append(available[0])
            elif (
                ranked and ranked[0][0] <= IMPORT_NORMAL_DISAMBIGUATION_CEILING
                and (len(ranked) == 1 or ranked[1][0] - ranked[0][0] > IMPORT_NORMAL_DISAMBIGUATION_MARGIN)
            ):
                matches.append((ranked[0][1], ranked[0][2]))
        if not matches:
            coarse_candidates = source_lookup.get(key, ())
            nearest_normal = min((
                max(math.sqrt(sum((a-b)**2 for a,b in zip(imported[i][1], source_identities[source][order[i]][1]))) for i in range(3))  # type: ignore[index]
                for source, order in coarse_candidates if source not in used
            ), default=None)
            diagnostics = {"position": 0, "normal": 0, "uv": 0, "skin": 0}
            for diagnostic_source, source_triangle in enumerate(parsed.triangles):
                if diagnostic_source in excluded:
                    continue
                diagnostic_identities = source_identities[diagnostic_source]
                assert diagnostic_identities is not None
                if source_triangle.material.casefold() != material_names[material_slot].casefold():
                    continue
                for order in orders:
                    for i in range(3):
                        left, right = imported[i], diagnostic_identities[order[i]]
                        diagnostics["position"] += int(left[0] == right[0])
                        diagnostics["normal"] += int(math.sqrt(sum((a-b)**2 for a,b in zip(left[1], right[1]))) <= IMPORT_NORMAL_EQUIVALENCE_TOLERANCE)
                        diagnostics["uv"] += int(left[2] == right[2])
                        diagnostics["skin"] += int(left[3] == right[3])
            raise RuntimeError(
                f"imported triangle {triangle_index} has no source-corner mapping; "
                f"coarse_candidates={len(coarse_candidates)} nearest_normal={nearest_normal} component_matches={diagnostics}"
            )
        if len(matches) > 1:
            token_rows = {
                tuple(parsed.triangles[source].corners[order[i]].tokens for i in range(3))
                for source, order in matches
            }
            if len(token_rows) != 1:
                normal_rank = tuple(sorted(
                    (
                        max(
                            math.sqrt(sum(
                                (a - b) ** 2
                                for a, b in zip(
                                    imported[i][1],
                                    source_identities[source][order[i]][1],  # type: ignore[index]
                                )
                            ))
                            for i in range(3)
                        ),
                        source,
                        order,
                    )
                    for source, order in matches
                ))
                if (
                    normal_rank[0][0] <= IMPORT_NORMAL_DISAMBIGUATION_CEILING
                    and normal_rank[1][0] - normal_rank[0][0]
                    > IMPORT_NORMAL_DISAMBIGUATION_MARGIN
                ):
                    matches = [(normal_rank[0][1], normal_rank[0][2])]
                else:
                    raise RuntimeError(
                        f"imported triangle {triangle_index} source-corner mapping is ambiguous; "
                        f"normal_rank={normal_rank[:8]}"
                    )
        matched_source, order = min(matches)
        used.add(matched_source)
        result.extend(matched_source * 3 + order[i] for i in range(3))
    return tuple(result)


def serialize_direct_smd(
    original_text: str,
    positions: Sequence[Sequence[float]],
    normals: Sequence[Sequence[float]],
    uvs: Sequence[Sequence[float]],
    influences: Sequence[Sequence[tuple[str, float]]],
    indices: Sequence[int],
    triangle_materials: Sequence[str],
    *,
    source_corner_ordinals: Sequence[int] | None = None,
    dropped_source_triangles: frozenset[int] = frozenset(),
) -> str:
    """Serialize retained direct tuples without a Blender export round-trip."""
    if not positions or len(indices) < 3 or len(indices) % 3 or len(triangle_materials) != len(indices) // 3:
        raise ValueError("direct SMD topology/material counts are invalid")
    if not (len(normals) == len(uvs) == len(influences) == len(positions)):
        raise ValueError("direct SMD attribute counts are invalid")
    parsed_source = parse_smd_triangles(
        original_text, allow_invalid_normals=bool(dropped_source_triangles)
    )
    if any(
        type(index) is not int
        or index < 0
        or index >= len(parsed_source.triangles)
        for index in dropped_source_triangles
    ):
        raise ValueError("dropped source triangle ordinal is invalid")
    source_corners = tuple(
        corner for triangle in parsed_source.triangles for corner in triangle.corners
    )
    if source_corner_ordinals is not None and (
        len(source_corner_ordinals) != len(positions)
        or any(type(value) is not int or value < 0 or value >= len(source_corners) for value in source_corner_ordinals)
        or any(value // 3 in dropped_source_triangles for value in source_corner_ordinals)
    ):
        raise ValueError("direct SMD source-corner provenance is invalid")
    lines = original_text.splitlines(keepends=True)
    triangle_line = next((i for i, line in enumerate(lines) if line.strip().casefold() == "triangles"), -1)
    if triangle_line < 0:
        raise ValueError("source SMD triangles section is missing")
    bone_by_name: dict[str, int] = {}
    in_nodes = False
    for raw in lines[:triangle_line]:
        folded = raw.strip().casefold()
        if folded == "nodes":
            in_nodes = True
            continue
        if in_nodes and folded == "end":
            in_nodes = False
            continue
        if in_nodes:
            match = re.match(r'^\s*(-?\d+)\s+"([^"]*)"\s+-?\d+', raw)
            if match:
                bone_by_name[match.group(2)] = int(match.group(1))
    if not bone_by_name:
        raise ValueError("source SMD nodes are missing")
    output = list(lines[: triangle_line + 1])
    if output[-1] and not output[-1].endswith(("\n", "\r")):
        output[-1] += "\n"
    for triangle, material in enumerate(triangle_materials):
        if type(material) is not str or not material.strip() or "\n" in material or "\r" in material:
            raise ValueError("direct SMD material is invalid")
        corners = tuple(indices[triangle * 3 : triangle * 3 + 3])
        if len(set(corners)) != 3 or any(type(index) is not int or index < 0 or index >= len(positions) for index in corners):
            raise ValueError("direct SMD triangle is degenerate")
        a, b, c = (positions[index] for index in corners)
        if any(len(row) != 3 for row in (a, b, c)):
            raise ValueError("direct SMD position is invalid")
        ab = tuple(float(b[i]) - float(a[i]) for i in range(3))
        ac = tuple(float(c[i]) - float(a[i]) for i in range(3))
        cross = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
        if sum(value * value for value in cross) <= 1e-30:
            raise ValueError("direct SMD triangle is degenerate")
        output.append(material.strip() + "\n")
        for vertex in corners:
            if source_corner_ordinals is not None:
                output.append(" ".join(source_corners[source_corner_ordinals[vertex]].tokens) + "\n")
                continue
            if len(positions[vertex]) != 3 or len(normals[vertex]) != 3 or len(uvs[vertex]) != 2:
                raise ValueError("direct SMD tuple width is invalid")
            combined: dict[int, float] = {}
            for name, weight in influences[vertex]:
                if name not in bone_by_name or not math.isfinite(float(weight)) or float(weight) <= 0.0:
                    raise ValueError("direct SMD influence is invalid")
                bone = bone_by_name[name]
                combined[bone] = combined.get(bone, 0.0) + float(weight)
            links = sorted(combined.items(), key=lambda item: (-item[1], item[0]))[:4]
            total = sum(weight for _bone, weight in links)
            if not links or not math.isfinite(total) or total <= 1e-12:
                raise ValueError("direct SMD influence sum is invalid")
            links = sorted(((bone, weight / total) for bone, weight in links), key=lambda item: item[0])
            primary = max(links, key=lambda item: (item[1], -item[0]))[0]
            tokens = [str(primary)]
            tokens.extend(_direct_float(value) for value in (*positions[vertex], *normals[vertex], *uvs[vertex]))
            tokens.append(str(len(links)))
            for bone, weight in links:
                tokens.extend((str(bone), _direct_float(weight)))
            output.append(" ".join(tokens) + "\n")
    output.append("end\n")
    serialized = "".join(output)
    parse_smd_triangles(serialized)
    return serialized


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
    for material, corners in expected:
        by_material_expected.setdefault(material, []).append(corners)
    if set(by_material_expected) != {triangle.material for triangle in exported.triangles}:
        raise RuntimeError("direct provenance material set differs from export")
    output = list(exported.lines)
    used_lines: set[int] = set()
    remaining = {material: list(rows) for material, rows in by_material_expected.items()}

    def coarse_corner(corner: SmdCorner) -> tuple[object, ...]:
        return (
            corner.tokens[0], tuple(round(value, 5) for value in corner.position),
            corner.uv, tuple(corner.tokens[9:]),
        )

    indexed: dict[tuple[object, ...], list[tuple[int, tuple[SmdCorner, ...]]]] = {}
    for material, rows in remaining.items():
        for row_index, row in enumerate(rows):
            for shift in range(3):
                rotated = row[shift:] + row[:shift]
                key = (material, *(coarse_corner(corner) for corner in rotated))
                indexed.setdefault(key, []).append((row_index, rotated))

    def corner_matches(source: SmdCorner, candidate: SmdCorner) -> bool:
        return (
            source.tokens[0] == candidate.tokens[0]
            and source.uv == candidate.uv
            and len(source.tokens) == len(candidate.tokens)
            and all(abs(a-b) <= position_tolerance for a,b in zip(source.position, candidate.position))
            and all(_numeric_token_equal(source.tokens[i], candidate.tokens[i]) for i in range(9, len(source.tokens)))
        )

    for after in exported.triangles:
        matches: list[tuple[int, tuple[SmdCorner, ...]]] = []
        key = (after.material, *(coarse_corner(corner) for corner in after.corners))
        for row_index, rotated in indexed.get(key, ()):
            if row_index < len(remaining[after.material]) and remaining[after.material][row_index] is not None and all(
                corner_matches(source, candidate) for source, candidate in zip(rotated, after.corners)
            ):
                matches.append((row_index, rotated))
        if not matches:
            raise RuntimeError("direct provenance has no matching output triangle")
        normal_signatures = {tuple(c.normal for c in row) for _index, row in matches}
        if len(normal_signatures) != 1:
            raise RuntimeError("direct provenance triangle match is ambiguous across hard normals")
        row_index, before = matches[0]
        remaining[after.material][row_index] = None  # type: ignore[list-item]
        for source, candidate in zip(before, after.corners):
                if candidate.line_index in used_lines:
                    raise RuntimeError("direct provenance is not bijective")
                used_lines.add(candidate.line_index)
                raw = output[candidate.line_index]
                for token_index in (6, 5, 4):
                    start, end = candidate.spans[token_index]
                    raw = raw[:start] + source.tokens[token_index] + raw[end:]
                output[candidate.line_index] = raw
    if any(any(row is not None for row in rows) for rows in remaining.values()):
        raise RuntimeError("direct provenance contains unmatched source triangles")
    if len(used_lines) != len(source_corner_ordinals):
        raise RuntimeError("direct provenance is not a complete bijection")
    return "".join(output)
