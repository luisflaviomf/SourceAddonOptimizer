from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from maximum_optimizer.visual_validation import (
    FidelityProfile,
    _rgb_mae,
    _silhouette_error,
    compare_render_sets,
    load_profile,
)
from calibrate_maximum_profiles import calibrate_profile


METRICS = (
    "silhouette_iou",
    "rgb_mae",
    "edge_error",
    "surface_bidirectional_p95",
    "surface_max",
    "normal_angle_p95",
    "uv_error_p95",
    "skinning_error_p95",
)
PASSES = ("textured", "clay")
ANGLES = ("front", "back", "left", "right", "top", "bottom", "iso1", "iso2")
GEOMETRY_METRICS = METRICS[3:]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_image(path: Path, *, box=(2, 2, 6, 6), color=(128, 128, 128, 255)) -> None:
    image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    if box is not None:
        for y in range(box[1], box[3]):
            for x in range(box[0], box[2]):
                image.putpixel((x, y), color)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _write_manifest(
    root: Path,
    entries: list[dict],
    *,
    geometry: list[dict] | None = None,
    passes: tuple[str, ...] = PASSES,
    angles: tuple[str, ...] = ANGLES,
    poses: tuple[str, ...] = ("bind",),
    regions: tuple[str, ...] = ("body",),
    pose_frames: dict[str, int] | None = None,
) -> None:
    if pose_frames is None:
        pose_frames = {pose: index for index, pose in enumerate(poses)}
    if geometry is None:
        geometry = [
            {
                "scope": region,
                "pose": pose,
                **{metric: 0.0 for metric in GEOMETRY_METRICS},
                "region_missing": False,
            }
            for region in regions
            for pose in poses
        ]
    payload = {
        "schema": 1,
        "configuration": {"schema": 1, "name": "engine-default", "bodygroups": {}, "lod_index": 0, "source_pairs": [{"source_identity": "fixture.smd", "reference_sha256": "a" * 64, "candidate_sha256": "b" * 64}]},
        "expected": {
            "passes": list(passes),
            "angles": list(angles),
            "poses": list(poses),
            "pose_frames": pose_frames,
            "regions": list(regions),
        },
        "entries": entries,
        "geometry": geometry,
        "bbox": {"min": [0, 0, 0], "max": [1, 1, 1], "diagonal": math.sqrt(3)},
        "sampling": {"stride": 1, "seed": 0},
        "geometry_audit": {
            f"{region}/{pose}": {
                "input_triangles": 1,
                "kept_triangles": 1,
                "filtered_degenerate_triangles": 0,
                "filtered_indices_sha256": hashlib.sha256(b"").hexdigest(),
            }
            for region in regions
            for pose in poses
        },
        "geometry_audit_algorithm": {
            "name": "relative-cross-area-squared-v1",
            "relative_area_squared_epsilon": 1e-24,
            "max_filtered_fraction": 0.05,
            "surface_correspondence": (
                "material-bone-multinormal-near-coincident-surface-v6"
            ),
            "uv_distance": "periodic-unit-torus-v1",
            "skinning_correspondence": (
                "dominant-bone-partitioned-stable-topology-v2"
            ),
        },
    }
    (root / "render_manifest.json").write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )


def _entry(root: Path, render_pass: str, pose: str, angle: str, **image_kwargs) -> dict:
    relative = f"{render_pass}/{pose}/{angle}.png"
    path = root / relative
    _write_image(path, **image_kwargs)
    entry = {
        "pass": render_pass,
        "pose": pose,
        "angle": angle,
        "image": relative,
        "sha256": _sha256(path),
        "texture_missing": False,
        "missing_materials": [],
        "resolved_materials": [],
    }
    if render_pass == "textured":
        entry["resolved_materials"] = [{
            "material_identity": "fixture.smd:slot:0:body",
            "resolution_rule": "materials-root-order-then-qc-search-order-v1",
            "root_index": 0,
            "search_path_index": 0,
            "vtf_root_index": 0,
            "vmt_sha256": "c" * 64,
            "vtf_sha256": "d" * 64,
            "shader": "vertexlitgeneric",
            "texture_directive": "$basetexture",
            "uses_texture_alpha": False,
            "duplicate_root_directives": [],
        }]
    return entry


def _entry_key(entry: dict) -> tuple[str, str, str]:
    return entry["pass"], entry["pose"], entry["angle"]


def _matrix_entries(
    root: Path,
    *,
    passes: tuple[str, ...] = PASSES,
    poses: tuple[str, ...] = ("bind",),
    angles: tuple[str, ...] = ANGLES,
) -> list[dict]:
    return [
        _entry(root, render_pass, pose, angle)
        for render_pass in passes
        for pose in poses
        for angle in angles
    ]


def _profile(**limits: float) -> FidelityProfile:
    values = {metric: 1.0 for metric in METRICS}
    values.update(limits)
    return FidelityProfile(
        schema=1,
        version="test-v1",
        calibrated=True,
        corpus_hash="a" * 64,
        limits=values,
    )


class VisualValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.reference = self.root / "reference"
        self.candidate = self.root / "candidate"
        self.reference.mkdir()
        self.candidate.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_matching(self, *, poses=("bind",), regions=("body",)):
        reference_entries = _matrix_entries(self.reference, poses=poses)
        candidate_entries = _matrix_entries(self.candidate, poses=poses)
        _write_manifest(
            self.reference, reference_entries, poses=poses, regions=regions
        )
        _write_manifest(
            self.candidate, candidate_entries, poses=poses, regions=regions
        )

    def test_resolved_material_evidence_mutations_fail_closed(self):
        self.write_matching()
        manifest_path = self.candidate / "render_manifest.json"
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutations = {
            "unknown shader": lambda evidence: evidence.__setitem__("shader", "customshader"),
            "wrong directive": lambda evidence: evidence.__setitem__("texture_directive", "$refracttinttexture"),
            "non boolean alpha": lambda evidence: evidence.__setitem__("uses_texture_alpha", 1),
            "bad hash": lambda evidence: evidence.__setitem__("vtf_sha256", "D" * 64),
            "negative root": lambda evidence: evidence.__setitem__("root_index", -1),
            "wrong rule": lambda evidence: evidence.__setitem__("resolution_rule", "unordered"),
            "extra field": lambda evidence: evidence.__setitem__("unsealed", True),
            "bad duplicate audit": lambda evidence: evidence.__setitem__(
                "duplicate_root_directives",
                [{"directive": "$alphatest", "ignored_values": []}],
            ),
            "noncanonical duplicate directive": lambda evidence: evidence.__setitem__(
                "duplicate_root_directives",
                [{"directive": "$AlphaTest", "ignored_values": ["0"]}],
            ),
            "casefold duplicate directives": lambda evidence: evidence.__setitem__(
                "duplicate_root_directives",
                [
                    {"directive": "$alphatest", "ignored_values": ["0"]},
                    {"directive": "$AlphaTest", "ignored_values": ["0"]},
                ],
            ),
        }
        for label, mutate in mutations.items():
            payload = json.loads(json.dumps(original))
            textured = next(entry for entry in payload["entries"] if entry["pass"] == "textured")
            mutate(textured["resolved_materials"][0])
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.subTest(label=label):
                result = compare_render_sets(self.reference, self.candidate, _profile())
                self.assertFalse(result.passed)
                self.assertIn("invalid_material_evidence", {failure.gate for failure in result.failures})

    def test_well_formed_material_audit_mutation_must_match_reference(self):
        self.write_matching()
        path = self.candidate / "render_manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload["entries"]:
            if entry["pass"] == "textured":
                entry["resolved_materials"][0]["duplicate_root_directives"] = [
                    {"directive": "$alphatest", "ignored_values": ["0"]},
                ]
        path.write_text(json.dumps(payload), encoding="utf-8")

        result = compare_render_sets(self.reference, self.candidate, _profile())

        self.assertFalse(result.passed)
        self.assertIn("material_evidence_mismatch", {failure.gate for failure in result.failures})

    def test_material_audit_must_be_consistent_across_angles(self):
        self.write_matching()
        path = self.candidate / "render_manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        textured = [entry for entry in payload["entries"] if entry["pass"] == "textured"]
        textured[0]["resolved_materials"][0]["duplicate_root_directives"] = [
            {"directive": "$alphatest", "ignored_values": ["0"]},
        ]
        path.write_text(json.dumps(payload), encoding="utf-8")

        result = compare_render_sets(self.reference, self.candidate, _profile())

        self.assertFalse(result.passed)
        self.assertIn("material_evidence_inconsistent", {failure.gate for failure in result.failures})

    def test_profile_is_deeply_read_only_and_requires_calibrated_finite_limits(self):
        profile = _profile()
        with self.assertRaises((TypeError, AttributeError)):
            profile.version = "changed"
        with self.assertRaises(TypeError):
            profile.limits["rgb_mae"] = 2.0

        invalid_profiles = (
            {"schema": 1, "version": "x", "calibrated": False, "limits": {}},
            {"schema": 1, "version": "x", "calibrated": True, "corpus_hash": "x", "limits": {}},
            {
                "schema": 1,
                "version": "x",
                "calibrated": True,
                "corpus_hash": "x",
                "limits": {metric: (float("nan") if metric == "rgb_mae" else 0) for metric in METRICS},
            },
        )
        for index, payload in enumerate(invalid_profiles):
            with self.subTest(index=index):
                path = self.root / f"profile-{index}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_profile(path)

        for schema in (True, 1.0):
            with self.subTest(schema=schema):
                with self.assertRaises(ValueError):
                    FidelityProfile(
                        schema=schema,
                        version="x",
                        calibrated=True,
                        corpus_hash="a" * 64,
                        limits={metric: 0.0 for metric in METRICS},
                    )
        for corpus_hash in ("A" * 64, "a" * 63, "g" * 64):
            with self.subTest(corpus_hash=corpus_hash):
                with self.assertRaises(ValueError):
                    FidelityProfile(
                        schema=1,
                        version="x",
                        calibrated=True,
                        corpus_hash=corpus_hash,
                        limits={metric: 0.0 for metric in METRICS},
                    )
        with self.assertRaises(ValueError):
            FidelityProfile(
                schema=1,
                version="x",
                calibrated=True,
                corpus_hash="a" * 64,
                limits={
                    metric: (10**1000 if metric == "rgb_mae" else 0.0)
                    for metric in METRICS
                },
            )

    def test_partial_alpha_uses_soft_iou_and_union_weighted_rgb(self):
        reference = Image.new("RGBA", (1, 1), (0, 0, 0, 128))
        candidate = Image.new("RGBA", (1, 1), (0, 0, 0, 64))
        self.assertAlmostEqual(_silhouette_error(reference, candidate), 0.5)

        reference = Image.new("RGBA", (2, 1))
        candidate = Image.new("RGBA", (2, 1))
        reference.putdata(((0, 0, 0, 128), (0, 0, 0, 255)))
        candidate.putdata(((255, 255, 255, 128), (0, 0, 0, 255)))
        self.assertAlmostEqual(_rgb_mae(reference, candidate), 128 / 383, places=6)

    def test_worst_angle_fails_even_when_average_is_small(self):
        reference_entries = _matrix_entries(self.reference)
        candidate_entries = _matrix_entries(self.candidate)
        index = next(
            i
            for i, entry in enumerate(candidate_entries)
            if (entry["pass"], entry["pose"], entry["angle"])
            == ("clay", "bind", "right")
        )
        candidate_entries[index] = _entry(
            self.candidate, "clay", "bind", "right", box=(0, 0, 2, 2)
        )
        _write_manifest(self.reference, reference_entries)
        _write_manifest(self.candidate, candidate_entries)

        result = compare_render_sets(
            self.reference, self.candidate, _profile(silhouette_iou=0.5)
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.worst_scope, "clay/right")
        self.assertIn("silhouette_iou", {failure.gate for failure in result.failures})
        self.assertAlmostEqual(result.metrics["silhouette_iou"], 1.0)
        self.assertEqual(result.metrics["fidelity_score"], 0.0)

    def test_empty_masks_and_linear_rgb_are_well_defined(self):
        key = ("clay", "bind", "front")
        reference_entries = _matrix_entries(self.reference)
        candidate_entries = _matrix_entries(self.candidate)
        ref_index = next(i for i, item in enumerate(reference_entries) if _entry_key(item) == key)
        candidate_index = next(i for i, item in enumerate(candidate_entries) if _entry_key(item) == key)
        ref_entry = _entry(self.reference, *key, box=None)
        candidate_entry = _entry(self.candidate, *key, box=None)
        reference_entries[ref_index] = ref_entry
        candidate_entries[candidate_index] = candidate_entry
        _write_manifest(self.reference, reference_entries)
        _write_manifest(self.candidate, candidate_entries)
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertEqual(result.metrics["silhouette_iou"], 0.0)
        self.assertEqual(result.metrics["edge_error"], 0.0)

        candidate_entry = _entry(self.candidate, *key, box=(2, 2, 6, 6))
        candidate_entries[candidate_index] = candidate_entry
        _write_manifest(self.candidate, candidate_entries)
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertEqual(result.metrics["silhouette_iou"], 1.0)
        self.assertEqual(result.metrics["edge_error"], 1.0)

        ref_entry = _entry(self.reference, *key, color=(128, 128, 128, 255))
        candidate_entry = _entry(self.candidate, *key, color=(255, 255, 255, 255))
        reference_entries[ref_index] = ref_entry
        candidate_entries[candidate_index] = candidate_entry
        _write_manifest(self.reference, reference_entries)
        _write_manifest(self.candidate, candidate_entries)
        result = compare_render_sets(self.reference, self.candidate, _profile())
        expected = 1.0 - ((128 / 255 + 0.055) / 1.055) ** 2.4
        self.assertAlmostEqual(result.metrics["rgb_mae"], expected, places=6)

    def test_pose_scope_and_geometry_use_worst_region(self):
        poses = ("bind", "run")
        regions = ("body", "wheel")
        reference_entries = _matrix_entries(self.reference, poses=poses)
        candidate_entries = _matrix_entries(self.candidate, poses=poses)
        geometry = [
            {
                "scope": "body",
                "pose": "bind",
                "surface_bidirectional_p95": 0.01,
                "surface_max": 0.02,
                "normal_angle_p95": 0.03,
                "uv_error_p95": 0.04,
                "skinning_error_p95": 0.05,
                "region_missing": False,
            },
            {
                "scope": "body",
                "pose": "run",
                **{metric: 0.0 for metric in GEOMETRY_METRICS},
                "region_missing": False,
            },
            {
                "scope": "wheel",
                "pose": "bind",
                **{metric: 0.0 for metric in GEOMETRY_METRICS},
                "region_missing": False,
            },
            {
                "scope": "wheel",
                "pose": "run",
                "surface_bidirectional_p95": 0.9,
                "surface_max": 0.8,
                "normal_angle_p95": 0.7,
                "uv_error_p95": 0.6,
                "skinning_error_p95": 0.5,
                "region_missing": False,
            },
        ]
        _write_manifest(
            self.reference, reference_entries, poses=poses, regions=regions
        )
        _write_manifest(
            self.candidate,
            candidate_entries,
            geometry=geometry,
            poses=poses,
            regions=regions,
        )

        result = compare_render_sets(
            self.reference,
            self.candidate,
            _profile(surface_bidirectional_p95=0.2),
        )

        self.assertEqual(result.metrics["surface_bidirectional_p95"], 0.9)
        failure = next(f for f in result.failures if f.gate == "surface_bidirectional_p95")
        self.assertEqual(failure.scope, "wheel/run")
        self.assertEqual(result.worst_scope, "wheel/run")

    def test_missing_corrupt_mismatched_or_textured_inputs_are_stable_failures(self):
        self.write_matching()
        manifest = json.loads((self.candidate / "render_manifest.json").read_text())
        manifest["entries"][0]["texture_missing"] = True
        (self.candidate / "render_manifest.json").write_text(json.dumps(manifest))
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertIn("texture_missing", {failure.gate for failure in result.failures})

        (self.candidate / manifest["entries"][0]["image"]).unlink()
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertIn("missing_image", {failure.gate for failure in result.failures})

        (self.reference / "render_manifest.json").unlink()
        first = compare_render_sets(self.reference, self.candidate, _profile())
        second = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertEqual(first.failures, second.failures)
        self.assertIn("missing_manifest", {failure.gate for failure in first.failures})

    def test_entry_sets_hashes_and_decodable_files_are_hard_gates(self):
        self.write_matching()
        manifest_path = self.candidate / "render_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["entries"][0]["angle"] = "back"
        (self.candidate / manifest["entries"][0]["image"]).unlink()
        manifest_path.write_text(json.dumps(manifest))
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertIn("entry_mismatch", {failure.gate for failure in result.failures})
        self.assertIn("missing_image", {failure.gate for failure in result.failures})

        self.write_matching()
        manifest = json.loads(manifest_path.read_text())
        manifest["entries"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertIn("hash_mismatch", {failure.gate for failure in result.failures})

        self.write_matching()
        image_path = self.candidate / "clay/bind/front.png"
        image_path.write_bytes(b"not an image")
        manifest = json.loads(manifest_path.read_text())
        target = next(
            entry
            for entry in manifest["entries"]
            if _entry_key(entry) == ("clay", "bind", "front")
        )
        target["sha256"] = _sha256(image_path)
        manifest_path.write_text(json.dumps(manifest))
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertIn("corrupt_image", {failure.gate for failure in result.failures})

    def test_zero_limits_have_defined_ratios(self):
        self.write_matching()
        passed = compare_render_sets(
            self.reference,
            self.candidate,
            _profile(**{metric: 0.0 for metric in METRICS}),
        )
        self.assertTrue(passed.passed)
        self.assertEqual(passed.metrics["fidelity_score"], 1.0)

        candidate_entry = _entry(
            self.candidate, "clay", "bind", "front", box=(0, 0, 2, 2)
        )
        manifest = json.loads((self.candidate / "render_manifest.json").read_text())
        index = next(
            i
            for i, entry in enumerate(manifest["entries"])
            if _entry_key(entry) == ("clay", "bind", "front")
        )
        manifest["entries"][index] = candidate_entry
        (self.candidate / "render_manifest.json").write_text(json.dumps(manifest))
        failed = compare_render_sets(
            self.reference,
            self.candidate,
            _profile(**{metric: 0.0 for metric in METRICS}),
        )
        self.assertFalse(failed.passed)
        self.assertEqual(failed.metrics["fidelity_score"], 0.0)

    def test_expected_matrix_rejects_empty_subset_duplicates_and_missing_entries(self):
        cases = []
        self.write_matching()
        baseline = json.loads((self.candidate / "render_manifest.json").read_text())

        empty = json.loads(json.dumps(baseline))
        empty["expected"]["passes"] = []
        empty["entries"] = []
        cases.append(("empty", empty, "invalid_expected"))

        subset = json.loads(json.dumps(baseline))
        subset["expected"]["passes"] = ["clay"]
        subset["entries"] = [e for e in subset["entries"] if e["pass"] == "clay"]
        cases.append(("subset", subset, "invalid_expected"))

        duplicate_expected = json.loads(json.dumps(baseline))
        duplicate_expected["expected"]["angles"].append("front")
        cases.append(("duplicate-expected", duplicate_expected, "invalid_expected"))

        missing = json.loads(json.dumps(baseline))
        missing["entries"].pop()
        cases.append(("missing-entry", missing, "entry_matrix"))

        duplicate = json.loads(json.dumps(baseline))
        duplicate["entries"].append(dict(duplicate["entries"][0]))
        cases.append(("duplicate-entry", duplicate, "duplicate_entry"))

        manifest_path = self.candidate / "render_manifest.json"
        for name, payload, expected_gate in cases:
            with self.subTest(name=name):
                manifest_path.write_text(json.dumps(payload), encoding="utf-8")
                result = compare_render_sets(self.reference, self.candidate, _profile())
                self.assertFalse(result.passed)
                self.assertIn(expected_gate, {failure.gate for failure in result.failures})

    def test_expected_must_match_and_geometry_matrix_is_complete(self):
        self.write_matching()
        manifest_path = self.candidate / "render_manifest.json"
        baseline = json.loads(manifest_path.read_text())

        mismatch = json.loads(json.dumps(baseline))
        mismatch["expected"]["regions"] = ["body", "extra"]
        manifest_path.write_text(json.dumps(mismatch))
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertIn("expected_mismatch", {failure.gate for failure in result.failures})

        missing_geometry = json.loads(json.dumps(baseline))
        missing_geometry["geometry"] = []
        manifest_path.write_text(json.dumps(missing_geometry))
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertIn("geometry_matrix", {failure.gate for failure in result.failures})

        missing_metric = json.loads(json.dumps(baseline))
        del missing_metric["geometry"][0]["uv_error_p95"]
        manifest_path.write_text(json.dumps(missing_metric))
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertIn("invalid_geometry", {failure.gate for failure in result.failures})

    def test_manifest_configuration_must_match_and_geometry_audit_is_validated(self):
        self.write_matching()
        reference_path = self.reference / "render_manifest.json"
        candidate_path = self.candidate / "render_manifest.json"
        reference = json.loads(reference_path.read_text())
        candidate = json.loads(candidate_path.read_text())
        reference["configuration"] = {"schema": 1, "name": "engine-default", "bodygroups": {"hood": 0}, "lod_index": 0, "source_pairs": [{"source_identity": "body.smd", "reference_sha256": "a" * 64, "candidate_sha256": "b" * 64}]}
        candidate["configuration"] = {"schema": 1, "name": "hood-open", "bodygroups": {"hood": 1}, "lod_index": 0, "source_pairs": [{"source_identity": "body.smd", "reference_sha256": "a" * 64, "candidate_sha256": "b" * 64}]}
        reference["geometry_audit"] = {"body/bind": {"input_triangles": 3, "kept_triangles": 2, "filtered_degenerate_triangles": 1, "filtered_indices_sha256": hashlib.sha256(b"1").hexdigest()}}
        candidate["geometry_audit"] = dict(reference["geometry_audit"])
        reference_path.write_text(json.dumps(reference), encoding="utf-8")
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

        result = compare_render_sets(self.reference, self.candidate, _profile())

        self.assertIn("configuration_mismatch", {failure.gate for failure in result.failures})
        candidate["configuration"] = reference["configuration"]
        candidate["geometry_audit"]["body/bind"]["kept_triangles"] = 3
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertIn("invalid_geometry_audit", {failure.gate for failure in result.failures})

    def test_region_missing_is_a_hard_failure_even_with_permissive_limits(self):
        self.write_matching()
        manifest_path = self.candidate / "render_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["geometry"][0]["region_missing"] = True
        manifest_path.write_text(json.dumps(manifest))

        result = compare_render_sets(
            self.reference,
            self.candidate,
            _profile(**{metric: 1_000_000.0 for metric in METRICS}),
        )

        self.assertFalse(result.passed)
        self.assertIn("region_missing", {failure.gate for failure in result.failures})

    def test_region_missing_field_is_required_and_exactly_boolean(self):
        self.write_matching()
        manifest_path = self.candidate / "render_manifest.json"
        baseline = json.loads(manifest_path.read_text())
        cases = ("absent", None), ("integer", 0), ("string", "false")
        for name, value in cases:
            with self.subTest(name=name):
                manifest = json.loads(json.dumps(baseline))
                if value is None:
                    del manifest["geometry"][0]["region_missing"]
                else:
                    manifest["geometry"][0]["region_missing"] = value
                manifest_path.write_text(json.dumps(manifest))
                result = compare_render_sets(
                    self.reference,
                    self.candidate,
                    _profile(**{metric: 1_000_000.0 for metric in METRICS}),
                )
                self.assertFalse(result.passed)
                self.assertIn(
                    "invalid_geometry", {failure.gate for failure in result.failures}
                )

    def test_pose_frame_mapping_is_required_complete_and_matches(self):
        self.write_matching(poses=("bind", "run"))
        manifest_path = self.candidate / "render_manifest.json"
        baseline = json.loads(manifest_path.read_text())

        for name, pose_frames in (
            ("missing", None),
            ("incomplete", {"bind": 0}),
            ("bool-frame", {"bind": 0, "run": True}),
        ):
            with self.subTest(name=name):
                manifest = json.loads(json.dumps(baseline))
                if pose_frames is None:
                    del manifest["expected"]["pose_frames"]
                else:
                    manifest["expected"]["pose_frames"] = pose_frames
                manifest_path.write_text(json.dumps(manifest))
                result = compare_render_sets(self.reference, self.candidate, _profile())
                self.assertIn(
                    "invalid_expected", {failure.gate for failure in result.failures}
                )

        mismatch = json.loads(json.dumps(baseline))
        mismatch["expected"]["pose_frames"]["run"] = 99
        manifest_path.write_text(json.dumps(mismatch))
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertIn("expected_mismatch", {failure.gate for failure in result.failures})


class RenderPreviewArgumentTests(unittest.TestCase):
    def test_module_imports_without_blender_and_legacy_arguments_are_unchanged(self):
        import render_previews

        args = render_previews._parse_args(
            ["--before", "before.smd", "--after", "after.smd", "--out", "renders"]
        )

        self.assertEqual(args.before, ["before.smd"])
        self.assertEqual(args.after, ["after.smd"])
        self.assertEqual(args.out, "renders")
        self.assertEqual(args.size, 1024)
        self.assertEqual(
            args.angles, "front,back,left,right,top,bottom,iso1,iso2"
        )
        self.assertIsNone(args.passes)
        self.assertIsNone(args.poses)
        self.assertIsNone(args.materials_root)
        self.assertIsNone(args.vtfcmd)
        self.assertIsNone(args.texture_cache)
        self.assertFalse(args.aggregate_regions)
        self.assertIsNone(args.region_manifest)
        self.assertIsNone(args.source_root)
        self.assertIsNone(args.focus_region)
        self.assertFalse(render_previews._is_extended_mode(args))

    def test_focus_argument_enables_extended_mode_and_has_hard_matrix(self):
        import render_previews

        args = render_previews._parse_args([
            "--before", "before.smd", "--after", "after.smd", "--out", "renders",
            "--focus-region", "r-" + "a" * 64,
        ])
        self.assertTrue(render_previews._is_extended_mode(args))
        self.assertEqual(
            render_previews._validated_focus_request(args, (("bind", 0),)),
            "r-" + "a" * 64,
        )
        for mutate in (
            lambda item: setattr(item, "aggregate_regions", True),
            lambda item: setattr(item, "focus_region", "bad"),
        ):
            changed = render_previews._parse_args([
                "--before", "before.smd", "--after", "after.smd", "--out", "renders",
                "--focus-region", "r-" + "a" * 64,
            ])
            mutate(changed)
            with self.assertRaises(ValueError):
                render_previews._validated_focus_request(changed, (("bind", 0),))
        with self.assertRaisesRegex(ValueError, "poses"):
            render_previews._validated_focus_request(
                args, (("bind", 0), ("run", 1), ("idle", 2))
            )

    def test_new_arguments_parse_passes_and_pose_frames_deterministically(self):
        import render_previews

        args = render_previews._parse_args(
            [
                "--before",
                "before.smd",
                "--after",
                "after.smd",
                "--out",
                "renders",
                "--passes",
                "textured,clay",
                "--poses",
                "bind:0,run:12",
                "--materials-root",
                "materials",
                "--vtfcmd",
                "VTFCmd.exe",
                "--texture-cache",
                "shared-vtf-cache",
                "--region-manifest",
                "maximum_region_manifest.json",
                "--source-root",
                "source-root",
                "--aggregate-regions",
            ]
        )

        self.assertTrue(render_previews._is_extended_mode(args))
        self.assertEqual(render_previews._parse_csv(args.passes), ("textured", "clay"))
        self.assertEqual(render_previews._parse_poses(args.poses), (("bind", 0), ("run", 12)))
        self.assertEqual(args.materials_root, ["materials"])
        self.assertEqual(args.vtfcmd, "VTFCmd.exe")
        self.assertEqual(args.texture_cache, "shared-vtf-cache")
        self.assertEqual(args.region_manifest, "maximum_region_manifest.json")
        self.assertEqual(args.source_root, "source-root")
        self.assertTrue(args.aggregate_regions)

    def test_extended_source_context_is_independent_from_manifest_directory(self):
        import render_previews
        from maximum_optimizer.regions import build_region_manifest

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_root = root / "deep" / "source"
            source_root.mkdir(parents=True)
            (source_root / "wheel.qc").write_text(
                '$cdmaterials "models/cars/wheel"\n$body "wheel" "wh.smd"\n',
                encoding="utf-8",
            )
            smd = source_root / "wh.smd"
            smd.write_text(
                "version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n"
                "0 0 0 0 0 0 0\nend\ntriangles\nrim\n"
                "0 0 0 0 0 0 1 0 0\n0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\nend\n",
                encoding="utf-8",
            )
            manifest = build_region_manifest(
                (("wh.smd", "wheel", ("rim",)),),
                occurrences={"wh.smd": ({
                    "graph_file": "wheel.qc", "directive": "$body/studio",
                    "line": 2, "logical_path": "wh.smd",
                },)},
            )

            identities, materials, search = render_previews._extended_source_context(
                manifest, source_root, [smd]
            )

            self.assertEqual(identities, ("wh.smd",))
            self.assertEqual(materials, {"wh.smd": ("rim",)})
            self.assertEqual(search, {"wh.smd": ("models/cars/wheel",)})

    def test_focus_source_context_selects_exact_paired_source_and_hashes(self):
        import render_previews
        from maximum_optimizer.regions import build_region_manifest

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_root = root / "source"
            candidate_root = root / "candidate"
            source_root.mkdir(); candidate_root.mkdir()
            before_body = source_root / "body.smd"; before_body.write_bytes(b"before-body")
            before_wheel = source_root / "wheel.smd"; before_wheel.write_bytes(b"before-wheel")
            after_body = candidate_root / "body_opt.smd"; after_body.write_bytes(b"after-body")
            after_wheel = candidate_root / "wheel_opt.smd"; after_wheel.write_bytes(b"after-wheel")
            manifest = build_region_manifest((
                ("body.smd", "body", ("paint",)),
                ("body.smd", "trim", ("chrome",)),
                ("wheel.smd", "wheel", ("rubber",)),
            ))
            body = next(item for item in manifest.entries if item.descriptor.object_name == "body")
            configuration = {
                "source_pairs": [
                    {"source_identity": "BODY.SMD", "reference_sha256": _sha256(before_body),
                     "candidate_sha256": _sha256(after_body)},
                    {"source_identity": "wheel.smd", "reference_sha256": _sha256(before_wheel),
                     "candidate_sha256": _sha256(after_wheel)},
                ]
            }

            selected = render_previews._focus_source_context(
                manifest, body.key, source_root,
                [before_wheel, before_body], [after_wheel, after_body], configuration,
            )

            self.assertEqual(selected[0], [before_body.resolve()])
            self.assertEqual(selected[1], [after_body.resolve()])
            self.assertEqual(selected[2], "body.smd")
            self.assertEqual(
                {entry.descriptor.object_name for entry in selected[3].entries},
                {"body", "trim"},
            )

            forged = json.loads(json.dumps(configuration))
            forged["source_pairs"][0]["candidate_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "hash"):
                render_previews._focus_source_context(
                    manifest, body.key, source_root,
                    [before_body], [after_body], forged,
                )
            with self.assertRaisesRegex(ValueError, "paired"):
                render_previews._focus_source_context(
                    manifest, body.key, source_root,
                    [before_body, before_body], [after_body, after_body], configuration,
                )
            with mock.patch.object(
                render_previews, "_path_is_link_or_reparse",
                side_effect=lambda path: Path(path) == after_body,
            ):
                with self.assertRaisesRegex(ValueError, "link or reparse"):
                    render_previews._focus_source_context(
                        manifest, body.key, source_root,
                        [before_body], [after_body], configuration,
                    )
            with mock.patch.object(
                render_previews, "_path_is_link_or_reparse",
                side_effect=lambda path: Path(path) == candidate_root,
            ):
                with self.assertRaisesRegex(ValueError, "ancestor"):
                    render_previews._focus_source_context(
                        manifest, body.key, source_root,
                        [before_body], [after_body], configuration,
                    )

    def test_focus_object_isolation_resolves_full_source_before_hiding_siblings(self):
        import render_previews
        from maximum_optimizer.regions import build_region_manifest

        class Material:
            def __init__(self, name): self.name = name
        class Data:
            def __init__(self, material): self.materials = [Material(material)]
        class Obj:
            type = "MESH"
            def __init__(self, name, material):
                self.name = name; self.data = Data(material); self.hide_render = False
                self._props = {"maximum_region_source_identity": "body.smd"}
            def get(self, name, default=None): return self._props.get(name, default)

        manifest = build_region_manifest((
            ("body.smd", "body", ("slot:0:paint",)),
            ("body.smd", "trim", ("slot:1:chrome",)),
        ))
        body = next(item for item in manifest.entries if item.descriptor.object_name == "body")
        body_obj = Obj("body", "paint")
        trim_obj = Obj("trim", "chrome")

        selected = render_previews._focus_mesh_objects(
            (trim_obj, body_obj), manifest,
            {"body.smd": ("paint", "chrome")}, body.key,
        )

        self.assertEqual(selected, (body_obj,))
        self.assertFalse(body_obj.hide_render)
        self.assertTrue(trim_obj.hide_render)
        with self.assertRaises(ValueError):
            render_previews._focus_mesh_objects(
                (body_obj,), manifest, {"body.smd": ("paint", "chrome")}, body.key,
            )

    def test_focus_render_payload_requires_exact_image_and_region_matrix(self):
        import render_previews

        region = "r-" + "a" * 64
        poses = (("bind", 0), ("run", 12))
        entries = [
            {"pass": render_pass, "pose": pose, "angle": angle}
            for render_pass in PASSES
            for pose, _frame in poses
            for angle in ANGLES
        ]
        snapshots = {pose: {region: {"triangles": [1]}} for pose, _frame in poses}
        render_previews._validate_focus_render_payload(
            entries, snapshots, PASSES, ANGLES, poses, region,
        )
        for changed_entries, changed_snapshots in (
            (entries[:-1], snapshots),
            (entries + [dict(entries[0])], snapshots),
            (entries, {"bind": snapshots["bind"]}),
            (entries, {**snapshots, "run": {region: snapshots["run"][region], "extra": {}}}),
        ):
            with self.subTest(
                entry_count=len(changed_entries), poses=tuple(changed_snapshots)
            ), self.assertRaises(ValueError):
                render_previews._validate_focus_render_payload(
                    changed_entries, changed_snapshots, PASSES, ANGLES, poses, region,
                )

    def test_focus_output_inventory_rejects_stale_extra_images(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            expected = root / "textured/bind/front.png"
            _write_image(expected)
            entries = [{"image": "textured/bind/front.png"}]
            render_previews._validate_focus_output_inventory(root, entries)

            _write_image(root / "clay/old-pose/iso1.png")
            with self.assertRaisesRegex(ValueError, "extra or missing"):
                render_previews._validate_focus_output_inventory(root, entries)

    def test_focus_render_scope_passes_only_target_to_snapshot_bbox_and_materials(self):
        import render_previews
        from maximum_optimizer.regions import build_region_manifest

        class Material:
            def __init__(self, name): self.name = name
        class Data:
            def __init__(self, material): self.materials = [Material(material)]
        class Obj:
            type = "MESH"
            def __init__(self, name, material):
                self.name = name; self.data = Data(material); self.hide_render = False
                self._props = {"maximum_region_source_identity": "body.smd"}
            def get(self, name, default=None): return self._props.get(name, default)

        manifest = build_region_manifest((
            ("body.smd", "body", ("slot:0:paint",)),
            ("body.smd", "trim", ("slot:1:chrome",)),
        ))
        region = next(item.key for item in manifest.entries if item.descriptor.object_name == "body")
        body = Obj("body", "paint"); trim = Obj("trim", "chrome")
        snapshots = {"bind": {region: {"triangles": [{"positions": ((0, 0, 0),) * 3}]}}}
        bbox = {"min": [0, 0, 0], "max": [1, 1, 1], "diagonal": 1.0}
        camera = mock.MagicMock(); camera.data = mock.MagicMock()
        fake_bpy = mock.MagicMock()
        with tempfile.TemporaryDirectory() as raw, \
             mock.patch.object(render_previews, "bpy", fake_bpy), \
             mock.patch.object(render_previews, "_clear_scene"), \
             mock.patch.object(render_previews, "_setup_scene"), \
             mock.patch.object(render_previews, "_ensure_camera", return_value=camera), \
             mock.patch.object(render_previews, "_get_mesh_objects", return_value=(trim, body)), \
             mock.patch.object(render_previews, "_capture_pose_snapshots", return_value=snapshots) as capture, \
             mock.patch.object(render_previews, "_framing_from_snapshots", return_value=(bbox, (render_previews.Vector((0, 0, 0)), 1.0, 2.0))), \
             mock.patch.object(render_previews, "_setup_lights"), \
             mock.patch.object(render_previews, "_set_pose_state"), \
             mock.patch.object(render_previews, "_set_camera_pose"), \
             mock.patch.object(render_previews, "_render_entry", side_effect=lambda _root, render_pass, pose, angle, *_args, **_kwargs: {"pass": render_pass, "pose": pose, "angle": angle}), \
             mock.patch.object(render_previews, "_apply_textured_materials", return_value={"missing": (), "resolved": ()}) as textured, \
             mock.patch.object(render_previews, "_apply_clay_material") as clay:
            render_previews._render_extended_set(
                "focus", [], Path(raw), ("front",), 64, PASSES, (("bind", 0),),
                (), None, Path(raw) / "cache", manifest, (),
                {"body.smd": ("paint", "chrome")}, focus_region=region,
            )

        self.assertEqual(capture.call_args.args[0], (body,))
        self.assertEqual(textured.call_args.args[0], (body,))
        self.assertEqual(clay.call_args.args[0], (body,))
        self.assertTrue(trim.hide_render)

    def test_explicit_texture_cache_is_shared_outside_long_state_name(self):
        import render_previews

        args = type("Args", (), {"texture_cache": "C:/short/cache"})()
        selected = render_previews._texture_cache_root(
            args, Path("C:/very/long/bodygroup-state/renders")
        )
        self.assertEqual(selected, Path("C:/short/cache").resolve())

    def test_configuration_or_aggregate_alone_enable_extended_mode(self):
        import render_previews

        configuration = render_previews._parse_args([
            "--before", "before.smd", "--after", "after.smd", "--out", "renders",
            "--configuration-manifest", "configuration.json",
        ])
        aggregate = render_previews._parse_args([
            "--before", "before.smd", "--after", "after.smd", "--out", "renders",
            "--aggregate-regions",
        ])
        self.assertTrue(render_previews._is_extended_mode(configuration))
        self.assertTrue(render_previews._is_extended_mode(aggregate))

    def test_invalid_new_pass_or_pose_is_rejected(self):
        import render_previews

        with self.assertRaisesRegex(ValueError, "pass"):
            render_previews._validated_passes("textured,fake")
        with self.assertRaisesRegex(ValueError, "pass"):
            render_previews._validated_passes("clay")
        with self.assertRaisesRegex(ValueError, "angle"):
            render_previews._validated_angles("front")
        for raw in ("run", "run:nope", "run:1,run:2", "bad/name:1"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "pose"):
                    render_previews._parse_poses(raw)
        with self.assertRaisesRegex(ValueError, "bind"):
            render_previews._parse_poses("run:1")
        args = render_previews._parse_args([
            "--before", "before.smd", "--after", "after.smd", "--out", "renders",
            "--passes", "textured,clay",
        ])
        with self.assertRaisesRegex(ValueError, "region manifest"):
            render_previews._required_region_manifest(args)

    def test_vmt_base_texture_parser_and_manifest_writer_are_deterministic(self):
        import render_previews

        self.assertEqual(
            render_previews._extract_base_texture(
                'VertexLitGeneric\n{\n  "$basetexture" "vehicles/paint/body"\n}'
            ),
            "vehicles/paint/body",
        )
        self.assertIsNone(render_previews._extract_base_texture("UnlitGeneric {}"))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image_path = root / "clay/bind/front.png"
            _write_image(image_path)
            entry = render_previews._render_entry(
                root, "clay", "bind", "front", image_path, texture_missing=False
            )
            render_previews._write_render_manifest(
                root,
                [entry],
                [],
                {"min": [0, 0, 0], "max": [1, 1, 1], "diagonal": math.sqrt(3)},
                expected={
                    "passes": list(PASSES),
                    "angles": list(ANGLES),
                    "poses": ["bind"],
                    "pose_frames": {"bind": 0},
                    "regions": ["body"],
                },
                stride=3,
                seed=17,
            )
            payload = json.loads((root / "render_manifest.json").read_text())
            self.assertEqual(payload["schema"], 1)
            self.assertEqual(payload["entries"], [entry])
            self.assertEqual(payload["expected"]["regions"], ["body"])
            self.assertEqual(payload["sampling"], {"stride": 3, "seed": 17})
            self.assertEqual(entry["sha256"], _sha256(image_path))
            self.assertEqual(entry["image"], "clay/bind/front.png")

    def test_renderer_consumes_explicit_shared_region_manifest(self):
        import render_previews
        from maximum_optimizer.regions import build_region_manifest

        observations = (
            ("models/car.smd", "Body_OPT.001", ("vehicles\\paint/Ç",)),
            ("models/car.smd", "Body.002", ("vehicles/paint/ç",)),
        )
        manifest = build_region_manifest(observations)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "regions.json"
            path.write_text(json.dumps(manifest.to_payload()), encoding="utf-8")
            loaded = render_previews._load_region_manifest(path)
        self.assertEqual(loaded.entries, manifest.entries)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_region_manifest((observations[0], observations[0]))

    def test_renderer_reads_legitimate_source_material_suffix_without_blender_renaming(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "body.smd"
            source.write_text(
                "version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n"
                "0 0 0 0 0 0 0\nend\ntriangles\nPaint.001\n"
                "0 0 0 0 0 0 1 0 0\n0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\nend\n",
                encoding="utf-8",
            )
            self.assertEqual(render_previews._smd_material_names(source), ("Paint.001",))

    def test_barycentric_loop_attributes_preserve_seams_and_smooth_normals(self):
        import render_previews

        weights = render_previews._barycentric_weights(
            (0.25, 0.25, 0.0),
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
        )
        self.assertEqual(weights, (0.5, 0.25, 0.25))
        first_seam = render_previews._interpolate_attribute(
            ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)), weights
        )
        second_seam = render_previews._interpolate_attribute(
            ((10.0, 10.0), (11.0, 10.0), (10.0, 11.0)), weights
        )
        self.assertEqual(first_seam, (0.25, 0.25))
        self.assertEqual(second_seam, (10.25, 10.25))
        normal = render_previews._interpolate_attribute(
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            weights,
            normalize=True,
        )
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in normal)), 1.0)

    def test_barycentric_weights_are_scale_invariant_for_small_valid_triangles(self):
        import render_previews

        weights = render_previews._barycentric_weights(
            (0.25e-6, 0.25e-6, 0.0),
            (0.0, 0.0, 0.0),
            (1.0e-6, 0.0, 0.0),
            (0.0, 1.0e-6, 0.0),
        )

        self.assertEqual(weights, (0.5, 0.25, 0.25))

    def test_barycentric_weights_resist_skinny_triangle_cancellation(self):
        import render_previews

        first = (1.816001057624817, 21.231998443603516, 8.67199993133545)
        second = (1.815999984741211, 7.868000030517578, 8.67199993133545)
        third = (1.815999984741211, 7.76800012588501, 8.67199993133545)
        midpoint = tuple((left + right) / 2.0 for left, right in zip(second, third))

        weights = render_previews._barycentric_weights(
            midpoint, first, second, third
        )

        self.assertAlmostEqual(weights[0], 0.0, places=6)
        self.assertAlmostEqual(weights[1], 0.5, places=6)
        self.assertAlmostEqual(weights[2], 0.5, places=6)

    def test_bidirectional_p95_uses_worst_direction_without_dilution(self):
        import render_previews

        self.assertEqual(
            render_previews._directional_p95_max([0.0] * 100, [1.0]),
            1.0,
        )

    def test_direct_topology_metrics_avoid_bvh_ambiguity_but_detect_uv_changes(self):
        import copy
        import render_previews

        diagonal_normal = (-0.1930412492974003, -0.5795026643954697, -0.3126656929623708)
        region = {
            "triangles": [
                {
                    "positions": ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                    "normals": (diagonal_normal,) * 3,
                    "uvs": ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
                }
            ]
        }
        metrics = render_previews._direct_topology_metrics(region, copy.deepcopy(region), 1.0, 1)
        self.assertEqual(
            metrics,
            {
                "surface_bidirectional_p95": 0.0,
                "surface_max": 0.0,
                "normal_angle_p95": 0.0,
                "uv_error_p95": 0.0,
                "skinning_error_p95": 0.0,
                "region_missing": False,
            },
        )
        changed = copy.deepcopy(region)
        changed["triangles"][0]["uvs"] = ((0.5, 0.0), (1.0, 0.0), (0.0, 1.0))
        self.assertGreater(
            render_previews._direct_topology_metrics(region, changed, 1.0, 1)[
                "uv_error_p95"
            ],
            0.0,
        )

    def test_zero_triangle_region_is_unconditionally_missing(self):
        import render_previews

        empty = {"scope": "empty", "triangles": []}
        metrics = render_previews._geometry_metrics_for_region(empty, empty, 1.0, 1)
        self.assertTrue(metrics["region_missing"])
        reference_entries = render_previews._reference_geometry_entries(
            {"bind": {"empty": empty}}, ("empty",), ("bind",)
        )
        self.assertTrue(reference_entries[0]["region_missing"])

    def test_degenerate_triangles_are_filtered_with_deterministic_audit(self):
        import render_previews

        region = {
            "scope": "body",
            "triangles": [
                {
                    "positions": ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                    "normals": ((0.0, 0.0, 1.0),) * 3,
                    "uvs": ((0.0, 0.0),) * 3,
                },
                {
                    "positions": ((2.0, 0.0, 0.0),) * 3,
                    "normals": ((0.0, 0.0, 1.0),) * 3,
                    "uvs": ((0.0, 0.0),) * 3,
                },
            ],
        }

        filtered = render_previews._audited_nondegenerate_region(region)

        self.assertEqual(len(filtered["triangles"]), 1)
        self.assertEqual(filtered["triangle_audit"], {
            "input_triangles": 2,
            "kept_triangles": 1,
            "filtered_degenerate_triangles": 1,
            "filtered_indices_sha256": hashlib.sha256(b"1").hexdigest(),
        })

    def test_explicit_aggregate_region_merges_real_triangle_payloads(self):
        import render_previews

        triangle = {
            "positions": ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            "normals": ((0.0, 0.0, 1.0),) * 3,
            "uvs": ((0.0, 0.0),) * 3,
        }
        merged = render_previews._aggregate_triangle_regions((
            {"triangles": [triangle]}, {"triangles": [triangle]},
        ))
        self.assertEqual(merged["scope"], "aggregate")
        self.assertEqual(len(merged["triangles"]), 2)
        self.assertEqual(merged["triangle_audit"]["kept_triangles"], 2)



    def test_pose_snapshot_union_drives_bbox_and_camera_fit(self):
        import render_previews

        def region(points):
            return {
                "scope": "body",
                "triangles": [
                    {
                        "positions": tuple(points),
                        "normals": ((0.0, 0.0, 1.0),) * 3,
                        "uvs": ((0.0, 0.0),) * 3,
                    }
                ],
            }

        snapshots = {
            "bind": {"body": region(((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 1.0, 0.0)))},
            "extreme": {
                "body": region(((100.0, -2.0, 0.0), (99.0, 3.0, 2.0), (100.0, 0.0, 1.0)))
            },
        }

        bbox, fit = render_previews._framing_from_snapshots(snapshots)

        self.assertEqual(bbox["min"], [0.0, -2.0, 0.0])
        self.assertEqual(bbox["max"], [100.0, 3.0, 2.0])
        self.assertEqual(tuple(fit[0]), (50.0, 0.5, 1.0))
        self.assertEqual(fit[1], 140.0)
        self.assertEqual(fit[2], 251.0)
        with self.assertRaisesRegex(ValueError, "evaluated triangle vertices"):
            render_previews._framing_from_snapshots(
                {"bind": {"empty": {"scope": "empty", "triangles": []}}}
            )

    def test_pose_capture_restores_original_frame(self):
        import render_previews

        class Scene:
            frame_current = 7

            def __init__(self):
                self.calls = []

            def frame_set(self, frame):
                self.frame_current = frame
                self.calls.append(frame)

        scene = Scene()

        def capture(_objects, frame):
            scene.frame_set(frame)
            return {"frame": frame}

        snapshots = render_previews._capture_pose_snapshots(
            (), (("bind", 0), ("extreme", 12)), scene=scene, capture=capture
        )

        self.assertEqual(snapshots, {"bind": {"frame": 0}, "extreme": {"frame": 12}})
        self.assertEqual(scene.calls, [0, 12, 7])
        self.assertEqual(scene.frame_current, 7)

        failing_scene = Scene()

        def failing_capture(_objects, frame):
            failing_scene.frame_set(frame)
            raise RuntimeError("capture failed")

        with self.assertRaisesRegex(RuntimeError, "capture failed"):
            render_previews._capture_pose_snapshots(
                (), (("bind", 0),), scene=failing_scene, capture=failing_capture
            )
        self.assertEqual(failing_scene.calls, [0, 7])
        self.assertEqual(failing_scene.frame_current, 7)

    def test_material_paths_cannot_escape_root(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            valid = root / "vehicles" / "body.vmt"
            valid.parent.mkdir()
            valid.write_text("VertexLitGeneric {}", encoding="utf-8")
            self.assertEqual(
                render_previews._contained_material_path(root, "vehicles/body", ".vmt"),
                valid.resolve(),
            )
            for value in (
                "/absolute",
                "C:/absolute",
                "\\\\server\\share\\material",
                "../escape",
                "vehicles/../../escape",
            ):
                with self.subTest(value=value):
                    self.assertIsNone(
                        render_previews._contained_material_path(root, value, ".vmt")
                    )
            escaping_vmt = root / "escape.vmt"
            escaping_vmt.write_text(
                'VertexLitGeneric\n{\n"$basetexture" "/outside"\n}', encoding="utf-8"
            )
            (root / "outside.vtf").write_bytes(b"vtf")
            with mock.patch.object(render_previews, "_convert_vtf") as convert:
                self.assertIsNone(
                    render_previews._source_texture_png(
                        "escape", root, root / "VTFCmd.exe", root / "cache"
                    )
                )
                convert.assert_not_called()

    def test_cdmaterials_resolve_short_smd_material_case_insensitively(self):
        import render_previews
        from maximum_optimizer.regions import build_region_manifest

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_root = root / "source"
            source_root.mkdir()
            (source_root / "Wheel.QC").write_text(
                '$cdmaterials "Models\\DiggerCars\\Pontiac_TransAm3\\"\n'
                '$include "Parts.QCI"\n',
                encoding="utf-8",
            )
            (source_root / "Parts.QCI").write_text(
                '$body "wheel" "wh.smd"\n',
                encoding="utf-8",
            )
            (source_root / "wh.smd").write_text("", encoding="utf-8")
            (source_root / "Unrelated.QC").write_text(
                '$cdmaterials "models/unrelated"\n$body "other" "other.smd"\n',
                encoding="utf-8",
            )
            (source_root / "other.smd").write_text("", encoding="utf-8")
            manifest = build_region_manifest(
                (("wh.smd", "wh", ("rim2",)),),
                occurrences={
                    "wh.smd": (
                        {
                            "graph_file": "parts.qci",
                            "directive": "$body/studio",
                            "line": 2,
                            "logical_path": "wh.smd",
                        },
                    )
                },
            )
            search = render_previews._source_cdmaterial_search_paths(
                manifest, source_root
            )
            self.assertEqual(
                search,
                {"wh.smd": ("Models/DiggerCars/Pontiac_TransAm3",)},
            )

            materials = root / "materials"
            material_dir = materials / "models" / "diggercars" / "pontiac_transam3"
            material_dir.mkdir(parents=True)
            (material_dir / "RIM2.VMT").write_text(
                'VertexLitGeneric\n{\n"$basetexture" "MODELS/DIGGERCARS/PONTIAC_TRANSAM3/RIM2"\n}',
                encoding="utf-8",
            )
            texture = material_dir / "rim2.VTF"
            texture.write_bytes(b"vtf")
            converted = root / "rim2.png"
            converted.write_bytes(b"png")
            with mock.patch.object(
                render_previews, "_convert_vtf", return_value=converted
            ) as convert:
                resolved = render_previews._source_texture_png(
                    "rim2",
                    materials,
                    root / "VTFCmd.exe",
                    root / "cache",
                    search_paths=search["wh.smd"],
                )
            self.assertEqual(resolved, converted)
            convert.assert_called_once_with(texture, root / "VTFCmd.exe", root / "cache")

    def test_cdmaterials_use_declared_search_order_and_escape_fails_closed(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            materials = root / "materials"
            for directory in ("models/first", "models/second"):
                target = materials / directory
                target.mkdir(parents=True)
                (target / "paint.vmt").write_text(
                    'VertexLitGeneric\n{\n"$basetexture" "models/shared/paint"\n}',
                    encoding="utf-8",
                )
            shared = materials / "models" / "shared"
            shared.mkdir()
            (shared / "paint.vtf").write_bytes(b"vtf")

            converted = root / "converted.png"
            with mock.patch.object(render_previews, "_convert_vtf", return_value=converted) as convert:
                self.assertEqual(
                    render_previews._source_texture_png(
                        "paint",
                        materials,
                        root / "VTFCmd.exe",
                        root / "cache",
                        search_paths=("models/first", "models/second"),
                    ),
                    converted,
                )
                self.assertIsNone(
                    render_previews._source_texture_png(
                        "paint",
                        materials,
                        root / "VTFCmd.exe",
                        root / "cache",
                        search_paths=("../outside",),
                    )
                )
                convert.assert_called_once_with(
                    shared / "paint.vtf", root / "VTFCmd.exe", root / "cache"
                )
                evidence = render_previews._source_material_evidence(
                    "paint",
                    materials,
                    search_paths=("models/first", "models/second"),
                )
                self.assertEqual(evidence["search_path_index"], 0)
                self.assertEqual(
                    evidence["resolution_rule"],
                    "materials-root-order-then-qc-search-order-v1",
                )

    def test_vmt_texture_alpha_is_opt_in_only(self):
        import render_previews

        opaque = 'VertexLitGeneric { "$basetexture" "cars/body" }'
        explicit_zero = 'VertexLitGeneric { "$translucent" "0" "$alphatest" 0 }'
        translucent = 'VertexLitGeneric { "$translucent" "1" }'
        alpha_tested = 'VertexLitGeneric { "$alphatest" 1 }'

        self.assertFalse(render_previews._vmt_uses_texture_alpha(opaque))
        self.assertFalse(render_previews._vmt_uses_texture_alpha(explicit_zero))
        self.assertTrue(render_previews._vmt_uses_texture_alpha(translucent))
        self.assertTrue(render_previews._vmt_uses_texture_alpha(alpha_tested))

    def test_refract_shader_uses_tint_texture_and_explicit_alpha_semantics(self):
        import render_previews

        refract = (
            'Refract\n{\n "$refracttinttexture" '
            '"models/diggercars/skyline/lights_glass"\n}\n'
        )
        self.assertEqual(
            render_previews._source_texture_reference(refract),
            (
                "refract",
                "$refracttinttexture",
                "models/diggercars/skyline/lights_glass",
                True,
            ),
        )
        self.assertIsNone(render_previews._source_texture_reference(
            'VertexLitGeneric { "$refracttinttexture" "wrong" }'
        ))

    def test_vmt_parser_accepts_one_line_root_directive_and_ignores_comments(self):
        import render_previews

        self.assertEqual(
            render_previews._source_texture_reference(
                'vErTeXlItGeNeRiC { "$BaseTexture" "cars/body" } // overlay comment'
            ),
            ("vertexlitgeneric", "$basetexture", "cars/body", False),
        )

    def test_vmt_parser_uses_first_duplicate_root_directive_like_source_materials(self):
        import render_previews

        duplicated = (
            'VertexLitGeneric { "$basetexture" "cars/body" '
            '"$alphatest" "1" "$alphatest" "0" }'
        )
        self.assertEqual(
            render_previews._source_texture_reference(duplicated),
            ("vertexlitgeneric", "$basetexture", "cars/body", True),
        )

    def test_vmt_duplicate_root_directives_are_recorded_as_ignored_evidence(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            material = root / "cars"
            material.mkdir()
            (material / "body.vmt").write_text(
                'VertexLitGeneric { "$basetexture" "cars/body" '
                '"$alphatest" "1" "$alphatest" "0" }',
                encoding="utf-8",
            )
            (material / "body.vtf").write_bytes(b"vtf")
            evidence = render_previews._source_material_evidence("cars/body", root)

        self.assertEqual(evidence["duplicate_root_directives"], [
            {"directive": "$alphatest", "ignored_values": ["0"]},
        ])

    def test_vmt_parser_rejects_nested_or_trailing_directive_and_unknown_shader(self):
        import render_previews

        nested = (
            'VertexLitGeneric { Proxies { AnimatedTexture { '
            '"$basetexture" "wrong/nested" } } }'
        )
        trailing = 'VertexLitGeneric { } "$basetexture" "wrong/trailing"'
        unknown = 'CustomShader { "$basetexture" "wrong/unknown" }'
        self.assertIsNone(render_previews._source_texture_reference(nested))
        self.assertIsNone(render_previews._source_texture_reference(trailing))
        self.assertIsNone(render_previews._source_texture_reference(unknown))
        with_root_and_proxies = (
            'VertexLitGeneric { "$basetexture" "right/root" '
            'Proxies { AnimatedTexture { "$basetexture" "wrong/nested" } } }'
        )
        self.assertEqual(
            render_previews._source_texture_reference(with_root_and_proxies),
            ("vertexlitgeneric", "$basetexture", "right/root", False),
        )

    def test_material_roots_are_ordered_overlays_and_resolution_is_audited(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cars = root / "cars"
            framework = root / "framework"
            car_path = cars / "models/diggercars/car"
            shared_path = framework / "models/diggercars/shared"
            car_path.mkdir(parents=True)
            shared_path.mkdir(parents=True)
            (car_path / "skin.vmt").write_text(
                'VertexLitGeneric\n{\n "$basetexture" "models/diggercars/car/skin"\n}', encoding="utf-8"
            )
            (car_path / "skin.vtf").write_bytes(b"car-skin")
            (shared_path / "black.vmt").write_text(
                'VertexLitGeneric\n{\n "$basetexture" "models/diggercars/shared/black"\n}', encoding="utf-8"
            )
            (shared_path / "black.vtf").write_bytes(b"framework-black")
            converted = root / "converted.png"
            with mock.patch.object(render_previews, "_convert_vtf", return_value=converted) as convert:
                self.assertEqual(
                    render_previews._source_texture_png(
                        "black", (cars, framework), root / "VTFCmd.exe", root / "cache",
                        search_paths=("models/diggercars/car", "models/diggercars/shared"),
                    ),
                    converted,
                )
            convert.assert_called_once_with(
                shared_path / "black.vtf", root / "VTFCmd.exe", root / "cache"
            )
            evidence = render_previews._source_material_evidence(
                "black", (cars, framework),
                search_paths=("models/diggercars/car", "models/diggercars/shared"),
            )
            self.assertEqual(evidence["root_index"], 1)
            self.assertEqual(evidence["search_path_index"], 1)
            self.assertEqual(
                evidence["resolution_rule"],
                "materials-root-order-then-qc-search-order-v1",
            )
            self.assertEqual(
                evidence["vmt_sha256"], hashlib.sha256((shared_path / "black.vmt").read_bytes()).hexdigest()
            )
            self.assertEqual(evidence["vtf_sha256"], hashlib.sha256(b"framework-black").hexdigest())

    def test_vtfcmd_conversion_retries_one_transient_failure(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "paint.vtf"
            source.write_bytes(b"vtf")
            tool = root / "VTFCmd.exe"
            tool.write_bytes(b"tool")
            calls = []

            def run(command, **_kwargs):
                calls.append(command)
                if len(calls) == 2:
                    output = Path(command[command.index("-output") + 1]) / "texture.png"
                    output.write_bytes(b"png")
                    return subprocess.CompletedProcess(command, 0)
                return subprocess.CompletedProcess(command, 1)

            with mock.patch.object(render_previews.subprocess, "run", side_effect=run):
                converted = render_previews._convert_vtf(source, tool, root / "cache")

            self.assertIsNotNone(converted)
            self.assertEqual(len(calls), 2)

    def test_vtfcmd_rc_zero_without_output_fails_with_diagnostics(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "paint.vtf"
            source.write_bytes(b"vtf")
            tool = root / "VTFCmd.exe"
            tool.write_bytes(b"tool")
            failed = subprocess.CompletedProcess(
                [str(tool)], 0, stdout="Error creating png file", stderr="legacy MAX_PATH"
            )
            with mock.patch.object(render_previews.subprocess, "run", return_value=failed):
                with self.assertRaisesRegex(RuntimeError, "Error creating png file.*MAX_PATH"):
                    render_previews._convert_vtf(source, tool, root / "cache")

    def test_vtfcmd_long_stem_concurrent_conversion_uses_short_unique_staging(self):
        import concurrent.futures
        import threading
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            long_source_dir = root.joinpath(*(["very_long_source_directory"] * 12))
            long_source_dir.mkdir(parents=True)
            source = long_source_dir / (("long_texture_name_" * 10) + ".vtf")
            source.write_bytes(b"same-vtf")
            tool = root / "VTFCmd.exe"
            tool.write_bytes(b"tool")
            worker_count = 8
            barrier = threading.Barrier(worker_count)
            output_dirs = []
            output_dirs_lock = threading.Lock()

            def run(command, **_kwargs):
                output_dir = Path(command[command.index("-output") + 1])
                with output_dirs_lock:
                    output_dirs.append(output_dir)
                barrier.wait(timeout=5)
                self.assertEqual(Path(command[command.index("-file") + 1]).name, "texture.vtf")
                (output_dir / "texture.png").write_bytes(b"png")
                return subprocess.CompletedProcess(command, 0)

            cache = root / "short-cache"
            with mock.patch.object(render_previews.subprocess, "run", side_effect=run):
                with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
                    converted = tuple(pool.map(
                        lambda _index: render_previews._convert_vtf(source, tool, cache),
                        range(worker_count),
                    ))

            self.assertEqual(len(set(converted)), 1)
            self.assertEqual(converted[0].read_bytes(), b"png")
            self.assertEqual(len(set(output_dirs)), worker_count)
            self.assertTrue(all(len(str(path / "texture.png")) < 160 for path in output_dirs))

    def test_vtfcmd_cache_key_changes_when_tool_changes(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "paint.vtf"
            source.write_bytes(b"vtf")
            tool = root / "VTFCmd.exe"
            tool.write_bytes(b"tool-v1")

            def run(command, **_kwargs):
                output_dir = Path(command[command.index("-output") + 1])
                (output_dir / "texture.png").write_bytes(b"png")
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.object(render_previews.subprocess, "run", side_effect=run) as run_mock:
                first = render_previews._convert_vtf(source, tool, root / "cache")
                tool.write_bytes(b"tool-v2")
                second = render_previews._convert_vtf(source, tool, root / "cache")

            self.assertNotEqual(first, second)
            self.assertEqual(run_mock.call_count, 2)

    def test_vtfcmd_copy_failure_cleans_unique_staging(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "paint.vtf"
            source.write_bytes(b"vtf")
            tool = root / "VTFCmd.exe"
            tool.write_bytes(b"tool")
            copied_to = []

            def fail_copy(_source, destination):
                copied_to.append(Path(destination))
                raise OSError("copy failed")

            with mock.patch.object(render_previews.shutil, "copy2", side_effect=fail_copy):
                with self.assertRaisesRegex(OSError, "copy failed"):
                    render_previews._convert_vtf(source, tool, root / "cache")

            self.assertEqual(len(copied_to), 1)
            self.assertFalse(copied_to[0].parent.exists())

    def test_vtfcmd_cache_rejects_reparse_output(self):
        import render_previews

        fake_stat = type("Stat", (), {"st_file_attributes": 0x400})()
        with mock.patch.object(Path, "lstat", return_value=fake_stat):
            self.assertTrue(render_previews._path_is_link_or_reparse(Path("cache")))

    def test_vtfcmd_publish_accepts_valid_file_from_concurrent_publisher(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            staged = root / "staged.png"
            output = root / "cache/texture.png"
            staged.write_bytes(b"staged")
            output.parent.mkdir()

            def concurrent_publish(_source, destination):
                Path(destination).write_bytes(b"winner")
                raise PermissionError(5, "Access is denied")

            with mock.patch.object(render_previews.os, "replace", side_effect=concurrent_publish):
                selected = render_previews._publish_cache_file(staged, output)

            self.assertEqual(selected, output)
            self.assertEqual(output.read_bytes(), b"winner")

    def test_vtfcmd_cache_directory_rejects_symlink_when_available(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target"
            target.mkdir()
            linked = root / "linked"
            try:
                os.symlink(target, linked, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink privilege unavailable: {exc}")
            with self.assertRaisesRegex(RuntimeError, "link/reparse"):
                render_previews._safe_contained_directory(linked)

    def test_vtfcmd_cache_directory_rejects_reparse_ancestor(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            unsafe_parent = root / "redirected"
            leaf = unsafe_parent / "cache" / "digest"
            real_check = render_previews._path_is_link_or_reparse

            def ancestor_is_reparse(path):
                return Path(path) == unsafe_parent or real_check(path)

            with mock.patch.object(
                render_previews,
                "_path_is_link_or_reparse",
                side_effect=ancestor_is_reparse,
            ):
                with self.assertRaisesRegex(RuntimeError, "ancestor.*link/reparse"):
                    render_previews._safe_contained_directory(leaf)

    def test_textured_material_application_uses_each_objects_source_search_paths(self):
        import render_previews

        class Object:
            name = "Wheel"

            def __init__(self):
                self.data = type("Data", (), {"materials": []})()

            def get(self, key, default=None):
                if key == "maximum_region_source_identity":
                    return "models/wheel/wh.smd"
                return default

        obj = Object()
        with mock.patch.object(
            render_previews, "_source_texture_png", return_value=None
        ) as texture, mock.patch.object(
            render_previews, "_make_missing_texture_material", return_value="missing"
        ):
            missing = render_previews._apply_textured_materials(
                (obj,),
                {("Wheel", 0): "rim2"},
                Path("materials"),
                Path("VTFCmd.exe"),
                Path("cache"),
                source_search_paths={
                    "models/wheel/wh.smd": ("models/diggercars/pontiac_transam3",)
                },
            )

        self.assertEqual(missing["missing"], ("models/wheel/wh.smd:slot:0:rim2",))
        self.assertEqual(missing["resolved"], ())
        texture.assert_called_once_with(
            "rim2",
            Path("materials"),
            Path("VTFCmd.exe"),
            Path("cache"),
            search_paths=("models/diggercars/pontiac_transam3",),
        )
        self.assertEqual(obj.data.materials, ["missing"])

    def test_material_path_rejects_symlink_when_platform_allows_it(self):
        import render_previews

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "materials"
            root.mkdir()
            outside = Path(raw) / "outside.vmt"
            outside.write_text("VertexLitGeneric {}", encoding="utf-8")
            link = root / "linked.vmt"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            self.assertIsNone(
                render_previews._contained_material_path(root, "linked", ".vmt")
            )

    def test_blender_42_and_newer_select_eevee_next(self):
        import render_previews

        self.assertEqual(render_previews._eevee_engine((5, 0, 0)), "BLENDER_EEVEE_NEXT")
        self.assertEqual(render_previews._eevee_engine((4, 2, 0)), "BLENDER_EEVEE_NEXT")
        self.assertEqual(render_previews._eevee_engine((4, 1, 9)), "BLENDER_EEVEE")
        self.assertEqual(
            render_previews._eevee_engine(
                (5, 0, 1),
                available=("BLENDER_EEVEE", "BLENDER_WORKBENCH", "CYCLES"),
            ),
            "BLENDER_EEVEE",
        )

        class Item:
            def __init__(self, identifier):
                self.identifier = identifier

        class Property:
            enum_items = (Item("BLENDER_EEVEE"), Item("CYCLES"))

        class Rna:
            properties = {"engine": Property()}

        class Render:
            bl_rna = Rna()

        self.assertEqual(
            render_previews._available_enum_identifiers(Render(), "engine"),
            ("BLENDER_EEVEE", "CYCLES"),
        )


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.metrics_root = self.root / "metrics"
        self.metrics_root.mkdir()
        self.previous_env = os.environ.get("MAXIMUM_TEST_CALIBRATION_ROOT")
        os.environ["MAXIMUM_TEST_CALIBRATION_ROOT"] = str(self.root)

    def tearDown(self):
        if self.previous_env is None:
            os.environ.pop("MAXIMUM_TEST_CALIBRATION_ROOT", None)
        else:
            os.environ["MAXIMUM_TEST_CALIBRATION_ROOT"] = self.previous_env
        self.temp.cleanup()

    def _metric_payload(self, value: float) -> dict:
        return {"metrics": {metric: value for metric in METRICS}}

    def _write_metric(self, relative: str, value: float) -> str:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._metric_payload(value), sort_keys=True), encoding="utf-8")
        return "${MAXIMUM_TEST_CALIBRATION_ROOT}/" + relative.replace("\\", "/")

    def _corpus(self, count=20, *, reverse=False) -> dict:
        families = []
        for index in range(count):
            family_id = f"family-{index:02d}"
            families.append(
                {
                    "partition": "calibration",
                    "id": family_id,
                    "addon_root": "${MAXIMUM_TEST_CALIBRATION_ROOT}",
                    "model_rel": f"models/real/model_{index:02d}.mdl",
                    "metrics": {
                        "roundtrip": self._write_metric(f"metrics/{family_id}-roundtrip.json", 0.1),
                        "known_good": self._write_metric(f"metrics/{family_id}-good.json", 0.09),
                        "known_bad": self._write_metric(f"metrics/{family_id}-bad.json", 0.2),
                    },
                }
            )
        if reverse:
            families.reverse()
        return {
            "schema": 1,
            "hard_floors": {metric: 0.01 for metric in METRICS},
            "families": families,
        }

    def _write_corpus(self, payload: dict, name="corpus.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def test_requires_twenty_unique_families(self):
        path = self._write_corpus(self._corpus(19))
        with self.assertRaisesRegex(ValueError, "20 unique"):
            calibrate_profile(path, "calibration", self.root / "profile.json")

        payload = self._corpus(20)
        payload["families"][-1]["id"] = payload["families"][0]["id"]
        path = self._write_corpus(payload)
        with self.assertRaisesRegex(ValueError, "20 unique"):
            calibrate_profile(path, "calibration", self.root / "profile.json")

    def test_calibration_is_deterministic_and_uses_linear_p99_plus_mad(self):
        first_corpus = self._write_corpus(self._corpus(), "first.json")
        second_corpus = self._write_corpus(self._corpus(reverse=True), "second.json")
        first_out = self.root / "first-profile.json"
        second_out = self.root / "second-profile.json"

        first = calibrate_profile(first_corpus, "calibration", first_out)
        second = calibrate_profile(second_corpus, "calibration", second_out)

        self.assertEqual(first_out.read_bytes(), second_out.read_bytes())
        self.assertEqual(first["corpus_hash"], second["corpus_hash"])
        self.assertTrue(first["calibrated"])
        self.assertEqual(first["family_count"], 20)
        self.assertEqual(first["limits"], {metric: 0.1 for metric in METRICS})
        self.assertNotIn(None, first.values())
        loaded = load_profile(first_out)
        self.assertEqual(dict(loaded.limits), first["limits"])

    def test_missing_environment_and_missing_or_nonfinite_metric_are_rejected(self):
        corpus = self._corpus()
        corpus["families"][0]["addon_root"] = "${MISSING_ADDON_ENV}"
        with self.assertRaisesRegex(ValueError, "MISSING_ADDON_ENV"):
            calibrate_profile(
                self._write_corpus(corpus), "calibration", self.root / "profile.json"
            )

        corpus = self._corpus()
        corpus["families"][0]["metrics"]["roundtrip"] = "${MISSING_CALIBRATION_ENV}/x.json"
        with self.assertRaisesRegex(ValueError, "MISSING_CALIBRATION_ENV"):
            calibrate_profile(
                self._write_corpus(corpus), "calibration", self.root / "profile.json"
            )

        for invalid in (None, float("nan"), float("inf")):
            with self.subTest(invalid=invalid):
                corpus = self._corpus()
                metric_path = self.root / "metrics/family-00-roundtrip.json"
                payload = self._metric_payload(0.1)
                if invalid is None:
                    del payload["metrics"]["rgb_mae"]
                else:
                    payload["metrics"]["rgb_mae"] = invalid
                metric_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "rgb_mae"):
                    calibrate_profile(
                        self._write_corpus(corpus),
                        "calibration",
                        self.root / "profile.json",
                    )

    def test_known_good_and_every_known_bad_are_enforced(self):
        corpus = self._corpus()
        bad_good = self.root / "metrics/family-00-good.json"
        bad_good.write_text(json.dumps(self._metric_payload(0.11)), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "known_good"):
            calibrate_profile(
                self._write_corpus(corpus), "calibration", self.root / "profile.json"
            )

        corpus = self._corpus()
        accepted_bad = self.root / "metrics/family-00-bad.json"
        accepted_bad.write_text(json.dumps(self._metric_payload(0.1)), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "known_bad"):
            calibrate_profile(
                self._write_corpus(corpus), "calibration", self.root / "profile.json"
            )

    def test_failed_calibration_preserves_existing_output_atomically(self):
        output = self.root / "profile.json"
        output.write_bytes(b"existing-profile")
        corpus = self._corpus(19)

        with self.assertRaises(ValueError):
            calibrate_profile(self._write_corpus(corpus), "calibration", output)

        self.assertEqual(output.read_bytes(), b"existing-profile")
        self.assertEqual(list(self.root.glob(".profile.json.*.tmp")), [])

    def test_schema_and_huge_integers_fail_stably_in_function_and_cli(self):
        corpus = self._corpus()
        corpus["schema"] = True
        path = self._write_corpus(corpus)
        with self.assertRaisesRegex(ValueError, "schema"):
            calibrate_profile(path, "calibration", self.root / "profile.json")

        corpus = self._corpus()
        metric_path = self.root / "metrics/family-00-roundtrip.json"
        payload = self._metric_payload(0.1)
        payload["metrics"]["rgb_mae"] = 10**1000
        metric_path.write_text(json.dumps(payload), encoding="utf-8")
        path = self._write_corpus(corpus)
        with self.assertRaisesRegex(ValueError, "rgb_mae"):
            calibrate_profile(path, "calibration", self.root / "profile.json")
        from calibrate_maximum_profiles import main

        self.assertEqual(
            main(
                [
                    "--corpus",
                    str(path),
                    "--partition",
                    "calibration",
                    "--out",
                    str(self.root / "profile.json"),
                ]
            ),
            2,
        )

    def test_atomic_output_uses_exclusive_random_temp_and_cleans_replace_error(self):
        from calibrate_maximum_profiles import calibrate_profile

        corpus_path = self._write_corpus(self._corpus())
        output = self.root / "profile.json"
        predictable = self.root / f".profile.json.{os.getpid()}.tmp"
        predictable.write_bytes(b"collision-sentinel")

        calibrate_profile(corpus_path, "calibration", output)

        self.assertEqual(predictable.read_bytes(), b"collision-sentinel")
        output.write_bytes(b"old")
        with mock.patch("calibrate_maximum_profiles.os.replace", side_effect=OSError("replace")):
            with self.assertRaisesRegex(OSError, "replace"):
                calibrate_profile(corpus_path, "calibration", output)
        self.assertEqual(output.read_bytes(), b"old")
        self.assertEqual(
            [path for path in self.root.glob(".profile.json.*.tmp") if path != predictable],
            [],
        )


if __name__ == "__main__":
    unittest.main()
