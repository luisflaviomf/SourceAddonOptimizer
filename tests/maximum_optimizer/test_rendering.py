from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import tempfile
import unittest

from PIL import Image

from maximum_optimizer.materials import MaterialSemantics
from maximum_optimizer.rendering import (
    RenderRequest,
    build_render_command,
    render_region_comparison,
    requires_targeted_render,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TargetedRenderingTests(unittest.TestCase):
    def test_only_semantic_near_limit_or_low_confidence_regions_escalate(self) -> None:
        opaque = MaterialSemantics.opaque()
        translucent = MaterialSemantics(
            translucent=True,
            resolver="addon",
            confidence=1.0,
        )

        self.assertFalse(requires_targeted_render(opaque, margin=0.40, confidence=1.0, reduction=0.40))
        self.assertTrue(requires_targeted_render(translucent, margin=0.40, confidence=1.0, reduction=0.05))
        self.assertFalse(requires_targeted_render(translucent, margin=0.40, confidence=1.0, reduction=0.0))
        self.assertTrue(requires_targeted_render(opaque, margin=0.10, confidence=1.0, reduction=0.0))
        self.assertTrue(requires_targeted_render(opaque, margin=0.40, confidence=0.60, reduction=0.05))
        self.assertFalse(requires_targeted_render(opaque, margin=0.40, confidence=0.60, reduction=0.0))

    def test_render_request_contains_one_region_not_family_states(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            camera = root / "camera.json"
            camera.write_text('{"angles":["iso1"]}', encoding="utf-8")
            request = RenderRequest(
                original_region_smd=FIXTURES / "two_components.smd",
                candidate_region_smd=FIXTURES / "two_components_OPT.smd",
                camera_json=camera,
                output_dir=root / "out",
                blender=Path("C:/Blender/blender.exe"),
                pose="reference",
                blender_version="Blender 5.0.1",
                material_roots=(root / "addon",),
                material_directories=(PurePosixPath("models/Cars/Vehicle"),),
            )
            command = build_render_command(request)

        self.assertEqual(command.count("--before"), 1)
        self.assertEqual(command.count("--after"), 1)
        self.assertNotIn("--all-bodygroups", command)
        self.assertIn("--camera-json", command)
        self.assertEqual(command[command.index("--size") + 1], "512")
        self.assertEqual(
            command[command.index("--material-directory") + 1],
            "models/Cars/Vehicle",
        )

    def test_identical_cached_render_is_reused_without_second_blender_run(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            camera = root / "camera.json"
            camera.write_text('{"angles":["front"]}', encoding="utf-8")
            request = RenderRequest(
                original_region_smd=FIXTURES / "two_components.smd",
                candidate_region_smd=FIXTURES / "two_components_OPT.smd",
                camera_json=camera,
                output_dir=root / "out",
                blender=Path("C:/Blender/blender.exe"),
                pose="reference",
                blender_version="Blender 5.0.1",
            )

            def fake_blender(command):
                calls.append(tuple(command))
                original = request.output_dir / "original"
                optimized = request.output_dir / "optimized"
                original.mkdir(parents=True, exist_ok=True)
                optimized.mkdir(parents=True, exist_ok=True)
                image = Image.new("RGB", (32, 32), (40, 50, 60))
                image.save(original / "front.png")
                image.save(optimized / "front.png")
                (request.output_dir / "preview_summary.json").write_text(
                    json.dumps(
                        {
                            "angles": ["front"],
                            "before": {"images": {"front": "original/front.png"}},
                            "after": {"images": {"front": "optimized/front.png"}},
                        }
                    ),
                    encoding="utf-8",
                )

            first = render_region_comparison(request, run_blender=fake_blender)
            second = render_region_comparison(
                request,
                run_blender=lambda _command: self.fail("cached render must be reused"),
            )

        self.assertTrue(first.passed)
        self.assertEqual(first.rgb_mae, 0.0)
        self.assertEqual(first.edge_error, 0.0)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(first.cache_key, second.cache_key)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
