#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

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


def _parse_wrapper_args(argv: list[str]):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--heuristic-map", required=True)
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


def _apply_collapse_decimate(obj, decimate_ratio: float) -> None:
    if decimate_ratio >= 1.0:
        return
    _activate_object(obj)
    mod = obj.modifiers.new(name="DECIMATE_BATCH_POLICY", type="DECIMATE")
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
    angle = base.math.radians(float(angle_deg))
    try:
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = angle
    except Exception:
        pass


def _apply_weighted_normal(obj, mode: str, weight: int, keep_sharp: bool) -> None:
    _activate_object(obj)
    mod = obj.modifiers.new(name="WEIGHTED_NORMAL_POLICY", type="WEIGHTED_NORMAL")
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
            _apply_collapse_decimate(obj, decimate_ratio)
        except Exception as exc:
            print(f"[POLICY][WARN] Collapse decimate failed on '{obj.name}': {exc}")

        if exp_args.ground_shade_smooth:
            _set_face_smoothing(obj)

        _set_auto_smooth(obj, final_autosmooth)

        try:
            _apply_weighted_normal(
                obj,
                mode=exp_args.ground_weighted_mode,
                weight=exp_args.ground_weighted_weight,
                keep_sharp=bool(exp_args.ground_keep_sharp),
            )
        except Exception as exc:
            print(f"[POLICY][WARN] Weighted normal failed on '{obj.name}': {exc}")

        _set_auto_smooth(obj, final_autosmooth)
        try:
            obj.select_set(False)
        except Exception:
            pass


def _make_apply_mesh_ops(exp_args):
    def _apply_mesh_ops(
        merge_dist: float,
        decimate_ratio: float,
        autosmooth_deg: float,
        use_planar: bool = False,
        planar_angle_deg: float = 2.0,
    ) -> None:
        pipeline = str(CURRENT_RULE.get("pipeline") or "baseline").lower()
        if pipeline == "experimental_ground_main":
            _apply_ground_weighted_main(exp_args, merge_dist, decimate_ratio, autosmooth_deg)
            return
        BASE_APPLY_MESH_OPS(merge_dist, decimate_ratio, autosmooth_deg, use_planar, planar_angle_deg)

    return _apply_mesh_ops


def _make_process_qc():
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
                    # Blender Source export esta zerando o bone primario em alguns verts rigidos
                    # do body; isso compila, mas certos rigs de carro chegam tortos no jogo.
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
            "[POLICY] "
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
            print("[POLICY] passthrough_copy skip=no_refs")
            return True

        desired_ext = ".dmx" if str(cfg.format).lower() == "dmx" else ".smd"
        replacements = {}

        for full_token, rel_token, _kind in refs:
            rel_norm = base.normalize_qc_path_token(rel_token)
            src_file = (qc_dir / rel_norm).resolve()
            if not src_file.exists():
                print(f"[POLICY][WARN] passthrough missing_ref={src_file}")
                continue
            if src_file.suffix.lower() != desired_ext:
                print(
                    "[POLICY][WARN] "
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
            print("[POLICY] passthrough_copy skip=no_outputs")
            return True

        qc_opt_text = qc_text
        for old, new in replacements.items():
            qc_opt_text = qc_opt_text.replace(old, new)

        qc_opt_path = qc_dir / f"{qc_path.stem}_OPT.qc"
        qc_opt_path.write_text(qc_opt_text, encoding="utf-8", errors="ignore")
        print(
            "[POLICY] "
            f"passthrough_copy=ON qc={qc_path.name} refs={len(replacements)} "
            f"format={cfg.format}"
        )
        return True

    def _process_qc(qc_path: Path, cfg) -> None:
        global CURRENT_RULE
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
        CURRENT_RULE = rule
        model_rel = rule.get("model_rel") or "(unknown)"
        preserve_original_mesh = bool(rule.get("preserve_original_mesh"))
        print(
            "[POLICY] "
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
                "[POLICY][WARN] "
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

    HEURISTIC_INDEX = _load_heuristic_map(heuristic_map_path)
    print(
        "[POLICY] "
        f"heuristic_map={heuristic_map_path} "
        f"entries={len(HEURISTIC_INDEX)} "
        f"ground_final_autosmooth={exp_args.ground_final_autosmooth} "
        f"ground_weighted_mode={exp_args.ground_weighted_mode} "
        f"ground_weighted_weight={exp_args.ground_weighted_weight} "
        f"ground_keep_sharp={'ON' if exp_args.ground_keep_sharp else 'OFF'} "
        f"ground_shade_smooth={'ON' if exp_args.ground_shade_smooth else 'OFF'}"
    )

    base.apply_mesh_ops = _make_apply_mesh_ops(exp_args)
    base.process_qc = _make_process_qc()
    sys.argv = [sys.argv[0], "--", *base_args]
    code = int(base.main() or 0)
    if POSTFIX_STATS["vertices"] > 0:
        print(
            "[POLICY] "
            f"rigid_primary_bone_fix_summary qcs={POSTFIX_STATS['qcs']} "
            f"files={POSTFIX_STATS['files']} vertices={POSTFIX_STATS['vertices']}"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
