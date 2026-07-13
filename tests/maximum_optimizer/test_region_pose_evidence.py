from __future__ import annotations

import copy
import hashlib
import json
import unittest


def _sha(marker: str) -> str:
    return hashlib.sha256(marker.encode("utf-8")).hexdigest()


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


FAMILY_ID = "dodge_charger"
REGION_KEY = "body:31"
FAMILY_INPUT = _sha("family-input")
SOURCE_SNAPSHOT = _sha("source-snapshot")
CONTROL_SNAPSHOT = _sha("control-snapshot")
REGION_MANIFEST = _sha("region-manifest")


def _contracts() -> dict[str, object]:
    return {
        "contract_kind": "exact-region-pose-contracts-v1",
        "renderer_contract_sha256": _sha("renderer-contract"),
        "action_contract_sha256": _sha("action-contract"),
        "pixel_gate_contract_sha256": _sha("pixel-gate-contract"),
        "toolchain_sha256": _sha("toolchain"),
    }


def _selector(
    mode: str = "bind-animation", *, region_manifest: str = REGION_MANIFEST,
) -> dict[str, object]:
    value = {
        "mode": mode,
        "pose_key": "door-open:17" if mode == "bind-animation" else "bind",
        "family_input_sha256": FAMILY_INPUT,
        "region_manifest_sha256": region_manifest,
        "selector_input_sha256": _sha("selector-input"),
        "selection_sha256": _sha("selection"),
        "action_name": "door_open" if mode == "bind-animation" else None,
        "action_source_sha256": _sha("action-source") if mode == "bind-animation" else None,
        "frame_ordinal": 17 if mode == "bind-animation" else None,
        "source_time": 17 if mode == "bind-animation" else None,
        "source_proof_sha256": None,
    }
    if mode == "bind-only/no-visible-displacement":
        value["source_proof_sha256"] = _sha("no-visible-source-proof")
    return value


def _action(*, region_manifest: str = REGION_MANIFEST) -> dict[str, object]:
    unsigned = {
        "proof_kind": "blender-action-proof-v1",
        "family_input_sha256": FAMILY_INPUT,
        "region_manifest_sha256": region_manifest,
        "selection_sha256": _sha("selection"),
        "action_name": "door_open",
        "action_source_sha256": _sha("action-source"),
        "frame_ordinal": 17,
        "source_time": 17,
        "scene_sha256": _sha("scene"),
    }
    return {**unsigned, "proof_sha256": _canonical_hash(unsigned)}


def _pixel_gate(*, region_manifest: str = REGION_MANIFEST) -> dict[str, object]:
    unsigned = {
        "proof_kind": "decoded-rgba-pixel-gate-v1",
        "family_input_sha256": FAMILY_INPUT,
        "region_manifest_sha256": region_manifest,
        "selection_sha256": _sha("selection"),
        "scene_sha256": _sha("scene"),
        "bind_pixel_bundle_sha256": _sha("bind-pixels"),
        "posed_pixel_bundle_sha256": _sha("posed-pixels"),
        "image_count": 8,
        "total_pixels": 8 * 256 * 256,
        "foreground_pixels": 200_000,
        "changed_pixels": 20_000,
        "changed_fraction": 0.1,
        "mean_absolute_error": 2.5,
        "minimum_changed_fraction": 0.0001,
    }
    return {**unsigned, "proof_sha256": _canonical_hash(unsigned)}


def _caps() -> dict[str, object]:
    return {
        "max_images": 64,
        "max_total_pixels": 67_108_864,
        "minimum_changed_fraction": 0.0001,
    }


def _build(*, region_key: str = REGION_KEY, mode: str = "bind-animation"):
    from maximum_optimizer.region_pose_evidence import build_region_pose_evidence

    bind_only = mode != "bind-animation"
    region_manifest = REGION_MANIFEST if region_key == REGION_KEY else _sha(region_key)
    return build_region_pose_evidence(
        family_id=FAMILY_ID,
        family_input_sha256=FAMILY_INPUT,
        source_snapshot_sha256=SOURCE_SNAPSHOT,
        control_snapshot_sha256=CONTROL_SNAPSHOT,
        region_key=region_key,
        region_manifest_sha256=region_manifest,
        contracts=_contracts(),
        selector=_selector(mode, region_manifest=region_manifest),
        blender_action_proof=None if bind_only else _action(region_manifest=region_manifest),
        pixel_gate=None if bind_only else _pixel_gate(region_manifest=region_manifest),
        caps=_caps(),
    )


def _parse(payload: object, *, region_key: str = REGION_KEY, region_manifest: str = REGION_MANIFEST):
    from maximum_optimizer.region_pose_evidence import parse_region_pose_evidence

    return parse_region_pose_evidence(
        payload,
        expected_evidence_sha256=payload["evidence_sha256"],
        expected_family_id=FAMILY_ID,
        expected_family_input_sha256=FAMILY_INPUT,
        expected_source_snapshot_sha256=SOURCE_SNAPSHOT,
        expected_control_snapshot_sha256=CONTROL_SNAPSHOT,
        expected_region_key=region_key,
        expected_region_manifest_sha256=region_manifest,
    )


class RegionPoseEvidenceTests(unittest.TestCase):
    def test_builder_emits_exact_sealed_schema_and_parser_returns_dataclass(self) -> None:
        from maximum_optimizer.region_pose_evidence import RegionPoseEvidence

        evidence = _build()
        payload = evidence.to_payload()
        self.assertEqual(
            set(payload),
            {
                "schema_version", "kind", "status", "family", "region",
                "contracts", "selector", "blender_action_proof", "pixel_gate",
                "caps", "evidence_sha256",
            },
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["kind"], "region-pose-evidence-v1")
        self.assertEqual(payload["status"], "proven")
        self.assertEqual(
            payload["evidence_sha256"],
            _canonical_hash({k: v for k, v in payload.items() if k != "evidence_sha256"}),
        )
        parsed = _parse(payload)
        self.assertIsInstance(parsed, RegionPoseEvidence)
        self.assertEqual(parsed.to_payload(), payload)

    def test_parser_rejects_missing_extra_and_bad_component_schema(self) -> None:
        base = _build().to_payload()
        mutations = []
        missing = copy.deepcopy(base)
        missing.pop("caps")
        mutations.append(missing)
        extra = copy.deepcopy(base)
        extra["ignored"] = True
        mutations.append(extra)
        nested = copy.deepcopy(base)
        nested["contracts"]["ignored"] = _sha("ignored")
        mutations.append(nested)
        bad_caps = copy.deepcopy(base)
        bad_caps["caps"]["max_images"] = 0
        mutations.append(bad_caps)
        for changed in mutations:
            changed["evidence_sha256"] = _canonical_hash(
                {k: v for k, v in changed.items() if k != "evidence_sha256"}
            )
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    _parse(changed)

    def test_external_bindings_reject_transplant_even_after_self_reseal(self) -> None:
        payload = _build().to_payload()
        mutations = [
            ("family", "family_id", "toyota_supra"),
            ("family", "family_input_sha256", _sha("other-family-input")),
            ("family", "source_snapshot_sha256", _sha("other-source")),
            ("family", "control_snapshot_sha256", _sha("other-control")),
            ("region", "region_key", "body:30"),
            ("region", "region_manifest_sha256", _sha("other-region-manifest")),
        ]
        for component, field, value in mutations:
            changed = copy.deepcopy(payload)
            changed[component][field] = value
            changed["evidence_sha256"] = _canonical_hash(
                {k: v for k, v in changed.items() if k != "evidence_sha256"}
            )
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    _parse(changed)

    def test_external_expected_evidence_sha_is_mandatory_and_not_self_trusting(self) -> None:
        from maximum_optimizer.region_pose_evidence import parse_region_pose_evidence

        payload = _build().to_payload()
        with self.assertRaises(ValueError):
            parse_region_pose_evidence(
                payload,
                expected_evidence_sha256=_sha("different-evidence"),
                expected_family_id=FAMILY_ID,
                expected_family_input_sha256=FAMILY_INPUT,
                expected_source_snapshot_sha256=SOURCE_SNAPSHOT,
                expected_control_snapshot_sha256=CONTROL_SNAPSHOT,
                expected_region_key=REGION_KEY,
                expected_region_manifest_sha256=REGION_MANIFEST,
            )

    def test_animation_requires_bound_action_and_pixel_proofs(self) -> None:
        from maximum_optimizer.region_pose_evidence import build_region_pose_evidence

        arguments = {
            "family_id": FAMILY_ID,
            "family_input_sha256": FAMILY_INPUT,
            "source_snapshot_sha256": SOURCE_SNAPSHOT,
            "control_snapshot_sha256": CONTROL_SNAPSHOT,
            "region_key": REGION_KEY,
            "region_manifest_sha256": REGION_MANIFEST,
            "contracts": _contracts(),
            "selector": _selector(),
            "blender_action_proof": _action(),
            "pixel_gate": _pixel_gate(),
            "caps": _caps(),
        }
        for missing in ("blender_action_proof", "pixel_gate"):
            changed = dict(arguments)
            changed[missing] = None
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError):
                    build_region_pose_evidence(**changed)
        changed = dict(arguments)
        changed["blender_action_proof"] = {**_action(), "frame_ordinal": 18}
        with self.assertRaises(ValueError):
            build_region_pose_evidence(**changed)

    def test_bind_only_reasons_are_typed_and_cannot_carry_pose_proofs(self) -> None:
        accepted = {
            "bind-only/no-eligible-animation",
            "bind-only/no-influenced-bone-delta",
            "bind-only/rigid-region",
            "bind-only/no-visible-displacement",
        }
        for mode in accepted:
            with self.subTest(mode=mode):
                parsed = _parse(_build(mode=mode).to_payload())
                self.assertEqual(parsed.selector["mode"], mode)
        with self.assertRaises(ValueError):
            _build(mode="bind-only/unknown")
        from maximum_optimizer.region_pose_evidence import build_region_pose_evidence

        with self.assertRaises(ValueError):
            build_region_pose_evidence(
                family_id=FAMILY_ID,
                family_input_sha256=FAMILY_INPUT,
                source_snapshot_sha256=SOURCE_SNAPSHOT,
                control_snapshot_sha256=CONTROL_SNAPSHOT,
                region_key=REGION_KEY,
                region_manifest_sha256=REGION_MANIFEST,
                contracts=_contracts(), selector=_selector("bind-only/rigid-region"),
                blender_action_proof=_action(), pixel_gate=None, caps=_caps(),
            )

    def test_no_visible_displacement_requires_source_proof(self) -> None:
        from maximum_optimizer.region_pose_evidence import build_region_pose_evidence

        selector = _selector("bind-only/no-visible-displacement")
        selector["source_proof_sha256"] = None
        with self.assertRaisesRegex(ValueError, "source proof"):
            build_region_pose_evidence(
                family_id=FAMILY_ID,
                family_input_sha256=FAMILY_INPUT,
                source_snapshot_sha256=SOURCE_SNAPSHOT,
                control_snapshot_sha256=CONTROL_SNAPSHOT,
                region_key=REGION_KEY,
                region_manifest_sha256=REGION_MANIFEST,
                contracts=_contracts(), selector=selector,
                blender_action_proof=None, pixel_gate=None, caps=_caps(),
            )

    def test_pixel_gate_must_match_caps_and_be_nontrivial(self) -> None:
        from maximum_optimizer.region_pose_evidence import build_region_pose_evidence

        base = {
            "family_id": FAMILY_ID, "family_input_sha256": FAMILY_INPUT,
            "source_snapshot_sha256": SOURCE_SNAPSHOT,
            "control_snapshot_sha256": CONTROL_SNAPSHOT,
            "region_key": REGION_KEY, "region_manifest_sha256": REGION_MANIFEST,
            "contracts": _contracts(), "selector": _selector(),
            "blender_action_proof": _action(), "pixel_gate": _pixel_gate(),
            "caps": _caps(),
        }
        for field, value in (
            ("changed_pixels", 0),
            ("changed_fraction", 0.0),
            ("posed_pixel_bundle_sha256", _pixel_gate()["bind_pixel_bundle_sha256"]),
            ("image_count", 65),
        ):
            changed = copy.deepcopy(base)
            changed["pixel_gate"][field] = value
            unsigned = {k: v for k, v in changed["pixel_gate"].items() if k != "proof_sha256"}
            changed["pixel_gate"]["proof_sha256"] = _canonical_hash(unsigned)
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    build_region_pose_evidence(**changed)


class RegionPoseFamilyBundleTests(unittest.TestCase):
    def _expected(self, proofs):
        from maximum_optimizer.region_pose_evidence import RegionPoseExpectedBindings

        result = {}
        for proof in proofs:
            payload = proof.to_payload()
            key = payload["region"]["region_key"]
            result[key] = RegionPoseExpectedBindings(
                evidence_sha256=payload["evidence_sha256"],
                family_id=FAMILY_ID,
                family_input_sha256=FAMILY_INPUT,
                source_snapshot_sha256=SOURCE_SNAPSHOT,
                control_snapshot_sha256=CONTROL_SNAPSHOT,
                region_key=key,
                region_manifest_sha256=payload["region"]["region_manifest_sha256"],
            )
        return result

    def test_family_bundle_is_canonical_and_has_one_proof_per_external_region(self) -> None:
        from maximum_optimizer.region_pose_evidence import (
            build_region_pose_family_bundle, parse_region_pose_family_bundle,
        )

        proofs = [_build(region_key="wheel:2"), _build(region_key="body:1")]
        expected_keys = ("body:1", "wheel:2")
        bundle = build_region_pose_family_bundle(
            family_id=FAMILY_ID, expected_region_keys=expected_keys,
            entries=reversed(proofs),
        )
        payload = bundle.to_payload()
        self.assertEqual(payload["kind"], "region-pose-family-bundle-v1")
        self.assertEqual(
            [item["region"]["region_key"] for item in payload["entries"]],
            list(expected_keys),
        )
        parsed = parse_region_pose_family_bundle(
            payload,
            expected_bundle_sha256=payload["bundle_sha256"],
            expected_family_id=FAMILY_ID,
            expected_region_keys=expected_keys,
            expected_bindings=self._expected(proofs),
        )
        self.assertEqual(parsed.to_payload(), payload)

    def test_bundle_rejects_missing_extra_duplicate_and_wrong_family(self) -> None:
        from maximum_optimizer.region_pose_evidence import build_region_pose_family_bundle

        first = _build(region_key="body:1")
        second = _build(region_key="wheel:2")
        cases = (
            (("body:1", "wheel:2"), [first]),
            (("body:1",), [first, second]),
            (("body:1", "wheel:2"), [first, first]),
        )
        for expected_keys, entries in cases:
            with self.subTest(expected_keys=expected_keys, count=len(entries)):
                with self.assertRaises(ValueError):
                    build_region_pose_family_bundle(
                        family_id=FAMILY_ID,
                        expected_region_keys=expected_keys,
                        entries=entries,
                    )
        transplanted = copy.deepcopy(first.to_payload())
        transplanted["family"]["family_id"] = "toyota_supra"
        transplanted["evidence_sha256"] = _canonical_hash(
            {k: v for k, v in transplanted.items() if k != "evidence_sha256"}
        )
        with self.assertRaises(ValueError):
            build_region_pose_family_bundle(
                family_id=FAMILY_ID, expected_region_keys=("body:1",),
                entries=[transplanted],
            )

    def test_bundle_parser_rejects_resealed_entry_transplant_against_external_bindings(self) -> None:
        from maximum_optimizer.region_pose_evidence import (
            build_region_pose_family_bundle, parse_region_pose_family_bundle,
        )

        proof = _build(region_key="body:1")
        payload = build_region_pose_family_bundle(
            family_id=FAMILY_ID, expected_region_keys=("body:1",), entries=[proof],
        ).to_payload()
        bindings = self._expected([proof])
        changed = copy.deepcopy(payload)
        entry = changed["entries"][0]
        entry["family"]["control_snapshot_sha256"] = _sha("transplanted-control")
        entry["evidence_sha256"] = _canonical_hash(
            {k: v for k, v in entry.items() if k != "evidence_sha256"}
        )
        changed["bundle_sha256"] = _canonical_hash(
            {k: v for k, v in changed.items() if k != "bundle_sha256"}
        )
        with self.assertRaises(ValueError):
            parse_region_pose_family_bundle(
                changed,
                expected_bundle_sha256=changed["bundle_sha256"],
                expected_family_id=FAMILY_ID,
                expected_region_keys=("body:1",),
                expected_bindings=bindings,
            )


if __name__ == "__main__":
    unittest.main()
