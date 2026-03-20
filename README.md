# GModAddonOptimizer

Also known as **SourceAddonOptimizer**.

Desktop tool for Garry's Mod / Source Engine addons with three independent workflows:

- Full addon optimization pipeline
- Quick model preview/render validation
- Recursive mass extraction of addon archives

## What it does

Optimization workflow:

- Step 1: Decompile `.mdl` files with Crowbar CLI into a work folder and back up `.phy`
- Step 2: Run Blender in background to import SMD/DMX, apply optimization, and generate `*_OPT.qc`
- Step 3: Compile `*_OPT.qc` with `studiomdl.exe`, restore `.phy` when needed, and build the final addon output folder

Mass unpack workflow:

- Scan a root folder recursively
- Find `.gma` and `.bin` archives inside nested subfolders
- Extract each supported addon automatically next to the original file
- Write the extracted contents to `<arquivo>_extraido`

Supported archive behavior:

- `.gma`: extracted directly with `gmad.exe`
- `.bin`: decompressed first; if the payload is a `GMAD` archive, it is extracted normally

If a `.bin` payload is not `GMAD`, it is reported as unsupported and the batch continues.

## Requirements

- Windows + Python **3.10+**
- `PySide6` for the GUI
- Blender (4.x/5.x) installed and callable, or pass `--blender`
  - Valve Source import/export addon enabled: `io_scene_valvesource` (Source Tools)
- Crowbar CLI decompiler (`CrowbarCommandLineDecomp.exe`)
  - This repo includes `CrowbarCommandLineDecomp.exe`, or set `CROWBAR_EXE`
- `studiomdl.exe` from Garry's Mod or another Source SDK path
- `gmad.exe` from Garry's Mod for addon extraction

Typical Garry's Mod paths:

- `...\GarrysMod\bin\studiomdl.exe`
- `...\GarrysMod\bin\gmad.exe`

## Install

```bash
python -m pip install -r requirements.txt
```

## Build (Windows)

One-click build:

```bat
build_release.cmd
```

What it does:

- runs the existing PyInstaller build flow for GUI + worker
- bundles the worker inside the GUI dist folder

Output:

- final app folder: `dist\GModAddonOptimizer\`
- main executable: `dist\GModAddonOptimizer\GModAddonOptimizer.exe`

Notes:

- this build follows the original project pattern
- the GUI executable and the worker executable remain separate
- the worker is copied into the GUI dist folder automatically
- `CrowbarCommandLineDecomp.exe` is bundled with the worker build

## Run (GUI)

```bash
python gui/main.py
```

Tabs available in the GUI:

- `Build`: full optimization pipeline
- `Teste rapido`: quick preview/render validation for a single model
- `Descompactar addons`: recursive scan + mass extraction of addon archives

### Using the `Descompactar addons` tab

1. Set `Pasta raiz` to the folder you want to scan recursively.
2. Set `gmad.exe` manually or click `Auto-detect`.
3. Click `Escanear` to classify the archives found.
4. Review the summary:
   - `Encontrados`
   - `GMA`
   - `BIN suportados`
   - `BIN nao suportados`
5. Choose the behavior for existing output folders:
   - `Pular existentes`
   - `Sobrescrever existentes`
   - `Falhar se existir`
6. Click `Descompactar encontrados`.
7. Follow the live log, progress bar, and final summary in the same tab.

The tab supports:

- `scan-only`
- persisted fields via settings
- cancellation during scan and extraction
- summary reload from `unpack_summary.json`

## Run Optimization (CLI)

```bash
python build_optimized_addon.py "C:\path\to\your_addon"
```

Common options:

- `--suffix "_optimized"`: output folder suffix
- `--ratio 0.50`: decimate ratio (`1.0` = no decimate)
- `--merge 0.0`: merge-by-distance (`0` disables)
- `--autosmooth 45`: degrees
- `--format smd` or `--format dmx`
- `--blender "C:\path\to\blender.exe"`
- `--studiomdl "C:\path\to\studiomdl.exe"`
- `--strict`: abort if any compile failures happen
- `--resume-opt`: skip QCs already optimized

## Run Mass Unpack (CLI)

Scan only:

```bash
python worker/worker_main.py unpack "C:\path\to\root" --scan-only
```

Full extraction:

```bash
python worker/worker_main.py unpack "C:\path\to\root" --gmad "C:\Program Files (x86)\Steam\steamapps\common\GarrysMod\bin\gmad.exe"
```

Common unpack options:

- `--existing skip`
- `--existing overwrite`
- `--existing fail`
- `--work "C:\path\to\custom_workdir"`

## Output

Optimization output:

- Work directory: `work/<addon_name><suffix>/`
- Final addon directory: `<addon_name><suffix>/` next to the original addon

Mass unpack output:

- Work directory: `work/<root_name>_unpack_runs/<timestamp>/`
- Summary file: `unpack_summary.json`
- Extracted addon directory: `<arquivo>_extraido` next to each `.gma` / `.bin` source file

## Scripts in this repo

- `build_optimized_addon.py`: optimization pipeline entrypoint
- `batch_decompile_organize.py`: Crowbar batch decompile + organization + `.phy` backup
- `batch_optimize_qc.py`: Blender optimization step
- `batch_compile_opt_qc.py`: `studiomdl.exe` batch compile + optional `.phy` restore
- `render_previews.py`: Blender headless preview renders for the quick test tab
- `batch_unpack_addons.py`: recursive scan, `.bin` decode, `gmad.exe` extraction, summary generation
- `worker/worker_main.py`: worker dispatcher for `build`, `preview`, and `unpack`
