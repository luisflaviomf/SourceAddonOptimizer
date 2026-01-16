# GModAddonOptimizer

Also known as **SourceAddonOptimizer**.

End-to-end optimizer for Garry's Mod / Source Engine addons: **decompile** models from an addon, **optimize** meshes in Blender (headless), **recompile** with `studiomdl.exe`, then **package** a new, optimized addon folder.

## What it does

- Step 1: Decompile `.mdl` files with Crowbar CLI into a work folder (and back up `.phy` for restore later)
- Step 2: Run Blender in background to import SMD/DMX, apply optimization (decimate/merge/autosmooth), and generate `*_OPT.qc`
- Step 3: Compile `*_OPT.qc` with `studiomdl.exe`, restore `.phy` (optional), and build the final addon output folder

## Requirements

- Windows + Python **3.10+**
- `PySide6` (only needed for the GUI)
- Blender (4.x/5.x) installed and callable (or pass `--blender`)
  - Valve Source import/export addon enabled: `io_scene_valvesource` (Source Tools)
- Crowbar CLI decompiler (`CrowbarCommandLineDecomp.exe`)
  - This repo includes `CrowbarCommandLineDecomp.exe`, or set `CROWBAR_EXE` to your own path
- `studiomdl.exe` (typically from your Garry's Mod install at `...\GarrysMod\bin\studiomdl.exe`)

## Install

```bash
python -m pip install -r requirements.txt
```

## Run (GUI)

```bash
python gui/main.py
```

## Run (CLI, recommended)

```bash
python build_optimized_addon.py "C:\path\to\your_addon"
```

Common options:

- `--suffix "_optimized"`: output folder suffix
- `--ratio 0.50`: decimate ratio (1.0 = no decimate)
- `--merge 0.0`: merge-by-distance (0 disables)
- `--autosmooth 45`: degrees
- `--format smd` or `--format dmx`
- `--blender "C:\path\to\blender.exe"`
- `--studiomdl "C:\path\to\studiomdl.exe"`
- `--strict`: abort if any compile failures happen
- `--resume-opt`: skip QCs already optimized (useful when resuming)

## Output

- Work directory: `work/<addon_name><suffix>/` (logs, decompiled sources, backups)
- Final addon directory: `<addon_name><suffix>/` next to your original addon (or timestamped if it already exists)

## Scripts in this repo

- `build_optimized_addon.py`: orchestrates the whole pipeline
- `batch_decompile_organize.py`: Crowbar batch decompile + organization + `.phy` backup
- `batch_optimize_qc.py`: Blender optimization step (run via Blender)
- `batch_compile_opt_qc.py`: `studiomdl.exe` batch compile + optional `.phy` restore
- `render_previews.py`: Blender headless preview renders (used by the GUI “quick test”)

