from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from benchmarks.lvs_models_adaptive.render_panels import (
    VIEW_MODES,
    _build_panel,
    _corresponding_mesh_smd,
    _largest_mesh_smd,
)


class RenderPanelsTests(unittest.TestCase):
    def test_visual_mesh_selection_uses_qc_body_instead_of_larger_collision_mesh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            visible = root / "visible.smd"
            physics = root / "physics.smd"
            header = "version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\ntriangles\n"
            triangle = "mat\n0 0 0 0 0 0 1 0 0\n0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\n"
            visible.write_text(header + triangle + "end\n", encoding="utf-8")
            physics.write_text(header + triangle + triangle + "end\n", encoding="utf-8")
            (root / "model.qc").write_text(
                '$bodygroup "Body"\n{\n studio "visible.smd"\n}\n$collisionmodel "physics.smd"\n',
                encoding="utf-8",
            )

            self.assertEqual(_largest_mesh_smd(root), visible)

    def test_visual_mesh_selection_reuses_original_source_identity_across_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original"
            candidate = root / "candidate"
            original.mkdir()
            candidate.mkdir()
            selected = original / "selected.smd"
            matched = candidate / "selected_opt.smd"
            larger = candidate / "larger.smd"
            header = "version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\ntriangles\n"
            triangle = "mat\n0 0 0 0 0 0 1 0 0\n0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\n"
            selected.write_text(header + triangle + "end\n", encoding="utf-8")
            matched.write_text(header + triangle + "end\n", encoding="utf-8")
            larger.write_text(header + triangle + triangle + "end\n", encoding="utf-8")
            (original / "model.qc").write_text(
                '$body "selected" "selected.smd"\n$body "larger" "larger.smd"\n',
                encoding="utf-8",
            )
            (candidate / "model.qc").write_text(
                '$body "selected" "selected_opt.smd"\n$body "larger" "larger.smd"\n',
                encoding="utf-8",
            )

            actual = _corresponding_mesh_smd(original, selected, candidate)

            self.assertEqual(actual, matched)

    def test_panel_columns_follow_selected_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lanes = ("normal-safe", "maximum-adaptive-v2")
            for lane in lanes:
                for mode in VIEW_MODES:
                    for side in ("original", "optimized"):
                        target = root / lane / mode / side / "iso1.png"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        Image.new("RGB", (8, 8), (64, 96, 128)).save(target)
            destination = root / "panel.png"

            _build_panel("wheel", root, destination, lanes)

            with Image.open(destination) as panel:
                self.assertEqual(panel.size, (3 * 480, 6 * 512))


if __name__ == "__main__":
    unittest.main()
