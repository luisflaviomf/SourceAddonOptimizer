# -*- mode: python ; coding: utf-8 -*-

import json
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

# exact_distribution authenticates its own on-disk sources. PYZ bytecode alone is
# insufficient: preserve exact source bytes, Lua templates and pins under _MEIPASS.
exact_pins = repo_root / "exact_distribution" / "vendor-pins.json"
exact_files = [exact_pins, *sorted((repo_root / "exact_distribution").glob("*.py"))]
exact_files += [repo_root / relative for relative in json.loads(exact_pins.read_text(encoding="utf-8"))["files"]]
exact_datas = [(str(path), str(path.relative_to(repo_root).parent)) for path in exact_files]

a = Analysis(
    [str(repo_root / "worker" / "worker_main.py")],
    pathex=[str(repo_root)],
    binaries=[
        (str(repo_root / "CrowbarCommandLineDecomp.exe"), "."),
        (
            str(maximum_package / "native" / "bin" / "win-x64" / "meshopt_bridge.dll"),
            "maximum_optimizer/native/bin/win-x64",
        ),
        (str(repo_root / "tools" / "vtx" / "meshopt_stripify.dll"), "."),
        # Models "Tela" mode: meshoptimizer v1.2 C API (third_party/meshoptimizer)
        (str(repo_root / "tools" / "meshopt" / "meshopt_simplify.dll"), "."),
        # Rust hot paths (native/gmo_native): bit-identical replacements loaded by texlab.native
        (str(repo_root / "tools" / "native" / "gmo_native.dll"), "."),
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
        (str(repo_root / "tools" / "vtx" / "LICENSE.meshoptimizer.txt"), "."),
        # Models "Tela" profile "stream": Lua runtime + light-probe model written into optimized addons
        (str(repo_root / "model_stream" / "runtime.lua"), "model_stream"),
        (str(repo_root / "model_stream" / "probe" / "probe_v1.mdl"), "model_stream/probe"),
        (str(repo_root / "model_stream" / "probe" / "probe_v1.vvd"), "model_stream/probe"),
        (str(repo_root / "model_stream" / "probe" / "probe_v1.dx90.vtx"), "model_stream/probe"),
        (str(repo_root / "model_stream" / "probe" / "probe_v1.vmt"), "model_stream/probe"),
        (str(repo_root / "model_stream" / "probe" / "probe_v1_normal.vtf"), "model_stream/probe"),
    ] + exact_datas,
    hiddenimports=collect_submodules("maximum_optimizer") + collect_submodules("exact_distribution") + [
        # texlab screen-aware VTF engine: numpy-only core (lab modules with torch/numba stay out)
        "model_screen", "model_screen.cli", "model_screen.simplify", "model_screen.binary",
        "model_screen.uvtol", "model_screen.meshopt", "model_screen.remesh", "model_screen.mirror",
        "model_screen.gridsearch", "vvd_engine_exact",
        "model_stream", "model_stream.encoder", "model_stream.coarse",
        "texlab", "texlab.screen_compress", "texlab.npsolve", "texlab.bcnp", "texlab.vtf",
        "texlab.density", "texlab.materials", "texlab.source_model", "texlab.allocate", "texlab.plan",
        "texlab.native",
        "PIL.Image",
        "PIL.ImageChops",
        "PIL.ImageDraw",
        "PIL.ImageFilter",
        "PIL._imaging",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "numba", "llvmlite", "flip_evaluator", "texlab.render", "texlab.evaluate",
              "texlab.downscale", "texlab.build_opt"],
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
