from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from uuid import UUID

from maximum_optimizer.companion_contract import MAX_INDEX_BYTES, write_bundle
from maximum_optimizer.qc_graph import QcOccurrence
from maximum_optimizer.qc_runtime import parse_qc_runtime_bindings
from maximum_optimizer.runtime_profile import (
    RuntimeEvidenceMap,
    importance_for_evidence,
    load_runtime_evidence,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
CREATED = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
PRODUCER = {"name": "Source Addon Optimizer Companion", "version": "1.0.0"}
ENVIRONMENT = {
    "game_build": "2026.08",
    "map": "gm_flatgrass",
    "gamemode": "sandbox",
    "realm": "singleplayer",
    "session_seconds": 120.0,
    "mounted_addons": [{"workshop_id": "3027256228", "title": "LVS Cars Pack"}],
}


def payload(
    *,
    evidence: str = "focused",
    skins: list[int] | None = None,
    bodygroups: list[dict[str, object]] | None = None,
    materials: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "models": [
            {
                "id": "model-1",
                "path": "MODELS\\LVS\\TEST\\RUNTIME_VEHICLE.MDL",
                "evidence": evidence,
                "skins": skins or [],
                "bodygroups": bodygroups or [],
                "submaterials": [],
                "materials": [],
            }
        ],
        "materials": materials or [],
        "lua_errors": [],
        "performance": {
            "samples": 10,
            "average_ms": 0.1,
            "p95_ms": 0.2,
            "max_ms": 0.3,
            "queue_high_water": 2,
        },
        "truncation": {"models": 0, "materials": 0, "lua_errors": 0, "bytes": 0},
    }


def write_profile(root: Path, profile_payload: dict[str, object], *, run_id: UUID | None = None) -> Path:
    return write_bundle(
        root,
        schema="sao-runtime-profile-v1",
        run_id=run_id or UUID("11111111-1111-4111-8111-111111111111"),
        created_utc=CREATED,
        producer=PRODUCER,
        environment=ENVIRONMENT,
        payloads=[profile_payload],
    ).root


def occurrence(bindings, source: str) -> QcOccurrence:
    return next(item for item in bindings.by_occurrence if item.source_path == PurePosixPath(source))


class RuntimeProfileTests(unittest.TestCase):
    def test_fixed_evidence_tier_mapping(self) -> None:
        self.assertEqual(
            {name: importance_for_evidence(name) for name in (
                "registered", "instantiated", "visible", "repeated_visible", "focused"
            )},
            {
                "registered": 0.10,
                "instantiated": 0.25,
                "visible": 0.50,
                "repeated_visible": 0.75,
                "focused": 1.00,
            },
        )
        with self.assertRaisesRegex(ValueError, "evidence"):
            importance_for_evidence("unknown")

    def test_qc_bindings_cover_model_body_model_bodygroup_choices_skins_and_cdmaterials(self) -> None:
        bindings = parse_qc_runtime_bindings(FIXTURES)
        body = bindings.by_occurrence[occurrence(bindings, "body.smd")]
        model = bindings.by_occurrence[occurrence(bindings, "interior.smd")]
        stock = bindings.by_occurrence[occurrence(bindings, "wheel_stock.smd")]
        sport = bindings.by_occurrence[occurrence(bindings, "wheel_sport.smd")]

        self.assertEqual(body.model_path, "models/lvs/test/runtime_vehicle.mdl")
        self.assertEqual(model.model_path, body.model_path)
        self.assertIsNone(body.bodygroup)
        self.assertEqual(stock.bodygroup, ("wheels", 0))
        self.assertEqual(sport.bodygroup, ("wheels", 2))
        self.assertEqual(body.skin_rows, (("paint", "tire"), ("paint_red", "tire_sport")))
        self.assertEqual(body.material_directories, (PurePosixPath("models/LVS/Test"),))

    def test_model_only_focus_is_moderated_to_point_four_for_every_bound_region(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            profile = write_profile(root / "profile", payload())
            result = load_runtime_evidence(profile, FIXTURES)

        self.assertIsNone(result.diagnostic)
        evidence = result.evidence
        self.assertIsInstance(evidence, RuntimeEvidenceMap)
        assert evidence is not None
        bindings = parse_qc_runtime_bindings(FIXTURES)
        for source, material in (("body.smd", "paint"), ("interior.smd", "glass"), ("wheel_stock.smd", "tire")):
            importance = evidence.for_region(occurrence(bindings, source), material)
            self.assertEqual(importance.model_baseline, 0.40)
            self.assertEqual(importance.observed_state_importance, 0.0)
            self.assertEqual(importance.region_material_importance, 0.0)
            self.assertEqual(importance.runtime_importance, 0.40)
            self.assertTrue(any("model" in reason for reason in importance.reasons))

    def test_bodygroup_skin_and_direct_material_evidence_remain_regional(self) -> None:
        profile_payload = payload(
            skins=[1],
            bodygroups=[{"name": "wheels", "value": 2, "evidence": "focused"}],
            materials=[
                {
                    "id": "material-red",
                    "path": "materials/models/lvs/test/paint_red.vmt",
                    "evidence": "visible",
                },
                {
                    "id": "material-glass",
                    "path": "materials/models/lvs/test/glass.vmt",
                    "evidence": "repeated_visible",
                },
            ],
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = load_runtime_evidence(write_profile(root / "profile", profile_payload), FIXTURES)

        assert result.evidence is not None
        evidence = result.evidence
        bindings = parse_qc_runtime_bindings(FIXTURES)
        body = evidence.for_region(occurrence(bindings, "body.smd"), "paint")
        interior = evidence.for_region(occurrence(bindings, "interior.smd"), "glass")
        stock = evidence.for_region(occurrence(bindings, "wheel_stock.smd"), "rubber")
        sport = evidence.for_region(occurrence(bindings, "wheel_sport.smd"), "rubber")

        self.assertEqual(body.observed_state_importance, 1.0)
        self.assertEqual(body.region_material_importance, 0.50)
        self.assertEqual(body.runtime_importance, 1.0)
        self.assertEqual(interior.observed_state_importance, 0.0)
        self.assertEqual(interior.region_material_importance, 0.75)
        self.assertEqual(interior.runtime_importance, 0.75)
        self.assertEqual(stock.observed_state_importance, 0.0)
        self.assertEqual(stock.runtime_importance, 0.40)
        self.assertEqual(sport.observed_state_importance, 1.0)
        self.assertEqual(sport.runtime_importance, 1.0)
        self.assertTrue(any("skin 1" in reason for reason in body.reasons))
        self.assertTrue(any("bodygroup wheels=2" in reason for reason in sport.reasons))
        self.assertTrue(any("paint_red.vmt" in reason for reason in body.reasons))

    def test_maximum_tier_and_set_union_are_deterministic_under_reordered_chunks(self) -> None:
        first = payload(
            evidence="registered",
            skins=[0],
            bodygroups=[{"name": "wheels", "value": 2, "evidence": "visible"}],
        )
        second = payload(
            evidence="focused",
            skins=[1],
            bodygroups=[{"name": "wheels", "value": 2, "evidence": "focused"}],
        )
        first["models"][0]["id"] = "model-a"  # type: ignore[index]
        second["models"][0]["id"] = "model-b"  # type: ignore[index]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            left = write_bundle(
                root / "left",
                schema="sao-runtime-profile-v1",
                run_id=UUID("22222222-2222-4222-8222-222222222222"),
                created_utc=CREATED,
                producer=PRODUCER,
                environment=ENVIRONMENT,
                payloads=[first, second],
            ).root
            right = write_bundle(
                root / "right",
                schema="sao-runtime-profile-v1",
                run_id=UUID("33333333-3333-4333-8333-333333333333"),
                created_utc=CREATED,
                producer=PRODUCER,
                environment=ENVIRONMENT,
                payloads=[second, first],
            ).root
            left_map = load_runtime_evidence(left, FIXTURES).evidence
            right_map = load_runtime_evidence(right, FIXTURES).evidence

        assert left_map is not None and right_map is not None
        self.assertEqual(set(left_map.skin_importance), {
            ("models/lvs/test/runtime_vehicle.mdl", 0),
            ("models/lvs/test/runtime_vehicle.mdl", 1),
        })
        bindings = parse_qc_runtime_bindings(FIXTURES)
        region = occurrence(bindings, "wheel_sport.smd")
        self.assertEqual(left_map.for_region(region, "rubber"), right_map.for_region(region, "rubber"))
        self.assertEqual(left_map.for_region(region, "rubber").runtime_importance, 1.0)

    def test_missing_binding_returns_all_zeroes_and_malformed_qc_adds_no_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            qc_root = root / "qc"
            qc_root.mkdir()
            (qc_root / "ambiguous.qc").write_text(
                '$modelname "models/one.mdl"\n'
                '$modelname "models/two.mdl"\n'
                '$bodygroup "wheels"\n{\n studio "wheel.smd"\n}\n',
                encoding="utf-8",
            )
            profile = write_profile(root / "profile", payload())
            result = load_runtime_evidence(profile, qc_root)
            assert result.evidence is not None
            binding_occurrence = next(iter(result.evidence.bindings.by_occurrence))

        assert result.evidence is not None
        self.assertEqual(result.evidence.for_region(binding_occurrence, "tire").runtime_importance, 0.0)
        missing = QcOccurrence(PurePosixPath("missing.qc"), "$body", 1, PurePosixPath("missing.smd"))
        importance = result.evidence.for_region(missing, "paint")
        self.assertEqual(
            (
                importance.model_baseline,
                importance.observed_state_importance,
                importance.region_material_importance,
                importance.runtime_importance,
                importance.reasons,
            ),
            (0.0, 0.0, 0.0, 0.0, ()),
        )

    def test_malformed_bodygroup_contributes_no_state_but_keeps_model_baseline(self) -> None:
        profile_payload = payload(
            bodygroups=[{"name": "wheels", "value": 0, "evidence": "focused"}],
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            qc_root = root / "qc"
            qc_root.mkdir()
            (qc_root / "malformed.qc").write_text(
                '$modelname "models/lvs/test/runtime_vehicle.mdl"\n'
                '$bodygroup "wheels"\n'
                'studio "wheel.smd"\n',
                encoding="utf-8",
            )
            result = load_runtime_evidence(write_profile(root / "profile", profile_payload), qc_root)
            assert result.evidence is not None
            region = next(iter(result.evidence.bindings.by_occurrence))
            importance = result.evidence.for_region(region, "rubber")

        self.assertEqual(importance.model_baseline, 0.40)
        self.assertEqual(importance.observed_state_importance, 0.0)
        self.assertEqual(importance.runtime_importance, 0.40)

    def test_non_runtime_schema_returns_typed_diagnostic(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "companion_contract"
            / "fixtures"
            / "valid"
            / "sao-validation-plan-v1.json"
        )
        envelope = json.loads(fixture_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = write_bundle(
                root / "plan",
                schema="sao-validation-plan-v1",
                run_id=UUID(envelope["run_id"]),
                created_utc=datetime.fromisoformat(envelope["created_utc"].replace("Z", "+00:00")),
                producer=envelope["producer"],
                environment=envelope["environment"],
                payloads=[envelope["payload"]],
            ).root
            result = load_runtime_evidence(plan, FIXTURES)

        self.assertIsNone(result.evidence)
        self.assertIsNotNone(result.diagnostic)
        assert result.diagnostic is not None
        self.assertIn("unexpected payload schema", result.diagnostic.message)

    def test_invalid_or_oversized_bundle_returns_typed_diagnostic_without_partial_map(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "invalid"
            root.mkdir()
            (root / "complete.ready.json").write_text("{}", encoding="utf-8")
            (root / "run-index.json").write_bytes(b" " * (MAX_INDEX_BYTES + 1))

            result = load_runtime_evidence(root, FIXTURES)

        self.assertIsNone(result.evidence)
        self.assertIsNotNone(result.diagnostic)
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, "invalid_runtime_profile")
        self.assertIn("byte limit", result.diagnostic.message)

    def test_profile_older_than_180_days_is_rejected_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            stale = write_bundle(
                root / "stale",
                schema="sao-runtime-profile-v1",
                run_id=UUID("44444444-4444-4444-8444-444444444444"),
                created_utc=datetime(2025, 1, 1, tzinfo=timezone.utc),
                producer=PRODUCER,
                environment=ENVIRONMENT,
                payloads=[payload()],
            ).root
            result = load_runtime_evidence(
                stale,
                FIXTURES,
                now=datetime(2026, 8, 16, tzinfo=timezone.utc),
            )

        self.assertIsNone(result.evidence)
        self.assertIsNotNone(result.diagnostic)
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, "stale_runtime_profile")


if __name__ == "__main__":
    unittest.main()
