# Maximum v2 Raw-Mask Silhouette Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test an exact fused native silhouette kernel that consumes all raw masks in one call and materially reduces Maximum v2 runtime without changing any output or decision.

**Architecture:** Keep the approved Python KD path as the default. An opt-in experimental path passes eight original and eight candidate row-major `L` masks to an isolated DLL, where four-neighbour boundary extraction, exact squared Euclidean distances, global p95 selection, and IoU counting share reusable thread-local buffers. The normal call returns only per-view intersection/union counts and the selected squared distance; optional caller-owned debug buffers expose boundaries and ordered distances solely for exact oracle tests.

**Tech Stack:** Python 3.11, Pillow, ctypes, C++17/MSVC x64, CMake, unittest, existing Maximum v2 benchmark harness.

## Global Constraints

- Keep 512 samples, eight canonical views, 256px masks, all gates, budgets, tolerances, candidates, regions, arrays, and hashes unchanged.
- The experiment is enabled only by `MAXIMUM_SILHOUETTE_EXPERIMENT_DLL`; no WPF, worker CLI, release resource, or approved DLL changes.
- Build all experimental binaries and evidence under `D:\gaco-max-v2-raw-mask-silhouette-20260721`; do not run `build_release_wpf.ps1`.
- Preserve the approved DLL SHA-256 `d5c944b8eabeaa17bea055aaa7a098cd11500725c37410e24a1b2eb61490da45` and profile SHA-256 `6029d5a8c80ec2b7caf6e50e82035b3aeb21103d6609c1d7345a3a3ff9e6c9d9`.
- Stop on any non-bitwise floating result, changed gate/region/candidate/array/hash, or native layout ambiguity; do not introduce a tolerance.
- Promotion requires silhouette median warm speedup >=3x, adaptive median improvement >=25%, gains on more than one model type, no simple-model regression, acceptable memory, and stable results.
- Do not optimize the 3D `_nearest` path in this task.

---

### Task 1: Freeze the raw-mask ABI with failing tests

**Files:**
- Create: `tests/maximum_optimizer/test_silhouette_native.py`
- Create: `maximum_optimizer/silhouette_native.py`
- Modify: `maximum_optimizer/native/meshopt_bridge.cpp`

**Interfaces:**
- Consumes: caller-owned binary masks with explicit width, height, row stride, view stride, and view count.
- Produces: `RawMaskBatchResult`, `RawMaskBatchDiagnostics`, optional ordered boundary/distance debug data, and native export `maximum_silhouette_metrics_raw_batch_v1`.

- [ ] **Step 1: Write edge-contract tests before implementation**

Cover empty, full, single pixel, border contact, disconnected components, tiny and odd dimensions, tied distances, maximum corner distance, horizontal/vertical mirroring, padded row/view strides, output capacities, and binary-mask validation. Build the Python oracle from existing `_boundary_points`, `_kd_tree`, `_kd_distance`, `ImageChops`, `math.sqrt`, and `_percentile`; assert exact boundary order, exact ordered squared distances, exact intersection/union counts, exact p95 squared value, and bitwise-equal final floats.

- [ ] **Step 2: Run the focused test and observe RED**

Run: `python -m unittest tests.maximum_optimizer.test_silhouette_native -v`

Expected: import/export failure because `maximum_optimizer.silhouette_native` and `maximum_silhouette_metrics_raw_batch_v1` do not exist.

- [ ] **Step 3: Define the minimal ctypes contract**

Use fixed-width fields and `ctypes.Structure` mirrors. `RawMaskSilhouetteKernel.measure(...)` accepts original bytes plus candidate `L` images, reuses a caller-side candidate byte buffer, performs one native call, reconstructs Python IoU in the original view order, and calls Python `math.sqrt` only on the selected squared integer. Debug buffers are absent in the performance path.

- [ ] **Step 4: Add the native batch implementation**

Implement exact nonzero four-neighbour row-major boundaries. Reuse thread-local vectors for boundaries, EDT scratch, and global ordered squared distances. Preserve the one-empty-mask sentinel as `silhouette_resolution ** 2`; compute the p95 rank with the same IEEE-754 `ceil(count * 0.95) - 1` expression. Record nanoseconds for boundary extraction, distance computation, and metric selection plus scratch capacity growth counts/bytes.

- [ ] **Step 5: Build only the experimental DLL on D:**

Configure/build with CMake from `maximum_optimizer/native` into `D:\gaco-max-v2-raw-mask-silhouette-20260721\native-build`; copy the DLL to `D:\gaco-max-v2-raw-mask-silhouette-20260721\bin\meshopt_bridge.raw-mask-experiment.dll`. Do not overwrite `maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll`.

- [ ] **Step 6: Run focused tests with the explicit DLL and observe GREEN**

Run with `MAXIMUM_SILHOUETTE_EXPERIMENT_DLL` pointing at the D-drive DLL. Expected: every native edge-contract test executes and passes, not skips.

- [ ] **Step 7: Commit the kernel contract**

Commit source and tests only; do not add the experimental DLL.

### Task 2: Integrate an opt-in A/B path without changing the default

**Files:**
- Modify: `maximum_optimizer/metrics.py`
- Modify: `tests/maximum_optimizer/test_metrics.py`
- Modify: `tests/maximum_optimizer/test_silhouette_native.py`

**Interfaces:**
- Consumes: `MAXIMUM_SILHOUETTE_EXPERIMENT_DLL` only in experimental subprocesses.
- Produces: the same `(silhouette_iou_loss, silhouette_boundary_p95_px)` pair and aggregate low-overhead timing counters.

- [ ] **Step 1: Write failing prepared-metric equivalence tests**

Use disc fixtures and asymmetric multi-view masks. Assert exact `RegionMetrics`, `ValidationDecision`, float byte representations, candidate arrays, and region order between baseline and experimental calls. Assert the default path still calls `_boundary_points`/KD and the opt-in path performs exactly one native crossing per region measurement.

- [ ] **Step 2: Run focused tests and observe RED**

Expected: the environment flag does not yet select a native path.

- [ ] **Step 3: Add minimal opt-in integration**

Retain `_silhouette_metrics_prepared` unchanged as baseline. Add a dispatcher that projects the same candidate masks, records mask-preparation time, and calls the batch kernel only when the explicit experiment DLL variable is set. Cache original contiguous mask bytes only in experimental prepared references; use `compare=False`/`repr=False` fields so diagnostic storage cannot affect semantic comparisons.

- [ ] **Step 4: Expose low-overhead diagnostics**

Aggregate mask preparation, input marshaling, total native call, native boundary, native distances, native metric production, output marshaling, caller postprocessing, Python buffer growth, native scratch growth, and total silhouette time. Reset/read functions must not alter calculations.

- [ ] **Step 5: Run focused and full suites**

Run the new suite against the D DLL, then `python -m unittest discover -s tests -t . -v` with the experiment variable absent. Expected: new tests pass and the original result remains 82/82.

- [ ] **Step 6: Commit the isolated integration**

Commit only Python source/tests; verify the approved DLL and profile hashes are unchanged.

### Task 3: Prove bitwise equivalence before performance work

**Files:**
- Create: `benchmarks/lvs_models_adaptive/_raw_mask_silhouette_experiment.py`
- Evidence: `D:\gaco-max-v2-raw-mask-silhouette-20260721\equivalence\*.json`

**Interfaces:**
- Consumes: frozen Pontiac original/normal trees and frozen Toyota, Dodge, and small-region cross-check fixtures.
- Produces: machine-readable oracle, regional, semantic, and hash comparisons.

- [ ] **Step 1: Run at least 10,000 deterministic asymmetric/random mask pairs**

Compare exact mask bytes, row-major boundary coordinates, ordered bidirectional squared distances, per-view offsets, intersections/unions, p95 squared, and final float bytes. Include every mandatory edge case and padded stride/alignment variants.

- [ ] **Step 2: Compare the four required real model types**

Use Pontiac wheel; Toyota curved glass/body multi-material region; Dodge mixed-weight/bones region; and the frozen small 150-triangle region. Compare metrics, every gate, selected candidate, regions and ordering, positions, indices, normals, UVs, skin weights, source/staging hashes, and canonical semantic fingerprints.

- [ ] **Step 3: Run one experimental full Pontiac compile**

Use a new D-drive work/output root with the frozen CLI inputs. Require 13,530 triangles, 10,221 vertices, 833,184 comparable bytes, zero DX80, no integrity failures, and byte-identical MDL/VVD/DX90/PHY hashes against `fb83a6b549963073245a9bb637be0de733c2fd6eba74328c322f323addf3d04b`.

- [ ] **Step 4: Stop immediately on any mismatch**

If a float is not bitwise equal, report both values/bytes, magnitude, cause, gate risk, and an exact alternative; do not benchmark or accept tolerance.

- [ ] **Step 5: Commit the reproducible harness**

Do not commit raw artifacts or compiled binaries.

### Task 4: Run balanced A/B performance measurements

**Files:**
- Modify: `benchmarks/lvs_models_adaptive/_raw_mask_silhouette_experiment.py`
- Evidence: `D:\gaco-max-v2-raw-mask-silhouette-20260721\runs\*.json`

**Interfaces:**
- Consumes: identical frozen Pontiac inputs, unique empty caches for cold runs, prepopulated caches for warm runs.
- Produces: per-run wall/CPU/RSS/allocation/timing records and exact semantic fingerprints.

- [ ] **Step 1: Execute five cold baseline and five cold experiment fresh-process runs**

Alternate lanes, use unique empty `FileRegionCache` roots, and retain every individual timing and fingerprint.

- [ ] **Step 2: Execute ten warm runs per lane in balanced `A,B,B,A` blocks**

Reload Python/DLL per subprocess, reuse prepopulated region-cache state, and preserve lane balance against drift.

- [ ] **Step 3: Measure four-type regional performance**

Run repeated baseline/experiment measurements for Pontiac, Toyota, Dodge, and small fixtures. Record median/min/max/sample standard deviation, peak working set, Python buffer growth, native scratch allocations, and regression ratio.

- [ ] **Step 4: Verify every timed run remained exact**

Reject any run whose semantic, staging, array, gate, or output fingerprint differs. Flag outliers with a documented rule; report rather than silently delete them.

- [ ] **Step 5: Remove the temporary harness if the experiment is rejected**

If promotion gates fail, revert every experimental source/test change and keep only the report. If gates pass, leave the path opt-in and unshipped pending user approval.

### Task 5: Analyze and report, then stop

**Files:**
- Create: `docs/superpowers/specs/2026-07-21-models-maximum-raw-mask-silhouette-result.md`
- Modify: `benchmarks/lvs_models_adaptive/results-2026-07-21.md`

**Interfaces:**
- Consumes: raw JSON evidence only.
- Produces: reproducible verdict `promote`, `adjust`, or `discard` without merge/release.

- [ ] **Step 1: Report all individual measurements and distributions**

Include cold/warm adaptive, total silhouette, native phases, total model projection, median/min/max/sample deviation, absolute and percentage gains, speedups, peak memory, and relevant allocation counts.

- [ ] **Step 2: Recompute Amdahl from measured medians**

Show remaining silhouette seconds/fraction, unchanged 5.920-second `_nearest` share as a new percentage, new adaptive and projected full-model time, and remaining seconds/percentage to 12 and 10 seconds. State whether batched `_nearest` is justified but do not implement it.

- [ ] **Step 3: Apply the promotion gates literally**

Require exact outputs, >=3x warm silhouette, >=25% warm adaptive, gains on multiple types, no meaningful small-model regression, acceptable memory, and stable distributions.

- [ ] **Step 4: Verify final repository and release isolation**

Run the original 82-test suite plus all native tests, record `git status --short`, hashes of the approved DLL/profile/WPF resource, and prove no experimental DLL entered the published executable/resource ZIP.

- [ ] **Step 5: Commit only the final permitted state and stop**

If successful, commit the opt-in experiment and report. If rejected, commit only the report after restoring source/tests. Do not merge, publish, build WPF, or start `_nearest` work.
