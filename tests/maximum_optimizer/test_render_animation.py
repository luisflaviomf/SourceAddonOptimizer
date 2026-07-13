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


def _animation_smd() -> str:
    return """version 1
nodes
0 \"root\" -1
1 \"Hood\" 0
end
skeleton
time 7
0 0 0 0 0 0 0
1 0 0 0 0 0 1
end
"""


def _fcurve(path='pose.bones["Hood"].rotation_euler', frame=0.0):
    return SimpleNamespace(
        data_path=path, array_index=2,
        keyframe_points=[SimpleNamespace(co=(frame, 1.0))],
    )


def _new_action(stem="idle", fcurves=None):
    slot = SimpleNamespace(name_display=stem, name=stem, target_id_type="OBJECT", handle=1)
    bag = SimpleNamespace(slot=slot, fcurves=list(fcurves or [_fcurve()]))
    strip = SimpleNamespace(channelbag=lambda selected: bag if selected is slot else None)
    action = SimpleNamespace(
        name="ArmatureAction", slots=[slot],
        layers=[SimpleNamespace(strips=[strip])],
    )
    return action, slot


def test_apply_animation_requires_new_blender5_action_slot_and_exact_channels(tmp_path):
    class Object:
        type = "ARMATURE"
        animation_data = None

        def select_set(self, _selected):
            pass

    armature = Object()
    root_bone = SimpleNamespace(name="root", parent=None)
    hood_bone = SimpleNamespace(name="Hood", parent=root_bone)
    armature.pose = SimpleNamespace(bones=[root_bone, hood_bone])
    armature.data = SimpleNamespace(bones=[root_bone, hood_bone])
    actions = []
    fake_bpy = SimpleNamespace(
        data=SimpleNamespace(actions=actions),
        context=SimpleNamespace(
            scene=SimpleNamespace(objects=[armature]),
            view_layer=SimpleNamespace(objects=SimpleNamespace(active=None)),
        )
    )

    def imported(_path):
        action, slot = _new_action()
        actions.append(action)
        armature.animation_data = SimpleNamespace(
            action=action, action_slot=slot,
        )
        return {"FINISHED"}

    source = tmp_path / "idle.smd"
    source.write_text(_animation_smd(), encoding="utf-8")

    with patch.object(render_previews, "bpy", fake_bpy), patch.object(
        render_previews, "_import_source", side_effect=imported
    ), patch.object(
        render_previews, "_animation_toolchain_proof",
        return_value={"toolchain_sha256": "a" * 64},
    ):
        binding = render_previews._apply_animation_source(
            source, (("bind", 0), ("representative", 0))
        )
    assert binding[2] is armature.animation_data.action_slot
    assert binding[3]["slot_name"] == "idle"
    assert binding[3]["source_times"] == [7]
    assert len(binding[3]["action_sha256"]) == 64


def test_apply_animation_rejects_stale_action_and_wrong_slot(tmp_path):
    stale, stale_slot = _new_action("stale")
    armature = SimpleNamespace(
        type="ARMATURE", animation_data=SimpleNamespace(action=stale, action_slot=stale_slot),
        pose=None, data=None,
        select_set=lambda _value: None,
    )
    root_bone = SimpleNamespace(name="root", parent=None)
    hood_bone = SimpleNamespace(name="Hood", parent=root_bone)
    armature.pose = SimpleNamespace(bones=[root_bone, hood_bone])
    armature.data = SimpleNamespace(bones=[root_bone, hood_bone])

    class Actions(list):
        def remove(self, value, do_unlink=True):
            super().remove(value)

    actions = Actions([stale])
    fake_bpy = SimpleNamespace(
        data=SimpleNamespace(actions=actions),
        context=SimpleNamespace(
            scene=SimpleNamespace(objects=[armature]),
            view_layer=SimpleNamespace(objects=SimpleNamespace(active=None)),
        ),
    )
    source = tmp_path / "idle.smd"
    source.write_text(_animation_smd(), encoding="utf-8")
    with patch.object(render_previews, "bpy", fake_bpy), patch.object(
        render_previews, "_import_source", return_value={"FINISHED"},
    ), patch.object(
        render_previews, "_animation_toolchain_proof",
        return_value={"toolchain_sha256": "a" * 64},
    ), pytest.raises(RuntimeError, match="new action"):
        render_previews._apply_animation_source(source, (("bind", 0), ("pose", 0)))

    def wrong_import(_path):
        action, slot = _new_action("wrong")
        actions.append(action)
        armature.animation_data.action = action
        armature.animation_data.action_slot = slot
        return {"FINISHED"}

    with patch.object(render_previews, "bpy", fake_bpy), patch.object(
        render_previews, "_import_source", side_effect=wrong_import,
    ), patch.object(
        render_previews, "_animation_toolchain_proof",
        return_value={"toolchain_sha256": "a" * 64},
    ), pytest.raises(RuntimeError, match="slot"):
        render_previews._apply_animation_source(source, (("bind", 0), ("pose", 0)))


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
    slot = object()
    updates = []
    armature = SimpleNamespace(
        animation_data=SimpleNamespace(action=action, action_slot=slot),
        data=SimpleNamespace(pose_position="POSE"),
        update_tag=lambda **kwargs: updates.append(kwargs),
    )
    frames = []
    scene = SimpleNamespace(frame_set=frames.append)
    view_updates = []
    view_layer = SimpleNamespace(update=lambda: view_updates.append(True))
    binding = (armature, action, slot, {"action_sha256": "a" * 64})
    render_previews._set_pose_state(
        binding, "bind", 0, scene=scene, view_layer=view_layer,
    )
    assert armature.animation_data.action is None
    assert armature.data.pose_position == "REST"
    render_previews._set_pose_state(
        binding, "representative", 10, scene=scene, view_layer=view_layer,
    )
    assert armature.animation_data.action is action
    assert armature.animation_data.action_slot is slot
    assert armature.data.pose_position == "POSE"
    assert frames == [0, 10]
    assert updates == [{"refresh": {"DATA"}}, {"refresh": {"DATA"}}]
    assert view_updates == [True, True]


def test_region_pose_requires_vertex_weight_on_selected_bone_or_descendant():
    root = SimpleNamespace(name="root", parent=None)
    hood = SimpleNamespace(name="Hood", parent=root)
    latch = SimpleNamespace(name="Latch", parent=hood)
    armature = SimpleNamespace(data=SimpleNamespace(bones=[root, hood, latch]))
    groups = [SimpleNamespace(name="Latch")]
    influenced = SimpleNamespace(
        vertex_groups=groups,
        data=SimpleNamespace(vertices=[SimpleNamespace(groups=[SimpleNamespace(group=0, weight=1.0)])]),
    )
    render_previews._validate_region_pose_influence((influenced,), armature, "Hood")
    uninfluenced = SimpleNamespace(
        vertex_groups=[SimpleNamespace(name="Door")],
        data=SimpleNamespace(vertices=[SimpleNamespace(groups=[SimpleNamespace(group=0, weight=1.0)])]),
    )
    with pytest.raises(ValueError, match="not influenced"):
        render_previews._validate_region_pose_influence((uninfluenced,), armature, "Hood")


def test_evaluated_region_pose_proof_uses_rest_to_raw_action_vertices():
    bind = (("models/car/hood.smd::hood", ((0.0, 0.0, 0.0), (3.0, 4.0, 0.0))),)
    posed = (("models/car/hood.smd::hood", ((0.0, 0.0, 0.0), (6.0, 8.0, 0.0))),)
    action_proof = {
        "action_sha256": "a" * 64,
        "animation_input_sha256": "b" * 64,
        "toolchain": {"toolchain_sha256": "c" * 64},
    }

    proof = render_previews._build_evaluated_region_pose_proof(
        bind, posed, bind, posed,
        action_proof=action_proof,
        frame=0,
        source_time=7,
        selected_bone="Hood",
        influenced_bones=("Hood", "Latch"),
    )

    assert proof["kind"] == "blender-evaluated-region-pose-delta-v1"
    assert proof["baseline"] == "armature-rest"
    assert proof["candidate_inputs_consulted"] is False
    assert proof["corrective_used_as_baseline"] is False
    assert proof["vertex_count"] == 2
    assert proof["moved_vertex_count"] == 1
    assert proof["maximum_displacement"] == pytest.approx(5.0)
    assert proof["rms_displacement"] == pytest.approx(5.0 / (2.0 ** 0.5))
    assert proof["bind_geometry_sha256"] != proof["posed_geometry_sha256"]
    assert len(proof["proof_sha256"]) == 64


def test_evaluated_region_pose_proof_rejects_repeat_or_topology_drift():
    bind = (("hood", ((0.0, 0.0, 0.0),)),)
    posed = (("hood", ((1.0, 0.0, 0.0),)),)
    changed_repeat = (("hood", ((1.0, 0.0, 0.0001),)),)
    proof = {
        "action_sha256": "a" * 64,
        "animation_input_sha256": "b" * 64,
        "toolchain": {"toolchain_sha256": "c" * 64},
    }
    args = dict(
        action_proof=proof, frame=0, source_time=0,
        selected_bone="Hood", influenced_bones=("Hood",),
    )

    with pytest.raises(ValueError, match="repeat"):
        render_previews._build_evaluated_region_pose_proof(
            bind, posed, bind, changed_repeat, **args,
        )
    with pytest.raises(ValueError, match="topology"):
        render_previews._build_evaluated_region_pose_proof(
            bind, (("extra", ((0.0, 0.0, 0.0),)),) + posed, bind,
            (("extra", ((0.0, 0.0, 0.0),)),) + posed, **args,
        )
