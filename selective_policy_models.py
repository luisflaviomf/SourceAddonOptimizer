from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

SMALL_UNKNOWN_THRESHOLD = 20_000

GROUND_BASE_TOKENS = (
    "base_glide_car",
    "base_glide_vehicle",
    "lvs_base_wheeldrive",
    "lvs_base_vehicle",
    "lvs_base_tracked",
    "lvs_base_tank",
    "simfphys_base",
)
GROUND_SIGNAL_TOKENS = (
    "createwheel",
    "wheeloffset",
    "suspension",
    "wheeldrive",
    "tracked",
    "tank",
    "armored",
    "armoured",
)
WHEEL_TOKENS = ("wheel", "rim", "tire", "tyre", "brake", "caliper", "hubcap", "hub", "fw", "rw", "wh")
ROTOR_TOKENS = ("rotor", "rmain", "rrear", "blade", "prop", "hub", "fan")
AIRCRAFT_MAIN_TOKENS = ("fuselage", "chassis", "body", "plane", "jet", "aircraft", "wingbody")
AIRCRAFT_PATH_TOKENS = ("jets/", "jet/", "planes/", "plane/", "aircraft/", "heli", "copter")
ATTACHMENT_TOKENS = (
    "turret",
    "gun",
    "mg",
    "hook",
    "winch",
    "cable",
    "pod",
    "weapon",
    "rocket",
    "missile",
    "glass",
    "window",
    "cockpit",
    "skid",
    "landing",
    "gear",
    "rail",
    "float",
)
DETACHED_TOKENS = (
    "gib",
    "dam",
    "damage",
    "door",
    "hood",
    "bonnet",
    "trunk",
    "boot",
    "bumper",
    "fender",
    "wing",
    "spoiler",
    "splitter",
    "diffuser",
    "skirt",
    "mirror",
    "light",
    "glass",
    "window",
    "windscreen",
    "grille",
    "grill",
    "seat",
    "dash",
    "interior",
    "antenna",
    "plate",
    "wiper",
    "exhaust",
    "flare",
    "bar",
)

ENTITY_PRINTNAME_RE = re.compile(r'^\s*ENT\.PrintName\s*=\s*"([^"]+)"', re.IGNORECASE)
ENTITY_MAIN_MODEL_RE = re.compile(r'^\s*ENT\.(?:MDL|ChassisModel)\s*=\s*"([^"]+\.mdl)"', re.IGNORECASE)
ENTITY_ROTOR_RE = re.compile(
    r'^\s*ENT\.(?:MainRotorModel|MainRotorFastModel|TailRotorModel|TailRotorFastModel)\s*=\s*"([^"]+\.mdl)"',
    re.IGNORECASE,
)
MODEL_ASSIGN_RE = re.compile(r'^\s*(?:model|mdl)\s*=\s*"([^"]+\.mdl)"', re.IGNORECASE)
QUOTED_MODEL_RE = re.compile(r'"(models/[^"]+\.mdl)"', re.IGNORECASE)
ANIMATION_NAME_RE = re.compile(r'^\s*\$animation\s+"([^"]+)"', re.IGNORECASE)


def _normalize_model_ref(value: str) -> str:
    low = value.replace("\\", "/").strip().lower()
    if low.startswith("models/"):
        low = low[len("models/") :]
    return low.lstrip("./")


def _entity_class_from_path(entities_root: Path, lua_path: Path) -> str:
    if lua_path.parent == entities_root:
        return lua_path.stem
    if lua_path.name.lower() in {"shared.lua", "init.lua", "cl_init.lua"}:
        return lua_path.parent.name
    return lua_path.stem


def _contains_token(text: str, tokens: tuple[str, ...]) -> str | None:
    low = text.lower()
    token_set = {tok for tok in re.split(r"[^a-z0-9]+", low) if tok}
    for tok in tokens:
        if len(tok) <= 2:
            if tok in token_set:
                return tok
        elif tok in low:
            return tok
    return None


def _detect_entity_family(lua_path: Path, text: str) -> str:
    low = text.lower()
    path_low = str(lua_path).replace("\\", "/").lower()
    if (
        "mainrotormodel" in low
        or "tailrotormodel" in low
        or "createrotor" in low
        or "base_glide_heli" in low
        or "helicopter" in low
        or "rotorcraft" in low
    ):
        return "rotorcraft"
    if (
        'ent.base = "base_glide_plane"' in low
        or "planeparams" in low
        or "/jets/" in low
        or "/planes/" in low
        or "jet_engine" in low
        or "aircraft" in low
        or "base_glide_plane" in path_low
        or "lvs_base_plane" in low
    ):
        return "fixedwing"
    if any(token in low for token in GROUND_BASE_TOKENS):
        return "ground"
    if any(token in low for token in GROUND_SIGNAL_TOKENS):
        return "ground"
    return "unknown"


def _record_model_ref(
    refs: dict,
    model_path: str,
    kind: str,
    entity_family: str,
    entity_class: str,
    print_name: str | None,
    lua_path: Path,
    line: str,
) -> None:
    model_norm = _normalize_model_ref(model_path)
    refs["tag_index"][model_norm].add(kind)
    refs["family_index"][model_norm].add(entity_family)
    refs["ref_index"][model_norm].append(
        {
            "kind": kind,
            "entity_family": entity_family,
            "entity_class": entity_class,
            "print_name": print_name or entity_class,
            "lua_path": str(lua_path),
            "line": line.strip(),
        }
    )


def _parse_entity_models(addon_path: Path) -> dict:
    refs = {
        "tag_index": defaultdict(set),
        "family_index": defaultdict(set),
        "ref_index": defaultdict(list),
        "spawn_entries": [],
    }
    entities_root = addon_path / "lua" / "entities"
    if not entities_root.exists():
        refs["summary"] = {
            "main_models": 0,
            "wheel_models": 0,
            "rotor_models": 0,
            "attachment_models": 0,
            "detached_models": 0,
            "entity_family_summary": {},
        }
        return refs

    seen_spawn = set()
    for lua_path in sorted(entities_root.rglob("*.lua"), key=lambda p: str(p).lower()):
        text = lua_path.read_text(encoding="utf-8", errors="ignore")
        entity_class = _entity_class_from_path(entities_root, lua_path)
        entity_family = _detect_entity_family(lua_path, text)
        print_name = None
        main_models_for_entity: set[str] = set()

        for raw in text.splitlines():
            line = raw.strip()
            if print_name is None:
                match = ENTITY_PRINTNAME_RE.match(line)
                if match:
                    print_name = match.group(1).strip()

            match = ENTITY_MAIN_MODEL_RE.match(line)
            if match:
                model_path = match.group(1).strip()
                _record_model_ref(refs, model_path, "main", entity_family, entity_class, print_name, lua_path, line)
                main_models_for_entity.add(_normalize_model_ref(model_path))
                continue

            match = ENTITY_ROTOR_RE.match(line)
            if match:
                _record_model_ref(refs, match.group(1).strip(), "rotor", entity_family, entity_class, print_name, lua_path, line)
                continue

            match = MODEL_ASSIGN_RE.match(line)
            if match:
                model_path = match.group(1).strip()
                model_low = _normalize_model_ref(model_path)
                if _contains_token(model_low, WHEEL_TOKENS):
                    kind = "wheel"
                elif _contains_token(model_low, ROTOR_TOKENS):
                    kind = "rotor"
                elif entity_family == "ground":
                    kind = "wheel"
                else:
                    kind = "attachment"
                _record_model_ref(refs, model_path, kind, entity_family, entity_class, print_name, lua_path, line)
                continue

            quoted_models = QUOTED_MODEL_RE.findall(line)
            if not quoted_models:
                continue
            line_low = line.lower()
            if "explosiongibs" in line_low or "/gibs/" in line_low or "gib" in line_low:
                kind = "detached"
            elif "/turrets/" in line_low or "turret" in line_low or "gun" in line_low or "mg_" in line_low:
                kind = "attachment"
            elif "rotor" in line_low:
                kind = "rotor"
            else:
                kind = None
            if kind:
                for model_path in quoted_models:
                    _record_model_ref(refs, model_path, kind, entity_family, entity_class, print_name, lua_path, line)

        if main_models_for_entity:
            key = (entity_class, print_name or entity_class)
            if key not in seen_spawn:
                refs["spawn_entries"].append(
                    {
                        "entity_family": entity_family,
                        "entity_class": entity_class,
                        "print_name": print_name or entity_class,
                        "main_models": sorted(main_models_for_entity),
                        "lua_path": str(lua_path),
                    }
                )
                seen_spawn.add(key)

    refs["summary"] = {
        "main_models": sum(1 for tags in refs["tag_index"].values() if "main" in tags),
        "wheel_models": sum(1 for tags in refs["tag_index"].values() if "wheel" in tags),
        "rotor_models": sum(1 for tags in refs["tag_index"].values() if "rotor" in tags),
        "attachment_models": sum(1 for tags in refs["tag_index"].values() if "attachment" in tags),
        "detached_models": sum(1 for tags in refs["tag_index"].values() if "detached" in tags),
        "entity_family_summary": dict(Counter(item["entity_family"] for item in refs["spawn_entries"])),
    }
    refs["spawn_entries"].sort(key=lambda item: item["entity_class"].lower())
    return refs


def _find_qc_in_src(src_dir: Path) -> Path | None:
    qcs = sorted(
        [path for path in src_dir.rglob("*.qc") if path.is_file() and not path.name.lower().endswith("_opt.qc")],
        key=lambda path: str(path).lower(),
    )
    if not qcs:
        return None
    return qcs[0]


def _sum_model_bytes(models_root: Path, model_rel: Path) -> int:
    folder = models_root / model_rel.parent
    if not folder.exists():
        return 0
    stem = model_rel.stem.lower()
    total = 0
    for path in folder.iterdir():
        if not path.is_file():
            continue
        if not path.name.lower().startswith(stem + "."):
            continue
        total += path.stat().st_size
    return int(total)


def _duplicate_qc_animation_names(qc_path: Path) -> list[str]:
    try:
        qc_text = qc_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    names = [match.group(1).strip().lower() for match in map(ANIMATION_NAME_RE.match, qc_text.splitlines()) if match]
    counts = Counter(names)
    return sorted(name for name, count in counts.items() if count > 1)


def _classify_model(model_rel: str, original_bundle_bytes: int, entity_refs: dict) -> tuple[str, str]:
    model_rel_low = _normalize_model_ref(model_rel)
    tags = entity_refs["tag_index"].get(model_rel_low, set())
    families = entity_refs["family_index"].get(model_rel_low, set())

    if "main" in tags:
        if "rotorcraft" in families or "fixedwing" in families:
            family = "rotorcraft" if "rotorcraft" in families else "fixedwing"
            return "aircraft_main_candidate", f"explicit_main_ref:{family}"
        if "ground" in families:
            return "ground_main_candidate", "explicit_main_ref:ground"
        if _contains_token(model_rel_low, AIRCRAFT_MAIN_TOKENS) and any(token in model_rel_low for token in AIRCRAFT_PATH_TOKENS):
            return "aircraft_main_candidate", "explicit_main_ref:path_aircraft"
        return "ground_main_candidate", "explicit_main_ref:unknown"

    if "wheel" in tags:
        return "baseline_wheel", "explicit_wheel_ref"
    if "rotor" in tags:
        return "baseline_rotor", "explicit_rotor_ref"
    if "attachment" in tags:
        return "baseline_attachment", "explicit_attachment_ref"
    if "detached" in tags:
        return "baseline_detached", "explicit_detached_ref"

    if "/gibs/" in model_rel_low:
        return "baseline_detached", "path_gibs_folder"

    token = _contains_token(model_rel_low, WHEEL_TOKENS)
    if token:
        return "baseline_wheel", f"wheel_token:{token}"
    token = _contains_token(model_rel_low, ROTOR_TOKENS)
    if token:
        return "baseline_rotor", f"rotor_token:{token}"
    token = _contains_token(model_rel_low, ATTACHMENT_TOKENS)
    if token:
        return "baseline_attachment", f"attachment_token:{token}"
    if _contains_token(model_rel_low, AIRCRAFT_MAIN_TOKENS) and any(token in model_rel_low for token in AIRCRAFT_PATH_TOKENS):
        return "aircraft_main_candidate", "aircraft_main_path"

    token = _contains_token(model_rel_low, DETACHED_TOKENS)
    if token:
        return "baseline_detached", f"detached_token:{token}"
    if original_bundle_bytes < SMALL_UNKNOWN_THRESHOLD:
        return "baseline_small_unknown", f"small_bundle_lt_{SMALL_UNKNOWN_THRESHOLD}"
    return "baseline_other", "defensive_fallback"


def _ground_candidate_is_confident(entry: dict) -> bool:
    families = {str(item).lower() for item in entry.get("reference_families", [])}
    reason = str(entry.get("reason") or "").lower()
    if "ground" in families:
        return True
    if reason.startswith("explicit_main_ref:ground"):
        return True
    return False


def _policy_descriptions() -> dict[str, str]:
    return {
        "experimental_ground_main": "Main hard-surface terrestre confiante usa shade smooth + weighted normals + autosmooth final 35 apos o decimate.",
        "baseline_aircraft": "Aeronaves e helicopteros permanecem no baseline defensivo.",
        "baseline_wheel": "Rodas, pneus e partes cilindricas sensiveis ficam no baseline simples.",
        "baseline_rotor": "Rotores, blades, props e hubs ficam no baseline simples.",
        "baseline_attachment": "Turrets, pods, armas, landing gear exposto e anexos finos ficam no baseline simples.",
        "baseline_detached": "Gibs e pecas destacadas/dano separado ficam no baseline simples.",
        "baseline_uncertain_main": "Main body detectado sem confianca suficiente; cai para baseline por seguranca.",
        "baseline_small_unknown": "Bundles pequenos nao classificados ficam no baseline simples.",
        "baseline_other": "Fallback defensivo para misc/weapon/unknown e qualquer caso incerto.",
    }


def _interpret_addon(entries: list[dict], entity_refs: dict) -> dict:
    family_summary = entity_refs.get("summary", {}).get("entity_family_summary", {})
    ground_spawn = int(family_summary.get("ground", 0))
    aircraft_spawn = int(family_summary.get("rotorcraft", 0)) + int(family_summary.get("fixedwing", 0))
    eligible_ground = sum(1 for item in entries if item["group"] == "experimental_ground_main")
    uncertain_main = sum(1 for item in entries if item["group"] == "baseline_uncertain_main")
    aircraft_main = sum(1 for item in entries if item["group"] == "baseline_aircraft")
    preserve_original_mesh = sum(1 for item in entries if item.get("preserve_original_mesh"))
    rigid_primary_bone_fix_candidate = sum(1 for item in entries if item.get("rigid_primary_bone_fix_candidate"))

    if ground_spawn > 0 and aircraft_spawn == 0:
        label = "ground-heavy"
        why = "spawn_entries detectados so para entidades terrestres"
    elif aircraft_spawn > 0 and ground_spawn == 0:
        label = "aircraft-heavy"
        why = "spawn_entries detectados so para entidades aereas"
    elif aircraft_spawn > 0 and ground_spawn > 0:
        label = "mixed"
        why = "spawn_entries misturam familias terrestre e aerea"
    else:
        label = "unknown"
        why = "sem familia confiavel suficiente em lua/entities"

    return {
        "label": label,
        "why": why,
        "spawn_family_summary": family_summary,
        "eligible_ground_main_count": eligible_ground,
        "uncertain_main_count": uncertain_main,
        "aircraft_main_count": aircraft_main,
        "preserve_original_mesh_count": preserve_original_mesh,
        "rigid_primary_bone_fix_candidate_count": rigid_primary_bone_fix_candidate,
    }


def build_final_policy_payload(addon_path: Path, decompile_results: list[dict], src_root: Path) -> dict:
    entity_refs = _parse_entity_models(addon_path)
    original_models_root = (addon_path / "models").resolve()
    src_root = src_root.resolve()

    entries = []
    skipped = 0

    for item in decompile_results:
        src_dir_value = item.get("src_dir")
        if not src_dir_value:
            skipped += 1
            continue
        src_dir = Path(src_dir_value).resolve()
        if not src_dir.exists():
            skipped += 1
            continue
        qc_path = _find_qc_in_src(src_dir)
        if qc_path is None:
            skipped += 1
            continue

        model_rel_value = str(item.get("model_rel") or item.get("model_rel_fallback") or item.get("mdl_rel_fs") or "")
        if not model_rel_value:
            skipped += 1
            continue

        model_path = Path(model_rel_value)
        original_bundle_bytes = _sum_model_bytes(original_models_root, model_path)
        base_group, base_reason = _classify_model(model_path.as_posix(), original_bundle_bytes, entity_refs)
        ref_key = _normalize_model_ref(model_path.as_posix())
        entry = {
            "model_rel": model_path.as_posix(),
            "src_dir_rel": src_dir.relative_to(src_root).as_posix(),
            "qc_rel": qc_path.relative_to(src_root).as_posix(),
            "base_group": base_group,
            "base_reason": base_reason,
            "original_bundle_bytes": original_bundle_bytes,
            "reference_tags": sorted(entity_refs["tag_index"].get(ref_key, set())),
            "reference_families": sorted(entity_refs["family_index"].get(ref_key, set())),
            "entity_refs": entity_refs["ref_index"].get(ref_key, []),
            "preserve_original_mesh": False,
        }
        duplicate_animation_names = _duplicate_qc_animation_names(qc_path)
        entry["qc_duplicate_animation_name_count"] = len(duplicate_animation_names)
        if duplicate_animation_names:
            entry["qc_duplicate_animation_names_sample"] = duplicate_animation_names[:12]
        entry["rigid_primary_bone_fix_candidate"] = False

        if base_group == "ground_main_candidate":
            if _ground_candidate_is_confident({"reference_families": entry["reference_families"], "reason": base_reason}):
                entry["group"] = "experimental_ground_main"
                entry["pipeline"] = "experimental_ground_main"
                if duplicate_animation_names:
                    entry["rigid_primary_bone_fix_candidate"] = True
                    entry["reason"] = (
                        f"{base_reason}|final_policy_ground|rigid_primary_bone_fix_candidate:"
                        f"{len(duplicate_animation_names)}"
                    )
                else:
                    entry["reason"] = f"{base_reason}|final_policy_ground"
            else:
                entry["group"] = "baseline_uncertain_main"
                entry["pipeline"] = "baseline"
                entry["reason"] = f"{base_reason}|fallback_uncertain_main"
        elif base_group == "aircraft_main_candidate":
            entry["group"] = "baseline_aircraft"
            entry["pipeline"] = "baseline"
            entry["reason"] = f"{base_reason}|baseline_aircraft_policy"
        else:
            entry["group"] = base_group
            entry["pipeline"] = "baseline"
            entry["reason"] = base_reason

        entries.append(entry)

    counts = dict(Counter(item["group"] for item in entries))
    interpretation = _interpret_addon(entries, entity_refs)

    return {
        "mode": "experimental_ground_policy",
        "policy": _policy_descriptions(),
        "interpretation": interpretation,
        "summary": {
            "counts": counts,
            "decompile_result_count": len(decompile_results),
            "classified_entry_count": len(entries),
            "skipped_entry_count": skipped,
            "preserve_original_mesh_count": sum(1 for item in entries if item.get("preserve_original_mesh")),
            "rigid_primary_bone_fix_candidate_count": sum(
                1 for item in entries if item.get("rigid_primary_bone_fix_candidate")
            ),
            "explicit_ref_summary": entity_refs.get("summary", {}),
            "spawn_entry_count": len(entity_refs.get("spawn_entries", [])),
            "entity_family_summary": entity_refs.get("summary", {}).get("entity_family_summary", {}),
        },
        "spawn_entries": entity_refs.get("spawn_entries", []),
        "entries": entries,
    }


def write_final_policy_files(
    addon_path: Path,
    decompile_results: list[dict],
    src_root: Path,
    logs_dir: Path,
) -> tuple[Path, Path]:
    payload = build_final_policy_payload(addon_path, decompile_results, src_root)
    logs_dir.mkdir(parents=True, exist_ok=True)
    map_path = logs_dir / "selective_policy_map.json"
    summary_path = logs_dir / "selective_policy_summary.json"
    map_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    summary_payload = {
        "mode": payload["mode"],
        "interpretation": payload["interpretation"],
        "summary": payload["summary"],
        "policy": payload["policy"],
        "map_path": str(map_path),
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    return map_path, summary_path
