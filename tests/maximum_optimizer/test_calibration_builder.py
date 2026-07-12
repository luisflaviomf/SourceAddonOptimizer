from __future__ import annotations

import math
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.lvs_models.build_calibration_corpus_v1 import (
    _apply_regional_replacements,
    _canonical_payload_hash,
    _validate_monaco_composite,
    build_byte_comparison,
    distribution,
)
from benchmarks.lvs_models.run_visual_states import (
    _write_json,
    short_texture_cache_root,
    state_region_manifest_path,
)
from maximum_optimizer.reporting import canonical_json


class CalibrationBuilderTests(unittest.TestCase):
    def test_regional_replacement_recomputes_real_donor_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "output/body_opt.smd"
            target.parent.mkdir()
            target.write_bytes(b"reviewed donor")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            regional = {"replacements": [{
                "target": "output\\body_opt.smd", "donor_sha256": digest,
            }]}
            outputs = {"body.smd": "0" * 64}
            _apply_regional_replacements(root, regional, outputs)
            self.assertEqual(outputs["body.smd"], digest)

            target.write_bytes(b"changed after review")
            with self.assertRaisesRegex(ValueError, "declared donor"):
                _apply_regional_replacements(root, regional, outputs)

    def test_monaco_composite_recomputes_inner_seals_before_binding_lane(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "monaco.json"
            artifacts = [
                {"path": f"compiled/model.{index}", "bytes": 1, "sha256": str(index) * 64}
                for index in range(5)
            ]
            artifacts[-1]["bytes"] = 11592076
            compiled = {
                "total_bytes": 11592080,
                "artifacts": [
                    {
                        "path": f"models/model.{index}",
                        "size_bytes": artifact["bytes"],
                        "sha256": artifact["sha256"],
                    }
                    for index, artifact in enumerate(artifacts)
                ],
            }
            origins = [
                {
                    "source": f"part{index}.smd",
                    "source_sha256": "a" * 64,
                    "output_sha256": "b" * 64,
                }
                for index in range(44)
            ]
            candidate_metrics = {
                "files": [{"fallback_reason": None} for _ in range(44)],
                "triangles_before": 206754,
                "triangles_after": 95128,
            }
            payload = {
                "schema": "maximum-accepted-composite-v1",
                "candidate_metrics": candidate_metrics,
                "candidate_metrics_sha256": _canonical_payload_hash(candidate_metrics),
                "composition": {
                    "file_origins": origins,
                    "stable_direct_visuals": 8,
                    "inherited_r040_visuals": 36,
                    "visual_fallback_count": 0,
                },
                "qc": {
                    "source_mesh_files": 44,
                    "optimized_mesh_files": 44,
                    "differing_fields": ["mesh_files"],
                },
                "compiled": {"total_bytes": 11592080, "artifacts": artifacts},
            }
            payload["seal_sha256"] = _canonical_payload_hash(payload)
            path.write_text(json.dumps(payload), encoding="utf-8")
            lane = {
                "compiled": compiled,
                "configurations": [{
                    "source_pairs": [{
                        "source_identity": "part0.smd",
                        "candidate_sha256": "b" * 64,
                    }],
                }],
            }
            self.assertEqual(
                _validate_monaco_composite(path, lane)["canonical_payload_sha256"],
                payload["seal_sha256"],
            )
            payload["candidate_metrics"]["triangles_after"] += 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "seal"):
                _validate_monaco_composite(path, lane)

    def test_byte_comparison_preserves_both_typed_denominators(self) -> None:
        self.assertEqual(build_byte_comparison(600, 1200, 1000), {
            "candidate_bytes": 600,
            "versus_shipped_original": {
                "denominator_kind": "shipped-original-compiled-family-v1",
                "denominator_bytes": 1200,
                "saved_bytes": 600,
                "reduction_fraction": 0.5,
            },
            "versus_roundtrip_control": {
                "denominator_kind": "strict-roundtrip-control-compiled-family-v1",
                "denominator_bytes": 1000,
                "saved_bytes": 400,
                "reduction_fraction": 0.4,
            },
        })

    def test_byte_comparison_rejects_nonpositive_or_larger_candidate(self) -> None:
        for values in ((0, 1200, 1000), (600, 0, 1000), (1201, 1200, 1300)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                build_byte_comparison(*values)

    def test_distribution_is_deterministic_and_uses_interpolated_p95(self) -> None:
        self.assertEqual(distribution([4, 0, 2, 1, 3]), {
            "count": 5,
            "min": 0.0,
            "median": 2.0,
            "p95": 3.8,
            "max": 4.0,
        })

    def test_distribution_rejects_empty_negative_or_nonfinite_values(self) -> None:
        for values in ([], [-1], [math.inf], [math.nan]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                distribution(values)

    def test_visual_runner_cache_is_short_and_deterministic(self) -> None:
        first = short_texture_cache_root(Path("C:/very/long/run/path"))
        second = short_texture_cache_root(Path("C:/very/long/run/path"))
        self.assertEqual(first, second)
        self.assertEqual(first.parent.name, "maximum-vtf-cache")
        self.assertEqual(len(first.name), 16)

    def test_visual_runner_region_manifest_stays_with_short_state_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state_root = Path(raw) / "001-bodygroup-front-bumper"
            selected = state_region_manifest_path(state_root)
            payload = {"schema": 1, "regions": ["body.smd"]}
            _write_json(selected, payload)

            self.assertEqual(selected, state_root / "region_manifest.json")
            self.assertLess(len(str(selected.resolve())), 240)
            self.assertEqual(json.loads(selected.read_text(encoding="utf-8")), payload)
            self.assertEqual(
                hashlib.sha256(selected.read_bytes()).hexdigest(),
                hashlib.sha256((canonical_json(payload) + "\n").encode("utf-8")).hexdigest(),
            )

    def test_visual_runner_region_manifest_rejects_worst_case_long_output(self) -> None:
        too_long = Path("C:/") / ("long-segment/" * 20) / "state"
        with self.assertRaisesRegex(ValueError, "MAX_PATH"):
            state_region_manifest_path(too_long)


if __name__ == "__main__":
    unittest.main()
