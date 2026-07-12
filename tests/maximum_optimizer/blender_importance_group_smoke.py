from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import bpy


repo_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
sys.path.insert(0, str(repo_root))
import batch_optimize_maximum


SIZE = 11
POSITIONS = tuple(
    (float(x), float(y), 0.15 * math.sin(x * 0.7) * math.cos(y * 0.5))
    for y in range(SIZE) for x in range(SIZE)
)
FACES = tuple(
    face
    for y in range(SIZE - 1) for x in range(SIZE - 1)
    for face in (
        (y * SIZE + x, y * SIZE + x + 1, (y + 1) * SIZE + x + 1),
        (y * SIZE + x, (y + 1) * SIZE + x + 1, (y + 1) * SIZE + x),
    )
)
IMPORTANT = tuple(index for index, (x, _y, _z) in enumerate(POSITIONS) if x == 0.0)


def run(name: str, invert: bool) -> dict[str, object]:
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(POSITIONS, (), FACES)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    group = obj.vertex_groups.new(name="importance")
    group.add(list(IMPORTANT), 1.0, "REPLACE")
    modifier = obj.modifiers.new(name="decimate", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = 0.2
    modifier.vertex_group = group.name
    modifier.invert_vertex_group = invert
    modifier.vertex_group_factor = 1.0
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    actual = tuple(tuple(float(value) for value in vertex.co) for vertex in mesh.vertices)
    retained = sum(
        any(sum((a - b) ** 2 for a, b in zip(position, candidate)) <= 1e-12 for candidate in actual)
        for position in (POSITIONS[index] for index in IMPORTANT)
    )
    mesh.calc_loop_triangles()
    return {
        "important_retained": retained,
        "important_retained_fraction": retained / len(IMPORTANT),
        "triangles_after": len(mesh.loop_triangles),
        "vertices_after": len(mesh.vertices),
    }


bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
not_inverted = run("not-inverted", False)
inverted = run("inverted", True)

mesh = bpy.data.meshes.new("integrated")
mesh.from_pydata(POSITIONS, (), FACES)
obj = bpy.data.objects.new("integrated", mesh)
bpy.context.scene.collection.objects.link(obj)
material = bpy.data.materials.new("paint")
mesh.materials.append(material)
uv_layer = mesh.uv_layers.new(name="UVMap")
for loop in mesh.loops:
    x, y, _z = mesh.vertices[loop.vertex_index].co
    uv_layer.data[loop.index].uv = (x / (SIZE - 1), y / (SIZE - 1))
root = obj.vertex_groups.new(name="root")
root.add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
for selected in bpy.context.selected_objects:
    selected.select_set(False)
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
integrated = batch_optimize_maximum._optimize_blender_object(
    obj,
    batch_optimize_maximum.CandidateConfig(
        "importance-smoke", "blender", 0.2, 0.0, True, (),
        strategy="blender-importance-map-v1", transfer="blender-native-v1",
    ),
    0.2,
)
print("IMPORTANCE_GROUP_SMOKE " + json.dumps({
    "version": list(bpy.app.version),
    "triangles_before": len(FACES),
    "important_vertices": len(IMPORTANT),
    "not_inverted": not_inverted,
    "inverted": inverted,
    "integrated": integrated,
}, sort_keys=True))
