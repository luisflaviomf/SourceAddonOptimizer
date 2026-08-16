from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from maximum_optimizer.companion_contract import inspect_bundle, normalize_virtual_path, read_bundle

from .bundle_helpers import json_bytes, write_bundle


FIXTURES = Path(__file__).with_name("fixtures")


def profile_chunk() -> dict[str, object]:
    return json.loads(
        (FIXTURES / "valid" / "sao-runtime-profile-v1.json").read_text(encoding="utf-8")
    )


def update_index_for_chunk(root: Path, raw: bytes) -> None:
    index = json.loads((root / "run-index.json").read_text(encoding="utf-8"))
    index["chunks"][0]["bytes"] = len(raw)
    index["chunks"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
    index["total_bytes"] = len(raw)
    index_raw = json_bytes(index)
    (root / "run-index.json").write_bytes(index_raw)
    ready = json.loads((root / "complete.ready.json").read_text(encoding="utf-8"))
    ready["index_sha256"] = hashlib.sha256(index_raw).hexdigest()
    (root / "complete.ready.json").write_bytes(json_bytes(ready))


def test_incomplete_bundle_is_rejected_as_incomplete(tmp_path: Path) -> None:
    root = write_bundle(tmp_path / "profile", [profile_chunk()])
    (root / "complete.ready.json").unlink()

    with pytest.raises(ValueError, match="incomplete"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


@pytest.mark.parametrize("tamper", ("bytes", "declared-size", "declared-hash"))
def test_chunk_corruption_is_rejected_before_payload_use(tmp_path: Path, tamper: str) -> None:
    root = write_bundle(tmp_path / tamper, [profile_chunk()])
    index_path = root / "run-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if tamper == "bytes":
        (root / "chunk-0001.json").write_bytes((root / "chunk-0001.json").read_bytes() + b" ")
    elif tamper == "declared-size":
        index["chunks"][0]["bytes"] += 1
        index_path.write_bytes(json_bytes(index))
    else:
        index["chunks"][0]["sha256"] = "0" * 64
        index_path.write_bytes(json_bytes(index))
    if tamper != "bytes":
        ready = json.loads((root / "complete.ready.json").read_text(encoding="utf-8"))
        ready["index_sha256"] = hashlib.sha256(index_path.read_bytes()).hexdigest()
        (root / "complete.ready.json").write_bytes(json_bytes(ready))

    with pytest.raises(ValueError, match="(size|hash)"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


@pytest.mark.parametrize("location", ("index", "envelope", "payload"))
def test_unknown_fields_are_rejected_in_every_closed_structure(tmp_path: Path, location: str) -> None:
    chunk = profile_chunk()
    if location == "envelope":
        chunk["executable"] = "cmd.exe"
    elif location == "payload":
        chunk["payload"]["url"] = "https://example.invalid"
    root = write_bundle(tmp_path / location, [chunk])
    if location == "index":
        index = json.loads((root / "run-index.json").read_text(encoding="utf-8"))
        index["arguments"] = ["--unsafe"]
        index_raw = json_bytes(index)
        (root / "run-index.json").write_bytes(index_raw)
        ready = json.loads((root / "complete.ready.json").read_text(encoding="utf-8"))
        ready["index_sha256"] = hashlib.sha256(index_raw).hexdigest()
        (root / "complete.ready.json").write_bytes(json_bytes(ready))

    with pytest.raises(ValueError, match="unknown fields"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


def test_newer_minor_exposes_only_safe_metadata(tmp_path: Path) -> None:
    chunk = profile_chunk()
    chunk["contract"]["minor"] = 1
    root = write_bundle(tmp_path / "newer", [chunk])

    inspection = inspect_bundle(root)

    assert inspection.schema == "sao-runtime-profile-v1"
    assert inspection.contract_major == 1
    assert inspection.contract_minor == 1
    assert inspection.compatible is False
    assert inspection.complete is True
    with pytest.raises(ValueError, match="unsupported contract"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


def test_different_major_is_incompatible(tmp_path: Path) -> None:
    chunk = profile_chunk()
    chunk["contract"]["major"] = 2
    root = write_bundle(tmp_path / "major", [chunk])

    with pytest.raises(ValueError, match="unsupported contract"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    root = write_bundle(tmp_path / "duplicate-key", [profile_chunk()])
    raw = (root / "chunk-0001.json").read_bytes().replace(
        b'{"chunk":', b'{"schema":"sao-runtime-profile-v1","chunk":', 1
    )
    (root / "chunk-0001.json").write_bytes(raw)
    update_index_for_chunk(root, raw)

    with pytest.raises(ValueError, match="duplicate JSON key"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


def test_non_finite_numbers_are_rejected(tmp_path: Path) -> None:
    root = write_bundle(tmp_path / "nan", [profile_chunk()])
    raw = (root / "chunk-0001.json").read_bytes().replace(b'"average_ms":0.15', b'"average_ms":NaN')
    (root / "chunk-0001.json").write_bytes(raw)
    update_index_for_chunk(root, raw)

    with pytest.raises(ValueError, match="finite"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


def test_excessive_json_depth_is_rejected(tmp_path: Path) -> None:
    chunk = profile_chunk()
    nested: object = "leaf"
    for _ in range(13):
        nested = {"nested": nested}
    chunk["payload"]["nested"] = nested
    root = write_bundle(tmp_path / "deep", [chunk])

    with pytest.raises(ValueError, match="depth"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


def test_duplicate_item_ids_are_rejected_across_chunks(tmp_path: Path) -> None:
    first = profile_chunk()
    second = profile_chunk()
    first["chunk"] = {"sequence": 1, "total": 2}
    second["chunk"] = {"sequence": 2, "total": 2}
    second["payload"]["models"][0]["path"] = "models/lvs/other.mdl"
    root = write_bundle(tmp_path / "duplicates", [first, second])

    with pytest.raises(ValueError, match="duplicate id"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


def test_oversized_chunk_is_rejected_before_json_parsing(tmp_path: Path) -> None:
    root = write_bundle(tmp_path / "oversized", [profile_chunk()])
    raw = b"{" + b"x" * 2_000_000
    (root / "chunk-0001.json").write_bytes(raw)
    update_index_for_chunk(root, raw)

    with pytest.raises(ValueError, match="chunk exceeds"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


def test_future_timestamp_is_rejected(tmp_path: Path) -> None:
    chunk = profile_chunk()
    chunk["created_utc"] = "2999-01-01T00:00:00Z"
    root = write_bundle(tmp_path / "future", [chunk])

    with pytest.raises(ValueError, match="future"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


def test_missing_and_repeated_chunk_filenames_are_rejected(tmp_path: Path) -> None:
    root = write_bundle(tmp_path / "missing", [profile_chunk()])
    (root / "chunk-0001.json").unlink()
    with pytest.raises(ValueError, match="missing"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")

    root = write_bundle(tmp_path / "repeated", [profile_chunk()])
    index = json.loads((root / "run-index.json").read_text(encoding="utf-8"))
    index["chunks"].append(dict(index["chunks"][0], sequence=2))
    index["chunk_count"] = 2
    index["total_bytes"] *= 2
    index_raw = json_bytes(index)
    (root / "run-index.json").write_bytes(index_raw)
    ready = json.loads((root / "complete.ready.json").read_text(encoding="utf-8"))
    ready["index_sha256"] = hashlib.sha256(index_raw).hexdigest()
    (root / "complete.ready.json").write_bytes(json_bytes(ready))
    with pytest.raises(ValueError, match="chunk filename"):
        read_bundle(root, expected_schema="sao-runtime-profile-v1")


def test_virtual_paths_are_ascii_normalized_and_allowlisted() -> None:
    cases = json.loads((FIXTURES / "invalid" / "cases.json").read_text(encoding="utf-8"))
    for source, expected in cases["safe_paths"].items():
        assert normalize_virtual_path(source) == expected
    for source in cases["unsafe_paths"]:
        with pytest.raises(ValueError, match="virtual path"):
            normalize_virtual_path(source)


def test_unknown_validation_operation_is_rejected(tmp_path: Path) -> None:
    chunk = json.loads(
        (FIXTURES / "valid" / "sao-validation-plan-v1.json").read_text(encoding="utf-8")
    )
    chunk["payload"]["tests"][0]["kind"] = "execute_lua"
    root = write_bundle(tmp_path / "operation", [chunk])

    with pytest.raises(ValueError, match="test kind"):
        read_bundle(root, expected_schema="sao-validation-plan-v1")
