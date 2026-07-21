from __future__ import annotations

import math
from pathlib import Path, PurePosixPath
import shutil
import struct
import tempfile
import unittest

from maximum_optimizer.closure import ReferenceInventory, audit_reference_closure
from maximum_optimizer.compiled_validation import (
    _read_vvd,
    expected_compiled_family,
    validate_compiled_family,
)
from maximum_optimizer.composition import (
    RegionReplacement,
    compose_source_tree,
    isolate_compile_failure,
)
from maximum_optimizer.qc_graph import QcOccurrence
from maximum_optimizer.regions import build_region_graph, correspond_graphs
from maximum_optimizer.smd import parse_smd


FIXTURES = Path(__file__).resolve().parent / "fixtures"
OCCURRENCE = QcOccurrence(
    PurePosixPath("vehicle.qc"), "$body", 2, PurePosixPath("vehicle.smd")
)


def _write_mdl_family(
    root: Path,
    *,
    include_dx90: bool = True,
    include_dx80: bool = False,
    mdl_version: int = 48,
    animations: int = 2,
) -> None:
    target = root / "cars" / "test"
    target.parent.mkdir(parents=True, exist_ok=True)
    counts = [0] * 24
    counts[0] = 4  # bones
    counts[6] = animations
    counts[8] = 3  # sequences
    counts[12] = 2  # textures/material identities
    counts[16] = 2  # skin references
    counts[17] = 3  # skin families
    counts[19] = 1  # bodyparts
    counts[21] = 2  # attachments
    mdl = bytearray(512)
    mdl[:4] = b"IDST"
    struct.pack_into("<i", mdl, 4, mdl_version)
    struct.pack_into("<i", mdl, 8, 12345)
    struct.pack_into("<i", mdl, 76, len(mdl))
    struct.pack_into("<24i", mdl, 156, *counts)
    target.with_suffix(".mdl").write_bytes(mdl)

    vvd = bytearray(256)
    vvd[:4] = b"IDSV"
    struct.pack_into("<3i", vvd, 4, 4, 12345, 1)
    struct.pack_into("<8i", vvd, 16, 3, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<4i", vvd, 48, 0, 64, 64, 208)
    target.with_suffix(".vvd").write_bytes(vvd)

    vtx = bytearray(36)
    struct.pack_into("<i", vtx, 0, 7)
    struct.pack_into("<i", vtx, 16, 12345)
    if include_dx90:
        target.with_suffix(".dx90.vtx").write_bytes(vtx)
    if include_dx80:
        target.with_suffix(".dx80.vtx").write_bytes(vtx)


class CompositionIntegrityTests(unittest.TestCase):
    def test_original_wheel_and_aggressive_body_share_one_composition(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original = root / "original"
            normal = root / "normal"
            original.mkdir()
            normal.mkdir()
            shutil.copy2(FIXTURES / "two_components.smd", original / "vehicle.smd")
            shutil.copy2(FIXTURES / "two_components_OPT.smd", normal / "vehicle.smd")
            (original / "vehicle.qc").write_text('$body "body" "vehicle.smd"\n', encoding="utf-8")
            (normal / "vehicle.qc").write_text('$body "body" "vehicle.smd"\n', encoding="utf-8")

            original_graph = build_region_graph(
                parse_smd((original / "vehicle.smd").read_text(encoding="utf-8")),
                OCCURRENCE,
            )
            normal_graph = build_region_graph(
                parse_smd((normal / "vehicle.smd").read_text(encoding="utf-8")),
                OCCURRENCE,
            )
            pairs = correspond_graphs(original_graph, normal_graph).pairs
            changed = next(pair for pair in pairs if pair.original.centroid[0] > 4.0)
            unchanged = next(pair for pair in pairs if pair.original.material == "glass")
            out = root / "composed"
            result = compose_source_tree(
                original,
                normal,
                (
                    RegionReplacement(OCCURRENCE.source_path, changed.original, changed.normal),
                    RegionReplacement(OCCURRENCE.source_path, unchanged.original, unchanged.original),
                ),
                out,
            )
            composed_graph = build_region_graph(
                parse_smd(result.sources[OCCURRENCE.source_path].read_text(encoding="utf-8")),
                OCCURRENCE,
            )

        changed_output = next(region for region in composed_graph.regions if region.centroid[0] > 4.0)
        glass_output = next(region for region in composed_graph.regions if region.material == "glass")
        self.assertAlmostEqual(changed_output.centroid[0], changed.normal.centroid[0])
        self.assertEqual(glass_output.triangles, unchanged.original.triangles)
        self.assertEqual(result.replaced_regions, 2)

    def test_compile_isolation_is_logarithmic_and_local(self) -> None:
        changed = tuple(f"s{index}.smd" for index in range(8))
        result = isolate_compile_failure(
            changed,
            lambda active: "s5.smd" not in active,
        )

        self.assertEqual(result.reverted_sources, ("s5.smd",))
        self.assertLessEqual(result.compile_count, math.ceil(math.log2(8)) + 1)

    def test_dx90_is_required_and_intermediate_dx80_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original = root / "original"
            candidate = root / "candidate"
            _write_mdl_family(original)
            _write_mdl_family(candidate, include_dx90=False)
            expected = expected_compiled_family(original, PurePosixPath("cars/test.mdl"))

            missing_dx90 = validate_compiled_family(candidate, expected)
            _write_mdl_family(candidate, include_dx90=True, include_dx80=True)
            with_dx80 = validate_compiled_family(candidate, expected)
            (candidate / "cars" / "test.dx80.vtx").unlink()
            valid = validate_compiled_family(candidate, expected)

        self.assertFalse(missing_dx90.passed)
        self.assertIn("missing_dx90", missing_dx90.failures)
        self.assertTrue(with_dx80.passed, with_dx80.failures)
        self.assertTrue(valid.passed, valid.failures)

    def test_valid_studiomdl_recompile_may_emit_v48_and_expand_animations(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original = root / "original"
            candidate = root / "candidate"
            _write_mdl_family(original, mdl_version=49, animations=84)
            _write_mdl_family(candidate, mdl_version=48, animations=166)
            expected = expected_compiled_family(original, PurePosixPath("cars/test.mdl"))

            expanded = validate_compiled_family(candidate, expected)
            _write_mdl_family(candidate, mdl_version=48, animations=40)
            lost = validate_compiled_family(candidate, expected)

        self.assertTrue(expanded.passed, expanded.failures)
        self.assertFalse(lost.passed)
        self.assertIn("animations_lost", lost.failures)

    def test_vvd_vertex_inventory_allows_total_above_single_mesh_limit(self) -> None:
        lod0 = 85_357
        data = bytearray(64 + lod0 * 64)
        data[:4] = b"IDSV"
        struct.pack_into("<3i", data, 4, 4, 12345, 1)
        struct.pack_into("<8i", data, 16, lod0, 0, 0, 0, 0, 0, 0, 0)
        struct.pack_into("<4i", data, 48, 0, 64, 64, 64 + lod0 * 48)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "large.vvd"
            path.write_bytes(data)

            checksum, vertices = _read_vvd(path)

        self.assertEqual(checksum, 12345)
        self.assertEqual(vertices, lod0)

    def test_new_framework_reference_fails_without_resolver(self) -> None:
        original = ReferenceInventory(addon=frozenset({"materials/cars/body.vmt"}))
        candidate = ReferenceInventory(
            addon=frozenset({"materials/cars/body.vmt"}),
            framework=frozenset({"materials/lvs/shared_glass.vmt"}),
        )

        audit = audit_reference_closure(original, candidate, framework_resolver_root=None)

        self.assertFalse(audit.passed)
        self.assertEqual(audit.external_references, ("materials/lvs/shared_glass.vmt",))


if __name__ == "__main__":
    unittest.main()
