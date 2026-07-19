from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from .qc_graph import QcGraph, _block_bounds, _contained_existing, _lex, _line_values
from .animation_pose_selector import _parse_animation
from .regions import RegionManifest, load_region_manifest_payload
from .reporting import deep_freeze


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NODE = re.compile(r'^\s*(-?\d+)\s+"([^"]+)"\s+(-?\d+)\s*$')


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _sha(value: object, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _relative(path: Path, root: Path) -> str:
    return path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()


def _file_proof(path: Path, root: Path) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise ValueError("catalog source file escapes exact source root") from exc
    cursor = root.resolve(strict=True)
    for part in Path(relative).parts:
        cursor /= part
        info = cursor.lstat()
        if cursor.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("catalog source file has reparse ancestry")
    before = resolved.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("catalog source proof is not a regular file")
    payload = resolved.read_bytes()
    after = resolved.stat()
    if (
        len(payload) != before.st_size
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ValueError("catalog source file changed while hashing")
    return {
        "path": relative,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


@dataclass(frozen=True)
class RegionAnimationCatalogCaps:
    max_regions: int = 64
    max_pairs_per_shard: int = 64
    max_animation_definitions: int = 4096
    max_weightlists: int = 1024

    def __post_init__(self) -> None:
        if (
            type(self.max_regions) is not int or self.max_regions != 64
            or type(self.max_pairs_per_shard) is not int
            or self.max_pairs_per_shard != 64
            or type(self.max_animation_definitions) is not int
            or not 1 <= self.max_animation_definitions <= 4096
            or type(self.max_weightlists) is not int
            or not 1 <= self.max_weightlists <= 1024
        ):
            raise ValueError("region animation catalog caps are invalid")

    def to_payload(self) -> dict[str, object]:
        unsigned = {
            "max_animation_definitions": self.max_animation_definitions,
            "max_pairs_per_shard": self.max_pairs_per_shard,
            "max_regions": self.max_regions,
            "max_weightlists": self.max_weightlists,
        }
        return {**unsigned, "caps_sha256": _digest(unsigned)}


@dataclass(frozen=True)
class RegionAnimationCatalog:
    _payload: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "_payload", deep_freeze(json.loads(_canonical_json(self._payload))),
        )

    def to_payload(self) -> dict[str, object]:
        return json.loads(_canonical_json(_thaw(self._payload)))


@dataclass(frozen=True)
class _Weightlist:
    name: str
    positive_bones: tuple[str, ...]
    graph_file: str
    line: int


@dataclass(frozen=True)
class _Animation:
    name: str
    path: Path
    graph_file: str
    line: int
    source_ordinal: int
    subtract_name: str | None
    subtract_frame: int | None
    weightlist_name: str | None


def _block_commands(tokens, start: int, end: int) -> list[tuple[str, list[Any], int]]:
    commands = []
    index = start
    while index < end:
        token = tokens[index]
        if token.kind == "newline":
            index += 1
            continue
        if token.value in {"{", "}"}:
            raise ValueError(f"nested QC semantic block is unsupported at line {token.line}")
        values, next_index = _line_values(tokens, index + 1, end)
        commands.append((token.value.casefold(), values, token.line))
        index = max(next_index, index + 1)
    return commands


def _parse_semantics(graph: QcGraph, caps: RegionAnimationCatalogCaps):
    animations: dict[str, _Animation] = {}
    weightlists: dict[str, _Weightlist] = {}
    ordinal = 0
    root = graph.family_root.resolve(strict=True)
    for graph_file in graph.files:
        tokens = _lex(graph_file.text)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.kind == "newline" or token.value in {"{", "}"}:
                index += 1
                continue
            directive = token.value.casefold()
            args, line_end = _line_values(tokens, index + 1, len(tokens))
            block = _block_bounds(tokens, line_end, len(tokens))
            if block is None:
                block_start = block_end = line_end
                next_index = max(line_end, index + 1)
            else:
                block_start, block_end, next_index = block
            graph_identity = _relative(graph_file.path, root)
            if directive == "$weightlist":
                if len(args) != 1 or block is None:
                    raise ValueError("weightlist definition is structurally ambiguous")
                key = args[0].value.casefold()
                if key in weightlists:
                    raise ValueError("duplicate or ambiguous weightlist name")
                positive = []
                seen_bones = set()
                for _command, values, command_line in _block_commands(tokens, block_start, block_end):
                    row = [_command, *(item.value for item in values)]
                    if len(row) != 2:
                        raise ValueError(f"weightlist row is invalid at line {command_line}")
                    bone, raw_weight = row
                    folded_bone = bone.casefold()
                    if folded_bone in seen_bones:
                        raise ValueError("duplicate or ambiguous weightlist bone")
                    seen_bones.add(folded_bone)
                    try:
                        weight = float(raw_weight)
                    except ValueError as exc:
                        raise ValueError("weightlist weight is invalid") from exc
                    if not math.isfinite(weight) or weight < 0.0:
                        raise ValueError("weightlist weight is invalid")
                    if weight > 0.0:
                        positive.append(bone)
                weightlists[key] = _Weightlist(
                    args[0].value, tuple(positive), graph_identity, token.line,
                )
            elif directive == "$animation":
                if len(args) != 2 or block is None:
                    raise ValueError("animation definition is structurally ambiguous")
                key = args[0].value.casefold()
                if key in animations:
                    raise ValueError("duplicate or ambiguous animation name")
                source = _contained_existing(
                    root, graph_file.path.parent, args[1].value, "animation source",
                )
                subtracts = []
                selected_weightlists = []
                for command, values, command_line in _block_commands(tokens, block_start, block_end):
                    if command == "subtract":
                        if len(values) != 2:
                            raise ValueError(f"subtract is invalid at line {command_line}")
                        try:
                            frame = int(values[1].value)
                        except ValueError as exc:
                            raise ValueError("subtract frame is invalid") from exc
                        if frame < 0:
                            raise ValueError("subtract frame is invalid")
                        subtracts.append((values[0].value, frame))
                    elif command == "weightlist":
                        if len(values) != 1:
                            raise ValueError(f"weightlist use is invalid at line {command_line}")
                        selected_weightlists.append(values[0].value)
                if len(subtracts) > 1 or len(selected_weightlists) > 1:
                    raise ValueError("animation subtract/weightlist relation is ambiguous")
                animations[key] = _Animation(
                    args[0].value, source, graph_identity, token.line, ordinal,
                    subtracts[0][0] if subtracts else None,
                    subtracts[0][1] if subtracts else None,
                    selected_weightlists[0] if selected_weightlists else None,
                )
                ordinal += 1
            index = next_index
    if len(animations) > caps.max_animation_definitions:
        raise ValueError("animation definition cap exceeded")
    if len(weightlists) > caps.max_weightlists:
        raise ValueError("weightlist definition cap exceeded")
    return animations, weightlists


def _geometry_lineage(path: Path, root: Path) -> tuple[tuple[str, ...], dict[str, str | None], dict[str, object]]:
    proof = _file_proof(path, root)
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("unable to read exact region SMD") from exc
    bones: dict[int, str] = {}
    parents: dict[int, int] = {}
    section = None
    direct: set[int] = set()
    expecting_material = False
    remaining = 0
    saw_end = False
    for raw in lines:
        stripped = raw.strip()
        folded = stripped.casefold()
        if folded in {"nodes", "skeleton", "triangles"}:
            section = folded
            expecting_material = folded == "triangles"
            continue
        if folded == "end":
            if section == "triangles":
                saw_end = True
            section = None
            continue
        if not stripped:
            continue
        if section == "nodes":
            match = _NODE.fullmatch(raw)
            if match is None:
                raise ValueError("region SMD node is invalid")
            bone = int(match.group(1))
            name = match.group(2)
            if bone in bones or name.casefold() in {value.casefold() for value in bones.values()}:
                raise ValueError("region SMD bone lineage is ambiguous")
            bones[bone] = name
            parents[bone] = int(match.group(3))
        elif section == "triangles":
            if expecting_material:
                expecting_material = False
                remaining = 3
                continue
            tokens = stripped.split()
            if len(tokens) < 9:
                raise ValueError("region SMD vertex is invalid")
            try:
                parent = int(tokens[0])
                weighted = [(parent, 1.0)]
                if len(tokens) >= 10:
                    count = int(tokens[9])
                    if count < 0 or len(tokens) != 10 + count * 2:
                        raise ValueError
                    if count:
                        weighted = []
                    for offset in range(count):
                        bone = int(tokens[10 + offset * 2])
                        weight = float(tokens[11 + offset * 2])
                        if not math.isfinite(weight) or weight < 0.0:
                            raise ValueError
                        if weight > 0.0:
                            weighted.append((bone, weight))
            except (ValueError, IndexError) as exc:
                raise ValueError("region SMD explicit weights are invalid") from exc
            if not weighted:
                raise ValueError("region SMD has no positive vertex weights")
            direct.update(bone for bone, _weight in weighted)
            remaining -= 1
            if remaining == 0:
                expecting_material = True
    if not bones or not direct or not saw_end or any(
        parent != -1 and parent not in bones for parent in parents.values()
    ) or any(bone not in bones for bone in direct):
        raise ValueError("region SMD lineage is incomplete")
    closure = set(direct)
    for bone in tuple(direct):
        visited = set()
        cursor = bone
        while cursor != -1:
            if cursor in visited:
                raise ValueError("region SMD bone lineage cycle")
            visited.add(cursor)
            closure.add(cursor)
            cursor = parents[cursor]
    lineage = tuple(sorted((bones[bone] for bone in closure), key=lambda item: (item.casefold(), item)))
    by_name = {
        name: None if parents[bone] == -1 else bones[parents[bone]]
        for bone, name in bones.items()
    }
    return lineage, by_name, proof


def _selection_payload(value: object) -> dict[str, object]:
    if hasattr(value, "to_payload") and callable(value.to_payload):
        value = value.to_payload()
    if not isinstance(value, Mapping):
        raise TypeError("region animation selector returned an invalid result")
    payload = json.loads(_canonical_json(value))
    if payload.get("candidate_inputs_consulted") is not False:
        raise ValueError("region animation selector consulted candidate inputs")
    _sha(payload.get("selection_sha256"), "selector selection seal")
    unsigned = {key: item for key, item in payload.items() if key != "selection_sha256"}
    if payload["selection_sha256"] != _digest(unsigned):
        raise ValueError("selector selection seal is invalid")
    return payload


def _animated(payload: Mapping[str, object]) -> bool:
    return "animation_relative_path" in payload


def _selection_rank(payload: Mapping[str, object]):
    return (
        -float(payload["displacement"]), -float(payload["rms_displacement"]),
        str(payload["animation_relative_path"]).casefold(),
        str(payload["animation_relative_path"]), int(payload["frame"]),
        int(payload["source_time"]), int(payload["bone_index"]),
    )


def build_region_animation_catalog(
    graph: QcGraph,
    manifest: RegionManifest,
    *,
    source_root: str | Path,
    family_id: str,
    family_input_sha256: str,
    source_snapshot_sha256: str,
    region_manifest_sha256: str,
    contracts_sha256: str,
    selector: Callable[..., object],
    caps: RegionAnimationCatalogCaps,
) -> RegionAnimationCatalog:
    if not isinstance(graph, QcGraph) or not isinstance(manifest, RegionManifest):
        raise TypeError("region animation catalog graph/manifest is invalid")
    if not isinstance(caps, RegionAnimationCatalogCaps) or not callable(selector):
        raise TypeError("region animation catalog selector/caps are invalid")
    root = Path(source_root).resolve(strict=True)
    if graph.family_root.resolve(strict=True) != root:
        raise ValueError("QC graph is not bound to the exact source root")
    if type(family_id) is not str or not family_id:
        raise ValueError("region animation catalog family is invalid")
    for value, label in (
        (family_input_sha256, "family input"),
        (source_snapshot_sha256, "source snapshot"),
        (region_manifest_sha256, "region manifest"),
        (contracts_sha256, "contracts"),
    ):
        _sha(value, label)
    canonical_manifest = load_region_manifest_payload(manifest.to_payload())
    if canonical_manifest.to_payload() != manifest.to_payload():
        raise ValueError("region manifest is not canonical")
    if not manifest.entries or len(manifest.entries) > caps.max_regions:
        raise ValueError("region animation catalog region cap exceeded")

    animations, weightlists = _parse_semantics(graph, caps)
    paired = []
    for action in animations.values():
        if action.subtract_name is None:
            continue
        corrective = animations.get(action.subtract_name.casefold())
        if corrective is None:
            raise ValueError("animation subtract target is unknown")
        if corrective.subtract_name is not None:
            raise ValueError("animation subtract relation is cyclic or chained")
        if action.weightlist_name is None:
            raise ValueError("subtract animation has no exact weightlist")
        weightlist = weightlists.get(action.weightlist_name.casefold())
        if weightlist is None:
            raise ValueError("animation weightlist target is unknown")
        action_proof = _file_proof(action.path, root)
        corrective_proof = _file_proof(corrective.path, root)
        action_skeleton = _parse_animation(action.path)
        corrective_skeleton = _parse_animation(corrective.path)
        if (
            action_skeleton.bones != corrective_skeleton.bones
            or action_skeleton.parents != corrective_skeleton.parents
        ):
            raise ValueError("animation action/corrective bone lineage differs")
        animation_lineage = {
            name: None if action_skeleton.parents[bone] == -1
            else action_skeleton.bones[action_skeleton.parents[bone]]
            for bone, name in action_skeleton.bones.items()
        }
        paired.append((
            action, corrective, weightlist, action_proof, corrective_proof,
            animation_lineage,
        ))
    paired.sort(key=lambda item: (
        str(item[3]["path"]).casefold(), str(item[3]["path"]),
        str(item[4]["path"]).casefold(), str(item[4]["path"]),
    ))
    definitions = [{
        "action_name": action.name,
        "action_path": action_proof["path"],
        "action_sha256": action_proof["sha256"],
        "corrective_name": corrective.name,
        "corrective_path": corrective_proof["path"],
        "corrective_sha256": corrective_proof["sha256"],
        "graph_file": action.graph_file,
        "line": action.line,
        "source_ordinal": action.source_ordinal,
        "subtract_frame": action.subtract_frame,
        "weightlist_name": weightlist.name,
        "weightlist_positive_bones": list(weightlist.positive_bones),
    } for action, corrective, weightlist, action_proof, corrective_proof, _lineage in paired]

    regions = []
    for entry in manifest.entries:
        geometry_path = _contained_existing(
            root, root, entry.descriptor.source_identity, "region geometry source",
        )
        lineage, region_bones, geometry_proof = _geometry_lineage(geometry_path, root)
        lineage_folded = {item.casefold() for item in lineage}
        related = [item for item in paired if any(
            bone.casefold() in lineage_folded for bone in item[2].positive_bones
        )]
        if any(item[5] != region_bones for item in related):
            raise ValueError("animation/geometry bone lineage differs")
        shard_payloads = []
        winners: list[tuple[dict[str, object], tuple[Any, ...]]] = []
        for shard_index, start in enumerate(range(0, len(related), caps.max_pairs_per_shard)):
            shard = related[start:start + caps.max_pairs_per_shard]
            pairs = tuple((item[0].path, item[1].path) for item in shard)
            selected = _selection_payload(selector(
                root, source_root=root, animation_pairs=pairs,
                geometry_paths=(geometry_path,),
            ))
            if _animated(selected):
                identity = (
                    str(selected["animation_relative_path"]).casefold(),
                    str(selected["reference_relative_path"]).casefold(),
                )
                by_identity = {
                    (str(item[3]["path"]).casefold(), str(item[4]["path"]).casefold()): item
                    for item in shard
                }
                if identity not in by_identity:
                    raise ValueError("selector chose a pair outside its exact shard")
                winners.append((selected, by_identity[identity]))
            pair_inventory = [{
                "action_path": item[3]["path"],
                "action_sha256": item[3]["sha256"],
                "corrective_path": item[4]["path"],
                "corrective_sha256": item[4]["sha256"],
            } for item in shard]
            shard_unsigned = {
                "ordinal": shard_index,
                "pair_count": len(shard),
                "pair_inventory": pair_inventory,
                "selection_payload": selected,
            }
            shard_payloads.append({**shard_unsigned, "proof_sha256": _digest(shard_unsigned)})
        if not related:
            mode = "bind-only/empty-relation"
            selection = None
        elif not winners:
            mode = "bind-only/all-shards-zero"
            selection = None
        else:
            winner_pairs = tuple((item[1][0].path, item[1][1].path) for item in winners)
            selection = _selection_payload(selector(
                root, source_root=root, animation_pairs=winner_pairs,
                geometry_paths=(geometry_path,),
            ))
            if not _animated(selection):
                raise ValueError("selector reduction lost a nonzero shard winner")
            expected = min((item[0] for item in winners), key=_selection_rank)
            if (
                str(selection["animation_relative_path"]).casefold()
                != str(expected["animation_relative_path"]).casefold()
                or str(selection["reference_relative_path"]).casefold()
                != str(expected["reference_relative_path"]).casefold()
                or _selection_rank(selection) != _selection_rank(expected)
            ):
                raise ValueError("selector shard reduction differs from the global maximum")
            mode = "bind-animation"
        region_unsigned = {
            "geometry": geometry_proof,
            "lineage_bones": list(lineage),
            "mode": mode,
            "region_key": entry.key,
            "related_pair_count": len(related),
            "selection_payload": selection,
            "shards": shard_payloads,
            "source_identity": entry.descriptor.source_identity,
        }
        regions.append({**region_unsigned, "proof_sha256": _digest(region_unsigned)})

    caps_payload = caps.to_payload()
    qc_inventory = [_file_proof(item.path, root) for item in graph.files]
    unsigned = {
        "schema": 1,
        "kind": "exact-qc-region-animation-catalog-v1",
        "candidate_inputs_consulted": False,
        "family_id": family_id,
        "family_input_sha256": family_input_sha256,
        "source_snapshot_sha256": source_snapshot_sha256,
        "region_manifest_sha256": region_manifest_sha256,
        "contracts_sha256": contracts_sha256,
        "caps": caps_payload,
        "qc_inventory": qc_inventory,
        "definitions": definitions,
        "regions": regions,
    }
    return RegionAnimationCatalog({**unsigned, "catalog_sha256": _digest(unsigned)})


def _catalog_object(value: object, keys: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys or len(value) != len(keys):
        raise ValueError(f"{label} schema is invalid")
    return value


def _catalog_list(value: object, *, maximum: int, label: str) -> list[object] | tuple[object, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ValueError(f"{label} is not a bounded list")
    return value


def _catalog_text(value: object, label: str) -> str:
    if (
        type(value) is not str or not value or value != value.strip()
        or len(value) > 4096 or any(ord(char) < 32 for char in value)
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _catalog_path(value: object, label: str) -> str:
    result = _catalog_text(value, label)
    path = PurePosixPath(result)
    if (
        path.is_absolute() or "\\" in result
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{label} is not a canonical relative path")
    return result


def _validate_file_payload(value: object, label: str) -> Mapping[str, object]:
    result = _catalog_object(value, {"path", "size", "sha256"}, label)
    _catalog_path(result["path"], f"{label}.path")
    if type(result["size"]) is not int or not 0 <= result["size"] <= 64 * 1024 * 1024:
        raise ValueError(f"{label}.size is invalid")
    _sha(result["sha256"], f"{label}.sha256")
    return result


def parse_region_animation_catalog(
    payload: object, *, expected_catalog_sha256: str, expected_family_id: str,
    expected_family_input_sha256: str, expected_source_snapshot_sha256: str,
    expected_region_manifest_sha256: str, expected_contracts_sha256: str,
    expected_caps_sha256: str,
) -> RegionAnimationCatalog:
    """Validate a persisted source-only regional animation catalog and bindings."""

    root_keys = {
        "schema", "kind", "candidate_inputs_consulted", "family_id",
        "family_input_sha256", "source_snapshot_sha256",
        "region_manifest_sha256", "contracts_sha256", "caps",
        "qc_inventory", "definitions", "regions", "catalog_sha256",
    }
    root = _catalog_object(payload, root_keys, "region animation catalog")
    if (
        root["schema"] != 1
        or root["kind"] != "exact-qc-region-animation-catalog-v1"
        or root["candidate_inputs_consulted"] is not False
    ):
        raise ValueError("region animation catalog contract differs")
    family_id = _catalog_text(root["family_id"], "catalog family id")
    for field in (
        "family_input_sha256", "source_snapshot_sha256",
        "region_manifest_sha256", "contracts_sha256", "catalog_sha256",
    ):
        _sha(root[field], f"catalog {field}")

    cap_keys = {
        "max_animation_definitions", "max_pairs_per_shard", "max_regions",
        "max_weightlists", "caps_sha256",
    }
    cap_payload = _catalog_object(root["caps"], cap_keys, "catalog caps")
    caps = RegionAnimationCatalogCaps(
        max_regions=cap_payload["max_regions"],
        max_pairs_per_shard=cap_payload["max_pairs_per_shard"],
        max_animation_definitions=cap_payload["max_animation_definitions"],
        max_weightlists=cap_payload["max_weightlists"],
    )
    if caps.to_payload() != dict(cap_payload):
        raise ValueError("catalog caps seal differs")

    qc_inventory = _catalog_list(
        root["qc_inventory"], maximum=caps.max_animation_definitions,
        label="catalog QC inventory",
    )
    qc_paths = []
    for index, item in enumerate(qc_inventory):
        proof = _validate_file_payload(item, f"catalog QC inventory[{index}]")
        qc_paths.append(proof["path"])
    if len(qc_paths) != len(set(qc_paths)):
        raise ValueError("catalog QC inventory duplicates")

    definition_keys = {
        "action_name", "action_path", "action_sha256", "corrective_name",
        "corrective_path", "corrective_sha256", "graph_file", "line",
        "source_ordinal", "subtract_frame", "weightlist_name",
        "weightlist_positive_bones",
    }
    definitions = _catalog_list(
        root["definitions"], maximum=caps.max_animation_definitions,
        label="catalog definitions",
    )
    pair_proofs: dict[tuple[str, str], tuple[str, str]] = {}
    definition_order = []
    for index, item in enumerate(definitions):
        definition = _catalog_object(item, definition_keys, f"catalog definition[{index}]")
        for field in ("action_name", "corrective_name", "weightlist_name"):
            _catalog_text(definition[field], f"catalog definition[{index}].{field}")
        action_path = _catalog_path(definition["action_path"], "catalog action path")
        corrective_path = _catalog_path(definition["corrective_path"], "catalog corrective path")
        graph_file = _catalog_path(definition["graph_file"], "catalog graph path")
        if graph_file not in qc_paths:
            raise ValueError("catalog definition graph is absent from QC inventory")
        action_sha = _sha(definition["action_sha256"], "catalog action SHA")
        corrective_sha = _sha(definition["corrective_sha256"], "catalog corrective SHA")
        if (
            type(definition["line"]) is not int or definition["line"] < 1
            or type(definition["source_ordinal"]) is not int
            or definition["source_ordinal"] < 0
            or type(definition["subtract_frame"]) is not int
            or definition["subtract_frame"] < 0
        ):
            raise ValueError("catalog definition ordinal/frame is invalid")
        bones = _catalog_list(
            definition["weightlist_positive_bones"], maximum=4096,
            label="catalog weightlist bones",
        )
        folded_bones = []
        for bone in bones:
            folded_bones.append(_catalog_text(bone, "catalog weightlist bone").casefold())
        if not bones or len(folded_bones) != len(set(folded_bones)):
            raise ValueError("catalog weightlist bones are empty or ambiguous")
        identity = (action_path.casefold(), corrective_path.casefold())
        if identity in pair_proofs:
            raise ValueError("catalog definition pair duplicates")
        pair_proofs[identity] = (action_sha, corrective_sha)
        definition_order.append((action_path.casefold(), action_path, corrective_path.casefold(), corrective_path))
    if definition_order != sorted(definition_order):
        raise ValueError("catalog definitions are not canonical")

    region_keys = {
        "geometry", "lineage_bones", "mode", "region_key",
        "related_pair_count", "selection_payload", "shards",
        "source_identity", "proof_sha256",
    }
    shard_keys = {"ordinal", "pair_count", "pair_inventory", "selection_payload", "proof_sha256"}
    pair_keys = {"action_path", "action_sha256", "corrective_path", "corrective_sha256"}
    regions = _catalog_list(root["regions"], maximum=caps.max_regions, label="catalog regions")
    if not regions:
        raise ValueError("catalog has no regions")
    region_names = []
    for region_index, item in enumerate(regions):
        region = _catalog_object(item, region_keys, f"catalog region[{region_index}]")
        region_name = _catalog_text(region["region_key"], "catalog region key")
        region_names.append(region_name)
        geometry = _validate_file_payload(region["geometry"], "catalog region geometry")
        source_identity = _catalog_path(region["source_identity"], "catalog source identity")
        if geometry["path"] != source_identity:
            raise ValueError("catalog region geometry/source identity differs")
        lineage = _catalog_list(region["lineage_bones"], maximum=4096, label="catalog region lineage")
        lineage_names = [_catalog_text(name, "catalog lineage bone") for name in lineage]
        if (
            not lineage_names
            or lineage_names != sorted(set(lineage_names), key=lambda name: (name.casefold(), name))
        ):
            raise ValueError("catalog region lineage is not canonical unique")
        if type(region["related_pair_count"]) is not int or not 0 <= region["related_pair_count"] <= len(definitions):
            raise ValueError("catalog related pair count is invalid")
        mode = region["mode"]
        if mode not in {"bind-animation", "bind-only/empty-relation", "bind-only/all-shards-zero"}:
            raise ValueError("catalog region mode differs")
        selection = None if region["selection_payload"] is None else _selection_payload(region["selection_payload"])
        shards = _catalog_list(
            region["shards"], maximum=(len(definitions) + 63) // 64,
            label="catalog region shards",
        )
        seen_pairs = set()
        winners = []
        total_pairs = 0
        for shard_index, raw_shard in enumerate(shards):
            shard = _catalog_object(raw_shard, shard_keys, f"catalog shard[{shard_index}]")
            if shard["ordinal"] != shard_index:
                raise ValueError("catalog shard ordinal differs")
            inventory = _catalog_list(
                shard["pair_inventory"], maximum=caps.max_pairs_per_shard,
                label="catalog shard pair inventory",
            )
            if shard["pair_count"] != len(inventory) or not inventory:
                raise ValueError("catalog shard pair count differs")
            total_pairs += len(inventory)
            shard_pairs = set()
            for raw_pair in inventory:
                pair = _catalog_object(raw_pair, pair_keys, "catalog shard pair")
                action_path = _catalog_path(pair["action_path"], "catalog shard action path")
                corrective_path = _catalog_path(pair["corrective_path"], "catalog shard corrective path")
                identity = (action_path.casefold(), corrective_path.casefold())
                hashes = (
                    _sha(pair["action_sha256"], "catalog shard action SHA"),
                    _sha(pair["corrective_sha256"], "catalog shard corrective SHA"),
                )
                if identity in seen_pairs or pair_proofs.get(identity) != hashes:
                    raise ValueError("catalog shard pair proof differs")
                seen_pairs.add(identity)
                shard_pairs.add(identity)
            shard_selection = _selection_payload(shard["selection_payload"])
            if _animated(shard_selection):
                selected_identity = (
                    str(shard_selection["animation_relative_path"]).casefold(),
                    str(shard_selection["reference_relative_path"]).casefold(),
                )
                if selected_identity not in shard_pairs:
                    raise ValueError("catalog shard selection is outside its inventory")
                winners.append(shard_selection)
            unsigned_shard = {key: value for key, value in shard.items() if key != "proof_sha256"}
            if _sha(shard["proof_sha256"], "catalog shard proof") != _digest(unsigned_shard):
                raise ValueError("catalog shard seal differs")
        if total_pairs != region["related_pair_count"]:
            raise ValueError("catalog region/shard pair count differs")
        if mode == "bind-only/empty-relation":
            if total_pairs or shards or selection is not None:
                raise ValueError("catalog empty relation carries animation evidence")
        elif mode == "bind-only/all-shards-zero":
            if not total_pairs or winners or selection is not None:
                raise ValueError("catalog zero-shard decision differs")
        else:
            if not winners or selection is None or not _animated(selection):
                raise ValueError("catalog animated decision lacks a winner")
            expected = min(winners, key=_selection_rank)
            if _selection_rank(selection) != _selection_rank(expected):
                raise ValueError("catalog global winner differs")
        unsigned_region = {key: value for key, value in region.items() if key != "proof_sha256"}
        if _sha(region["proof_sha256"], "catalog region proof") != _digest(unsigned_region):
            raise ValueError("catalog region seal differs")
    if len(region_names) != len(set(region_names)):
        raise ValueError("catalog region keys duplicate")

    unsigned_root = {key: value for key, value in root.items() if key != "catalog_sha256"}
    if root["catalog_sha256"] != _digest(unsigned_root):
        raise ValueError("catalog root seal differs")
    for value, label in (
        (expected_catalog_sha256, "expected catalog"),
        (expected_family_input_sha256, "expected family input"),
        (expected_source_snapshot_sha256, "expected source snapshot"),
        (expected_region_manifest_sha256, "expected region manifest"),
        (expected_contracts_sha256, "expected contracts"),
        (expected_caps_sha256, "expected caps"),
    ):
        _sha(value, label)
    if type(expected_family_id) is not str or not expected_family_id:
        raise ValueError("expected family id is invalid")
    actual = (
        root["catalog_sha256"], family_id, root["family_input_sha256"],
        root["source_snapshot_sha256"], root["region_manifest_sha256"],
        root["contracts_sha256"], cap_payload["caps_sha256"],
    )
    expected = (
        expected_catalog_sha256, expected_family_id, expected_family_input_sha256,
        expected_source_snapshot_sha256, expected_region_manifest_sha256,
        expected_contracts_sha256, expected_caps_sha256,
    )
    if actual != expected:
        raise ValueError("external region animation catalog binding differs")
    return RegionAnimationCatalog(root)
