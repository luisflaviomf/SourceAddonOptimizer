# LVS model benchmark corpus

This directory freezes the ten model families named by the approved LVS design. `corpus.json`
contains only POSIX paths relative to environment-provided roots, byte sizes and SHA-256 hashes.
It contains no workstation path. The first five ordered families are the pressure partition; all
ten, in the same order, are the full partition.

## Required roots

- `LVS_SOURCE_ROOT`: the successful decompile `src` directory.
- `LVS_ORIGINAL_MODELS_ROOT`: the original addon's `models` directory.
- `LVS_BLENDER_MODELS_ROOT`: existing `b050/compiled/models` directory.
- `LVS_FIDELITY_MODELS_ROOT`: existing `f050/compiled/models` directory.

Loading the corpus fails closed if an environment variable is absent, a root is not absolute, a
path escapes its root, any path component is a symlink/reparse point, a file hash or size differs,
or a model's exact declared Source sidecar set differs. The source directory for each QC is also
an exact hashed manifest.

## Reproduce the committed metadata

Run `benchmark_lvs_models.py freeze` with all four root arguments to regenerate `corpus.json`.
Then set the four environment variables and run:

```text
python benchmark_lvs_models.py import-baselines
```

The import reads and verifies existing artifacts; it does not rerun Blender or Fidelity. Its
canonical `baseline.json` keeps `original`, `control`, `blender`, `fidelity` and `experiment`
lanes distinct. `.dx80.vtx` bytes are additionally reported as `dx80_optional` and excluded from
the geometry-comparable byte total. Reports remain `unverified` and make no quality claim until
all structural, visual and runtime gates pass.

## Strict control roundtrip

`generate-control` is restricted to this fixed ten-family corpus. It privately copies the declared
source files into `.superpowers/lvs-task2-control`, calls StudioMDL directly on the unchanged QC,
does not invoke the existing QC/SMD autofix helpers, and records either the compiler result or an
explicit failure. It never edits the decompile tree. Example:

```text
python benchmark_lvs_models.py generate-control --studiomdl <studiomdl.exe>
```

After the run, `record-control` verifies and records each generated sidecar hash plus explicit
compiler failures in `control.json`. The StudioMDL location is represented by `STUDIOMDL_EXE`;
only its version, size and SHA-256 are committed.

Large compiled binaries and control workspaces are deliberately not committed.

## Fixed-topology smoothing experiment

`smoothing_fixed_v1.json` is the immutable Task 4 lane. The Blender-side runner is
`run_smoothing_fixed_v1.py`; it operates only on an isolated copy of one family, rebuilds the
same triangle indices/positions/UV/material/skin payload, reconstructs sharp boundaries from
canonical positions and intended corner normals, and applies Blender 5 custom normals per loop.
The fixed-topology SMD contract is ordered and corner-exact: triangle order/winding, triangle
material, corner ordinal, UV, bone/link/weight payload and every other non-normal value must match.
Only position components permit the documented Source Tools float32 serialization equivalence of
at most `0.000002`; normals are restored one-to-one from the corresponding original corner and are
never selected by nearest attribute key or collapsed across duplicate corners.

The lane remains `unverified`: the Pontiac wheel compiled byte-for-byte equal to the strict
control, while Dodge Charger was rejected before compile by the ordered corner/bone gate. Textures,
Task 8 renders and runtime validation were not available/run, so this record makes no quality
claim and the strategy is not a winner. The wheel record embeds every candidate/control artifact
path, size and SHA-256 plus a recomputable canonical bundle digest; its strict parser rejects any
path, size, hash, digest, membership, ordering or equality drift.

## Meshoptimizer direct evidence

`meshopt_direct_v1.json` is schema v2 evidence for all five direct ratios. It contains all three
visual SMD source/raw/restored hashes and locked/achieved metrics per ratio, plus portable
candidate/control compiled sidecar path, size and SHA-256 manifests. Regenerate and verify it with:

```text
python benchmarks/lvs_models/build_meshopt_direct_v1.py
python -m unittest tests.maximum_optimizer.test_direct_evidence -v
```

The builder rehashes every ignored artifact. The parser recomputes the canonical digest and keeps
quality `unverified`; this lane does not authorize a winner.

## Compiler-aware Blender adaptive checkpoint

`blender_adaptive_v1.json` freezes the Task 7 wheel and Dodge Charger checkpoints. Both compile
smaller than the frozen `b050` artifacts: wheel `502,779` vs `633,089` bytes (-20.58%), and Charger
`13,326,209` vs `14,738,984` bytes (-9.59%). Actual StudioMDL compiled bytes select candidates;
the `82V+12T` proxy is diagnostic only and is checked against VVD plus both VTX variants. The
record binds tool, implementation, candidate-metrics, payload, compile-log and sidecar hashes.
Charger preserves exactly two visual
SMDs after fail-closed appearance checks (`roof_paint.smd` and `body29_model0.smd`); their source
and output hashes are identical in the evidence.

This is a positive compiled-byte result, not a promoted winner. Only one pressure family plus the
wheel checkpoint ran. Wheel QC-aware textures resolved, but bodygroups occlude internal detail.
The Charger render attempt produced no accepted manifest because its aggregate geometry metric
fails closed on an original degenerate triangle. Thresholds and runtime remain uncalibrated, so
quality is explicitly `unverified`.

Regenerate from ignored local artifacts by setting `LVS_TASK7_EVIDENCE_ROOT`,
`LVS_BLENDER_MODELS_ROOT`, `BLENDER_EXE` and `STUDIOMDL_EXE`, then run:

```text
python benchmarks/lvs_models/build_blender_adaptive_v1.py
python -m unittest tests.maximum_optimizer.test_compiler_aware_evidence -v
```
