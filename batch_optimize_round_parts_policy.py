#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import math
import shutil
import sys
import uuid
from collections import Counter
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import batch_optimize_qc as base


BASE_APPLY_MESH_OPS = base.apply_mesh_ops
BASE_PROCESS_QC = base.process_qc
HEURISTIC_INDEX: dict[str, dict] = {}
CURRENT_RULE: dict[str, object] = {
    "group": "baseline_other",
    "pipeline": "baseline",
    "reason": "default_fallback",
}
POSTFIX_STATS = {
    "qcs": 0,
    "files": 0,
    "vertices": 0,
}
ROUND_STATS = {
    "wheel_models": 0,
    "round_objects": 0,
    "adaptive_floor_hits": 0,
}
EMBEDDED_STATS = {
    "candidate_objects": 0,
    "candidate_faces": 0,
    "candidate_components": 0,
    "qualified_candidate_faces": 0,
    "rejected_candidate_faces": 0,
    "separated_groups": 0,
    "qualified_round_parts": 0,
    "qualified_components": 0,
    "adaptive_floor_hits": 0,
}

EMBEDDED_BONE_TOKENS = ("steering", "speedometer", "tachometer", "tach", "gauge", "dial", "cluster")
EMBEDDED_MATERIAL_TOKENS = ("steer", "gauge", "speedo", "tach", "dial", "cluster")
EMBEDDED_GENERIC_BODY_MATERIALS = (
    "carmaterials",
    "base_body",
    "carmaterials_chrome",
    "vehiclelights_trans",
    "vehiclelights",
    "windows",
)


def _parse_wrapper_args(argv: list[str]):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--heuristic-map", required=True)
    ap.add_argument("--summary-dir", default=None)
    ap.add_argument("--ground-final-autosmooth", type=float, default=35.0)
    ap.add_argument(
        "--ground-weighted-mode",
        default="FACE_AREA_WITH_ANGLE",
        choices=["FACE_AREA", "CORNER_ANGLE", "FACE_AREA_WITH_ANGLE"],
    )
    ap.add_argument("--ground-weighted-weight", type=int, default=50)
    ap.add_argument("--ground-shade-smooth", action="store_true")
    ap.set_defaults(ground_keep_sharp=True)
    ap.add_argument("--ground-no-keep-sharp", dest="ground_keep_sharp", action="store_false")
    ap.add_argument(
        "--wheel-variant",
        default="silhouette_floor_20",
        choices=[
            "baseline",
            "normals_only",
            "silhouette_floor_16",
            "silhouette_floor_20",
            "silhouette_floor_20_normals",
        ],
    )
    ap.add_argument("--wheel-round-thickness-ratio", type=float, default=0.62)
    ap.add_argument("--wheel-round-balance-min", type=float, default=0.72)
    ap.add_argument("--wheel-round-cv-max", type=float, default=0.18)
    ap.add_argument("--wheel-floor-cap", type=float, default=0.72)
    ap.add_argument("--wheel-weighted-mode", default="FACE_AREA")
    ap.add_argument("--wheel-weighted-weight", type=int, default=35)
    ap.set_defaults(wheel_keep_sharp=True)
    ap.add_argument("--wheel-no-keep-sharp", dest="wheel_keep_sharp", action="store_false")
    ap.add_argument("--embedded-variant", default="floor_24", choices=["off", "floor_24", "floor_28"])
    ap.add_argument("--embedded-floor-cap", type=float, default=0.90)
    ap.add_argument("--embedded-max-size-ratio", type=float, default=0.28)
    ap.add_argument("--embedded-round-thickness-ratio", type=float, default=0.75)
    ap.add_argument("--embedded-round-balance-min", type=float, default=0.58)
    ap.add_argument("--embedded-round-cv-max", type=float, default=0.26)
    ap.add_argument("--embedded-generic-body-face-ratio-min", type=float, default=0.12)
    ap.add_argument("--embedded-generic-body-face-count-min", type=int, default=1000)
    return ap.parse_known_args(argv)


def _normalize_rel(path_like: str) -> str:
    return str(path_like).replace("\\", "/").lstrip("./").lower()


def _load_heuristic_map(path: Path) -> dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    entries = raw.get("entries") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError(f"Invalid heuristic map payload: {path}")

    out: dict[str, dict] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        qc_rel = item.get("qc_rel")
        if not qc_rel:
            continue
        out[_normalize_rel(qc_rel)] = dict(item)
    return out


def _activate_object(obj) -> None:
    for other in base.bpy.context.scene.objects:
        try:
            other.select_set(False)
        except Exception:
            pass
    try:
        obj.select_set(True)
    except Exception:
        pass
    try:
        base.bpy.context.view_layer.objects.active = obj
    except Exception:
        pass


def _ensure_object_mode() -> None:
    try:
        if base.bpy.context.object and base.bpy.context.object.mode != "OBJECT":
            base.bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass


def _merge_vertices(obj, merge_dist: float) -> None:
    if merge_dist <= 0.0:
        return
    _activate_object(obj)
    try:
        base.bpy.ops.object.mode_set(mode="EDIT")
        base.bpy.ops.mesh.select_all(action="SELECT")
        try:
            base.bpy.ops.mesh.merge_by_distance(distance=merge_dist)
        except Exception:
            base.bpy.ops.mesh.remove_doubles(threshold=merge_dist)
    finally:
        _ensure_object_mode()


def _apply_collapse_decimate(obj, decimate_ratio: float, *, modifier_name: str) -> None:
    if decimate_ratio >= 1.0:
        return
    _activate_object(obj)
    mod = obj.modifiers.new(name=modifier_name, type="DECIMATE")
    mod.ratio = float(decimate_ratio)
    base.bpy.ops.object.modifier_apply(modifier=mod.name)


def _set_face_smoothing(obj) -> None:
    if not getattr(obj, "data", None):
        return
    try:
        for poly in obj.data.polygons:
            poly.use_smooth = True
    except Exception:
        pass
    _activate_object(obj)
    try:
        base.bpy.ops.object.shade_smooth()
    except Exception:
        pass


def _set_auto_smooth(obj, angle_deg: float) -> None:
    if not getattr(obj, "data", None):
        return
    mesh = obj.data
    angle = math.radians(float(angle_deg))
    try:
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = angle
    except Exception:
        pass


def _apply_weighted_normal(obj, mode: str, weight: int, keep_sharp: bool, *, modifier_name: str) -> None:
    _activate_object(obj)
    mod = obj.modifiers.new(name=modifier_name, type="WEIGHTED_NORMAL")
    try:
        mod.mode = mode
    except Exception:
        pass
    try:
        mod.weight = int(weight)
    except Exception:
        pass
    try:
        mod.keep_sharp = bool(keep_sharp)
    except Exception:
        pass
    try:
        mod.thresh = 0.01
    except Exception:
        pass
    base.bpy.ops.object.modifier_apply(modifier=mod.name)


def _apply_ground_weighted_main(exp_args, merge_dist: float, decimate_ratio: float, autosmooth_deg: float) -> None:
    final_autosmooth = (
        float(exp_args.ground_final_autosmooth)
        if exp_args.ground_final_autosmooth is not None
        else float(autosmooth_deg)
    )

    for obj in [o for o in base.bpy.context.scene.objects if o.type == "MESH"]:
        _ensure_object_mode()
        _activate_object(obj)

        if merge_dist > 0.0:
            _merge_vertices(obj, merge_dist)

        try:
            _apply_collapse_decimate(obj, decimate_ratio, modifier_name="DECIMATE_BATCH_ROUND_POLICY_GROUND")
        except Exception as exc:
            print(f"[ROUNDPOLICY][WARN] Ground collapse decimate failed on '{obj.name}': {exc}")

        if exp_args.ground_shade_smooth:
            _set_face_smoothing(obj)

        _set_auto_smooth(obj, final_autosmooth)
        try:
            _apply_weighted_normal(
                obj,
                mode=str(exp_args.ground_weighted_mode),
                weight=int(exp_args.ground_weighted_weight),
                keep_sharp=bool(exp_args.ground_keep_sharp),
                modifier_name="WEIGHTED_NORMAL_ROUND_POLICY_GROUND",
            )
        except Exception as exc:
            print(f"[ROUNDPOLICY][WARN] Ground weighted normal failed on '{obj.name}': {exc}")
        _set_auto_smooth(obj, final_autosmooth)

        try:
            obj.select_set(False)
        except Exception:
            pass


def _object_vertices_np(obj):
    if not getattr(obj, "data", None) or not getattr(obj.data, "vertices", None):
        return None
    try:
        world = obj.matrix_world
        verts = [[*(world @ v.co)] for v in obj.data.vertices]
    except Exception:
        return None
    if not verts:
        return None
    return np.asarray(verts, dtype=np.float64)


def _convex_hull(points_2d: np.ndarray) -> np.ndarray:
    pts = np.unique(np.round(points_2d, 6), axis=0)
    if len(pts) <= 3:
        return pts

    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 1e-9:
            lower.pop()
        lower.append(p)

    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 1e-9:
            upper.pop()
        upper.append(p)

    return np.asarray(lower[:-1] + upper[:-1], dtype=np.float64)


def _detect_round_profile(obj, exp_args):
    verts = _object_vertices_np(obj)
    if verts is None or len(verts) < 24:
        return None

    centered = verts - verts.mean(axis=0, keepdims=True)
    cov = np.cov(centered, rowvar=False)
    try:
        eigvals, eigvecs = np.linalg.eigh(cov)
    except Exception:
        return None

    order = np.argsort(eigvals)
    eigvecs = eigvecs[:, order]
    projected = centered @ eigvecs
    extents = projected.max(axis=0) - projected.min(axis=0)
    if np.any(extents <= 1e-6):
        return None

    thickness = float(extents[0])
    radius_a = float(extents[1])
    radius_b = float(extents[2])
    avg_radius_extent = (radius_a + radius_b) / 2.0
    thickness_ratio = thickness / max(avg_radius_extent, 1e-6)
    balance = min(radius_a, radius_b) / max(radius_a, radius_b)

    profile_2d = projected[:, 1:3]
    hull = _convex_hull(profile_2d)
    if len(hull) < 8:
        return None

    center_2d = hull.mean(axis=0)
    radii = np.linalg.norm(hull - center_2d, axis=1)
    mean_radius = float(np.mean(radii))
    if mean_radius <= 1e-6:
        return None
    radial_cv = float(np.std(radii) / mean_radius)

    angles = np.arctan2(hull[:, 1] - center_2d[1], hull[:, 0] - center_2d[0])
    angles = np.sort((angles + (2.0 * math.pi)) % (2.0 * math.pi))
    gaps = np.diff(np.concatenate([angles, angles[:1] + (2.0 * math.pi)]))
    max_gap_deg = float(np.degrees(np.max(gaps))) if len(gaps) else 360.0
    estimated_sides = int(max(3, round(360.0 / max(max_gap_deg, 1e-6))))

    if thickness_ratio > float(exp_args.wheel_round_thickness_ratio):
        return None
    if balance < float(exp_args.wheel_round_balance_min):
        return None
    if radial_cv > float(exp_args.wheel_round_cv_max):
        return None

    return {
        "thickness_ratio": thickness_ratio,
        "balance": balance,
        "radial_cv": radial_cv,
        "estimated_sides": estimated_sides,
        "max_gap_deg": max_gap_deg,
    }


def _wheel_ratio_for_object(base_ratio: float, round_info: dict, exp_args) -> float:
    variant = str(exp_args.wheel_variant)
    if variant == "silhouette_floor_16":
        target_sides = 16
    elif variant in ("silhouette_floor_20", "silhouette_floor_20_normals"):
        target_sides = 20
    else:
        return float(base_ratio)

    estimated_sides = max(int(round_info.get("estimated_sides") or 0), 1)
    floor_ratio = target_sides / float(estimated_sides)
    floor_ratio = min(float(exp_args.wheel_floor_cap), floor_ratio)
    return float(max(float(base_ratio), floor_ratio))


def _apply_wheel_variant(exp_args, merge_dist: float, decimate_ratio: float, autosmooth_deg: float) -> None:
    ROUND_STATS["wheel_models"] += 1
    variant = str(exp_args.wheel_variant)
    weighted_for_variant = variant in ("normals_only", "silhouette_floor_20_normals")

    for obj in [o for o in base.bpy.context.scene.objects if o.type == "MESH"]:
        _ensure_object_mode()
        _activate_object(obj)

        if merge_dist > 0.0:
            _merge_vertices(obj, merge_dist)

        round_info = _detect_round_profile(obj, exp_args)
        obj_ratio = float(decimate_ratio)
        if round_info:
            ROUND_STATS["round_objects"] += 1
            obj_ratio = _wheel_ratio_for_object(decimate_ratio, round_info, exp_args)
            if obj_ratio > float(decimate_ratio) + 1e-6:
                ROUND_STATS["adaptive_floor_hits"] += 1
                print(
                    "[ROUNDPOLICY][WHEEL] "
                    f"variant={variant} obj={obj.name} ratio={obj_ratio:.4f} "
                    f"base={float(decimate_ratio):.4f} sides~{round_info['estimated_sides']} "
                    f"gap={round_info['max_gap_deg']:.2f} thickness_ratio={round_info['thickness_ratio']:.3f} "
                    f"balance={round_info['balance']:.3f} radial_cv={round_info['radial_cv']:.4f}"
                )

        try:
            _apply_collapse_decimate(obj, obj_ratio, modifier_name="DECIMATE_BATCH_ROUND_POLICY_WHEEL")
        except Exception as exc:
            print(f"[ROUNDPOLICY][WARN] Wheel collapse decimate failed on '{obj.name}': {exc}")

        _set_auto_smooth(obj, autosmooth_deg)
        if weighted_for_variant and round_info:
            _set_face_smoothing(obj)
            try:
                _apply_weighted_normal(
                    obj,
                    mode=str(exp_args.wheel_weighted_mode),
                    weight=int(exp_args.wheel_weighted_weight),
                    keep_sharp=bool(exp_args.wheel_keep_sharp),
                    modifier_name="WEIGHTED_NORMAL_ROUND_POLICY_WHEEL",
                )
            except Exception as exc:
                print(f"[ROUNDPOLICY][WARN] Wheel weighted normal failed on '{obj.name}': {exc}")
            _set_auto_smooth(obj, autosmooth_deg)

        try:
            obj.select_set(False)
        except Exception:
            pass


def _name_has_token(name: str | None, tokens: tuple[str, ...]) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return any(tok in lowered for tok in tokens)


def _dominant_group_name(obj, vert) -> str | None:
    groups = getattr(vert, "groups", None)
    if not groups:
        return None
    best = None
    best_weight = -1.0
    for group_ref in groups:
        if group_ref.weight > best_weight:
            best = group_ref.group
            best_weight = group_ref.weight
    if best is None:
        return None
    try:
        return obj.vertex_groups[best].name
    except Exception:
        return None


def _body_max_extent(obj) -> float:
    verts = _object_vertices_np(obj)
    if verts is None or len(verts) == 0:
        return 0.0
    extents = verts.max(axis=0) - verts.min(axis=0)
    return float(max(extents))


def _poly_material_name(obj, poly) -> str | None:
    if not (0 <= poly.material_index < len(obj.material_slots)):
        return None
    try:
        mat = obj.material_slots[poly.material_index].material
        return mat.name if mat else None
    except Exception:
        return None


def _collect_embedded_candidate_faces(obj) -> set[int]:
    mesh = getattr(obj, "data", None)
    if not mesh or not getattr(mesh, "polygons", None):
        return set()

    candidate_faces: set[int] = set()
    for poly in mesh.polygons:
        mat_name = _poly_material_name(obj, poly)
        mat_hit = _name_has_token(mat_name, EMBEDDED_MATERIAL_TOKENS)

        bone_hits = 0
        for vert_index in poly.vertices:
            vg_name = _dominant_group_name(obj, mesh.vertices[vert_index])
            if _name_has_token(vg_name, EMBEDDED_BONE_TOKENS):
                bone_hits += 1
        if mat_hit or bone_hits >= 2:
            candidate_faces.add(poly.index)
    return candidate_faces


def _candidate_face_components(obj, face_indices: set[int]) -> list[set[int]]:
    mesh = getattr(obj, "data", None)
    if not mesh or not face_indices:
        return []

    vert_to_faces: dict[int, list[int]] = defaultdict(list)
    for face_index in face_indices:
        poly = mesh.polygons[face_index]
        for vert_index in poly.vertices:
            vert_to_faces[int(vert_index)].append(face_index)

    components: list[set[int]] = []
    visited: set[int] = set()
    for start in face_indices:
        if start in visited:
            continue
        stack = [start]
        component: set[int] = set()
        visited.add(start)
        while stack:
            face_index = stack.pop()
            component.add(face_index)
            for vert_index in mesh.polygons[face_index].vertices:
                for other in vert_to_faces[int(vert_index)]:
                    if other not in visited:
                        visited.add(other)
                        stack.append(other)
        components.append(component)
    components.sort(key=len, reverse=True)
    return components


def _component_vertices_np(obj, face_indices: set[int]) -> np.ndarray | None:
    mesh = getattr(obj, "data", None)
    if not mesh or not face_indices:
        return None
    vert_indices = {int(vert) for face_index in face_indices for vert in mesh.polygons[face_index].vertices}
    if not vert_indices:
        return None
    world = obj.matrix_world
    verts = [[*(world @ mesh.vertices[idx].co)] for idx in sorted(vert_indices)]
    if not verts:
        return None
    return np.asarray(verts, dtype=np.float64)


def _component_policy_flags(obj, face_indices: set[int]) -> dict[str, object]:
    mesh = getattr(obj, "data", None)
    if not mesh or not face_indices:
        return {
            "face_ratio": 0.0,
            "bone_only": False,
            "generic_only": False,
            "materials": {},
        }
    materials = Counter()
    any_mat_hit = False
    generic_only = True
    for face_index in face_indices:
        poly = mesh.polygons[face_index]
        mat_name = _poly_material_name(obj, poly)
        mat_lower = str(mat_name or "").lower()
        if _name_has_token(mat_name, EMBEDDED_MATERIAL_TOKENS):
            any_mat_hit = True
        if not mat_lower or mat_lower not in EMBEDDED_GENERIC_BODY_MATERIALS:
            generic_only = False
        if mat_lower:
            materials[mat_lower] += 1
    total_faces = len(mesh.polygons) or 1
    return {
        "face_ratio": float(len(face_indices) / total_faces),
        "bone_only": not any_mat_hit,
        "generic_only": generic_only and bool(materials),
        "materials": dict(materials),
    }


def _round_info_from_points(verts: np.ndarray) -> dict[str, float] | None:
    if verts is None or len(verts) < 24:
        return None

    centered = verts - verts.mean(axis=0, keepdims=True)
    cov = np.cov(centered, rowvar=False)
    try:
        eigvals, eigvecs = np.linalg.eigh(cov)
    except Exception:
        return None

    order = np.argsort(eigvals)
    eigvecs = eigvecs[:, order]
    projected = centered @ eigvecs
    extents = projected.max(axis=0) - projected.min(axis=0)
    if np.any(extents <= 1e-6):
        return None

    thickness = float(extents[0])
    radius_a = float(extents[1])
    radius_b = float(extents[2])
    avg_radius_extent = (radius_a + radius_b) / 2.0
    thickness_ratio = thickness / max(avg_radius_extent, 1e-6)
    balance = min(radius_a, radius_b) / max(radius_a, radius_b)

    profile_2d = projected[:, 1:3]
    hull = _convex_hull(profile_2d)
    if len(hull) < 8:
        return None

    center_2d = hull.mean(axis=0)
    radii = np.linalg.norm(hull - center_2d, axis=1)
    mean_radius = float(np.mean(radii))
    if mean_radius <= 1e-6:
        return None
    radial_cv = float(np.std(radii) / mean_radius)

    angles = np.arctan2(hull[:, 1] - center_2d[1], hull[:, 0] - center_2d[0])
    angles = np.sort((angles + (2.0 * math.pi)) % (2.0 * math.pi))
    gaps = np.diff(np.concatenate([angles, angles[:1] + (2.0 * math.pi)]))
    max_gap_deg = float(np.degrees(np.max(gaps))) if len(gaps) else 360.0
    estimated_sides = int(max(3, round(360.0 / max(max_gap_deg, 1e-6))))
    max_extent = float(max(extents))

    return {
        "thickness_ratio": thickness_ratio,
        "balance": balance,
        "radial_cv": radial_cv,
        "estimated_sides": estimated_sides,
        "max_gap_deg": max_gap_deg,
        "max_extent": max_extent,
    }


def _qualify_embedded_component_faces(obj, face_indices: set[int], body_extent: float, exp_args):
    verts = _component_vertices_np(obj, face_indices)
    if verts is None or len(verts) < 24:
        return None
    policy = _component_policy_flags(obj, face_indices)
    if (
        policy["bone_only"]
        and policy["generic_only"]
        and len(face_indices) >= int(exp_args.embedded_generic_body_face_count_min)
        and float(policy["face_ratio"]) >= float(exp_args.embedded_generic_body_face_ratio_min)
    ):
        print(
            "[ROUNDPOLICY][EMBED][GUARD] "
            f"reject=generic_body_spill obj={obj.name} component_faces={len(face_indices)} "
            f"face_ratio={float(policy['face_ratio']):.4f} materials={policy['materials']}"
        )
        return None
    round_info = _round_info_from_points(verts)
    if not round_info:
        return None
    max_extent = float(round_info.get("max_extent") or 0.0)
    if body_extent > 1e-6 and max_extent / body_extent > float(exp_args.embedded_max_size_ratio):
        return None
    if round_info["thickness_ratio"] > float(exp_args.embedded_round_thickness_ratio):
        return None
    if round_info["balance"] < float(exp_args.embedded_round_balance_min):
        return None
    if round_info["radial_cv"] > float(exp_args.embedded_round_cv_max):
        return None
    return round_info


def _separate_selected_faces(obj, face_indices: set[int]) -> list:
    if not face_indices:
        return []
    _ensure_object_mode()
    for other in base.bpy.context.scene.objects:
        try:
            other.select_set(False)
        except Exception:
            pass
    for poly in obj.data.polygons:
        poly.select = poly.index in face_indices
    _activate_object(obj)
    before = {o.name for o in base.bpy.context.scene.objects if o.type == "MESH"}
    base.bpy.ops.object.mode_set(mode="EDIT")
    base.bpy.ops.mesh.separate(type="SELECTED")
    _ensure_object_mode()
    return [o for o in base.bpy.context.scene.objects if o.type == "MESH" and o.name not in before]


def _split_loose_parts(obj) -> list:
    _ensure_object_mode()
    for other in base.bpy.context.scene.objects:
        try:
            other.select_set(False)
        except Exception:
            pass
    _activate_object(obj)
    base.bpy.ops.object.mode_set(mode="EDIT")
    base.bpy.ops.mesh.select_all(action="SELECT")
    base.bpy.ops.mesh.separate(type="LOOSE")
    _ensure_object_mode()
    return [o for o in base.bpy.context.selected_objects if o.type == "MESH"]


def _embedded_target_sides(exp_args) -> int:
    if str(exp_args.embedded_variant) == "floor_24":
        return 24
    if str(exp_args.embedded_variant) == "floor_28":
        return 28
    return 0


def _qualify_embedded_round(obj, body_extent: float, exp_args):
    round_info = _detect_round_profile(obj, exp_args)
    if not round_info:
        return None
    verts = _object_vertices_np(obj)
    if verts is None or len(verts) < 24:
        return None
    max_extent = float(max(verts.max(axis=0) - verts.min(axis=0)))
    if body_extent > 1e-6 and max_extent / body_extent > float(exp_args.embedded_max_size_ratio):
        return None
    if round_info["thickness_ratio"] > float(exp_args.embedded_round_thickness_ratio):
        return None
    if round_info["balance"] < float(exp_args.embedded_round_balance_min):
        return None
    if round_info["radial_cv"] > float(exp_args.embedded_round_cv_max):
        return None
    return round_info


def _detail_ratio(base_ratio: float, round_info: dict, exp_args) -> float:
    target_sides = _embedded_target_sides(exp_args)
    if target_sides <= 0:
        return float(base_ratio)
    estimated_sides = max(int(round_info.get("estimated_sides") or 0), 1)
    floor_ratio = target_sides / float(estimated_sides)
    floor_ratio = min(float(exp_args.embedded_floor_cap), floor_ratio)
    return float(max(float(base_ratio), floor_ratio))


def _apply_object_decimate(obj, ratio: float, autosmooth_deg: float, *, smooth_faces: bool) -> None:
    _ensure_object_mode()
    _activate_object(obj)
    try:
        _apply_collapse_decimate(obj, ratio, modifier_name="DECIMATE_BATCH_ROUND_POLICY_EMBED")
    except Exception as exc:
        print(f"[ROUNDPOLICY][WARN] Collapse decimate failed on '{obj.name}': {exc}")
    if smooth_faces:
        _set_face_smoothing(obj)
    _set_auto_smooth(obj, autosmooth_deg)
    try:
        obj.select_set(False)
    except Exception:
        pass


def _apply_ground_with_embedded_round(exp_args, merge_dist: float, decimate_ratio: float, autosmooth_deg: float) -> None:
    mesh_objects = [o for o in base.bpy.context.scene.objects if o.type == "MESH"]
    body_extents = {obj.name: _body_max_extent(obj) for obj in mesh_objects}
    embedded_parts = []

    for obj in list(mesh_objects):
        if merge_dist > 0.0:
            _merge_vertices(obj, merge_dist)
        face_indices = _collect_embedded_candidate_faces(obj)
        if not face_indices:
            continue
        EMBEDDED_STATS["candidate_objects"] += 1
        EMBEDDED_STATS["candidate_faces"] += len(face_indices)

        components = _candidate_face_components(obj, face_indices)
        EMBEDDED_STATS["candidate_components"] += len(components)

        qualified_face_indices: set[int] = set()
        qualified_components = 0
        for component in components:
            info = _qualify_embedded_component_faces(obj, component, body_extents.get(obj.name, 0.0), exp_args)
            if info:
                qualified_face_indices.update(component)
                qualified_components += 1
                EMBEDDED_STATS["qualified_components"] += 1
            else:
                EMBEDDED_STATS["rejected_candidate_faces"] += len(component)

        EMBEDDED_STATS["qualified_candidate_faces"] += len(qualified_face_indices)
        rejected_faces = len(face_indices) - len(qualified_face_indices)
        print(
            "[ROUNDPOLICY][EMBED][FILTER] "
            f"obj={obj.name} candidate_faces={len(face_indices)} "
            f"candidate_components={len(components)} qualified_components={qualified_components} "
            f"qualified_faces={len(qualified_face_indices)} rejected_faces={rejected_faces}"
        )

        if not qualified_face_indices:
            continue

        separated = _separate_selected_faces(obj, qualified_face_indices)
        for sep_obj in separated:
            parts = _split_loose_parts(sep_obj) or [sep_obj]
            for part in parts:
                EMBEDDED_STATS["separated_groups"] += 1
                info = _qualify_embedded_round(part, body_extents.get(obj.name, 0.0), exp_args)
                if not info:
                    print(
                        "[ROUNDPOLICY][EMBED][WARN] Qualified-only split produced non-round part "
                        f"source={obj.name} part={part.name}"
                    )
                    continue
                part["_embedded_round_floor_ratio"] = _detail_ratio(decimate_ratio, info, exp_args)
                part["_embedded_round_info"] = info
                embedded_parts.append(part)
                EMBEDDED_STATS["qualified_round_parts"] += 1

    embedded_part_names = {obj.name for obj in embedded_parts}
    final_autosmooth = float(exp_args.ground_final_autosmooth)

    for obj in [o for o in base.bpy.context.scene.objects if o.type == "MESH"]:
        if obj.name in embedded_part_names:
            info = obj.get("_embedded_round_info") or {}
            obj_ratio = float(obj.get("_embedded_round_floor_ratio", decimate_ratio))
            if obj_ratio > float(decimate_ratio) + 1e-6:
                EMBEDDED_STATS["adaptive_floor_hits"] += 1
            print(
                "[ROUNDPOLICY][EMBED] "
                f"variant={exp_args.embedded_variant} obj={obj.name} ratio={obj_ratio:.4f} "
                f"base={float(decimate_ratio):.4f} sides~{info.get('estimated_sides')} "
                f"gap={info.get('max_gap_deg', 0.0):.2f} thickness_ratio={info.get('thickness_ratio', 0.0):.3f} "
                f"balance={info.get('balance', 0.0):.3f} radial_cv={info.get('radial_cv', 0.0):.4f}"
            )
            _apply_object_decimate(obj, obj_ratio, autosmooth_deg, smooth_faces=True)
            continue

        if exp_args.ground_shade_smooth:
            _set_face_smoothing(obj)
        _apply_object_decimate(obj, float(decimate_ratio), final_autosmooth, smooth_faces=False)
        try:
            _apply_weighted_normal(
                obj,
                mode=str(exp_args.ground_weighted_mode),
                weight=int(exp_args.ground_weighted_weight),
                keep_sharp=bool(exp_args.ground_keep_sharp),
                modifier_name="WEIGHTED_NORMAL_ROUND_POLICY_EMBED",
            )
        except Exception as exc:
            print(f"[ROUNDPOLICY][WARN] Ground weighted normal failed on '{obj.name}': {exc}")
        _set_auto_smooth(obj, final_autosmooth)


def _normalize_rigid_primary_bone_ids(smd_path: Path) -> dict[str, int]:
    if smd_path.suffix.lower() != ".smd" or not smd_path.exists():
        return {"files": 0, "vertices": 0}

    changed_vertices = 0
    out_lines: list[str] = []
    for raw_line in smd_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = raw_line.split()
        if len(parts) >= 12:
            try:
                numlinks = int(parts[9])
            except Exception:
                numlinks = -1
            if numlinks == 1 and len(parts) >= 12:
                first_bone = parts[0]
                link_bone = parts[10]
                if first_bone == "0" and link_bone != "0":
                    parts[0] = link_bone
                    raw_line = " ".join(parts)
                    changed_vertices += 1
        out_lines.append(raw_line)

    if changed_vertices <= 0:
        return {"files": 0, "vertices": 0}

    smd_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8", errors="ignore")
    return {"files": 1, "vertices": changed_vertices}


def _postprocess_qc_outputs(qc_path: Path, cfg, rule: dict[str, object]) -> None:
    if str(cfg.format).lower() != "smd":
        return

    group = str(rule.get("group") or "").lower()
    base_group = str(rule.get("base_group") or "").lower()
    if group != "experimental_ground_main" and base_group != "ground_main_candidate":
        return

    qc_text = qc_path.read_text(encoding="utf-8", errors="ignore")
    refs = base.extract_file_refs_from_qc(qc_text)
    if not refs:
        return

    out_dir = qc_path.parent / "output"
    qc_changed_files = 0
    qc_changed_vertices = 0
    for _full_token, rel_token, kind in refs:
        if kind != "mesh":
            continue
        rel_norm = base.normalize_qc_path_token(rel_token)
        src_file = (qc_path.parent / rel_norm).resolve()
        out_name = base.build_output_name(src_file, cfg.format)
        out_file = (out_dir / out_name).resolve()
        fix_stats = _normalize_rigid_primary_bone_ids(out_file)
        qc_changed_files += int(fix_stats.get("files", 0))
        qc_changed_vertices += int(fix_stats.get("vertices", 0))

    if qc_changed_vertices <= 0:
        return

    POSTFIX_STATS["qcs"] += 1
    POSTFIX_STATS["files"] += qc_changed_files
    POSTFIX_STATS["vertices"] += qc_changed_vertices
    print(
        "[ROUNDPOLICY] "
        f"rigid_primary_bone_fix=ON qc={qc_path.name} "
        f"files={qc_changed_files} vertices={qc_changed_vertices} "
        f"model={rule.get('model_rel') or '(unknown)'}"
    )


def _copy_refs_passthrough(qc_path: Path, cfg) -> bool:
    qc_dir = qc_path.parent
    out_dir = qc_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    qc_text = qc_path.read_text(encoding="utf-8", errors="ignore")
    refs = base.extract_file_refs_from_qc(qc_text)
    if not refs:
        print("[ROUNDPOLICY] passthrough_copy skip=no_refs")
        return True

    desired_ext = ".dmx" if str(cfg.format).lower() == "dmx" else ".smd"
    replacements = {}

    for full_token, rel_token, _kind in refs:
        rel_norm = base.normalize_qc_path_token(rel_token)
        src_file = (qc_dir / rel_norm).resolve()
        if not src_file.exists():
            print(f"[ROUNDPOLICY][WARN] passthrough missing_ref={src_file}")
            continue
        if src_file.suffix.lower() != desired_ext:
            print(
                "[ROUNDPOLICY][WARN] "
                f"passthrough format mismatch file={src_file.name} src_ext={src_file.suffix.lower()} "
                f"desired_ext={desired_ext}"
            )
            return False

        out_name = base.build_output_name(src_file, cfg.format)
        out_file = (out_dir / out_name).resolve()
        shutil.copy2(src_file, out_file)

        new_rel = f"output/{out_name}"
        if full_token.startswith('"') and full_token.endswith('"'):
            new_token = f'"{new_rel}"'
        else:
            new_token = new_rel
        replacements[full_token] = new_token

    if not replacements:
        print("[ROUNDPOLICY] passthrough_copy skip=no_outputs")
        return True

    qc_opt_text = qc_text
    for old, new in replacements.items():
        qc_opt_text = qc_opt_text.replace(old, new)

    qc_opt_path = qc_dir / f"{qc_path.stem}_OPT.qc"
    qc_opt_path.write_text(qc_opt_text, encoding="utf-8", errors="ignore")
    print(
        "[ROUNDPOLICY] "
        f"passthrough_copy=ON qc={qc_path.name} refs={len(replacements)} "
        f"format={cfg.format}"
    )
    return True


def _make_apply_mesh_ops(exp_args):
    def _apply_mesh_ops(
        merge_dist: float,
        decimate_ratio: float,
        autosmooth_deg: float,
        use_planar: bool = False,
        planar_angle_deg: float = 2.0,
    ) -> None:
        pipeline = str(CURRENT_RULE.get("pipeline") or "baseline").lower()
        group = str(CURRENT_RULE.get("group") or "baseline_other").lower()
        if pipeline == "experimental_ground_main":
            if str(exp_args.embedded_variant) != "off":
                _apply_ground_with_embedded_round(exp_args, merge_dist, decimate_ratio, autosmooth_deg)
                return
            _apply_ground_weighted_main(exp_args, merge_dist, decimate_ratio, autosmooth_deg)
            return
        if group == "baseline_wheel" and str(exp_args.wheel_variant) != "baseline":
            _apply_wheel_variant(exp_args, merge_dist, decimate_ratio, autosmooth_deg)
            return
        BASE_APPLY_MESH_OPS(merge_dist, decimate_ratio, autosmooth_deg, use_planar, planar_angle_deg)

    return _apply_mesh_ops


def _make_process_qc():
    def _process_qc(qc_path: Path, cfg) -> None:
        root = Path(cfg.root).expanduser().resolve()
        try:
            qc_rel = qc_path.relative_to(root).as_posix()
        except Exception:
            qc_rel = qc_path.name

        rule = HEURISTIC_INDEX.get(_normalize_rel(qc_rel))
        if rule is None:
            rule = {
                "group": "baseline_other",
                "pipeline": "baseline",
                "reason": "missing_rule",
                "qc_rel": qc_rel,
            }

        CURRENT_RULE.clear()
        CURRENT_RULE.update(
            {
                "group": rule.get("group", "baseline_other"),
                "pipeline": rule.get("pipeline", "baseline"),
                "reason": rule.get("reason", "default_fallback"),
                "base_group": rule.get("base_group"),
                "model_rel": rule.get("model_rel"),
            }
        )

        model_rel = rule.get("model_rel") or "(unknown)"
        preserve_original_mesh = bool(rule.get("preserve_original_mesh"))
        print(
            "[ROUNDPOLICY][QC] "
            f"qc={qc_rel} "
            f"group={rule.get('group')} "
            f"pipeline={rule.get('pipeline')} "
            f"reason={rule.get('reason')} "
            f"preserve_original_mesh={'ON' if preserve_original_mesh else 'OFF'} "
            f"model={model_rel}"
        )

        if preserve_original_mesh:
            if _copy_refs_passthrough(qc_path, cfg):
                return None
            local_cfg = copy.copy(cfg)
            local_cfg.ratio = 1.0
            local_cfg.merge = 0.0
            if hasattr(local_cfg, "use_planar"):
                local_cfg.use_planar = False
            print(
                "[ROUNDPOLICY][WARN] "
                f"passthrough_copy failed; fallback_to_safe_baseline qc={qc_rel} "
                f"ratio_override={local_cfg.ratio} merge_override={local_cfg.merge}"
            )
            result = BASE_PROCESS_QC(qc_path, local_cfg)
            _postprocess_qc_outputs(qc_path, local_cfg, rule)
            return result

        result = BASE_PROCESS_QC(qc_path, cfg)
        _postprocess_qc_outputs(qc_path, cfg, rule)
        return result

    return _process_qc


def _summary_payload(exp_args, heuristic_map_path: Path) -> dict[str, object]:
    return {
        "mode": "experimental_round_parts_policy",
        "heuristic_map": str(heuristic_map_path),
        "wheel_variant": str(exp_args.wheel_variant),
        "embedded_variant": str(exp_args.embedded_variant),
        "summary": {
            "wheel_models": int(ROUND_STATS["wheel_models"]),
            "wheel_round_objects": int(ROUND_STATS["round_objects"]),
            "wheel_adaptive_floor_hits": int(ROUND_STATS["adaptive_floor_hits"]),
            "embedded_candidate_objects": int(EMBEDDED_STATS["candidate_objects"]),
            "embedded_candidate_faces": int(EMBEDDED_STATS["candidate_faces"]),
            "embedded_candidate_components": int(EMBEDDED_STATS["candidate_components"]),
            "embedded_qualified_candidate_faces": int(EMBEDDED_STATS["qualified_candidate_faces"]),
            "embedded_rejected_candidate_faces": int(EMBEDDED_STATS["rejected_candidate_faces"]),
            "embedded_separated_groups": int(EMBEDDED_STATS["separated_groups"]),
            "embedded_qualified_components": int(EMBEDDED_STATS["qualified_components"]),
            "embedded_qualified_round_parts": int(EMBEDDED_STATS["qualified_round_parts"]),
            "embedded_adaptive_floor_hits": int(EMBEDDED_STATS["adaptive_floor_hits"]),
            "rigid_primary_bone_fix_qcs": int(POSTFIX_STATS["qcs"]),
            "rigid_primary_bone_fix_files": int(POSTFIX_STATS["files"]),
            "rigid_primary_bone_fix_vertices": int(POSTFIX_STATS["vertices"]),
        },
    }


def _write_summary_part(summary_dir: Path | None, payload: dict[str, object]) -> None:
    if summary_dir is None:
        return
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / f"round_parts_policy_summary_{uuid.uuid4().hex}.json"
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[ROUNDPOLICY] summary_part_json={summary_path}")


def main() -> int:
    global HEURISTIC_INDEX

    argv = sys.argv[1:]
    if "--" in argv:
        idx = argv.index("--")
        wrapped_args = argv[idx + 1 :]
    else:
        wrapped_args = argv

    exp_args, base_args = _parse_wrapper_args(wrapped_args)
    heuristic_map_path = Path(exp_args.heuristic_map).expanduser().resolve()
    if not heuristic_map_path.exists():
        print(f"[ERROR] Heuristic map not found: {heuristic_map_path}")
        return 2

    summary_dir = Path(exp_args.summary_dir).expanduser().resolve() if exp_args.summary_dir else None
    HEURISTIC_INDEX = _load_heuristic_map(heuristic_map_path)
    print(
        "[ROUNDPOLICY] "
        f"heuristic_map={heuristic_map_path} "
        f"entries={len(HEURISTIC_INDEX)} "
        f"wheel_variant={exp_args.wheel_variant} "
        f"embedded_variant={exp_args.embedded_variant} "
        f"ground_final_autosmooth={exp_args.ground_final_autosmooth} "
        f"ground_weighted_mode={exp_args.ground_weighted_mode} "
        f"ground_weighted_weight={exp_args.ground_weighted_weight} "
        f"ground_keep_sharp={'ON' if exp_args.ground_keep_sharp else 'OFF'} "
        f"ground_shade_smooth={'ON' if exp_args.ground_shade_smooth else 'OFF'}"
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
        summary = payload.get("summary", {})
        print(
            "[ROUNDPOLICY][SUMMARY] "
            f"wheel_models={summary.get('wheel_models', 0)} "
            f"wheel_round_objects={summary.get('wheel_round_objects', 0)} "
            f"wheel_adaptive_floor_hits={summary.get('wheel_adaptive_floor_hits', 0)} "
            f"embedded_candidate_objects={summary.get('embedded_candidate_objects', 0)} "
            f"embedded_qualified_round_parts={summary.get('embedded_qualified_round_parts', 0)} "
            f"embedded_rejected_candidate_faces={summary.get('embedded_rejected_candidate_faces', 0)} "
            f"embedded_adaptive_floor_hits={summary.get('embedded_adaptive_floor_hits', 0)} "
            f"rigid_primary_bone_fix_vertices={summary.get('rigid_primary_bone_fix_vertices', 0)}"
        )
        _write_summary_part(summary_dir, payload)


if __name__ == "__main__":
    raise SystemExit(main())
