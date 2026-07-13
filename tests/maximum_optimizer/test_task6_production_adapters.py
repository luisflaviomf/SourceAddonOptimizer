from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import render_previews

from maximum_optimizer.candidates import CandidateTools
from maximum_optimizer.candidates import CandidateBuild
from maximum_optimizer.composite import compose_candidate_sources
from maximum_optimizer.domain import CandidateSpec, CompileFileProof, FamilyManifest, StructuralFingerprint
from maximum_optimizer.processes import ProcessCancelledError, ProcessResult
from maximum_optimizer.orchestrator import ProductionAdapters as OrchestratorProductionAdapters
from maximum_optimizer.production_adapters import (
    AdaptiveDirectProductionBoundary,
    AdaptiveDirectCompileResult,
    SourceUnionRenderTools,
    SourceUnionPoseBinding,
    validate_source_union_cli_contract,
    validate_source_union_pose_bindings,
)
from tests.maximum_optimizer.test_task6_direct_compositor import DirectCompositorFixture, _smd
from maximum_optimizer.source_components import (
    build_source_component_manifest,
    source_component_manifest_from_payload,
    source_component_transfer_from_payload,
    validate_source_component_transfer_against_manifest,
)
from tests.maximum_optimizer.test_task6_source_union_comparator import (
    _contract as unused_contract,
    _write_union_side,
)
from maximum_optimizer.visual_validation import FidelityProfile, REQUIRED_METRICS, SourceUnionComparisonContract


def _fingerprint() -> StructuralFingerprint:
    return StructuralFingerprint("vehicles/test.mdl", (), (), (), (), (), (), (), (), (), (), None)


def _prefiltered_component_smds(*, missing_component: bool = False) -> tuple[bytes, bytes]:
    prefix = (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\n'
    )
    first = (
        "paint\n0 0 0 0 0 0 1 0 0\n0 1 0 0 0 0 1 1 0\n"
        "0 0 1 0 0 0 1 0 1\n"
    )
    first_connected = (
        "paint\n0 1 0 0 0 0 1 0 0\n0 1 1 0 0 0 1 1 0\n"
        "0 0 1 0 0 0 1 0 1\n"
    )
    degenerate_bridge = (
        "paint\n0 0 0 0 0 0 1 0 0\n0 10 0 0 0 0 1 1 0\n"
        "0 10 0 0 0 0 1 0 1\n"
    )
    second = (
        "paint\n0 10 0 0 0 0 1 0 0\n0 11 0 0 0 0 1 1 0\n"
        "0 10 1 0 0 0 1 0 1\n"
    )
    second_connected = (
        "paint\n0 11 0 0 0 0 1 0 0\n0 11 1 0 0 0 1 1 0\n"
        "0 10 1 0 0 0 1 0 1\n"
    )
    source = (
        prefix + first + first_connected + degenerate_bridge
        + second + second_connected + "end\n"
    ).encode()
    retained = first + (first_connected if missing_component else second)
    return source, (prefix + retained + "end\n").encode()


class CompileRunner:
    def __init__(
        self, model_rel: str, *, extra=False, missing=None, set_event=None,
        mutate_source: Path | None = None,
    ) -> None:
        self.model_rel = model_rel
        self.extra = extra
        self.missing = missing
        self.set_event = set_event
        self.mutate_source = mutate_source
        self.commands = []

    def __call__(self, command, cwd, log_path, cancel_event):
        command = tuple(str(item) for item in command)
        self.commands.append(command)
        out = Path(command[command.index("--out") + 1])
        model = out / "models" / Path(*self.model_rel.split("/"))
        model.parent.mkdir(parents=True, exist_ok=True)
        for suffix in (".mdl", ".vvd", ".dx90.vtx"):
            if suffix != self.missing:
                model.with_suffix(suffix).write_bytes((suffix + " current").encode())
        if self.extra:
            (out / "models" / "unrelated.bin").write_bytes(b"extra")
        (out / "compile_summary.json").write_text(json.dumps({
            "results": [{
                "model_rel": self.model_rel, "status": "ok", "returncode": 0,
                "expected_mdl": str(model),
            }],
        }), encoding="utf-8")
        if self.set_event is not None:
            self.set_event.set()
        if self.mutate_source is not None:
            data = self.mutate_source.read_bytes()
            self.mutate_source.write_bytes(bytes((data[0] ^ 1,)) + data[1:])
        return ProcessResult(command, 0, 0.01, Path(log_path))


class SourceUnionRunner:
    def __init__(
        self, fixture, *, malformed_visibility=False, missing_image=False,
        after_output=None, visibility_mutator=None, raw_mutator=None,
    ) -> None:
        self.fixture = fixture; self.malformed_visibility = malformed_visibility
        self.missing_image = missing_image; self.after_output = after_output
        self.visibility_mutator = visibility_mutator; self.raw_mutator = raw_mutator
        self.commands = []; self.contracts = []

    def __call__(self, command, cwd, log_path, cancel_event):
        command = tuple(str(item) for item in command); self.commands.append(command)
        raw = Path(command[command.index("--out") + 1])
        contract_path = Path(command[command.index("--source-union-contract") + 1])
        visibility_path = Path(command[command.index("--source-union-visibility-out") + 1])
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
        self.contracts.append(payload)
        comparison = payload["comparison_contract"]
        contract = SourceUnionComparisonContract(
            comparison["target_sha256"], comparison["source_identity"],
            comparison["source_coverage_sha256"], comparison["reference_source_sha256"],
            comparison["candidate_source_sha256"], comparison["material_contract_sha256"],
            tuple(tuple(item) for item in comparison["pose_frames"]),
            comparison["union_key"], comparison["contract_sha256"],
        )
        (raw / "original").mkdir(parents=True); (raw / "optimized").mkdir(parents=True)
        _write_union_side(raw / "original", "reference", contract)
        _write_union_side(raw / "optimized", "candidate", contract)
        if self.missing_image:
            next((raw / "optimized").rglob("*.png")).unlink()
        if self.raw_mutator is not None:
            self.raw_mutator(raw, payload)
        observations = []
        for side in ("candidate", "reference"):
            for component in payload["component_keys"]:
                for camera in payload["cameras"]:
                    observations.append({
                        "side": side, "component_key": component, "pose_key": "bind",
                        "camera_key": camera,
                        "visible_mask_pixels": 3 if camera == "camera-00" else 0,
                    })
        visibility = {
            "schema": 1, "kind": "adaptive-direct-source-union-visibility-v1",
            "target_sha256": payload["target_sha256"],
            "source_identity": payload["source_identity"],
            "source_coverage_sha256": payload["source_coverage_sha256"],
            "cameras": payload["cameras"], "pose_keys": ["bind"],
            "observations": observations,
        }
        if self.visibility_mutator is not None:
            self.visibility_mutator(visibility)
        visibility["evidence_sha256"] = hashlib.sha256(
            json.dumps(visibility, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if self.malformed_visibility: visibility["extra"] = True
        visibility_path.write_text(json.dumps(visibility), encoding="utf-8")
        if self.after_output is not None:
            self.after_output(command, payload, cancel_event)
        return ProcessResult(command, 0, 0.01, Path(log_path))


class ProductionAdapterContractTests(unittest.TestCase):
    def _render_fixture(self, root: Path, *, missing_component: bool = False):
        source_bytes, candidate_bytes = _prefiltered_component_smds(
            missing_component=missing_component
        )
        component_manifest = build_source_component_manifest(source_bytes)
        return DirectCompositorFixture(
            root,
            components=tuple(item.component_key for item in component_manifest.components),
            component_manifest_sha256=component_manifest.component_manifest_sha256,
            visual_source_bytes=source_bytes,
            direct_output_bytes=candidate_bytes,
        ), component_manifest

    def _render_case(
        self, root: Path, runner, workspace: Path, component_manifest, *,
        dependency_provider=None, base_build=None, candidate_transform=None,
        event=None, source_proof=None, snapshot=None, tools_texture_cache=None,
    ):
        fixture = runner.fixture
        spec = CandidateSpec(
            "recovery-" + fixture.recipe.recipe_sha256,
            "blender", fixture.recipe.direct_ratio, 0.0, "blender-adaptive-v1",
            strategy="blender-adaptive-v1", transfer="blender-native-v1",
            composite_recipe=fixture.recipe,
        )
        compiled = root / "compiled" / "models"; compiled.mkdir(parents=True, exist_ok=True)
        artifact = compiled / "task6.mdl"; artifact.write_bytes(b"compiled model")
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        candidate = CandidateBuild(
            spec, root / "candidate", fixture.base_root / "main.qc", compiled,
            {"status": "ok", "returncode": 0},
            {"task6.mdl": "candidate-compile"}, (), None,
        )
        compile_result = AdaptiveDirectCompileResult.create(
            candidate, (CompileFileProof(
                "task6.mdl", ".mdl", len(artifact.read_bytes()), artifact_hash,
            ),), "a" * 64,
        )
        if candidate_transform is not None:
            compile_result = replace(
                compile_result, build=candidate_transform(compile_result.build)
            )
        manifest = FamilyManifest(
            fixture.base_snapshot.family_id, "task6.mdl", fixture.base_root,
            root, _fingerprint(), fixture.base_snapshot.family_input_sha256,
            (".mdl",),
        )
        renderer_script = root / "renderer.py"
        if not renderer_script.exists(): renderer_script.write_bytes(b"# source union renderer\n")
        blender = root / "blender.exe"
        if not blender.exists(): blender.write_bytes(b"blender")
        tools = SourceUnionRenderTools(
            blender, renderer_script, hashlib.sha256(renderer_script.read_bytes()).hexdigest(),
            texture_cache=tools_texture_cache,
            dependency_digest_provider=dependency_provider or (
                lambda _event: fixture.requests[0].dependency_proof_sha256
            ),
        )
        source = source_proof or next(
            item for item in fixture.coverage.sources
            if item.source_identity == fixture.requests[0].source_identity
        )
        profile = FidelityProfile(1, "union", True, "f" * 64, {metric: 1.0 for metric in REQUIRED_METRICS})
        return AdaptiveDirectProductionBoundary(process_runner=runner).render_adaptive_direct_source_union(
            manifest=manifest, base_build=base_build or fixture.base_build,
            candidate_compile=compile_result,
            coverage=fixture.coverage, source_proof=source,
            snapshot=snapshot or fixture.snapshots[0],
            component_manifest=component_manifest,
            profile=profile, tools=tools, workspace=workspace,
            cancel_event=event or threading.Event(),
        )

    def _compile_case(
        self, root: Path, runner, *, event=None, mutate_composed=None,
        mutate_manifest=None,
    ):
        fixture = DirectCompositorFixture(root)
        composed = compose_candidate_sources(
            fixture.base_build, fixture.recipe, fixture.resolver,
            root / "composed", None, coverage_manifest=fixture.coverage,
        )
        if mutate_composed is not None:
            mutate_composed(composed)
        repo = root / "repo"; repo.mkdir()
        (repo / "batch_compile_opt_qc.py").write_text("# compiler", encoding="utf-8")
        tools_paths = []
        for name in ("python.exe", "blender.exe", "studiomdl.exe"):
            path = root / name; path.write_bytes(b"tool"); tools_paths.append(path)
        tools = CandidateTools(tools_paths[0], tools_paths[1], tools_paths[2], repo)
        spec = CandidateSpec(
            "recovery-" + fixture.recipe.recipe_sha256,
            "blender", fixture.recipe.direct_ratio, 0.0, "blender-adaptive-v1",
            strategy="blender-adaptive-v1", transfer="blender-native-v1",
            composite_recipe=fixture.recipe,
        )
        manifest = FamilyManifest(
            fixture.base_snapshot.family_id, "task6.mdl", fixture.base_root,
            root, _fingerprint(), fixture.base_snapshot.family_input_sha256,
            (".mdl", ".vvd", ".vtx"),
        )
        if mutate_manifest is not None:
            manifest = mutate_manifest(manifest)
        result = AdaptiveDirectProductionBoundary(process_runner=runner).compile_adaptive_direct_candidate(
            manifest=manifest, spec=spec, composed=composed, tools=tools,
            cancel_event=event or threading.Event(),
        )
        return result, composed, tools

    def test_source_union_cli_is_discriminated_and_has_no_state_selectors(self) -> None:
        args = render_previews._parse_args([
            "--before", "reference.smd", "--after", "candidate.smd", "--out", "raw",
            "--passes", "textured,clay", "--poses", "bind:0",
            "--source-union-contract", "contract.json",
            "--source-union-visibility-out", "visibility.json",
        ])
        validate_source_union_cli_contract(args)

    def test_real_orchestrator_wrapper_replaces_untrusted_dependency_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            blender = root / "caller-blender.exe"; blender.write_bytes(b"caller")
            renderer = root / "caller-renderer.py"; renderer.write_bytes(b"caller")
            authoritative_blender = root / "real-blender.exe"; authoritative_blender.write_bytes(b"real")
            repo = root / "repo"; repo.mkdir()
            authoritative_renderer = repo / "render_previews.py"; authoritative_renderer.write_bytes(b"real renderer")
            materials = root / "materials"; materials.mkdir()
            vtfcmd = root / "VTFCmd.exe"; vtfcmd.write_bytes(b"vtfcmd")
            texture_cache = root / "texture-cache"; texture_cache.mkdir()
            (texture_cache / "poison.png").write_bytes(b"fake cached png")
            caller = mock.Mock(side_effect=AssertionError("caller provider must not run"))
            tools = SourceUnionRenderTools(
                blender, renderer, hashlib.sha256(renderer.read_bytes()).hexdigest(),
                dependency_digest_provider=caller,
            )
            adapter = object.__new__(OrchestratorProductionAdapters)
            adapter.config = mock.Mock(
                blender_path=authoritative_blender,
                repo_root=repo,
            )
            adapter.cancel_event = threading.Event()
            expected = object()
            with mock.patch(
                "maximum_optimizer.orchestrator._dependency_proof",
                return_value={"digest": "8" * 64},
            ) as proof, mock.patch.object(
                AdaptiveDirectProductionBoundary,
                "render_adaptive_direct_source_union",
                return_value=expected,
            ) as boundary, mock.patch(
                "maximum_optimizer.orchestrator.FocusedRenderCache",
                side_effect=AssertionError("source union must never access focused cache"),
            ) as focused_cache, mock.patch.object(
                adapter, "_materials_roots", return_value=(materials,),
            ), mock.patch.object(
                adapter, "_vtfcmd", return_value=vtfcmd,
            ):
                compile_result = mock.Mock()
                compile_result.build.workspace = root / "candidate"
                self.assertIs(
                    adapter.render_adaptive_direct_source_union(
                        tools=tools, candidate_compile=compile_result,
                    ), expected
                )
                passed = boundary.call_args.kwargs["tools"]
                self.assertEqual(passed.blender_exe, authoritative_blender)
                self.assertEqual(passed.renderer_script, authoritative_renderer)
                self.assertEqual(passed.materials_roots, (materials,))
                self.assertEqual(passed.vtfcmd, vtfcmd)
                self.assertIsNone(passed.texture_cache)
                self.assertEqual(
                    passed.dependency_digest_provider(adapter.cancel_event), "8" * 64
                )
                caller.assert_not_called()
                proof.assert_called_once_with(adapter.config, adapter.cancel_event)
                focused_cache.assert_not_called()
        with mock.patch.object(render_previews.sys, "argv", [
            "render_previews.py", "--before", "reference.smd", "--after", "candidate.smd",
            "--out", "raw", "--passes", "textured,clay", "--poses", "bind:0",
            "--source-union-contract", "contract.json",
            "--source-union-visibility-out", "visibility.json",
        ]):
            with self.assertRaisesRegex(SystemExit, "source-union renderer unavailable"):
                render_previews.main()
        for forbidden in (
            ["--configuration-manifest", "config.json"],
            ["--focus-region", "r-" + "a" * 64],
            ["--aggregate-regions"],
        ):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                changed = render_previews._parse_args([
                    "--before", "reference.smd", "--after", "candidate.smd", "--out", "raw",
                    "--passes", "textured,clay", "--poses", "bind:0",
                    "--source-union-contract", "contract.json",
                    "--source-union-visibility-out", "visibility.json", *forbidden,
                ])
                validate_source_union_cli_contract(changed)
        with self.assertRaises(ValueError):
            validate_source_union_cli_contract(render_previews._parse_args([
                "--before", "r.smd", "--after", "c.smd", "--out", "raw",
                "--passes", "clay,textured", "--poses", "bind:0",
                "--source-union-contract", "contract.json",
                "--source-union-visibility-out", "visibility.json",
            ]))
        for poses, angles in (
            ("bind:0,garbage", "front,back,left,right,top,bottom,iso1,iso2"),
            ("bind:0,bind:0", "front,back,left,right,top,bottom,iso1,iso2"),
            ("bind:0,turn:12", "front,back,left,right,top,bottom,iso1,iso2"),
            ("bind:0", "wrong"),
        ):
            with self.subTest(poses=poses, angles=angles), self.assertRaises(ValueError):
                validate_source_union_cli_contract(render_previews._parse_args([
                    "--before", "r.smd", "--after", "c.smd", "--out", "raw",
                    "--passes", "textured,clay", "--poses", poses, "--angles", angles,
                    "--source-union-contract", "contract.json",
                    "--source-union-visibility-out", "visibility.json",
                ]))

    def test_pose_binding_accepts_bind_and_fails_closed_for_unsealed_anchor(self) -> None:
        bind = SourceUnionPoseBinding.bind("6" * 64)
        self.assertEqual((bind.pose_key, bind.frame), ("bind", 0))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            before = root / "before.smd"; after = root / "after.smd"
            animation = (
                'version 1\nnodes\n0 "root" -1\nend\nskeleton\n'
                'time 0\n0 0 0 0 0 0 0\ntime 12\n0 0 0 0 0 0 0\nend\n'
            ).encode()
            before.write_bytes(animation); after.write_bytes(animation)
            with self.assertRaises(ValueError):
                SourceUnionPoseBinding.anchor(
                    "turn", 12, before, after,
                    hashlib.sha256(before.read_bytes()).hexdigest(),
                    hashlib.sha256(after.read_bytes()).hexdigest(),
                    "6" * 64,
                )
            validate_source_union_pose_bindings(
                (bind,), ("bind",), "6" * 64, threading.Event()
            )
        with self.assertRaises(ValueError):
            SourceUnionPoseBinding("turn", 12, None, None, None, None, "6" * 64)
        with self.assertRaises(ValueError):
            SourceUnionPoseBinding.anchor(
                "turn", 0, Path("C:/before.smd"), Path("C:/after.smd"),
                "a" * 64, "b" * 64, "6" * 64,
            )

    def test_compile_adapter_runs_only_compiler_and_proves_current_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture = DirectCompositorFixture(root)
            composed = compose_candidate_sources(
                fixture.base_build, fixture.recipe, fixture.resolver,
                root / "composed", None, coverage_manifest=fixture.coverage,
            )
            repo = root / "repo"; repo.mkdir()
            (repo / "batch_compile_opt_qc.py").write_text("# compiler", encoding="utf-8")
            tools_paths = []
            for name in ("python.exe", "blender.exe", "studiomdl.exe"):
                path = root / name; path.write_bytes(b"tool"); tools_paths.append(path)
            tools = CandidateTools(tools_paths[0], tools_paths[1], tools_paths[2], repo)
            spec = CandidateSpec(
                "recovery-" + fixture.recipe.recipe_sha256,
                "blender", fixture.recipe.direct_ratio, 0.0, "blender-adaptive-v1",
                strategy="blender-adaptive-v1", transfer="blender-native-v1",
                composite_recipe=fixture.recipe,
            )
            manifest = FamilyManifest(
                fixture.base_snapshot.family_id, "task6.mdl", fixture.base_root,
                root, _fingerprint(), fixture.base_snapshot.family_input_sha256,
                (".mdl", ".vvd", ".vtx"),
            )
            runner = CompileRunner(manifest.model_rel)
            result = AdaptiveDirectProductionBoundary(process_runner=runner).compile_adaptive_direct_candidate(
                manifest=manifest, spec=spec, composed=composed, tools=tools,
                cancel_event=threading.Event(),
            )
            self.assertEqual(len(runner.commands), 1)
            self.assertIn("batch_compile_opt_qc.py", runner.commands[0][1])
            self.assertNotIn(str(tools.blender_exe), runner.commands[0])
            self.assertEqual(runner.commands[0][-1], "--no-restore-phy")
            self.assertEqual(tuple(item.relative_path for item in result.compile_files), (
                "task6.dx90.vtx", "task6.mdl", "task6.vvd",
            ))
            self.assertEqual(result.build.spec, spec)
            self.assertEqual(
                result.build.compile_record.get("composition_evidence_sha256"), None
            )
            self.assertEqual(
                result.composition_evidence_sha256,
                composed.composition.evidence_sha256,
            )
            with self.assertRaisesRegex(ValueError, "seal"):
                replace(result, composition_evidence_sha256="0" * 64)
            rerooted = AdaptiveDirectCompileResult.create(
                replace(
                    result.build,
                    workspace=root / "rerooted",
                    optimized_qc=root / "rerooted" / "src" / "main.qc",
                    compiled_models_dir=root / "rerooted" / "compiled" / "models",
                ),
                result.compile_files,
                result.composition_evidence_sha256,
            )
            self.assertEqual(rerooted.result_sha256, result.result_sha256)

    def test_compile_adapter_rejects_extra_artifact_and_wrong_composition_before_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runner = CompileRunner("task6.mdl", extra=True)
            with self.assertRaises(ValueError):
                self._compile_case(root, runner)
            self.assertEqual(len(runner.commands), 1)
            self.assertFalse((root / "composed" / "compiled").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_runner = CompileRunner("task6.mdl")

            # Obtain the deterministic composed source path through the same helper
            # shape, then let the runner mutate it same-size before returning.
            def attach_mutator(composed):
                fixture_runner.mutate_source = composed.workspace / "src/meshes/part-00.smd"

            with self.assertRaises(ValueError):
                self._compile_case(root, fixture_runner, mutate_composed=attach_mutator)
            self.assertEqual(len(fixture_runner.commands), 1)
            self.assertFalse((root / "composed" / "compiled").exists())
            self.assertFalse((root / "composed" / "logs").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            event = threading.Event()
            runner = CompileRunner("task6.mdl", set_event=event)
            with self.assertRaises(ProcessCancelledError):
                self._compile_case(root, runner, event=event)
            self.assertEqual(len(runner.commands), 1)
            self.assertFalse((root / "composed" / "compiled").exists())
            self.assertFalse((root / "composed" / "logs").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runner = CompileRunner("task6.mdl", missing=".vvd")
            with self.assertRaises(Exception):
                self._compile_case(root, runner)
            self.assertFalse((root / "composed" / "compiled").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runner = CompileRunner("task6.mdl")
            event = threading.Event(); event.set()
            with self.assertRaises(Exception):
                self._compile_case(root, runner, event=event)
            self.assertEqual(runner.commands, [])

    def test_source_union_adapter_is_fresh_private_canonical_and_e1_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, component_manifest = self._render_fixture(root)
            runner = SourceUnionRunner(fixture)
            first = self._render_case(root, runner, root / "union-one", component_manifest)
            second = self._render_case(root, runner, root / "union-two", component_manifest)
            self.assertTrue(first.validation.passed)
            self.assertEqual(first.evidence_sha256, second.evidence_sha256)
            self.assertEqual(len(runner.commands), 2)
            command = runner.commands[0]
            self.assertIn("--source-union-contract", command)
            self.assertNotIn("--configuration-manifest", command)
            self.assertNotIn("--focus-region", command)
            self.assertEqual(command[1:4], ("--background", "--python", command[3]))
            self.assertEqual(command[4], "--")
            self.assertEqual(command[command.index("--passes") + 1], "textured,clay")
            self.assertEqual(
                command[command.index("--angles") + 1],
                "front,back,left,right,top,bottom,iso1,iso2",
            )
            self.assertEqual(command[command.index("--poses") + 1], "bind:0")
            private_cache = Path(command[command.index("--texture-cache") + 1])
            self.assertTrue(str(private_cache).startswith(str(root / "union-one")))
            self.assertFalse(private_cache.exists())
            before = Path(command[command.index("--before") + 1])
            after = Path(command[command.index("--after") + 1])
            self.assertTrue(str(before).startswith(str(root / "union-one")))
            self.assertTrue(str(after).startswith(str(root / "union-one")))
            contract = runner.contracts[0]
            parsed_manifest = source_component_manifest_from_payload(
                contract["component_manifest"]
            )
            parsed_transfer = source_component_transfer_from_payload(
                contract["candidate_component_transfer"]
            )
            self.assertEqual(parsed_manifest, component_manifest)
            validate_source_component_transfer_against_manifest(
                parsed_transfer, parsed_manifest
            )
            self.assertEqual(
                hashlib.sha256(before.read_bytes()).hexdigest(),
                component_manifest.filtered_source_sha256,
            )
            self.assertNotEqual(
                component_manifest.filtered_source_sha256,
                component_manifest.source_sha256,
            )
            self.assertEqual(
                contract["comparison_contract"]["reference_source_sha256"],
                component_manifest.filtered_source_sha256,
            )

    def test_source_union_ignores_poisoned_shared_texture_cache_and_uses_fresh_private_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            shared = root / "shared-cache"; shared.mkdir()
            poison = shared / "poison.png"; poison.write_bytes(b"fake cached png")
            runner = SourceUnionRunner(fixture)
            self._render_case(
                root, runner, root / "union", components,
                tools_texture_cache=shared,
            )
            command = runner.commands[0]
            private_cache = Path(command[command.index("--texture-cache") + 1])
            self.assertNotEqual(private_cache, shared)
            self.assertFalse(private_cache.exists())
            self.assertEqual(poison.read_bytes(), b"fake cached png")

    def test_source_union_adapter_rejects_wrong_base_or_candidate_before_runner(self) -> None:
        for label in ("base", "candidate"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture, component_manifest = self._render_fixture(root)
                runner = SourceUnionRunner(fixture)
                base_build = None
                candidate_change = None
                if label == "base":
                    base_build = replace(
                        fixture.base_build,
                        spec=replace(fixture.base_build.spec, candidate_id="wrong-base"),
                    )
                else:
                    candidate_change = lambda build: replace(
                        build, spec=fixture.base_build.spec
                    )
                with self.assertRaisesRegex(ValueError, "binding|base|candidate|seal"):
                    self._render_case(
                        root, runner, root / "union", component_manifest,
                        base_build=base_build,
                        candidate_transform=candidate_change,
                    )
                self.assertEqual(runner.commands, [])

    def test_source_union_adapter_rejects_malformed_visibility_and_raw_gap_with_cleanup(self) -> None:
        for malformed, missing in ((True, False), (False, True)):
            with self.subTest(malformed=malformed, missing=missing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); fixture, component_manifest = self._render_fixture(root)
                runner = SourceUnionRunner(
                    fixture, malformed_visibility=malformed, missing_image=missing
                )
                workspace = root / "union"
                with self.assertRaises(ValueError):
                    self._render_case(root, runner, workspace, component_manifest)
                self.assertFalse(workspace.exists())

    def test_source_union_visibility_parser_rejects_hostile_resealed_payloads(self) -> None:
        def missing(value): value["observations"].pop()
        def extra(value): value["observations"].append(dict(value["observations"][0]))
        def duplicate(value): value["observations"][1] = dict(value["observations"][0])
        def reordered(value): value["observations"][0], value["observations"][1] = value["observations"][1], value["observations"][0]
        def boolean_schema(value): value["schema"] = True
        def boolean_pixels(value): value["observations"][0]["visible_mask_pixels"] = True
        def negative(value): value["observations"][0]["visible_mask_pixels"] = -1
        def candidate_occluded(value):
            component = value["observations"][0]["component_key"]
            for item in value["observations"]:
                if item["side"] == "candidate" and item["component_key"] == component:
                    item["visible_mask_pixels"] = 0
        for label, mutation in (
            ("missing", missing), ("extra", extra), ("duplicate", duplicate),
            ("reordered", reordered), ("boolean-schema", boolean_schema),
            ("boolean-pixels", boolean_pixels), ("negative", negative),
            ("candidate-occluded", candidate_occluded),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
                runner = SourceUnionRunner(fixture, visibility_mutator=mutation)
                workspace = root / "union"
                with self.assertRaises(ValueError):
                    self._render_case(root, runner, workspace, components)
                self.assertFalse(workspace.exists())

    def test_source_union_raw_boundary_rejects_nested_extra_swapped_and_mutated_bytes(self) -> None:
        def nested_extra(raw, _payload):
            path = raw / "optimized" / "render_manifest.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["entries"][0]["hidden"] = True
            path.write_text(json.dumps(value), encoding="utf-8")
        def extra_file(raw, _payload): (raw / "optimized" / "extra.bin").write_bytes(b"x")
        def jpeg(raw, _payload): next((raw / "optimized").rglob("*.png")).write_bytes(b"not a png")
        def same_size(raw, _payload):
            path = next((raw / "optimized").rglob("*.png")); data = bytearray(path.read_bytes())
            data[len(data) // 2] ^= 1; path.write_bytes(data)
        def swapped(raw, _payload):
            left = raw / "original" / "render_manifest.json"
            right = raw / "optimized" / "render_manifest.json"
            first, second = left.read_bytes(), right.read_bytes()
            left.write_bytes(second); right.write_bytes(first)
        def duplicate_entry(raw, _payload):
            path = raw / "optimized" / "render_manifest.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["entries"].append(dict(value["entries"][0]))
            path.write_text(json.dumps(value), encoding="utf-8")
        for label, mutation, structural in (
            ("nested-extra", nested_extra, False), ("extra-file", extra_file, True),
            ("jpeg", jpeg, True), ("same-size", same_size, True),
            ("swapped", swapped, False),
            ("duplicate-entry", duplicate_entry, True),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
                runner = SourceUnionRunner(fixture, raw_mutator=mutation)
                workspace = root / "union"
                if structural:
                    with self.assertRaises(ValueError):
                        self._render_case(root, runner, workspace, components)
                    self.assertFalse(workspace.exists())
                else:
                    record = self._render_case(root, runner, workspace, components)
                    self.assertFalse(record.validation.passed)

    def test_source_union_revalidates_dependency_and_all_current_bytes_after_process(self) -> None:
        def mutate_named(command, _payload, _event, flag):
            if flag == "source":
                path = next(item for item in command if item.endswith("reference.smd"))
                path = Path(path)
            elif flag == "candidate":
                path = next(item for item in command if item.endswith("candidate.smd"))
                path = Path(path)
            elif flag == "renderer":
                path = Path(command[command.index("--python") + 1])
            elif flag == "contract":
                path = Path(command[command.index("--source-union-contract") + 1])
            data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1; path.write_bytes(data)

        for flag in ("source", "candidate", "renderer", "contract"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
                runner = SourceUnionRunner(
                    fixture,
                    after_output=lambda command, payload, event, flag=flag: mutate_named(
                        command, payload, event, flag
                    ),
                )
                workspace = root / "union"
                with self.assertRaises(ValueError):
                    self._render_case(root, runner, workspace, components)
                self.assertFalse(workspace.exists())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            runner = SourceUnionRunner(fixture); workspace = root / "union"
            with self.assertRaisesRegex(ValueError, "dependency"):
                self._render_case(
                    root, runner, workspace, components,
                    dependency_provider=lambda _event: "0" * 64,
                )
            self.assertEqual(runner.commands, [])
            self.assertFalse(workspace.exists())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            current = {"digest": fixture.requests[0].dependency_proof_sha256}
            runner = SourceUnionRunner(
                fixture,
                after_output=lambda _command, _payload, _event: current.update(digest="0" * 64),
            )
            workspace = root / "union"
            with self.assertRaisesRegex(ValueError, "dependency"):
                self._render_case(
                    root, runner, workspace, components,
                    dependency_provider=lambda _event: current["digest"],
                )
            self.assertFalse(workspace.exists())

    def test_source_union_process_cannot_add_private_control_or_raw_root_files(self) -> None:
        for location in ("inputs", "control", "raw"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
                def poison(command, _payload, _event, location=location):
                    output_root = Path(command[command.index("--out") + 1]).parent
                    (output_root / location / "hidden.bin").write_bytes(b"hidden")
                workspace = root / "union"
                with self.assertRaises(ValueError):
                    self._render_case(
                        root, SourceUnionRunner(fixture, after_output=poison),
                        workspace, components,
                    )
                self.assertFalse(workspace.exists())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            def poison_top(command, _payload, _event):
                (Path(command[command.index("--out") + 1]).parent / "hidden-top.bin").write_bytes(b"hidden")
            workspace = root / "union"
            with self.assertRaises(ValueError):
                self._render_case(
                    root, SourceUnionRunner(fixture, after_output=poison_top),
                    workspace, components,
                )
            self.assertFalse(workspace.exists())

    def test_source_union_never_deletes_preexisting_unowned_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            workspace = root / "union"; workspace.mkdir()
            marker = workspace / "external.marker"; marker.write_bytes(b"preserve")
            runner = SourceUnionRunner(fixture)
            with self.assertRaisesRegex(ValueError, "workspace"):
                self._render_case(root, runner, workspace, components)
            self.assertEqual(marker.read_bytes(), b"preserve")
            self.assertEqual(runner.commands, [])

    @unittest.skipUnless(os.name == "nt", "Windows junction semantics")
    def test_source_union_rejects_compiled_root_junction_and_preserves_external_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            junction_holder = {"path": None}
            external = root / "external-compiled"; external.mkdir()
            marker = external / "external.marker"; marker.write_bytes(b"preserve")
            def junction_compile(build):
                lexical = root / "compiled" / "models"
                artifact = lexical / "task6.mdl"
                (external / "task6.mdl").write_bytes(artifact.read_bytes())
                shutil.rmtree(lexical)
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(lexical), str(external)],
                    capture_output=True, text=True,
                )
                if created.returncode != 0:
                    self.skipTest(f"junction creation unavailable: {created.stderr or created.stdout}")
                junction_holder["path"] = lexical
                return build
            workspace = root / "union"; runner = SourceUnionRunner(fixture)
            try:
                with self.assertRaisesRegex(ValueError, "compiled|reparse"):
                    self._render_case(
                        root, runner, workspace, components,
                        candidate_transform=junction_compile,
                    )
                self.assertEqual(runner.commands, [])
                self.assertEqual(marker.read_bytes(), b"preserve")
            finally:
                junction = junction_holder["path"]
                if junction is not None and os.path.lexists(junction):
                    os.rmdir(junction)

    def test_source_union_rejects_precreated_authorized_and_reparse_output(self) -> None:
        def precreate(command, _payload, _event):
            (Path(command[command.index("--out") + 1]).parent / "authorized").mkdir()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            workspace = root / "union"
            with self.assertRaisesRegex(ValueError, "authorized"):
                self._render_case(
                    root, SourceUnionRunner(fixture, after_output=precreate),
                    workspace, components,
                )
            self.assertFalse(workspace.exists())

        for reparse_name in ("inputs", "control", "raw"):
            with self.subTest(reparse=reparse_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
                marker = {"process_done": False}
                runner = SourceUnionRunner(
                    fixture,
                    after_output=lambda _command, _payload, _event: marker.update(process_done=True),
                )
                original = __import__(
                    "maximum_optimizer.production_adapters", fromlist=["_has_reparse_ancestor"]
                )._has_reparse_ancestor
                def hostile(path):
                    return (
                        marker["process_done"] and Path(path).name == reparse_name
                    ) or original(path)
                workspace = root / "union"
                with mock.patch(
                    "maximum_optimizer.production_adapters._has_reparse_ancestor",
                    side_effect=hostile,
                ), self.assertRaisesRegex(ValueError, "reparse"):
                    self._render_case(root, runner, workspace, components)
                self.assertFalse(workspace.exists())

    def test_source_union_rejects_wrong_component_source_snapshot_and_stale_compile_preprocess(self) -> None:
        for label in ("component", "snapshot", "source-proof", "compile"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
                runner = SourceUnionRunner(fixture)
                selected_components = components
                selected_snapshot = None
                selected_proof = None
                candidate_transform = None
                if label == "component":
                    selected_components = build_source_component_manifest(_smd(8))
                elif label == "snapshot":
                    selected_snapshot = fixture.snapshots[1]
                elif label == "source-proof":
                    selected_proof = next(
                        item for item in fixture.coverage.sources
                        if item.source_identity == fixture.requests[1].source_identity
                    )
                else:
                    def candidate_transform(build):
                        path = root / "compiled" / "models" / "task6.mdl"
                        data = bytearray(path.read_bytes()); data[0] ^= 1; path.write_bytes(data)
                        return build
                workspace = root / "union"
                with self.assertRaises(ValueError):
                    self._render_case(
                        root, runner, workspace, selected_components,
                        snapshot=selected_snapshot, source_proof=selected_proof,
                        candidate_transform=candidate_transform,
                    )
                self.assertEqual(runner.commands, [])
                self.assertFalse(workspace.exists())

    def test_source_union_rejects_candidate_that_drops_an_entire_component_before_render(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root, missing_component=True)
            runner = SourceUnionRunner(fixture); workspace = root / "union"
            with self.assertRaisesRegex(ValueError, "component"):
                self._render_case(root, runner, workspace, components)
            self.assertEqual(runner.commands, [])
            self.assertFalse(workspace.exists())

    def test_source_union_final_revalidation_rejects_compare_time_mutations(self) -> None:
        from maximum_optimizer import production_adapters as module
        real_compare = module.compare_source_union_render_sets
        for label in (
            "source", "candidate", "renderer", "compiled", "dependency",
            "raw", "authorized", "contract", "private", "raw-manifest",
            "visibility",
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
                state = {"digest": fixture.requests[0].dependency_proof_sha256}
                def compare(*args, **kwargs):
                    result = real_compare(*args, **kwargs)
                    if label == "source":
                        path = fixture.base_root / fixture.requests[0].source_relative_path
                        data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1; path.write_bytes(data)
                    elif label == "candidate":
                        path = (
                            fixture.snapshots[0].source_root
                            / fixture.snapshots[0].output_relative_path
                        )
                        data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1; path.write_bytes(data)
                    elif label == "renderer":
                        path = root / "renderer.py"
                        data = bytearray(path.read_bytes()); data[0] ^= 1; path.write_bytes(data)
                    elif label == "compiled":
                        path = root / "compiled" / "models" / "task6.mdl"
                        data = bytearray(path.read_bytes()); data[0] ^= 1; path.write_bytes(data)
                    elif label == "raw":
                        path = next(Path(args[1]).rglob("*.png"))
                        data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1; path.write_bytes(data)
                    elif label == "authorized":
                        authorized = Path(args[1]).parents[1] / "authorized"
                        path = next(authorized.rglob("*.png"))
                        data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1; path.write_bytes(data)
                    elif label == "contract":
                        path = Path(args[1]).parents[1] / "control" / "source-union-contract.json"
                        data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1; path.write_bytes(data)
                    elif label == "private":
                        path = Path(args[1]).parents[1] / "inputs" / "candidate.smd"
                        data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1; path.write_bytes(data)
                    elif label == "raw-manifest":
                        path = Path(args[1]) / "render_manifest.json"
                        path.write_text("[]", encoding="utf-8")
                    elif label == "visibility":
                        path = Path(args[1]).parents[1] / "control" / "source-union-visibility.json"
                        data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1; path.write_bytes(data)
                    else:
                        state["digest"] = "0" * 64
                    return result
                workspace = root / "union"
                with mock.patch(
                    "maximum_optimizer.production_adapters.compare_source_union_render_sets",
                    side_effect=compare,
                ), self.assertRaises(ValueError):
                    self._render_case(
                        root, SourceUnionRunner(fixture), workspace, components,
                        dependency_provider=lambda _event: state["digest"],
                    )
                self.assertFalse(workspace.exists())

    def test_source_union_cancellation_pre_process_and_compare_cleans_owned_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            event = threading.Event(); event.set(); runner = SourceUnionRunner(fixture)
            with self.assertRaises(ProcessCancelledError):
                self._render_case(root, runner, root / "union", components, event=event)
            self.assertEqual(runner.commands, [])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            event = threading.Event()
            runner = SourceUnionRunner(
                fixture,
                after_output=lambda _command, _payload, _event: event.set(),
            )
            workspace = root / "union"
            with self.assertRaises(ProcessCancelledError):
                self._render_case(root, runner, workspace, components, event=event)
            self.assertFalse(workspace.exists())

        from maximum_optimizer import production_adapters as module
        real_compare = module.compare_source_union_render_sets
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            event = threading.Event()
            def cancel_compare(*args, **kwargs):
                result = real_compare(*args, **kwargs); event.set(); return result
            workspace = root / "union"
            with mock.patch(
                "maximum_optimizer.production_adapters.compare_source_union_render_sets",
                side_effect=cancel_compare,
            ), self.assertRaises(ProcessCancelledError):
                self._render_case(
                    root, SourceUnionRunner(fixture), workspace, components, event=event,
                )
            self.assertFalse(workspace.exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runner = CompileRunner("task6.mdl")
            with self.assertRaises(ValueError):
                self._compile_case(
                    root, runner,
                    mutate_manifest=lambda value: replace(
                        value, family_id="f" * 64, input_hash="e" * 64,
                    ),
                )
            self.assertEqual(runner.commands, [])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runner = CompileRunner("task6.mdl")

            def stale_roots(composed):
                stale = composed.workspace / "compiled" / "models"
                stale.mkdir(parents=True)
                (stale / "task6.ani").write_bytes(b"stale exact-family sidecar")
                (composed.workspace / "logs").mkdir()

            with self.assertRaises(ValueError):
                self._compile_case(root, runner, mutate_composed=stale_roots)
            self.assertEqual(runner.commands, [])
            self.assertFalse((root / "composed" / "compiled").exists())
            self.assertFalse((root / "composed" / "logs").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runner = CompileRunner("task6.mdl")
            with self.assertRaises(ValueError):
                self._compile_case(
                    root, runner,
                    mutate_composed=lambda composed: composed.optimized_qc.write_text(
                        composed.optimized_qc.read_text(encoding="utf-8") + "// stale\n",
                        encoding="utf-8",
                    ),
                )
            self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main()
