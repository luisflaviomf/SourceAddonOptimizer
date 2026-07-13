"""Fail-closed contracts for exact region/pose render evidence.

This module seals already-produced selector, Blender action and decoded-pixel
proofs.  It performs no rendering.  Every parser requires expectations from a
caller-owned manifest so a transplanted and self-resealed payload is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping


_HEX = frozenset("0123456789abcdef")
_MAX_TEXT = 256
_MAX_REGIONS = 64
_VIEW_ORDER = (
    "front", "back", "left", "right", "top", "bottom",
    "front_left", "rear_right",
)
_VIEW_DIRECTIONS = {
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
    "front_left": (-0.7071067811865476, -0.7071067811865476, 0.0),
    "rear_right": (0.7071067811865476, 0.7071067811865476, 0.0),
}
_CANONICAL_ORTHOGONAL_PAIR = ("front", "right")
_EVIDENCE_KEYS = {
    "schema_version", "kind", "status", "family", "region", "contracts",
    "selector", "blender_action_proof", "pixel_gate", "caps",
    "evidence_sha256",
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
    "caps_sha256",
}
_SELECTOR_KEYS = {
    "decision_kind", "mode", "algorithm", "candidate_inputs_consulted",
    "family_input_sha256", "region_manifest_sha256", "contracts_sha256",
    "toolchain_sha256", "animation_inventory", "geometry_inventory",
    "selector_input_sha256", "selected", "attempted",
    "reason_source_proof_sha256", "selection_sha256",
}
_ANIMATION_INVENTORY_KEYS = {
    "animation_relative_path", "animation_sha256", "reference_relative_path",
    "reference_sha256",
}
_GEOMETRY_INVENTORY_KEYS = {"relative_path", "sha256"}
_DECISION_KEYS = {
    "pose_key", "animation_sha256", "reference_sha256", "frame_ordinal",
    "source_time", "bone_index", "bone_name", "displacement", "action_name",
    "action_source_sha256",
}
_SELECTOR_MODES = {
    "bind-animation", "bind-only/no-eligible-animation",
    "bind-only/no-influenced-bone-delta", "bind-only/rigid-region",
    "bind-only/no-visible-displacement",
}
_ACTION_KEYS = {
    "proof_kind", "family_input_sha256", "region_manifest_sha256",
    "contracts_sha256", "toolchain_sha256", "selection_sha256",
    "action_name", "action_source_sha256", "frame_ordinal", "source_time",
    "slot_index", "slot_sha256", "fcurve_inventory_sha256",
    "evaluated_pose_sha256", "scene_sha256", "proof_sha256",
}
_PIXEL_KEYS = {
    "proof_kind", "family_input_sha256", "region_manifest_sha256",
    "contracts_sha256", "toolchain_sha256", "caps_sha256",
    "selection_sha256", "scene_sha256", "bind_decoded_bundle_sha256",
    "first_decoded_bundle_sha256", "repeat_decoded_bundle_sha256",
    "qualifying_orthogonal_pair", "views", "proof_sha256",
}
_VIEW_KEYS = {
    "view_key", "direction", "width", "height", "bind_rgba_sha256",
    "posed_first_rgba_sha256", "posed_repeat_rgba_sha256",
    "foreground_pixels", "changed_pixels_first", "changed_pixels_repeat",
    "changed_fraction_first", "changed_fraction_repeat",
    "mean_absolute_error_first", "mean_absolute_error_repeat",
}
_BUNDLE_KEYS = {
    "schema_version", "kind", "status", "family_common",
    "expected_region_keys", "entries", "bundle_sha256",
}
_COMMON_KEYS = {
    "family_id", "family_input_sha256", "source_snapshot_sha256",
    "control_snapshot_sha256", "contracts_sha256", "toolchain_sha256",
    "caps_sha256",
}


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _thaw(value), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("region pose evidence must be canonical JSON data") from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _thaw(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, list):
        return [_thaw(item) for item in value]
    return value


def _freeze(value: object) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _json_copy(value: object) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def canonical_region_pose_evidence_hash(payload: Mapping[str, Any]) -> str:
    return _hash({key: value for key, value in payload.items() if key != "evidence_sha256"})


def canonical_region_pose_family_bundle_hash(payload: Mapping[str, Any]) -> str:
    return _hash({key: value for key, value in payload.items() if key != "bundle_sha256"})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _mapping(value: object, keys: set[str], name: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{name} must be an object")
    _require(set(value) == keys, f"{name} schema is invalid")
    return value


def _sequence(value: object, name: str) -> list[Any] | tuple[Any, ...]:
    _require(isinstance(value, (list, tuple)), f"{name} must be a list")
    return value


def _text(value: object, name: str) -> str:
    _require(
        isinstance(value, str) and 0 < len(value) <= _MAX_TEXT
        and value == value.strip()
        and not any(ord(character) < 32 for character in value),
        f"{name} must be bounded non-empty text",
    )
    return value


def _sha(value: object, name: str) -> str:
    _require(
        isinstance(value, str) and len(value) == 64
        and all(character in _HEX for character in value),
        f"{name} must be a lowercase SHA-256",
    )
    return value


def _integer(value: object, name: str, minimum: int = 0, maximum: int | None = None) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{name} must be an integer")
    _require(value >= minimum and (maximum is None or value <= maximum), f"{name} is outside its cap")
    return value


def _number(value: object, name: str, *, positive: bool = False) -> float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(float(value)), f"{name} must be finite",
    )
    result = float(value)
    _require(result > 0.0 if positive else result >= 0.0, f"{name} is invalid")
    return result


def _validate_family(value: object) -> Mapping[str, Any]:
    family = _mapping(value, _FAMILY_KEYS, "family")
    _text(family["family_id"], "family.family_id")
    for field in _FAMILY_KEYS - {"family_id"}:
        _sha(family[field], f"family.{field}")
    return family


def _validate_region(value: object) -> Mapping[str, Any]:
    region = _mapping(value, _REGION_KEYS, "region")
    _text(region["region_key"], "region.region_key")
    _sha(region["region_manifest_sha256"], "region.region_manifest_sha256")
    return region


def _validate_contracts(value: object) -> Mapping[str, Any]:
    contracts = _mapping(value, _CONTRACT_KEYS, "contracts")
    _require(contracts["contract_kind"] == "exact-region-pose-contracts-v1", "contracts kind is invalid")
    for field in _CONTRACT_KEYS - {"contract_kind"}:
        _sha(contracts[field], f"contracts.{field}")
    unsigned = {key: item for key, item in contracts.items() if key != "contracts_sha256"}
    _require(contracts["contracts_sha256"] == _hash(unsigned), "contracts seal mismatch")
    return contracts


def _validate_caps(value: object) -> Mapping[str, Any]:
    caps = _mapping(value, _CAP_KEYS, "caps")
    _require(caps["max_regions_per_family"] == _MAX_REGIONS, "family region cap is invalid")
    _require(caps["required_view_count"] == len(_VIEW_ORDER), "view inventory cap is invalid")
    _require(caps["required_width"] == 512 and caps["required_height"] == 512, "pixel dimensions must be 512x512")
    _require(caps["max_total_pixels"] == 8 * 512 * 512, "pixel inventory cap is invalid")
    minimum = _number(caps["minimum_changed_fraction"], "caps.minimum_changed_fraction", positive=True)
    _require(minimum <= 1.0, "minimum changed fraction is invalid")
    _sha(caps["caps_sha256"], "caps.caps_sha256")
    unsigned = {key: item for key, item in caps.items() if key != "caps_sha256"}
    _require(caps["caps_sha256"] == _hash(unsigned), "caps seal mismatch")
    return caps


def _validate_inventory(value: object, *, animation: bool) -> list[Mapping[str, Any]]:
    name = "selector.animation_inventory" if animation else "selector.geometry_inventory"
    values = _sequence(value, name)
    _require(len(values) <= 256 and (animation or bool(values)), f"{name} count is invalid")
    keys = _ANIMATION_INVENTORY_KEYS if animation else _GEOMETRY_INVENTORY_KEYS
    result: list[Mapping[str, Any]] = []
    paths: list[tuple[str, ...]] = []
    for index, raw in enumerate(values):
        item = _mapping(raw, keys, f"{name}[{index}]")
        if animation:
            animation_path = _text(item["animation_relative_path"], f"{name}[{index}].animation_relative_path")
            reference_path = _text(item["reference_relative_path"], f"{name}[{index}].reference_relative_path")
            _sha(item["animation_sha256"], f"{name}[{index}].animation_sha256")
            _sha(item["reference_sha256"], f"{name}[{index}].reference_sha256")
            paths.append((animation_path.casefold(), animation_path, reference_path.casefold(), reference_path))
        else:
            path = _text(item["relative_path"], f"{name}[{index}].relative_path")
            _sha(item["sha256"], f"{name}[{index}].sha256")
            paths.append((path.casefold(), path))
        result.append(item)
    _require(paths == sorted(paths) and len(paths) == len(set(paths)), f"{name} is not canonical and unique")
    return result


def _validate_decision(value: object, name: str) -> Mapping[str, Any]:
    decision = _mapping(value, _DECISION_KEYS, name)
    for field in ("pose_key", "bone_name", "action_name"):
        _text(decision[field], f"{name}.{field}")
    for field in ("animation_sha256", "reference_sha256", "action_source_sha256"):
        _sha(decision[field], f"{name}.{field}")
    for field in ("frame_ordinal", "source_time", "bone_index"):
        _integer(decision[field], f"{name}.{field}")
    _number(decision["displacement"], f"{name}.displacement", positive=True)
    return decision


def _validate_selector(
    value: object, *, family: Mapping[str, Any], region: Mapping[str, Any],
    contracts: Mapping[str, Any],
) -> Mapping[str, Any]:
    selector = _mapping(value, _SELECTOR_KEYS, "selector")
    _require(selector["decision_kind"] == "exact-region-pose-decision-v1", "selector decision kind is invalid")
    _require(selector["mode"] in _SELECTOR_MODES, "selector mode is invalid")
    _require(selector["algorithm"] == "exact-region-lineage-displacement-v2", "selector algorithm is invalid")
    _require(selector["candidate_inputs_consulted"] is False, "selector consulted candidate inputs")
    for field in (
        "family_input_sha256", "region_manifest_sha256", "contracts_sha256",
        "toolchain_sha256", "selector_input_sha256", "selection_sha256",
    ):
        _sha(selector[field], f"selector.{field}")
    _require(
        selector["family_input_sha256"] == family["family_input_sha256"]
        and selector["region_manifest_sha256"] == region["region_manifest_sha256"]
        and selector["contracts_sha256"] == contracts["contracts_sha256"]
        and selector["toolchain_sha256"] == contracts["toolchain_sha256"],
        "selector family/region/contracts/toolchain binding mismatch",
    )
    animations = _validate_inventory(selector["animation_inventory"], animation=True)
    _validate_inventory(selector["geometry_inventory"], animation=False)
    selected = selector["selected"]
    attempted = selector["attempted"]
    mode = selector["mode"]
    if mode == "bind-animation":
        selected = _validate_decision(selected, "selector.selected")
        _require(attempted is None and selector["reason_source_proof_sha256"] is None, "animation selector has bind-only fields")
    elif mode == "bind-only/no-visible-displacement":
        attempted = _validate_decision(attempted, "selector.attempted")
        _require(selected is None, "no-visible bind-only selector cannot select animation")
        _sha(selector["reason_source_proof_sha256"], "selector.reason_source_proof_sha256")
    else:
        _require(
            selected is None and attempted is None and selector["reason_source_proof_sha256"] is None,
            "bind-only selector carries unsupported attempted/selected fields",
        )
    decision = selected if mode == "bind-animation" else attempted
    if decision is not None:
        pair = (decision["animation_sha256"], decision["reference_sha256"])
        inventory_pairs = {(item["animation_sha256"], item["reference_sha256"]) for item in animations}
        _require(pair in inventory_pairs, "selector decision is absent from exact animation inventory")
        _require(decision["action_source_sha256"] == decision["animation_sha256"], "selector action source is not selected animation")
    input_fields = {
        key: selector[key] for key in (
            "decision_kind", "mode", "algorithm", "candidate_inputs_consulted",
            "family_input_sha256", "region_manifest_sha256", "contracts_sha256",
            "toolchain_sha256", "animation_inventory", "geometry_inventory",
        )
    }
    _require(selector["selector_input_sha256"] == _hash(input_fields), "selector input seal mismatch")
    unsigned = {key: item for key, item in selector.items() if key != "selection_sha256"}
    _require(selector["selection_sha256"] == _hash(unsigned), "selection seal is not canonically derived")
    return selector


def _validate_action(
    value: object, *, family: Mapping[str, Any], region: Mapping[str, Any],
    contracts: Mapping[str, Any], selector: Mapping[str, Any],
) -> Mapping[str, Any]:
    action = _mapping(value, _ACTION_KEYS, "blender_action_proof")
    _require(action["proof_kind"] == "blender-action-proof-v1", "Blender action proof kind is invalid")
    for field in (
        "family_input_sha256", "region_manifest_sha256", "contracts_sha256",
        "toolchain_sha256", "selection_sha256", "action_source_sha256",
        "slot_sha256", "fcurve_inventory_sha256", "evaluated_pose_sha256",
        "scene_sha256", "proof_sha256",
    ):
        _sha(action[field], f"blender_action_proof.{field}")
    _text(action["action_name"], "blender_action_proof.action_name")
    for field in ("frame_ordinal", "source_time", "slot_index"):
        _integer(action[field], f"blender_action_proof.{field}")
    _require(
        action["family_input_sha256"] == family["family_input_sha256"]
        and action["region_manifest_sha256"] == region["region_manifest_sha256"]
        and action["contracts_sha256"] == contracts["contracts_sha256"]
        and action["toolchain_sha256"] == contracts["toolchain_sha256"]
        and action["selection_sha256"] == selector["selection_sha256"],
        "Blender action binding mismatch",
    )
    selected = selector["selected"]
    _require(isinstance(selected, Mapping), "Blender action has no selected animation")
    for field in ("action_name", "action_source_sha256", "frame_ordinal", "source_time"):
        _require(action[field] == selected[field], f"Blender action {field} differs from selector")
    unsigned = {key: item for key, item in action.items() if key != "proof_sha256"}
    _require(action["proof_sha256"] == _hash(unsigned), "Blender action proof seal mismatch")
    return action


def _validate_view(
    raw: object, *, index: int, caps: Mapping[str, Any],
) -> Mapping[str, Any]:
    view = _mapping(raw, _VIEW_KEYS, f"pixel_gate.views[{index}]")
    key = view["view_key"]
    _require(key == _VIEW_ORDER[index], "pixel views are not in canonical order")
    direction = _sequence(view["direction"], f"pixel_gate.views[{index}].direction")
    _require(tuple(direction) == _VIEW_DIRECTIONS[key], "pixel view direction is invalid")
    _require(
        view["width"] == caps["required_width"]
        and view["height"] == caps["required_height"],
        "pixel view dimensions must be 512x512",
    )
    pixels = int(view["width"]) * int(view["height"])
    foreground = _integer(view["foreground_pixels"], "pixel foreground pixels", 1, pixels)
    for field in ("bind_rgba_sha256", "posed_first_rgba_sha256", "posed_repeat_rgba_sha256"):
        _sha(view[field], f"pixel_gate.views[{index}].{field}")
    _require(
        view["posed_first_rgba_sha256"] == view["posed_repeat_rgba_sha256"]
        and view["bind_rgba_sha256"] != view["posed_first_rgba_sha256"],
        "pixel first/repeat decoded output differs or bind is unchanged",
    )
    first = _integer(view["changed_pixels_first"], "pixel first changed pixels", 1, foreground)
    repeat = _integer(view["changed_pixels_repeat"], "pixel repeat changed pixels", 1, foreground)
    first_fraction = _number(view["changed_fraction_first"], "pixel first changed fraction", positive=True)
    repeat_fraction = _number(view["changed_fraction_repeat"], "pixel repeat changed fraction", positive=True)
    first_mae = _number(view["mean_absolute_error_first"], "pixel first MAE", positive=True)
    repeat_mae = _number(view["mean_absolute_error_repeat"], "pixel repeat MAE", positive=True)
    minimum = float(caps["minimum_changed_fraction"])
    _require(
        first == repeat and first_fraction == repeat_fraction and first_mae == repeat_mae
        and first_fraction >= minimum
        and math.isclose(first_fraction, first / foreground, rel_tol=1e-12, abs_tol=1e-15),
        "pixel first/repeat metrics differ or do not qualify",
    )
    return view


def _pixel_bundle(views: Iterable[Mapping[str, Any]], field: str) -> str:
    return _hash([{"view_key": view["view_key"], "rgba_sha256": view[field]} for view in views])


def _validate_pixel_gate(
    value: object, *, family: Mapping[str, Any], region: Mapping[str, Any],
    contracts: Mapping[str, Any], selector: Mapping[str, Any],
    action: Mapping[str, Any], caps: Mapping[str, Any],
) -> Mapping[str, Any]:
    gate = _mapping(value, _PIXEL_KEYS, "pixel_gate")
    _require(gate["proof_kind"] == "decoded-rgba-pixel-gate-v1", "pixel gate proof kind is invalid")
    for field in (
        "family_input_sha256", "region_manifest_sha256", "contracts_sha256",
        "toolchain_sha256", "caps_sha256", "selection_sha256", "scene_sha256",
        "bind_decoded_bundle_sha256", "first_decoded_bundle_sha256",
        "repeat_decoded_bundle_sha256", "proof_sha256",
    ):
        _sha(gate[field], f"pixel_gate.{field}")
    _require(
        gate["family_input_sha256"] == family["family_input_sha256"]
        and gate["region_manifest_sha256"] == region["region_manifest_sha256"]
        and gate["contracts_sha256"] == contracts["contracts_sha256"]
        and gate["toolchain_sha256"] == contracts["toolchain_sha256"]
        and gate["caps_sha256"] == caps["caps_sha256"]
        and gate["selection_sha256"] == selector["selection_sha256"]
        and gate["scene_sha256"] == action["scene_sha256"],
        "pixel gate binding mismatch",
    )
    raw_views = _sequence(gate["views"], "pixel_gate.views")
    _require(len(raw_views) == caps["required_view_count"] == 8, "pixel gate requires exactly eight views")
    views = [_validate_view(raw, index=index, caps=caps) for index, raw in enumerate(raw_views)]
    _require(
        len(views) * int(caps["required_width"]) * int(caps["required_height"])
        == caps["max_total_pixels"], "pixel inventory dimensions do not match cap",
    )
    _require(gate["bind_decoded_bundle_sha256"] == _pixel_bundle(views, "bind_rgba_sha256"), "bind decoded bundle seal mismatch")
    _require(gate["first_decoded_bundle_sha256"] == _pixel_bundle(views, "posed_first_rgba_sha256"), "first decoded bundle seal mismatch")
    _require(gate["repeat_decoded_bundle_sha256"] == _pixel_bundle(views, "posed_repeat_rgba_sha256"), "repeat decoded bundle seal mismatch")
    _require(
        gate["first_decoded_bundle_sha256"] == gate["repeat_decoded_bundle_sha256"],
        "decoded first/repeat bundles differ",
    )
    pair = _sequence(gate["qualifying_orthogonal_pair"], "pixel qualifying orthogonal pair")
    _require(tuple(pair) == _CANONICAL_ORTHOGONAL_PAIR, "pixel qualifying pair is not canonical orthogonal pair")
    left = _VIEW_DIRECTIONS[pair[0]]
    right = _VIEW_DIRECTIONS[pair[1]]
    _require(abs(sum(a * b for a, b in zip(left, right))) <= 1e-12, "pixel qualifying pair is not orthogonal")
    unsigned = {key: item for key, item in gate.items() if key != "proof_sha256"}
    _require(gate["proof_sha256"] == _hash(unsigned), "pixel gate proof seal mismatch")
    return gate


@dataclass(frozen=True)
class RegionPoseEvidence:
    family: Mapping[str, Any]
    region: Mapping[str, Any]
    contracts: Mapping[str, Any]
    selector: Mapping[str, Any]
    blender_action_proof: Mapping[str, Any] | None
    pixel_gate: Mapping[str, Any] | None
    caps: Mapping[str, Any]
    evidence_sha256: str

    def to_payload(self) -> dict[str, Any]:
        return _json_copy({
            "schema_version": 1, "kind": "region-pose-evidence-v1",
            "status": "proven", "family": self.family, "region": self.region,
            "contracts": self.contracts, "selector": self.selector,
            "blender_action_proof": self.blender_action_proof,
            "pixel_gate": self.pixel_gate, "caps": self.caps,
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
        _text(self.family_id, "expected family id")
        _text(self.region_key, "expected region key")
        for field in (
            "evidence_sha256", "family_input_sha256", "source_snapshot_sha256",
            "control_snapshot_sha256", "region_manifest_sha256",
            "contracts_sha256", "toolchain_sha256", "caps_sha256",
        ):
            _sha(getattr(self, field), f"expected {field}")


def _parse_core(payload: object) -> RegionPoseEvidence:
    root = _mapping(payload, _EVIDENCE_KEYS, "region pose evidence")
    _require(root["schema_version"] == 1, "unsupported region pose evidence schema")
    _require(root["kind"] == "region-pose-evidence-v1" and root["status"] == "proven", "region pose evidence is not proven v1")
    family = _validate_family(root["family"])
    region = _validate_region(root["region"])
    contracts = _validate_contracts(root["contracts"])
    caps = _validate_caps(root["caps"])
    selector = _validate_selector(root["selector"], family=family, region=region, contracts=contracts)
    if selector["mode"] == "bind-animation":
        _require(root["blender_action_proof"] is not None and root["pixel_gate"] is not None, "animation evidence lacks action/pixel proof")
        action = _validate_action(root["blender_action_proof"], family=family, region=region, contracts=contracts, selector=selector)
        gate = _validate_pixel_gate(root["pixel_gate"], family=family, region=region, contracts=contracts, selector=selector, action=action, caps=caps)
    else:
        _require(root["blender_action_proof"] is None and root["pixel_gate"] is None, "bind-only evidence cannot carry animation proofs")
        action = None
        gate = None
    seal = _sha(root["evidence_sha256"], "evidence_sha256")
    _require(seal == canonical_region_pose_evidence_hash(root), "region pose evidence seal mismatch")
    return RegionPoseEvidence(
        family=_freeze(family), region=_freeze(region), contracts=_freeze(contracts),
        selector=_freeze(selector), blender_action_proof=_freeze(action) if action else None,
        pixel_gate=_freeze(gate) if gate else None, caps=_freeze(caps),
        evidence_sha256=seal,
    )


def build_region_pose_evidence(
    *, family_id: str, family_input_sha256: str, source_snapshot_sha256: str,
    control_snapshot_sha256: str, region_key: str, region_manifest_sha256: str,
    contracts: Mapping[str, Any], selector: Mapping[str, Any],
    blender_action_proof: Mapping[str, Any] | None,
    pixel_gate: Mapping[str, Any] | None, caps: Mapping[str, Any],
) -> RegionPoseEvidence:
    unsigned = {
        "schema_version": 1, "kind": "region-pose-evidence-v1", "status": "proven",
        "family": {
            "family_id": family_id, "family_input_sha256": family_input_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "control_snapshot_sha256": control_snapshot_sha256,
        },
        "region": {"region_key": region_key, "region_manifest_sha256": region_manifest_sha256},
        "contracts": _json_copy(contracts), "selector": _json_copy(selector),
        "blender_action_proof": _json_copy(blender_action_proof),
        "pixel_gate": _json_copy(pixel_gate), "caps": _json_copy(caps),
    }
    return _parse_core({**unsigned, "evidence_sha256": _hash(unsigned)})


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
        parsed.evidence_sha256, parsed.family["family_id"],
        parsed.family["family_input_sha256"], parsed.family["source_snapshot_sha256"],
        parsed.family["control_snapshot_sha256"], parsed.region["region_key"],
        parsed.region["region_manifest_sha256"], parsed.contracts["contracts_sha256"],
        parsed.contracts["toolchain_sha256"], parsed.caps["caps_sha256"],
    )
    expected_tuple = (
        expected.evidence_sha256, expected.family_id, expected.family_input_sha256,
        expected.source_snapshot_sha256, expected.control_snapshot_sha256,
        expected.region_key, expected.region_manifest_sha256,
        expected.contracts_sha256, expected.toolchain_sha256, expected.caps_sha256,
    )
    _require(actual == expected_tuple, "external region pose binding mismatch")
    return parsed


def _family_common(entry: RegionPoseEvidence) -> dict[str, Any]:
    return {
        "family_id": entry.family["family_id"],
        "family_input_sha256": entry.family["family_input_sha256"],
        "source_snapshot_sha256": entry.family["source_snapshot_sha256"],
        "control_snapshot_sha256": entry.family["control_snapshot_sha256"],
        "contracts_sha256": entry.contracts["contracts_sha256"],
        "toolchain_sha256": entry.contracts["toolchain_sha256"],
        "caps_sha256": entry.caps["caps_sha256"],
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
        return _json_copy({
            "schema_version": 1, "kind": "region-pose-family-bundle-v1",
            "status": "proven", "family_common": self.family_common,
            "expected_region_keys": self.expected_region_keys,
            "entries": tuple(entry.to_payload() for entry in self.entries),
            "bundle_sha256": self.bundle_sha256,
        })


def _canonical_region_keys(value: Iterable[str]) -> tuple[str, ...]:
    _require(not isinstance(value, (str, bytes)), "expected region keys must be a sequence")
    try:
        keys = tuple(value)
    except TypeError as exc:
        raise ValueError("expected region keys must be iterable") from exc
    _require(0 < len(keys) <= _MAX_REGIONS, "expected region count exceeds family cap")
    for key in keys:
        _text(key, "expected region key")
    _require(keys == tuple(sorted(keys)) and len(keys) == len(set(keys)), "expected region keys are not canonical and unique")
    return keys


def _entry_payload(value: RegionPoseEvidence | Mapping[str, Any]) -> object:
    return value.to_payload() if isinstance(value, RegionPoseEvidence) else value


def build_region_pose_family_bundle(
    *, family_id: str, expected_region_keys: Iterable[str],
    entries: Iterable[RegionPoseEvidence | Mapping[str, Any]],
) -> RegionPoseFamilyBundle:
    _text(family_id, "family id")
    keys = _canonical_region_keys(expected_region_keys)
    try:
        parsed = tuple(_parse_core(_entry_payload(entry)) for entry in entries)
    except TypeError as exc:
        raise ValueError("family entries must be iterable") from exc
    _require(len(parsed) == len(keys) <= _MAX_REGIONS, "family entry count differs from capped universe")
    actual_keys = tuple(item.region["region_key"] for item in parsed)
    _require(len(actual_keys) == len(set(actual_keys)) and set(actual_keys) == set(keys), "family bundle region coverage is invalid")
    common = _family_common(parsed[0])
    _require(common["family_id"] == family_id, "family bundle id mismatch")
    _require(all(_family_common(item) == common for item in parsed), "family bundle contains hybrid family inputs")
    ordered = tuple(sorted(parsed, key=lambda item: item.region["region_key"]))
    unsigned = {
        "schema_version": 1, "kind": "region-pose-family-bundle-v1",
        "status": "proven", "family_common": common,
        "expected_region_keys": list(keys),
        "entries": [entry.to_payload() for entry in ordered],
    }
    return RegionPoseFamilyBundle(_freeze(common), keys, ordered, _hash(unsigned))


def parse_region_pose_family_bundle(
    payload: object, *, expected_bundle_sha256: str, expected_family_id: str,
    expected_region_keys: Iterable[str],
    expected_bindings: Mapping[str, RegionPoseExpectedBindings],
) -> RegionPoseFamilyBundle:
    root = _mapping(payload, _BUNDLE_KEYS, "region pose family bundle")
    _require(root["schema_version"] == 1, "unsupported region pose family bundle schema")
    _require(root["kind"] == "region-pose-family-bundle-v1" and root["status"] == "proven", "family bundle is not proven v1")
    keys = _canonical_region_keys(expected_region_keys)
    _require(root["expected_region_keys"] == list(keys), "external family region universe mismatch")
    _sha(expected_bundle_sha256, "expected bundle SHA-256")
    seal = _sha(root["bundle_sha256"], "bundle_sha256")
    _require(seal == canonical_region_pose_family_bundle_hash(root) == expected_bundle_sha256, "family bundle seal/external binding mismatch")
    common = _mapping(root["family_common"], _COMMON_KEYS, "family_common")
    _text(common["family_id"], "family_common.family_id")
    for field in _COMMON_KEYS - {"family_id"}:
        _sha(common[field], f"family_common.{field}")
    _require(common["family_id"] == expected_family_id, "external family bundle id mismatch")
    _require(isinstance(expected_bindings, Mapping) and set(expected_bindings) == set(keys), "external region bindings are incomplete")
    raw_entries = _sequence(root["entries"], "family bundle entries")
    _require(len(raw_entries) == len(keys) <= _MAX_REGIONS, "family bundle entry count differs from capped universe")
    parsed: list[RegionPoseEvidence] = []
    seen: list[str] = []
    common_tuple = tuple(common[field] for field in (
        "family_id", "family_input_sha256", "source_snapshot_sha256",
        "control_snapshot_sha256", "contracts_sha256", "toolchain_sha256",
        "caps_sha256",
    ))
    for index, raw in enumerate(raw_entries):
        _require(isinstance(raw, Mapping) and isinstance(raw.get("region"), Mapping), f"family entry {index} is invalid")
        key = raw["region"].get("region_key")
        _require(isinstance(key, str) and key in expected_bindings, f"family entry {index} region is unexpected")
        binding = expected_bindings[key]
        _require(isinstance(binding, RegionPoseExpectedBindings), "external region binding type is invalid")
        binding_common = (
            binding.family_id, binding.family_input_sha256,
            binding.source_snapshot_sha256, binding.control_snapshot_sha256,
            binding.contracts_sha256, binding.toolchain_sha256, binding.caps_sha256,
        )
        _require(binding_common == common_tuple, "external family bindings contain a hybrid tuple")
        parsed.append(parse_region_pose_evidence(
            raw, expected_evidence_sha256=binding.evidence_sha256,
            expected_family_id=binding.family_id,
            expected_family_input_sha256=binding.family_input_sha256,
            expected_source_snapshot_sha256=binding.source_snapshot_sha256,
            expected_control_snapshot_sha256=binding.control_snapshot_sha256,
            expected_region_key=binding.region_key,
            expected_region_manifest_sha256=binding.region_manifest_sha256,
            expected_contracts_sha256=binding.contracts_sha256,
            expected_toolchain_sha256=binding.toolchain_sha256,
            expected_caps_sha256=binding.caps_sha256,
        ))
        seen.append(key)
    _require(tuple(seen) == keys and len(seen) == len(set(seen)), "family entries are not canonical and unique")
    _require(all(_family_common(item) == dict(common) for item in parsed), "family entries do not match common tuple")
    return RegionPoseFamilyBundle(_freeze(common), keys, tuple(parsed), seal)


__all__ = [
    "RegionPoseEvidence", "RegionPoseExpectedBindings", "RegionPoseFamilyBundle",
    "build_region_pose_evidence", "parse_region_pose_evidence",
    "build_region_pose_family_bundle", "parse_region_pose_family_bundle",
    "canonical_region_pose_evidence_hash", "canonical_region_pose_family_bundle_hash",
]
