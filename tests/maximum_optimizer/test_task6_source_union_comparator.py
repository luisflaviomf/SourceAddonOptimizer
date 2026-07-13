from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maximum_optimizer.visual_validation import (
    GEOMETRY_METRICS,
    FidelityProfile,
    REQUIRED_METRICS,
    SourceUnionComparisonContract,
    compare_source_union_render_sets,
    compare_render_sets,
)
from tests.maximum_optimizer.test_visual_validation import (
    ANGLES, PASSES, _entry, _write_manifest,
)


H = {letter: letter * 64 for letter in "0123456789abcdef"}


def _profile() -> FidelityProfile:
    return FidelityProfile(1, "union", True, H["f"], {metric: 1.0 for metric in REQUIRED_METRICS})


def _contract() -> SourceUnionComparisonContract:
    return SourceUnionComparisonContract.create(
        target_sha256=H["0"], source_identity="meshes/body.smd",
        source_coverage_sha256=H["1"], reference_source_sha256=H["2"],
        candidate_source_sha256=H["3"], material_contract_sha256=H["4"],
        pose_frames=(('bind', 0),), union_key="source-union-" + "1" * 32,
    )


def _write_union_side(root: Path, side: str, contract: SourceUnionComparisonContract) -> None:
    entries = [_entry(root, render_pass, "bind", angle) for render_pass in PASSES for angle in ANGLES]
    _write_manifest(root, entries, regions=(contract.union_key,))
    path = root / "render_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("configuration"); payload.pop("bbox"); payload.pop("sampling")
    payload.update({
        "kind": "adaptive-direct-source-union-render-v1", "side": side,
        "contract_sha256": contract.contract_sha256,
        "target_sha256": contract.target_sha256,
        "source_identity": contract.source_identity,
        "source_coverage_sha256": contract.source_coverage_sha256,
        "source_sha256": (
            contract.reference_source_sha256 if side == "reference"
            else contract.candidate_source_sha256
        ),
        "material_contract_sha256": contract.material_contract_sha256,
    })
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


class SourceUnionComparatorTests(unittest.TestCase):
    def test_valid_state_independent_manifest_uses_all_real_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference"; candidate = root / "candidate"
            reference.mkdir(); candidate.mkdir(); contract = _contract()
            _write_union_side(reference, "reference", contract)
            _write_union_side(candidate, "candidate", contract)
            result = compare_source_union_render_sets(reference, candidate, _profile(), expected_contract=contract)
            self.assertTrue(result.passed)
            self.assertEqual(set(REQUIRED_METRICS), set(result.metrics) - {"fidelity_score"})

    def test_shared_core_matches_legacy_for_identical_images_and_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); contract = _contract()
            union_ref = root / "union-ref"; union_cand = root / "union-cand"
            legacy_ref = root / "legacy-ref"; legacy_cand = root / "legacy-cand"
            for path in (union_ref, union_cand, legacy_ref, legacy_cand): path.mkdir()
            _write_union_side(union_ref, "reference", contract)
            _write_union_side(union_cand, "candidate", contract)
            for path in (legacy_ref, legacy_cand):
                entries = [_entry(path, render_pass, "bind", angle) for render_pass in PASSES for angle in ANGLES]
                _write_manifest(path, entries, regions=(contract.union_key,))
            union = compare_source_union_render_sets(union_ref, union_cand, _profile(), expected_contract=contract)
            legacy = compare_render_sets(legacy_ref, legacy_cand, _profile())
            self.assertEqual(union, legacy)

    def test_every_geometry_gate_and_manifest_binding_is_enforced(self) -> None:
        for field in GEOMETRY_METRICS:
            with self.subTest(metric=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); reference = root / "reference"; candidate = root / "candidate"
                reference.mkdir(); candidate.mkdir(); contract = _contract()
                _write_union_side(reference, "reference", contract); _write_union_side(candidate, "candidate", contract)
                path = candidate / "render_manifest.json"; payload = json.loads(path.read_text())
                payload["geometry"][0][field] = 2.0; path.write_text(json.dumps(payload))
                result = compare_source_union_render_sets(reference, candidate, _profile(), expected_contract=contract)
                self.assertIn(field, {item.gate for item in result.failures})
        for field in (
            "kind", "side", "contract_sha256", "target_sha256", "source_identity",
            "source_coverage_sha256", "source_sha256", "material_contract_sha256",
        ):
            with self.subTest(binding=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); reference = root / "reference"; candidate = root / "candidate"
                reference.mkdir(); candidate.mkdir(); contract = _contract()
                _write_union_side(reference, "reference", contract); _write_union_side(candidate, "candidate", contract)
                path = candidate / "render_manifest.json"; payload = json.loads(path.read_text())
                payload[field] = "wrong"; path.write_text(json.dumps(payload))
                result = compare_source_union_render_sets(reference, candidate, _profile(), expected_contract=contract)
                self.assertFalse(result.passed)

    def test_manifest_binding_extra_state_image_and_geometry_mutations_fail(self) -> None:
        mutations = (
            "configuration", "side", "image", "geometry",
            "entry-extra", "geometry-extra", "missing-materials-type",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); reference = root / "reference"; candidate = root / "candidate"
                reference.mkdir(); candidate.mkdir(); contract = _contract()
                _write_union_side(reference, "reference", contract)
                _write_union_side(candidate, "candidate", contract)
                manifest = candidate / "render_manifest.json"
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                if mutation == "configuration": payload["configuration"] = {"bodygroups": {}, "lod_index": 0}
                elif mutation == "side": payload["side"] = "reference"
                elif mutation == "image":
                    image = candidate / payload["entries"][0]["image"]
                    data = image.read_bytes(); image.write_bytes(data[:-1] + bytes((data[-1] ^ 1,)))
                elif mutation == "geometry": payload["geometry"][0][GEOMETRY_METRICS[0]] = 2.0
                elif mutation == "entry-extra": payload["entries"][0]["ignored_state_selector"] = {"bodygroup": 7}
                elif mutation == "geometry-extra": payload["geometry"][0]["ignored_contract"] = "wrong"
                else: payload["entries"][0]["missing_materials"] = "not-a-list"
                manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
                result = compare_source_union_render_sets(reference, candidate, _profile(), expected_contract=contract)
                self.assertFalse(result.passed)


if __name__ == "__main__": unittest.main()
