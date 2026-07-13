from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from maximum_optimizer.profile_readiness_audit import (
    canonical_profile_readiness_audit_hash,
    parse_profile_readiness_audit,
)


AUDIT_PATH = (
    Path(__file__).resolve().parents[2]
    / "benchmarks/lvs_models/profile_readiness_audit_v1.json"
)


def _payload() -> dict:
    return json.loads(AUDIT_PATH.read_text(encoding="utf-8"))


class ProfileReadinessAuditTests(unittest.TestCase):
    def test_committed_audit_is_sealed_non_authorizing_and_uses_labels(self):
        payload = _payload()
        parsed = parse_profile_readiness_audit(payload)

        self.assertEqual(
            payload["evidence_sha256"],
            canonical_profile_readiness_audit_hash(payload),
        )
        self.assertEqual("profile-readiness-insufficient", parsed["status"])
        self.assertIs(parsed["calibrated"], False)
        self.assertIs(parsed["authorizing"], False)
        self.assertIs(parsed["scope"]["holdout_consulted"], False)
        self.assertEqual(
            "missing-focused-round-rigid-evidence",
            parsed["decision"]["reason_code"],
        )
        self.assertTrue(all(
            item["label"].startswith("research://")
            for item in parsed["source_artifacts"]
        ))
        self.assertEqual(
            0,
            parsed["classes"]["round-rigid-v1"]["focused"]["coverage"]
            ["accepted_sample_count"],
        )

    def test_resealed_calibrated_or_authorizing_status_is_rejected(self):
        mutations = (
            lambda item: item.__setitem__("status", "calibrated"),
            lambda item: item.__setitem__("calibrated", True),
            lambda item: item.__setitem__("authorizing", True),
            lambda item: item["decision"].__setitem__("authorizing", True),
        )
        for mutate in mutations:
            changed = copy.deepcopy(_payload())
            mutate(changed)
            changed["evidence_sha256"] = canonical_profile_readiness_audit_hash(changed)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(
                ValueError, "non-authorizing"
            ):
                parse_profile_readiness_audit(changed)

    def test_resealed_absolute_external_path_is_rejected(self):
        changed = copy.deepcopy(_payload())
        changed["source_artifacts"][0]["label"] = "C:/Temp/private.json"
        changed["evidence_sha256"] = canonical_profile_readiness_audit_hash(changed)
        with self.assertRaisesRegex(ValueError, "research label"):
            parse_profile_readiness_audit(changed)

    def test_resealed_distribution_forgery_is_rejected(self):
        changed = copy.deepcopy(_payload())
        distribution = (
            changed["classes"]["round-rigid-v1"]["whole"]
            ["accepted_distribution"]["silhouette_iou"]
        )
        distribution["max"] += 0.0001
        changed["evidence_sha256"] = canonical_profile_readiness_audit_hash(changed)
        with self.assertRaisesRegex(ValueError, "distribution"):
            parse_profile_readiness_audit(changed)


if __name__ == "__main__":
    unittest.main()
