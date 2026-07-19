from __future__ import annotations

import copy
import hashlib
import json

import pytest


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _fixture(*, no_visible: bool = False):
    import render_previews
    from maximum_optimizer.region_pose_render_request import (
        build_region_pose_render_request,
    )
    from tests.maximum_optimizer.test_region_pose_evidence import (
        _action, _animation_selection, _caps, _contracts, _evaluated,
        _no_visible_pixel_evidence, _pixel_evidence,
    )

    region_key = "r-" + "1" * 64
    region_manifest = _sha("body31")
    contracts = _contracts()
    caps = _caps()
    request = build_region_pose_render_request(
        family_id="charger",
        family_input_sha256=_sha("family"),
        source_snapshot_sha256=_sha("snapshot"),
        catalog_sha256=_sha("catalog"),
        region_key=region_key,
        region_manifest_sha256=region_manifest,
        source_identity="body.smd",
        contracts_sha256=contracts["contracts_sha256"],
        caps_sha256=caps["caps_sha256"],
        selection_payload=_animation_selection(),
    )
    pixels = (
        _no_visible_pixel_evidence(region_manifest=region_manifest)
        if no_visible else _pixel_evidence(region_manifest=region_manifest)
    )
    producer = render_previews._build_source_pose_producer_payload(
        request,
        action_proof=_action(),
        evaluated_region_proof=_evaluated(),
        pixel_evidence=pixels,
    )
    return request, producer, contracts, caps


def test_positive_source_producer_finalizes_canonical_region_pose_evidence():
    from maximum_optimizer.region_pose_producer import finalize_region_pose_producer

    request, producer, contracts, caps = _fixture()
    result = finalize_region_pose_producer(
        request=request,
        producer_payload=producer,
        expected_producer_sha256=producer["evidence_sha256"],
        contracts=contracts,
        caps=caps,
    ).to_payload()

    request_payload = request.to_payload()
    assert result["selector_decision"]["mode"] == "bind-animation"
    assert result["selector_decision"]["selection_payload"] == request_payload["selection_payload"]
    assert result["family"]["control_snapshot_sha256"] == request_payload["catalog_sha256"]
    assert result["action_proof"] == producer["action_proof"]
    assert result["pixel_evidence"] == producer["pixel_evidence"]


def test_no_visible_source_producer_falls_back_to_bind_with_complete_reason():
    from maximum_optimizer.region_pose_producer import finalize_region_pose_producer

    request, producer, contracts, caps = _fixture(no_visible=True)
    result = finalize_region_pose_producer(
        request=request,
        producer_payload=producer,
        expected_producer_sha256=producer["evidence_sha256"],
        contracts=contracts,
        caps=caps,
    ).to_payload()

    decision = result["selector_decision"]
    assert decision["mode"] == "bind-only/no-visible-displacement"
    assert decision["selection_payload"]["pose_keys"] == ["bind"]
    assert decision["attempted_selection_payload"] == request.to_payload()["selection_payload"]
    assert decision["decision_proof"]["pixel_evidence_sha256"] == producer["pixel_evidence"]["evidence_sha256"]


def test_source_producer_rejects_extra_fields_resealed_tamper_and_wrong_external_hash():
    from maximum_optimizer.region_pose_producer import finalize_region_pose_producer

    request, producer, contracts, caps = _fixture()
    extra = copy.deepcopy(producer)
    extra["candidate_path"] = "candidate/body.smd"
    changed = copy.deepcopy(producer)
    changed["region_key"] = "r-" + "2" * 64
    changed["evidence_sha256"] = _hash({
        key: value for key, value in changed.items() if key != "evidence_sha256"
    })

    for payload, expected in (
        (extra, producer["evidence_sha256"]),
        (changed, changed["evidence_sha256"]),
        (producer, "f" * 64),
    ):
        with pytest.raises(ValueError):
            finalize_region_pose_producer(
                request=request,
                producer_payload=payload,
                expected_producer_sha256=expected,
                contracts=contracts,
                caps=caps,
            )


def test_default_caps_and_contracts_are_canonical_and_toolchain_bound():
    from maximum_optimizer.region_pose_producer import (
        build_region_pose_caps, build_region_pose_contracts,
    )

    caps = build_region_pose_caps()
    first = build_region_pose_contracts("a" * 64)
    repeat = build_region_pose_contracts("a" * 64)
    changed = build_region_pose_contracts("b" * 64)

    assert caps["caps_sha256"] == "c3a04a53e1c9e605dfba49ce8695e396348312619e99ebab6e873a7c024d96a5"
    assert first == repeat
    assert first["contracts_sha256"] != changed["contracts_sha256"]
    assert first["toolchain_sha256"] == "a" * 64
