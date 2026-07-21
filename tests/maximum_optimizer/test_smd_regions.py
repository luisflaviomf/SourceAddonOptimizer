from __future__ import annotations

from pathlib import Path, PurePosixPath
import tempfile
import unittest

from maximum_optimizer.qc_graph import QcOccurrence, scan_qc_occurrences
from maximum_optimizer.regions import build_region_graph, correspond_graphs
from maximum_optimizer.smd import parse_smd, serialize_smd


FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORIGINAL = FIXTURES / "two_components.smd"
NORMAL = FIXTURES / "two_components_OPT.smd"
OCCURRENCE = QcOccurrence(
    qc_path=PurePosixPath("vehicle.qc"),
    directive="$body",
    line=2,
    source_path=PurePosixPath("two_components.smd"),
)


class SmdTests(unittest.TestCase):
    def test_roundtrip_preserves_geometry_and_caps_export_to_three_influences(self) -> None:
        document = parse_smd(ORIGINAL.read_text(encoding="utf-8"))

        exported = parse_smd(serialize_smd(document))

        self.assertEqual(len(exported.triangles), 4)
        self.assertEqual(exported.header_lines, document.header_lines)
        first = exported.triangles[0].vertices[0]
        self.assertEqual(tuple(value.bone for value in first.influences), (0, 1, 2))
        self.assertAlmostEqual(sum(value.weight for value in first.influences), 1.0)
        self.assertEqual(exported.triangles[3].material, "glass")

    def test_malformed_or_animation_only_smd_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "triangles"):
            parse_smd("version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\n")
        with self.assertRaisesRegex(ValueError, "mid-triangle"):
            parse_smd("version 1\ntriangles\npaint\n0 0 0 0 0 0 1 0 0\nend\n")


class QcGraphTests(unittest.TestCase):
    def test_qc_scanner_records_each_render_occurrence(self) -> None:
        occurrences = scan_qc_occurrences(FIXTURES)

        self.assertEqual(len(occurrences), 2)
        self.assertEqual(occurrences[0].directive, "$body")
        self.assertEqual(occurrences[0].source_path, PurePosixPath("two_components.smd"))
        self.assertEqual(occurrences[1].directive, "$bodygroup/studio")
        self.assertEqual(occurrences[1].line, 5)

    def test_qc_scanner_attaches_all_cdmaterials_to_earlier_occurrences(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "vehicle.qc").write_text(
                '$body "body" "vehicle.smd"\n'
                '$cdmaterials "models\\Cars\\Vehicle\\"\n'
                '$cdmaterials "models/Cars/shared"\n',
                encoding="utf-8",
            )

            occurrence = scan_qc_occurrences(root)[0]

        self.assertEqual(
            occurrence.material_directories,
            (PurePosixPath("models/Cars/Vehicle"), PurePosixPath("models/Cars/shared")),
        )


class RegionGraphTests(unittest.TestCase):
    def test_nonmanifold_source_maps_regions_instead_of_rejecting_whole_model(self) -> None:
        text = ORIGINAL.read_text(encoding="utf-8")
        first_triangle = text.split("paint\n", 1)[1].split("paint\n", 1)[0]
        nonmanifold_text = text.replace("glass\n", "paint\n" + first_triangle + "glass\n")
        original = build_region_graph(parse_smd(nonmanifold_text), OCCURRENCE)
        normal = build_region_graph(parse_smd(nonmanifold_text), OCCURRENCE)

        correspondence = correspond_graphs(original, normal)

        self.assertTrue(original.nonmanifold)
        self.assertEqual(correspondence.status, "mapped")

    def test_material_and_connected_components_are_distinct_and_deterministic(self) -> None:
        document = parse_smd(ORIGINAL.read_text(encoding="utf-8"))

        first = build_region_graph(document, OCCURRENCE)
        second = build_region_graph(document, OCCURRENCE)

        self.assertEqual(first, second)
        self.assertEqual(
            [(region.material, region.local_ordinal, region.triangle_ordinals) for region in first.regions],
            [("glass", 0, (3,)), ("paint", 0, (0, 1)), ("paint", 1, (2,))],
        )
        self.assertEqual(len({region.key.value for region in first.regions}), 3)

    def test_original_and_normal_components_map_one_to_one(self) -> None:
        original = build_region_graph(parse_smd(ORIGINAL.read_text(encoding="utf-8")), OCCURRENCE)
        normal = build_region_graph(parse_smd(NORMAL.read_text(encoding="utf-8")), OCCURRENCE)

        correspondence = correspond_graphs(original, normal)

        self.assertEqual(correspondence.status, "mapped")
        self.assertEqual(len(correspondence.pairs), 3)
        self.assertEqual(
            {pair.original.key for pair in correspondence.pairs},
            {region.key for region in original.regions},
        )

    def test_duplicate_normal_component_falls_back_only_its_material_regions(self) -> None:
        original = build_region_graph(parse_smd(ORIGINAL.read_text(encoding="utf-8")), OCCURRENCE)
        normal_text = NORMAL.read_text(encoding="utf-8")
        record = "paint\n0 8.02 0 0 0 0 1 0 0\n0 9.02 0 0 0 0 1 1 0\n0 8.52 1 0 0 0 1 0.5 1\n"
        ambiguous_text = normal_text.replace("glass\n", record + "glass\n")
        normal = build_region_graph(parse_smd(ambiguous_text), OCCURRENCE)

        correspondence = correspond_graphs(original, normal)

        self.assertEqual(correspondence.status, "mapped")
        paint = tuple(pair for pair in correspondence.pairs if pair.original.material == "paint")
        glass = tuple(pair for pair in correspondence.pairs if pair.original.material == "glass")
        self.assertTrue(paint)
        self.assertTrue(all(not pair.confident and pair.normal == pair.original for pair in paint))
        self.assertTrue(all(pair.confident for pair in glass))
        self.assertIn("local fallback", correspondence.reason)


if __name__ == "__main__":
    unittest.main()
