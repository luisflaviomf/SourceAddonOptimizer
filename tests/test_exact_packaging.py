"""Exercise packaging configuration without building or replacing release artifacts."""
import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def test_worker_spec_distributes_actual_authenticated_sources_and_templates(monkeypatch):
    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.collect_submodules = lambda package: [package + ".cli", package + ".builder", package + ".runtime", package + ".codecs"]
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    captured = {}
    def analysis(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pure=[], zipped_data=[], scripts=[], binaries=[], zipfiles=[], datas=kwargs["datas"])
    runpy.run_path(str(ROOT / "pyinstaller/worker.spec"), init_globals={
        "SPECPATH": str(ROOT / "pyinstaller"), "Analysis": analysis,
        "PYZ": lambda *a, **k: None, "EXE": lambda *a, **k: None, "COLLECT": lambda *a, **k: None})
    distributed = {str(Path(destination) / Path(source).name).replace("\\", "/"): Path(source)
                   for source, destination in captured["datas"]}
    for name in ("builder.py", "runtime.py", "codecs.py", "cli.py", "vendor-pins.json"):
        relative = "exact_distribution/" + name
        assert distributed[relative].read_bytes() == (ROOT / relative).read_bytes()
    pins = json.loads((ROOT / "exact_distribution/vendor-pins.json").read_bytes())
    for relative, pin in pins["files"].items():
        assert hashlib.sha256(distributed[relative].read_bytes()).hexdigest() == pin["sha256"]
    assert "exact_distribution.cli" in captured["hiddenimports"]


def test_packager_staleness_tracks_pinned_code_templates_and_product_sources(tmp_path):
    # Load only function definitions from the real script. Never execute its
    # PyInstaller, staging cleanup, ZIP replacement or publishing entrypoint.
    script_path = str(ROOT / "pyinstaller/package_wpf_tools.ps1").replace("'", "''")
    worker_path = str(tmp_path / "worker.exe").replace("'", "''")
    script = f"""
    $ErrorActionPreference = 'Stop'
    $tree = [System.Management.Automation.Language.Parser]::ParseFile('{script_path}', [ref]$null, [ref]$null)
    foreach ($statement in $tree.EndBlock.Statements) {{
        if ($statement -is [System.Management.Automation.Language.FunctionDefinitionAst]) {{ Invoke-Expression $statement.Extent.Text }}
    }}
    [System.IO.File]::WriteAllText('{worker_path}', 'test')
    (Get-Item -LiteralPath '{worker_path}').LastWriteTimeUtc = [DateTime]::UtcNow.AddDays(1)
    $fresh = Test-WorkerRebuildNeeded -WorkerExe '{worker_path}'
    (Get-Item -LiteralPath '{worker_path}').LastWriteTimeUtc = [DateTime]::MinValue.AddYears(2000)
    $stale = Test-WorkerRebuildNeeded -WorkerExe '{worker_path}'
    @{{ fresh = $fresh; stale = $stale; files = @(Get-WorkerSourceFiles | ForEach-Object {{ $_.FullName }}) }} | ConvertTo-Json -Compress
    """
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["fresh"] is False and value["stale"] is True
    tracked = {Path(path).relative_to(ROOT).as_posix() for path in value["files"]}
    assert {"exact_distribution/builder.py", "exact_distribution/runtime.py", "exact_distribution/vendor-pins.json"} <= tracked
    assert set(json.loads((ROOT / "exact_distribution/vendor-pins.json").read_bytes())["files"]) <= tracked
