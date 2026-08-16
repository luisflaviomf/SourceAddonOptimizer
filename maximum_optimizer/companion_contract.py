from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Callable
from uuid import UUID, uuid4


CONTRACT_MAJOR = 1
CONTRACT_MINOR = 0
MAX_CHUNK_BYTES = 2_000_000
MAX_COMPLETION_BYTES = 4_096
MAX_INDEX_BYTES = 262_144
MAX_BUNDLE_BYTES = 20_000_000
MAX_JSON_DEPTH = 12
MAX_TOTAL_KEYS = 100_000
MAX_MODELS = 4_096
MAX_MATERIALS = 16_384
MAX_PLAN_MODELS = 250
MAX_SCREENSHOTS = 1_000
MAX_PATH_BYTES = 512
MAX_PATH_COMPONENT_BYTES = 128

SUPPORTED_SCHEMAS = frozenset(
    {
        "sao-runtime-profile-v1",
        "sao-validation-plan-v1",
        "sao-structural-result-v1",
        "sao-visual-result-v1",
    }
)
TEST_KINDS = frozenset(
    {"model_structure", "model_state", "material_resolve", "model_render"}
)
STATUSES = frozenset({"PASS", "WARNING", "FAILED", "INCOMPLETE"})
EVIDENCE = frozenset(
    {"registered", "instantiated", "visible", "repeated_visible", "focused"}
)
VIEWS = frozenset({"front", "side", "rear", "isometric", "closeup"})
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WORKSHOP_ID = re.compile(r"^[0-9]{1,20}$")
_CHUNK_FILE = re.compile(r"^chunk-([0-9]{4})\.json$")
_SCREENSHOT_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_EXTENSIONS = {
    "models": frozenset({".ani", ".mdl", ".phy", ".vtx", ".vvd"}),
    "materials": frozenset({".vmt", ".vtf"}),
    "sound": frozenset({".mp3", ".ogg", ".wav"}),
    "lua": frozenset({".lua"}),
    "maps": frozenset({".bsp"}),
}


@dataclass(frozen=True)
class BundleInspection:
    root: Path
    schema: str
    contract_major: int
    contract_minor: int
    run_id: UUID
    created_utc: datetime
    producer: dict[str, object]
    complete: bool
    compatible: bool
    index_sha256: str


@dataclass(frozen=True)
class ValidatedBundle:
    root: Path
    schema: str
    contract_major: int
    contract_minor: int
    run_id: UUID
    created_utc: datetime
    producer: dict[str, object]
    environment: dict[str, object]
    payloads: tuple[dict[str, object], ...]
    index_sha256: str


def _require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise ValueError(f"{label} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")


def _object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    return value


def _array(value: object, label: str, *, maximum: int) -> list[Any]:
    if type(value) is not list:
        raise ValueError(f"{label} must be an array")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds its item limit")
    return value


def _string(value: object, label: str, *, minimum: int = 0, maximum: int) -> str:
    if type(value) is not str or not minimum <= len(value) <= maximum:
        raise ValueError(f"{label} must be a bounded string")
    return value


def _integer(value: object, label: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{label} must be a bounded integer")
    return value


def _number(value: object, label: str, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite")
    result = float(value)
    if result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f"{label} must be bounded")
    return result


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")
    return value


def _enum(value: object, allowed: frozenset[str], label: str) -> str:
    if type(value) is not str or value not in allowed:
        raise ValueError(f"invalid {label}: {value}")
    return value


def _unique_scalars(values: list[Any], label: str) -> None:
    signatures = [(type(value).__name__, value) for value in values]
    if len(set(signatures)) != len(signatures):
        raise ValueError(f"{label} must contain unique values")


def _parse_timestamp(value: object) -> datetime:
    raw = _string(value, "created_utc", minimum=20, maximum=32)
    if not raw.endswith("Z"):
        raise ValueError("created_utc must be RFC 3339 UTC")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("created_utc must be RFC 3339 UTC") from exc
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("created_utc must be RFC 3339 UTC")
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("created_utc is in the future")
    return parsed


def _validate_json_shape(value: object) -> None:
    key_count = 0

    def visit(current: object, depth: int) -> None:
        nonlocal key_count
        if depth > MAX_JSON_DEPTH:
            raise ValueError("JSON exceeds maximum depth")
        if type(current) is dict:
            key_count += len(current)
            if key_count > MAX_TOTAL_KEYS:
                raise ValueError("JSON exceeds maximum key count")
            for key, item in current.items():
                if type(key) is not str:
                    raise ValueError("JSON object key must be a string")
                visit(item, depth + 1)
        elif type(current) is list:
            for item in current:
                visit(item, depth + 1)
        elif type(current) is float and not math.isfinite(current):
            raise ValueError("JSON numbers must be finite")

    visit(value, 1)


def _read_bytes(path: Path, maximum: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is missing") from exc
    if size > maximum:
        raise ValueError(f"{label} exceeds its byte limit")
    return path.read_bytes()


def _load_object(path: Path, maximum: int, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_bytes(path, maximum, label)

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("JSON numbers must be finite")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{label} root must be an object")
    _validate_json_shape(value)
    return value, raw


def normalize_virtual_path(value: object) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        raise ValueError("invalid virtual path")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid virtual path: non-ASCII characters are not supported in v1") from exc
    normalized = value.replace("\\", "/").lower()
    if (
        normalized.startswith("/")
        or "//" in normalized
        or ":" in normalized
        or len(normalized.encode("ascii")) > MAX_PATH_BYTES
    ):
        raise ValueError("invalid virtual path")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("invalid virtual path")
    if any(len(part.encode("ascii")) > MAX_PATH_COMPONENT_BYTES for part in path.parts):
        raise ValueError("invalid virtual path component")
    root = path.parts[0]
    if root not in _EXTENSIONS or path.suffix not in _EXTENSIONS[root]:
        raise ValueError("invalid virtual path extension or root")
    return path.as_posix()


def _normalize_screenshot_path(value: object, run_id: UUID) -> str:
    if type(value) is not str or value != value.strip() or "\\" in value:
        raise ValueError("invalid screenshot path")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid screenshot path") from exc
    path = PurePosixPath(value.lower())
    if (
        path.is_absolute()
        or len(path.parts) != 3
        or path.parts[0] != "screenshots"
        or path.parts[1] != str(run_id)
        or path.suffix != ".png"
        or any(part in {"", ".", ".."} or _SCREENSHOT_COMPONENT.fullmatch(part) is None for part in path.parts)
    ):
        raise ValueError("invalid screenshot path")
    return path.as_posix()


def _validate_contract(value: object) -> tuple[int, int]:
    contract = _object(value, "contract")
    _require_keys(contract, {"major", "minor"}, "contract")
    return (_integer(contract["major"], "contract.major"), _integer(contract["minor"], "contract.minor"))


def _validate_producer(value: object) -> dict[str, object]:
    producer = _object(value, "producer")
    _require_keys(producer, {"name", "version"}, "producer")
    _string(producer["name"], "producer.name", minimum=1, maximum=64)
    _string(producer["version"], "producer.version", minimum=1, maximum=32)
    return dict(producer)


def _validate_environment(value: object) -> dict[str, object]:
    environment = _object(value, "environment")
    _require_keys(environment, {"game_build", "map", "gamemode", "realm", "session_seconds", "mounted_addons"}, "environment")
    _string(environment["game_build"], "environment.game_build", maximum=64)
    _string(environment["map"], "environment.map", maximum=128)
    _string(environment["gamemode"], "environment.gamemode", maximum=128)
    _enum(environment["realm"], frozenset({"server", "client", "singleplayer"}), "realm")
    _number(environment["session_seconds"], "environment.session_seconds", maximum=604_800)
    for addon in _array(environment["mounted_addons"], "environment.mounted_addons", maximum=4_096):
        item = _object(addon, "mounted addon")
        _require_keys(item, {"workshop_id", "title"}, "mounted addon")
        workshop_id = _string(item["workshop_id"], "mounted addon workshop_id", minimum=1, maximum=20)
        if _WORKSHOP_ID.fullmatch(workshop_id) is None:
            raise ValueError("invalid mounted addon workshop_id")
        _string(item["title"], "mounted addon title", maximum=256)
    return dict(environment)


def _validate_chunk_meta(value: object, sequence: int, total: int) -> None:
    chunk = _object(value, "chunk")
    _require_keys(chunk, {"sequence", "total"}, "chunk")
    if _integer(chunk["sequence"], "chunk.sequence", minimum=1) != sequence:
        raise ValueError("chunk sequence is invalid")
    if _integer(chunk["total"], "chunk.total", minimum=1) != total:
        raise ValueError("chunk total is invalid")


def _validate_ids(items: list[Any], label: str) -> None:
    seen: set[str] = set()
    for raw in items:
        item = _object(raw, label)
        identity = _string(item.get("id"), f"{label}.id", minimum=1, maximum=64)
        if identity in seen:
            raise ValueError(f"duplicate id in {label}: {identity}")
        seen.add(identity)


def _validate_bodygroup(value: object, *, evidence: bool) -> None:
    item = _object(value, "bodygroup")
    expected = {"name", "value", "evidence"} if evidence else {"name", "value"}
    _require_keys(item, expected, "bodygroup")
    _string(item["name"], "bodygroup.name", minimum=1, maximum=128)
    _integer(item["value"], "bodygroup.value", maximum=255)
    if evidence:
        _enum(item["evidence"], EVIDENCE, "evidence")


def _validate_performance(value: object, schema: str) -> None:
    item = _object(value, "performance")
    if schema == "sao-runtime-profile-v1":
        _require_keys(item, {"samples", "average_ms", "p95_ms", "max_ms", "queue_high_water"}, "performance")
        _integer(item["samples"], "performance.samples")
        _number(item["average_ms"], "performance.average_ms")
        _number(item["p95_ms"], "performance.p95_ms")
        _number(item["max_ms"], "performance.max_ms")
        _integer(item["queue_high_water"], "performance.queue_high_water")
    elif schema == "sao-structural-result-v1":
        _require_keys(item, {"tests", "wall_seconds"}, "performance")
        _integer(item["tests"], "performance.tests")
        _number(item["wall_seconds"], "performance.wall_seconds")
    else:
        _require_keys(item, {"models", "screenshots", "wall_seconds"}, "performance")
        _integer(item["models"], "performance.models")
        _integer(item["screenshots"], "performance.screenshots")
        _number(item["wall_seconds"], "performance.wall_seconds")


def _validate_truncation(value: object, fields: set[str]) -> None:
    item = _object(value, "truncation")
    _require_keys(item, fields, "truncation")
    for field in fields:
        _integer(item[field], f"truncation.{field}")


def _validate_profile_payload(payload: dict[str, Any]) -> None:
    _require_keys(payload, {"models", "materials", "lua_errors", "performance", "truncation"}, "payload")
    models = _array(payload["models"], "models", maximum=MAX_MODELS)
    materials = _array(payload["materials"], "materials", maximum=MAX_MATERIALS)
    errors = _array(payload["lua_errors"], "lua_errors", maximum=1_024)
    _validate_ids(models, "models")
    _validate_ids(materials, "materials")
    _validate_ids(errors, "lua_errors")
    for raw in models:
        item = _object(raw, "model")
        _require_keys(item, {"id", "path", "evidence", "skins", "bodygroups", "submaterials", "materials"}, "model")
        if not normalize_virtual_path(item["path"]).endswith(".mdl"):
            raise ValueError("model path must identify an mdl")
        _enum(item["evidence"], EVIDENCE, "evidence")
        skins = _array(item["skins"], "model.skins", maximum=256)
        for skin in skins:
            _integer(skin, "model skin", maximum=255)
        _unique_scalars(skins, "model.skins")
        for bodygroup in _array(item["bodygroups"], "model.bodygroups", maximum=1_024):
            _validate_bodygroup(bodygroup, evidence=True)
        for submaterial in _array(item["submaterials"], "model.submaterials", maximum=256):
            sub = _object(submaterial, "submaterial")
            _require_keys(sub, {"slot", "material", "evidence"}, "submaterial")
            _integer(sub["slot"], "submaterial.slot", maximum=255)
            if not normalize_virtual_path(sub["material"]).endswith(".vmt"):
                raise ValueError("submaterial must identify a VMT")
            _enum(sub["evidence"], EVIDENCE, "evidence")
        model_materials = _array(item["materials"], "model.materials", maximum=4_096)
        for material in model_materials:
            if not normalize_virtual_path(material).endswith(".vmt"):
                raise ValueError("model material must identify a VMT")
        _unique_scalars(model_materials, "model.materials")
    for raw in materials:
        item = _object(raw, "material")
        _require_keys(item, {"id", "path", "evidence"}, "material")
        if not normalize_virtual_path(item["path"]).endswith(".vmt"):
            raise ValueError("material path must identify a VMT")
        _enum(item["evidence"], EVIDENCE, "evidence")
    for raw in errors:
        item = _object(raw, "lua_error")
        _require_keys(item, {"id", "realm", "message", "source", "count"}, "lua_error")
        _enum(item["realm"], frozenset({"server", "client", "menu"}), "lua error realm")
        _string(item["message"], "lua_error.message", maximum=1_024)
        source = _string(item["source"], "lua_error.source", maximum=512)
        if source:
            normalize_virtual_path(source)
        _integer(item["count"], "lua_error.count", minimum=1, maximum=1_000_000)
    _validate_performance(payload["performance"], "sao-runtime-profile-v1")
    _validate_truncation(payload["truncation"], {"models", "materials", "lua_errors", "bytes"})


def _validate_plan_payload(payload: dict[str, Any]) -> None:
    _require_keys(payload, {"tests", "limits", "dx80_excluded", "truncation"}, "payload")
    tests = _array(payload["tests"], "tests", maximum=5_000)
    _validate_ids(tests, "tests")
    model_paths: set[str] = set()
    for raw in tests:
        item = _object(raw, "test")
        _require_keys(item, {"id", "kind", "path", "required_files", "expected_phy", "skins", "bodygroups", "materials", "views", "priority", "expected_bytes"}, "test")
        kind = _enum(item["kind"], TEST_KINDS, "test kind")
        path = normalize_virtual_path(item["path"])
        if kind != "material_resolve":
            if not path.startswith("models/") or not path.endswith(".mdl"):
                raise ValueError("model test path must identify an MDL")
            model_paths.add(path)
        elif not path.startswith("materials/") or not path.endswith(".vmt"):
            raise ValueError("material test path must identify a VMT")
        required = _array(item["required_files"], "test.required_files", maximum=64)
        for required_path in required:
            normalize_virtual_path(required_path)
        _unique_scalars(required, "test.required_files")
        _boolean(item["expected_phy"], "test.expected_phy")
        skins = _array(item["skins"], "test.skins", maximum=256)
        for skin in skins:
            _integer(skin, "test skin", maximum=255)
        _unique_scalars(skins, "test.skins")
        for bodygroup in _array(item["bodygroups"], "test.bodygroups", maximum=1_024):
            _validate_bodygroup(bodygroup, evidence=False)
        materials = _array(item["materials"], "test.materials", maximum=4_096)
        for material in materials:
            normalize_virtual_path(material)
        _unique_scalars(materials, "test.materials")
        views = _array(item["views"], "test.views", maximum=4)
        for view in views:
            _enum(view, VIEWS, "view")
        _unique_scalars(views, "test.views")
        _integer(item["priority"], "test.priority", maximum=1_000_000)
        _integer(item["expected_bytes"], "test.expected_bytes")
    if len(model_paths) > MAX_PLAN_MODELS:
        raise ValueError("plan exceeds model limit")
    limits = _object(payload["limits"], "limits")
    _require_keys(limits, {"max_models", "max_screenshots", "max_ordinary_views", "screenshot_retries"}, "limits")
    _integer(limits["max_models"], "limits.max_models", minimum=1, maximum=250)
    _integer(limits["max_screenshots"], "limits.max_screenshots", minimum=1, maximum=1_000)
    _integer(limits["max_ordinary_views"], "limits.max_ordinary_views", minimum=1, maximum=4)
    if _integer(limits["screenshot_retries"], "limits.screenshot_retries") != 1:
        raise ValueError("limits.screenshot_retries must be one")
    if _boolean(payload["dx80_excluded"], "dx80_excluded") is not True:
        raise ValueError("DX80 must be excluded")
    _validate_truncation(payload["truncation"], {"tests", "models", "screenshots"})


def _validate_status_resource(value: object) -> None:
    item = _object(value, "resource")
    _require_keys(item, {"id", "test_id", "path", "status", "reason", "warnings"}, "resource")
    _string(item["id"], "resource.id", minimum=1, maximum=64)
    _string(item["test_id"], "resource.test_id", minimum=1, maximum=64)
    normalize_virtual_path(item["path"])
    _enum(item["status"], STATUSES, "status")
    _string(item["reason"], "resource.reason", maximum=1_024)
    for warning in _array(item["warnings"], "resource.warnings", maximum=64):
        _string(warning, "resource warning", maximum=512)


def _sha256_value(value: object, label: str) -> str:
    raw = _string(value, label, minimum=64, maximum=64)
    if _HEX_SHA256.fullmatch(raw) is None:
        raise ValueError(f"{label} must be lowercase SHA-256")
    return raw


def _validate_result_payload(payload: dict[str, Any], schema: str, run_id: UUID) -> None:
    visual = schema == "sao-visual-result-v1"
    expected = {"plan_sha256", "overall_status", "resources", "performance", "truncation"}
    if visual:
        expected.add("screenshots")
    _require_keys(payload, expected, "payload")
    _sha256_value(payload["plan_sha256"], "plan_sha256")
    _enum(payload["overall_status"], STATUSES, "overall status")
    resources = _array(payload["resources"], "resources", maximum=5_000)
    _validate_ids(resources, "resources")
    for resource in resources:
        _validate_status_resource(resource)
    _validate_performance(payload["performance"], schema)
    if visual:
        screenshots = _array(payload["screenshots"], "screenshots", maximum=MAX_SCREENSHOTS)
        _validate_ids(screenshots, "screenshots")
        for raw in screenshots:
            item = _object(raw, "screenshot")
            _require_keys(item, {"id", "test_id", "path", "sha256", "bytes", "view", "retry_count", "status", "camera", "state"}, "screenshot")
            _string(item["test_id"], "screenshot.test_id", minimum=1, maximum=64)
            _normalize_screenshot_path(item["path"], run_id)
            _sha256_value(item["sha256"], "screenshot.sha256")
            _integer(item["bytes"], "screenshot.bytes")
            _enum(item["view"], VIEWS, "view")
            _integer(item["retry_count"], "screenshot.retry_count", maximum=1)
            _enum(item["status"], STATUSES, "status")
            camera = _object(item["camera"], "screenshot.camera")
            _require_keys(camera, {"fov", "distance", "crop_width", "crop_height"}, "screenshot.camera")
            _number(camera["fov"], "screenshot.camera.fov", minimum=1, maximum=120)
            _number(camera["distance"], "screenshot.camera.distance", maximum=1_000_000)
            _integer(camera["crop_width"], "screenshot.camera.crop_width", minimum=1, maximum=4_096)
            _integer(camera["crop_height"], "screenshot.camera.crop_height", minimum=1, maximum=4_096)
            model_state = _object(item["state"], "screenshot.state")
            _require_keys(model_state, {"skin", "bodygroups"}, "screenshot.state")
            _integer(model_state["skin"], "screenshot.state.skin", maximum=255)
            for bodygroup in _array(model_state["bodygroups"], "screenshot.state.bodygroups", maximum=1_024):
                _validate_bodygroup(bodygroup, evidence=False)
        _validate_truncation(payload["truncation"], {"models", "screenshots"})
    else:
        _validate_truncation(payload["truncation"], {"resources", "warnings"})


_PAYLOAD_VALIDATORS: dict[str, Callable[..., None]] = {
    "sao-runtime-profile-v1": _validate_profile_payload,
    "sao-validation-plan-v1": _validate_plan_payload,
}


def _read_index_and_ready(root: Path) -> tuple[dict[str, Any], bytes, UUID, datetime, dict[str, object], dict[str, object], int, int]:
    if not root.is_dir():
        raise ValueError("bundle root is missing")
    if not (root / "complete.ready.json").is_file():
        raise ValueError("bundle is incomplete: complete.ready.json is missing")
    index, index_raw = _load_object(root / "run-index.json", MAX_INDEX_BYTES, "run index")
    ready, _ready_raw = _load_object(root / "complete.ready.json", MAX_COMPLETION_BYTES, "completion marker")
    _require_keys(ready, {"schema", "run_id", "index_sha256"}, "completion marker")
    if ready["schema"] != "sao-completion-v1":
        raise ValueError("invalid completion schema")
    index_hash = hashlib.sha256(index_raw).hexdigest()
    if _sha256_value(ready["index_sha256"], "completion index_sha256") != index_hash:
        raise ValueError("completion index hash does not match")
    _require_keys(index, {"schema", "contract", "run_id", "payload_schema", "created_utc", "producer", "environment", "chunk_count", "total_bytes", "chunks"}, "run index")
    if index["schema"] != "sao-run-index-v1":
        raise ValueError("invalid run index schema")
    try:
        run_id = UUID(_string(index["run_id"], "run_id", minimum=36, maximum=36))
    except ValueError as exc:
        raise ValueError("invalid run_id UUID") from exc
    if ready["run_id"] != str(run_id):
        raise ValueError("completion run_id does not match the index")
    major, minor = _validate_contract(index["contract"])
    created = _parse_timestamp(index["created_utc"])
    producer = _validate_producer(index["producer"])
    environment = _validate_environment(index["environment"])
    return index, index_raw, run_id, created, producer, environment, major, minor


def inspect_bundle(root: Path) -> BundleInspection:
    bundle_root = Path(root)
    index, index_raw, run_id, created, producer, _environment, major, minor = _read_index_and_ready(bundle_root)
    schema = _string(index["payload_schema"], "payload_schema", minimum=1, maximum=64)
    return BundleInspection(bundle_root, schema, major, minor, run_id, created, producer, True, schema in SUPPORTED_SCHEMAS and major == CONTRACT_MAJOR and minor == CONTRACT_MINOR, hashlib.sha256(index_raw).hexdigest())


def read_bundle(root: Path, *, expected_schema: str) -> ValidatedBundle:
    bundle_root = Path(root)
    index, index_raw, run_id, created, producer, environment, major, minor = _read_index_and_ready(bundle_root)
    schema = _string(index["payload_schema"], "payload_schema", minimum=1, maximum=64)
    if schema != expected_schema:
        raise ValueError(f"unexpected payload schema: {schema}")
    if schema not in SUPPORTED_SCHEMAS or major != CONTRACT_MAJOR or minor != CONTRACT_MINOR:
        raise ValueError(f"unsupported contract {major}.{minor} for {schema}")
    chunk_count = _integer(index["chunk_count"], "chunk_count", minimum=1, maximum=10_000)
    total_bytes = _integer(index["total_bytes"], "total_bytes", maximum=MAX_BUNDLE_BYTES)
    declarations = _array(index["chunks"], "chunks", maximum=10_000)
    if len(declarations) != chunk_count:
        raise ValueError("chunk_count does not match declarations")

    prevalidated_filenames: set[str] = set()
    for sequence, raw_declaration in enumerate(declarations, start=1):
        declaration = _object(raw_declaration, "chunk declaration")
        _require_keys(declaration, {"sequence", "file", "bytes", "sha256"}, "chunk declaration")
        if _integer(declaration["sequence"], "chunk declaration sequence", minimum=1) != sequence:
            raise ValueError("chunk declarations are reordered")
        filename = _string(declaration["file"], "chunk filename", minimum=15, maximum=15)
        match = _CHUNK_FILE.fullmatch(filename)
        if match is None or int(match.group(1)) != sequence or filename in prevalidated_filenames:
            raise ValueError("chunk filename is invalid or repeated")
        prevalidated_filenames.add(filename)

    filenames: set[str] = set()
    declared_total = 0
    payloads: list[dict[str, object]] = []
    identities: dict[str, set[str]] = {}
    for sequence, raw_declaration in enumerate(declarations, start=1):
        declaration = _object(raw_declaration, "chunk declaration")
        _require_keys(declaration, {"sequence", "file", "bytes", "sha256"}, "chunk declaration")
        if _integer(declaration["sequence"], "chunk declaration sequence", minimum=1) != sequence:
            raise ValueError("chunk declarations are reordered")
        filename = _string(declaration["file"], "chunk filename", minimum=15, maximum=15)
        match = _CHUNK_FILE.fullmatch(filename)
        if match is None or int(match.group(1)) != sequence or filename in filenames:
            raise ValueError("chunk filename is invalid or repeated")
        filenames.add(filename)
        declared_bytes = _integer(declaration["bytes"], "chunk bytes", minimum=1)
        if declared_bytes > MAX_CHUNK_BYTES:
            raise ValueError("chunk exceeds its byte limit")
        declared_total += declared_bytes
        chunk_path = bundle_root / filename
        chunk_raw = _read_bytes(chunk_path, MAX_CHUNK_BYTES, "chunk")
        if len(chunk_raw) != declared_bytes:
            raise ValueError("chunk size does not match its declaration")
        if hashlib.sha256(chunk_raw).hexdigest() != _sha256_value(declaration["sha256"], "chunk sha256"):
            raise ValueError("chunk hash does not match its declaration")
        chunk, _ = _load_object(chunk_path, MAX_CHUNK_BYTES, "chunk")
        _require_keys(chunk, {"schema", "contract", "run_id", "created_utc", "producer", "environment", "chunk", "payload"}, "chunk envelope")
        if chunk["schema"] != schema:
            raise ValueError("chunk schema does not match the index")
        if chunk["run_id"] != str(run_id):
            raise ValueError("chunk run_id does not match the index")
        if _validate_contract(chunk["contract"]) != (major, minor):
            raise ValueError("chunk contract does not match the index")
        if _parse_timestamp(chunk["created_utc"]) != created:
            raise ValueError("chunk timestamp does not match the index")
        if _validate_producer(chunk["producer"]) != producer:
            raise ValueError("chunk producer does not match the index")
        if _validate_environment(chunk["environment"]) != environment:
            raise ValueError("chunk environment does not match the index")
        _validate_chunk_meta(chunk["chunk"], sequence, chunk_count)
        payload = _object(chunk["payload"], "payload")
        if schema in _PAYLOAD_VALIDATORS:
            _PAYLOAD_VALIDATORS[schema](payload)
        else:
            _validate_result_payload(payload, schema, run_id)
        for collection in ("models", "materials", "lua_errors", "tests", "resources", "screenshots"):
            values = payload.get(collection)
            if type(values) is not list:
                continue
            seen = identities.setdefault(collection, set())
            for value in values:
                item = _object(value, collection)
                identity = _string(item.get("id"), f"{collection}.id", minimum=1, maximum=64)
                if identity in seen:
                    raise ValueError(f"duplicate id across chunks in {collection}: {identity}")
                seen.add(identity)
        payloads.append(dict(payload))
    if declared_total != total_bytes:
        raise ValueError("total_bytes does not match chunk declarations")
    if len(identities.get("models", ())) > MAX_MODELS or len(identities.get("materials", ())) > MAX_MATERIALS:
        raise ValueError("profile exceeds aggregate item limits")
    if len(identities.get("screenshots", ())) > MAX_SCREENSHOTS:
        raise ValueError("visual result exceeds screenshot limit")
    return ValidatedBundle(bundle_root, schema, major, minor, run_id, created, producer, environment, tuple(payloads), hashlib.sha256(index_raw).hexdigest())


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _format_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_utc must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_atomic_file(path: Path, payload: bytes) -> None:
    partial = path.with_name(path.name + ".partial")
    with partial.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def _cleanup_writer_partial(root: Path) -> None:
    if not root.is_dir() or not root.name.endswith(".partial"):
        return
    for child in root.iterdir():
        if child.is_file() and child.parent == root:
            child.unlink(missing_ok=True)
    try:
        root.rmdir()
    except OSError:
        pass


def write_bundle(
    root: Path,
    *,
    schema: str,
    run_id: UUID,
    created_utc: datetime,
    producer: dict[str, object],
    environment: dict[str, object],
    payloads: list[dict[str, object]] | tuple[dict[str, object], ...],
) -> ValidatedBundle:
    if schema not in SUPPORTED_SCHEMAS:
        raise ValueError(f"unsupported contract {CONTRACT_MAJOR}.{CONTRACT_MINOR} for {schema}")
    if type(run_id) is not UUID:
        raise ValueError("run_id must be a UUID")
    created_raw = _format_timestamp(created_utc)
    _parse_timestamp(created_raw)
    producer_value = _validate_producer(producer)
    environment_value = _validate_environment(environment)
    if type(payloads) not in (list, tuple) or not payloads or len(payloads) > 10_000:
        raise ValueError("payloads must be a bounded non-empty sequence")

    total = len(payloads)
    envelopes: list[dict[str, object]] = []
    encoded_chunks: list[bytes] = []
    for sequence, raw_payload in enumerate(payloads, start=1):
        payload = _object(raw_payload, "payload")
        if schema in _PAYLOAD_VALIDATORS:
            _PAYLOAD_VALIDATORS[schema](payload)
        else:
            _validate_result_payload(payload, schema, run_id)
        envelope: dict[str, object] = {
            "schema": schema,
            "contract": {"major": CONTRACT_MAJOR, "minor": CONTRACT_MINOR},
            "run_id": str(run_id),
            "created_utc": created_raw,
            "producer": producer_value,
            "environment": environment_value,
            "chunk": {"sequence": sequence, "total": total},
            "payload": payload,
        }
        _validate_json_shape(envelope)
        encoded = _json_bytes(envelope)
        if len(encoded) > MAX_CHUNK_BYTES:
            raise ValueError("chunk exceeds its byte limit")
        envelopes.append(envelope)
        encoded_chunks.append(encoded)
    if sum(map(len, encoded_chunks)) > MAX_BUNDLE_BYTES:
        raise ValueError("bundle exceeds its byte limit")

    destination = Path(root).resolve(strict=False)
    if destination.exists():
        raise ValueError(f"bundle destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_root = destination.parent / f".{destination.name}.{os.getpid()}.{uuid4().hex}.partial"
    partial_root.mkdir()
    try:
        declarations: list[dict[str, object]] = []
        for sequence, encoded in enumerate(encoded_chunks, start=1):
            filename = f"chunk-{sequence:04d}.json"
            _write_atomic_file(partial_root / filename, encoded)
            declarations.append(
                {
                    "sequence": sequence,
                    "file": filename,
                    "bytes": len(encoded),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                }
            )
        index = {
            "schema": "sao-run-index-v1",
            "contract": {"major": CONTRACT_MAJOR, "minor": CONTRACT_MINOR},
            "run_id": str(run_id),
            "payload_schema": schema,
            "created_utc": created_raw,
            "producer": producer_value,
            "environment": environment_value,
            "chunk_count": total,
            "total_bytes": sum(len(value) for value in encoded_chunks),
            "chunks": declarations,
        }
        index_raw = _json_bytes(index)
        if len(index_raw) > MAX_INDEX_BYTES:
            raise ValueError("run index exceeds its byte limit")
        _write_atomic_file(partial_root / "run-index.json", index_raw)
        ready = {
            "schema": "sao-completion-v1",
            "run_id": str(run_id),
            "index_sha256": hashlib.sha256(index_raw).hexdigest(),
        }
        ready_raw = _json_bytes(ready)
        if len(ready_raw) > MAX_COMPLETION_BYTES:
            raise ValueError("completion marker exceeds its byte limit")
        _write_atomic_file(partial_root / "complete.ready.json", ready_raw)
        read_bundle(partial_root, expected_schema=schema)
        if destination.exists():
            raise ValueError(f"bundle destination already exists: {destination}")
        os.rename(partial_root, destination)
    except Exception:
        _cleanup_writer_partial(partial_root)
        raise
    return read_bundle(destination, expected_schema=schema)
