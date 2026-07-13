from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from maximum_optimizer.candidates import (
    DirectSourceTools, _direct_prefilter_proof, _validate_direct_smd_output,
    build_direct_source_snapshot,
)
from maximum_optimizer.composite import build_direct_source_request
from maximum_optimizer.processes import ProcessCancelledError
from maximum_optimizer.smd_contract import prefilter_direct_degenerate_smd
from tests.maximum_optimizer.test_task6_contracts import H


def _source() -> str:
    return (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\nmetal\n'
        '0 2 0 0 0 0 1 0 0\n0 2 0 0 0 0 1 0 0\n0 2 0 0 0 0 1 0 0\n'
        'metal\n0 0 0 0 0 0 1 0 0\n0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\n'
        'metal\n0 0 0 1 0 0 1 0 0\n0 1 0 1 0 0 1 1 0\n0 0 1 1 0 0 1 0 1\nend\n'
    )


def _proof(text: str):
    return _direct_prefilter_proof(text)


def _request(text: str, *, expected_prefilter=None):
    data = text.encode("utf-8")
    return build_direct_source_request(
        family_id=H["0"], family_input_sha256=H["1"],
        base_candidate_id="base", base_spec_sha256=H["2"],
        base_cache_digest=H["3"], base_source_manifest_sha256=H["4"],
        base_source_snapshot_sha256=H["5"], coverage_manifest_sha256=H["6"],
        source_coverage_sha256=H["7"], optimizer_contract_sha256=H["8"],
        whole_profile_sha256=H["9"], focused_profile_sha256=H["a"],
        dependency_proof_sha256=H["b"], source_identity="body.smd",
        source_relative_path="body.smd", source_size=len(data),
        source_sha256=hashlib.sha256(data).hexdigest(), direct_ratio=0.5,
        expected_prefilter=_proof(text) if expected_prefilter is None else expected_prefilter,
    )


def _first_triangle(text: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.strip().casefold() == "triangles")
    return "".join(lines[:start + 1] + lines[start + 1:start + 5] + ["end\n"])


def _reverse_first(text: str) -> str:
    lines = _first_triangle(text).splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.strip().casefold() == "triangles")
    return "".join(
        lines[:start + 2] + [lines[start + 2], lines[start + 4], lines[start + 3]] + ["end\n"]
    )


class DirectSourceBuilderTests(unittest.TestCase):
    def test_fixed_ratios_reject_output_above_deterministic_target(self) -> None:
        prefix = (
            'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
            '0 0 0 0 0 0 0\nend\ntriangles\n'
        )
        records = []
        for index in range(10):
            x = index * 2
            records.append(
                f'metal\n0 {x} 0 0 0 0 1 0 0\n0 {x + 1} 0 0 0 0 1 1 0\n'
                f'0 {x} 1 0 0 0 1 0 1\n'
            )
        source = prefix + "".join(records) + "end\n"
        for ratio, target in ((0.50, 5), (0.45, 4), (0.40, 4), (0.35, 3)):
            output = prefix + "".join(records[:target + 1]) + "end\n"
            with self.subTest(ratio=ratio), self.assertRaisesRegex(ValueError, "ratio target"):
                _validate_direct_smd_output(source, output, ratio)

    def test_multi_material_floors_are_applied_independently(self) -> None:
        prefix = (
            'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
            '0 0 0 0 0 0 0\nend\ntriangles\n'
        )
        def record(material: str, index: int) -> str:
            x = index * 2
            return (
                f'{material}\n0 {x} 0 0 0 0 1 0 0\n0 {x + 1} 0 0 0 0 1 1 0\n'
                f'0 {x} 1 0 0 0 1 0 1\n'
            )
        metal = [record("metal", i) for i in range(4)]
        glass = [record("glass", i + 10) for i in range(2)]
        source = prefix + "".join(metal + glass) + "end\n"
        output = prefix + "".join(metal[:2] + glass[:1]) + "end\n"
        self.assertEqual(_validate_direct_smd_output(source, output, 0.5), (6, 3))

    def test_winding_provenance_is_independent_of_corner_normals(self) -> None:
        lines = prefilter_direct_degenerate_smd(self.text).filtered_text.splitlines(keepends=True)
        in_triangles = False
        for index, raw in enumerate(lines):
            folded = raw.strip().casefold()
            if folded == "triangles":
                in_triangles = True
                continue
            tokens = raw.split()
            if in_triangles and len(tokens) >= 9 and tokens[0].lstrip("-").isdigit():
                tokens[6] = "-1"
                lines[index] = " ".join(tokens) + "\n"
        negative = "".join(lines)
        self.assertEqual(_validate_direct_smd_output(negative, _first_triangle(negative), 0.5), (2, 1))
        with self.assertRaisesRegex(RuntimeError, "cycle or winding"):
            _validate_direct_smd_output(negative, _reverse_first(negative), 0.5)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_root = self.root / "source"; self.source_root.mkdir()
        self.text = _source()
        (self.source_root / "body.smd").write_bytes(self.text.encode("utf-8"))
        self.request = _request(self.text)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_one_current_snapshot_from_golden_prefilter_and_fake_runner(self) -> None:
        calls = []
        def runner(input_path, output_path, request, cancel_event):
            calls.append((input_path, output_path, request.direct_ratio))
            output_path.write_bytes(_first_triangle(input_path.read_bytes().decode("utf-8")).encode("utf-8"))
        workspace = self.root / "direct"
        snapshot = build_direct_source_snapshot(
            self.request, workspace, DirectSourceTools(self.source_root, runner), threading.Event()
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(snapshot.source_root, workspace)
        self.assertEqual(snapshot.output_relative_path, "output.smd")
        self.assertEqual(snapshot.triangles_before, 2)
        self.assertEqual(snapshot.triangles_after, 1)
        self.assertEqual(tuple(path.name for path in workspace.iterdir()), ("output.smd",))
        self.assertEqual(snapshot.prefilter, self.request.expected_prefilter)

    def test_rejects_stale_input_and_wrong_expected_prefilter_before_runner(self) -> None:
        runner = mock.Mock(side_effect=AssertionError("runner called"))
        (self.source_root / "body.smd").write_bytes(b"stale")
        with self.assertRaises(ValueError):
            build_direct_source_snapshot(
                self.request, self.root / "stale-work", DirectSourceTools(self.source_root, runner), None
            )
        runner.assert_not_called()
        (self.source_root / "body.smd").write_bytes(self.text.encode("utf-8"))
        different = _proof(self.text.replace("metal\n0 2", "rust\n0 2", 1))
        mismatched_request = _request(self.text, expected_prefilter=different)
        with self.assertRaises(ValueError):
            build_direct_source_snapshot(
                mismatched_request, self.root / "proof-work",
                DirectSourceTools(self.source_root, runner), None,
            )
        runner.assert_not_called()

    def test_dropped_row_mutation_changes_prefilter_and_request_identity(self) -> None:
        mutated = self.text.replace("0 2 0 0 0 0 1 0 0", "0 3 0 0 0 0 1 0 0", 1)
        first, second = _proof(self.text), _proof(mutated)
        self.assertNotEqual(first.triangles[0].source_sha256, second.triangles[0].source_sha256)
        self.assertNotEqual(_request(self.text).request_sha256, _request(mutated).request_sha256)

    def test_cancellation_cleans_owned_workspace_before_runner(self) -> None:
        event = threading.Event(); event.set()
        runner = mock.Mock(side_effect=AssertionError("runner called"))
        workspace = self.root / "cancelled"
        with self.assertRaises(ProcessCancelledError):
            build_direct_source_snapshot(
                self.request, workspace, DirectSourceTools(self.source_root, runner), event
            )
        runner.assert_not_called()
        self.assertFalse(workspace.exists())

    def test_cancellation_during_or_immediately_after_runner_cleans_workspace(self) -> None:
        for mode in ("raise", "return"):
            event = threading.Event()
            workspace = self.root / f"cancel-{mode}"
            def runner(input_path, output_path, request, cancel_event, mode=mode):
                output_path.write_bytes(_first_triangle(input_path.read_bytes().decode()).encode())
                cancel_event.set()
                if mode == "raise":
                    raise ProcessCancelledError("runner cancelled")
            with self.subTest(mode=mode), self.assertRaises(ProcessCancelledError):
                build_direct_source_snapshot(
                    self.request, workspace, DirectSourceTools(self.source_root, runner), event
                )
            self.assertFalse(workspace.exists())

    def test_rejects_prefilter_only_extra_output_and_attribute_mutation_without_snapshot(self) -> None:
        variants = {
            "prefilter-only": lambda text, root: text,
            "extra": lambda text, root: (_first_triangle(text), (root / "extra.smd").write_text(text))[0],
            "mutated-uv": lambda text, root: _first_triangle(text).replace("1 0\n", "0.5 0\n", 1),
            "mutated-normal": lambda text, root: _first_triangle(text).replace("0 0 1 0 0", "0 1 0 0 0", 1),
            "mutated-bone": lambda text, root: _first_triangle(text).replace("0 0 0 0 0 0 1", "1 0 0 0 0 0 1", 1),
            "material": lambda text, root: _first_triangle(text).replace("metal\n", "paint\n", 1),
            "nodes": lambda text, root: _first_triangle(text).replace('"root"', '"other"', 1),
            "skeleton": lambda text, root: _first_triangle(text).replace("time 0", "time 1", 1),
            "winding": lambda text, root: _reverse_first(text),
            "nan": lambda text, root: _first_triangle(text).replace("0 0 0 0 0 0 1", "0 nan 0 0 0 0 1", 1),
            "directory": lambda text, root: (_first_triangle(text), (root / "unexpected").mkdir())[0],
            "trailing": lambda text, root: _first_triangle(text) + "garbage\n",
        }
        for name, transform in variants.items():
            workspace = self.root / name
            def runner(input_path, output_path, request, cancel_event, transform=transform):
                text = input_path.read_bytes().decode("utf-8")
                output_path.write_bytes(transform(text, output_path.parent).encode("utf-8"))
            with self.subTest(name=name), self.assertRaises((ValueError, RuntimeError)):
                build_direct_source_snapshot(
                    self.request, workspace, DirectSourceTools(self.source_root, runner), None
                )
            self.assertFalse(workspace.exists())

    def test_requires_fresh_nonoverlapping_workspace(self) -> None:
        runner = mock.Mock()
        with self.assertRaises(ValueError):
            build_direct_source_snapshot(
                self.request, self.source_root / "inside", DirectSourceTools(self.source_root, runner), None
            )
        occupied = self.root / "occupied"; occupied.mkdir(); (occupied / "x").write_text("x")
        with self.assertRaises(ValueError):
            build_direct_source_snapshot(
                self.request, occupied, DirectSourceTools(self.source_root, runner), None
            )
        runner.assert_not_called()

    def test_rejects_symlink_output_when_platform_can_create_it(self) -> None:
        workspace = self.root / "symlink-output"
        def runner(input_path, output_path, request, cancel_event):
            try:
                output_path.symlink_to(input_path)
            except OSError as exc:
                raise unittest.SkipTest(f"symlink unavailable: {exc}")
        with self.assertRaises(ValueError):
            build_direct_source_snapshot(
                self.request, workspace, DirectSourceTools(self.source_root, runner), None
            )
        self.assertFalse(workspace.exists())


if __name__ == "__main__":
    unittest.main()
