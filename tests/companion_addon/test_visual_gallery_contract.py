from __future__ import annotations

import json
from pathlib import Path

import jsonschema


REPOSITORY = Path(__file__).resolve().parents[2]
ADDON = REPOSITORY / "companion" / "sourceaddonoptimizer_companion"
LUA = ADDON / "lua" / "sourceaddonoptimizer"


def read(relative: str) -> str:
    return (LUA / relative).read_text(encoding="utf-8")


def test_dashboard_has_one_admin_only_entrypoint_and_five_clear_panels() -> None:
    source = read("client/dashboard.lua")
    assert 'concommand.Add("sao_companion"' in source
    assert "spawnmenu.AddToolMenuOption(" in source
    assert "function SAO.Dashboard.CanUseLocalAdmin()" in source
    assert "LocalPlayer()" in source and ":IsSuperAdmin()" in source
    for panel in ("Overview", "Profile", "Validate", "Results", "Help"):
        assert f'AddSheet("{panel}"' in source
    for label in ("PASS", "WARNING", "FAILED", "INCOMPLETE"):
        assert label in source
    assert "Start Visual Validation" in source
    assert "Cancel Validation" in source


def test_model_gallery_uses_fixed_lighting_camera_fit_and_declared_state_only() -> None:
    source = read("client/model_gallery.lua")
    assert 'vgui.Create("DModelPanel"' in source
    assert "SetAmbientLight(" in source
    assert "SetDirectionalLight(" in source
    assert "GetRenderBounds()" in source
    assert "SetCamPos(" in source
    assert "SetLookAt(" in source
    assert "SetFOV(" in source
    assert "local model_paint = panel.Paint" in source
    assert "model_paint(self, width, height)" in source
    assert "test.skins" in source
    assert "test.bodygroups" in source
    for view in ("front", "side", "rear", "isometric", "closeup"):
        assert f"{view} =" in source
    for forbidden in ("SetSubMaterial(arguments", "RunConsoleCommand", "ConCommand"):
        assert forbidden not in source


def test_capture_is_render_safe_fixed_local_png_and_never_networked() -> None:
    source = read("client/screenshot_capture.lua")
    assert 'hook.Add("PostRenderVGUI"' in source
    assert "render.Capture(" in source
    assert 'format = "png"' in source
    assert "panel:LocalToScreen(" in source
    assert "SAO.Constants.DataFolders.screenshots" in source
    assert "SAO.Constants.SCREENSHOT_RETRIES" in source
    assert "job.wait_frames <= 120" in source
    assert "file.Open(" in source and '"DATA"' in source
    assert "SAO.Contract.Sha256(raw)" in source
    assert "net." not in source
    assert "http." not in source


def test_visual_queue_enforces_caps_resume_cancel_and_map_disconnect_hooks() -> None:
    source = read("client/asset_validator.lua")
    constants = read("shared/constants.lua")
    for token in ("MAX_PLAN_MODELS", "MAX_SCREENSHOTS", "MAX_ORDINARY_VIEWS", "SCREENSHOT_RETRIES"):
        assert token in constants + source
    assert "SAO.Validation.ReadPlan(" in source
    assert "save_progress(" in source
    assert "visual-progress-" in source
    assert "finish_current_model(" in source
    assert 'hook.Add("ShutDown"' in source
    assert 'hook.Add("InitPostEntity"' in source
    assert "function SAO.AssetValidator.Cancel(" in source
    assert "INCOMPLETE" in source
    assert "WARNING" in source
    assert "SetActiveTab(validation_tab)" in read("client/dashboard.lua")


def test_gallery_size_depends_on_screen_not_windows_dpi_or_absolute_pixels_only() -> None:
    dashboard = read("client/dashboard.lua")
    gallery = read("client/model_gallery.lua")
    assert "ScrW()" in dashboard + gallery
    assert "ScrH()" in dashboard + gallery
    assert "math.Clamp(" in dashboard + gallery
    assert "ScreenScale(" in dashboard + gallery


def test_visual_result_writer_is_atomic_bounded_and_contains_camera_state_metadata() -> None:
    writer = read("client/result_writer.lua")
    assert '"sao-visual-result-v1"' in writer
    assert '"run-index.json"' in writer
    assert '"complete.ready.json"' in writer
    assert '".partial.json"' in writer
    assert "plan_sha256" in writer
    assert "camera" in writer
    assert "state" in writer
    assert "retry_count" in writer
    assert "SAO.Constants.MAX_SCREENSHOTS" in writer
    assert "net." not in writer


def test_representative_visual_result_matches_frozen_schema() -> None:
    schema = json.loads((REPOSITORY / "companion" / "contracts" / "sao-visual-result-v1.schema.json").read_text(encoding="utf-8"))
    envelope = {
        "schema": "sao-visual-result-v1",
        "contract": {"major": 1, "minor": 0},
        "run_id": "123e4567-e89b-12d3-a456-426614174000",
        "created_utc": "2026-08-16T12:00:00Z",
        "producer": {"name": "Source Addon Optimizer Companion", "version": "1.0.0"},
        "environment": {"game_build": "2026.08.16", "map": "gm_flatgrass", "gamemode": "sandbox", "realm": "client", "session_seconds": 3.0, "mounted_addons": []},
        "chunk": {"sequence": 1, "total": 1},
        "payload": {
            "plan_sha256": "a" * 64,
            "overall_status": "PASS",
            "resources": [{"id": "test-1", "test_id": "test-1", "path": "models/example/car.mdl", "status": "PASS", "reason": "captured", "warnings": []}],
            "screenshots": [{
                "id": "shot-1", "test_id": "test-1", "path": "screenshots/123e4567-e89b-12d3-a456-426614174000/test-1-front.png", "sha256": "b" * 64,
                "bytes": 100, "view": "front", "retry_count": 0, "status": "PASS",
                "camera": {"fov": 35.0, "distance": 120.0, "crop_width": 512, "crop_height": 512},
                "state": {"skin": 0, "bodygroups": [{"name": "wheels", "value": 0}]},
            }],
            "performance": {"models": 1, "screenshots": 1, "wall_seconds": 3.0},
            "truncation": {"models": 0, "screenshots": 0},
        },
    }
    jsonschema.Draft202012Validator(schema).validate(envelope)
