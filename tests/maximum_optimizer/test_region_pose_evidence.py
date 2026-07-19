from __future__ import annotations

import copy
import hashlib
import json
import unittest


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


FAMILY = "dodge_charger"
FAMILY_INPUT = _sha("family-input")
SOURCE = _sha("source")
CONTROL = _sha("control")
REGION = "body:31"
REGION_MANIFEST = _sha("body31")
TOOLCHAIN = _sha("toolchain")


def _contracts():
    unsigned = {
        "contract_kind": "exact-region-pose-contracts-v1",
        "renderer_contract_sha256": _sha("renderer-contract"),
        "action_contract_sha256": _sha("action-contract"),
        "pixel_gate_contract_sha256": _sha("pixel-contract"),
        "toolchain_sha256": _toolchain()["toolchain_sha256"],
    }
    return {**unsigned, "contracts_sha256": _hash(unsigned)}


def _caps():
    unsigned = {
        "max_regions_per_family": 64, "required_view_count": 8,
        "required_width": 512, "required_height": 512,
        "max_total_pixels": 8 * 512 * 512,
        "minimum_changed_fraction": 0.0001,
        "minimum_silhouette_pixels": 27,
    }
    return {**unsigned, "caps_sha256": _hash(unsigned)}


def _animation_selection():
    unsigned = {
        "animation_relative_path": "anims/door.smd",
        "animation_sha256": _sha("door-animation"),
        "baseline": "geometry-rest", "bone_index": 1, "bone_name": "Hood",
        "candidate_inputs_consulted": False,
        "corrective_used_as_baseline": False,
        "displacement": 5.0, "displacement_squared": "25",
        "frame": 0, "geometry_inventory_sha256": _sha("geometry-inventory"),
        "pose_name": "door", "reference_relative_path": "anims/door_ref.smd",
        "reference_sha256": _sha("door-ref"),
        "raw_render_max_displacement": 5.0,
        "raw_render_rms_displacement": 3.5, "rms_displacement": 3.5,
        "selector": "exact-raw-render-region-displacement-v3",
        "selector_input_sha256": _sha("selector-input"), "source_time": 17,
    }
    return {**unsigned, "selection_sha256": _hash(unsigned)}


def _bind_selection():
    unsigned = {
        "candidate_inputs_consulted": False,
        "geometry_inventory_sha256": _sha("geometry-inventory"),
        "pose_keys": ["bind"],
        "reason": "no-exact-region-influencing-animation-displacement",
        "selector": "exact-raw-render-region-displacement-v3",
        "selector_input_sha256": _sha("selector-input"),
    }
    return {**unsigned, "selection_sha256": _hash(unsigned)}


def _toolchain():
    unsigned = {
        "blender_version": [5, 0, 1],
        "files": [
            {"path": "blender.exe", "size": 100, "sha256": _sha("blender")},
            {"path": "import_smd.py", "size": 50, "sha256": _sha("importer")},
        ],
        "kind": "blender5-source-tools-action-runtime-v1",
    }
    return {**unsigned, "toolchain_sha256": _hash(unsigned)}


def _action():
    unsigned = {
        "animation_input_sha256": _animation_selection()["animation_sha256"],
        "bone_lineage": [
            {"id": 0, "name": "root", "parent": -1},
            {"id": 1, "name": "Hood", "parent": 0},
            {"id": 2, "name": "Latch", "parent": 1},
        ],
        "curves": [{
            "array_index": 2, "data_path": 'pose.bones["Hood"].rotation_euler',
            "keyframes": [[0.0, 1.0]],
        }],
        "slot_name": "door", "source_times": [17], "toolchain": _toolchain(),
    }
    return {**unsigned, "action_sha256": _hash(unsigned)}


def _evaluated():
    unsigned = {
        "action_sha256": _action()["action_sha256"],
        "animation_input_sha256": _animation_selection()["animation_sha256"],
        "baseline": "armature-rest", "bind_geometry_sha256": _sha("bind-geometry"),
        "candidate_inputs_consulted": False,
        "corrective_used_as_baseline": False, "frame": 0,
        "influenced_bones": ["Hood", "Latch"],
        "kind": "blender-evaluated-region-pose-delta-v1",
        "maximum_displacement": 5.0, "moved_vertex_count": 20,
        "posed_geometry_sha256": _sha("posed-geometry"),
        "rms_displacement": 3.5, "selected_bone": "Hood", "source_time": 17,
        "toolchain_sha256": _toolchain()["toolchain_sha256"], "vertex_count": 100,
    }
    return {**unsigned, "proof_sha256": _hash(unsigned)}


CAMERAS = {
    "back": [0.0, 1.0, 0.0], "bottom": [0.0, 0.0, -1.0],
    "front": [0.0, -1.0, 0.0],
    "front_left": [-0.7071067811865476, -0.7071067811865476, 0.0],
    "left": [-1.0, 0.0, 0.0],
    "rear_right": [0.7071067811865476, 0.7071067811865476, 0.0],
    "right": [1.0, 0.0, 0.0], "top": [0.0, 0.0, 1.0],
}


def _pixel_public():
    qualified = {"front", "right"}
    views = []
    for key in sorted(CAMERAS):
        changed = 100 if key in qualified else 0
        views.append({
            "changed_fraction": changed / 100_000,
            "changed_pixels": changed, "foreground_pixels": 100_000,
            "height": 512, "key": key,
            "mean_absolute_error": 1.0 if changed else 0.0, "width": 512,
        })
    unsigned = {
        "bind_pixel_bundle_sha256": _sha("bind-pixels"),
        "changed_fraction": 200 / 800_000, "changed_pixels": 200,
        "foreground_pixels": 800_000, "image_count": 8,
        "mean_absolute_error": 0.25, "minimum_changed_fraction": 0.0001,
        "minimum_silhouette_pixels": 27,
        "posed_pixel_bundle_sha256": _sha("posed-pixels"),
        "total_pixels": 8 * 512 * 512, "views": views,
        "camera_directions": [
            {"key": key, "direction": CAMERAS[key]} for key in sorted(CAMERAS)
        ],
        "qualified_silhouette_views": sorted(qualified),
    }
    return {**unsigned, "evidence_sha256": _hash(unsigned)}


def _pixel_evidence(pair=("front", "right"), *, region_manifest=REGION_MANIFEST):
    public = _pixel_public()
    unsigned = {
        "kind": "pose-pixel-family-evidence-v1", "first": public,
        "repeat": copy.deepcopy(public), "deterministic": True,
        "canonical_orthogonal_pair": list(pair),
        "selection_sha256": _animation_selection()["selection_sha256"],
        "action_sha256": _action()["action_sha256"],
        "evaluated_region_proof_sha256": _evaluated()["proof_sha256"],
        "region_manifest_sha256": region_manifest,
        "toolchain_sha256": _toolchain()["toolchain_sha256"],
        "caps_sha256": _caps()["caps_sha256"],
    }
    return {**unsigned, "evidence_sha256": _hash(unsigned)}


def _no_visible_pixel_evidence(*, region_manifest=REGION_MANIFEST):
    public = _pixel_public()
    public["kind"] = "pose-pixel-measurement-v1"
    for view in public["views"]:
        view["changed_pixels"] = 10
        view["changed_fraction"] = 10 / view["foreground_pixels"]
        view["mean_absolute_error"] = 0.25
    public["qualified_silhouette_views"] = []
    public["changed_pixels"] = 80
    public["changed_fraction"] = 80 / public["foreground_pixels"]
    public["mean_absolute_error"] = 0.25
    public["evidence_sha256"] = _hash({k: v for k, v in public.items() if k != "evidence_sha256"})
    unsigned = {
        "kind": "pose-pixel-no-visible-evidence-v1",
        "first": public, "repeat": copy.deepcopy(public), "deterministic": True,
        "canonical_orthogonal_pair": None,
        "selection_sha256": _animation_selection()["selection_sha256"],
        "action_sha256": _action()["action_sha256"],
        "evaluated_region_proof_sha256": _evaluated()["proof_sha256"],
        "region_manifest_sha256": region_manifest,
        "toolchain_sha256": _toolchain()["toolchain_sha256"],
        "caps_sha256": _caps()["caps_sha256"],
    }
    return {**unsigned, "evidence_sha256": _hash(unsigned)}


def _reason_proof(mode: str, *, evaluated=None, pixel=None):
    inventory = [] if mode in {"bind-only/no-eligible-animation", "bind-only/rigid-region"} else [{
        "animation_relative_path": "anims/door.smd",
        "animation_sha256": _animation_selection()["animation_sha256"],
        "reference_relative_path": "anims/door_ref.smd",
        "reference_sha256": _animation_selection()["reference_sha256"],
    }]
    attempted = _animation_selection() if mode == "bind-only/no-visible-displacement" else None
    unsigned = {
        "kind": mode.removeprefix("bind-only/") + "-proof-v1",
        "animation_inventory": inventory,
        "geometry_inventory_sha256": _bind_selection()["geometry_inventory_sha256"],
        "region_manifest_sha256": REGION_MANIFEST if mode == "bind-only/rigid-region" else None,
        "attempted_selection_sha256": attempted["selection_sha256"] if attempted else None,
        "evaluated_region_proof_sha256": evaluated["proof_sha256"] if attempted else None,
        "pixel_evidence_sha256": pixel["evidence_sha256"] if attempted else None,
    }
    return {**unsigned, "proof_sha256": _hash(unsigned)}


def _decision(mode="bind-animation", *, evaluated=None, pixel=None):
    if mode == "bind-animation":
        selection, attempted, reason = _animation_selection(), None, None
    else:
        selection = _bind_selection()
        attempted = _animation_selection() if mode == "bind-only/no-visible-displacement" else None
        reason = _reason_proof(mode, evaluated=evaluated, pixel=pixel)
    unsigned = {
        "mode": mode, "selection_payload": selection,
        "attempted_selection_payload": attempted, "decision_proof": reason,
    }
    return {**unsigned, "decision_sha256": _hash(unsigned)}


def _build(*, mode="bind-animation", region_key=REGION, family_overrides=None):
    from maximum_optimizer.region_pose_evidence import build_region_pose_evidence

    evaluated = _evaluated()
    manifest = REGION_MANIFEST if region_key == REGION else _sha(region_key)
    pixels = (
        _no_visible_pixel_evidence(region_manifest=manifest)
        if mode == "bind-only/no-visible-displacement"
        else _pixel_evidence(region_manifest=manifest)
    )
    animated = mode in {"bind-animation", "bind-only/no-visible-displacement"}
    decision = _decision(mode, evaluated=evaluated, pixel=pixels)
    if manifest != REGION_MANIFEST:
        if decision["decision_proof"] is not None and decision["mode"] == "bind-only/rigid-region":
            decision["decision_proof"]["region_manifest_sha256"] = manifest
            proof = decision["decision_proof"]
            proof["proof_sha256"] = _hash({k: v for k, v in proof.items() if k != "proof_sha256"})
            decision["decision_sha256"] = _hash({k: v for k, v in decision.items() if k != "decision_sha256"})
    overrides = family_overrides or {}
    return build_region_pose_evidence(
        family_id=overrides.get("family_id", FAMILY),
        family_input_sha256=overrides.get("family_input_sha256", FAMILY_INPUT),
        source_snapshot_sha256=overrides.get("source_snapshot_sha256", SOURCE),
        control_snapshot_sha256=overrides.get("control_snapshot_sha256", CONTROL),
        region_key=region_key, region_manifest_sha256=manifest,
        contracts=_contracts(), selector_decision=decision,
        action_proof=_action() if animated else None,
        evaluated_region_proof=evaluated if animated else None,
        pixel_evidence=pixels if animated else None, caps=_caps(),
    )


def _reseal_cross(raw):
    decision = raw["selector_decision"]
    for key in ("selection_payload", "attempted_selection_payload"):
        selection = decision.get(key)
        if selection is not None:
            selection["selection_sha256"] = _hash({k: v for k, v in selection.items() if k != "selection_sha256"})
    active = decision["attempted_selection_payload"] or decision["selection_payload"]
    action = raw.get("action_proof")
    if action is not None:
        action["action_sha256"] = _hash({k: v for k, v in action.items() if k != "action_sha256"})
    evaluated = raw.get("evaluated_region_proof")
    if evaluated is not None:
        evaluated["action_sha256"] = action["action_sha256"]
        evaluated["animation_input_sha256"] = active["animation_sha256"]
        evaluated["proof_sha256"] = _hash({k: v for k, v in evaluated.items() if k != "proof_sha256"})
    pixels = raw.get("pixel_evidence")
    if pixels is not None:
        pixels["selection_sha256"] = active["selection_sha256"]
        pixels["action_sha256"] = action["action_sha256"]
        pixels["evaluated_region_proof_sha256"] = evaluated["proof_sha256"]
        pixels["evidence_sha256"] = _hash({k: v for k, v in pixels.items() if k != "evidence_sha256"})
    reason = decision.get("decision_proof")
    if reason is not None and decision["mode"] == "bind-only/no-visible-displacement":
        reason["attempted_selection_sha256"] = active["selection_sha256"]
        reason["evaluated_region_proof_sha256"] = evaluated["proof_sha256"]
        reason["pixel_evidence_sha256"] = pixels["evidence_sha256"]
        reason["proof_sha256"] = _hash({k: v for k, v in reason.items() if k != "proof_sha256"})
    decision["decision_sha256"] = _hash({k: v for k, v in decision.items() if k != "decision_sha256"})
    raw["evidence_sha256"] = _hash({k: v for k, v in raw.items() if k != "evidence_sha256"})
    return raw


def _binding(proof):
    from maximum_optimizer.region_pose_evidence import RegionPoseExpectedBindings

    raw = proof.to_payload()
    return RegionPoseExpectedBindings(
        evidence_sha256=raw["evidence_sha256"], family_id=raw["family"]["family_id"],
        family_input_sha256=raw["family"]["family_input_sha256"],
        source_snapshot_sha256=raw["family"]["source_snapshot_sha256"],
        control_snapshot_sha256=raw["family"]["control_snapshot_sha256"],
        region_key=raw["region"]["region_key"],
        region_manifest_sha256=raw["region"]["region_manifest_sha256"],
        contracts_sha256=raw["contracts"]["contracts_sha256"],
        toolchain_sha256=raw["contracts"]["toolchain_sha256"],
        caps_sha256=raw["caps"]["caps_sha256"],
    )


def _parse(raw, binding=None):
    from maximum_optimizer.region_pose_evidence import parse_region_pose_evidence
    binding = binding or _binding(type("P", (), {"to_payload": lambda _: raw})())
    return parse_region_pose_evidence(
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
    )


class EvidenceTests(unittest.TestCase):
    def test_preserves_exact_public_payloads_and_deep_immutability(self):
        proof = _build()
        raw = proof.to_payload()
        self.assertEqual(raw["selector_decision"]["selection_payload"], _animation_selection())
        self.assertEqual(raw["action_proof"], _action())
        self.assertEqual(raw["evaluated_region_proof"], _evaluated())
        self.assertEqual(raw["pixel_evidence"]["first"], _pixel_public())
        parsed = _parse(raw)
        with self.assertRaises(TypeError): parsed.selector_decision["mode"] = "x"
        with self.assertRaises(TypeError): parsed.action_proof["curves"][0]["array_index"] = 0

    def test_public_payload_minus_seal_is_recomputed_for_every_nested_proof(self):
        base = _build().to_payload()
        paths = [
            ("selector_decision", "selection_payload", "displacement"),
            ("action_proof", None, "slot_name"),
            ("evaluated_region_proof", None, "maximum_displacement"),
            ("pixel_evidence", "first", "changed_pixels"),
        ]
        for outer, inner, field in paths:
            changed = copy.deepcopy(base)
            target = changed[outer] if inner is None else changed[outer][inner]
            target[field] = "tampered" if isinstance(target[field], str) else 999
            with self.subTest(path=(outer, inner, field)), self.assertRaises(ValueError):
                _parse({**changed, "evidence_sha256": _hash({k: v for k, v in changed.items() if k != "evidence_sha256"})})

    def test_action_and_evaluated_cross_bind_selection_frame_bones_counts_and_toolchain(self):
        base = _build().to_payload()
        mutations = []
        for component, field, value, seal in (
            ("action_proof", "animation_input_sha256", _sha("other-animation"), "action_sha256"),
            ("evaluated_region_proof", "frame", 1, "proof_sha256"),
            ("evaluated_region_proof", "selected_bone", "Door", "proof_sha256"),
            ("evaluated_region_proof", "moved_vertex_count", 101, "proof_sha256"),
            ("evaluated_region_proof", "toolchain_sha256", _sha("other-tool"), "proof_sha256"),
        ):
            changed = copy.deepcopy(base); target = changed[component]; target[field] = value
            target[seal] = _hash({k: v for k, v in target.items() if k != seal}); mutations.append(changed)
        for changed in mutations:
            with self.assertRaises(ValueError):
                _parse({**changed, "evidence_sha256": _hash({k: v for k, v in changed.items() if k != "evidence_sha256"})})

    def test_public_source_time_remains_signed_while_frame_is_nonnegative_ordinal(self):
        raw = _build().to_payload()
        selection = raw["selector_decision"]["selection_payload"]
        selection["source_time"] = -7
        selection["selection_sha256"] = _hash({k: v for k, v in selection.items() if k != "selection_sha256"})
        decision = raw["selector_decision"]
        decision["decision_sha256"] = _hash({k: v for k, v in decision.items() if k != "decision_sha256"})
        action = raw["action_proof"]
        action["source_times"] = [-7]
        action["action_sha256"] = _hash({k: v for k, v in action.items() if k != "action_sha256"})
        evaluated = raw["evaluated_region_proof"]
        evaluated["source_time"] = -7
        evaluated["action_sha256"] = action["action_sha256"]
        evaluated["proof_sha256"] = _hash({k: v for k, v in evaluated.items() if k != "proof_sha256"})
        pixels = raw["pixel_evidence"]
        pixels["selection_sha256"] = selection["selection_sha256"]
        pixels["action_sha256"] = action["action_sha256"]
        pixels["evaluated_region_proof_sha256"] = evaluated["proof_sha256"]
        pixels["evidence_sha256"] = _hash({k: v for k, v in pixels.items() if k != "evidence_sha256"})
        raw["evidence_sha256"] = _hash({k: v for k, v in raw.items() if k != "evidence_sha256"})
        self.assertEqual(_parse(raw).evaluated_region_proof["source_time"], -7)

    def test_public_bone_lineage_accepts_canonical_ids_with_forward_parent_reference(self):
        raw = _build().to_payload()
        action = raw["action_proof"]
        action["bone_lineage"] = [
            {"id": 1, "name": "Hood", "parent": 2},
            {"id": 2, "name": "root", "parent": -1},
            {"id": 3, "name": "Latch", "parent": 1},
        ]
        action["action_sha256"] = _hash({k: v for k, v in action.items() if k != "action_sha256"})
        evaluated = raw["evaluated_region_proof"]
        evaluated["action_sha256"] = action["action_sha256"]
        evaluated["proof_sha256"] = _hash({k: v for k, v in evaluated.items() if k != "proof_sha256"})
        pixels = raw["pixel_evidence"]
        pixels["action_sha256"] = action["action_sha256"]
        pixels["evaluated_region_proof_sha256"] = evaluated["proof_sha256"]
        pixels["evidence_sha256"] = _hash({k: v for k, v in pixels.items() if k != "evidence_sha256"})
        raw["evidence_sha256"] = _hash({k: v for k, v in raw.items() if k != "evidence_sha256"})
        self.assertEqual(_parse(raw).action_proof["bone_lineage"][0]["parent"], 2)

    def test_action_requested_frame_and_evaluated_influenced_bones_are_bound(self):
        for mutation in ("unkeyed-selected-frame", "unknown-influenced-bone"):
            raw = _build().to_payload()
            action = raw["action_proof"]
            evaluated = raw["evaluated_region_proof"]
            if mutation == "unkeyed-selected-frame":
                action["source_times"] = [17, 18]
                action["curves"][0]["keyframes"] = [[1.0, 1.0]]
            else:
                evaluated["influenced_bones"] = ["Hood", "Unknown"]
            action["action_sha256"] = _hash({k: v for k, v in action.items() if k != "action_sha256"})
            evaluated["action_sha256"] = action["action_sha256"]
            evaluated["proof_sha256"] = _hash({k: v for k, v in evaluated.items() if k != "proof_sha256"})
            pixels = raw["pixel_evidence"]
            pixels["action_sha256"] = action["action_sha256"]
            pixels["evaluated_region_proof_sha256"] = evaluated["proof_sha256"]
            pixels["evidence_sha256"] = _hash({k: v for k, v in pixels.items() if k != "evidence_sha256"})
            raw["evidence_sha256"] = _hash({k: v for k, v in raw.items() if k != "evidence_sha256"})
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                _parse(raw)

    def test_pixel_requires_repeat_exactly_eight_512_foreground_and_dynamic_pair(self):
        base = _build().to_payload()
        mutations = []
        one = copy.deepcopy(base); one["pixel_evidence"]["first"]["views"] = one["pixel_evidence"]["first"]["views"][:1]; mutations.append(one)
        tiny = copy.deepcopy(base); tiny["pixel_evidence"]["first"]["views"][0]["width"] = 1; mutations.append(tiny)
        empty = copy.deepcopy(base); empty["pixel_evidence"]["first"]["views"][0]["foreground_pixels"] = 0; mutations.append(empty)
        repeat = copy.deepcopy(base); repeat["pixel_evidence"]["repeat"]["posed_pixel_bundle_sha256"] = _sha("repeat-drift"); mutations.append(repeat)
        pair = copy.deepcopy(base); pair["pixel_evidence"]["canonical_orthogonal_pair"] = ["front", "back"]; mutations.append(pair)
        for changed in mutations:
            with self.subTest(pixel=changed["pixel_evidence"]), self.assertRaises(ValueError):
                _parse({**changed, "evidence_sha256": _hash({k: v for k, v in changed.items() if k != "evidence_sha256"})})

    def test_nonqualifying_views_may_have_bounded_subthreshold_silhouette_changes(self):
        raw = _build().to_payload()
        first = raw["pixel_evidence"]["first"]
        left = next(item for item in first["views"] if item["key"] == "left")
        left["changed_pixels"] = 26
        left["changed_fraction"] = 26 / left["foreground_pixels"]
        left["mean_absolute_error"] = 0.5
        first["changed_pixels"] += 26
        first["changed_fraction"] = first["changed_pixels"] / first["foreground_pixels"]
        first["mean_absolute_error"] = 0.3125
        first["evidence_sha256"] = _hash({k: v for k, v in first.items() if k != "evidence_sha256"})
        raw["pixel_evidence"]["repeat"] = copy.deepcopy(first)
        _reseal_cross(raw)
        self.assertNotIn("left", _parse(raw).pixel_evidence["first"]["qualified_silhouette_views"])

    def test_dynamic_pair_uses_camera_directions_not_fixed_names(self):
        evidence = _pixel_evidence(pair=("front", "right"))
        first = evidence["first"]
        for camera in first["camera_directions"]:
            if camera["key"] == "front": camera["direction"] = [1.0, 0.0, 0.0]
            if camera["key"] == "right": camera["direction"] = [0.0, 0.0, 1.0]
        unsigned = {k: v for k, v in first.items() if k != "evidence_sha256"}
        first["evidence_sha256"] = _hash(unsigned)
        evidence["repeat"] = copy.deepcopy(first)
        evidence["evidence_sha256"] = _hash({k: v for k, v in evidence.items() if k != "evidence_sha256"})
        base = _build().to_payload(); base["pixel_evidence"] = evidence
        base["evidence_sha256"] = _hash({k: v for k, v in base.items() if k != "evidence_sha256"})
        self.assertEqual(_parse(base).pixel_evidence["canonical_orthogonal_pair"], ("front", "right"))

    def test_pixel_envelope_cross_binds_selection_action_evaluated_region_toolchain_and_caps(self):
        base = _build().to_payload()
        for field in (
            "selection_sha256", "action_sha256", "evaluated_region_proof_sha256",
            "region_manifest_sha256", "toolchain_sha256", "caps_sha256",
        ):
            changed = copy.deepcopy(base)
            changed["pixel_evidence"][field] = _sha("wrong-" + field)
            pixel = changed["pixel_evidence"]
            pixel["evidence_sha256"] = _hash({k: v for k, v in pixel.items() if k != "evidence_sha256"})
            changed["evidence_sha256"] = _hash({k: v for k, v in changed.items() if k != "evidence_sha256"})
            with self.subTest(field=field), self.assertRaises(ValueError):
                _parse(changed)

    def test_bind_only_typed_reasons_and_no_visible_sources(self):
        for mode in (
            "bind-only/no-eligible-animation", "bind-only/no-influenced-bone-delta",
            "bind-only/rigid-region", "bind-only/no-visible-displacement",
        ):
            raw = _build(mode=mode).to_payload(); parsed = _parse(raw)
            inventory = parsed.selector_decision["decision_proof"]["animation_inventory"]
            if mode == "bind-only/no-eligible-animation": self.assertEqual(inventory, ())
            if mode == "bind-only/no-visible-displacement":
                self.assertIsNotNone(parsed.selector_decision["attempted_selection_payload"])
                self.assertIsNotNone(parsed.evaluated_region_proof)
                self.assertIsNotNone(parsed.pixel_evidence)
        base = _build(mode="bind-only/no-visible-displacement").to_payload()
        for field in ("attempted_selection_payload",):
            changed = copy.deepcopy(base); changed["selector_decision"][field] = None
            with self.assertRaises(ValueError): _parse({**changed, "evidence_sha256": _hash({k: v for k, v in changed.items() if k != "evidence_sha256"})})

    def test_no_visible_requires_deterministic_measurement_without_orthogonal_qualified_pair(self):
        raw = _build(mode="bind-only/no-visible-displacement").to_payload()
        self.assertIsNone(_parse(raw).pixel_evidence["canonical_orthogonal_pair"])
        one = _build(mode="bind-only/no-visible-displacement").to_payload()
        first = one["pixel_evidence"]["first"]
        front = next(item for item in first["views"] if item["key"] == "front")
        delta = 100 - front["changed_pixels"]
        front["changed_pixels"] = 100
        front["changed_fraction"] = 100 / front["foreground_pixels"]
        first["changed_pixels"] += delta
        first["changed_fraction"] = first["changed_pixels"] / first["foreground_pixels"]
        first["qualified_silhouette_views"] = ["front"]
        first["evidence_sha256"] = _hash({k: v for k, v in first.items() if k != "evidence_sha256"})
        one["pixel_evidence"]["repeat"] = copy.deepcopy(first)
        _reseal_cross(one)
        self.assertEqual(_parse(one).pixel_evidence["first"]["qualified_silhouette_views"], ("front",))
        positive = _pixel_evidence()
        raw["pixel_evidence"] = positive
        _reseal_cross(raw)
        with self.assertRaises(ValueError):
            _parse(raw)

    def test_mirrors_producer_selection_action_lineage_and_tool_inventory_invariants(self):
        mutations = []

        def case(mutator):
            raw = _build().to_payload(); mutator(raw); mutations.append(_reseal_cross(raw))

        case(lambda raw: raw["action_proof"].__setitem__("slot_name", "wrong-slot"))
        case(lambda raw: raw["action_proof"]["curves"][0].__setitem__("array_index", 3))
        case(lambda raw: raw["action_proof"].__setitem__("source_times", [17, 17]))
        case(lambda raw: raw["selector_decision"]["selection_payload"].__setitem__("bone_index", 2))
        case(lambda raw: raw["selector_decision"]["selection_payload"].__setitem__("pose_name", "wrong"))
        case(lambda raw: raw["selector_decision"]["selection_payload"].__setitem__("animation_relative_path", "../door.smd"))
        def tiny_selection(raw):
            selected = raw["selector_decision"]["selection_payload"]
            selected["displacement"] = selected["raw_render_max_displacement"] = 1e-13
            selected["rms_displacement"] = selected["raw_render_rms_displacement"] = 1e-13
            selected["displacement_squared"] = "1e-26"
        case(tiny_selection)
        def tiny_evaluated(raw):
            evaluated = raw["evaluated_region_proof"]
            evaluated["maximum_displacement"] = evaluated["rms_displacement"] = 1e-13
        case(tiny_evaluated)
        case(lambda raw: raw["action_proof"]["toolchain"]["files"].reverse())
        def sibling(raw):
            raw["action_proof"]["bone_lineage"].append({"id": 3, "name": "Door", "parent": 0})
            raw["evaluated_region_proof"]["influenced_bones"] = ["Door", "Hood", "Latch"]
        case(sibling)
        for index, raw in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(ValueError):
                _parse(raw)

    def test_bind_reason_animation_inventory_is_capped_at_sixty_four(self):
        raw = _build(mode="bind-only/no-influenced-bone-delta").to_payload()
        item = raw["selector_decision"]["decision_proof"]["animation_inventory"][0]
        raw["selector_decision"]["decision_proof"]["animation_inventory"] = [
            {**item, "animation_relative_path": f"anims/{index:02d}.smd"}
            for index in range(65)
        ]
        proof = raw["selector_decision"]["decision_proof"]
        proof["proof_sha256"] = _hash({k: v for k, v in proof.items() if k != "proof_sha256"})
        raw["selector_decision"]["decision_sha256"] = _hash({
            k: v for k, v in raw["selector_decision"].items() if k != "decision_sha256"
        })
        raw["evidence_sha256"] = _hash({k: v for k, v in raw.items() if k != "evidence_sha256"})
        with self.assertRaises(ValueError):
            _parse(raw)

    def test_external_family_region_contract_toolchain_caps_reject_reseal(self):
        proof = _build(); raw = proof.to_payload(); expected = _binding(proof)
        for field in (
            "family_id", "source_snapshot_sha256", "region_manifest_sha256",
            "contracts_sha256", "toolchain_sha256", "caps_sha256",
        ):
            values = dict(expected.__dict__); values[field] = "other" if field == "family_id" else _sha("other-" + field)
            from maximum_optimizer.region_pose_evidence import RegionPoseExpectedBindings
            with self.subTest(field=field), self.assertRaises(ValueError):
                _parse(raw, RegionPoseExpectedBindings(**values))


class BundleTests(unittest.TestCase):
    def _proofs(self, count=2): return [_build(region_key=f"body:{i:02d}") for i in range(count)]

    def test_bundle_canonical_common_tuple_and_cap64_parser_builder_parity(self):
        from maximum_optimizer.region_pose_evidence import build_region_pose_family_bundle, parse_region_pose_family_bundle
        proofs = self._proofs(); keys = tuple(sorted(p.region["region_key"] for p in proofs))
        bundle = build_region_pose_family_bundle(family_id=FAMILY, expected_region_keys=keys, entries=reversed(proofs))
        raw = bundle.to_payload(); bindings = {p.region["region_key"]: _binding(p) for p in proofs}
        self.assertEqual(parse_region_pose_family_bundle(
            raw, expected_bundle_sha256=raw["bundle_sha256"], expected_family_id=FAMILY,
            expected_region_keys=keys, expected_bindings=bindings,
        ).to_payload(), raw)
        proofs65 = self._proofs(65); keys65 = tuple(sorted(p.region["region_key"] for p in proofs65))
        with self.assertRaises(ValueError): build_region_pose_family_bundle(family_id=FAMILY, expected_region_keys=keys65, entries=proofs65)
        with self.assertRaises(ValueError): build_region_pose_family_bundle(family_id=FAMILY, expected_region_keys=("body:00",), entries=(p for p in proofs65))
        oversized = copy.deepcopy(raw)
        oversized["entries"] = oversized["entries"] * 33
        oversized["bundle_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "cap|count"):
            parse_region_pose_family_bundle(
                oversized, expected_bundle_sha256="0" * 64,
                expected_family_id=FAMILY, expected_region_keys=keys,
                expected_bindings=bindings,
            )

    def test_bundle_rejects_missing_extra_duplicate_cross_family_and_hybrid_snapshot(self):
        from maximum_optimizer.region_pose_evidence import build_region_pose_family_bundle
        proofs = self._proofs(); keys = tuple(sorted(p.region["region_key"] for p in proofs))
        cases = [
            (keys, proofs[:1]), (keys[:1], proofs), (keys, [proofs[0], proofs[0]]),
            (keys, [proofs[0], _build(region_key=keys[1], family_overrides={"family_id": "toyota"})]),
            (keys, [proofs[0], _build(region_key=keys[1], family_overrides={"source_snapshot_sha256": _sha("hybrid")})]),
        ]
        for universe, entries in cases:
            with self.subTest(universe=universe), self.assertRaises(ValueError):
                build_region_pose_family_bundle(family_id=FAMILY, expected_region_keys=universe, entries=entries)


if __name__ == "__main__": unittest.main()
