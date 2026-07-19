from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

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


def _mesh(
    bones: tuple[tuple[int, str, int], ...],
    *,
    bind: dict[int, tuple[float, ...]] | None = None,
) -> str:
    rows = [_smd(bones=bones, frames={
        0: bind or {bone: (0, 0, 0, 0, 0, 0) for bone, _name, _parent in bones}
    }).rstrip(), "triangles"]
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
        geometry_bind = {
            bone: bind.get(bone, (0, 0, 0, 0, 0, 0))
            for bone, _name, _parent in self.bones
        }
        (self.root / "body.smd").write_text(
            _mesh(self.bones, bind=geometry_bind), encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def select(self):
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        return select_animation_pose(
            self.root,
            source_root=self.root,
            animation_pairs=(("hood.smd", "hood_corrective_animation.smd"),
                             ("door.smd", "door_corrective_animation.smd")),
            geometry_paths=("body.smd",),
        )

    def test_selects_largest_exact_source_transform_and_binds_lineage(self) -> None:
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        selected = self.select()
        self.assertEqual(selected.selector, "exact-raw-render-region-displacement-v3")
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
        first = select_animation_pose(
            self.root, source_root=self.root,
            animation_pairs=(("a.smd", "a_corrective_animation.smd"),
                             ("hood.smd", "hood_corrective_animation.smd"),
                             ("door.smd", "door_corrective_animation.smd")),
            geometry_paths=("body.smd",),
        )
        ignored_candidate = self.root / "output" / "candidate.smd"
        ignored_candidate.parent.mkdir()
        ignored_candidate.write_text("candidate data must not affect exact selection", encoding="utf-8")
        second = select_animation_pose(
            self.root, source_root=self.root,
            animation_pairs=(("a.smd", "a_corrective_animation.smd"),
                             ("hood.smd", "hood_corrective_animation.smd"),
                             ("door.smd", "door_corrective_animation.smd")),
            geometry_paths=("body.smd",),
        )
        self.assertEqual(first, second)
        self.assertEqual(first.animation_relative_path, "a.smd")

    def test_all_exact_pair_hashes_are_bound_not_only_the_winner(self) -> None:
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        before = self.select()
        door = self.root / "door.smd"
        door.write_text(door.read_text(encoding="utf-8") + "// exact source mutation\n", encoding="utf-8")
        after = self.select()
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
            geometry_bind = {
                bone: ({0: (0, 0, 0, 0, 0, -1.570796)}).get(
                    bone, (0, 0, 0, 0, 0, 0),
                )
                for bone, _name, _parent in self.bones
            }
            (root / "body.smd").write_text(
                _mesh(self.bones, bind=geometry_bind), encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "paired exact animation"):
                select_animation_pose(
                    root, source_root=root,
                    animation_pairs=(("idle.smd", "idle_corrective_animation.smd"),),
                    geometry_paths=("body.smd",),
                )
            (root / "idle_corrective_animation.smd").write_text(zero, encoding="utf-8")
            selected = select_animation_pose(
                root, source_root=root,
                animation_pairs=(("idle.smd", "idle_corrective_animation.smd"),),
                geometry_paths=("body.smd",),
            )
            self.assertEqual(selected.to_payload()["pose_keys"], ["bind"])
            self.assertEqual(
                selected.reason, "no-exact-region-influencing-animation-displacement",
            )

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
            verify_pose_pixel_gate(
                {"front": bind}, {"front": posed},
                camera_directions={"front": (1.0, 0.0, 0.0)},
                minimum_silhouette_pixels=1,
            )

    def test_decoded_pixel_gate_accepts_nonzero_geometric_change_and_seals_pixels(self) -> None:
        from maximum_optimizer.animation_pose_selector import verify_pose_pixel_gate

        bind = self.root / "bind-real.png"
        posed = self.root / "posed-real.png"
        bind_right = self.root / "bind-right.png"
        posed_right = self.root / "posed-right.png"
        Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(bind)
        Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(bind_right)
        image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        image.putpixel((4, 5), (255, 0, 0, 255))
        image.save(posed)
        image.save(posed_right)
        proof = verify_pose_pixel_gate(
            {"front": bind, "right": bind_right},
            {"front": posed, "right": posed_right},
            camera_directions={"front": (1.0, 0.0, 0.0), "right": (0.0, 1.0, 0.0)},
            minimum_changed_fraction=0.005,
            minimum_silhouette_pixels=1,
        )
        self.assertEqual(proof.changed_pixels, 2)
        self.assertEqual(proof.total_pixels, 200)
        self.assertAlmostEqual(proof.changed_fraction, 1.0)
        self.assertGreater(proof.mean_absolute_error, 0.0)
        self.assertNotEqual(proof.bind_pixel_bundle_sha256, proof.posed_pixel_bundle_sha256)
        self.assertEqual(len(proof.evidence_sha256), 64)
        payload = proof.to_payload()
        seal = payload.pop("evidence_sha256")
        from maximum_optimizer.animation_pose_selector import _canonical_hash
        self.assertEqual(_canonical_hash(payload), seal)

    def test_pixel_measurement_preserves_subthreshold_source_evidence(self) -> None:
        from maximum_optimizer.animation_pose_selector import (
            measure_pose_pixels,
            verify_no_visible_pose_pixel_measurement,
            verify_pose_pixel_gate,
        )

        bind_images = {}
        posed_images = {}
        directions = {"front": (1.0, 0.0, 0.0), "right": (0.0, 1.0, 0.0)}
        for key in directions:
            bind = self.root / f"measure-{key}-bind.png"
            posed = self.root / f"measure-{key}-posed.png"
            Image.new("RGBA", (10, 10), (10, 20, 30, 255)).save(bind)
            Image.new("RGBA", (10, 10), (11, 20, 30, 255)).save(posed)
            bind_images[key] = bind
            posed_images[key] = posed

        measurement = measure_pose_pixels(
            bind_images, posed_images, camera_directions=directions,
            minimum_changed_fraction=0.005, minimum_silhouette_pixels=1,
            required_view_count=2, required_size=(10, 10),
        )
        payload = measurement.to_payload()
        assert payload["kind"] == "pose-pixel-measurement-v1"
        assert payload["changed_pixels"] == 0
        assert payload["qualified_silhouette_views"] == []
        assert payload["mean_absolute_error"] > 0.0
        assert verify_no_visible_pose_pixel_measurement(measurement) == measurement
        with self.assertRaisesRegex(ValueError, "orthogonal"):
            verify_pose_pixel_gate(
                bind_images, posed_images, camera_directions=directions,
                minimum_changed_fraction=0.005, minimum_silhouette_pixels=1,
                required_view_count=2, required_size=(10, 10),
            )

    def test_no_visible_measurement_rejects_qualifying_orthogonal_pair(self) -> None:
        from maximum_optimizer.animation_pose_selector import (
            measure_pose_pixels,
            verify_no_visible_pose_pixel_measurement,
        )

        bind_images = {}
        posed_images = {}
        directions = {"front": (1.0, 0.0, 0.0), "right": (0.0, 1.0, 0.0)}
        for key in directions:
            bind = self.root / f"visible-{key}-bind.png"
            posed = self.root / f"visible-{key}-posed.png"
            Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(bind)
            changed = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
            changed.putpixel((4, 5), (255, 0, 0, 255))
            changed.save(posed)
            bind_images[key] = bind
            posed_images[key] = posed
        measurement = measure_pose_pixels(
            bind_images, posed_images, camera_directions=directions,
            minimum_changed_fraction=0.005, minimum_silhouette_pixels=1,
            required_view_count=2, required_size=(10, 10),
        )
        with self.assertRaisesRegex(ValueError, "orthogonal"):
            verify_no_visible_pose_pixel_measurement(measurement)

    def test_pixel_gate_ignores_invisible_rgb_and_requires_every_view(self) -> None:
        from maximum_optimizer.animation_pose_selector import verify_pose_pixel_gate

        bind_a = self.root / "bind-a.png"
        pose_a = self.root / "pose-a.png"
        bind_b = self.root / "bind-b.png"
        pose_b = self.root / "pose-b.png"
        Image.new("RGBA", (10, 10), (1, 2, 3, 0)).save(bind_a)
        Image.new("RGBA", (10, 10), (250, 240, 230, 0)).save(pose_a)
        Image.new("RGBA", (10, 10), (0, 0, 0, 255)).save(bind_b)
        changed = Image.new("RGBA", (10, 10), (0, 0, 0, 255))
        changed.putpixel((1, 1), (255, 0, 0, 255))
        changed.save(pose_b)
        with self.assertRaisesRegex(ValueError, "foreground"):
            verify_pose_pixel_gate(
                {"a": bind_a, "b": bind_b}, {"a": pose_a, "b": pose_b},
                camera_directions={"a": (1.0, 0.0, 0.0), "b": (0.0, 1.0, 0.0)},
                minimum_changed_fraction=0.005,
                minimum_silhouette_pixels=1,
            )

    def test_pixel_gate_rejects_opposite_only_silhouette_views(self) -> None:
        from maximum_optimizer.animation_pose_selector import verify_pose_pixel_gate

        paths = {}
        for key in ("front", "back"):
            bind = self.root / f"{key}-bind.png"
            pose = self.root / f"{key}-pose.png"
            Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(bind)
            changed = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            changed.putpixel((2, 2), (255, 255, 255, 255))
            changed.save(pose)
            paths[key] = (bind, pose)
        with self.assertRaisesRegex(ValueError, "orthogonal"):
            verify_pose_pixel_gate(
                {key: item[0] for key, item in paths.items()},
                {key: item[1] for key, item in paths.items()},
                camera_directions={"front": (1.0, 0.0, 0.0), "back": (-1.0, 0.0, 0.0)},
                minimum_silhouette_pixels=1,
            )

    def test_selector_rejects_nonfinite_geometry_and_lineage_mismatch(self) -> None:
        body = self.root / "body.smd"
        original = body.read_text(encoding="utf-8")
        body.write_text(original.replace("-40.000000", "nan", 1), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "non-finite"):
            self.select()
        body.write_text(original.replace('1 "Hood" 0', '1 "Wrong" 0'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "lineage"):
            self.select()

    def test_selector_uses_only_explicit_contained_allowlists(self) -> None:
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        candidate = self.root / "candidate" / "candidate.smd"
        candidate.parent.mkdir()
        candidate.write_text(_mesh(self.bones), encoding="utf-8")
        before = self.select()
        candidate.write_text(candidate.read_text(encoding="utf-8") + "// mutate\n", encoding="utf-8")
        self.assertEqual(before, self.select())
        with tempfile.TemporaryDirectory() as outside_raw:
            outside = Path(outside_raw) / "outside.smd"
            outside.write_text(_mesh(self.bones), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "contained"):
                select_animation_pose(
                    self.root, source_root=self.root,
                    animation_pairs=(("hood.smd", "hood_corrective_animation.smd"),),
                    geometry_paths=(outside,),
                )

    def test_selector_emits_importer_frame_ordinal_and_binds_source_time(self) -> None:
        hood = self.root / "hood.smd"
        hood.write_text(_smd(
            bones=self.bones,
            frames={
                10: {0: (0, 0, 0, 0, 0, -1.570796), 1: (0, 0, 0, 0, 0, 0.2)},
                30: {0: (0, 0, 0, 0, 0, -1.570796), 1: (0, 0, 0, 0, 0, 2.0)},
            },
        ), encoding="utf-8")
        selected = self.select()
        self.assertEqual(selected.frame, 1)
        self.assertEqual(selected.source_time, 30)
        self.assertEqual(selected.to_payload()["source_time"], 30)

    def test_selector_frame_ordinal_follows_textual_time_block_order(self) -> None:
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        header = _smd(bones=self.bones, frames={}).split("skeleton", 1)[0]
        animation = header + """skeleton
time 7
0 0 0 0 0 0 -1.570796
1 0 0 0 0 0 0.2
time 3
0 0 0 0 0 0 -1.570796
1 0 0 0 0 0 2.0
end
"""
        (self.root / "hood.smd").write_text(animation, encoding="utf-8")

        selected = select_animation_pose(
            self.root, source_root=self.root,
            animation_pairs=(("hood.smd", "hood_corrective_animation.smd"),),
            geometry_paths=("body.smd",),
        )

        self.assertEqual(selected.frame, 1)
        self.assertEqual(selected.source_time, 3)

    def test_missing_animation_transform_inherits_nonzero_geometry_bind(self) -> None:
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        body = self.root / "body.smd"
        rows = [_smd(bones=self.bones, frames={0: {
            0: (10, 0, 0, 0, 0, 0),
            1: (0, 5, 0, 0, 0, 0),
            2: (0, 0, 2, 0, 0, 0),
        }}).rstrip(), "triangles"]
        rows.append("material")
        for position in ((-1, 0, 0), (1, 0, 0), (0, 1, 0)):
            rows.append(f"1 {position[0]} {position[1]} {position[2]} 0 0 1 0 0 1 1 1.0")
        rows.extend(("end", ""))
        body.write_text("\n".join(rows), encoding="utf-8")
        (self.root / "hood_corrective_animation.smd").write_text(
            _smd(bones=self.bones, frames={0: {0: (10, 0, 0, 0, 0, 0)}}),
            encoding="utf-8",
        )
        (self.root / "hood.smd").write_text(
            _smd(bones=self.bones, frames={0: {1: (0, 5, 0, 0, 0, 0.1)}}),
            encoding="utf-8",
        )
        # Door animation is omitted from the allowlist; Hood frame omits the root.
        # It must inherit root's non-zero bind instead of translating by -10.
        selected = select_animation_pose(
            self.root, source_root=self.root,
            animation_pairs=(("hood.smd", "hood_corrective_animation.smd"),),
            geometry_paths=("body.smd",),
        )
        self.assertEqual(selected.bone_name, "Hood")
        self.assertLess(selected.displacement, 5.0)

    def test_selector_scores_raw_rest_to_action_with_parent_local_ref_rows(self) -> None:
        from maximum_optimizer.animation_pose_selector import select_animation_pose

        nested = ((0, "root", -1), (1, "Hood", 0))
        bind = {
            0: (10, 0, 0, 0, 0, 0),
            1: (0, 5, 0, 0, 0, 0),
        }
        rows = [_smd(bones=nested, frames={0: bind}).rstrip(), "triangles", "material"]
        for x, y, z in ((10, 6, 0), (10, 7, 0), (11, 6, 0)):
            rows.append(f"1 {x} {y} {z} 0 0 1 0 0 1 1 1.0")
        # Source Tools merges mesh vertices by (coordinate, weights); repeated
        # triangle corners must not bias the evaluated-vertex RMS.
        rows.extend(("material",) + tuple(
            f"1 10 6 0 {index} 1 {index / 10} {index / 20} 1 1 1 1.0"
            for index in range(3)
        ))
        rows.extend(("end", ""))
        (self.root / "body.smd").write_text("\n".join(rows), encoding="utf-8")
        (self.root / "nested.smd").write_text(_smd(
            bones=nested,
            frames={7: {**bind, 1: (0, 5, 0, 0, 0, 1.5707963267948966)}},
        ), encoding="utf-8")
        # Corrective stays sealed as provenance but cannot become the raw-render baseline.
        (self.root / "nested_corrective.smd").write_text(_smd(
            bones=nested,
            frames={0: {**bind, 1: (0, 5, 0, 0, 0, -1.5707963267948966)}},
        ), encoding="utf-8")

        selected = select_animation_pose(
            self.root, source_root=self.root,
            animation_pairs=(("nested.smd", "nested_corrective.smd"),),
            geometry_paths=("body.smd",),
        )

        self.assertEqual(selected.selector, "exact-raw-render-region-displacement-v3")
        self.assertAlmostEqual(selected.displacement, 8.0 ** 0.5, places=5)
        self.assertAlmostEqual(selected.rms_displacement, (14.0 / 3.0) ** 0.5, places=5)
        payload = selected.to_payload()
        self.assertEqual(payload["baseline"], "geometry-rest")
        self.assertFalse(payload["corrective_used_as_baseline"])
        self.assertAlmostEqual(payload["raw_render_max_displacement"], 8.0 ** 0.5, places=5)
        self.assertAlmostEqual(payload["raw_render_rms_displacement"], (14.0 / 3.0) ** 0.5, places=5)

    def test_geometry_bone_ids_may_differ_when_name_parent_lineage_matches(self) -> None:
        remapped = ((10, "root", -1), (20, "Hood", 10), (30, "Door", 10))
        rows = [_smd(bones=remapped, frames={
            0: {
                bone: ((0, 0, 0, 0, 0, -1.570796) if name == "root" else
                       (0, 0, 0, 0, 0, 0))
                for bone, name, _parent in remapped
            }
        }).rstrip(), "triangles"]
        for bone in (20, 30):
            rows.append("material")
            for position in ((-2, 0, 0), (2, 0, 0), (0, 2, 0)):
                rows.append(f"{bone} {position[0]} {position[1]} {position[2]} 0 0 1 0 0 1 {bone} 1.0")
        rows.extend(("end", ""))
        (self.root / "body.smd").write_text("\n".join(rows), encoding="utf-8")
        selected = self.select()
        self.assertEqual(selected.bone_name, "Hood")

    def test_multi_frame_corrective_reference_is_rejected(self) -> None:
        (self.root / "hood_corrective_animation.smd").write_text(
            _smd(bones=self.bones, frames={
                0: {0: (0, 0, 0, 0, 0, 0)},
                1: {0: (0, 0, 0, 0, 0, 0)},
            }), encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.select()

    def test_empty_corrective_frame_is_rejected_but_compatible_subset_is_valid(self) -> None:
        (self.root / "hood_corrective_animation.smd").write_text(
            _smd(bones=self.bones, frames={0: {}}), encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "corrective.*empty"):
            self.select()
        (self.root / "hood_corrective_animation.smd").write_text(
            _smd(bones=self.bones, frames={0: {
                0: (0, 0, 0, 0, 0, -1.570796),
            }}), encoding="utf-8",
        )
        self.assertEqual(self.select().animation_relative_path, "hood.smd")

    def test_malformed_geometry_vertex_and_negative_link_count_are_rejected(self) -> None:
        body = self.root / "body.smd"
        original = body.read_text(encoding="utf-8")
        lines = original.splitlines()
        vertex_index = next(
            index for index, line in enumerate(lines)
            if len(line.split()) >= 12 and line.split()[0].lstrip("-").isdigit()
        )
        malformed = list(lines)
        malformed[vertex_index] = malformed[vertex_index].replace(
            malformed[vertex_index].split()[1], "broken", 1,
        )
        body.write_text("\n".join(malformed) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "geometry vertex"):
            self.select()

        negative = list(lines)
        tokens = negative[vertex_index].split()
        tokens[9:] = ["-1"]
        negative[vertex_index] = " ".join(tokens)
        body.write_text("\n".join(negative) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "weights"):
            self.select()

        negative_weight = list(lines)
        tokens = negative_weight[vertex_index].split()
        tokens[9:] = ["2", "1", "1.0", "2", "-0.25"]
        negative_weight[vertex_index] = " ".join(tokens)
        body.write_text("\n".join(negative_weight) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "negative.*weight|weights"):
            self.select()

    def test_geometry_triangle_inventory_requires_complete_triplets_and_end(self) -> None:
        body = self.root / "body.smd"
        original = body.read_text(encoding="utf-8")
        lines = original.splitlines()
        last_end = max(index for index, line in enumerate(lines) if line.strip() == "end")
        body.write_text("\n".join(lines[:last_end]) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "triangle.*end"):
            self.select()

        lines = original.splitlines()
        last_end = max(index for index, line in enumerate(lines) if line.strip() == "end")
        del lines[last_end - 1]
        body.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "triangle.*complete"):
            self.select()

    def test_selection_payload_seal_is_exactly_self_verifiable(self) -> None:
        from maximum_optimizer.animation_pose_selector import _canonical_hash

        payload = self.select().to_payload()
        seal = payload.pop("selection_sha256")
        self.assertEqual(_canonical_hash(payload), seal)

    def test_external_charger_fixture_matches_blender_evaluated_vertices(self) -> None:
        from maximum_optimizer.animation_pose_selector import _parse_animation, select_animation_pose

        raw_root = os.environ.get("MAXIMUM_CHARGER_FIXTURE_ROOT")
        if raw_root is None:
            self.skipTest("MAXIMUM_CHARGER_FIXTURE_ROOT is not configured")
        root = Path(raw_root).resolve(strict=True)
        corrective = _parse_animation(
            root / "charger_anims" / "hood2_corrective_animation.smd",
        )
        self.assertEqual(len(corrective.bones), 32)
        self.assertEqual(len(next(iter(corrective.frames.values()))), 2)
        arguments = dict(
            source_root=root,
            animation_pairs=(("hood2.smd", "hood2_corrective_animation.smd"),),
            geometry_paths=("hood_a.smd",),
        )
        first = select_animation_pose(root / "charger_anims", **arguments)
        repeat = select_animation_pose(root / "charger_anims", **arguments)

        self.assertEqual(first, repeat)
        self.assertEqual(first.selection_sha256, repeat.selection_sha256)
        self.assertAlmostEqual(first.displacement, 94.90866126623365, places=9)
        self.assertAlmostEqual(first.rms_displacement, 67.92509682249054, places=9)
        # Independent Blender 5.0.1 + Source Tools evaluated-vertex probe.
        self.assertLess(abs(first.displacement - 94.9086307914813), 5e-5)
        self.assertLess(abs(first.rms_displacement - 67.92508631579854), 5e-5)

    def test_selector_enforces_pair_and_exact_file_caps_before_parsing(self) -> None:
        from maximum_optimizer import animation_pose_selector as module

        with patch.object(module, "_MAX_ANIMATION_PAIRS", 1), self.assertRaisesRegex(
            ValueError, "allowlist count",
        ):
            self.select()
        with patch.object(module, "_MAX_EXACT_FILE_BYTES", 8), self.assertRaisesRegex(
            ValueError, "paired exact animation",
        ):
            self.select()

    def test_pixel_gate_rejects_untyped_keys_pre_read_byte_cap_and_wrong_size(self) -> None:
        from maximum_optimizer import animation_pose_selector as module

        image = self.root / "bounded.png"
        Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(image)
        with self.assertRaisesRegex(ValueError, "key"):
            module.verify_pose_pixel_gate(
                {1: image}, {1: image}, camera_directions={1: (1, 0, 0)},  # type: ignore[dict-item]
            )
        with patch.object(module, "_MAX_IMAGE_BYTES", 4), self.assertRaisesRegex(
            ValueError, "decode",
        ):
            module.verify_pose_pixel_gate(
                {"front": image}, {"front": image},
                camera_directions={"front": (1, 0, 0)},
            )
        with self.assertRaisesRegex(ValueError, "dimensions"):
            module.verify_pose_pixel_gate(
                {"front": image}, {"front": image},
                camera_directions={"front": (1, 0, 0)},
                required_size=(512, 512),
            )

    def test_png_decoder_rejects_idat_before_ihdr(self) -> None:
        from maximum_optimizer import animation_pose_selector as module

        valid = self.root / "valid.png"
        invalid = self.root / "idat-first.png"
        Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(valid)
        payload = valid.read_bytes()
        signature = payload[:8]
        chunks = []
        offset = 8
        while offset < len(payload):
            size = int.from_bytes(payload[offset:offset + 4], "big")
            end = offset + size + 12
            chunks.append(payload[offset:end])
            offset = end
        ihdr = next(chunk for chunk in chunks if chunk[4:8] == b"IHDR")
        idat = next(chunk for chunk in chunks if chunk[4:8] == b"IDAT")
        remaining = [chunk for chunk in chunks if chunk not in (ihdr, idat)]
        invalid.write_bytes(signature + idat + ihdr + b"".join(remaining))

        with self.assertRaisesRegex(ValueError, "header.*first|order"):
            module._decode_png_rgba8(invalid)

    def test_png_decoder_rejects_unknown_critical_chunk(self) -> None:
        from maximum_optimizer import animation_pose_selector as module

        valid = self.root / "valid-critical.png"
        invalid = self.root / "unknown-critical.png"
        Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(valid)
        payload = valid.read_bytes()
        ihdr_size = int.from_bytes(payload[8:12], "big") + 12
        insertion = 8 + ihdr_size
        kind = b"ABCD"
        critical = struct.pack(">I", 0) + kind + struct.pack(">I", zlib.crc32(kind))
        invalid.write_bytes(payload[:insertion] + critical + payload[insertion:])

        with self.assertRaisesRegex(ValueError, "critical"):
            module._decode_png_rgba8(invalid)


if __name__ == "__main__":
    unittest.main()
