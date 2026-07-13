from __future__ import annotations

from dataclasses import dataclass
import hashlib
from itertools import product
from pathlib import Path, PurePosixPath
import re

from .domain import require_canonical_relative
from .qc_graph import QcGraph, QcReference, _block_bounds, _lex
from .reporting import canonical_json


_MAX_STATES = 16
_MAX_TEXTURE_ROWS = 256
_MAX_TEXTURE_WIDTH = 256


def _digest(kind: str, payload: object) -> str:
    return f"{kind}-" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _relative(graph: QcGraph, path: Path, label: str) -> str:
    try:
        value = path.resolve(strict=True).relative_to(
            graph.family_root.resolve(strict=True)
        ).as_posix()
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} escapes QC graph root") from exc
    return require_canonical_relative(value, label)


def _source_identity(graph: QcGraph, path: Path) -> str:
    relative = _relative(graph, path, "QC state source")
    parts = list(PurePosixPath(relative).parts)
    if len(parts) >= 2 and parts[-2].casefold() == "output":
        parts.pop(-2)
    elif parts and parts[0].casefold() == "output":
        parts.pop(0)
    leaf = PurePosixPath(parts[-1])
    parts[-1] = re.sub(r"_opt$", "", leaf.stem, flags=re.IGNORECASE) + leaf.suffix.casefold()
    return PurePosixPath(*(part.casefold() for part in parts)).as_posix()


@dataclass(frozen=True)
class QcActiveOccurrence:
    graph_relative_path: str
    directive: str
    line: int
    source_path: Path
    source_identity: str
    occurrence_ordinal: int
    base_occurrence_ordinal: int
    replacement_original_identity: str | None = None


@dataclass(frozen=True)
class QcActiveState:
    state_key: str
    bodygroup_key: str
    lod_key: str
    skin_key: str
    bodygroup_indices: tuple[tuple[str, int], ...]
    lod_index: int
    skin_index: int
    active: tuple[QcActiveOccurrence, ...]


@dataclass(frozen=True)
class _SkinRow:
    graph_path: str
    line: int
    name: str
    index: int
    materials: tuple[str, ...]


@dataclass(frozen=True)
class _LodLevel:
    index: int
    graph_path: str
    line: int
    group: str
    replacements: tuple[tuple[str, QcReference], ...]


def _texturegroup_rows(graph: QcGraph) -> tuple[_SkinRow, ...]:
    declarations: list[tuple[str, int, str, tuple[tuple[str, ...], ...]]] = []
    for graph_file in graph.files:
        tokens = _lex(graph_file.text)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.value.casefold() != "$texturegroup":
                index += 1
                continue
            cursor = index + 1
            args = []
            while cursor < len(tokens) and tokens[cursor].kind not in {"newline", "brace"}:
                args.append(tokens[cursor])
                cursor += 1
            if len(args) != 1:
                raise ValueError("$texturegroup requires exactly one name")
            block = _block_bounds(tokens, cursor, len(tokens))
            if block is None:
                raise ValueError("$texturegroup requires a row block")
            block_start, block_end, after_block = block
            rows: list[tuple[str, ...]] = []
            cursor = block_start
            while cursor < block_end:
                if tokens[cursor].kind == "newline":
                    cursor += 1
                    continue
                if tokens[cursor].value != "{":
                    raise ValueError("$texturegroup contains an unknown row command")
                row = _block_bounds(tokens, cursor, block_end)
                if row is None:
                    raise ValueError("$texturegroup row is malformed")
                row_start, row_end, next_row = row
                if any(item.value in {"{", "}"} for item in tokens[row_start:row_end]):
                    raise ValueError("$texturegroup rows cannot be nested")
                values = tuple(
                    item.value.replace("\\", "/")
                    for item in tokens[row_start:row_end]
                    if item.kind != "newline"
                )
                if not values or len(values) > _MAX_TEXTURE_WIDTH or any(not value for value in values):
                    raise ValueError("$texturegroup row is empty or exceeds bound")
                rows.append(values)
                cursor = next_row
            if (
                not rows or len(rows) > _MAX_TEXTURE_ROWS
                or len({len(row) for row in rows}) != 1
            ):
                raise ValueError("$texturegroup rows are incomplete or inconsistent")
            declarations.append((
                _relative(graph, graph_file.path, "texturegroup graph"),
                token.line, args[0].value, tuple(rows),
            ))
            index = after_block
    if len(declarations) > 1:
        raise ValueError("multiple $texturegroup declarations are ambiguous")
    if not declarations:
        return (_SkinRow("", 0, "default", 0, ()),)
    graph_path, line, name, rows = declarations[0]
    return tuple(
        _SkinRow(graph_path, line, name, index, materials)
        for index, materials in enumerate(rows)
    )


def _lod_levels(graph: QcGraph, ordinals: dict[int, int]) -> tuple[_LodLevel, ...]:
    grouped: dict[str, list[QcReference]] = {}
    order: list[str] = []
    for reference in graph.references:
        if reference.directive != "$lod/replacemodel":
            continue
        if not reference.group:
            raise ValueError("LOD replacement occurrence has no group")
        if reference.group not in grouped:
            grouped[reference.group] = []
            order.append(reference.group)
        grouped[reference.group].append(reference)
    result = [_LodLevel(0, "", 0, "lod0", ())]
    for level_index, group in enumerate(order, start=1):
        references = grouped[group]
        if len(references) % 2:
            raise ValueError("LOD replacement references do not form original/replacement pairs")
        replacements: list[tuple[str, QcReference]] = []
        originals: set[str] = set()
        for index in range(0, len(references), 2):
            original, replacement = references[index:index + 2]
            if (
                original.line != replacement.line
                or original.graph_file != replacement.graph_file
                or ordinals[id(original)] + 1 != ordinals[id(replacement)]
            ):
                raise ValueError("LOD original/replacement pair provenance is ambiguous")
            identity = _source_identity(graph, original.source_path)
            if identity in originals:
                raise ValueError("LOD replacement original is duplicated")
            originals.add(identity)
            replacements.append((identity, replacement))
        first = references[0]
        result.append(_LodLevel(
            level_index,
            _relative(graph, first.graph_file, "LOD graph"),
            first.line, group, tuple(replacements),
        ))
    return tuple(result)


def _occurrence(
    graph: QcGraph, reference: QcReference, ordinals: dict[int, int],
    *, base: QcReference, replacement_original_identity: str | None,
) -> QcActiveOccurrence:
    return QcActiveOccurrence(
        _relative(graph, reference.graph_file, "active occurrence graph"),
        reference.directive, reference.line, reference.source_path,
        _source_identity(graph, reference.source_path), ordinals[id(reference)],
        ordinals[id(base)], replacement_original_identity,
    )


def enumerate_qc_states(
    graph: QcGraph, *, maximum_states: int = _MAX_STATES,
) -> tuple[QcActiveState, ...]:
    if not isinstance(graph, QcGraph):
        raise TypeError("QC state enumeration requires a QcGraph")
    if type(maximum_states) is not int or not 1 <= maximum_states <= _MAX_STATES:
        raise ValueError("QC state maximum must be between 1 and 16")
    ordinals = {id(reference): index for index, reference in enumerate(graph.references)}
    if len(ordinals) != len(graph.references):
        raise ValueError("QC graph occurrence objects are duplicated")
    groups = tuple(sorted(graph.bodygroups, key=lambda item: (
        _relative(graph, item.graph_file, "bodygroup graph").casefold(),
        _relative(graph, item.graph_file, "bodygroup graph"), item.line, item.name.casefold(), item.name,
    )))
    body_products = tuple(product(*(range(len(group.choices)) for group in groups))) if groups else ((),)
    skins = _texturegroup_rows(graph)
    lods = _lod_levels(graph, ordinals)
    state_count = len(body_products) * len(skins) * len(lods)
    if not 1 <= state_count <= maximum_states:
        raise ValueError(f"QC complete state count {state_count} exceeds 16-state bound")

    fixed = tuple(
        reference for reference in graph.references
        if reference.role == "visual"
        and reference.directive not in {"$bodygroup/studio", "$lod/replacemodel"}
    )
    states: list[QcActiveState] = []
    for choices in body_products:
        selected = list(fixed)
        bodygroup_payload = []
        bodygroup_indices = []
        for group_index, (group, choice_index) in enumerate(zip(groups, choices)):
            graph_path = _relative(graph, group.graph_file, "bodygroup graph")
            choice = group.choices[choice_index]
            identity = None if choice is None else _source_identity(graph, choice.source_path)
            group_identity = f"{group_index:03d}:{group.name}"
            bodygroup_indices.append((group_identity, choice_index))
            bodygroup_payload.append({
                "index": group_index, "graph_path": graph_path, "line": group.line,
                "name": group.name, "choice_index": choice_index,
                "source_identity": identity,
            })
            if choice is not None:
                selected.append(choice)
        bodygroup_key = _digest("bodygroup", bodygroup_payload)
        for lod in lods:
            replacement_by_identity = dict(lod.replacements)
            lod_payload = {
                "index": lod.index, "graph_path": lod.graph_path,
                "line": lod.line, "group": lod.group,
                "replacements": [
                    {
                        "original_identity": original,
                        "replacement_identity": _source_identity(graph, replacement.source_path),
                        "occurrence_ordinal": ordinals[id(replacement)],
                    }
                    for original, replacement in lod.replacements
                ],
            }
            lod_key = _digest("lod", lod_payload)
            active = []
            for base in selected:
                original_identity = _source_identity(graph, base.source_path)
                replacement = replacement_by_identity.get(original_identity)
                active.append(_occurrence(
                    graph, replacement or base, ordinals, base=base,
                    replacement_original_identity=(
                        original_identity if replacement is not None else None
                    ),
                ))
            active_tuple = tuple(sorted(active, key=lambda item: (
                item.base_occurrence_ordinal, item.occurrence_ordinal,
                item.source_identity.casefold(), item.source_identity,
            )))
            for skin in skins:
                skin_payload = {
                    "graph_path": skin.graph_path, "line": skin.line,
                    "name": skin.name, "index": skin.index,
                    "materials": list(skin.materials),
                }
                skin_key = _digest("skin", skin_payload)
                state_key = _digest("state", {
                    "bodygroup_key": bodygroup_key,
                    "lod_key": lod_key, "skin_key": skin_key,
                })
                states.append(QcActiveState(
                    state_key, bodygroup_key, lod_key, skin_key,
                    tuple(bodygroup_indices), lod.index, skin.index, active_tuple,
                ))
    states.sort(key=lambda item: (item.state_key.casefold(), item.state_key))
    if len({item.state_key for item in states}) != len(states):
        raise ValueError("QC complete state keys collide")
    return tuple(states)
