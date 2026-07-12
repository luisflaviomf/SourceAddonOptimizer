from __future__ import annotations

import unittest

from maximum_optimizer.smd_contract import (
    map_imported_corners_to_smd,
    prefilter_direct_degenerate_smd,
    serialize_direct_smd,
)


def _fixture(*, retained_normal: str = "0 0 1") -> str:
    return (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\nmetal\n'
        '0 2 0 0 nan 0 0 0 0\n'
        '0 2 0 0 0 0 0 0 0\n'
        '0 2 0 0 0 0 0 0 0\n'
        'metal\n'
        f'0 0 0 0 {retained_normal} 0 0\n'
        f'0 1 0 0 {retained_normal} 1 0\n'
        f'0 0 1 0 {retained_normal} 0 1\n'
        'end\n'
    )


class DirectDegeneratePrefilterTests(unittest.TestCase):
    def test_drops_only_zero_area_and_binds_deterministic_evidence(self) -> None:
        source = _fixture()

        result = prefilter_direct_degenerate_smd(source)
        repeated = prefilter_direct_degenerate_smd(source)

        self.assertEqual(result, repeated)
        self.assertEqual(result.dropped_source_triangles, (0,))
        self.assertNotIn("nan", result.filtered_text.casefold())
        self.assertEqual(result.evidence["strategy"], "direct-degenerate-prefilter-v1")
        self.assertEqual(result.evidence["source_triangle_count"], 2)
        self.assertEqual(result.evidence["dropped_count"], 1)
        self.assertEqual(result.evidence["dropped_fraction"], 0.5)
        self.assertRegex(result.evidence["evidence_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(result.evidence["triangles"], [{
            "ordinal": 0,
            "material": "metal",
            "primary_bones": ["root", "root", "root"],
            "reason": "cross-squared-at-most-1e-30",
            "source_sha256": result.evidence["triangles"][0]["source_sha256"],
        }])
        self.assertRegex(
            result.evidence["triangles"][0]["source_sha256"], r"^[0-9a-f]{64}$"
        )

    def test_allows_invalid_normals_only_in_dropped_triangles(self) -> None:
        source = _fixture()
        result = prefilter_direct_degenerate_smd(source)
        positions = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        triangles = ((0, 1, 2),)
        normals = ((0.0, 0.0, 1.0),) * 3
        uvs = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
        influences = ((("root", 1.0),),) * 3

        mapping = map_imported_corners_to_smd(
            source,
            positions,
            triangles,
            normals,
            uvs,
            (0,),
            ("metal",),
            influences,
            dropped_source_triangles=frozenset(result.dropped_source_triangles),
        )
        serialized = serialize_direct_smd(
            source,
            positions,
            normals,
            uvs,
            influences,
            (0, 1, 2),
            ("metal",),
            source_corner_ordinals=mapping,
            dropped_source_triangles=frozenset(result.dropped_source_triangles),
        )

        self.assertEqual(mapping, (3, 4, 5))
        self.assertNotIn("nan", serialized.casefold())
        with self.assertRaisesRegex(ValueError, "invalid normal outside dropped"):
            prefilter_direct_degenerate_smd(
                _fixture(retained_normal="0 0 0")
            )


if __name__ == "__main__":
    unittest.main()
