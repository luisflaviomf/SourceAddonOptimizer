from __future__ import annotations

import json
import math
import copy
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

    def _write_json(self, name: str, payload: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    @staticmethod
    def _legacy_profile() -> dict:
        return {
            "schema": 1,
            "version": "legacy-calibrated-v1",
            "calibrated": True,
            "corpus_hash": "a" * 64,
            "limits": _limits(),
        }

    @staticmethod
    def _typed_profile() -> dict:
        return {
            "schema": 2,
            "version": "lvs-family-typed-v1",
            "calibrated": True,
            "corpus_hash": "b" * 64,
            "selector": "audited-original-round-family-v1",
            "profiles": {
                "general-body-detail-v1": {"limits": _limits(0.2)},
                "round-rigid-v1": {"limits": _limits(0.05)},
            },
        }

    @staticmethod
    def _focused_profile() -> dict:
        return {
            "schema": 3,
            "version": "lvs-focused-v1",
            "calibrated": True,
            "corpus_hash": "c" * 64,
            "selector": "audited-original-round-family-v1",
            "focused_evidence_sha256": (
                "2cc6b330ef97466f4d10986787f2ffd0d35f960c0bd47a32e0159f9559c6615c"
            ),
            "focused_policy": {
                "schema": 1,
                "selector": "surface-risk-top-k-v1",
                "top_k": 3,
            },
            "profiles": {
                "general-body-detail-v1": {
                    "limits": _limits(0.2),
                    "focused_limits": _limits(0.1),
                },
                "round-rigid-v1": {
                    "limits": _limits(0.05),
                    "focused_limits": _limits(0.025),
                },
            },
        }

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

    def test_negative_weight_is_malformed_and_forces_general(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            GENERAL_BODY_DETAIL, classify_original_family,
        )

        positions, triangles = _open_cylinder()
        malformed = _smd(positions, triangles).replace(
            "0 0 1 0 0", "0 0 1 0 0 2 0 1.0 1 -0.5", 1
        )
        source = self._write("negative-weight.smd", malformed)

        result = classify_original_family(_graph(self.root, (source,)))

        self.assertEqual(result.profile_class, GENERAL_BODY_DETAIL)
        self.assertIn("invalid-smd", result.reason)

    def test_overfull_weight_total_is_malformed_and_forces_general(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            GENERAL_BODY_DETAIL, classify_original_family,
        )

        positions, triangles = _open_cylinder()
        malformed = _smd(positions, triangles).replace(
            "0 0 1 0 0", "0 0 1 0 0 2 0 0.8 1 0.8", 1
        )
        source = self._write("overfull-weight.smd", malformed)

        result = classify_original_family(_graph(self.root, (source,)))

        self.assertEqual(result.profile_class, GENERAL_BODY_DETAIL)
        self.assertIn("invalid-smd", result.reason)

    def test_missing_or_outside_source_is_audited_and_forces_general(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            GENERAL_BODY_DETAIL, classify_original_family,
        )

        family = self.root / "family"
        family.mkdir()
        outside = self._write("outside.smd", _smd(*_open_cylinder()))
        missing = family / "missing.smd"

        result = classify_original_family(_graph(family, (missing, outside)))

        self.assertEqual(result.profile_class, GENERAL_BODY_DETAIL)
        self.assertEqual(
            tuple(item.source for item in result.sources),
            ("missing.smd", "outside.smd"),
        )
        self.assertEqual(
            tuple(item.reason for item in result.sources),
            ("missing-source", "source-outside-family"),
        )

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

    def test_legacy_profile_maps_both_classes_to_one_global_profile(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            GENERAL_BODY_DETAIL, LEGACY_GLOBAL, ROUND_RIGID, load_fidelity_profile_set,
        )

        profiles = load_fidelity_profile_set(
            self._write_json("legacy.json", self._legacy_profile())
        )

        self.assertEqual(profiles.mode, LEGACY_GLOBAL)
        self.assertIs(profiles.profile_for(GENERAL_BODY_DETAIL), profiles.profile_for(ROUND_RIGID))
        self.assertEqual(profiles.version, "legacy-calibrated-v1")

    def test_typed_profile_exposes_exact_distinct_classes(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            GENERAL_BODY_DETAIL, ROUND_RIGID, TYPED_SELECTOR,
            load_fidelity_profile_set,
        )

        profiles = load_fidelity_profile_set(
            self._write_json("typed.json", self._typed_profile())
        )

        self.assertEqual(profiles.mode, TYPED_SELECTOR)
        self.assertEqual(profiles.profile_for(GENERAL_BODY_DETAIL).limits["edge_error"], 0.2)
        self.assertEqual(profiles.profile_for(ROUND_RIGID).limits["edge_error"], 0.05)
        self.assertIn(ROUND_RIGID, profiles.profile_for(ROUND_RIGID).version)

    def test_schema3_exposes_trusted_focused_policy_and_profiles(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            GENERAL_BODY_DETAIL, ROUND_RIGID, load_fidelity_profile_set,
        )

        profiles = load_fidelity_profile_set(
            self._write_json("focused.json", self._focused_profile())
        )

        self.assertEqual(profiles.focused_policy.selector, "surface-risk-top-k-v1")
        self.assertEqual(profiles.focused_policy.top_k, 3)
        self.assertEqual(
            profiles.focused_profile_for(GENERAL_BODY_DETAIL).limits["edge_error"],
            0.1,
        )
        self.assertEqual(
            profiles.focused_profile_for(ROUND_RIGID).version,
            "lvs-focused-v1:round-rigid-v1:focused",
        )
        with self.assertRaises(TypeError):
            profiles.focused_profiles[ROUND_RIGID] = profiles.profile_for(ROUND_RIGID)

    def test_schema1_and_schema2_do_not_enable_focused_validation(self) -> None:
        from maximum_optimizer.fidelity_selection import (
            ROUND_RIGID, load_fidelity_profile_set,
        )

        for name, payload in (
            ("legacy", self._legacy_profile()),
            ("typed", self._typed_profile()),
        ):
            with self.subTest(name=name):
                profiles = load_fidelity_profile_set(self._write_json(f"{name}.json", payload))
                self.assertIsNone(profiles.focused_policy)
                with self.assertRaisesRegex(ValueError, "focused fidelity is not enabled"):
                    profiles.focused_profile_for(ROUND_RIGID)

    def test_schema3_fails_closed_on_untrusted_or_invalid_focused_fields(self) -> None:
        from maximum_optimizer.fidelity_selection import load_fidelity_profile_set

        mutations = (
            lambda item: item.update(focused_evidence_sha256="0" * 64),
            lambda item: item["focused_policy"].update(top_k=0),
            lambda item: item["focused_policy"].update(top_k=5),
            lambda item: item["focused_policy"].update(top_k=True),
            lambda item: item["focused_policy"].update(selector="unknown"),
            lambda item: item["focused_policy"].update(extra=True),
            lambda item: item["profiles"]["round-rigid-v1"]["focused_limits"].pop(
                "rgb_mae"
            ),
            lambda item: item["profiles"]["round-rigid-v1"]["focused_limits"].update(
                rgb_mae=float("inf")
            ),
            lambda item: item["profiles"]["round-rigid-v1"]["focused_limits"].update(
                rgb_mae=-0.1
            ),
            lambda item: item["profiles"]["round-rigid-v1"]["focused_limits"].update(
                rgb_mae=True
            ),
            lambda item: item.update(extra=True),
        )
        for index, mutate in enumerate(mutations):
            payload = copy.deepcopy(self._focused_profile())
            mutate(payload)
            with self.subTest(index=index), self.assertRaises(ValueError):
                load_fidelity_profile_set(
                    self._write_json(f"invalid-focused-{index}.json", payload)
                )

    def test_typed_profile_schema_fails_closed(self) -> None:
        from maximum_optimizer.fidelity_selection import load_fidelity_profile_set

        mutations = []
        for mutate in (
            lambda item: item.update(selector="unknown"),
            lambda item: item.update(calibrated=False),
            lambda item: item.update(corpus_hash="bad"),
            lambda item: item.update(extra=True),
            lambda item: item["profiles"].pop("round-rigid-v1"),
            lambda item: item["profiles"].update({"extra": {"limits": _limits()}}),
            lambda item: item["profiles"]["round-rigid-v1"]["limits"].pop("rgb_mae"),
            lambda item: item["profiles"]["round-rigid-v1"]["limits"].update(
                rgb_mae=float("inf")
            ),
        ):
            payload = copy.deepcopy(self._typed_profile())
            mutate(payload)
            mutations.append(payload)
        for index, payload in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(ValueError):
                load_fidelity_profile_set(self._write_json(f"invalid-{index}.json", payload))

    def test_profile_for_rejects_unknown_class(self) -> None:
        from maximum_optimizer.fidelity_selection import load_fidelity_profile_set

        profiles = load_fidelity_profile_set(
            self._write_json("typed.json", self._typed_profile())
        )
        with self.assertRaises(ValueError):
            profiles.profile_for("unknown")


if __name__ == "__main__":
    unittest.main()
