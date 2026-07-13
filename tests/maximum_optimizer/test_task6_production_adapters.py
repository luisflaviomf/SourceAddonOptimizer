from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
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
from maximum_optimizer.domain import CandidateSpec, CompileFileProof, FamilyManifest, SourceFileProof, StructuralFingerprint
from maximum_optimizer.processes import ProcessCancelledError, ProcessResult
from maximum_optimizer.orchestrator import ProductionAdapters as OrchestratorProductionAdapters
from maximum_optimizer.production_adapters import (
    AdaptiveDirectProductionBoundary,
    AdaptiveDirectCompileResult,
    SourceUnionRenderTools,
    SourceUnionPoseBinding,
    build_source_union_python_runtime_contract,
    validate_source_union_cli_contract,
    validate_source_union_pose_bindings,
)
from tests.maximum_optimizer.test_task6_direct_compositor import DirectCompositorFixture, _smd
from maximum_optimizer.source_components import (
    build_source_component_manifest,
    current_filtered_source_component_bytes,
    source_component_manifest_from_payload,
    source_component_transfer_from_payload,
    validate_source_component_transfer_against_manifest,
)
from maximum_optimizer.source_materials import (
    build_source_union_material_contract,
    require_current_source_union_material_contract,
    source_union_material_contract_payload,
    source_union_material_render_evidence,
)
from tests.maximum_optimizer.test_task6_source_union_comparator import (
    _contract as unused_contract,
    _write_union_side,
)
from maximum_optimizer.visual_validation import FidelityProfile, REQUIRED_METRICS, SourceUnionComparisonContract
from maximum_optimizer.smd_state_contracts import (
    SmdAnimationPairInput, build_smd_pose_contract, build_smd_skeleton_contract,
)


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


def _animation_pair(root: Path, *, frames: tuple[int, ...] = (0, 12)) -> SmdAnimationPairInput:
    before_root = root / "animation-before"; after_root = root / "animation-after"
    before_root.mkdir(); after_root.mkdir()
    rows = ['version 1', 'nodes', '0 "root" -1', 'end', 'skeleton']
    for frame in frames:
        rows.extend((f"time {frame}", "0 0 0 0 0 0 0"))
    payload = ("\n".join((*rows, "end", ""))).encode()
    before = before_root / "first-name.smd"; after = after_root / "unrelated-name.smd"
    before.write_bytes(payload); after.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    return SmdAnimationPairInput(
        before, before_root, SourceFileProof("first-name.smd", "animation-source", "first-name.smd", len(payload), digest),
        after, after_root, SourceFileProof("unrelated-name.smd", "animation-source", "unrelated-name.smd", len(payload), digest),
    )


def _animation_pose_contract(root: Path, pair: SmdAnimationPairInput):
    visual_root = root / "pose-visual"; visual_root.mkdir()
    visual = visual_root / "body.smd"
    visual.write_bytes(_prefiltered_component_smds()[0])
    payload = visual.read_bytes(); digest = hashlib.sha256(payload).hexdigest()
    proof = SourceFileProof("body.smd", "visual-source", "body.smd", len(payload), digest)
    skeleton = build_smd_skeleton_contract(
        visual, visual_root, proof, threading.Event(),
    )
    return build_smd_pose_contract(
        skeleton, animation_pair=pair, cancel_event=threading.Event(),
    )


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
        self.commands = []; self.contracts = []; self.cwds = []

    def __call__(self, command, cwd, log_path, cancel_event):
        command = tuple(str(item) for item in command); self.commands.append(command)
        self.cwds.append(Path(cwd))
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
        for side in ("original", "optimized"):
            manifest_path = raw / side / "render_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["entries"]:
                entry["resolved_materials"] = (
                    payload["material_render_evidence"]
                    if entry["pass"] == "textured" else []
                )
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True), encoding="utf-8",
            )
        if self.missing_image:
            next((raw / "optimized").rglob("*.png")).unlink()
        if self.raw_mutator is not None:
            self.raw_mutator(raw, payload)
        observations = []
        pose_keys = tuple(item[0] for item in contract.pose_frames)
        for side in ("candidate", "reference"):
            for component in payload["component_keys"]:
                for pose_key in pose_keys:
                    for camera in payload["cameras"]:
                        observations.append({
                            "side": side, "component_key": component, "pose_key": pose_key,
                            "camera_key": camera,
                            "visible_mask_pixels": 3 if camera == "camera-00" else 0,
                        })
        visibility = {
            "schema": 1, "kind": "adaptive-direct-source-union-visibility-v1",
            "target_sha256": payload["target_sha256"],
            "source_identity": payload["source_identity"],
            "source_coverage_sha256": payload["source_coverage_sha256"],
            "cameras": payload["cameras"], "pose_keys": list(pose_keys),
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
    def _python_runtime_root(self, root: Path) -> Path:
        runtime_root = root / "renderer-runtime" / "maximum_optimizer"
        if not runtime_root.exists():
            runtime_root.mkdir(parents=True)
            package_root = Path(__import__("maximum_optimizer").__file__).parent
            for relative in (
                "__init__.py", "qc_graph.py", "regions.py", "reporting.py",
                "smd_contract.py", "source_components.py",
            ):
                shutil.copy2(package_root / relative, runtime_root / relative)
        return runtime_root

    def test_source_union_private_python_runtime_contract_api_is_required(self) -> None:
        from maximum_optimizer import production_adapters as module

        self.assertTrue(callable(getattr(
            module, "build_source_union_python_runtime_contract", None,
        )))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime_root = self._python_runtime_root(root)
            contract = build_source_union_python_runtime_contract(
                runtime_root, threading.Event(),
            )
            self.assertEqual(
                tuple(item.path for item in contract.files),
                (
                    "__init__.py", "qc_graph.py", "regions.py", "reporting.py",
                    "smd_contract.py", "source_components.py",
                ),
            )
            path = runtime_root / "reporting.py"
            data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "runtime"):
                module.require_current_source_union_python_runtime_contract(
                    contract, threading.Event(),
                )

    def _render_fixture(
        self, root: Path, *, missing_component: bool = False,
        poses: tuple[str, ...] = ("bind",),
        pose_contract_sha256: str = "6" * 64,
    ):
        source_bytes, candidate_bytes = _prefiltered_component_smds(
            missing_component=missing_component
        )
        component_manifest = build_source_component_manifest(source_bytes)
        material_first = root / "materials-first"
        material_second = root / "materials-second"
        (material_first / "vehicles").mkdir(parents=True)
        (material_second / "textures").mkdir(parents=True)
        (material_first / "vehicles/paint.vmt").write_text(
            'VertexLitGeneric { "$basetexture" "textures/paint" }',
            encoding="utf-8",
        )
        (material_second / "textures/paint.vtf").write_bytes(b"paint texture")
        filtered = current_filtered_source_component_bytes(component_manifest, source_bytes)
        material_contract = build_source_union_material_contract(
            source_identity="meshes/part-00.smd",
            filtered_source_bytes=filtered,
            requests=({
                "material_region_key": "material-000",
                "smd_material": "paint",
                "search_paths": ("vehicles",),
            },),
            roots=(material_first, material_second),
            cancel_event=threading.Event(),
        )
        fixture = DirectCompositorFixture(
            root,
            components=tuple(item.component_key for item in component_manifest.components),
            states=("damaged", "default"),
            component_manifest_sha256=component_manifest.component_manifest_sha256,
            material_contract_sha256=material_contract.material_contract_sha256,
            visual_source_bytes=source_bytes,
            direct_output_bytes=candidate_bytes,
            poses=poses,
            pose_contract_sha256=pose_contract_sha256,
        )
        fixture.material_contract = material_contract
        fixture.material_roots = (material_first, material_second)
        return fixture, component_manifest

    def _material_contract_variant(
        self, fixture, component_manifest, *, source_identity=None,
        filtered_source_bytes=None, material_region_key="material-000",
    ):
        source_path = fixture.base_root / fixture.requests[0].source_relative_path
        filtered = filtered_source_bytes or current_filtered_source_component_bytes(
            component_manifest, source_path.read_bytes()
        )
        return build_source_union_material_contract(
            source_identity=source_identity or fixture.requests[0].source_identity,
            filtered_source_bytes=filtered,
            requests=({
                "material_region_key": material_region_key,
                "smd_material": "paint", "search_paths": ("vehicles",),
            },),
            roots=fixture.material_roots, cancel_event=threading.Event(),
        )

    def _render_case(
        self, root: Path, runner, workspace: Path, component_manifest, *,
        dependency_provider=None, base_build=None, candidate_transform=None,
        event=None, source_proof=None, snapshot=None, tools_texture_cache=None,
        material_contract=None, material_roots=None, python_runtime_contract=None,
        pose_bindings=None,
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
        runtime_root = self._python_runtime_root(root)
        python_runtime = python_runtime_contract or (
            build_source_union_python_runtime_contract(
                runtime_root, threading.Event(),
            )
        )
        tools = SourceUnionRenderTools(
            blender, renderer_script, hashlib.sha256(renderer_script.read_bytes()).hexdigest(),
            python_runtime,
            materials_roots=material_roots or fixture.material_roots,
            texture_cache=tools_texture_cache,
            dependency_digest_provider=dependency_provider or (
                lambda _event: fixture.requests[0].dependency_proof_sha256
            ),
            pose_bindings=pose_bindings,
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
            material_contract=material_contract or fixture.material_contract,
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
            "--source-union-control-sha256", "0" * 64,
            "--source-union-visibility-out", "visibility.json",
        ])
        validate_source_union_cli_contract(args)
        for invalid in (None, True, "0" * 63, "A" * 64):
            with self.subTest(control_sha256=invalid), self.assertRaises(ValueError):
                args.source_union_control_sha256 = invalid
                validate_source_union_cli_contract(args)
        args.source_union_control_sha256 = "0" * 64
        animated = render_previews._parse_args([
            "--before", "reference.smd", "--after", "candidate.smd", "--out", "raw",
            "--passes", "textured,clay", "--poses", "bind:0,animation:12",
            "--animation-before", "first-name.smd",
            "--animation-after", "unrelated-name.smd",
            "--source-union-contract", "contract.json",
            "--source-union-control-sha256", "0" * 64,
            "--source-union-visibility-out", "visibility.json",
        ])
        validate_source_union_cli_contract(animated)
        render_previews._validate_source_union_cli_args(animated)

    def test_real_orchestrator_wrapper_replaces_untrusted_dependency_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            blender = root / "caller-blender.exe"; blender.write_bytes(b"caller")
            renderer = root / "caller-renderer.py"; renderer.write_bytes(b"caller")
            authoritative_blender = root / "real-blender.exe"; authoritative_blender.write_bytes(b"real")
            repo = root / "repo"; repo.mkdir()
            authoritative_renderer = repo / "render_previews.py"; authoritative_renderer.write_bytes(b"real renderer")
            authoritative_runtime = repo / "maximum_optimizer"
            authoritative_runtime.mkdir()
            package_root = Path(__import__("maximum_optimizer").__file__).parent
            for relative in (
                "__init__.py", "qc_graph.py", "regions.py", "reporting.py",
                "smd_contract.py", "source_components.py",
            ):
                shutil.copy2(package_root / relative, authoritative_runtime / relative)
            materials = root / "materials"; materials.mkdir()
            vtfcmd = root / "VTFCmd.exe"; vtfcmd.write_bytes(b"vtfcmd")
            texture_cache = root / "texture-cache"; texture_cache.mkdir()
            (texture_cache / "poison.png").write_bytes(b"fake cached png")
            caller = mock.Mock(side_effect=AssertionError("caller provider must not run"))
            tools = SourceUnionRenderTools(
                blender, renderer, hashlib.sha256(renderer.read_bytes()).hexdigest(),
                build_source_union_python_runtime_contract(
                    authoritative_runtime, threading.Event(),
                ),
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
                self.assertEqual(
                    passed.python_runtime,
                    build_source_union_python_runtime_contract(
                        authoritative_runtime, threading.Event(),
                    ),
                )
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
            "--source-union-control-sha256", "0" * 64,
            "--source-union-visibility-out", "visibility.json",
        ]):
            with self.assertRaisesRegex(
                SystemExit, "render_previews.py must be executed by Blender",
            ):
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
                    "--source-union-control-sha256", "0" * 64,
                    "--source-union-visibility-out", "visibility.json", *forbidden,
                ])
                validate_source_union_cli_contract(changed)
        with self.assertRaises(ValueError):
            validate_source_union_cli_contract(render_previews._parse_args([
                "--before", "r.smd", "--after", "c.smd", "--out", "raw",
                "--passes", "clay,textured", "--poses", "bind:0",
                "--source-union-contract", "contract.json",
                "--source-union-control-sha256", "0" * 64,
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
                    "--source-union-control-sha256", "0" * 64,
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

    def test_paired_pose_binding_is_current_exact_and_rejects_more_than_two_poses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); pair = _animation_pair(root)
            pose = _animation_pose_contract(root, pair)
            with self.assertRaises((TypeError, ValueError)):
                SourceUnionPoseBinding.paired_anchor(
                    pair, 12, "6" * 64, threading.Event(),
                )
            bindings = (
                SourceUnionPoseBinding.bind(pose.pose_contract_sha256),
                SourceUnionPoseBinding.paired_anchor(
                    pair, pose, threading.Event(),
                ),
            )
            validate_source_union_pose_bindings(
                bindings, ("bind", "animation"), pose.pose_contract_sha256, threading.Event(),
            )
            with self.assertRaises(ValueError):
                validate_source_union_pose_bindings(
                    bindings + (bindings[1],),
                    ("bind", "animation", "extra"), pose.pose_contract_sha256,
                    threading.Event(),
                )
            pair.original_path.write_bytes(pair.original_path.read_bytes() + b"// stale\n")
            with self.assertRaisesRegex(ValueError, "stale|divergent"):
                bindings[1].revalidate(threading.Event())

    def test_source_union_renders_exact_bind_and_paired_anchor_and_revalidates_callbacks(self) -> None:
        for stale in (False, True):
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                pair = _animation_pair(root)
                pose = _animation_pose_contract(root, pair)
                fixture, components = self._render_fixture(
                    root, poses=("bind", "animation"),
                    pose_contract_sha256=pose.pose_contract_sha256,
                )
                bindings = (
                    SourceUnionPoseBinding.bind(pose.pose_contract_sha256),
                    SourceUnionPoseBinding.paired_anchor(
                        pair, pose, threading.Event(),
                    ),
                )

                def after_output(_command, _payload, _event):
                    if stale:
                        pair.original_path.write_bytes(
                            pair.original_path.read_bytes() + b"// callback mutation\n"
                        )

                runner = SourceUnionRunner(fixture, after_output=after_output)
                workspace = root / "union"
                if stale:
                    with self.assertRaisesRegex(ValueError, "animation"):
                        self._render_case(
                            root, runner, workspace, components,
                            pose_bindings={fixture.requests[0].source_identity: bindings},
                        )
                    self.assertFalse(workspace.exists())
                else:
                    record = self._render_case(
                        root, runner, workspace, components,
                        pose_bindings={fixture.requests[0].source_identity: bindings},
                    )
                    command = runner.commands[0]
                    self.assertEqual(
                        command[command.index("--poses") + 1],
                        "bind:0,animation:12",
                    )
                    self.assertIn("--animation-before", command)
                    self.assertIn("--animation-after", command)
                    self.assertEqual(record.target.pose_keys, ("bind", "animation"))
                    self.assertEqual(len(record.files), 64)

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
            self.assertEqual(len(fixture.coverage.sources[0].witnesses), 2)
            self.assertTrue(all(
                witness.material_contract_sha256
                == fixture.material_contract.material_contract_sha256
                for witness in fixture.coverage.sources[0].witnesses
            ))
            self.assertEqual(first.evidence_sha256, second.evidence_sha256)
            self.assertEqual(len(runner.commands), 2)
            command = runner.commands[0]
            private_inputs = root / "union-one" / "inputs"
            private_runtime = private_inputs / "maximum_optimizer"
            self.assertEqual(runner.cwds[0], private_inputs)
            self.assertEqual(
                tuple(sorted(
                    path.relative_to(private_runtime).as_posix()
                    for path in private_runtime.rglob("*") if path.is_file()
                )),
                (
                    "__init__.py", "qc_graph.py", "regions.py", "reporting.py",
                    "smd_contract.py", "source_components.py",
                ),
            )
            self.assertFalse((private_runtime / "__pycache__").exists())
            self.assertIn("--source-union-contract", command)
            control_path = Path(
                command[command.index("--source-union-contract") + 1]
            )
            self.assertEqual(
                command[command.index("--source-union-control-sha256") + 1],
                hashlib.sha256(control_path.read_bytes()).hexdigest(),
            )
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
            material_args = tuple(
                Path(command[index + 1]) for index, item in enumerate(command)
                if item == "--materials-root"
            )
            self.assertEqual(len(material_args), len(fixture.material_roots))
            self.assertTrue(all(
                path.parent == root / "union-one" / "material-roots"
                for path in material_args
            ))
            self.assertTrue(all(path not in fixture.material_roots for path in material_args))
            self.assertFalse(any("source-materials-acquire" in str(path) for path in material_args))
            private_cache = Path(command[command.index("--texture-cache") + 1])
            self.assertTrue(str(private_cache).startswith(str(root / "union-one")))
            self.assertFalse(private_cache.exists())
            before = Path(command[command.index("--before") + 1])
            after = Path(command[command.index("--after") + 1])
            self.assertTrue(str(before).startswith(str(root / "union-one")))
            self.assertTrue(str(after).startswith(str(root / "union-one")))
            contract = runner.contracts[0]
            authorization = require_current_source_union_material_contract(
                fixture.material_contract,
                filtered_source_bytes=current_filtered_source_component_bytes(
                    component_manifest,
                    (fixture.base_root / fixture.requests[0].source_relative_path).read_bytes(),
                ),
                roots=fixture.material_roots, cancel_event=threading.Event(),
            )
            expected_material_evidence = list(
                source_union_material_render_evidence(authorization)
            )
            self.assertEqual(
                contract["material_contract"],
                source_union_material_contract_payload(fixture.material_contract),
            )
            self.assertEqual(
                contract["material_render_evidence"], expected_material_evidence,
            )
            self.assertEqual(
                contract["material_contract_sha256"],
                fixture.material_contract.material_contract_sha256,
            )
            self.assertEqual(
                contract["comparison_contract"]["material_contract_sha256"],
                fixture.material_contract.material_contract_sha256,
            )
            expected_control_fields = {
                "schema", "kind", "target_sha256", "comparison_contract",
                "source_identity", "source_coverage_sha256", "component_keys",
                "component_manifest", "candidate_component_transfer",
                "material_region_keys", "material_contract_sha256",
                "material_contract", "material_render_evidence",
                "pose_frames", "angles", "cameras", "renderer_sha256",
                "python_runtime_contract_sha256", "python_runtime_files",
            }
            self.assertEqual(set(contract), expected_control_fields)
            runtime_contract = build_source_union_python_runtime_contract(
                root / "renderer-runtime/maximum_optimizer", threading.Event(),
            )
            self.assertEqual(
                contract["python_runtime_contract_sha256"],
                runtime_contract.contract_sha256,
            )
            self.assertEqual(
                contract["python_runtime_files"],
                [
                    {"path": item.path, "size": item.size, "sha256": item.sha256}
                    for item in runtime_contract.files
                ],
            )
            for raw_side in ("original", "optimized"):
                manifest = json.loads((
                    root / "union-one" / "raw" / raw_side / "render_manifest.json"
                ).read_text(encoding="utf-8"))
                self.assertEqual(
                    manifest["material_contract_sha256"],
                    fixture.material_contract.material_contract_sha256,
                )
                for entry in manifest["entries"]:
                    self.assertEqual(
                        entry["resolved_materials"],
                        expected_material_evidence if entry["pass"] == "textured" else [],
                    )
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

    def test_source_union_renderer_imports_only_private_python_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            renderer = root / "renderer.py"
            renderer.write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "sys.dont_write_bytecode = True\n"
                "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
                "from maximum_optimizer.source_components import "
                "build_source_component_manifest\n",
                encoding="utf-8",
            )
            fixture, components = self._render_fixture(root)
            base_runner = SourceUnionRunner(fixture)

            def import_then_render(command, cwd, log_path, cancel_event):
                script = Path(command[command.index("--python") + 1])
                imported = subprocess.run(
                    [sys.executable, "-I", str(script)],
                    cwd=cwd, capture_output=True, text=True, check=False,
                )
                if imported.returncode != 0:
                    raise ValueError(imported.stderr)
                return base_runner(command, cwd, log_path, cancel_event)
            import_then_render.fixture = fixture

            record = self._render_case(
                root, import_then_render, root / "union", components,
            )
            self.assertTrue(record.validation.passed)

    def test_source_union_rejects_changed_python_runtime_before_e1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            runtime_root = self._python_runtime_root(root)
            runtime_contract = build_source_union_python_runtime_contract(
                runtime_root, threading.Event(),
            )
            path = runtime_root / "reporting.py"
            data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1
            path.write_bytes(data)
            runner = SourceUnionRunner(fixture)
            workspace = root / "union"
            with self.assertRaisesRegex(ValueError, "runtime"):
                self._render_case(
                    root, runner, workspace, components,
                    python_runtime_contract=runtime_contract,
                )
            self.assertEqual(runner.commands, [])
            self.assertFalse(workspace.exists())

    def test_source_union_python_runtime_copy_races_fail_closed(self) -> None:
        from maximum_optimizer import production_adapters as module

        real_copy = module._copy_file_no_follow
        for label in ("source", "destination"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture, components = self._render_fixture(root)
                runtime_root = self._python_runtime_root(root)
                runtime_contract = build_source_union_python_runtime_contract(
                    runtime_root, threading.Event(),
                )
                state = {"mutated": False, "identity": None}

                def copy_then_race(source, destination, *args, **kwargs):
                    identity = real_copy(source, destination, *args, **kwargs)
                    if (
                        not state["mutated"]
                        and Path(source).parent == runtime_root
                        and Path(source).name == "reporting.py"
                    ):
                        state["mutated"] = True
                        if label == "source":
                            path = Path(source)
                            data = bytearray(path.read_bytes())
                            data[len(data) // 2] ^= 1
                            path.write_bytes(data)
                        else:
                            winner = root / "foreign-copy-reporting.py"
                            shutil.copy2(destination, winner)
                            Path(destination).unlink()
                            os.rename(winner, destination)
                            info = os.lstat(destination)
                            state["identity"] = (
                                int(info.st_dev), int(info.st_ino),
                                int(getattr(
                                    info, "st_ctime_ns", int(info.st_ctime * 1e9),
                                )),
                            )
                    return identity

                runner = SourceUnionRunner(fixture)
                workspace = root / "union"
                with mock.patch(
                    "maximum_optimizer.production_adapters._copy_file_no_follow",
                    side_effect=copy_then_race,
                ), self.assertRaises(ValueError):
                    self._render_case(
                        root, runner, workspace, components,
                        python_runtime_contract=runtime_contract,
                    )
                self.assertEqual(runner.commands, [])
                if label == "source":
                    self.assertFalse(workspace.exists())
                else:
                    self.assertTrue(workspace.exists())
                    info = os.lstat(
                        workspace / "inputs/maximum_optimizer/reporting.py"
                    )
                    self.assertEqual(
                        state["identity"],
                        (
                            int(info.st_dev), int(info.st_ino),
                            int(getattr(
                                info, "st_ctime_ns", int(info.st_ctime * 1e9),
                            )),
                        ),
                    )

    def test_source_union_revalidates_original_and_private_python_runtime_after_process(self) -> None:
        for label in ("original", "private", "private-missing"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture, components = self._render_fixture(root)
                runtime_root = self._python_runtime_root(root)
                runtime_contract = build_source_union_python_runtime_contract(
                    runtime_root, threading.Event(),
                )

                def mutate_runtime(command, _payload, _event):
                    if label == "original":
                        path = runtime_root / "reporting.py"
                    else:
                        renderer = Path(command[command.index("--python") + 1])
                        path = renderer.parent / "maximum_optimizer/reporting.py"
                    if label == "private-missing":
                        path.unlink()
                        return
                    data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1
                    path.write_bytes(data)

                workspace = root / "union"
                with self.assertRaisesRegex(ValueError, "runtime"):
                    self._render_case(
                        root,
                        SourceUnionRunner(fixture, after_output=mutate_runtime),
                        workspace, components,
                        python_runtime_contract=runtime_contract,
                    )
                self.assertFalse(workspace.exists())

    def test_source_union_preserves_unowned_private_python_runtime_entries(self) -> None:
        for label in ("extra", "cache", "symlink"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture, components = self._render_fixture(root)

                def add_unowned(command, _payload, _event):
                    renderer = Path(command[command.index("--python") + 1])
                    runtime = renderer.parent / "maximum_optimizer"
                    if label == "extra":
                        (runtime / "foreign.py").write_bytes(b"foreign")
                    elif label == "cache":
                        cache = runtime / "__pycache__"; cache.mkdir()
                        (cache / "reporting.pyc").write_bytes(b"foreign cache")
                    else:
                        external = root / "external.py"
                        external.write_bytes(b"external")
                        try:
                            (runtime / "foreign-link.py").symlink_to(external)
                        except OSError as exc:
                            self.skipTest(f"symlink creation unavailable: {exc}")

                workspace = root / "union"
                with self.assertRaisesRegex(ValueError, "runtime"):
                    self._render_case(
                        root, SourceUnionRunner(fixture, after_output=add_unowned),
                        workspace, components,
                    )
                self.assertTrue(workspace.exists())
                if label == "extra":
                    self.assertEqual(
                        (workspace / "inputs/maximum_optimizer/foreign.py").read_bytes(),
                        b"foreign",
                    )
                elif label == "cache":
                    self.assertEqual(
                        (workspace / "inputs/maximum_optimizer/__pycache__/reporting.pyc").read_bytes(),
                        b"foreign cache",
                    )
                else:
                    self.assertTrue(
                        (workspace / "inputs/maximum_optimizer/foreign-link.py").is_symlink()
                    )

    def test_source_union_preserves_replaced_private_python_runtime_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            state = {"identity": None}

            def replace_runtime_file(command, _payload, _event):
                renderer = Path(command[command.index("--python") + 1])
                victim = renderer.parent / "maximum_optimizer/reporting.py"
                winner = root / "foreign-reporting.py"
                shutil.copy2(victim, winner)
                victim.unlink(); os.rename(winner, victim)
                info = os.lstat(victim)
                state["identity"] = (
                    int(info.st_dev), int(info.st_ino),
                    int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))),
                )

            workspace = root / "union"
            with self.assertRaisesRegex(ValueError, "runtime"):
                self._render_case(
                    root,
                    SourceUnionRunner(fixture, after_output=replace_runtime_file),
                    workspace, components,
                )
            self.assertTrue(workspace.exists())
            info = os.lstat(workspace / "inputs/maximum_optimizer/reporting.py")
            self.assertEqual(
                state["identity"],
                (
                    int(info.st_dev), int(info.st_ino),
                    int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))),
                ),
            )

    def test_source_union_runtime_contract_rejects_symlinked_required_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime_root = self._python_runtime_root(root)
            victim = runtime_root / "reporting.py"
            external = root / "external-reporting.py"
            external.write_bytes(victim.read_bytes())
            victim.unlink()
            try:
                victim.symlink_to(external)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaises(ValueError):
                build_source_union_python_runtime_contract(
                    runtime_root, threading.Event(),
                )

    def test_source_union_material_contract_binding_rejects_before_e1(self) -> None:
        for label in ("source", "filter", "region", "witness", "root-order"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture, components = self._render_fixture(root)
                if label == "source":
                    contract = self._material_contract_variant(
                        fixture, components, source_identity="meshes/wrong.smd",
                    )
                elif label == "filter":
                    source_path = fixture.base_root / fixture.requests[0].source_relative_path
                    filtered = current_filtered_source_component_bytes(
                        components, source_path.read_bytes()
                    ).replace(b"0 0 0 0 0 0 1", b"0 0.25 0 0 0 0 1", 1)
                    contract = self._material_contract_variant(
                        fixture, components, filtered_source_bytes=filtered,
                    )
                elif label == "region":
                    contract = self._material_contract_variant(
                        fixture, components, material_region_key="wrong-region",
                    )
                elif label == "witness":
                    texture = fixture.material_roots[1] / "textures/paint.vtf"
                    texture.write_bytes(b"other texture")
                    contract = self._material_contract_variant(fixture, components)
                else:
                    contract = fixture.material_contract
                runner = SourceUnionRunner(fixture)
                workspace = root / "union"
                with self.assertRaises(ValueError):
                    self._render_case(
                        root, runner, workspace, components,
                        material_contract=contract,
                        material_roots=(
                            tuple(reversed(fixture.material_roots))
                            if label == "root-order" else None
                        ),
                    )
                self.assertEqual(runner.commands, [])
                self.assertFalse(workspace.exists())

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

    @unittest.skipUnless(os.name == "nt", "Windows junction semantics")
    def test_source_union_rejects_nested_private_cache_junction_and_preserves_external(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            external = root / "external-cache"; external.mkdir()
            marker = external / "external.marker"; marker.write_bytes(b"preserve")
            def nested_junction(command, _payload, _event):
                cache = Path(command[command.index("--texture-cache") + 1])
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(cache / "nested"), str(external)],
                    capture_output=True, text=True,
                )
                if created.returncode != 0:
                    self.skipTest(f"junction creation unavailable: {created.stderr or created.stdout}")
            workspace = root / "union"
            with self.assertRaisesRegex(ValueError, "cache|reparse"):
                self._render_case(
                    root, SourceUnionRunner(fixture, after_output=nested_junction),
                    workspace, components,
                )
            self.assertEqual(marker.read_bytes(), b"preserve")
            self.assertFalse(workspace.exists())

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
        def missing_textured_material(raw, _payload):
            path = raw / "optimized" / "render_manifest.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            next(
                item for item in value["entries"] if item["pass"] == "textured"
            )["resolved_materials"] = []
            path.write_text(json.dumps(value), encoding="utf-8")
        def material_on_clay(raw, payload):
            path = raw / "original" / "render_manifest.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            next(
                item for item in value["entries"] if item["pass"] == "clay"
            )["resolved_materials"] = payload["material_render_evidence"]
            path.write_text(json.dumps(value), encoding="utf-8")
        for label, mutation, structural in (
            ("nested-extra", nested_extra, False), ("extra-file", extra_file, True),
            ("jpeg", jpeg, True), ("same-size", same_size, True),
            ("swapped", swapped, False),
            ("duplicate-entry", duplicate_entry, True),
            ("missing-textured-material", missing_textured_material, True),
            ("material-on-clay", material_on_clay, True),
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
        def mutate_named(command, _payload, _event, flag, fixture):
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
            elif flag == "original-vmt":
                path = fixture.material_roots[0] / "vehicles/paint.vmt"
            elif flag == "original-vtf":
                path = fixture.material_roots[1] / "textures/paint.vtf"
            elif flag == "private-vmt":
                roots = [
                    Path(command[index + 1]) for index, item in enumerate(command)
                    if item == "--materials-root"
                ]
                path = roots[0] / "vehicles/paint.vmt"
            elif flag == "private-vtf":
                roots = [
                    Path(command[index + 1]) for index, item in enumerate(command)
                    if item == "--materials-root"
                ]
                path = roots[1] / "textures/paint.vtf"
            elif flag == "shadow":
                shadow = fixture.material_roots[0] / "textures/paint.vtf"
                shadow.parent.mkdir()
                shadow.write_bytes(b"higher priority")
                return
            else:
                roots = [
                    Path(command[index + 1]) for index, item in enumerate(command)
                    if item == "--materials-root"
                ]
                (roots[0] / "extra.vtf").write_bytes(b"extra")
                return
            data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1; path.write_bytes(data)

        for flag in (
            "source", "candidate", "renderer", "contract",
            "original-vmt", "original-vtf", "private-vmt", "private-vtf",
            "shadow", "private-extra",
        ):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
                runner = SourceUnionRunner(
                    fixture,
                    after_output=lambda command, payload, event, flag=flag: mutate_named(
                        command, payload, event, flag, fixture
                    ),
                )
                workspace = root / "union"
                with self.assertRaises(ValueError):
                    self._render_case(root, runner, workspace, components)
                if flag == "private-extra":
                    self.assertEqual(
                        (workspace / "material-roots/root-000/extra.vtf").read_bytes(),
                        b"extra",
                    )
                else:
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

    def test_source_union_rejects_replaced_private_material_root_and_preserves_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            state = {"winner_identity": None}

            def replace_material_root(command, _payload, _event):
                output_root = Path(command[command.index("--out") + 1]).parent
                material_tree = output_root / "material-roots"
                winner = root / "external-material-winner"
                shutil.copytree(material_tree, winner)
                (winner / "external.marker").write_bytes(b"preserve")
                info = os.lstat(winner)
                state["winner_identity"] = (
                    int(info.st_dev), int(info.st_ino),
                    int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))),
                )
                shutil.rmtree(material_tree)
                os.rename(winner, material_tree)

            workspace = root / "union"
            with self.assertRaises(ValueError):
                self._render_case(
                    root,
                    SourceUnionRunner(fixture, after_output=replace_material_root),
                    workspace, components,
                )
            self.assertTrue(workspace.exists())
            material_tree = workspace / "material-roots"
            info = os.lstat(material_tree)
            self.assertEqual(
                state["winner_identity"],
                (
                    int(info.st_dev), int(info.st_ino),
                    int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))),
                ),
            )
            self.assertEqual(
                (material_tree / "external.marker").read_bytes(), b"preserve",
            )
            self.assertEqual(
                tuple(workspace.parent.glob(".material-roots.source-materials-acquire-*")),
                (),
            )

    def test_source_union_rejects_private_material_swap_before_materialize_returns(self) -> None:
        from maximum_optimizer import production_adapters as module

        real_materialize = module.materialize_private_source_union_material_roots
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            marker = workspace / "material-roots" / "external.marker"

            def replace_before_return(*args, **kwargs):
                private = real_materialize(*args, **kwargs)
                destination = Path(args[2])
                winner = root / "external-before-return"
                shutil.copytree(destination, winner)
                (winner / "external.marker").write_bytes(b"preserve")
                shutil.rmtree(destination)
                os.rename(winner, destination)
                return private

            runner = SourceUnionRunner(fixture)
            with mock.patch(
                "maximum_optimizer.production_adapters."
                "materialize_private_source_union_material_roots",
                side_effect=replace_before_return,
            ), self.assertRaises(ValueError):
                self._render_case(root, runner, workspace, components)
            self.assertEqual(runner.commands, [])
            self.assertTrue(workspace.exists())
            self.assertEqual(marker.read_bytes(), b"preserve")

    def test_source_union_rejects_replaced_private_material_child_and_preserves_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)

            def replace_material_child(command, _payload, _event):
                output_root = Path(command[command.index("--out") + 1]).parent
                child = output_root / "material-roots/root-000"
                winner = root / "external-root-000"
                shutil.copytree(child, winner)
                (winner / "external.marker").write_bytes(b"preserve")
                shutil.rmtree(child)
                os.rename(winner, child)

            workspace = root / "union"
            with self.assertRaises(ValueError):
                self._render_case(
                    root,
                    SourceUnionRunner(fixture, after_output=replace_material_child),
                    workspace, components,
                )
            self.assertTrue(workspace.exists())
            self.assertEqual(
                (workspace / "material-roots/root-000/external.marker").read_bytes(),
                b"preserve",
            )
            self.assertEqual(
                tuple(workspace.parent.glob(".material-roots.source-materials-acquire-*")),
                (),
            )

    def test_source_union_preserves_replaced_private_child_when_blender_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            successful_runner = SourceUnionRunner(fixture)

            def replace_child_and_fail(command, cwd, log_path, cancel_event):
                process = successful_runner(command, cwd, log_path, cancel_event)
                output_root = Path(command[command.index("--out") + 1]).parent
                child = output_root / "material-roots/root-000"
                winner = root / "external-failed-root"
                shutil.copytree(child, winner)
                (winner / "external.marker").write_bytes(b"preserve")
                shutil.rmtree(child)
                os.rename(winner, child)
                return ProcessResult(process.command, 1, process.elapsed, process.log_path)
            replace_child_and_fail.fixture = fixture

            workspace = root / "union"
            with self.assertRaises(ValueError):
                self._render_case(
                    root, replace_child_and_fail, workspace, components,
                )
            self.assertTrue(workspace.exists())
            self.assertEqual(
                (workspace / "material-roots/root-000/external.marker").read_bytes(),
                b"preserve",
            )

    def test_source_union_preserves_exact_private_descendant_swap_when_blender_fails(self) -> None:
        for label, relative in (
            ("nested", Path("root-000/vehicles")),
            ("file", Path("root-000/vehicles/paint.vmt")),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture, components = self._render_fixture(root)
                successful_runner = SourceUnionRunner(fixture)
                state = {"identity": None}

                def replace_descendant_and_fail(command, cwd, log_path, cancel_event):
                    process = successful_runner(command, cwd, log_path, cancel_event)
                    output_root = Path(command[command.index("--out") + 1]).parent
                    victim = output_root / "material-roots" / relative
                    winner = root / f"external-failed-{label}"
                    if victim.is_dir():
                        shutil.copytree(victim, winner)
                        shutil.rmtree(victim)
                    else:
                        shutil.copy2(victim, winner)
                        victim.unlink()
                    os.rename(winner, victim)
                    info = os.lstat(victim)
                    state["identity"] = (
                        int(info.st_dev), int(info.st_ino),
                        int(getattr(
                            info, "st_ctime_ns", int(info.st_ctime * 1e9),
                        )),
                    )
                    return ProcessResult(
                        process.command, 1, process.elapsed, process.log_path,
                    )
                replace_descendant_and_fail.fixture = fixture

                workspace = root / "union"
                with self.assertRaises(ValueError):
                    self._render_case(
                        root, replace_descendant_and_fail, workspace, components,
                    )
                self.assertTrue(workspace.exists())
                info = os.lstat(workspace / "material-roots" / relative)
                self.assertEqual(
                    state["identity"],
                    (
                        int(info.st_dev), int(info.st_ino),
                        int(getattr(
                            info, "st_ctime_ns", int(info.st_ctime * 1e9),
                        )),
                    ),
                )

    def test_source_union_preserves_foreign_material_publish_race_winner(self) -> None:
        from maximum_optimizer import source_materials as material_module

        real_rename = material_module.os.rename
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            destination = workspace / "material-roots"
            marker = destination / "external.marker"

            def race_publish(source, target):
                if Path(target) == destination:
                    destination.mkdir()
                    marker.write_bytes(b"preserve")
                return real_rename(source, target)

            runner = SourceUnionRunner(fixture)
            with mock.patch(
                "maximum_optimizer.source_materials.os.rename",
                side_effect=race_publish,
            ), self.assertRaises((ValueError, OSError)):
                self._render_case(root, runner, workspace, components)
            self.assertEqual(runner.commands, [])
            self.assertTrue(workspace.exists())
            self.assertEqual(marker.read_bytes(), b"preserve")

    def test_source_union_preserves_foreign_material_winner_on_pre_publish_cancel(self) -> None:
        from maximum_optimizer import source_materials as material_module

        real_copy = material_module._copy_file_no_follow
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            destination = workspace / "material-roots"
            marker = destination / "external.marker"
            event = threading.Event()
            state = {"raced": False}

            def race_then_cancel(*args, **kwargs):
                result = real_copy(*args, **kwargs)
                if not state["raced"]:
                    destination.mkdir()
                    marker.write_bytes(b"preserve")
                    event.set()
                    state["raced"] = True
                return result

            runner = SourceUnionRunner(fixture)
            with mock.patch(
                "maximum_optimizer.source_materials._copy_file_no_follow",
                side_effect=race_then_cancel,
            ), self.assertRaises(ProcessCancelledError):
                self._render_case(
                    root, runner, workspace, components, event=event,
                )
            self.assertEqual(runner.commands, [])
            self.assertTrue(workspace.exists())
            self.assertEqual(marker.read_bytes(), b"preserve")

    def test_source_union_preserves_foreign_material_destination_present_at_acquire(self) -> None:
        from maximum_optimizer import production_adapters as module

        real_materialize = module.materialize_private_source_union_material_roots
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            destination = workspace / "material-roots"
            marker = destination / "external.marker"

            def precreate_destination(*args, **kwargs):
                destination.mkdir()
                marker.write_bytes(b"preserve")
                return real_materialize(*args, **kwargs)

            runner = SourceUnionRunner(fixture)
            with mock.patch(
                "maximum_optimizer.production_adapters."
                "materialize_private_source_union_material_roots",
                side_effect=precreate_destination,
            ), self.assertRaises(ValueError):
                self._render_case(root, runner, workspace, components)
            self.assertEqual(runner.commands, [])
            self.assertTrue(workspace.exists())
            self.assertEqual(marker.read_bytes(), b"preserve")

    def test_source_union_preserves_foreign_material_staging_collision(self) -> None:
        from maximum_optimizer import production_adapters as module
        from maximum_optimizer import source_materials as material_module

        real_materialize = module.materialize_private_source_union_material_roots
        fixed_uuid = "1" * 32
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            staging = workspace / (
                f".material-roots.source-materials-acquire-{fixed_uuid}"
            )
            marker = staging / "external.marker"

            def collide_staging(*args, **kwargs):
                staging.mkdir()
                marker.write_bytes(b"preserve")
                return real_materialize(*args, **kwargs)

            runner = SourceUnionRunner(fixture)
            fake_uuid = mock.Mock(hex=fixed_uuid)
            with mock.patch(
                "maximum_optimizer.production_adapters."
                "materialize_private_source_union_material_roots",
                side_effect=collide_staging,
            ), mock.patch.object(
                material_module.uuid, "uuid4", return_value=fake_uuid,
            ), self.assertRaises((ValueError, OSError)):
                self._render_case(root, runner, workspace, components)
            self.assertEqual(runner.commands, [])
            self.assertTrue(workspace.exists())
            self.assertEqual(marker.read_bytes(), b"preserve")

    def test_source_union_rejects_exact_staging_descendant_swaps_before_publish(self) -> None:
        from maximum_optimizer import source_materials as material_module

        real_copy = material_module._copy_file_no_follow
        for label, relative in (
            ("root", Path("root-000")),
            ("nested", Path("root-000/vehicles")),
            ("file", Path("root-000/vehicles/paint.vmt")),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture, components = self._render_fixture(root)
                workspace = root / "union"
                state = {"swapped": False, "identity": None}

                def swap_after_first_copy(*args, **kwargs):
                    result = real_copy(*args, **kwargs)
                    if not state["swapped"]:
                        target = Path(args[1])
                        staging = target.parents[2]
                        victim = staging / relative
                        winner = root / f"foreign-{label}"
                        if victim.is_dir():
                            shutil.copytree(victim, winner)
                            shutil.rmtree(victim)
                        else:
                            shutil.copy2(victim, winner)
                            victim.unlink()
                        os.rename(winner, victim)
                        info = os.lstat(victim)
                        state["identity"] = (
                            int(info.st_dev), int(info.st_ino),
                            int(getattr(
                                info, "st_ctime_ns", int(info.st_ctime * 1e9),
                            )),
                        )
                        state["swapped"] = True
                    return result

                runner = SourceUnionRunner(fixture)
                with mock.patch(
                    "maximum_optimizer.source_materials._copy_file_no_follow",
                    side_effect=swap_after_first_copy,
                ), self.assertRaises(ValueError):
                    self._render_case(root, runner, workspace, components)
                self.assertEqual(runner.commands, [])
                staging_roots = tuple(workspace.glob(
                    ".material-roots.source-materials-acquire-*"
                ))
                self.assertEqual(len(staging_roots), 1)
                info = os.lstat(staging_roots[0] / relative)
                self.assertEqual(
                    state["identity"],
                    (
                        int(info.st_dev), int(info.st_ino),
                        int(getattr(
                            info, "st_ctime_ns", int(info.st_ctime * 1e9),
                        )),
                    ),
                )

    def test_source_union_preserves_exact_staging_descendant_swap_on_cancel(self) -> None:
        from maximum_optimizer import source_materials as material_module

        real_copy = material_module._copy_file_no_follow
        for label, relative in (
            ("root", Path("root-000")),
            ("nested", Path("root-000/vehicles")),
            ("file", Path("root-000/vehicles/paint.vmt")),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture, components = self._render_fixture(root)
                workspace = root / "union"
                event = threading.Event()
                state = {"swapped": False, "identity": None}

                def swap_then_cancel(*args, **kwargs):
                    result = real_copy(*args, **kwargs)
                    if not state["swapped"]:
                        target = Path(args[1])
                        staging = target.parents[2]
                        victim = staging / relative
                        winner = root / f"foreign-cancel-{label}"
                        if victim.is_dir():
                            shutil.copytree(victim, winner)
                            shutil.rmtree(victim)
                        else:
                            shutil.copy2(victim, winner)
                            victim.unlink()
                        os.rename(winner, victim)
                        info = os.lstat(victim)
                        state["identity"] = (
                            int(info.st_dev), int(info.st_ino),
                            int(getattr(
                                info, "st_ctime_ns", int(info.st_ctime * 1e9),
                            )),
                        )
                        state["swapped"] = True
                        event.set()
                    return result

                runner = SourceUnionRunner(fixture)
                with mock.patch(
                    "maximum_optimizer.source_materials._copy_file_no_follow",
                    side_effect=swap_then_cancel,
                ), self.assertRaises(ProcessCancelledError):
                    self._render_case(
                        root, runner, workspace, components, event=event,
                    )
                self.assertEqual(runner.commands, [])
                staging_roots = tuple(workspace.glob(
                    ".material-roots.source-materials-acquire-*"
                ))
                self.assertEqual(len(staging_roots), 1)
                info = os.lstat(staging_roots[0] / relative)
                self.assertEqual(
                    state["identity"],
                    (
                        int(info.st_dev), int(info.st_ino),
                        int(getattr(
                            info, "st_ctime_ns", int(info.st_ctime * 1e9),
                        )),
                    ),
                )

    def test_source_union_preserves_unknown_staging_descendant_on_cancel(self) -> None:
        from maximum_optimizer import source_materials as material_module

        real_copy = material_module._copy_file_no_follow
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            event = threading.Event()
            state = {"injected": False}

            def inject_extra_then_cancel(*args, **kwargs):
                result = real_copy(*args, **kwargs)
                if not state["injected"]:
                    target = Path(args[1])
                    (target.parent / "foreign.marker").write_bytes(b"preserve")
                    state["injected"] = True
                    event.set()
                return result

            runner = SourceUnionRunner(fixture)
            with mock.patch(
                "maximum_optimizer.source_materials._copy_file_no_follow",
                side_effect=inject_extra_then_cancel,
            ), self.assertRaises(ProcessCancelledError):
                self._render_case(
                    root, runner, workspace, components, event=event,
                )
            self.assertEqual(runner.commands, [])
            staging_roots = tuple(workspace.glob(
                ".material-roots.source-materials-acquire-*"
            ))
            self.assertEqual(len(staging_roots), 1)
            self.assertEqual(
                (staging_roots[0] / "root-000/vehicles/foreign.marker").read_bytes(),
                b"preserve",
            )

    def test_source_union_preserves_unknown_staging_directory_on_cancel(self) -> None:
        from maximum_optimizer import source_materials as material_module

        real_copy = material_module._copy_file_no_follow
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            event = threading.Event()

            def inject_directory_then_cancel(*args, **kwargs):
                result = real_copy(*args, **kwargs)
                foreign = Path(args[1]).parent / "foreign-dir"
                foreign.mkdir()
                (foreign / "external.marker").write_bytes(b"preserve")
                event.set()
                return result

            with mock.patch(
                "maximum_optimizer.source_materials._copy_file_no_follow",
                side_effect=inject_directory_then_cancel,
            ), self.assertRaises(ProcessCancelledError):
                self._render_case(
                    root, SourceUnionRunner(fixture), workspace, components,
                    event=event,
                )
            staging_roots = tuple(workspace.glob(
                ".material-roots.source-materials-acquire-*"
            ))
            self.assertEqual(len(staging_roots), 1)
            self.assertEqual(
                (staging_roots[0] / "root-000/vehicles/foreign-dir/external.marker").read_bytes(),
                b"preserve",
            )

    def test_source_union_preserves_unknown_published_file_on_cancel(self) -> None:
        from maximum_optimizer import source_materials as material_module

        real_require = material_module.require_current_private_source_union_material_lease
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            event = threading.Event()
            marker = workspace / "material-roots/root-000/vehicles/foreign.marker"

            def inject_then_validate(lease, destination, cancel_event):
                marker.write_bytes(b"preserve")
                event.set()
                return real_require(lease, destination, cancel_event)

            with mock.patch(
                "maximum_optimizer.source_materials."
                "require_current_private_source_union_material_lease",
                side_effect=inject_then_validate,
            ), self.assertRaises(ProcessCancelledError):
                self._render_case(
                    root, SourceUnionRunner(fixture), workspace, components,
                    event=event,
                )
            self.assertEqual(marker.read_bytes(), b"preserve")

    def test_source_union_preserves_unknown_published_file_when_blender_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            successful_runner = SourceUnionRunner(fixture)
            workspace = root / "union"
            marker = workspace / "material-roots/root-000/vehicles/foreign.marker"

            def inject_then_fail(command, cwd, log_path, cancel_event):
                process = successful_runner(command, cwd, log_path, cancel_event)
                marker.write_bytes(b"preserve")
                return ProcessResult(
                    process.command, 1, process.elapsed, process.log_path,
                )
            inject_then_fail.fixture = fixture

            with self.assertRaises(ValueError):
                self._render_case(
                    root, inject_then_fail, workspace, components,
                )
            self.assertEqual(marker.read_bytes(), b"preserve")

    def test_source_union_preserves_exact_published_file_swap_on_cancel(self) -> None:
        from maximum_optimizer import source_materials as material_module

        real_require = material_module.require_current_private_source_union_material_lease
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            event = threading.Event()
            state = {"swapped": False, "identity": None}

            def swap_then_validate(lease, destination, cancel_event):
                destination = Path(destination)
                if not state["swapped"]:
                    victim = destination / "root-000/vehicles/paint.vmt"
                    winner = root / "foreign-published-paint.vmt"
                    shutil.copy2(victim, winner)
                    victim.unlink()
                    os.rename(winner, victim)
                    info = os.lstat(victim)
                    state["identity"] = (
                        int(info.st_dev), int(info.st_ino),
                        int(getattr(
                            info, "st_ctime_ns", int(info.st_ctime * 1e9),
                        )),
                    )
                    state["swapped"] = True
                    event.set()
                return real_require(lease, destination, cancel_event)

            runner = SourceUnionRunner(fixture)
            with mock.patch(
                "maximum_optimizer.source_materials."
                "require_current_private_source_union_material_lease",
                side_effect=swap_then_validate,
            ), self.assertRaises(ProcessCancelledError):
                self._render_case(
                    root, runner, workspace, components, event=event,
                )
            self.assertEqual(runner.commands, [])
            victim = workspace / "material-roots/root-000/vehicles/paint.vmt"
            info = os.lstat(victim)
            self.assertEqual(
                state["identity"],
                (
                    int(info.st_dev), int(info.st_ino),
                    int(getattr(
                        info, "st_ctime_ns", int(info.st_ctime * 1e9),
                    )),
                ),
            )

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
            "raw-manifest-material-hash", "raw-manifest-geometry",
            "raw-manifest-expected", "raw-manifest-entry-pass",
            "visibility", "material-original", "material-private",
            "material-shadow", "material-extra", "python-original",
            "python-private",
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
                    elif label.startswith("raw-manifest-"):
                        path = Path(args[0]) / "render_manifest.json"
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        if label == "raw-manifest-material-hash":
                            payload["material_contract_sha256"] = "0" * 64
                        elif label == "raw-manifest-geometry":
                            payload["entries"][0]["geometry"] = {"forged": True}
                        elif label == "raw-manifest-expected":
                            payload["entries"][0]["expected"] = False
                        else:
                            payload["entries"][0]["pass"] = "clay"
                        path.write_text(
                            json.dumps(payload, sort_keys=True), encoding="utf-8",
                        )
                    elif label == "visibility":
                        path = Path(args[1]).parents[1] / "control" / "source-union-visibility.json"
                        data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 1; path.write_bytes(data)
                    elif label == "material-original":
                        path = fixture.material_roots[1] / "textures/paint.vtf"
                        data = bytearray(path.read_bytes())
                        data[len(data) // 2] ^= 1
                        path.write_bytes(data)
                    elif label == "material-private":
                        path = (
                            Path(args[1]).parents[1]
                            / "material-roots/root-001/textures/paint.vtf"
                        )
                        data = bytearray(path.read_bytes())
                        data[len(data) // 2] ^= 1
                        path.write_bytes(data)
                    elif label == "material-shadow":
                        path = fixture.material_roots[0] / "textures/paint.vtf"
                        path.parent.mkdir()
                        path.write_bytes(b"higher priority")
                    elif label == "material-extra":
                        path = Path(args[1]).parents[1] / "material-roots/root-000/extra.vtf"
                        path.write_bytes(b"extra")
                    elif label == "python-original":
                        path = root / "renderer-runtime/maximum_optimizer/reporting.py"
                        data = bytearray(path.read_bytes())
                        data[len(data) // 2] ^= 1
                        path.write_bytes(data)
                    elif label == "python-private":
                        path = (
                            Path(args[1]).parents[1]
                            / "inputs/maximum_optimizer/reporting.py"
                        )
                        data = bytearray(path.read_bytes())
                        data[len(data) // 2] ^= 1
                        path.write_bytes(data)
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
                if label == "material-extra":
                    self.assertEqual(
                        (workspace / "material-roots/root-000/extra.vtf").read_bytes(),
                        b"extra",
                    )
                else:
                    self.assertFalse(workspace.exists())

    def test_source_union_preserves_exact_private_file_when_comparator_raises(self) -> None:
        from maximum_optimizer import production_adapters as module

        real_compare = module.compare_source_union_render_sets
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            state = {"identity": None}

            def replace_file_then_raise(*args, **kwargs):
                real_compare(*args, **kwargs)
                victim = (
                    Path(args[1]).parents[1]
                    / "material-roots/root-000/vehicles/paint.vmt"
                )
                winner = root / "foreign-compare-paint.vmt"
                shutil.copy2(victim, winner)
                victim.unlink()
                os.rename(winner, victim)
                info = os.lstat(victim)
                state["identity"] = (
                    int(info.st_dev), int(info.st_ino),
                    int(getattr(
                        info, "st_ctime_ns", int(info.st_ctime * 1e9),
                    )),
                )
                raise RuntimeError("hostile comparator failure")

            with mock.patch(
                "maximum_optimizer.production_adapters.compare_source_union_render_sets",
                side_effect=replace_file_then_raise,
            ), self.assertRaisesRegex(RuntimeError, "hostile comparator"):
                self._render_case(
                    root, SourceUnionRunner(fixture), workspace, components,
                )
            self.assertTrue(workspace.exists())
            info = os.lstat(
                workspace / "material-roots/root-000/vehicles/paint.vmt"
            )
            self.assertEqual(
                state["identity"],
                (
                    int(info.st_dev), int(info.st_ino),
                    int(getattr(
                        info, "st_ctime_ns", int(info.st_ctime * 1e9),
                    )),
                ),
            )

    def test_source_union_preserves_replaced_python_runtime_when_comparator_raises(self) -> None:
        from maximum_optimizer import production_adapters as module

        real_compare = module.compare_source_union_render_sets
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture, components = self._render_fixture(root)
            workspace = root / "union"
            state = {"identity": None}

            def replace_runtime_then_raise(*args, **kwargs):
                real_compare(*args, **kwargs)
                victim = (
                    Path(args[1]).parents[1]
                    / "inputs/maximum_optimizer/reporting.py"
                )
                winner = root / "foreign-compare-reporting.py"
                shutil.copy2(victim, winner)
                victim.unlink(); os.rename(winner, victim)
                info = os.lstat(victim)
                state["identity"] = (
                    int(info.st_dev), int(info.st_ino),
                    int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))),
                )
                raise RuntimeError("hostile runtime comparator failure")

            with mock.patch(
                "maximum_optimizer.production_adapters.compare_source_union_render_sets",
                side_effect=replace_runtime_then_raise,
            ), self.assertRaisesRegex(RuntimeError, "hostile runtime comparator"):
                self._render_case(
                    root, SourceUnionRunner(fixture), workspace, components,
                )
            self.assertTrue(workspace.exists())
            info = os.lstat(
                workspace / "inputs/maximum_optimizer/reporting.py"
            )
            self.assertEqual(
                state["identity"],
                (
                    int(info.st_dev), int(info.st_ino),
                    int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))),
                ),
            )

    def test_boundary_never_reacquires_replaced_workspace_after_e1_returns(self) -> None:
        from maximum_optimizer import production_adapters as module
        real_e1 = module.validate_adaptive_direct_source_union
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture, components = self._render_fixture(root)
            workspace = root / "union"; marker = workspace / "external.marker"
            state = {"digest": fixture.requests[0].dependency_proof_sha256}
            def replace_after_success(*args, **kwargs):
                record = real_e1(*args, **kwargs)
                shutil.rmtree(workspace)
                workspace.mkdir()
                marker.write_bytes(b"preserve")
                state["digest"] = "0" * 64
                return record
            with mock.patch(
                "maximum_optimizer.production_adapters.validate_adaptive_direct_source_union",
                side_effect=replace_after_success,
            ), self.assertRaises(ValueError):
                self._render_case(
                    root, SourceUnionRunner(fixture), workspace, components,
                    dependency_provider=lambda _event: state["digest"],
                )
            self.assertEqual(marker.read_bytes(), b"preserve")

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
