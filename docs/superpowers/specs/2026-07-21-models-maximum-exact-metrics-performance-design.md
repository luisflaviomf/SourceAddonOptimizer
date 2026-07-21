# Models Maximum v2 exact metrics performance design

**Date:** 2026-07-21  
**Status:** approved for implementation  
**Scope:** exact acceleration of regional silhouette validation

## Goal

Reduce the cold-run time of Maximum Adaptive v2 without changing any selected
region, metric value, quality threshold, sample count, silhouette resolution,
render escalation, or compiled output.

The reference case is the Pontiac Trans Am wheel holdout family. Its frozen
run takes 56.012 seconds, including 27.117 seconds in
`adaptive-simplification`. Detailed instrumentation attributes 11.69 seconds
to prepared silhouette boundary-distance work, 4.28 seconds to 3D nearest
surface queries, and only 1.36 seconds to meshoptimizer.

## Non-goals

- Do not introduce coarse-to-fine acceptance or approximate metrics.
- Do not change the calibrated profile or its SHA-256.
- Do not reduce the 512 samples, eight canonical views, or 256px masks.
- Do not change Blender rendering or StudioMDL compilation in this iteration.
- Do not change the WPF or legacy Python GUI.
- Do not use additional CPU/GPU concurrency as the primary optimization.

## Considered approaches

### 1. Exact squared Euclidean distance field — selected

Replace recursive KD-tree construction and per-boundary-point queries with an
exact squared Euclidean distance transform over each 256x256 binary boundary
mask. The transform is linear in the number of mask pixels and preserves the
current Euclidean distance definition.

This has the best risk/reward ratio: the masks, boundary pixels, bidirectional
comparison, p95 calculation, and budgets remain unchanged.

### 2. Coarse-to-fine metrics — deferred

Starting at a lower resolution or sample count could save more time but may
miss a local defect or change a pass/fail decision. It is incompatible with
the exact-output requirement for this experiment.

### 3. Native C++ metric implementation — fallback only

Moving the distance transform or full metric kernel into the existing native
bridge offers more headroom but increases ABI, packaging, and maintenance
risk. It is considered only if the exact Python transform cannot meet the
promotion gate.

## Design

`maximum_optimizer.metrics._SilhouetteReference` will retain the existing
mask and ordered boundary points, but its recursive KD tree will be replaced
by an immutable row-major squared-distance field.

For each canonical view:

1. Rasterize the region exactly as today.
2. Extract the same four-neighbour boundary pixels in the same order.
3. If the boundary is non-empty, calculate an exact squared Euclidean distance
   field using the separable one-dimensional lower-envelope algorithm: rows,
   then columns.
4. Store the original field in the prepared reference.

For a candidate:

1. Rasterize and extract its boundary without changing existing code.
2. Preserve the current empty/non-empty handling.
3. Build the candidate field once.
4. Read candidate-field distances at original boundary points and original-
   field distances at candidate boundary points.
5. Apply `sqrt` and the existing p95 function exactly as today.

All grid costs are integer squared pixel distances. The final square roots and
percentile ordering therefore match the exact KD-tree result. No approximation
or early exit is introduced.

## Error handling

- An empty boundary continues to use the existing empty-mask behavior.
- Invalid dimensions or out-of-range boundary coordinates fail closed with
  `ValueError`.
- The pipeline's existing validator exception handling restores the local
  region if an unexpected metric error occurs.

## Test strategy

TDD starts with a failing unit test for the wished-for distance-field API.
The independent oracle is brute-force squared Euclidean distance on small,
asymmetric grids, including edge and single-point cases.

Existing metric tests must continue to prove deterministic region metrics,
silhouette rejection of a hexagonal wheel, UV/normal/skinning independence,
and prepared-reference reuse.

The real Pontiac comparison must preserve, byte for byte where applicable:

- final triangle count: 13,530;
- all 19 region representations and selected triangle counts;
- all failed-gate tuples and targeted-render decisions;
- one targeted render and one StudioMDL compile;
- final comparable model bytes and compiled integrity.

## Performance promotion gate

Use the same Pontiac input and frozen profile on cold, isolated work/cache
paths. Promote only if:

- `adaptive-simplification` improves by at least 25% from 27.117 seconds
  (20.338 seconds or less);
- total wall time improves from 56.012 seconds;
- exact output and fidelity contracts above remain unchanged.

If the exact Python transform misses the timing gate, discard it rather than
weakening quality. The next experiment will be the same exact transform in the
existing native bridge, not reduced-resolution validation.
