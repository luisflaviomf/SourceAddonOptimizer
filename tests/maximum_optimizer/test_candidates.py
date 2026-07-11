from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from maximum_optimizer.candidates import (
    BlenderAdapter,
    CandidateBuildError,
    CandidateTools,
    FidelityAdapter,
)
from maximum_optimizer.domain import CandidateSpec, FamilyManifest, StructuralFingerprint
from maximum_optimizer.processes import ProcessCancelledError, ProcessResult, run_process


class ProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _script(self, name: str, body: str) -> Path:
        path = self.root / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_captures_stdout_stderr_nonzero_exit_and_quoted_command(self):
        script = self._script(
            "fail script.py",
            "import sys\n"
            "sys.stdout.buffer.write('out-utf8-ç\\n'.encode('utf-8'))\n"
            "sys.stderr.buffer.write('err-utf8-ã\\n'.encode('utf-8'))\n"
            "raise SystemExit(7)\n",
        )
        log_path = self.root / "logs" / "process.log"

        result = run_process(
            [Path(sys.executable), script, "argument with spaces"],
            self.root,
            log_path,
            threading.Event(),
        )

        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.command, (str(Path(sys.executable)), str(script), "argument with spaces"))
        self.assertGreaterEqual(result.elapsed_seconds, 0)
        self.assertEqual(result.log_path, log_path)
        log = log_path.read_text(encoding="utf-8")
        self.assertIn("argument with spaces", log)
        self.assertIn("== stdout ==", log)
        self.assertIn("out-utf8-ç", log)
        self.assertIn("== stderr ==", log)
        self.assertIn("err-utf8-ã", log)

    def test_cancel_before_launch_does_not_start_process(self):
        marker = self.root / "started.txt"
        script = self._script("approve.py", f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n")
        cancelled = threading.Event()
        cancelled.set()

        with self.assertRaises(ProcessCancelledError):
            run_process([sys.executable, script], self.root, self.root / "cancel.log", cancelled)

        self.assertFalse(marker.exists())

    def test_cancel_during_wait_reaps_process(self):
        script = self._script("sleep.py", "import time\ntime.sleep(60)\n")
        cancelled = threading.Event()
        timer = threading.Timer(0.2, cancelled.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(ProcessCancelledError):
                run_process([sys.executable, script], self.root, self.root / "cancel.log", cancelled)
        finally:
            timer.cancel()

        self.assertLess(time.monotonic() - started, 5)

    @unittest.skipUnless(os.name == "nt", "Windows process-tree semantics")
    def test_cancel_terminates_spawned_child_tree_on_windows(self):
        child_pid_path = self.root / "child.pid"
        child = self._script("child.py", "import time\ntime.sleep(60)\n")
        parent = self._script(
            "spawn-child.py",
            "import subprocess, sys, time\n"
            f"p = subprocess.Popen([sys.executable, {str(child)!r}])\n"
            f"open({str(child_pid_path)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(60)\n",
        )
        cancelled = threading.Event()

        def cancel_after_child_starts() -> None:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not child_pid_path.exists():
                time.sleep(0.02)
            cancelled.set()

        trigger = threading.Thread(target=cancel_after_child_starts)
        trigger.start()
        with self.assertRaises(ProcessCancelledError):
            run_process([sys.executable, parent], self.root, self.root / "tree.log", cancelled)
        trigger.join(timeout=5)
        self.assertTrue(child_pid_path.exists(), "child never started")
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))

        deadline = time.monotonic() + 3
        alive = True
        while time.monotonic() < deadline:
            query = subprocess.run(
                ["tasklist", "/FI", f"PID eq {child_pid}", "/NH"],
                capture_output=True,
                text=True,
                shell=False,
            )
            alive = str(child_pid) in query.stdout
            if not alive:
                break
            time.sleep(0.05)
        self.assertFalse(alive, f"child process {child_pid} survived cancellation")


class MaterializingRunner:
    def __init__(self, model_rel: str, *, fail_stage: str | None = None, record_status: str = "ok") -> None:
        self.model_rel = model_rel
        self.fail_stage = fail_stage
        self.record_status = record_status
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, cwd, log_path, cancel_event) -> ProcessResult:
        normalized = tuple(str(item) for item in command)
        self.commands.append(normalized)
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("materialized test process\n", encoding="utf-8")
        is_compile = any(Path(item).name.casefold() == "batch_compile_opt_qc.py" for item in normalized)
        stage = "compile" if is_compile else "optimize"
        if self.fail_stage == stage:
            return ProcessResult(normalized, 9, 0.01, log_path)

        if is_compile:
            out_dir = Path(normalized[normalized.index("--out") + 1])
            model_path = out_dir / "models" / Path(self.model_rel)
            model_path.parent.mkdir(parents=True, exist_ok=True)
            model_path.write_bytes(b"mdl")
            model_path.with_suffix(".vvd").write_bytes(b"vvd")
            model_path.with_suffix(".dx90.vtx").write_bytes(b"vtx")
            (model_path.parent / "ignore.txt").write_text("ignore", encoding="utf-8")
            summary = {
                "total": 1,
                "ok": 1 if self.record_status == "ok" else 0,
                "fail": 0 if self.record_status == "ok" else 1,
                "results": [
                    {
                        "index": 1,
                        "status": self.record_status,
                        "qc_path": "src/main_OPT.qc",
                        "model_rel": self.model_rel.swapcase(),
                        "message": "",
                    }
                ],
            }
            (out_dir / "compile_summary.json").write_text(json.dumps(summary), encoding="utf-8")
        else:
            src = Path(normalized[normalized.index("--") + 1])
            (src / "main_OPT.qc").write_text(
                f'$modelname "{self.model_rel}"\n$body "body" "mesh_OPT.smd"\n',
                encoding="utf-8",
            )
            (src / "mesh_OPT.smd").write_text("version 1\n", encoding="utf-8")
        return ProcessResult(normalized, 0, 0.01, log_path)


class CandidateAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for script in (
            "batch_optimize_qc.py",
            "batch_optimize_round_parts_policy.py",
            "batch_compile_opt_qc.py",
        ):
            (self.repo / script).write_text("# test tool\n", encoding="utf-8")
        self.python = self.root / "python.exe"
        self.blender = self.root / "blender.exe"
        self.studiomdl = self.root / "studiomdl.exe"
        for tool in (self.python, self.blender, self.studiomdl):
            tool.write_bytes(b"tool")
        self.heuristic_map = self.root / "heuristics.json"
        self.heuristic_map.write_text('{"entries": []}', encoding="utf-8")
        self.source = self.root / "family-source"
        self.source.mkdir()
        (self.source / "main.qc").write_text(
            '$modelname "vehicles/test.mdl"\n$body "body" "mesh.smd"\n', encoding="utf-8"
        )
        (self.source / "mesh.smd").write_text("version 1\n", encoding="utf-8")
        (self.source / "nested").mkdir()
        (self.source / "nested" / "family.dat").write_bytes(b"family")
        self.original_models = self.root / "original-models"
        self.original_models.mkdir()
        (self.original_models / "must-not-clone.mdl").write_bytes(b"original")
        fingerprint = StructuralFingerprint(
            model_name="vehicles/test.mdl",
            bodygroups=("body",),
            materials=(),
            skin_families=(),
            bones=(),
            bone_parents=(),
            attachments=(),
            hitboxes=(),
            sequences=(),
            mesh_files=("mesh.smd",),
            lod_mesh_files=(),
            physics_mesh=None,
        )
        self.manifest = FamilyManifest(
            "family-1",
            "vehicles/test.mdl",
            self.source,
            self.original_models,
            fingerprint,
            "input-hash",
            (".mdl", ".vvd", ".vtx"),
        )
        self.spec = CandidateSpec("fidelity-r055", "fidelity", 0.55, 0.01, "repair-v1")
        self.workspace = self.root / "candidate"
        self.tools = CandidateTools(
            self.python,
            self.blender,
            self.studiomdl,
            self.repo,
            heuristic_map=self.heuristic_map,
            compile_jobs=2,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_fidelity_adapter_preserves_validated_stack_and_materializes_isolated_build(self):
        runner = MaterializingRunner(self.manifest.model_rel)
        build = FidelityAdapter(process_runner=runner).generate(
            self.manifest, self.spec, self.workspace, self.tools
        )

        optimize = build.commands[0]
        joined = " ".join(optimize)
        self.assertIn("batch_optimize_round_parts_policy.py", joined)
        self.assertIn("--ratio 0.55", joined)
        self.assertIn("--merge 0", joined)
        self.assertIn("--autosmooth 45", joined)
        self.assertIn("--format smd", joined)
        self.assertIn("--ground-final-autosmooth 35", joined)
        self.assertIn("--ground-weighted-mode FACE_AREA_WITH_ANGLE", joined)
        self.assertIn("--ground-weighted-weight 50", joined)
        self.assertIn("--ground-shade-smooth", optimize)
        self.assertIn("--wheel-variant silhouette_floor_20", joined)
        self.assertIn("--embedded-variant floor_24", joined)
        self.assertIn("--summary-dir", optimize)
        self.assertTrue((self.workspace / "src" / "nested" / "family.dat").is_file())
        self.assertFalse((self.workspace / "src" / "must-not-clone.mdl").exists())
        self.assertTrue((self.workspace / "logs" / "vehicle_steer_turn_basis_fix_summary.json").is_file())
        self.assertEqual(build.optimized_qc, self.workspace / "src" / "main_OPT.qc")
        self.assertEqual(build.compiled_models_dir, self.workspace / "compiled" / "models")
        self.assertEqual(build.compile_record["status"], "ok")
        self.assertEqual(
            build.provenance,
            {
                "vehicles/test.dx90.vtx": "candidate-compile",
                "vehicles/test.mdl": "candidate-compile",
                "vehicles/test.vvd": "candidate-compile",
            },
        )
        self.assertEqual(len(build.commands), 2)
        compile_command = build.commands[1]
        self.assertEqual(compile_command[:2], (str(self.python), str(self.repo / "batch_compile_opt_qc.py")))
        self.assertIn("--compile-jobs", compile_command)
        self.assertEqual(compile_command[compile_command.index("--compile-jobs") + 1], "2")
        self.assertIn("--no-restore-phy", compile_command)
        self.assertNotIn("--restore-skin-from", compile_command)
        self.assertNotIn("--restore-phy-from", compile_command)

    def test_blender_adapter_uses_baseline_script_and_ratio(self):
        spec = CandidateSpec("blender-r04", "blender", 0.4, 0.02, "repair-v1")
        build = BlenderAdapter(process_runner=MaterializingRunner(self.manifest.model_rel)).generate(
            self.manifest, spec, self.workspace, self.tools
        )

        self.assertEqual(
            build.commands[0][:4],
            (
                str(self.blender),
                "--background",
                "--python",
                str(self.repo / "batch_optimize_qc.py"),
            ),
        )
        self.assertIn("--ratio 0.4 --merge 0 --autosmooth 45 --format smd", " ".join(build.commands[0]))
        self.assertFalse((self.workspace / "logs" / "vehicle_steer_turn_basis_fix_summary.json").exists())

    def test_build_copies_compile_mappings_and_makes_them_read_only(self):
        build = BlenderAdapter(process_runner=MaterializingRunner(self.manifest.model_rel)).generate(
            self.manifest, self.spec, self.workspace, self.tools
        )

        with self.assertRaises(TypeError):
            build.compile_record["status"] = "failed"
        with self.assertRaises(TypeError):
            build.provenance["new.mdl"] = "original"
        with self.assertRaises(FrozenInstanceError):
            build.workspace = self.root

    def test_nonzero_optimize_fails_with_stage_and_log_without_compile_fallback(self):
        runner = MaterializingRunner(self.manifest.model_rel, fail_stage="optimize")

        with self.assertRaises(CandidateBuildError) as caught:
            BlenderAdapter(process_runner=runner).generate(self.manifest, self.spec, self.workspace, self.tools)

        self.assertEqual(caught.exception.stage, "optimize")
        self.assertEqual(caught.exception.log_path, self.workspace / "logs" / "optimize.log")
        self.assertEqual(len(runner.commands), 1)

    def test_nonzero_compile_and_failed_summary_record_do_not_fallback(self):
        cases = (("compile", "ok"), (None, "failed"))
        for index, (fail_stage, record_status) in enumerate(cases):
            with self.subTest(fail_stage=fail_stage, record_status=record_status):
                workspace = self.root / f"candidate-{index}"
                runner = MaterializingRunner(
                    self.manifest.model_rel, fail_stage=fail_stage, record_status=record_status
                )
                with self.assertRaises(CandidateBuildError) as caught:
                    BlenderAdapter(process_runner=runner).generate(
                        self.manifest, self.spec, workspace, self.tools
                    )
                self.assertEqual(caught.exception.stage, "compile")
                self.assertEqual(caught.exception.log_path, workspace / "logs" / "compile.log")
                self.assertFalse((workspace / "compiled" / "models" / "original.mdl").exists())

    def test_rejects_nonempty_or_nested_workspace_before_copying(self):
        self.workspace.mkdir()
        marker = self.workspace / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(CandidateBuildError, "not empty"):
            BlenderAdapter(process_runner=MaterializingRunner(self.manifest.model_rel)).generate(
                self.manifest, self.spec, self.workspace, self.tools
            )
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

        nested = self.source / "candidate"
        with self.assertRaisesRegex(CandidateBuildError, "inside source"):
            BlenderAdapter(process_runner=MaterializingRunner(self.manifest.model_rel)).generate(
                self.manifest, self.spec, nested, self.tools
            )

    def test_rejects_ambiguous_source_qcs_for_same_model(self):
        (self.source / "duplicate.qc").write_text(
            '$modelname "VEHICLES/TEST.MDL"\n$body "body" "mesh.smd"\n', encoding="utf-8"
        )

        with self.assertRaisesRegex(CandidateBuildError, "ambiguous"):
            BlenderAdapter(process_runner=MaterializingRunner(self.manifest.model_rel)).generate(
                self.manifest, self.spec, self.workspace, self.tools
            )

    def test_accepts_nested_qc_with_shared_include_inside_family_root(self):
        (self.source / "main.qc").unlink()
        (self.source / "mesh.smd").unlink()
        qc_dir = self.source / "nested" / "qc"
        qc_dir.mkdir()
        shared = self.source / "shared"
        shared.mkdir()
        (qc_dir / "main.qc").write_text(
            '$modelname "vehicles/test.mdl"\n$include "../../shared/body.qci"\n',
            encoding="utf-8",
        )
        (shared / "body.qci").write_text('$body "body" "mesh.smd"\n', encoding="utf-8")
        (shared / "mesh.smd").write_text("version 1\n", encoding="utf-8")

        build = BlenderAdapter(process_runner=MaterializingRunner(self.manifest.model_rel)).generate(
            self.manifest, self.spec, self.workspace, self.tools
        )

        self.assertTrue((build.workspace / "src" / "shared" / "body.qci").is_file())

    def test_validates_all_tools_and_fidelity_map_before_launch(self):
        missing_tools = CandidateTools(
            self.python,
            self.root / "missing-blender.exe",
            self.studiomdl,
            self.repo,
            heuristic_map=None,
        )
        runner = MaterializingRunner(self.manifest.model_rel)

        with self.assertRaisesRegex(CandidateBuildError, "blender"):
            BlenderAdapter(process_runner=runner).generate(
                self.manifest, self.spec, self.workspace, missing_tools
            )
        self.assertEqual(runner.commands, [])

        fidelity_tools = CandidateTools(
            self.python, self.blender, self.studiomdl, self.repo, heuristic_map=None
        )
        with self.assertRaisesRegex(CandidateBuildError, "heuristic"):
            FidelityAdapter(process_runner=runner).generate(
                self.manifest, self.spec, self.root / "fidelity", fidelity_tools
            )
        self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main()
