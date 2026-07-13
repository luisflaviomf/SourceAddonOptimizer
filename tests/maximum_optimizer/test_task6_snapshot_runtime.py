from __future__ import annotations

import hashlib
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
from maximum_optimizer.domain import (
    DirectDroppedTriangleProof, DirectInputMaterialProof, DirectMaterialTriangleProof,
)


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


def _request():
    triangle = DirectDroppedTriangleProof(
        0,
        "paint",
        ("root",),
        "cross-squared-at-most-1e-30",
        H["1"],
    )
    prefilter = build_direct_prefilter_proof(
        source_triangle_count=10,
        triangles=(triangle,),
    )
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
        source_size=100,
        source_sha256=H["1"],
        direct_ratio=0.5,
        expected_prefilter=prefilter,
        expected_materials=(DirectInputMaterialProof(0, "paint", 9, H["c"]),),
    )


def _snapshot(root: Path, relative: str = "output.smd", content: bytes | None = None):
    content = _output_smd() if content is None else content
    request = _request()
    return build_direct_source_snapshot(
        request=request,
        source_root=root,
        output_relative_path=relative,
        output_size=len(content),
        output_sha256=hashlib.sha256(content).hexdigest(),
        triangles_before=9,
        triangles_after=4,
        material_triangles=(DirectMaterialTriangleProof(0, "paint", 9, 4, 4),),
        prefilter=request.expected_prefilter,
    )


class DirectSourceSnapshotRuntimeTests(unittest.TestCase):
    def test_factory_rejects_fully_resealed_material_count_forgery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            content = _output_smd(5)
            (root / "output.smd").write_bytes(content)
            request = _request()
            with self.assertRaisesRegex(ValueError, "material ratio evidence"):
                build_direct_source_snapshot(
                    request=request, source_root=root,
                    output_relative_path="output.smd", output_size=len(content),
                    output_sha256=hashlib.sha256(content).hexdigest(),
                    triangles_before=9, triangles_after=3,
                    material_triangles=(DirectMaterialTriangleProof(0, "paint", 9, 4, 3),),
                    prefilter=request.expected_prefilter,
                )

    def test_factory_and_revalidator_accept_exact_current_single_smd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "output.smd").write_bytes(_output_smd())

            snapshot = _snapshot(root)

            self.assertIs(
                revalidate_direct_source_snapshot(snapshot, threading.Event()),
                snapshot,
            )

    def test_factory_rejects_missing_declared_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "exactly one|missing"):
                _snapshot(Path(temporary).resolve())

    def test_factory_rejects_stale_size_or_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "output.smd").write_bytes(b"stale")
            with self.assertRaisesRegex(ValueError, "current bytes|size|hash"):
                _snapshot(root, content=_output_smd())

    def test_factory_rejects_extra_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "output.smd").write_bytes(_output_smd())
            (root / "extra.bin").write_bytes(b"extra")
            with self.assertRaisesRegex(ValueError, "exactly one|extra"):
                _snapshot(root)

    def test_factory_rejects_case_alias_of_declared_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "Output.smd").write_bytes(_output_smd())
            with self.assertRaisesRegex(ValueError, "canonical|declared|case"):
                _snapshot(root, relative="output.smd")

    def test_factory_rejects_reparse_root_before_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "output.smd").write_bytes(_output_smd())
            with mock.patch(
                "maximum_optimizer.composite._has_reparse_ancestor",
                return_value=True,
            ), self.assertRaisesRegex(ValueError, "reparse"):
                _snapshot(root)

    def test_factory_rejects_symlink_output_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / "target.smd"
            target.write_bytes(_output_smd())
            link = root / "output.smd"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "reparse|symlink"):
                _snapshot(root)

    def test_factory_rejects_special_file_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "output.smd").write_bytes(_output_smd())
            real_is_regular = stat.S_ISREG
            with mock.patch(
                "maximum_optimizer.composite.stat.S_ISREG",
                side_effect=lambda mode: False if real_is_regular(mode) else real_is_regular(mode),
            ), self.assertRaisesRegex(ValueError, "special"):
                _snapshot(root)


if __name__ == "__main__":
    unittest.main()
