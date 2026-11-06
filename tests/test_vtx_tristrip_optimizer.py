from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from vtx_tristrip_optimizer import (
    TRI_STRIP,
    VtxOptimizeError,
    VtxReader,
    optimize_tree,
    optimize_vtx_bytes,
    oriented_triangle_counter,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
STRIPIFIER_DLL = REPO_ROOT / "tools" / "vtx" / "meshopt_stripify.dll"


def build_vtx_fixture() -> bytes:
    """Build one VTX v7 mesh containing two consistently wound triangles."""

    file_header_size = 36
    material_list_size = 8
    body_part_size = 8
    model_size = 8
    lod_size = 12
    mesh_size = 9
    strip_group_size = 25
    strip_size = 27
    vertex_size = 9

    material_list = file_header_size
    body_part = material_list + material_list_size
    model = body_part + body_part_size
    lod = model + model_size
    mesh = lod + lod_size
    group = mesh + mesh_size
    strip = group + strip_group_size
    vertices = strip + strip_size
    indices = vertices + 4 * vertex_size
    output = bytearray(indices + 6 * 2)

    struct.pack_into(
        "<iiHHiiiiii",
        output,
        0,
        7,
        32,
        3,
        3,
        3,
        0x12345678,
        1,
        material_list,
        1,
        body_part,
    )
    struct.pack_into("<ii", output, material_list, 0, 0)
    struct.pack_into("<ii", output, body_part, 1, model - body_part)
    struct.pack_into("<ii", output, model, 1, lod - model)
    struct.pack_into("<iif", output, lod, 1, mesh - lod, 0.0)
    struct.pack_into("<iiB", output, mesh, 1, group - mesh, 0)
    struct.pack_into(
        "<iiiiiiB",
        output,
        group,
        4,
        vertices - group,
        6,
        indices - group,
        1,
        strip - group,
        0x02,
    )
    struct.pack_into("<iiiihBii", output, strip, 6, 0, 4, 0, 0, 0x01, 0, 0)
    for vertex_index in range(4):
        struct.pack_into(
            "<3BBH3b",
            output,
            vertices + vertex_index * vertex_size,
            0,
            0,
            0,
            0,
            vertex_index,
            0,
            0,
            0,
        )
    struct.pack_into("<6H", output, indices, 0, 1, 2, 2, 1, 3)
    return bytes(output)


def build_vtx_fixture_with_repeated_bone_changes() -> bytes:
    """Build two strips whose immutable bone-state tables are byte-identical."""

    file_header_size = 36
    material_list_size = 8
    body_part_size = 8
    model_size = 8
    lod_size = 12
    mesh_size = 9
    strip_group_size = 25
    strip_size = 27
    vertex_size = 9

    material_list = file_header_size
    body_part = material_list + material_list_size
    model = body_part + body_part_size
    lod = model + model_size
    mesh = lod + lod_size
    group = mesh + mesh_size
    strips = group + strip_group_size
    vertices = strips + 2 * strip_size
    indices = vertices + 4 * vertex_size
    bone_changes = indices + 6 * 2
    output = bytearray(bone_changes + 2 * 8)

    struct.pack_into(
        "<iiHHiiiiii",
        output,
        0,
        7,
        32,
        3,
        3,
        3,
        0x12345678,
        1,
        material_list,
        1,
        body_part,
    )
    struct.pack_into("<ii", output, material_list, 0, 0)
    struct.pack_into("<ii", output, body_part, 1, model - body_part)
    struct.pack_into("<ii", output, model, 1, lod - model)
    struct.pack_into("<iif", output, lod, 1, mesh - lod, 0.0)
    struct.pack_into("<iiB", output, mesh, 1, group - mesh, 0)
    struct.pack_into(
        "<iiiiiiB",
        output,
        group,
        4,
        vertices - group,
        6,
        indices - group,
        2,
        strips - group,
        0x02,
    )
    first_strip = strips
    second_strip = strips + strip_size
    struct.pack_into(
        "<iiiihBii",
        output,
        first_strip,
        3,
        0,
        3,
        0,
        1,
        0x01,
        1,
        bone_changes - first_strip,
    )
    struct.pack_into(
        "<iiiihBii",
        output,
        second_strip,
        3,
        3,
        3,
        1,
        1,
        0x01,
        1,
        bone_changes + 8 - second_strip,
    )
    for vertex_index in range(4):
        struct.pack_into(
            "<3BBH3b",
            output,
            vertices + vertex_index * vertex_size,
            0,
            0,
            0,
            0,
            vertex_index,
            0,
            0,
            0,
        )
    struct.pack_into("<6H", output, indices, 0, 1, 2, 2, 1, 3)
    struct.pack_into("<ii", output, bone_changes, 0, 0)
    struct.pack_into("<ii", output, bone_changes + 8, 0, 0)
    return bytes(output)


class VtxTriStripOptimizerTests(unittest.TestCase):
    def test_rejects_an_unpinned_native_stripifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tampered = Path(temporary) / "meshopt_stripify.dll"
            tampered.write_bytes(STRIPIFIER_DLL.read_bytes() + b"tampered")

            with self.assertRaisesRegex(VtxOptimizeError, "SHA-256"):
                optimize_vtx_bytes(build_vtx_fixture(), tampered)

    def test_rewrites_triangle_list_and_preserves_oriented_topology(self) -> None:
        source = build_vtx_fixture()

        candidate, report = optimize_vtx_bytes(source, STRIPIFIER_DLL)

        parsed = VtxReader(Path("fixture.dx90.vtx"), candidate).parse()
        strips = [strip for group in parsed.strip_groups for strip in group.strips]
        recovered = parsed.strip_groups[0].indices
        self.assertLess(len(candidate), len(source))
        self.assertEqual([strip.flags for strip in strips], [TRI_STRIP])
        self.assertEqual(report.converted_strips, 1)
        self.assertGreater(report.saved_bytes, 0)
        self.assertEqual(
            oriented_triangle_counter((0, 1, 2, 2, 1, 3)),
            oriented_triangle_counter(report.recovered_triangle_list),
        )
        self.assertGreater(len(recovered), 0)

    def test_pools_identical_bone_state_change_tables(self) -> None:
        source = build_vtx_fixture_with_repeated_bone_changes()

        candidate, _report = optimize_vtx_bytes(source, STRIPIFIER_DLL)

        reader = VtxReader(Path("fixture.dx90.vtx"), candidate)
        parsed = reader.parse()
        bone_spans = {
            (span.start, span.end)
            for span in reader.spans
            if span.kind == "bone_state_changes"
        }
        self.assertEqual(len(parsed.strip_groups[0].strips), 2)
        self.assertEqual(len(bone_spans), 1)

    def test_tree_pass_is_transactional_and_reports_per_file_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = root / "models" / "car.dx90.vtx"
            invalid = root / "models" / "broken.dx90.vtx"
            report_path = root / "logs" / "vtx-tristrip.json"
            valid.parent.mkdir(parents=True)
            valid.write_bytes(build_vtx_fixture())
            invalid.write_bytes(b"not a VTX")
            invalid_before = invalid.read_bytes()

            summary = optimize_tree(root, STRIPIFIER_DLL, report_path=report_path)

            self.assertEqual(summary.scanned_files, 2)
            self.assertEqual(summary.optimized_files, 1)
            self.assertEqual(summary.failed_files, 1)
            self.assertEqual(invalid.read_bytes(), invalid_before)
            self.assertEqual(VtxReader(valid, valid.read_bytes()).parse().strip_groups[0].strips[0].flags, TRI_STRIP)
            document = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(document["summary"]["failed_files"], 1)

    def test_second_tree_pass_keeps_an_existing_tristrip_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "models" / "car.dx90.vtx"
            target.parent.mkdir(parents=True)
            target.write_bytes(build_vtx_fixture())
            first = optimize_tree(root, STRIPIFIER_DLL)
            first_bytes = target.read_bytes()

            second = optimize_tree(root, STRIPIFIER_DLL)

            self.assertEqual(first.optimized_files, 1)
            self.assertEqual(second.optimized_files, 0)
            self.assertEqual(second.already_tristrip_files, 1)
            self.assertEqual(target.read_bytes(), first_bytes)


if __name__ == "__main__":
    unittest.main()
