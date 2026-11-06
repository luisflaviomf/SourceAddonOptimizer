from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from maximum_optimizer.companion_contract import read_bundle, write_bundle


FIXTURES = Path(__file__).with_name("fixtures") / "valid"
CONTRACTS = Path(__file__).resolve().parents[2] / "companion" / "contracts"
RUN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CREATED = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
PRODUCER = {"name": "Source Addon Optimizer Companion", "version": "1.0.0"}
ENVIRONMENT = {
    "game_build": "2026.08",
    "map": "gm_flatgrass",
    "gamemode": "sandbox",
    "realm": "singleplayer",
    "session_seconds": 30.0,
    "mounted_addons": [],
}


def profile_payload() -> dict[str, object]:
    fixture = json.loads((FIXTURES / "sao-runtime-profile-v1.json").read_text(encoding="utf-8"))
    return fixture["payload"]


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_write_bundle_finishes_atomically_and_round_trips(tmp_path: Path) -> None:
    root = tmp_path / "profile"

    written = write_bundle(
        root,
        schema="sao-runtime-profile-v1",
        run_id=RUN_ID,
        created_utc=CREATED,
        producer=PRODUCER,
        environment=ENVIRONMENT,
        payloads=[profile_payload()],
    )

    assert written.root == root
    assert written.run_id == RUN_ID
    assert sorted(path.name for path in root.iterdir()) == [
        "chunk-0001.json",
        "complete.ready.json",
        "run-index.json",
    ]
    assert not tuple(tmp_path.glob("*.partial"))
    assert read_bundle(root, expected_schema="sao-runtime-profile-v1").payloads == (
        profile_payload(),
    )


def test_duplicate_run_destination_is_preserved_byte_for_byte(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    write_bundle(
        root,
        schema="sao-runtime-profile-v1",
        run_id=RUN_ID,
        created_utc=CREATED,
        producer=PRODUCER,
        environment=ENVIRONMENT,
        payloads=[profile_payload()],
    )
    before = tree_hashes(root)

    with pytest.raises(ValueError, match="already exists"):
        write_bundle(
            root,
            schema="sao-runtime-profile-v1",
            run_id=RUN_ID,
            created_utc=CREATED,
            producer=PRODUCER,
            environment=ENVIRONMENT,
            payloads=[profile_payload()],
        )

    assert tree_hashes(root) == before


def test_writer_removes_its_partial_directory_after_validation_failure(tmp_path: Path) -> None:
    payload = profile_payload()
    payload["command"] = "quit"

    with pytest.raises(ValueError, match="unknown fields"):
        write_bundle(
            tmp_path / "profile",
            schema="sao-runtime-profile-v1",
            run_id=RUN_ID,
            created_utc=CREATED,
            producer=PRODUCER,
            environment=ENVIRONMENT,
            payloads=[payload],
        )

    assert not (tmp_path / "profile").exists()
    assert not tuple(tmp_path.glob("*.partial"))


def test_contract_documents_are_closed_and_limits_match_wire_constants() -> None:
    limits = json.loads((CONTRACTS / "contract-limits-v1.json").read_text(encoding="utf-8"))
    assert limits["max_chunk_bytes"] == 2_000_000
    assert limits["max_profile_bytes"] == 20_000_000
    assert limits["max_json_depth"] == 12
    assert limits["max_total_keys"] == 100_000
    assert limits["max_models"] == 4_096
    assert limits["max_materials"] == 16_384
    assert limits["max_plan_models"] == 250
    assert limits["max_screenshots"] == 1_000
    assert limits["max_ordinary_views"] == 4
    for schema in (
        "sao-runtime-profile-v1",
        "sao-validation-plan-v1",
        "sao-structural-result-v1",
        "sao-visual-result-v1",
    ):
        document = json.loads((CONTRACTS / f"{schema}.schema.json").read_text(encoding="utf-8"))
        assert document["additionalProperties"] is False
        assert document["properties"]["schema"]["const"] == schema
        if schema == "sao-runtime-profile-v1":
            assert set(document["$defs"]) == {
                "contract",
                "producer",
                "environment",
                "chunk",
                "bodygroup",
                "submaterial",
                "model",
                "material",
                "lua_error",
                "performance",
                "truncation",
                "evidence",
            }


def test_every_schema_object_is_closed_and_every_array_has_an_item_contract() -> None:
    def inspect(node: object, location: str) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, location
        if node.get("type") == "array":
            assert "items" in node, location
        for key, value in node.items():
            if isinstance(value, dict):
                inspect(value, f"{location}/{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    inspect(item, f"{location}/{key}/{index}")

    for path in CONTRACTS.glob("*.schema.json"):
        inspect(json.loads(path.read_text(encoding="utf-8")), path.name)
