from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from maximum_optimizer.companion_contract import read_bundle

from .bundle_helpers import write_bundle


FIXTURES = Path(__file__).with_name("fixtures") / "valid"


@pytest.mark.parametrize(
    "schema,run_id",
    (
        ("sao-runtime-profile-v1", "11111111-1111-4111-8111-111111111111"),
        ("sao-validation-plan-v1", "22222222-2222-4222-8222-222222222222"),
        ("sao-structural-result-v1", "33333333-3333-4333-8333-333333333333"),
        ("sao-visual-result-v1", "44444444-4444-4444-8444-444444444444"),
    ),
)
def test_read_bundle_accepts_each_valid_closed_contract(tmp_path: Path, schema: str, run_id: str) -> None:
    chunk = json.loads((FIXTURES / f"{schema}.json").read_text(encoding="utf-8"))
    root = write_bundle(tmp_path / schema, [chunk])

    bundle = read_bundle(root, expected_schema=schema)

    assert bundle.run_id == UUID(run_id)
    assert bundle.schema == schema
    assert bundle.contract_major == 1
    assert bundle.contract_minor == 0
    assert bundle.payloads == (chunk["payload"],)
