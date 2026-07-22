from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from batch_compile_opt_qc import DEFAULT_ROOT, _studiomdl_execution_paths


class StudioMdlPathTests(unittest.TestCase):
    def test_default_scan_root_is_portable(self) -> None:
        self.assertEqual(DEFAULT_ROOT, ".")
        self.assertFalse(Path(DEFAULT_ROOT).is_absolute())

    @unittest.skipUnless(os.name == "nt", "directory junctions are Windows-specific")
    def test_long_source_and_game_paths_receive_short_live_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / ("source-" + "x" * 180)
            game = root / ("game-" + "y" * 180)
            source.mkdir()
            game.mkdir()
            (source / "mesh.smd").write_text("version 1\n", encoding="utf-8")
            (game / "gameinfo.txt").write_text("GameInfo {}\n", encoding="utf-8")

            with _studiomdl_execution_paths(source, game) as (short_source, short_game):
                self.assertLess(len(str(short_source)), 100)
                self.assertLess(len(str(short_game)), 100)
                self.assertEqual((short_source / "mesh.smd").read_text(encoding="utf-8"), "version 1\n")
                self.assertTrue((short_game / "gameinfo.txt").is_file())
                source_alias = short_source
                game_alias = short_game

            self.assertFalse(source_alias.exists())
            self.assertFalse(game_alias.exists())


if __name__ == "__main__":
    unittest.main()
