from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from render_previews import (
    _render_extended_pair,
    _render_side_flags,
    _reuse_reference_entries,
)


class RenderReferenceModeTests(unittest.TestCase):
    def test_candidate_only_mode_loads_both_geometries_but_renders_only_candidate(self) -> None:
        calls: list[tuple[str, bool, object]] = []

        def reference(*, render_images: bool):
            calls.append(("reference", render_images, None))
            return (["reference-entry"], {"reference": "geometry"}, "fit", "bbox")

        def candidate(*, render_images: bool, fit):
            calls.append(("candidate", render_images, fit))
            return (["candidate-entry"], {"candidate": "geometry"}, fit, "bbox")

        reference_result, candidate_result = _render_extended_pair(
            "candidate", reference, candidate
        )

        self.assertEqual(
            calls,
            [("reference", False, None), ("candidate", True, "fit")],
        )
        self.assertEqual(reference_result[1], {"reference": "geometry"})
        self.assertEqual(candidate_result[1], {"candidate": "geometry"})

    def test_render_side_flags_are_strict(self) -> None:
        self.assertEqual(_render_side_flags("both"), (True, True))
        self.assertEqual(_render_side_flags("reference"), (True, False))
        self.assertEqual(_render_side_flags("candidate"), (False, True))
        with self.assertRaises(ValueError):
            _render_side_flags("invalid")

    def test_reused_reference_entries_require_exact_image_hashes(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        image = root / "textured/bind/front.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"sealed-reference")
        entry = {
            "pass": "textured",
            "pose": "bind",
            "angle": "front",
            "image": "textured/bind/front.png",
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "texture_missing": False,
            "missing_materials": [],
            "resolved_materials": [],
        }
        (root / "render_manifest.json").write_text(
            json.dumps({"schema": 1, "entries": [entry]}), encoding="utf-8"
        )

        self.assertEqual(_reuse_reference_entries(root), [entry])
        image.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "hash"):
            _reuse_reference_entries(root)


if __name__ == "__main__":
    unittest.main()
