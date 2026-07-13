from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from maximum_optimizer.benchmarking import canonical_json_bytes
from maximum_optimizer.dx90_runtime import (
    Dx90RuntimeError,
    build_runtime_plan,
    render_probe_lua,
    stage_runtime_probe,
    verify_capture_pairs,
    verify_animation_capture_pairs,
    verify_realm_reports,
    parse_gmod_tasklist_pids,
    console_has_runtime_lua_error,
    steam_build_fingerprint,
    runtime_outputs_ready,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Dx90RuntimePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.candidates = self.root / "candidates"
        payloads = {
            "models/car.mdl": b"mdl",
            "models/car.vvd": b"vvd",
            "models/car.dx90.vtx": b"dx90",
            "models/car.phy": b"phy",
        }
        kept = []
        family_root = self.candidates / "car"
        for relative, payload in payloads.items():
            path = family_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            kept.append({"path": relative, "size_bytes": len(payload), "sha256": _sha(payload)})
        omitted = {"path": "models/car.dx80.vtx", "size_bytes": 4, "sha256": _sha(b"dx80")}
        family = {
            "family_id": "car",
            "compiled_stem": "models/car",
            "kept": kept,
            "omitted_dx80": omitted,
            "source_manifest_sha256": "a" * 64,
            "source_bytes": sum(len(value) for value in payloads.values()) + 4,
            "candidate_bytes": sum(len(value) for value in payloads.values()),
            "saved_dx80_bytes": 4,
        }
        manifest_body = {
            "schema_version": 1,
            "corpus_id": "fixture",
            "family_ids": ["car"],
            "families": [family],
        }
        manifest = {
            **manifest_body,
            "sha256": _sha(canonical_json_bytes(manifest_body)),
        }
        self.experiment = {
            "candidate_manifest": manifest,
            "runtime_evidence": {
                "status": "pending",
                "candidate_manifest_sha256": manifest["sha256"],
            },
        }

    def test_builds_unique_paths_and_binds_every_exact_candidate_hash(self) -> None:
        plan = build_runtime_plan(self.experiment, self.candidates, "proof-12345678")

        self.assertEqual(plan.manifest_sha256, self.experiment["candidate_manifest"]["sha256"])
        self.assertEqual(plan.family_ids, ("car",))
        self.assertEqual(plan.model_paths, ("models/maximum_dx90_runtime/proof-12345678/car/car.mdl",))
        self.assertEqual(len(plan.artifacts), 4)
        self.assertFalse(any(item.staged_relative.endswith(".dx80.vtx") for item in plan.artifacts))
        for item in plan.artifacts:
            self.assertEqual(item.sha256, _sha(item.source.read_bytes()))
            self.assertTrue(item.staged_relative.startswith("models/maximum_dx90_runtime/proof-12345678/"))

    def test_rejects_hash_drift_extra_sidecar_dx80_and_unsafe_run_id(self) -> None:
        target = self.candidates / "car/models/car.vvd"
        target.write_bytes(b"drift")
        with self.assertRaisesRegex(Dx90RuntimeError, "hash|size"):
            build_runtime_plan(self.experiment, self.candidates, "proof-12345678")
        target.write_bytes(b"vvd")

        extra = self.candidates / "car/models/car.dx80.vtx"
        extra.write_bytes(b"forbidden")
        with self.assertRaisesRegex(Dx90RuntimeError, "exact|DX80"):
            build_runtime_plan(self.experiment, self.candidates, "proof-12345678")
        extra.unlink()

        for run_id in ("short", "../escape", "UPPERCASE-1234", "proof_12345678"):
            with self.subTest(run_id=run_id), self.assertRaisesRegex(Dx90RuntimeError, "run_id"):
                build_runtime_plan(self.experiment, self.candidates, run_id)

    def test_lua_rehashes_game_files_rejects_both_mount_types_and_requires_all_capabilities(self) -> None:
        plan = build_runtime_plan(self.experiment, self.candidates, "proof-12345678")
        lua = render_probe_lua(plan)

        for required in (
            "GetAddonStatus", "noaddons", "noworkshop", "util.SHA256", "file.Read",
            ".dx80.vtx", "dynamic_model_load", "rendering", "bodygroups_skins",
            "animation", "physics", "damage", "render.Capture", "EntityTakeDamage",
            "GetNumPoseParameters", "SetPoseParameter", "animation_a_png", "animation_b_png",
            "GetRenderTarget", "PushRenderTarget", "render.Clear", "cam.Start3D",
            "pose_parameters", "sequences", "LookupSequence(name)", "GetBoneMatrix",
            "animation_bones_a", "animation_bones_b", "active:ResetSequenceInfo()",
            'ents.Create("prop_dynamic_override")', "GetNW2String", "ents.GetAll()",
            "player.GetAll()[1]",
            "entity:WorldToLocal(position)",
            "entity:WorldToLocalAngles(angles)",
            "poseNetName", 'animated:SetPoseParameter("hood"', "net.ReadBool()",
            "if animated.InvalidateBoneCache then",
        ):
            with self.subTest(required=required):
                self.assertIn(required, lua)
        self.assertIn(plan.manifest_sha256, lua)
        self.assertIn(plan.model_paths[0], lua)
        self.assertNotIn("models/car.mdl", lua)
        self.assertIn('game.ConsoleCommand("quit\\n")', lua)
        self.assertIn("preferredPoseNames", lua)
        self.assertLess(lua.index('"hood"'), lua.index('"vehicle_steer"'))
        self.assertLess(lua.index('"left_door"'), lua.index('"vehicle_steer"'))
        self.assertIn("active:SetBodygroup(groupId, 0)", lua)
        self.assertIn("active:SetSkin(0)", lua)
        self.assertIn("local baseSequenceId = poseSequenceId", lua)

    def test_stage_is_collision_safe_exact_and_cleanup_restores_install(self) -> None:
        plan = build_runtime_plan(self.experiment, self.candidates, "proof-12345678")
        install = self.root / "GarrysMod"
        (install / "garrysmod/lua/autorun").mkdir(parents=True)
        (install / "garrysmod/data").mkdir()
        (install / "gmod.exe").write_bytes(b"engine")

        staged = stage_runtime_probe(plan, install)
        self.assertTrue(staged.lua_path.is_file())
        self.assertEqual(staged.lua_sha256, _sha(staged.lua_path.read_bytes()))
        copied = {
            path.relative_to(install / "garrysmod").as_posix()
            for path in staged.model_root.rglob("*") if path.is_file()
        }
        self.assertEqual(copied, {item.staged_relative for item in plan.artifacts})
        self.assertFalse(any(path.endswith(".dx80.vtx") for path in copied))

        with self.assertRaisesRegex(Dx90RuntimeError, "exists"):
            stage_runtime_probe(plan, install)
        staged.cleanup()
        self.assertFalse(staged.model_root.exists())
        self.assertFalse(staged.lua_path.exists())
        self.assertFalse((install / "garrysmod/models").exists())
        self.assertTrue((install / "gmod.exe").is_file())

    def test_capture_verifier_requires_real_paired_png_difference_for_every_family(self) -> None:
        captures = self.root / "captures"
        captures.mkdir()
        baseline = Image.new("RGB", (32, 24), (20, 30, 40))
        model = baseline.copy()
        for x in range(8, 24):
            for y in range(6, 18):
                model.putpixel((x, y), (220, 210, 200))
        baseline.save(captures / "car-baseline.png")
        model.save(captures / "car-model.png")

        metrics = verify_capture_pairs(("car",), captures)
        self.assertEqual(metrics[0]["width"], 32)
        self.assertEqual(metrics[0]["height"], 24)
        self.assertEqual(metrics[0]["changed_pixels"], 16 * 12)
        self.assertRegex(metrics[0]["baseline_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(metrics[0]["model_sha256"], r"^[0-9a-f]{64}$")

        baseline.save(captures / "car-model.png")
        with self.assertRaisesRegex(Dx90RuntimeError, "identical|difference"):
            verify_capture_pairs(("car",), captures)
        model.save(captures / "car-animation-a.png")
        animation_b = model.copy()
        for x in range(10, 14):
            for y in range(8, 12):
                animation_b.putpixel((x, y), (1, 2, 3))
        animation_b.save(captures / "car-animation-b.png")
        animation_metrics = verify_animation_capture_pairs(("car",), captures)
        self.assertGreater(animation_metrics[0]["changed_pixels"], 0)
        Image.new("RGB", (32, 24), (1, 2, 3)).save(captures / "car-animation-b.png")
        with self.assertRaisesRegex(Dx90RuntimeError, "global|fraction"):
            verify_animation_capture_pairs(("car",), captures)

    def test_realm_verifier_requires_all_six_capabilities_and_exact_runtime_hashes(self) -> None:
        plan = build_runtime_plan(self.experiment, self.candidates, "proof-12345678")
        runtime_artifacts = [
            {"path": item.staged_relative, "size_bytes": item.size_bytes, "sha256": item.sha256}
            for item in plan.artifacts
        ]
        server = {
            "realm": "server", "noaddons": True, "noworkshop": True, "errors": [],
            "addon_status_api_available": True,
            "candidate_manifest_sha256": plan.manifest_sha256,
            "artifacts": runtime_artifacts,
            "capabilities": {"dynamic_model_load": True, "physics": True, "damage": True},
            "families": [{
                "family_id": "car", "model_path": plan.model_paths[0], "valid_model": True,
                "entity_valid": True, "loaded_model": plan.model_paths[0],
                "physics_object_valid": True, "physics_mass": 100, "physics_moved": True,
                "damage_observed": True,
            }],
        }
        client = {
            "realm": "client", "noaddons": True, "noworkshop": True, "errors": [],
            "addon_status_api_available": True,
            "candidate_manifest_sha256": plan.manifest_sha256,
            "artifacts": runtime_artifacts,
            "capabilities": {
                "dynamic_model_load": True, "rendering": True,
                "bodygroups_skins": True, "animation": True,
            },
            "families": [{
                "family_id": "car", "model_path": plan.model_paths[0], "entity_valid": True,
                "loaded_model": plan.model_paths[0], "bodygroups_skins": True,
                "animation": True, "baseline_png": "car-baseline", "model_png": "car-model",
                "animation_a_png": "car-animation-a", "animation_b_png": "car-animation-b",
                "animation_bones_a": [{
                    "id": 0, "name": "hood", "position": [0.0, 0.0, 0.0],
                    "angles": [0.0, 0.0, 0.0],
                }],
                "animation_bones_b": [{
                    "id": 0, "name": "hood", "position": [0.0, 0.0, 1.0],
                    "angles": [0.0, 0.0, 15.0],
                }],
            }],
        }

        capabilities = verify_realm_reports(plan, server, client, "clean console")
        self.assertEqual(set(capabilities), {
            "dynamic_model_load", "rendering", "bodygroups_skins", "animation", "physics", "damage"
        })
        client["families"][0]["animation"] = False
        with self.assertRaisesRegex(Dx90RuntimeError, "animation"):
            verify_realm_reports(plan, server, client, "clean console")
        client["families"][0]["animation"] = True
        client["families"][0]["animation_bones_b"] = list(
            client["families"][0]["animation_bones_a"]
        )
        with self.assertRaisesRegex(Dx90RuntimeError, "bone"):
            verify_realm_reports(plan, server, client, "clean console")
        client["families"][0]["animation_bones_b"] = [{
            "id": 0, "name": "hood", "position": [0.0, 0.0, 1.0],
            "angles": [0.0, 0.0, 15.0],
        }]
        with self.assertRaisesRegex(Dx90RuntimeError, "console"):
            verify_realm_reports(
                plan, server, client,
                "Error Vertex File for models/maximum_dx90_runtime/proof-12345678/car/car.dx90.vtx",
            )
        for report in (server, client):
            report["addon_status_api_available"] = False
            report["noaddons"] = None
            report["noworkshop"] = None
        capabilities = verify_realm_reports(
            plan,
            server,
            client,
            "Game is ran with -noaddons, not loading legacy/folder addons!\n"
            "Mounted 0 of 0 workshop addons!",
        )
        self.assertIn("dynamic_model_load", capabilities)

    def test_tasklist_parser_tracks_real_child_gmod_process(self) -> None:
        output = (
            '"gmod.exe","106712","Console","1","1,234,567 K"\n'
            '"steam.exe","73308","Console","1","200,000 K"\n'
            '"gmod.exe","106900","Console","1","1,111,111 K"\n'
        )
        self.assertEqual(parse_gmod_tasklist_pids(output), (106712, 106900))
        self.assertEqual(parse_gmod_tasklist_pids("INFO: No tasks are running"), ())

    def test_console_error_detector_only_flags_generated_runtime_lua_errors(self) -> None:
        self.assertTrue(console_has_runtime_lua_error(
            "[ERROR] lua/autorun/maximum_dx90_runtime_proof-12345678.lua:54: "
            "attempt to call method 'InvalidateBoneCache' (a nil value)"
        ))
        self.assertFalse(console_has_runtime_lua_error(
            "Failed to load models/m_pst.mdl!\nMounted 0 of 0 workshop addons!"
        ))

    def test_steam_build_fingerprint_ignores_last_played_but_seals_depots_and_branch(self) -> None:
        before = '''"AppState" { "buildid" "24073281" "LastPlayed" "1"
        "InstalledDepots" { "4001" { "manifest" "111" "size" "222" }
        "4002" { "manifest" "333" "size" "444" } }
        "UserConfig" { "BetaKey" "x86-64" } }'''
        after = before.replace('"LastPlayed" "1"', '"LastPlayed" "999"')
        changed_build = after.replace('"manifest" "333"', '"manifest" "334"')

        self.assertEqual(steam_build_fingerprint(before), steam_build_fingerprint(after))
        self.assertNotEqual(steam_build_fingerprint(before), steam_build_fingerprint(changed_build))
        self.assertRegex(steam_build_fingerprint(before)["sha256"], r"^[0-9a-f]{64}$")

    def test_runtime_completion_requires_both_reports_and_all_render_and_animation_pairs(self) -> None:
        plan = build_runtime_plan(self.experiment, self.candidates, "proof-12345678")
        data = self.root / "runtime-data"
        data.mkdir()
        expected = (
            "server.json", "client.json", "car-baseline.png", "car-model.png",
            "car-animation-a.png", "car-animation-b.png",
        )
        for name in expected[:-1]:
            (data / name).write_bytes(b"x")
        self.assertFalse(runtime_outputs_ready(plan, data))
        (data / expected[-1]).write_bytes(b"x")
        self.assertTrue(runtime_outputs_ready(plan, data))


if __name__ == "__main__":
    unittest.main()
