from __future__ import annotations

import copy
import hashlib
import json


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _selection() -> dict[str, object]:
    unsigned = {
        "animation_relative_path": "anims/hood.smd",
        "animation_sha256": _sha("animation"),
        "baseline": "geometry-rest",
        "bone_index": 1,
        "bone_name": "Hood",
        "candidate_inputs_consulted": False,
        "corrective_used_as_baseline": False,
        "displacement": 4.0,
        "displacement_squared": "16",
        "frame": 1,
        "geometry_inventory_sha256": _sha("geometry"),
        "pose_name": "hood",
        "reference_relative_path": "anims/hood_ref.smd",
        "reference_sha256": _sha("reference"),
        "raw_render_max_displacement": 4.0,
        "raw_render_rms_displacement": 2.0,
        "rms_displacement": 2.0,
        "selector": "exact-raw-render-region-displacement-v3",
        "selector_input_sha256": _sha("selector-input"),
        "source_time": 17,
    }
    return {**unsigned, "selection_sha256": _hash(unsigned)}


def _build():
    from maximum_optimizer.region_pose_render_request import (
        build_region_pose_render_request,
    )

    return build_region_pose_render_request(
        family_id="charger",
        family_input_sha256=_sha("family"),
        source_snapshot_sha256=_sha("snapshot"),
        catalog_sha256=_sha("catalog"),
        region_key="r-" + "1" * 64,
        region_manifest_sha256=_sha("manifest"),
        source_identity="models/charger/body.smd",
        contracts_sha256=_sha("contracts"),
        caps_sha256=_sha("caps"),
        selection_payload=_selection(),
    )


def test_request_is_source_only_strict_and_externally_bound():
    from maximum_optimizer.region_pose_render_request import (
        parse_region_pose_render_request,
    )

    request = _build()
    payload = request.to_payload()
    assert payload["candidate_inputs_consulted"] is False
    assert not any("candidate" in key for key in payload if key != "candidate_inputs_consulted")
    parsed = parse_region_pose_render_request(
        payload,
        expected_request_sha256=payload["request_sha256"],
        expected_catalog_sha256=_sha("catalog"),
        expected_region_key="r-" + "1" * 64,
        expected_region_manifest_sha256=_sha("manifest"),
        expected_contracts_sha256=_sha("contracts"),
        expected_caps_sha256=_sha("caps"),
    )
    assert parsed == request


def test_request_rejects_candidate_fields_traversal_and_resealed_selection_tamper():
    from maximum_optimizer.region_pose_render_request import (
        parse_region_pose_render_request,
    )

    payload = _build().to_payload()
    mutations = []
    candidate = copy.deepcopy(payload)
    candidate["candidate_path"] = "candidate/body.smd"
    mutations.append(candidate)
    traversal = copy.deepcopy(payload)
    traversal["source_identity"] = "../body.smd"
    traversal["request_sha256"] = _hash({
        key: value for key, value in traversal.items() if key != "request_sha256"
    })
    mutations.append(traversal)
    selection = copy.deepcopy(payload)
    selection["selection_payload"]["frame"] = 2
    selection["request_sha256"] = _hash({
        key: value for key, value in selection.items() if key != "request_sha256"
    })
    mutations.append(selection)
    for changed in mutations:
        try:
            parse_region_pose_render_request(
                changed,
                expected_request_sha256=changed.get("request_sha256", payload["request_sha256"]),
                expected_catalog_sha256=_sha("catalog"),
                expected_region_key="r-" + "1" * 64,
                expected_region_manifest_sha256=_sha("manifest"),
                expected_contracts_sha256=_sha("contracts"),
                expected_caps_sha256=_sha("caps"),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid source-only pose request was accepted")
