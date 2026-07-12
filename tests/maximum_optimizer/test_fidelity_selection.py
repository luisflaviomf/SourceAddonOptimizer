from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest

from maximum_optimizer.qc_graph import QcGraph, QcReference
from maximum_optimizer.visual_validation import REQUIRED_METRICS


def _open_cylinder(sides: int = 16):
    positions = [
        (math.cos(2 * math.pi * index / sides), math.sin(2 * math.pi * index / sides), z)
        for z in (-0.2, 0.2)
        for index in range(sides)
    ]
    triangles = []
    for index in range(sides):
        following = (index + 1) % sides
        triangles.extend((
            (index, following, sides + following),
            (index, sides + following, sides + index),
        ))
    return tuple(positions), tuple(triangles)


def _box():
    positions = (
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    )
    triangles = (
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    )
    return positions, triangles


def _smd(positions, triangles, *, weighted_first: bool = False) -> str:
    lines = ["version 1", "nodes", '0 "root" -1', "end", "skeleton", "time 0",
             "0 0 0 0 0 0 0", "end", "triangles"]
    corner = 0
    for triangle in triangles:
        lines.append("material")
        for index in triangle:
            x, y, z = positions[index]
            suffix = " 2 0 0.5 1 0.5" if weighted_first and corner == 0 else ""
            lines.append(f"0 {x:.9f} {y:.9f} {z:.9f} 0 0 1 0 0{suffix}")
            corner += 1
    lines.extend(("end", ""))
    return "\n".join(lines)


def _graph(root: Path, sources: tuple[Path, ...]) -> QcGraph:
    qc = root / "model.qc"
    qc.write_text("$modelname model.mdl\n", encoding="utf-8")
    references = tuple(
        QcReference(qc, "$body", position + 1, source.name, source, "visual", 0, 1)
        for position, source in enumerate(sources)
    )
    return QcGraph(qc, root, (), references)


def _limits(value: float = 0.1) -> dict[str, float]:
    return {metric: value for metric in REQUIRED_METRICS}


class FidelitySelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_all_unique_rigid_round_sources_select_round_in_stable_order(self) -> None:
        from maximum_optimizer.fidelity_selection import ROUND_RIGID, classify_original_family

        positions, triangles = _open_cylinder()
        zeta = self._write("zeta.smd", _smd(positions, triangles))
        alpha = self._write("alpha.smd", _smd(positions, triangles))

        result = classify_original_family(_graph(self.root, (zeta, alpha, zeta)))

        self.assertEqual(result.profile_class, ROUND_RIGID)
        self.assertEqual(tuple(item.source for item in result.sources), ("alpha.smd", "zeta.smd"))
        self.assertTrue(all(item.eligible and item.axis == 2 for item in result.sources))

    def test_significant_nonround_source_forces_general(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            GENERAL_BODY_DETAIL, classify_original_family,
        )

        round_positions, round_triangles = _open_cylinder()
        box_positions, box_triangles = _box()
        wheel = self._write("wheel.smd", _smd(round_positions, round_triangles))
        body = self._write("body.smd", _smd(box_positions, box_triangles))

        result = classify_original_family(_graph(self.root, (wheel, body)))

        self.assertEqual(result.profile_class, GENERAL_BODY_DETAIL)
        self.assertIn("body.smd", result.reason)
        self.assertFalse(result.sources[0].eligible)

    def test_weighted_round_source_forces_general(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            GENERAL_BODY_DETAIL, classify_original_family,
        )

        positions, triangles = _open_cylinder()
        weighted = self._write("weighted.smd", _smd(positions, triangles, weighted_first=True))

        result = classify_original_family(_graph(self.root, (weighted,)))

        self.assertEqual(result.profile_class, GENERAL_BODY_DETAIL)
        self.assertIn("not-rigid", result.reason)

    def test_unsupported_or_malformed_source_falls_back_to_general(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            GENERAL_BODY_DETAIL, classify_original_family,
        )

        for name, text, reason in (
            ("mesh.dmx", "dmx", "unsupported-source-format"),
            ("broken.smd", "version 1\n", "invalid-smd"),
        ):
            with self.subTest(name=name):
                source = self._write(name, text)
                result = classify_original_family(_graph(self.root, (source,)))
                self.assertEqual(result.profile_class, GENERAL_BODY_DETAIL)
                self.assertIn(reason, result.reason)

    def test_empty_visual_graph_falls_back_to_general(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            GENERAL_BODY_DETAIL, classify_original_family,
        )

        result = classify_original_family(_graph(self.root, ()))

        self.assertEqual(result.profile_class, GENERAL_BODY_DETAIL)
        self.assertEqual(result.reason, "no-visual-sources")
        self.assertEqual(result.sources, ())


if __name__ == "__main__":
    unittest.main()
