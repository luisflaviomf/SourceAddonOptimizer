# batch_optimize_qc.py
# Blender 3.x/4.x/5.x (background) – otimiza malhas referenciadas em QCs do Source (SMD/DMX)
#
# Corrige seu erro principal:
#   [ERRO] Calling operator "bpy.ops.import_scene.qc" error, could not be found
# …tentando auto-habilitar o addon do Blender Source Tools (io_scene_valvesource).
#
# Uso (igual ao seu):
# blender.exe --background --python "...\batch_optimize_qc.py" -- "C:\...\lvscrowbar" --ratio 0.75 --merge 0.0001 --autosmooth 45 --format smd
#
# Dicas:
# - Por padrão IGNORA *_OPT.qc (pra não reprocessar). Use --include-opt se quiser.
# - Cria uma pasta "output" ao lado de cada QC e gera um "<nome>_OPT.qc" apontando para os arquivos otimizados.

import argparse
import itertools
import math
import os
import re
import shutil
import sys
from pathlib import Path

import bpy


# ---------------------------
# Utilidades Blender
# ---------------------------

_SOURCE_OPS_LOGGED = False

SENSITIVE_BODYGROUP_STRUCTURAL_TOKENS = (
    "hood",
    "bonnet",
    "trunk",
    "boot",
    "door",
    "bumper",
    "fender",
    "wing",
    "spoiler",
    "splitter",
    "diffuser",
    "skirt",
    "flare",
    "vent",
    "scoop",
    "lip",
    "canard",
)

SENSITIVE_BODYGROUP_DETAIL_TOKENS = (
    "mirror",
    "grille",
    "grill",
    "headlight",
    "headlights",
    "taillight",
    "taillights",
    "light",
    "lights",
    "trim",
)

SENSITIVE_BODYGROUP_TOKENS = tuple(
    dict.fromkeys(SENSITIVE_BODYGROUP_STRUCTURAL_TOKENS + SENSITIVE_BODYGROUP_DETAIL_TOKENS)
)

SENSITIVE_BODYGROUP_PROFILE_DEFAULTS = {
    "structural_panel": {"floor": 0.90, "retry_floor": 0.96},
    "detail_panel": {"floor": 0.95, "retry_floor": 0.98},
    "generic_sensitive": {"floor": 0.92, "retry_floor": 0.97},
}

SENSITIVE_BODYGROUP_AUTOSMOOTH_FLOOR = 35.0
SENSITIVE_BODYGROUP_OPEN_EDGE_ABS_GROWTH_LIMIT = 24
SENSITIVE_BODYGROUP_OPEN_EDGE_REL_GROWTH_LIMIT = 0.35
SENSITIVE_BODYGROUP_TOPOLOGY_ROUND_DIGITS = 6


def _op_exists(op_path: str) -> bool:
    """
    Ex.: "import_scene.qc" -> bpy.ops.import_scene.qc
    """
    a, b = op_path.split(".", 1)
    return hasattr(getattr(bpy.ops, a, None), b)


def _get_op_callable(op_path: str):
    """
    Retorna o callable do operador (ex.: bpy.ops.export_scene.smd) ou None.
    """
    a, b = op_path.split(".", 1)
    mod = getattr(bpy.ops, a, None)
    if mod is None:
        return None
    return getattr(mod, b, None)


def _safe_operator_rna_props(op_callable):
    try:
        return op_callable.get_rna_type().properties
    except Exception:
        return None


def _describe_operator(op_path: str) -> str:
    op_callable = _get_op_callable(op_path)
    if not op_callable:
        return f"{op_path}: NOT FOUND"

    try:
        op_idname = op_callable.idname()
    except Exception:
        op_idname = "<?>"

    props = _safe_operator_rna_props(op_callable)
    if not props:
        return f"{op_path}: FOUND idname={op_idname} props=(unavailable)"

    parts = []
    for p in props:
        ident = getattr(p, "identifier", None)
        if not ident or ident == "rna_type":
            continue
        subtype = ""
        try:
            subtype = getattr(p, "subtype", "") or ""
        except Exception:
            subtype = ""
        if subtype and subtype != "NONE":
            parts.append(f"{ident}<{subtype}>")
        else:
            parts.append(ident)

    return f"{op_path}: FOUND idname={op_idname} props=[{', '.join(parts)}]"


def _find_ops_in_module(module_name: str, token: str):
    mod = getattr(bpy.ops, module_name, None)
    if mod is None:
        return []
    out = []
    token = token.lower()
    for name in dir(mod):
        if name.startswith("_"):
            continue
        if token in name.lower():
            out.append(f"{module_name}.{name}")
    return sorted(set(out))


def log_source_operator_introspection() -> None:
    """
    Loga (uma vez) quais operadores de import/export existem e quais props (RNA) aceitam.
    Isso ajuda a depurar mudan��as de addon/Blender (ex.: filepath vs file_path).
    """
    global _SOURCE_OPS_LOGGED
    if _SOURCE_OPS_LOGGED:
        return
    _SOURCE_OPS_LOGGED = True

    print("[INFO] Introspecao RNA dos operadores Source (import/export):")
    known = [
        "import_scene.qc",
        "import_scene.smd",
        "import_scene.dmx",
        "export_scene.smd",
        "export_scene.dmx",
    ]
    for op_path in known:
        print("  - " + _describe_operator(op_path))

    # Blender Source Tools recente pode usar scene.vs.export_path/export_format (sem 'filepath' no export op).
    scene = bpy.context.scene
    vs = getattr(scene, "vs", None)
    if vs is not None:
        try:
            export_list_len = len(getattr(vs, "export_list", []))
        except Exception:
            export_list_len = "?"
        try:
            print(
                "  - scene.vs: "
                f"export_path={getattr(vs, 'export_path', None)!r} "
                f"export_format={getattr(vs, 'export_format', None)!r} "
                f"export_list_len={export_list_len}"
            )
        except Exception:
            pass

    for fmt in ("smd", "dmx"):
        imp = _find_ops_in_module("import_scene", fmt)
        exp = _find_ops_in_module("export_scene", fmt)
        if imp:
            print(f"  - import_scene.* contendo '{fmt}': {', '.join(imp)}")
        if exp:
            print(f"  - export_scene.* contendo '{fmt}': {', '.join(exp)}")


def _operator_props_dict(op_callable) -> dict:
    props = _safe_operator_rna_props(op_callable)
    if not props:
        return {}
    out = {}
    for p in props:
        ident = getattr(p, "identifier", None)
        if not ident or ident == "rna_type":
            continue
        out[ident] = p
    return out


def _pick_string_prop_by_subtype(props: dict, subtype: str):
    target = (subtype or "").upper()
    if not target:
        return None
    for name, p in props.items():
        if getattr(p, "type", None) != "STRING":
            continue
        try:
            st = (getattr(p, "subtype", "") or "").upper()
        except Exception:
            st = ""
        if st == target:
            return name
    return None


def _build_export_kwargs(op_callable, dst: Path):
    """
    Retorna (kwargs, strategy_label). kwargs pode ser {} se precisar de fallback via scene props.
    """
    props = _operator_props_dict(op_callable)
    fp = dst.as_posix()

    if not props:
        # Sem RNA disponivel: tenta o padrao mais comum.
        return {"filepath": fp}, "filepath(blind)"

    # 1) Nomes mais comuns
    for name in ("filepath", "file_path", "path"):
        if name in props:
            return {name: fp}, name

    # 2) Qualquer StringProperty com subtype FILE_PATH
    file_prop = _pick_string_prop_by_subtype(props, "FILE_PATH")
    if file_prop:
        return {file_prop: fp}, f"{file_prop}<FILE_PATH>"

    # 3) directory + filename
    dir_prop = None
    for name in ("directory", "dir_path", "dirpath"):
        if name in props:
            dir_prop = name
            break
    if not dir_prop:
        dir_prop = _pick_string_prop_by_subtype(props, "DIR_PATH")

    name_prop = None
    for name in ("filename", "file_name"):
        if name in props:
            name_prop = name
            break
    if not name_prop:
        name_prop = _pick_string_prop_by_subtype(props, "FILE_NAME")

    if dir_prop and name_prop:
        directory = dst.parent.as_posix()
        if not directory.endswith("/"):
            directory += "/"
        return {dir_prop: directory, name_prop: dst.name}, f"{dir_prop}+{name_prop}"

    # 4) directory + files (OperatorFileListElement)
    if dir_prop and "files" in props:
        directory = dst.parent.as_posix()
        if not directory.endswith("/"):
            directory += "/"
        return {dir_prop: directory, "files": [{"name": dst.name}]}, f"{dir_prop}+files"

    return {}, "scene_props"


def _try_set_scene_export_path(dst: Path, fmt: str):
    """
    Alguns addons guardam o caminho no Scene e o operador nao aceita args de path.
    Tenta setar um campo string plausivel no scene e retorna o nome usado (ou None).
    """
    scene = bpy.context.scene
    fp = dst.as_posix()
    fmt = (fmt or "").lower()

    preferred = [
        f"{fmt}_export_path",
        f"export_{fmt}_path",
        f"{fmt}_path",
        "export_path",
        "file_path",
        "filepath",
    ]

    candidates = []
    for name in preferred:
        if hasattr(scene, name):
            candidates.append(name)

    if not candidates:
        for name in dir(scene):
            if name.startswith("_"):
                continue
            low = name.lower()
            if fmt and fmt not in low:
                continue
            if "export" not in low:
                continue
            if ("path" not in low) and ("file" not in low):
                continue
            candidates.append(name)

    for name in candidates:
        try:
            cur = getattr(scene, name)
        except Exception:
            continue
        if not isinstance(cur, str):
            continue
        try:
            setattr(scene, name, fp)
            return name
        except Exception:
            continue

    return None


def _sanitise_filename_basic(name: str) -> str:
    out = name
    for badchar in '/?<>\\:*|"':
        out = out.replace(badchar, "_")
    return out


def _try_import_valvesource_utils():
    """
    Blender Source Tools costuma ser io_scene_valvesource, mas existem variantes.
    Retorna o modulo utils ou None.
    """
    for mod in (
        "io_scene_valvesource",
        "io_scene_valvesource_beta",
        "io_scene_source",
        "io_scene_smd",
    ):
        try:
            return __import__(f"{mod}.utils", fromlist=["utils"])
        except Exception:
            continue
    return None


def _looks_like_valvesource_exporter(op_callable) -> bool:
    props = _operator_props_dict(op_callable)
    if "collection" not in props or "export_scene" not in props:
        return False
    scene = bpy.context.scene
    vs = getattr(scene, "vs", None)
    return bool(vs) and hasattr(vs, "export_path") and hasattr(vs, "export_format")


def _export_via_valvesource_collection(op_callable, dst: Path, fmt: str):
    """
    Suporte especifico para o Blender Source Tools recente (io_scene_valvesource),
    onde export_scene.smd NAO aceita filepath e usa scene.vs.export_path/export_format.
    """
    scene = bpy.context.scene
    vs = getattr(scene, "vs", None)
    if not vs:
        raise RuntimeError("Scene nao tem 'vs' (ValveSource_SceneProps).")

    out_dir = dst.parent
    export_format = "SMD" if fmt == "smd" else "DMX"

    try:
        vs.export_format = export_format
    except Exception:
        pass
    try:
        vs.export_path = str(out_dir.resolve())
    except Exception:
        vs.export_path = out_dir.as_posix()

    # evita passos extra (compile de QC) em batch/headless
    try:
        if hasattr(vs, "qc_compile"):
            vs.qc_compile = False
    except Exception:
        pass

    utils_mod = _try_import_valvesource_utils()
    if utils_mod and hasattr(utils_mod, "State"):
        try:
            utils_mod.State.update_scene(scene)
        except Exception as e:
            print(f"[WARN] ValveSource State.update_scene falhou: {e}")

    col_name = dst.stem
    export_col = bpy.data.collections.get(col_name)
    if export_col is None:
        export_col = bpy.data.collections.new(col_name)

    # garante que a collection existe no scene
    try:
        root_col = scene.collection
        if export_col.name not in {c.name for c in root_col.children}:
            root_col.children.link(export_col)
    except Exception:
        pass

    # configura collection pra exportar no root (sem subdir) e nao mutada
    try:
        if hasattr(export_col, "vs"):
            if hasattr(export_col.vs, "subdir"):
                export_col.vs.subdir = ""
            if hasattr(export_col.vs, "mute"):
                export_col.vs.mute = False
    except Exception:
        pass

    # linka tudo que esta no scene pra collection e marca export=True quando existir
    for ob in list(scene.objects):
        try:
            export_col.objects.link(ob)
        except Exception:
            pass
        try:
            if hasattr(ob, "vs") and hasattr(ob.vs, "export"):
                ob.vs.export = True
        except Exception:
            pass

    if utils_mod and hasattr(utils_mod, "State"):
        try:
            utils_mod.State.update_scene(scene)
        except Exception:
            pass

    result = op_callable("EXEC_DEFAULT", collection=export_col.name, export_scene=False)

    # destino esperado: <export_path>/<subdir>/<name>.<ext>
    if dst.exists():
        return result

    # se o addon sanitizou o nome, tenta renomear pro nome esperado
    alt = dst.with_name(_sanitise_filename_basic(dst.stem) + dst.suffix)
    if alt.exists() and alt != dst:
        try:
            alt.replace(dst)
        except Exception:
            pass
        if dst.exists():
            return result

    raise RuntimeError(f"Arquivo nao foi criado pelo exporter: {dst}")


def export_mesh(dst: Path, fmt: str = "smd") -> None:
    """
    Export robusto para SMD/DMX: detecta operador, props aceitas e escolhe a melhor estrategia.
    Gera erro detalhado se nao conseguir exportar.
    """
    fmt = (fmt or "smd").lower()
    if fmt not in ("smd", "dmx"):
        raise ValueError(f"Formato de export desconhecido: {fmt}")

    # Export precisa selecionar tudo (malha + armature) pra manter rig
    select_all_objects()

    preferred = [f"export_scene.{fmt}"]
    # Alguns addons exportam DMX via export_scene.smd (controlado por props/scene).
    if fmt == "dmx" and _op_exists("export_scene.smd"):
        preferred.append("export_scene.smd")
    candidates = []
    for p in preferred:
        if _op_exists(p) and p not in candidates:
            candidates.append(p)
    candidates.extend([p for p in _find_ops_in_module("export_scene", fmt) if p not in candidates])

    if not candidates:
        available = ", ".join(dir(getattr(bpy.ops, "export_scene", object()))) or "(none)"
        raise RuntimeError(
            f"Nenhum operador de export para '{fmt}' encontrado. "
            f"Tentei: {preferred}. export_scene ops: {available}"
        )

    errors = []
    for op_path in candidates:
        op_callable = _get_op_callable(op_path)
        if not op_callable:
            continue

        kwargs, strategy = _build_export_kwargs(op_callable, dst)
        print(f"[EXPORT] fmt={fmt} op={op_path} strategy={strategy}")
        if kwargs:
            print(f"[EXPORT] kwargs={kwargs}")
        else:
            print("[EXPORT] kwargs=(none)")

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if kwargs:
                result = op_callable("EXEC_DEFAULT", **kwargs)
            else:
                if _looks_like_valvesource_exporter(op_callable):
                    print("[EXPORT] strategy=valvesource(scene.vs.export_path/export_format + collection)")
                    result = _export_via_valvesource_collection(op_callable, dst, fmt)
                else:
                    used = _try_set_scene_export_path(dst, fmt)
                    print(f"[EXPORT] scene_prop={used if used else '(none)'}")
                    result = op_callable("EXEC_DEFAULT")

            # Normalmente retorna {'FINISHED'}; mas alguns addons retornam None.
            if not dst.exists():
                raise RuntimeError(f"Operador retornou {result!r}, mas arquivo nao foi criado: {dst}")
            return
        except Exception as e:
            errors.append(f"{op_path} ({strategy}): {e}")
            continue

    details = "\n".join("  - " + s for s in errors)
    props_dump = "\n".join("  - " + _describe_operator(p) for p in candidates)
    raise RuntimeError(
        f"Falhou export '{dst}' (fmt={fmt}). Tentativas:\n{details}\n\nOperadores/props detectados:\n{props_dump}"
    )


def ensure_source_tools_enabled() -> None:
    """
    Tenta habilitar Blender Source Tools (Valve Source) automaticamente.
    Se não estiver instalado, o script vai falhar com uma mensagem clara.
    """
    candidates = [
        "io_scene_valvesource",          # Blender Source Tools (nome mais comum)
        "io_scene_valvesource_beta",
        "io_scene_source",               # variações antigas/alternativas
        "io_scene_smd",
    ]

    # Se já tem algum import/export do Source, ok
    if _op_exists("import_scene.qc") or _op_exists("import_scene.smd") or _op_exists("import_scene.dmx"):
        return

    for mod in candidates:
        try:
            bpy.ops.preferences.addon_enable(module=mod)
        except Exception:
            pass

    # Recheca
    if _op_exists("import_scene.qc") or _op_exists("import_scene.smd") or _op_exists("import_scene.dmx"):
        return

    msg = (
        "\n[ERRO] Não encontrei operadores de import do Source no Blender.\n"
        "Você precisa instalar/ativar o addon 'Blender Source Tools' (Valve Source).\n"
        "Depois, rode de novo.\n\n"
        "Sinais de que está ok:\n"
        "- bpy.ops.import_scene.qc (import de QC)\n"
        "- bpy.ops.import_scene.smd / bpy.ops.export_scene.smd\n"
        "- bpy.ops.import_scene.dmx / bpy.ops.export_scene.dmx\n"
    )
    raise RuntimeError(msg)


def clean_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False, confirm=False)

    # purge orphans
    try:
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    except TypeError:
        # versões antigas
        try:
            bpy.ops.outliner.orphans_purge()
        except Exception:
            pass


def select_all_objects() -> None:
    for obj in bpy.context.scene.objects:
        obj.select_set(True)
    # define um ativo
    mesh = next((o for o in bpy.context.scene.objects if o.type == "MESH"), None)
    if mesh:
        bpy.context.view_layer.objects.active = mesh
    else:
        any_obj = next((o for o in bpy.context.scene.objects), None)
        if any_obj:
            bpy.context.view_layer.objects.active = any_obj


def _apply_planar_decimate(obj, planar_angle_deg: float) -> None:
    if planar_angle_deg <= 0.0:
        return

    mod = obj.modifiers.new(name="DECIMATE_PLANAR", type="DECIMATE")
    try:
        mod.decimate_type = "DISSOLVE"
    except Exception:
        pass
    if hasattr(mod, "angle_limit"):
        mod.angle_limit = math.radians(float(planar_angle_deg))
    if hasattr(mod, "use_dissolve_boundaries"):
        mod.use_dissolve_boundaries = False
    if hasattr(mod, "delimit"):
        try:
            mod.delimit = {"NORMAL", "MATERIAL", "SEAM"}
        except Exception:
            pass
    bpy.ops.object.modifier_apply(modifier=mod.name)


def apply_mesh_ops(merge_dist: float, decimate_ratio: float, autosmooth_deg: float, use_planar: bool = False, planar_angle_deg: float = 2.0) -> None:
    # autosmooth em todas as malhas
    angle = math.radians(float(autosmooth_deg))
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        me = obj.data
        try:
            me.use_auto_smooth = True
            me.auto_smooth_angle = angle
        except Exception:
            pass

    # merge by distance + decimate em cada mesh
    for obj in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        # Merge (0 ou negativo desliga)
        if merge_dist > 0.0:
            try:
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.mesh.select_all(action="SELECT")
                # Blender 2.8+ geralmente tem merge_by_distance
                try:
                    bpy.ops.mesh.merge_by_distance(distance=merge_dist)
                except Exception:
                    # fallback antigo
                    bpy.ops.mesh.remove_doubles(threshold=merge_dist)
            finally:
                try:
                    bpy.ops.object.mode_set(mode="OBJECT")
                except Exception:
                    pass

        if use_planar:
            try:
                _apply_planar_decimate(obj, planar_angle_deg)
            except Exception as e:
                print(f"[WARN] Falhou planar decimate em '{obj.name}': {e}")

        # Decimate (ratio)
        if decimate_ratio < 1.0:
            try:
                mod = obj.modifiers.new(name="DECIMATE_BATCH", type="DECIMATE")
                mod.ratio = float(decimate_ratio)
                bpy.ops.object.modifier_apply(modifier=mod.name)
            except Exception as e:
                print(f"[WARN] Falhou decimate em '{obj.name}': {e}")

        obj.select_set(False)


# ---------------------------
# Parse de QC (pega apenas SMD/DMX de MALHA, não animações)
# ---------------------------

_re_quoted = re.compile(r'"([^"]+)"')


def _extract_smd_dmx_token(line: str):
    q = _re_quoted.findall(line)
    cand = None
    if q:
        # geralmente o arquivo e o ultimo quoted
        for t in reversed(q):
            if t.lower().endswith((".smd", ".dmx")):
                cand = t
                break
    else:
        toks = line.split()
        for t in reversed(toks):
            tt = t.strip().strip('"').strip()
            if tt.lower().endswith((".smd", ".dmx")):
                cand = tt
                break
    if cand:
        if f'"{cand}"' in line:
            return f'"{cand}"', cand
        return cand, cand
    return None, None


def extract_file_refs_from_qc(qc_text: str):
    """
    Retorna lista de (ref_original, ref_sem_aspas, kind) para SMD/DMX.
    kind: "mesh" ou "physics".
    - $body / $model
    - $bodygroup { studio "x.smd" }
    - $collisionmodel / $collisionjoints
    Ignora $sequence (animaÇõÇæes).
    """
    refs = []
    lines = qc_text.splitlines()

    in_bodygroup = False
    for raw in lines:
        line = _strip_comments(raw).strip()
        if not line:
            continue

        low = line.lower()

        # entra/sai de bodygroup block
        if low.startswith("$bodygroup"):
            in_bodygroup = True
        if in_bodygroup and "{" in line:
            pass
        if in_bodygroup and "}" in line:
            in_bodygroup = False
            continue

        if low.startswith("$sequence"):
            continue

        if low.startswith("$body") or low.startswith("$model"):
            full_tok, path_tok = _extract_smd_dmx_token(line)
            if full_tok and path_tok:
                refs.append((full_tok, path_tok, "mesh"))
            continue

        if in_bodygroup:
            if "studio" in low and (".smd" in low or ".dmx" in low):
                full_tok, path_tok = _extract_smd_dmx_token(line)
                if full_tok and path_tok:
                    refs.append((full_tok, path_tok, "mesh"))
            continue

        if low.startswith("$collisionmodel") or low.startswith("$collisionjoints"):
            full_tok, path_tok = _extract_smd_dmx_token(line)
            if full_tok and path_tok:
                refs.append((full_tok, path_tok, "physics"))

    # remove duplicados mantendo ordem (se aparecer como physics, prioriza physics)
    out = []
    idx_map = {}
    for full_tok, path_tok, kind in refs:
        key = path_tok.lower()
        if key in idx_map:
            idx = idx_map[key]
            prev_kind = out[idx][2]
            if prev_kind != "physics" and kind == "physics":
                out[idx] = (out[idx][0], out[idx][1], "physics")
            continue
        idx_map[key] = len(out)
        out.append((full_tok, path_tok, kind))
    return out


def _extract_bodygroup_name(line: str) -> str:
    quoted = _re_quoted.findall(line)
    if quoted:
        return quoted[0].strip()
    parts = line.split(None, 1)
    if len(parts) < 2:
        return ""
    tail = parts[1].strip()
    if tail.startswith("{"):
        return ""
    return tail.split("{", 1)[0].strip().strip('"')


def _normalize_context_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _contains_any_token(text: str, token_set) -> bool:
    normalized = _normalize_context_label(text)
    if not normalized:
        return False
    tokens = set(normalized.split())
    return any(token in tokens or token in normalized for token in token_set)


def _contains_sensitive_bodygroup_token(text: str) -> bool:
    return _contains_any_token(text, SENSITIVE_BODYGROUP_TOKENS)


def extract_file_ref_metadata_from_qc(qc_text: str) -> dict[str, dict]:
    metadata: dict[str, dict] = {}
    lines = qc_text.splitlines()

    in_bodygroup = False
    current_bodygroup = ""
    for raw in lines:
        line = _strip_comments(raw).strip()
        if not line:
            continue

        low = line.lower()
        if low.startswith("$bodygroup"):
            current_bodygroup = _extract_bodygroup_name(line)
            in_bodygroup = True
            if "}" in line:
                in_bodygroup = False
            continue

        if in_bodygroup and "}" in line:
            in_bodygroup = False
            current_bodygroup = ""
            continue

        full_tok, path_tok = _extract_smd_dmx_token(line)
        if not full_tok or not path_tok:
            continue

        key = normalize_qc_path_token(path_tok).lower()
        if in_bodygroup and "studio" in low:
            metadata[key] = {
                "context_type": "bodygroup",
                "context_name": current_bodygroup,
            }
            continue

        if low.startswith("$body") or low.startswith("$model"):
            metadata[key] = {
                "context_type": "model",
                "context_name": "",
            }
            continue

        if low.startswith("$collisionmodel") or low.startswith("$collisionjoints"):
            metadata[key] = {
                "context_type": "physics",
                "context_name": "",
            }

    return metadata

def _strip_comments(line: str) -> str:
    # QC normalmente usa // comentário
    if "//" in line:
        return line.split("//", 1)[0]
    return line

def extract_mesh_file_refs_from_qc(qc_text: str):
    """
    Retorna lista de (ref_original, ref_sem_aspas) para SMD/DMX de malha.
    - $body / $model  (2o argumento costuma ser o arquivo)
    - $bodygroup { studio "x.smd" }
    Ignora $sequence (animações).
    """
    refs = []
    lines = qc_text.splitlines()

    in_bodygroup = False
    for raw in lines:
        line = _strip_comments(raw).strip()
        if not line:
            continue

        low = line.lower()

        # entra/sai de bodygroup block
        if low.startswith("$bodygroup"):
            in_bodygroup = True
        if in_bodygroup and "{" in line:
            # continua no bloco
            pass
        if in_bodygroup and "}" in line:
            in_bodygroup = False
            continue

        # ignora sequences (animações)
        if low.startswith("$sequence"):
            continue

        # $body / $model
        if low.startswith("$body") or low.startswith("$model"):
            # tenta pegar o último token entre aspas que pareça arquivo
            q = _re_quoted.findall(line)
            cand = None
            if q:
                # geralmente o arquivo é o último quoted
                for t in reversed(q):
                    if t.lower().endswith((".smd", ".dmx")):
                        cand = t
                        break
            else:
                # fallback: tokens "soltos"
                toks = line.split()
                for t in reversed(toks):
                    tt = t.strip().strip('"').strip()
                    if tt.lower().endswith((".smd", ".dmx")):
                        cand = tt
                        break
            if cand:
                # preserva como aparece no QC (com aspas se tiver)
                if f'"{cand}"' in line:
                    refs.append((f'"{cand}"', cand))
                else:
                    refs.append((cand, cand))
            continue

        # dentro de $bodygroup: procura "studio <file>"
        if in_bodygroup:
            if "studio" in low and (".smd" in low or ".dmx" in low):
                q = _re_quoted.findall(line)
                cand = None
                if q:
                    for t in reversed(q):
                        if t.lower().endswith((".smd", ".dmx")):
                            cand = t
                            break
                else:
                    toks = line.split()
                    for t in reversed(toks):
                        tt = t.strip().strip('"').strip()
                        if tt.lower().endswith((".smd", ".dmx")):
                            cand = tt
                            break
                if cand:
                    if f'"{cand}"' in line:
                        refs.append((f'"{cand}"', cand))
                    else:
                        refs.append((cand, cand))

    # remove duplicados mantendo ordem
    seen = set()
    out = []
    for full_tok, path_tok in refs:
        key = path_tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((full_tok, path_tok))
    return out


def normalize_qc_path_token(path_token: str) -> str:
    # QC costuma aceitar / ou \. Vamos padronizar para /
    return path_token.replace("\\", "/").lstrip("./")


def _is_sensitive_bodygroup_mesh(src_file: Path, kind: str, ref_meta: dict | None) -> bool:
    if kind != "mesh":
        return False
    if not ref_meta or ref_meta.get("context_type") != "bodygroup":
        return False

    bodygroup_name = str(ref_meta.get("context_name") or "")
    stem = src_file.stem
    return _contains_sensitive_bodygroup_token(bodygroup_name) or _contains_sensitive_bodygroup_token(stem)


def _classify_sensitive_bodygroup_mesh(src_file: Path, kind: str, ref_meta: dict | None) -> dict | None:
    if not _is_sensitive_bodygroup_mesh(src_file, kind, ref_meta):
        return None

    bodygroup_name = str((ref_meta or {}).get("context_name") or "")
    combined = f"{bodygroup_name} {src_file.stem}"
    if _contains_any_token(combined, SENSITIVE_BODYGROUP_STRUCTURAL_TOKENS):
        label = "structural_panel"
    elif _contains_any_token(combined, SENSITIVE_BODYGROUP_DETAIL_TOKENS):
        label = "detail_panel"
    else:
        label = "generic_sensitive"

    defaults = SENSITIVE_BODYGROUP_PROFILE_DEFAULTS[label]
    return {
        "label": label,
        "bodygroup": bodygroup_name,
        "floor": float(defaults["floor"]),
        "retry_floor": float(defaults["retry_floor"]),
    }


def _copy_ref_passthrough(src_file: Path, out_file: Path, out_fmt: str) -> bool:
    desired_ext = ".dmx" if str(out_fmt).lower() == "dmx" else ".smd"
    if src_file.suffix.lower() != desired_ext:
        return False
    out_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_file, out_file)
    return True


def _export_ref_with_settings(
    src_file: Path,
    out_file: Path,
    kind: str,
    out_fmt: str,
    *,
    merge_dist: float,
    decimate_ratio: float,
    autosmooth_deg: float,
    use_planar: bool,
    planar_angle_deg: float,
) -> bool:
    clean_scene()

    try:
        import_source_file(src_file)
    except Exception as e:
        print(f"[ERRO] falhou import '{src_file}': {e}")
        clean_scene()
        return False

    if kind == "mesh":
        try:
            apply_mesh_ops(merge_dist, decimate_ratio, autosmooth_deg, use_planar, planar_angle_deg)
        except Exception as e:
            print(f"[WARN] Falhou otimizacao em '{src_file.name}': {e}")

    try:
        export_source_file(out_file, out_fmt)
    except Exception as e:
        print(f"[ERRO] falhou export '{out_file}': {e}")
        clean_scene()
        return False

    clean_scene()
    return True


def extract_skinfamilies_from_qc(qc_text: str):
    """
    Retorna lista de linhas (cada uma lista de materiais) do $texturegroup "skinfamilies".
    """
    m = re.search(r'\$texturegroup\s+"skinfamilies"\s*\{', qc_text, flags=re.IGNORECASE)
    if not m:
        return []
    start = m.end()
    depth = 1
    i = start
    end = None
    while i < len(qc_text):
        ch = qc_text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
        i += 1
    if end is None:
        return []
    block = qc_text[start:end]
    rows = []
    for raw in block.splitlines():
        line = _strip_comments(raw).strip()
        if not line.startswith("{"):
            continue
        toks = _re_quoted.findall(line)
        if toks:
            rows.append(toks)
    return rows


def _dedupe_preserve(items):
    seen = set()
    out = []
    for it in items:
        key = it.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# ---------------------------
# Pós-processamento SMD (corrige physics)
# ---------------------------

def _bbox_extents(bbox):
    (mn, mx) = bbox
    return (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])


def _bbox_center(bbox):
    (mn, mx) = bbox
    return ((mn[0] + mx[0]) * 0.5, (mn[1] + mx[1]) * 0.5, (mn[2] + mx[2]) * 0.5)


def compute_smd_bbox(filepath: Path):
    """
    Retorna bbox ((minx,miny,minz),(maxx,maxy,maxz)) da seção 'triangles' de um SMD,
    ou None se não conseguir extrair.
    """
    try:
        f = filepath.open("r", encoding="utf-8", errors="ignore")
    except Exception:
        return None

    with f:
        in_triangles = False
        any_v = False
        minx = miny = minz = float("inf")
        maxx = maxy = maxz = float("-inf")

        for raw in f:
            s = raw.strip()
            if not in_triangles:
                if s == "triangles":
                    in_triangles = True
                continue
            if s == "end":
                break

            parts = s.split()
            if not parts:
                continue
            if not parts[0].lstrip("-").isdigit():
                continue
            if len(parts) < 4:
                continue

            try:
                x = float(parts[1])
                y = float(parts[2])
                z = float(parts[3])
            except Exception:
                continue

            any_v = True
            if x < minx:
                minx = x
            if y < miny:
                miny = y
            if z < minz:
                minz = z
            if x > maxx:
                maxx = x
            if y > maxy:
                maxy = y
            if z > maxz:
                maxz = z

        if not any_v:
            return None
        return ((minx, miny, minz), (maxx, maxy, maxz))


def compute_smd_topology_stats(filepath: Path):
    """
    Retorna estatísticas topológicas aproximadas para um SMD:
    - triangles: quantidade de triângulos
    - open_edges: arestas que aparecem uma única vez

    A contagem usa as posições dos vértices com arredondamento estável.
    É suficiente para detectar quando uma malha fechada passa a abrir
    durante o decimate em painéis/bodygroups sensíveis.
    """
    try:
        lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None

    try:
        i = lines.index("triangles") + 1
    except ValueError:
        return None

    triangles = 0
    edges = {}

    def _vertex_key(line: str):
        parts = line.split()
        if len(parts) < 4:
            raise ValueError(f"vertex line incompleta: {line!r}")
        return (
            round(float(parts[1]), SENSITIVE_BODYGROUP_TOPOLOGY_ROUND_DIGITS),
            round(float(parts[2]), SENSITIVE_BODYGROUP_TOPOLOGY_ROUND_DIGITS),
            round(float(parts[3]), SENSITIVE_BODYGROUP_TOPOLOGY_ROUND_DIGITS),
        )

    while i < len(lines):
        if lines[i].strip() == "end":
            break
        if i + 3 >= len(lines):
            return None
        try:
            v1 = _vertex_key(lines[i + 1])
            v2 = _vertex_key(lines[i + 2])
            v3 = _vertex_key(lines[i + 3])
        except Exception:
            return None

        triangles += 1
        for edge in (
            tuple(sorted((v1, v2))),
            tuple(sorted((v2, v3))),
            tuple(sorted((v3, v1))),
        ):
            edges[edge] = edges.get(edge, 0) + 1
        i += 4

    return {
        "triangles": triangles,
        "open_edges": sum(1 for count in edges.values() if count == 1),
    }


def _validate_sensitive_bodygroup_topology(src_file: Path, out_file: Path):
    src_stats = compute_smd_topology_stats(src_file)
    out_stats = compute_smd_topology_stats(out_file)
    if not src_stats or not out_stats:
        return False, "topology_stats_unavailable", src_stats, out_stats

    open_before = int(src_stats["open_edges"])
    open_after = int(out_stats["open_edges"])
    if open_after <= open_before:
        return True, "ok", src_stats, out_stats

    if open_before == 0:
        return False, "closed_mesh_opened", src_stats, out_stats

    open_delta = open_after - open_before
    abs_limit = SENSITIVE_BODYGROUP_OPEN_EDGE_ABS_GROWTH_LIMIT
    rel_limit = math.ceil(open_before * SENSITIVE_BODYGROUP_OPEN_EDGE_REL_GROWTH_LIMIT)
    trigger_delta = max(abs_limit, rel_limit)
    trigger_after = max(open_before + abs_limit, math.ceil(open_before * (1.0 + SENSITIVE_BODYGROUP_OPEN_EDGE_REL_GROWTH_LIMIT)))

    if open_delta >= trigger_delta and open_after >= trigger_after:
        return False, "open_edges_regressed", src_stats, out_stats

    return True, "ok", src_stats, out_stats


def _read_smd_triangles(filepath: Path):
    try:
        lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None, None, None

    header = []
    end_lines = []
    triangles = []
    i = 0
    while i < len(lines):
        header.append(lines[i])
        if lines[i].strip().lower() == "triangles":
            i += 1
            break
        i += 1
    if not header or header[-1].strip().lower() != "triangles":
        return None, None, None

    while i < len(lines):
        mat = lines[i].strip()
        if mat.lower() == "end":
            end_lines = lines[i:]
            break
        if i + 3 >= len(lines):
            break
        v1 = lines[i + 1]
        v2 = lines[i + 2]
        v3 = lines[i + 3]
        triangles.append((mat, [v1, v2, v3]))
        i += 4
    if not end_lines:
        end_lines = ["end"]
    return header, triangles, end_lines


def _write_smd_triangles(filepath: Path, header, triangles, end_lines):
    out_lines = []
    out_lines.extend(header)
    for mat, verts in triangles:
        out_lines.append(mat)
        out_lines.extend(verts)
    out_lines.extend(end_lines)
    filepath.write_text("\n".join(out_lines) + "\n", encoding="utf-8", errors="ignore")


def _parse_vertex_tokens(line: str):
    toks = line.strip().split()
    if len(toks) < 9:
        return None
    return toks


def _format_vertex_tokens(tokens):
    return " ".join(tokens)


def _compute_dummy_triangle_vertices(ref_tokens, center, eps):
    cx, cy, cz = center
    coords = [
        (cx, cy, cz),
        (cx + eps, cy, cz),
        (cx, cy + eps, cz),
    ]
    out = []
    for x, y, z in coords:
        t = list(ref_tokens)
        t[1] = f"{x:.6f}"
        t[2] = f"{y:.6f}"
        t[3] = f"{z:.6f}"
        out.append(_format_vertex_tokens(t))
    return out


def enforce_smd_material_order(smd_path: Path, target_order: list[str]):
    header, triangles, end_lines = _read_smd_triangles(smd_path)
    if header is None or triangles is None:
        return {"status": "no_triangles"}

    groups = {}
    for mat, verts in triangles:
        key = mat.lower()
        if key not in groups:
            groups[key] = {"name": mat, "tris": []}
        groups[key]["tris"].append(verts)

    # Reference vertex tokens for dummy triangles.
    ref_tokens = None
    for _mat, verts in triangles:
        ref_tokens = _parse_vertex_tokens(verts[0])
        if ref_tokens:
            break
    if not ref_tokens:
        return {"status": "no_ref_vertex"}

    bbox = compute_smd_bbox(smd_path)
    if bbox:
        ctr = _bbox_center(bbox)
        ext = _bbox_extents(bbox)
        max_dim = max(ext)
        eps = max_dim * 0.0001 if max_dim > 0 else 0.001
    else:
        ctr = (0.0, 0.0, 0.0)
        eps = 0.001

    new_tris = []
    missing = []
    used = set()

    for mat in target_order:
        key = mat.lower()
        if key in groups:
            for verts in groups[key]["tris"]:
                new_tris.append((groups[key]["name"], verts))
            used.add(key)
        else:
            missing.append(mat)
            dummy = _compute_dummy_triangle_vertices(ref_tokens, ctr, eps)
            new_tris.append((mat, dummy))
            used.add(key)

    # Append any materials not in target_order (preserve original order).
    for mat, verts in triangles:
        key = mat.lower()
        if key in used:
            continue
        new_tris.append((mat, verts))
        used.add(key)

    _write_smd_triangles(smd_path, header, new_tris, end_lines)
    return {"status": "ok", "missing": missing, "total": len(target_order)}


def _parse_smd_nodes(lines):
    nodes = {}
    in_nodes = False
    for ln in lines:
        s = ln.strip()
        if s == "nodes":
            in_nodes = True
            continue
        if in_nodes:
            if s == "end":
                break
            parts = s.split()
            if len(parts) >= 3 and parts[0].isdigit():
                idx = int(parts[0])
                if '"' in ln:
                    try:
                        name = ln.split('"')[1]
                    except Exception:
                        name = parts[1]
                else:
                    name = parts[1]
                nodes[idx] = name
    return nodes


def _iter_smd_triangle_vertices(lines):
    in_triangles = False
    current_mat = None
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not in_triangles:
            if s == "triangles":
                in_triangles = True
            i += 1
            continue
        if s == "end":
            break
        if current_mat is None:
            current_mat = s
            i += 1
            continue
        for _ in range(3):
            if i >= len(lines):
                return
            vln = lines[i].strip()
            i += 1
            if vln:
                yield current_mat, vln
        current_mat = None


def _parse_smd_links(vln):
    parts = vln.split()
    if len(parts) < 9:
        return []
    base_bone = parts[0]
    if len(parts) == 9:
        try:
            return [(int(float(base_bone)), 1.0)]
        except Exception:
            return []
    try:
        num = int(float(parts[9]))
    except Exception:
        try:
            return [(int(float(base_bone)), 1.0)]
        except Exception:
            return []
    links = []
    j = 10
    for _ in range(num):
        if j + 1 >= len(parts):
            break
        try:
            b = int(float(parts[j]))
            w = float(parts[j + 1])
        except Exception:
            break
        links.append((b, w))
        j += 2
    if not links:
        try:
            return [(int(float(base_bone)), 1.0)]
        except Exception:
            return []
    return links


def validate_smd_weights(filepath: Path):
    try:
        lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None
    nodes = _parse_smd_nodes(lines)
    multi = 0
    glass_bleed = 0
    for mat, vln in _iter_smd_triangle_vertices(lines):
        links = _parse_smd_links(vln)
        if not links:
            continue
        if len(links) > 1:
            multi += 1
            mat_l = mat.lower()
            if "glass" in mat_l:
                names = {nodes.get(b, str(b)) for b, _ in links}
                if "Car" in names and ("LD" in names or "RD" in names):
                    glass_bleed += 1
    return {"multi": multi, "glass_bleed": glass_bleed}


def _perm_parity(perm):
    # +1 se par, -1 se impar
    perm = list(perm)
    seen = [False] * len(perm)
    parity = 0
    for i in range(len(perm)):
        if seen[i]:
            continue
        j = i
        cycle_len = 0
        while not seen[j]:
            seen[j] = True
            j = perm[j]
            cycle_len += 1
        if cycle_len > 0:
            parity += cycle_len - 1
    return 1 if (parity % 2 == 0) else -1


def _det_from_perm_signs(perm, signs):
    return _perm_parity(perm) * signs[0] * signs[1] * signs[2]


def _iter_axis_transforms(allow_reflection: bool):
    for perm in itertools.permutations((0, 1, 2)):
        for signs in itertools.product((-1, 1), repeat=3):
            det = _det_from_perm_signs(perm, signs)
            if not allow_reflection and det < 0:
                continue
            yield perm, signs, det


def _transform_cost(perm, signs):
    sign_cost = sum(1 for s in signs if s < 0)
    perm_cost = sum(1 for i, p in enumerate(perm) if i != p)
    return sign_cost * 0.5 + perm_cost


def _apply_axis_transform(x: float, y: float, z: float, perm, signs):
    vals = (x, y, z)
    return (
        signs[0] * vals[perm[0]],
        signs[1] * vals[perm[1]],
        signs[2] * vals[perm[2]],
    )


def _is_identity_transform(perm, signs) -> bool:
    return perm == (0, 1, 2) and signs == (1, 1, 1)


def format_axis_transform(perm, signs) -> str:
    axes = ("x", "y", "z")
    parts = []
    for i in range(3):
        sign = "-" if signs[i] < 0 else ""
        parts.append(f"{axes[i]}={sign}{axes[perm[i]]}")
    return " ".join(parts)


def transform_bbox_axes(bbox, perm, signs):
    """
    Transform linear (permutaixos + sinais) aplicado ao bbox.
    """
    (mn, mx) = bbox
    corners = [
        (mn[0], mn[1], mn[2]),
        (mn[0], mn[1], mx[2]),
        (mn[0], mx[1], mn[2]),
        (mn[0], mx[1], mx[2]),
        (mx[0], mn[1], mn[2]),
        (mx[0], mn[1], mx[2]),
        (mx[0], mx[1], mn[2]),
        (mx[0], mx[1], mx[2]),
    ]
    out_pts = [_apply_axis_transform(p[0], p[1], p[2], perm, signs) for p in corners]
    minx = min(p[0] for p in out_pts)
    miny = min(p[1] for p in out_pts)
    minz = min(p[2] for p in out_pts)
    maxx = max(p[0] for p in out_pts)
    maxy = max(p[1] for p in out_pts)
    maxz = max(p[2] for p in out_pts)
    return ((minx, miny, minz), (maxx, maxy, maxz))


def bbox_score(candidate_bbox, ref_bbox) -> float:
    """
    Score 'quanto parece' com o bbox de referência:
    - compara proporções (extents normalizados)
    - compara centro (normalizado pelo tamanho de referência)
    """
    pe = _bbox_extents(candidate_bbox)
    re = _bbox_extents(ref_bbox)
    pc = _bbox_center(candidate_bbox)
    rc = _bbox_center(ref_bbox)

    pmax = max(pe) if pe else 1.0
    rmax = max(re) if re else 1.0

    pe_n = [(e / pmax) if pmax else 0.0 for e in pe]
    re_n = [(e / rmax) if rmax else 0.0 for e in re]

    s = sum(abs(pe_n[i] - re_n[i]) for i in range(3))
    # centro normalizado
    for i in range(3):
        denom = re[i] if re[i] else 1.0
        s += abs(pc[i] - rc[i]) / denom
    return float(s)


def _rot_z_candidates():
    # Z rotations (0/90/180/270) with optional Z inversion
    mapping = {
        0: ((0, 1, 2), (1, 1, 1)),
        90: ((1, 0, 2), (-1, 1, 1)),
        180: ((0, 1, 2), (-1, -1, 1)),
        270: ((1, 0, 2), (1, -1, 1)),
    }
    out = []
    for rot, (perm, signs) in mapping.items():
        for invz in (False, True):
            s = signs
            if invz:
                s = (s[0], s[1], -s[2])
            out.append((rot, invz, perm, s))
    return out


def pick_physics_transform(phys_bbox, ref_bbox, allow_reflection: bool = False):
    """
    Decide (perm, signs, det) usando apenas rotacoes em Z + invert Z.
    Retorna identidade se nao houver melhora clara.
    """
    if not phys_bbox or not ref_bbox:
        ident = ((0, 1, 2), (1, 1, 1), 1)
        return ident, {}

    candidates = []
    for rot, invz, perm, signs in _rot_z_candidates():
        bb = transform_bbox_axes(phys_bbox, perm, signs)
        score = bbox_score(bb, ref_bbox)
        cost = _transform_cost(perm, signs)
        det = _det_from_perm_signs(perm, signs)
        candidates.append({
            "rot": rot,
            "invz": invz,
            "perm": perm,
            "signs": signs,
            "det": det,
            "score": score,
            "cost": cost,
        })

    candidates.sort(key=lambda c: (c["score"], c["cost"]))
    best = candidates[0]

    id_score = None
    for c in candidates:
        if c["rot"] == 0 and c["invz"] is False:
            id_score = c["score"]
            break
    if id_score is None:
        id_score = best["score"]

    best_score = best["score"]
    improve = id_score - best_score
    top = candidates[:3]

    meta = {
        "best": best,
        "best_score": best_score,
        "id_score": id_score,
        "improve": improve,
        "top": top,
        "all": candidates,
    }

    # Se a melhor opcao e identidade, nada a fazer
    if best["rot"] == 0 and best["invz"] is False:
        return ((0, 1, 2), (1, 1, 1), 1), meta

    # Protecao: nao aplicar se ganho for baixo
    if improve < 0.10:
        meta["reason"] = "low_gain"
        return ((0, 1, 2), (1, 1, 1), 1), meta

    # Ambiguo: scores muito proximos e ganho modesto
    if len(candidates) > 1:
        second = candidates[1]
        if abs(second["score"] - best_score) < 0.005 and improve < 0.30:
            meta["reason"] = "ambiguous"
            return ((0, 1, 2), (1, 1, 1), 1), meta

    return (best["perm"], best["signs"], best["det"]), meta


def transform_smd_triangles(filepath: Path, perm=(0, 1, 2), signs=(1, 1, 1)) -> bool:
    """
    Reescreve apenas a secao 'triangles' (pos/normal) aplicando a transformacao.
    Retorna True se aplicou (houve modificacao), False se nao.
    """
    if _is_identity_transform(perm, signs):
        return False

    lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines(True)
    out = []
    in_triangles = False
    changed = False

    for line in lines:
        stripped = line.strip()
        if not in_triangles:
            out.append(line)
            if stripped == "triangles":
                in_triangles = True
            continue

        if stripped == "end":
            in_triangles = False
            out.append(line)
            continue

        parts = stripped.split()
        if parts and parts[0].lstrip("-").isdigit() and len(parts) >= 9:
            try:
                x = float(parts[1])
                y = float(parts[2])
                z = float(parts[3])
                nx = float(parts[4])
                ny = float(parts[5])
                nz = float(parts[6])
            except Exception:
                out.append(line)
                continue

            x2, y2, z2 = _apply_axis_transform(x, y, z, perm, signs)
            nx2, ny2, nz2 = _apply_axis_transform(nx, ny, nz, perm, signs)

            eol = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
            parts[1] = f"{x2:.6f}"
            parts[2] = f"{y2:.6f}"
            parts[3] = f"{z2:.6f}"
            parts[4] = f"{nx2:.6f}"
            parts[5] = f"{ny2:.6f}"
            parts[6] = f"{nz2:.6f}"
            out.append(" ".join(parts) + eol)
            changed = True
            continue

        out.append(line)

    if not changed:
        return False

    filepath.write_text("".join(out), encoding="utf-8", errors="ignore")
    return True


# ---------------------------
# Import/Export Source (SMD/DMX)
# ---------------------------

def import_source_file(filepath: Path) -> None:
    fp = str(filepath)

    # Preferir operador específico por extensão
    ext = filepath.suffix.lower()

    if ext == ".dmx" and _op_exists("import_scene.dmx"):
        bpy.ops.import_scene.dmx(filepath=fp)
        return

    # SMD (ou fallback pra DMX via SMD se o addon assim fizer)
    if _op_exists("import_scene.smd"):
        bpy.ops.import_scene.smd(filepath=fp)
        return

    # QC direto
    if ext == ".qc" and _op_exists("import_scene.qc"):
        bpy.ops.import_scene.qc(filepath=fp)
        return

    raise RuntimeError(f"Sem operador de import compatível para: {filepath.name}")


def export_source_file(filepath: Path, fmt: str) -> None:
    export_mesh(filepath, fmt)
    return
    fp = str(filepath)

    # Export precisa selecionar tudo (malha + armature) pra manter rig
    select_all_objects()

    fmt = fmt.lower()
    if fmt == "dmx":
        if not _op_exists("export_scene.dmx"):
            raise RuntimeError("export_scene.dmx não existe (addon não fornece export DMX).")
        bpy.ops.export_scene.dmx(filepath=fp)
        return

    # smd default
    if not _op_exists("export_scene.smd"):
        raise RuntimeError("export_scene.smd não existe (addon não fornece export SMD).")
    bpy.ops.export_scene.smd(filepath=fp)


# ---------------------------
# Pipeline
# ---------------------------

def build_output_name(src: Path, out_fmt: str) -> str:
    # mantém base e põe _opt
    base = src.stem + "_opt"
    ext = ".dmx" if out_fmt.lower() == "dmx" else ".smd"
    return base + ext

def _qc_outputs_complete(qc_path: Path, cfg) -> bool:
    qc_dir = qc_path.parent
    qc_opt = qc_dir / f"{qc_path.stem}_OPT.qc"
    if not qc_opt.exists():
        return False

    qc_text = qc_path.read_text(encoding="utf-8", errors="ignore")
    refs = extract_file_refs_from_qc(qc_text)
    if not refs:
        return True

    out_dir = qc_dir / "output"
    for _full, rel, _kind in refs:
        rel_norm = normalize_qc_path_token(rel)
        src_file = (qc_dir / rel_norm).resolve()
        if not src_file.exists():
            continue
        out_name = build_output_name(src_file, cfg.format)
        out_file = (out_dir / out_name).resolve()
        if not out_file.exists():
            return False
    return True



def process_qc(qc_path: Path, cfg) -> None:
    qc_dir = qc_path.parent
    out_dir = qc_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    qc_text = qc_path.read_text(encoding="utf-8", errors="ignore")

    refs = extract_file_refs_from_qc(qc_text)
    ref_metadata = extract_file_ref_metadata_from_qc(qc_text)
    print(f"SMDs/DMXs detectadas no QC: {len(refs)}")

    if not refs:
        print("[INFO] Nenhuma malha referenciada (ou só animações). Pulando.")
        return

    replacements = {}  # original_token -> new_token

    render_bbox = None
    render_bbox_src = None

    mesh_outputs = []

    for i, (full_token, rel_token, kind) in enumerate(refs, start=1):
        rel_norm = normalize_qc_path_token(rel_token)
        src_file = (qc_dir / rel_norm).resolve()

        if not src_file.exists():
            print(f"[WARN] Arquivo não encontrado: {rel_token} -> {src_file}")
            continue

        out_name = build_output_name(src_file, cfg.format)
        out_file = (out_dir / out_name).resolve()
        ref_meta = ref_metadata.get(rel_norm.lower())
        sensitive_profile = _classify_sensitive_bodygroup_mesh(src_file, kind, ref_meta)
        processed_ok = False

        if sensitive_profile:
            bodygroup_name = str(sensitive_profile["bodygroup"] or "(unknown)")
            local_merge = 0.0
            local_use_planar = False
            local_planar_angle = cfg.planar_angle
            local_autosmooth = max(float(cfg.autosmooth), SENSITIVE_BODYGROUP_AUTOSMOOTH_FLOOR)

            if cfg.format.lower() == "smd" and src_file.suffix.lower() == ".smd":
                conservative_ratio = max(float(cfg.ratio), float(sensitive_profile["floor"]))
                retry_ratio = max(float(cfg.ratio), float(sensitive_profile["retry_floor"]))
                attempts = [("Conservative", conservative_ratio)]
                if retry_ratio > conservative_ratio + 1e-6:
                    attempts.append(("Conservative retry", retry_ratio))

                last_topology_reason = "topology_guardrail_rejected"
                for attempt_label, local_ratio in attempts:
                    print(
                        "  "
                        f"[{i}/{len(refs)}] {attempt_label}: {src_file.name} ({kind}) "
                        f"[class={sensitive_profile['label']} bodygroup={bodygroup_name} "
                        f"ratio={local_ratio:.2f} merge={local_merge:.4f}]"
                    )
                    processed_ok = _export_ref_with_settings(
                        src_file,
                        out_file,
                        kind,
                        cfg.format,
                        merge_dist=local_merge,
                        decimate_ratio=local_ratio,
                        autosmooth_deg=local_autosmooth,
                        use_planar=local_use_planar,
                        planar_angle_deg=local_planar_angle,
                    )
                    if not processed_ok:
                        continue

                    topo_ok, topo_reason, src_stats, out_stats = _validate_sensitive_bodygroup_topology(
                        src_file, out_file
                    )
                    if topo_ok:
                        if src_stats and out_stats:
                            print(
                                "[TOPOGUARD] Accept: "
                                f"{out_file.name} class={sensitive_profile['label']} "
                                f"open_edges={src_stats['open_edges']}->{out_stats['open_edges']} "
                                f"triangles={src_stats['triangles']}->{out_stats['triangles']}"
                            )
                        processed_ok = True
                        break

                    last_topology_reason = topo_reason
                    before_open = src_stats["open_edges"] if src_stats else "?"
                    after_open = out_stats["open_edges"] if out_stats else "?"
                    before_tri = src_stats["triangles"] if src_stats else "?"
                    after_tri = out_stats["triangles"] if out_stats else "?"
                    print(
                        "[TOPOGUARD] Reject: "
                        f"{out_file.name} class={sensitive_profile['label']} "
                        f"reason={topo_reason} "
                        f"open_edges={before_open}->{after_open} "
                        f"triangles={before_tri}->{after_tri}"
                    )
                    processed_ok = False

                if not processed_ok and _copy_ref_passthrough(src_file, out_file, cfg.format):
                    print(
                        "[TOPOGUARD] Passthrough fallback: "
                        f"{src_file.name} ({kind}) [class={sensitive_profile['label']} "
                        f"bodygroup={bodygroup_name} reason={last_topology_reason}]"
                    )
                    processed_ok = True

                if not processed_ok:
                    print(
                        "  "
                        f"[{i}/{len(refs)}] Safe baseline fallback: {src_file.name} ({kind}) "
                        f"[class={sensitive_profile['label']} bodygroup={bodygroup_name}]"
                    )
                    processed_ok = _export_ref_with_settings(
                        src_file,
                        out_file,
                        kind,
                        cfg.format,
                        merge_dist=0.0,
                        decimate_ratio=1.0,
                        autosmooth_deg=local_autosmooth,
                        use_planar=False,
                        planar_angle_deg=local_planar_angle,
                    )
            elif _copy_ref_passthrough(src_file, out_file, cfg.format):
                print(
                    "  "
                    f"[{i}/{len(refs)}] Passthrough: {src_file.name} ({kind}) "
                    f"[class={sensitive_profile['label']} bodygroup={bodygroup_name}]"
                )
                processed_ok = True
            else:
                print(
                    "  "
                    f"[{i}/{len(refs)}] Safe baseline: {src_file.name} ({kind}) "
                    f"[class={sensitive_profile['label']} bodygroup={bodygroup_name}]"
                )
                processed_ok = _export_ref_with_settings(
                    src_file,
                    out_file,
                    kind,
                    cfg.format,
                    merge_dist=0.0,
                    decimate_ratio=1.0,
                    autosmooth_deg=local_autosmooth,
                    use_planar=False,
                    planar_angle_deg=local_planar_angle,
                )
        else:
            print(f"  [{i}/{len(refs)}] Import: {src_file.name} ({kind})")
            processed_ok = _export_ref_with_settings(
                src_file,
                out_file,
                kind,
                cfg.format,
                merge_dist=cfg.merge,
                decimate_ratio=cfg.ratio,
                autosmooth_deg=cfg.autosmooth,
                use_planar=cfg.use_planar,
                planar_angle_deg=cfg.planar_angle,
            )

        if not processed_ok:
            continue
        if kind == "mesh" and cfg.format.lower() == "smd":
            mesh_outputs.append(out_file)

        # Captura bbox de referência (uma vez) a partir da primeira malha exportada
        if (
            kind == "mesh"
            and render_bbox is None
            and cfg.format.lower() == "smd"
            and getattr(cfg, "fix_physics", "auto") != "off"
        ):
            render_bbox = compute_smd_bbox(out_file)
            render_bbox_src = out_file
            if render_bbox:
                ext = _bbox_extents(render_bbox)
                ctr = _bbox_center(render_bbox)
                print(
                    "[PHYSFIX] render_bbox_ref="
                    f"{out_file.name} center=({ctr[0]:.3f},{ctr[1]:.3f},{ctr[2]:.3f}) "
                    f"ext=({ext[0]:.3f},{ext[1]:.3f},{ext[2]:.3f})"
                )
            else:
                print(f"[PHYSFIX][WARN] Não consegui calcular bbox de referência: {out_file}")

        # Auto-fix para SMDs de physics ($collisionmodel/$collisionjoints)
        if kind == "physics" and cfg.format.lower() == "smd":
            mode = getattr(cfg, "fix_physics", "auto")
            if mode != "off":
                phys_bbox = compute_smd_bbox(out_file)
                if not phys_bbox:
                    print(f"[PHYSFIX][WARN] Nao consegui calcular bbox do physics: {out_file}")
                else:
                    ref_bbox = render_bbox
                    ref_src = render_bbox_src

                    if not ref_bbox:
                        print(f"[PHYSFIX][WARN] Sem bbox de referencia para: {out_file}")
                    else:
                        ext = _bbox_extents(ref_bbox)
                        ctr = _bbox_center(ref_bbox)
                        print(
                            "[PHYSFIX] ref_bbox="
                            f"{ref_src.name if ref_src else '(none)'} center=({ctr[0]:.3f},{ctr[1]:.3f},{ctr[2]:.3f}) "
                            f"ext=({ext[0]:.3f},{ext[1]:.3f},{ext[2]:.3f})"
                        )

                        if mode == "force":
                            # Conversao fixa (compatibilidade com versoes anteriores)
                            perm = (1, 0, 2)
                            signs = (1, -1, -1)
                            det = _det_from_perm_signs(perm, signs)
                            meta = {"forced": True}
                        else:
                            (perm, signs, det), meta = pick_physics_transform(
                                phys_bbox, ref_bbox, allow_reflection=False
                            )

                        # Log top candidates
                        if meta and "top" in meta:
                            top = meta["top"]
                            parts = []
                            for c in top:
                                parts.append(
                                    f"rot={c['rot']} invz={1 if c['invz'] else 0} score={c['score']:.3f}"
                                )
                            print("[PHYSFIX] top3: " + "; ".join(parts))

                        if not _is_identity_transform(perm, signs):
                            applied = transform_smd_triangles(out_file, perm=perm, signs=signs)
                            if applied:
                                after_bbox = compute_smd_bbox(out_file) or phys_bbox
                                ext_b = _bbox_extents(phys_bbox)
                                ext_a = _bbox_extents(after_bbox)
                                xform = format_axis_transform(perm, signs)
                                print(
                                    f"[PHYSFIX] Applied xform={xform} det={det:+d} "
                                    f"file={out_file.name} ext_before=({ext_b[0]:.3f},{ext_b[1]:.3f},{ext_b[2]:.3f}) "
                                    f"ext_after=({ext_a[0]:.3f},{ext_a[1]:.3f},{ext_a[2]:.3f})"
                                )
                                if meta and "id_score" in meta and "best_score" in meta:
                                    print(
                                        f"[PHYSFIX] score id={meta['id_score']:.3f} best={meta['best_score']:.3f} "
                                        f"improve={meta.get('improve', 0.0):.3f}"
                                    )
                            else:
                                print(f"[PHYSFIX][WARN] Falhou aplicar transformacao em: {out_file}")
                        else:
                            reason = meta.get("reason") if meta else None
                            if reason:
                                print(f"[PHYSFIX] Skip ({reason}) file={out_file.name}")
                            elif mode == "auto" and meta and "id_score" in meta:
                                print(
                                    f"[PHYSFIX] Skipped (auto) file={out_file.name} "
                                    f"score={meta['id_score']:.3f}"
                                )

# Token novo no QC: "output/arquivo_opt.smd"
        new_rel = f"output/{out_name}"
        if full_token.startswith('"') and full_token.endswith('"'):
            new_token = f'"{new_rel}"'
        else:
            new_token = new_rel

        replacements[full_token] = new_token

        clean_scene()

    if not replacements:
        print("[INFO] Nada foi exportado, então não vou gerar QC _OPT.")
        return

    # Gera QC otimizado
    qc_opt_text = qc_text
    for old, new in replacements.items():
        qc_opt_text = qc_opt_text.replace(old, new)

    qc_opt_path = qc_dir / f"{qc_path.stem}_OPT.qc"
    qc_opt_path.write_text(qc_opt_text, encoding="utf-8", errors="ignore")

    print(f"[OK] Gerado: {qc_opt_path}")
    print(f"[OK] Saída:  {out_dir}")

    # Garantir ordem de materiais para skinfamilies (SMD apenas).
    skin_rows = extract_skinfamilies_from_qc(qc_text)
    if cfg.format.lower() == "smd" and skin_rows and mesh_outputs:
        target_order = _dedupe_preserve(skin_rows[0])
        if target_order:
            primary_smd = mesh_outputs[0]
            result = enforce_smd_material_order(primary_smd, target_order)
            if result.get("status") == "ok":
                missing = result.get("missing") or []
                if missing:
                    print(
                        "[SKINFIX] Inseridos "
                        f"{len(missing)} material(is) faltante(s) em {primary_smd.name}"
                    )
                else:
                    print(f"[SKINFIX] Ordem de materiais preservada em {primary_smd.name}")
            else:
                print(f"[SKINFIX][WARN] Falha aplicando skinfix em {primary_smd}: {result}")

    # Validação rápida de pesos (detecta numlinks>1 e bleed em glass)
    if cfg.format.lower() != "smd":
        print("[WEIGHTS] Skip (formato diferente de SMD).")
        return
    if not mesh_outputs:
        print("[WEIGHTS] Skip (nenhuma malha exportada).")
        return

    total_multi = 0
    total_glass_bleed = 0
    issues = []
    for smd in mesh_outputs:
        stats = validate_smd_weights(smd)
        if not stats:
            continue
        total_multi += stats["multi"]
        total_glass_bleed += stats["glass_bleed"]
        if stats["multi"] > 0 or stats["glass_bleed"] > 0:
            issues.append((smd, stats))

    if total_multi == 0 and total_glass_bleed == 0:
        print("[WEIGHTS] OK: numlinks>1=0, glass_car_door=0")
        return

    print(
        "[WEIGHTS] ALERTA: "
        f"numlinks>1={total_multi}, glass_car_door={total_glass_bleed}, files={len(issues)}"
    )
    for smd, stats in issues:
        print(
            f"[WEIGHTS] {smd.name}: numlinks>1={stats['multi']}, glass_car_door={stats['glass_bleed']}"
        )


def find_qcs(root: Path, include_opt: bool):
    qcs = []
    for p in root.rglob("*.qc"):
        name = p.name.lower()
        if not include_opt and name.endswith("_opt.qc"):
            continue
        # evita reprocessar QCs dentro de output/
        if "output" in [part.lower() for part in p.parts]:
            continue
        qcs.append(p)
    return sorted(qcs)


def parse_args(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("root", type=str, help="Pasta raiz (busca recursiva por .qc)")
    ap.add_argument("--ratio", type=float, default=0.75, help="Decimate ratio (1.0 = sem decimate)")
    ap.add_argument("--merge", type=float, default=0.0001, help="Merge by distance")
    ap.add_argument("--autosmooth", type=float, default=45.0, help="Auto smooth em graus")
    ap.add_argument("--use-planar", action="store_true", help="Aplica planar decimate antes do collapse decimate.")
    ap.add_argument("--planar-angle", type=float, default=2.0, help="Planar angle em graus quando --use-planar estiver ativo.")
    ap.add_argument("--format", type=str, default="smd", choices=["smd", "dmx"], help="Formato de export")
    ap.add_argument(
        "--fix-physics",
        type=str,
        default="auto",
        choices=["auto", "force", "off"],
        help="Corrige SMDs de physics ($collisionmodel/$collisionjoints) quando a colisao sai com eixos trocados/invertidos (auto|force|off).",
    )
    ap.add_argument("--include-opt", action="store_true", help="Também processa *_OPT.qc")
    ap.add_argument("--resume", action="store_true", help="Skip QCs when outputs already exist.")
    ap.add_argument("--shard-count", type=int, default=0, help="Split work across N shards (0 disables).")
    ap.add_argument("--shard-index", type=int, default=0, help="Shard index (0-based).")
    return ap.parse_args(argv)


def main():
    # Blender passa args depois de "--"
    if "--" not in sys.argv:
        print("[ERRO] Rode com '--' antes dos args do script. Ex.: blender --background --python script.py -- <root> ...")
        return 2

    idx = sys.argv.index("--")
    cfg = parse_args(sys.argv[idx + 1:])

    root = Path(cfg.root).expanduser().resolve()
    if not root.exists():
        print(f"[ERRO] ROOT não existe: {root}")
        return 2

    print(f"ROOT: {root}")
    print(
        f"Config: ratio={cfg.ratio} merge={cfg.merge} autosmooth={cfg.autosmooth} "
        f"use_planar={'ON' if cfg.use_planar else 'OFF'} planar_angle={cfg.planar_angle} "
        f"format={cfg.format} fix_physics={getattr(cfg, 'fix_physics', 'auto')} "
        f"include_opt={'ON' if cfg.include_opt else 'OFF'}"
    )

    # garante addon do Source Tools
    ensure_source_tools_enabled()
    log_source_operator_introspection()

    qcs = find_qcs(root, cfg.include_opt)
    print(f"Encontrados {len(qcs)} QC(s) (busca recursiva).")

    if cfg.shard_count and cfg.shard_count > 1:
        if cfg.shard_index < 0 or cfg.shard_index >= cfg.shard_count:
            print('[ERRO] shard-index fora do intervalo.')
            return 2
        total_all = len(qcs)
        qcs = [qc for i, qc in enumerate(qcs) if i % cfg.shard_count == cfg.shard_index]
        print(f'[SHARD] count={cfg.shard_count} index={cfg.shard_index} selected={len(qcs)}/{total_all}')


    completed = 0
    current_index = 0
    current_rel = None
    try:
        for n, qc in enumerate(qcs, start=1):
            current_index = n
            try:
                current_rel = qc.relative_to(root)
            except Exception:
                current_rel = qc

            rel_display = str(current_rel).replace('\\', '/')
            if cfg.resume and _qc_outputs_complete(qc, cfg):
                print(f'[RESUME] Skip: {rel_display}')
                print(f'QC_SKIP: {rel_display}')
                completed = n
                continue

            print(f"\n=== ({n}/{len(qcs)}) QC: {current_rel} ===")
            print(f"Pasta:  {qc.parent}")
            print(f"Output: {qc.parent / 'output'}")
            try:
                process_qc(qc, cfg)
            except Exception as e:
                print(f"[ERRO] Falha processando QC '{qc}': {e}")
            print(f'QC_DONE: {rel_display}')
            completed = n
    except KeyboardInterrupt:
        print("\n[INFO] Interrompido (Ctrl+C). Saindo de forma limpa.")
        if current_index:
            print(f"[INFO] QC atual: ({current_index}/{len(qcs)}) {current_rel}")
        print(f"[INFO] QCs concluidos: {completed}/{len(qcs)}")
        return 130

    print("\nFinalizado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
