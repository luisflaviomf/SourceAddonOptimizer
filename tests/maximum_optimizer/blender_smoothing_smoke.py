from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import bpy
import tempfile


repo_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
sys.path.insert(0, str(repo_root))

from maximum_optimizer.smoothing import apply_reconstructed_smoothing
import batch_optimize_qc
import batch_optimize_maximum


positions = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))
faces = ((0, 1, 2), (0, 2, 3))
component = 1.0 / math.sqrt(2.0)
normals = ((0, -component, component), (0, 0, 1), (0, 0, 1), (0, component, component))
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
mesh = bpy.data.meshes.new("smoothing-smoke")
mesh.from_pydata(positions, (), faces)
obj = bpy.data.objects.new("smoothing-smoke", mesh)
bpy.context.scene.collection.objects.link(obj)
material = bpy.data.materials.new("smoothing_material")
mesh.materials.append(material)
uv_layer = mesh.uv_layers.new(name="UVMap")
uvs = ((0, 0), (1, 0), (1, 1), (0, 1))
for loop in mesh.loops:
    uv_layer.data[loop.index].uv = uvs[loop.vertex_index]
group = obj.vertex_groups.new(name="root")
for index in range(len(positions)):
    group.add([index], 1.0, "REPLACE")
flat_default = [polygon.use_smooth for polygon in mesh.polygons]

loop_normals = (normals[0], normals[1], normals[2], normals[0], normals[2], normals[3])
mesh.normals_split_custom_set(loop_normals)
def payload_snapshot():
    mesh.calc_loop_triangles()
    return {
        "positions": [tuple(vertex.co) for vertex in mesh.vertices],
        "indices": [tuple(triangle.vertices) for triangle in mesh.loop_triangles],
        "uvs": [tuple(item.uv) for item in mesh.uv_layers.active.data],
        "materials": [polygon.material_index for polygon in mesh.polygons],
        "skin": [
            tuple(sorted((obj.vertex_groups[item.group].name, item.weight) for item in vertex.groups))
            for vertex in mesh.vertices
        ],
    }
before_payload = payload_snapshot()
batch_optimize_maximum.rebuild_mesh_smoothing_only(obj)
payload_unchanged = before_payload == payload_snapshot()
actual = [tuple(corner.vector) for corner in mesh.corner_normals]
expected = list(loop_normals)
max_error = max(
    math.sqrt(sum((actual_value - expected_value) ** 2 for actual_value, expected_value in zip(a, e)))
    for a, e in zip(actual, expected)
)
smooth_after = [polygon.use_smooth for polygon in mesh.polygons]
has_custom_normals = bool(mesh.has_custom_normals)

def rounded_key(values):
    return tuple(round(float(value), 5) for value in values)


intended_by_position = {}
for position, normal in zip(positions, normals):
    intended_by_position.setdefault(rounded_key(position), set()).add(rounded_key(normal))
intended_keys = sum(len(values) for values in intended_by_position.values())
intended_hard = sum(len(values) > 1 for values in intended_by_position.values())

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "smoothing-smoke.smd"
    batch_optimize_qc.export_source_file(out, "smd")
    lines = out.read_text(encoding="utf-8", errors="replace").splitlines()
vertices = []
triangle_count = 0
in_triangles = False
expect_material = False
vertex_in_triangle = 0
for line in lines:
    stripped = line.strip()
    if stripped == "triangles":
        in_triangles = True
        expect_material = True
        continue
    if not in_triangles:
        continue
    if stripped == "end":
        break
    if expect_material:
        expect_material = False
        vertex_in_triangle = 0
        triangle_count += 1
        continue
    parts = stripped.split()
    if len(parts) < 9:
        raise RuntimeError("invalid exported SMD vertex")
    vertices.append((tuple(map(float, parts[1:4])), tuple(map(float, parts[4:7]))))
    vertex_in_triangle += 1
    if vertex_in_triangle == 3:
        expect_material = True
exported_by_position = {}
for position, normal in vertices:
    exported_by_position.setdefault(rounded_key(position), set()).add(rounded_key(normal))
print("SMOOTHING_SMOKE " + json.dumps({
    "version": list(bpy.app.version),
    "flat_default": flat_default,
    "smooth_after": smooth_after,
    "has_custom_normals": has_custom_normals,
    "max_normal_error": max_error,
    "exported_triangles": triangle_count,
    "intended_position_normal_keys": intended_keys,
    "exported_position_normal_keys": sum(len(values) for values in exported_by_position.values()),
    "intended_hard_normal_positions": intended_hard,
    "exported_hard_normal_positions": sum(len(values) > 1 for values in exported_by_position.values()),
    "payload_unchanged": payload_unchanged,
}, sort_keys=True))
