from __future__ import annotations

import copy
import hashlib
import json
import unittest


def _sha(marker: str) -> str:
    return hashlib.sha256(marker.encode()).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


FAMILY_ID = "dodge_charger"
REGION_KEY = "body:31"
FAMILY_INPUT = _sha("family-input")
SOURCE = _sha("source")
CONTROL = _sha("control")
REGION_MANIFEST = _sha("region-manifest")
TOOLCHAIN = _sha("toolchain")


def _contracts() -> dict:
    value = {
        "contract_kind": "exact-region-pose-contracts-v1",
        "renderer_contract_sha256": _sha("renderer-contract"),
        "action_contract_sha256": _sha("action-contract"),
        "pixel_gate_contract_sha256": _sha("pixel-contract"),
        "toolchain_sha256": TOOLCHAIN,
    }
    return {**value, "contracts_sha256": _hash(value)}


def _caps() -> dict:
    value = {
        "max_regions_per_family": 64,
        "required_view_count": 8,
        "required_width": 512,
        "required_height": 512,
        "max_total_pixels": 8 * 512 * 512,
        "minimum_changed_fraction": 0.0001,
    }
    return {**value, "caps_sha256": _hash(value)}


def _selector(
    mode: str = "bind-animation", *, region_manifest: str = REGION_MANIFEST,
) -> dict:
    animation_inventory = [
        {
            "animation_relative_path": "anims/door.smd",
            "animation_sha256": _sha("door-animation"),
            "reference_relative_path": "anims/door_ref.smd",
            "reference_sha256": _sha("door-reference"),
        }
    ]
    geometry_inventory = [
        {"relative_path": "parts/body31.smd", "sha256": _sha("body31-geometry")}
    ]
    candidate = {
        "pose_key": "door-open:17",
        "animation_sha256": _sha("door-animation"),
        "reference_sha256": _sha("door-reference"),
        "frame_ordinal": 17,
        "source_time": 17,
        "bone_index": 4,
        "bone_name": "door_l",
        "displacement": 28.5,
        "action_name": "door_open",
        "action_source_sha256": _sha("door-animation"),
    }
    common = {
        "decision_kind": "exact-region-pose-decision-v1",
        "mode": mode,
        "algorithm": "exact-region-lineage-displacement-v2",
        "candidate_inputs_consulted": False,
        "family_input_sha256": FAMILY_INPUT,
        "region_manifest_sha256": region_manifest,
        "contracts_sha256": _contracts()["contracts_sha256"],
        "toolchain_sha256": TOOLCHAIN,
        "animation_inventory": animation_inventory,
        "geometry_inventory": geometry_inventory,
    }
    selector_input = _hash(common)
    selected = candidate if mode == "bind-animation" else None
    attempted = candidate if mode == "bind-only/no-visible-displacement" else None
    reason_source = _sha("decoded-no-visible-proof") if attempted else None
    unsigned = {
        **common,
        "selector_input_sha256": selector_input,
        "selected": selected,
        "attempted": attempted,
        "reason_source_proof_sha256": reason_source,
    }
    return {**unsigned, "selection_sha256": _hash(unsigned)}


def _action(*, region_manifest: str = REGION_MANIFEST) -> dict:
    selected = _selector(region_manifest=region_manifest)["selected"]
    unsigned = {
        "proof_kind": "blender-action-proof-v1",
        "family_input_sha256": FAMILY_INPUT,
        "region_manifest_sha256": region_manifest,
        "contracts_sha256": _contracts()["contracts_sha256"],
        "toolchain_sha256": TOOLCHAIN,
        "selection_sha256": _selector(region_manifest=region_manifest)["selection_sha256"],
        "action_name": selected["action_name"],
        "action_source_sha256": selected["action_source_sha256"],
        "frame_ordinal": selected["frame_ordinal"],
        "source_time": selected["source_time"],
        "slot_index": 0,
        "slot_sha256": _sha("action-slot"),
        "fcurve_inventory_sha256": _sha("fcurves"),
        "evaluated_pose_sha256": _sha("evaluated-pose"),
        "scene_sha256": _sha("scene"),
    }
    return {**unsigned, "proof_sha256": _hash(unsigned)}


DIRECTIONS = {
    "front": [0.0, -1.0, 0.0], "back": [0.0, 1.0, 0.0],
    "left": [-1.0, 0.0, 0.0], "right": [1.0, 0.0, 0.0],
    "top": [0.0, 0.0, 1.0], "bottom": [0.0, 0.0, -1.0],
    "front_left": [-0.7071067811865476, -0.7071067811865476, 0.0],
    "rear_right": [0.7071067811865476, 0.7071067811865476, 0.0],
}


def _pixel(*, region_manifest: str = REGION_MANIFEST) -> dict:
    views = []
    for key, direction in DIRECTIONS.items():
        views.append({
            "view_key": key, "direction": direction, "width": 512, "height": 512,
            "bind_rgba_sha256": _sha(f"bind-{key}"),
            "posed_first_rgba_sha256": _sha(f"posed-{key}"),
            "posed_repeat_rgba_sha256": _sha(f"posed-{key}"),
            "foreground_pixels": 100_000,
            "changed_pixels_first": 10_000,
            "changed_pixels_repeat": 10_000,
            "changed_fraction_first": 0.1,
            "changed_fraction_repeat": 0.1,
            "mean_absolute_error_first": 2.5,
            "mean_absolute_error_repeat": 2.5,
        })
    bind_bundle = _hash([
        {"view_key": item["view_key"], "rgba_sha256": item["bind_rgba_sha256"]}
        for item in views
    ])
    first_bundle = _hash([
        {"view_key": item["view_key"], "rgba_sha256": item["posed_first_rgba_sha256"]}
        for item in views
    ])
    repeat_bundle = _hash([
        {"view_key": item["view_key"], "rgba_sha256": item["posed_repeat_rgba_sha256"]}
        for item in views
    ])
    unsigned = {
        "proof_kind": "decoded-rgba-pixel-gate-v1",
        "family_input_sha256": FAMILY_INPUT,
        "region_manifest_sha256": region_manifest,
        "contracts_sha256": _contracts()["contracts_sha256"],
        "toolchain_sha256": TOOLCHAIN,
        "caps_sha256": _caps()["caps_sha256"],
        "selection_sha256": _selector(region_manifest=region_manifest)["selection_sha256"],
        "scene_sha256": _sha("scene"),
        "bind_decoded_bundle_sha256": bind_bundle,
        "first_decoded_bundle_sha256": first_bundle,
        "repeat_decoded_bundle_sha256": repeat_bundle,
        "qualifying_orthogonal_pair": ["front", "right"],
        "views": views,
    }
    return {**unsigned, "proof_sha256": _hash(unsigned)}


def _build(*, region_key: str = REGION_KEY, mode: str = "bind-animation", **family_overrides):
    from maximum_optimizer.region_pose_evidence import build_region_pose_evidence

    manifest = REGION_MANIFEST if region_key == REGION_KEY else _sha(region_key)
    bind = mode != "bind-animation"
    return build_region_pose_evidence(
        family_id=family_overrides.get("family_id", FAMILY_ID),
        family_input_sha256=family_overrides.get("family_input_sha256", FAMILY_INPUT),
        source_snapshot_sha256=family_overrides.get("source_snapshot_sha256", SOURCE),
        control_snapshot_sha256=family_overrides.get("control_snapshot_sha256", CONTROL),
        region_key=region_key, region_manifest_sha256=manifest,
        contracts=_contracts(), selector=_selector(mode, region_manifest=manifest),
        blender_action_proof=None if bind else _action(region_manifest=manifest),
        pixel_gate=None if bind else _pixel(region_manifest=manifest), caps=_caps(),
    )


def _expected(proof):
    from maximum_optimizer.region_pose_evidence import RegionPoseExpectedBindings

    payload = proof.to_payload()
    return RegionPoseExpectedBindings(
        evidence_sha256=payload["evidence_sha256"], family_id=payload["family"]["family_id"],
        family_input_sha256=payload["family"]["family_input_sha256"],
        source_snapshot_sha256=payload["family"]["source_snapshot_sha256"],
        control_snapshot_sha256=payload["family"]["control_snapshot_sha256"],
        region_key=payload["region"]["region_key"],
        region_manifest_sha256=payload["region"]["region_manifest_sha256"],
        contracts_sha256=payload["contracts"]["contracts_sha256"],
        toolchain_sha256=payload["contracts"]["toolchain_sha256"],
        caps_sha256=payload["caps"]["caps_sha256"],
    )


def _parse(payload):
    from maximum_optimizer.region_pose_evidence import parse_region_pose_evidence

    binding = _expected(type("Proof", (), {"to_payload": lambda _self: payload})())
    return parse_region_pose_evidence(
        payload, expected_evidence_sha256=binding.evidence_sha256,
        expected_family_id=binding.family_id,
        expected_family_input_sha256=binding.family_input_sha256,
        expected_source_snapshot_sha256=binding.source_snapshot_sha256,
        expected_control_snapshot_sha256=binding.control_snapshot_sha256,
        expected_region_key=binding.region_key,
        expected_region_manifest_sha256=binding.region_manifest_sha256,
        expected_contracts_sha256=binding.contracts_sha256,
        expected_toolchain_sha256=binding.toolchain_sha256,
        expected_caps_sha256=binding.caps_sha256,
    )


class RegionPoseEvidenceTests(unittest.TestCase):
    def test_schema_seals_derived_selection_and_returns_deeply_immutable_data(self):
        payload = _build().to_payload()
        parsed = _parse(payload)
        selector = payload["selector"]
        self.assertEqual(
            selector["selection_sha256"],
            _hash({k: v for k, v in selector.items() if k != "selection_sha256"}),
        )
        self.assertEqual(parsed.to_payload(), payload)
        with self.assertRaises(TypeError):
            parsed.family["family_id"] = "transplant"
        with self.assertRaises(TypeError):
            parsed.selector["selected"]["frame_ordinal"] = 18
        with self.assertRaises(TypeError):
            parsed.pixel_gate["views"][0]["width"] = 1

    def test_selector_rejects_lossy_schema_inventory_and_underived_selection(self):
        from maximum_optimizer.region_pose_evidence import build_region_pose_evidence

        base = _build().to_payload()
        mutations = []
        for mutation in ("missing", "extra", "selection", "inventory-order", "duplicate"):
            changed = copy.deepcopy(base)
            selector = changed["selector"]
            if mutation == "missing": selector.pop("geometry_inventory")
            elif mutation == "extra": selector["lossy"] = True
            elif mutation == "selection": selector["selected"]["frame_ordinal"] = 18
            elif mutation == "inventory-order":
                selector["geometry_inventory"] += [{"relative_path": "a.smd", "sha256": _sha("a") }]
            else: selector["animation_inventory"] *= 2
            mutations.append(changed)
        for changed in mutations:
            with self.subTest(selector=changed["selector"]):
                with self.assertRaises(ValueError):
                    build_region_pose_evidence(
                        family_id=FAMILY_ID, family_input_sha256=FAMILY_INPUT,
                        source_snapshot_sha256=SOURCE, control_snapshot_sha256=CONTROL,
                        region_key=REGION_KEY, region_manifest_sha256=REGION_MANIFEST,
                        contracts=changed["contracts"], selector=changed["selector"],
                        blender_action_proof=changed["blender_action_proof"],
                        pixel_gate=changed["pixel_gate"], caps=changed["caps"],
                    )

    def test_bind_only_is_subsealed_and_no_visible_preserves_attempted_selection(self):
        for mode in (
            "bind-only/no-eligible-animation", "bind-only/no-influenced-bone-delta",
            "bind-only/rigid-region", "bind-only/no-visible-displacement",
        ):
            parsed = _parse(_build(mode=mode).to_payload())
            self.assertEqual(parsed.selector["mode"], mode)
            if mode == "bind-only/no-visible-displacement":
                self.assertIsNotNone(parsed.selector["attempted"])
                self.assertIsNotNone(parsed.selector["reason_source_proof_sha256"])
            else:
                self.assertIsNone(parsed.selector["attempted"])
        changed = _selector("bind-only/no-visible-displacement")
        changed["attempted"] = None
        unsigned = {k: v for k, v in changed.items() if k != "selection_sha256"}
        changed["selection_sha256"] = _hash(unsigned)
        base = _build(mode="bind-only/no-visible-displacement").to_payload()
        from maximum_optimizer.region_pose_evidence import build_region_pose_evidence
        with self.assertRaises(ValueError):
            build_region_pose_evidence(
                family_id=FAMILY_ID, family_input_sha256=FAMILY_INPUT,
                source_snapshot_sha256=SOURCE, control_snapshot_sha256=CONTROL,
                region_key=REGION_KEY, region_manifest_sha256=REGION_MANIFEST,
                contracts=base["contracts"], selector=changed,
                blender_action_proof=None, pixel_gate=None, caps=base["caps"],
            )

    def test_action_requires_slot_fcurve_evaluated_and_matches_selected_action(self):
        from maximum_optimizer.region_pose_evidence import build_region_pose_evidence

        base = _build().to_payload()
        for field in ("slot_sha256", "fcurve_inventory_sha256", "evaluated_pose_sha256"):
            changed = copy.deepcopy(base)
            changed["blender_action_proof"].pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_region_pose_evidence(
                    family_id=FAMILY_ID, family_input_sha256=FAMILY_INPUT,
                    source_snapshot_sha256=SOURCE, control_snapshot_sha256=CONTROL,
                    region_key=REGION_KEY, region_manifest_sha256=REGION_MANIFEST,
                    contracts=changed["contracts"], selector=changed["selector"],
                    blender_action_proof=changed["blender_action_proof"],
                    pixel_gate=changed["pixel_gate"], caps=changed["caps"],
                )
        changed = copy.deepcopy(base)
        changed["blender_action_proof"]["action_name"] = "wrong_action"
        action = changed["blender_action_proof"]
        action["proof_sha256"] = _hash({k: v for k, v in action.items() if k != "proof_sha256"})
        with self.assertRaises(ValueError):
            _parse({**changed, "evidence_sha256": _hash({k: v for k, v in changed.items() if k != "evidence_sha256"})})

    def test_pixel_gate_requires_eight_512_views_orthogonal_pair_and_exact_repeat(self):
        from maximum_optimizer.region_pose_evidence import build_region_pose_evidence

        base = _build().to_payload()
        mutations = []
        one = copy.deepcopy(base); one["pixel_gate"]["views"] = one["pixel_gate"]["views"][:1]; mutations.append(one)
        tiny = copy.deepcopy(base); tiny["pixel_gate"]["views"][0]["width"] = 1; mutations.append(tiny)
        diagonal = copy.deepcopy(base); diagonal["pixel_gate"]["qualifying_orthogonal_pair"] = ["front", "back"]; mutations.append(diagonal)
        nochange = copy.deepcopy(base); nochange["pixel_gate"]["views"][0]["changed_pixels_repeat"] = 0; mutations.append(nochange)
        repeat = copy.deepcopy(base); repeat["pixel_gate"]["views"][0]["posed_repeat_rgba_sha256"] = _sha("different-repeat"); mutations.append(repeat)
        for changed in mutations:
            gate = changed["pixel_gate"]
            gate["proof_sha256"] = _hash({k: v for k, v in gate.items() if k != "proof_sha256"})
            with self.subTest(gate=gate), self.assertRaises(ValueError):
                build_region_pose_evidence(
                    family_id=FAMILY_ID, family_input_sha256=FAMILY_INPUT,
                    source_snapshot_sha256=SOURCE, control_snapshot_sha256=CONTROL,
                    region_key=REGION_KEY, region_manifest_sha256=REGION_MANIFEST,
                    contracts=changed["contracts"], selector=changed["selector"],
                    blender_action_proof=changed["blender_action_proof"],
                    pixel_gate=gate, caps=changed["caps"],
                )

    def test_external_contract_toolchain_and_caps_bindings_reject_self_reseal(self):
        from maximum_optimizer.region_pose_evidence import parse_region_pose_evidence

        payload = _build().to_payload()
        binding = _expected(_build())
        common = dict(
            expected_evidence_sha256=payload["evidence_sha256"],
            expected_family_id=FAMILY_ID, expected_family_input_sha256=FAMILY_INPUT,
            expected_source_snapshot_sha256=SOURCE, expected_control_snapshot_sha256=CONTROL,
            expected_region_key=REGION_KEY, expected_region_manifest_sha256=REGION_MANIFEST,
            expected_contracts_sha256=binding.contracts_sha256,
            expected_toolchain_sha256=binding.toolchain_sha256,
            expected_caps_sha256=binding.caps_sha256,
        )
        for field in ("expected_contracts_sha256", "expected_toolchain_sha256", "expected_caps_sha256"):
            args = {**common, field: _sha("wrong-" + field)}
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_region_pose_evidence(payload, **args)


class RegionPoseFamilyBundleTests(unittest.TestCase):
    def _proofs(self, count=2):
        return [_build(region_key=f"body:{index:02d}") for index in range(count)]

    def _bindings(self, proofs):
        return {proof.region["region_key"]: _expected(proof) for proof in proofs}

    def test_bundle_has_common_tuple_canonical_order_and_external_parity(self):
        from maximum_optimizer.region_pose_evidence import (
            build_region_pose_family_bundle, parse_region_pose_family_bundle,
        )
        proofs = self._proofs()
        keys = tuple(sorted(proof.region["region_key"] for proof in proofs))
        bundle = build_region_pose_family_bundle(
            family_id=FAMILY_ID, expected_region_keys=keys, entries=reversed(proofs),
        )
        payload = bundle.to_payload()
        self.assertEqual(payload["kind"], "region-pose-family-bundle-v1")
        self.assertEqual(payload["family_common"]["family_input_sha256"], FAMILY_INPUT)
        parsed = parse_region_pose_family_bundle(
            payload, expected_bundle_sha256=payload["bundle_sha256"],
            expected_family_id=FAMILY_ID, expected_region_keys=keys,
            expected_bindings=self._bindings(proofs),
        )
        self.assertEqual(parsed.to_payload(), payload)

    def test_bundle_rejects_missing_extra_duplicate_hybrid_and_cross_family_binding(self):
        from maximum_optimizer.region_pose_evidence import build_region_pose_family_bundle

        proofs = self._proofs()
        keys = tuple(sorted(proof.region["region_key"] for proof in proofs))
        cases = [
            (keys, proofs[:1]), (keys[:1], proofs), (keys, [proofs[0], proofs[0]]),
            (keys, [proofs[0], _build(region_key=keys[1], source_snapshot_sha256=_sha("hybrid"))]),
            (keys, [proofs[0], _build(region_key=keys[1], family_id="toyota_supra")]),
        ]
        for expected_keys, entries in cases:
            with self.subTest(expected_keys=expected_keys), self.assertRaises(ValueError):
                build_region_pose_family_bundle(
                    family_id=FAMILY_ID, expected_region_keys=expected_keys, entries=entries,
                )

    def test_builder_and_parser_both_reject_sixty_five_regions(self):
        from maximum_optimizer.region_pose_evidence import (
            build_region_pose_family_bundle, parse_region_pose_family_bundle,
        )

        proofs = self._proofs(65)
        keys = tuple(sorted(proof.region["region_key"] for proof in proofs))
        with self.assertRaises(ValueError):
            build_region_pose_family_bundle(
                family_id=FAMILY_ID, expected_region_keys=keys, entries=proofs,
            )
        valid = self._proofs(2)
        valid_keys = tuple(sorted(item.region["region_key"] for item in valid))
        payload = build_region_pose_family_bundle(
            family_id=FAMILY_ID, expected_region_keys=valid_keys, entries=valid,
        ).to_payload()
        changed = copy.deepcopy(payload)
        changed["expected_region_keys"] = list(keys)
        changed["bundle_sha256"] = _hash({k: v for k, v in changed.items() if k != "bundle_sha256"})
        with self.assertRaises(ValueError):
            parse_region_pose_family_bundle(
                changed, expected_bundle_sha256=changed["bundle_sha256"],
                expected_family_id=FAMILY_ID, expected_region_keys=keys,
                expected_bindings=self._bindings(proofs),
            )

    def test_bundle_parser_rejects_cross_family_and_hybrid_external_bindings(self):
        from maximum_optimizer.region_pose_evidence import (
            RegionPoseExpectedBindings, build_region_pose_family_bundle,
            parse_region_pose_family_bundle,
        )

        proofs = self._proofs()
        keys = tuple(sorted(item.region["region_key"] for item in proofs))
        payload = build_region_pose_family_bundle(
            family_id=FAMILY_ID, expected_region_keys=keys, entries=proofs,
        ).to_payload()
        for field, value in (
            ("family_id", "toyota_supra"),
            ("source_snapshot_sha256", _sha("hybrid-external-source")),
            ("contracts_sha256", _sha("hybrid-external-contracts")),
        ):
            bindings = self._bindings(proofs)
            original = bindings[keys[1]]
            bindings[keys[1]] = RegionPoseExpectedBindings(
                **{**original.__dict__, field: value}
            )
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_region_pose_family_bundle(
                    payload, expected_bundle_sha256=payload["bundle_sha256"],
                    expected_family_id=FAMILY_ID, expected_region_keys=keys,
                    expected_bindings=bindings,
                )


if __name__ == "__main__":
    unittest.main()
