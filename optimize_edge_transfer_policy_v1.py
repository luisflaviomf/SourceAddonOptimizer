#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import bmesh

sys.dont_write_bytecode = True


REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import batch_optimize_qc as base
import batch_optimize_round_parts_policy as rp


NOVEL_STATS = {
    "objects_seen": 0,
    "objects_protected": 0,
    "objects_transfer_ok": 0,
    "objects_transfer_fail": 0,
    "protected_vertices": 0,
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
    try:
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = math.radians(float(angle_deg))
    except Exception:
        pass


def _duplicate_source_object(obj):
    src = obj.copy()
    src.data = obj.data.copy()
    src.name = f"{obj.name}_SRC_REF"
    src.hide_render = True
    try:
        src.hide_set(True)
    except Exception:
        pass
    base.bpy.context.scene.collection.objects.link(src)
    return src


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


def _collect_protected_vertices(obj) -> set[int]:
    curvature_deg = _env_float("GMO_EXP_NOVEL_CURVATURE_DEG", 32.0)
    expand_rings = _env_int("GMO_EXP_NOVEL_EXPAND_RINGS", 1)

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

        protected: set[int] = set()
        curvature_limit = math.radians(float(curvature_deg))

        for edge in bm.edges:
            edge_verts = [int(v.index) for v in edge.verts]
            protect = False
            if edge.is_boundary or not edge.is_manifold:
                protect = True
            if getattr(edge, "seam", False):
                protect = True
            if hasattr(edge, "smooth") and not bool(edge.smooth):
                protect = True
            if len(edge.link_faces) == 2:
                face_a, face_b = edge.link_faces
                if int(face_a.material_index) != int(face_b.material_index):
                    protect = True
                else:
                    try:
                        if float(face_a.normal.angle(face_b.normal)) >= curvature_limit:
                            protect = True
                    except Exception:
                        pass
            if protect:
                protected.update(edge_verts)

        protected = _expand_vertices(bm, protected, int(expand_rings))
        return protected
    finally:
        bm.free()


def _assign_protect_group(obj, vertex_ids: set[int]) -> str | None:
    if not vertex_ids:
        return None
    name = "VG_DECIMATE_PROTECT"
    vg = obj.vertex_groups.get(name)
    if vg is None:
        vg = obj.vertex_groups.new(name=name)
    vg.add(sorted(vertex_ids), 1.0, "REPLACE")
    return name


def _pick_loop_mapping(mod) -> str | None:
    try:
        prop = mod.bl_rna.properties.get("loop_mapping")
    except Exception:
        prop = None
    if prop is None:
        return None
    try:
        identifiers = [item.identifier for item in prop.enum_items]
    except Exception:
        identifiers = []
    preferred = [
        "POLYINTERP_LNORPROJ",
        "POLYINTERP_NEAREST",
        "NEAREST_POLYNOR",
        "POLYINTERP_VNORPROJ",
    ]
    for name in preferred:
        if name in identifiers:
            return name
    return identifiers[0] if identifiers else None


def _apply_transfer_normals(obj, source_obj) -> bool:
    _activate_object(obj)
    _set_auto_smooth(obj, _env_float("GMO_EXP_NOVEL_AUTOSMOOTH_FLOOR", 35.0))
    mod = obj.modifiers.new(name="DATA_TRANSFER_NOVEL", type="DATA_TRANSFER")
    mod.object = source_obj
    try:
        mod.use_loop_data = True
    except Exception:
        pass
    try:
        mod.data_types_loops = {"CUSTOM_NORMAL"}
    except Exception:
        pass
    try:
        mod.mix_mode = "REPLACE"
    except Exception:
        pass
    try:
        mod.mix_factor = 1.0
    except Exception:
        pass
    mapping = _pick_loop_mapping(mod)
    if mapping:
        try:
            mod.loop_mapping = mapping
        except Exception:
            pass
    try:
        base.bpy.ops.object.modifier_apply(modifier=mod.name)
        return True
    except Exception as exc:
        print(f"[NOVEL][WARN] Data transfer normals failed on '{obj.name}': {exc}")
        return False


def _apply_collapse_decimate(obj, decimate_ratio: float, protect_group: str | None) -> None:
    if decimate_ratio >= 1.0:
        return
    _activate_object(obj)
    mod = obj.modifiers.new(name="DECIMATE_NOVEL", type="DECIMATE")
    mod.ratio = float(decimate_ratio)
    if protect_group:
        try:
            mod.vertex_group = str(protect_group)
        except Exception:
            pass
        try:
            mod.vertex_group_factor = float(_env_float("GMO_EXP_NOVEL_PROTECT_FACTOR", 100.0))
        except Exception:
            pass
    base.bpy.ops.object.modifier_apply(modifier=mod.name)


def _cleanup_source_objects(source_objects: list) -> None:
    for obj in source_objects:
        try:
            data = getattr(obj, "data", None)
            base.bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and data.users == 0:
                base.bpy.data.meshes.remove(data, do_unlink=True)
        except Exception:
            pass


def _novel_apply_mesh_ops(
    merge_dist: float,
    decimate_ratio: float,
    autosmooth_deg: float,
    use_planar: bool = False,
    planar_angle_deg: float = 2.0,
) -> None:
    use_transfer_normals = _env_bool("GMO_EXP_NOVEL_TRANSFER_NORMALS", True)
    use_protect_group = _env_bool("GMO_EXP_NOVEL_PROTECT_GROUP", False)
    source_objects = []

    for obj in [o for o in base.bpy.context.scene.objects if o.type == "MESH"]:
        NOVEL_STATS["objects_seen"] += 1
        _ensure_object_mode()
        source_obj = _duplicate_source_object(obj) if use_transfer_normals else None
        if source_obj is not None:
            source_objects.append(source_obj)

        if merge_dist > 0.0:
            _merge_vertices(obj, merge_dist)

        protect_group = None
        protected_vertices: set[int] = set()
        if use_protect_group:
            protected_vertices = _collect_protected_vertices(obj)
            protect_group = _assign_protect_group(obj, protected_vertices)
            if protected_vertices:
                NOVEL_STATS["objects_protected"] += 1
                NOVEL_STATS["protected_vertices"] += len(protected_vertices)
                print(
                    "[NOVEL][PROTECT] "
                    f"object={obj.name} vertices={len(protected_vertices)} "
                    f"factor={_env_float('GMO_EXP_NOVEL_PROTECT_FACTOR', 100.0):.1f}"
                )

        if use_planar:
            try:
                base._apply_planar_decimate(obj, planar_angle_deg)
            except Exception as exc:
                print(f"[NOVEL][WARN] Planar decimate failed on '{obj.name}': {exc}")

        try:
            _apply_collapse_decimate(obj, decimate_ratio, protect_group)
        except Exception as exc:
            print(f"[NOVEL][WARN] Collapse decimate failed on '{obj.name}': {exc}")

        _set_face_smoothing(obj)
        _set_auto_smooth(obj, autosmooth_deg)

        if source_obj is not None:
            if _apply_transfer_normals(obj, source_obj):
                NOVEL_STATS["objects_transfer_ok"] += 1
            else:
                NOVEL_STATS["objects_transfer_fail"] += 1
                try:
                    rp._apply_weighted_normal(
                        obj,
                        mode="FACE_AREA_WITH_ANGLE",
                        weight=50,
                        keep_sharp=True,
                        modifier_name="WEIGHTED_NORMAL_NOVEL_FALLBACK",
                    )
                except Exception as exc:
                    print(f"[NOVEL][WARN] Weighted normal fallback failed on '{obj.name}': {exc}")
            _set_auto_smooth(obj, autosmooth_deg)

        try:
            obj.select_set(False)
        except Exception:
            pass

    _cleanup_source_objects(source_objects)


def _summary_payload(exp_args, heuristic_map_path: Path) -> dict[str, object]:
    payload = rp._summary_payload(exp_args, heuristic_map_path)
    payload["mode"] = "experimental_round_parts_policy_novel_edge_transfer_v1"
    payload["novel"] = {
        "transfer_normals": _env_bool("GMO_EXP_NOVEL_TRANSFER_NORMALS", True),
        "protect_group": _env_bool("GMO_EXP_NOVEL_PROTECT_GROUP", False),
        "protect_factor": _env_float("GMO_EXP_NOVEL_PROTECT_FACTOR", 100.0),
        "curvature_deg": _env_float("GMO_EXP_NOVEL_CURVATURE_DEG", 32.0),
        "expand_rings": _env_int("GMO_EXP_NOVEL_EXPAND_RINGS", 1),
        "stats": {
            "objects_seen": int(NOVEL_STATS["objects_seen"]),
            "objects_protected": int(NOVEL_STATS["objects_protected"]),
            "objects_transfer_ok": int(NOVEL_STATS["objects_transfer_ok"]),
            "objects_transfer_fail": int(NOVEL_STATS["objects_transfer_fail"]),
            "protected_vertices": int(NOVEL_STATS["protected_vertices"]),
        },
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
    rp.BASE_APPLY_MESH_OPS = _novel_apply_mesh_ops

    print(
        "[NOVEL] "
        f"heuristic_map={heuristic_map_path} "
        f"entries={len(rp.HEURISTIC_INDEX)} "
        f"transfer_normals={'ON' if _env_bool('GMO_EXP_NOVEL_TRANSFER_NORMALS', True) else 'OFF'} "
        f"protect_group={'ON' if _env_bool('GMO_EXP_NOVEL_PROTECT_GROUP', False) else 'OFF'} "
        f"protect_factor={_env_float('GMO_EXP_NOVEL_PROTECT_FACTOR', 100.0):.1f} "
        f"curvature_deg={_env_float('GMO_EXP_NOVEL_CURVATURE_DEG', 32.0):.1f} "
        f"expand_rings={_env_int('GMO_EXP_NOVEL_EXPAND_RINGS', 1)}"
    )

    original_apply_mesh_ops = base.apply_mesh_ops
    original_process_qc = base.process_qc
    try:
        base.apply_mesh_ops = rp._make_apply_mesh_ops(exp_args)
        base.process_qc = rp._make_process_qc()
        sys.argv = [sys.argv[0], "--", *base_args]
        return int(base.main() or 0)
    finally:
        base.apply_mesh_ops = original_apply_mesh_ops
        base.process_qc = original_process_qc
        payload = _summary_payload(exp_args, heuristic_map_path)
        summary = payload.get("novel", {}).get("stats", {})
        print(
            "[NOVEL][SUMMARY] "
            f"objects_seen={summary.get('objects_seen', 0)} "
            f"objects_protected={summary.get('objects_protected', 0)} "
            f"objects_transfer_ok={summary.get('objects_transfer_ok', 0)} "
            f"objects_transfer_fail={summary.get('objects_transfer_fail', 0)} "
            f"protected_vertices={summary.get('protected_vertices', 0)}"
        )
        rp._write_summary_part(summary_dir, payload)


if __name__ == "__main__":
    raise SystemExit(main())
