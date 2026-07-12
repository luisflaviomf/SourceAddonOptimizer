from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import render_previews


def test_parser_accepts_paired_real_animation_sources():
    args = render_previews._parse_args([
        "--before", "mesh.smd", "--after", "mesh_OPT.smd", "--out", "renders",
        "--animation-before", "idle.smd", "--animation-after", "idle_OPT.smd",
    ])
    assert args.animation_before == "idle.smd"
    assert args.animation_after == "idle_OPT.smd"


def test_apply_animation_selects_single_armature_and_requires_real_action_range():
    class Object:
        type = "ARMATURE"
        animation_data = None

        def select_set(self, _selected):
            pass

    armature = Object()
    fake_bpy = SimpleNamespace(
        context=SimpleNamespace(
            scene=SimpleNamespace(objects=[armature]),
            view_layer=SimpleNamespace(objects=SimpleNamespace(active=None)),
        )
    )

    def imported(_path):
        armature.animation_data = SimpleNamespace(
            action=SimpleNamespace(frame_range=(0.0, 10.0))
        )

    with patch.object(render_previews, "bpy", fake_bpy), patch.object(
        render_previews, "_import_source", side_effect=imported
    ):
        render_previews._apply_animation_source(
            Path("idle.smd"), (("bind", 0), ("representative", 10))
        )


def test_apply_animation_fails_closed_without_unambiguous_armature():
    fake_bpy = SimpleNamespace(
        context=SimpleNamespace(scene=SimpleNamespace(objects=[]))
    )
    with patch.object(render_previews, "bpy", fake_bpy), pytest.raises(
        RuntimeError, match="representative-animation-unavailable"
    ):
        render_previews._apply_animation_source(
            Path("idle.smd"), (("bind", 0), ("representative", 10))
        )


def test_bind_pose_disables_action_while_representative_restores_it():
    action = object()
    armature = SimpleNamespace(animation_data=SimpleNamespace(action=action))
    frames = []
    scene = SimpleNamespace(frame_set=frames.append)
    binding = (armature, action)
    render_previews._set_pose_state(binding, "bind", 0, scene=scene)
    assert armature.animation_data.action is None
    render_previews._set_pose_state(binding, "representative", 10, scene=scene)
    assert armature.animation_data.action is action
    assert frames == [0, 10]
