from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from maximum_optimizer import orchestrator as orchestrator_module
from maximum_optimizer.candidates import CandidateBuild, CandidateBuildError, CandidateTools
from maximum_optimizer.domain import (
    GateFailure,
    CandidateEvaluation,
    CandidateSpec,
    FamilyManifest,
    FocusRegionResult,
    FocusedRegionPolicy,
    FocusedGateResult,
    SearchBudget,
    StructuralFingerprint,
    ValidationResult,
)
from maximum_optimizer.fidelity_selection import (
    GENERAL_BODY_DETAIL,
    ROUND_RIGID,
    FamilyFidelitySelection,
    SourceFidelityAudit,
)
from maximum_optimizer.orchestrator import (
    AttemptReport,
    MaximumConfigError,
    MaximumRunConfig,
    ProductionAdapters,
    _aggregate_visual_results,
    _copytree_cancellable,
    _dependency_proof,
    _restore_cache_payload,
    _sha256_file,
    _seal_cache_entry,
    _copy_selected_family,
    _promote_verified_tree,
    _paired_representative_animation,
    _recover_output_transaction,
    _tree_manifest,
    _transaction_marker_path,
    _verify_cache_entry,
    _worst,
    validate_run_paths,
    run_maximum_addon,
)
from maximum_optimizer.reporting import canonical_json, event_line
from maximum_optimizer.processes import ProcessCancelledError, ProcessResult
from maximum_optimizer.qc_graph import parse_qc_graph
from maximum_optimizer.regions import (
    build_region_manifest,
    load_region_manifest_payload,
    resolve_region_assignments,
)
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


def _typed_profile(path: Path) -> Path:
    limits = json.loads(_profile(path).read_text(encoding="utf-8"))["limits"]
    payload = {
        "schema": 2,
        "version": "test-typed-v1",
        "calibrated": True,
        "corpus_hash": "b" * 64,
        "selector": "audited-original-round-family-v1",
        "profiles": {
            GENERAL_BODY_DETAIL: {"limits": {**limits, "edge_error": 0.2}},
            ROUND_RIGID: {"limits": {**limits, "edge_error": 0.05}},
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _focused_profile(path: Path) -> Path:
    from maximum_optimizer.calibration_evidence import (
        TRUSTED_CALIBRATION_EVIDENCE_V3_SHA256,
    )

    limits = json.loads(_profile(path).read_text(encoding="utf-8"))["limits"]
    payload = {
        "schema": 3,
        "version": "test-focused-v1",
        "calibrated": True,
        "corpus_hash": "c" * 64,
        "selector": "audited-original-round-family-v1",
        "focused_evidence_sha256": TRUSTED_CALIBRATION_EVIDENCE_V3_SHA256,
        "focused_policy": {
            "schema": 1,
            "selector": "surface-risk-top-k-v1",
            "top_k": 1,
        },
        "profiles": {
            GENERAL_BODY_DETAIL: {
                "limits": {**limits, "edge_error": 0.2},
                "focused_limits": {**limits, "edge_error": 0.1},
            },
            ROUND_RIGID: {
                "limits": {**limits, "edge_error": 0.05},
                "focused_limits": {**limits, "edge_error": 0.025},
            },
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


def _focus_target():
    from maximum_optimizer.domain import FocusTarget

    return FocusTarget(
        0, "r-" + "1" * 64, "test.smd", 0, "engine-default", (), 0,
        "bind", 0.01, 0.02, 0.1, 0.2, "2" * 64,
    )


def _whole_index_payload(workspace: Path) -> dict:
    files = {
        "render-source/maximum_region_manifest.json": b'{"schema":1}',
        "render-source/state-region.json": b'{"schema":1}',
        "render-source/state-configuration.json": b'{"schema":1}',
        "renders/engine-default/original/render_manifest.json": b'{"schema":1}',
        "renders/engine-default/optimized/render_manifest.json": json.dumps({
            "schema": 1,
            "geometry": [{
                "scope": "r-" + "1" * 64, "pose": "bind",
                "surface_bidirectional_p95": 0.01, "surface_max": 0.02,
            }],
        }, sort_keys=True).encode("utf-8"),
        "render-source/test.smd": b"reference-source",
        "src/test_OPT.smd": b"candidate-source",
        "logs/render-animation-classification.json": b'{"schema":1,"required":false}',
    }
    for relative, content in files.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def proof(relative):
        return {
            "path": relative,
            "sha256": hashlib.sha256((workspace / relative).read_bytes()).hexdigest(),
        }

    return {
        "schema": 1,
        "selector_version": "surface-risk-top-k-v1",
        "family": {
            "family_id": "a" * 64, "model_rel": "test.mdl",
            "input_sha256": "b" * 64,
        },
        "candidate": {
            "candidate_id": "candidate-100",
            "spec": CandidateSpec(
                "candidate-100", "blender", 1.0, 0.01, "transfer-v1"
            ).cache_payload(),
            "cache_digest": "c" * 64,
        },
        "profiles": {
            "whole": {"version": "whole-v1", "corpus_hash": "d" * 64, "file_sha256": "e" * 64},
            "focused": {"version": "focused-v1", "corpus_hash": "d" * 64, "file_sha256": "e" * 64},
        },
        "dependency": {"digest": "f" * 64},
        "renderer": {"version": "focus-render-v1", "digest": "1" * 64},
        "full_region_manifest": proof("render-source/maximum_region_manifest.json"),
        "animation": {
            "classification": proof("logs/render-animation-classification.json"),
            "reference": None, "candidate": None,
        },
        "states": [{
            "state_index": 0, "state_name": "engine-default",
            "bodygroups": [], "lod_index": 0, "poses": ["bind"],
            "region_manifest": proof("render-source/state-region.json"),
            "configuration_manifest": proof("render-source/state-configuration.json"),
            "reference_manifest": proof("renders/engine-default/original/render_manifest.json"),
            "candidate_manifest": proof("renders/engine-default/optimized/render_manifest.json"),
            "sources": [{
                "source_identity": "test.smd",
                "reference": proof("render-source/test.smd"),
                "candidate": proof("src/test_OPT.smd"),
            }],
            "geometry_rows": [{
                "scope": "r-" + "1" * 64, "pose": "bind",
                "surface_bidirectional_p95": 0.01, "surface_max": 0.02,
            }],
        }],
    }


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
        self.visual_profiles: list[tuple[str, str, str]] = []

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
        source_root = workspace / "src"
        source_root.mkdir()
        (source_root / "test_OPT.smd").write_bytes(b"candidate-source")
        qc = source_root / "optimized.qc"
        qc.write_text('$body body "test_OPT.smd"\n', encoding="utf-8")
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
        self.visual_profiles.append(
            (manifest.model_rel, candidate.spec.candidate_id, profile.version)
        )
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

    def test_candidate_evaluation_trailing_focused_fields_preserve_legacy_construction(self):
        from maximum_optimizer.compiled_size import scan_compiled_models

        compiled = self.root / "candidate-evaluation/models"
        compiled.mkdir(parents=True)
        (compiled / "test.mdl").write_bytes(b"x")
        size = scan_compiled_models(compiled)
        structural = ValidationResult(True)
        visual = ValidationResult(True, metrics={"fidelity_score": 0.9})

        evaluation = CandidateEvaluation(
            CandidateSpec("legacy", "blender", 1.0, 0.0, "legacy"),
            size, structural, visual, compiled,
        )

        self.assertIs(evaluation.whole_visual, visual)
        self.assertEqual(dict(evaluation.focused_by_region), {})
        with self.assertRaises(TypeError):
            evaluation.focused_by_region["r-" + "1" * 64] = object()

    def test_schema3_orders_focused_gate_before_candidate_cache_store_and_never_focuses_control(self):
        order = []
        self.config = MaximumRunConfig(
            self.config.addon_dir, self.config.output_dir, self.config.work_dir,
            self.config.blender_path, self.config.studiomdl_path,
            self.config.repo_root, SearchBudget(1, .025, 0),
            _focused_profile(self.root / "focused-profile.json"), False, False,
        )
        self.adapters.candidate_schedule = lambda _manifest: (
            CandidateSpec("candidate-100", "blender", 1.0, 0.01, "transfer-v1"),
        )
        original_build = self.adapters.build
        original_visual = self.adapters.visual

        def build(*args, **kwargs):
            result = original_build(*args, **kwargs)
            if result.spec.candidate_id != "roundtrip-control":
                order.append("build")
            return result

        def structural(manifest, candidate):
            if candidate.spec.candidate_id != "roundtrip-control":
                order.append("structural")
            return ValidationResult(True)

        def visual(manifest, control, candidate, profile, *, focused_profile=None):
            if candidate.spec.candidate_id == "roundtrip-control":
                self.assertIsNone(focused_profile)
            else:
                order.append("whole")
                self.assertIsNotNone(focused_profile)
            return original_visual(manifest, control, candidate, profile)

        def focused_visual(manifest, control, candidate, whole_profile, focused_profile, policy):
            self.assertNotEqual(candidate.spec.candidate_id, "roundtrip-control")
            order.append("focused")
            target = _focus_target()
            result = FocusRegionResult(
                target, ValidationResult(True, metrics={"fidelity_score": 0.95}),
                "3" * 64, False,
            )
            return FocusedGateResult(
                result.validation, (target,), {target.region_key: result}, "4" * 64,
            )

        self.adapters.build = build
        self.adapters.visual = visual
        self.adapters.focused_visual = focused_visual
        original_store = orchestrator_module.CandidateCache.store

        def store(cache, *args, **kwargs):
            order.append("store")
            return original_store(cache, *args, **kwargs)

        with patch.object(
            orchestrator_module.CandidateCache, "store",
            autospec=True,
            side_effect=store,
        ):
            report = run_maximum_addon(
                self.config,
                adapters=self.adapters,
                validator=structural,
                event_sink=self.events.append,
                profile_selector=lambda _manifest: FamilyFidelitySelection(
                    GENERAL_BODY_DETAIL, "test", (),
                ),
            )

        self.assertEqual(order, ["build", "structural", "whole", "focused", "store"])
        self.assertEqual(report.families[0].selected_candidate, "candidate-100")
        records = list((self.config.work_dir / "cache").glob("*/payload/maximum_cache_record.json"))
        self.assertEqual(len(records), 1)
        cache_record = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(cache_record["schema"], 3)
        self.assertEqual(cache_record["source_snapshot"]["candidate_id"], "candidate-100")

    def test_production_recovery_adapter_returns_typed_composition_failure(self):
        from tests.maximum_optimizer.test_composite import recipe as recovery_recipe
        from maximum_optimizer.cache import CacheKey
        from maximum_optimizer.orchestrator import ProductionAdapters

        adapter = ProductionAdapters(self.config, threading.Event())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            recipe_value = recovery_recipe(root)
            base_spec = CandidateSpec(
                "base", "blender", 0.4, 0.0, "blender-adaptive-v1",
                strategy="blender-adaptive-v1", transfer="blender-native-v1",
            )
            base = CandidateBuild(
                base_spec, root / "base-work", root / "missing.qc",
                root / "compiled", {}, {}, (), None,
            )
            composite_spec = CandidateSpec(
                "recovery-" + recipe_value.recipe_sha256,
                "blender", 0.4, 0.0, "blender-adaptive-v1",
                strategy="blender-adaptive-v1", transfer="blender-native-v1",
                composite_recipe=recipe_value,
            )
            result = adapter.recover_focused_candidate(
                manifest=self.family, control_build=base, base_build=base,
                base_evaluation=None, recipe=recipe_value, spec=composite_spec,
                cache_key=CacheKey("1" * 64), snapshots_by_sha256={},
                workspace=root / "recovery", whole_profile=load_profile(self.config.profile_path),
                focused_profile=load_profile(self.config.profile_path),
                policy=FocusedRegionPolicy(1, "surface-risk-top-k-v1", 1),
                structural_validator=self.structural,
                tools=CandidateTools(
                    Path(sys.executable), self.config.blender_path,
                    self.config.studiomdl_path, self.config.repo_root,
                ),
                prior_recoveries=(), cancel_event=threading.Event(),
            )
        self.assertEqual(result.evidence.terminal_status, "composition_failed")
        self.assertIsNone(result.authorization)

    def test_outer_recovery_boundary_rehashes_exact_compiled_membership(self):
        workspace = self.root / "current-compile-boundary"
        compiled = workspace / "compiled/models"
        compiled.mkdir(parents=True)
        mdl = compiled / "test.mdl"
        mdl.write_bytes(b"first-model-bytes")
        build = CandidateBuild(
            CandidateSpec("candidate", "blender", 1.0, 0.01, "transfer-v1"),
            workspace, workspace / "src/main.qc", compiled, {},
            {"test.mdl": "candidate-compile"}, (), None,
        )
        first = orchestrator_module._current_recovery_compile_files(
            self.family, build, threading.Event()
        )
        mdl.write_bytes(b"second-model-byte")
        second = orchestrator_module._current_recovery_compile_files(
            self.family, build, threading.Event()
        )
        self.assertNotEqual(first, second)

        (compiled / "test.vvd").write_bytes(b"unreported-family-member")
        with self.assertRaisesRegex(ValueError, "membership"):
            orchestrator_module._current_recovery_compile_files(
                self.family, build, threading.Event()
            )

    def test_authorized_recovery_output_rejects_mutation_during_copy(self):
        workspace = self.root / "authorized-output-copy"
        compiled = workspace / "compiled/models"
        compiled.mkdir(parents=True)
        (compiled / "test.mdl").write_bytes(b"authorized-model")
        build = CandidateBuild(
            CandidateSpec("candidate", "blender", 1.0, 0.01, "transfer-v1"),
            workspace, workspace / "src/main.qc", compiled, {},
            {"test.mdl": "candidate-compile"}, (), None,
        )
        proofs = orchestrator_module._current_recovery_compile_files(
            self.family, build, threading.Event()
        )
        output_models = self.root / "authorized-output/models"
        output_models.mkdir(parents=True)
        original_copy = orchestrator_module._copy_file_cancellable

        def mutating_copy(source, destination, cancel_event, *args, **kwargs):
            result = original_copy(source, destination, cancel_event, *args, **kwargs)
            path = Path(destination)
            path.write_bytes(b"X" * path.stat().st_size)
            return result

        with patch.object(
            orchestrator_module, "_copy_file_cancellable",
            side_effect=mutating_copy,
        ), self.assertRaisesRegex(ValueError, "differs from authorization"):
            orchestrator_module._copy_authorized_recovery_family(
                build, output_models, self.family, proofs, threading.Event()
            )

    def test_private_recovery_mutation_between_seal_and_semantics_is_rejected(self):
        entry = self.root / "private-recovery-entry"
        payload = entry / "payload"
        payload.mkdir(parents=True)
        (payload / "maximum_cache_record.json").write_text("{}", encoding="utf-8")
        focused = payload / "focused.bin"
        focused.write_bytes(b"AUTHORIZED")
        original_seal = orchestrator_module._seal_cache_entry

        def seal_then_mutate(cache_entry, cancel_event=None):
            original_seal(cache_entry, cancel_event)
            focused.write_bytes(b"MUTATED___")

        def semantic_validator():
            if focused.read_bytes() != b"AUTHORIZED":
                raise ValueError("semantic focused bytes changed")

        with patch.object(
            orchestrator_module, "_seal_cache_entry",
            side_effect=seal_then_mutate,
        ), self.assertRaisesRegex(ValueError, "semantic focused bytes changed"):
            orchestrator_module._seal_then_validate_private_recovery_entry(
                entry, semantic_validator
            )
        self.assertFalse((entry / "complete.json").exists())

    def test_recovery_cache_complete_marker_validates_and_detects_mutation(self):
        from maximum_optimizer.cache import CandidateCache, CacheKey

        source = self.root / "recovery-cache-source"
        source.mkdir()
        (source / "maximum_cache_record.json").write_text("{}", encoding="utf-8")
        (source / "artifact.bin").write_bytes(b"authorized")
        key = CacheKey("7" * 64)
        cache = CandidateCache(self.root / "recovery-cache")
        entry, owned = cache.store_validated(
            key, source, {"candidate": "recovery"},
            finalize_staging=lambda staging: orchestrator_module._seal_cache_entry(
                staging, None
            ),
            validate_existing=lambda _entry: None,
        )

        self.assertTrue(owned)
        self.assertTrue(orchestrator_module._verify_recovery_cache_entry(
            entry, key, threading.Event()
        ))
        (entry / "metadata.json").write_text("{}", encoding="utf-8")
        self.assertFalse(orchestrator_module._verify_recovery_cache_entry(
            entry, key, threading.Event()
        ))

    def test_real_typed_recovery_boundary_and_final_cache_transaction_pass(self):
        from dataclasses import replace
        from tests.maximum_optimizer.test_focused_cache import (
            _cache_payload, _material_proof, _render_file_proofs,
            _validation_metrics, _write_render_side,
        )
        from maximum_optimizer.cache import CandidateCache, CacheKey
        from maximum_optimizer.composite import (
            build_recovery_source_snapshot, build_source_tree_manifest,
            candidate_spec_sha256, optimizer_contract_sha256,
            recovery_candidate_spec,
        )
        from maximum_optimizer.domain import (
            ChangedSourceProof, CompositeRecipe, CompositionProof,
            FocusedEvidenceRef, SourceOverlay,
            changed_source_proof_payload, composition_proof_payload,
            source_overlay_payload,
        )
        from maximum_optimizer.focused_cache import (
            FocusedEvidenceContext, FocusedRecoveryContext,
            build_final_whole_authorization_evidence,
            build_focused_recovery_evidence, build_focused_render_evidence,
            compile_manifest_sha256, focused_gate_evidence_payload,
            focused_recovery_evidence_payload,
        )
        from maximum_optimizer.focused_regions import FocusSelection

        def digest(value):
            return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

        event = threading.Event()
        workspace = self.root / "real-recovery-boundary"
        index_payload = _whole_index_payload(workspace)
        source = workspace / "src"
        (source / "main_OPT.qc").write_text(
            '$modelname "test.mdl"\n$body body "test_OPT.smd"\n',
            encoding="utf-8",
        )
        graph = parse_qc_graph(source / "main_OPT.qc", source)
        source_manifest = build_source_tree_manifest(
            source, graph, "candidate-source-v1", event
        )
        base_source = self.root / "real-recovery-base"
        base_source.mkdir()
        (base_source / "test_OPT.smd").write_bytes(b"compressed-source")
        (base_source / "main_OPT.qc").write_text(
            '$modelname "test.mdl"\n$body body "test_OPT.smd"\n',
            encoding="utf-8",
        )
        base_manifest = build_source_tree_manifest(
            base_source,
            parse_qc_graph(base_source / "main_OPT.qc", base_source),
            "candidate-source-v1", event,
        )
        base_spec = CandidateSpec(
            "base", "blender", 0.4, 0.0, "blender-adaptive-v1",
            strategy="blender-adaptive-v1", transfer="blender-native-v1",
        )
        cache_payload = _cache_payload()
        whole_profile_sha = digest(cache_payload["whole_profile"])
        focused_profile_sha = digest(cache_payload["focused_profile"])
        original_snapshot = build_recovery_source_snapshot(
            kind="original", family_id=self.family.family_id,
            family_input_sha256=self.family.input_hash,
            optimizer_contract_sha256=optimizer_contract_sha256(base_spec),
            whole_profile_sha256=whole_profile_sha,
            focused_profile_sha256=focused_profile_sha,
            dependency_proof_sha256=cache_payload["dependency_proof_sha256"],
            candidate_id=None, candidate_cache_digest=None,
            source_root=source, source_manifest=source_manifest,
            focused_evidence=(),
        )
        target = _focus_target()
        base_file = next(
            item for item in base_manifest.files if item.file_identity == "test.smd"
        )
        current_file = next(
            item for item in source_manifest.files if item.file_identity == "test.smd"
        )
        overlay = SourceOverlay(
            "test.smd", "exact-original", target.region_key,
            base_file.sha256, current_file.sha256, current_file.size,
            original_snapshot.snapshot_sha256, None, None, None, (),
            "donors-exhausted-v1",
        )
        recipe_raw = {
            "schema": 1, "kind": "focused-recovery-v1",
            "family_id": self.family.family_id,
            "family_input_sha256": self.family.input_hash,
            "base_candidate_id": "base",
            "base_spec_sha256": candidate_spec_sha256(base_spec),
            "base_cache_digest": "b" * 64,
            "base_source_manifest_sha256": base_manifest.digest,
            "optimizer_contract_sha256": optimizer_contract_sha256(base_spec),
            "whole_profile_sha256": whole_profile_sha,
            "focused_profile_sha256": focused_profile_sha,
            "dependency_proof_sha256": cache_payload["dependency_proof_sha256"],
            "round_index": 0, "direct_ratio": None,
            "overlays": [source_overlay_payload(overlay)],
            "selector_version": "surface-risk-top-k-v1",
            "prefilter_version": None,
        }
        recipe = CompositeRecipe(
            1, "focused-recovery-v1", self.family.family_id,
            self.family.input_hash, "base", candidate_spec_sha256(base_spec),
            "b" * 64, base_manifest.digest,
            optimizer_contract_sha256(base_spec), whole_profile_sha,
            focused_profile_sha, cache_payload["dependency_proof_sha256"],
            0, None, (overlay,), "surface-risk-top-k-v1", None,
            digest(recipe_raw),
        )
        spec = recovery_candidate_spec(base_spec, recipe)
        changed = ChangedSourceProof(
            "test.smd", "test_OPT.smd", base_file.size, base_file.sha256,
            current_file.size, current_file.sha256,
            digest(source_overlay_payload(overlay)),
            original_snapshot.snapshot_sha256,
        )
        composition_raw = {
            "schema": 1, "kind": "focused-recovery-v1", "recipe_sha256": recipe.recipe_sha256,
            "base_manifest_sha256": base_manifest.digest,
            "composed_manifest_sha256": source_manifest.digest,
            "changed_sources": [changed_source_proof_payload(changed)],
        }
        composition = CompositionProof(
            1, recipe.recipe_sha256, base_manifest.digest,
            source_manifest.digest, (changed,), digest(composition_raw),
        )
        compiled = workspace / "compiled/models"
        compiled.mkdir(parents=True)
        (compiled / "test.mdl").write_bytes(b"compiled-recovery")
        key = CacheKey("9" * 64)
        build = CandidateBuild(
            spec, workspace, source / "main_OPT.qc", compiled, {},
            {"test.mdl": "candidate-compile"}, (), None,
        )
        compile_files = orchestrator_module._current_recovery_compile_files(
            self.family, build, event
        )
        compile_sha = compile_manifest_sha256(compile_files)
        structural_validation = ValidationResult(True)
        structural = ProductionAdapters._structural_recovery_evidence(
            key.digest, composition.evidence_sha256, compile_sha,
            self.family, structural_validation,
        )
        selection = FocusSelection(
            target.selector_input_sha256, (target,), (target,)
        )
        expected = dict(cache_payload["expected"])
        expected["region_key"] = target.region_key
        initial_reference = self.root / "initial-focused/reference"
        initial_candidate = self.root / "initial-focused/candidate"
        _write_render_side(initial_reference)
        _write_render_side(initial_candidate)
        failed_metrics = _validation_metrics(0.0)
        failed_metrics["edge_error"] = 0.2
        failed_metrics["fidelity_score"] = 0.0
        initial_validation = ValidationResult(
            False,
            (GateFailure("edge_error", "bind", 0.2, 0.05, "failed"),),
            failed_metrics, "bind",
        )
        material = _material_proof()
        initial_record = build_focused_render_evidence(
            target, initial_validation, expected,
            _render_file_proofs(initial_reference, initial_candidate),
            material["digest"], False,
        )
        base_context = FocusedEvidenceContext(
            1, self.family.family_id, "base",
            FocusedRegionPolicy(1, "surface-risk-top-k-v1", 1),
            cache_payload["whole_profile"], cache_payload["focused_profile"],
            cache_payload["trusted_evidence_v3_sha256"],
            cache_payload["dependency_proof_sha256"],
            {target.region_key: material},
        )
        initial_auth = focused_gate_evidence_payload(
            base_context, selection, (initial_record,)
        )
        recovery_context = FocusedRecoveryContext(
            2, base_context, "b" * 64, initial_auth["authorization_sha256"]
        )
        focused_root = (
            workspace / "focused-authorized/round-000"
            / f"{target.rank:03d}-{target.region_key}"
        )
        focused_reference = focused_root / "reference"
        focused_candidate = focused_root / "candidate"
        _write_render_side(focused_reference)
        _write_render_side(focused_candidate)
        pass_validation = ValidationResult(
            True, metrics=_validation_metrics(0.0)
        )
        final_record = build_focused_render_evidence(
            target, pass_validation, expected,
            _render_file_proofs(focused_reference, focused_candidate),
            material["digest"], False,
        )
        index_payload["family"] = {
            "family_id": self.family.family_id,
            "model_rel": self.family.model_rel,
            "input_sha256": self.family.input_hash,
        }
        index_payload["candidate"] = {
            "candidate_id": spec.candidate_id,
            "spec": spec.cache_payload(), "cache_digest": key.digest,
        }
        index_payload["dependency"] = {
            "digest": recipe.dependency_proof_sha256,
        }
        whole_seal = orchestrator_module._write_whole_visual_index(
            workspace / "logs/whole-visual-index.json", workspace,
            index_payload, event,
        )
        whole_validation = ValidationResult(
            True, metrics=_validation_metrics(0.0)
        )
        whole_render_sha = digest({
            "whole_index_sha256": whole_seal,
            "validation": orchestrator_module.validation_result_payload(
                whole_validation
            ),
        })
        final_whole = build_final_whole_authorization_evidence(
            spec.candidate_id, key.digest, recipe.recipe_sha256,
            composition.evidence_sha256, compile_sha,
            "logs/whole-visual-index.json", whole_seal,
            whole_render_sha, whole_validation,
        )
        recovery = build_focused_recovery_evidence(
            0, "authorized", recipe, composition, (changed,), (),
            compile_files, structural, (final_record,), final_whole,
        )
        authorization = focused_recovery_evidence_payload(
            recovery_context, selection, (initial_record,), (recovery,)
        )
        snapshot = build_recovery_source_snapshot(
            kind="candidate", family_id=self.family.family_id,
            family_input_sha256=self.family.input_hash,
            optimizer_contract_sha256=recipe.optimizer_contract_sha256,
            whole_profile_sha256=recipe.whole_profile_sha256,
            focused_profile_sha256=recipe.focused_profile_sha256,
            dependency_proof_sha256=recipe.dependency_proof_sha256,
            candidate_id=spec.candidate_id,
            candidate_cache_digest=key.digest, source_root=source,
            source_manifest=source_manifest,
            focused_evidence=(FocusedEvidenceRef(
                target.region_key, final_record.evidence_sha256
            ),),
        )
        build = replace(build, source_snapshot=snapshot)
        region = FocusRegionResult(
            target, pass_validation, final_record.evidence_sha256, False
        )
        focused_validation = orchestrator_module._aggregate_visual_results((
            (target.region_key, pass_validation),
        ))
        gate = FocusedGateResult(
            focused_validation, (target,), {target.region_key: region},
            recovery.evidence_sha256,
        )
        evaluation = CandidateEvaluation(
            spec,
            orchestrator_module._family_snapshot(
                orchestrator_module.scan_compiled_models(compiled),
                self.family.model_rel,
            ),
            structural_validation,
            orchestrator_module._aggregate_focused_gate(whole_validation, gate),
            compiled, whole_validation, {target.region_key: region},
        )
        result = orchestrator_module.FocusedRecoveryAdapterResult(
            build, evaluation, recovery, authorization,
            recovery_context, (initial_record,), selection, (recovery,),
        )
        validated = orchestrator_module._validate_authorized_recovery_boundary(
            result=result, manifest=self.family, recipe=recipe,
            cache_key=key, cancel_event=event,
        )
        self.assertEqual(canonical_json(validated), canonical_json(authorization))

        orchestrator_module._store_cache_record(
            workspace, build, evaluation.structural, evaluation.visual,
            key=key, manifest=self.family,
            dependency_digest=recipe.dependency_proof_sha256,
        )
        (workspace / "logs/focused-region-gate.json").write_text(
            canonical_json(authorization), encoding="utf-8"
        )
        cache = CandidateCache(self.root / "real-recovery-cache")
        entry, owned = cache.store_validated(
            key, workspace, {"candidate": spec.candidate_id},
            finalize_staging=lambda staging: (
                orchestrator_module._seal_then_validate_private_recovery_entry(
                    staging,
                    lambda: None,
                )
            ),
            validate_existing=lambda _entry: None,
            copy_function=lambda src, dst: orchestrator_module._copy_file_cancellable(
                src, dst, event
            ),
        )
        self.assertTrue(owned)
        self.assertTrue(orchestrator_module._verify_recovery_cache_entry(
            entry, key, event
        ))
        cached_build = orchestrator_module._load_cached_build(
            entry, spec, key=key, manifest=self.family,
            dependency_digest=recipe.dependency_proof_sha256,
            materialized_workspace=entry / "payload", cancel_event=event,
            optimizer_contract_digest=recipe.optimizer_contract_sha256,
            whole_profile_digest=recipe.whole_profile_sha256,
            focused_profile_digest=recipe.focused_profile_sha256,
        )
        cached_authorization = json.loads(
            (entry / "payload/logs/focused-region-gate.json").read_text(
                encoding="utf-8"
            )
        )
        cached_evaluation = replace(
            evaluation,
            size=orchestrator_module._family_snapshot(
                orchestrator_module.scan_compiled_models(
                    cached_build.compiled_models_dir
                ), self.family.model_rel,
            ),
            compiled_models_dir=cached_build.compiled_models_dir,
        )
        cached_result = replace(
            result, build=cached_build, evaluation=cached_evaluation,
            authorization=cached_authorization,
        )
        cached_validated = orchestrator_module._validate_authorized_recovery_boundary(
            result=cached_result, manifest=self.family, recipe=recipe,
            cache_key=key, cancel_event=event,
        )
        self.assertEqual(
            canonical_json(cached_validated), canonical_json(authorization)
        )

    def test_outer_recovery_boundary_rehashes_focused_rerun_snapshots(self):
        from tests.maximum_optimizer.test_focused_cache import (
            _cache_payload, _render_file_proofs, _target, _validation_metrics,
            _write_render_side,
        )
        from maximum_optimizer.focused_cache import build_focused_render_evidence

        workspace = self.root / "focused-rerun-current-bytes"
        target = _target()
        snapshot = (
            workspace / "focused-authorized/round-000"
            / f"{target.rank:03d}-{target.region_key}"
        )
        reference = snapshot / "reference"
        candidate = snapshot / "candidate"
        _write_render_side(reference)
        _write_render_side(candidate)
        payload = _cache_payload()
        record = build_focused_render_evidence(
            target, ValidationResult(True, metrics=_validation_metrics(0.0)),
            payload["expected"], _render_file_proofs(reference, candidate),
            payload["material_proof"]["digest"], False,
        )
        result = SimpleNamespace(recoveries=(
            SimpleNamespace(round_index=0, rerun_records=(record,)),
        ))

        orchestrator_module._validate_current_focused_rerun_files(
            result, workspace, threading.Event()
        )
        image = candidate / "textured/bind/front.png"
        original = image.read_bytes()
        image.write_bytes(b"X" * len(original))
        with self.assertRaisesRegex(ValueError, "focused render bytes changed|render image is corrupt"):
            orchestrator_module._validate_current_focused_rerun_files(
                result, workspace, threading.Event()
            )

    def test_recovery_focus_artifacts_normalize_fresh_hit_and_prior_rounds(self):
        from tests.maximum_optimizer.test_focused_cache import (
            _cache_payload, _render_file_proofs, _target, _validation_metrics,
            _write_render_side,
        )
        from maximum_optimizer.focused_cache import build_focused_render_evidence

        target = _target()
        identity = f"{target.rank:03d}-{target.region_key}"
        payload = _cache_payload()
        adapter = ProductionAdapters(self.config, threading.Event())

        first = self.root / "recovery-fresh"
        fresh_reference = first / "focused-renders" / identity / "original"
        fresh_candidate = first / "focused-renders" / identity / "optimized"
        _write_render_side(fresh_reference)
        _write_render_side(fresh_candidate)
        record = build_focused_render_evidence(
            target, ValidationResult(True, metrics=_validation_metrics(0.0)),
            payload["expected"],
            _render_file_proofs(fresh_reference, fresh_candidate),
            payload["material_proof"]["digest"], False,
        )
        first_root = adapter._materialize_recovery_focused_artifacts(
            first, 0, (record,), (), threading.Event()
        )
        prior = SimpleNamespace(
            round_index=0, evidence_sha256="8" * 64,
            rerun_records=(record,),
        )
        adapter._recovery_artifact_roots[prior.evidence_sha256] = first_root

        second = self.root / "recovery-hit"
        hit_reference = second / "focused-snapshots" / identity / "reference"
        hit_candidate = second / "focused-snapshots" / identity / "candidate"
        _write_render_side(hit_reference)
        _write_render_side(hit_candidate)
        second_root = adapter._materialize_recovery_focused_artifacts(
            second, 1, (record,), (prior,), threading.Event()
        )

        self.assertTrue((second_root / "round-000" / identity).is_dir())
        self.assertTrue((second_root / "round-001" / identity).is_dir())

        for index, status in enumerate((
            "composition_failed", "compile_failed", "structural_failed",
        ), start=2):
            workspace = self.root / f"recovery-after-{status}"
            hit_reference = workspace / "focused-snapshots" / identity / "reference"
            hit_candidate = workspace / "focused-snapshots" / identity / "candidate"
            _write_render_side(hit_reference)
            _write_render_side(hit_candidate)
            rootless = SimpleNamespace(
                round_index=0, evidence_sha256=str(index) * 64,
                rerun_records=(), terminal_status=status,
            )
            recovered = adapter._materialize_recovery_focused_artifacts(
                workspace, 1, (record,), (rootless,), threading.Event()
            )
            self.assertTrue((recovered / "round-000").is_dir())
            self.assertTrue((recovered / "round-001" / identity).is_dir())

    def test_outer_recovery_boundary_rejects_minimal_forged_authorization(self):
        forged = SimpleNamespace(
            evidence=SimpleNamespace(terminal_status="authorized"),
            build=None, evaluation=None, authorization={
                "schema": 2, "candidate_id": "forged"
            }, recovery_context=None, selection=None, initial_records=(),
            recoveries=(),
        )
        with self.assertRaisesRegex(ValueError, "invalid"):
            orchestrator_module._validate_authorized_recovery_boundary(
                result=forged, manifest=self.family, recipe=SimpleNamespace(
                    recipe_sha256="1" * 64,
                ), cache_key=orchestrator_module.CacheKey("2" * 64),
                cancel_event=threading.Event(),
            )

    def test_production_recovery_adapter_reaches_authorized_terminal_path(self):
        from tests.maximum_optimizer.test_composite import H, recipe, snapshot
        from tests.maximum_optimizer.test_focused_cache import (
            _cache_payload, _material_proof, _validation_metrics,
        )
        from maximum_optimizer.cache import CacheKey
        from maximum_optimizer.domain import FocusRegionResult
        from maximum_optimizer.focused_cache import FocusedEvidenceContext
        from maximum_optimizer.focused_regions import FocusSelection

        adapter = ProductionAdapters(self.config, threading.Event())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            recipe_value = recipe(root)
            base_snapshot = snapshot(root)
            base_spec = CandidateSpec(
                "candidate-a", "blender", 0.4, 0.0, "blender-adaptive-v1",
                strategy="blender-adaptive-v1", transfer="blender-native-v1",
            )
            base_workspace = root / "base-work"
            base_workspace.mkdir()
            base_build = CandidateBuild(
                base_spec, base_workspace, root / "base.qc", root / "base-compiled",
                {}, {}, (), base_snapshot,
            )
            metrics = _validation_metrics(0.0)
            whole = ValidationResult(True, metrics=metrics)
            target = _focus_target()
            selection = FocusSelection(
                target.selector_input_sha256, (target,), (target,)
            )
            payload = _cache_payload()
            context = FocusedEvidenceContext(
                1, recipe_value.family_id, base_spec.candidate_id,
                FocusedRegionPolicy(1, "surface-risk-top-k-v1", 1),
                payload["whole_profile"], payload["focused_profile"],
                payload["trusted_evidence_v3_sha256"],
                recipe_value.dependency_proof_sha256,
                {target.region_key: _material_proof()},
            )
            record = SimpleNamespace(
                target=target, evidence_sha256="6" * 64, validation=whole,
            )
            adapter._focused_runtime[base_workspace.resolve()] = (
                context, selection, (record,), {"authorization_sha256": "7" * 64},
            )
            composite_spec = CandidateSpec(
                "recovery-" + recipe_value.recipe_sha256,
                "blender", 0.4, 0.0, "blender-adaptive-v1",
                strategy="blender-adaptive-v1", transfer="blender-native-v1",
                composite_recipe=recipe_value,
            )
            recovery_workspace = root / "recovery"
            source = recovery_workspace / "src"
            source.mkdir(parents=True)
            (recovery_workspace / "logs").mkdir()
            compiled = recovery_workspace / "compiled" / "models"
            compiled.mkdir(parents=True)
            (compiled / "test.mdl").write_bytes(b"compiled-model")
            compiled_build = CandidateBuild(
                composite_spec, recovery_workspace, source / "main_OPT.qc",
                compiled, {}, {"test.mdl": "candidate-compile"}, (), None,
            )
            composed = SimpleNamespace(
                workspace=recovery_workspace,
                optimized_qc=source / "main_OPT.qc",
                composition=SimpleNamespace(
                    evidence_sha256="8" * 64, changed_sources=(),
                ),
                source_manifest=base_snapshot.source_manifest,
            )
            focused_gate = FocusedGateResult(
                whole, (target,),
                {target.region_key: FocusRegionResult(
                    target, whole, record.evidence_sha256, False,
                )},
                "9" * 64,
            )

            def focused_visual(*_args, **_kwargs):
                adapter._focused_runtime[recovery_workspace.resolve()] = (
                    context, selection, (record,), {"schema": 1},
                )
                return focused_gate

            def final_visual(*_args, **_kwargs):
                index = recovery_workspace / "logs/whole-visual-index.json"
                index.parent.mkdir(parents=True, exist_ok=True)
                index.write_text("{}", encoding="utf-8")
                adapter._whole_index_seals[recovery_workspace.resolve()] = hashlib.sha256(
                    index.read_bytes()
                ).hexdigest()
                return whole

            authorized_round = SimpleNamespace(
                terminal_status="authorized", evidence_sha256="a" * 64,
            )
            authorization = {
                "schema": 2, "candidate_id": composite_spec.candidate_id,
                "authorization_sha256": "b" * 64,
            }
            base_evaluation = CandidateEvaluation(
                base_spec,
                orchestrator_module._family_snapshot(
                    orchestrator_module.scan_compiled_models(compiled), "test.mdl"
                ),
                ValidationResult(True), whole, compiled, whole,
                focused_gate.regions,
            )
            with patch.object(
                orchestrator_module, "compose_candidate_sources", return_value=composed,
            ), patch.object(
                orchestrator_module, "_matching_qcs", return_value=(root / "main.qc",),
            ), patch.object(
                orchestrator_module, "parse_qc_graph", return_value=object(),
            ), patch.object(
                orchestrator_module, "_validate_complete_recovery_graph_pairing",
            ), patch.object(
                orchestrator_module, "_compile_composed_candidate",
                return_value=compiled_build,
            ), patch.object(
                adapter, "_seed_recovery_focus_index",
            ), patch.object(
                adapter, "_materialize_recovery_focused_artifacts",
                return_value=recovery_workspace / "focused-authorized",
            ), patch.object(
                adapter, "focused_visual", side_effect=focused_visual,
            ), patch.object(
                adapter, "visual", side_effect=final_visual,
            ), patch.object(
                orchestrator_module, "build_focused_recovery_evidence",
                return_value=authorized_round,
            ), patch.object(
                orchestrator_module, "focused_recovery_evidence_payload",
                return_value=authorization,
            ), patch.object(
                orchestrator_module, "validate_focused_gate_evidence_payload",
            ), patch.object(
                orchestrator_module, "FocusedRecoveryAdapterResult",
                side_effect=lambda build, evaluation, evidence, auth, *extra: SimpleNamespace(
                    build=build, evaluation=evaluation, evidence=evidence,
                    authorization=auth,
                ),
            ):
                result = adapter.recover_focused_candidate(
                    manifest=self.family, control_build=base_build,
                    base_build=base_build, base_evaluation=base_evaluation,
                    recipe=recipe_value, spec=composite_spec,
                    cache_key=CacheKey("1" * 64),
                    snapshots_by_sha256={base_snapshot.snapshot_sha256: base_snapshot},
                    workspace=recovery_workspace,
                    whole_profile=load_profile(self.config.profile_path),
                    focused_profile=load_profile(self.config.profile_path),
                    policy=FocusedRegionPolicy(1, "surface-risk-top-k-v1", 1),
                    structural_validator=lambda *_args: ValidationResult(True),
                    tools=CandidateTools(
                        Path(sys.executable), self.config.blender_path,
                        self.config.studiomdl_path, self.config.repo_root,
                    ),
                    prior_recoveries=(), cancel_event=threading.Event(),
                )

        self.assertEqual(result.evidence.terminal_status, "authorized")
        self.assertEqual(result.authorization, authorization)
        self.assertTrue(result.evaluation.visual.passed)
        self.assertEqual(result.build.source_snapshot.candidate_id, composite_spec.candidate_id)

    def test_original_recovery_snapshot_is_authoritative_and_never_none(self):
        from dataclasses import replace
        from maximum_optimizer.composite import (
            build_recovery_source_snapshot, build_source_tree_manifest,
            optimizer_contract_sha256,
        )
        from maximum_optimizer.orchestrator import _build_original_recovery_snapshot

        source = self.root / "authoritative-original"
        source.mkdir()
        (source / "body.smd").write_bytes(b"original")
        qc = source / "main.qc"
        qc.write_text(
            '$modelname "test.mdl"\n$body body "body.smd"\n',
            encoding="utf-8",
        )
        manifest = replace(self.family, source_dir=source)
        spec = CandidateSpec("candidate", "blender", 0.5, 0.01, "transfer-v1")
        graph = parse_qc_graph(qc, source)
        tree = build_source_tree_manifest(source, graph, "candidate-source-v1", None)
        base = build_recovery_source_snapshot(
            kind="candidate", family_id=manifest.family_id,
            family_input_sha256=manifest.input_hash,
            optimizer_contract_sha256=optimizer_contract_sha256(spec),
            whole_profile_sha256="1" * 64, focused_profile_sha256="2" * 64,
            dependency_proof_sha256="3" * 64, candidate_id="candidate",
            candidate_cache_digest="4" * 64, source_root=source,
            source_manifest=tree, focused_evidence=(),
        )
        original = _build_original_recovery_snapshot(
            manifest, base, threading.Event()
        )
        self.assertEqual(original.kind, "original")
        self.assertEqual(original.source_root, source)
        self.assertEqual(original.source_manifest.files[0].file_identity, "body.smd")

    def test_models_bridge_preflights_schema3_with_profile_set_loader_without_cli_changes(self):
        from maximum_optimizer.orchestrator import run_maximum_from_existing_args

        repo = Path(__file__).parents[2]
        profile = _focused_profile(self.root / "bridge-focused.json")
        blender = self.root / "bridge-blender.exe"
        studiomdl = self.root / "bridge-studiomdl.exe"
        blender.write_bytes(b"tool")
        studiomdl.write_bytes(b"tool")
        args = SimpleNamespace(
            maximum_profile=str(profile), blender=str(blender), studiomdl=str(studiomdl),
            maximum_max_candidates=1, maximum_min_ratio_step=.025,
            maximum_min_marginal_saving=0.0, maximum_resume=False,
            resume_opt=False, overwrite=False, decompile_jobs=1,
        )
        work = self.root / "bridge-work"

        def decompile(*_args, **_kwargs):
            logs = work / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "decompile_manifest.json").write_text(
                json.dumps({"total": 1, "results": []}), encoding="utf-8"
            )
            return ProcessResult(("python",), 0, 0.0, logs / "decompile.log")

        with patch.object(
            orchestrator_module, "load_fidelity_profile_set",
            wraps=orchestrator_module.load_fidelity_profile_set,
        ) as profile_loader, patch.object(
            orchestrator_module, "load_profile",
            side_effect=AssertionError("schema-1-only loader must not be called"),
        ), patch.object(
            orchestrator_module, "run_process", side_effect=decompile,
        ), patch(
            "selective_policy_models.write_final_policy_files",
        ), patch.object(
            orchestrator_module, "run_maximum_addon",
            return_value=SimpleNamespace(status="success"),
        ):
            result = run_maximum_from_existing_args(
                args, repo_root=repo, addon_path=self.addon,
                out_addon_dir=self.root / "bridge-output", work_dir=work,
            )

        self.assertEqual(result, 0)
        profile_loader.assert_called_once_with(profile.resolve())

    def test_whole_visual_index_is_exact_sealed_and_rechecks_current_bytes(self):
        workspace = self.root / "whole-index"
        payload = _whole_index_payload(workspace)
        path = workspace / "logs/whole-visual-index.json"

        seal = orchestrator_module._write_whole_visual_index(
            path, workspace, payload, threading.Event()
        )
        loaded = orchestrator_module._load_whole_visual_index(
            path, workspace, seal, threading.Event()
        )
        self.assertEqual(loaded["evidence_sha256"], seal)

        source = workspace / "render-source/test.smd"
        original = source.read_bytes()
        source.write_bytes(b"X" * len(original))
        with self.assertRaisesRegex(ValueError, "hash|changed|seal"):
            orchestrator_module._load_whole_visual_index(
                path, workspace, seal, threading.Event()
            )

    def test_whole_visual_index_rejects_stale_current_profile_dependency_renderer_and_cache_bindings(self):
        from maximum_optimizer.visual_validation import FidelityProfile

        index = _whole_index_payload(self.root / "whole-current-bindings")
        limits = json.loads(_profile(self.root / "bindings-profile.json").read_text())["limits"]
        whole = FidelityProfile(1, "whole-v1", True, "d" * 64, limits)
        focused = FidelityProfile(1, "focused-v1", True, "d" * 64, limits)
        valid = {
            "whole_profile": whole,
            "focused_profile": focused,
            "profile_file_sha256": "e" * 64,
            "dependency_digest": "f" * 64,
            "renderer_digest": "1" * 64,
            "candidate_cache_digest": "c" * 64,
        }
        orchestrator_module._validate_whole_index_current_bindings(index, **valid)
        mutations = {
            "profile_file_sha256": "2" * 64,
            "dependency_digest": "3" * 64,
            "renderer_digest": "4" * 64,
            "candidate_cache_digest": "5" * 64,
            "whole_profile": FidelityProfile(1, "whole-v1", True, "6" * 64, limits),
            "focused_profile": FidelityProfile(1, "focused-v1", True, "7" * 64, limits),
        }
        for field, changed in mutations.items():
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "current|differs|binding"
            ):
                orchestrator_module._validate_whole_index_current_bindings(
                    index, **{**valid, field: changed}
                )

    def test_candidate_evidence_write_and_remove_reject_reparse_ancestor(self):
        workspace = self.root / "evidence-workspace"
        outside = self.root / "evidence-outside"
        workspace.mkdir()
        outside.mkdir()
        protected = outside / "focused-region-gate.json"
        protected.write_text("protected", encoding="utf-8")
        try:
            (workspace / "logs").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink privilege unavailable: {exc}")

        with self.assertRaisesRegex(ValueError, "ancestor|contained|reparse"):
            orchestrator_module._safe_workspace_atomic_json(
                workspace, workspace / "logs/focused-region-gate.json",
                {"schema": 1}, "focused evidence",
            )
        with self.assertRaises(CandidateBuildError):
            ProductionAdapters._remove_evidence_file(
                workspace, workspace / "logs/focused-region-gate.json"
            )
        self.assertEqual(protected.read_text(encoding="utf-8"), "protected")

    def test_focused_workspace_cleanup_rejects_nested_reparse_tree(self):
        workspace = self.root / "focused-cleanup"
        nested = workspace / "focused-inputs/target/nested"
        nested.mkdir(parents=True)
        (nested / "keep.bin").write_bytes(b"keep")

        with patch(
            "maximum_optimizer.focused_cache._is_reparse",
            side_effect=lambda path: Path(path).name == "nested",
        ), self.assertRaisesRegex(CandidateBuildError, "unsafe"):
            orchestrator_module._remove_workspace_owned_tree(
                workspace, workspace / "focused-inputs/target",
                "focused input snapshot root",
            )

        self.assertTrue((nested / "keep.bin").is_file())

        mutable_parent = workspace / "focused-renders"
        mutable_parent.mkdir()
        with patch.object(
            orchestrator_module, "_is_reparse",
            side_effect=lambda path: Path(path) == mutable_parent,
        ), self.assertRaisesRegex(CandidateBuildError, "ancestor.*unsafe"):
            orchestrator_module._safe_workspace_mkdir(
                workspace, mutable_parent / "new-target", "focused render root"
            )
        self.assertFalse((mutable_parent / "new-target").exists())

    def test_whole_visual_index_rejects_self_reseal_path_escape_and_state_gaps(self):
        workspace = self.root / "whole-index-adversarial"
        payload = _whole_index_payload(workspace)
        path = workspace / "logs/whole-visual-index.json"
        seal = orchestrator_module._write_whole_visual_index(
            path, workspace, payload, threading.Event()
        )

        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["family"]["model_rel"] = "other.mdl"
        raw.pop("evidence_sha256")
        raw["evidence_sha256"] = hashlib.sha256(
            canonical_json(raw).encode("utf-8")
        ).hexdigest()
        path.write_text(canonical_json(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "seal"):
            orchestrator_module._load_whole_visual_index(
                path, workspace, seal, threading.Event()
            )

        payload = _whole_index_payload(workspace)
        payload["states"][0]["reference_manifest"]["path"] = "../escape.json"
        with self.assertRaisesRegex(ValueError, "relative|path"):
            orchestrator_module._write_whole_visual_index(
                path, workspace, payload, threading.Event()
            )

        payload = _whole_index_payload(workspace)
        second = json.loads(json.dumps(payload["states"][0]))
        second["state_index"] = 2
        second["state_name"] = "gap"
        payload["states"].append(second)
        with self.assertRaisesRegex(ValueError, "state"):
            orchestrator_module._write_whole_visual_index(
                path, workspace, payload, threading.Event()
            )

        payload = _whole_index_payload(workspace)
        payload["states"][0]["geometry_rows"] = [{
            "scope": "r-" + "2" * 64, "pose": "bind",
            "surface_bidirectional_p95": .01, "surface_max": .02,
        }, payload["states"][0]["geometry_rows"][0]]
        with self.assertRaisesRegex(ValueError, "geometry.*canonical|order"):
            orchestrator_module._write_whole_visual_index(
                path, workspace, payload, threading.Event()
            )

        payload = _whole_index_payload(workspace)
        payload["states"][0]["geometry_rows"][0]["surface_max"] = 0.021
        with self.assertRaisesRegex(ValueError, "geometry.*manifest|differ"):
            orchestrator_module._write_whole_visual_index(
                path, workspace, payload, threading.Event()
            )

        payload = _whole_index_payload(workspace)
        source = payload["states"][0]["sources"][0]
        source["candidate"] = {
            "path": source["reference"]["path"].upper(),
            "sha256": source["reference"]["sha256"],
        }
        with self.assertRaisesRegex(ValueError, "case-collid|self-reference"):
            orchestrator_module._write_whole_visual_index(
                path, workspace, payload, threading.Event()
            )

    def test_schema3_failed_or_incomplete_focus_never_stores_or_wins(self):
        self.config = MaximumRunConfig(
            self.config.addon_dir, self.config.output_dir, self.config.work_dir,
            self.config.blender_path, self.config.studiomdl_path,
            self.config.repo_root, SearchBudget(1, .025, 0),
            _focused_profile(self.root / "focused-reject-profile.json"), False, False,
        )
        self.adapters.candidate_schedule = lambda _manifest: (
            CandidateSpec("candidate-100", "blender", 1.0, 0.01, "transfer-v1"),
        )

        def visual(manifest, control, candidate, profile, *, focused_profile=None):
            return ValidationResult(True, metrics={"fidelity_score": 0.99})

        def focused_visual(*_args):
            target = _focus_target()
            validation = ValidationResult(
                False,
                (GateFailure("edge_error", "bind", .2, .1, "failed"),),
                {"fidelity_score": 0.0, "edge_error": .2}, "bind",
            )
            result = FocusRegionResult(target, validation, "3" * 64, False)
            return FocusedGateResult(
                validation, (target,), {target.region_key: result}, "4" * 64,
            )

        self.adapters.visual = visual
        self.adapters.focused_visual = focused_visual
        with patch.object(
            orchestrator_module.CandidateCache, "store", autospec=True,
        ) as store:
            report = run_maximum_addon(
                self.config, adapters=self.adapters, validator=self.structural,
                event_sink=self.events.append,
                profile_selector=lambda _manifest: FamilyFidelitySelection(
                    GENERAL_BODY_DETAIL, "test", (),
                ),
            )
        store.assert_not_called()
        self.assertIsNone(report.families[0].selected_candidate)
        self.assertEqual(report.families[0].status, "failed")
        self.assertIn("byte-exact focused recovery", report.families[0].reason)

    def test_recovery_planning_failure_is_isolated_before_reservation(self):
        self.config = MaximumRunConfig(
            self.config.addon_dir, self.config.output_dir, self.config.work_dir,
            self.config.blender_path, self.config.studiomdl_path,
            self.config.repo_root, SearchBudget(4, .025, 0),
            _focused_profile(self.root / "focused-plan-failure.json"), False, False,
        )
        self.adapters.candidate_schedule = lambda _manifest: (
            CandidateSpec("candidate-100", "blender", 1.0, 0.01, "transfer-v1"),
        )
        self.adapters.visual = lambda *_args, **_kwargs: ValidationResult(
            True, metrics={"fidelity_score": 0.99}
        )

        def focused_visual(*_args):
            target = _focus_target()
            metrics = {"fidelity_score": 0.0, "edge_error": 0.2}
            failed = ValidationResult(
                False,
                (GateFailure("edge_error", "bind", 0.2, 0.1, "failed"),),
                metrics, "bind",
            )
            region = FocusRegionResult(target, failed, "3" * 64, False)
            return FocusedGateResult(
                failed, (target,), {target.region_key: region}, "4" * 64,
            )

        self.adapters.focused_visual = focused_visual
        self.adapters.recover_focused_candidate = lambda **_kwargs: self.fail(
            "adapter must not run when planning fails"
        )
        with patch.object(
            orchestrator_module, "_build_original_recovery_snapshot",
            side_effect=RuntimeError("synthetic recovery planning failure"),
        ):
            report = run_maximum_addon(
                self.config, adapters=self.adapters, validator=self.structural,
                event_sink=self.events.append,
                profile_selector=lambda _manifest: FamilyFidelitySelection(
                    GENERAL_BODY_DETAIL, "test", (),
                ),
            )

        planning = [
            attempt for attempt in report.families[0].attempts
            if attempt.candidate_id == "recovery-plan-0"
        ]
        self.assertEqual(len(planning), 1)
        self.assertEqual(planning[0].status, "compile_failed")
        self.assertIn("synthetic recovery planning failure", planning[0].error)

    def test_schema3_cancellation_after_focused_adapter_never_stores_promotes_or_updates_best(self):
        cancel = threading.Event()
        self.config = MaximumRunConfig(
            self.config.addon_dir, self.config.output_dir, self.config.work_dir,
            self.config.blender_path, self.config.studiomdl_path,
            self.config.repo_root, SearchBudget(1, .025, 0),
            _focused_profile(self.root / "focused-cancel-profile.json"), False, False,
        )
        self.adapters.candidate_schedule = lambda _manifest: (
            CandidateSpec("candidate-100", "blender", 1.0, 0.01, "transfer-v1"),
        )
        self.adapters.visual = lambda *_args, **_kwargs: ValidationResult(True)

        def focused_visual(*_args):
            target = _focus_target()
            result = FocusRegionResult(target, ValidationResult(True), "3" * 64, False)
            cancel.set()
            return FocusedGateResult(
                result.validation, (target,), {target.region_key: result}, "4" * 64,
            )

        self.adapters.focused_visual = focused_visual
        with patch.object(
            orchestrator_module.CandidateCache, "store", autospec=True,
        ) as store:
            report = run_maximum_addon(
                self.config, cancel, adapters=self.adapters, validator=self.structural,
                event_sink=self.events.append,
                profile_selector=lambda _manifest: FamilyFidelitySelection(
                    GENERAL_BODY_DETAIL, "test", (),
                ),
            )
        store.assert_not_called()
        self.assertTrue(report.cancelled)
        self.assertFalse(self.config.output_dir.exists())
        self.assertNotIn("best_updated", [event["kind"] for event in self.events])
        candidate_terminal = [
            event for event in self.events
            if event["kind"] == "candidate_finished"
            and event.get("candidate") == "candidate-100"
        ]
        self.assertEqual(len(candidate_terminal), 1)
        self.assertEqual(candidate_terminal[0]["status"], "cancelled")

    def test_focused_aggregate_rejects_unsealed_or_rank_gapped_results(self):
        target = _focus_target()
        result = FocusRegionResult(target, ValidationResult(True), "not-a-seal", False)
        gate = FocusedGateResult(
            result.validation, (target,), {target.region_key: result}, "4" * 64,
        )
        with self.assertRaisesRegex(ValueError, "seal|evidence"):
            orchestrator_module._aggregate_focused_gate(ValidationResult(True), gate)

        from dataclasses import replace
        gapped = replace(target, rank=1)
        result = FocusRegionResult(gapped, ValidationResult(True), "3" * 64, False)
        gate = FocusedGateResult(
            result.validation, (gapped,), {gapped.region_key: result}, "4" * 64,
        )
        with self.assertRaisesRegex(ValueError, "rank|cardinality"):
            orchestrator_module._aggregate_focused_gate(ValidationResult(True), gate)

        result = FocusRegionResult(target, ValidationResult(True), "3" * 64, False)
        mismatched = FocusedGateResult(
            ValidationResult(False), (target,), {target.region_key: result}, "4" * 64,
        )
        with self.assertRaisesRegex(ValueError, "validation|region"):
            orchestrator_module._aggregate_focused_gate(
                ValidationResult(True), mismatched
            )

    def test_focused_aggregate_keeps_the_lowest_fidelity_failure_scope(self):
        from dataclasses import replace

        first = _focus_target()
        second = replace(
            first, rank=1, region_key="r-" + "8" * 64,
            selector_input_sha256=first.selector_input_sha256,
        )
        first_validation = ValidationResult(
            False, (GateFailure("edge_error", "bind", .9, .1, "failed"),),
            {"fidelity_score": .1, "edge_error": .9}, "bind",
        )
        second_validation = ValidationResult(
            False, (GateFailure("edge_error", "bind", .5, .1, "failed"),),
            {"fidelity_score": .5, "edge_error": .5}, "bind",
        )
        regions = {
            first.region_key: FocusRegionResult(
                first, first_validation, "3" * 64, False
            ),
            second.region_key: FocusRegionResult(
                second, second_validation, "4" * 64, False
            ),
        }
        gate = FocusedGateResult(
            _aggregate_visual_results((
                (first.region_key, first_validation),
                (second.region_key, second_validation),
            )),
            (first, second), regions, "5" * 64,
        )
        whole = ValidationResult(
            False, (GateFailure("rgb_mae", "whole", .4, .2, "failed"),),
            {"fidelity_score": .2, "rgb_mae": .4}, "whole",
        )

        aggregate = orchestrator_module._aggregate_focused_gate(whole, gate)

        self.assertEqual(aggregate.worst_scope, f"{first.region_key}/bind")

        one_region_gate = FocusedGateResult(
            _aggregate_visual_results(((first.region_key, second_validation),)),
            (first,), {first.region_key: FocusRegionResult(
                first, second_validation, "6" * 64, False
            )}, "7" * 64,
        )
        worse_whole_without_explicit_scope = ValidationResult(
            False,
            (GateFailure("rgb_mae", "whole-bind", .8, .2, "failed"),),
            {"fidelity_score": .05, "rgb_mae": .8}, "",
        )
        preserved = orchestrator_module._aggregate_focused_gate(
            worse_whole_without_explicit_scope, one_region_gate
        )
        self.assertEqual(preserved.worst_scope, "whole-bind")

    def test_schema3_compiled_cache_hit_reruns_whole_and_focused_without_second_store(self):
        self.config = MaximumRunConfig(
            self.config.addon_dir, self.config.output_dir, self.config.work_dir,
            self.config.blender_path, self.config.studiomdl_path,
            self.config.repo_root, SearchBudget(1, .025, 0),
            _focused_profile(self.root / "focused-resume-profile.json"), True, True,
        )
        self.adapters.candidate_schedule = lambda _manifest: (
            CandidateSpec("candidate-100", "blender", 1.0, 0.01, "transfer-v1"),
        )
        whole_calls = []
        focused_calls = []

        def visual(manifest, control, candidate, profile, *, focused_profile=None):
            if candidate.spec.candidate_id != "roundtrip-control":
                whole_calls.append(candidate.spec.candidate_id)
            return ValidationResult(True)

        def focused_visual(*args):
            candidate = args[2]
            focused_calls.append(candidate.spec.candidate_id)
            target = _focus_target()
            result = FocusRegionResult(target, ValidationResult(True), "3" * 64, False)
            return FocusedGateResult(
                result.validation, (target,), {target.region_key: result}, "4" * 64,
            )

        self.adapters.visual = visual
        self.adapters.focused_visual = focused_visual
        original_store = orchestrator_module.CandidateCache.store
        stores = []

        def store(cache, *args, **kwargs):
            stores.append(args[0].digest)
            return original_store(cache, *args, **kwargs)

        kwargs = {
            "adapters": self.adapters,
            "validator": self.structural,
            "profile_selector": lambda _manifest: FamilyFidelitySelection(
                GENERAL_BODY_DETAIL, "test", (),
            ),
        }
        with patch.object(
            orchestrator_module.CandidateCache, "store", autospec=True, side_effect=store,
        ):
            first = run_maximum_addon(self.config, **kwargs)
            second = run_maximum_addon(self.config, **kwargs)

        self.assertEqual(first.families[0].selected_candidate, "candidate-100")
        self.assertEqual(second.families[0].selected_candidate, "candidate-100")
        self.assertEqual(whole_calls, ["candidate-100", "candidate-100"])
        self.assertEqual(focused_calls, ["candidate-100", "candidate-100"])
        self.assertEqual(len(stores), 1)
        candidate_builds = [
            call for call in self.adapters.calls if call[1] == "candidate-100"
        ]
        self.assertEqual(len(candidate_builds), 1)

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

    def test_per_lod_visual_aggregate_reports_worst_score_and_scope(self):
        result = _aggregate_visual_results((
            ("base", ValidationResult(True, metrics={"fidelity_score": 0.99, "rgb_mae": 0.01}, worst_scope="base-scope")),
            ("lod-1", ValidationResult(False, (
                GateFailure("rgb_mae", "lod-scope", 0.4, 0.2, "too large"),
            ), {"fidelity_score": 0.40, "rgb_mae": 0.4}, "lod-scope")),
        ))
        self.assertFalse(result.passed)
        self.assertEqual(result.metrics["fidelity_score"], 0.40)
        self.assertEqual(result.metrics["rgb_mae"], 0.4)
        self.assertEqual(result.worst_scope, "lod-1/lod-scope")
        self.assertEqual(result.failures[0].scope, "lod-1/lod-scope")

    def test_inventory_exception_writes_failed_terminal_report(self):
        self.adapters.inventory = lambda _config: (_ for _ in ()).throw(RuntimeError("inventory boom"))
        report = self.run_optimizer()
        self.assertEqual(report.status, "failed")
        self.assertEqual([event["kind"] for event in self.events], ["run_started", "run_finished"])
        self.assertEqual(self.events[0]["family_count"], 0)
        self.assertEqual(json.loads(report.report_path.read_text(encoding="utf-8"))["status"], "failed")
        self.assertFalse(self.config.output_dir.exists())

    def test_transaction_recovery_runs_before_inventory_or_new_work(self):
        order = []
        original_inventory = self.adapters.inventory
        self.adapters.inventory = lambda config: (
            order.append("inventory") or original_inventory(config)
        )
        with patch(
            "maximum_optimizer.orchestrator._recover_output_transaction",
            side_effect=lambda destination: order.append("recovery"),
        ):
            self.run_optimizer()
        self.assertEqual(order[:2], ["recovery", "inventory"])

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
        self.assertEqual(family.status, "preserved", family.reason)
        self.assertIsNone(family.selected_candidate)
        self.assertIn("no candidate passed", family.reason)
        self.assertEqual((self.config.output_dir / "models" / "test.mdl").read_bytes(), b"o" * 120)

    def test_roundtrip_control_uses_exact_preserving_adaptive_blender_path(self):
        observed = []
        original_build = self.adapters.build

        def build(manifest, spec, workspace, tools, cancel_event):
            if spec.candidate_id == "roundtrip-control":
                observed.append(spec)
            return original_build(manifest, spec, workspace, tools, cancel_event)

        self.adapters.build = build
        self.run_optimizer()

        self.assertEqual(len(observed), 1)
        control = observed[0]
        self.assertEqual(control.strategy, "blender-adaptive-v1")
        self.assertEqual(control.transfer, "blender-native-v1")
        self.assertTrue(control.update_vertices)
        self.assertEqual(control.target_ratio, 1.0)

    def test_preserved_family_always_omits_dx80_from_output_and_accounting(self):
        dx80 = self.config.addon_dir / "models" / "test.dx80.vtx"
        dx80.write_bytes(b"8" * 20)
        original_build = self.adapters.build

        def build(*args, **kwargs):
            result = original_build(*args, **kwargs)
            if result.spec.candidate_id != "roundtrip-control":
                return result
            control_dx80 = result.compiled_models_dir / "test.dx80.vtx"
            control_dx80.write_bytes(b"8" * 20)
            return CandidateBuild(
                result.spec,
                result.workspace,
                result.optimized_qc,
                result.compiled_models_dir,
                result.compile_record,
                {**result.provenance, "test.dx80.vtx": "candidate-compile"},
                result.commands,
                result.source_snapshot,
            )

        self.adapters.build = build
        self.adapters.visual = lambda _manifest, _control, candidate, _profile: ValidationResult(
            candidate.spec.candidate_id == "roundtrip-control",
            worst_scope="all",
        )

        report = self.run_optimizer()

        family = report.families[0]
        self.assertEqual(family.status, "preserved", family.reason)
        self.assertFalse((self.config.output_dir / "models" / "test.dx80.vtx").exists())
        self.assertEqual(family.original_size.total_bytes, 140)
        self.assertEqual(family.selected_size.total_bytes, 120)
        self.assertEqual(family.savings["optional_removed_bytes"], 20)
        self.assertEqual(report.final_size.total_bytes, 120)
        self.assertNotIn(".dx80.vtx", report.final_size.bytes_by_kind)

    def test_optimized_family_always_omits_candidate_dx80(self):
        (self.config.addon_dir / "models" / "test.dx80.vtx").write_bytes(b"8" * 20)
        original_build = self.adapters.build

        def build(*args, **kwargs):
            result = original_build(*args, **kwargs)
            candidate_dx80 = result.compiled_models_dir / "test.dx80.vtx"
            candidate_dx80.write_bytes(b"8" * 10)
            return CandidateBuild(
                result.spec,
                result.workspace,
                result.optimized_qc,
                result.compiled_models_dir,
                result.compile_record,
                {**result.provenance, "test.dx80.vtx": "candidate-compile"},
                result.commands,
                result.source_snapshot,
            )

        self.adapters.build = build

        report = self.run_optimizer()

        family = report.families[0]
        self.assertEqual(family.status, "optimized")
        self.assertEqual(family.selected_candidate, "candidate-60")
        self.assertFalse((self.config.output_dir / "models" / "test.dx80.vtx").exists())
        self.assertEqual(family.selected_size.total_bytes, 60)
        self.assertEqual(report.final_size.total_bytes, 60)
        self.assertNotIn(".dx80.vtx", report.final_size.bytes_by_kind)

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
        missing_events = [
            event for event in self.events
            if event.get("family") == "not_decompiled.mdl"
            and event["kind"] in {"family_started", "family_finished"}
        ]
        self.assertEqual(len(missing_events), 2)
        self.assertTrue(all(event["family_index"] == 1 for event in missing_events))
        self.assertTrue(all(event["family_total"] == 2 for event in missing_events))

    def test_cancelled_run_has_exact_terminal_event_and_never_promotes(self):
        self.adapters.cancel_on = "candidate-60"
        cancel = threading.Event()
        report = self.run_optimizer(cancel_event=cancel)
        self.assertTrue(report.cancelled)
        self.assertFalse(self.config.output_dir.exists())
        terminals = [event["kind"] for event in self.events if event["kind"] in {"run_finished", "run_cancelled"}]
        self.assertEqual(terminals, ["run_cancelled"])

    def test_pre_set_cancel_writes_exact_terminal_journal_without_inventory(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(self.adapters, "inventory") as inventory, patch.object(
            self.adapters, "tool_versions"
        ) as versions:
            report = self.run_optimizer(cancel_event=cancel)
        inventory.assert_not_called()
        versions.assert_not_called()
        self.assertTrue(report.cancelled)
        self.assertEqual(report.status, "cancelled")
        self.assertEqual(
            [event["kind"] for event in self.events],
            ["run_started", "run_cancelled"],
        )
        self.assertTrue(report.report_path.is_file())
        audit = json.loads(
            (self.config.work_dir / "logs/fidelity-profile-selection.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(audit, {
            "schema": 1,
            "selector": "legacy-global-v1",
            "families": [],
        })
        self.assertFalse(self.config.output_dir.exists())

    def test_pre_set_cancel_persists_journal_before_sink_and_skips_model_scan(self):
        cancel = threading.Event()
        cancel.set()
        seen = []
        report_path = self.config.work_dir / "logs" / "maximum_report.json"
        def sink(event):
            self.assertTrue(report_path.is_file())
            durable = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["kind"] for item in durable["events"]],
                ["run_started", "run_cancelled"],
            )
            seen.append(event)
        with patch("maximum_optimizer.orchestrator.scan_compiled_models") as scan:
            report = run_maximum_addon(
                self.config,
                cancel_event=cancel,
                adapters=self.adapters,
                validator=self.structural,
                event_sink=sink,
            )
        scan.assert_not_called()
        self.assertTrue(report.cancelled)
        self.assertEqual([item["kind"] for item in seen], ["run_started", "run_cancelled"])

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
        self.assertEqual(family_started["family_index"], 0)
        self.assertEqual(family_started["family_total"], 1)
        candidate_started = next(event for event in self.events if event["kind"] == "candidate_started")
        self.assertIn("candidate_id", candidate_started)
        self.assertEqual(candidate_started["candidate_index"], 0)
        self.assertEqual(candidate_started["candidate_total"], self.config.budget.max_candidates + 1)
        final_payload = json.loads(report.report_path.read_text(encoding="utf-8"))
        self.assertEqual(final_payload["events"][-1]["kind"], "run_finished")

    def test_legacy_profile_never_invokes_typed_selector(self):
        def forbidden(_manifest):
            raise AssertionError("legacy profile invoked typed selector")

        report = run_maximum_addon(
            self.config,
            adapters=self.adapters,
            validator=self.structural,
            event_sink=self.events.append,
            profile_selector=forbidden,
        )

        self.assertEqual(report.status, "success")
        self.assertTrue(self.adapters.visual_profiles)
        self.assertEqual(
            {version for _family, _candidate, version in self.adapters.visual_profiles},
            {"test-calibrated-v1"},
        )

    def test_typed_profile_is_selected_once_and_reused_for_control_and_candidates(self):
        self.config = MaximumRunConfig(**{
            **self.config.to_kwargs(),
            "profile_path": _typed_profile(self.root / "typed.json"),
        })
        calls = []

        def selector(manifest):
            calls.append((manifest.model_rel, len(self.adapters.calls)))
            return FamilyFidelitySelection(
                ROUND_RIGID,
                "all-visual-sources-round-rigid",
                (SourceFidelityAudit("wheel.smd", True, "eligible", 2),),
            )

        report = run_maximum_addon(
            self.config,
            adapters=self.adapters,
            validator=self.structural,
            event_sink=self.events.append,
            profile_selector=selector,
        )

        self.assertEqual(report.status, "success")
        self.assertEqual(calls, [("test.mdl", 0)])
        self.assertTrue(self.adapters.visual_profiles)
        self.assertEqual(
            {version for _family, _candidate, version in self.adapters.visual_profiles},
            {"test-typed-v1:round-rigid-v1"},
        )
        audit = json.loads(
            (self.config.work_dir / "logs/fidelity-profile-selection.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(audit["selector"], "audited-original-round-family-v1")
        self.assertEqual(audit["families"][0]["profile_class"], ROUND_RIGID)
        self.assertEqual(audit["families"][0]["sources"][0]["source"], "wheel.smd")

    def test_typed_general_fallback_continues_with_general_profile(self):
        self.config = MaximumRunConfig(**{
            **self.config.to_kwargs(),
            "profile_path": _typed_profile(self.root / "typed.json"),
        })

        report = run_maximum_addon(
            self.config,
            adapters=self.adapters,
            validator=self.structural,
            profile_selector=lambda _manifest: FamilyFidelitySelection(
                GENERAL_BODY_DETAIL,
                "body.dmx:unsupported-source-format:.dmx",
                (SourceFidelityAudit(
                    "body.dmx", False, "unsupported-source-format:.dmx", None
                ),),
            ),
        )

        self.assertEqual(report.status, "success")
        self.assertEqual(
            {version for _family, _candidate, version in self.adapters.visual_profiles},
            {"test-typed-v1:general-body-detail-v1"},
        )

    def test_typed_selector_error_fails_family_before_any_build(self):
        self.config = MaximumRunConfig(**{
            **self.config.to_kwargs(),
            "profile_path": _typed_profile(self.root / "typed.json"),
        })

        report = run_maximum_addon(
            self.config,
            adapters=self.adapters,
            validator=self.structural,
            profile_selector=lambda _manifest: (_ for _ in ()).throw(ValueError("bad source")),
        )

        self.assertEqual(report.status, "failed")
        self.assertEqual(report.families[0].status, "failed")
        self.assertEqual(self.adapters.calls, [])
        audit = json.loads(
            (self.config.work_dir / "logs/fidelity-profile-selection.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(audit["families"][0]["status"], "failed")

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
        second = _family(self.root, "other")
        (self.addon / "models" / "other.mdl").write_bytes(b"z" * 90)
        self.adapters._families = (self.family, second)
        self.adapters.cancel_during_visual = True
        report = self.run_optimizer(cancel_event=threading.Event())
        self.assertTrue(report.cancelled)
        self.assertFalse(self.config.output_dir.exists())
        self.assertEqual([event["kind"] for event in self.events][-1], "run_cancelled")
        audit = json.loads(
            (self.config.work_dir / "logs/fidelity-profile-selection.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(audit["families"]), 2)
        self.assertEqual(audit["families"][1]["model_rel"], "other.mdl")
        self.assertEqual(audit["families"][1]["status"], "cancelled")

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
        destination = self.root / "transaction-output"
        staging = self.root / (".transaction-output.maximum-staging-" + "f" * 32)
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

    def test_promotion_honors_cancel_at_final_pre_replace_barrier(self):
        staging = self.root / "cancel-staging"
        destination = self.root / "cancel-output"
        staging.mkdir()
        destination.mkdir()
        (staging / "new.bin").write_bytes(b"new")
        (destination / "old.bin").write_bytes(b"old")
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ProcessCancelledError):
            _promote_verified_tree(
                staging, destination, _tree_manifest(staging), cancel_event=cancel
            )
        self.assertEqual((destination / "old.bin").read_bytes(), b"old")
        self.assertTrue(staging.exists())

    def test_promotion_is_non_interruptible_after_first_replace(self):
        destination = self.root / "commit-output"
        staging = self.root / (".commit-output.maximum-staging-" + "1" * 32)
        staging.mkdir()
        (staging / "new.bin").write_bytes(b"new")
        expected = _tree_manifest(staging)
        cancel = threading.Event()
        real_replace = __import__("os").replace
        def replace_then_cancel(source, target):
            real_replace(source, target)
            if Path(source) == staging and Path(target) == destination:
                cancel.set()
        with patch("maximum_optimizer.orchestrator.os.replace", side_effect=replace_then_cancel):
            _promote_verified_tree(
                staging, destination, expected, cancel_event=cancel
            )
        self.assertTrue(cancel.is_set())
        self.assertEqual((destination / "new.bin").read_bytes(), b"new")

    def test_marker_owned_backup_is_restored_after_crash_between_renames(self):
        destination = self.root / "recover-output"
        staging = self.root / (".recover-output.maximum-staging-" + "a" * 32)
        destination.mkdir()
        staging.mkdir()
        (destination / "old.bin").write_bytes(b"old")
        (staging / "new.bin").write_bytes(b"new")
        expected = _tree_manifest(staging)
        class Crash(BaseException):
            pass
        with self.assertRaises(Crash):
            _promote_verified_tree(
                staging,
                destination,
                expected,
                crash_hook=lambda phase: (_ for _ in ()).throw(Crash())
                if phase == "backup_renamed" else None,
            )
        self.assertFalse(destination.exists())
        _recover_output_transaction(destination)
        self.assertEqual((destination / "old.bin").read_bytes(), b"old")
        self.assertFalse(staging.exists())
        self.assertFalse(tuple(self.root.glob(".recover-output.maximum-backup-*")))

    def test_recovery_fails_closed_on_ambiguous_marker_owned_backups(self):
        destination = self.root / "ambiguous-output"
        staging = self.root / (".ambiguous-output.maximum-staging-" + "b" * 32)
        destination.mkdir()
        staging.mkdir()
        (destination / "old.bin").write_bytes(b"old")
        (staging / "new.bin").write_bytes(b"new")
        class Crash(BaseException):
            pass
        with self.assertRaises(Crash):
            _promote_verified_tree(
                staging, destination, _tree_manifest(staging),
                crash_hook=lambda phase: (_ for _ in ()).throw(Crash())
                if phase == "backup_renamed" else None,
            )
        extra = self.root / (".ambiguous-output.maximum-backup-" + "c" * 32)
        extra.mkdir()
        (extra / "other.bin").write_bytes(b"other")
        with self.assertRaisesRegex(MaximumConfigError, "ambiguous"):
            _recover_output_transaction(destination)
        self.assertFalse(destination.exists())
        self.assertTrue(extra.exists())

    def test_destination_present_is_never_overwritten_during_recovery(self):
        destination = self.root / "committed-output"
        staging = self.root / (".committed-output.maximum-staging-" + "d" * 32)
        destination.mkdir()
        staging.mkdir()
        (destination / "old.bin").write_bytes(b"old")
        (staging / "new.bin").write_bytes(b"new")
        class Crash(BaseException):
            pass
        with self.assertRaises(Crash):
            _promote_verified_tree(
                staging, destination, _tree_manifest(staging),
                crash_hook=lambda phase: (_ for _ in ()).throw(Crash())
                if phase == "staging_renamed" else None,
            )
        self.assertEqual((destination / "new.bin").read_bytes(), b"new")
        _recover_output_transaction(destination)
        self.assertEqual((destination / "new.bin").read_bytes(), b"new")
        self.assertFalse(tuple(self.root.glob(".committed-output.maximum-backup-*")))

    def test_legacy_orphan_without_marker_is_diagnostic_only(self):
        destination = self.root / "legacy-output"
        orphan = self.root / (".legacy-output.maximum-backup-" + "e" * 32)
        orphan.mkdir()
        (orphan / "old.bin").write_bytes(b"old")
        with self.assertRaisesRegex(MaximumConfigError, "without.*marker"):
            _recover_output_transaction(destination)
        self.assertFalse(destination.exists())
        self.assertEqual((orphan / "old.bin").read_bytes(), b"old")

    def test_crash_barriers_before_backup_and_after_verification_recover_safely(self):
        class Crash(BaseException):
            pass
        for index, phase in enumerate(("marker_created", "verified"), start=2):
            with self.subTest(phase=phase):
                destination = self.root / f"phase-{index}-output"
                staging = self.root / (
                    f".phase-{index}-output.maximum-staging-" + str(index) * 32
                )
                destination.mkdir()
                staging.mkdir()
                (destination / "old.bin").write_bytes(b"old")
                (staging / "new.bin").write_bytes(b"new")
                with self.assertRaises(Crash):
                    _promote_verified_tree(
                        staging, destination, _tree_manifest(staging),
                        crash_hook=lambda current, wanted=phase: (
                            (_ for _ in ()).throw(Crash()) if current == wanted else None
                        ),
                    )
                _recover_output_transaction(destination)
                expected = b"old" if phase == "marker_created" else b"new"
                filename = "old.bin" if phase == "marker_created" else "new.bin"
                self.assertEqual((destination / filename).read_bytes(), expected)
                self.assertFalse(tuple(self.root.glob(
                    f".phase-{index}-output.maximum-backup-*"
                )))
                self.assertFalse(_transaction_marker_path(destination).exists())

    def test_invalid_marker_path_schema_fails_without_mutation(self):
        destination = self.root / "invalid-marker-output"
        marker = _transaction_marker_path(destination)
        marker.write_text(json.dumps({
            "schema": 1,
            "destination": destination.name,
            "staging": "../outside",
            "backup": None,
            "nonce": "3" * 32,
            "phase": "marker_created",
            "had_destination": False,
        }), encoding="utf-8")
        with self.assertRaisesRegex(MaximumConfigError, "staging|marker"):
            _recover_output_transaction(destination)
        self.assertTrue(marker.exists())

    def test_dangling_reparse_marker_fails_closed_before_run_mutation(self):
        destination = self.root / "dangling-output"
        marker = _transaction_marker_path(destination)
        real_lexists = __import__("os").path.lexists
        with patch(
            "maximum_optimizer.orchestrator.os.path.lexists",
            side_effect=lambda path: Path(path) == marker or real_lexists(path),
        ), patch(
            "maximum_optimizer.orchestrator._is_reparse",
            side_effect=lambda path: Path(path) == marker,
        ):
            with self.assertRaisesRegex(MaximumConfigError, "marker|orphan"):
                _recover_output_transaction(destination)

    def test_animation_pair_requires_identical_frame_evidence(self):
        original_root = self.root / "animation-original"
        candidate_root = self.root / "animation-candidate"
        original_root.mkdir()
        candidate_root.mkdir()
        mesh = 'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\ntriangles\nend\n'
        for root, frames, qc_name in (
            (original_root, (0, 10), "main.qc"),
            (candidate_root, (0, 10, 20), "main_OPT.qc"),
        ):
            (root / "mesh.smd").write_text(mesh, encoding="utf-8")
            animation = [
                'version 1', 'nodes', '0 "root" -1', 'end', 'skeleton',
            ]
            for frame in frames:
                animation.extend((f"time {frame}", "0 0 0 0 0 0 0"))
            animation.extend(("end", "triangles", "end", ""))
            (root / "anim.smd").write_text("\n".join(animation), encoding="utf-8")
            (root / qc_name).write_text(
                '$modelname "test.mdl"\n$body "body" "mesh.smd"\n'
                '$sequence "idle" "anim.smd"\n',
                encoding="utf-8",
            )
        from maximum_optimizer.qc_graph import parse_qc_graph
        original_graph = parse_qc_graph(original_root / "main.qc", original_root)
        candidate_graph = parse_qc_graph(candidate_root / "main_OPT.qc", candidate_root)
        self.assertIsNone(_paired_representative_animation(original_graph, candidate_graph))

    def test_real_smd_deformation_evidence_classifies_rigid_one_bone_as_bind_only(self):
        rigid = self.root / "rigid.smd"
        rigid.write_text(
            "version 1\n"
            "nodes\n0 \"root\" -1\nend\n"
            "skeleton\ntime 0\n0 0 0 0 0 0 0\nend\n"
            "triangles\nmaterial/base\n"
            "0 0 0 0 0 0 1 0 0\n"
            "0 1 0 0 0 0 1 1 0\n"
            "0 0 1 0 0 0 1 0 1\n"
            "end\n",
            encoding="utf-8",
        )
        helper = getattr(orchestrator_module, "_smd_deformation_required", None)
        self.assertIsNotNone(helper, "conservative real-SMD deformation helper is missing")
        self.assertFalse(helper((rigid,)))

    def test_real_smd_deformation_evidence_classifies_weighted_multi_bone_as_required(self):
        deformable = self.root / "deformable.smd"
        deformable.write_text(
            "version 1\n"
            "nodes\n0 \"root\" -1\n1 \"child\" 0\nend\n"
            "skeleton\ntime 0\n0 0 0 0 0 0 0\n1 0 0 0 0 0 0\nend\n"
            "triangles\nmaterial/base\n"
            "0 0 0 0 0 0 1 0 0 2 0 0.5 1 0.5\n"
            "0 1 0 0 0 0 1 1 0 1 0 1\n"
            "1 0 1 0 0 0 1 0 1\n"
            "end\n",
            encoding="utf-8",
        )
        helper = getattr(orchestrator_module, "_smd_deformation_required", None)
        self.assertIsNotNone(helper, "conservative real-SMD deformation helper is missing")
        self.assertTrue(helper((deformable,)))

    def test_zero_links_and_zero_explicit_sum_use_parent_remainder(self):
        helper = getattr(orchestrator_module, "_smd_deformation_required", None)
        self.assertIsNotNone(helper)
        cases = {
            "zero_links": "0 0 0 0 0 0 1 0 0 0",
            "zero_explicit": "0 0 0 0 0 0 1 0 0 1 0 0",
        }
        for name, vertex in cases.items():
            with self.subTest(name=name):
                path = self.root / f"valid-{name}.smd"
                path.write_text(
                    "version 1\nnodes\n0 \"root\" -1\nend\n"
                    "skeleton\ntime 0\n0 0 0 0 0 0 0\nend\n"
                    f"triangles\nmaterial/base\n{vertex}\n"
                    "0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\nend\n",
                    encoding="utf-8",
                )
                self.assertFalse(helper((path,)))

    def test_ambiguous_smd_link_evidence_never_authorizes_bind_only(self):
        helper = getattr(orchestrator_module, "_smd_deformation_required", None)
        self.assertIsNotNone(helper, "conservative real-SMD deformation helper is missing")
        cases = {
            "unknown": "0 0 0 0 0 0 1 0 0 1 9 1",
            "negative": "0 0 0 0 0 0 1 0 0 1 0 -0.1",
            "nonfinite": "0 0 0 0 0 0 1 0 0 1 0 nan",
            "malformed": "0 0 0 0 0 0 1 0 0 2 0 1",
            "over_one": "0 0 0 0 0 0 1 0 0 1 0 2.0",
            "duplicate_bone": "0 0 0 0 0 0 1 0 0 2 0 0.5 0 0.5",
            "sum_over_one": "0 0 0 0 0 0 1 0 0 1 0 1.1",
        }
        for name, vertex in cases.items():
            with self.subTest(name=name):
                path = self.root / f"ambiguous-{name}.smd"
                path.write_text(
                    "version 1\nnodes\n0 \"root\" -1\nend\n"
                    "skeleton\ntime 0\n0 0 0 0 0 0 0\nend\n"
                    f"triangles\nmaterial/base\n{vertex}\n"
                    "0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\nend\n",
                    encoding="utf-8",
                )
                self.assertTrue(helper((path,)))

        remainder = self.root / "valid-parent-remainder.smd"
        remainder.write_text(
            "version 1\nnodes\n0 \"root\" -1\nend\n"
            "skeleton\ntime 0\n0 0 0 0 0 0 0\nend\n"
            "triangles\nmaterial/base\n"
            "0 0 0 0 0 0 1 0 0 1 0 0.25\n"
            "0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\nend\n",
            encoding="utf-8",
        )
        self.assertFalse(helper((remainder,)))

    def test_production_visual_uses_nested_paths_real_animation_and_separate_lods(self):
        source = self.root / "production-source"
        source.mkdir()
        smd = (
            "version 1\nnodes\n0 \"root\" -1\n1 \"child\" 0\nend\n"
            "skeleton\ntime 0\n0 0 0 0 0 0 0\n1 0 0 0 0 0 0\nend\n"
            "triangles\nmaterial/base\n"
            "0 0 0 0 0 0 1 0 0 2 0 0.5 1 0.5\n"
            "0 1 0 0 0 0 1 1 0 1 0 1\n"
            "1 0 1 0 0 0 1 0 1\nend\n"
        )
        (source / "mesh.smd").write_text(smd, encoding="utf-8")
        (source / "lod.smd").write_text(smd, encoding="utf-8")
        animation_smd = (
            "version 1\nnodes\n0 \"root\" -1\n1 \"child\" 0\nend\n"
            "skeleton\ntime 0\n0 0 0 0 0 0 0\n1 0 0 0 0 0 0\n"
            "time 10\n0 1 0 0 0 0 0\n1 0 1 0 0 0 0\nend\ntriangles\nend\n"
        )
        (source / "anim.smd").write_text(animation_smd, encoding="utf-8")
        (source / "nested").mkdir()
        (source / "nested" / "main.qc").write_text(
            '$modelname "test.mdl"\n$body "body" "../mesh.smd"\n'
            '$lod 10 { replacemodel "../mesh.smd" "../lod.smd" }\n'
            '$sequence "idle" "../anim.smd"\n',
            encoding="utf-8",
        )
        fp = StructuralFingerprint(
            "test.mdl", ("body",), (), (), ("root",), (), (), (), ("idle",),
            ("mesh.smd",), ("lod.smd",), None,
        )
        manifest = FamilyManifest(
            hashlib.sha256(b"production-family").hexdigest(), "test.mdl", source, self.addon / "models", fp,
            "b" * 64, (".mdl",),
        )
        workspace = self.root / "production-candidate"
        nested = workspace / "src" / "nested"
        nested.mkdir(parents=True)
        (workspace / "logs").mkdir()
        (workspace / "src" / "mesh_OPT.smd").write_text(smd, encoding="utf-8")
        (workspace / "src" / "lod_OPT.smd").write_text(smd, encoding="utf-8")
        (workspace / "src" / "anim.smd").write_text(animation_smd, encoding="utf-8")
        qc = nested / "main_OPT.qc"
        qc.write_text(
            '$modelname "test.mdl"\n$body "body" "../mesh_OPT.smd"\n'
            '$lod 10 { replacemodel "../mesh_OPT.smd" "../lod_OPT.smd" }\n'
            '$sequence "idle" "../anim.smd"\n',
            encoding="utf-8",
        )
        compiled = workspace / "compiled"
        compiled.mkdir()
        (workspace / "renders").mkdir()
        (workspace / "renders" / "stale.txt").write_text("must disappear", encoding="utf-8")
        build = CandidateBuild(
            CandidateSpec("production", "blender", 0.5, 0.0, "test"),
            workspace, qc, compiled, {}, {"test.mdl": "candidate-compile"}, (),
        )
        commands = []
        full_region_manifest = build_region_manifest((
            ("mesh.smd", "Body", ("material/base",)),
            ("lod.smd", "Body", ("material/lod",)),
        ))
        def write_full_region_manifest():
            (workspace / "render-source" / "maximum_region_manifest.json").write_text(
                canonical_json(full_region_manifest.to_payload()) + "\n",
                encoding="utf-8",
            )
        rendered_region_sources = []
        def runner(command, **kwargs):
            commands.append(tuple(str(item) for item in command))
            if "--python-expr" in command:
                write_full_region_manifest()
            else:
                region_manifest_path = Path(command[command.index("--region-manifest") + 1])
                region_manifest = load_region_manifest_payload(json.loads(
                    region_manifest_path.read_text(encoding="utf-8")
                ))
                before_paths = tuple(
                    Path(command[index + 1])
                    for index, item in enumerate(command)
                    if item == "--before"
                )
                explicit_source_root = Path(
                    command[command.index("--source-root") + 1]
                )
                observations = tuple(
                    (
                        path.relative_to(explicit_source_root).as_posix(),
                        "Body",
                        ("material/lod",) if path.name == "lod.smd" else ("material/base",),
                    )
                    for path in before_paths
                )
                resolve_region_assignments(
                    region_manifest, observations, require_complete=True
                )
                rendered_region_sources.append({
                    entry.descriptor.source_identity for entry in region_manifest.entries
                })
                out = Path(command[command.index("--out") + 1])
                for side in ("original", "optimized"):
                    (out / side).mkdir(parents=True)
                    (out / side / "render_manifest.json").write_text(json.dumps({
                        "geometry": [
                            {
                                "scope": entry.key, "pose": pose,
                                "surface_bidirectional_p95": .01,
                                "surface_max": .02,
                            }
                            for entry in region_manifest.entries
                            for pose in ("bind", "representative")
                        ],
                    }), encoding="utf-8")
            return ProcessResult(tuple(str(item) for item in command), 0, 0.01, kwargs["log_path"])
        adapter = ProductionAdapters(self.config, threading.Event())
        with (
            patch("maximum_optimizer.orchestrator.run_process", side_effect=runner),
            patch("maximum_optimizer.orchestrator.compare_render_sets", return_value=ValidationResult(True)),
        ):
            result = adapter.visual(manifest, build, build, load_profile(self.config.profile_path))
        self.assertTrue(result.passed)
        self.assertFalse((workspace / "renders" / "stale.txt").exists())
        renders = commands[1:]
        self.assertEqual(len(renders), 2)
        self.assertEqual([len([v for v in command if v == "--before"]) for command in renders], [1, 1])
        self.assertTrue(renders[0][renders[0].index("--before") + 1].endswith("mesh.smd"))
        self.assertTrue(renders[1][renders[1].index("--before") + 1].endswith("lod.smd"))
        self.assertTrue(renders[1][renders[1].index("--after") + 1].endswith("lod_OPT.smd"))
        self.assertEqual(rendered_region_sources, [{"mesh.smd"}, {"lod.smd"}])
        self.assertNotEqual(
            renders[0][renders[0].index("--region-manifest") + 1],
            renders[1][renders[1].index("--region-manifest") + 1],
        )
        for render in renders:
            self.assertTrue(
                render[render.index("--source-root") + 1].endswith("render-source")
            )
            self.assertEqual(render[render.index("--poses") + 1], "bind:0,representative:10")
            self.assertTrue(render[render.index("--animation-before") + 1].endswith("anim.smd"))
            self.assertTrue(render[render.index("--animation-after") + 1].endswith("anim.smd"))
        classification_path = workspace / "logs" / "render-animation-classification.json"
        self.assertEqual(
            json.loads(classification_path.read_text(encoding="utf-8")),
            {
                "schema": 1,
                "required": True,
                "reason": "deformable-with-representative-animation",
                "selected_frame": 10,
                "original_source_identity": "anim.smd",
                "candidate_source_identity": "anim.smd",
            },
        )

        (self.root / "render_previews.py").write_text("# renderer", encoding="utf-8")
        adapter.bind_candidate_cache_digest(build, "a" * 64)
        with (
            patch("maximum_optimizer.orchestrator.run_process", side_effect=runner),
            patch("maximum_optimizer.orchestrator.compare_render_sets", return_value=ValidationResult(True)),
        ):
            focused_whole = adapter.visual(
                manifest, build, build, load_profile(self.config.profile_path),
                focused_profile=load_profile(self.config.profile_path),
            )
        self.assertTrue(focused_whole.passed)
        whole_index_path = workspace / "logs/whole-visual-index.json"
        self.assertTrue(whole_index_path.is_file())
        seal = adapter._whole_index_seals[workspace.resolve()]
        loaded_index = orchestrator_module._load_whole_visual_index(
            whole_index_path, workspace, seal, threading.Event()
        )
        self.assertEqual(len(loaded_index["states"]), 2)

        def no_fresh_output(command, **kwargs):
            if "--python-expr" in command:
                write_full_region_manifest()
            return ProcessResult(tuple(str(item) for item in command), 0, 0.01, kwargs["log_path"])
        with patch("maximum_optimizer.orchestrator.run_process", side_effect=no_fresh_output):
            with self.assertRaisesRegex(CandidateBuildError, "fresh manifest"):
                adapter.visual(manifest, build, build, load_profile(self.config.profile_path))

        qc.write_text(
            '$modelname "test.mdl"\n$body "body" "../mesh_OPT.smd"\n'
            '$lod 10 { replacemodel "../mesh_OPT.smd" "../lod_OPT.smd" }\n',
            encoding="utf-8",
        )
        with patch("maximum_optimizer.orchestrator.run_process", side_effect=no_fresh_output):
            unavailable = adapter.visual(manifest, build, build, load_profile(self.config.profile_path))
        self.assertFalse(unavailable.passed)
        self.assertEqual(unavailable.failures[0].gate, "representative-animation-unavailable")
        self.assertEqual(
            json.loads(classification_path.read_text(encoding="utf-8")),
            {
                "schema": 1,
                "required": True,
                "reason": "representative-animation-unavailable",
                "selected_frame": None,
                "original_source_identity": None,
                "candidate_source_identity": None,
            },
        )

    def test_production_visual_rigid_time_zero_sequence_renders_bind_only_and_records_reason(self):
        source = self.root / "rigid-production-source"
        source.mkdir()
        rigid_smd = (
            "version 1\nnodes\n0 \"root\" -1\nend\n"
            "skeleton\ntime 0\n0 0 0 0 0 0 0\nend\n"
            "triangles\nmaterial/base\n"
            "0 0 0 0 0 0 1 0 0\n0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\nend\n"
        )
        animation_smd = (
            "version 1\nnodes\n0 \"root\" -1\nend\n"
            "skeleton\ntime 0\n0 0 0 0 0 0 0\nend\ntriangles\nend\n"
        )
        (source / "mesh.smd").write_text(rigid_smd, encoding="utf-8")
        (source / "anim.smd").write_text(animation_smd, encoding="utf-8")
        (source / "main.qc").write_text(
            '$modelname "rigid.mdl"\n$body "body" "mesh.smd"\n$sequence "idle" "anim.smd"\n',
            encoding="utf-8",
        )
        fp = StructuralFingerprint(
            "rigid.mdl", ("body",), (), (), ("root",), (), (), (), ("idle",),
            ("mesh.smd",), (), None,
        )
        manifest = FamilyManifest(
            "rigid-production-family", "rigid.mdl", source, self.addon / "models", fp,
            "c" * 64, (".mdl",),
        )
        workspace = self.root / "rigid-production-candidate"
        candidate_source = workspace / "src"
        candidate_source.mkdir(parents=True)
        (workspace / "logs").mkdir()
        (candidate_source / "mesh_OPT.smd").write_text(rigid_smd, encoding="utf-8")
        (candidate_source / "anim.smd").write_text(animation_smd, encoding="utf-8")
        qc = candidate_source / "main_OPT.qc"
        qc.write_text(
            '$modelname "rigid.mdl"\n$body "body" "mesh_OPT.smd"\n$sequence "idle" "anim.smd"\n',
            encoding="utf-8",
        )
        compiled = workspace / "compiled"
        compiled.mkdir()
        build = CandidateBuild(
            CandidateSpec("rigid-production", "blender", 0.5, 0.0, "test"),
            workspace, qc, compiled, {}, {"rigid.mdl": "candidate-compile"}, (),
        )
        region_manifest = build_region_manifest((("mesh.smd", "Body", ("material/base",)),))
        commands = []

        def runner(command, **kwargs):
            command = tuple(str(item) for item in command)
            commands.append(command)
            if "--python-expr" in command:
                (workspace / "render-source" / "maximum_region_manifest.json").write_text(
                    canonical_json(region_manifest.to_payload()) + "\n",
                    encoding="utf-8",
                )
            else:
                out = Path(command[command.index("--out") + 1])
                for side in ("original", "optimized"):
                    (out / side).mkdir(parents=True)
                    (out / side / "render_manifest.json").write_text("{}", encoding="utf-8")
            return ProcessResult(command, 0, 0.01, kwargs["log_path"])

        adapter = ProductionAdapters(self.config, threading.Event())
        with (
            patch("maximum_optimizer.orchestrator.run_process", side_effect=runner),
            patch("maximum_optimizer.orchestrator.compare_render_sets", return_value=ValidationResult(True)),
        ):
            result = adapter.visual(manifest, build, build, load_profile(self.config.profile_path))

        self.assertTrue(result.passed)
        render = commands[1]
        self.assertEqual(render[render.index("--poses") + 1], "bind:0")
        self.assertNotIn("--animation-before", render)
        self.assertNotIn("--animation-after", render)
        self.assertEqual(
            json.loads((workspace / "logs" / "render-animation-classification.json").read_text()),
            {
                "schema": 1,
                "required": False,
                "reason": "rigid-or-bind-only",
                "selected_frame": None,
                "original_source_identity": None,
                "candidate_source_identity": None,
            },
        )

    def test_visual_configurations_use_engine_default_and_bounded_one_at_a_time_bodygroups(self):
        source = self.root / "bodygroup-states"
        source.mkdir()
        for name in ("body.smd", "hood_closed.smd", "hood_open.smd", "wheel_a.smd", "wheel_b.smd"):
            (source / name).write_text("mesh", encoding="utf-8")
        qc = source / "car.qc"
        qc.write_text(
            '$body body "body.smd"\n'
            '$bodygroup hood { studio "hood_closed.smd" studio "hood_open.smd" }\n'
            '$bodygroup wheel { blank studio "wheel_a.smd" studio "wheel_b.smd" }\n',
            encoding="utf-8",
        )

        states = orchestrator_module._graph_visual_configurations(
            parse_qc_graph(qc, source), max_alternatives=2
        )

        self.assertEqual(states[0].name, "engine-default")
        self.assertRegex(states[1].name, r"bodygroup-hood-000-[0-9a-f]{8}-1")
        self.assertRegex(states[2].name, r"bodygroup-wheel-001-[0-9a-f]{8}-1")
        self.assertEqual(
            [[path.name for path in state.sources] for state in states],
            [
                ["body.smd", "hood_closed.smd"],
                ["body.smd", "hood_open.smd"],
                ["body.smd", "hood_closed.smd", "wheel_a.smd"],
            ],
        )
        self.assertEqual(states[0].bodygroup_indices, (("000:hood", 0), ("001:wheel", 0)))
        self.assertEqual(states[2].bodygroup_indices, (("000:hood", 0), ("001:wheel", 1)))

    def test_duplicate_bodygroup_names_remain_distinct_by_source_index(self):
        source = self.root / "duplicate-bodygroup-names"
        source.mkdir()
        for name in ("base.smd", "first.smd", "second.smd"):
            (source / name).write_text("mesh", encoding="utf-8")
        qc = source / "car.qc"
        qc.write_text(
            '$body body "base.smd"\n'
            '$bodygroup part { blank studio "first.smd" }\n'
            '$bodygroup part { blank studio "second.smd" }\n', encoding="utf-8"
        )
        states = orchestrator_module._graph_visual_configurations(
            parse_qc_graph(qc, source), max_alternatives=2
        )
        self.assertEqual(states[0].bodygroup_indices, (("000:part", 0), ("001:part", 0)))
        self.assertEqual(states[1].bodygroup_indices, (("000:part", 1), ("001:part", 0)))
        self.assertEqual(states[2].bodygroup_indices, (("000:part", 0), ("001:part", 1)))

        candidate = self.root / "duplicate-bodygroup-candidate"
        (candidate / "output").mkdir(parents=True)
        for name in ("base", "first", "second"):
            (candidate / "output" / f"{name}_OPT.smd").write_text("mesh", encoding="utf-8")
        candidate_qc = candidate / "car_OPT.qc"
        candidate_qc.write_text(
            '$body body "output/base_OPT.smd"\n'
            '$bodygroup part { blank studio "output/second_OPT.smd" }\n'
            '$bodygroup part { blank studio "output/first_OPT.smd" }\n', encoding="utf-8"
        )
        reordered = orchestrator_module._graph_visual_configurations(
            parse_qc_graph(candidate_qc, candidate), max_alternatives=2
        )
        with self.assertRaisesRegex(ValueError, "logical source identities"):
            orchestrator_module._validate_visual_configuration_pairing(states, reordered)

    def test_visual_state_names_disambiguate_sanitized_bodygroup_collisions(self):
        source = self.root / "colliding-bodygroup-states"
        source.mkdir()
        for name in ("base.smd", "one.smd", "two.smd"):
            (source / name).write_text("mesh", encoding="utf-8")
        qc = source / "car.qc"
        qc.write_text(
            '$body body "base.smd"\n'
            '$bodygroup "a b" { blank studio "one.smd" }\n'
            '$bodygroup "a-b" { blank studio "two.smd" }\n',
            encoding="utf-8",
        )

        states = orchestrator_module._graph_visual_configurations(
            parse_qc_graph(qc, source), max_alternatives=2
        )

        self.assertEqual(len({state.name for state in states}), 3)
        self.assertNotEqual(states[1].name, states[2].name)
        self.assertRegex(states[1].name, r"bodygroup-a-b-000-[0-9a-f]{8}-1")
        self.assertRegex(states[2].name, r"bodygroup-a-b-001-[0-9a-f]{8}-1")

    def test_visual_pairing_accepts_opt_paths_but_rejects_logical_source_reorder(self):
        original = self.root / "pair-original"
        candidate = self.root / "pair-candidate"
        (original / "models").mkdir(parents=True)
        (candidate / "models" / "output").mkdir(parents=True)
        for name in ("body.smd", "hood.smd"):
            (original / "models" / name).write_text("mesh", encoding="utf-8")
            (candidate / "models" / "output" / name.replace(".smd", "_OPT.smd")).write_text("mesh", encoding="utf-8")
        (original / "car.qc").write_text(
            '$body body "models/body.smd"\n$bodygroup hood { studio "models/hood.smd" }\n', encoding="utf-8"
        )
        candidate_qc = candidate / "car_OPT.qc"
        candidate_qc.write_text(
            '$body body "models/output/body_OPT.smd"\n'
            '$bodygroup hood { studio "models/output/hood_OPT.smd" }\n', encoding="utf-8"
        )
        before = orchestrator_module._graph_visual_configurations(
            parse_qc_graph(original / "car.qc", original)
        )
        after = orchestrator_module._graph_visual_configurations(
            parse_qc_graph(candidate_qc, candidate)
        )
        orchestrator_module._validate_visual_configuration_pairing(before, after)

        candidate_qc.write_text(
            '$body body "models/output/hood_OPT.smd"\n'
            '$bodygroup hood { studio "models/output/body_OPT.smd" }\n', encoding="utf-8"
        )
        reordered = orchestrator_module._graph_visual_configurations(
            parse_qc_graph(candidate_qc, candidate)
        )
        with self.assertRaisesRegex(ValueError, "logical source identities"):
            orchestrator_module._validate_visual_configuration_pairing(before, reordered)

    def test_production_material_roots_keep_addon_first_and_accept_explicit_overlays(self):
        addon_materials = self.addon / "materials"
        overlay = self.root / "framework-materials"
        addon_materials.mkdir(exist_ok=True)
        overlay.mkdir()
        adapter = ProductionAdapters(self.config, threading.Event())

        with patch.dict(os.environ, {"MAXIMUM_MATERIAL_ROOTS": str(overlay)}):
            roots = adapter._materials_roots()

        self.assertEqual(roots, (addon_materials.resolve(), overlay.resolve()))

    def test_default_schedule_prefers_calibrated_position_direct_engine(self):
        self.adapters.candidate_schedule = None
        report = self.run_optimizer()
        attempts = [
            attempt for attempt in report.families[0].attempts
            if attempt.candidate_id != "roundtrip-control"
        ]
        self.assertGreater(len(attempts), 1)
        self.assertEqual(attempts[0].engine, "meshoptimizer")
        self.assertEqual(attempts[0].candidate_id, "meshopt-position-e0005")
        self.assertEqual(attempts[1].engine, "meshoptimizer")

    def test_explicit_rnd_flag_wires_compiler_aware_blender_schedule(self):
        with patch.dict(os.environ, {"MAXIMUM_RND_BLENDER_ADAPTIVE": "1"}):
            schedule = orchestrator_module._default_schedule()
        adaptive = [item for item in schedule if item.strategy == "blender-adaptive-v1"]
        self.assertEqual(tuple(item.target_ratio for item in adaptive), (0.45, 0.4, 0.35, 0.3, 0.25))
        self.assertTrue(all(item.engine == "blender" for item in adaptive))

    def test_default_schedule_searches_position_direct_error_ladder(self):
        schedule = orchestrator_module._default_schedule()
        direct = [
            item for item in schedule
            if item.strategy == "meshopt-direct-position-v1"
        ]
        self.assertEqual(
            tuple(item.target_error for item in direct),
            (0.005, 0.00625, 0.0075, 0.009, 0.01, 0.015, 0.02),
        )
        self.assertTrue(all(item.target_ratio == 0.20 for item in direct))
        self.assertTrue(all(item.update_vertices is False for item in direct))

    def test_production_profile_is_calibrated_and_keeps_the_conservative_visual_frontier(self):
        profile = load_profile(
            Path(__file__).parents[2]
            / "maximum_optimizer"
            / "profiles"
            / "maximum-experimental-v1.json"
        )
        self.assertTrue(profile.calibrated)
        self.assertLessEqual(profile.limits["edge_error"], 0.105)
        self.assertLessEqual(profile.limits["silhouette_iou"], 0.0125)
        self.assertLessEqual(profile.limits["rgb_mae"], 0.0021)

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

    def test_existing_work_tree_internal_reparse_is_rejected_before_mutation(self):
        self.config.work_dir.mkdir(parents=True)
        unsafe = self.config.work_dir / "src"
        unsafe.mkdir()
        sentinel = unsafe / "keep.txt"
        sentinel.write_text("external", encoding="utf-8")
        original_is_reparse = __import__(
            "maximum_optimizer.orchestrator", fromlist=["_is_reparse"]
        )._is_reparse
        with patch(
            "maximum_optimizer.orchestrator._is_reparse",
            side_effect=lambda path: Path(path) == unsafe or original_is_reparse(Path(path)),
        ):
            with self.assertRaisesRegex(MaximumConfigError, "work.*symlink|work.*junction"):
                validate_run_paths(self.config, create=False)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "external")

    def test_existing_output_file_is_rejected_even_with_overwrite(self):
        output_file = self.root / "existing-output"
        output_file.write_bytes(b"not-a-directory")
        config = MaximumRunConfig(
            **{**self.config.to_kwargs(), "output_dir": output_file, "overwrite": True}
        )

        with self.assertRaisesRegex(MaximumConfigError, "output.*directory"):
            validate_run_paths(config, create=False)

        self.assertEqual(output_file.read_bytes(), b"not-a-directory")

    def test_chunked_hash_observes_cancellation_between_chunks(self):
        source = self.root / "large.bin"
        source.write_bytes(b"abcdefgh")

        class CancelAfterTwoChecks:
            calls = 0

            def is_set(self):
                self.calls += 1
                return self.calls >= 3

        with self.assertRaises(ProcessCancelledError):
            _sha256_file(source, CancelAfterTwoChecks(), chunk_size=2)

    def test_chunked_tree_copy_observes_cancellation_during_a_file(self):
        source = self.root / "copy-source"
        source.mkdir()
        (source / "large.bin").write_bytes(b"abcdefgh")
        destination = self.root / "copy-destination"

        class CancelAfterTwoChecks:
            calls = 0

            def is_set(self):
                self.calls += 1
                return self.calls >= 3

        with self.assertRaises(ProcessCancelledError):
            _copytree_cancellable(
                source,
                destination,
                CancelAfterTwoChecks(),
                chunk_size=2,
            )

        self.assertFalse((destination / "large.bin").exists())

    def test_cache_restore_uses_cancellable_chunked_copy(self):
        cache_entry = self.root / "cache-entry"
        payload = cache_entry / "payload"
        payload.mkdir(parents=True)
        (payload / "large.bin").write_bytes(b"abcdefgh")
        workspace = self.root / "restored-workspace"

        class CancelAfterTwoChecks:
            calls = 0

            def is_set(self):
                self.calls += 1
                return self.calls >= 3

        with self.assertRaises(ProcessCancelledError):
            _restore_cache_payload(
                cache_entry,
                workspace,
                CancelAfterTwoChecks(),
                chunk_size=2,
            )

        self.assertFalse(workspace.exists())

    def test_tree_manifest_observes_cancellation_while_hashing(self):
        tree = self.root / "manifest-tree"
        tree.mkdir()
        (tree / "large.bin").write_bytes(b"abcdefgh")

        class CancelAfterTwoChecks:
            calls = 0

            def is_set(self):
                self.calls += 1
                return self.calls >= 3

        with self.assertRaises(ProcessCancelledError):
            _tree_manifest(tree, CancelAfterTwoChecks(), chunk_size=2)

    def test_cache_integrity_verification_observes_cancel_event(self):
        entry = self.root / "verify-cache"
        payload = entry / "payload"
        payload.mkdir(parents=True)
        (payload / "maximum_cache_record.json").write_text("{}", encoding="utf-8")
        (payload / "large.bin").write_bytes(b"abcdefgh")
        _seal_cache_entry(entry)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ProcessCancelledError):
            _verify_cache_entry(entry, cancel)

    def test_dependency_proof_hashes_modern_blender_extensions(self):
        extension = (
            self.config.blender_path.parent
            / "5.0"
            / "extensions"
            / "user_default"
            / "source_tools"
            / "__init__.py"
        )
        extension.parent.mkdir(parents=True)
        extension.write_text("version = 1", encoding="utf-8")

        first = _dependency_proof(self.config)
        extension.write_text("version = 2", encoding="utf-8")
        second = _dependency_proof(self.config)

        labels = {entry["label"] for entry in first["files"]}
        self.assertTrue(any(label.startswith("blender_extension/") for label in labels))
        self.assertNotEqual(first["digest"], second["digest"])

    def test_dependency_proof_binds_vtfcmd_bytes_and_authorized_material_roots(self):
        vtfcmd = self.root / "VTFCmd.exe"; vtfcmd.write_bytes(b"version-1")
        materials = self.root / "external-materials"; materials.mkdir()
        with patch.dict(os.environ, {
            "VTFCMD": str(vtfcmd),
            "MAXIMUM_MATERIAL_ROOTS": str(materials),
        }, clear=False):
            first = _dependency_proof(self.config)
            vtfcmd.write_bytes(b"version-2")
            second = _dependency_proof(self.config)
        indexed = {entry["label"]: entry for entry in first["files"]}
        self.assertEqual(indexed["tool/vtfcmd"]["state"], "file")
        self.assertIn(str(materials.resolve()), first["material_roots"])
        self.assertNotEqual(first["digest"], second["digest"])

    def test_configured_vtfcmd_finds_wpf_versioned_tool_extraction(self):
        local = self.root / "local-app-data"
        expected = (
            local / "GmodAddonOptimizer" / "tools" / "VTFEdit" / "1"
            / "VTFEdit" / "VTFCmd.exe"
        )
        expected.parent.mkdir(parents=True)
        expected.write_bytes(b"vtfcmd")

        with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}, clear=False):
            os.environ.pop("VTFCMD", None)
            actual = orchestrator_module._configured_vtfcmd(self.config)

        self.assertEqual(actual, expected.resolve())

    def test_unknown_schedule_engine_is_rejected_before_candidate_build(self):
        self.adapters.candidate_schedule = lambda _manifest: (
            CandidateSpec("unknown-engine", "unknown", 0.5, 0.01, "test"),
        )

        report = self.run_optimizer()

        self.assertEqual(report.families[0].status, "failed")
        self.assertIn("schedule", report.families[0].reason)
        self.assertEqual(self.adapters.calls, [("test.mdl", "roundtrip-control")])

    def test_production_adapter_rejects_unknown_engine_explicitly(self):
        adapter = ProductionAdapters(self.config, threading.Event())
        spec = CandidateSpec("unknown-engine", "unknown", 0.5, 0.01, "test")

        with self.assertRaisesRegex(CandidateBuildError, "unknown candidate engine"):
            adapter.build(
                self.family,
                spec,
                self.root / "unknown-workspace",
                object(),
                threading.Event(),
            )

    def test_worst_scopes_only_claim_scope_proven_for_each_metric(self):
        structural = ValidationResult(
            False,
            failures=(GateFailure("surface_max", "nose", 2.0, 1.0, "too large"),),
            metrics={"surface_max": 2.0, "rgb_mae": 0.25},
            worst_scope="nose",
        )
        attempt = AttemptReport(
            "candidate",
            "blender",
            "rejected",
            None,
            structural,
            None,
            False,
            "",
            {},
        )

        metrics, scopes = _worst((attempt,))

        self.assertEqual(metrics, {"surface_max": 2.0, "rgb_mae": 0.25})
        self.assertEqual(scopes, {"surface_max": "nose"})


if __name__ == "__main__":
    unittest.main()
