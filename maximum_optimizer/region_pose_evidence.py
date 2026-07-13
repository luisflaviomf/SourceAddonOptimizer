"""Strict, externally-bound region pose evidence contracts.

The builders only seal already-produced proofs.  They do not run Blender and
the parsers deliberately require expectations obtained outside the payload, so
copying and resealing evidence for another family or region fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable, Mapping


_SHA_LENGTH = 64
_MAX_TEXT = 256
_MAX_IMAGES = 64
_MAX_TOTAL_PIXELS = 67_108_864
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
    "pixel_gate_contract_sha256", "toolchain_sha256",
}
_SELECTOR_KEYS = {
    "mode", "pose_key", "family_input_sha256", "region_manifest_sha256",
    "selector_input_sha256", "selection_sha256", "action_name",
    "action_source_sha256", "frame_ordinal", "source_time",
    "source_proof_sha256",
}
_ACTION_KEYS = {
    "proof_kind", "family_input_sha256", "region_manifest_sha256",
    "selection_sha256", "action_name", "action_source_sha256",
    "frame_ordinal", "source_time", "scene_sha256", "proof_sha256",
}
_PIXEL_KEYS = {
    "proof_kind", "family_input_sha256", "region_manifest_sha256",
    "selection_sha256", "scene_sha256", "bind_pixel_bundle_sha256",
    "posed_pixel_bundle_sha256", "image_count", "total_pixels",
    "foreground_pixels", "changed_pixels", "changed_fraction",
    "mean_absolute_error", "minimum_changed_fraction", "proof_sha256",
}
_CAP_KEYS = {"max_images", "max_total_pixels", "minimum_changed_fraction"}
_SELECTOR_MODES = {
    "bind-animation",
    "bind-only/no-eligible-animation",
    "bind-only/no-influenced-bone-delta",
    "bind-only/rigid-region",
    "bind-only/no-visible-displacement",
}
_BUNDLE_KEYS = {
    "schema_version", "kind", "status", "family_id",
    "expected_region_keys", "entries", "bundle_sha256",
}


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("region pose evidence must be canonical JSON data") from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _copy_json(value: object) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def canonical_region_pose_evidence_hash(payload: Mapping[str, Any]) -> str:
    """Return the v1 evidence seal, excluding its self-referential field."""

    return _hash({key: value for key, value in payload.items() if key != "evidence_sha256"})


def canonical_region_pose_family_bundle_hash(payload: Mapping[str, Any]) -> str:
    """Return the v1 family bundle seal, excluding its bundle seal field."""

    return _hash({key: value for key, value in payload.items() if key != "bundle_sha256"})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _mapping(value: object, keys: set[str], name: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{name} must be an object")
    _require(set(value) == keys, f"{name} schema is invalid")
    return value


def _text(value: object, name: str) -> str:
    _require(
        isinstance(value, str)
        and 0 < len(value) <= _MAX_TEXT
        and value == value.strip()
        and not any(ord(character) < 32 for character in value),
        f"{name} must be bounded non-empty text",
    )
    return value


def _sha(value: object, name: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == _SHA_LENGTH
        and all(character in "0123456789abcdef" for character in value),
        f"{name} must be a lowercase SHA-256",
    )
    return value


def _integer(value: object, name: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{name} must be an integer")
    _require(value >= minimum and (maximum is None or value <= maximum), f"{name} is outside its cap")
    return value


def _number(value: object, name: str, *, positive: bool = False) -> float:
    _require(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value)),
        f"{name} must be finite",
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
    _require(
        contracts["contract_kind"] == "exact-region-pose-contracts-v1",
        "contracts kind is invalid",
    )
    for field in _CONTRACT_KEYS - {"contract_kind"}:
        _sha(contracts[field], f"contracts.{field}")
    return contracts


def _validate_caps(value: object) -> Mapping[str, Any]:
    caps = _mapping(value, _CAP_KEYS, "caps")
    _integer(caps["max_images"], "caps.max_images", minimum=1, maximum=_MAX_IMAGES)
    _integer(
        caps["max_total_pixels"], "caps.max_total_pixels",
        minimum=1, maximum=_MAX_TOTAL_PIXELS,
    )
    minimum = _number(
        caps["minimum_changed_fraction"],
        "caps.minimum_changed_fraction", positive=True,
    )
    _require(minimum <= 1.0, "caps.minimum_changed_fraction is invalid")
    return caps


def _validate_selector(
    value: object,
    *,
    family: Mapping[str, Any],
    region: Mapping[str, Any],
) -> Mapping[str, Any]:
    selector = _mapping(value, _SELECTOR_KEYS, "selector")
    mode = selector["mode"]
    _require(mode in _SELECTOR_MODES, "selector mode is invalid")
    _text(selector["pose_key"], "selector.pose_key")
    _require(
        selector["family_input_sha256"] == family["family_input_sha256"]
        and selector["region_manifest_sha256"] == region["region_manifest_sha256"],
        "selector family/region binding mismatch",
    )
    for field in (
        "family_input_sha256", "region_manifest_sha256",
        "selector_input_sha256", "selection_sha256",
    ):
        _sha(selector[field], f"selector.{field}")
    if mode == "bind-animation":
        _require(selector["pose_key"] != "bind", "animation selector cannot select bind")
        _text(selector["action_name"], "selector.action_name")
        _sha(selector["action_source_sha256"], "selector.action_source_sha256")
        _integer(selector["frame_ordinal"], "selector.frame_ordinal")
        _integer(selector["source_time"], "selector.source_time")
        _require(selector["source_proof_sha256"] is None, "animation selector has unexpected bind-only proof")
    else:
        _require(selector["pose_key"] == "bind", "bind-only selector must select bind")
        _require(
            selector["action_name"] is None
            and selector["action_source_sha256"] is None
            and selector["frame_ordinal"] is None
            and selector["source_time"] is None,
            "bind-only selector cannot carry animation fields",
        )
        if mode == "bind-only/no-visible-displacement":
            _sha(selector["source_proof_sha256"], "selector source proof")
        else:
            _require(selector["source_proof_sha256"] is None, "bind-only reason has unexpected source proof")
    return selector


def _validate_action(
    value: object,
    *,
    family: Mapping[str, Any],
    region: Mapping[str, Any],
    selector: Mapping[str, Any],
) -> Mapping[str, Any]:
    action = _mapping(value, _ACTION_KEYS, "blender_action_proof")
    _require(action["proof_kind"] == "blender-action-proof-v1", "Blender action proof kind is invalid")
    for field in (
        "family_input_sha256", "region_manifest_sha256", "selection_sha256",
        "action_source_sha256", "scene_sha256", "proof_sha256",
    ):
        _sha(action[field], f"blender_action_proof.{field}")
    _text(action["action_name"], "blender_action_proof.action_name")
    _integer(action["frame_ordinal"], "blender_action_proof.frame_ordinal")
    _integer(action["source_time"], "blender_action_proof.source_time")
    _require(
        action["family_input_sha256"] == family["family_input_sha256"]
        and action["region_manifest_sha256"] == region["region_manifest_sha256"]
        and action["selection_sha256"] == selector["selection_sha256"],
        "Blender action family/region/selection binding mismatch",
    )
    for field in ("action_name", "action_source_sha256", "frame_ordinal", "source_time"):
        _require(action[field] == selector[field], f"Blender action {field} binding mismatch")
    expected = _hash({key: item for key, item in action.items() if key != "proof_sha256"})
    _require(action["proof_sha256"] == expected, "Blender action proof seal mismatch")
    return action


def _validate_pixel_gate(
    value: object,
    *,
    family: Mapping[str, Any],
    region: Mapping[str, Any],
    selector: Mapping[str, Any],
    action: Mapping[str, Any],
    caps: Mapping[str, Any],
) -> Mapping[str, Any]:
    gate = _mapping(value, _PIXEL_KEYS, "pixel_gate")
    _require(gate["proof_kind"] == "decoded-rgba-pixel-gate-v1", "pixel gate proof kind is invalid")
    for field in (
        "family_input_sha256", "region_manifest_sha256", "selection_sha256",
        "scene_sha256", "bind_pixel_bundle_sha256", "posed_pixel_bundle_sha256",
        "proof_sha256",
    ):
        _sha(gate[field], f"pixel_gate.{field}")
    _require(
        gate["family_input_sha256"] == family["family_input_sha256"]
        and gate["region_manifest_sha256"] == region["region_manifest_sha256"]
        and gate["selection_sha256"] == selector["selection_sha256"]
        and gate["scene_sha256"] == action["scene_sha256"],
        "pixel gate family/region/selection/action binding mismatch",
    )
    image_count = _integer(
        gate["image_count"], "pixel_gate.image_count", minimum=1,
        maximum=int(caps["max_images"]),
    )
    total = _integer(
        gate["total_pixels"], "pixel_gate.total_pixels", minimum=image_count,
        maximum=int(caps["max_total_pixels"]),
    )
    foreground = _integer(
        gate["foreground_pixels"], "pixel_gate.foreground_pixels", minimum=1,
        maximum=total,
    )
    changed = _integer(
        gate["changed_pixels"], "pixel_gate.changed_pixels", minimum=1,
        maximum=foreground,
    )
    fraction = _number(gate["changed_fraction"], "pixel_gate.changed_fraction", positive=True)
    minimum = _number(
        gate["minimum_changed_fraction"],
        "pixel_gate.minimum_changed_fraction", positive=True,
    )
    _require(
        minimum == float(caps["minimum_changed_fraction"])
        and minimum <= fraction <= 1.0
        and math.isclose(fraction, changed / foreground, rel_tol=1e-12, abs_tol=1e-15),
        "pixel gate changed fraction or cap binding mismatch",
    )
    _number(gate["mean_absolute_error"], "pixel_gate.mean_absolute_error", positive=True)
    _require(
        gate["bind_pixel_bundle_sha256"] != gate["posed_pixel_bundle_sha256"],
        "pixel gate bind and posed pixels are identical",
    )
    expected = _hash({key: item for key, item in gate.items() if key != "proof_sha256"})
    _require(gate["proof_sha256"] == expected, "pixel gate proof seal mismatch")
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
        return _copy_json({
            "schema_version": 1,
            "kind": "region-pose-evidence-v1",
            "status": "proven",
            "family": self.family,
            "region": self.region,
            "contracts": self.contracts,
            "selector": self.selector,
            "blender_action_proof": self.blender_action_proof,
            "pixel_gate": self.pixel_gate,
            "caps": self.caps,
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

    def __post_init__(self) -> None:
        _sha(self.evidence_sha256, "expected evidence SHA-256")
        _text(self.family_id, "expected family id")
        _text(self.region_key, "expected region key")
        for field in (
            "family_input_sha256", "source_snapshot_sha256",
            "control_snapshot_sha256", "region_manifest_sha256",
        ):
            _sha(getattr(self, field), f"expected {field}")


def _parse_core(payload: object) -> RegionPoseEvidence:
    root = _mapping(payload, _EVIDENCE_KEYS, "region pose evidence")
    _require(root["schema_version"] == 1, "unsupported region pose schema")
    _require(root["kind"] == "region-pose-evidence-v1", "region pose evidence kind is invalid")
    _require(root["status"] == "proven", "region pose evidence is not proven")
    family = _validate_family(root["family"])
    region = _validate_region(root["region"])
    contracts = _validate_contracts(root["contracts"])
    caps = _validate_caps(root["caps"])
    selector = _validate_selector(root["selector"], family=family, region=region)
    if selector["mode"] == "bind-animation":
        _require(root["blender_action_proof"] is not None, "animation evidence lacks Blender action proof")
        _require(root["pixel_gate"] is not None, "animation evidence lacks pixel gate proof")
        action = _validate_action(
            root["blender_action_proof"], family=family, region=region,
            selector=selector,
        )
        gate = _validate_pixel_gate(
            root["pixel_gate"], family=family, region=region,
            selector=selector, action=action, caps=caps,
        )
    else:
        _require(
            root["blender_action_proof"] is None and root["pixel_gate"] is None,
            "bind-only evidence cannot carry animation proofs",
        )
        action = None
        gate = None
    evidence_sha256 = _sha(root["evidence_sha256"], "evidence_sha256")
    _require(
        evidence_sha256 == canonical_region_pose_evidence_hash(root),
        "region pose evidence seal mismatch",
    )
    return RegionPoseEvidence(
        family=_copy_json(family), region=_copy_json(region),
        contracts=_copy_json(contracts), selector=_copy_json(selector),
        blender_action_proof=_copy_json(action) if action is not None else None,
        pixel_gate=_copy_json(gate) if gate is not None else None,
        caps=_copy_json(caps), evidence_sha256=evidence_sha256,
    )


def build_region_pose_evidence(
    *,
    family_id: str,
    family_input_sha256: str,
    source_snapshot_sha256: str,
    control_snapshot_sha256: str,
    region_key: str,
    region_manifest_sha256: str,
    contracts: Mapping[str, Any],
    selector: Mapping[str, Any],
    blender_action_proof: Mapping[str, Any] | None,
    pixel_gate: Mapping[str, Any] | None,
    caps: Mapping[str, Any],
) -> RegionPoseEvidence:
    """Validate component proofs and build a canonical v1 evidence seal."""

    unsigned = {
        "schema_version": 1,
        "kind": "region-pose-evidence-v1",
        "status": "proven",
        "family": {
            "family_id": family_id,
            "family_input_sha256": family_input_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "control_snapshot_sha256": control_snapshot_sha256,
        },
        "region": {
            "region_key": region_key,
            "region_manifest_sha256": region_manifest_sha256,
        },
        "contracts": _copy_json(contracts),
        "selector": _copy_json(selector),
        "blender_action_proof": _copy_json(blender_action_proof),
        "pixel_gate": _copy_json(pixel_gate),
        "caps": _copy_json(caps),
    }
    return _parse_core({**unsigned, "evidence_sha256": _hash(unsigned)})


def parse_region_pose_evidence(
    payload: object,
    *,
    expected_evidence_sha256: str,
    expected_family_id: str,
    expected_family_input_sha256: str,
    expected_source_snapshot_sha256: str,
    expected_control_snapshot_sha256: str,
    expected_region_key: str,
    expected_region_manifest_sha256: str,
) -> RegionPoseEvidence:
    """Parse v1 evidence against non-self-authenticating external bindings."""

    expected = RegionPoseExpectedBindings(
        evidence_sha256=expected_evidence_sha256,
        family_id=expected_family_id,
        family_input_sha256=expected_family_input_sha256,
        source_snapshot_sha256=expected_source_snapshot_sha256,
        control_snapshot_sha256=expected_control_snapshot_sha256,
        region_key=expected_region_key,
        region_manifest_sha256=expected_region_manifest_sha256,
    )
    parsed = _parse_core(payload)
    _require(parsed.evidence_sha256 == expected.evidence_sha256, "external evidence SHA-256 mismatch")
    _require(parsed.family["family_id"] == expected.family_id, "external family id binding mismatch")
    _require(
        parsed.family["family_input_sha256"] == expected.family_input_sha256,
        "external family input binding mismatch",
    )
    _require(
        parsed.family["source_snapshot_sha256"] == expected.source_snapshot_sha256,
        "external source snapshot binding mismatch",
    )
    _require(
        parsed.family["control_snapshot_sha256"] == expected.control_snapshot_sha256,
        "external control snapshot binding mismatch",
    )
    _require(parsed.region["region_key"] == expected.region_key, "external region key binding mismatch")
    _require(
        parsed.region["region_manifest_sha256"] == expected.region_manifest_sha256,
        "external region manifest binding mismatch",
    )
    return parsed


@dataclass(frozen=True)
class RegionPoseFamilyBundle:
    family_id: str
    expected_region_keys: tuple[str, ...]
    entries: tuple[RegionPoseEvidence, ...]
    bundle_sha256: str

    def to_payload(self) -> dict[str, Any]:
        return _copy_json({
            "schema_version": 1,
            "kind": "region-pose-family-bundle-v1",
            "status": "proven",
            "family_id": self.family_id,
            "expected_region_keys": list(self.expected_region_keys),
            "entries": [entry.to_payload() for entry in self.entries],
            "bundle_sha256": self.bundle_sha256,
        })


def _canonical_region_keys(value: Iterable[str]) -> tuple[str, ...]:
    try:
        keys = tuple(value)
    except TypeError as exc:
        raise ValueError("expected region keys must be iterable") from exc
    _require(bool(keys), "expected region keys must not be empty")
    for key in keys:
        _text(key, "expected region key")
    _require(keys == tuple(sorted(keys)) and len(keys) == len(set(keys)), "expected region keys are not canonical and unique")
    return keys


def _entry_payload(value: RegionPoseEvidence | Mapping[str, Any]) -> object:
    if isinstance(value, RegionPoseEvidence):
        return value.to_payload()
    return value


def build_region_pose_family_bundle(
    *,
    family_id: str,
    expected_region_keys: Iterable[str],
    entries: Iterable[RegionPoseEvidence | Mapping[str, Any]],
) -> RegionPoseFamilyBundle:
    """Build a canonical family bundle with exactly one proof per region key."""

    _text(family_id, "family id")
    keys = _canonical_region_keys(expected_region_keys)
    try:
        parsed = tuple(_parse_core(_entry_payload(entry)) for entry in entries)
    except TypeError as exc:
        raise ValueError("family entries must be iterable") from exc
    _require(bool(parsed), "family entries must not be empty")
    _require(
        all(item.family["family_id"] == family_id for item in parsed),
        "family bundle contains a proof for another family",
    )
    actual_keys = tuple(item.region["region_key"] for item in parsed)
    _require(len(actual_keys) == len(set(actual_keys)), "family bundle contains duplicate region proofs")
    _require(set(actual_keys) == set(keys), "family bundle region coverage differs from external expectation")
    ordered = tuple(sorted(parsed, key=lambda item: item.region["region_key"]))
    unsigned = {
        "schema_version": 1,
        "kind": "region-pose-family-bundle-v1",
        "status": "proven",
        "family_id": family_id,
        "expected_region_keys": list(keys),
        "entries": [entry.to_payload() for entry in ordered],
    }
    return RegionPoseFamilyBundle(family_id, keys, ordered, _hash(unsigned))


def parse_region_pose_family_bundle(
    payload: object,
    *,
    expected_bundle_sha256: str,
    expected_family_id: str,
    expected_region_keys: Iterable[str],
    expected_bindings: Mapping[str, RegionPoseExpectedBindings],
) -> RegionPoseFamilyBundle:
    """Parse a family bundle while checking every entry against external inputs."""

    root = _mapping(payload, _BUNDLE_KEYS, "region pose family bundle")
    _require(root["schema_version"] == 1, "unsupported region pose family bundle schema")
    _require(root["kind"] == "region-pose-family-bundle-v1", "region pose family bundle kind is invalid")
    _require(root["status"] == "proven", "region pose family bundle is not proven")
    _text(expected_family_id, "expected family id")
    _require(root["family_id"] == expected_family_id, "external family bundle id mismatch")
    keys = _canonical_region_keys(expected_region_keys)
    _require(root["expected_region_keys"] == list(keys), "external family region universe mismatch")
    _sha(expected_bundle_sha256, "expected bundle SHA-256")
    actual_bundle_sha = _sha(root["bundle_sha256"], "bundle_sha256")
    _require(
        actual_bundle_sha == canonical_region_pose_family_bundle_hash(root),
        "family bundle seal mismatch",
    )
    _require(actual_bundle_sha == expected_bundle_sha256, "external family bundle SHA-256 mismatch")
    _require(isinstance(expected_bindings, Mapping), "external region bindings must be an object")
    _require(set(expected_bindings) == set(keys), "external region bindings are incomplete")
    raw_entries = root["entries"]
    _require(isinstance(raw_entries, list), "family bundle entries must be a list")
    _require(len(raw_entries) == len(keys), "family bundle entry coverage is incomplete")
    parsed: list[RegionPoseEvidence] = []
    seen: list[str] = []
    for index, raw in enumerate(raw_entries):
        _require(isinstance(raw, Mapping), f"family bundle entry {index} must be an object")
        raw_region = raw.get("region")
        _require(isinstance(raw_region, Mapping), f"family bundle entry {index} region is invalid")
        key = raw_region.get("region_key")
        _require(isinstance(key, str) and key in expected_bindings, f"family bundle entry {index} region is unexpected")
        binding = expected_bindings[key]
        _require(isinstance(binding, RegionPoseExpectedBindings), "external region binding type is invalid")
        parsed.append(parse_region_pose_evidence(
            raw,
            expected_evidence_sha256=binding.evidence_sha256,
            expected_family_id=binding.family_id,
            expected_family_input_sha256=binding.family_input_sha256,
            expected_source_snapshot_sha256=binding.source_snapshot_sha256,
            expected_control_snapshot_sha256=binding.control_snapshot_sha256,
            expected_region_key=binding.region_key,
            expected_region_manifest_sha256=binding.region_manifest_sha256,
        ))
        seen.append(key)
    _require(tuple(seen) == keys and len(seen) == len(set(seen)), "family bundle entries are not canonical and unique")
    return RegionPoseFamilyBundle(expected_family_id, keys, tuple(parsed), actual_bundle_sha)


__all__ = [
    "RegionPoseEvidence", "RegionPoseExpectedBindings", "RegionPoseFamilyBundle",
    "build_region_pose_evidence", "parse_region_pose_evidence",
    "build_region_pose_family_bundle", "parse_region_pose_family_bundle",
    "canonical_region_pose_evidence_hash",
    "canonical_region_pose_family_bundle_hash",
]
