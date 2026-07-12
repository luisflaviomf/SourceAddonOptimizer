from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from maximum_optimizer.smoothing_evidence import (
    SmoothingEvidenceError,
    build_artifact_comparison,
    parse_artifact_comparison,
)


class SmoothingEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control = (
            {"path": "models/car.mdl", "size_bytes": 10, "sha256": "1" * 64},
            {"path": "models/car.vvd", "size_bytes": 20, "sha256": "2" * 64},
            {"path": "models/car.dx80.vtx", "size_bytes": 30, "sha256": "3" * 64},
            {"path": "models/car.dx90.vtx", "size_bytes": 40, "sha256": "4" * 64},
            {"path": "models/car.phy", "size_bytes": 50, "sha256": "5" * 64},
        )
        self.payload = build_artifact_comparison(self.control, self.control)

    def test_bundle_roundtrips_and_recomputes_digest_and_equality(self) -> None:
        parsed = parse_artifact_comparison(self.payload)
        self.assertTrue(parsed["bundle_equal"])
        self.assertEqual(len(parsed["artifacts"]), 5)

    def test_committed_wheel_bundle_is_portable_and_recomputes(self) -> None:
        evidence = json.loads(
            Path("benchmarks/lvs_models/smoothing_fixed_v1.json").read_text(encoding="utf-8")
        )
        wheel = next(record for record in evidence["records"] if record["family_id"] == "pontiac_transam_wheel")
        parsed = parse_artifact_comparison(wheel["artifact_comparison"])
        self.assertTrue(parsed["bundle_equal"])

    def test_bundle_rejects_tampered_path_size_hash_digest_duplicate_and_missing(self) -> None:
        mutations = []
        for field, value in (
            ("path", "models/other.mdl"), ("size_bytes", 11), ("sha256", "f" * 64)
        ):
            item = copy.deepcopy(self.payload)
            item["artifacts"][0]["candidate"][field] = value
            mutations.append(item)
        digest = copy.deepcopy(self.payload)
        digest["bundle_sha256"] = "0" * 64
        mutations.append(digest)
        duplicate = copy.deepcopy(self.payload)
        duplicate["artifacts"][1] = copy.deepcopy(duplicate["artifacts"][0])
        mutations.append(duplicate)
        missing = copy.deepcopy(self.payload)
        missing["artifacts"].pop()
        mutations.append(missing)
        for payload in mutations:
            with self.subTest(payload=payload):
                with self.assertRaises(SmoothingEvidenceError):
                    parse_artifact_comparison(payload)


if __name__ == "__main__":
    unittest.main()
