#!/usr/bin/env python3
# Render before/after previews for a single model using Blender (headless).

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

try:
    import bpy
    from mathutils import Vector
except ModuleNotFoundError:  # Parsing/tests run in the worker Python, outside Blender.
    bpy = None

    class Vector(tuple):
        def __new__(cls, values):
            return super().__new__(cls, values)


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
    ap.add_argument(
        "--angles",
        default="front,back,left,right,top,bottom,iso1,iso2",
        help="Comma-separated list of angles",
    )
    ap.add_argument("--passes", default=None, help="Opt-in render passes: textured,clay")
    ap.add_argument("--poses", default=None, help="Opt-in pose frames as name:frame CSV")
    ap.add_argument("--materials-root", default=None, help="Source materials directory")
    ap.add_argument("--vtfcmd", default=None, help="Optional VTFCmd executable")
    return ap.parse_args(argv)


def _parse_csv(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _validated_passes(raw: str | None) -> tuple[str, ...]:
    passes = _parse_csv(raw or "textured,clay")
    if not passes or len(set(passes)) != len(passes) or any(
        render_pass not in {"textured", "clay"} for render_pass in passes
    ):
        raise ValueError("render pass list must contain unique textured/clay values")
    return passes


def _parse_poses(raw: str | None) -> tuple[tuple[str, int], ...]:
    if raw is None or not raw.strip():
        return (("bind", 0),)
    poses: list[tuple[str, int]] = []
    seen: set[str] = set()
    for token in _parse_csv(raw):
        if token.count(":") != 1:
            raise ValueError(f"invalid pose: {token}")
        name, frame_raw = (part.strip() for part in token.split(":", 1))
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in seen:
            raise ValueError(f"invalid pose: {token}")
        try:
            frame = int(frame_raw)
        except ValueError as exc:
            raise ValueError(f"invalid pose: {token}") from exc
        if frame < 0:
            raise ValueError(f"invalid pose: {token}")
        seen.add(name)
        poses.append((name, frame))
    if not poses:
        raise ValueError("pose list cannot be empty")
    return tuple(poses)


def _is_extended_mode(args) -> bool:
    return any(
        value is not None
        for value in (args.passes, args.poses, args.materials_root, args.vtfcmd)
    )


def _extract_base_texture(vmt_text: str) -> str | None:
    match = re.search(
        r'(?im)^\s*"?\$basetexture"?\s+"?([^"\s}]+)',
        vmt_text,
    )
    if match is None:
        return None
    value = match.group(1).replace("\\", "/").strip("/")
    return value or None


def _render_entry(
    root: Path,
    render_pass: str,
    pose: str,
    angle: str,
    image_path: Path,
    *,
    texture_missing: bool,
) -> dict:
    return {
        "pass": render_pass,
        "pose": pose,
        "angle": angle,
        "image": image_path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "texture_missing": bool(texture_missing),
    }


def _write_render_manifest(
    root: Path,
    entries: list[dict],
    geometry: list[dict],
    bbox: dict,
    *,
    stride: int,
    seed: int,
) -> Path:
    manifest = {
        "schema": 1,
        "entries": entries,
        "geometry": geometry,
        "bbox": bbox,
        "sampling": {"stride": stride, "seed": seed},
    }
    path = root / "render_manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


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


def _setup_scene(size: int, *, transparent: bool = False):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.film_transparent = transparent
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


def _make_preview_material(name: str):
    mat = bpy.data.materials.new(name=f"Preview_{name}")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = _color_from_name(name)
        bsdf.inputs["Roughness"].default_value = 0.45
        if "Specular" in bsdf.inputs:
            bsdf.inputs["Specular"].default_value = 0.25
        elif "Specular IOR Level" in bsdf.inputs:
            bsdf.inputs["Specular IOR Level"].default_value = 0.25
    return mat


def _apply_preview_materials(objs):
    cache = {}
    for obj in objs:
        if not hasattr(obj.data, "materials"):
            continue
        if obj.data.materials:
            for idx, mat in enumerate(obj.data.materials):
                key = mat.name if mat else f"{obj.name}_{idx}"
                preview = cache.get(key)
                if preview is None:
                    preview = _make_preview_material(key)
                    cache[key] = preview
                obj.data.materials[idx] = preview
        else:
            key = f"{obj.name}_mat"
            preview = cache.get(key)
            if preview is None:
                preview = _make_preview_material(key)
                cache[key] = preview
            obj.data.materials.append(preview)


def _make_clay_material():
    mat = bpy.data.materials.new(name="MaximumControlledClay")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.65
        if "Specular IOR Level" in bsdf.inputs:
            bsdf.inputs["Specular IOR Level"].default_value = 0.2
    return mat


def _apply_clay_material(objs) -> None:
    clay = _make_clay_material()
    for obj in objs:
        if not hasattr(obj.data, "materials"):
            continue
        obj.data.materials.clear()
        obj.data.materials.append(clay)


def _convert_vtf(vtf_path: Path, vtfcmd: Path | None, cache_root: Path) -> Path | None:
    if vtfcmd is None or not vtfcmd.is_file() or not vtf_path.is_file():
        return None
    digest = hashlib.sha256(vtf_path.read_bytes()).hexdigest()
    output_dir = cache_root / digest
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{vtf_path.stem}.png"
    if output_path.is_file():
        return output_path
    command = [
        str(vtfcmd),
        "-file",
        str(vtf_path),
        "-output",
        str(output_dir),
        "-exportformat",
        "png",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return output_path if result.returncode == 0 and output_path.is_file() else None


def _source_texture_png(
    material_name: str,
    materials_root: Path | None,
    vtfcmd: Path | None,
    cache_root: Path,
) -> Path | None:
    if materials_root is None or not materials_root.is_dir():
        return None
    relative = material_name.replace("\\", "/").strip("/")
    if relative.casefold().endswith(".vmt"):
        relative = relative[:-4]
    vmt_path = materials_root / f"{relative}.vmt"
    if not vmt_path.is_file():
        return None
    try:
        base_texture = _extract_base_texture(vmt_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if base_texture is None:
        return None
    return _convert_vtf(materials_root / f"{base_texture}.vtf", vtfcmd, cache_root)


def _make_textured_material(name: str, png_path: Path):
    mat = bpy.data.materials.new(name=f"MaximumTextured_{name}")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(str(png_path), check_existing=True)
    texture.image.colorspace_settings.name = "sRGB"
    if bsdf:
        links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(texture.outputs["Alpha"], bsdf.inputs["Alpha"])
        bsdf.inputs["Roughness"].default_value = 0.5
    return mat


def _make_missing_texture_material(name: str):
    mat = bpy.data.materials.new(name=f"MaximumMissingTexture_{name}")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
        bsdf.inputs["Alpha"].default_value = 0.0
    return mat


def _apply_textured_materials(
    objs,
    source_materials: dict[tuple[str, int], str],
    materials_root: Path | None,
    vtfcmd: Path | None,
    cache_root: Path,
) -> bool:
    missing = False
    material_cache = {}
    for obj in objs:
        if not hasattr(obj.data, "materials"):
            continue
        source_indices = [
            index for (object_name, index) in source_materials if object_name == obj.name
        ]
        original_count = max(source_indices, default=0) + 1
        obj.data.materials.clear()
        for index in range(original_count):
            name = source_materials.get((obj.name, index), "")
            png_path = _source_texture_png(name, materials_root, vtfcmd, cache_root)
            if png_path is None:
                missing = True
                obj.data.materials.append(_make_missing_texture_material(f"{obj.name}_{index}"))
                continue
            cache_key = str(png_path)
            material = material_cache.get(cache_key)
            if material is None:
                material = _make_textured_material(name, png_path)
                material_cache[cache_key] = material
            obj.data.materials.append(material)
    return missing


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


def _import_source(path: Path):
    ext = path.suffix.lower()
    if ext == ".smd" and hasattr(bpy.ops.import_scene, "smd"):
        bpy.ops.import_scene.smd(filepath=str(path))
        return
    if ext == ".dmx" and hasattr(bpy.ops.import_scene, "dmx"):
        bpy.ops.import_scene.dmx(filepath=str(path))
        return
    raise RuntimeError(
        "Importador SMD/DMX nao encontrado. Verifique se o Blender Source Tools esta habilitado."
    )


def _render_set(label: str, src_paths: list[Path], out_dir: Path, angles: list[str], size: int, fit=None):
    _clear_scene()
    _setup_scene(size)
    cam_obj = _ensure_camera()

    for src_path in src_paths:
        _import_source(src_path)
    objs = _get_mesh_objects()
    if not objs:
        raise RuntimeError(f"No mesh objects found for {label}: {src_paths}")

    _apply_preview_materials(objs)
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


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _capture_regions(objs, frame: int) -> dict[str, dict]:
    bpy.context.scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    regions = {}
    for obj in sorted(objs, key=lambda item: item.name.casefold()):
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        if mesh is None:
            continue
        try:
            mesh.calc_loop_triangles()
            matrix = evaluated.matrix_world.copy()
            normal_matrix = matrix.to_3x3().inverted().transposed()
            vertices = [matrix @ vertex.co.copy() for vertex in mesh.vertices]
            normals = [(normal_matrix @ vertex.normal).normalized() for vertex in mesh.vertices]
            uv_values = [(0.0, 0.0) for _ in mesh.vertices]
            uv_layer = mesh.uv_layers.active
            if uv_layer is not None:
                assigned = set()
                for loop in mesh.loops:
                    if loop.vertex_index not in assigned:
                        uv = uv_layer.data[loop.index].uv
                        uv_values[loop.vertex_index] = (float(uv.x), float(uv.y))
                        assigned.add(loop.vertex_index)
            triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
            regions[obj.name.casefold()] = {
                "scope": obj.name,
                "vertices": vertices,
                "normals": normals,
                "uvs": uv_values,
                "triangles": triangles,
            }
        finally:
            evaluated.to_mesh_clear()
    return regions


def _geometry_metrics_for_region(reference: dict, candidate: dict, diagonal: float, stride: int) -> dict:
    from mathutils.bvhtree import BVHTree

    if not reference["triangles"] or not candidate["triangles"]:
        return {
            "surface_bidirectional_p95": 1.0,
            "surface_max": 1.0,
            "normal_angle_p95": 180.0,
            "uv_error_p95": 1.0,
            "skinning_error_p95": 1.0,
            "region_missing": False,
        }
    ref_bvh = BVHTree.FromPolygons(reference["vertices"], reference["triangles"], all_triangles=True)
    candidate_bvh = BVHTree.FromPolygons(
        candidate["vertices"], candidate["triangles"], all_triangles=True
    )
    distances: list[float] = []
    normal_angles: list[float] = []
    uv_errors: list[float] = []

    def sample(source: dict, target: dict, target_bvh) -> None:
        for index in range(0, len(source["vertices"]), stride):
            nearest = target_bvh.find_nearest(source["vertices"][index])
            if nearest is None:
                distances.append(diagonal)
                normal_angles.append(180.0)
                uv_errors.append(1.0)
                continue
            location, target_normal, polygon_index, distance = nearest
            distances.append(float(distance) / diagonal)
            dot = max(-1.0, min(1.0, source["normals"][index].dot(target_normal.normalized())))
            normal_angles.append(math.degrees(math.acos(dot)))
            target_triangle = target["triangles"][polygon_index]
            nearest_index = min(
                target_triangle,
                key=lambda vertex_index: (target["vertices"][vertex_index] - location).length_squared,
            )
            source_uv = source["uvs"][index]
            target_uv = target["uvs"][nearest_index]
            uv_errors.append(math.dist(source_uv, target_uv))

    sample(candidate, reference, ref_bvh)
    sample(reference, candidate, candidate_bvh)
    return {
        "surface_bidirectional_p95": _percentile(distances, 0.95),
        "surface_max": max(distances, default=0.0),
        "normal_angle_p95": _percentile(normal_angles, 0.95),
        "uv_error_p95": _percentile(uv_errors, 0.95),
        "skinning_error_p95": _percentile(distances, 0.95),
        "region_missing": False,
    }


def _skinning_error(
    reference_bind: dict,
    candidate_bind: dict,
    reference_pose: dict,
    candidate_pose: dict,
    diagonal: float,
    stride: int,
) -> float:
    from mathutils.bvhtree import BVHTree

    if not reference_bind["triangles"] or not candidate_bind["triangles"]:
        return 1.0
    reference_bvh = BVHTree.FromPolygons(
        reference_bind["vertices"], reference_bind["triangles"], all_triangles=True
    )
    candidate_bvh = BVHTree.FromPolygons(
        candidate_bind["vertices"], candidate_bind["triangles"], all_triangles=True
    )
    errors = []

    def sample(source_bind: dict, source_pose: dict, target_bind: dict, target_pose: dict, target_bvh) -> None:
        for index in range(0, len(source_bind["vertices"]), stride):
            if index >= len(source_pose["vertices"]):
                errors.append(1.0)
                continue
            nearest = target_bvh.find_nearest(source_bind["vertices"][index])
            if nearest is None:
                errors.append(1.0)
                continue
            location, _, polygon_index, _ = nearest
            target_triangle = target_bind["triangles"][polygon_index]
            target_index = min(
                target_triangle,
                key=lambda vertex_index: (
                    target_bind["vertices"][vertex_index] - location
                ).length_squared,
            )
            if target_index >= len(target_pose["vertices"]):
                errors.append(1.0)
                continue
            source_displacement = (
                source_pose["vertices"][index] - source_bind["vertices"][index]
            )
            target_displacement = (
                target_pose["vertices"][target_index]
                - target_bind["vertices"][target_index]
            )
            errors.append((source_displacement - target_displacement).length / diagonal)

    sample(candidate_bind, candidate_pose, reference_bind, reference_pose, reference_bvh)
    sample(reference_bind, reference_pose, candidate_bind, candidate_pose, candidate_bvh)
    return _percentile(errors, 0.95)


def _geometry_entries(
    reference_snapshots: dict[str, dict[str, dict]],
    candidate_snapshots: dict[str, dict[str, dict]],
    diagonal: float,
    stride: int,
) -> list[dict]:
    entries = []
    for pose in sorted(set(reference_snapshots) | set(candidate_snapshots)):
        reference_regions = reference_snapshots.get(pose, {})
        candidate_regions = candidate_snapshots.get(pose, {})
        for key in sorted(set(reference_regions) | set(candidate_regions)):
            reference = reference_regions.get(key)
            candidate = candidate_regions.get(key)
            scope = (reference or candidate)["scope"]
            if reference is None or candidate is None:
                metrics = {
                    "surface_bidirectional_p95": 1.0,
                    "surface_max": 1.0,
                    "normal_angle_p95": 180.0,
                    "uv_error_p95": 1.0,
                    "skinning_error_p95": 1.0,
                    "region_missing": True,
                }
            else:
                metrics = _geometry_metrics_for_region(reference, candidate, diagonal, stride)
                if pose == "bind":
                    metrics["skinning_error_p95"] = 0.0
                else:
                    reference_bind = reference_snapshots.get("bind", {}).get(key)
                    candidate_bind = candidate_snapshots.get("bind", {}).get(key)
                    if reference_bind is None or candidate_bind is None:
                        metrics["skinning_error_p95"] = 1.0
                    else:
                        metrics["skinning_error_p95"] = _skinning_error(
                            reference_bind,
                            candidate_bind,
                            reference,
                            candidate,
                            diagonal,
                            stride,
                        )
            entries.append({"scope": scope, "pose": pose, **metrics})
    return entries


def _bbox_payload(objs) -> dict:
    min_v, max_v = _compute_bbox(objs)
    diagonal = (max_v - min_v).length
    return {
        "min": [float(min_v.x), float(min_v.y), float(min_v.z)],
        "max": [float(max_v.x), float(max_v.y), float(max_v.z)],
        "diagonal": float(diagonal),
    }


def _render_extended_set(
    label: str,
    src_paths: list[Path],
    root: Path,
    angles: tuple[str, ...],
    size: int,
    passes: tuple[str, ...],
    poses: tuple[tuple[str, int], ...],
    materials_root: Path | None,
    vtfcmd: Path | None,
    texture_cache: Path,
    *,
    fit=None,
) -> tuple[list[dict], dict[str, dict[str, dict]], tuple, dict]:
    _clear_scene()
    _setup_scene(size, transparent=True)
    cam_obj = _ensure_camera()
    for src_path in src_paths:
        _import_source(src_path)
    objs = _get_mesh_objects()
    if not objs:
        raise RuntimeError(f"No mesh objects found for {label}: {src_paths}")
    source_materials = {
        (obj.name, index): material.name if material else ""
        for obj in objs
        if hasattr(obj.data, "materials")
        for index, material in enumerate(obj.data.materials)
    }
    snapshots = {
        pose_name: _capture_regions(objs, frame) for pose_name, frame in poses
    }
    bbox = _bbox_payload(objs)
    center, ortho_scale, dist = _fit_camera(objs, cam_obj, fit=fit)
    cam_obj.data.ortho_scale = ortho_scale
    _setup_lights(center, ortho_scale)
    entries = []
    root.mkdir(parents=True, exist_ok=True)
    for render_pass in passes:
        if render_pass == "clay":
            _apply_clay_material(objs)
            texture_missing = False
        else:
            texture_missing = _apply_textured_materials(
                objs, source_materials, materials_root, vtfcmd, texture_cache
            )
        for pose_name, frame in poses:
            bpy.context.scene.frame_set(frame)
            for angle in angles:
                direction = ANGLE_DIRS[angle]
                _set_camera_pose(cam_obj, center, direction, dist)
                image_path = root / render_pass / pose_name / f"{angle}.png"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                bpy.context.scene.render.filepath = str(image_path)
                bpy.ops.render.render(write_still=True)
                entries.append(
                    _render_entry(
                        root,
                        render_pass,
                        pose_name,
                        angle,
                        image_path,
                        texture_missing=texture_missing,
                    )
                )
    return entries, snapshots, (center, ortho_scale, dist), bbox


def _run_extended(args, before: list[Path], after: list[Path], out_dir: Path, angles: list[str]) -> None:
    unknown_angles = sorted(set(angles) - set(ANGLE_DIRS))
    if unknown_angles:
        raise SystemExit(f"[ERROR] Unknown angles: {', '.join(unknown_angles)}")
    try:
        passes = _validated_passes(args.passes)
        poses = _parse_poses(args.poses)
    except ValueError as exc:
        raise SystemExit(f"[ERROR] {exc}") from exc
    materials_root = Path(args.materials_root).resolve() if args.materials_root else None
    vtfcmd = Path(args.vtfcmd).resolve() if args.vtfcmd else None
    texture_cache = out_dir / ".vtf-cache"
    original_dir = out_dir / "original"
    candidate_dir = out_dir / "optimized"
    reference_entries, reference_snapshots, fit, bbox = _render_extended_set(
        "before",
        before,
        original_dir,
        tuple(angles),
        args.size,
        passes,
        poses,
        materials_root,
        vtfcmd,
        texture_cache,
    )
    candidate_entries, candidate_snapshots, _, candidate_bbox = _render_extended_set(
        "after",
        after,
        candidate_dir,
        tuple(angles),
        args.size,
        passes,
        poses,
        materials_root,
        vtfcmd,
        texture_cache,
        fit=fit,
    )
    stride = 7
    seed = 0
    diagonal = max(float(bbox["diagonal"]), 1e-12)
    geometry = _geometry_entries(reference_snapshots, candidate_snapshots, diagonal, stride)
    _write_render_manifest(
        original_dir, reference_entries, [], bbox, stride=stride, seed=seed
    )
    manifest_path = _write_render_manifest(
        candidate_dir,
        candidate_entries,
        geometry,
        candidate_bbox,
        stride=stride,
        seed=seed,
    )
    print(f"[OK] Render manifest: {manifest_path}")


def main():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = argv[1:]

    args = _parse_args(argv)
    if bpy is None:
        raise SystemExit("[ERROR] render_previews.py must be executed by Blender.")
    _ensure_source_tools()
    before = [Path(p).resolve() for p in _expand_paths(args.before)]
    after = [Path(p).resolve() for p in _expand_paths(args.after)]
    out_dir = Path(args.out).resolve()
    angles = [a.strip() for a in args.angles.split(",") if a.strip()]

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

    if _is_extended_mode(args):
        _run_extended(args, before, after, out_dir, angles)
        return

    original_dir = out_dir / "original"
    optimized_dir = out_dir / "optimized"

    before_tris, fit = _render_set("before", before, original_dir, angles, args.size, fit=None)
    after_tris, _ = _render_set("after", after, optimized_dir, angles, args.size, fit=fit)

    before_files = [str(p) for p in before]
    after_files = [str(p) for p in after]
    summary = {
        "angles": angles,
        "size": args.size,
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
