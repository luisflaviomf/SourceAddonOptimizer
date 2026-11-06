# Companion controlled-gallery smoke test

Date: 2026-08-16 (America/Sao_Paulo)

## Scope

This smoke test exercises the real Garry's Mod client renderer, the bounded visual queue, fixed camera views, PNG capture, atomic visual-result export, and the strict Python bundle reader. It uses the base-game `models/props_c17/oildrum001.mdl` so the test does not depend on a Workshop addon.

Environment:

- Garry's Mod build: `2026.08.13` (`10119`)
- Map/gamemode: `gm_flatgrass` / `sandbox`
- Platform: Windows, windowed, workshop mounting disabled
- Windows DPI during the run: 96 DPI / 100%
- Views: front, side, rear, isometric
- Gallery: neutral dark background, fixed lighting, 35-degree FOV, bounding-box camera fit

## Rejected first attempt and fixes

Run `72000000-0000-4000-8000-000000000001` was rejected: all four 1,627-byte images contained only the gallery background. The investigation found and fixed three real-engine defects:

1. `DModelPanel:SetBackgroundColor` does not exist in Garry's Mod.
2. Replacing `DModelPanel.Paint` and calling `DrawModel` directly bypassed the panel's 3D render context.
3. A hidden Validate tab could leave a capture waiting indefinitely.

The renderer now paints the neutral background and then invokes the original `DModelPanel.Paint` when its entity is valid. Starting visual validation selects the Validate tab, and capture has a bounded 120-frame paint wait followed by at most one retry.

## Passing 1280x720 run

Run ID: `72000000-0000-4000-8000-000000000002`

- Result bundle: `<GARRYSMOD_ROOT>/garrysmod/data/sourceaddonoptimizer/validation_results/72000000-0000-4000-8000-000000000002/visual-0001`
- Screenshot folder: `<GARRYSMOD_ROOT>/garrysmod/data/sourceaddonoptimizer/screenshots/72000000-0000-4000-8000-000000000002`
- Overall status: `PASS`
- Screenshots: 4/4 `PASS`, 35,680-41,438 bytes
- Capture wall time reported by the addon: 0.3003 seconds
- Verification: the strict Python reader accepted the bundle; every on-disk PNG size and SHA-256 matched its declaration
- Visual inspection: barrel is visible, centered, fully framed, and consistently lit in all four views

## Passing 1920x1080 run

Run ID: `10800000-0000-4000-8000-000000000001`

- Result bundle: `<GARRYSMOD_ROOT>/garrysmod/data/sourceaddonoptimizer/validation_results/10800000-0000-4000-8000-000000000001/visual-0001`
- Screenshot folder: `<GARRYSMOD_ROOT>/garrysmod/data/sourceaddonoptimizer/screenshots/10800000-0000-4000-8000-000000000001`
- Overall status: `PASS`
- Screenshots: 4/4 `PASS`, 114,847-126,934 bytes
- Capture wall time reported by the addon: 0.3043 seconds
- Verification: the strict Python reader accepted the bundle; every on-disk PNG size and SHA-256 matched its declaration
- Visual inspection: barrel is visible, centered, fully framed, and consistently lit in all four views

No new Companion or Lua errors appeared in the final engine log. The temporary smoke autorun was removed, all Garry's Mod processes launched for the test were stopped, and the successful evidence folders were preserved.

## Scaling gap

The 150% Windows-scaling case was **not executed**. This machine was configured at 96 DPI / 100%; changing the global desktop scale would disrupt the active user session and can require sign-out. The implementation avoids hard-coded desktop coordinates, derives layout from `ScrW()`, `ScrH()`, and `ScreenScale()`, and has automated contract coverage for that behavior, but a real 150% DPI smoke remains an explicit release-validation item rather than a claimed pass.
