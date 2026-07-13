from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path

import render_previews

from maximum_optimizer.candidates import CandidateTools
from maximum_optimizer.composite import compose_candidate_sources
from maximum_optimizer.domain import CandidateSpec, FamilyManifest, StructuralFingerprint
from maximum_optimizer.processes import ProcessResult
from maximum_optimizer.production_adapters import (
    ProductionAdapters,
    SourceUnionPoseBinding,
    validate_source_union_cli_contract,
)
from tests.maximum_optimizer.test_task6_direct_compositor import DirectCompositorFixture


def _fingerprint() -> StructuralFingerprint:
    return StructuralFingerprint("vehicles/test.mdl", (), (), (), (), (), (), (), (), (), (), None)


class CompileRunner:
    def __init__(self, model_rel: str, *, extra=False) -> None:
        self.model_rel = model_rel
        self.extra = extra
        self.commands = []

    def __call__(self, command, cwd, log_path, cancel_event):
        command = tuple(str(item) for item in command)
        self.commands.append(command)
        out = Path(command[command.index("--out") + 1])
        model = out / "models" / Path(*self.model_rel.split("/"))
        model.parent.mkdir(parents=True, exist_ok=True)
        for suffix in (".mdl", ".vvd", ".dx90.vtx"):
            model.with_suffix(suffix).write_bytes((suffix + " current").encode())
        if self.extra:
            (out / "models" / "unrelated.bin").write_bytes(b"extra")
        (out / "compile_summary.json").write_text(json.dumps({
            "results": [{
                "model_rel": self.model_rel, "status": "ok", "returncode": 0,
                "expected_mdl": str(model),
            }],
        }), encoding="utf-8")
        return ProcessResult(command, 0, 0.01, Path(log_path))


class ProductionAdapterContractTests(unittest.TestCase):
    def _compile_case(self, root: Path, runner, *, event=None, mutate_composed=None):
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
        result = ProductionAdapters(process_runner=runner).compile_adaptive_direct_candidate(
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

    def test_pose_binding_supports_bind_and_proven_anchor_but_rejects_stale(self) -> None:
        bind = SourceUnionPoseBinding.bind()
        self.assertEqual((bind.pose_key, bind.frame), ("bind", 0))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            before = root / "before.smd"; after = root / "after.smd"
            before.write_bytes(b"before animation"); after.write_bytes(b"after animation")
            anchor = SourceUnionPoseBinding.anchor(
                "turn", 12, before, after,
                hashlib.sha256(before.read_bytes()).hexdigest(),
                hashlib.sha256(after.read_bytes()).hexdigest(),
            )
            anchor.revalidate(threading.Event())
            before.write_bytes(b"stale animation")
            with self.assertRaises(ValueError):
                anchor.revalidate(threading.Event())
        with self.assertRaises(ValueError):
            SourceUnionPoseBinding("turn", 12, None, None, None, None)

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
            result = ProductionAdapters(process_runner=runner).compile_adaptive_direct_candidate(
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

    def test_compile_adapter_rejects_extra_artifact_and_wrong_composition_before_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runner = CompileRunner("task6.mdl", extra=True)
            with self.assertRaises(ValueError):
                self._compile_case(root, runner)
            self.assertEqual(len(runner.commands), 1)
            self.assertFalse((root / "composed" / "compiled").exists())
            self.assertFalse((root / "composed" / "logs").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runner = CompileRunner("task6.mdl")
            event = threading.Event(); event.set()
            with self.assertRaises(Exception):
                self._compile_case(root, runner, event=event)
            self.assertEqual(runner.commands, [])
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
