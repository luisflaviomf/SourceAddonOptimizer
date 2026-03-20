from __future__ import annotations

import json
import math
import re
from pathlib import Path

ANIM_RE = re.compile(r'^\s*\$animation\s+(".*?"|\S+)\s+(".*?"|\S+)', re.IGNORECASE)
SEQ_RE = re.compile(r'^\s*\$sequence\s+(".*?"|\S+)', re.IGNORECASE)
MODELNAME_RE = re.compile(r'^\s*\$modelname\s+(".*?"|\S+)', re.IGNORECASE)
QUOTED_RE = re.compile(r'"([^"]+)"')
SUBTRACT_RE = re.compile(r'\bsubtract\s+(".*?"|\S+)', re.IGNORECASE)


def _token(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    return value


def _normalize_rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except Exception:
        return path.as_posix()


def _wrap_pi(value: float) -> float:
    while value > math.pi:
        value -= 2.0 * math.pi
    while value <= -math.pi:
        value += 2.0 * math.pi
    return value


def _parse_modelname(qc_path: Path) -> str | None:
    for line in qc_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = MODELNAME_RE.match(line)
        if not match:
            continue
        return _token(match.group(1)).replace("\\", "/")
    return None


def _parse_bone_pose(path: Path, bone_name: str = "steering_wheel") -> tuple[float, ...] | None:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    nodes = {}
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "nodes":
            i += 1
            while i < len(lines) and lines[i].strip() != "end":
                parts = lines[i].split('"')
                if len(parts) >= 3:
                    try:
                        nodes[int(parts[0].strip())] = parts[1]
                    except Exception:
                        pass
                i += 1
        if stripped == "skeleton":
            i += 1
            break
        i += 1

    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "end":
            break
        if stripped.startswith("time "):
            i += 1
            continue
        parts = stripped.split()
        if len(parts) >= 7:
            try:
                bone_index = int(parts[0])
            except Exception:
                i += 1
                continue
            if nodes.get(bone_index) == bone_name:
                return tuple(float(item) for item in parts[1:7])
        i += 1
    return None


def _patch_turn_anim(path: Path, rz_offset: float) -> bool:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    nodes: dict[int, str] = {}
    out: list[str] = []
    state = "scan"
    steering_index = None
    changed = False

    for line in lines:
        stripped = line.strip()
        if state == "scan":
            out.append(line)
            if stripped == "nodes":
                state = "nodes"
            continue
        if state == "nodes":
            out.append(line)
            if stripped == "end":
                state = "scan2"
                continue
            parts = line.split('"')
            if len(parts) >= 3:
                try:
                    nodes[int(parts[0].strip())] = parts[1]
                except Exception:
                    pass
            continue
        if state == "scan2":
            out.append(line)
            if stripped == "skeleton":
                state = "skeleton"
                steering_index = next((idx for idx, name in nodes.items() if name == "steering_wheel"), None)
            continue
        if state == "skeleton":
            if stripped == "end":
                out.append(line)
                state = "done"
                continue
            if stripped.startswith("time ") or steering_index is None:
                out.append(line)
                continue
            parts = stripped.split()
            if len(parts) >= 7:
                try:
                    bone_index = int(parts[0])
                except Exception:
                    out.append(line)
                    continue
                if bone_index == steering_index:
                    values = [float(item) for item in parts[1:7]]
                    values[5] = _wrap_pi(values[5] + rz_offset)
                    prefix = line[: len(line) - len(line.lstrip())]
                    out.append(prefix + f"{bone_index} " + " ".join(f"{value:.6f}" for value in values))
                    changed = True
                    continue
            out.append(line)
            continue
        out.append(line)

    if changed:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def _collect_blocks(qc_text: str) -> tuple[dict[str, dict], list[dict]]:
    animations: dict[str, dict] = {}
    sequences: list[dict] = []
    block_kind = None
    current_name = None
    current_file = None
    current_lines: list[str] = []
    depth = 0

    for raw in qc_text.splitlines():
        if block_kind is None:
            anim_match = ANIM_RE.match(raw)
            seq_match = SEQ_RE.match(raw)
            if anim_match:
                block_kind = "animation"
                current_name = _token(anim_match.group(1))
                current_file = _token(anim_match.group(2))
                current_lines = [raw]
                depth = raw.count("{") - raw.count("}")
                if depth <= 0:
                    animations[current_name] = {"file": current_file, "lines": list(current_lines)}
                    block_kind = None
                continue
            if seq_match:
                block_kind = "sequence"
                current_name = _token(seq_match.group(1))
                current_file = None
                current_lines = [raw]
                depth = raw.count("{") - raw.count("}")
                if depth <= 0:
                    sequences.append({"name": current_name, "lines": list(current_lines)})
                    block_kind = None
                continue
        else:
            current_lines.append(raw)
            depth += raw.count("{") - raw.count("}")
            if depth <= 0:
                if block_kind == "animation":
                    animations[current_name] = {"file": current_file, "lines": list(current_lines)}
                else:
                    sequences.append({"name": current_name, "lines": list(current_lines)})
                block_kind = None
            continue

    return animations, sequences


def _analyze_qc(qc_path: Path) -> dict | None:
    qc_text = qc_path.read_text(encoding="utf-8", errors="ignore")
    if "vehicle_steer" not in qc_text or "steering_wheel" not in qc_text:
        return None

    animations, sequences = _collect_blocks(qc_text)
    turning_anims = set()
    for sequence in sequences:
        joined = "\n".join(sequence["lines"]).lower()
        if "delta" not in joined or "vehicle_steer" not in joined or "blend" not in joined:
            continue
        for quoted in QUOTED_RE.findall("\n".join(sequence["lines"])):
            if quoted in animations:
                turning_anims.add(quoted)

    if not turning_anims:
        return None

    qc_dir = qc_path.parent
    turn_files = []
    corrective = []

    for anim_name in sorted(turning_anims):
        entry = animations.get(anim_name)
        if not entry:
            continue
        smd_path = qc_dir / entry["file"].replace("\\", "/")
        pose = _parse_bone_pose(smd_path)
        if pose is None:
            continue
        turn_files.append({"anim": anim_name, "path": smd_path, "pose": pose})

        for raw in entry["lines"]:
            match = SUBTRACT_RE.search(raw)
            if not match:
                continue
            corrective_name = _token(match.group(1))
            corrective_entry = animations.get(corrective_name)
            if not corrective_entry:
                continue
            corrective_path = qc_dir / corrective_entry["file"].replace("\\", "/")
            corrective_pose = _parse_bone_pose(corrective_path)
            if corrective_pose is None:
                continue
            corrective.append({"anim": corrective_name, "path": corrective_path, "pose": corrective_pose})

    if not turn_files or not corrective:
        return None

    turn_rz = [item["pose"][5] for item in turn_files]
    corrective_rz = [item["pose"][5] for item in corrective]
    turn_zero = all(abs(value) <= 0.02 for value in turn_rz)
    corrective_plus_90 = all(abs(value - (math.pi / 2.0)) <= 0.02 for value in corrective_rz)
    if not (turn_zero and corrective_plus_90):
        return None

    return {
        "qc_path": qc_path,
        "model_rel": _parse_modelname(qc_path),
        "turn_files": turn_files,
        "corrective": corrective,
    }


def apply_under_root(root: Path, report_path: Path | None = None) -> dict:
    root = root.expanduser().resolve()
    hits = []
    total_patched_turn_files = 0

    for qc_path in sorted(root.rglob("*_OPT.qc")):
        if "\\output\\" in str(qc_path).lower():
            continue
        info = _analyze_qc(qc_path)
        if not info:
            continue

        patched_turn_files = []
        for item in info["turn_files"]:
            turn_path = Path(item["path"])
            if _patch_turn_anim(turn_path, math.pi / 2.0):
                patched_turn_files.append(_normalize_rel(turn_path, root))

        if not patched_turn_files:
            continue

        total_patched_turn_files += len(patched_turn_files)
        hits.append(
            {
                "qc_rel": _normalize_rel(qc_path, root),
                "model_rel": info.get("model_rel"),
                "patched_turn_files": patched_turn_files,
                "turn_animations": [item["anim"] for item in info["turn_files"]],
                "corrective_animations": [item["anim"] for item in info["corrective"]],
            }
        )

    payload = {
        "mode": "experimental_vehicle_steer_turn_basis_fix",
        "summary": {
            "detected_qc_count": len(hits),
            "patched_turn_file_count": total_patched_turn_files,
        },
        "entries": hits,
    }

    if report_path is not None:
        report_path = report_path.expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return payload
