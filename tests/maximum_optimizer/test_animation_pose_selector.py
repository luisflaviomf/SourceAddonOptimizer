from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image, PngImagePlugin


def _smd(*, bones: tuple[tuple[int, str, int], ...], frames: dict[int, dict[int, tuple[float, ...]]]) -> str:
    rows = ["version 1", "nodes"]
    rows.extend(f'  {index} "{name}" {parent}' for index, name, parent in bones)
    rows.extend(("end", "skeleton"))
    for frame, transforms in sorted(frames.items()):
        rows.append(f"  time {frame}")
        rows.extend(
            f"    {bone} " + " ".join(f"{value:.6f}" for value in values)
            for bone, values in sorted(transforms.items())
        )
    rows.extend(("end", ""))
    return "\n".join(rows)


def _mesh(bones: tuple[tuple[int, str, int], ...]) -> str:
    rows = [_smd(bones=bones, frames={0: {0: (0, 0, 0, 0, 0, 0)}}).rstrip(), "triangles"]
    for bone, size in ((1, 40.0), (2, 0.1)):
        rows.append("material")
        for position in ((-size, 0, 0), (size, 0, 0), (0, size, 0)):
            rows.append(
                f"  {bone} {position[0]:.6f} {position[1]:.6f} {position[2]:.6f} "
                f"0 0 1 0 0 1 {bone} 1.0"
            )
    rows.extend(("end", ""))
    return "\n".join(rows)


class AnimationPoseSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bones = ((0, "root", -1), (1, "Hood", 0), (2, "Door", 0))
        bind = {0: (0, 0, 0, 0, 0, -1.570796)}
        (self.root / "hood_corrective_animation.smd").write_text(
            _smd(bones=self.bones, frames={0: bind}), encoding="utf-8",
        )
        (self.root / "hood.smd").write_text(_smd(
            bones=self.bones,
            frames={0: {**bind, 1: (0, 0, 0, 0, 0, 1.8)}},
        ), encoding="utf-8")
        (self.root / "door_corrective_animation.smd").write_text(
            _smd(bones=self.bones, frames={0: bind}), encoding="utf-8",
        )
        (self.root / "door.smd").write_text(_smd(
            bones=self.bones,
            frames={0: {**bind, 2: (0, 0, 0, 0, 0, 1.2)}},
        ), encoding="utf-8")
        (self.root / "body.smd").write_text(_mesh(self.bones), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_selects_largest_exact_source_transform_and_binds_lineage(self) -> None:
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        selected = select_animation_pose(self.root, source_root=self.root)
        self.assertEqual(selected.selector, "exact-max-bone-displacement-v1")
        self.assertEqual(selected.animation_relative_path, "hood.smd")
        self.assertEqual(selected.reference_relative_path, "hood_corrective_animation.smd")
        self.assertEqual(selected.frame, 0)
        self.assertEqual(selected.bone_name, "Hood")
        self.assertGreater(selected.displacement, 1.79)
        self.assertEqual(
            selected.animation_sha256,
            hashlib.sha256((self.root / "hood.smd").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            selected.reference_sha256,
            hashlib.sha256((self.root / "hood_corrective_animation.smd").read_bytes()).hexdigest(),
        )
        payload = selected.to_payload()
        self.assertFalse(payload["candidate_inputs_consulted"])
        self.assertEqual(payload["pose_name"], "hood")
        self.assertEqual(len(payload["selector_input_sha256"]), 64)
        self.assertEqual(len(payload["geometry_inventory_sha256"]), 64)
        self.assertEqual(len(payload["selection_sha256"]), 64)

    def test_ranking_is_candidate_independent_and_canonical_on_ties(self) -> None:
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        (self.root / "a_corrective_animation.smd").write_text(
            (self.root / "door_corrective_animation.smd").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (self.root / "a.smd").write_text(
            (self.root / "hood.smd").read_text(encoding="utf-8"), encoding="utf-8",
        )
        first = select_animation_pose(self.root, source_root=self.root)
        ignored_candidate = self.root / "output" / "candidate.smd"
        ignored_candidate.parent.mkdir()
        ignored_candidate.write_text("candidate data must not affect exact selection", encoding="utf-8")
        second = select_animation_pose(self.root, source_root=self.root)
        self.assertEqual(first, second)
        self.assertEqual(first.animation_relative_path, "a.smd")

    def test_all_exact_pair_hashes_are_bound_not_only_the_winner(self) -> None:
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        before = select_animation_pose(self.root, source_root=self.root)
        door = self.root / "door.smd"
        door.write_text(door.read_text(encoding="utf-8") + "// exact source mutation\n", encoding="utf-8")
        after = select_animation_pose(self.root, source_root=self.root)
        self.assertEqual(before.animation_relative_path, after.animation_relative_path)
        self.assertNotEqual(before.selector_input_sha256, after.selector_input_sha256)
        self.assertNotEqual(before.selection_sha256, after.selection_sha256)

    def test_missing_pair_or_zero_displacement_fails_closed(self) -> None:
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            zero = _smd(
                bones=self.bones,
                frames={0: {0: (0, 0, 0, 0, 0, -1.570796)}},
            )
            (root / "idle.smd").write_text(zero, encoding="utf-8")
            (root / "body.smd").write_text(_mesh(self.bones), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "paired exact animation"):
                select_animation_pose(root, source_root=root)
            (root / "idle_corrective_animation.smd").write_text(zero, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "meaningful displacement"):
                select_animation_pose(root, source_root=root)

    def test_decoded_pixel_gate_rejects_metadata_only_png_difference(self) -> None:
        from maximum_optimizer.animation_pose_selector import verify_pose_pixel_gate

        bind = self.root / "bind.png"
        posed = self.root / "posed.png"
        pixels = Image.new("RGBA", (8, 8), (20, 30, 40, 255))
        first_meta = PngImagePlugin.PngInfo()
        first_meta.add_text("run", "first")
        second_meta = PngImagePlugin.PngInfo()
        second_meta.add_text("run", "repeat")
        pixels.save(bind, pnginfo=first_meta)
        pixels.save(posed, pnginfo=second_meta)
        self.assertNotEqual(bind.read_bytes(), posed.read_bytes())
        with self.assertRaisesRegex(ValueError, "decoded pixel"):
            verify_pose_pixel_gate({"front": bind}, {"front": posed})

    def test_decoded_pixel_gate_accepts_nonzero_geometric_change_and_seals_pixels(self) -> None:
        from maximum_optimizer.animation_pose_selector import verify_pose_pixel_gate

        bind = self.root / "bind-real.png"
        posed = self.root / "posed-real.png"
        Image.new("RGBA", (10, 10), (0, 0, 0, 255)).save(bind)
        image = Image.new("RGBA", (10, 10), (0, 0, 0, 255))
        image.putpixel((4, 5), (255, 0, 0, 255))
        image.save(posed)
        proof = verify_pose_pixel_gate(
            {"front": bind}, {"front": posed}, minimum_changed_fraction=0.005,
        )
        self.assertEqual(proof.changed_pixels, 1)
        self.assertEqual(proof.total_pixels, 100)
        self.assertAlmostEqual(proof.changed_fraction, 0.01)
        self.assertGreater(proof.mean_absolute_error, 0.0)
        self.assertNotEqual(proof.bind_pixel_bundle_sha256, proof.posed_pixel_bundle_sha256)
        self.assertEqual(len(proof.evidence_sha256), 64)


if __name__ == "__main__":
    unittest.main()
