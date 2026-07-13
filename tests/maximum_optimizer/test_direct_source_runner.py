from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from maximum_optimizer.candidates import DirectSourceTools, build_direct_source_snapshot
from maximum_optimizer import direct_source_runner as runner_module
from maximum_optimizer.direct_source_runner import (
    BlenderDirectSourceRunner,
    DirectSourceRunnerTools,
)
from maximum_optimizer.processes import (
    ProcessCancelledError,
    ProcessResult,
)
from maximum_optimizer.smd_contract import prefilter_direct_degenerate_smd
from tests.maximum_optimizer.test_task6_direct_builder import (
    _first_triangle,
    _request,
    _source,
)


class FakeBlenderProcess:
    def __init__(
        self, *, returncode: int = 0, mutate_tool: Path | None = None,
        output_transform=None,
    ) -> None:
        self.returncode = returncode
        self.mutate_tool = mutate_tool
        self.output_transform = output_transform
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, cwd, log_path, cancel_event):
        normalized = tuple(os.fspath(item) for item in command)
        self.commands.append(normalized)
        split = normalized.index("--")
        root = Path(normalized[split + 1])
        candidate_path = Path(normalized[split + 3])
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        source = (root / "source.smd").read_text(encoding="utf-8")
        optimized = _first_triangle(source).encode("utf-8")
        if self.output_transform is not None:
            optimized = self.output_transform(source).encode("utf-8")
        output = root / "output" / "source_opt.smd"
        output.parent.mkdir()
        output.write_bytes(optimized)
        (root / "maximum_region_manifest.json").write_bytes(b"{}\n")
        metrics = {
            "schema_version": 1,
            "candidate_id": candidate["candidate_id"],
            "engine": "meshoptimizer",
            "engine_version": "1.2.0",
            "triangles_before": 2,
            "triangles_after": 1,
            "files": [{
                "source": str(root / "source.smd"),
                "output": str(output),
                "triangles_before": 2,
                "triangles_after": 1,
                "objects": [{
                    "strategy": "meshopt-direct-position-v1",
                    "transfer": "direct-v1",
                }],
            }],
            "provenance": [{
                "graph_file": "model.qc",
                "directive": "$body",
                "line": 2,
                "logical_path": "source.smd",
                "role": "visual",
                "status": "optimized",
                "reason": "meshoptimizer-attribute-aware",
                "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "output": "output/source_opt.smd",
                "output_sha256": hashlib.sha256(optimized).hexdigest(),
            }],
            "region_manifest": "maximum_region_manifest.json",
            "region_manifest_sha256": hashlib.sha256(b"{}\n").hexdigest(),
        }
        (root / "candidate_metrics.json").write_text(
            json.dumps(metrics, sort_keys=True), encoding="utf-8"
        )
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("fake blender\n", encoding="utf-8")
        if self.mutate_tool is not None:
            self.mutate_tool.write_bytes(b"mutated")
        return ProcessResult(normalized, self.returncode, 0.01, Path(log_path))


class DirectSourceRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source_root = self.root / "source-root"
        self.source_root.mkdir()
        self.source_text = _source()
        (self.source_root / "body.smd").write_bytes(self.source_text.encode("utf-8"))
        self.request = _request(self.source_text)
        self.work_root = self.root / "runner-work"
        self.work_root.mkdir()
        self.blender = self.root / "blender.exe"
        self.blender.write_bytes(b"blender")
        self.meshopt = self.root / "meshopt.dll"
        self.meshopt.write_bytes(b"meshopt")
        self.repo_root = Path(__file__).resolve().parents[2]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def tools(self, process, **overrides) -> DirectSourceRunnerTools:
        values = {
            "blender_exe": self.blender,
            "repo_root": self.repo_root,
            "meshopt_dll": self.meshopt,
            "work_root": self.work_root,
            "process_runner": process,
        }
        values.update(overrides)
        return DirectSourceRunnerTools(**values)

    def test_real_boundary_materializes_minimal_root_and_builds_snapshot(self) -> None:
        process = FakeBlenderProcess()
        runner = BlenderDirectSourceRunner(self.tools(process))
        workspace = self.root / "snapshot"
        results = []

        def capture(input_path, output_path, request, event):
            result = runner(input_path, output_path, request, event)
            results.append(result)
            return result

        snapshot = build_direct_source_snapshot(
            self.request,
            workspace,
            DirectSourceTools(self.source_root, capture),
            threading.Event(),
        )

        self.assertEqual(snapshot.triangles_after, 1)
        self.assertEqual(tuple(path.name for path in workspace.iterdir()), ("output.smd",))
        self.assertEqual(len(process.commands), 1)
        self.assertIn("--python-exit-code", process.commands[0])
        self.assertEqual(
            process.commands[0][process.commands[0].index("--python-exit-code") + 1],
            "1",
        )
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertTrue(result.run_root.is_dir())
        self.assertEqual(result.request_sha256, self.request.request_sha256)
        self.assertEqual(
            tuple(item.relative_path for item in result.artifacts),
            tuple(sorted(item.relative_path for item in result.artifacts)),
        )
        candidate = json.loads((result.run_root / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(candidate["strategy"], "meshopt-direct-position-v1")
        self.assertEqual(candidate["transfer"], "direct-v1")
        self.assertEqual(candidate["target_error"], 0.01)
        self.assertEqual(
            candidate["direct_degenerate_prefilter"],
            "direct-degenerate-prefilter-v1",
        )
        self.assertEqual(
            (result.run_root / "model.qc").read_text(encoding="utf-8"),
            '$modelname "maximum/direct.mdl"\n$body "body" "source.smd"\n',
        )

    def test_pre_cancel_does_not_create_request_root_or_launch(self) -> None:
        process = FakeBlenderProcess()
        runner = BlenderDirectSourceRunner(self.tools(process))
        event = threading.Event()
        event.set()
        input_path = self.root / "input.smd"
        input_path.write_bytes(
            prefilter_direct_degenerate_smd(self.source_text).filtered_text.encode("utf-8")
        )

        with self.assertRaises(ProcessCancelledError):
            runner(input_path, self.root / "output.smd", self.request, event)

        self.assertEqual(process.commands, [])
        self.assertEqual(tuple(self.work_root.iterdir()), ())

    def test_nonzero_process_is_quarantined_and_never_publishes(self) -> None:
        process = FakeBlenderProcess(returncode=7)
        runner = BlenderDirectSourceRunner(self.tools(process))
        input_path = self.root / "input.smd"
        input_path.write_bytes(
            prefilter_direct_degenerate_smd(self.source_text).filtered_text.encode("utf-8")
        )
        output = self.root / "output.smd"

        with self.assertRaisesRegex(RuntimeError, "exit code 7"):
            runner(input_path, output, self.request, threading.Event())

        self.assertFalse(output.exists())
        quarantines = tuple(self.work_root.glob(".*.direct-source-quarantine-*"))
        self.assertEqual(len(quarantines), 1)
        self.assertTrue((quarantines[0] / "candidate_metrics.json").is_file())

    def test_artifact_budget_and_unsafe_tree_fail_closed(self) -> None:
        for mode in ("budget", "symlink"):
            with self.subTest(mode=mode):
                process = FakeBlenderProcess()
                if mode == "symlink":
                    original = process.__call__

                    def unsafe(command, cwd, log_path, event):
                        result = original(command, cwd, log_path, event)
                        run_root = Path(command[command.index("--") + 1])
                        try:
                            (run_root / "unsafe-link").symlink_to(self.root / "outside")
                        except OSError as exc:
                            self.skipTest(f"symlink unavailable: {exc}")
                        return result

                    process_runner = unsafe
                    max_files = 64
                else:
                    process_runner = process
                    max_files = 3
                runner = BlenderDirectSourceRunner(self.tools(
                    process_runner, max_artifact_files=max_files,
                ))
                input_path = self.root / f"input-{mode}.smd"
                input_path.write_bytes(
                    prefilter_direct_degenerate_smd(self.source_text).filtered_text.encode("utf-8")
                )
                with self.assertRaises(ValueError):
                    runner(
                        input_path, self.root / f"output-{mode}.smd",
                        self.request, threading.Event(),
                    )
                quarantines = tuple(self.work_root.glob(".*.direct-source-quarantine-*"))
                self.assertTrue(quarantines)

    def test_tool_mutation_and_output_collision_preserve_external_bytes(self) -> None:
        process = FakeBlenderProcess(mutate_tool=self.meshopt)
        runner = BlenderDirectSourceRunner(self.tools(process))
        input_path = self.root / "input-tool.smd"
        input_path.write_bytes(
            prefilter_direct_degenerate_smd(self.source_text).filtered_text.encode("utf-8")
        )
        with self.assertRaisesRegex(ValueError, "tool changed"):
            runner(input_path, self.root / "tool-output.smd", self.request, threading.Event())

        self.meshopt.write_bytes(b"meshopt")
        process = FakeBlenderProcess()
        output = self.root / "foreign-output.smd"

        def collide(command, cwd, log_path, event):
            result = process(command, cwd, log_path, event)
            output.write_bytes(b"foreign")
            return result

        runner = BlenderDirectSourceRunner(self.tools(collide))
        with self.assertRaises(FileExistsError):
            runner(input_path, output, self.request, threading.Event())
        self.assertEqual(output.read_bytes(), b"foreign")

    def test_artifact_change_at_final_prepublication_barrier_never_publishes(self) -> None:
        process = FakeBlenderProcess()
        runner = BlenderDirectSourceRunner(self.tools(process))
        input_path = self.root / "input-stale.smd"
        input_path.write_bytes(
            prefilter_direct_degenerate_smd(self.source_text).filtered_text.encode("utf-8")
        )
        output = self.root / "stale-output.smd"
        real_inventory = runner_module._bounded_artifact_proofs
        calls = {"count": 0}

        def mutate_before_final(root, event, **budgets):
            calls["count"] += 1
            if calls["count"] == 2:
                (Path(root) / "candidate.json").write_bytes(b"changed")
            return real_inventory(root, event, **budgets)

        with mock.patch(
            "maximum_optimizer.direct_source_runner._bounded_artifact_proofs",
            side_effect=mutate_before_final,
        ), self.assertRaisesRegex(ValueError, "artifacts changed"):
            runner(input_path, output, self.request, threading.Event())
        self.assertFalse(output.exists())

    def test_novel_meshopt_triangle_fails_before_publication(self) -> None:
        def novel_triangle(text: str) -> str:
            lines = text.splitlines(keepends=True)
            start = next(
                index for index, line in enumerate(lines)
                if line.strip().casefold() == "triangles"
            )
            # One syntactically valid triangle assembled from two different source
            # triangles.  It is exactly the class of novel collapse topology that
            # the sealed retained-cycle contract forbids.
            return "".join(
                lines[: start + 2]
                + [lines[start + 2], lines[start + 3], lines[start + 8]]
                + ["end\n"]
            )

        process = FakeBlenderProcess(output_transform=novel_triangle)
        runner = BlenderDirectSourceRunner(self.tools(process))
        input_path = self.root / "input-novel.smd"
        input_path.write_bytes(
            prefilter_direct_degenerate_smd(self.source_text).filtered_text.encode("utf-8")
        )
        output = self.root / "novel-output.smd"

        with self.assertRaisesRegex(RuntimeError, "cycle or winding"):
            runner(input_path, output, self.request, threading.Event())

        self.assertFalse(output.exists())
        self.assertEqual(
            len(tuple(self.work_root.glob(".*.direct-source-quarantine-*"))), 1,
        )

    def test_replaced_request_root_is_never_quarantined_or_deleted_by_name(self) -> None:
        base = FakeBlenderProcess()
        state = {"run_root": None, "owned": None}

        def replace_root(command, cwd, log_path, event):
            result = base(command, cwd, log_path, event)
            run_root = Path(command[command.index("--") + 1])
            owned = run_root.with_name(run_root.name + ".detached-owned")
            os.rename(run_root, owned)
            run_root.mkdir()
            (run_root / "foreign.marker").write_bytes(b"preserve")
            state.update(run_root=run_root, owned=owned)
            return result

        runner = BlenderDirectSourceRunner(self.tools(replace_root))
        input_path = self.root / "input-replaced-root.smd"
        input_path.write_bytes(
            prefilter_direct_degenerate_smd(self.source_text).filtered_text.encode("utf-8")
        )

        with self.assertRaises((OSError, ValueError)):
            runner(
                input_path, self.root / "replaced-root-output.smd",
                self.request, threading.Event(),
            )

        self.assertEqual((state["run_root"] / "foreign.marker").read_bytes(), b"preserve")
        self.assertTrue(state["owned"].is_dir())


if __name__ == "__main__":
    unittest.main()
