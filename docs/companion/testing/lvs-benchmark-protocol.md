# LVS profile-assisted Maximum v2 benchmark protocol

Date fixed before either acceptance lane: 2026-08-16.

## Question and lanes

This benchmark answers one narrow question: does an explicitly selected, real-game
`sao-runtime-profile-v1` improve protection of observed regions without regressing
the existing no-profile Maximum v2 path?

- A — `maximum-no-profile`: current Maximum v2, no runtime-profile argument.
- B — `maximum-profile`: byte-identical command plus
  `--maximum-runtime-profile <approved bundle>`.

The lanes use the same current checkout, frozen input family, Blender, StudioMDL,
native silhouette DLL, worker options, and framework resolver. No OS cache flush is
performed. Each lane receives one unmeasured warm-up followed by three measured
runs. Order is paired and alternates A/B, B/A, A/B per family.

## Families selected before execution

The acceptance set contains five of the eight frozen corpus families:

| Family | Partition | Why it is in the set |
|---|---|---|
| `caterham_620r` | development | Exposed wheels/tires, steering wheel, headlights, pipes, arches, many detachable bodygroups |
| `ferrari_365_fullrig` | development | Rigged/skinned full body, wheel, glass/lenses, lights, exhausts, curved panels |
| `ford_fairlane` | development | Large detailed family, wheels, glass, lights, interior, skins and extensive bodygroups |
| `dodge_charger` | holdout | Large holdout with blower, wheel, steering, glass, lights, exhaust, interior and material/state boundaries |
| `pontiac_transam_wheel` | holdout | Isolated high-density circular wheel/tire and cylinder stress case |

This set spans 145 frozen files and 63,545,189 input bytes. Its source paths,
counts, byte totals and SHA-256 tree fingerprints remain enforced by
`benchmarks/lvs_models_adaptive/corpus.json` before every copy. The full source
addon and framework are read-only inputs; neither is copied into the optimized
addon as a dependency.

## Runtime profile

The profile was recorded in Garry's Mod build `2026.08.13 (10119)` on
`gm_flatgrass`. The test driver spawned all eight corpus vehicles and explicitly
focused their primary entities. It is a bounded 15-second driver and is removed
from the installed addon after use.

- Run ID: `fc0aaeb1-7271-6a08-744f-142507b8ef4f`
- Bundle index SHA-256: `05080fb25731c218cef9e145fdc3d8243d673ea9ea7f474c363513d38911e314`
- Models: 276; focused models: 9
- Profiler average: 0.020151 ms; p95: 0.1 ms; maximum slice: 3.8565 ms
- Queue high-water: 609
- Truncation: zero models, materials, Lua errors, and bytes

These values pass the fixed 0.5/2/5 ms profiler limits. The copied, validated
profile and all raw runs live under
`C:\SAOCompanionEvidence\2026-08-16-v1`; that location is outside the repository,
the source addon, the framework, and `%LOCALAPPDATA%\GmodAddonOptimizer\work`.

## Recorded evidence

`scripts/benchmark_companion.py` records the immutable plan before starting a
lane, environment/tool hashes, Git state, active power plan, free disk space,
exact commands, stdout, wall/CPU/RSS measurements, output size excluding DX80,
triangle/vertex counts, optimized/preserved/failed models, render/evaluation/
compile counts, integrity failures, selected SMD hashes, compiled MDL/VVD/DX90/
PHY hashes, and the Maximum report. Runs are resumable only when the existing
plan compares exactly.

## Acceptance gates

- A must remain behaviorally unchanged and valid.
- No family-wide preservation and no new full-family candidate/render.
- B median wall-time overhead no greater than 10%.
- B output-size increase no greater than 5% per family and in aggregate.
- No visual regression in matched rounded regions at native resolution.
- DX90/VVD/PHY integrity, zero final DX80, and no new external dependency.
- Every cap and truncation count is explicit.

The one-family Pontiac pilot is diagnostic only and is not included in acceptance
medians. Its measured outputs were byte-identical; A was 50.76 s and B 57.72 s,
while the warm-ups were 68.56 s and 68.42 s. This variance is why the decision is
based on the complete three-run paired set.

## Rejected and retained scheduling calibration

The first full-family warm-up rejected the original 128-triangle direct-evidence
threshold. Caterham B scheduled 29 extra regions, restored 21 to original,
increased output from 3,250,167 to 3,524,095 bytes (+8.43%), and increased wall
time from 138.6 to 211.8 seconds (+52.8%). No acceptance runs used that setting.
Raw evidence is retained under `acceptance-5`.

The allowed scheduling threshold was then raised to 1,024 triangles without
changing either approved risk formula. The focused Caterham pilot scheduled only
two substantial 1,082/1,124-triangle regions, recovered neither, produced
3,250,165 bytes and 36,859 triangles in 126.26 seconds, retained one targeted
render and passed all compiled integrity checks. The setting was nevertheless
rejected by the next development-family warm-up: Ferrari scheduled four
1,292--4,624-triangle material islands, recovered all four, and grew from
5,866,665 to 6,246,994 bytes (+6.48%), beyond the 5% acceptance limit. Raw
evidence is retained under `acceptance-5-threshold-1024`.

The retained threshold is 5,000 triangles. This leaves the approved risk
formula and the 0.40 model-evidence baseline unchanged, so runtime evidence still
tightens every region already selected by geometry. It only creates a new
validator for a visually dominant region instead of assuming that a focused
model makes each small or medium material island visible. The Ferrari rejection
case then matched 3 models, 37 materials and 2,182 regions, scheduled zero extra
validators, performed zero recoveries and one compile, and produced 5,867,005
bytes and 72,648 triangles in 269.15 seconds. That is +340 bytes (+0.006%) against
the previous no-profile warm-up while retaining the profile-adjusted regional
targets. Raw evidence is under `tuning-threshold-5000-ferrari`. The final paired
set uses 5,000 and is stored in a fresh evidence root.
