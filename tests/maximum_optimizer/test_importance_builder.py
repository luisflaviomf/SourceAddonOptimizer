from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image, PngImagePlugin

from benchmarks.lvs_models.build_blender_importance_map_v1 import (
    image_set_sha256,
    implementation_snapshot,
)
from maximum_optimizer.importance_evidence import (
    canonical_implementation_snapshot_hash,
)


class ImportanceBuilderTests(unittest.TestCase):
    def test_implementation_snapshot_is_explicitly_archived_and_self_sealed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.py"
            second = Path(tmp) / "second.py"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            snapshot = implementation_snapshot({"first.py": first, "second.py": second})
        self.assertEqual(snapshot["scope"], "archived-execution-snapshot")
        self.assertEqual(
            snapshot["snapshot_sha256"],
            canonical_implementation_snapshot_hash(snapshot["files"]),
        )

    def test_image_set_digest_is_deterministic_and_content_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "first"
            equivalent = Path(tmp) / "equivalent"
            for index, angle in enumerate(
                ("front", "back", "left", "right", "top", "bottom", "iso1", "iso2")
            ):
                for target, note in ((root, "first"), (equivalent, "different metadata")):
                    path = target / "clay" / "bind" / f"{angle}.png"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    metadata = PngImagePlugin.PngInfo()
                    metadata.add_text("note", note)
                    Image.new("RGBA", (2, 2), (index, 20, 30, 255)).save(
                        path, pnginfo=metadata
                    )
            first = image_set_sha256(root)
            self.assertEqual(first, image_set_sha256(root))
            self.assertEqual(first, image_set_sha256(equivalent))
            Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(
                root / "clay" / "bind" / "iso2.png"
            )
            self.assertNotEqual(first, image_set_sha256(root))


if __name__ == "__main__":
    unittest.main()
