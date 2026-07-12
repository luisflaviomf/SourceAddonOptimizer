from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / ".superpowers/lvs-task8-calibration"
RECORDS = (
    ("dodge_charger", "focused-recovery-v1/dodge_charger_exact", "grille_a", "recovered-exact", "exact source restored grille contour and tooth detail"),
    ("dodge_charger", "focused-recovery-v1/dodge_charger_exact", "headlight", "recovered-exact", "exact source restored circular headlight boundaries"),
    ("toyota_supra", "focused-recovery-v1/toyota_supra", "body", "recovered-r025", "r025 donor removed the visible triangular body shading artifact"),
    ("toyota_supra", "focused-regions-v1/toyota_supra_r015", "body13_model0", "preserved-r015", "accepted r015 region was retained by the minimal recovery"),
    ("nissan_skyline_gtr32", "focused-recovery-v1/nissan_skyline_exact", "headlights_a", "recovered-exact", "exact source restored curved headlight outlines"),
    ("nissan_skyline_gtr32", "focused-recovery-v1/nissan_skyline_exact", "fenders_a", "recovered-exact", "exact source restored fender cutouts and thin detail"),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(payload: dict) -> str:
    canonical = dict(payload)
    canonical.pop("evidence_sha256", None)
    encoded = (json.dumps(canonical, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return hashlib.sha256(encoded).hexdigest()


def build() -> dict:
    records = []
    for family, directory, region, status, reason in RECORDS:
        root = RESEARCH / directory / region
        configuration = json.loads((root / "configuration.json").read_text(encoding="utf-8"))
        pair = configuration["source_pairs"][0]
        records.append({
            "family_id": family,
            "region": region,
            "status": status,
            "reason": reason,
            "reference_sha256": pair["reference_sha256"],
            "candidate_sha256": pair["candidate_sha256"],
            "configuration_sha256": digest(root / "configuration.json"),
            "region_manifest_sha256": digest(root / "region_manifest.json"),
            "reference_render_manifest_sha256": digest(root / "renders/original/render_manifest.json"),
            "candidate_render_manifest_sha256": digest(root / "renders/optimized/render_manifest.json"),
        })
    payload = {
        "schema_version": 1,
        "strategy": "focused-regional-recovery-evidence-v1",
        "quality_status": "manually-reviewed-recovery-supplement",
        "records": records,
    }
    payload["evidence_sha256"] = canonical_hash(payload)
    return payload


def main() -> int:
    out = ROOT / "benchmarks/lvs_models/focused_recovery_evidence_v1.json"
    out.write_text(json.dumps(build(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
