from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from maximum_optimizer.candidates import CandidateBuild, CandidateBuildError
from maximum_optimizer.domain import (
    CandidateSpec,
    FamilyManifest,
    SearchBudget,
    StructuralFingerprint,
    ValidationResult,
)
from maximum_optimizer.orchestrator import (
    MaximumRunConfig,
    run_maximum_addon,
)
from maximum_optimizer.reporting import canonical_json, event_line


def _profile(path: Path) -> Path:
    payload = {
        "schema": 1,
        "version": "test-calibrated-v1",
        "calibrated": True,
        "corpus_hash": "a" * 64,
        "limits": {
            "silhouette_iou": 1.0,
            "rgb_mae": 1.0,
            "edge_error": 1.0,
            "surface_bidirectional_p95": 1.0,
            "surface_max": 1.0,
            "normal_angle_p95": 180.0,
            "uv_error_p95": 1.0,
            "skinning_error_p95": 1.0,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _family(root: Path, name: str = "test") -> FamilyManifest:
    source = root / "source" / name
    source.mkdir(parents=True)
    (source / f"{name}.qc").write_text("// source", encoding="utf-8")
    fp = StructuralFingerprint(
        f"models/{name}.mdl", (), (), (), (), (), (), (), (), (f"{name}.smd",), (), None
    )
    return FamilyManifest(
        hashlib.sha256(name.encode()).hexdigest(),
        f"{name}.mdl",
        source,
        root / "addon" / "models",
        fp,
        hashlib.sha256((name + "-input").encode()).hexdigest(),
        (".mdl",),
    )


class FakeAdapters:
    def __init__(self, root: Path, families: tuple[FamilyManifest, ...], sizes: dict[str, int]):
        self.root = root
        self._families = families
        self.sizes = sizes
        self.calls: list[tuple[str, str]] = []
        self.fail: set[tuple[str, str]] = set()
        self.cancel_on: str | None = None
        self.cancel_during_visual = False
        self.cancel_event = None
        self.extra_collision = False

    def inventory(self, config):
        return self._families

    def candidate_schedule(self, manifest):
        return tuple(
            CandidateSpec(candidate_id, "blender", ratio, 0.01, "transfer-v1")
            for candidate_id, ratio in (("candidate-100", 1.0), ("candidate-60", .6), ("candidate-40", .4))
        )

    def build(self, manifest, spec, workspace, tools, cancel_event):
        self.cancel_event = cancel_event
        self.calls.append((manifest.model_rel, spec.candidate_id))
        if self.cancel_on == spec.candidate_id:
            cancel_event.set()
        if (manifest.model_rel, spec.candidate_id) in self.fail:
            raise CandidateBuildError("synthetic compile failure", stage="compile")
        workspace.mkdir(parents=True, exist_ok=True)
        compiled = workspace / "compiled" / "models"
        compiled.mkdir(parents=True)
        mdl = compiled / manifest.model_rel
        mdl.parent.mkdir(parents=True, exist_ok=True)
        mdl.write_bytes(b"x" * self.sizes[spec.candidate_id])
        qc = workspace / "optimized.qc"
        qc.write_text("// optimized", encoding="utf-8")
        logical = mdl.relative_to(compiled).as_posix()
        provenance = {logical: "candidate-compile"}
        if self.extra_collision:
            collision = compiled / "other" / "test.mdl"
            collision.parent.mkdir(parents=True)
            collision.write_bytes(b"collision")
            provenance["other/test.mdl"] = "candidate-compile"
        return CandidateBuild(
            spec, workspace, qc, compiled,
            {"status": "ok", "returncode": 0, "model_rel": manifest.model_rel, "expected_mdl": str(mdl.resolve())},
            provenance, (),
        )

    def visual(self, manifest, control, candidate, profile):
        if self.cancel_during_visual:
            self.cancel_event.set()
        passed = candidate.spec.candidate_id != "candidate-40"
        return ValidationResult(passed, metrics={"fidelity_score": .99 if passed else .1}, worst_scope="r-" + "1" * 64 + "/bind")

    def tool_versions(self):
        return {"blender": "fake-1", "studiomdl": "fake-1"}


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addon = self.root / "addon"
        (self.addon / "models").mkdir(parents=True)
        (self.addon / "models" / "test.mdl").write_bytes(b"o" * 120)
        (self.addon / "materials").mkdir()
        (self.addon / "materials" / "keep.vmt").write_text("keep", encoding="utf-8")
        self.family = _family(self.root)
        self.events: list[dict] = []
        self.config = MaximumRunConfig(
            addon_dir=self.addon,
            output_dir=self.root / "output",
            work_dir=self.root / "work",
            blender_path=self.root / "blender.exe",
            studiomdl_path=self.root / "studiomdl.exe",
            repo_root=self.root,
            budget=SearchBudget(4, .025, 0),
            profile_path=_profile(self.root / "profile.json"),
            resume=False,
            overwrite=False,
        )
        self.adapters = FakeAdapters(self.root, (self.family,), {
            "roundtrip-control": 110,
            "candidate-100": 100,
            "candidate-60": 60,
            "candidate-40": 40,
        })

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def structural(manifest, build):
        return ValidationResult(True, metrics={"failure_count": 0.0})

    def run_optimizer(self, **kwargs):
        return run_maximum_addon(
            self.config,
            adapters=self.adapters,
            validator=self.structural,
            event_sink=self.events.append,
            **kwargs,
        )

    def test_promotes_smallest_passing_compiled_candidate_and_preserves_unrelated_files(self):
        report = self.run_optimizer()
        family = report.families[0]
        self.assertEqual(family.selected_candidate, "candidate-60")
        self.assertEqual(family.status, "optimized")
        self.assertEqual(report.final_size.total_bytes, 60)
        self.assertEqual((self.config.output_dir / "models" / "test.mdl").stat().st_size, 60)
        self.assertEqual((self.config.output_dir / "materials" / "keep.vmt").read_text(), "keep")
        self.assertEqual(family.original_size.total_bytes, 120)
        self.assertEqual(family.control_size.total_bytes, 110)
        self.assertEqual(family.selected_size.total_bytes, 60)
        self.assertEqual(family.savings["roundtrip_delta_bytes"], -10)
        self.assertEqual(family.savings["geometric_delta_bytes"], 50)
        self.assertEqual(family.savings["total_saving_bytes"], 60)
        payload = json.loads(report.report_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["families"][0]["selected_candidate"], "candidate-60")
        self.assertEqual(payload["final_size"]["total_bytes"], 60)
        with self.assertRaises(TypeError):
            report.tool_versions["blender"] = "mutated"
        with self.assertRaises(TypeError):
            family.savings["total_saving_bytes"] = 0

    def test_event_encoding_is_exact_one_line_canonical_json_and_rejects_nan(self):
        self.assertEqual(
            event_line({"schema": 1, "kind": "stage", "z": 2, "a": "ç"}),
            'MAXIMUM_EVENT {"a":"ç","kind":"stage","schema":1,"z":2}',
        )
        self.assertNotIn("\n", event_line({"schema": 1, "kind": "stage"}))
        with self.assertRaisesRegex(ValueError, "non-finite"):
            canonical_json({"metric": float("nan")})

    def test_no_passing_variant_preserves_exact_original_explicitly(self):
        self.adapters.visual = lambda *args: ValidationResult(False, worst_scope="all")
        report = self.run_optimizer()
        family = report.families[0]
        self.assertEqual(family.status, "preserved")
        self.assertIsNone(family.selected_candidate)
        self.assertIn("no candidate passed", family.reason)
        self.assertEqual((self.config.output_dir / "models" / "test.mdl").read_bytes(), b"o" * 120)

    def test_compile_failure_is_isolated_between_families(self):
        second = _family(self.root, "other")
        (self.addon / "models" / "other.mdl").write_bytes(b"z" * 90)
        self.adapters._families = (self.family, second)
        self.adapters.fail.add(("test.mdl", "roundtrip-control"))
        report = self.run_optimizer()
        self.assertEqual([item.status for item in report.families], ["failed", "optimized"])
        self.assertEqual((self.config.output_dir / "models" / "test.mdl").read_bytes(), b"o" * 120)

    def test_multi_family_batch_can_optimize_one_and_explicitly_preserve_another(self):
        second = _family(self.root, "other")
        (self.addon / "models" / "other.mdl").write_bytes(b"z" * 90)
        self.adapters._families = (self.family, second)
        def visual(manifest, control, candidate, profile):
            passed = manifest.model_rel == "test.mdl" and candidate.spec.candidate_id != "candidate-40"
            return ValidationResult(passed, metrics={"fidelity_score": 0.9 if passed else 0.0})
        self.adapters.visual = visual
        report = self.run_optimizer()
        self.assertEqual([item.status for item in report.families], ["optimized", "preserved"])
        self.assertEqual((self.config.output_dir / "models" / "other.mdl").read_bytes(), b"z" * 90)

    def test_cancelled_run_has_exact_terminal_event_and_never_promotes(self):
        self.adapters.cancel_on = "candidate-60"
        cancel = threading.Event()
        report = self.run_optimizer(cancel_event=cancel)
        self.assertTrue(report.cancelled)
        self.assertFalse(self.config.output_dir.exists())
        terminals = [event["kind"] for event in self.events if event["kind"] in {"run_finished", "run_cancelled"}]
        self.assertEqual(terminals, ["run_cancelled"])

    def test_event_order_and_partial_report_are_explicit(self):
        report = self.run_optimizer()
        kinds = [event["kind"] for event in self.events]
        self.assertEqual(kinds[0], "run_started")
        self.assertEqual(kinds[-1], "run_finished")
        self.assertIn("family_started", kinds)
        self.assertIn("candidate_started", kinds)
        self.assertIn("stage", kinds)
        self.assertIn("candidate_finished", kinds)
        self.assertIn("best_updated", kinds)
        self.assertIn("family_finished", kinds)
        self.assertTrue(all(event["schema"] == 1 for event in self.events))
        self.assertTrue(report.report_path.is_file())
        self.assertFalse(tuple(report.report_path.parent.glob(".maximum_report.json.tmp-*")))

    def test_resume_reuses_only_complete_keyed_candidate_results(self):
        first = self.run_optimizer()
        first_call_count = len(self.adapters.calls)
        self.config = MaximumRunConfig(
            **{
                **self.config.to_kwargs(),
                "output_dir": self.root / "output-resumed",
                "resume": True,
            }
        )
        self.events.clear()
        resumed = self.run_optimizer()
        second_calls = self.adapters.calls[first_call_count:]
        self.assertEqual(second_calls[0], ("test.mdl", "roundtrip-control"))
        self.assertFalse(
            {candidate for _family_name, candidate in second_calls}
            & {"candidate-100", "candidate-60", "candidate-40"}
        )
        candidate_attempts = [
            attempt for attempt in resumed.families[0].attempts
            if attempt.candidate_id != "roundtrip-control"
        ]
        self.assertTrue(candidate_attempts)
        keyed_attempts = [
            attempt for attempt in candidate_attempts
            if attempt.candidate_id in {"candidate-100", "candidate-60", "candidate-40"}
        ]
        self.assertTrue(all(attempt.cache_hit for attempt in keyed_attempts))
        self.assertEqual(resumed.final_size.total_bytes, first.final_size.total_bytes)

    def test_cancellation_during_visual_validation_is_terminal_and_unpromoted(self):
        self.adapters.cancel_during_visual = True
        report = self.run_optimizer(cancel_event=threading.Event())
        self.assertTrue(report.cancelled)
        self.assertFalse(self.config.output_dir.exists())
        self.assertEqual([event["kind"] for event in self.events][-1], "run_cancelled")

    def test_structural_failure_cannot_be_rescued_by_visual_pass(self):
        self.adapters.visual = lambda *args: ValidationResult(True, metrics={"fidelity_score": 1.0})
        def structural(_manifest, build):
            return ValidationResult(build.spec.candidate_id != "candidate-40")
        report = run_maximum_addon(
            self.config,
            adapters=self.adapters,
            validator=structural,
            event_sink=self.events.append,
        )
        self.assertEqual(report.families[0].selected_candidate, "candidate-60")

    def test_candidate_provenance_cannot_overwrite_unrelated_model(self):
        (self.addon / "models" / "other").mkdir()
        (self.addon / "models" / "other" / "test.mdl").write_bytes(b"original-unrelated")
        self.adapters.extra_collision = True
        report = self.run_optimizer()
        self.assertEqual(report.status, "success")
        self.assertEqual(
            (self.config.output_dir / "models" / "other" / "test.mdl").read_bytes(),
            b"original-unrelated",
        )

    def test_profile_hash_change_invalidates_resume_even_with_same_version(self):
        self.run_optimizer()
        payload = json.loads(self.config.profile_path.read_text(encoding="utf-8"))
        payload["limits"]["rgb_mae"] = 0.5
        self.config.profile_path.write_text(json.dumps(payload), encoding="utf-8")
        first_count = len(self.adapters.calls)
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "output_dir": self.root / "profile-miss", "resume": True}
        )
        self.events.clear()
        self.run_optimizer()
        rebuilt = {candidate for _family_name, candidate in self.adapters.calls[first_count:]}
        self.assertTrue({"candidate-100", "candidate-60", "candidate-40"} <= rebuilt)

    def test_tool_hash_change_invalidates_resume(self):
        self.run_optimizer()
        first_count = len(self.adapters.calls)
        self.adapters.tool_versions = lambda: {"blender": "fake-2", "studiomdl": "fake-1"}
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "output_dir": self.root / "tool-miss", "resume": True}
        )
        self.events.clear()
        self.run_optimizer()
        rebuilt = {candidate for _family_name, candidate in self.adapters.calls[first_count:]}
        self.assertTrue({"candidate-100", "candidate-60", "candidate-40"} <= rebuilt)

    def test_corrupt_cache_entry_is_a_miss_and_never_promoted_directly(self):
        self.run_optimizer()
        complete_markers = tuple((self.config.work_dir / "cache").glob("*/complete.json"))
        self.assertTrue(complete_markers)
        complete_markers[0].write_text("not-json", encoding="utf-8")
        first_count = len(self.adapters.calls)
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "output_dir": self.root / "cache-miss", "resume": True}
        )
        self.events.clear()
        report = self.run_optimizer()
        self.assertEqual(report.status, "success")
        self.assertGreater(len(self.adapters.calls), first_count + 1)

    def test_promotion_failure_preserves_existing_output_and_reports_failure(self):
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "overwrite": True}
        )
        self.config.output_dir.mkdir()
        (self.config.output_dir / "sentinel.txt").write_text("old", encoding="utf-8")
        with patch(
            "maximum_optimizer.orchestrator.atomic_replace_tree",
            side_effect=OSError("synthetic promotion failure"),
        ):
            report = self.run_optimizer()
        self.assertEqual(report.status, "failed")
        self.assertEqual((self.config.output_dir / "sentinel.txt").read_text(), "old")
        self.assertEqual(self.events[-1]["kind"], "run_finished")
        self.assertEqual(self.events[-1]["status"], "failed")

    def test_default_schedule_prefers_blender_when_meshopt_is_not_preferred(self):
        self.adapters.candidate_schedule = None
        report = self.run_optimizer()
        engines = [attempt.engine for attempt in report.families[0].attempts if attempt.candidate_id != "roundtrip-control"]
        self.assertGreater(len(engines), 1)
        self.assertEqual(engines[0], "fidelity")
        self.assertEqual(engines[1], "blender")

    def test_uncalibrated_production_profile_fails_closed_before_promotion(self):
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "profile_path": Path(__file__).parents[2] / "maximum_optimizer" / "profiles" / "maximum-experimental-v1.json"}
        )
        with self.assertRaisesRegex(ValueError, "calibrated"):
            self.run_optimizer()
        self.assertFalse(self.config.output_dir.exists())


if __name__ == "__main__":
    unittest.main()
