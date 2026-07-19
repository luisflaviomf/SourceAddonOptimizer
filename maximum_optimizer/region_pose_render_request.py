"""Strict candidate-free request consumed by the Blender region-pose producer."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Mapping

from .region_pose_evidence import _hash, _validate_animation_selection


_ROOT_KEYS = {
    "schema", "kind", "candidate_inputs_consulted", "family_id",
    "family_input_sha256", "source_snapshot_sha256", "catalog_sha256",
    "region_key", "region_manifest_sha256", "source_identity",
    "contracts_sha256", "caps_sha256", "selection_payload",
    "request_sha256",
}
_SHA = re.compile(r"^[0-9a-f]{64}$")
_REGION = re.compile(r"^r-[0-9a-f]{64}$")


def _thaw(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _copy(value: object) -> Any:
    return json.loads(json.dumps(
        _thaw(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ))


def _freeze(value: object) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _sha(value: object, label: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise ValueError(f"{label} is not a lowercase SHA-256")
    return value


def _text(value: object, label: str) -> str:
    if (
        type(value) is not str or not value or value != value.strip()
        or len(value) > 4096 or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _relative(value: object, label: str) -> str:
    result = _text(value, label)
    path = PurePosixPath(result)
    if (
        path.is_absolute() or "\\" in result or ":" in result
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{label} is not a canonical relative path")
    return result


@dataclass(frozen=True)
class RegionPoseRenderRequest:
    _payload: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_payload", _freeze(_copy(self._payload)))

    def to_payload(self) -> dict[str, object]:
        return _copy(self._payload)


def _parse_core(payload: object, *, verify_seal: bool = True) -> RegionPoseRenderRequest:
    if not isinstance(payload, Mapping) or set(payload) != _ROOT_KEYS:
        raise ValueError("region pose render request schema is invalid")
    root = payload
    if (
        root["schema"] != 1
        or root["kind"] != "source-only-region-pose-render-request-v1"
        or root["candidate_inputs_consulted"] is not False
    ):
        raise ValueError("region pose render request contract differs")
    _text(root["family_id"], "request family id")
    if type(root["region_key"]) is not str or _REGION.fullmatch(root["region_key"]) is None:
        raise ValueError("request region key is invalid")
    _relative(root["source_identity"], "request source identity")
    for field in (
        "family_input_sha256", "source_snapshot_sha256", "catalog_sha256",
        "region_manifest_sha256", "contracts_sha256", "caps_sha256",
        "request_sha256",
    ):
        _sha(root[field], f"request {field}")
    _validate_animation_selection(root["selection_payload"], "request selection")
    if verify_seal:
        unsigned = {key: value for key, value in root.items() if key != "request_sha256"}
        if root["request_sha256"] != _hash(unsigned):
            raise ValueError("region pose render request seal differs")
    return RegionPoseRenderRequest(root)


def build_region_pose_render_request(
    *, family_id: str, family_input_sha256: str,
    source_snapshot_sha256: str, catalog_sha256: str,
    region_key: str, region_manifest_sha256: str, source_identity: str,
    contracts_sha256: str, caps_sha256: str,
    selection_payload: Mapping[str, object],
) -> RegionPoseRenderRequest:
    unsigned = {
        "schema": 1,
        "kind": "source-only-region-pose-render-request-v1",
        "candidate_inputs_consulted": False,
        "family_id": family_id,
        "family_input_sha256": family_input_sha256,
        "source_snapshot_sha256": source_snapshot_sha256,
        "catalog_sha256": catalog_sha256,
        "region_key": region_key,
        "region_manifest_sha256": region_manifest_sha256,
        "source_identity": source_identity,
        "contracts_sha256": contracts_sha256,
        "caps_sha256": caps_sha256,
        "selection_payload": _copy(selection_payload),
    }
    provisional = {**unsigned, "request_sha256": "0" * 64}
    _parse_core(provisional, verify_seal=False)
    return _parse_core({**unsigned, "request_sha256": _hash(unsigned)})


def parse_region_pose_render_request(
    payload: object, *, expected_request_sha256: str,
    expected_catalog_sha256: str, expected_region_key: str,
    expected_region_manifest_sha256: str, expected_contracts_sha256: str,
    expected_caps_sha256: str,
) -> RegionPoseRenderRequest:
    parsed = _parse_core(payload)
    root = parsed.to_payload()
    for value, label in (
        (expected_request_sha256, "expected request"),
        (expected_catalog_sha256, "expected catalog"),
        (expected_region_manifest_sha256, "expected region manifest"),
        (expected_contracts_sha256, "expected contracts"),
        (expected_caps_sha256, "expected caps"),
    ):
        _sha(value, label)
    if type(expected_region_key) is not str or _REGION.fullmatch(expected_region_key) is None:
        raise ValueError("expected region key is invalid")
    actual = (
        root["request_sha256"], root["catalog_sha256"], root["region_key"],
        root["region_manifest_sha256"], root["contracts_sha256"],
        root["caps_sha256"],
    )
    expected = (
        expected_request_sha256, expected_catalog_sha256, expected_region_key,
        expected_region_manifest_sha256, expected_contracts_sha256,
        expected_caps_sha256,
    )
    if actual != expected:
        raise ValueError("external region pose render request binding differs")
    return parsed


__all__ = [
    "RegionPoseRenderRequest", "build_region_pose_render_request",
    "parse_region_pose_render_request",
]
