from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest import mock

from maximum_optimizer.composite import (
    build_direct_prefilter_proof,
    build_direct_source_request,
    build_direct_source_snapshot,
    revalidate_direct_source_snapshot,
)
from maximum_optimizer import composite as composite_module
from maximum_optimizer.candidates import _direct_input_material_proofs, _direct_prefilter_proof
from maximum_optimizer.domain import (
    DirectDroppedTriangleProof, DirectInputMaterialProof, DirectMaterialTriangleProof,
)
from maximum_optimizer.smd_contract import prefilter_direct_degenerate_smd
from maximum_optimizer.processes import ProcessCancelledError


H = {character: character * 64 for character in "0123456789abcdef"}


def _output_smd(count: int = 4) -> bytes:
    triangles = []
    for index in range(count):
        x = index * 2
        triangles.append(
            f"paint\n0 {x} 0 0 0 0 1 0 0\n0 {x + 1} 0 0 0 0 1 1 0\n0 {x} 1 0 0 0 1 0 1\n"
        )
    return (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\n' + "".join(triangles) + "end\n"
    ).encode()


def _input_smd() -> bytes:
    rows = [
        "paint\n0 0 0 0 0 0 1 0 0\n0 0 0 0 0 0 1 0 0\n0 0 0 0 0 0 1 0 0\n"
    ]
    for index in range(9):
        x = index * 2
        rows.append(
            f"paint\n0 {x} 0 0 0 0 1 0 0\n0 {x + 1} 0 0 0 0 1 1 0\n0 {x} 1 0 0 0 1 0 1\n"
        )
    return (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\n' + "".join(rows) + "end\n"
    ).encode()


def _request():
    source = _input_smd()
    text = source.decode()
    prefilter = _direct_prefilter_proof(text)
    filtered = prefilter_direct_degenerate_smd(text).filtered_text
    return build_direct_source_request(
        family_id=H["0"],
        family_input_sha256=H["1"],
        base_candidate_id="base",
        base_spec_sha256=H["2"],
        base_cache_digest=H["3"],
        base_source_manifest_sha256=H["4"],
        base_source_snapshot_sha256=H["5"],
        coverage_manifest_sha256=H["a"],
        source_coverage_sha256=H["b"],
        optimizer_contract_sha256=H["6"],
        whole_profile_sha256=H["7"],
        focused_profile_sha256=H["8"],
        dependency_proof_sha256=H["9"],
        source_identity="body.smd",
        source_relative_path="body.smd",
        source_size=len(source),
        source_sha256=hashlib.sha256(source).hexdigest(),
        direct_ratio=0.5,
        expected_prefilter=prefilter,
        expected_materials=_direct_input_material_proofs(filtered),
    )


def _snapshot(
    root: Path, input_root: Path, relative: str = "output.smd",
    content: bytes | None = None,
):
    content = _output_smd() if content is None else content
    request = _request()
    return build_direct_source_snapshot(
        request=request,
        input_source_root=input_root,
        source_root=root,
        output_relative_path=relative,
        output_size=len(content),
        output_sha256=hashlib.sha256(content).hexdigest(),
        triangles_before=9,
        triangles_after=4,
        material_triangles=(DirectMaterialTriangleProof(0, "paint", 9, 4, 4),),
        prefilter=request.expected_prefilter,
    )


def _roots(base: Path) -> tuple[Path, Path]:
    input_root = base / "input"; output_root = base / "output"
    input_root.mkdir(); output_root.mkdir()
    (input_root / "body.smd").write_bytes(_input_smd())
    return input_root, output_root


class DirectSourceSnapshotRuntimeTests(unittest.TestCase):
    def test_revalidator_rejects_stale_or_borrowed_input_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve(); input_root, output_root = _roots(base)
            (output_root / "output.smd").write_bytes(_output_smd())
            snapshot = _snapshot(output_root, input_root)
            (input_root / "body.smd").write_bytes(b"stale")
            with self.assertRaisesRegex(ValueError, "input"):
                revalidate_direct_source_snapshot(snapshot)
            borrowed = base / "borrowed"; borrowed.mkdir()
            (borrowed / "body.smd").write_bytes(b"borrowed")
            with self.assertRaisesRegex(ValueError, "input"):
                revalidate_direct_source_snapshot(replace(snapshot, input_source_root=borrowed))

    def test_revalidator_rejects_reparse_input_root_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve(); input_root, output_root = _roots(base)
            (output_root / "output.smd").write_bytes(_output_smd())
            snapshot = _snapshot(output_root, input_root)
            alias = base / "input-link"
            try:
                alias.symlink_to(input_root, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "input|reparse"):
                revalidate_direct_source_snapshot(replace(snapshot, input_source_root=alias))

    def test_cancellation_during_input_hash_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, output_root = _roots(Path(temporary).resolve())
            (output_root / "output.smd").write_bytes(_output_smd())
            snapshot = _snapshot(output_root, input_root)
            event = threading.Event()
            real_read = composite_module._read_regular_no_follow
            def read(path, cancel_event, **kwargs):
                if Path(path).name == "body.smd":
                    event.set()
                return real_read(path, cancel_event, **kwargs)
            with mock.patch(
                "maximum_optimizer.composite._read_regular_no_follow", side_effect=read,
            ), self.assertRaises(ProcessCancelledError):
                revalidate_direct_source_snapshot(snapshot, event)

    def test_factory_rejects_resealed_reversed_corner_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, output_root = _roots(Path(temporary).resolve())
            lines = _output_smd().decode().splitlines(keepends=True)
            start = next(i for i, line in enumerate(lines) if line.strip() == "triangles")
            lines[start + 2], lines[start + 3] = lines[start + 3], lines[start + 2]
            content = "".join(lines).encode(); (output_root / "output.smd").write_bytes(content)
            request = _request()
            with self.assertRaisesRegex(ValueError, "provenance"):
                build_direct_source_snapshot(
                    request=request, input_source_root=input_root, source_root=output_root,
                    output_relative_path="output.smd", output_size=len(content),
                    output_sha256=hashlib.sha256(content).hexdigest(),
                    triangles_before=9, triangles_after=4,
                    material_triangles=(DirectMaterialTriangleProof(0, "paint", 9, 4, 4),),
                    prefilter=request.expected_prefilter,
                )

    def test_factory_rejects_fully_resealed_material_count_forgery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, root = _roots(Path(temporary).resolve())
            content = _output_smd(5); (root / "output.smd").write_bytes(content)
            request = _request()
            with self.assertRaisesRegex(ValueError, "material ratio evidence"):
                build_direct_source_snapshot(
                    request=request, input_source_root=input_root, source_root=root,
                    output_relative_path="output.smd", output_size=len(content),
                    output_sha256=hashlib.sha256(content).hexdigest(),
                    triangles_before=9, triangles_after=3,
                    material_triangles=(DirectMaterialTriangleProof(0, "paint", 9, 4, 3),),
                    prefilter=request.expected_prefilter,
                )

    def test_factory_and_revalidator_accept_exact_current_single_smd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, root = _roots(Path(temporary).resolve())
            (root / "output.smd").write_bytes(_output_smd())

            snapshot = _snapshot(root, input_root)

            self.assertIs(
                revalidate_direct_source_snapshot(snapshot, threading.Event()),
                snapshot,
            )

    def test_factory_rejects_missing_declared_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, output_root = _roots(Path(temporary).resolve())
            with self.assertRaisesRegex(ValueError, "exactly one|missing"):
                _snapshot(output_root, input_root)

    def test_factory_rejects_stale_size_or_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, root = _roots(Path(temporary).resolve())
            (root / "output.smd").write_bytes(b"stale")
            with self.assertRaisesRegex(ValueError, "current bytes|size|hash"):
                _snapshot(root, input_root, content=_output_smd())

    def test_factory_rejects_extra_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, root = _roots(Path(temporary).resolve())
            (root / "output.smd").write_bytes(_output_smd())
            (root / "extra.bin").write_bytes(b"extra")
            with self.assertRaisesRegex(ValueError, "exactly one|extra"):
                _snapshot(root, input_root)

    def test_factory_rejects_case_alias_of_declared_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, root = _roots(Path(temporary).resolve())
            (root / "Output.smd").write_bytes(_output_smd())
            with self.assertRaisesRegex(ValueError, "canonical|declared|case"):
                _snapshot(root, input_root, relative="output.smd")

    def test_factory_rejects_reparse_root_before_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, root = _roots(Path(temporary).resolve())
            (root / "output.smd").write_bytes(_output_smd())
            with mock.patch(
                "maximum_optimizer.composite._has_reparse_ancestor",
                return_value=True,
            ), self.assertRaisesRegex(ValueError, "reparse"):
                _snapshot(root, input_root)

    def test_factory_rejects_symlink_output_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, root = _roots(Path(temporary).resolve())
            target = root / "target.smd"
            target.write_bytes(_output_smd())
            link = root / "output.smd"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "reparse|symlink"):
                _snapshot(root, input_root)

    def test_factory_rejects_special_file_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, root = _roots(Path(temporary).resolve())
            (root / "output.smd").write_bytes(_output_smd())
            real_is_regular = stat.S_ISREG
            with mock.patch(
                "maximum_optimizer.composite.stat.S_ISREG",
                side_effect=lambda mode: False if real_is_regular(mode) else real_is_regular(mode),
            ), self.assertRaisesRegex(ValueError, "special"):
                _snapshot(root, input_root)


if __name__ == "__main__":
    unittest.main()
