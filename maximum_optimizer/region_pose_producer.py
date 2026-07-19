"""Finalize candidate-free Blender pose output into canonical region evidence."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
from typing import Any

from .region_pose_evidence import _hash, build_region_pose_evidence
from .region_pose_render_request import RegionPoseRenderRequest


_SHA = re.compile(r"^[0-9a-f]{64}$")
_PRODUCER_KEYS = {
    "schema", "kind", "candidate_inputs_consulted", "request_sha256",
    "catalog_sha256", "region_key", "source_identity", "selection_sha256",
    "action_proof", "evaluated_region_proof", "pixel_evidence",
    "evidence_sha256",
}


def _copy(value: object) -> Any:
    try:
        return json.loads(json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ))
    except (TypeError, ValueError) as exc:
        raise ValueError("source pose producer must be canonical JSON data") from exc


def _sealed(unsigned: Mapping[str, object], field: str) -> dict[str, object]:
    return {**_copy(unsigned), field: _hash(unsigned)}


def _validated_producer(
    request: RegionPoseRenderRequest,
    producer_payload: object,
    expected_producer_sha256: str,
) -> tuple[dict[str, object], dict[str, object]]:
    if not isinstance(request, RegionPoseRenderRequest):
        raise TypeError("request must be a RegionPoseRenderRequest")
    if type(expected_producer_sha256) is not str or _SHA.fullmatch(expected_producer_sha256) is None:
        raise ValueError("expected source pose producer seal is invalid")
    if not isinstance(producer_payload, Mapping) or set(producer_payload) != _PRODUCER_KEYS:
        raise ValueError("source pose producer schema is invalid")
    producer = _copy(producer_payload)
    if (
        producer["schema"] != 1
        or producer["kind"] != "blender-source-only-region-pose-producer-v1"
        or producer["candidate_inputs_consulted"] is not False
    ):
        raise ValueError("source pose producer contract differs")
    seal = producer["evidence_sha256"]
    if (
        type(seal) is not str or _SHA.fullmatch(seal) is None
        or seal != expected_producer_sha256
        or seal != _hash({key: value for key, value in producer.items() if key != "evidence_sha256"})
    ):
        raise ValueError("source pose producer seal differs")
    request_payload = request.to_payload()
    selection = request_payload["selection_payload"]
    expected = (
        request_payload["request_sha256"], request_payload["catalog_sha256"],
        request_payload["region_key"], request_payload["source_identity"],
        selection["selection_sha256"],
    )
    actual = tuple(producer[field] for field in (
        "request_sha256", "catalog_sha256", "region_key", "source_identity",
        "selection_sha256",
    ))
    if actual != expected:
        raise ValueError("source pose producer/request binding differs")
    if not all(isinstance(producer[field], Mapping) for field in (
        "action_proof", "evaluated_region_proof", "pixel_evidence",
    )):
        raise ValueError("source pose producer proof payload differs")
    return request_payload, producer


def _selector_decision(
    selection: Mapping[str, object],
    evaluated: Mapping[str, object],
    pixels: Mapping[str, object],
) -> dict[str, object]:
    pixel_kind = pixels.get("kind")
    if pixel_kind == "pose-pixel-family-evidence-v1":
        unsigned = {
            "mode": "bind-animation",
            "selection_payload": _copy(selection),
            "attempted_selection_payload": None,
            "decision_proof": None,
        }
        return _sealed(unsigned, "decision_sha256")
    if pixel_kind != "pose-pixel-no-visible-evidence-v1":
        raise ValueError("source pose producer pixel result kind differs")
    bind_unsigned = {
        "candidate_inputs_consulted": False,
        "geometry_inventory_sha256": selection["geometry_inventory_sha256"],
        "pose_keys": ["bind"],
        "reason": "no-exact-region-influencing-animation-displacement",
        "selector": "exact-raw-render-region-displacement-v3",
        "selector_input_sha256": selection["selector_input_sha256"],
    }
    bind = _sealed(bind_unsigned, "selection_sha256")
    reason_unsigned = {
        "kind": "no-visible-displacement-proof-v1",
        "animation_inventory": [{
            "animation_relative_path": selection["animation_relative_path"],
            "animation_sha256": selection["animation_sha256"],
            "reference_relative_path": selection["reference_relative_path"],
            "reference_sha256": selection["reference_sha256"],
        }],
        "geometry_inventory_sha256": selection["geometry_inventory_sha256"],
        "region_manifest_sha256": None,
        "attempted_selection_sha256": selection["selection_sha256"],
        "evaluated_region_proof_sha256": evaluated["proof_sha256"],
        "pixel_evidence_sha256": pixels["evidence_sha256"],
    }
    reason = _sealed(reason_unsigned, "proof_sha256")
    decision_unsigned = {
        "mode": "bind-only/no-visible-displacement",
        "selection_payload": bind,
        "attempted_selection_payload": _copy(selection),
        "decision_proof": reason,
    }
    return _sealed(decision_unsigned, "decision_sha256")


def finalize_region_pose_producer(
    *, request: RegionPoseRenderRequest, producer_payload: object,
    expected_producer_sha256: str, contracts: Mapping[str, object],
    caps: Mapping[str, object],
):
    """Validate source-only output and emit the externally bound final proof."""

    request_payload, producer = _validated_producer(
        request, producer_payload, expected_producer_sha256,
    )
    if not isinstance(contracts, Mapping) or not isinstance(caps, Mapping):
        raise TypeError("region pose contracts and caps must be mappings")
    if (
        contracts.get("contracts_sha256") != request_payload["contracts_sha256"]
        or caps.get("caps_sha256") != request_payload["caps_sha256"]
    ):
        raise ValueError("source pose request contracts/caps binding differs")
    selection = request_payload["selection_payload"]
    action = producer["action_proof"]
    evaluated = producer["evaluated_region_proof"]
    pixels = producer["pixel_evidence"]
    decision = _selector_decision(selection, evaluated, pixels)
    return build_region_pose_evidence(
        family_id=request_payload["family_id"],
        family_input_sha256=request_payload["family_input_sha256"],
        source_snapshot_sha256=request_payload["source_snapshot_sha256"],
        control_snapshot_sha256=request_payload["catalog_sha256"],
        region_key=request_payload["region_key"],
        region_manifest_sha256=request_payload["region_manifest_sha256"],
        contracts=contracts,
        selector_decision=decision,
        action_proof=action,
        evaluated_region_proof=evaluated,
        pixel_evidence=pixels,
        caps=caps,
    )


__all__ = ["finalize_region_pose_producer"]
