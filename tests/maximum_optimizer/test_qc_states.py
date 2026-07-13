from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from maximum_optimizer.qc_graph import parse_qc_graph
from maximum_optimizer.qc_states import enumerate_qc_states


def _write_state_tree(root: Path, *, texturegroups: int = 1) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in (
        "fixed.smd", "fixed_lod.smd", "door_a.smd", "door_b.smd",
        "door_a_lod.smd", "door_b_lod.smd", "wheel.smd", "wheel_lod.smd",
    ):
        (root / name).write_bytes(name.encode())
    texture = (
        '$texturegroup "skinfamilies"\n{\n { "paint" "glass" }\n'
        ' { "paint_alt" "glass_alt" }\n}\n'
    ) * texturegroups
    (root / "main.qc").write_text(
        '$modelname "models/state.mdl"\n'
        '$body "fixed" "fixed.smd"\n'
        '$bodygroup "door"\n{\n studio "door_a.smd"\n studio "door_b.smd"\n}\n'
        '$bodygroup "wheel"\n{\n blank\n studio "wheel.smd"\n}\n'
        '$lod 20\n{\n'
        ' replacemodel "fixed.smd" "fixed_lod.smd"\n'
        ' replacemodel "door_a.smd" "door_a_lod.smd"\n'
        ' replacemodel "door_b.smd" "door_b_lod.smd"\n'
        ' replacemodel "wheel.smd" "wheel_lod.smd"\n'
        '}\n' + texture,
        encoding="utf-8",
    )
    return root / "main.qc"


class QcStateEnumerationTests(unittest.TestCase):
    def test_exact_cartesian_product_is_root_independent_and_active_only(self) -> None:
        with tempfile.TemporaryDirectory() as first_raw, tempfile.TemporaryDirectory() as second_raw:
            first = Path(first_raw); second = Path(second_raw)
            first_states = enumerate_qc_states(parse_qc_graph(_write_state_tree(first), first))
            second_states = enumerate_qc_states(parse_qc_graph(_write_state_tree(second), second))
            self.assertEqual(len(first_states), 16)
            self.assertEqual(
                tuple((item.state_key, item.bodygroup_key, item.lod_key, item.skin_key) for item in first_states),
                tuple((item.state_key, item.bodygroup_key, item.lod_key, item.skin_key) for item in second_states),
            )
            self.assertEqual(len({item.state_key for item in first_states}), 16)
            blank_states = [
                item for item in first_states
                if all(active.source_identity != "wheel.smd" for active in item.active)
                and all(active.source_identity != "wheel_lod.smd" for active in item.active)
            ]
            self.assertEqual(len(blank_states), 8)
            self.assertTrue(all(len(item.active) == 2 for item in blank_states))

    def test_lod_preserves_original_replacement_pairs_and_exact_replacement_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            states = enumerate_qc_states(parse_qc_graph(_write_state_tree(root), root))
            lod_states = [item for item in states if item.lod_index == 1]
            self.assertEqual(len(lod_states), 8)
            selected = next(
                item for item in lod_states
                if {active.source_identity for active in item.active}
                == {"fixed_lod.smd", "door_a_lod.smd", "wheel_lod.smd"}
            )
            by_source = {item.source_identity: item for item in selected.active}
            self.assertEqual(by_source["fixed_lod.smd"].directive, "$lod/replacemodel")
            self.assertEqual(by_source["fixed_lod.smd"].line, 15)
            self.assertEqual(by_source["door_a_lod.smd"].line, 16)
            self.assertEqual(by_source["wheel_lod.smd"].line, 18)
            self.assertTrue(all(item.replacement_original_identity for item in selected.active))

    def test_rejects_state_overflow_before_any_smd_reader_can_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            graph = parse_qc_graph(_write_state_tree(root), root)
            with self.assertRaisesRegex(ValueError, "16|bound"):
                enumerate_qc_states(graph, maximum_states=15)

    def test_rejects_multiple_or_malformed_texturegroups_and_odd_lod_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "multiple"
            graph = parse_qc_graph(_write_state_tree(root, texturegroups=2), root)
            with self.assertRaisesRegex(ValueError, "texturegroup"):
                enumerate_qc_states(graph)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "malformed"
            qc = _write_state_tree(root)
            text = qc.read_text(encoding="utf-8").replace(
                '{ "paint" "glass" }', '{ { "paint" } }',
            )
            qc.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "texturegroup"):
                enumerate_qc_states(parse_qc_graph(qc, root))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "odd"
            qc = _write_state_tree(root)
            text = qc.read_text(encoding="utf-8").replace(
                'replacemodel "wheel.smd" "wheel_lod.smd"',
                'replacemodel "wheel.smd"',
            )
            qc.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "LOD|pair"):
                enumerate_qc_states(parse_qc_graph(qc, root))

    def test_real_wheel_two_states_and_real_charger_rejects_early_read_only(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        corpus = repository / ".superpowers/benchmark/task4_smoothing_fixed_v1"
        wheel = corpus / "pontiac_transam_wheel/wheel.qc"
        charger = corpus / "dodge_charger/charger.qc"
        if not wheel.is_file() or not charger.is_file():
            self.skipTest("real checked-in LVS source corpus is unavailable")
        before = {
            path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in (wheel, charger)
        }
        wheel_states = enumerate_qc_states(parse_qc_graph(wheel, wheel.parent))
        self.assertEqual(len(wheel_states), 2)
        with self.assertRaisesRegex(ValueError, "16|bound"):
            enumerate_qc_states(parse_qc_graph(charger, charger.parent))
        after = {
            path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in (wheel, charger)
        }
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
