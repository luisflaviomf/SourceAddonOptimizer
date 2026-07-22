from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import zipfile

from .silhouette_native import NativeSilhouettePackage, RawMaskSilhouetteKernel


MANIFEST_RELATIVE_PATH = "_internal/maximum_optimizer/native/tool-package-manifest.json"
DLL_RELATIVE_PATH = "_internal/maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll"
ATTRIBUTE_CONTRACT_RELATIVE_PATHS = (
    "_internal/maximum_optimizer/mesh_attributes.py",
    "_internal/maximum_optimizer/meshopt_bridge.py",
)
TOOL_VERSION = "0.1.18"


def _safe_path(raw: object) -> str:
    value = str(raw).replace("\\", "/")
    path = PurePosixPath(value)
    if not value or value.startswith("/") or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise RuntimeError(f"unsafe package path: {value!r}")
    return path.as_posix()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_pe_amd64(payload: bytes, label: str) -> None:
    if len(payload) < 64 or payload[:2] != b"MZ":
        raise RuntimeError(f"native DLL is not a PE file: {label}")
    pe_offset = struct.unpack_from("<I", payload, 0x3C)[0]
    if pe_offset + 6 > len(payload) or payload[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise RuntimeError(f"native DLL has an invalid PE header: {label}")
    machine = struct.unpack_from("<H", payload, pe_offset + 4)[0]
    if machine != 0x8664:
        raise RuntimeError(f"native DLL is not AMD64: {label} (0x{machine:04x})")


def _parse_manifest(payload: bytes) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"tool package manifest is invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("tool package manifest root must be an object")
    if manifest.get("schemaVersion") != 1 or manifest.get("toolVersion") != TOOL_VERSION:
        raise RuntimeError("unsupported tool package manifest contract")
    if manifest.get("toolName") != "SourceAddonOptimizer":
        raise RuntimeError("unexpected tool package name")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise RuntimeError("tool package manifest has no files")
    declared: dict[str, dict[str, object]] = {}
    ordered: list[str] = []
    folded: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise RuntimeError("tool package file entry must be an object")
        path = _safe_path(raw.get("path"))
        key = path.casefold()
        if key in folded:
            raise RuntimeError(f"duplicate package path: {path}")
        folded.add(key)
        try:
            size = int(raw["size"])
            digest = str(raw["sha256"]).lower()
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid package file declaration: {path}") from exc
        if size < 0 or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise RuntimeError(f"invalid package hash/size declaration: {path}")
        declared[path] = {"path": path, "size": size, "sha256": digest}
        ordered.append(path)
    if ordered != sorted(ordered, key=lambda item: item.encode("utf-8")):
        raise RuntimeError("tool package file manifest is not bytewise sorted")
    missing_contract = [path for path in ATTRIBUTE_CONTRACT_RELATIVE_PATHS if path not in declared]
    if missing_contract:
        raise RuntimeError(f"tool package attribute contract sources are missing: {missing_contract}")
    silhouette = manifest.get("silhouette")
    if not isinstance(silhouette, dict):
        raise RuntimeError("tool package silhouette contract is missing")
    dll_path = _safe_path(silhouette.get("dllPath"))
    if dll_path != DLL_RELATIVE_PATH or dll_path not in declared:
        raise RuntimeError("tool package silhouette DLL declaration is missing")
    dll_entry = declared[dll_path]
    if (
        silhouette.get("apiVersion") != "1.0.0"
        or silhouette.get("buildId") != "maximum-silhouette-raw-v1-20260722"
        or silhouette.get("architecture") != "x64"
        or str(silhouette.get("sha256", "")).lower() != dll_entry["sha256"]
        or int(silhouette.get("size", -1)) != dll_entry["size"]
        or silhouette.get("minimumWorkerContract") != TOOL_VERSION
        or silhouette.get("minimumWpfContract") != TOOL_VERSION
    ):
        raise RuntimeError("tool package silhouette contract disagrees with file manifest")
    return manifest, declared


def validate_package_directory(package_root: Path, *, verify_native_abi: bool = False) -> dict[str, object]:
    root = Path(package_root).resolve()
    manifest_path = root / Path(MANIFEST_RELATIVE_PATH)
    manifest, declared = _parse_manifest(manifest_path.read_bytes())
    actual_paths = sorted(
        (
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path.resolve() != manifest_path.resolve()
        ),
        key=lambda item: item.encode("utf-8"),
    )
    if actual_paths != list(declared):
        missing = sorted(set(declared) - set(actual_paths))
        extra = sorted(set(actual_paths) - set(declared))
        raise RuntimeError(f"tool package contents differ from manifest: missing={missing} extra={extra}")
    for relative, entry in declared.items():
        path = root / Path(relative)
        payload = path.read_bytes()
        if len(payload) != entry["size"] or _sha256_bytes(payload) != entry["sha256"]:
            raise RuntimeError(f"tool package file hash/size mismatch: {relative}")
    dll_payload = (root / Path(DLL_RELATIVE_PATH)).read_bytes()
    _validate_pe_amd64(dll_payload, DLL_RELATIVE_PATH)
    if verify_native_abi:
        silhouette = manifest["silhouette"]
        assert isinstance(silhouette, dict)
        RawMaskSilhouetteKernel(
            NativeSilhouettePackage(
                (root / Path(DLL_RELATIVE_PATH)).resolve(),
                str(silhouette["sha256"]),
                int(silhouette["size"]),
                str(silhouette["apiVersion"]),
                str(silhouette["buildId"]),
                str(silhouette["architecture"]),
            )
        )
    return manifest


def validate_package_zip(zip_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(Path(zip_path), "r") as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [_safe_path(info.filename) for info in infos]
        if len({name.casefold() for name in names}) != len(names):
            raise RuntimeError("tool package ZIP has duplicate paths")
        by_name = dict(zip(names, infos))
        if MANIFEST_RELATIVE_PATH not in by_name:
            raise RuntimeError("tool package ZIP is missing its manifest")
        manifest, declared = _parse_manifest(archive.read(by_name[MANIFEST_RELATIVE_PATH]))
        actual = sorted(
            (name for name in names if name != MANIFEST_RELATIVE_PATH),
            key=lambda item: item.encode("utf-8"),
        )
        if actual != list(declared):
            raise RuntimeError("tool package ZIP contents differ from manifest")
        dll_payload = b""
        for relative, entry in declared.items():
            payload = archive.read(by_name[relative])
            if len(payload) != entry["size"] or _sha256_bytes(payload) != entry["sha256"]:
                raise RuntimeError(f"tool package ZIP hash/size mismatch: {relative}")
            if relative == DLL_RELATIVE_PATH:
                dll_payload = payload
        _validate_pe_amd64(dll_payload, DLL_RELATIVE_PATH)
        return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--zip", action="store_true")
    parser.add_argument("--verify-native-abi", action="store_true")
    args = parser.parse_args(argv)
    if args.zip:
        validate_package_zip(args.path)
    else:
        validate_package_directory(args.path, verify_native_abi=args.verify_native_abi)
    print(f"validated SourceAddonOptimizer package: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
