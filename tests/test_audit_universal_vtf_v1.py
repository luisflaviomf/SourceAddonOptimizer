from __future__ import annotations

import importlib.util
import hashlib
import json
import struct
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_universal_vtf_v1.py"
FIXTURES = (
    ROOT
    / "GmodAddonCompressor-master"
    / "GmodAddonCompressor"
    / "Resources"
    / "universal-vtf-v1-fixtures.json"
)


def _load_auditor():
    spec = importlib.util.spec_from_file_location("audit_universal_vtf_v1", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tree_hash(tmp_path: Path) -> None:
    audit = _load_auditor()
    (tmp_path / "a.txt").write_bytes(b"A")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(bytes([0, 255]))

    inventory = audit.tree_inventory(tmp_path)

    assert inventory["file_count"] == 2
    assert inventory["total_bytes"] == 3
    assert inventory["tree_sha256"] == (
        "75e21d8d75cff49a5579a92b2f881167a5cb57ee7fa08ba2602ee84458b424eb"
    )


def test_missing_manifest_file_fails(tmp_path: Path) -> None:
    audit = _load_auditor()
    manifest = {
        "schema": "universal-vtf-manifest-v1",
        "rows": [{"relativePath": "materials/missing.vtf"}],
    }

    with pytest.raises(audit.AuditError, match="missing"):
        audit.audit_corpus(manifest, tmp_path)


def test_frozen_fixture_parity_is_independently_clean() -> None:
    audit = _load_auditor()

    receipt = audit.audit_fixtures(FIXTURES)

    assert receipt["status"] == "CLEAN"
    assert receipt["schema"] == "universal-vtf-v1-fixture-audit-v1"
    assert receipt["maximum_error"] <= 1e-12
    assert receipt["checks"] >= 40


def test_bc3_transcode_is_opaque_and_rgb_exact() -> None:
    audit = _load_auditor()
    # Alpha is deliberately non-opaque: the V1 bridge drops alpha but must copy
    # the authored BC3 colour palette/index stream without changing decoded RGB.
    alpha = bytes.fromhex("0080e4e4e4e4e4e4")
    for colour in (
        bytes.fromhex("00f8e007e4e4e4e4"),  # c0 > c1
        bytes.fromhex("e00700f8e4e4e4e4"),  # c0 < c1; swap/remap
        bytes.fromhex("1f001f00e4e4e4e4"),  # equal endpoints
    ):
        bc3 = alpha + colour
        bc1 = audit.transcode_bc3_block(bc3)
        assert len(bc1) == 8
        assert int.from_bytes(bc1[:2], "little") > int.from_bytes(bc1[2:4], "little")
        assert audit.decode_bc3_rgb(bc3) == audit.decode_bc1_rgb(bc1)


def test_frozen_policy_and_toolchain_fingerprints_match_producer() -> None:
    audit = _load_auditor()
    policy, toolchain = audit._policy_fingerprint(
        "9bf65c2b404365c23836b6eb2ff206a0fed6d8c217a71792406b775c8fb6dad1"
    )

    assert policy == "de1fd1acd3434d34d6365aec224d56e85a4a319ff935ab708ffa93f87be14266"
    assert toolchain == "415bd8ae0217aea0f85037eb5004fd3b03246229cfbff43ed28d12e086f0a4c6"


def test_vtf_parser_rejects_truncated_resource_dictionary(tmp_path: Path) -> None:
    audit = _load_auditor()
    path = tmp_path / "bad.vtf"
    header = bytearray(80)
    header[:4] = b"VTF\0"
    header[4:8] = (7).to_bytes(4, "little", signed=True)
    header[8:12] = (3).to_bytes(4, "little", signed=True)
    header[12:16] = (80).to_bytes(4, "little", signed=True)
    header[16:18] = (4).to_bytes(2, "little")
    header[18:20] = (4).to_bytes(2, "little")
    header[24:26] = (1).to_bytes(2, "little")
    header[52:56] = (13).to_bytes(4, "little", signed=True)
    header[56] = 1
    header[57:61] = (-1).to_bytes(4, "little", signed=True)
    header[63:65] = (1).to_bytes(2, "little")
    header[68:72] = (1).to_bytes(4, "little", signed=True)
    path.write_bytes(header + bytes(8))

    with pytest.raises(audit.AuditError, match="resource"):
        audit.parse_vtf(path)


def _dxt1_vtf(width: int, height: int, mip_count: int) -> bytes:
    header = bytearray(80)
    header[:4] = b"VTF\0"
    struct.pack_into("<iii", header, 4, 7, 2, len(header))
    struct.pack_into("<HH", header, 16, width, height)
    struct.pack_into("<H", header, 24, 1)
    struct.pack_into("<i", header, 52, 13)
    header[56] = mip_count
    struct.pack_into("<i", header, 57, -1)
    struct.pack_into("<H", header, 63, 1)
    block = struct.pack("<HHI", 0xF800, 0x07E0, 0)
    payload = bytearray()
    for level in reversed(range(mip_count)):
        w, h = max(1, width >> level), max(1, height >> level)
        payload.extend(block * (max(1, (w + 3) // 4) * max(1, (h + 3) // 4)))
    return bytes(header + payload)


def test_small_physical_corpus_passes_end_to_end(tmp_path: Path) -> None:
    audit = _load_auditor()
    raw, safe, candidate = tmp_path / "raw", tmp_path / "safe", tmp_path / "candidate"
    for root in (raw, safe, candidate):
        (root / "materials").mkdir(parents=True)
        (root / "models").mkdir()
        (root / "models" / "same.mdl").write_bytes(b"model")
    relative = "materials/a.vtf"
    raw_vtf = _dxt1_vtf(8, 8, 4)
    selected_vtf = _dxt1_vtf(4, 4, 3)
    (raw / relative).write_bytes(raw_vtf)
    (safe / relative).write_bytes(raw_vtf)
    (candidate / relative).write_bytes(selected_vtf)
    raw_tree, safe_tree, final_tree = (
        audit.tree_inventory(raw), audit.tree_inventory(safe), audit.tree_inventory(candidate)
    )
    raw_layout = audit.parse_vtf(raw / relative)
    raw_sha, safe_sha, final_sha = map(
        lambda value: hashlib.sha256(value).hexdigest(), (raw_vtf, raw_vtf, selected_vtf)
    )
    mip_receipts = []
    for level in range(1, 4):
        info = raw_layout["levels"][level]
        start = info["offset"] - raw_layout["high_offset"]
        payload = raw_layout["high_payload"][start : start + info["byte_count"]]
        mip_receipts.append({
            "level": level, "width": info["width"], "height": info["height"],
            "depth": info["depth"], "planeCount": 1, "payloadBytes": len(payload),
            "payloadSha256": hashlib.sha256(payload).hexdigest(),
            "alpha": audit._alpha_receipt(payload, 13),
        })
    bridge_document = {
        "schema": "bc3-raw-to-approved-v1", "priorManifestSha256": "1" * 64,
        "rawTreeSha256": raw_tree["tree_sha256"], "rawTreeBytes": raw_tree["total_bytes"],
        "rawTreeFileCount": raw_tree["file_count"],
        "approvedSafeTreeSha256": safe_tree["tree_sha256"],
        "approvedSafeTreeBytes": safe_tree["total_bytes"],
        "approvedSafeTreeFileCount": safe_tree["file_count"],
        "physicalOpaqueBc3Count": 0, "rawDxt1Count": 1,
        "semanticExceptionBc3Count": 0, "equivalentPriorSafeColorChainCount": 0,
        "rows": [{
            "relativePath": relative, "rawBytes": len(raw_vtf), "rawSha256": raw_sha,
            "priorSafeSourceBytes": len(raw_vtf), "priorSafeSourceSha256": safe_sha,
            "approvedSafeBytes": len(raw_vtf), "approvedSafeSha256": safe_sha,
            "consumerReceiptSha256": "2" * 64, "rawFormat": 13,
            "eligibilityMode": "rawDxt1", "approvedOutputPath": relative,
            "priorSafeColorBlocksEquivalent": False, "rawMips": mip_receipts,
        }],
    }
    bridge_path = tmp_path / "bridge.json"
    bridge_path.write_text(json.dumps(bridge_document, separators=(",", ":")), encoding="utf-8")
    bridge_sha = hashlib.sha256(bridge_path.read_bytes()).hexdigest()
    policy_sha, toolchain_sha = audit._policy_fingerprint(bridge_sha)
    row = {
        "relativePath": relative, "family": "a", "semanticClasses": ["diffuse"],
        "consumerProofSha256": "3" * 64, "rawWidth": 8, "rawHeight": 8,
        "rawDepth": 1, "rawFrames": 1, "rawFaces": 1, "rawMipCount": 4,
        "rawFormat": 13, "finalWidth": 4, "finalHeight": 4, "finalDepth": 1,
        "finalMipCount": 3, "finalFormat": 13, "authored": True, "generated": False,
        "selectedLevel": 1, "conservativeLevel": 1, "balancedLevel": 1,
        "aggressiveLevel": 1, "route": "numericLinear", "profileAttempts": {},
        "routeAttempts": [], "worstGate": None, "rawBytes": len(raw_vtf),
        "rawSha256": raw_sha, "safeBytes": len(raw_vtf), "safeSha256": safe_sha,
        "spatialBytes": len(selected_vtf), "spatialSha256": final_sha,
        "finalBytes": len(selected_vtf), "finalSha256": final_sha,
        "mandatoryFloorApplied": True, "byteFloor": False, "blocker": None,
        "bcEligibilityMode": "rawDxt1", "bcReceiptSha256": bridge_sha,
        "lowResolutionImageRegenerated": False, "resourceCount": 0,
    }
    def tree(authority, source):
        return {"authority": authority, "fileCount": source["file_count"],
                "bytes": source["total_bytes"], "treeSha256": source["tree_sha256"]}
    manifest = {
        "schema": "universal-vtf-manifest-v1", "profile": "balanced",
        "policyFingerprint": policy_sha, "fixtureSha256": audit.FIXTURE_SHA256,
        "toolchainSha256": toolchain_sha, "bcBridgeSha256": bridge_sha,
        "runtimeCapsSha256": None, "rawTree": tree("raw", raw_tree),
        "safeTree": tree("safe", safe_tree), "finalTree": tree("final", final_tree),
        "rows": [row],
    }
    manifest["aggregate"] = audit._aggregate_expected(manifest)

    receipt = audit.audit_corpus(
        manifest, candidate, raw=raw, safe=safe, bridge=bridge_path
    )

    assert receipt["status"] == "CLEAN"
    assert receipt["rows"] == 1
    assert receipt["bc_receipts"] == 1


def test_fixture_document_is_canonical_json() -> None:
    raw = FIXTURES.read_bytes()
    document = json.loads(raw)
    assert document["schema"] == "universal-vtf-v1-fixtures"
    assert document["version"] == 1
