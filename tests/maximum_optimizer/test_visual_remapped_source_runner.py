from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from maximum_optimizer.processes import ProcessCancelledError, ProcessResult
from maximum_optimizer.regions import build_region_manifest
from maximum_optimizer.smd_contract import prefilter_direct_degenerate_smd
from maximum_optimizer.visual_remapped_source_runner import (
    BlenderVisualRemappedSourceRunner,
    VisualRemappedRunnerTools,
    visual_remapped_cache_digest,
    visual_remapped_source_evidence_from_payload,
    visual_remapped_source_evidence_payload,
    visual_remapped_source_request,
    visual_remapped_source_request_from_payload,
    visual_remapped_source_request_payload,
)
from tests.maximum_optimizer.test_task6_direct_builder import _request
from tests.maximum_optimizer.test_remapped_topology import _corner, _fan_source
from tests.maximum_optimizer.test_visual_remapped_topology import _boundary_change


def _reseal(payload: dict, field: str) -> None:
    copied = dict(payload)
    copied.pop(field, None)
    payload[field] = hashlib.sha256(json.dumps(
        copied, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


class FakeVisualBlender:
    def __init__(
        self, *, returncode: int = 0, output_text: str | None = None,
        mutate_tool: Path | None = None, metrics_strategy: str | None = None,
        elapsed_seconds: float = 0.01,
    ) -> None:
        self.returncode = returncode
        self.output_text = output_text
        self.mutate_tool = mutate_tool
        self.metrics_strategy = metrics_strategy
        self.elapsed_seconds = elapsed_seconds
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, cwd, log_path, cancel_event):
        normalized = tuple(os.fspath(item) for item in command)
        self.commands.append(normalized)
        split = normalized.index("--")
        root = Path(normalized[split + 1])
        candidate_path = Path(normalized[split + 3])
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        source_bytes = (root / "source.smd").read_bytes()
        output_bytes = (self.output_text or _boundary_change()).encode("utf-8")
        output = root / "output" / "source_opt.smd"
        output.parent.mkdir()
        output.write_bytes(output_bytes)
        manifest = build_region_manifest(
            (("source.smd", "body", ("slot:0:metal",)),),
            occurrences={"source.smd": ({
                "graph_file": "model.qc", "directive": "$body", "line": 2,
                "logical_path": "source.smd",
            },)},
        )
        manifest_bytes = (json.dumps(
            manifest.to_payload(), sort_keys=True, separators=(",", ":"),
        ) + "\n").encode("utf-8")
        (root / "maximum_region_manifest.json").write_bytes(manifest_bytes)
        metrics = {
            "schema_version": 1,
            "candidate_id": candidate["candidate_id"],
            "engine": "meshoptimizer",
            "engine_version": "1.2.0",
            "triangles_before": 4,
            "triangles_after": 2,
            "files": [{
                "source": str(root / "source.smd"),
                "output": str(output),
                "triangles_before": 4,
                "triangles_after": 2,
                "objects": [{
                    "strategy": self.metrics_strategy or candidate["strategy"],
                    "transfer": candidate["transfer"],
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
                "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "output": "output/source_opt.smd",
                "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
            }],
            "region_manifest": "maximum_region_manifest.json",
            "region_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        }
        (root / "candidate_metrics.json").write_text(
            json.dumps(metrics, sort_keys=True), encoding="utf-8",
        )
        Path(log_path).write_text("fake visual blender\n", encoding="utf-8")
        if self.mutate_tool is not None:
            self.mutate_tool.write_bytes(b"mutated")
        return ProcessResult(
            normalized, self.returncode, self.elapsed_seconds, Path(log_path)
        )


class VisualRemappedSourceRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source_text = _fan_source()
        self.source_bytes = self.source_text.encode("utf-8")
        self.input = self.root / "input.smd"
        self.input.write_bytes(self.source_bytes)
        self.base_request = _request(self.source_text)
        self.request = visual_remapped_source_request(
            self.base_request, self.source_bytes,
        )
        self.work_root = self.root / "work"
        self.work_root.mkdir()
        self.blender = self.root / "blender.exe"
        self.blender.write_bytes(b"blender")
        self.meshopt = self.root / "meshopt.dll"
        self.meshopt.write_bytes(b"meshopt")
        self.repo_root = Path(__file__).resolve().parents[2]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def tools(self, process, **overrides) -> VisualRemappedRunnerTools:
        values = {
            "blender_exe": self.blender,
            "repo_root": self.repo_root,
            "meshopt_dll": self.meshopt,
            "work_root": self.work_root,
            "process_runner": process,
        }
        values.update(overrides)
        return VisualRemappedRunnerTools(**values)

    def test_request_is_distinct_strict_and_reparsable(self) -> None:
        self.assertEqual(self.request.strategy, "meshopt-remapped-visual-v1")
        self.assertEqual(self.request.transfer, "visual-remapped-topology-v1")
        self.assertEqual(self.request.quality_status, "unverified")
        self.assertFalse(self.request.authorizing)
        payload = visual_remapped_source_request_payload(self.request)
        self.assertEqual(
            visual_remapped_source_request_from_payload(payload), self.request,
        )

        payload["unknown"] = True
        with self.assertRaisesRegex(ValueError, "fields"):
            visual_remapped_source_request_from_payload(payload)

    def test_real_boundary_builds_sealed_unverified_evidence_and_publishes(self) -> None:
        process = FakeVisualBlender()
        runner = BlenderVisualRemappedSourceRunner(self.tools(process))
        output = self.root / "candidate.smd"

        result = runner(self.input, output, self.request, threading.Event())

        self.assertEqual(output.read_text(encoding="utf-8"), _boundary_change())
        self.assertEqual(len(process.commands), 1)
        candidate = json.loads(
            (result.run_root / "candidate.json").read_text(encoding="utf-8")
        )
        self.assertEqual(candidate["strategy"], "meshopt-remapped-visual-v1")
        self.assertEqual(candidate["transfer"], "visual-remapped-topology-v1")
        self.assertFalse(candidate["update_vertices"])
        self.assertEqual(candidate["ratio"], 0.5)
        evidence = result.evidence
        self.assertEqual(evidence.quality_status, "unverified")
        self.assertFalse(evidence.authorizing)
        self.assertEqual(evidence.topology.triangles_before, 4)
        self.assertEqual(evidence.topology.triangles_after, 2)
        self.assertEqual(evidence.topology.achieved_ratio, 0.5)
        self.assertEqual(
            (evidence.topology.removed_boundary_edges,
             evidence.topology.added_boundary_edges),
            (2, 2),
        )
        self.assertEqual(
            evidence.cache_digest,
            visual_remapped_cache_digest(self.request, evidence.toolchain),
        )
        self.assertEqual(
            visual_remapped_source_evidence_from_payload(
                visual_remapped_source_evidence_payload(evidence)
            ),
            evidence,
        )
        self.assertEqual(
            tuple(item.relative_path for item in evidence.artifacts),
            tuple(sorted(item.relative_path for item in evidence.artifacts)),
        )
        self.assertEqual(tuple(item.role for item in evidence.toolchain.tools), (
            "batch-script", "blender", "meshoptimizer",
        ))

    def test_resealed_stale_cache_and_strategy_mismatch_are_rejected(self) -> None:
        result = BlenderVisualRemappedSourceRunner(
            self.tools(FakeVisualBlender())
        )(self.input, self.root / "candidate.smd", self.request, threading.Event())
        payload = visual_remapped_source_evidence_payload(result.evidence)
        payload["cache_digest"] = "a" * 64
        _reseal(payload, "evidence_sha256")
        with self.assertRaisesRegex(ValueError, "cache"):
            visual_remapped_source_evidence_from_payload(payload)

        with self.assertRaisesRegex(ValueError, "strategy"):
            BlenderVisualRemappedSourceRunner(
                self.tools(FakeVisualBlender(metrics_strategy="meshopt-direct-position-v1"))
            )(self.input, self.root / "other.smd", self.request, threading.Event())

    def test_cancel_failure_tool_mutation_and_artifact_toctou_never_publish(self) -> None:
        event = threading.Event()
        event.set()
        runner = BlenderVisualRemappedSourceRunner(self.tools(FakeVisualBlender()))
        with self.assertRaises(ProcessCancelledError):
            runner(self.input, self.root / "cancelled.smd", self.request, event)
        self.assertEqual(tuple(self.work_root.iterdir()), ())

        cases = (
            ("exit", FakeVisualBlender(returncode=7), RuntimeError),
            ("tool", FakeVisualBlender(mutate_tool=self.meshopt), ValueError),
        )
        for name, process, error in cases:
            with self.subTest(name=name), self.assertRaises(error):
                BlenderVisualRemappedSourceRunner(self.tools(process))(
                    self.input, self.root / f"{name}.smd", self.request,
                    threading.Event(),
                )
            self.meshopt.write_bytes(b"meshopt")

        real_inventory = __import__(
            "maximum_optimizer.visual_remapped_source_runner",
            fromlist=["_bounded_artifact_proofs"],
        )._bounded_artifact_proofs
        calls = {"count": 0}

        def mutate_inventory(root, event, **limits):
            calls["count"] += 1
            if calls["count"] == 2:
                (Path(root) / "candidate.json").write_bytes(b"stale")
            return real_inventory(root, event, **limits)

        with mock.patch(
            "maximum_optimizer.visual_remapped_source_runner._bounded_artifact_proofs",
            side_effect=mutate_inventory,
        ), self.assertRaisesRegex(ValueError, "artifacts changed"):
            BlenderVisualRemappedSourceRunner(self.tools(FakeVisualBlender()))(
                self.input, self.root / "toctou.smd", self.request,
                threading.Event(),
            )
        self.assertFalse((self.root / "toctou.smd").exists())

    def test_tools_and_work_root_are_pinned_at_configuration_time(self) -> None:
        process = FakeVisualBlender()
        tools = self.tools(process)
        self.meshopt.write_bytes(b"changed-before-call")
        with self.assertRaisesRegex(ValueError, "pin changed"):
            BlenderVisualRemappedSourceRunner(tools)(
                self.input, self.root / "stale-tool.smd", self.request,
                threading.Event(),
            )
        self.assertEqual(process.commands, [])
        self.meshopt.write_bytes(b"meshopt")

        process = FakeVisualBlender()
        tools = self.tools(process)
        detached = self.root / "detached-work"
        os.rename(self.work_root, detached)
        self.work_root.mkdir()
        with self.assertRaisesRegex(ValueError, "pin changed"):
            BlenderVisualRemappedSourceRunner(tools)(
                self.input, self.root / "stale-root.smd", self.request,
                threading.Event(),
            )
        self.assertEqual(process.commands, [])

    def test_symlink_artifact_fails_closed_when_platform_allows_it(self) -> None:
        base = FakeVisualBlender()

        def unsafe(command, cwd, log_path, event):
            result = base(command, cwd, log_path, event)
            run_root = Path(command[command.index("--") + 1])
            try:
                (run_root / "unsafe-link").symlink_to(self.root / "outside")
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            return result

        with self.assertRaises(ValueError):
            BlenderVisualRemappedSourceRunner(self.tools(unsafe))(
                self.input, self.root / "unsafe.smd", self.request, threading.Event(),
            )

    def test_budget_collision_and_replaced_root_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "budget"):
            BlenderVisualRemappedSourceRunner(self.tools(
                FakeVisualBlender(), max_artifact_files=3,
            ))(self.input, self.root / "budget.smd", self.request, threading.Event())

        collision = self.root / "collision.smd"
        collision.write_bytes(b"foreign")
        with self.assertRaises(ValueError):
            BlenderVisualRemappedSourceRunner(self.tools(FakeVisualBlender()))(
                self.input, collision, self.request, threading.Event(),
            )
        self.assertEqual(collision.read_bytes(), b"foreign")

        state = {}

        def replace_root(command, cwd, log_path, event):
            result = FakeVisualBlender()(command, cwd, log_path, event)
            run_root = Path(command[command.index("--") + 1])
            owned = run_root.with_name(run_root.name + ".owned")
            os.rename(run_root, owned)
            run_root.mkdir()
            (run_root / "foreign.marker").write_bytes(b"preserve")
            state.update(run_root=run_root, owned=owned)
            return result

        with self.assertRaises((OSError, ValueError)):
            BlenderVisualRemappedSourceRunner(self.tools(replace_root))(
                self.input, self.root / "replaced.smd", self.request,
                threading.Event(),
            )
        self.assertEqual((state["run_root"] / "foreign.marker").read_bytes(), b"preserve")
        self.assertTrue(state["owned"].is_dir())

    def test_timeout_malformed_metrics_provenance_and_transfer_fail_closed(self) -> None:
        with self.assertRaises(ProcessCancelledError):
            BlenderVisualRemappedSourceRunner(self.tools(
                FakeVisualBlender(elapsed_seconds=2.0), max_process_seconds=1.0,
            ))(self.input, self.root / "timeout.smd", self.request, threading.Event())

        mutations = {
            "malformed": None,
            "provenance": ("provenance", "output_sha256", "f" * 64),
            "transfer": ("files", "transfer", "direct-v1"),
        }
        for name, mutation in mutations.items():
            process = FakeVisualBlender()

            def corrupt(command, cwd, log_path, event, *, process=process, mutation=mutation):
                result = process(command, cwd, log_path, event)
                root = Path(command[command.index("--") + 1])
                path = root / "candidate_metrics.json"
                if mutation is None:
                    path.write_bytes(b"{")
                else:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    section, field, value = mutation
                    if section == "provenance":
                        payload["provenance"][0][field] = value
                    else:
                        payload["files"][0]["objects"][0][field] = value
                    path.write_text(json.dumps(payload), encoding="utf-8")
                return result

            with self.subTest(name=name), self.assertRaises(ValueError):
                BlenderVisualRemappedSourceRunner(self.tools(corrupt))(
                    self.input, self.root / f"{name}.smd", self.request,
                    threading.Event(),
                )
            self.assertFalse((self.root / f"{name}.smd").exists())

    def test_source_and_output_mutation_and_structural_regression_never_publish(self) -> None:
        changed = self.source_bytes + b"\n"
        self.input.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "input byte proof"):
            BlenderVisualRemappedSourceRunner(self.tools(FakeVisualBlender()))(
                self.input, self.root / "changed-input.smd", self.request,
                threading.Event(),
            )
        self.input.write_bytes(self.source_bytes)

        import maximum_optimizer.visual_remapped_source_runner as runner_module
        real_copy = runner_module._copy_file_no_follow

        def mutate_before_copy(source, destination, event, **kwargs):
            Path(source).write_bytes(changed)
            return real_copy(source, destination, event, **kwargs)

        with mock.patch(
            "maximum_optimizer.visual_remapped_source_runner._copy_file_no_follow",
            side_effect=mutate_before_copy,
        ), self.assertRaisesRegex(ValueError, "changed during snapshot"):
            BlenderVisualRemappedSourceRunner(self.tools(FakeVisualBlender()))(
                self.input, self.root / "source-toctou.smd", self.request,
                threading.Event(),
            )
        self.input.write_bytes(self.source_bytes)

        bad_output = _boundary_change().replace(
            _corner("a"),
            _corner("a").replace(" 0 0 0 ", " 0.25 0 0 ", 1),
            1,
        )
        with self.assertRaises(RuntimeError):
            BlenderVisualRemappedSourceRunner(self.tools(
                FakeVisualBlender(output_text=bad_output),
            ))(self.input, self.root / "structural.smd", self.request, threading.Event())

        real_publish = runner_module._publish_no_replace

        def mutate_before_publish(source, destination, event, **kwargs):
            Path(source).write_bytes(b"mutated")
            return real_publish(source, destination, event, **kwargs)

        with mock.patch(
            "maximum_optimizer.visual_remapped_source_runner._publish_no_replace",
            side_effect=mutate_before_publish,
        ), self.assertRaisesRegex(ValueError, "publication differs"):
            BlenderVisualRemappedSourceRunner(self.tools(FakeVisualBlender()))(
                self.input, self.root / "output-toctou.smd", self.request,
                threading.Event(),
            )
        self.assertFalse((self.root / "output-toctou.smd").exists())

    def test_resealed_artifact_role_lie_is_rejected(self) -> None:
        evidence = BlenderVisualRemappedSourceRunner(
            self.tools(FakeVisualBlender())
        )(self.input, self.root / "candidate.smd", self.request, threading.Event()).evidence
        payload = visual_remapped_source_evidence_payload(evidence)
        source = next(
            item for item in payload["artifacts"]
            if item["relative_path"] == "source.smd"
        )
        source["kind"] = "auxiliary"
        _reseal(payload, "evidence_sha256")

        with self.assertRaisesRegex(ValueError, "relationships"):
            visual_remapped_source_evidence_from_payload(payload)

        payload = visual_remapped_source_evidence_payload(evidence)
        payload["artifacts"] = [
            item for item in payload["artifacts"]
            if item["relative_path"] != "candidate_metrics.json"
        ]
        _reseal(payload, "evidence_sha256")
        with self.assertRaisesRegex(ValueError, "relationships"):
            visual_remapped_source_evidence_from_payload(payload)

    def test_post_process_cancel_and_publication_collision_preserve_external_bytes(self) -> None:
        event = threading.Event()
        base = FakeVisualBlender()

        def cancel_after_process(command, cwd, log_path, deadline):
            result = base(command, cwd, log_path, deadline)
            event.set()
            return result

        with self.assertRaises(ProcessCancelledError):
            BlenderVisualRemappedSourceRunner(self.tools(cancel_after_process))(
                self.input, self.root / "post-cancel.smd", self.request, event,
            )
        self.assertFalse((self.root / "post-cancel.smd").exists())

        collision = self.root / "racing-output.smd"
        base = FakeVisualBlender()

        def collide_after_process(command, cwd, log_path, deadline):
            result = base(command, cwd, log_path, deadline)
            collision.write_bytes(b"foreign")
            return result

        with self.assertRaises(FileExistsError):
            BlenderVisualRemappedSourceRunner(self.tools(collide_after_process))(
                self.input, collision, self.request, threading.Event(),
            )
        self.assertEqual(collision.read_bytes(), b"foreign")

    def test_validated_metrics_bytes_are_the_bytes_sealed_in_artifacts(self) -> None:
        import maximum_optimizer.visual_remapped_source_runner as runner_module

        real_validate = runner_module._validate_metrics

        def validate_then_swap(root, *args, **kwargs):
            result = real_validate(root, *args, **kwargs)
            path = Path(root) / "candidate_metrics.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["files"][0]["objects"][0]["strategy"] = (
                "meshopt-direct-position-v1"
            )
            path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            return result

        with mock.patch.object(
            runner_module, "_validate_metrics", side_effect=validate_then_swap,
        ), self.assertRaisesRegex(ValueError, "validated artifact"):
            BlenderVisualRemappedSourceRunner(self.tools(FakeVisualBlender()))(
                self.input, self.root / "metrics-race.smd", self.request,
                threading.Event(),
            )
        self.assertFalse((self.root / "metrics-race.smd").exists())

    def test_candidate_qc_and_manifest_are_exact_and_semantically_bound(self) -> None:
        base = FakeVisualBlender()

        def mutate_candidate(command, cwd, log_path, event):
            result = base(command, cwd, log_path, event)
            root = Path(cwd)
            path = root / "candidate.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["ratio"] = 0.9
            path.write_text(json.dumps(payload), encoding="utf-8")
            return result

        with self.assertRaisesRegex(ValueError, "candidate artifact"):
            BlenderVisualRemappedSourceRunner(self.tools(mutate_candidate))(
                self.input, self.root / "mutated-candidate.smd", self.request,
                threading.Event(),
            )

        base = FakeVisualBlender()

        def mutate_qc(command, cwd, log_path, event):
            result = base(command, cwd, log_path, event)
            (Path(cwd) / "model.qc").write_text(
                '$body "wrong" "source.smd"\n', encoding="utf-8",
            )
            return result

        with self.assertRaisesRegex(ValueError, "QC artifact"):
            BlenderVisualRemappedSourceRunner(self.tools(mutate_qc))(
                self.input, self.root / "mutated-qc.smd", self.request,
                threading.Event(),
            )

        base = FakeVisualBlender()

        def invalid_manifest(command, cwd, log_path, event):
            result = base(command, cwd, log_path, event)
            root = Path(cwd)
            raw = b"{}\n"
            (root / "maximum_region_manifest.json").write_bytes(raw)
            metrics = json.loads(
                (root / "candidate_metrics.json").read_text(encoding="utf-8")
            )
            metrics["region_manifest_sha256"] = hashlib.sha256(raw).hexdigest()
            (root / "candidate_metrics.json").write_text(
                json.dumps(metrics), encoding="utf-8",
            )
            return result

        with self.assertRaisesRegex(ValueError, "region manifest"):
            BlenderVisualRemappedSourceRunner(self.tools(invalid_manifest))(
                self.input, self.root / "invalid-manifest.smd", self.request,
                threading.Event(),
            )

    def test_output_parent_identity_change_before_publish_fails_closed(self) -> None:
        import maximum_optimizer.visual_remapped_source_runner as runner_module

        parent = self.root / "destination"
        parent.mkdir()
        detached = self.root / "destination-detached"
        destination = parent / "candidate.smd"
        real_publish = runner_module._publish_no_replace

        def replace_parent(source, output, event, **kwargs):
            os.rename(parent, detached)
            parent.mkdir()
            return real_publish(source, output, event, **kwargs)

        with mock.patch.object(
            runner_module, "_publish_no_replace", side_effect=replace_parent,
        ), self.assertRaisesRegex(ValueError, "output parent changed"):
            BlenderVisualRemappedSourceRunner(self.tools(FakeVisualBlender()))(
                self.input, destination, self.request, threading.Event(),
            )
        self.assertFalse(destination.exists())
        self.assertEqual(tuple(detached.iterdir()), ())

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_tool_reparse_ancestor_is_rejected(self) -> None:
        real = self.root / "real-tools"
        real.mkdir()
        (real / "blender.exe").write_bytes(b"blender")
        (real / "meshopt.dll").write_bytes(b"meshopt")
        alias = self.root / "tool-alias"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(real)],
            capture_output=True, text=True,
        )
        if created.returncode:
            self.skipTest(created.stdout + created.stderr)
        try:
            with self.assertRaisesRegex(ValueError, "unsafe"):
                VisualRemappedRunnerTools(
                    blender_exe=alias / "blender.exe", repo_root=self.repo_root,
                    meshopt_dll=alias / "meshopt.dll", work_root=self.work_root,
                    process_runner=FakeVisualBlender(),
                )
        finally:
            subprocess.run(
                ["cmd", "/c", "rmdir", str(alias)], capture_output=True,
            )

    def test_tools_are_revalidated_immediately_before_spawn(self) -> None:
        import maximum_optimizer.visual_remapped_source_runner as runner_module

        process = FakeVisualBlender()
        real_acquire = runner_module._acquire_run_root

        def acquire_then_mutate(*args, **kwargs):
            result = real_acquire(*args, **kwargs)
            self.meshopt.write_bytes(b"changed-before-spawn")
            return result

        with mock.patch.object(
            runner_module, "_acquire_run_root", side_effect=acquire_then_mutate,
        ), self.assertRaisesRegex(ValueError, "pin changed"):
            BlenderVisualRemappedSourceRunner(self.tools(process))(
                self.input, self.root / "pre-spawn.smd", self.request,
                threading.Event(),
            )
        self.assertEqual(process.commands, [])

    def test_source_request_lineage_rejects_prefix_transplant_and_allows_prefilter(self) -> None:
        transplanted = self.source_text.replace('"root"', '"transplanted"')
        with self.assertRaisesRegex(ValueError, "lineage"):
            visual_remapped_source_request(
                self.base_request, transplanted.encode("utf-8"),
            )

        degenerate = (
            "metal\n"
            "0 0 0 0 0 0 1 0 0\n"
            "0 0 0 0 0 0 1 0 0\n"
            "0 0 0 0 0 0 1 0 0\n"
        )
        raw_text = self.source_text.replace(
            "triangles\n", "triangles\n" + degenerate, 1,
        )
        raw = raw_text.encode("utf-8")
        filtered = prefilter_direct_degenerate_smd(raw_text).filtered_text.encode("utf-8")
        request = visual_remapped_source_request(
            _request(raw_text), filtered, raw_source_bytes=raw,
        )
        self.assertEqual(request.input_sha256, hashlib.sha256(filtered).hexdigest())
        self.input.write_bytes(raw)
        result = BlenderVisualRemappedSourceRunner(
            self.tools(FakeVisualBlender())
        )(
            self.input, self.root / "legitimate-prefilter.smd", request,
            threading.Event(),
        )
        self.assertEqual(result.evidence.source_sha256, request.input_sha256)

    def test_cache_identity_changes_with_toolchain(self) -> None:
        first = BlenderVisualRemappedSourceRunner(
            self.tools(FakeVisualBlender())
        )(self.input, self.root / "first.smd", self.request, threading.Event())
        self.blender.write_bytes(b"new-blender")
        self.meshopt.write_bytes(b"new-meshopt")
        second = BlenderVisualRemappedSourceRunner(
            self.tools(FakeVisualBlender())
        )(self.input, self.root / "second.smd", self.request, threading.Event())
        self.assertNotEqual(first.evidence.toolchain, second.evidence.toolchain)
        self.assertNotEqual(first.evidence.cache_digest, second.evidence.cache_digest)

    def test_control_and_configured_budgets_fail_early(self) -> None:
        with self.assertRaisesRegex(ValueError, "budget"):
            self.tools(FakeVisualBlender(), max_artifact_files=65)
        with self.assertRaisesRegex(ValueError, "budget"):
            self.tools(FakeVisualBlender(), max_artifact_bytes=2 * 1024**3 + 1)

        base = FakeVisualBlender()

        def huge_metrics(command, cwd, log_path, event):
            result = base(command, cwd, log_path, event)
            with (Path(cwd) / "candidate_metrics.json").open("ab") as stream:
                stream.write(b" " * (16 * 1024**2 + 1))
            return result

        with self.assertRaisesRegex(ValueError, "control.*limit"):
            BlenderVisualRemappedSourceRunner(self.tools(huge_metrics))(
                self.input, self.root / "huge-control.smd", self.request,
                threading.Event(),
            )

    def test_direct_construction_with_bad_artifact_type_fails_as_value_error(self) -> None:
        from dataclasses import replace

        evidence = BlenderVisualRemappedSourceRunner(
            self.tools(FakeVisualBlender())
        )(self.input, self.root / "candidate.smd", self.request, threading.Event()).evidence
        with self.assertRaises(ValueError):
            replace(evidence, artifacts=(object(),))


if __name__ == "__main__":
    unittest.main()
