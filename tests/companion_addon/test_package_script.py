from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE = REPOSITORY / "companion" / "sourceaddonoptimizer_companion"
SCRIPT = REPOSITORY / "scripts" / "package_companion.ps1"
EMBEDDED = (
    REPOSITORY
    / "GmodAddonCompressor-master"
    / "GmodAddonCompressor"
    / "Resources"
    / "SourceAddonOptimizerCompanion.zip"
)
WPF_PROJECT = EMBEDDED.parents[1] / "GmodAddonCompressor.csproj"


def package(source: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-SourceRoot",
            str(source),
            "-OutputPath",
            str(output),
            "-PackageVersion",
            "1.0.0",
        ],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_package_script_is_deterministic_and_manifest_verified(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_result = package(SOURCE, first)
    second_result = package(SOURCE, second)
    assert first_result.returncode == 0, first_result.stderr + first_result.stdout
    assert second_result.returncode == 0, second_result.stderr + second_result.stdout
    assert "Companion package validated" in first_result.stdout
    assert first.read_bytes() == second.read_bytes()

    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert names[0] == "companion-package-manifest.json"
        assert names[1:] == sorted(names[1:])
        assert all(name == name.lower() and name.isascii() for name in names)
        assert all(item.date_time == (2000, 1, 1, 0, 0, 0) for item in archive.infolist())
        manifest = json.loads(archive.read(names[0]))
        assert manifest["schemaVersion"] == 1
        assert manifest["packageVersion"] == "1.0.0"
        assert manifest["addonDirectory"] == "sourceaddonoptimizer_companion"
        declared = manifest["files"]
        assert [item["path"] for item in declared] == sorted(item["path"] for item in declared)
        assert names[1:] == ["sourceaddonoptimizer_companion/" + item["path"] for item in declared]
        for item in declared:
            payload = archive.read("sourceaddonoptimizer_companion/" + item["path"])
            assert item["bytes"] == len(payload)
            assert item["sha256"] == hashlib.sha256(payload).hexdigest()


def test_package_script_rejects_unsafe_or_undeclared_content(tmp_path: Path) -> None:
    uppercase = tmp_path / "uppercase"
    shutil.copytree(SOURCE, uppercase)
    (uppercase / "lua" / "Bad.lua").write_text("return false\n", encoding="utf-8")
    result = package(uppercase, tmp_path / "uppercase.zip")
    assert result.returncode != 0
    assert "lowercase" in (result.stderr + result.stdout).lower()

    forbidden = tmp_path / "forbidden"
    shutil.copytree(SOURCE, forbidden)
    (forbidden / "payload.exe").write_bytes(b"MZ")
    result = package(forbidden, tmp_path / "forbidden.zip")
    assert result.returncode != 0
    assert "extension" in (result.stderr + result.stdout).lower()

    duplicate_autorun = tmp_path / "duplicate-autorun"
    shutil.copytree(SOURCE, duplicate_autorun)
    (duplicate_autorun / "lua" / "autorun" / "second.lua").write_text(
        "SAO = SAO or {}\n", encoding="utf-8"
    )
    result = package(duplicate_autorun, tmp_path / "duplicate.zip")
    assert result.returncode != 0
    assert "autorun" in (result.stderr + result.stdout).lower()


def test_wpf_embeds_the_verified_deterministic_package(tmp_path: Path) -> None:
    assert EMBEDDED.is_file()
    project = WPF_PROJECT.read_text(encoding="utf-8")
    assert 'EmbeddedResource Include="Resources\\SourceAddonOptimizerCompanion.zip"' in project
    assert "GmodAddonCompressor.Resources.SourceAddonOptimizerCompanion.zip" in project
    rebuilt = tmp_path / "rebuilt.zip"
    result = package(SOURCE, rebuilt)
    assert result.returncode == 0, result.stderr + result.stdout
    assert rebuilt.read_bytes() == EMBEDDED.read_bytes()
