import struct
import tempfile
import unittest
from pathlib import Path

from maximum_optimizer.compiled_size import (
    compare_snapshots,
    read_vvd_lod_vertices,
    scan_compiled_models,
)
from maximum_optimizer.domain import ArtifactStat, CompiledSizeSnapshot


def vvd_bytes(lods=(100, 50, 25)):
    values = list(lods) + [0] * (8 - len(lods))
    return struct.pack("<4siii8i", b"IDSV", 4, 1234, len(lods), *values) + b"x" * 64


def snapshot(total_bytes, bytes_by_kind=None):
    artifact = ArtifactStat("model.mdl", ".mdl", total_bytes)
    return CompiledSizeSnapshot(
        Path("models"), total_bytes, bytes_by_kind or {}, {}, (artifact,)
    )


class CompiledSizeTests(unittest.TestCase):
    def test_reads_all_declared_lods(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "a.vvd")
            path.write_bytes(vvd_bytes())
            self.assertEqual(read_vvd_lod_vertices(path), (100, 50, 25))

    def test_truncated_vvd_has_no_vertex_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "a.vvd")
            path.write_bytes(b"IDSV")
            self.assertEqual(read_vvd_lod_vertices(path), ())

    def test_scan_counts_real_vtx_variants_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.mdl").write_bytes(b"m" * 10)
            (root / "a.vvd").write_bytes(vvd_bytes((10,)))
            (root / "a.dx90.vtx").write_bytes(b"v" * 7)
            (root / "a.dx80.vtx").write_bytes(b"v" * 5)
            snap = scan_compiled_models(root)
            self.assertEqual(snap.total_bytes, sum(p.stat().st_size for p in root.iterdir()))
            self.assertEqual(snap.bytes_by_kind[".dx90.vtx"], 7)
            self.assertEqual(snap.bytes_by_kind[".dx80.vtx"], 5)
            self.assertEqual(snap.vertices_by_lod[0], 10)

    def test_compare_separates_roundtrip_optional_and_geometric_deltas(self):
        result = compare_snapshots(
            snapshot(100),
            snapshot(110, {".dx80.vtx": 20}),
            snapshot(50, {".dx80.vtx": 0}),
        )

        self.assertEqual(result["roundtrip_delta_bytes"], 10)
        self.assertEqual(result["roundtrip_delta_percent"], 10.0)
        self.assertEqual(result["optional_removed_bytes"], 20)
        self.assertAlmostEqual(result["optional_removed_percent"], 20 / 110 * 100)
        self.assertEqual(result["geometric_delta_bytes"], 40)
        self.assertAlmostEqual(result["geometric_delta_percent"], 40 / 110 * 100)
        self.assertEqual(result["total_saving_bytes"], 50)
        self.assertEqual(result["total_saving_percent"], 50.0)

    def test_compare_uses_zero_percent_when_denominator_is_zero(self):
        result = compare_snapshots(snapshot(0), snapshot(0), snapshot(0))

        self.assertEqual(result["roundtrip_delta_percent"], 0.0)
        self.assertEqual(result["optional_removed_percent"], 0.0)
        self.assertEqual(result["geometric_delta_percent"], 0.0)
        self.assertEqual(result["total_saving_percent"], 0.0)


if __name__ == "__main__":
    unittest.main()
