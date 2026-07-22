# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


block_cipher = None

try:
    spec_dir = Path(SPECPATH).resolve()
except NameError:
    spec_dir = Path.cwd() / "pyinstaller"

repo_root = spec_dir.parent
if not (repo_root / "worker" / "worker_main.py").exists():
    repo_root = Path.cwd()

maximum_package = repo_root / "maximum_optimizer"

a = Analysis(
    [str(repo_root / "worker" / "worker_main.py")],
    pathex=[str(repo_root)],
    binaries=[
        (str(repo_root / "CrowbarCommandLineDecomp.exe"), "."),
        (
            str(maximum_package / "native" / "bin" / "win-x64" / "meshopt_bridge.dll"),
            "maximum_optimizer/native/bin/win-x64",
        ),
    ],
    datas=[
        (str(repo_root / "build_optimized_addon.py"), "."),
        (str(repo_root / "batch_decompile_organize.py"), "."),
        (str(repo_root / "batch_optimize_qc.py"), "."),
        (str(repo_root / "batch_optimize_selective_policy.py"), "."),
        (str(repo_root / "batch_optimize_round_parts_policy.py"), "."),
        (str(repo_root / "optimize_edge_transfer_policy_v1.py"), "."),
        (str(repo_root / "optimize_fidelity_partition_policy_v1.py"), "."),
        (str(repo_root / "batch_optimize_parallel.py"), "."),
        (str(repo_root / "batch_compile_opt_qc.py"), "."),
        (str(repo_root / "render_previews.py"), "."),
        (str(maximum_package / "mesh_attributes.py"), "maximum_optimizer"),
        (str(maximum_package / "meshopt_bridge.py"), "maximum_optimizer"),
        (
            str(maximum_package / "profiles" / "maximum-adaptive-v2.json"),
            "maximum_optimizer/profiles",
        ),
    ],
    hiddenimports=collect_submodules("maximum_optimizer") + [
        "PIL.Image",
        "PIL.ImageChops",
        "PIL.ImageDraw",
        "PIL.ImageFilter",
        "PIL._imaging",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GModAddonOptimizerWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="GModAddonOptimizerWorker",
)
