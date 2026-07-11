from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from .domain import FamilyManifest, StructuralFingerprint


_SOURCE_SUFFIXES = (".smd", ".dmx")
_ARTIFACT_KINDS = (".mdl", ".vvd", ".ani", ".phy", ".dx90.vtx", ".dx80.vtx", ".vtx")
_NODE_RE = re.compile(r'^\s*(-?\d+)\s+"([^"]*)"\s+(-?\d+)')


def _normalize(value: str) -> str:
    return value.replace("\\", "/").strip().strip('"')


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _normalize(value)
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(result)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _relative_declaration(value: str, label: str) -> tuple[str, PurePosixPath]:
    normalized = _normalize(value)
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError(f"absolute {label} is not allowed: {value}")
    return normalized, posix_path


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0

    def flush() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    while index < len(text):
        char = text[index]
        if quoted:
            if char == '"':
                quoted = False
                flush()
            elif char == "\\" and index + 1 < len(text) and text[index + 1] == '"':
                current.append('"')
                index += 1
            else:
                current.append(char)
        elif char == '"':
            flush()
            quoted = True
        elif char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            flush()
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        elif char in "{}":
            flush()
            tokens.append(char)
        elif char in "\r\n":
            flush()
            if not tokens or tokens[-1] != "\n":
                tokens.append("\n")
        elif char.isspace():
            flush()
        else:
            current.append(char)
        index += 1
    flush()
    return tokens


def _line_args(tokens: list[str], start: int) -> tuple[list[str], int]:
    result: list[str] = []
    index = start
    while index < len(tokens) and tokens[index] not in ("\n", "{", "}"):
        result.append(tokens[index])
        index += 1
    return result, index


def _following_block(tokens: list[str], index: int) -> tuple[list[str] | None, int]:
    while index < len(tokens) and tokens[index] == "\n":
        index += 1
    if index >= len(tokens) or tokens[index] != "{":
        return None, index
    depth = 1
    start = index + 1
    index += 1
    while index < len(tokens) and depth:
        if tokens[index] == "{":
            depth += 1
        elif tokens[index] == "}":
            depth -= 1
        index += 1
    if depth:
        raise ValueError("unterminated QC block")
    return tokens[start : index - 1], index


def _block_commands(tokens: list[str], command: str) -> list[list[str]]:
    result: list[list[str]] = []
    index = 0
    wanted = command.casefold()
    while index < len(tokens):
        if tokens[index].casefold() == wanted:
            args = []
            index += 1
            while (
                index < len(tokens)
                and tokens[index] not in ("\n", "{", "}")
                and tokens[index].casefold() != wanted
            ):
                args.append(tokens[index])
                index += 1
            result.append(args)
        else:
            index += 1
    return result


def _skin_rows(tokens: list[str]) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    index = 0
    while index < len(tokens):
        if tokens[index] != "{":
            index += 1
            continue
        block, next_index = _following_block(tokens, index)
        if block is None:
            index += 1
            continue
        values = tuple(_normalize(token) for token in block if token not in ("\n", "{", "}"))
        if values:
            rows.append(values)
        index = next_index
    return rows


@dataclass
class _Inventory:
    model_name: str = ""
    bodygroups: list[str] = field(default_factory=list)
    skin_families: list[tuple[str, ...]] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    hitboxes: list[str] = field(default_factory=list)
    sequences: list[str] = field(default_factory=list)
    mesh_files: list[str] = field(default_factory=list)
    lod_mesh_files: list[str] = field(default_factory=list)
    physics_mesh: str | None = None
    source_files: list[Path] = field(default_factory=list)
    references: list[tuple[str, Path, bool]] = field(default_factory=list)


def _add_reference(
    inventory: _Inventory,
    raw_path: str,
    current_dir: Path,
    family_root: Path,
    *,
    mesh: bool,
) -> None:
    logical, relative_path = _relative_declaration(raw_path, "source path")
    if not logical or relative_path.suffix.casefold() not in _SOURCE_SUFFIXES:
        return
    resolved = (current_dir / Path(*relative_path.parts)).resolve()
    if not _is_within(resolved, family_root):
        raise ValueError(f"referenced source escapes family root: {logical}")
    inventory.references.append((logical, resolved, mesh))


def _parse_qc_file(
    path: Path,
    family_root: Path,
    inventory: _Inventory,
    visited: set[Path],
) -> None:
    resolved = path.resolve()
    if not _is_within(resolved, family_root):
        raise ValueError(f"include escapes family root: {path}")
    if resolved in visited:
        return
    visited.add(resolved)
    inventory.source_files.append(resolved)
    tokens = _tokenize(resolved.read_text(encoding="utf-8", errors="replace"))
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("$"):
            index += 1
            continue
        directive = token.casefold()
        args, after_args = _line_args(tokens, index + 1)
        block, after_block = _following_block(tokens, after_args)
        index = after_block if block is not None else max(after_args + 1, index + 1)

        if directive == "$modelname" and args and not inventory.model_name:
            inventory.model_name = _normalize(args[0])
        elif directive in ("$body", "$model") and args:
            inventory.bodygroups.append(args[0])
            if len(args) > 1:
                inventory.mesh_files.append(args[1])
                _add_reference(inventory, args[1], resolved.parent, family_root, mesh=True)
        elif directive == "$bodygroup" and args:
            inventory.bodygroups.append(args[0])
            for studio_args in _block_commands(block or [], "studio"):
                if studio_args:
                    inventory.mesh_files.append(studio_args[0])
                    _add_reference(inventory, studio_args[0], resolved.parent, family_root, mesh=True)
        elif directive == "$texturegroup":
            inventory.skin_families.extend(_skin_rows(block or []))
        elif directive == "$attachment" and args:
            inventory.attachments.append(args[0])
        elif directive in ("$hbox", "$hboxset"):
            normalized_args = [_normalize(value) for value in args]
            inventory.hitboxes.append(" ".join((directive, *normalized_args)).strip())
        elif directive == "$sequence" and args:
            inventory.sequences.append(args[0])
            for value in args[1:]:
                _add_reference(inventory, value, resolved.parent, family_root, mesh=False)
            for value in block or []:
                if value not in ("\n", "{", "}"):
                    _add_reference(inventory, value, resolved.parent, family_root, mesh=False)
        elif directive == "$lod":
            for replace_args in _block_commands(block or [], "replacemodel"):
                for value in replace_args:
                    _add_reference(inventory, value, resolved.parent, family_root, mesh=True)
                if len(replace_args) > 1:
                    inventory.lod_mesh_files.append(replace_args[1])
        elif directive in ("$collisionmodel", "$collisionjoints") and args:
            if inventory.physics_mesh is None:
                inventory.physics_mesh = _normalize(args[0])
            _add_reference(inventory, args[0], resolved.parent, family_root, mesh=True)
        elif directive == "$include" and args:
            include_name, relative_include = _relative_declaration(args[0], "include path")
            include_path = (resolved.parent / Path(*relative_include.parts)).resolve()
            if not _is_within(include_path, family_root):
                raise ValueError(f"include escapes family root: {include_name}")
            _parse_qc_file(include_path, family_root, inventory, visited)


def _read_smd(path: Path) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    if not path.is_file() or path.suffix.casefold() != ".smd":
        return [], [], []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    bones: list[str] = []
    parent_ids: list[tuple[str, int]] = []
    by_id: dict[int, str] = {}
    materials: list[str] = []
    section = ""
    triangle_row = 0
    for raw_line in lines:
        line = raw_line.strip()
        folded = line.casefold()
        if folded in ("nodes", "triangles"):
            section = folded
            triangle_row = 0
            continue
        if folded == "end":
            section = ""
            continue
        if not line:
            continue
        if section == "nodes":
            match = _NODE_RE.match(raw_line)
            if match:
                bone_id = int(match.group(1))
                name = match.group(2)
                by_id[bone_id] = name
                bones.append(name)
                parent_ids.append((name, int(match.group(3))))
        elif section == "triangles":
            if triangle_row == 0:
                materials.append(_normalize(line))
                triangle_row = 3
            else:
                triangle_row -= 1
    parents = [(child, by_id[parent]) for child, parent in parent_ids if parent in by_id and parent >= 0]
    return bones, parents, materials


def _make_fingerprint(inventory: _Inventory) -> StructuralFingerprint:
    bones: list[str] = []
    bone_parents: list[tuple[str, str]] = []
    smd_materials: list[str] = []
    references_by_path: dict[Path, bool] = {}
    for _logical, path, mesh in inventory.references:
        references_by_path[path] = references_by_path.get(path, False) or mesh
    for path, mesh in references_by_path.items():
        file_bones, file_parents, file_materials = _read_smd(path)
        bones.extend(file_bones)
        bone_parents.extend(file_parents)
        if mesh:
            smd_materials.extend(file_materials)

    parent_values = [f"{child}\0{parent}" for child, parent in bone_parents]
    unique_parents = tuple(tuple(value.split("\0", 1)) for value in _unique(parent_values))
    skin_materials = [material for family in inventory.skin_families for material in family]
    return StructuralFingerprint(
        model_name=_normalize(inventory.model_name),
        bodygroups=_unique(inventory.bodygroups),
        materials=_unique(skin_materials + smd_materials),
        skin_families=tuple(inventory.skin_families),
        bones=_unique(bones),
        bone_parents=unique_parents,
        attachments=_unique(inventory.attachments),
        hitboxes=_unique(inventory.hitboxes),
        sequences=_unique(inventory.sequences),
        mesh_files=_unique(inventory.mesh_files),
        lod_mesh_files=_unique(inventory.lod_mesh_files),
        physics_mesh=_normalize(inventory.physics_mesh) if inventory.physics_mesh else None,
    )


def _inventory_qc(
    qc_path: Path, *, family_root: Path | None = None
) -> tuple[StructuralFingerprint, _Inventory]:
    resolved = qc_path.resolve()
    root = family_root.resolve() if family_root is not None else resolved.parent
    inventory = _Inventory()
    _parse_qc_file(resolved, root, inventory, set())
    return _make_fingerprint(inventory), inventory


def parse_qc_fingerprint(qc_path: Path) -> StructuralFingerprint:
    fingerprint, _inventory = _inventory_qc(qc_path)
    return fingerprint


def _safe_model_rel(value: str) -> str:
    normalized, relative_path = _relative_declaration(value, "model_rel")
    if not normalized or ".." in relative_path.parts:
        raise ValueError(f"invalid model_rel: {value}")
    return relative_path.as_posix()


def _resolve_source_dir(value: object, src_root: Path, model_rel: str) -> Path:
    if value:
        path = Path(str(value))
        resolved = path.resolve() if path.is_absolute() else (src_root / path).resolve()
    else:
        model_path = Path(model_rel)
        resolved = (src_root / model_path.parent / model_path.stem).resolve()
    if not _is_within(resolved, src_root):
        raise ValueError(f"source directory is outside src_root: {resolved}")
    return resolved


def _choose_qc(source_dir: Path, chosen: object, src_root: Path) -> Path:
    candidates = sorted(
        (path.resolve() for path in source_dir.rglob("*.qc") if path.is_file()),
        key=lambda path: path.relative_to(source_dir).as_posix().casefold(),
    )
    if chosen:
        basename = Path(str(chosen).replace("\\", "/")).name.casefold()
        named = [path for path in candidates if path.name.casefold() == basename]
        if named:
            candidates = named
    if not candidates:
        raise ValueError(f"no QC found in source directory: {source_dir}")
    qc_path = candidates[0]
    if not _is_within(qc_path, source_dir) or not _is_within(qc_path, src_root):
        raise ValueError(f"QC is outside src_root: {qc_path}")
    return qc_path


def _original_artifacts(models_root: Path, model_rel: str) -> list[tuple[str, Path]]:
    model_path = Path(model_rel)
    directory = (models_root / model_path.parent).resolve()
    if not _is_within(directory, models_root):
        raise ValueError(f"model path is outside original models root: {model_rel}")
    base = model_path.stem.casefold()
    expected = {base + kind: kind for kind in _ARTIFACT_KINDS}
    artifacts: list[tuple[str, Path]] = []
    if directory.is_dir():
        for path in directory.iterdir():
            kind = expected.get(path.name.casefold())
            if kind is not None and path.is_file():
                artifacts.append((kind, path.resolve()))
    return sorted(artifacts, key=lambda item: item[0].casefold())


def _hash_inputs(
    source_dir: Path,
    inventory: _Inventory,
    artifacts: list[tuple[str, Path]],
    models_root: Path,
) -> str:
    entries: dict[tuple[str, str], Path] = {}
    for path in inventory.source_files:
        logical = "source/" + path.relative_to(source_dir).as_posix()
        entries[(logical.casefold(), logical)] = path
    for _declared, path, _mesh in inventory.references:
        logical = "source/" + path.relative_to(source_dir).as_posix()
        entries[(logical.casefold(), logical)] = path
    for _kind, path in artifacts:
        logical = "models/" + path.relative_to(models_root).as_posix()
        entries[(logical.casefold(), logical)] = path

    digest = hashlib.sha256()
    for (_folded, logical), path in sorted(entries.items()):
        name = logical.encode("utf-8")
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def build_family_manifests(
    addon_models_dir: Path,
    decompile_manifest_path: Path,
    src_root: Path,
) -> tuple[FamilyManifest, ...]:
    models_root = addon_models_dir.resolve()
    source_root = src_root.resolve()
    payload = json.loads(decompile_manifest_path.read_text(encoding="utf-8"))
    records = [record for record in payload.get("results", ()) if record.get("status") == "ok"]
    records.sort(
        key=lambda record: _normalize(
            str(record.get("model_rel") or record.get("model_rel_fallback") or "")
        ).casefold()
    )

    manifests: list[FamilyManifest] = []
    for record in records:
        model_rel = _safe_model_rel(str(record.get("model_rel") or record.get("model_rel_fallback") or ""))
        source_dir = _resolve_source_dir(record.get("src_dir"), source_root, model_rel)
        qc_path = _choose_qc(source_dir, record.get("qc_chosen"), source_root)
        fingerprint, inventory = _inventory_qc(qc_path, family_root=source_dir)
        artifacts = _original_artifacts(models_root, model_rel)
        family_id = hashlib.sha256(model_rel.casefold().encode("utf-8")).hexdigest()
        manifests.append(
            FamilyManifest(
                family_id=family_id,
                model_rel=model_rel,
                source_dir=source_dir,
                original_models_dir=models_root,
                fingerprint=fingerprint,
                input_hash=_hash_inputs(source_dir, inventory, artifacts, models_root),
                required_artifact_kinds=tuple(kind for kind, _path in artifacts),
            )
        )
    return tuple(manifests)
