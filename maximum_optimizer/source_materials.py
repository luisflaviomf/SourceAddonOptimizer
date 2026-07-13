from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import render_previews

from .focused_cache import (
    DuplicateDirectiveProof,
    _assert_safe_tree,
    _copy_file_no_follow,
    _has_reparse_ancestor,
    _read_regular_no_follow,
)
from .processes import ProcessCancelledError
from .reporting import canonical_json
from .smd_contract import direct_smd_material_counts
from .source_union import (
    _quarantine_cleanup_if_owned, _workspace_root_identity,
)


_HASH = re.compile(r"[0-9a-f]{64}")
_MAX_MATERIALS = 64
_MAX_FILES = 128
_MAX_BYTES = 512 * 1024 * 1024
_MAX_VMT_BYTES = 8 * 1024 * 1024


def _cancel(event, message: str) -> None:
    if event is not None and event.is_set():
        raise ProcessCancelledError(message)


def _relative(value: str, label: str) -> str:
    if type(value) is not str or not value or any(char in value for char in "\r\n\0"):
        raise ValueError(f"{label} is invalid")
    posix = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise ValueError(f"{label} is not canonical")
    canonical = PurePosixPath(*(part for part in posix.parts if part not in ("", "."))).as_posix()
    if canonical != value or canonical in ("", "."):
        raise ValueError(f"{label} is not canonical")
    return canonical


@dataclass(frozen=True)
class SourceUnionMaterialRoot:
    root_index: int
    root_identity: str

    def __post_init__(self) -> None:
        if (
            type(self.root_index) is not int or self.root_index < 0
            or self.root_identity != f"material-root-{self.root_index:03d}"
        ):
            raise ValueError("source-union material root is invalid")


@dataclass(frozen=True)
class SourceUnionMaterialFile:
    root_index: int
    path: str
    kind: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.root_index) is not int or self.root_index < 0
            or self.kind not in {"vmt", "vtf"}
            or type(self.size) is not int or self.size < 0
            or _HASH.fullmatch(self.sha256 or "") is None
        ):
            raise ValueError("source-union material file is invalid")
        _relative(self.path, "source-union material file path")
        if not self.path.casefold().endswith("." + self.kind):
            raise ValueError("source-union material file kind differs from path")


@dataclass(frozen=True)
class SourceUnionMaterialBindingProof:
    material_region_key: str
    smd_material: str
    search_paths: tuple[str, ...]
    root_index: int
    search_path_index: int
    vmt_file_index: int
    vtf_root_index: int
    vtf_file_index: int
    shader: str
    texture_directive: str
    uses_texture_alpha: bool
    duplicate_root_directives: tuple[DuplicateDirectiveProof, ...]

    def __post_init__(self) -> None:
        searches = tuple(self.search_paths)
        duplicates = tuple(self.duplicate_root_directives)
        duplicate_names = tuple(item.directive for item in duplicates)
        if (
            type(self.material_region_key) is not str or not self.material_region_key
            or type(self.smd_material) is not str or not self.smd_material
            or any(type(value) is not int or value < 0 for value in (
                self.root_index, self.search_path_index, self.vmt_file_index,
                self.vtf_root_index, self.vtf_file_index,
            ))
            or type(self.shader) is not str or not self.shader
            or type(self.texture_directive) is not str or not self.texture_directive
            or type(self.uses_texture_alpha) is not bool
            or any(not isinstance(item, DuplicateDirectiveProof) for item in duplicates)
            or duplicate_names != tuple(sorted(set(duplicate_names)))
            or any(
                item.directive != item.directive.casefold() or not item.directive
                or not item.ignored_values
                or any(type(value) is not str for value in item.ignored_values)
                for item in duplicates
            )
        ):
            raise ValueError("source-union material binding is invalid")
        _relative(self.material_region_key, "source-union material region key")
        _relative(self.smd_material, "source-union SMD material")
        if (
            self.shader not in render_previews._SUPPORTED_VMT_SHADERS
            or self.texture_directive != (
                "$refracttinttexture" if self.shader == "refract" else "$basetexture"
            )
            or (self.shader == "refract" and self.uses_texture_alpha is not True)
        ):
            raise ValueError("source-union material shader/directive contract is invalid")
        if any(_relative(item, "source-union material search path") != item for item in searches):
            raise ValueError("source-union material search paths are invalid")
        if len({item.casefold() for item in searches}) != len(searches):
            raise ValueError("source-union material search paths are duplicated")
        object.__setattr__(self, "search_paths", searches)
        object.__setattr__(self, "duplicate_root_directives", duplicates)


def _duplicate_payload(value: DuplicateDirectiveProof) -> dict[str, object]:
    return {"directive": value.directive, "ignored_values": list(value.ignored_values)}


def _unsigned_payload(value: "SourceUnionMaterialContract") -> dict[str, object]:
    return {
        "schema": value.schema,
        "kind": value.kind,
        "resolution_rule": value.resolution_rule,
        "source_identity": value.source_identity,
        "filtered_source_sha256": value.filtered_source_sha256,
        "roots": [{
            "root_index": item.root_index, "root_identity": item.root_identity,
        } for item in value.roots],
        "files": [{
            "root_index": item.root_index, "path": item.path, "kind": item.kind,
            "size": item.size, "sha256": item.sha256,
        } for item in value.files],
        "bindings": [{
            "material_region_key": item.material_region_key,
            "smd_material": item.smd_material,
            "search_paths": list(item.search_paths),
            "root_index": item.root_index,
            "search_path_index": item.search_path_index,
            "vmt_file_index": item.vmt_file_index,
            "vtf_root_index": item.vtf_root_index,
            "vtf_file_index": item.vtf_file_index,
            "shader": item.shader,
            "texture_directive": item.texture_directive,
            "uses_texture_alpha": item.uses_texture_alpha,
            "duplicate_root_directives": [
                _duplicate_payload(value) for value in item.duplicate_root_directives
            ],
        } for item in value.bindings],
    }


@dataclass(frozen=True)
class SourceUnionMaterialContract:
    schema: int
    kind: str
    resolution_rule: str
    source_identity: str
    filtered_source_sha256: str
    roots: tuple[SourceUnionMaterialRoot, ...]
    files: tuple[SourceUnionMaterialFile, ...]
    bindings: tuple[SourceUnionMaterialBindingProof, ...]
    material_contract_sha256: str

    def __post_init__(self) -> None:
        roots = tuple(self.roots); files = tuple(self.files); bindings = tuple(self.bindings)
        file_keys = tuple((item.root_index, item.path.casefold(), item.path) for item in files)
        referenced = tuple(sorted({
            index for item in bindings for index in (item.vmt_file_index, item.vtf_file_index)
        }))
        if (
            type(self.schema) is not int or self.schema != 1
            or self.kind != "adaptive-direct-source-union-material-v1"
            or self.resolution_rule != "materials-root-order-then-qc-search-order-v1"
            or _relative(self.source_identity, "source-union material source") != self.source_identity
            or _HASH.fullmatch(self.filtered_source_sha256 or "") is None
            or _HASH.fullmatch(self.material_contract_sha256 or "") is None
            or not roots or len(roots) > _MAX_MATERIALS
            or tuple(item.root_index for item in roots) != tuple(range(len(roots)))
            or any(not isinstance(item, SourceUnionMaterialRoot) for item in roots)
            or not files or len(files) > _MAX_FILES
            or any(not isinstance(item, SourceUnionMaterialFile) for item in files)
            or file_keys != tuple(sorted(file_keys))
            or len({(a, b) for a, b, _ in file_keys}) != len(file_keys)
            or any(item.root_index >= len(roots) for item in files)
            or sum(item.size for item in files) > _MAX_BYTES
            or not bindings or len(bindings) > _MAX_MATERIALS
            or any(not isinstance(item, SourceUnionMaterialBindingProof) for item in bindings)
            or len({item.material_region_key.casefold() for item in bindings}) != len(bindings)
            or len({item.smd_material.casefold() for item in bindings}) != len(bindings)
            or referenced != tuple(range(len(files)))
        ):
            raise ValueError("source-union material contract is invalid")
        for binding in bindings:
            if (
                binding.root_index >= len(roots)
                or binding.vtf_root_index >= len(roots)
                or binding.vmt_file_index >= len(files)
                or binding.vtf_file_index >= len(files)
                or files[binding.vmt_file_index].root_index != binding.root_index
                or files[binding.vmt_file_index].kind != "vmt"
                or files[binding.vtf_file_index].root_index != binding.vtf_root_index
                or files[binding.vtf_file_index].kind != "vtf"
                or binding.search_path_index >= max(1, len(binding.search_paths))
            ):
                raise ValueError("source-union material binding/file relation is invalid")
        if hashlib.sha256(canonical_json(_unsigned_payload(self)).encode()).hexdigest() != self.material_contract_sha256:
            raise ValueError("source-union material contract seal is invalid")
        object.__setattr__(self, "roots", roots)
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "bindings", bindings)


def source_union_material_contract_payload(value: SourceUnionMaterialContract) -> dict[str, object]:
    if not isinstance(value, SourceUnionMaterialContract):
        raise TypeError("source-union material contract is invalid")
    return {**_unsigned_payload(value), "material_contract_sha256": value.material_contract_sha256}


def source_union_material_contract_from_payload(value: object) -> SourceUnionMaterialContract:
    fields = {
        "schema", "kind", "resolution_rule", "source_identity",
        "filtered_source_sha256", "roots", "files", "bindings",
        "material_contract_sha256",
    }
    if type(value) is not dict or set(value) != fields or any(
        type(value[name]) is not list for name in ("roots", "files", "bindings")
    ):
        raise ValueError("source-union material contract payload fields are invalid")
    roots = []
    for raw in value["roots"]:
        if type(raw) is not dict or set(raw) != {"root_index", "root_identity"}:
            raise ValueError("source-union material root payload fields are invalid")
        roots.append(SourceUnionMaterialRoot(**raw))
    files = []
    file_fields = {"root_index", "path", "kind", "size", "sha256"}
    for raw in value["files"]:
        if type(raw) is not dict or set(raw) != file_fields:
            raise ValueError("source-union material file payload fields are invalid")
        files.append(SourceUnionMaterialFile(**raw))
    bindings = []
    binding_fields = {
        "material_region_key", "smd_material", "search_paths", "root_index",
        "search_path_index", "vmt_file_index", "vtf_root_index", "vtf_file_index",
        "shader", "texture_directive", "uses_texture_alpha",
        "duplicate_root_directives",
    }
    for raw in value["bindings"]:
        if (
            type(raw) is not dict or set(raw) != binding_fields
            or type(raw["search_paths"]) is not list
            or type(raw["duplicate_root_directives"]) is not list
        ):
            raise ValueError("source-union material binding payload fields are invalid")
        duplicates = []
        for item in raw["duplicate_root_directives"]:
            if type(item) is not dict or set(item) != {"directive", "ignored_values"} or type(item["ignored_values"]) is not list:
                raise ValueError("source-union duplicate directive payload fields are invalid")
            duplicates.append(DuplicateDirectiveProof(item["directive"], tuple(item["ignored_values"])))
        copied = dict(raw); copied["search_paths"] = tuple(copied["search_paths"])
        copied["duplicate_root_directives"] = tuple(duplicates)
        bindings.append(SourceUnionMaterialBindingProof(**copied))
    copied = dict(value); copied["roots"] = tuple(roots); copied["files"] = tuple(files)
    copied["bindings"] = tuple(bindings)
    return SourceUnionMaterialContract(**copied)


def _read_material(
    path: Path, root: Path, event, *, max_bytes: int,
) -> tuple[bytes, str]:
    payload = _read_regular_no_follow(path, event, contained_root=root, max_bytes=max_bytes)
    return payload, hashlib.sha256(payload).hexdigest()


def build_source_union_material_contract(
    *, source_identity: str, filtered_source_bytes: bytes, requests,
    roots, cancel_event,
) -> SourceUnionMaterialContract:
    if type(filtered_source_bytes) is not bytes:
        raise TypeError("source-union filtered source must be bytes")
    text = filtered_source_bytes.decode("utf-8", errors="strict")
    materials = tuple(item[0] for item in direct_smd_material_counts(text))
    request_values = tuple(requests)
    raw_roots = tuple(Path(os.path.abspath(root)) for root in roots)
    if not raw_roots or any(_has_reparse_ancestor(root) or not root.is_dir() for root in raw_roots):
        raise ValueError("source-union material roots are unsafe")
    root_values = tuple(root.resolve(strict=True) for root in raw_roots)
    if len(request_values) != len(materials) or len(materials) > _MAX_MATERIALS:
        raise ValueError("source-union SMD material request cardinality differs")
    parsed_requests = []
    for index, (raw, smd_material) in enumerate(zip(request_values, materials)):
        if type(raw) is not dict or set(raw) != {"material_region_key", "smd_material", "search_paths"}:
            raise ValueError("source-union material request fields are invalid")
        if type(raw["search_paths"]) not in (list, tuple):
            raise ValueError("source-union material search paths are invalid")
        searches = tuple(raw["search_paths"])
        if raw["smd_material"] != smd_material:
            raise ValueError("source-union SMD material order differs")
        parsed_requests.append((raw["material_region_key"], smd_material, searches))
    file_values: dict[tuple[int, str], SourceUnionMaterialFile] = {}
    planned_sizes: dict[tuple[int, str], int] = {}
    planned_total = 0

    def register_selected(root_index: int, path: Path, kind: str) -> tuple[int, str]:
        nonlocal planned_total
        _cancel(cancel_event, "cancelled before source-union material stat")
        info = path.lstat()
        if _has_reparse_ancestor(path) or not stat.S_ISREG(info.st_mode):
            raise ValueError("source-union selected material file is unsafe")
        relative = path.relative_to(root_values[root_index]).as_posix()
        key = (root_index, relative.casefold())
        previous = planned_sizes.get(key)
        if previous is None:
            if len(planned_sizes) >= _MAX_FILES:
                raise ValueError("source-union material file bound exceeded")
            if kind == "vmt" and info.st_size > _MAX_VMT_BYTES:
                raise ValueError("source-union material VMT bound exceeded")
            if planned_total + info.st_size > _MAX_BYTES:
                raise ValueError("source-union material byte bound exceeded")
            planned_sizes[key] = info.st_size
            planned_total += info.st_size
        elif previous != info.st_size:
            raise ValueError("source-union material file size changed")
        return key

    pending = []
    for region_key, smd_material, searches in parsed_requests:
        _cancel(cancel_event, "cancelled during source-union material resolution")
        identity = smd_material[:-4] if smd_material.casefold().endswith(".vmt") else smd_material
        normalized = PurePosixPath(identity.replace("\\", "/"))
        if not identity or normalized.is_absolute() or PureWindowsPath(identity).drive or ".." in normalized.parts:
            raise ValueError("source-union SMD material identity is unsafe")
        candidates = (
            tuple((PurePosixPath(path) / normalized).as_posix() for path in searches)
            if searches and len(normalized.parts) == 1 else (normalized.as_posix(),)
        )
        selected_vmt = None
        for root_index, root in enumerate(root_values):
            for search_index, candidate in enumerate(candidates):
                path = render_previews._contained_material_path(root, candidate, ".vmt")
                if path is not None and path.is_file():
                    selected_vmt = (root_index, search_index, path)
                    break
            if selected_vmt is not None:
                break
        if selected_vmt is None:
            raise ValueError(f"source-union material VMT is missing: {smd_material}")
        vmt_root, search_index, vmt_path = selected_vmt
        vmt_key = register_selected(vmt_root, vmt_path, "vmt")
        vmt_bytes, vmt_hash = _read_material(
            vmt_path, root_values[vmt_root], cancel_event, max_bytes=_MAX_VMT_BYTES,
        )
        vmt_text = vmt_bytes.decode("utf-8", errors="replace")
        parsed = render_previews._parse_vmt_root(vmt_text)
        texture = render_previews._source_texture_reference(vmt_text)
        if parsed is None or texture is None:
            raise ValueError(f"source-union material VMT is unsupported: {smd_material}")
        shader, directive, texture_identity, uses_alpha = texture
        selected_vtf = None
        for root_index, root in enumerate(root_values):
            path = render_previews._contained_material_path(root, texture_identity, ".vtf")
            if path is not None and path.is_file():
                selected_vtf = (root_index, path)
                break
        if selected_vtf is None:
            raise ValueError(f"source-union material VTF is missing: {smd_material}")
        vtf_root, vtf_path = selected_vtf
        vtf_key = register_selected(vtf_root, vtf_path, "vtf")
        vtf_bytes, vtf_hash = _read_material(
            vtf_path, root_values[vtf_root], cancel_event, max_bytes=_MAX_BYTES,
        )
        vmt_relative = vmt_path.relative_to(root_values[vmt_root]).as_posix()
        vtf_relative = vtf_path.relative_to(root_values[vtf_root]).as_posix()
        vmt_file = SourceUnionMaterialFile(
            vmt_root, vmt_relative, "vmt", len(vmt_bytes), vmt_hash,
        )
        vtf_file = SourceUnionMaterialFile(
            vtf_root, vtf_relative, "vtf", len(vtf_bytes), vtf_hash,
        )
        for key, file in (
            ((vmt_root, vmt_relative.casefold()), vmt_file),
            ((vtf_root, vtf_relative.casefold()), vtf_file),
        ):
            existing = file_values.get(key)
            if existing is not None and existing != file:
                raise ValueError("source-union material file case collision")
            file_values[key] = file
        duplicates = tuple(
            DuplicateDirectiveProof(name, tuple(values))
            for name, values in sorted(parsed[2].items())
        )
        pending.append((
            region_key, smd_material, searches, vmt_root, search_index,
            vmt_key, vtf_root, vtf_key, shader, directive,
            uses_alpha, duplicates,
        ))
    files = tuple(sorted(file_values.values(), key=lambda item: (
        item.root_index, item.path.casefold(), item.path,
    )))
    index_by_key = {(item.root_index, item.path.casefold()): index for index, item in enumerate(files)}
    bindings = tuple(SourceUnionMaterialBindingProof(
        region, material, tuple(searches), vmt_root, search_index,
        index_by_key[vmt_key], vtf_root, index_by_key[vtf_key], shader,
        directive, uses_alpha, duplicates,
    ) for (
        region, material, searches, vmt_root, search_index, vmt_key,
        vtf_root, vtf_key, shader, directive, uses_alpha, duplicates,
    ) in pending)
    root_proofs = tuple(SourceUnionMaterialRoot(index, f"material-root-{index:03d}") for index in range(len(root_values)))
    values = dict(
        schema=1, kind="adaptive-direct-source-union-material-v1",
        resolution_rule="materials-root-order-then-qc-search-order-v1",
        source_identity=source_identity,
        filtered_source_sha256=hashlib.sha256(filtered_source_bytes).hexdigest(),
        roots=root_proofs, files=files, bindings=bindings,
    )
    provisional = object.__new__(SourceUnionMaterialContract)
    for name, value in (*values.items(), ("material_contract_sha256", "0" * 64)):
        object.__setattr__(provisional, name, value)
    seal = hashlib.sha256(canonical_json(_unsigned_payload(provisional)).encode()).hexdigest()
    return SourceUnionMaterialContract(**values, material_contract_sha256=seal)


def require_current_source_union_material_contract(
    contract: SourceUnionMaterialContract, *, filtered_source_bytes: bytes,
    roots, cancel_event,
) -> None:
    if not isinstance(contract, SourceUnionMaterialContract):
        raise TypeError("source-union material contract is invalid")
    requests = tuple({
        "material_region_key": item.material_region_key,
        "smd_material": item.smd_material,
        "search_paths": item.search_paths,
    } for item in contract.bindings)
    try:
        current = build_source_union_material_contract(
            source_identity=contract.source_identity,
            filtered_source_bytes=filtered_source_bytes,
            requests=requests, roots=roots, cancel_event=cancel_event,
        )
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise ValueError("source-union current material contract is unavailable") from exc
    if current != contract:
        raise ValueError("source-union current material contract differs")


def materialize_private_source_union_material_roots(
    contract: SourceUnionMaterialContract, roots, destination: Path, cancel_event,
    *, filtered_source_bytes: bytes,
) -> tuple[Path, ...]:
    raw_roots = tuple(Path(os.path.abspath(item)) for item in roots)
    if any(_has_reparse_ancestor(root) or not root.is_dir() for root in raw_roots):
        raise ValueError("source-union material roots are unsafe")
    roots = tuple(root.resolve(strict=True) for root in raw_roots)
    destination = Path(os.path.abspath(destination))
    if os.path.lexists(destination) or _has_reparse_ancestor(destination.parent):
        raise ValueError("source-union private material destination is unsafe")
    require_current_source_union_material_contract(
        contract, filtered_source_bytes=filtered_source_bytes,
        roots=roots, cancel_event=cancel_event,
    )
    # The filtered bytes are contract-bound but not stored; callers revalidate source
    # separately and materialization only needs current selected material bytes.
    staging = destination.with_name(
        f".{destination.name}.source-materials-acquire-{uuid.uuid4().hex}"
    )
    staging.mkdir(parents=False, exist_ok=False)
    staging_identity = _workspace_root_identity(staging)
    private = tuple(staging / f"root-{index:03d}" for index in range(len(contract.roots)))
    try:
        for path in private:
            path.mkdir()
        expected = set()
        for file in contract.files:
            source = roots[file.root_index] / Path(*file.path.split("/"))
            target = private[file.root_index] / Path(*file.path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_file_no_follow(source, target, cancel_event, contained_root=roots[file.root_index])
            expected.add(f"root-{file.root_index:03d}/{file.path}")
        _assert_safe_tree(staging, cancel_event, expected, max_files=_MAX_FILES, max_bytes=_MAX_BYTES)
        require_current_source_union_material_contract(
            contract, filtered_source_bytes=filtered_source_bytes,
            roots=roots, cancel_event=cancel_event,
        )
        require_current_source_union_material_contract(
            contract, filtered_source_bytes=filtered_source_bytes,
            roots=private, cancel_event=cancel_event,
        )
        _assert_safe_tree(
            staging, cancel_event, expected,
            max_files=_MAX_FILES, max_bytes=_MAX_BYTES,
        )
        os.rename(staging, destination)
        if _workspace_root_identity(destination) != staging_identity:
            raise ValueError("source-union private material destination identity changed")
        return tuple(
            destination / f"root-{index:03d}" for index in range(len(contract.roots))
        )
    except BaseException:
        _quarantine_cleanup_if_owned(staging, staging_identity)
        raise


def source_union_material_render_evidence(
    contract: SourceUnionMaterialContract,
) -> tuple[dict[str, object], ...]:
    files = contract.files
    return tuple({
        "material_identity": item.material_region_key,
        "resolution_rule": contract.resolution_rule,
        "root_index": item.root_index,
        "search_path_index": item.search_path_index,
        "vtf_root_index": item.vtf_root_index,
        "vmt_sha256": files[item.vmt_file_index].sha256,
        "vtf_sha256": files[item.vtf_file_index].sha256,
        "shader": item.shader,
        "texture_directive": item.texture_directive,
        "uses_texture_alpha": item.uses_texture_alpha,
        "duplicate_root_directives": [
            _duplicate_payload(value) for value in item.duplicate_root_directives
        ],
    } for item in contract.bindings)
