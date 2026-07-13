#!/usr/bin/env python3
# Render before/after previews for a single model using Blender (headless).

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import types
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath

sys.dont_write_bytecode = True

_SOURCE_UNION_BOOTSTRAP_RUNTIME_FILES = None
_SOURCE_UNION_BOOTSTRAP_CONTROL_BYTES = None


def _bootstrap_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _bootstrap_safe_ancestry(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    return not any(
        component.exists() and _bootstrap_reparse(component)
        for component in (*reversed(absolute.parents), absolute)
    )


def _bootstrap_read_regular(path: Path, *, max_bytes: int) -> bytes:
    if not _bootstrap_safe_ancestry(path):
        raise ValueError("source-union bootstrap path has reparse ancestry")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
        raise ValueError("source-union bootstrap file is invalid")
    payload = path.read_bytes()
    after = path.lstat()
    if (
        not stat.S_ISREG(after.st_mode)
        or len(payload) != before.st_size
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ValueError("source-union bootstrap file changed while captured")
    return payload


def _bootstrap_install_runtime(root: Path, captured: dict[str, bytes]) -> None:
    order = (
        "__init__.py", "reporting.py", "smd_contract.py",
        "source_components.py", "regions.py", "qc_graph.py",
    )
    allowed_modules = {
        "maximum_optimizer",
        *(f"maximum_optimizer.{name[:-3]}" for name in order[1:]),
    }
    for name in tuple(sys.modules):
        if name == "maximum_optimizer" or name.startswith("maximum_optimizer."):
            del sys.modules[name]
    package = types.ModuleType("maximum_optimizer")
    package.__file__ = str(root / "__init__.py")
    package.__package__ = "maximum_optimizer"
    package.__path__ = []
    sys.modules["maximum_optimizer"] = package
    try:
        exec(
            compile(captured["__init__.py"], package.__file__, "exec"),
            package.__dict__,
        )
        package.__path__ = []
        for filename in order[1:]:
            short_name = filename[:-3]
            qualified = f"maximum_optimizer.{short_name}"
            module = types.ModuleType(qualified)
            module.__file__ = str(root / filename)
            module.__package__ = "maximum_optimizer"
            sys.modules[qualified] = module
            exec(compile(captured[filename], module.__file__, "exec"), module.__dict__)
            setattr(package, short_name, module)
        package.__path__ = []
    except BaseException:
        for name in tuple(sys.modules):
            if name in allowed_modules:
                del sys.modules[name]
        raise


def _bootstrap_source_union_runtime() -> None:
    global _SOURCE_UNION_BOOTSTRAP_CONTROL_BYTES
    global _SOURCE_UNION_BOOTSTRAP_RUNTIME_FILES
    contract_flag = "--source-union-contract"
    visibility_flag = "--source-union-visibility-out"
    control_hash_flag = "--source-union-control-sha256"
    flags = (contract_flag, visibility_flag, control_hash_flag)
    source_tokens = tuple(
        value for value in sys.argv if value.startswith("--source-union-")
    )
    if not source_tokens:
        return
    if any(
        not any(value == flag or value.startswith(flag + "=") for flag in flags)
        for value in source_tokens
    ):
        raise ValueError("source-union bootstrap flag is unknown")

    def flag_value(flag: str) -> str:
        values = []
        for index, value in enumerate(sys.argv):
            if value == flag:
                if index + 1 >= len(sys.argv) or sys.argv[index + 1].startswith("--"):
                    raise ValueError(f"source-union bootstrap {flag} value is missing")
                values.append(sys.argv[index + 1])
            elif value.startswith(flag + "="):
                values.append(value[len(flag) + 1:])
        if len(values) != 1 or not values[0]:
            raise ValueError(f"source-union bootstrap {flag} must occur exactly once")
        return values[0]

    contract_value = flag_value(contract_flag)
    flag_value(visibility_flag)
    expected_control_hash = flag_value(control_hash_flag)
    if re.fullmatch(r"[0-9a-f]{64}", expected_control_hash) is None:
        raise ValueError("source-union bootstrap control hash argument is invalid")
    script = Path(__file__).resolve(strict=True)
    inputs = script.parent
    workspace = inputs.parent
    contract = Path(contract_value).resolve(strict=True)
    if (
        script != inputs / "render_previews.py"
        or inputs.name != "inputs"
        or contract != workspace / "control" / "source-union-contract.json"
    ):
        raise ValueError("source-union bootstrap private paths differ")
    control_bytes = _bootstrap_read_regular(contract, max_bytes=32 * 1024 * 1024)
    if hashlib.sha256(control_bytes).hexdigest() != expected_control_hash:
        raise ValueError("source-union bootstrap control bytes differ from parent anchor")
    try:
        control = json.loads(control_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("source-union bootstrap control JSON is invalid") from exc
    control_fields = {
        "schema", "kind", "target_sha256", "comparison_contract",
        "source_identity", "source_coverage_sha256", "component_keys",
        "component_manifest", "candidate_component_transfer",
        "material_region_keys", "material_contract_sha256",
        "material_contract", "material_render_evidence", "pose_frames",
        "angles", "cameras", "renderer_sha256",
        "python_runtime_contract_sha256", "python_runtime_files",
    }
    if type(control) is not dict or set(control) != control_fields:
        raise ValueError("source-union bootstrap control fields are invalid")
    raw_files = control["python_runtime_files"]
    names = (
        "__init__.py", "qc_graph.py", "regions.py", "reporting.py",
        "smd_contract.py", "source_components.py",
    )
    if type(raw_files) is not list or len(raw_files) != len(names):
        raise ValueError("source-union bootstrap runtime file count differs")
    files = []
    total = 0
    for index, item in enumerate(raw_files):
        if (
            type(item) is not dict or set(item) != {"path", "size", "sha256"}
            or type(item["path"]) is not str or item["path"] != names[index]
            or type(item["size"]) is not int or item["size"] < 0
            or type(item["sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
        ):
            raise ValueError("source-union bootstrap runtime proof is invalid")
        total += item["size"]
        if total > 16 * 1024 * 1024:
            raise ValueError("source-union bootstrap runtime byte bound exceeded")
        files.append(dict(item))
    unsigned = {
        "schema": 1,
        "kind": "adaptive-direct-source-union-python-runtime-v1",
        "files": files,
    }
    seal = control["python_runtime_contract_sha256"]
    canonical = json.dumps(
        unsigned, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if (
        type(seal) is not str or re.fullmatch(r"[0-9a-f]{64}", seal) is None
        or hashlib.sha256(canonical).hexdigest() != seal
    ):
        raise ValueError("source-union bootstrap runtime seal differs")
    runtime = inputs / "maximum_optimizer"
    if not runtime.is_dir() or not _bootstrap_safe_ancestry(runtime):
        raise ValueError("source-union bootstrap runtime root is invalid")
    entries = tuple(runtime.iterdir())
    if tuple(sorted(path.name for path in entries)) != names:
        raise ValueError("source-union bootstrap runtime inventory differs")
    captured = {}
    for proof in files:
        payload = _bootstrap_read_regular(
            runtime / proof["path"], max_bytes=16 * 1024 * 1024,
        )
        if (
            len(payload) != proof["size"]
            or hashlib.sha256(payload).hexdigest() != proof["sha256"]
        ):
            raise ValueError("source-union bootstrap runtime bytes differ")
        captured[proof["path"]] = payload
    _SOURCE_UNION_BOOTSTRAP_RUNTIME_FILES = tuple(files)
    _SOURCE_UNION_BOOTSTRAP_CONTROL_BYTES = control_bytes
    _bootstrap_install_runtime(runtime, captured)


try:
    _bootstrap_source_union_runtime()
except (OSError, TypeError, ValueError) as exc:
    raise SystemExit(f"[ERROR] {exc}") from exc

_SCRIPT_ROOT = Path(__file__).resolve().parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from maximum_optimizer.regions import (
    RegionManifest,
    is_region_key,
    load_region_manifest_payload,
    manifest_for_region,
    manifest_for_source,
    normalized_source_identity as _normalized_source_identity,
    resolve_region_assignments as _resolve_region_assignments,
    source_material_slot_identities as _source_material_slot_identities,
)
from maximum_optimizer.qc_graph import _lex as _lex_qc, parse_qc_graph

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
GEOMETRY_AUDIT_ALGORITHM = {
    "name": "relative-cross-area-squared-v1",
    "relative_area_squared_epsilon": 1e-24,
    "max_filtered_fraction": 0.05,
}


@dataclass(frozen=True)
class _SourceUnionControl:
    comparison: object
    component_manifest: object
    candidate_transfer: object
    material_contract: object
    material_evidence: tuple[dict[str, object], ...]
    component_keys: tuple[str, ...]
    material_region_keys: tuple[str, ...]
    python_runtime_contract_sha256: str
    python_runtime_files: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class _SourceUnionComparisonControl:
    target_sha256: str
    source_identity: str
    source_coverage_sha256: str
    reference_source_sha256: str
    candidate_source_sha256: str
    material_contract_sha256: str
    pose_frames: tuple[tuple[str, int], ...]
    union_key: str
    contract_sha256: str


@dataclass(frozen=True)
class _SourceUnionMaterialBindingControl:
    material_region_key: str
    smd_material: str
    search_paths: tuple[str, ...]
    root_index: int
    search_path_index: int
    vmt_file_index: int
    vmt_path: str
    texture_identity: str
    vtf_root_index: int
    vtf_file_index: int
    vtf_path: str
    shader: str
    texture_directive: str
    uses_texture_alpha: bool
    duplicate_root_directives: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class _SourceUnionMaterialControl:
    source_identity: str
    filtered_source_sha256: str
    bindings: tuple[_SourceUnionMaterialBindingControl, ...]
    material_contract_sha256: str


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    )


def _strict_hash(value: object, label: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _strict_relative(value: object, label: str) -> str:
    if (
        type(value) is not str or not value or len(value) > 4096
        or any(char in value for char in "\r\n\0")
    ):
        raise ValueError(f"{label} is invalid")
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    canonical = PurePosixPath(*(part for part in posix.parts if part not in ("", "."))).as_posix()
    if (
        posix.is_absolute() or windows.is_absolute() or windows.drive
        or ".." in posix.parts or canonical != value or canonical in ("", ".")
    ):
        raise ValueError(f"{label} is not canonical")
    return value


def _strict_smd_material(value: object) -> str:
    if type(value) is not str or "\\" in value:
        raise ValueError("source-union SMD material is invalid")
    _strict_relative(value, "source-union SMD material")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("source-union SMD material is unsafe")
    return value


def _strict_duplicate_directives(raw: object) -> tuple[dict[str, object], ...]:
    if type(raw) is not list or len(raw) > 64:
        raise ValueError("source-union duplicate directives are invalid")
    result = []
    total = 0
    for item in raw:
        if (
            type(item) is not dict
            or set(item) != {"directive", "ignored_values"}
            or type(item["directive"]) is not str
            or not item["directive"]
            or len(item["directive"]) > 4096
            or item["directive"] != item["directive"].casefold()
            or type(item["ignored_values"]) is not list
            or not 0 < len(item["ignored_values"]) <= 64
            or any(
                type(value) is not str or not value or len(value) > 4096
                or any(char in value for char in "\r\n\0")
                for value in item["ignored_values"]
            )
        ):
            raise ValueError("source-union duplicate directive shape is invalid")
        total += len(item["ignored_values"])
        if total > 1024:
            raise ValueError("source-union duplicate directive bound exceeded")
        result.append({
            "directive": item["directive"],
            "ignored_values": list(item["ignored_values"]),
        })
    names = tuple(item["directive"] for item in result)
    if names != tuple(sorted(set(names))):
        raise ValueError("source-union duplicate directive order differs")
    return tuple(result)


def _parse_source_union_comparison(raw: object) -> _SourceUnionComparisonControl:
    fields = {
        "contract_sha256", "target_sha256", "source_identity",
        "source_coverage_sha256", "reference_source_sha256",
        "candidate_source_sha256", "material_contract_sha256",
        "pose_frames", "union_key",
    }
    if type(raw) is not dict or set(raw) != fields or type(raw["pose_frames"]) is not list:
        raise ValueError("source-union comparison control fields are invalid")
    poses = tuple(
        tuple(item) if type(item) is list else () for item in raw["pose_frames"]
    )
    if poses != (("bind", 0),) or type(poses[0][1]) is not int:
        raise ValueError("source-union comparison pose contract is invalid")
    hashes = tuple(_strict_hash(raw[name], f"source-union comparison {name}") for name in (
        "target_sha256", "source_coverage_sha256", "reference_source_sha256",
        "candidate_source_sha256", "material_contract_sha256", "contract_sha256",
    ))
    source_identity = _strict_relative(raw["source_identity"], "source-union comparison source")
    union_key = raw["union_key"]
    if (
        type(union_key) is not str
        or union_key != "source-union-" + raw["source_coverage_sha256"][:32]
    ):
        raise ValueError("source-union comparison union key is invalid")
    unsigned = {
        key: raw[key] for key in (
            "target_sha256", "source_identity", "source_coverage_sha256",
            "reference_source_sha256", "candidate_source_sha256",
            "material_contract_sha256", "pose_frames", "union_key",
        )
    }
    if hashlib.sha256(_canonical_json(unsigned).encode()).hexdigest() != raw["contract_sha256"]:
        raise ValueError("source-union comparison contract seal mismatch")
    return _SourceUnionComparisonControl(
        target_sha256=hashes[0], source_identity=source_identity,
        source_coverage_sha256=hashes[1], reference_source_sha256=hashes[2],
        candidate_source_sha256=hashes[3], material_contract_sha256=hashes[4],
        pose_frames=poses, union_key=union_key, contract_sha256=hashes[5],
    )


def _parse_source_union_material_control(
    raw: object, reference_bytes: bytes, material_roots,
) -> tuple[_SourceUnionMaterialControl, tuple[dict[str, object], ...]]:
    fields = {
        "schema", "kind", "resolution_rule", "source_identity",
        "filtered_source_sha256", "roots", "files", "bindings",
        "material_contract_sha256",
    }
    if type(raw) is not dict or set(raw) != fields:
        raise ValueError("source-union material control fields are invalid")
    if (
        type(raw["schema"]) is not int or raw["schema"] != 1
        or raw["kind"] != "adaptive-direct-source-union-material-v1"
        or raw["resolution_rule"] != "materials-root-order-then-qc-search-order-v1"
        or type(raw["roots"]) is not list or type(raw["files"]) is not list
        or type(raw["bindings"]) is not list
    ):
        raise ValueError("source-union material control identity is invalid")
    if (
        not 0 < len(raw["roots"]) <= 64
        or not 0 < len(raw["files"]) <= 512
        or not 0 < len(raw["bindings"]) <= 256
    ):
        raise ValueError("source-union material control bound exceeded")
    source_identity = _strict_relative(raw["source_identity"], "source-union material source")
    filtered_hash = _strict_hash(raw["filtered_source_sha256"], "source-union material source hash")
    if filtered_hash != hashlib.sha256(reference_bytes).hexdigest():
        raise ValueError("source-union material filtered source differs")
    contract_hash = _strict_hash(raw["material_contract_sha256"], "source-union material contract hash")
    unsigned = dict(raw); unsigned.pop("material_contract_sha256")
    if hashlib.sha256(_canonical_json(unsigned).encode()).hexdigest() != contract_hash:
        raise ValueError("source-union material contract seal mismatch")
    roots = tuple(material_roots)
    if len(raw["roots"]) != len(roots) or not roots:
        raise ValueError("source-union material root count differs")
    for index, root in enumerate(raw["roots"]):
        if (
            type(root) is not dict
            or set(root) != {"root_index", "root_identity"}
            or type(root["root_index"]) is not int
            or root["root_index"] != index
            or type(root["root_identity"]) is not str
            or root["root_identity"] != f"material-root-{index:03d}"
        ):
            raise ValueError("source-union material root contract differs")
    file_fields = {"root_index", "path", "kind", "size", "sha256"}
    files = []
    total_size = 0
    for item in raw["files"]:
        if type(item) is not dict or set(item) != file_fields:
            raise ValueError("source-union material file fields are invalid")
        root_index = item["root_index"]
        path = _strict_relative(item["path"], "source-union material file")
        if (
            type(root_index) is not int or root_index not in range(len(roots))
            or item["kind"] not in {"vmt", "vtf"}
            or type(item["size"]) is not int or item["size"] < 0
            or (item["kind"] == "vmt" and item["size"] > 8 * 1024 * 1024)
            or not path.casefold().endswith("." + item["kind"])
        ):
            raise ValueError("source-union material file contract is invalid")
        total_size += item["size"]
        if total_size > 512 * 1024 * 1024:
            raise ValueError("source-union material byte bound exceeded")
        digest = _strict_hash(item["sha256"], "source-union material file hash")
        current = _contained_material_path(roots[root_index], path, "")
        if current is None or not current.is_file():
            raise ValueError("source-union material file is unavailable")
        payload = current.read_bytes()
        if len(payload) != item["size"] or hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("source-union material file bytes differ")
        files.append(dict(item))
    file_keys = tuple(
        (item["root_index"], item["path"].casefold(), item["path"])
        for item in files
    )
    if file_keys != tuple(sorted(file_keys)) or len({key[:2] for key in file_keys}) != len(files):
        raise ValueError("source-union material file order differs")
    binding_fields = {
        "material_region_key", "smd_material", "search_paths", "root_index",
        "search_path_index", "vmt_file_index", "vmt_path", "texture_identity",
        "vtf_root_index", "vtf_file_index", "vtf_path", "shader",
        "texture_directive", "uses_texture_alpha", "duplicate_root_directives",
    }
    bindings = []
    evidence = []
    referenced = set()
    for item in raw["bindings"]:
        if type(item) is not dict or set(item) != binding_fields:
            raise ValueError("source-union material binding fields are invalid")
        searches = item["search_paths"]
        duplicates = item["duplicate_root_directives"]
        if (
            type(searches) is not list or type(duplicates) is not list
            or type(item["uses_texture_alpha"]) is not bool
            or any(type(item[name]) is not int for name in (
                "root_index", "search_path_index", "vmt_file_index",
                "vtf_root_index", "vtf_file_index",
            ))
        ):
            raise ValueError("source-union material binding contract is invalid")
        searches_tuple = tuple(
            _strict_relative(value, "source-union material search path")
            for value in searches
        )
        if (
            len(searches_tuple) > 64
            or len({value.casefold() for value in searches_tuple}) != len(searches_tuple)
        ):
            raise ValueError("source-union material search paths differ")
        duplicate_values = _strict_duplicate_directives(duplicates)
        smd_material = _strict_smd_material(item["smd_material"])
        shader = item["shader"]
        texture_directive = item["texture_directive"]
        if (
            type(shader) is not str or not shader or len(shader) > 4096
            or shader not in _SUPPORTED_VMT_SHADERS
            or type(texture_directive) is not str or not texture_directive
            or len(texture_directive) > 4096
            or texture_directive != (
                "$refracttinttexture" if shader == "refract" else "$basetexture"
            )
            or (shader == "refract" and item["uses_texture_alpha"] is not True)
        ):
            raise ValueError("source-union material shader/directive contract differs")
        binding = _SourceUnionMaterialBindingControl(
            material_region_key=_strict_relative(item["material_region_key"], "source-union material region"),
            smd_material=smd_material,
            search_paths=searches_tuple,
            root_index=item["root_index"], search_path_index=item["search_path_index"],
            vmt_file_index=item["vmt_file_index"],
            vmt_path=_strict_relative(item["vmt_path"], "source-union material VMT"),
            texture_identity=_strict_relative(item["texture_identity"], "source-union texture identity"),
            vtf_root_index=item["vtf_root_index"], vtf_file_index=item["vtf_file_index"],
            vtf_path=_strict_relative(item["vtf_path"], "source-union material VTF"),
            shader=shader, texture_directive=texture_directive,
            uses_texture_alpha=item["uses_texture_alpha"],
            duplicate_root_directives=duplicate_values,
        )
        material_identity = (
            binding.smd_material[:-4]
            if binding.smd_material.casefold().endswith(".vmt")
            else binding.smd_material
        )
        expected_vmt = material_identity
        if binding.search_paths and len(PurePosixPath(material_identity).parts) == 1:
            expected_vmt = (
                PurePosixPath(binding.search_paths[binding.search_path_index])
                / material_identity
            ).as_posix() if binding.search_path_index < len(binding.search_paths) else ""
        elif binding.search_path_index != 0:
            expected_vmt = ""
        expected_vmt = expected_vmt if expected_vmt.casefold().endswith(".vmt") else expected_vmt + ".vmt"
        expected_vtf = (
            binding.texture_identity
            if binding.texture_identity.casefold().endswith(".vtf")
            else binding.texture_identity + ".vtf"
        )
        if (
            binding.root_index not in range(len(roots))
            or binding.vtf_root_index not in range(len(roots))
            or binding.vmt_file_index not in range(len(files))
            or binding.vtf_file_index not in range(len(files))
            or binding.search_path_index not in range(max(1, len(searches_tuple)))
            or files[binding.vmt_file_index]["path"] != binding.vmt_path
            or files[binding.vtf_file_index]["path"] != binding.vtf_path
            or files[binding.vmt_file_index]["root_index"] != binding.root_index
            or files[binding.vmt_file_index]["kind"] != "vmt"
            or files[binding.vtf_file_index]["root_index"] != binding.vtf_root_index
            or files[binding.vtf_file_index]["kind"] != "vtf"
            or binding.vmt_path.casefold() != expected_vmt.casefold()
            or binding.vtf_path.casefold() != expected_vtf.casefold()
        ):
            raise ValueError("source-union material binding/file relation differs")
        current = _source_material_evidence(
            binding.smd_material, roots, search_paths=searches_tuple,
        )
        expected = {
            "material_identity": binding.material_region_key,
            "resolution_rule": raw["resolution_rule"],
            "root_index": binding.root_index,
            "search_path_index": binding.search_path_index,
            "vtf_root_index": binding.vtf_root_index,
            "vmt_sha256": files[binding.vmt_file_index]["sha256"],
            "vtf_sha256": files[binding.vtf_file_index]["sha256"],
            "shader": binding.shader,
            "texture_directive": binding.texture_directive,
            "uses_texture_alpha": binding.uses_texture_alpha,
            "duplicate_root_directives": list(duplicate_values),
        }
        if current is None or {**current, "material_identity": binding.material_region_key} != expected:
            raise ValueError("source-union current material evidence differs")
        bindings.append(binding); evidence.append(expected)
        referenced.update((binding.vmt_file_index, binding.vtf_file_index))
    binding_region_keys = tuple(item.material_region_key.casefold() for item in bindings)
    binding_materials = tuple(item.smd_material.casefold() for item in bindings)
    if (
        referenced != set(range(len(files))) or not bindings
        or len(set(binding_region_keys)) != len(bindings)
        or len(set(binding_materials)) != len(bindings)
    ):
        raise ValueError("source-union material file coverage differs")
    return _SourceUnionMaterialControl(
        source_identity, filtered_hash, tuple(bindings), contract_hash,
    ), tuple(evidence)


def _parse_source_union_python_runtime(
    raw_files: object, raw_contract_hash: object, runtime_root: Path | None = None,
) -> tuple[str, tuple[dict[str, object], ...]]:
    names = (
        "__init__.py", "qc_graph.py", "regions.py", "reporting.py",
        "smd_contract.py", "source_components.py",
    )
    contract_hash = _strict_hash(
        raw_contract_hash, "source-union Python runtime contract hash",
    )
    if type(raw_files) is not list or len(raw_files) != len(names):
        raise ValueError("source-union Python runtime file count differs")
    files = []
    total = 0
    for index, item in enumerate(raw_files):
        if (
            type(item) is not dict or set(item) != {"path", "size", "sha256"}
            or type(item["path"]) is not str or item["path"] != names[index]
            or type(item["size"]) is not int or item["size"] < 0
        ):
            raise ValueError("source-union Python runtime file proof is invalid")
        digest = _strict_hash(
            item["sha256"], "source-union Python runtime file hash",
        )
        total += item["size"]
        if total > 16 * 1024 * 1024:
            raise ValueError("source-union Python runtime byte bound exceeded")
        files.append({
            "path": item["path"], "size": item["size"], "sha256": digest,
        })
    unsigned = {
        "schema": 1,
        "kind": "adaptive-direct-source-union-python-runtime-v1",
        "files": files,
    }
    if hashlib.sha256(_canonical_json(unsigned).encode()).hexdigest() != contract_hash:
        raise ValueError("source-union Python runtime contract seal mismatch")
    if (
        _SOURCE_UNION_BOOTSTRAP_RUNTIME_FILES is not None
        and tuple(files) != _SOURCE_UNION_BOOTSTRAP_RUNTIME_FILES
    ):
        raise ValueError("source-union Python runtime differs from bootstrap capture")
    runtime_root = (
        Path(runtime_root).resolve()
        if runtime_root is not None
        else Path(__file__).resolve().parent / "maximum_optimizer"
    )
    if (
        runtime_root.parent.name != "inputs"
        or runtime_root.name != "maximum_optimizer"
        or not runtime_root.is_dir()
        or _path_is_link_or_reparse(runtime_root)
    ):
        raise ValueError("source-union private Python runtime root differs")
    try:
        entries = tuple(runtime_root.iterdir())
    except OSError as exc:
        raise ValueError("source-union private Python runtime is unavailable") from exc
    if tuple(sorted(path.name for path in entries)) != names:
        raise ValueError("source-union private Python runtime inventory differs")
    for proof in files:
        path = runtime_root / proof["path"]
        try:
            info = path.lstat()
            payload = path.read_bytes()
        except OSError as exc:
            raise ValueError("source-union private Python runtime file is unavailable") from exc
        if (
            _path_is_link_or_reparse(path)
            or not stat.S_ISREG(info.st_mode)
            or info.st_size != proof["size"]
            or len(payload) != proof["size"]
            or hashlib.sha256(payload).hexdigest() != proof["sha256"]
        ):
            raise ValueError("source-union private Python runtime bytes differ")
    return contract_hash, tuple(files)


def _parse_source_union_control(
    payload: object,
    reference_bytes: bytes,
    candidate_bytes: bytes,
    material_roots,
    *,
    private_python_runtime_root: Path | None = None,
) -> _SourceUnionControl:
    from maximum_optimizer.source_components import (
        source_component_manifest_from_payload,
        source_component_transfer_from_payload,
        validate_source_component_transfer_against_manifest,
    )
    from maximum_optimizer.smd_contract import (
        direct_smd_material_counts,
        match_direct_output_triangle_ordinals,
        parse_smd_triangles,
    )

    fields = {
        "schema", "kind", "target_sha256", "comparison_contract",
        "source_identity", "source_coverage_sha256", "component_keys",
        "component_manifest", "candidate_component_transfer",
        "material_region_keys", "material_contract_sha256",
        "material_contract", "material_render_evidence", "pose_frames",
        "angles", "cameras", "renderer_sha256",
        "python_runtime_contract_sha256", "python_runtime_files",
    }
    if type(payload) is not dict or set(payload) != fields:
        raise ValueError("source-union control fields are invalid")
    if (
        type(payload["schema"]) is not int
        or payload["schema"] != 1
        or payload["kind"] != "adaptive-direct-source-union-render-v1"
    ):
        raise ValueError("source-union control identity is invalid")
    raw_comparison = payload["comparison_contract"]
    comparison_fields = {
        "contract_sha256", "target_sha256", "source_identity",
        "source_coverage_sha256", "reference_source_sha256",
        "candidate_source_sha256", "material_contract_sha256",
        "pose_frames", "union_key",
    }
    if type(raw_comparison) is not dict or set(raw_comparison) != comparison_fields:
        raise ValueError("source-union comparison control fields are invalid")
    try:
        comparison = _parse_source_union_comparison(raw_comparison)
        component_manifest = source_component_manifest_from_payload(
            payload["component_manifest"]
        )
        candidate_transfer = source_component_transfer_from_payload(
            payload["candidate_component_transfer"]
        )
        validate_source_component_transfer_against_manifest(
            candidate_transfer, component_manifest,
        )
        material_contract, material_evidence = _parse_source_union_material_control(
            payload["material_contract"], reference_bytes, material_roots,
        )
        runtime_hash, runtime_files = _parse_source_union_python_runtime(
            payload["python_runtime_files"],
            payload["python_runtime_contract_sha256"],
            private_python_runtime_root,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("source-union sealed control is invalid") from exc

    component_keys = tuple(payload["component_keys"]) if type(payload["component_keys"]) is list else ()
    material_region_keys = (
        tuple(payload["material_region_keys"])
        if type(payload["material_region_keys"]) is list else ()
    )
    expected_components = tuple(
        item.component_key for item in component_manifest.components
    )
    expected_materials = tuple(
        item.material_region_key for item in material_contract.bindings
    )
    reference_hash = hashlib.sha256(reference_bytes).hexdigest()
    candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()
    renderer_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if any((
        payload["target_sha256"] != comparison.target_sha256,
        payload["source_identity"] != comparison.source_identity,
        payload["source_coverage_sha256"] != comparison.source_coverage_sha256,
        payload["material_contract_sha256"] != comparison.material_contract_sha256,
        material_contract.material_contract_sha256 != comparison.material_contract_sha256,
        material_contract.source_identity != comparison.source_identity,
        material_contract.filtered_source_sha256 != reference_hash,
        component_manifest.filtered_source_sha256 != reference_hash,
        candidate_transfer.candidate_sha256 != candidate_hash,
        comparison.reference_source_sha256 != reference_hash,
        comparison.candidate_source_sha256 != candidate_hash,
        component_keys != expected_components,
        material_region_keys != expected_materials,
        payload["pose_frames"] != {"bind": 0},
        payload["angles"] != list(ANGLE_DIRS),
        payload["cameras"] != [f"camera-{index:02d}" for index in range(8)],
        payload["renderer_sha256"] != renderer_hash,
    )):
        raise ValueError("source-union control binding differs")

    try:
        reference_text = reference_bytes.decode("utf-8", errors="strict")
        candidate_text = candidate_bytes.decode("utf-8", errors="strict")
        direct_smd_material_counts(reference_text)
        direct_smd_material_counts(candidate_text)
        reference_triangles = parse_smd_triangles(reference_text).triangles
        candidate_triangles = parse_smd_triangles(candidate_text).triangles
        reference_materials = tuple(
            item[0] for item in direct_smd_material_counts(reference_text)
        )
        source_ordinals = match_direct_output_triangle_ordinals(
            reference_text, candidate_text,
        )
    except (UnicodeError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError("source-union current SMD provenance is invalid") from exc
    if (
        len(reference_triangles) != component_manifest.triangle_count
        or len(candidate_triangles) != candidate_transfer.triangle_count
        or source_ordinals != tuple(
            item.source_triangle_ordinal for item in candidate_transfer.triangles
        )
        or reference_materials != tuple(
            item.smd_material for item in material_contract.bindings
        )
    ):
        raise ValueError("source-union current SMD provenance differs")

    if (
        type(payload["material_render_evidence"]) is not list
        or list(material_evidence) != payload["material_render_evidence"]
    ):
        raise ValueError("source-union material render evidence differs")
    return _SourceUnionControl(
        comparison=comparison,
        component_manifest=component_manifest,
        candidate_transfer=candidate_transfer,
        material_contract=material_contract,
        material_evidence=material_evidence,
        component_keys=component_keys,
        material_region_keys=material_region_keys,
        python_runtime_contract_sha256=runtime_hash,
        python_runtime_files=runtime_files,
    )


def _source_union_component_groups(
    smd_bytes: bytes, component_manifest, candidate_transfer=None,
):
    from maximum_optimizer.smd_contract import (
        direct_smd_material_counts, parse_smd_triangles,
    )
    from maximum_optimizer.source_components import (
        SourceComponentManifest, SourceComponentTransferProof,
        validate_source_component_transfer_against_manifest,
    )

    if not isinstance(component_manifest, SourceComponentManifest):
        raise TypeError("source-union component manifest is invalid")
    try:
        text = smd_bytes.decode("utf-8", errors="strict")
        direct_smd_material_counts(text)
        triangles = parse_smd_triangles(text).triangles
    except (UnicodeError, TypeError, ValueError) as exc:
        raise ValueError("source-union direct SMD is invalid") from exc
    if candidate_transfer is None:
        if len(triangles) != component_manifest.triangle_count:
            raise ValueError("source-union reference triangle count differs")
        by_ordinal = {
            ordinal: component.component_key
            for component in component_manifest.components
            for ordinal in component.triangle_ordinals
        }
    else:
        if not isinstance(candidate_transfer, SourceComponentTransferProof):
            raise TypeError("source-union candidate transfer is invalid")
        validate_source_component_transfer_against_manifest(
            candidate_transfer, component_manifest,
        )
        if len(triangles) != candidate_transfer.triangle_count:
            raise ValueError("source-union candidate triangle count differs")
        by_ordinal = {
            item.candidate_triangle_ordinal: item.component_key
            for item in candidate_transfer.triangles
        }
    grouped = {key: [] for key in (
        item.component_key for item in component_manifest.components
    )}
    for ordinal, triangle in enumerate(triangles):
        try:
            grouped[by_ordinal[ordinal]].append(triangle)
        except KeyError as exc:
            raise ValueError("source-union component provenance is incomplete") from exc
    if any(not grouped[key] for key in grouped):
        raise ValueError("source-union component provenance dropped a component")
    return tuple((key, tuple(grouped[key])) for key in grouped)


def _count_cryptomatte_components(
    pixels,
    manifest: dict[str, str],
    component_keys: tuple[str, ...],
) -> dict[str, int]:
    if (
        type(manifest) is not dict
        or set(manifest) != set(component_keys)
        or any(
            type(value) is not str
            or re.fullmatch(r"[0-9a-f]{8}", value) is None
            for value in manifest.values()
        )
    ):
        raise ValueError("cryptomatte manifest component set is invalid")
    by_hash = {int(value, 16): key for key, value in manifest.items()}
    if len(by_hash) != len(component_keys):
        raise ValueError("cryptomatte manifest component hashes collide")
    counts = {key: 0 for key in component_keys}
    for pixel in pixels:
        visible = set()
        for identifier, coverage in pixel:
            if not isinstance(coverage, (int, float)) or not math.isfinite(float(coverage)):
                raise ValueError("cryptomatte coverage is invalid")
            if float(coverage) <= 0.0:
                continue
            try:
                value = struct.unpack("<I", struct.pack("<f", float(identifier)))[0]
            except (OverflowError, TypeError, ValueError, struct.error) as exc:
                raise ValueError("cryptomatte identifier is invalid") from exc
            key = by_hash.get(value)
            if key is not None:
                visible.add(key)
        for key in visible:
            counts[key] += 1
    return counts


def _source_union_visibility_payload(
    control: _SourceUnionControl,
    counts: dict[tuple[str, str, str], int],
) -> dict[str, object]:
    if not isinstance(control, _SourceUnionControl) or type(counts) is not dict:
        raise TypeError("source-union visibility inputs are invalid")
    expected_keys = tuple(
        (side, component, f"camera-{camera:02d}")
        for side in ("candidate", "reference")
        for component in control.component_keys
        for camera in range(8)
    )
    if set(counts) != set(expected_keys) or any(
        type(counts[key]) is not int or counts[key] < 0 for key in expected_keys
    ):
        raise ValueError("source-union visibility count matrix differs")
    comparison = control.comparison
    unsigned = {
        "schema": 1,
        "kind": "adaptive-direct-source-union-visibility-v1",
        "target_sha256": comparison.target_sha256,
        "source_identity": comparison.source_identity,
        "source_coverage_sha256": comparison.source_coverage_sha256,
        "cameras": [f"camera-{index:02d}" for index in range(8)],
        "pose_keys": ["bind"],
        "observations": [{
            "side": side,
            "component_key": component,
            "pose_key": "bind",
            "camera_key": camera,
            "visible_mask_pixels": counts[(side, component, camera)],
        } for side, component, camera in expected_keys],
    }
    seal = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**unsigned, "evidence_sha256": seal}


def _require_source_union_dithered_alpha(material, uses_alpha: bool) -> None:
    if type(uses_alpha) is not bool:
        raise TypeError("source-union alpha contract is invalid")
    if not uses_alpha:
        return
    if not hasattr(material, "surface_render_method"):
        raise ValueError("source-union alpha requires Blender DITHERED rendering")
    try:
        material.surface_render_method = "DITHERED"
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("source-union alpha requires Blender DITHERED rendering") from exc
    if material.surface_render_method != "DITHERED":
        raise ValueError("source-union alpha requires Blender DITHERED rendering")


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
    current = root
    if current.is_symlink() or not current.is_dir():
        return None
    for part in relative.parts:
        try:
            matches = tuple(
                child for child in current.iterdir()
                if child.name.casefold() == part.casefold()
            )
        except OSError:
            return None
        if len(matches) != 1 or matches[0].is_symlink():
            return None
        current = matches[0]
    try:
        current.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return current.resolve()


def _normalized_material_search_path(raw: str) -> str:
    normalized = raw.replace("\\", "/").strip().rstrip("/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(raw)
    if (
        not normalized
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
    ):
        raise ValueError(f"unsafe $cdmaterials path: {raw}")
    return PurePosixPath(*(part for part in posix.parts if part not in ("", "."))).as_posix()


def _source_cdmaterial_search_paths(
    manifest: RegionManifest, manifest_root: Path
) -> dict[str, tuple[str, ...]]:
    manifest_root = Path(manifest_root).resolve(strict=True)
    sources = sorted({entry.descriptor.source_identity for entry in manifest.entries})
    occurrence_files: dict[str, list[str]] = {source: [] for source in sources}
    for entry in manifest.entries:
        target = occurrence_files[entry.descriptor.source_identity]
        for occurrence in entry.occurrences:
            if occurrence.graph_file not in target:
                target.append(occurrence.graph_file)

    def paths_from_files(paths: tuple[Path, ...]) -> tuple[str, ...]:
        search_paths: list[str] = []
        for path in paths:
            graph_file = path.relative_to(manifest_root).as_posix()
            try:
                tokens = _lex_qc(path.read_text(encoding="utf-8-sig", errors="strict"))
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"cannot read QC/QCI material evidence: {graph_file}") from exc
            for index, token in enumerate(tokens):
                if token.value.casefold() != "$cdmaterials":
                    continue
                cursor = index + 1
                if cursor >= len(tokens) or tokens[cursor].kind in {"newline", "brace"}:
                    raise ValueError(f"$cdmaterials has no path at {graph_file}:{token.line}")
                value = _normalized_material_search_path(tokens[cursor].value)
                if value not in search_paths:
                    search_paths.append(value)
        return tuple(search_paths)

    collected: dict[str, list[str]] = {source: [] for source in sources}
    matched_graph: set[str] = set()
    root_qcs = tuple(
        path
        for path in sorted(
            (item for item in manifest_root.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(manifest_root).as_posix().casefold(),
        )
        if path.suffix.casefold() == ".qc"
        and not path.stem.casefold().endswith("_opt")
        and "output" not in {part.casefold() for part in path.parts}
    )
    for root_qc in root_qcs:
        graph = parse_qc_graph(root_qc, manifest_root)
        graph_sources = {
            _normalized_source_identity(
                reference.source_path.relative_to(manifest_root).as_posix()
            )
            for reference in graph.references
            if reference.role == "visual"
        }
        relevant = graph_sources.intersection(sources)
        if not relevant:
            continue
        graph_paths = paths_from_files(tuple(item.path for item in graph.files))
        for source in relevant:
            matched_graph.add(source)
            for value in graph_paths:
                if value not in collected[source]:
                    collected[source].append(value)

    for source in sources:
        if source in matched_graph:
            continue
        direct_files: list[Path] = []
        for graph_file in occurrence_files[source]:
            path = _contained_material_path(manifest_root, graph_file, "")
            if path is None or not path.is_file():
                raise ValueError(f"QC/QCI material evidence is missing: {graph_file}")
            if path not in direct_files:
                direct_files.append(path)
        collected[source].extend(paths_from_files(tuple(direct_files)))
    return {source: tuple(collected[source]) for source in sources}


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
    ap.add_argument("--animation-before", default=None, help="Original animation SMD/DMX for representative poses")
    ap.add_argument("--animation-after", default=None, help="Optimized animation SMD/DMX for representative poses")
    ap.add_argument(
        "--materials-root",
        default=None,
        action="append",
        help=(
            "Source materials directory; short SMD material names are resolved "
            "through $cdmaterials in the QC/QCI occurrences recorded by --region-manifest"
        ),
    )
    ap.add_argument("--vtfcmd", default=None, help="Optional VTFCmd executable")
    ap.add_argument(
        "--texture-cache", default=None,
        help="Shared short cache directory for deterministic VTF conversions",
    )
    ap.add_argument(
        "--region-manifest", default=None,
        help="Required shared Maximum region manifest for extended validation",
    )
    ap.add_argument(
        "--source-root", default=None,
        help="Required source/QC root for extended validation; independent of manifest location",
    )
    ap.add_argument(
        "--configuration-manifest", default=None,
        help="Required paired bodygroup/LOD configuration identity for extended validation",
    )
    ap.add_argument(
        "--focus-region", default=None,
        help="Render exactly one canonical Maximum region in isolation",
    )
    ap.add_argument(
        "--aggregate-regions", action="store_true",
        help="Research-only whole-state geometry scope for calibration anchors with changed mesh partitioning",
    )
    ap.add_argument(
        "--source-union-contract", default=None,
        help="State-independent adaptive-direct source-union contract JSON",
    )
    ap.add_argument(
        "--source-union-visibility-out", default=None,
        help="Strict source-union component visibility JSON output",
    )
    ap.add_argument(
        "--source-union-control-sha256", default=None,
        help="Parent-owned SHA-256 anchor for the private source-union control bytes",
    )
    return ap.parse_args(argv)


def _validate_source_union_cli_args(args) -> None:
    enabled = getattr(args, "source_union_contract", None)
    visibility = getattr(args, "source_union_visibility_out", None)
    control_hash = getattr(args, "source_union_control_sha256", None)
    if bool(enabled) != bool(visibility):
        raise ValueError("source-union contract and visibility output must be paired")
    if not enabled:
        raise ValueError("source-union mode is not enabled")
    if type(control_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", control_hash) is None:
        raise ValueError("source-union control hash anchor is invalid")
    if any((
        getattr(args, "configuration_manifest", None) is not None,
        getattr(args, "focus_region", None) is not None,
        bool(getattr(args, "aggregate_regions", False)),
    )):
        raise ValueError("source-union mode cannot carry state/focus selectors")
    if args.passes != "textured,clay":
        raise ValueError("source-union pass command is not canonical")
    if args.angles != "front,back,left,right,top,bottom,iso1,iso2":
        raise ValueError("source-union angle command is not canonical")
    tokens = tuple(
        token.strip() for token in str(args.poses or "").split(",")
        if token.strip()
    )
    parsed = []
    for token in tokens:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+):(\d+)", token)
        if match is None:
            raise ValueError("source-union pose command is malformed")
        parsed.append((match.group(1), int(match.group(2))))
    if parsed != [("bind", 0)]:
        raise ValueError("source-union E2A accepts bind:0 only")


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


def _required_source_root(args) -> Path:
    if not args.source_root:
        raise ValueError("extended Maximum validation requires an explicit source root")
    path = Path(args.source_root).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"source root does not exist: {path}")
    return path


def _extended_source_context(
    region_manifest: RegionManifest,
    source_root: Path,
    before: list[Path],
) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    source_root = Path(source_root).resolve(strict=True)
    source_search_paths = _source_cdmaterial_search_paths(region_manifest, source_root)
    manifest_sources = {
        entry.descriptor.source_identity for entry in region_manifest.entries
    }
    source_identities = []
    source_materials: dict[str, tuple[str, ...]] = {}
    for source in before:
        try:
            relative = source.resolve(strict=True).relative_to(source_root)
        except ValueError as exc:
            raise ValueError(f"render source is outside explicit source root: {source}") from exc
        identity = _normalized_source_identity(relative.as_posix())
        if identity not in manifest_sources:
            raise ValueError(f"render source is missing from region manifest: {identity}")
        source_identities.append(identity)
        source_materials[identity] = _smd_material_names(source)
    return tuple(source_identities), source_materials, source_search_paths


def _required_configuration_manifest(args) -> dict:
    if not args.configuration_manifest:
        raise ValueError("extended Maximum validation requires an explicit configuration manifest")
    path = Path(args.configuration_manifest).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read configuration manifest: {path}") from exc
    if type(payload) is not dict or set(payload) != {"schema", "name", "bodygroups", "lod_index", "source_pairs"}:
        raise ValueError("configuration manifest schema fields are invalid")
    if payload["schema"] != 1 or type(payload["name"]) is not str or not payload["name"]:
        raise ValueError("configuration manifest identity is invalid")
    if type(payload["lod_index"]) is not int or payload["lod_index"] < 0:
        raise ValueError("configuration manifest LOD index is invalid")
    bodygroups = payload["bodygroups"]
    if type(bodygroups) is not dict or any(
        type(name) is not str or not name or type(index) is not int or index < 0
        for name, index in bodygroups.items()
    ):
        raise ValueError("configuration manifest bodygroup indices are invalid")
    pairs = payload["source_pairs"]
    if type(pairs) is not list or not pairs or any(
        type(pair) is not dict
        or set(pair) != {"source_identity", "reference_sha256", "candidate_sha256"}
        or type(pair["source_identity"]) is not str
        or not pair["source_identity"]
        or any(
            type(pair[field]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", pair[field]) is None
            for field in ("reference_sha256", "candidate_sha256")
        )
        for pair in pairs
    ):
        raise ValueError("configuration manifest source pair identity is invalid")
    return payload


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _reject_focus_reparse_ancestors(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    for component in (*reversed(absolute.parents), absolute):
        if _path_is_link_or_reparse(component):
            raise ValueError(
                f"focused source ancestor is a link or reparse point: {component}"
            )


def _focus_source_context(
    region_manifest: RegionManifest,
    focus_region: str,
    source_root: Path,
    before: list[Path],
    after: list[Path],
    configuration: dict,
) -> tuple[list[Path], list[Path], str, RegionManifest]:
    selected_manifest = manifest_for_region(region_manifest, focus_region)
    source_identity = selected_manifest.entries[0].descriptor.source_identity
    source_manifest = manifest_for_source(region_manifest, source_identity)
    if len(before) != len(after) or not before:
        raise ValueError("focused validation requires paired before/after sources")
    source_root = Path(source_root).resolve(strict=True)

    pairs_by_source = {}
    for pair in configuration.get("source_pairs", ()):
        if type(pair) is not dict or set(pair) != {
            "source_identity", "reference_sha256", "candidate_sha256"
        }:
            raise ValueError("focused configuration source pair is invalid")
        identity = _normalized_source_identity(pair["source_identity"])
        if identity in pairs_by_source:
            raise ValueError("focused configuration has duplicate source identities")
        pairs_by_source[identity] = pair

    candidates = {}
    for before_path, after_path in zip(before, after):
        before_path = Path(before_path)
        after_path = Path(after_path)
        _reject_focus_reparse_ancestors(before_path)
        _reject_focus_reparse_ancestors(after_path)
        try:
            resolved_before = before_path.resolve(strict=True)
            relative = resolved_before.relative_to(source_root)
            resolved_after = after_path.resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise ValueError("focused source path is outside its trusted root or missing") from exc
        if (
            not resolved_before.is_file() or not resolved_after.is_file()
            or resolved_before.suffix.casefold() != ".smd"
            or resolved_after.suffix.casefold() != ".smd"
        ):
            raise ValueError("focused source paths must be regular SMD files")
        identity = _normalized_source_identity(relative.as_posix())
        if identity in candidates:
            raise ValueError("focused validation requires uniquely paired source identities")
        pair = pairs_by_source.get(identity)
        if pair is None:
            raise ValueError("focused source is absent from the configuration pairs")
        if (
            _file_sha256(resolved_before) != pair["reference_sha256"]
            or _file_sha256(resolved_after) != pair["candidate_sha256"]
        ):
            raise ValueError("focused source hash does not match the configuration pair")
        candidates[identity] = (resolved_before, resolved_after)
    if source_identity not in candidates:
        raise ValueError("focused region source is absent from the paired render inputs")
    selected_before, selected_after = candidates[source_identity]
    return [selected_before], [selected_after], source_identity, source_manifest


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
    return bool(args.aggregate_regions) or any(
        value is not None
        for value in (
            args.passes, args.poses, args.materials_root, args.vtfcmd,
            args.texture_cache, args.region_manifest, args.configuration_manifest,
            getattr(args, "focus_region", None),
        )
    )


def _validated_focus_request(args, poses: tuple[tuple[str, int], ...]) -> str | None:
    focus_region = args.focus_region
    if focus_region is None:
        return None
    if not is_region_key(focus_region):
        raise ValueError("focused region key is invalid")
    if args.aggregate_regions:
        raise ValueError("focused rendering cannot be combined with aggregate regions")
    if len(poses) > 2 or not poses or poses[0][0] != "bind":
        raise ValueError("focused rendering poses must be bind plus at most one representative pose")
    return focus_region


def _validate_focus_render_payload(
    entries,
    snapshots,
    passes: tuple[str, ...],
    angles: tuple[str, ...],
    poses: tuple[tuple[str, int], ...],
    focus_region: str,
) -> None:
    pose_names = tuple(name for name, _frame in poses)
    expected = {
        (render_pass, pose, angle)
        for render_pass in passes for pose in pose_names for angle in angles
    }
    observed = []
    for entry in entries:
        if type(entry) is not dict:
            raise ValueError("focused render entry is invalid")
        observed.append((entry.get("pass"), entry.get("pose"), entry.get("angle")))
    if len(observed) != len(expected) or set(observed) != expected:
        raise ValueError("focused render image matrix is incomplete or duplicated")
    if tuple(snapshots) != pose_names or any(
        set(snapshots[pose]) != {focus_region} for pose in pose_names
    ):
        raise ValueError("focused geometry matrix must contain exactly one region per pose")


def _validate_focus_output_inventory(root: Path, entries) -> None:
    root = Path(root)
    _reject_focus_reparse_ancestors(root)
    expected = set()
    for entry in entries:
        image = entry.get("image") if type(entry) is dict else None
        if type(image) is not str or not image:
            raise ValueError("focused render entry image path is invalid")
        relative = PurePosixPath(image.replace("\\", "/"))
        windows = PureWindowsPath(image)
        if relative.is_absolute() or windows.is_absolute() or windows.drive or ".." in relative.parts:
            raise ValueError("focused render entry image path escapes its root")
        canonical = PurePosixPath(*(part for part in relative.parts if part not in ("", "."))).as_posix()
        if canonical != image or canonical in expected:
            raise ValueError("focused render entry image path is non-canonical or duplicated")
        expected.add(canonical)
    actual = set()
    if root.exists():
        for path in root.rglob("*"):
            if _path_is_link_or_reparse(path):
                raise ValueError("focused output contains a link or reparse point")
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
    if actual != expected:
        raise ValueError("focused output contains extra or missing image files")


def _texture_cache_root(args, out_dir: Path) -> Path:
    return (
        Path(args.texture_cache).expanduser().resolve()
        if args.texture_cache
        else (Path(out_dir) / ".vtf-cache").resolve()
    )


_SUPPORTED_VMT_SHADERS = frozenset({
    "vertexlitgeneric",
    "lightmappedgeneric",
    "unlitgeneric",
    "refract",
})


def _vmt_tokens(vmt_text: str) -> tuple[str, ...] | None:
    tokens: list[str] = []
    index = 0
    while index < len(vmt_text):
        char = vmt_text[index]
        if char.isspace():
            index += 1
            continue
        if char == "/" and index + 1 < len(vmt_text) and vmt_text[index + 1] == "/":
            newline = vmt_text.find("\n", index + 2)
            index = len(vmt_text) if newline < 0 else newline + 1
            continue
        if char in "{}":
            tokens.append(char)
            index += 1
            continue
        if char == '"':
            index += 1
            value: list[str] = []
            while index < len(vmt_text) and vmt_text[index] != '"':
                value.append(vmt_text[index])
                index += 1
            if index >= len(vmt_text):
                return None
            tokens.append("".join(value))
            index += 1
            continue
        end = index
        while (
            end < len(vmt_text)
            and not vmt_text[end].isspace()
            and vmt_text[end] not in '{}"'
            and not (vmt_text[end] == "/" and end + 1 < len(vmt_text) and vmt_text[end + 1] == "/")
        ):
            end += 1
        if end == index:
            return None
        tokens.append(vmt_text[index:end])
        index = end
    return tuple(tokens)


def _parse_vmt_root(
    vmt_text: str,
) -> tuple[str, dict[str, str], dict[str, tuple[str, ...]]] | None:
    tokens = _vmt_tokens(vmt_text)
    if tokens is None or len(tokens) < 3 or tokens[1] != "{":
        return None
    shader = tokens[0].casefold()
    if shader not in _SUPPORTED_VMT_SHADERS:
        return None
    directives: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}
    depth = 1
    index = 2
    while index < len(tokens):
        token = tokens[index]
        if token == "{":
            depth += 1
            index += 1
            continue
        if token == "}":
            depth -= 1
            index += 1
            if depth == 0:
                return (
                    shader,
                    directives,
                    {key: tuple(values) for key, values in duplicates.items()},
                ) if index == len(tokens) else None
            if depth < 0:
                return None
            continue
        if depth == 1:
            if index + 1 >= len(tokens) or tokens[index + 1] == "}":
                return None
            if tokens[index + 1] == "{":
                index += 1
                continue
            key = token.casefold()
            if key in directives:
                duplicates.setdefault(key, []).append(tokens[index + 1])
            else:
                directives[key] = tokens[index + 1]
            index += 2
            continue
        index += 1
    return None


def _extract_base_texture(vmt_text: str) -> str | None:
    parsed = _parse_vmt_root(vmt_text)
    if parsed is None:
        return None
    value = parsed[1].get("$basetexture", "").replace("\\", "/").strip()
    return value or None


def _source_texture_reference(
    vmt_text: str,
) -> tuple[str, str, str, bool] | None:
    parsed = _parse_vmt_root(vmt_text)
    if parsed is None:
        return None
    shader, directives, _duplicates = parsed
    directive = "$refracttinttexture" if shader == "refract" else "$basetexture"
    raw_texture = directives.get(directive)
    if raw_texture is None:
        return None
    texture = raw_texture.replace("\\", "/").strip()
    if not texture:
        return None
    return shader, directive, texture, shader == "refract" or _root_uses_texture_alpha(directives)


def _root_uses_texture_alpha(directives: dict[str, str]) -> bool:
    return any(
        directives.get(key, "").casefold() in {"1", "true"}
        for key in ("$translucent", "$alphatest")
    )


def _vmt_uses_texture_alpha(vmt_text: str) -> bool:
    parsed = _parse_vmt_root(vmt_text)
    return False if parsed is None else _root_uses_texture_alpha(parsed[1])


def _render_entry(
    root: Path,
    render_pass: str,
    pose: str,
    angle: str,
    image_path: Path,
    *,
    texture_missing: bool,
    missing_materials: tuple[str, ...] = (),
    resolved_materials: tuple[dict, ...] = (),
) -> dict:
    return {
        "pass": render_pass,
        "pose": pose,
        "angle": angle,
        "image": image_path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "texture_missing": bool(texture_missing),
        "missing_materials": list(missing_materials),
        "resolved_materials": list(resolved_materials),
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
    configuration: dict | None = None,
    geometry_audit: dict | None = None,
) -> Path:
    manifest = {
        "schema": 1,
        "expected": expected,
        "entries": entries,
        "geometry": geometry,
        "bbox": bbox,
        "sampling": {"stride": stride, "seed": seed},
    }
    if configuration is not None:
        manifest["configuration"] = configuration
    if geometry_audit is not None:
        manifest["geometry_audit"] = geometry_audit
        manifest["geometry_audit_algorithm"] = GEOMETRY_AUDIT_ALGORITHM
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


def _path_is_link_or_reparse(path: Path) -> bool:
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    return bool(
        getattr(info, "st_file_attributes", 0) & 0x400
        or stat.S_ISLNK(getattr(info, "st_mode", 0))
    )


def _reject_reparse_ancestors(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    for component in (*reversed(absolute.parents), absolute):
        if _path_is_link_or_reparse(component):
            raise RuntimeError(
                f"texture cache ancestor is a link/reparse point: {component}"
            )


def _safe_contained_directory(path: Path, *, parent: Path | None = None) -> Path:
    path = Path(path)
    _reject_reparse_ancestors(path)
    path.mkdir(parents=True, exist_ok=True)
    _reject_reparse_ancestors(path)
    if not path.is_dir():
        raise RuntimeError(f"texture cache directory is unsafe: {path}")
    resolved = path.resolve(strict=True)
    if parent is not None:
        parent_resolved = Path(parent).resolve(strict=True)
        try:
            resolved.relative_to(parent_resolved)
        except ValueError as exc:
            raise RuntimeError(f"texture cache directory escapes parent: {path}") from exc
    return resolved


def _valid_regular_cache_file(path: Path) -> bool:
    path = Path(path)
    _reject_reparse_ancestors(path.parent)
    if _path_is_link_or_reparse(path):
        raise RuntimeError(f"texture cache output is a link/reparse point: {path}")
    try:
        info = path.stat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise RuntimeError(f"texture cache output is not a non-empty regular file: {path}")
    return True


def _publish_cache_file(staged_output: Path, output_path: Path) -> Path:
    for attempt in range(20):
        if _valid_regular_cache_file(output_path):
            return output_path
        try:
            os.replace(staged_output, output_path)
        except (FileExistsError, PermissionError):
            if _valid_regular_cache_file(output_path):
                return output_path
            if attempt == 19:
                raise
            time.sleep(0.01)
            continue
        if _valid_regular_cache_file(output_path):
            return output_path
        raise RuntimeError(f"texture cache publish did not create a valid file: {output_path}")
    raise RuntimeError(f"texture cache publish exhausted retries: {output_path}")


def _convert_vtf(vtf_path: Path, vtfcmd: Path | None, cache_root: Path) -> Path | None:
    if vtfcmd is None or not vtfcmd.is_file() or not vtf_path.is_file():
        return None
    source_digest = hashlib.sha256(vtf_path.read_bytes()).hexdigest()
    tool_digest = hashlib.sha256(vtfcmd.read_bytes()).hexdigest()
    digest = hashlib.sha256(f"{tool_digest}:{source_digest}".encode("ascii")).hexdigest()
    cache_root = _safe_contained_directory(cache_root)
    output_dir = _safe_contained_directory(cache_root / digest, parent=cache_root)
    output_path = output_dir / "texture.png"
    if _valid_regular_cache_file(output_path):
        return output_path
    attempts = []
    for _attempt in range(2):
        staging_parent = _safe_contained_directory(
            Path(tempfile.gettempdir()).resolve() / "maximum-vtf-stage"
        )
        staging_dir = _safe_contained_directory(
            staging_parent / uuid.uuid4().hex,
            parent=staging_parent,
        )
        staged_input = staging_dir / "texture.vtf"
        staged_output = staging_dir / "texture.png"
        try:
            shutil.copy2(vtf_path, staged_input)
            command = [
                str(vtfcmd),
                "-file",
                str(staged_input),
                "-output",
                str(staging_dir),
                "-exportformat",
                "png",
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode == 0 and _valid_regular_cache_file(staged_output):
                return _publish_cache_file(staged_output, output_path)
            attempts.append(
                f"rc={result.returncode}; stdout={(result.stdout or '')[-500:]!r}; "
                f"stderr={(result.stderr or '')[-500:]!r}"
            )
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
    raise RuntimeError(
        f"VTF texture conversion failed for {vtf_path}: " + " | ".join(attempts)
    )


def _source_texture_png(
    material_name: str,
    materials_root,
    vtfcmd: Path | None,
    cache_root: Path,
    *,
    search_paths: tuple[str, ...] = (),
) -> Path | None:
    resolved = _source_material_files(
        material_name, materials_root, search_paths=search_paths
    )
    if resolved is None:
        return None
    return _convert_vtf(resolved["vtf_path"], vtfcmd, cache_root)


def _material_roots(materials_root) -> tuple[Path, ...]:
    if materials_root is None:
        return ()
    values = materials_root if isinstance(materials_root, (tuple, list)) else (materials_root,)
    return tuple(Path(value) for value in values if Path(value).is_dir())


def _source_material_files(
    material_name: str,
    materials_root,
    *,
    search_paths: tuple[str, ...] = (),
) -> dict | None:
    roots = _material_roots(materials_root)
    if not roots:
        return None
    relative = material_name.replace("\\", "/").strip()
    if relative.casefold().endswith(".vmt"):
        relative = relative[:-4]
    normalized = PurePosixPath(relative)
    windows = PureWindowsPath(material_name)
    if (
        not relative
        or normalized.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in normalized.parts
    ):
        return None
    candidates = (
        tuple(PurePosixPath(path) / normalized for path in search_paths)
        if search_paths and len(normalized.parts) == 1
        else (normalized,)
    )
    vmt_path = None
    vmt_root_index = -1
    search_path_index = -1
    for root_index, root in enumerate(roots):
        for candidate_index, candidate in enumerate(candidates):
            match = _contained_material_path(root, candidate.as_posix(), ".vmt")
            if match is not None and match.is_file():
                vmt_path = match
                vmt_root_index = root_index
                search_path_index = candidate_index
                break
        if vmt_path is not None:
            break
    if vmt_path is None:
        return None
    try:
        vmt_text = vmt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    texture_reference = _source_texture_reference(vmt_text)
    parsed_vmt = _parse_vmt_root(vmt_text)
    if texture_reference is None or parsed_vmt is None:
        return None
    shader, texture_directive, base_texture, uses_texture_alpha = texture_reference
    duplicate_root_directives = [
        {"directive": directive, "ignored_values": list(values)}
        for directive, values in sorted(parsed_vmt[2].items(), key=lambda item: item[0])
    ]
    vtf_path = None
    vtf_root_index = -1
    for root_index, root in enumerate(roots):
        match = _contained_material_path(root, base_texture, ".vtf")
        if match is not None and match.is_file():
            vtf_path = match
            vtf_root_index = root_index
            break
    if vtf_path is None:
        return None
    return {
        "vmt_path": vmt_path,
        "vmt_root_index": vmt_root_index,
        "search_path_index": search_path_index,
        "vtf_path": vtf_path,
        "vtf_root_index": vtf_root_index,
        "shader": shader,
        "texture_directive": texture_directive,
        "uses_texture_alpha": uses_texture_alpha,
        "duplicate_root_directives": duplicate_root_directives,
    }


def _source_material_evidence(
    material_name: str,
    materials_root,
    *,
    search_paths: tuple[str, ...] = (),
) -> dict | None:
    resolved = _source_material_files(
        material_name, materials_root, search_paths=search_paths
    )
    if resolved is None:
        return None
    vmt_path = resolved["vmt_path"]
    vtf_path = resolved["vtf_path"]
    return {
        "resolution_rule": "materials-root-order-then-qc-search-order-v1",
        "root_index": resolved["vmt_root_index"],
        "search_path_index": resolved["search_path_index"],
        "vtf_root_index": resolved["vtf_root_index"],
        "vmt_sha256": hashlib.sha256(vmt_path.read_bytes()).hexdigest(),
        "vtf_sha256": hashlib.sha256(vtf_path.read_bytes()).hexdigest(),
        "shader": resolved["shader"],
        "texture_directive": resolved["texture_directive"],
        "uses_texture_alpha": resolved["uses_texture_alpha"],
        "duplicate_root_directives": resolved["duplicate_root_directives"],
    }


def _source_uses_texture_alpha(
    material_name: str,
    materials_root: Path | None,
    *,
    search_paths: tuple[str, ...] = (),
) -> bool:
    evidence = _source_material_evidence(
        material_name, materials_root, search_paths=search_paths
    )
    return bool(evidence and evidence["uses_texture_alpha"])


def _make_textured_material(name: str, png_path: Path, *, use_texture_alpha: bool):
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
        if use_texture_alpha:
            links.new(texture.outputs["Alpha"], bsdf.inputs["Alpha"])
        else:
            bsdf.inputs["Alpha"].default_value = 1.0
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
    *,
    source_search_paths: dict[str, tuple[str, ...]] | None = None,
) -> dict:
    missing_materials: set[str] = set()
    resolved_materials: dict[str, dict] = {}
    material_cache = {}
    source_search_paths = source_search_paths or {}
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
            source_identity = obj.get("maximum_region_source_identity", "")
            search_paths = source_search_paths.get(source_identity, ())
            png_path = _source_texture_png(
                name,
                materials_root,
                vtfcmd,
                cache_root,
                search_paths=search_paths,
            )
            if png_path is None:
                missing_materials.add(
                    f"{source_identity}:slot:{index}:{name or '<empty>'}"
                )
                obj.data.materials.append(_make_missing_texture_material(f"{obj.name}_{index}"))
                continue
            use_texture_alpha = _source_uses_texture_alpha(
                name,
                materials_root,
                search_paths=search_paths,
            )
            material_identity = f"{source_identity}:slot:{index}:{name or '<empty>'}"
            evidence = _source_material_evidence(
                name, materials_root, search_paths=search_paths
            )
            if evidence is None:
                raise ValueError(f"resolved texture has no immutable source evidence: {material_identity}")
            resolved_materials[material_identity] = {
                "material_identity": material_identity,
                **evidence,
            }
            cache_key = (str(png_path), use_texture_alpha)
            material = material_cache.get(cache_key)
            if material is None:
                material = _make_textured_material(
                    name,
                    png_path,
                    use_texture_alpha=use_texture_alpha,
                )
                material_cache[cache_key] = material
            obj.data.materials.append(material)
    return {
        "missing": tuple(sorted(missing_materials, key=str.casefold)),
        "resolved": tuple(
            resolved_materials[key]
            for key in sorted(resolved_materials, key=str.casefold)
        ),
    }


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


def _focus_mesh_objects(
    objs,
    source_manifest: RegionManifest,
    source_material_evidence: dict[str, tuple[str, ...]],
    focus_region: str,
):
    manifest_for_region(source_manifest, focus_region)
    ordered = tuple(sorted(
        objs, key=lambda item: (_region_source_identity(item), item.name.casefold(), item.name)
    ))
    observations = []
    for obj in ordered:
        source_identity = _region_source_identity(obj)
        materials = source_material_evidence.get(source_identity)
        if materials is None:
            raise ValueError("focused mesh source has no canonical material evidence")
        observations.append((
            source_identity,
            obj.name,
            _source_material_slot_identities(
                source_identity,
                materials,
                tuple(
                    material.name if material else "none"
                    for material in getattr(obj.data, "materials", ())
                ),
            ),
        ))
    assignments = _resolve_region_assignments(
        source_manifest, tuple(observations), require_complete=True
    )
    selected = tuple(
        obj for obj, observation in zip(ordered, observations)
        if assignments[observation] == focus_region
    )
    if len(selected) != 1:
        raise ValueError("focused region must resolve to exactly one mesh object")
    selected_ids = {id(obj) for obj in selected}
    for obj in ordered:
        visible = id(obj) in selected_ids
        obj.hide_render = not visible
        if hasattr(obj, "hide_set"):
            obj.hide_set(not visible)
    return selected


def _capture_regions(
    objs,
    frame: int,
    region_manifest: RegionManifest,
    source_material_evidence: dict[str, tuple[str, ...]],
    *,
    aggregate: bool = False,
) -> dict[str, dict]:
    bpy.context.scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    if aggregate:
        captured = []
        for obj in sorted(
            objs, key=lambda item: (_region_source_identity(item), item.name.casefold(), item.name)
        ):
            region = _capture_object_region(obj, "aggregate", depsgraph, audited=False)
            if region is not None:
                captured.append(region)
        return {"aggregate": _aggregate_triangle_regions(captured)}
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
        region = _capture_object_region(obj, region_key, depsgraph)
        if region is not None:
            regions[region_key] = region
    return regions


def _capture_object_region(
    obj, region_key: str, depsgraph, *, audited: bool = True
) -> dict | None:
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    if mesh is None:
        return None
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
            triangles.append({
                "positions": tuple(positions),
                "normals": tuple(normals),
                "uvs": tuple(uvs),
            })
        region = {
            "scope": region_key,
            "source_object": obj.name,
            "triangles": triangles,
        }
        return _audited_nondegenerate_region(region) if audited else region
    finally:
        evaluated.to_mesh_clear()


def _aggregate_triangle_regions(regions) -> dict:
    return _audited_nondegenerate_region({
        "scope": "aggregate",
        "source_object": "aggregate",
        "triangles": [
            triangle for region in regions for triangle in region.get("triangles", ())
        ],
    })


def _apply_animation_source(path: Path, poses: tuple[tuple[str, int], ...]):
    armatures = tuple(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    if len(armatures) != 1:
        raise RuntimeError(
            f"representative-animation-unavailable: expected one armature, found {len(armatures)}"
        )
    armature = armatures[0]
    for obj in bpy.context.scene.objects:
        obj.select_set(False)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    _import_source(path)
    armatures_after = tuple(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    if len(armatures_after) != 1 or armatures_after[0] is not armature:
        raise RuntimeError("representative-animation-unavailable: animation import is ambiguous")
    action = getattr(getattr(armature, "animation_data", None), "action", None)
    if action is None:
        raise RuntimeError("representative-animation-unavailable: no action was assigned")
    start, end = (float(value) for value in action.frame_range)
    if any(frame < start or frame > end for name, frame in poses if name != "bind"):
        raise RuntimeError("representative-animation-unavailable: pose is outside action frame range")
    return armature, action


def _set_pose_state(animation_binding, pose_name: str, frame: int, *, scene=None) -> None:
    scene = scene or bpy.context.scene
    if animation_binding is not None:
        armature, action = animation_binding
        armature.animation_data.action = None if pose_name == "bind" else action
    scene.frame_set(frame)


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


def _audited_nondegenerate_region(region: dict) -> dict:
    kept = []
    filtered_indices = []
    for index, triangle in enumerate(region.get("triangles", ())):
        first, second, third = (
            tuple(float(component) for component in point)
            for point in triangle["positions"]
        )
        first_edge = tuple(second[axis] - first[axis] for axis in range(3))
        second_edge = tuple(third[axis] - first[axis] for axis in range(3))
        cross = (
            first_edge[1] * second_edge[2] - first_edge[2] * second_edge[1],
            first_edge[2] * second_edge[0] - first_edge[0] * second_edge[2],
            first_edge[0] * second_edge[1] - first_edge[1] * second_edge[0],
        )
        area_squared = sum(value * value for value in cross)
        edge_squared = max(
            sum((second[axis] - first[axis]) ** 2 for axis in range(3)),
            sum((third[axis] - first[axis]) ** 2 for axis in range(3)),
            sum((third[axis] - second[axis]) ** 2 for axis in range(3)),
        )
        tolerance = (
            edge_squared * edge_squared
        ) * GEOMETRY_AUDIT_ALGORITHM["relative_area_squared_epsilon"]
        if not math.isfinite(area_squared) or area_squared <= tolerance:
            filtered_indices.append(index)
        else:
            kept.append(triangle)
    encoded = ",".join(str(index) for index in filtered_indices).encode("ascii")
    result = dict(region)
    result["triangles"] = kept
    result["triangle_audit"] = {
        "input_triangles": len(region.get("triangles", ())),
        "kept_triangles": len(kept),
        "filtered_degenerate_triangles": len(filtered_indices),
        "filtered_indices_sha256": hashlib.sha256(encoded).hexdigest(),
    }
    return result


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


def _geometry_audit_payload(snapshots: dict[str, dict[str, dict]]) -> dict:
    payload = {}
    for pose in sorted(snapshots):
        for region in sorted(snapshots[pose]):
            audit = snapshots[pose][region].get("triangle_audit")
            if not isinstance(audit, dict):
                raise ValueError(f"missing deterministic triangle audit: {region}/{pose}")
            payload[f"{region}/{pose}"] = dict(audit)
    return payload


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
    animation_binding=None,
) -> dict[str, dict[str, dict]]:
    scene = scene or bpy.context.scene
    capture = capture or _capture_regions
    original_frame = scene.frame_current
    try:
        snapshots = {}
        for pose_name, frame in poses:
            if animation_binding is not None:
                _set_pose_state(animation_binding, pose_name, frame, scene=scene)
            snapshots[pose_name] = capture(objs, frame)
        return snapshots
    finally:
        if animation_binding is not None:
            animation_binding[0].animation_data.action = animation_binding[1]
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


def _source_union_build_objects(groups, material_contract):
    if bpy is None:
        raise RuntimeError("source-union mesh construction requires Blender")
    material_indices = {
        binding.smd_material.casefold(): index
        for index, binding in enumerate(material_contract.bindings)
    }
    objects = []
    for component_key, triangles in groups:
        vertices = []
        faces = []
        loop_uvs = []
        loop_normals = []
        face_materials = []
        for triangle in triangles:
            material_index = material_indices.get(triangle.material.casefold())
            if material_index is None:
                raise ValueError(
                    f"source-union SMD material is not contract-bound: {triangle.material}"
                )
            face = []
            for corner in triangle.corners:
                face.append(len(vertices))
                vertices.append(tuple(float(value) for value in corner.position))
                loop_normals.append(tuple(float(value) for value in corner.normal))
                loop_uvs.append((float(corner.uv[0]), 1.0 - float(corner.uv[1])))
            faces.append(tuple(face))
            face_materials.append(material_index)
        mesh = bpy.data.meshes.new(f"{component_key}-mesh")
        mesh.from_pydata(vertices, (), faces)
        mesh.update(calc_edges=False)
        if len(mesh.polygons) != len(faces) or len(mesh.loops) != len(loop_uvs):
            raise ValueError("source-union Blender mesh cardinality differs")
        uv_layer = mesh.uv_layers.new(name="UVMap")
        for index, uv in enumerate(loop_uvs):
            uv_layer.data[index].uv = uv
        for polygon, material_index in zip(mesh.polygons, face_materials):
            polygon.material_index = material_index
            polygon.use_smooth = True
        if not hasattr(mesh, "normals_split_custom_set"):
            raise ValueError("source-union Blender custom normals are unavailable")
        mesh.normals_split_custom_set(loop_normals)
        obj = bpy.data.objects.new(component_key, mesh)
        bpy.context.collection.objects.link(obj)
        if obj.name != component_key:
            raise ValueError("source-union component object name is not exact")
        objects.append(obj)
    if tuple(obj.name for obj in objects) != tuple(key for key, _ in groups):
        raise ValueError("source-union component object order differs")
    return tuple(objects)


def _source_union_apply_textured_materials(
    objects, control: _SourceUnionControl, material_roots, vtfcmd, texture_cache,
) -> None:
    materials = []
    for binding in control.material_contract.bindings:
        png_path = _source_texture_png(
            binding.smd_material,
            material_roots,
            vtfcmd,
            texture_cache,
            search_paths=binding.search_paths,
        )
        if png_path is None:
            raise ValueError(
                f"source-union contracted texture conversion failed: {binding.smd_material}"
            )
        material = _make_textured_material(
            binding.smd_material,
            png_path,
            use_texture_alpha=binding.uses_texture_alpha,
        )
        _require_source_union_dithered_alpha(
            material, binding.uses_texture_alpha,
        )
        materials.append(material)
    for obj in objects:
        obj.data.materials.clear()
        for material in materials:
            obj.data.materials.append(material)
        if len(obj.data.materials) != len(materials):
            raise ValueError("source-union Blender material slots differ")


def _source_union_apply_clay(objects) -> None:
    clay = _make_clay_material()
    for obj in objects:
        obj.data.materials.clear()
        obj.data.materials.append(clay)
        for polygon in obj.data.polygons:
            polygon.material_index = 0


def _source_union_capture(objects, union_key: str) -> dict[str, dict[str, dict]]:
    bpy.context.scene.frame_set(0)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    captured = []
    for obj in objects:
        region = _capture_object_region(obj, union_key, depsgraph, audited=False)
        if region is not None:
            captured.append(region)
    aggregate = _aggregate_triangle_regions(captured)
    aggregate["scope"] = union_key
    aggregate["source_object"] = union_key
    return {"bind": {union_key: aggregate}}


def _source_union_setup_cryptomatte(directory: Path):
    scene = bpy.context.scene
    view_layer = scene.view_layers[0]
    view_layer.use_pass_cryptomatte_object = True
    view_layer.pass_cryptomatte_depth = 16
    if hasattr(view_layer, "use_pass_cryptomatte_accurate"):
        view_layer.use_pass_cryptomatte_accurate = True
    tree = bpy.data.node_groups.new(
        f"MaximumSourceUnionCompositor-{uuid.uuid4().hex}", "CompositorNodeTree"
    )
    scene.compositing_node_group = tree
    layers = tree.nodes.new("CompositorNodeRLayers")
    output = tree.nodes.new("CompositorNodeOutputFile")
    output.directory = str(directory)
    output.file_name = "source-union-crypto"
    output.format.file_format = "OPEN_EXR_MULTILAYER"
    output.format.color_depth = "32"
    for index in range(8):
        name = f"CryptoObject{index:02d}"
        if name not in layers.outputs:
            raise ValueError(f"source-union Cryptomatte layer is unavailable: {name}")
        output.file_output_items.new("RGBA", name)
        tree.links.new(layers.outputs[name], output.inputs[name])
    return output


def _source_union_crypto_exr(directory: Path) -> Path:
    values = tuple(sorted(directory.glob("*.exr")))
    if len(values) != 1 or not values[0].is_file():
        raise ValueError("source-union Cryptomatte EXR output is not exact")
    return values[0]


def _source_union_read_cryptomatte(path: Path, component_keys: tuple[str, ...]):
    try:
        import OpenImageIO as oiio
    except ModuleNotFoundError as exc:
        raise ValueError("source-union OpenImageIO is unavailable") from exc
    image = oiio.ImageInput.open(str(path))
    if image is None:
        raise ValueError("source-union Cryptomatte EXR cannot be opened")
    manifest = None
    layers = {}
    width = height = None
    try:
        subimage = 0
        while image.seek_subimage(subimage, 0):
            spec = image.spec()
            if width is None:
                width, height = int(spec.width), int(spec.height)
            elif (int(spec.width), int(spec.height)) != (width, height):
                raise ValueError("source-union Cryptomatte dimensions differ")
            attributes = {
                item.name: item.value for item in spec.extra_attribs
            }
            for key, value in attributes.items():
                if key.startswith("cryptomatte/") and key.endswith("/manifest"):
                    parsed = json.loads(value)
                    if manifest is not None and parsed != manifest:
                        raise ValueError("source-union Cryptomatte manifests differ")
                    manifest = parsed
            names = tuple(str(value) for value in spec.channelnames)
            subimage_name = str(attributes.get("oiio:subimagename", ""))
            match = re.search(r"CryptoObject([0-7][0-9]?)", subimage_name)
            if match is None:
                match = next((
                    re.search(r"CryptoObject([0-7][0-9]?)", name)
                    for name in names
                    if re.search(r"CryptoObject([0-7][0-9]?)", name)
                ), None)
            if match is not None:
                layer_index = int(match.group(1))
                if layer_index not in range(8) or int(spec.nchannels) != 4:
                    raise ValueError("source-union Cryptomatte layer schema differs")
                values = image.read_image(subimage, 0, 0, 4, oiio.FLOAT)
                flat = tuple(float(value) for value in values.flat)
                if len(flat) != width * height * 4:
                    raise ValueError("source-union Cryptomatte pixel count differs")
                if layer_index in layers:
                    raise ValueError("source-union Cryptomatte layer is duplicated")
                layers[layer_index] = flat
            subimage += 1
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("source-union Cryptomatte EXR is invalid") from exc
    finally:
        image.close()
    if manifest is None or set(layers) != set(range(8)) or width is None or height is None:
        raise ValueError("source-union Cryptomatte EXR coverage is incomplete")
    pixels = []
    for pixel in range(width * height):
        pairs = []
        for layer_index in range(8):
            offset = pixel * 4
            values = layers[layer_index]
            pairs.extend(((values[offset], values[offset + 1]), (
                values[offset + 2], values[offset + 3],
            )))
        pixels.append(tuple(pairs))
    return _count_cryptomatte_components(pixels, manifest, component_keys)


def _source_union_manifest_payload(
    control: _SourceUnionControl,
    side: str,
    entries: list[dict],
    geometry: list[dict],
    geometry_audit: dict,
) -> dict[str, object]:
    comparison = control.comparison
    if side not in {"reference", "candidate"}:
        raise ValueError("source-union manifest side is invalid")
    return {
        "schema": 1,
        "kind": "adaptive-direct-source-union-render-v1",
        "side": side,
        "contract_sha256": comparison.contract_sha256,
        "target_sha256": comparison.target_sha256,
        "source_identity": comparison.source_identity,
        "source_coverage_sha256": comparison.source_coverage_sha256,
        "source_sha256": (
            comparison.reference_source_sha256
            if side == "reference" else comparison.candidate_source_sha256
        ),
        "material_contract_sha256": comparison.material_contract_sha256,
        "expected": {
            "passes": ["textured", "clay"],
            "angles": list(ANGLE_DIRS),
            "poses": ["bind"],
            "pose_frames": {"bind": 0},
            "regions": [comparison.union_key],
        },
        "entries": entries,
        "geometry": geometry,
        "geometry_audit": geometry_audit,
        "geometry_audit_algorithm": GEOMETRY_AUDIT_ALGORITHM,
    }


def _source_union_write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _canonicalize_source_union_png(path: Path) -> None:
    path = Path(path)
    payload = path.read_bytes()
    signature = b"\x89PNG\r\n\x1a\n"
    if not payload.startswith(signature) or len(payload) > 512 * 1024 * 1024:
        raise ValueError("source-union PNG signature/size is invalid")
    offset = len(signature)
    chunks = []
    names = []
    while offset < len(payload):
        if len(payload) - offset < 12:
            raise ValueError("source-union PNG chunk is truncated")
        size = struct.unpack(">I", payload[offset:offset + 4])[0]
        end = offset + 12 + size
        if size > 512 * 1024 * 1024 or end > len(payload):
            raise ValueError("source-union PNG chunk size is invalid")
        kind = payload[offset + 4:offset + 8]
        data = payload[offset + 8:offset + 8 + size]
        expected_crc = struct.unpack(">I", payload[offset + 8 + size:end])[0]
        if (
            len(kind) != 4
            or any(not (65 <= value <= 90 or 97 <= value <= 122) for value in kind)
            or zlib.crc32(kind + data) != expected_crc
        ):
            raise ValueError("source-union PNG chunk CRC/type is invalid")
        names.append(kind)
        volatile = (
            kind == b"tEXt" and data.partition(b"\0")[0]
            in {b"Date", b"RenderTime"}
        )
        if not volatile:
            chunks.append(payload[offset:end])
        offset = end
        if kind == b"IEND":
            break
    if (
        offset != len(payload)
        or not names or names[0] != b"IHDR"
        or names.count(b"IHDR") != 1
        or names.count(b"IEND") != 1
        or names[-1] != b"IEND"
        or b"IDAT" not in names
    ):
        raise ValueError("source-union PNG chunk inventory is invalid")
    canonical = signature + b"".join(chunks)
    if canonical != payload:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(canonical)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _render_source_union_side(
    *,
    logical_side: str,
    groups,
    control: _SourceUnionControl,
    out_dir: Path,
    size: int,
    material_roots,
    vtfcmd,
    texture_cache: Path,
    fit=None,
):
    _clear_scene()
    scene = _setup_scene(size, transparent=True)
    scene.render.resolution_percentage = 100
    objects = _source_union_build_objects(groups, control.material_contract)
    snapshots = _source_union_capture(objects, control.comparison.union_key)
    bbox, evaluated_fit = _framing_from_snapshots(snapshots)
    center, ortho_scale, distance = fit or evaluated_fit
    camera = _ensure_camera()
    camera.data.ortho_scale = ortho_scale
    _setup_lights(center, ortho_scale)
    _source_union_apply_textured_materials(
        objects, control, material_roots, vtfcmd, texture_cache,
    )
    out_dir.mkdir(parents=True, exist_ok=False)
    entries = []
    counts = {}
    render_calls = 0
    with tempfile.TemporaryDirectory(prefix="maximum-source-union-crypto-") as temporary:
        crypto_dir = Path(temporary)
        crypto_output = _source_union_setup_cryptomatte(crypto_dir)
        for render_pass in ("textured", "clay"):
            if render_pass == "clay":
                _source_union_apply_clay(objects)
                crypto_output.mute = True
            else:
                crypto_output.mute = False
            for camera_index, angle in enumerate(ANGLE_DIRS):
                for stale in crypto_dir.glob("*.exr"):
                    stale.unlink()
                _set_camera_pose(camera, center, ANGLE_DIRS[angle], distance)
                image_path = out_dir / render_pass / "bind" / f"{angle}.png"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                scene.render.filepath = str(image_path)
                bpy.ops.render.render(write_still=True)
                render_calls += 1
                if not image_path.is_file():
                    raise ValueError("source-union PNG output is missing")
                _canonicalize_source_union_png(image_path)
                if render_pass == "textured":
                    exr = _source_union_crypto_exr(crypto_dir)
                    try:
                        visible = _source_union_read_cryptomatte(
                            exr, control.component_keys,
                        )
                    finally:
                        if exr.exists():
                            exr.unlink()
                    for component, pixels in visible.items():
                        counts[(logical_side, component, f"camera-{camera_index:02d}")] = pixels
                elif tuple(crypto_dir.glob("*.exr")):
                    raise ValueError("source-union clay render emitted auxiliary EXR")
                entries.append(_render_entry(
                    out_dir, render_pass, "bind", angle, image_path,
                    texture_missing=False,
                    missing_materials=(),
                    resolved_materials=(
                        control.material_evidence if render_pass == "textured" else ()
                    ),
                ))
    if render_calls != 16 or len(entries) != 16 or len(counts) != len(control.component_keys) * 8:
        raise ValueError("source-union render matrix is incomplete")
    return entries, snapshots, (center, ortho_scale, distance), bbox, counts, render_calls


def _run_source_union(args, before: list[Path], after: list[Path], out_dir: Path) -> None:
    if bpy is None:
        raise SystemExit("[ERROR] render_previews.py must be executed by Blender.")
    if len(before) != 1 or len(after) != 1:
        raise ValueError("source-union renderer requires one reference/candidate SMD pair")
    reference_path = before[0].resolve(strict=True)
    candidate_path = after[0].resolve(strict=True)
    contract_path = Path(args.source_union_contract).resolve(strict=True)
    visibility_path = Path(args.source_union_visibility_out).resolve()
    out_dir = out_dir.resolve()
    workspace = contract_path.parent.parent
    inputs = workspace / "inputs"
    material_tree = workspace / "material-roots"
    if any((
        reference_path != inputs / "reference.smd",
        candidate_path != inputs / "candidate.smd",
        out_dir != workspace / "raw",
        contract_path != workspace / "control" / "source-union-contract.json",
        visibility_path != workspace / "control" / "source-union-visibility.json",
        Path(__file__).resolve() != inputs / "render_previews.py",
    )):
        raise ValueError("source-union renderer private workspace paths differ")
    material_roots = tuple(Path(value).resolve(strict=True) for value in (args.materials_root or ()))
    if (
        not material_roots
        or tuple(root.parent for root in material_roots) != (material_tree,) * len(material_roots)
        or tuple(root.name for root in material_roots) != tuple(
            f"root-{index:03d}" for index in range(len(material_roots))
        )
    ):
        raise ValueError("source-union renderer material roots are not exact private roots")
    if out_dir.exists() or visibility_path.exists():
        raise ValueError("source-union renderer outputs already exist")
    reference_bytes = reference_path.read_bytes()
    candidate_bytes = candidate_path.read_bytes()
    control_bytes = _SOURCE_UNION_BOOTSTRAP_CONTROL_BYTES
    if control_bytes is None:
        raise ValueError("source-union renderer has no parent-anchored bootstrap control")
    if hashlib.sha256(control_bytes).hexdigest() != args.source_union_control_sha256:
        raise ValueError("source-union bootstrap control anchor differs")
    try:
        payload = json.loads(control_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("source-union control JSON is invalid") from exc
    control = _parse_source_union_control(
        payload, reference_bytes, candidate_bytes, material_roots,
    )
    reference_groups = _source_union_component_groups(
        reference_bytes, control.component_manifest,
    )
    candidate_groups = _source_union_component_groups(
        candidate_bytes, control.component_manifest, control.candidate_transfer,
    )
    vtfcmd = Path(args.vtfcmd).resolve(strict=True) if args.vtfcmd else None
    texture_cache = Path(args.texture_cache).resolve(strict=True)
    if texture_cache != workspace / "texture-cache":
        raise ValueError("source-union renderer texture cache is not private")
    original_dir = out_dir / "original"
    optimized_dir = out_dir / "optimized"
    reference = _render_source_union_side(
        logical_side="reference", groups=reference_groups,
        control=control, out_dir=original_dir, size=args.size,
        material_roots=material_roots, vtfcmd=vtfcmd,
        texture_cache=texture_cache,
    )
    candidate = _render_source_union_side(
        logical_side="candidate", groups=candidate_groups,
        control=control, out_dir=optimized_dir, size=args.size,
        material_roots=material_roots, vtfcmd=vtfcmd,
        texture_cache=texture_cache, fit=reference[2],
    )
    if reference[5] + candidate[5] != 32:
        raise ValueError("source-union renderer did not execute exactly 32 renders")
    diagonal = max(float(reference[3]["diagonal"]), 1e-12)
    geometry = _geometry_entries(reference[1], candidate[1], diagonal, 7)
    reference_geometry = _reference_geometry_entries(
        reference[1], (control.comparison.union_key,), ("bind",),
    )
    reference_manifest = _source_union_manifest_payload(
        control, "reference", reference[0], reference_geometry,
        _geometry_audit_payload(reference[1]),
    )
    candidate_manifest = _source_union_manifest_payload(
        control, "candidate", candidate[0], geometry,
        _geometry_audit_payload(candidate[1]),
    )
    _source_union_write_json(original_dir / "render_manifest.json", reference_manifest)
    _source_union_write_json(optimized_dir / "render_manifest.json", candidate_manifest)
    visibility = _source_union_visibility_payload(
        control, {**reference[4], **candidate[4]},
    )
    _source_union_write_json(visibility_path, visibility)
    if (
        not visibility_path.is_file()
        or {path.name for path in visibility_path.parent.iterdir() if path.is_file()}
        != {contract_path.name, visibility_path.name}
    ):
        raise ValueError("source-union renderer control output inventory differs")
    final = {
        path.relative_to(out_dir).as_posix()
        for path in out_dir.rglob("*") if path.is_file()
    }
    expected = {
        f"{side}/render_manifest.json" for side in ("original", "optimized")
    } | {
        f"{side}/{render_pass}/bind/{angle}.png"
        for side in ("original", "optimized")
        for render_pass in ("textured", "clay")
        for angle in ANGLE_DIRS
    }
    if final != expected:
        raise ValueError("source-union renderer final raw inventory differs")


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
    animation_source: Path | None = None,
    *,
    fit=None,
    source_search_paths: dict[str, tuple[str, ...]] | None = None,
    aggregate_regions: bool = False,
    focus_region: str | None = None,
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
    animation_binding = (
        _apply_animation_source(animation_source, poses)
        if animation_source is not None
        else None
    )
    objs = _get_mesh_objects()
    if not objs:
        raise RuntimeError(f"No mesh objects found for {label}: {src_paths}")
    render_objs = (
        _focus_mesh_objects(
            objs, region_manifest, source_material_evidence, focus_region
        )
        if focus_region is not None else tuple(objs)
    )
    capture_manifest = (
        manifest_for_region(region_manifest, focus_region)
        if focus_region is not None else region_manifest
    )
    blender_source_materials = {
        (obj.name, index): material.name if material else ""
        for obj in render_objs
        if hasattr(obj.data, "materials")
        for index, material in enumerate(obj.data.materials)
    }
    snapshots = _capture_pose_snapshots(
        render_objs,
        poses,
        capture=lambda captured_objects, frame: _capture_regions(
            captured_objects, frame, capture_manifest, source_material_evidence,
            aggregate=aggregate_regions,
        ),
        animation_binding=animation_binding,
    )
    bbox, evaluated_fit = _framing_from_snapshots(snapshots)
    center, ortho_scale, dist = fit or evaluated_fit
    cam_obj.data.ortho_scale = ortho_scale
    _setup_lights(center, ortho_scale)
    entries = []
    root.mkdir(parents=True, exist_ok=True)
    for render_pass in passes:
        if render_pass == "clay":
            _apply_clay_material(render_objs)
            material_audit = {"missing": (), "resolved": ()}
        else:
            material_audit = _apply_textured_materials(
                render_objs,
                blender_source_materials,
                materials_root,
                vtfcmd,
                texture_cache,
                source_search_paths=source_search_paths,
            )
        for pose_name, frame in poses:
            _set_pose_state(animation_binding, pose_name, frame)
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
                        texture_missing=bool(material_audit["missing"]),
                        missing_materials=material_audit["missing"],
                        resolved_materials=material_audit["resolved"],
                    )
                )
    return entries, snapshots, (center, ortho_scale, dist), bbox


def _run_extended(args, before: list[Path], after: list[Path], out_dir: Path, angles: list[str]) -> None:
    try:
        passes = _validated_passes(args.passes)
        validated_angles = _validated_angles(",".join(angles))
        poses = _parse_poses(args.poses)
        focus_region = _validated_focus_request(args, poses)
    except ValueError as exc:
        raise SystemExit(f"[ERROR] {exc}") from exc
    materials_root = tuple(Path(value).resolve(strict=True) for value in (args.materials_root or ()))
    vtfcmd = Path(args.vtfcmd).resolve() if args.vtfcmd else None
    texture_cache = _texture_cache_root(args, out_dir)
    region_manifest_path = _required_region_manifest(args)
    source_root = _required_source_root(args)
    configuration = _required_configuration_manifest(args)
    region_manifest = _load_region_manifest(region_manifest_path)
    if len(before) != len(after):
        raise ValueError("extended Maximum validation requires paired before/after sources")
    if bool(args.animation_before) != bool(args.animation_after):
        raise ValueError("extended Maximum validation requires paired animation sources")
    animation_before = Path(args.animation_before).resolve(strict=True) if args.animation_before else None
    animation_after = Path(args.animation_after).resolve(strict=True) if args.animation_after else None
    if focus_region is not None:
        before, after, source_identity, region_manifest = _focus_source_context(
            region_manifest, focus_region, source_root, before, after, configuration
        )
        source_identities_tuple = (source_identity,)
        source_identities, source_materials, source_search_paths = _extended_source_context(
            region_manifest, source_root, before
        )
        if source_identities != source_identities_tuple:
            raise ValueError("focused source identity changed during material audit")
    else:
        source_identities_tuple, source_materials, source_search_paths = _extended_source_context(
            region_manifest, source_root, before
        )
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
        animation_before,
        source_search_paths=source_search_paths,
        aggregate_regions=args.aggregate_regions,
        focus_region=focus_region,
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
        animation_after,
        fit=fit,
        source_search_paths=source_search_paths,
        aggregate_regions=args.aggregate_regions,
        focus_region=focus_region,
    )
    if focus_region is not None:
        _validate_focus_render_payload(
            reference_entries, reference_snapshots, passes, validated_angles,
            poses, focus_region,
        )
        _validate_focus_render_payload(
            candidate_entries, candidate_snapshots, passes, validated_angles,
            poses, focus_region,
        )
        _validate_focus_output_inventory(original_dir, reference_entries)
        _validate_focus_output_inventory(candidate_dir, candidate_entries)
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
        configuration=configuration,
        geometry_audit=_geometry_audit_payload(reference_snapshots),
    )
    manifest_path = _write_render_manifest(
        candidate_dir,
        candidate_entries,
        geometry,
        candidate_bbox,
        expected=expected,
        stride=stride,
        seed=seed,
        configuration=configuration,
        geometry_audit=_geometry_audit_payload(candidate_snapshots),
    )
    print(f"[OK] Render manifest: {manifest_path}")


def main():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = argv[1:]

    args = _parse_args(argv)
    source_union = (
        args.source_union_contract is not None
        or args.source_union_visibility_out is not None
    )
    if source_union:
        try:
            _validate_source_union_cli_args(args)
        except ValueError as exc:
            raise SystemExit(f"[ERROR] {exc}") from exc
    if bpy is None:
        raise SystemExit("[ERROR] render_previews.py must be executed by Blender.")
    if not source_union:
        _ensure_source_tools()
    before_inputs = [Path(p).expanduser() for p in _expand_paths(args.before)]
    after_inputs = [Path(p).expanduser() for p in _expand_paths(args.after)]
    if args.focus_region is not None:
        before = before_inputs
        after = after_inputs
    else:
        before = [path.resolve() for path in before_inputs]
        after = [path.resolve() for path in after_inputs]
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

    if source_union:
        try:
            _run_source_union(args, before, after, out_dir)
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            raise SystemExit(f"[ERROR] {exc}") from exc
        return

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
