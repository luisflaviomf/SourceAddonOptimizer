"""Strict external-binding contract for canonical region/pose producer proofs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping


_MAX_REGIONS = 64
_MAX_TEXT = 4096
_MAX_ANIMATIONS = 64
_MAX_BONES = 4096
_MAX_CURVES = 100_000
_MAX_FRAMES = 4096
_MAX_TOOL_FILES = 16
_HEX = frozenset("0123456789abcdef")
_MODES = {
    "bind-animation", "bind-only/no-eligible-animation",
    "bind-only/no-influenced-bone-delta", "bind-only/rigid-region",
    "bind-only/no-visible-displacement",
}
_ROOT_KEYS = {
    "schema_version", "kind", "status", "family", "region", "contracts",
    "selector_decision", "action_proof", "evaluated_region_proof",
    "pixel_evidence", "caps", "evidence_sha256",
}
_FAMILY_KEYS = {
    "family_id", "family_input_sha256", "source_snapshot_sha256",
    "control_snapshot_sha256",
}
_REGION_KEYS = {"region_key", "region_manifest_sha256"}
_CONTRACT_KEYS = {
    "contract_kind", "renderer_contract_sha256", "action_contract_sha256",
    "pixel_gate_contract_sha256", "toolchain_sha256", "contracts_sha256",
}
_CAP_KEYS = {
    "max_regions_per_family", "required_view_count", "required_width",
    "required_height", "max_total_pixels", "minimum_changed_fraction",
    "minimum_silhouette_pixels", "caps_sha256",
}
_DECISION_KEYS = {
    "mode", "selection_payload", "attempted_selection_payload",
    "decision_proof", "decision_sha256",
}
_ANIMATION_SELECTION_KEYS = {
    "animation_relative_path", "animation_sha256", "baseline", "bone_index",
    "bone_name", "candidate_inputs_consulted", "corrective_used_as_baseline",
    "displacement", "displacement_squared", "frame",
    "geometry_inventory_sha256", "pose_name", "reference_relative_path",
    "reference_sha256", "raw_render_max_displacement",
    "raw_render_rms_displacement", "rms_displacement", "selection_sha256",
    "selector", "selector_input_sha256", "source_time",
}
_BIND_SELECTION_KEYS = {
    "candidate_inputs_consulted", "geometry_inventory_sha256", "pose_keys",
    "reason", "selection_sha256", "selector", "selector_input_sha256",
}
_REASON_KEYS = {
    "kind", "animation_inventory", "geometry_inventory_sha256",
    "region_manifest_sha256", "attempted_selection_sha256",
    "evaluated_region_proof_sha256", "pixel_evidence_sha256", "proof_sha256",
}
_ANIMATION_ITEM_KEYS = {
    "animation_relative_path", "animation_sha256", "reference_relative_path",
    "reference_sha256",
}
_ACTION_KEYS = {
    "animation_input_sha256", "bone_lineage", "curves", "slot_name",
    "source_times", "toolchain", "action_sha256",
}
_TOOLCHAIN_KEYS = {
    "blender_version", "files", "kind", "toolchain_sha256",
}
_TOOL_FILE_KEYS = {"path", "size", "sha256"}
_BONE_KEYS = {"id", "name", "parent"}
_CURVE_KEYS = {"array_index", "data_path", "keyframes"}
_EVALUATED_KEYS = {
    "action_sha256", "animation_input_sha256", "baseline",
    "bind_geometry_sha256", "candidate_inputs_consulted",
    "corrective_used_as_baseline", "frame", "influenced_bones", "kind",
    "maximum_displacement", "moved_vertex_count", "posed_geometry_sha256",
    "proof_sha256", "rms_displacement", "selected_bone", "source_time",
    "toolchain_sha256", "vertex_count",
}
_PIXEL_EVIDENCE_KEYS = {
    "kind", "first", "repeat", "deterministic",
    "canonical_orthogonal_pair", "selection_sha256", "action_sha256",
    "evaluated_region_proof_sha256", "region_manifest_sha256",
    "toolchain_sha256", "caps_sha256", "evidence_sha256",
}
_PIXEL_PUBLIC_KEYS = {
    "bind_pixel_bundle_sha256", "changed_fraction", "changed_pixels",
    "evidence_sha256", "foreground_pixels", "image_count",
    "mean_absolute_error", "minimum_changed_fraction",
    "minimum_silhouette_pixels", "posed_pixel_bundle_sha256", "total_pixels",
    "views", "camera_directions", "qualified_silhouette_views",
}
_PIXEL_MEASUREMENT_KEYS = _PIXEL_PUBLIC_KEYS | {"kind"}
_PIXEL_VIEW_KEYS = {
    "changed_fraction", "changed_pixels", "foreground_pixels", "height",
    "key", "mean_absolute_error", "width",
}
_CAMERA_KEYS = {"key", "direction"}
_BUNDLE_KEYS = {
    "schema_version", "kind", "status", "family_common",
    "expected_region_keys", "entries", "bundle_sha256",
}
_COMMON_KEYS = {
    "family_id", "family_input_sha256", "source_snapshot_sha256",
    "control_snapshot_sha256", "contracts_sha256", "toolchain_sha256",
    "caps_sha256",
}


def _thaw(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _freeze(value: object) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _thaw(value), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("region pose proof must be canonical JSON data") from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _copy(value: object) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def canonical_region_pose_evidence_hash(payload: Mapping[str, Any]) -> str:
    return _hash({key: item for key, item in payload.items() if key != "evidence_sha256"})


def canonical_region_pose_family_bundle_hash(payload: Mapping[str, Any]) -> str:
    return _hash({key: item for key, item in payload.items() if key != "bundle_sha256"})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _mapping(value: object, keys: set[str], name: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{name} must be an object")
    _require(len(value) == len(keys) and set(value) == keys, f"{name} schema is invalid")
    return value


def _sequence(value: object, name: str, *, maximum: int, minimum: int = 0) -> list[Any] | tuple[Any, ...]:
    _require(isinstance(value, (list, tuple)), f"{name} must be a bounded list")
    _require(minimum <= len(value) <= maximum, f"{name} count is outside its cap")
    return value


def _bounded_tuple(value: Iterable[Any], name: str, maximum: int) -> tuple[Any, ...]:
    _require(not isinstance(value, (str, bytes, Mapping)), f"{name} must be an iterable")
    try:
        items = tuple(itertools.islice(iter(value), maximum + 1))
    except TypeError as exc:
        raise ValueError(f"{name} must be an iterable") from exc
    _require(len(items) <= maximum, f"{name} exceeds its cap")
    return items


def _text(value: object, name: str) -> str:
    _require(
        isinstance(value, str) and 0 < len(value) <= _MAX_TEXT
        and value == value.strip() and not any(ord(char) < 32 for char in value),
        f"{name} is invalid text",
    )
    return value


def _sha(value: object, name: str) -> str:
    _require(
        isinstance(value, str) and len(value) == 64
        and all(char in _HEX for char in value), f"{name} is not lowercase SHA-256",
    )
    return value


def _integer(value: object, name: str, minimum: int = 0, maximum: int | None = None) -> int:
    _require(type(value) is int and value >= minimum and (maximum is None or value <= maximum), f"{name} is invalid")
    return value


def _signed_integer(value: object, name: str) -> int:
    _require(
        type(value) is int and -(2 ** 31) <= value < 2 ** 31,
        f"{name} is invalid",
    )
    return value


def _number(value: object, name: str, *, minimum: float = 0.0, positive: bool = False) -> float:
    _require(type(value) in (int, float) and math.isfinite(float(value)), f"{name} is non-finite")
    result = float(value)
    _require(result > minimum if positive else result >= minimum, f"{name} is invalid")
    return result


def _validate_family(value: object) -> Mapping[str, Any]:
    result = _mapping(value, _FAMILY_KEYS, "family")
    _text(result["family_id"], "family id")
    for field in _FAMILY_KEYS - {"family_id"}:
        _sha(result[field], f"family.{field}")
    return result


def _validate_region(value: object) -> Mapping[str, Any]:
    result = _mapping(value, _REGION_KEYS, "region")
    _text(result["region_key"], "region key")
    _sha(result["region_manifest_sha256"], "region manifest")
    return result


def _validate_contracts(value: object) -> Mapping[str, Any]:
    result = _mapping(value, _CONTRACT_KEYS, "contracts")
    _require(result["contract_kind"] == "exact-region-pose-contracts-v1", "contracts kind differs")
    for field in _CONTRACT_KEYS - {"contract_kind"}:
        _sha(result[field], f"contracts.{field}")
    unsigned = {key: item for key, item in result.items() if key != "contracts_sha256"}
    _require(result["contracts_sha256"] == _hash(unsigned), "contracts seal differs")
    return result


def _validate_caps(value: object) -> Mapping[str, Any]:
    result = _mapping(value, _CAP_KEYS, "caps")
    _require(
        result["max_regions_per_family"] == 64
        and result["required_view_count"] == 8
        and result["required_width"] == result["required_height"] == 512
        and result["max_total_pixels"] == 8 * 512 * 512,
        "region/pixel inventory caps differ",
    )
    minimum = _number(result["minimum_changed_fraction"], "minimum changed fraction", positive=True)
    _require(minimum <= 1.0, "minimum changed fraction exceeds one")
    _integer(result["minimum_silhouette_pixels"], "minimum silhouette pixels", 1, 512 * 512)
    _sha(result["caps_sha256"], "caps seal")
    unsigned = {key: item for key, item in result.items() if key != "caps_sha256"}
    _require(result["caps_sha256"] == _hash(unsigned), "caps seal differs")
    return result


def _validate_animation_selection(value: object, name: str) -> Mapping[str, Any]:
    result = _mapping(value, _ANIMATION_SELECTION_KEYS, name)
    _require(
        result["selector"] == "exact-raw-render-region-displacement-v3"
        and result["baseline"] == "geometry-rest"
        and result["candidate_inputs_consulted"] is False
        and result["corrective_used_as_baseline"] is False,
        f"{name} public selector contract differs",
    )
    for field in ("animation_relative_path", "bone_name", "pose_name", "reference_relative_path"):
        _text(result[field], f"{name}.{field}")
    for field in ("animation_relative_path", "reference_relative_path"):
        path = PurePosixPath(result[field])
        _require(
            not path.is_absolute() and "\\" not in result[field]
            and all(part not in {"", ".", ".."} for part in path.parts),
            f"{name}.{field} is not a canonical relative path",
        )
    _require(
        result["pose_name"]
        == PurePosixPath(result["animation_relative_path"]).stem.casefold(),
        f"{name} pose name differs from animation path",
    )
    for field in (
        "animation_sha256", "reference_sha256", "geometry_inventory_sha256",
        "selector_input_sha256", "selection_sha256",
    ):
        _sha(result[field], f"{name}.{field}")
    for field in ("bone_index", "frame"):
        _integer(result[field], f"{name}.{field}")
    _signed_integer(result["source_time"], f"{name}.source_time")
    displacement = _number(result["displacement"], f"{name}.displacement", positive=True)
    maximum = _number(result["raw_render_max_displacement"], f"{name}.raw max", positive=True)
    rms = _number(result["rms_displacement"], f"{name}.rms", positive=True)
    raw_rms = _number(result["raw_render_rms_displacement"], f"{name}.raw rms", positive=True)
    _require(
        displacement == maximum and rms == raw_rms and rms <= maximum
        and displacement > 1e-12 and rms > 1e-12,
        f"{name} displacement fields differ or are below threshold",
    )
    _require(isinstance(result["displacement_squared"], str), f"{name} squared displacement is invalid")
    try:
        squared = float(result["displacement_squared"])
    except ValueError as exc:
        raise ValueError(f"{name} squared displacement is invalid") from exc
    _require(math.isfinite(squared) and math.isclose(squared, displacement * displacement, rel_tol=1e-12), f"{name} squared displacement differs")
    unsigned = {key: item for key, item in result.items() if key != "selection_sha256"}
    _require(result["selection_sha256"] == _hash(unsigned), f"{name} public selection seal differs")
    return result


def _validate_bind_selection(value: object, name: str) -> Mapping[str, Any]:
    result = _mapping(value, _BIND_SELECTION_KEYS, name)
    _require(
        result["candidate_inputs_consulted"] is False
        and result["selector"] == "exact-raw-render-region-displacement-v3"
        and result["pose_keys"] == ["bind"]
        and result["reason"] == "no-exact-region-influencing-animation-displacement",
        f"{name} public bind-only contract differs",
    )
    for field in ("geometry_inventory_sha256", "selector_input_sha256", "selection_sha256"):
        _sha(result[field], f"{name}.{field}")
    unsigned = {key: item for key, item in result.items() if key != "selection_sha256"}
    _require(result["selection_sha256"] == _hash(unsigned), f"{name} public bind seal differs")
    return result


def _validate_animation_inventory(value: object, name: str) -> list[Mapping[str, Any]]:
    raw = _sequence(value, name, maximum=_MAX_ANIMATIONS)
    result = []
    order = []
    for index, item in enumerate(raw):
        parsed = _mapping(item, _ANIMATION_ITEM_KEYS, f"{name}[{index}]")
        for field in ("animation_relative_path", "reference_relative_path"):
            _text(parsed[field], f"{name}[{index}].{field}")
        for field in ("animation_sha256", "reference_sha256"):
            _sha(parsed[field], f"{name}[{index}].{field}")
        identity = (parsed["animation_relative_path"].casefold(), parsed["animation_relative_path"], parsed["reference_relative_path"])
        order.append(identity)
        result.append(parsed)
    _require(order == sorted(order) and len(order) == len(set(order)), f"{name} is not canonical unique")
    return result


def _validate_reason(
    value: object, *, mode: str, bind: Mapping[str, Any], attempted: Mapping[str, Any] | None,
    region: Mapping[str, Any], evaluated: Mapping[str, Any] | None,
    pixels: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    result = _mapping(value, _REASON_KEYS, "selector decision proof")
    expected_kind = mode.removeprefix("bind-only/") + "-proof-v1"
    _require(result["kind"] == expected_kind, "bind-only reason proof kind differs")
    inventory = _validate_animation_inventory(result["animation_inventory"], "reason animation inventory")
    _require(result["geometry_inventory_sha256"] == bind["geometry_inventory_sha256"], "reason geometry inventory differs")
    _sha(result["geometry_inventory_sha256"], "reason geometry inventory")
    if mode == "bind-only/no-eligible-animation":
        _require(not inventory, "no-eligible reason must carry an empty inventory")
    elif mode == "bind-only/no-influenced-bone-delta":
        _require(bool(inventory), "no-influenced reason must carry consulted source inventory")
    elif mode == "bind-only/rigid-region":
        _require(not inventory and result["region_manifest_sha256"] == region["region_manifest_sha256"], "rigid reason proof differs")
        _sha(result["region_manifest_sha256"], "rigid region manifest")
    elif mode == "bind-only/no-visible-displacement":
        _require(bool(inventory) and attempted is not None and evaluated is not None and pixels is not None, "no-visible reason lacks attempted source proof")
        _require(
            result["attempted_selection_sha256"] == attempted["selection_sha256"]
            and result["evaluated_region_proof_sha256"] == evaluated["proof_sha256"]
            and result["pixel_evidence_sha256"] == pixels["evidence_sha256"],
            "no-visible source proof binding differs",
        )
        for field in ("attempted_selection_sha256", "evaluated_region_proof_sha256", "pixel_evidence_sha256"):
            _sha(result[field], f"reason.{field}")
    if mode != "bind-only/rigid-region":
        _require(result["region_manifest_sha256"] is None, "non-rigid reason carries region proof")
    if mode != "bind-only/no-visible-displacement":
        _require(
            result["attempted_selection_sha256"] is None
            and result["evaluated_region_proof_sha256"] is None
            and result["pixel_evidence_sha256"] is None,
            "bind-only reason carries unrelated source proof",
        )
    _sha(result["proof_sha256"], "reason proof seal")
    unsigned = {key: item for key, item in result.items() if key != "proof_sha256"}
    _require(result["proof_sha256"] == _hash(unsigned), "reason proof seal differs")
    return result


def _validate_toolchain(value: object) -> Mapping[str, Any]:
    result = _mapping(value, _TOOLCHAIN_KEYS, "action toolchain")
    _require(result["kind"] == "blender5-source-tools-action-runtime-v1", "action toolchain kind differs")
    version = _sequence(result["blender_version"], "Blender version", maximum=3, minimum=3)
    _require(all(type(item) is int and item >= 0 for item in version), "Blender version differs")
    files = _sequence(result["files"], "action tool files", maximum=_MAX_TOOL_FILES, minimum=1)
    paths = []
    for index, raw in enumerate(files):
        item = _mapping(raw, _TOOL_FILE_KEYS, f"action tool files[{index}]")
        paths.append(_text(item["path"], "tool path"))
        _integer(item["size"], "tool size", 1, 1024 * 1024 * 1024)
        _sha(item["sha256"], "tool file hash")
    _require(
        len(paths) == len(set(paths))
        and paths == sorted(paths, key=lambda path: (path.casefold(), path)),
        "action tool file inventory is not canonical unique",
    )
    _sha(result["toolchain_sha256"], "action toolchain seal")
    unsigned = {key: item for key, item in result.items() if key != "toolchain_sha256"}
    _require(result["toolchain_sha256"] == _hash(unsigned), "action toolchain seal differs")
    return result


def _validate_action(value: object, *, selection: Mapping[str, Any], contracts: Mapping[str, Any]) -> Mapping[str, Any]:
    result = _mapping(value, _ACTION_KEYS, "action proof")
    _sha(result["animation_input_sha256"], "action animation input")
    _sha(result["action_sha256"], "action seal")
    _text(result["slot_name"], "action slot name")
    _require(
        result["slot_name"].casefold() == selection["pose_name"],
        "action slot stem differs from selected pose",
    )
    toolchain = _validate_toolchain(result["toolchain"])
    _require(toolchain["toolchain_sha256"] == contracts["toolchain_sha256"], "action/contracts toolchain differs")
    bones = _sequence(result["bone_lineage"], "action bone lineage", maximum=_MAX_BONES, minimum=1)
    bone_ids = []
    bone_parents = []
    bone_names = set()
    for index, raw in enumerate(bones):
        bone = _mapping(raw, _BONE_KEYS, f"action bone lineage[{index}]")
        identity = _integer(bone["id"], "bone id")
        parent = _integer(bone["parent"], "bone parent", -1)
        name = _text(bone["name"], "bone name")
        _require(name not in bone_names, "action bone names duplicate")
        bone_ids.append(identity); bone_parents.append(parent); bone_names.add(name)
    _require(bone_ids == sorted(set(bone_ids)), "action bone ids are not canonical")
    _require(
        all(parent == -1 or parent in set(bone_ids) for parent in bone_parents),
        "action bone parent lineage differs",
    )
    source_times = _sequence(result["source_times"], "action source times", maximum=_MAX_FRAMES, minimum=1)
    for item in source_times:
        _signed_integer(item, "action source time")
    _require(len(source_times) == len(set(source_times)), "action source times duplicate")
    curves = _sequence(result["curves"], "action curves", maximum=_MAX_CURVES, minimum=1)
    curve_order = []
    keyed_frames = set()
    for index, raw in enumerate(curves):
        curve = _mapping(raw, _CURVE_KEYS, f"action curves[{index}]")
        path = _text(curve["data_path"], "curve path")
        array = _integer(curve["array_index"], "curve array index", 0, 3)
        match = re.fullmatch(r'pose\.bones\["([^"]+)"\]\.(location|rotation_euler|rotation_quaternion|scale)', path)
        _require(match is not None and match.group(1) in bone_names, "action curve bone path differs")
        maximum_index = 3 if match.group(2) == "rotation_quaternion" else 2
        _require(array <= maximum_index, "action curve array index differs from channel")
        curve_order.append((path, array))
        points = _sequence(curve["keyframes"], "action keyframes", maximum=_MAX_FRAMES, minimum=1)
        frames = []
        for point in points:
            pair = _sequence(point, "action keyframe", maximum=2, minimum=2)
            frame = _number(pair[0], "action keyframe ordinal")
            _number(pair[1], "action keyframe value", minimum=-math.inf)
            _require(frame == int(frame) and 0 <= int(frame) < len(source_times), "action keyframe ordinal differs")
            frames.append(int(frame))
        _require(frames == sorted(set(frames)), "action keyframe order/uniqueness differs")
        keyed_frames.update(frames)
    _require(curve_order == sorted(set(curve_order)), "action curves are not canonical unique")
    _require(
        result["animation_input_sha256"] == selection["animation_sha256"]
        and selection["frame"] < len(source_times)
        and source_times[selection["frame"]] == selection["source_time"],
        "action/selection source frame binding differs",
    )
    _require(selection["frame"] in keyed_frames, "selected action frame is not keyed")
    lineage_by_id = {item["id"]: item["name"] for item in bones}
    _require(
        lineage_by_id.get(selection["bone_index"]) == selection["bone_name"],
        "selected bone id/name differs from action lineage",
    )
    unsigned = {key: item for key, item in result.items() if key != "action_sha256"}
    _require(result["action_sha256"] == _hash(unsigned), "public action seal differs")
    return result


def _validate_evaluated(
    value: object, *, selection: Mapping[str, Any], action: Mapping[str, Any],
    contracts: Mapping[str, Any],
) -> Mapping[str, Any]:
    result = _mapping(value, _EVALUATED_KEYS, "evaluated region proof")
    _require(
        result["kind"] == "blender-evaluated-region-pose-delta-v1"
        and result["baseline"] == "armature-rest"
        and result["candidate_inputs_consulted"] is False
        and result["corrective_used_as_baseline"] is False,
        "evaluated region typed contract differs",
    )
    for field in (
        "action_sha256", "animation_input_sha256", "bind_geometry_sha256",
        "posed_geometry_sha256", "toolchain_sha256", "proof_sha256",
    ):
        _sha(result[field], f"evaluated.{field}")
    _require(result["bind_geometry_sha256"] != result["posed_geometry_sha256"], "evaluated bind/pose hashes are equal")
    frame = _integer(result["frame"], "evaluated frame")
    source_time = _signed_integer(result["source_time"], "evaluated source time")
    vertex_count = _integer(result["vertex_count"], "evaluated vertex count", 1, 100_000_000)
    moved = _integer(result["moved_vertex_count"], "evaluated moved vertex count", 1, vertex_count)
    maximum = _number(result["maximum_displacement"], "evaluated maximum", positive=True)
    rms = _number(result["rms_displacement"], "evaluated RMS", positive=True)
    _require(
        rms <= maximum and moved <= vertex_count
        and maximum > 1e-12 and rms > 1e-12,
        "evaluated counts/displacement differ or are below threshold",
    )
    influenced = _sequence(result["influenced_bones"], "evaluated influenced bones", maximum=_MAX_BONES, minimum=1)
    _require(all(isinstance(item, str) and item for item in influenced), "evaluated influenced bone invalid")
    _require(tuple(influenced) == tuple(sorted(set(influenced), key=lambda name: (name.casefold(), name))), "evaluated influenced bones are not canonical")
    action_bones = {item["name"] for item in action["bone_lineage"]}
    _require(set(influenced) <= action_bones, "evaluated influenced bone is absent from action lineage")
    selected_bone = _text(result["selected_bone"], "evaluated selected bone")
    _require(selected_bone in influenced and selected_bone == selection["bone_name"], "evaluated selected/influenced bone differs")
    ids_by_name = {item["name"]: item["id"] for item in action["bone_lineage"]}
    parents = {item["id"]: item["parent"] for item in action["bone_lineage"]}
    selected_id = ids_by_name[selected_bone]
    descendants = set()
    for name, identity in ids_by_name.items():
        cursor = identity
        visited = set()
        while cursor != -1 and cursor not in visited:
            if cursor == selected_id:
                descendants.add(name)
                break
            visited.add(cursor)
            cursor = parents.get(cursor, -1)
    _require(
        set(influenced) <= descendants,
        "evaluated influenced bone is not selected or descendant",
    )
    _require(
        result["action_sha256"] == action["action_sha256"]
        and result["animation_input_sha256"] == selection["animation_sha256"]
        and result["toolchain_sha256"] == contracts["toolchain_sha256"]
        and frame == selection["frame"] and source_time == selection["source_time"],
        "evaluated selection/action/toolchain binding differs",
    )
    unsigned = {key: item for key, item in result.items() if key != "proof_sha256"}
    _require(result["proof_sha256"] == _hash(unsigned), "public evaluated region seal differs")
    return result


def _validate_pixel_public(
    value: object, *, caps: Mapping[str, Any], name: str,
    measurement: bool = False,
) -> tuple[Mapping[str, Any], dict[str, tuple[float, float, float]]]:
    result = _mapping(
        value, _PIXEL_MEASUREMENT_KEYS if measurement else _PIXEL_PUBLIC_KEYS, name,
    )
    if measurement:
        _require(result["kind"] == "pose-pixel-measurement-v1", f"{name} kind differs")
    for field in ("bind_pixel_bundle_sha256", "posed_pixel_bundle_sha256", "evidence_sha256"):
        _sha(result[field], f"{name}.{field}")
    _require(result["bind_pixel_bundle_sha256"] != result["posed_pixel_bundle_sha256"], f"{name} bind/posed bundle is equal")
    _require(result["image_count"] == caps["required_view_count"] == 8 and result["total_pixels"] == caps["max_total_pixels"], f"{name} image/pixel inventory differs")
    _require(result["minimum_changed_fraction"] == caps["minimum_changed_fraction"] and result["minimum_silhouette_pixels"] == caps["minimum_silhouette_pixels"], f"{name} threshold binding differs")
    views = _sequence(result["views"], f"{name}.views", maximum=8, minimum=8)
    cameras = _sequence(result["camera_directions"], f"{name}.camera_directions", maximum=8, minimum=8)
    view_keys = []
    foreground_total = changed_total = 0
    error_weight = 0.0
    computed_qualified = []
    for index, raw in enumerate(views):
        view = _mapping(raw, _PIXEL_VIEW_KEYS, f"{name}.views[{index}]")
        key = _text(view["key"], "pixel view key"); view_keys.append(key)
        _require(view["width"] == view["height"] == 512, f"{name} view is not 512x512")
        foreground = _integer(view["foreground_pixels"], "pixel view foreground", 1, 512 * 512)
        changed = _integer(view["changed_pixels"], "pixel view changed", 0, foreground)
        fraction = _number(view["changed_fraction"], "pixel changed fraction")
        mae = _number(view["mean_absolute_error"], "pixel view MAE")
        _require(math.isclose(fraction, changed / foreground, rel_tol=1e-12, abs_tol=1e-15), f"{name} view fraction differs")
        qualifies = changed >= caps["minimum_silhouette_pixels"] and fraction >= caps["minimum_changed_fraction"]
        if qualifies:
            computed_qualified.append(key)
        foreground_total += foreground; changed_total += changed; error_weight += mae * foreground
    _require(view_keys == sorted(set(view_keys)), f"{name} view keys are not canonical unique")
    directions = {}
    camera_keys = []
    for index, raw in enumerate(cameras):
        camera = _mapping(raw, _CAMERA_KEYS, f"{name}.camera_directions[{index}]")
        key = _text(camera["key"], "camera key"); camera_keys.append(key)
        vector = _sequence(camera["direction"], "camera direction", maximum=3, minimum=3)
        _require(all(type(item) in (int, float) and math.isfinite(float(item)) for item in vector), "camera direction is non-finite")
        direction = tuple(float(item) for item in vector)
        length = math.sqrt(sum(item * item for item in direction))
        _require(math.isclose(length, 1.0, rel_tol=1e-12, abs_tol=1e-12), "camera direction is not normalized")
        directions[key] = direction
    _require(camera_keys == view_keys, f"{name} camera/view inventory differs")
    _require(result["qualified_silhouette_views"] == computed_qualified, f"{name} qualified view inventory differs")
    _require(result["foreground_pixels"] == foreground_total and result["changed_pixels"] == changed_total, f"{name} aggregate pixel counts differ")
    _require(math.isclose(float(result["changed_fraction"]), changed_total / foreground_total, rel_tol=1e-12), f"{name} aggregate changed fraction differs")
    _require(math.isclose(float(result["mean_absolute_error"]), error_weight / foreground_total, rel_tol=1e-12), f"{name} aggregate MAE differs")
    unsigned = {key: item for key, item in result.items() if key != "evidence_sha256"}
    _require(result["evidence_sha256"] == _hash(unsigned), f"{name} public pixel seal differs")
    return result, directions


def _validate_pixel_evidence(value: object, *, caps: Mapping[str, Any]) -> Mapping[str, Any]:
    result = _mapping(value, _PIXEL_EVIDENCE_KEYS, "pixel evidence")
    positive = result["kind"] == "pose-pixel-family-evidence-v1"
    no_visible = result["kind"] == "pose-pixel-no-visible-evidence-v1"
    _require((positive or no_visible) and result["deterministic"] is True, "pixel family evidence contract differs")
    first, directions = _validate_pixel_public(
        result["first"], caps=caps, name="pixel first", measurement=no_visible,
    )
    repeat, repeat_directions = _validate_pixel_public(
        result["repeat"], caps=caps, name="pixel repeat", measurement=no_visible,
    )
    _require(first == repeat and directions == repeat_directions, "pixel first/repeat evidence differs")
    qualified = tuple(first["qualified_silhouette_views"])
    candidates = sorted(
        (left, right)
        for index, left in enumerate(qualified)
        for right in qualified[index + 1:]
        if abs(sum(a * b for a, b in zip(directions[left], directions[right]))) <= 0.25
    )
    if positive:
        _require(bool(candidates), "pixel evidence lacks orthogonal qualifying pair")
        pair = _sequence(
            result["canonical_orthogonal_pair"], "canonical orthogonal pair",
            maximum=2, minimum=2,
        )
        _require(tuple(pair) == candidates[0], "pixel orthogonal pair is not dynamically canonical")
    else:
        _require(
            not candidates and result["canonical_orthogonal_pair"] is None,
            "no-visible pixel evidence contains an orthogonal qualifying pair",
        )
    for field in (
        "selection_sha256", "action_sha256", "evaluated_region_proof_sha256",
        "region_manifest_sha256", "toolchain_sha256", "caps_sha256",
        "evidence_sha256",
    ):
        _sha(result[field], f"pixel family evidence.{field}")
    _require(result["caps_sha256"] == caps["caps_sha256"], "pixel evidence/caps binding differs")
    unsigned = {key: item for key, item in result.items() if key != "evidence_sha256"}
    _require(result["evidence_sha256"] == _hash(unsigned), "pixel family evidence seal differs")
    return result


def _validate_decision(
    value: object, *, region: Mapping[str, Any], action: Mapping[str, Any] | None,
    evaluated: Mapping[str, Any] | None, pixels: Mapping[str, Any] | None,
    contracts: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    result = _mapping(value, _DECISION_KEYS, "selector decision")
    mode = result["mode"]
    _require(mode in _MODES, "selector decision mode differs")
    if mode == "bind-animation":
        selection = _validate_animation_selection(result["selection_payload"], "selector selection")
        _require(result["attempted_selection_payload"] is None and result["decision_proof"] is None, "animation decision has bind-only proof")
    else:
        selection = _validate_bind_selection(result["selection_payload"], "selector bind selection")
        attempted = None
        if mode == "bind-only/no-visible-displacement":
            attempted = _validate_animation_selection(result["attempted_selection_payload"], "selector attempted selection")
        else:
            _require(result["attempted_selection_payload"] is None, "bind-only decision has unexpected attempt")
        _validate_reason(
            result["decision_proof"], mode=mode, bind=selection, attempted=attempted,
            region=region, evaluated=evaluated, pixels=pixels,
        )
        selection = attempted or selection
    _sha(result["decision_sha256"], "selector decision seal")
    unsigned = {key: item for key, item in result.items() if key != "decision_sha256"}
    _require(result["decision_sha256"] == _hash(unsigned), "selector decision seal differs")
    if mode in {"bind-animation", "bind-only/no-visible-displacement"}:
        _require(action is not None and evaluated is not None and pixels is not None, "animated decision lacks source proofs")
        expected_pixel_kind = (
            "pose-pixel-family-evidence-v1" if mode == "bind-animation"
            else "pose-pixel-no-visible-evidence-v1"
        )
        _require(
            pixels["kind"] == expected_pixel_kind,
            "pixel evidence kind differs from selector decision",
        )
        action = _validate_action(action, selection=selection, contracts=contracts)
        evaluated = _validate_evaluated(
            evaluated, selection=selection, action=action, contracts=contracts,
        )
        _require(
            pixels["selection_sha256"] == selection["selection_sha256"]
            and pixels["action_sha256"] == action["action_sha256"]
            and pixels["evaluated_region_proof_sha256"] == evaluated["proof_sha256"]
            and pixels["region_manifest_sha256"] == region["region_manifest_sha256"]
            and pixels["toolchain_sha256"] == contracts["toolchain_sha256"],
            "pixel selection/action/evaluated/region/toolchain binding differs",
        )
    else:
        _require(action is None and evaluated is None and pixels is None, "bind-only decision carries animation proofs")
    return result, selection


@dataclass(frozen=True)
class RegionPoseEvidence:
    family: Mapping[str, Any]
    region: Mapping[str, Any]
    contracts: Mapping[str, Any]
    selector_decision: Mapping[str, Any]
    action_proof: Mapping[str, Any] | None
    evaluated_region_proof: Mapping[str, Any] | None
    pixel_evidence: Mapping[str, Any] | None
    caps: Mapping[str, Any]
    evidence_sha256: str

    def to_payload(self) -> dict[str, Any]:
        return _copy({
            "schema_version": 1, "kind": "region-pose-evidence-v1", "status": "proven",
            "family": self.family, "region": self.region, "contracts": self.contracts,
            "selector_decision": self.selector_decision, "action_proof": self.action_proof,
            "evaluated_region_proof": self.evaluated_region_proof,
            "pixel_evidence": self.pixel_evidence, "caps": self.caps,
            "evidence_sha256": self.evidence_sha256,
        })


@dataclass(frozen=True)
class RegionPoseExpectedBindings:
    evidence_sha256: str
    family_id: str
    family_input_sha256: str
    source_snapshot_sha256: str
    control_snapshot_sha256: str
    region_key: str
    region_manifest_sha256: str
    contracts_sha256: str
    toolchain_sha256: str
    caps_sha256: str

    def __post_init__(self) -> None:
        _text(self.family_id, "expected family id"); _text(self.region_key, "expected region key")
        for field in (
            "evidence_sha256", "family_input_sha256", "source_snapshot_sha256",
            "control_snapshot_sha256", "region_manifest_sha256",
            "contracts_sha256", "toolchain_sha256", "caps_sha256",
        ):
            _sha(getattr(self, field), f"expected {field}")


def _parse_core(payload: object, *, verify_seal: bool = True) -> RegionPoseEvidence:
    root = _mapping(payload, _ROOT_KEYS, "region pose evidence")
    _require(root["schema_version"] == 1 and root["kind"] == "region-pose-evidence-v1" and root["status"] == "proven", "region pose evidence is not proven v1")
    family = _validate_family(root["family"]); region = _validate_region(root["region"])
    contracts = _validate_contracts(root["contracts"]); caps = _validate_caps(root["caps"])
    action = root["action_proof"]; evaluated = root["evaluated_region_proof"]
    pixels = root["pixel_evidence"]
    if pixels is not None: pixels = _validate_pixel_evidence(pixels, caps=caps)
    decision, _selection = _validate_decision(
        root["selector_decision"], region=region, action=action,
        evaluated=evaluated, pixels=pixels, contracts=contracts,
    )
    seal = _sha(root["evidence_sha256"], "evidence seal")
    if verify_seal:
        _require(seal == canonical_region_pose_evidence_hash(root), "region pose evidence seal differs")
    return RegionPoseEvidence(
        _freeze(family), _freeze(region), _freeze(contracts), _freeze(decision),
        _freeze(action) if action is not None else None,
        _freeze(evaluated) if evaluated is not None else None,
        _freeze(pixels) if pixels is not None else None, _freeze(caps), seal,
    )


def build_region_pose_evidence(
    *, family_id: str, family_input_sha256: str, source_snapshot_sha256: str,
    control_snapshot_sha256: str, region_key: str, region_manifest_sha256: str,
    contracts: Mapping[str, Any], selector_decision: Mapping[str, Any],
    action_proof: Mapping[str, Any] | None,
    evaluated_region_proof: Mapping[str, Any] | None,
    pixel_evidence: Mapping[str, Any] | None, caps: Mapping[str, Any],
) -> RegionPoseEvidence:
    unsigned = {
        "schema_version": 1, "kind": "region-pose-evidence-v1", "status": "proven",
        "family": {"family_id": family_id, "family_input_sha256": family_input_sha256, "source_snapshot_sha256": source_snapshot_sha256, "control_snapshot_sha256": control_snapshot_sha256},
        "region": {"region_key": region_key, "region_manifest_sha256": region_manifest_sha256},
        "contracts": contracts, "selector_decision": selector_decision,
        "action_proof": action_proof, "evaluated_region_proof": evaluated_region_proof,
        "pixel_evidence": pixel_evidence, "caps": caps,
    }
    provisional = {**unsigned, "evidence_sha256": "0" * 64}
    _parse_core(provisional, verify_seal=False)  # bounds and sub-seals before root hash
    return _parse_core({**_copy(unsigned), "evidence_sha256": _hash(unsigned)})


def parse_region_pose_evidence(
    payload: object, *, expected_evidence_sha256: str, expected_family_id: str,
    expected_family_input_sha256: str, expected_source_snapshot_sha256: str,
    expected_control_snapshot_sha256: str, expected_region_key: str,
    expected_region_manifest_sha256: str, expected_contracts_sha256: str,
    expected_toolchain_sha256: str, expected_caps_sha256: str,
) -> RegionPoseEvidence:
    expected = RegionPoseExpectedBindings(
        expected_evidence_sha256, expected_family_id, expected_family_input_sha256,
        expected_source_snapshot_sha256, expected_control_snapshot_sha256,
        expected_region_key, expected_region_manifest_sha256,
        expected_contracts_sha256, expected_toolchain_sha256, expected_caps_sha256,
    )
    parsed = _parse_core(payload)
    actual = (
        parsed.evidence_sha256, parsed.family["family_id"], parsed.family["family_input_sha256"],
        parsed.family["source_snapshot_sha256"], parsed.family["control_snapshot_sha256"],
        parsed.region["region_key"], parsed.region["region_manifest_sha256"],
        parsed.contracts["contracts_sha256"], parsed.contracts["toolchain_sha256"],
        parsed.caps["caps_sha256"],
    )
    _require(actual == tuple(expected.__dict__.values()), "external region pose binding differs")
    return parsed


def _family_common(entry: RegionPoseEvidence) -> dict[str, Any]:
    return {
        "family_id": entry.family["family_id"], "family_input_sha256": entry.family["family_input_sha256"],
        "source_snapshot_sha256": entry.family["source_snapshot_sha256"],
        "control_snapshot_sha256": entry.family["control_snapshot_sha256"],
        "contracts_sha256": entry.contracts["contracts_sha256"],
        "toolchain_sha256": entry.contracts["toolchain_sha256"], "caps_sha256": entry.caps["caps_sha256"],
    }


@dataclass(frozen=True)
class RegionPoseFamilyBundle:
    family_common: Mapping[str, Any]
    expected_region_keys: tuple[str, ...]
    entries: tuple[RegionPoseEvidence, ...]
    bundle_sha256: str

    @property
    def family_id(self) -> str:
        return self.family_common["family_id"]

    def to_payload(self) -> dict[str, Any]:
        return _copy({
            "schema_version": 1, "kind": "region-pose-family-bundle-v1", "status": "proven",
            "family_common": self.family_common, "expected_region_keys": self.expected_region_keys,
            "entries": tuple(entry.to_payload() for entry in self.entries), "bundle_sha256": self.bundle_sha256,
        })


def _region_keys(value: Iterable[str]) -> tuple[str, ...]:
    keys = _bounded_tuple(value, "expected region keys", _MAX_REGIONS)
    _require(bool(keys), "expected region keys are empty")
    for key in keys: _text(key, "expected region key")
    _require(keys == tuple(sorted(set(keys))), "expected region keys are not canonical unique")
    return keys


def build_region_pose_family_bundle(
    *, family_id: str, expected_region_keys: Iterable[str],
    entries: Iterable[RegionPoseEvidence | Mapping[str, Any]],
) -> RegionPoseFamilyBundle:
    _text(family_id, "family id"); keys = _region_keys(expected_region_keys)
    raw_entries = _bounded_tuple(entries, "family entries", _MAX_REGIONS)
    _require(len(raw_entries) == len(keys), "family entry/universe count differs")
    parsed = tuple(_parse_core(item.to_payload() if isinstance(item, RegionPoseEvidence) else item) for item in raw_entries)
    actual_keys = tuple(item.region["region_key"] for item in parsed)
    _require(len(actual_keys) == len(set(actual_keys)) and set(actual_keys) == set(keys), "family region coverage differs")
    common = _family_common(parsed[0])
    _require(common["family_id"] == family_id and all(_family_common(item) == common for item in parsed), "family bundle common tuple is hybrid")
    ordered = tuple(sorted(parsed, key=lambda item: item.region["region_key"]))
    unsigned = {
        "schema_version": 1, "kind": "region-pose-family-bundle-v1", "status": "proven",
        "family_common": common, "expected_region_keys": list(keys),
        "entries": [item.to_payload() for item in ordered],
    }
    return RegionPoseFamilyBundle(_freeze(common), keys, ordered, _hash(unsigned))


def parse_region_pose_family_bundle(
    payload: object, *, expected_bundle_sha256: str, expected_family_id: str,
    expected_region_keys: Iterable[str],
    expected_bindings: Mapping[str, RegionPoseExpectedBindings],
) -> RegionPoseFamilyBundle:
    root = _mapping(payload, _BUNDLE_KEYS, "family bundle")
    _require(root["schema_version"] == 1 and root["kind"] == "region-pose-family-bundle-v1" and root["status"] == "proven", "family bundle is not proven v1")
    keys = _region_keys(expected_region_keys)
    _require(root["expected_region_keys"] == list(keys), "family bundle external universe differs")
    seal = _sha(root["bundle_sha256"], "bundle seal")
    _sha(expected_bundle_sha256, "expected bundle seal")
    _require(seal == expected_bundle_sha256, "bundle/external seal differs")
    common = _mapping(root["family_common"], _COMMON_KEYS, "family common")
    _require(common["family_id"] == expected_family_id, "family common id differs")
    for field in _COMMON_KEYS - {"family_id"}: _sha(common[field], f"family common.{field}")
    _require(isinstance(expected_bindings, Mapping) and len(expected_bindings) == len(keys) and set(expected_bindings) == set(keys), "external region bindings differ")
    entries = _sequence(root["entries"], "family bundle entries", maximum=_MAX_REGIONS, minimum=len(keys))
    _require(len(entries) == len(keys), "family bundle entry count differs")
    common_tuple = tuple(common[field] for field in (
        "family_id", "family_input_sha256", "source_snapshot_sha256", "control_snapshot_sha256",
        "contracts_sha256", "toolchain_sha256", "caps_sha256",
    ))
    parsed = []
    seen = []
    for raw in entries:
        _require(isinstance(raw, Mapping) and isinstance(raw.get("region"), Mapping), "family entry is invalid")
        key = raw["region"].get("region_key"); _require(key in expected_bindings, "family entry region is unexpected")
        binding = expected_bindings[key]; _require(isinstance(binding, RegionPoseExpectedBindings), "external binding type differs")
        _require((
            binding.family_id, binding.family_input_sha256, binding.source_snapshot_sha256,
            binding.control_snapshot_sha256, binding.contracts_sha256,
            binding.toolchain_sha256, binding.caps_sha256,
        ) == common_tuple, "external family common tuple is hybrid")
        parsed.append(parse_region_pose_evidence(
            raw, expected_evidence_sha256=binding.evidence_sha256,
            expected_family_id=binding.family_id, expected_family_input_sha256=binding.family_input_sha256,
            expected_source_snapshot_sha256=binding.source_snapshot_sha256,
            expected_control_snapshot_sha256=binding.control_snapshot_sha256,
            expected_region_key=binding.region_key,
            expected_region_manifest_sha256=binding.region_manifest_sha256,
            expected_contracts_sha256=binding.contracts_sha256,
            expected_toolchain_sha256=binding.toolchain_sha256,
            expected_caps_sha256=binding.caps_sha256,
        )); seen.append(key)
    _require(tuple(seen) == keys and len(seen) == len(set(seen)), "family entries are not canonical unique")
    _require(all(_family_common(item) == dict(common) for item in parsed), "family entry/common tuple differs")
    _require(
        seal == canonical_region_pose_family_bundle_hash(root),
        "bundle canonical seal differs",
    )
    return RegionPoseFamilyBundle(_freeze(common), keys, tuple(parsed), seal)


__all__ = [
    "RegionPoseEvidence", "RegionPoseExpectedBindings", "RegionPoseFamilyBundle",
    "build_region_pose_evidence", "parse_region_pose_evidence",
    "build_region_pose_family_bundle", "parse_region_pose_family_bundle",
    "canonical_region_pose_evidence_hash", "canonical_region_pose_family_bundle_hash",
]
