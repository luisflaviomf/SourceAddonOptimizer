from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import zipfile

from maximum_optimizer.tool_package_manifest import (
    ATTRIBUTE_CONTRACT_RELATIVE_PATHS,
    DLL_RELATIVE_PATH,
    MANIFEST_RELATIVE_PATH,
    validate_package_directory,
    validate_package_zip,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
NATIVE_DLL = REPO_ROOT / "maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll"
def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_package(root: Path, *, include_attribute_contract: bool = True) -> Path:
    dll = root / DLL_RELATIVE_PATH
    dll.parent.mkdir(parents=True)
    shutil.copy2(NATIVE_DLL, dll)
    (root / "SourceAddonOptimizerWorker.exe").write_bytes(b"worker")
    (root / "_internal/base_library.zip").parent.mkdir(parents=True, exist_ok=True)
    (root / "_internal/base_library.zip").write_bytes(b"base")
    if include_attribute_contract:
        for relative in ATTRIBUTE_CONTRACT_RELATIVE_PATHS:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode("ascii"))
    paths = sorted(
        (path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()),
        key=lambda value: value.encode("utf-8"),
    )
    files = [
        {"path": relative, "size": (root / relative).stat().st_size, "sha256": _digest(root / relative)}
        for relative in paths
    ]
    dll_entry = next(item for item in files if item["path"] == DLL_RELATIVE_PATH)
    manifest = {
        "schemaVersion": 1,
        "toolName": "SourceAddonOptimizer",
        "toolVersion": "0.1.18",
        "silhouette": {
            "apiVersion": "1.0.0",
            "buildId": "maximum-silhouette-raw-v1-20260722",
            "architecture": "x64",
            "dllPath": DLL_RELATIVE_PATH,
            "sha256": dll_entry["sha256"],
            "size": dll_entry["size"],
            "minimumWorkerContract": "0.1.18",
            "minimumWpfContract": "0.1.18",
        },
        "files": files,
    }
    manifest_path = root / MANIFEST_RELATIVE_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


@unittest.skipUnless(NATIVE_DLL.is_file(), "promoted native DLL not built")
class ToolPackageManifestTests(unittest.TestCase):
    def test_worker_spec_packages_attribute_contract_sources(self) -> None:
        spec = (REPO_ROOT / "pyinstaller/worker.spec").read_text(encoding="utf-8")
        for relative in ATTRIBUTE_CONTRACT_RELATIVE_PATHS:
            with self.subTest(relative=relative):
                self.assertIn(Path(relative).name, spec)

    def test_package_without_attribute_contract_sources_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_package(root, include_attribute_contract=False)
            with self.assertRaisesRegex(RuntimeError, "attribute contract"):
                validate_package_directory(root)

    def test_complete_directory_and_zip_validate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "package"
            build_package(root)
            manifest = validate_package_directory(root)
            archive = Path(raw) / "package.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
                for path in root.rglob("*"):
                    if path.is_file():
                        output.write(path, path.relative_to(root).as_posix())
            zip_manifest = validate_package_zip(archive)
        self.assertEqual(manifest["silhouette"], zip_manifest["silhouette"])

    def test_tamper_missing_and_extra_files_fail_closed(self) -> None:
        for mutation, expected in (
            (lambda root: (root / "SourceAddonOptimizerWorker.exe").write_bytes(b"tampered"), "hash/size"),
            (lambda root: (root / "_internal/base_library.zip").unlink(), "contents differ"),
            (lambda root: (root / "unexpected.dll").write_bytes(b"extra"), "contents differ"),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                build_package(root)
                mutation(root)
                with self.assertRaisesRegex(RuntimeError, expected):
                    validate_package_directory(root)

    def test_unsorted_duplicate_and_unsafe_manifest_paths_fail_closed(self) -> None:
        for mutation, expected in (
            (lambda files: files.reverse(), "not bytewise sorted"),
            (lambda files: files.append(dict(files[0])), "duplicate"),
            (lambda files: files[0].update(path="../escape.dll"), "unsafe"),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                manifest_path = build_package(root)
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutation(manifest["files"])
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, expected):
                    validate_package_directory(root)

    def test_non_amd64_dll_is_rejected_even_when_hashes_are_self_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = build_package(root)
            dll_path = root / DLL_RELATIVE_PATH
            payload = bytearray(dll_path.read_bytes())
            pe_offset = struct.unpack_from("<I", payload, 0x3C)[0]
            struct.pack_into("<H", payload, pe_offset + 4, 0x014C)
            dll_path.write_bytes(payload)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            digest = _digest(dll_path)
            size = dll_path.stat().st_size
            for item in manifest["files"]:
                if item["path"] == DLL_RELATIVE_PATH:
                    item.update(sha256=digest, size=size)
            manifest["silhouette"].update(sha256=digest, size=size)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "not AMD64"):
                validate_package_directory(root)


if __name__ == "__main__":
    unittest.main()
