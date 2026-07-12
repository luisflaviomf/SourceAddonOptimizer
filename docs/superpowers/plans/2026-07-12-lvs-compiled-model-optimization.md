# LVS Compiled Model Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce and validate a model strategy that reduces final Source compiled bytes on the LVS corpus without structural or visual regressions.

**Architecture:** A reproducible benchmark harness owns the corpus, baselines and immutable experiment records. Strategy implementations remain isolated behind candidate adapters; every candidate is compiled by StudioMDL and judged by final bytes plus existing structural/visual gates.

**Tech Stack:** Python 3.11, Blender 5.0.1, Blender Source Tools, StudioMDL, meshoptimizer v1.2/MSVC, Pillow, Garry's Mod runtime.

## Global Constraints

- Work in `feature/maximum-optimizer`; do not expose Maximum in WPF yet.
- Never process the complete 1.4 GB addon for an inner-loop experiment.
- Preserve original addon and decompiled source trees read-only.
- Count DX80 removal separately from geometry savings.
- No promotion from uncalibrated or texture-missing visual results.

---

### Task 1: Repair real QC inventory gates

**Files:** Modify `maximum_optimizer/qc_inventory.py`; add real-fixture regressions under `tests/maximum_optimizer/`.

- [ ] Add a failing minimal adjacent-directive test and a Monaco real-QC regression for skins, attachments, sequences and collision.
- [ ] Run focused tests and observe the current skipped-directive failure.
- [ ] Correct token advancement without weakening include/path containment.
- [ ] Run QC, structural and full Python suites; commit.

### Task 2: Freeze corpus and benchmark harness

**Files:** Create `benchmarks/lvs_models/corpus.json`, `benchmark_lvs_models.py`, `maximum_optimizer/benchmarking.py`, tests and documentation.

- [ ] Write tests for the fixed ten-family corpus, immutable result schema, compiled-byte decomposition and cache keys.
- [ ] Implement pressure/full partitions and exact source/compiled hashes.
- [ ] Import original/control/existing Blender baselines without recomputing unchanged stages.
- [ ] Generate baseline report and commit it with paths parameterized by environment variables.

### Task 3: Validate the lossless DX90-only candidate

**Files:** Add packaging policy module/tests and benchmark experiment record; do not alter default release policy.

- [ ] Test exact removal accounting and rejection when DX90 is absent.
- [ ] Build/copy pressure-set candidates with DX80 omitted and all other hashes identical.
- [ ] Validate tools and Garry's Mod runtime loading on representative bodygroup/animated/physics models.
- [ ] Record result as separate optional saving; commit only after runtime proof.

### Task 4: Repair smoothing with fixed topology

**Files:** Modify `batch_optimize_maximum.py` and add Blender/pure regression tests.

- [ ] Reproduce flat `from_pydata` output and excessive hard-normal positions with fixed indices.
- [ ] Reconstruct smooth islands/sharp boundaries and apply custom split normals deterministically.
- [ ] Compile the identical-topology pressure set and require lower/equal VVD/VTX with equivalent renders.
- [ ] Commit the smoothing strategy and evidence.

### Task 5: Implement `meshopt-direct-v1`

**Files:** Modify candidate domain/search/cache payload, `batch_optimize_maximum.py`, meshopt facade and tests.

- [ ] Add an explicit immutable `update_vertices=false`, `transfer=direct-v1` strategy and failing command/cache tests.
- [ ] Bypass projection for no-update output; copy returned original position/normal/UV/bone attributes and compact referenced tuples only.
- [ ] Prove output tuple count is monotonic and attributes of retained tuples are bitwise identical.
- [ ] Compile ratios 0.85/0.70/0.55/0.40/0.25 on the pressure set; retain only passing improvements.
- [ ] Commit implementation and benchmark evidence.

### Task 6: Position-remapped permissive topology

**Files:** Modify `maximum_optimizer/mesh_attributes.py`, native bridge/options, Blender strategy and tests.

- [ ] Add fixtures where UV/normal seams share positions and prove they are not geometric borders.
- [ ] Build canonical-position adjacency; lock true open borders, selectively protect appearance/material/skin boundaries.
- [ ] Remove artificial per-material simplification borders while preserving output material ownership.
- [ ] Benchmark pressure then full corpus, comparing locked percentage, achieved ratio, compiled vertices and gates.
- [ ] Commit only if it improves at least one approved family without regressions.

### Task 7: Compiler-aware Blender hybrid and adaptive search

**Files:** Add strategy/scoring/search tests and benchmark records.

- [ ] Use compiled vertex/triangle cost as the proxy, confirmed by StudioMDL snapshots.
- [ ] Run Blender topology with repaired smoothing/attributes and ratios by stable region.
- [ ] Compare all approved strategies; choose strictly smaller compiled output per family.
- [ ] Promote a winner only when the design acceptance criteria are met; otherwise continue Task 8.

### Task 8: Iterative advanced experiments

**Files:** Separate versioned strategy modules/tests per experiment.

- [ ] Test exact duplicate/degenerate removal and float canonicalization.
- [ ] Test bounded normal/UV/weight canonicalization with calibrated visual gates.
- [ ] Test planar attribute-affine reduction on car-body/interior regions.
- [ ] If still insufficient, prototype wedge-aware appearance-QEM and compare on the pressure set.
- [ ] Record every rejected hypothesis and continue until a strategy meets acceptance or all listed tracks are reproducibly exhausted.

### Task 9: Full validation and product decision

- [ ] Run all ten families, structural/visual/control gates and in-game representative validation.
- [ ] Report median/worst savings, per-extension bytes and failure reasons.
- [ ] Set the winning engine/preference only if acceptance passes.
- [ ] Only then resume WPF Tasks 12–14 and release packaging.

