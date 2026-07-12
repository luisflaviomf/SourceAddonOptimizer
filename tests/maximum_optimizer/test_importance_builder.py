from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from benchmarks.lvs_models.build_blender_importance_map_v1 import image_set_sha256


class ImportanceBuilderTests(unittest.TestCase):
    def test_image_set_digest_is_deterministic_and_content_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, angle in enumerate(
                ("front", "back", "left", "right", "top", "bottom", "iso1", "iso2")
            ):
                path = root / "clay" / "bind" / f"{angle}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"fixture-{index}".encode("ascii"))
            first = image_set_sha256(root)
            self.assertEqual(first, image_set_sha256(root))
            (root / "clay" / "bind" / "iso2.png").write_bytes(b"changed")
            self.assertNotEqual(first, image_set_sha256(root))


if __name__ == "__main__":
    unittest.main()
