#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import math
import os
import re
import sys
from pathlib import Path

import bmesh
from mathutils import kdtree


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import batch_optimize_qc as base
import batch_optimize_round_parts_policy as rp
import optimize_edge_transfer_policy_v1 as novel


PARTITION_STATS = {
    "qcs_total": 0,
    "qcs_viewmodel": 0,
    "qcs_worldmodel": 0,
    "qcs_ground_main": 0,
    "qcs_aircraft": 0,
    "qcs_detail": 0,
    "qcs_misc": 0,
    "objects_seen": 0,
    "objects_shell": 0,
    "objects_shell_reflective": 0,
    "objects_glass": 0,
    "objects_wheel": 0,
    "objects_interior": 0,
    "objects_chassis": 0,
    "objects_misc": 0,
    "objects_protected": 0,
    "protected_vertices": 0,
    "objects_transfer_ok": 0,
    "objects_transfer_fail": 0,
    "objects_weighted_ok": 0,
    "objects_weighted_fail": 0,
    "objects_limited_dissolve_ok": 0,
    "objects_limited_dissolve_fail": 0,
    "objects_planar_ok": 0,
    "objects_planar_fail": 0,
    "objects_symmetry_ok": 0,
    "objects_symmetry_fail": 0,
    "objects_symmetry_axis_x": 0,
    "objects_symmetry_axis_y": 0,
    "objects_symmetry_axis_z": 0,
}

CURRENT_PROFILE: dict[str, object] = {
    "qc_class": "misc_safe",
}

REFLECTIVE_TOKENS = (
    "glass",
    "chrome",
    "window",
    "windscreen",
    "windshield",
    "mirror",
    "headlight",
    "taillight",
    "taillamp",
    "lamp",
    "light",
    "paint",
    "skin",
    "body",
    "carmaterials",
    "vehiclegeneric",
)

SHELL_TOKENS = (
    "body",
    "hood",
    "bonnet",
    "trunk",
    "boot",
    "door",
    "fender",
    "quarter",
    "roof",
    "panel",
    "bumper",
    "wing",
    "skirt",
    "spoiler",
    "splitter",
    "canard",
    "diffuser",
    "flare",
    "lip",
    "mirror",
    "grille",
    "grill",
    "vent",
    "scoop",
    "cover",
)

GLASS_TOKENS = (
    "glass",
    "window",
    "windscreen",
    "windshield",
    "mirror",
    "headlight",
    "taillight",
    "taillamp",
    "fog",
    "lamp",
    "light",
)

INTERIOR_TOKENS = (
    "interior",
    "cockpit",
    "dash",
    "dashboard",
    "seat",
    "console",
    "carpet",
    "ceiling",
    "gauge",
    "cluster",
    "speedo",
    "tach",
    "steering",
    "pedal",
    "shifter",
    "lever",
)

NAME_PRIORITY_INTERIOR_PREFIX_TOKENS = (
    "int",
)

CHASSIS_TOKENS = (
    "engine",
    "chassis",
    "frame",
    "suspension",
    "axle",
    "brake",
    "exhaust",
    "driveshaft",
    "gearbox",
    "transmission",
    "radiator",
    "cooler",
    "subframe",
    "under",
    "floorpan",
)

WHEEL_TOKENS = (
    "wheel",
    "rim",
    "tire",
    "tyre",
    "wh",
    "fw",
    "rw",
)

AIRCRAFT_TOKENS = (
    "plane",
    "aircraft",
    "jet",
    "heli",
    "helicopter",
    "wing",
    "fuselage",
    "cockpit",
)

CLASS_ENV_SUFFIX = {
    "viewmodel_sensitive": "VIEWMODEL",
    "weapon_worldmodel": "WORLDMODEL",
    "ground_main": "GROUND",
    "aircraft_sensitive": "AIRCRAFT",
    "detail_sensitive": "DETAIL",
    "misc_safe": "MISC",
}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return str(value).replace("\\", "/").strip().lower()


def _tokenize(text: str) -> set[str]:
    parts = re.split(r"[^a-z0-9]+", _normalize_text(text))
    return {part for part in parts if part}


def _has_any_token(blob: str, tokens: tuple[str, ...]) -> bool:
    normalized = _normalize_text(blob)
    tokenized = _tokenize(normalized)
    for token in tokens:
        if token in tokenized or token in normalized:
            return True
    return False


def _rule_blob(rule: dict[str, object], qc_rel: str) -> str:
    parts = [
        qc_rel,
        str(rule.get("model_rel") or ""),
        str(rule.get("reason") or ""),
        str(rule.get("group") or ""),
        str(rule.get("base_group") or ""),
    ]
    return " | ".join(_normalize_text(part) for part in parts if part)


def _looks_like_viewmodel(rule: dict[str, object], qc_rel: str) -> bool:
    blob = _rule_blob(rule, qc_rel)
    if not blob:
        return False
    if "/viewmodels/" in blob or "/hands/" in blob or "viewmodel" in blob:
        return True
    if re.search(r"(^|/)(v_|c_)", blob):
        return True
    if "/weapons/" in blob and re.search(r"/v_[^/]+(?:\.mdl|\.qc)?", blob):
        return True
    return False


def _looks_like_weapon_worldmodel(rule: dict[str, object], qc_rel: str) -> bool:
    blob = _rule_blob(rule, qc_rel)
    if "weapons/" not in blob:
        return False
    return bool(re.search(r"/w_[^/]+(?:\.mdl|\.qc)?", blob))


def _looks_like_aircraft(rule: dict[str, object], qc_rel: str) -> bool:
    return _has_any_token(_rule_blob(rule, qc_rel), AIRCRAFT_TOKENS)


def _group_name(rule: dict[str, object]) -> str:
    return _normalize_text(str(rule.get("group") or "baseline_other"))


def _classify_qc(rule: dict[str, object], qc_rel: str) -> str:
    group = _group_name(rule)
    if _looks_like_viewmodel(rule, qc_rel):
        return "viewmodel_sensitive"
    if _looks_like_weapon_worldmodel(rule, qc_rel):
        return "weapon_worldmodel"
    if group == "experimental_ground_main":
        return "ground_main"
    if group == "baseline_aircraft" or _looks_like_aircraft(rule, qc_rel):
        return "aircraft_sensitive"
    if group in {"baseline_wheel", "baseline_rotor", "baseline_attachment", "baseline_detached", "baseline_uncertain_main"}:
        return "detail_sensitive"
    return "misc_safe"


def _set_stat(name: str, delta: int = 1) -> None:
    PARTITION_STATS[name] = int(PARTITION_STATS.get(name, 0)) + int(delta)


def _build_local_cfg(cfg, rule: dict[str, object], qc_rel: str):
    local_cfg = copy.copy(cfg)
    notes: list[str] = []
    qc_class = _classify_qc(rule, qc_rel)

    if qc_class == "viewmodel_sensitive":
        local_cfg.ratio = max(float(local_cfg.ratio), float(_env_float("GMO_EXP_FIP_VIEWMODEL_RATIO", 0.80)))
        local_cfg.autosmooth = max(float(local_cfg.autosmooth), float(_env_float("GMO_EXP_FIP_VIEWMODEL_AUTOSMOOTH", 35.0)))
        if hasattr(local_cfg, "use_planar"):
            local_cfg.use_planar = False
        _set_stat("qcs_viewmodel")
        notes.append(f"viewmodel_guard ratio={float(local_cfg.ratio):.2f} autosmooth={float(local_cfg.autosmooth):.1f} use_planar=OFF")
    elif qc_class == "weapon_worldmodel":
        local_cfg.ratio = min(float(local_cfg.ratio), float(_env_float("GMO_EXP_FIP_WORLDMODEL_RATIO", 0.55)))
        local_cfg.autosmooth = max(float(local_cfg.autosmooth), float(_env_float("GMO_EXP_FIP_WORLDMODEL_AUTOSMOOTH", 35.0)))
        if hasattr(local_cfg, "use_planar"):
            local_cfg.use_planar = False
        _set_stat("qcs_worldmodel")
        notes.append(f"worldmodel_partition ratio={float(local_cfg.ratio):.2f} autosmooth={float(local_cfg.autosmooth):.1f} use_planar=OFF")
    elif qc_class == "ground_main":
        local_cfg.ratio = min(float(local_cfg.ratio), float(_env_float("GMO_EXP_FIP_GROUND_BASE_RATIO", 0.60)))
        local_cfg.autosmooth = max(float(local_cfg.autosmooth), float(_env_float("GMO_EXP_FIP_GROUND_AUTOSMOOTH", 35.0)))
        if hasattr(local_cfg, "use_planar"):
            local_cfg.use_planar = False
        _set_stat("qcs_ground_main")
        notes.append(f"ground_partition ratio={float(local_cfg.ratio):.2f} autosmooth={float(local_cfg.autosmooth):.1f} use_planar=OFF")
    elif qc_class == "aircraft_sensitive":
        local_cfg.ratio = max(float(local_cfg.ratio), float(_env_float("GMO_EXP_FIP_AIRCRAFT_RATIO", 0.72)))
        local_cfg.autosmooth = max(float(local_cfg.autosmooth), float(_env_float("GMO_EXP_FIP_AIRCRAFT_AUTOSMOOTH", 35.0)))
        if hasattr(local_cfg, "use_planar"):
            local_cfg.use_planar = False
        _set_stat("qcs_aircraft")
        notes.append(f"aircraft_guard ratio={float(local_cfg.ratio):.2f} autosmooth={float(local_cfg.autosmooth):.1f} use_planar=OFF")
    elif qc_class == "detail_sensitive":
        local_cfg.ratio = max(float(local_cfg.ratio), float(_env_float("GMO_EXP_FIP_DETAIL_RATIO", 0.72)))
        local_cfg.autosmooth = max(float(local_cfg.autosmooth), float(_env_float("GMO_EXP_FIP_DETAIL_AUTOSMOOTH", 35.0)))
        if hasattr(local_cfg, "use_planar"):
            local_cfg.use_planar = False
        _set_stat("qcs_detail")
        notes.append(f"detail_guard ratio={float(local_cfg.ratio):.2f} autosmooth={float(local_cfg.autosmooth):.1f} use_planar=OFF")
    else:
        local_cfg.ratio = min(float(local_cfg.ratio), float(_env_float("GMO_EXP_FIP_MISC_BASE_RATIO", 0.60)))
        local_cfg.autosmooth = max(float(local_cfg.autosmooth), float(_env_float("GMO_EXP_FIP_MISC_AUTOSMOOTH", float(local_cfg.autosmooth))))
        if hasattr(local_cfg, "use_planar"):
            local_cfg.use_planar = False
        _set_stat("qcs_misc")
        notes.append(f"misc_partition ratio={float(local_cfg.ratio):.2f} autosmooth={float(local_cfg.autosmooth):.1f} use_planar=OFF")

    _set_stat("qcs_total")
    return local_cfg, qc_class, notes


def _expand_vertices(bm: bmesh.types.BMesh, verts: set[int], rings: int) -> set[int]:
    if rings <= 0 or not verts:
        return set(verts)
    expanded = set(verts)
    frontier = set(verts)
    for _ in range(rings):
        nxt: set[int] = set()
        for vid in frontier:
            if vid >= len(bm.verts):
                continue
            for edge in bm.verts[vid].link_edges:
                for vert in edge.verts:
                    nxt.add(int(vert.index))
        frontier = nxt - expanded
        expanded.update(frontier)
        if not frontier:
            break
    return expanded


def _merge_vertex_weight(target: dict[int, float], vertex_id: int, weight: float) -> None:
    clipped = max(0.0, min(1.0, float(weight)))
    if clipped <= 0.0:
        return
    previous = target.get(int(vertex_id), 0.0)
    if clipped > previous:
        target[int(vertex_id)] = clipped


def _expand_vertex_weights(
    bm: bmesh.types.BMesh,
    vertex_weights: dict[int, float],
    rings: int,
) -> dict[int, float]:
    if rings <= 0 or not vertex_weights:
        return {int(vid): float(weight) for vid, weight in vertex_weights.items() if float(weight) > 0.0}

    decay = max(0.0, min(1.0, float(_env_float("GMO_EXP_FIP_PROTECT_RING_DECAY", 0.65))))
    min_weight = max(0.0, min(1.0, float(_env_float("GMO_EXP_FIP_PROTECT_MIN_WEIGHT", 0.20))))
    expanded = {int(vid): max(0.0, min(1.0, float(weight))) for vid, weight in vertex_weights.items() if float(weight) > 0.0}
    frontier = dict(expanded)

    for _ in range(rings):
        nxt: dict[int, float] = {}
        for vid, source_weight in frontier.items():
            if vid >= len(bm.verts):
                continue
            propagated = float(source_weight) * decay
            if propagated < min_weight:
                continue
            for edge in bm.verts[vid].link_edges:
                for vert in edge.verts:
                    _merge_vertex_weight(nxt, int(vert.index), propagated)
        if not nxt:
            break
        for vid, weight in nxt.items():
            _merge_vertex_weight(expanded, vid, weight)
        frontier = nxt

    return expanded


def _collect_protected_vertex_weights(obj) -> dict[int, float]:
    curvature_deg = _env_float("GMO_EXP_FIP_CURVATURE_DEG", 35.0)
    expand_rings = _env_int("GMO_EXP_FIP_EXPAND_RINGS", 1)

    mesh = getattr(obj, "data", None)
    if mesh is None or not getattr(mesh, "vertices", None):
        return set()

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        for idx, vert in enumerate(bm.verts):
            vert.index = idx

        protected: dict[int, float] = {}
        curvature_limit = math.radians(float(curvature_deg))

        for edge in bm.edges:
            edge_verts = [int(v.index) for v in edge.verts]
            protect_weight = 0.0
            if edge.is_boundary or not edge.is_manifold:
                protect_weight = max(protect_weight, float(_env_float("GMO_EXP_FIP_PROTECT_WEIGHT_BOUNDARY", 1.0)))
            if getattr(edge, "seam", False):
                protect_weight = max(protect_weight, float(_env_float("GMO_EXP_FIP_PROTECT_WEIGHT_SEAM", 1.0)))
            if hasattr(edge, "smooth") and not bool(edge.smooth):
                protect_weight = max(protect_weight, float(_env_float("GMO_EXP_FIP_PROTECT_WEIGHT_SHARP", 0.90)))
            if len(edge.link_faces) == 2:
                face_a, face_b = edge.link_faces
                if int(face_a.material_index) != int(face_b.material_index):
                    protect_weight = max(protect_weight, float(_env_float("GMO_EXP_FIP_PROTECT_WEIGHT_MATERIAL", 0.95)))
                else:
                    try:
                        if float(face_a.normal.angle(face_b.normal)) >= curvature_limit:
                            protect_weight = max(
                                protect_weight,
                                float(_env_float("GMO_EXP_FIP_PROTECT_WEIGHT_CURVATURE", 0.72)),
                            )
                    except Exception:
                        pass
            if protect_weight > 0.0:
                for vid in edge_verts:
                    _merge_vertex_weight(protected, vid, protect_weight)

        return _expand_vertex_weights(bm, protected, int(expand_rings))
    finally:
        bm.free()


def _collect_protected_vertices(obj) -> set[int]:
    return set(_collect_protected_vertex_weights(obj).keys())


def _assign_protect_group(obj, vertex_weights: dict[int, float] | set[int]) -> str | None:
    if not vertex_weights:
        return None
    name = "VG_DECIMATE_PROTECT"
    vg = obj.vertex_groups.get(name)
    if vg is None:
        vg = obj.vertex_groups.new(name=name)

    use_weighted_map = _env_bool("GMO_EXP_FIP_WEIGHTED_PROTECT_MAP", False)
    if isinstance(vertex_weights, dict) and use_weighted_map:
        for vid, weight in sorted(vertex_weights.items()):
            clipped = max(0.0, min(1.0, float(weight)))
            if clipped <= 0.0:
                continue
            vg.add([int(vid)], clipped, "REPLACE")
        return name

    if isinstance(vertex_weights, dict):
        vertex_ids = sorted(int(vid) for vid in vertex_weights.keys())
    else:
        vertex_ids = sorted(int(vid) for vid in vertex_weights)
    if not vertex_ids:
        return None
    vg.add(vertex_ids, 1.0, "REPLACE")
    return name


def _object_material_names(obj) -> list[str]:
    names: list[str] = []
    try:
        for slot in getattr(obj, "material_slots", []):
            material = getattr(slot, "material", None)
            if material and getattr(material, "name", None):
                names.append(str(material.name).lower())
    except Exception:
        pass
    return names


def _object_name_blob(obj) -> str:
    return str(getattr(obj, "name", "") or "").lower()


def _object_material_blob(obj) -> str:
    return " ".join(_object_material_names(obj))


def _object_surface_blob(obj) -> str:
    parts = [_object_name_blob(obj), _object_material_blob(obj)]
    return " ".join(part for part in parts if part)


def _object_structural_blob(obj) -> str:
    parts = [_object_name_blob(obj)]
    if _env_bool("GMO_EXP_FIP_STRUCTURAL_FROM_MATERIALS", True):
        parts.append(_object_material_blob(obj))
    return " ".join(part for part in parts if part)


def _object_is_reflective_like(obj) -> bool:
    return _has_any_token(_object_surface_blob(obj), REFLECTIVE_TOKENS)


def _name_has_semantic_token(name_blob: str, tokens: tuple[str, ...], *, prefix_tokens: tuple[str, ...] = ()) -> bool:
    normalized = _normalize_text(name_blob)
    if not normalized:
        return False
    tokenized = _tokenize(normalized)

    for token in tokens:
        if token in tokenized:
            return True
        if len(token) >= 4 and token in normalized:
            return True

    for prefix in prefix_tokens:
        if any(part.startswith(prefix) for part in tokenized):
            return True

    return False


def _current_qc_class() -> str:
    return str(CURRENT_PROFILE.get("qc_class") or "misc_safe")


def _scene_meshes() -> list[object]:
    return [o for o in base.bpy.context.scene.objects if o.type == "MESH" and not o.name.endswith("_SRC_REF")]


def _scene_total_triangles() -> int:
    total = 0
    for obj in _scene_meshes():
        try:
            total += len(obj.data.polygons)
        except Exception:
            pass
    return max(total, 1)


def _classify_object_profile(obj, total_scene_tris: int) -> str:
    qc_class = _current_qc_class()
    name_blob = _object_name_blob(obj)
    structural_blob = _object_structural_blob(obj)
    tri_count = 0
    try:
        tri_count = len(obj.data.polygons)
    except Exception:
        tri_count = 0
    tri_share = float(tri_count) / float(max(total_scene_tris, 1))
    reflective = _object_is_reflective_like(obj)

    def _default_profile() -> str:
        if _has_any_token(structural_blob, GLASS_TOKENS):
            return "glass_sensitive"
        if _has_any_token(structural_blob, WHEEL_TOKENS):
            return "wheel_visible"
        if _has_any_token(structural_blob, INTERIOR_TOKENS):
            return "interior_hidden"
        if _has_any_token(structural_blob, CHASSIS_TOKENS):
            return "chassis_hidden"

        if qc_class == "viewmodel_sensitive":
            if tri_share >= float(_env_float("GMO_EXP_FIP_VIEW_SURFACE_TRI_SHARE", 0.10)):
                return "view_surface"
            return "misc_safe"

        if _has_any_token(structural_blob, SHELL_TOKENS):
            return "shell_reflective" if reflective else "shell_structural"

        if reflective and tri_share >= float(_env_float("GMO_EXP_FIP_REFLECTIVE_TRI_SHARE", 0.08)):
            return "shell_reflective"

        if tri_share >= float(_env_float("GMO_EXP_FIP_LARGE_OBJECT_TRI_SHARE", 0.22)):
            return "shell_structural"

        return "misc_safe"

    default_profile = _default_profile()

    if _env_bool("GMO_EXP_FIP_NAME_PRIORITY_CLASSIFY", False):
        name_profile = ""
        if _name_has_semantic_token(name_blob, GLASS_TOKENS):
            name_profile = "glass_sensitive"
        elif _name_has_semantic_token(name_blob, WHEEL_TOKENS):
            name_profile = "wheel_visible"
        elif _name_has_semantic_token(
            name_blob,
            INTERIOR_TOKENS,
            prefix_tokens=NAME_PRIORITY_INTERIOR_PREFIX_TOKENS,
        ):
            name_profile = "interior_hidden"
        elif _name_has_semantic_token(name_blob, CHASSIS_TOKENS):
            name_profile = "chassis_hidden"
        elif _name_has_semantic_token(name_blob, SHELL_TOKENS):
            name_profile = "shell_reflective" if reflective else "shell_structural"

        if name_profile:
            if _env_bool("GMO_EXP_FIP_NAME_PRIORITY_NO_DEMOTION", False):
                protect_rank = {
                    "glass_sensitive": 5,
                    "wheel_visible": 4,
                    "view_surface": 4,
                    "shell_reflective": 3,
                    "shell_structural": 2,
                    "interior_hidden": 1,
                    "chassis_hidden": 0,
                    "misc_safe": -1,
                }
                if protect_rank.get(name_profile, -1) < protect_rank.get(default_profile, -1):
                    return default_profile
            return name_profile

    return default_profile


def _assign_ratio(base_ratio: float, profile: str) -> float:
    if profile == "glass_sensitive":
        return max(float(base_ratio), float(_env_float("GMO_EXP_FIP_GLASS_RATIO", 1.0)))
    if profile == "shell_reflective":
        return max(float(base_ratio), float(_env_float("GMO_EXP_FIP_SHELL_REFLECTIVE_RATIO", 0.82)))
    if profile == "shell_structural":
        return max(float(base_ratio), float(_env_float("GMO_EXP_FIP_SHELL_RATIO", 0.78)))
    if profile == "view_surface":
        return max(float(base_ratio), float(_env_float("GMO_EXP_FIP_VIEW_SURFACE_RATIO", 0.85)))
    if profile == "wheel_visible":
        return max(float(base_ratio), float(_env_float("GMO_EXP_FIP_WHEEL_RATIO", 0.80)))
    if profile == "interior_hidden":
        return min(float(base_ratio), float(_env_float("GMO_EXP_FIP_INTERIOR_RATIO", 0.35)))
    if profile == "chassis_hidden":
        return min(float(base_ratio), float(_env_float("GMO_EXP_FIP_CHASSIS_RATIO", 0.32)))
    return min(float(base_ratio), float(_env_float("GMO_EXP_FIP_MISC_RATIO", 0.55)))


def _assign_autosmooth(base_autosmooth: float, profile: str) -> float:
    if profile == "glass_sensitive":
        return max(float(base_autosmooth), float(_env_float("GMO_EXP_FIP_GLASS_AUTOSMOOTH", 35.0)))
    if profile in {"shell_reflective", "shell_structural", "view_surface"}:
        return max(float(base_autosmooth), float(_env_float("GMO_EXP_FIP_SHELL_AUTOSMOOTH", 35.0)))
    if profile == "wheel_visible":
        return max(
            float(base_autosmooth),
            float(_env_float("GMO_EXP_FIP_WHEEL_AUTOSMOOTH", _env_float("GMO_EXP_FIP_SHELL_AUTOSMOOTH", 35.0))),
        )
    if profile == "interior_hidden":
        return max(float(base_autosmooth), float(_env_float("GMO_EXP_FIP_INTERIOR_AUTOSMOOTH", 30.0)))
    if profile == "chassis_hidden":
        return max(float(base_autosmooth), float(_env_float("GMO_EXP_FIP_CHASSIS_AUTOSMOOTH", 30.0)))
    return max(float(base_autosmooth), float(_env_float("GMO_EXP_FIP_MISC_OBJECT_AUTOSMOOTH", base_autosmooth)))


def _profile_allows_transfer(profile: str) -> bool:
    if profile in {"glass_sensitive"}:
        return _env_bool("GMO_EXP_FIP_TRANSFER_GLASS", True)
    if profile in {"shell_reflective", "shell_structural", "view_surface"}:
        return _env_bool("GMO_EXP_FIP_TRANSFER_SHELL", True)
    if profile == "wheel_visible":
        return _env_bool("GMO_EXP_FIP_TRANSFER_WHEEL", False)
    return _env_bool("GMO_EXP_FIP_TRANSFER_MISC", False)


def _profile_allows_weighted(profile: str) -> bool:
    if profile in {"shell_reflective", "shell_structural", "view_surface"}:
        return _env_bool("GMO_EXP_FIP_WEIGHTED_SHELL", False)
    if profile == "glass_sensitive":
        return _env_bool("GMO_EXP_FIP_WEIGHTED_GLASS", False)
    if profile == "wheel_visible":
        return _env_bool("GMO_EXP_FIP_WEIGHTED_WHEEL", True)
    return _env_bool("GMO_EXP_FIP_WEIGHTED_MISC", True)


def _profile_needs_protect(profile: str) -> bool:
    if not _env_bool("GMO_EXP_FIP_PROTECT_GROUP", True):
        return False
    if profile == "wheel_visible":
        return _env_bool("GMO_EXP_FIP_PROTECT_WHEEL", True)
    return profile in {"glass_sensitive", "shell_reflective", "shell_structural", "view_surface"}


def _profile_force_face_smoothing(profile: str) -> bool:
    if profile == "wheel_visible":
        return _env_bool("GMO_EXP_FIP_WHEEL_FORCE_SMOOTH", True)
    return True


def _profile_limited_dissolve_angle(profile: str) -> float:
    if profile in {"shell_reflective", "shell_structural", "view_surface"}:
        return float(_env_float("GMO_EXP_FIP_SHELL_DISSOLVE_ANGLE", 0.0))
    if profile == "glass_sensitive":
        return float(_env_float("GMO_EXP_FIP_GLASS_DISSOLVE_ANGLE", 0.0))
    if profile == "wheel_visible":
        return float(_env_float("GMO_EXP_FIP_WHEEL_DISSOLVE_ANGLE", 0.0))
    if profile == "interior_hidden":
        return float(_env_float("GMO_EXP_FIP_INTERIOR_DISSOLVE_ANGLE", 0.0))
    if profile == "chassis_hidden":
        return float(_env_float("GMO_EXP_FIP_CHASSIS_DISSOLVE_ANGLE", 0.0))
    return float(_env_float("GMO_EXP_FIP_MISC_DISSOLVE_ANGLE", 0.0))


def _profile_planar_angle(profile: str) -> float:
    if profile in {"shell_reflective", "shell_structural", "view_surface"}:
        return float(_env_float("GMO_EXP_FIP_SHELL_PLANAR_ANGLE", 0.0))
    if profile == "glass_sensitive":
        return float(_env_float("GMO_EXP_FIP_GLASS_PLANAR_ANGLE", 0.0))
    if profile == "wheel_visible":
        return float(_env_float("GMO_EXP_FIP_WHEEL_PLANAR_ANGLE", 0.0))
    if profile == "interior_hidden":
        return float(_env_float("GMO_EXP_FIP_INTERIOR_PLANAR_ANGLE", 0.0))
    if profile == "chassis_hidden":
        return float(_env_float("GMO_EXP_FIP_CHASSIS_PLANAR_ANGLE", 0.0))
    return float(_env_float("GMO_EXP_FIP_MISC_PLANAR_ANGLE", 0.0))


def _profile_allows_symmetry(profile: str) -> bool:
    if not _env_bool("GMO_EXP_FIP_SYMMETRY_ENABLE", False):
        return False
    if profile in {"shell_reflective", "shell_structural", "view_surface"}:
        return _env_bool("GMO_EXP_FIP_SYMMETRY_SHELL", True)
    if profile == "glass_sensitive":
        return _env_bool("GMO_EXP_FIP_SYMMETRY_GLASS", False)
    if profile == "wheel_visible":
        return _env_bool("GMO_EXP_FIP_SYMMETRY_WHEEL", True)
    return _env_bool("GMO_EXP_FIP_SYMMETRY_MISC", False)


def _apply_limited_dissolve(obj, angle_deg: float) -> bool:
    if angle_deg <= 0.0:
        return False
    novel._activate_object(obj)
    try:
        base.bpy.ops.object.mode_set(mode="EDIT")
        base.bpy.ops.mesh.select_all(action="SELECT")
        base.bpy.ops.mesh.dissolve_limited(
            angle_limit=math.radians(float(angle_deg)),
            use_dissolve_boundaries=False,
        )
        _set_stat("objects_limited_dissolve_ok")
        return True
    except Exception as exc:
        print(f"[FIP][WARN] Limited dissolve failed on '{obj.name}': {exc}")
        _set_stat("objects_limited_dissolve_fail")
        return False
    finally:
        novel._ensure_object_mode()


def _apply_planar_if_needed(obj, angle_deg: float) -> bool:
    if angle_deg <= 0.0:
        return False
    try:
        base._apply_planar_decimate(obj, angle_deg)
        _set_stat("objects_planar_ok")
        return True
    except Exception as exc:
        print(f"[FIP][WARN] Planar decimate failed on '{obj.name}': {exc}")
        _set_stat("objects_planar_fail")
        return False


def _apply_weighted_normal(obj) -> bool:
    novel._activate_object(obj)
    mod = obj.modifiers.new(name="WEIGHTED_NORMAL_PARTITION", type="WEIGHTED_NORMAL")
    try:
        mod.mode = str(os.environ.get("GMO_EXP_FIP_WEIGHTED_MODE", "FACE_AREA_WITH_ANGLE"))
    except Exception:
        pass
    try:
        mod.weight = int(_env_int("GMO_EXP_FIP_WEIGHTED_WEIGHT", 50))
    except Exception:
        pass
    try:
        mod.keep_sharp = _env_bool("GMO_EXP_FIP_WEIGHTED_KEEP_SHARP", True)
    except Exception:
        pass
    try:
        mod.thresh = float(_env_float("GMO_EXP_FIP_WEIGHTED_THRESH", 0.01))
    except Exception:
        pass
    try:
        base.bpy.ops.object.modifier_apply(modifier=mod.name)
        return True
    except Exception as exc:
        print(f"[FIP][WARN] Weighted normal failed on '{obj.name}': {exc}")
        return False


def _sample_mesh_vertices(mesh, sample_limit: int) -> list[object]:
    vertices = list(getattr(mesh, "vertices", []))
    if sample_limit <= 0 or len(vertices) <= sample_limit:
        return [vert.co.copy() for vert in vertices]
    step = float(len(vertices)) / float(sample_limit)
    sampled: list[object] = []
    cursor = 0.0
    for _ in range(sample_limit):
        sampled.append(vertices[min(len(vertices) - 1, int(cursor))].co.copy())
        cursor += step
    return sampled


def _choose_symmetry_axis(obj) -> tuple[str | None, float | None]:
    mesh = getattr(obj, "data", None)
    vertices = list(getattr(mesh, "vertices", [])) if mesh is not None else []
    if len(vertices) < 24:
        return None, None

    axes_raw = str(os.environ.get("GMO_EXP_FIP_SYMMETRY_AXES", "YXZ") or "YXZ").upper()
    axes = [axis for axis in axes_raw if axis in {"X", "Y", "Z"}]
    if not axes:
        axes = ["Y", "X", "Z"]

    coords = [vert.co.copy() for vert in vertices]
    bounds_min = [min(coord[idx] for coord in coords) for idx in range(3)]
    bounds_max = [max(coord[idx] for coord in coords) for idx in range(3)]
    extents = [max_v - min_v for min_v, max_v in zip(bounds_min, bounds_max)]
    scale = max(max(extents), 1e-6)
    max_score = float(_env_float("GMO_EXP_FIP_SYMMETRY_MAX_SCORE", 0.018))
    max_center = float(_env_float("GMO_EXP_FIP_SYMMETRY_CENTER_MAX", 0.060))
    min_axis_share = float(_env_float("GMO_EXP_FIP_SYMMETRY_MIN_AXIS_SHARE", 0.12))
    sample_limit = max(24, int(_env_int("GMO_EXP_FIP_SYMMETRY_SAMPLE_VERTS", 600)))

    tree = kdtree.KDTree(len(coords))
    for idx, coord in enumerate(coords):
        tree.insert(coord, idx)
    tree.balance()

    sample_coords = _sample_mesh_vertices(mesh, sample_limit)
    best_axis = None
    best_score = None
    for axis_name in axes:
        axis_idx = "XYZ".index(axis_name)
        axis_extent = extents[axis_idx] / scale
        if axis_extent < min_axis_share:
            continue
        plane_offset = abs((bounds_min[axis_idx] + bounds_max[axis_idx]) * 0.5) / scale
        if plane_offset > max_center:
            continue

        total_score = 0.0
        for coord in sample_coords:
            mirrored = coord.copy()
            mirrored[axis_idx] *= -1.0
            _co, _index, distance = tree.find(mirrored)
            total_score += float(distance) / scale
        score = total_score / float(len(sample_coords))
        if best_score is None or score < best_score:
            best_axis = axis_name
            best_score = score

    if best_axis is None or best_score is None or best_score > max_score:
        return None, best_score
    return best_axis, best_score


def _apply_partition_mesh_ops(
    merge_dist: float,
    decimate_ratio: float,
    autosmooth_deg: float,
    use_planar: bool = False,
    planar_angle_deg: float = 2.0,
) -> None:
    total_scene_tris = _scene_total_triangles()
    source_objects: list[object] = []
    source_map: dict[str, object] = {}
    try:
        for obj in _scene_meshes():
            _set_stat("objects_seen")
            profile = _classify_object_profile(obj, total_scene_tris)
            if profile == "shell_reflective":
                _set_stat("objects_shell_reflective")
            elif profile == "shell_structural" or profile == "view_surface":
                _set_stat("objects_shell")
            elif profile == "glass_sensitive":
                _set_stat("objects_glass")
            elif profile == "wheel_visible":
                _set_stat("objects_wheel")
            elif profile == "interior_hidden":
                _set_stat("objects_interior")
            elif profile == "chassis_hidden":
                _set_stat("objects_chassis")
            else:
                _set_stat("objects_misc")

            novel._ensure_object_mode()
            obj_ratio = _assign_ratio(decimate_ratio, profile)
            obj_autosmooth = _assign_autosmooth(autosmooth_deg, profile)
            transfer_enabled = _profile_allows_transfer(profile)
            weighted_enabled = _profile_allows_weighted(profile)
            symmetry_enabled = _profile_allows_symmetry(profile)
            limited_dissolve_angle = _profile_limited_dissolve_angle(profile)
            profile_planar_angle = _profile_planar_angle(profile)
            effective_planar_angle = max(float(planar_angle_deg) if bool(use_planar) else 0.0, profile_planar_angle)

            source_obj = novel._duplicate_source_object(obj) if transfer_enabled else None
            if source_obj is not None:
                source_map[obj.name] = source_obj
                source_objects.append(source_obj)

            if merge_dist > 0.0:
                novel._merge_vertices(obj, merge_dist)
            if limited_dissolve_angle > 0.0:
                _apply_limited_dissolve(obj, limited_dissolve_angle)
            if effective_planar_angle > 0.0:
                _apply_planar_if_needed(obj, effective_planar_angle)

            protect_group = None
            if _profile_needs_protect(profile):
                protected_weights = _collect_protected_vertex_weights(obj)
                if protected_weights:
                    protect_group = _assign_protect_group(obj, protected_weights)
                    _set_stat("objects_protected")
                    _set_stat("protected_vertices", len(protected_weights))

            symmetry_axis = None
            symmetry_score = None
            try:
                if obj_ratio < 1.0:
                    novel._activate_object(obj)
                    mod = obj.modifiers.new(name="DECIMATE_FIP", type="DECIMATE")
                    mod.ratio = float(obj_ratio)
                    if symmetry_enabled:
                        symmetry_axis, symmetry_score = _choose_symmetry_axis(obj)
                        if symmetry_axis:
                            try:
                                mod.use_symmetry = True
                                mod.symmetry_axis = str(symmetry_axis)
                                _set_stat("objects_symmetry_ok")
                                _set_stat(f"objects_symmetry_axis_{str(symmetry_axis).lower()}")
                            except Exception as exc:
                                print(f"[FIP][WARN] Symmetry setup failed on '{obj.name}': {exc}")
                                symmetry_axis = None
                                _set_stat("objects_symmetry_fail")
                        else:
                            _set_stat("objects_symmetry_fail")
                    if protect_group:
                        try:
                            mod.vertex_group = str(protect_group)
                        except Exception:
                            pass
                        try:
                            mod.vertex_group_factor = float(_env_float("GMO_EXP_FIP_PROTECT_FACTOR", 12.0))
                        except Exception:
                            pass
                    base.bpy.ops.object.modifier_apply(modifier=mod.name)
            except Exception as exc:
                print(f"[FIP][WARN] Collapse decimate failed on '{obj.name}': {exc}")

            if _profile_force_face_smoothing(profile):
                novel._set_face_smoothing(obj)
            novel._set_auto_smooth(obj, obj_autosmooth)

            if source_obj is not None:
                if novel._apply_transfer_normals(obj, source_obj):
                    _set_stat("objects_transfer_ok")
                else:
                    _set_stat("objects_transfer_fail")
                    if weighted_enabled and _apply_weighted_normal(obj):
                        _set_stat("objects_weighted_ok")
                    elif weighted_enabled:
                        _set_stat("objects_weighted_fail")
            elif weighted_enabled:
                if _apply_weighted_normal(obj):
                    _set_stat("objects_weighted_ok")
                else:
                    _set_stat("objects_weighted_fail")

            if _profile_force_face_smoothing(profile):
                novel._set_face_smoothing(obj)
            novel._set_auto_smooth(obj, obj_autosmooth)

            print(
                "[FIP][OBJ] "
                f"qc_class={_current_qc_class()} obj={obj.name} profile={profile} "
                f"ratio={float(obj_ratio):.3f} autosmooth={float(obj_autosmooth):.1f} "
                f"dissolve={float(limited_dissolve_angle):.2f} planar={float(effective_planar_angle):.2f} "
                f"transfer={'ON' if transfer_enabled else 'OFF'} "
                f"weighted={'ON' if weighted_enabled else 'OFF'} "
                f"force_smooth={'ON' if _profile_force_face_smoothing(profile) else 'OFF'} "
                f"symmetry={symmetry_axis or 'OFF'} "
                f"symmetry_score={(f'{float(symmetry_score):.4f}' if symmetry_score is not None else 'n/a')} "
                f"protect={'ON' if bool(protect_group) else 'OFF'} "
                f"triangles={len(getattr(obj.data, 'polygons', []))}"
            )

            try:
                obj.select_set(False)
            except Exception:
                pass
    finally:
        novel._cleanup_source_objects(source_objects)


def _make_apply_mesh_ops(_exp_args):
    def _apply_mesh_ops(
        merge_dist: float,
        decimate_ratio: float,
        autosmooth_deg: float,
        use_planar: bool = False,
        planar_angle_deg: float = 2.0,
    ) -> None:
        _apply_partition_mesh_ops(merge_dist, decimate_ratio, autosmooth_deg, use_planar, planar_angle_deg)

    return _apply_mesh_ops


def _make_process_qc():
    def _process_qc(qc_path: Path, cfg) -> None:
        root = Path(cfg.root).expanduser().resolve()
        try:
            qc_rel = qc_path.relative_to(root).as_posix()
        except Exception:
            qc_rel = qc_path.name

        rule = rp.HEURISTIC_INDEX.get(rp._normalize_rel(qc_rel))
        if rule is None:
            rule = {
                "group": "baseline_other",
                "pipeline": "baseline",
                "reason": "missing_rule",
                "qc_rel": qc_rel,
            }

        rp.CURRENT_RULE.clear()
        rp.CURRENT_RULE.update(
            {
                "group": rule.get("group", "baseline_other"),
                "pipeline": rule.get("pipeline", "baseline"),
                "reason": rule.get("reason", "default_fallback"),
                "base_group": rule.get("base_group"),
                "model_rel": rule.get("model_rel"),
            }
        )

        preserve_original_mesh = bool(rule.get("preserve_original_mesh"))
        if preserve_original_mesh:
            CURRENT_PROFILE.clear()
            CURRENT_PROFILE.update({"qc_class": "passthrough"})
            if rp._copy_refs_passthrough(qc_path, cfg):
                return None

        local_cfg, qc_class, notes = _build_local_cfg(cfg, rule, qc_rel)
        CURRENT_PROFILE.clear()
        CURRENT_PROFILE.update({"qc_class": qc_class})

        print(
            "[FIP][QC] "
            f"qc={qc_rel} class={qc_class} "
            f"group={rule.get('group')} pipeline={rule.get('pipeline')} "
            f"model={rule.get('model_rel') or '(unknown)'}"
        )
        if notes:
            print(f"[FIP][CFG] qc={qc_rel} notes={' ; '.join(notes)}")

        result = rp.BASE_PROCESS_QC(qc_path, local_cfg)
        rp._postprocess_qc_outputs(qc_path, local_cfg, rule)
        return result

    return _process_qc


def _summary_payload(exp_args, heuristic_map_path: Path) -> dict[str, object]:
    payload = rp._summary_payload(exp_args, heuristic_map_path)
    payload["mode"] = "experimental_round_parts_policy_fidelity_partition_v1"
    payload["partition"] = {
        "profile_base_ratio": _env_float("GMO_EXP_FIP_PROFILE_BASE_RATIO", 0.30),
        "profile_ratio_anchor": _env_float("GMO_EXP_FIP_PROFILE_RATIO_ANCHOR", 0.30),
        "profile_ratio_delta": _env_float("GMO_EXP_FIP_PROFILE_RATIO_DELTA", 0.0),
        "profile_base_autosmooth": _env_float("GMO_EXP_FIP_PROFILE_BASE_AUTOSMOOTH", 30.0),
        "viewmodel_ratio": _env_float("GMO_EXP_FIP_VIEWMODEL_RATIO", 0.80),
        "worldmodel_ratio": _env_float("GMO_EXP_FIP_WORLDMODEL_RATIO", 0.55),
        "ground_base_ratio": _env_float("GMO_EXP_FIP_GROUND_BASE_RATIO", 0.60),
        "aircraft_ratio": _env_float("GMO_EXP_FIP_AIRCRAFT_RATIO", 0.72),
        "detail_ratio": _env_float("GMO_EXP_FIP_DETAIL_RATIO", 0.72),
        "misc_base_ratio": _env_float("GMO_EXP_FIP_MISC_BASE_RATIO", 0.60),
        "shell_reflective_ratio": _env_float("GMO_EXP_FIP_SHELL_REFLECTIVE_RATIO", 0.82),
        "shell_ratio": _env_float("GMO_EXP_FIP_SHELL_RATIO", 0.78),
        "glass_ratio": _env_float("GMO_EXP_FIP_GLASS_RATIO", 1.0),
        "wheel_ratio": _env_float("GMO_EXP_FIP_WHEEL_RATIO", 0.80),
        "wheel_autosmooth": _env_float(
            "GMO_EXP_FIP_WHEEL_AUTOSMOOTH",
            _env_float("GMO_EXP_FIP_SHELL_AUTOSMOOTH", 35.0),
        ),
        "interior_ratio": _env_float("GMO_EXP_FIP_INTERIOR_RATIO", 0.35),
        "chassis_ratio": _env_float("GMO_EXP_FIP_CHASSIS_RATIO", 0.32),
        "misc_ratio": _env_float("GMO_EXP_FIP_MISC_RATIO", 0.55),
        "transfer_shell": _env_bool("GMO_EXP_FIP_TRANSFER_SHELL", True),
        "transfer_glass": _env_bool("GMO_EXP_FIP_TRANSFER_GLASS", True),
        "transfer_wheel": _env_bool("GMO_EXP_FIP_TRANSFER_WHEEL", False),
        "transfer_misc": _env_bool("GMO_EXP_FIP_TRANSFER_MISC", False),
        "weighted_shell": _env_bool("GMO_EXP_FIP_WEIGHTED_SHELL", False),
        "weighted_glass": _env_bool("GMO_EXP_FIP_WEIGHTED_GLASS", False),
        "weighted_wheel": _env_bool("GMO_EXP_FIP_WEIGHTED_WHEEL", True),
        "wheel_force_smooth": _env_bool("GMO_EXP_FIP_WHEEL_FORCE_SMOOTH", True),
        "weighted_misc": _env_bool("GMO_EXP_FIP_WEIGHTED_MISC", True),
        "protect_group": _env_bool("GMO_EXP_FIP_PROTECT_GROUP", True),
        "protect_wheel": _env_bool("GMO_EXP_FIP_PROTECT_WHEEL", True),
        "protect_factor": _env_float("GMO_EXP_FIP_PROTECT_FACTOR", 12.0),
        "weighted_protect_map": _env_bool("GMO_EXP_FIP_WEIGHTED_PROTECT_MAP", False),
        "protect_weight_boundary": _env_float("GMO_EXP_FIP_PROTECT_WEIGHT_BOUNDARY", 1.0),
        "protect_weight_seam": _env_float("GMO_EXP_FIP_PROTECT_WEIGHT_SEAM", 1.0),
        "protect_weight_sharp": _env_float("GMO_EXP_FIP_PROTECT_WEIGHT_SHARP", 0.90),
        "protect_weight_material": _env_float("GMO_EXP_FIP_PROTECT_WEIGHT_MATERIAL", 0.95),
        "protect_weight_curvature": _env_float("GMO_EXP_FIP_PROTECT_WEIGHT_CURVATURE", 0.72),
        "protect_ring_decay": _env_float("GMO_EXP_FIP_PROTECT_RING_DECAY", 0.65),
        "protect_min_weight": _env_float("GMO_EXP_FIP_PROTECT_MIN_WEIGHT", 0.20),
        "curvature_deg": _env_float("GMO_EXP_FIP_CURVATURE_DEG", 35.0),
        "expand_rings": _env_int("GMO_EXP_FIP_EXPAND_RINGS", 1),
        "name_priority_classify": _env_bool("GMO_EXP_FIP_NAME_PRIORITY_CLASSIFY", False),
        "symmetry_enable": _env_bool("GMO_EXP_FIP_SYMMETRY_ENABLE", False),
        "symmetry_shell": _env_bool("GMO_EXP_FIP_SYMMETRY_SHELL", True),
        "symmetry_glass": _env_bool("GMO_EXP_FIP_SYMMETRY_GLASS", False),
        "symmetry_wheel": _env_bool("GMO_EXP_FIP_SYMMETRY_WHEEL", True),
        "symmetry_misc": _env_bool("GMO_EXP_FIP_SYMMETRY_MISC", False),
        "symmetry_axes": str(os.environ.get("GMO_EXP_FIP_SYMMETRY_AXES", "YXZ") or "YXZ"),
        "symmetry_max_score": _env_float("GMO_EXP_FIP_SYMMETRY_MAX_SCORE", 0.018),
        "symmetry_center_max": _env_float("GMO_EXP_FIP_SYMMETRY_CENTER_MAX", 0.060),
        "symmetry_min_axis_share": _env_float("GMO_EXP_FIP_SYMMETRY_MIN_AXIS_SHARE", 0.12),
        "symmetry_sample_verts": _env_int("GMO_EXP_FIP_SYMMETRY_SAMPLE_VERTS", 600),
        "shell_dissolve_angle": _env_float("GMO_EXP_FIP_SHELL_DISSOLVE_ANGLE", 0.0),
        "glass_dissolve_angle": _env_float("GMO_EXP_FIP_GLASS_DISSOLVE_ANGLE", 0.0),
        "wheel_dissolve_angle": _env_float("GMO_EXP_FIP_WHEEL_DISSOLVE_ANGLE", 0.0),
        "interior_dissolve_angle": _env_float("GMO_EXP_FIP_INTERIOR_DISSOLVE_ANGLE", 0.0),
        "chassis_dissolve_angle": _env_float("GMO_EXP_FIP_CHASSIS_DISSOLVE_ANGLE", 0.0),
        "misc_dissolve_angle": _env_float("GMO_EXP_FIP_MISC_DISSOLVE_ANGLE", 0.0),
        "shell_planar_angle": _env_float("GMO_EXP_FIP_SHELL_PLANAR_ANGLE", 0.0),
        "glass_planar_angle": _env_float("GMO_EXP_FIP_GLASS_PLANAR_ANGLE", 0.0),
        "wheel_planar_angle": _env_float("GMO_EXP_FIP_WHEEL_PLANAR_ANGLE", 0.0),
        "interior_planar_angle": _env_float("GMO_EXP_FIP_INTERIOR_PLANAR_ANGLE", 0.0),
        "chassis_planar_angle": _env_float("GMO_EXP_FIP_CHASSIS_PLANAR_ANGLE", 0.0),
        "misc_planar_angle": _env_float("GMO_EXP_FIP_MISC_PLANAR_ANGLE", 0.0),
        "view_surface_tri_share": _env_float("GMO_EXP_FIP_VIEW_SURFACE_TRI_SHARE", 0.10),
        "reflective_tri_share": _env_float("GMO_EXP_FIP_REFLECTIVE_TRI_SHARE", 0.08),
        "large_object_tri_share": _env_float("GMO_EXP_FIP_LARGE_OBJECT_TRI_SHARE", 0.22),
        "stats": {key: int(value) for key, value in PARTITION_STATS.items()},
    }
    return payload


def main() -> int:
    argv = sys.argv[1:]
    if "--" in argv:
        idx = argv.index("--")
        wrapped_args = argv[idx + 1 :]
    else:
        wrapped_args = argv

    exp_args, base_args = rp._parse_wrapper_args(wrapped_args)
    heuristic_map_path = Path(exp_args.heuristic_map).expanduser().resolve()
    if not heuristic_map_path.exists():
        print(f"[ERROR] Heuristic map not found: {heuristic_map_path}")
        return 2

    summary_dir = Path(exp_args.summary_dir).expanduser().resolve() if exp_args.summary_dir else None
    rp.HEURISTIC_INDEX = rp._load_heuristic_map(heuristic_map_path)

    print(
        "[FIP] "
        f"heuristic_map={heuristic_map_path} "
        f"entries={len(rp.HEURISTIC_INDEX)} "
        f"profile_base_ratio={_env_float('GMO_EXP_FIP_PROFILE_BASE_RATIO', 0.30):.2f} "
        f"profile_ratio_delta={_env_float('GMO_EXP_FIP_PROFILE_RATIO_DELTA', 0.0):+.2f} "
        f"viewmodel_ratio={_env_float('GMO_EXP_FIP_VIEWMODEL_RATIO', 0.80):.2f} "
        f"ground_base_ratio={_env_float('GMO_EXP_FIP_GROUND_BASE_RATIO', 0.60):.2f} "
        f"shell_ratio={_env_float('GMO_EXP_FIP_SHELL_RATIO', 0.78):.2f} "
        f"glass_ratio={_env_float('GMO_EXP_FIP_GLASS_RATIO', 1.0):.2f} "
        f"interior_ratio={_env_float('GMO_EXP_FIP_INTERIOR_RATIO', 0.35):.2f} "
        f"chassis_ratio={_env_float('GMO_EXP_FIP_CHASSIS_RATIO', 0.32):.2f} "
        f"symmetry={'ON' if _env_bool('GMO_EXP_FIP_SYMMETRY_ENABLE', False) else 'OFF'} "
        f"symmetry_axes={str(os.environ.get('GMO_EXP_FIP_SYMMETRY_AXES', 'YXZ') or 'YXZ')} "
        f"large_tri_share={_env_float('GMO_EXP_FIP_LARGE_OBJECT_TRI_SHARE', 0.22):.2f} "
        f"reflective_tri_share={_env_float('GMO_EXP_FIP_REFLECTIVE_TRI_SHARE', 0.08):.2f}"
    )

    original_apply_mesh_ops = base.apply_mesh_ops
    original_process_qc = base.process_qc
    try:
        base.apply_mesh_ops = _make_apply_mesh_ops(exp_args)
        base.process_qc = _make_process_qc()
        sys.argv = [sys.argv[0], "--", *base_args]
        return int(base.main() or 0)
    finally:
        base.apply_mesh_ops = original_apply_mesh_ops
        base.process_qc = original_process_qc
        payload = _summary_payload(exp_args, heuristic_map_path)
        rp._write_summary_part(summary_dir, payload)
        if summary_dir:
            summary_dir.mkdir(parents=True, exist_ok=True)
            summary_path = summary_dir / "fidelity_partition_policy_summary.json"
            summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"[FIP] summary_json={summary_path}")


if __name__ == "__main__":
    raise SystemExit(main())
