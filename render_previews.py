#!/usr/bin/env python3
# Render before/after previews for a single model using Blender (headless).

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import bpy
from mathutils import Vector


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))


ANGLE_DIRS = {
    "front": Vector((0.0, -1.0, 0.0)),
    "back": Vector((0.0, 1.0, 0.0)),
    "left": Vector((-1.0, 0.0, 0.0)),
    "right": Vector((1.0, 0.0, 0.0)),
    "top": Vector((0.0, 0.0, 1.0)),
    "bottom": Vector((0.0, 0.0, -1.0)),
    "iso1": Vector((1.0, -1.0, 1.0)),
    "iso2": Vector((-1.0, -1.0, 1.0)),
}


def _parse_args(argv: list[str]):
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True, action="append", help="Path to original SMD/DMX (repeatable)")
    ap.add_argument("--after", required=True, action="append", help="Path to optimized SMD/DMX (repeatable)")
    ap.add_argument("--out", required=True, help="Output dir for renders + summary JSON")
    ap.add_argument("--size", type=int, default=1024, help="Render resolution (square)")
    ap.add_argument("--camera-json", default=None, help="Optional deterministic camera description")
    ap.add_argument("--pose", default="reference", help="Pose identity recorded by the caller")
    ap.add_argument("--material-root", action="append", default=[], help="Addon/framework material root")
    ap.add_argument(
        "--angles",
        default="front,back,left,right,top,bottom,iso1,iso2",
        help="Comma-separated list of angles",
    )
    return ap.parse_args(argv)


def _expand_paths(values: list[str]) -> list[str]:
    out = []
    for raw in values:
        for part in raw.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _ensure_source_tools():
    # Try enabling Source Tools if not already enabled.
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_valvesource")
    except Exception:
        pass


def _clear_scene():
    # Avoid read_factory_settings (it can remove addon props and break handlers).
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for datablock in (bpy.data.meshes, bpy.data.cameras, bpy.data.lights, bpy.data.materials, bpy.data.images):
        for block in list(datablock):
            datablock.remove(block, do_unlink=True)


def _setup_scene(size: int):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"

    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.use_nodes = True
    nodes = scene.world.node_tree.nodes
    bg = nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.12, 0.13, 0.15, 1.0)
        bg.inputs[1].default_value = 1.0
    return scene


def _add_light(name: str, location: Vector, energy: float, size: float = 10.0):
    light_data = bpy.data.lights.new(name=name, type="AREA")
    light_data.energy = energy
    light_data.size = size
    light_obj = bpy.data.objects.new(name, light_data)
    light_obj.location = location
    bpy.context.collection.objects.link(light_obj)
    return light_obj


def _setup_lights(center: Vector, scale: float):
    dist = scale * 2.0
    _add_light("Key", center + Vector((dist, -dist, dist)), energy=1500, size=scale)
    _add_light("Fill", center + Vector((-dist, -dist, dist * 0.6)), energy=900, size=scale)
    _add_light("Rim", center + Vector((0.0, dist, dist)), energy=700, size=scale)


def _ensure_camera():
    cam_data = bpy.data.cameras.new(name="PreviewCamera")
    cam_obj = bpy.data.objects.new("PreviewCamera", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    cam_data.type = "ORTHO"
    cam_data.clip_start = 0.1
    cam_data.clip_end = 100000.0
    return cam_obj


def _get_mesh_objects():
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def _color_from_name(name: str):
    digest = hashlib.md5(name.encode("utf-8", errors="ignore")).digest()
    r = 0.25 + (digest[0] / 255.0) * 0.55
    g = 0.25 + (digest[1] / 255.0) * 0.55
    b = 0.25 + (digest[2] / 255.0) * 0.55
    return (r, g, b, 1.0)


def _material_semantics(name: str, material_roots: list[Path]):
    relative = Path(*name.replace("\\", "/").removesuffix(".vmt").split("/")).with_suffix(".vmt")
    for root in material_roots:
        candidate = root / "materials" / relative
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8", errors="replace")
        text = "\n".join(line.split("//", 1)[0] for line in text.splitlines())
        enabled = lambda key: re.search(rf'(?i)"?{re.escape(key)}"?\s+"?(?:1|true|yes)"?', text) is not None
        return {
            "transparent": enabled("$translucent") or enabled("$alphatest") or enabled("$additive"),
            "refractive": re.search(r"(?i)refract", text) is not None,
            "two_sided": enabled("$nocull"),
        }
    return {"transparent": False, "refractive": False, "two_sided": False}


def _make_preview_material(name: str, material_roots: list[Path]):
    mat = bpy.data.materials.new(name=f"Preview_{name}")
    mat.use_nodes = True
    semantics = _material_semantics(name, material_roots)
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = _color_from_name(name)
        bsdf.inputs["Roughness"].default_value = 0.45
        if "Specular" in bsdf.inputs:
            bsdf.inputs["Specular"].default_value = 0.25
        elif "Specular IOR Level" in bsdf.inputs:
            bsdf.inputs["Specular IOR Level"].default_value = 0.25
        if semantics["transparent"] and "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = 0.45
        if semantics["refractive"] and "Transmission Weight" in bsdf.inputs:
            bsdf.inputs["Transmission Weight"].default_value = 0.65
    mat.use_backface_culling = not semantics["two_sided"]
    if semantics["transparent"]:
        try:
            mat.surface_render_method = "DITHERED"
        except (AttributeError, TypeError):
            pass
    return mat


def _apply_preview_materials(objs, material_roots: list[Path]):
    cache = {}
    for obj in objs:
        if not hasattr(obj.data, "materials"):
            continue
        if obj.data.materials:
            for idx, mat in enumerate(obj.data.materials):
                key = mat.name if mat else f"{obj.name}_{idx}"
                preview = cache.get(key)
                if preview is None:
                    preview = _make_preview_material(key, material_roots)
                    cache[key] = preview
                obj.data.materials[idx] = preview
        else:
            key = f"{obj.name}_mat"
            preview = cache.get(key)
            if preview is None:
                preview = _make_preview_material(key, material_roots)
                cache[key] = preview
            obj.data.materials.append(preview)


def _count_tris(objs):
    total = 0
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in objs:
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        if mesh is None:
            continue
        mesh.calc_loop_triangles()
        total += len(mesh.loop_triangles)
        eval_obj.to_mesh_clear()
    return total


def _compute_bbox(objs):
    min_v = Vector((float("inf"), float("inf"), float("inf")))
    max_v = Vector((float("-inf"), float("-inf"), float("-inf")))
    for obj in objs:
        for corner in obj.bound_box:
            v = obj.matrix_world @ Vector(corner)
            min_v.x = min(min_v.x, v.x)
            min_v.y = min(min_v.y, v.y)
            min_v.z = min(min_v.z, v.z)
            max_v.x = max(max_v.x, v.x)
            max_v.y = max(max_v.y, v.y)
            max_v.z = max(max_v.z, v.z)
    return min_v, max_v


def _fit_camera(objs, cam_obj, fit=None):
    if fit:
        center, ortho_scale, dist = fit
        return center, ortho_scale, dist

    min_v, max_v = _compute_bbox(objs)
    center = (min_v + max_v) * 0.5
    extents = max_v - min_v
    max_dim = max(extents.x, extents.y, extents.z)
    ortho_scale = max_dim * 1.4 if max_dim > 0 else 1.0
    dist = max_dim * 2.5 + 1.0
    return center, ortho_scale, dist


def _set_camera_pose(cam_obj, center: Vector, direction: Vector, dist: float):
    dir_norm = direction.normalized()
    cam_obj.location = center + (dir_norm * dist)
    to_target = center - cam_obj.location
    cam_obj.rotation_euler = to_target.to_track_quat("-Z", "Y").to_euler()


def _import_smd_without_addon(path: Path):
    from maximum_optimizer.smd import parse_smd

    document = parse_smd(path.read_text(encoding="utf-8", errors="replace"))
    positions = []
    normals = []
    uvs = []
    faces = []
    materials = []
    material_indices = []
    for triangle in document.triangles:
        start = len(positions)
        for vertex in triangle.vertices:
            positions.append(vertex.position)
            normals.append(vertex.normal)
            uvs.append(vertex.uv)
        faces.append((start, start + 1, start + 2))
        if triangle.material not in materials:
            materials.append(triangle.material)
        material_indices.append(materials.index(triangle.material))

    mesh = bpy.data.meshes.new(path.stem)
    mesh.from_pydata(positions, [], faces)
    mesh.update()
    for name in materials:
        mesh.materials.append(bpy.data.materials.new(name=name))
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for polygon, material_index in zip(mesh.polygons, material_indices):
        polygon.material_index = material_index
        polygon.use_smooth = True
        for loop_index in polygon.loop_indices:
            uv_layer.data[loop_index].uv = uvs[mesh.loops[loop_index].vertex_index]
    if hasattr(mesh, "normals_split_custom_set"):
        try:
            mesh.normals_split_custom_set([normals[loop.vertex_index] for loop in mesh.loops])
        except RuntimeError:
            pass
    obj = bpy.data.objects.new(path.stem, mesh)
    bpy.context.collection.objects.link(obj)


def _import_source(path: Path):
    ext = path.suffix.lower()
    if ext == ".smd":
        _import_smd_without_addon(path)
        return
    if ext == ".dmx" and hasattr(bpy.ops.import_scene, "dmx"):
        bpy.ops.import_scene.dmx(filepath=str(path))
        return
    raise RuntimeError(
        "Importador SMD/DMX nao encontrado. Verifique se o Blender Source Tools esta habilitado."
    )


def _render_set(
    label: str,
    src_paths: list[Path],
    out_dir: Path,
    angles: list[str],
    size: int,
    fit=None,
    material_roots=None,
):
    _clear_scene()
    _setup_scene(size)
    cam_obj = _ensure_camera()

    for src_path in src_paths:
        _import_source(src_path)
    objs = _get_mesh_objects()
    if not objs:
        raise RuntimeError(f"No mesh objects found for {label}: {src_paths}")

    _apply_preview_materials(objs, material_roots or [])
    tris = _count_tris(objs)

    center, ortho_scale, dist = _fit_camera(objs, cam_obj, fit=fit)
    cam_obj.data.ortho_scale = ortho_scale
    _setup_lights(center, ortho_scale)

    out_dir.mkdir(parents=True, exist_ok=True)
    for angle in angles:
        direction = ANGLE_DIRS.get(angle, ANGLE_DIRS["front"])
        _set_camera_pose(cam_obj, center, direction, dist)
        bpy.context.scene.render.filepath = str(out_dir / f"{angle}.png")
        bpy.ops.render.render(write_still=True)

    return tris, (center, ortho_scale, dist)


def main():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = argv[1:]

    args = _parse_args(argv)
    _ensure_source_tools()
    before = [Path(p).resolve() for p in _expand_paths(args.before)]
    after = [Path(p).resolve() for p in _expand_paths(args.after)]
    out_dir = Path(args.out).resolve()
    angles = [a.strip() for a in args.angles.split(",") if a.strip()]
    material_roots = [Path(value).resolve() for value in args.material_root]
    camera_fit = None
    if args.camera_json:
        camera_path = Path(args.camera_json).resolve()
        camera = json.loads(camera_path.read_text(encoding="utf-8"))
        if isinstance(camera.get("angles"), list):
            angles = [str(value) for value in camera["angles"] if str(value) in ANGLE_DIRS]
        if all(name in camera for name in ("center", "ortho_scale", "distance")):
            center = camera["center"]
            if not isinstance(center, list) or len(center) != 3:
                raise SystemExit("[ERROR] Camera center must contain three values.")
            camera_fit = (Vector(tuple(float(value) for value in center)), float(camera["ortho_scale"]), float(camera["distance"]))

    if args.pose != "reference":
        raise SystemExit(f"[ERROR] Unsupported preview pose: {args.pose}")

    if not before:
        raise SystemExit("[ERROR] Before list is empty.")
    if not after:
        raise SystemExit("[ERROR] After list is empty.")
    for p in before:
        if not p.exists():
            raise SystemExit(f"[ERROR] Before file not found: {p}")
    for p in after:
        if not p.exists():
            raise SystemExit(f"[ERROR] After file not found: {p}")

    original_dir = out_dir / "original"
    optimized_dir = out_dir / "optimized"

    before_tris, fit = _render_set(
        "before", before, original_dir, angles, args.size, fit=camera_fit, material_roots=material_roots
    )
    after_tris, _ = _render_set(
        "after", after, optimized_dir, angles, args.size, fit=fit, material_roots=material_roots
    )

    before_files = [str(p) for p in before]
    after_files = [str(p) for p in after]
    summary = {
        "angles": angles,
        "size": args.size,
        "pose": args.pose,
        "before": {
            "file": before_files[0] if before_files else "",
            "files": before_files,
            "tris": before_tris,
            "images": {angle: f"original/{angle}.png" for angle in angles},
        },
        "after": {
            "file": after_files[0] if after_files else "",
            "files": after_files,
            "tris": after_tris,
            "images": {angle: f"optimized/{angle}.png" for angle in angles},
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "preview_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[OK] Preview summary: {summary_path}")


if __name__ == "__main__":
    main()
