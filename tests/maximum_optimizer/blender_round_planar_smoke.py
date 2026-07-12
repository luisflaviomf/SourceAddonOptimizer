"""Run with Blender --background --python after acquiring the shared Blender lock."""

import json
import math
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import batch_optimize_maximum as maximum


for obj in tuple(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

sides = 24
positions = []
for z in (-0.2, 0.2):
    positions.extend(
        (math.cos(2.0 * math.pi * index / sides), math.sin(2.0 * math.pi * index / sides), z)
        for index in range(sides)
    )
positions.extend(((0.0, 0.0, -0.2), (0.0, 0.0, 0.2)))
bottom_center, top_center = sides * 2, sides * 2 + 1
triangles = []
for index in range(sides):
    following = (index + 1) % sides
    triangles.extend((
        (index, following, sides + following),
        (index, sides + following, sides + index),
        (bottom_center, following, index),
        (top_center, sides + index, sides + following),
    ))

mesh = bpy.data.meshes.new("round-planar-smoke")
mesh.from_pydata(positions, (), triangles)
mesh.update()
obj = bpy.data.objects.new("round-planar-smoke", mesh)
bpy.context.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
bone = obj.vertex_groups.new(name="wheel")
bone.add(list(range(len(positions))), 1.0, "REPLACE")

candidate = maximum.CandidateConfig(
    "round-planar-smoke",
    "blender",
    0.5,
    0.0,
    True,
    (),
    strategy="round-planar-priority-v1",
    transfer="blender-native-v1",
)
metrics = maximum._optimize_blender_object(obj, candidate, 0.5)

assert metrics["round_admission"] == "eligible", metrics
assert metrics["round_priority_vertices_requested"] > 0, metrics
assert metrics["round_priority_vertices_survived"] > 0, metrics
assert metrics["round_planar_triangles_after"] < metrics["triangles_before"], metrics
assert metrics["triangles_after"] < metrics["triangles_before"], metrics
assert obj.vertex_groups.get("__maximum_round_priority_v1__") is None
print(json.dumps(metrics, sort_keys=True))
