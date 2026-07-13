# DX90-only Garry's Mod runtime proof

Date: 2026-07-13
Status: approved autonomous R&D lane; opt-in and non-production until proven

## Decision

The existing DX80 omission experiment remains isolated from geometry savings and from the production selector. It may change from `pending` to `proven` only after an automated Garry's Mod run proves all six required capabilities against the exact five-family candidate manifest. Static decompile, StudioMDL, textmode startup, self-reported Lua booleans, or a screenshot without a paired control cannot authorize the lane.

## Disposable installation layout

The runner refuses to start when `gmod.exe` is already running or when any reserved path already exists. It stages the exact manifest-declared `.mdl`, `.vvd`, `.dx90.vtx`, `.phy`, and optional same-stem sidecars below a unique loose-content namespace:

`garrysmod/models/maximum_dx90_runtime/<run-id>/<family-id>/<stem>.*`

No `.dx80.vtx` is copied. The namespace prevents a same-stem DX80 file from the Workshop source from satisfying a missing lookup. Garry's Mod starts with both `-noaddons` and `-noworkshop`; the harness asserts those flags through `GetAddonStatus()` in both realms. A unique loose autorun Lua file is the only test bootstrap. Staged models, Lua, console output, and in-game data are removed in `finally`; pre-existing files are never overwritten or deleted.

## Runtime capabilities

Before model creation, both Lua realms read every staged sidecar from `GAME`, recompute SHA-256, compare it with the frozen manifest, and assert that the expected `.dx80.vtx` path does not exist.

The server creates each family as a physics prop and records:

- `dynamic_model_load`: valid model, valid spawned entity, and no error-model substitution;
- `physics`: valid movable physics object with finite positive mass and observed movement after an applied velocity;
- `damage`: an `EntityTakeDamage` observation for the exact entity and exact injected damage.

The client creates a separate clientside model for each family and records:

- `rendering`: a baseline PNG with the model hidden and a paired PNG from the same camera with the model explicitly drawn under a built-in debug material;
- `bodygroups_skins`: every declared bodygroup value and every skin can be set and read back; zero-variant models are recorded as not applicable, never invented as coverage;
- `animation`: at least one representative non-static sequence advances to a distinct cycle and produces a changed bone transform or paired rendered image.

The Python verifier independently hashes all reports and PNGs, parses PNG dimensions, computes paired pixel deltas, scans the complete console log for model/VTX/VVD load errors, and requires capability coverage exactly equal to the six-item contract. A timeout, crash, black/identical capture, absent sequence, missing realm report, hash drift, extra sidecar, or any evidence inconsistency leaves the experiment `pending` with the exact failure reason.

## Evidence boundary

Local evidence binds the candidate-manifest digest, launcher/Lua hashes, `gmod.exe` hash, Steam app-manifest hash and build ID, exact launch arguments, per-artifact runtime hashes, per-family capability observations, screenshot hashes and pixel metrics, and complete console-log hash. Only the strict verifier may convert this bundle into the smaller `RuntimeEvidence` consumed by `dx90_optional.py`.

This test establishes runtime compatibility only. It does not classify DX80 removal as geometry compression and does not authorize static-prop/VRAD use.
