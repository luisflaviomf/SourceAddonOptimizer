# GMod Addon Optimizer
 
## Current features:

- Image compression (VTF, JPG, JPEG, PNG)
- Compressing audio files (WAV, MP3|Demo, OGG|Demo)
- LUA code minimization
- Model optimization (SourceAddonOptimizer: decompile + Blender optimize + compile)

## Requirements (audio)

- Audio compression (WAV/MP3/OGG) uses FFmpeg (bundled and extracted at runtime).
- If FFmpeg cannot be extracted, audio processing is disabled and the app shows a warning.

## Requirements (models)

- Blender (4.x/5.x) installed
- `studiomdl.exe` (from Garry's Mod install)

In the app, go to **Models** and set paths via **Auto-detect** or **Browse**.

## Settings

The app generates its real settings file at runtime in:

`%LOCALAPPDATA%\GmodAddonOptimizer\settings.json`

The repo includes `settings.example.json` only as a reference (do not edit it expecting the app to read it).
It contains two examples: a minimal one (paths + preset + suffix) and a full one (custom params).

## Tools extraction

The SourceAddonOptimizer tools are extracted to:

`%LOCALAPPDATA%\GmodAddonOptimizer\tools\SourceAddonOptimizer\<TOOL_VERSION>\`

This avoids admin permissions and keeps the install self-contained.

The worker zip must exist in the project at:

`GmodAddonCompressor/Resources/SourceAddonOptimizer.win-x64.zip`

At runtime it is embedded via `GmodAddonCompressor/Properties/Resources.resx` and extracted into `%LOCALAPPDATA%` by `ToolExtractionSystem`.
No external zip is needed in the publish output.

## Theme

All theme brushes and styles are centralized in `GmodAddonCompressor/App.xaml`.
To tweak the palette, edit the resources there (e.g. `AppBackground`, `Surface1`, `Surface2`, `Surface3`, `Border`, `TextPrimary`, `TextSecondary`, `TextMuted`, `TextDisabled`, `Accent`, `AccentHover`, `AccentPressed`, `AccentForeground`).

## Quick test

1) Open the app and select an addon folder.  
2) Go to **Models** and set Blender + StudioMDL paths.  
3) Click **Optimize Models** (or use **Pipeline** to run models + compress in one click).

## Release (win-x64)

```bat
cd GmodAddonCompressor
dotnet publish -c Release -r win-x64 --self-contained false
```

# BE SURE TO INSTALL THIS PACKAGE FOR THE APP TO WORK CORRECTLY:

https://dotnet.microsoft.com/en-us/download/dotnet/6.0/runtime
