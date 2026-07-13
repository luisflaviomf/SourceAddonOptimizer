from __future__ import annotations

import hashlib
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
    runtime_evidence_from_proof,
    seal_runtime_proof_report,
    verify_runtime_proof_bundle,
    launch_private_process_job,
    owned_process_identity_matches,
    process_identities_by_name,
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
        self.proof_nonce = "b" * 64
        self.engine_build_sha256 = "c" * 64
        self.runtime_executable_sha256 = "d" * 64

    def _plan(self, run_id: str = "proof-12345678"):
        return build_runtime_plan(
            self.experiment,
            self.candidates,
            run_id,
            proof_nonce=self.proof_nonce,
            engine_build_sha256=self.engine_build_sha256,
            runtime_executable_sha256=self.runtime_executable_sha256,
        )

    def test_builds_unique_paths_and_binds_every_exact_candidate_hash(self) -> None:
        plan = self._plan()

        self.assertEqual(plan.manifest_sha256, self.experiment["candidate_manifest"]["sha256"])
        self.assertEqual(plan.family_ids, ("car",))
        self.assertEqual(plan.proof_nonce, self.proof_nonce)
        self.assertEqual(plan.engine_build_sha256, self.engine_build_sha256)
        self.assertEqual(plan.runtime_executable_sha256, self.runtime_executable_sha256)
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
            self._plan()
        target.write_bytes(b"vvd")

        extra = self.candidates / "car/models/car.dx80.vtx"
        extra.write_bytes(b"forbidden")
        with self.assertRaisesRegex(Dx90RuntimeError, "exact|DX80"):
            self._plan()
        extra.unlink()

        for run_id in ("short", "../escape", "UPPERCASE-1234", "proof_12345678"):
            with self.subTest(run_id=run_id), self.assertRaisesRegex(Dx90RuntimeError, "run_id"):
                self._plan(run_id)

    def test_rejects_noncanonical_family_identity_before_staging(self) -> None:
        experiment = copy.deepcopy(self.experiment)
        manifest = experiment["candidate_manifest"]
        manifest["family_ids"] = ["car/../car"]
        manifest["families"][0]["family_id"] = "car/../car"
        body = {
            key: manifest[key]
            for key in ("schema_version", "corpus_id", "family_ids", "families")
        }
        manifest["sha256"] = _sha(canonical_json_bytes(body))
        experiment["runtime_evidence"]["candidate_manifest_sha256"] = manifest["sha256"]
        with self.assertRaisesRegex(Dx90RuntimeError, "family.*identity|logical"):
            build_runtime_plan(
                experiment,
                self.candidates,
                "proof-12345678",
                proof_nonce=self.proof_nonce,
                engine_build_sha256=self.engine_build_sha256,
                runtime_executable_sha256=self.runtime_executable_sha256,
            )

    def test_lua_rehashes_game_files_rejects_both_mount_types_and_requires_all_capabilities(self) -> None:
        plan = self._plan()
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
            "proof_nonce", "engine_build_sha256", "runtime_executable_sha256",
            "lua_sha256", "maximum_dx90_console_owner_", "damage_value",
            "vectorValues",
        ):
            with self.subTest(required=required):
                self.assertIn(required, lua)
        self.assertIn(plan.manifest_sha256, lua)
        self.assertIn(plan.model_paths[0], lua)
        self.assertNotIn("models/car.mdl", lua)
        self.assertIn('game.ConsoleCommand("quit\\n")', lua)
        self.assertIn("preferredPoseNames", lua)
        self.assertNotIn('"vehicle_steer"', lua)
        self.assertNotIn('"left_door"', lua)
        self.assertIn("active:SetBodygroup(groupId, 0)", lua)
        self.assertIn("active:SetSkin(0)", lua)
        self.assertIn("local baseSequenceId = poseSequenceId", lua)

    def test_stage_is_collision_safe_exact_and_cleanup_restores_install(self) -> None:
        plan = self._plan()
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

    def test_cleanup_refuses_reparse_content_and_does_not_hide_delete_failure(self) -> None:
        plan = self._plan()
        install = self.root / "GarrysMod"
        (install / "garrysmod/lua/autorun").mkdir(parents=True)
        (install / "garrysmod/data").mkdir()
        (install / "gmod.exe").write_bytes(b"engine")

        staged = stage_runtime_probe(plan, install)
        staged_child = next(path for path in staged.model_root.rglob("*") if path.is_file())
        with mock.patch(
            "maximum_optimizer.dx90_runtime._path_is_reparse",
            side_effect=lambda path: Path(path) == staged_child,
        ):
            with self.assertRaisesRegex(Dx90RuntimeError, "reparse"):
                staged.cleanup()
        self.assertTrue(staged_child.is_file())

        with mock.patch(
            "maximum_optimizer.dx90_runtime.shutil.rmtree",
            side_effect=OSError("locked"),
        ):
            with self.assertRaisesRegex(Dx90RuntimeError, "remove|locked"):
                staged.cleanup()
        self.assertTrue(staged.model_root.is_dir())
        staged.cleanup()

    def test_capture_verifier_requires_real_paired_png_difference_for_every_family(self) -> None:
        captures = self.root / "captures"
        captures.mkdir()
        baseline = Image.new("RGB", (800, 600), (20, 30, 40))
        model = baseline.copy()
        for x in range(200, 600):
            for y in range(150, 450):
                model.putpixel((x, y), (220, 210, 200))
        baseline.save(captures / "car-baseline.png")
        model.save(captures / "car-model.png")

        metrics = verify_capture_pairs(("car",), captures)
        self.assertEqual(metrics[0]["width"], 800)
        self.assertEqual(metrics[0]["height"], 600)
        self.assertEqual(metrics[0]["changed_pixels"], 400 * 300)
        self.assertRegex(metrics[0]["baseline_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(metrics[0]["model_sha256"], r"^[0-9a-f]{64}$")

        baseline.save(captures / "car-model.png")
        with self.assertRaisesRegex(Dx90RuntimeError, "identical|difference"):
            verify_capture_pairs(("car",), captures)
        model.save(captures / "car-animation-a.png")
        animation_b = model.copy()
        for x in range(300, 340):
            for y in range(250, 270):
                animation_b.putpixel((x, y), (1, 2, 3))
        animation_b.save(captures / "car-animation-b.png")
        animation_metrics = verify_animation_capture_pairs(("car",), captures)
        self.assertGreater(animation_metrics[0]["changed_pixels"], 0)
        Image.new("RGB", (800, 600), (1, 2, 3)).save(captures / "car-animation-b.png")
        with self.assertRaisesRegex(Dx90RuntimeError, "global|fraction"):
            verify_animation_capture_pairs(("car",), captures)

        model.save(captures / "car-animation-a.png")
        tiny = model.copy()
        for x in range(4):
            tiny.putpixel((x, 0), (255, 255, 255))
        tiny.save(captures / "car-animation-b.png")
        with self.assertRaisesRegex(Dx90RuntimeError, "weak|fraction"):
            verify_animation_capture_pairs(("car",), captures)

        small_baseline = Image.new("RGB", (100, 100), (0, 0, 0))
        small_model = small_baseline.copy()
        for x in range(20, 80):
            for y in range(20, 80):
                small_model.putpixel((x, y), (255, 255, 255))
        small_baseline.save(captures / "car-baseline.png")
        small_model.save(captures / "car-model.png")
        with self.assertRaisesRegex(Dx90RuntimeError, "800|dimensions"):
            verify_capture_pairs(("car",), captures)

    def test_realm_verifier_requires_all_six_capabilities_and_exact_runtime_hashes(self) -> None:
        plan = self._plan()
        runtime_artifacts = [
            {"path": item.staged_relative, "size_bytes": item.size_bytes, "sha256": item.sha256}
            for item in plan.artifacts
        ]
        server = {
            "realm": "server", "noaddons": True, "noworkshop": True, "errors": [],
            "addon_status_api_available": True,
            "run_id": plan.run_id, "proof_nonce": plan.proof_nonce,
            "corpus_id": plan.corpus_id, "lua_sha256": "e" * 64,
            "engine_build_sha256": plan.engine_build_sha256,
            "runtime_executable_sha256": plan.runtime_executable_sha256,
            "candidate_manifest_sha256": plan.manifest_sha256,
            "artifacts": runtime_artifacts,
            "capabilities": {"dynamic_model_load": True, "physics": True, "damage": True},
            "families": [{
                "family_id": "car", "model_path": plan.model_paths[0], "valid_model": True,
                "entity_valid": True, "loaded_model": plan.model_paths[0],
                "physics_object_valid": True, "physics_mass": 100, "physics_moved": True,
                "physics_start": [0.0, 0.0, 0.0], "physics_end": [2.0, 0.0, 0.0],
                "damage_observed": True, "damage_value": 17.0,
                "animation_entity_valid": True,
                "animation_loaded_model": plan.model_paths[0],
            }],
        }
        client = {
            "realm": "client", "noaddons": True, "noworkshop": True, "errors": [],
            "addon_status_api_available": True,
            "run_id": plan.run_id, "proof_nonce": plan.proof_nonce,
            "corpus_id": plan.corpus_id, "lua_sha256": "e" * 64,
            "engine_build_sha256": plan.engine_build_sha256,
            "runtime_executable_sha256": plan.runtime_executable_sha256,
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
                "bodygroups": [{"id": 0, "count": 2, "values": [0, 1]}],
                "skin_count": 2, "skin_values": [0, 1],
                "sequence_count": 1,
                "sequences": [{"id": 0, "name": "hood", "duration": 1.0, "lastframe": 1}],
                "pose_parameter_count": 1,
                "pose_parameters": [{
                    "id": 0, "name": "hood", "minimum": 0.0, "maximum": 1.0,
                }],
                "animation_mode": "pose_parameter", "animation_pose_name": "hood",
                "animation_pose_minimum": 0.0, "animation_pose_maximum": 1.0,
                "animation_value_a": 0.0, "animation_value_b": 1.0,
                "animation_blend_sequence": 0, "animation_blend_sequence_name": "hood",
                "animation_base_sequence": 0, "animation_base_sequence_name": "hood",
                "animation_bones_a": [{
                    "id": 0, "name": "Hood", "position": [0.0, 0.0, 0.0],
                    "angles": [0.0, 0.0, 0.0],
                }],
                "animation_bones_b": [{
                    "id": 0, "name": "Hood", "position": [0.0, 0.0, 1.0],
                    "angles": [0.0, 0.0, 15.0],
                }],
            }],
        }

        capabilities = verify_realm_reports(plan, server, client, "clean console")
        self.assertEqual(set(capabilities), {
            "dynamic_model_load", "rendering", "bodygroups_skins", "animation", "physics", "damage"
        })
        client["proof_nonce"] = "f" * 64
        with self.assertRaisesRegex(Dx90RuntimeError, "binding|nonce"):
            verify_realm_reports(plan, server, client, "clean console")
        client["proof_nonce"] = plan.proof_nonce
        client["unknown"] = True
        with self.assertRaisesRegex(Dx90RuntimeError, "schema|keys"):
            verify_realm_reports(plan, server, client, "clean console")
        del client["unknown"]
        client["families"][0]["bodygroups"][0]["values"] = [0, 0]
        with self.assertRaisesRegex(Dx90RuntimeError, "bodygroup"):
            verify_realm_reports(plan, server, client, "clean console")
        client["families"][0]["bodygroups"][0]["values"] = [0, 1]
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
            "id": 0, "name": "Hood", "position": [0.0, 0.0, 1.0],
            "angles": [0.0, 0.0, 15.0],
        }]
        server["families"][0]["physics_start"] = [0.0, 0.0, 0.0]
        server["families"][0]["physics_end"] = [0.0, 0.0, 0.0]
        server["families"][0]["damage_value"] = 0.0
        with self.assertRaisesRegex(Dx90RuntimeError, "physics|damage"):
            verify_realm_reports(plan, server, client, "clean console")
        server["families"][0]["physics_end"] = [2.0, 0.0, 0.0]
        server["families"][0]["damage_value"] = 17.0
        with self.assertRaisesRegex(Dx90RuntimeError, "Lua|console"):
            verify_realm_reports(
                plan,
                server,
                client,
                "[ERROR] lua/autorun/maximum_dx90_runtime_proof-12345678.lua:1: "
                "attempt to call nil value",
            )
        with self.assertRaisesRegex(Dx90RuntimeError, "console"):
            verify_realm_reports(
                plan, server, client,
                "Error Vertex File for models/maximum_dx90_runtime/proof-12345678/car/car.dx90.vtx",
            )
        for report in (server, client):
            report["addon_status_api_available"] = False
            report["noaddons"] = "unavailable"
            report["noworkshop"] = "unavailable"
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
        plan = self._plan()
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

    def test_sealed_runtime_bundle_strictly_replays_raw_evidence_before_conversion(self) -> None:
        plan = self._plan()
        proof = self.root / "proof"
        raw = proof / "raw"
        raw.mkdir(parents=True)
        artifacts = [
            {"path": item.staged_relative, "size_bytes": item.size_bytes, "sha256": item.sha256}
            for item in plan.artifacts
        ]
        common = {
            "run_id": plan.run_id,
            "proof_nonce": plan.proof_nonce,
            "corpus_id": plan.corpus_id,
            "candidate_manifest_sha256": plan.manifest_sha256,
            "lua_sha256": _sha(render_probe_lua(plan).encode("utf-8")),
            "engine_build_sha256": plan.engine_build_sha256,
            "runtime_executable_sha256": plan.runtime_executable_sha256,
            "addon_status_api_available": True,
            "noaddons": True,
            "noworkshop": True,
            "artifacts": artifacts,
            "errors": [],
        }
        server = {
            **common,
            "realm": "server",
            "capabilities": {"dynamic_model_load": True, "physics": True, "damage": True},
            "families": [{
                "family_id": "car", "model_path": plan.model_paths[0], "valid_model": True,
                "animation_entity_valid": True, "animation_loaded_model": plan.model_paths[0],
                "entity_valid": True, "loaded_model": plan.model_paths[0],
                "physics_object_valid": True, "physics_mass": 100.0,
                "physics_start": [0.0, 0.0, 0.0], "physics_end": [2.0, 0.0, 0.0],
                "physics_moved": True, "damage_observed": True, "damage_value": 17.0,
            }],
        }
        client = {
            **common,
            "realm": "client",
            "capabilities": {
                "dynamic_model_load": True, "rendering": True,
                "bodygroups_skins": True, "animation": True,
            },
            "families": [{
                "family_id": "car", "model_path": plan.model_paths[0], "entity_valid": True,
                "loaded_model": plan.model_paths[0], "bodygroups_skins": True,
                "bodygroups": [{"id": 0, "count": 2, "values": [0, 1]}],
                "skin_count": 2, "skin_values": [0, 1], "sequence_count": 1,
                "sequences": [{"id": 0, "name": "hood", "duration": 1.0, "lastframe": 1}],
                "pose_parameter_count": 1,
                "pose_parameters": [{"id": 0, "name": "hood", "minimum": 0.0, "maximum": 1.0}],
                "animation": True, "animation_mode": "pose_parameter",
                "animation_pose_name": "hood", "animation_pose_minimum": 0.0,
                "animation_pose_maximum": 1.0, "animation_value_a": 0.0,
                "animation_value_b": 1.0, "animation_blend_sequence": 0,
                "animation_blend_sequence_name": "hood", "animation_base_sequence": 0,
                "animation_base_sequence_name": "hood", "baseline_png": "car-baseline",
                "model_png": "car-model", "animation_a_png": "car-animation-a",
                "animation_b_png": "car-animation-b",
                "animation_bones_a": [{
                    "id": 0, "name": "Hood", "position": [0.0, 0.0, 0.0],
                    "angles": [0.0, 179.0, 0.0],
                }],
                "animation_bones_b": [{
                    "id": 0, "name": "Hood", "position": [0.0, 0.0, 0.0],
                    "angles": [0.0, -169.0, 0.0],
                }],
            }],
        }
        (raw / "server.json").write_bytes(canonical_json_bytes(server))
        (raw / "client.json").write_bytes(canonical_json_bytes(client))
        (raw / "console.log").write_text(
            "maximum_dx90_console_owner_" + plan.proof_nonce, encoding="utf-8"
        )
        baseline = Image.new("RGB", (800, 600), (0, 0, 0))
        model = baseline.copy()
        for x in range(250, 550):
            for y in range(220, 380):
                model.putpixel((x, y), (255, 255, 255))
        animation = model.copy()
        for x in range(350, 390):
            for y in range(180, 200):
                animation.putpixel((x, y), (255, 255, 255))
        baseline.save(raw / "car-baseline.png")
        model.save(raw / "car-model.png")
        model.save(raw / "car-animation-a.png")
        animation.save(raw / "car-animation-b.png")
        capture_metrics = {
            "rendering": list(verify_capture_pairs(plan.family_ids, raw)),
            "animation": list(verify_animation_capture_pairs(plan.family_ids, raw)),
        }
        inventory = [
            {"path": path.name, "size_bytes": path.stat().st_size, "sha256": _sha(path.read_bytes())}
            for path in sorted(raw.iterdir())
        ]
        report = seal_runtime_proof_report({
            "schema_version": 2, "status": "proven", "reason": None,
            "run_id": plan.run_id, "proof_nonce": plan.proof_nonce,
            "target": "gmod_dynamic_runtime", "corpus_id": plan.corpus_id,
            "family_ids": list(plan.family_ids),
            "candidate_manifest_sha256": plan.manifest_sha256,
            "launcher_script_sha256": "f" * 64,
            "lua_sha256": common["lua_sha256"],
            "launcher_executable_sha256": "a" * 64,
            "runtime_executable_sha256": plan.runtime_executable_sha256,
            "launcher_executable_sha256_after": "a" * 64,
            "runtime_executable_sha256_after": plan.runtime_executable_sha256,
            "engine_build_id": "fixture-build",
            "engine_build_sha256": plan.engine_build_sha256,
            "engine_build_fingerprint": {
                "build_id": "fixture-build", "installed_depots": [
                    {"depot_id": "1", "manifest_id": "2", "size_bytes": 3}
                ], "beta_keys": ["fixture"], "sha256": plan.engine_build_sha256,
            },
            "appmanifest_sha256_before": "1" * 64,
            "appmanifest_sha256_after": "2" * 64,
            "stable_build_identity_unchanged": True,
            "launch_arguments": ["-noaddons", "-noworkshop"],
            "hidden_window": True, "timeout_seconds": 180, "process_exit_code": 0,
            "runtime_processes_exited": True, "external_completion_termination": True,
            "timed_out": False, "candidate_artifacts": [
                {"family_id": item.family_id, "staged_path": item.staged_relative,
                 "size_bytes": item.size_bytes, "sha256": item.sha256}
                for item in plan.artifacts
            ],
            "observed_processes": [{
                "pid": 123, "parent_pid": 1, "create_time": 1.0,
                "exe_path": "C:/fixture/bin/win64/gmod.exe",
                "exe_sha256": plan.runtime_executable_sha256,
            }],
            "server_report_sha256": _sha((raw / "server.json").read_bytes()),
            "client_report_sha256": _sha((raw / "client.json").read_bytes()),
            "console_sha256": _sha((raw / "console.log").read_bytes()),
            "capture_metrics": capture_metrics,
            "capabilities": [
                "animation", "bodygroups_skins", "damage", "dynamic_model_load",
                "physics", "rendering",
            ],
            "raw_inventory": inventory, "cleanup_verified": True,
        })
        (proof / "runtime-proof.json").write_bytes(canonical_json_bytes(report))
        proof_sha = _sha((proof / "runtime-proof.json").read_bytes())
        verified = verify_runtime_proof_bundle(
            proof, plan, expected_proof_sha256=proof_sha,
            expected_launcher_script_sha256="f" * 64,
        )
        self.assertEqual(verified["evidence_sha256"], report["evidence_sha256"])
        evidence = runtime_evidence_from_proof(
            proof, plan, expected_proof_sha256=proof_sha,
            expected_launcher_script_sha256="f" * 64,
        )
        self.assertTrue(evidence.runtime_proven)
        (raw / "car-animation-b.png").write_bytes(b"tampered")
        with self.assertRaisesRegex(Dx90RuntimeError, "inventory|hash"):
            verify_runtime_proof_bundle(
                proof, plan, expected_proof_sha256=proof_sha,
                expected_launcher_script_sha256="f" * 64,
            )

    @unittest.skipUnless(sys.platform == "win32", "Windows private job contract")
    def test_private_process_job_owns_and_terminates_only_its_exact_tree(self) -> None:
        job = launch_private_process_job(
            [sys.executable, "-c", "import time; time.sleep(30)"], self.root
        )
        self.addCleanup(job.close)
        identities = job.process_identities()
        self.assertTrue(any(item["pid"] == job.root_pid for item in identities))
        self.assertTrue(all(item["exe_sha256"] == _sha(Path(item["exe_path"]).read_bytes()) for item in identities))
        job.terminate()
        self.assertTrue(job.wait_empty(5.0))
        self.assertEqual(job.active_pids(), ())

    def test_breakaway_identity_requires_nonce_time_path_and_hash(self) -> None:
        marker = "maximum_dx90_process_owner_" + self.proof_nonce
        identity = {
            "pid": 10, "parent_pid": 1, "create_time": 100.0,
            "exe_path": "C:/GMod/bin/win64/gmod.exe", "exe_sha256": "d" * 64,
            "cmdline": ["gmod.exe", "+echo", marker],
        }
        allowed = {"c:/gmod/bin/win64/gmod.exe": "d" * 64}
        self.assertTrue(owned_process_identity_matches(
            identity, owner_marker=marker, launched_after=99.0,
            allowed_executables=allowed,
        ))
        for field, value in (
            ("create_time", 98.0), ("exe_sha256", "e" * 64),
            ("cmdline", ["gmod.exe"]),
        ):
            tampered = dict(identity)
            tampered[field] = value
            self.assertFalse(owned_process_identity_matches(
                tampered, owner_marker=marker, launched_after=99.0,
                allowed_executables=allowed,
            ))
        child = dict(identity)
        child.update({"pid": 11, "parent_pid": 10, "cmdline": ["gmod.exe", "--type=renderer"]})
        self.assertTrue(owned_process_identity_matches(
            child, owner_marker=marker, launched_after=99.0,
            allowed_executables=allowed, owned_parent_pids={10},
        ))

    def test_named_process_inventory_fails_closed_when_target_identity_is_unreadable(self) -> None:
        import psutil

        class UnreadableGmod:
            info = {
                "pid": 10, "ppid": 1, "name": "gmod.exe",
                "exe": sys.executable, "create_time": 100.0,
            }

            def cmdline(self):
                raise psutil.AccessDenied(pid=10)

        with mock.patch("psutil.process_iter", return_value=[UnreadableGmod()]):
            with self.assertRaisesRegex(Dx90RuntimeError, "inspect.*gmod|identity"):
                process_identities_by_name("gmod.exe")


if __name__ == "__main__":
    unittest.main()
