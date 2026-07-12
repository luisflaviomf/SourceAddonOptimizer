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
from pathlib import PurePosixPath, PureWindowsPath

_SCRIPT_ROOT = Path(__file__).resolve().parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from maximum_optimizer.regions import (
    RegionManifest,
    load_region_manifest_payload,
    normalized_source_identity as _normalized_source_identity,
    resolve_region_assignments as _resolve_region_assignments,
    source_material_slot_identities as _source_material_slot_identities,
)

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


def _eevee_engine(
    version: tuple[int, ...], *, available: tuple[str, ...] | None = None
) -> str:
    preferred = "BLENDER_EEVEE_NEXT" if version >= (4, 2) else "BLENDER_EEVEE"
    if available is None or preferred in available:
        return preferred
    if "BLENDER_EEVEE" in available:
        return "BLENDER_EEVEE"
    raise ValueError(f"no supported EEVEE engine is available: {available!r}")


def _available_enum_identifiers(owner, property_name: str) -> tuple[str, ...]:
    return tuple(
        item.identifier
        for item in owner.bl_rna.properties[property_name].enum_items
    )


def _barycentric_weights(point, first, second, third) -> tuple[float, float, float]:
    v0 = tuple(second[index] - first[index] for index in range(3))
    v1 = tuple(third[index] - first[index] for index in range(3))
    v2 = tuple(point[index] - first[index] for index in range(3))
    d00 = sum(value * value for value in v0)
    d01 = sum(v0[index] * v1[index] for index in range(3))
    d11 = sum(value * value for value in v1)
    d20 = sum(v2[index] * v0[index] for index in range(3))
    d21 = sum(v2[index] * v1[index] for index in range(3))
    denominator = d00 * d11 - d01 * d01
    if abs(denominator) <= 1e-20:
        raise ValueError("degenerate triangle")
    second_weight = (d11 * d20 - d01 * d21) / denominator
    third_weight = (d00 * d21 - d01 * d20) / denominator
    first_weight = 1.0 - second_weight - third_weight
    return first_weight, second_weight, third_weight


def _interpolate_attribute(values, weights, *, normalize: bool = False) -> tuple[float, ...]:
    result = tuple(
        sum(weights[item] * values[item][component] for item in range(3))
        for component in range(len(values[0]))
    )
    if normalize:
        length = math.sqrt(sum(value * value for value in result))
        if length > 0:
            result = tuple(value / length for value in result)
    return result


def _directional_p95_max(forward: list[float], reverse: list[float]) -> float:
    return max(_percentile(forward, 0.95), _percentile(reverse, 0.95))


def _contained_material_path(root: Path, raw: str, suffix: str) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    normalized = raw.replace("\\", "/").strip()
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(raw)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
    ):
        return None
    root = Path(root)
    if root.is_symlink():
        return None
    relative = Path(*posix.parts)
    if suffix and not str(relative).casefold().endswith(suffix.casefold()):
        relative = Path(f"{relative}{suffix}")
    unresolved = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return None
    resolved_root = root.resolve()
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved


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
    ap.add_argument(
        "--region-manifest", default=None,
        help="Required shared Maximum region manifest for extended validation",
    )
    return ap.parse_args(argv)


def _load_region_manifest(path: Path) -> RegionManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read region manifest: {path}") from exc
    return load_region_manifest_payload(payload)


def _smd_material_names(path: Path) -> tuple[str, ...]:
    path = Path(path)
    if path.suffix.casefold() != ".smd":
        raise ValueError(f"Maximum material identity requires SMD source evidence: {path}")
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read Source material evidence: {path}") from exc
    in_triangles = False
    materials: list[str] = []
    vertex_rows_remaining = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not in_triangles:
            if line.casefold() == "triangles":
                in_triangles = True
            continue
        if line.casefold() == "end":
            break
        if vertex_rows_remaining:
            vertex_rows_remaining -= 1
            continue
        if not line:
            continue
        if line not in materials:
            materials.append(line)
        vertex_rows_remaining = 3
    if not materials or vertex_rows_remaining:
        raise ValueError(f"SMD material evidence is missing or truncated: {path}")
    return tuple(materials)


def _required_region_manifest(args) -> Path:
    if not args.region_manifest:
        raise ValueError("extended Maximum validation requires an explicit region manifest")
    path = Path(args.region_manifest).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"region manifest does not exist: {path}")
    return path


def _parse_csv(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _validated_passes(raw: str | None) -> tuple[str, ...]:
    passes = _parse_csv(raw or "textured,clay")
    if passes != ("textured", "clay"):
        raise ValueError("render pass list must be exactly textured,clay")
    return passes


def _validated_angles(raw: str | None) -> tuple[str, ...]:
    angles = _parse_csv(raw)
    expected = tuple(ANGLE_DIRS)
    if angles != expected:
        raise ValueError(f"render angle list must be exactly {','.join(expected)}")
    return angles


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
    if "bind" not in {name for name, _ in poses}:
        raise ValueError("pose list must include bind")
    return tuple(poses)


def _is_extended_mode(args) -> bool:
    return any(
        value is not None
        for value in (args.passes, args.poses, args.materials_root, args.vtfcmd, args.region_manifest)
    )


def _extract_base_texture(vmt_text: str) -> str | None:
    match = re.search(
        r'(?im)^\s*"?\$basetexture"?\s+"?([^"\s}]+)',
        vmt_text,
    )
    if match is None:
        return None
    value = match.group(1).replace("\\", "/").strip()
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
    expected: dict,
    stride: int,
    seed: int,
) -> Path:
    manifest = {
        "schema": 1,
        "expected": expected,
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
    available_engines = _available_enum_identifiers(scene.render, "engine")
    scene.render.engine = _eevee_engine(
        tuple(bpy.app.version), available=available_engines
    )
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
    relative = material_name.replace("\\", "/").strip()
    if relative.casefold().endswith(".vmt"):
        relative = relative[:-4]
    vmt_path = _contained_material_path(materials_root, relative, ".vmt")
    if vmt_path is None or not vmt_path.is_file():
        return None
    try:
        base_texture = _extract_base_texture(vmt_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if base_texture is None:
        return None
    vtf_path = _contained_material_path(materials_root, base_texture, ".vtf")
    if vtf_path is None:
        return None
    return _convert_vtf(vtf_path, vtfcmd, cache_root)


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


def _region_source_identity(obj) -> str:
    try:
        value = obj.get("maximum_region_source_identity")
    except AttributeError:
        value = getattr(obj, "maximum_region_source_identity", None)
    if not isinstance(value, str) or not value:
        raise ValueError(f"mesh object has no region source identity: {obj.name}")
    return value


def _capture_regions(
    objs,
    frame: int,
    region_manifest: RegionManifest,
    source_material_evidence: dict[str, tuple[str, ...]],
) -> dict[str, dict]:
    bpy.context.scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    observations = tuple(
        (
            _region_source_identity(obj),
            obj.name,
            _source_material_slot_identities(
                _region_source_identity(obj),
                source_material_evidence[_region_source_identity(obj)],
                tuple(
                    material.name if material else "none"
                    for material in getattr(obj.data, "materials", ())
                ),
            ),
        )
        for obj in objs
    )
    assignments = _resolve_region_assignments(region_manifest, observations)
    regions = {}
    for obj, observation in sorted(
        zip(objs, observations), key=lambda item: assignments[item[1]]
    ):
        region_key = assignments[observation]
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        if mesh is None:
            continue
        try:
            mesh.calc_loop_triangles()
            matrix = evaluated.matrix_world.copy()
            normal_matrix = matrix.to_3x3().inverted().transposed()
            uv_layer = mesh.uv_layers.active
            triangles = []
            for triangle in mesh.loop_triangles:
                positions = []
                normals = []
                uvs = []
                for loop_index in triangle.loops:
                    loop = mesh.loops[loop_index]
                    vertex = mesh.vertices[loop.vertex_index]
                    positions.append(matrix @ vertex.co.copy())
                    normals.append((normal_matrix @ loop.normal).normalized())
                    if uv_layer is None:
                        uvs.append((0.0, 0.0))
                    else:
                        uv = uv_layer.data[loop_index].uv
                        uvs.append((float(uv.x), float(uv.y)))
                triangles.append(
                    {
                        "positions": tuple(positions),
                        "normals": tuple(normals),
                        "uvs": tuple(uvs),
                    }
                )
            regions[region_key] = {
                "scope": region_key,
                "source_object": obj.name,
                "triangles": triangles,
            }
        finally:
            evaluated.to_mesh_clear()
    return regions


def _flatten_region(region: dict):
    positions = [
        position
        for triangle in region["triangles"]
        for position in triangle["positions"]
    ]
    polygons = [
        (index * 3, index * 3 + 1, index * 3 + 2)
        for index in range(len(region["triangles"]))
    ]
    return positions, polygons


def _direct_topology_metrics(
    reference: dict, candidate: dict, diagonal: float, stride: int
) -> dict | None:
    if len(reference["triangles"]) != len(candidate["triangles"]):
        return None
    paired_loops = []
    for reference_triangle, candidate_triangle in zip(
        reference["triangles"], candidate["triangles"]
    ):
        if tuple(map(tuple, reference_triangle["positions"])) != tuple(
            map(tuple, candidate_triangle["positions"])
        ):
            return None
        paired_loops.extend(
            zip(
                reference_triangle["normals"],
                candidate_triangle["normals"],
                reference_triangle["uvs"],
                candidate_triangle["uvs"],
            )
        )
    normal_angles = []
    uv_errors = []
    for index in range(0, len(paired_loops), stride):
        reference_normal, candidate_normal, reference_uv, candidate_uv = paired_loops[index]
        reference_values = tuple(reference_normal)
        candidate_values = tuple(candidate_normal)
        if reference_values == candidate_values:
            normal_angles.append(0.0)
            uv_errors.append(math.dist(reference_uv, candidate_uv))
            continue
        reference_length = math.sqrt(sum(value * value for value in reference_values))
        candidate_length = math.sqrt(sum(value * value for value in candidate_values))
        if reference_length == 0 or candidate_length == 0:
            normal_angles.append(180.0)
        else:
            dot = sum(
                reference_values[component] * candidate_values[component]
                for component in range(3)
            ) / (reference_length * candidate_length)
            normal_angles.append(math.degrees(math.acos(max(-1.0, min(1.0, dot)))))
        uv_errors.append(math.dist(reference_uv, candidate_uv))
    return {
        "surface_bidirectional_p95": 0.0,
        "surface_max": 0.0,
        "normal_angle_p95": _percentile(normal_angles, 0.95),
        "uv_error_p95": _percentile(uv_errors, 0.95),
        "skinning_error_p95": 0.0,
        "region_missing": False,
    }


def _geometry_metrics_for_region(reference: dict, candidate: dict, diagonal: float, stride: int) -> dict:
    if not reference["triangles"] or not candidate["triangles"]:
        return {
            "surface_bidirectional_p95": 1.0,
            "surface_max": 1.0,
            "normal_angle_p95": 180.0,
            "uv_error_p95": 1.0,
            "skinning_error_p95": 1.0,
            "region_missing": True,
        }
    direct = _direct_topology_metrics(reference, candidate, diagonal, stride)
    if direct is not None:
        return direct
    from mathutils.bvhtree import BVHTree
    reference_positions, reference_polygons = _flatten_region(reference)
    candidate_positions, candidate_polygons = _flatten_region(candidate)
    ref_bvh = BVHTree.FromPolygons(reference_positions, reference_polygons, all_triangles=True)
    candidate_bvh = BVHTree.FromPolygons(candidate_positions, candidate_polygons, all_triangles=True)

    def sample(source: dict, target: dict, target_bvh):
        distances: list[float] = []
        normal_angles: list[float] = []
        uv_errors: list[float] = []
        source_samples = [
            (position, triangle["normals"][loop], triangle["uvs"][loop])
            for triangle in source["triangles"]
            for loop, position in enumerate(triangle["positions"])
        ]
        for index in range(0, len(source_samples), stride):
            source_position, source_normal, source_uv = source_samples[index]
            nearest = target_bvh.find_nearest(source_position)
            if nearest is None:
                distances.append(diagonal)
                normal_angles.append(180.0)
                uv_errors.append(1.0)
                continue
            location, _, polygon_index, distance = nearest
            distances.append(float(distance) / diagonal)
            target_triangle = target["triangles"][polygon_index]
            weights = _barycentric_weights(
                tuple(location), *(tuple(value) for value in target_triangle["positions"])
            )
            target_normal = Vector(
                _interpolate_attribute(target_triangle["normals"], weights, normalize=True)
            )
            dot = max(-1.0, min(1.0, source_normal.dot(target_normal)))
            normal_angles.append(math.degrees(math.acos(dot)))
            target_uv = _interpolate_attribute(target_triangle["uvs"], weights)
            uv_errors.append(math.dist(source_uv, target_uv))
        return distances, normal_angles, uv_errors

    forward_distance, forward_normal, forward_uv = sample(candidate, reference, ref_bvh)
    reverse_distance, reverse_normal, reverse_uv = sample(reference, candidate, candidate_bvh)
    return {
        "surface_bidirectional_p95": _directional_p95_max(
            forward_distance, reverse_distance
        ),
        "surface_max": max((*forward_distance, *reverse_distance), default=0.0),
        "normal_angle_p95": _directional_p95_max(forward_normal, reverse_normal),
        "uv_error_p95": _directional_p95_max(forward_uv, reverse_uv),
        "skinning_error_p95": 0.0,
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
    reference_positions, reference_polygons = _flatten_region(reference_bind)
    candidate_positions, candidate_polygons = _flatten_region(candidate_bind)
    reference_bvh = BVHTree.FromPolygons(
        reference_positions, reference_polygons, all_triangles=True
    )
    candidate_bvh = BVHTree.FromPolygons(
        candidate_positions, candidate_polygons, all_triangles=True
    )

    def sample(source_bind: dict, source_pose: dict, target_bind: dict, target_pose: dict, target_bvh):
        errors = []
        source_bind_positions, _ = _flatten_region(source_bind)
        source_pose_positions, _ = _flatten_region(source_pose)
        for index in range(0, len(source_bind_positions), stride):
            if index >= len(source_pose_positions):
                errors.append(1.0)
                continue
            nearest = target_bvh.find_nearest(source_bind_positions[index])
            if nearest is None:
                errors.append(1.0)
                continue
            location, _, polygon_index, _ = nearest
            target_bind_triangle = target_bind["triangles"][polygon_index]
            if polygon_index >= len(target_pose["triangles"]):
                errors.append(1.0)
                continue
            weights = _barycentric_weights(
                tuple(location),
                *(tuple(value) for value in target_bind_triangle["positions"]),
            )
            target_bind_position = Vector(
                _interpolate_attribute(target_bind_triangle["positions"], weights)
            )
            target_pose_position = Vector(
                _interpolate_attribute(
                    target_pose["triangles"][polygon_index]["positions"], weights
                )
            )
            source_displacement = source_pose_positions[index] - source_bind_positions[index]
            target_displacement = target_pose_position - target_bind_position
            errors.append((source_displacement - target_displacement).length / diagonal)
        return errors

    forward = sample(
        candidate_bind, candidate_pose, reference_bind, reference_pose, reference_bvh
    )
    reverse = sample(
        reference_bind, reference_pose, candidate_bind, candidate_pose, candidate_bvh
    )
    return _directional_p95_max(forward, reverse)


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


def _reference_geometry_entries(
    snapshots: dict[str, dict[str, dict]],
    regions: tuple[str, ...],
    poses: tuple[str, ...],
) -> list[dict]:
    entries = []
    for region in regions:
        for pose in poses:
            snapshot = snapshots.get(pose, {}).get(region)
            missing = snapshot is None or not snapshot.get("triangles")
            entries.append(
                {
                    "scope": region,
                    "pose": pose,
                    "surface_bidirectional_p95": 1.0 if missing else 0.0,
                    "surface_max": 1.0 if missing else 0.0,
                    "normal_angle_p95": 180.0 if missing else 0.0,
                    "uv_error_p95": 1.0 if missing else 0.0,
                    "skinning_error_p95": 1.0 if missing else 0.0,
                    "region_missing": missing,
                }
            )
    return entries


def _bbox_payload(objs) -> dict:
    min_v, max_v = _compute_bbox(objs)
    diagonal = (max_v - min_v).length
    return {
        "min": [float(min_v.x), float(min_v.y), float(min_v.z)],
        "max": [float(max_v.x), float(max_v.y), float(max_v.z)],
        "diagonal": float(diagonal),
    }


def _capture_pose_snapshots(
    objs,
    poses: tuple[tuple[str, int], ...],
    *,
    scene=None,
    capture=None,
) -> dict[str, dict[str, dict]]:
    scene = scene or bpy.context.scene
    capture = capture or _capture_regions
    original_frame = scene.frame_current
    try:
        return {pose_name: capture(objs, frame) for pose_name, frame in poses}
    finally:
        scene.frame_set(original_frame)


def _framing_from_snapshots(snapshots: dict[str, dict[str, dict]]) -> tuple[dict, tuple]:
    positions = [
        tuple(float(component) for component in position)
        for pose_regions in snapshots.values()
        for region in pose_regions.values()
        for triangle in region.get("triangles", ())
        for position in triangle["positions"]
    ]
    if not positions:
        raise ValueError("cannot frame render set without evaluated triangle vertices")
    minimum = [min(position[axis] for position in positions) for axis in range(3)]
    maximum = [max(position[axis] for position in positions) for axis in range(3)]
    extents = [maximum[axis] - minimum[axis] for axis in range(3)]
    diagonal = math.dist(minimum, maximum)
    center = Vector(
        tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
    )
    max_dimension = max(extents)
    ortho_scale = max_dimension * 1.4 if max_dimension > 0 else 1.0
    distance = max_dimension * 2.5 + 1.0
    bbox = {"min": minimum, "max": maximum, "diagonal": diagonal}
    return bbox, (center, ortho_scale, distance)


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
    region_manifest: RegionManifest,
    source_identities: tuple[str, ...],
    source_material_evidence: dict[str, tuple[str, ...]],
    *,
    fit=None,
) -> tuple[list[dict], dict[str, dict[str, dict]], tuple, dict]:
    _clear_scene()
    _setup_scene(size, transparent=True)
    cam_obj = _ensure_camera()
    if len(src_paths) != len(source_identities):
        raise ValueError("render source/region identity counts do not match")
    def object_identity(obj) -> int:
        return int(obj.as_pointer()) if hasattr(obj, "as_pointer") else id(obj)

    for src_path, source_identity in zip(src_paths, source_identities):
        before_ids = {object_identity(obj) for obj in bpy.context.scene.objects}
        _import_source(src_path)
        imported = tuple(
            obj for obj in bpy.context.scene.objects
            if object_identity(obj) not in before_ids and obj.type == "MESH"
        )
        if not imported:
            raise RuntimeError(f"No mesh objects imported for region source: {src_path}")
        for obj in imported:
            obj["maximum_region_source_identity"] = source_identity
    objs = _get_mesh_objects()
    if not objs:
        raise RuntimeError(f"No mesh objects found for {label}: {src_paths}")
    blender_source_materials = {
        (obj.name, index): material.name if material else ""
        for obj in objs
        if hasattr(obj.data, "materials")
        for index, material in enumerate(obj.data.materials)
    }
    snapshots = _capture_pose_snapshots(
        objs,
        poses,
        capture=lambda captured_objects, frame: _capture_regions(
            captured_objects, frame, region_manifest, source_material_evidence
        ),
    )
    bbox, evaluated_fit = _framing_from_snapshots(snapshots)
    center, ortho_scale, dist = fit or evaluated_fit
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
                objs, blender_source_materials, materials_root, vtfcmd, texture_cache
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
    try:
        passes = _validated_passes(args.passes)
        validated_angles = _validated_angles(",".join(angles))
        poses = _parse_poses(args.poses)
    except ValueError as exc:
        raise SystemExit(f"[ERROR] {exc}") from exc
    materials_root = Path(args.materials_root).resolve() if args.materials_root else None
    vtfcmd = Path(args.vtfcmd).resolve() if args.vtfcmd else None
    texture_cache = out_dir / ".vtf-cache"
    region_manifest_path = _required_region_manifest(args)
    region_manifest = _load_region_manifest(region_manifest_path)
    if len(before) != len(after):
        raise ValueError("extended Maximum validation requires paired before/after sources")
    manifest_sources = {
        entry.descriptor.source_identity for entry in region_manifest.entries
    }
    source_identities = []
    source_materials: dict[str, tuple[str, ...]] = {}
    for source in before:
        try:
            relative = source.resolve(strict=True).relative_to(region_manifest_path.parent.resolve(strict=True))
        except ValueError as exc:
            raise ValueError(f"render source is outside region manifest root: {source}") from exc
        identity = _normalized_source_identity(relative.as_posix())
        if identity not in manifest_sources:
            raise ValueError(f"render source is missing from region manifest: {identity}")
        source_identities.append(identity)
        source_materials[identity] = _smd_material_names(source)
    source_identities_tuple = tuple(source_identities)
    original_dir = out_dir / "original"
    candidate_dir = out_dir / "optimized"
    reference_entries, reference_snapshots, fit, bbox = _render_extended_set(
        "before",
        before,
        original_dir,
        validated_angles,
        args.size,
        passes,
        poses,
        materials_root,
        vtfcmd,
        texture_cache,
        region_manifest,
        source_identities_tuple,
        source_materials,
    )
    candidate_entries, candidate_snapshots, _, candidate_bbox = _render_extended_set(
        "after",
        after,
        candidate_dir,
        validated_angles,
        args.size,
        passes,
        poses,
        materials_root,
        vtfcmd,
        texture_cache,
        region_manifest,
        source_identities_tuple,
        source_materials,
        fit=fit,
    )
    stride = 7
    seed = 0
    diagonal = max(float(bbox["diagonal"]), 1e-12)
    geometry = _geometry_entries(reference_snapshots, candidate_snapshots, diagonal, stride)
    pose_names = tuple(name for name, _ in poses)
    regions = tuple(sorted(reference_snapshots["bind"]))
    expected = {
        "passes": list(passes),
        "angles": list(validated_angles),
        "poses": list(pose_names),
        "pose_frames": {name: frame for name, frame in poses},
        "regions": list(regions),
    }
    reference_geometry = _reference_geometry_entries(
        reference_snapshots, regions, pose_names
    )
    _write_render_manifest(
        original_dir,
        reference_entries,
        reference_geometry,
        bbox,
        expected=expected,
        stride=stride,
        seed=seed,
    )
    manifest_path = _write_render_manifest(
        candidate_dir,
        candidate_entries,
        geometry,
        candidate_bbox,
        expected=expected,
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
