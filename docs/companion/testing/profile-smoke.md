# Companion runtime-profile smoke test

Date: 2026-08-16 (America/Sao_Paulo)

## Scope

This is the first real-engine smoke test of the Companion as an unpacked addon. It validates session authorization, lifecycle profiling, an occupied base-game vehicle, atomic bundle export, and cross-language bundle import. It is not the later LVS performance/quality benchmark.

Environment:

- Garry's Mod build: `2026.08.13`
- Realm: single-player
- Map/gamemode: `gm_flatgrass` / `sandbox`
- Workshop mounting disabled for isolation
- Addon installed temporarily as a folder below `<GARRYSMOD_ROOT>/garrysmod/addons/`

Launch transcript:

```text
gmod.exe -windowed -w 640 -h 480 -novid -nosound -nojoy -noworkshop -console +sv_lan 1 +map gm_flatgrass
```

The temporary smoke driver called the same public profiler boundary used by the console commands `sao_profile_start` and `sao_profile_stop`, spawned `prop_vehicle_jeep` with `models/buggy.mdl`, and called `Player:EnterVehicle` before export.

## Rejected writer design discovered in-engine

The first two profile runs started, spawned the vehicle, and entered it, but export returned `chunk write failed`. An isolated DATA-sandbox probe established:

```text
file.Open("open.partial", "wb", "DATA")                  -> refused
file.Open("open.partial.json", "wb", "DATA")             -> success, 5 bytes
file.Rename("rename-source.json", "renamed.json")         -> success
file.Rename("ready-source.json", "renamed.ready")         -> refused
```

Consequently the pre-implementation names `*.partial` and `complete.ready` cannot be produced by Garry's Mod. Before release, the v1 protocol was corrected consistently to temporary `*.partial.json` files and final `complete.ready.json`. This matches the documented restrictions of the [Garry's Mod file library](https://wiki.facepunch.com/gmod/file) and the behavior observed in the installed engine.

## Passing run

Run ID: `99231068-8a14-2f0a-12eb-02c1666fcac1`

```text
start_profile   PASS  99231068-8a14-2f0a-12eb-02c1666fcac1
spawn_vehicle  PASS  models/buggy.mdl
enter_vehicle  PASS  models/buggy.mdl
stop_and_export PASS 99231068-8a14-2f0a-12eb-02c1666fcac1
```

The standard-library Python reader then verified the real game-produced index, declared byte sizes, SHA-256 hashes, completion marker, schema, and payload:

```text
chunks: 1
models: 250
materials: 23
lua_errors: 0
files: chunk-0001.json, complete.ready.json, run-index.json
chunk bytes: 56,105
index bytes: 594
completion bytes: 160
```

The `models/buggy.mdl` record was exported at `focused` evidence with skin `0`, named bodygroups `Body=0` and `tau_cannon=0`, and both base buggy materials.

Profiler measurements for the 5.176-second smoke session:

```text
samples: 334
average: 0.0052 ms
p95 approximation: 0.1 ms
maximum slice: 0.7095 ms
queue high-water: 91
truncation: models=0, materials=0, lua_errors=0, bytes=0
```

These numbers pass the provisional 0.5/2/5 ms profiler thresholds for this small smoke scenario. They do not replace the required representative LVS benchmark.

## Cleanup

The test-only driver and manually copied addon folder were removed after validation. The successful compact profile bundle remains under `<GARRYSMOD_ROOT>/garrysmod/data/sourceaddonoptimizer/profiles/99231068-8a14-2f0a-12eb-02c1666fcac1` as local evidence; failed partial runs and probe artifacts were removed.
