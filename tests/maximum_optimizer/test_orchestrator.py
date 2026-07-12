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
    MaximumConfigError,
    MaximumRunConfig,
    ProductionAdapters,
    _seal_cache_entry,
    _copy_selected_family,
    _promote_verified_tree,
    _tree_manifest,
    validate_run_paths,
    run_maximum_addon,
)
from maximum_optimizer.reporting import canonical_json, event_line
from maximum_optimizer.processes import ProcessResult
from maximum_optimizer.visual_validation import load_profile


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

    def test_inventory_exception_writes_failed_terminal_report(self):
        self.adapters.inventory = lambda _config: (_ for _ in ()).throw(RuntimeError("inventory boom"))
        report = self.run_optimizer()
        self.assertEqual(report.status, "failed")
        self.assertEqual([event["kind"] for event in self.events], ["run_started", "run_finished"])
        self.assertEqual(self.events[0]["family_count"], 0)
        self.assertEqual(json.loads(report.report_path.read_text(encoding="utf-8"))["status"], "failed")
        self.assertFalse(self.config.output_dir.exists())

    def test_injected_event_sink_failure_does_not_prevent_terminal_report(self):
        report = run_maximum_addon(
            self.config,
            adapters=self.adapters,
            validator=self.structural,
            event_sink=lambda _event: (_ for _ in ()).throw(RuntimeError("sink boom")),
        )
        payload = json.loads(report.report_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["events"][0]["kind"], "run_started")
        self.assertEqual(payload["events"][-1]["kind"], "run_finished")

    def test_invalid_schedule_is_explicit_family_failure_with_terminal_event(self):
        self.adapters.candidate_schedule = lambda _manifest: ()
        report = self.run_optimizer()
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.families[0].status, "failed")
        self.assertEqual(self.events[-1]["kind"], "run_finished")

    def test_no_passing_variant_preserves_exact_original_explicitly(self):
        self.adapters.visual = lambda _manifest, _control, candidate, _profile: ValidationResult(
            candidate.spec.candidate_id == "roundtrip-control",
            worst_scope="all",
        )
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

    def test_original_model_missing_from_decompile_inventory_is_explicitly_preserved(self):
        (self.addon / "models" / "not_decompiled.mdl").write_bytes(b"n" * 77)
        self.adapters.inventory_diagnostics = lambda _config: {
            "not_decompiled.mdl": "decompile failed: synthetic"
        }
        report = self.run_optimizer()
        missing = next(item for item in report.families if item.model_rel == "not_decompiled.mdl")
        self.assertEqual(missing.status, "preserved")
        self.assertIn("decompile failed", missing.reason)
        self.assertEqual(missing.original_size.total_bytes, 77)
        self.assertEqual(missing.selected_size.total_bytes, 77)
        self.assertEqual(
            (self.config.output_dir / "models" / "not_decompiled.mdl").read_bytes(),
            b"n" * 77,
        )

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
        self.assertEqual(self.events[0]["family_count"], 1)
        self.assertEqual(self.events[0]["report_path"], "logs/maximum_report.json")
        family_started = next(event for event in self.events if event["kind"] == "family_started")
        self.assertEqual(family_started["family_id"], self.family.family_id)
        candidate_started = next(event for event in self.events if event["kind"] == "candidate_started")
        self.assertIn("candidate_id", candidate_started)
        self.assertIn("index", candidate_started)
        self.assertIn("total", candidate_started)
        final_payload = json.loads(report.report_path.read_text(encoding="utf-8"))
        self.assertEqual(final_payload["events"][-1]["kind"], "run_finished")

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
        self.assertEqual(report.status, "failed")
        self.assertFalse(self.config.output_dir.exists())
        self.assertEqual(
            (self.addon / "models" / "other" / "test.mdl").read_bytes(),
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

    def _cached_payload_for_size(self, size: int) -> Path:
        for payload in (self.config.work_dir / "cache").glob("*/payload"):
            model = payload / "compiled" / "models" / "test.mdl"
            if model.is_file() and model.stat().st_size == size:
                return payload
        self.fail(f"cache payload with compiled size {size} not found")

    def test_same_size_tampered_cached_mdl_is_rebuilt_before_use(self):
        self.run_optimizer()
        payload = self._cached_payload_for_size(60)
        (payload / "compiled" / "models" / "test.mdl").write_bytes(b"y" * 60)
        first_count = len(self.adapters.calls)
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "output_dir": self.root / "tamper-miss", "resume": True}
        )
        self.events.clear()
        report = self.run_optimizer()
        rebuilt = {candidate for _family_name, candidate in self.adapters.calls[first_count:]}
        self.assertIn("candidate-60", rebuilt)
        self.assertEqual((self.config.output_dir / "models" / "test.mdl").read_bytes(), b"x" * 60)
        self.assertEqual(report.status, "success")

    def test_cache_hit_reruns_structural_and_visual_authorization(self):
        self.run_optimizer()
        first_count = len(self.adapters.calls)
        counts = {"structural": 0, "visual": 0}
        def structural(_manifest, _build):
            counts["structural"] += 1
            return ValidationResult(True)
        def visual(_manifest, _control, candidate, _profile):
            counts["visual"] += 1
            return ValidationResult(
                candidate.spec.candidate_id == "roundtrip-control",
                worst_scope="fresh-material",
            )
        self.adapters.visual = visual
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "output_dir": self.root / "fresh-gates", "resume": True}
        )
        self.events.clear()
        report = run_maximum_addon(
            self.config,
            adapters=self.adapters,
            validator=structural,
            event_sink=self.events.append,
        )
        rebuilt_known = {
            candidate for _family_name, candidate in self.adapters.calls[first_count:]
            if candidate in {"candidate-100", "candidate-60", "candidate-40"}
        }
        self.assertFalse(rebuilt_known)
        self.assertGreaterEqual(counts["structural"], 4)  # control + three cached candidates
        self.assertEqual(counts["visual"], 4)
        self.assertEqual(report.families[0].status, "preserved")

    def test_policy_and_script_content_changes_invalidate_candidate_cache(self):
        policy = self.config.work_dir / "logs" / "selective_policy_map.json"
        policy.parent.mkdir(parents=True)
        policy.write_text("first", encoding="utf-8")
        script = self.config.repo_root / "batch_optimize_maximum.py"
        script.write_text("first", encoding="utf-8")
        self.run_optimizer()
        policy.write_text("second", encoding="utf-8")
        script.write_text("second", encoding="utf-8")
        first_count = len(self.adapters.calls)
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "output_dir": self.root / "dependency-miss", "resume": True}
        )
        self.events.clear()
        self.run_optimizer()
        rebuilt = {candidate for _family_name, candidate in self.adapters.calls[first_count:]}
        self.assertTrue({"candidate-100", "candidate-60", "candidate-40"} <= rebuilt)

    def test_cached_missing_provenance_cannot_authorize_optimization(self):
        self.run_optimizer()
        payload = self._cached_payload_for_size(60)
        record = payload / "maximum_cache_record.json"
        data = json.loads(record.read_text(encoding="utf-8"))
        data["provenance"] = {}
        record.write_text(json.dumps(data), encoding="utf-8")
        _seal_cache_entry(payload.parent)
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "output_dir": self.root / "missing-provenance", "resume": True}
        )
        self.events.clear()
        report = run_maximum_addon(
            self.config,
            adapters=self.adapters,
            validator=lambda _manifest, build: ValidationResult(bool(build.provenance)),
            event_sink=self.events.append,
        )
        self.assertNotEqual(report.families[0].selected_candidate, "candidate-60")

    def test_cached_string_false_is_rejected_not_bool_coerced(self):
        self.run_optimizer()
        payload = self._cached_payload_for_size(60)
        record = payload / "maximum_cache_record.json"
        data = json.loads(record.read_text(encoding="utf-8"))
        data["visual"]["passed"] = "false"
        record.write_text(json.dumps(data), encoding="utf-8")
        _seal_cache_entry(payload.parent)
        first_count = len(self.adapters.calls)
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "output_dir": self.root / "string-false", "resume": True}
        )
        self.events.clear()
        self.run_optimizer()
        rebuilt = {candidate for _family_name, candidate in self.adapters.calls[first_count:]}
        self.assertIn("candidate-60", rebuilt)

    def test_promotion_failure_preserves_existing_output_and_reports_failure(self):
        self.config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "overwrite": True}
        )
        self.config.output_dir.mkdir()
        (self.config.output_dir / "sentinel.txt").write_text("old", encoding="utf-8")
        with patch(
            "maximum_optimizer.orchestrator._promote_verified_tree",
            side_effect=OSError("synthetic promotion failure"),
        ):
            report = self.run_optimizer()
        self.assertEqual(report.status, "failed")
        self.assertEqual((self.config.output_dir / "sentinel.txt").read_text(), "old")
        self.assertEqual(self.events[-1]["kind"], "run_finished")
        self.assertEqual(self.events[-1]["status"], "failed")

    def test_control_visual_incompatibility_preserves_with_roundtrip_diagnostic(self):
        original_visual = self.adapters.visual
        self.adapters.visual = lambda manifest, control, candidate, profile: (
            ValidationResult(False, worst_scope="control-view")
            if candidate.spec.candidate_id == "roundtrip-control"
            else original_visual(manifest, control, candidate, profile)
        )
        report = self.run_optimizer()
        family = report.families[0]
        self.assertEqual(family.status, "preserved")
        self.assertIn("control", family.reason)
        self.assertIsNotNone(family.control_size)
        self.assertIn("roundtrip_delta_bytes", family.savings)

    def test_equal_or_larger_passing_candidates_are_not_called_optimized(self):
        self.adapters.sizes.update({"candidate-100": 130, "candidate-60": 125, "candidate-40": 124})
        self.adapters.visual = lambda *args: ValidationResult(True, metrics={"fidelity_score": 1.0})
        report = self.run_optimizer()
        self.assertEqual(report.families[0].status, "preserved")
        self.assertIsNone(report.families[0].selected_candidate)
        self.assertIn("positive compiled saving", report.families[0].reason)

    def test_copy_requires_nonempty_complete_family_provenance(self):
        workspace = self.root / "manual-build"
        compiled = workspace / "compiled"
        compiled.mkdir(parents=True)
        qc = workspace / "x.qc"
        qc.write_text("", encoding="utf-8")
        build = CandidateBuild(
            CandidateSpec("manual", "blender", 0.5, 0.0, "test"),
            workspace, qc, compiled, {}, {}, (),
        )
        output_models = self.root / "manual-output"
        output_models.mkdir()
        with self.assertRaisesRegex(ValueError, "provenance"):
            _copy_selected_family(build, output_models, "test.mdl")

    def test_family_copy_rejects_sibling_prefix_artifact(self):
        workspace = self.root / "prefix-build"
        compiled = workspace / "compiled"
        compiled.mkdir(parents=True)
        (compiled / "test.mdl").write_bytes(b"model")
        (compiled / "test.extra.mdl").write_bytes(b"wrong")
        qc = workspace / "x.qc"
        qc.write_text("", encoding="utf-8")
        build = CandidateBuild(
            CandidateSpec("prefix", "blender", 0.5, 0.0, "test"),
            workspace, qc, compiled, {},
            {"test.mdl": "candidate-compile", "test.extra.mdl": "candidate-compile"}, (),
        )
        output_models = self.root / "prefix-output"
        output_models.mkdir()
        with self.assertRaisesRegex(ValueError, "family artifact"):
            _copy_selected_family(build, output_models, "test.mdl")

    def test_verified_promotion_rolls_back_when_final_manifest_mutates(self):
        staging = self.root / "transaction-staging"
        destination = self.root / "transaction-output"
        staging.mkdir()
        destination.mkdir()
        (staging / "new.bin").write_bytes(b"new")
        (destination / "old.bin").write_bytes(b"old")
        expected = _tree_manifest(staging)
        calls = 0
        def reader(path):
            nonlocal calls
            calls += 1
            actual = _tree_manifest(path)
            if calls == 2:
                return {**actual, "mutated.bin": {"size": 1, "sha256": "0" * 64}}
            return actual
        with self.assertRaisesRegex(ValueError, "manifest"):
            _promote_verified_tree(staging, destination, expected, manifest_reader=reader)
        self.assertEqual((destination / "old.bin").read_bytes(), b"old")
        self.assertFalse((destination / "new.bin").exists())

    def test_production_visual_command_uses_nested_family_root_all_lods_and_poses(self):
        source = self.root / "production-source"
        source.mkdir()
        smd = "version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\ntriangles\nend\n"
        (source / "mesh.smd").write_text(smd, encoding="utf-8")
        (source / "lod.smd").write_text(smd, encoding="utf-8")
        (source / "main.qc").write_text(
            '$modelname "test.mdl"\n$body "body" "mesh.smd"\n$lod 10 { replacemodel "mesh.smd" "lod.smd" }\n',
            encoding="utf-8",
        )
        fp = StructuralFingerprint(
            "test.mdl", ("body",), (), (), ("root",), (), (), (), ("idle",),
            ("mesh.smd",), ("lod.smd",), None,
        )
        manifest = FamilyManifest(
            "production-family", "test.mdl", source, self.addon / "models", fp,
            "b" * 64, (".mdl",),
        )
        workspace = self.root / "production-candidate"
        nested = workspace / "src" / "nested"
        nested.mkdir(parents=True)
        (workspace / "logs").mkdir()
        (workspace / "src" / "mesh_OPT.smd").write_text(smd, encoding="utf-8")
        (workspace / "src" / "lod_OPT.smd").write_text(smd, encoding="utf-8")
        qc = nested / "main_OPT.qc"
        qc.write_text(
            '$modelname "test.mdl"\n$body "body" "../mesh_OPT.smd"\n$lod 10 { replacemodel "../mesh_OPT.smd" "../lod_OPT.smd" }\n',
            encoding="utf-8",
        )
        compiled = workspace / "compiled"
        compiled.mkdir()
        build = CandidateBuild(
            CandidateSpec("production", "blender", 0.5, 0.0, "test"),
            workspace, qc, compiled, {}, {"test.mdl": "candidate-compile"}, (),
        )
        commands = []
        def runner(command, **kwargs):
            commands.append(tuple(str(item) for item in command))
            if "--python-expr" in command:
                (workspace / "render-source" / "maximum_region_manifest.json").write_text("{}", encoding="utf-8")
            else:
                (workspace / "renders" / "original").mkdir(parents=True)
                (workspace / "renders" / "optimized").mkdir(parents=True)
            return ProcessResult(tuple(str(item) for item in command), 0, 0.01, kwargs["log_path"])
        adapter = ProductionAdapters(self.config, threading.Event())
        with (
            patch("maximum_optimizer.orchestrator.run_process", side_effect=runner),
            patch("maximum_optimizer.orchestrator.compare_render_sets", return_value=ValidationResult(True)),
        ):
            result = adapter.visual(manifest, build, build, load_profile(self.config.profile_path))
        self.assertTrue(result.passed)
        render = commands[1]
        before_values = [render[index + 1] for index, value in enumerate(render) if value == "--before"]
        after_values = [render[index + 1] for index, value in enumerate(render) if value == "--after"]
        self.assertEqual(len(before_values), 2)
        self.assertEqual(len(after_values), 2)
        self.assertTrue(any(value.endswith("lod.smd") for value in before_values))
        self.assertTrue(any(value.endswith("lod_OPT.smd") for value in after_values))
        self.assertEqual(render[render.index("--poses") + 1], "bind:0,representative:1")

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

    def test_lexical_work_overlap_is_rejected_before_any_mutation(self):
        sentinel = self.addon / "do-not-touch.txt"
        sentinel.write_text("original", encoding="utf-8")
        config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "work_dir": self.addon}
        )
        with self.assertRaisesRegex(MaximumConfigError, "overlap"):
            validate_run_paths(config, create=False)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")

    def test_lexical_ancestor_overlap_is_rejected_before_any_mutation(self):
        config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "work_dir": self.root}
        )
        with self.assertRaisesRegex(MaximumConfigError, "overlap"):
            validate_run_paths(config, create=False)
        self.assertFalse((self.root / "logs").exists())

    def test_output_reparse_component_is_rejected_even_when_resolve_points_outside(self):
        (self.root / "output-link").mkdir()
        raw_output = self.root / "output-link" / "child"
        config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "output_dir": raw_output, "overwrite": True}
        )
        with patch(
            "maximum_optimizer.orchestrator._is_reparse",
            side_effect=lambda path: Path(path) == self.root / "output-link",
        ):
            with self.assertRaisesRegex(MaximumConfigError, "reparse"):
                validate_run_paths(config, create=False)
        self.assertFalse(raw_output.exists())


if __name__ == "__main__":
    unittest.main()
