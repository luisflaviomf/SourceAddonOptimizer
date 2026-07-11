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

from PIL import Image

from maximum_optimizer.visual_validation import (
    FidelityProfile,
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
) -> None:
    payload = {
        "schema": 1,
        "entries": entries,
        "geometry": geometry or [],
        "bbox": {"min": [0, 0, 0], "max": [1, 1, 1], "diagonal": math.sqrt(3)},
        "sampling": {"stride": 1, "seed": 0},
    }
    (root / "render_manifest.json").write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )


def _entry(root: Path, render_pass: str, pose: str, angle: str, **image_kwargs) -> dict:
    relative = f"{render_pass}/{pose}/{angle}.png"
    path = root / relative
    _write_image(path, **image_kwargs)
    return {
        "pass": render_pass,
        "pose": pose,
        "angle": angle,
        "image": relative,
        "sha256": _sha256(path),
        "texture_missing": False,
    }


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

    def write_matching(self, keys=(("clay", "bind", "front"),)):
        reference_entries = [_entry(self.reference, *key) for key in keys]
        candidate_entries = [_entry(self.candidate, *key) for key in keys]
        _write_manifest(self.reference, reference_entries)
        _write_manifest(self.candidate, candidate_entries)

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

    def test_worst_angle_fails_even_when_average_is_small(self):
        keys = (("clay", "bind", "front"), ("clay", "bind", "right"))
        reference_entries = [_entry(self.reference, *key) for key in keys]
        candidate_entries = [
            _entry(self.candidate, *keys[0]),
            _entry(self.candidate, *keys[1], box=(0, 0, 2, 2)),
        ]
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
        ref_entry = _entry(self.reference, *key, box=None)
        candidate_entry = _entry(self.candidate, *key, box=None)
        _write_manifest(self.reference, [ref_entry])
        _write_manifest(self.candidate, [candidate_entry])
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertEqual(result.metrics["silhouette_iou"], 0.0)
        self.assertEqual(result.metrics["edge_error"], 0.0)

        candidate_entry = _entry(self.candidate, *key, box=(2, 2, 6, 6))
        _write_manifest(self.candidate, [candidate_entry])
        result = compare_render_sets(self.reference, self.candidate, _profile())
        self.assertEqual(result.metrics["silhouette_iou"], 1.0)
        self.assertEqual(result.metrics["edge_error"], 1.0)

        ref_entry = _entry(self.reference, *key, color=(128, 128, 128, 255))
        candidate_entry = _entry(self.candidate, *key, color=(255, 255, 255, 255))
        _write_manifest(self.reference, [ref_entry])
        _write_manifest(self.candidate, [candidate_entry])
        result = compare_render_sets(self.reference, self.candidate, _profile())
        expected = 1.0 - ((128 / 255 + 0.055) / 1.055) ** 2.4
        self.assertAlmostEqual(result.metrics["rgb_mae"], expected, places=6)

    def test_pose_scope_and_geometry_use_worst_region(self):
        keys = (("clay", "bind", "front"), ("clay", "run", "front"))
        reference_entries = [_entry(self.reference, *key) for key in keys]
        candidate_entries = [_entry(self.candidate, *key) for key in keys]
        geometry = [
            {
                "scope": "body",
                "pose": "bind",
                "surface_bidirectional_p95": 0.01,
                "surface_max": 0.02,
                "normal_angle_p95": 0.03,
                "uv_error_p95": 0.04,
                "skinning_error_p95": 0.05,
            },
            {
                "scope": "wheel",
                "pose": "run",
                "surface_bidirectional_p95": 0.9,
                "surface_max": 0.8,
                "normal_angle_p95": 0.7,
                "uv_error_p95": 0.6,
                "skinning_error_p95": 0.5,
            },
        ]
        _write_manifest(self.reference, reference_entries)
        _write_manifest(self.candidate, candidate_entries, geometry=geometry)

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
        self.write_matching((("textured", "bind", "front"),))
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
        manifest["entries"][0]["sha256"] = _sha256(image_path)
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
        _write_manifest(self.candidate, [candidate_entry])
        failed = compare_render_sets(
            self.reference,
            self.candidate,
            _profile(**{metric: 0.0 for metric in METRICS}),
        )
        self.assertFalse(failed.passed)
        self.assertEqual(failed.metrics["fidelity_score"], 0.0)


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
        self.assertFalse(render_previews._is_extended_mode(args))

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
            ]
        )

        self.assertTrue(render_previews._is_extended_mode(args))
        self.assertEqual(render_previews._parse_csv(args.passes), ("textured", "clay"))
        self.assertEqual(render_previews._parse_poses(args.poses), (("bind", 0), ("run", 12)))
        self.assertEqual(args.materials_root, "materials")
        self.assertEqual(args.vtfcmd, "VTFCmd.exe")

    def test_invalid_new_pass_or_pose_is_rejected(self):
        import render_previews

        with self.assertRaisesRegex(ValueError, "pass"):
            render_previews._validated_passes("textured,fake")
        for raw in ("run", "run:nope", "run:1,run:2", "bad/name:1"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "pose"):
                    render_previews._parse_poses(raw)

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
                stride=3,
                seed=17,
            )
            payload = json.loads((root / "render_manifest.json").read_text())
            self.assertEqual(payload["schema"], 1)
            self.assertEqual(payload["entries"], [entry])
            self.assertEqual(payload["sampling"], {"stride": 3, "seed": 17})
            self.assertEqual(entry["sha256"], _sha256(image_path))
            self.assertEqual(entry["image"], "clay/bind/front.png")


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


if __name__ == "__main__":
    unittest.main()
