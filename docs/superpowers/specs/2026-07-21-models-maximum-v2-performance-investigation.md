# Maximum v2 rigorous adaptive performance investigation

**Date:** 2026-07-21
**Status:** investigation complete; no optimization implemented
**Scope:** Pontiac Trans Am wheel `adaptive-simplification` stage

## Verdict

The expensive step is not meshoptimizer, Blender, StudioMDL, BVH construction,
normal comparison, UV comparison, or skinning arithmetic. It is exact regional
validation.

One low-overhead cold decomposition accounts for **31.457 of 31.502 seconds
(99.859%)** without double counting. Preparing the nine original references
and measuring their nine candidates consumes **25.335 seconds (80.424%)**.
The detailed hierarchy attributes **19.709 of 33.651 instrumented seconds
(58.570%)** to silhouettes and **5.920 seconds (17.594%)** to 9,216 3D nearest
queries. The detailed wall includes instrumentation overhead and is not the
performance result; its values are used only to divide the already-accounted
validation parent.

The five unprofiled cold runs have median **29.342 s** (27.627–31.776 s). The
five unprofiled warm runs have median **24.931 s** (23.644–29.988 s). A warm
candidate cache therefore saves only **4.410 s / 15.03%** at the median because
the current cache avoids simplification but repeats all expensive validation.

Reaching 12 seconds from the frozen 27.117-second baseline requires eliminating
**55.748%** of the whole stage; reaching 10 seconds requires **63.123%**. Even
making meshoptimizer, risk classification, or cache writes instantaneous cannot
reach either target. The combined reference/candidate validation would need an
approximately **3.26x** speedup for 12 seconds or **4.65x** for 10 seconds.

## Frozen contract and scope isolation

The investigated production inputs are:

- repository commit: `1e80afd2d67fe224c3f85fa243d1cda151b0a3d6`;
- profile SHA-256: `6029d5a8c80ec2b7caf6e50e82035b3aeb21103d6609c1d7345a3a3ff9e6c9d9`;
- Release x64 bridge SHA-256: `d5c944b8eabeaa17bea055aaa7a098cd11500725c37410e24a1b2eb61490da45`;
- frozen result: 16,304 original triangles, 13,530 final triangles, 833,184
  comparable final bytes, 19 simplifier evaluations, one targeted render, one
  StudioMDL compile, zero DX80 bytes, and no integrity failures;
- frozen real stage: 27.117 s wall / 23.609 s CPU;
- frozen real full run: 56.012 s wall.

The harness executes the production inventory, classification, simplification,
cache, metrics, decisions, and source composition. It stubs targeted rendering
and compilation after the adaptive stage, so those later processes cannot
contaminate its timing. The compile stub points only at the frozen compiled
model family for size/hash accounting. No WPF, .NET, worker packaging, Blender,
or StudioMDL work occurs inside the measured stage.

Raw evidence is under `D:\gaco-max-v2-rigorous-profile-20260721`. `C:` had zero
free bytes, so the planned evidence root was moved to `D:`, which had 371 GB
free. Cold means a fresh Python process and a unique empty region cache. Warm
means a fresh process and a prepopulated persistent region cache. Windows page
cache was not forcibly cleared.

## Ten unprofiled measurements

These are the performance measurements. No profiler or function wrapper was
active in them.

| Condition | Run | Adaptive wall | Adaptive CPU | Evaluations | Cache hits |
|---|---:|---:|---:|---:|---:|
| Cold | 1 | 29.342 s | 26.672 s | 19 | 0 |
| Cold | 2 | 30.685 s | 28.219 s | 19 | 0 |
| Cold | 3 | 27.627 s | 25.234 s | 19 | 0 |
| Cold | 4 | 27.990 s | 25.063 s | 19 | 0 |
| Cold | 5 | 31.776 s | 29.313 s | 19 | 0 |
| Warm | 1 | 29.988 s | 27.172 s | 0 | 19 |
| Warm | 2 | 24.931 s | 18.781 s | 0 | 19 |
| Warm | 3 | 26.921 s | 19.172 s | 0 | 19 |
| Warm | 4 | 24.443 s | 18.156 s | 0 | 19 |
| Warm | 5 | 23.644 s | 16.156 s | 0 | 19 |

| Statistic | Cold wall | Warm wall | Cold CPU | Warm CPU |
|---|---:|---:|---:|---:|
| First | 29.342 s | 29.988 s | 26.672 s | 27.172 s |
| Median | **29.342 s** | **24.931 s** | **26.672 s** | **18.781 s** |
| Minimum | 27.627 s | 23.644 s | 25.063 s | 16.156 s |
| Maximum | 31.776 s | 29.988 s | 29.313 s | 27.172 s |
| Population coefficient of variation | 5.34% | 8.75% | 6.17% | 19.04% |

The inventory/data-loading stage is outside the adaptive wall and was stable:
0.613 s cold median and 0.617 s warm median in the isolated harness. The frozen
end-to-end run measured 1.742 s there. The difference reflects hot OS pages and
the reduced harness context, not an algorithm change.

## Non-overlapping adaptive wall

This table comes from one separate cold run with only eleven outer timers. Its
31.502-second wall is within the measured cold range. Rows are mutually
exclusive; inclusive child details appear only in the next section.

| Category | Calls | Measured total | Mean/call | Adaptive wall | Scaled share of frozen 27.117 s |
|---|---:|---:|---:|---:|---:|
| Candidate metric measurement | 9 | 16.390 s | 1.821 s | 52.029% | 14.109 s |
| Original reference preparation | 9 | 8.945 s | 0.994 s | 28.395% | 7.700 s |
| Risk measurement | 19 | 2.200 s | 0.116 s | 6.984% | 1.894 s |
| Regional simplification, inclusive | 19 | 2.129 s | 0.112 s | 6.757% | 1.832 s |
| Cold cache writes | 19 | 1.745 s | 0.092 s | 5.539% | 1.502 s |
| Material resolution | 19 | 0.028 s | 1.459 ms | 0.088% | 0.024 s |
| Source hashing | 3 | 0.012 s | 4.148 ms | 0.040% | 0.011 s |
| Cold cache reads/misses | 19 | 0.007 s | 0.392 ms | 0.024% | 0.006 s |
| Budget construction | 19 | 0.001 s | 0.033 ms | 0.002% | 0.001 s |
| Final gate comparison | 9 | 0.000 s | 0.050 ms | 0.001% | <0.001 s |
| Adaptive-risk predicate | 19 | 0.000 s | 0.017 ms | 0.001% | <0.001 s |
| **Subtotal named functions** | — | **31.457 s** | — | **99.859%** | **27.079 s** |
| Residual orchestration/list work | — | 0.044 s | — | 0.141% | 0.038 s |
| **Closed total** | — | **31.502 s** | — | **100.000%** | **27.117 s** |

The scaled column is a share projection, not a claim that the older frozen run
was instrumented retroactively.

The separate warm outer run took 23.970 s. Reference preparation plus candidate
measurement still consumed 20.632 s (86.08%); JSON cache reads consumed 1.592 s
and risk measurement 1.680 s. This directly proves that warm-cache validation
is invariant repeated work.

## Detailed inclusive hierarchy

The detailed run took 33.651 s because per-query timers and fingerprint capture
add overhead. Percentages below use that detailed wall. Child rows overlap
their parent and must not be added to the non-overlapping table.

| Parent/child operation | Calls | Inclusive total | Mean/call | Detailed wall |
|---|---:|---:|---:|---:|
| Original silhouette preparation | 9 | 8.309 s | 0.923 s | 24.692% |
| Candidate silhouette measurement | 9 | 11.400 s | 1.267 s | 33.878% |
| ↳ all mask projections | 144 | 3.141 s | 21.814 ms | 9.335% |
| ↳ all four-neighbour boundary scans | 144 | 8.555 s | 59.411 ms | 25.424% |
| ↳ all 2D KD builds | 144 | 1.120 s | 7.780 ms | 3.329% |
| ↳ all root 2D KD queries | 120,820 | 4.728 s | 39.136 µs | 14.051% |
| Bidirectional attribute/distance pass | 18 | 6.429 s | 0.357 s | 19.106% |
| ↳ 3D nearest queries | 9,216 | 5.920 s | 0.642 ms | 17.594% |
| ↳ skinning arithmetic | 9,216 | 0.271 s | 29.455 µs | 0.807% |
| ↳ normal arithmetic | 9,216 | 0.053 s | 5.781 µs | 0.158% |
| ↳ UV arithmetic | 9,216 | 0.047 s | 5.046 µs | 0.138% |
| ↳ surface scalar arithmetic | 9,216 | 0.006 s | 0.684 µs | 0.019% |
| 3D BVH construction | 18 | 0.552 s | 30.676 ms | 1.641% |
| Deterministic sample generation | 18 | 0.487 s | 27.080 ms | 1.449% |
| Structural validation | 18 | 0.645 s | 35.851 ms | 1.918% |
| Triangle-data conversion | 18 | 0.368 s | 20.453 ms | 1.094% |
| Percentiles | 72 | 0.016 s | 0.221 ms | 0.047% |
| meshopt Python bridge, inclusive | 19 | 0.592 s | 31.141 ms | 1.758% |
| ↳ native meshoptimizer call | 19 | 0.050 s | 2.635 ms | 0.149% |

Within the 1.963 s detailed simplification parent, only 0.050 s was native
meshoptimizer. Python bridge validation/marshalling used about 0.520 s, and SMD
array construction, topology classification, and result reconstruction account
for the remaining 1.371 s. A profiled run confirms
`classify_position_topology` is material inside that residual, but profiler
inflation prevents assigning its profiled absolute time to the real wall.

## Call tree and self time

An adaptive-only `cProfile` run took 102.978 s and made 97,936,396 recorded
calls. That inflated wall is excluded from performance claims. It confirms the
following call tree:

```text
adaptive-simplification
└── optimize_all (19 regions)
    ├── measure_risk (19)
    └── optimize_region (19)
        ├── simplify_smd_region (19)
        │   ├── classify_position_topology (19)
        │   └── meshopt_bridge.simplify_mesh (19)
        └── validator (9 reduced candidates)
            ├── prepare_region_reference (9)
            │   ├── _build_bvh (9 roots)
            │   ├── _samples (9)
            │   └── _prepare_silhouettes (9 × 8 views)
            └── measure_region_prepared (9)
                ├── _build_bvh + _samples
                ├── _direction_metrics (18 directions)
                │   └── _nearest (9,216)
                └── _silhouette_metrics_prepared (9 × 8 views)
                    └── _kd_distance (120,820 root queries)
```

| Function | Calls | Self | Cumulative | Note |
|---|---:|---:|---:|---|
| boundary generator at `metrics.py:390` | 25,491,978 | 11.527 s | 11.527 s | Per-pixel neighbour checks |
| `_boundary_points` | 144 | 9.837 s | 28.782 s | Largest cumulative leaf group |
| built-in `sum` | 4,939,370 | 8.046 s | 16.279 s | Geometry/vector Python loops |
| built-in `any` | 5,178,142 | 7.421 s | 19.264 s | Boundary and validation loops |
| `_dot` | 3,283,435 | 6.368 s | 18.022 s | Projection/nearest arithmetic |
| `_sub` | 1,423,831 | 4.879 s | 7.847 s | Geometry arithmetic |
| `_kd_distance` | 1,976,504 recursive | 4.486 s | 5.016 s | 2D boundary search |
| `_closest_barycentric` | 195,956 | 2.311 s | 14.223 s | 3D nearest search |
| `_project_region` | 144 | 1.995 s | 10.920 s | Python coordinate setup + Pillow draw |
| `_nearest` | 9,216 | 1.990 s | 26.325 s | Dominated by children |

Self time and cumulative time are deliberately shown separately. Cumulative
rows overlap and are not summed.

The selected production-function table below supplies the same distinction for
the full metric contract. `Calls` is total/primitive for recursive functions;
`cum/call` uses primitive root calls in those rows. Percent is exclusive self
time divided by the 100.960-second profiled total. The raw profile JSON retains
every function and every caller tuple.

| Function | Calls | Self | Cumulative | Self/call | Cum/root call | Self % | Includes children? |
|---|---:|---:|---:|---:|---:|---:|---|
| `measure_risk` | 19 | 1.364 s | 7.294 s | 71.804 ms | 383.893 ms | 1.351% | Yes |
| `simplify_smd_region` | 19 | 0.808 s | 6.409 s | 42.514 ms | 337.323 ms | 0.800% | Yes |
| `classify_position_topology` | 19 | 0.345 s | 1.799 s | 18.142 ms | 94.690 ms | 0.341% | Yes |
| `meshopt_bridge.simplify_mesh` | 19 | 0.155 s | 2.130 s | 8.178 ms | 112.105 ms | 0.154% | Yes |
| `prepare_region_reference` | 9 | 0.004 s | 31.848 s | 0.472 ms | 3.539 s | 0.004% | Yes |
| `measure_region_prepared` | 9 | 0.005 s | 55.706 s | 0.604 ms | 6.190 s | 0.005% | Yes |
| `_validate_structure` | 18 | 0.400 s | 2.028 s | 22.229 ms | 112.658 ms | 0.396% | Yes |
| `_triangle_data` | 18 | 0.383 s | 1.811 s | 21.294 ms | 100.631 ms | 0.380% | Yes |
| `_build_bvh` | 8,558/18 | 0.081 s | 1.753 s | 0.009 ms | 97.366 ms | 0.080% | Recursive |
| `_samples` | 18 | 0.102 s | 2.089 s | 5.691 ms | 116.033 ms | 0.101% | Yes |
| `_prepare_silhouettes` | 9 | 0.123 s | 27.057 s | 13.654 ms | 3.006 s | 0.122% | Yes |
| `_silhouette_metrics_prepared` | 9 | 0.016 s | 25.461 s | 1.797 ms | 2.829 s | 0.016% | Yes |
| `_project_region` | 144 | 1.995 s | 10.920 s | 13.852 ms | 75.831 ms | 1.976% | Yes |
| `_boundary_points` | 144 | 9.837 s | 28.782 s | 68.313 ms | 199.872 ms | 9.744% | Yes |
| `_kd_tree` | 241,784/144 | 0.529 s | 1.778 s | 0.002 ms | 12.347 ms | 0.524% | Recursive |
| `_kd_distance` | 1,976,504/120,820 | 4.486 s | 5.016 s | 0.002 ms | 0.042 ms | 4.443% | Recursive |
| `_direction_metrics` | 18 | 0.193 s | 27.335 s | 10.707 ms | 1.519 s | 0.191% | Yes |
| `_nearest` | 9,216 | 1.990 s | 26.325 s | 0.216 ms | 2.856 ms | 1.971% | Yes |
| `_closest_barycentric` | 195,956 | 2.311 s | 14.223 s | 0.012 ms | 0.073 ms | 2.289% | Yes |
| `_pose_displacement` | 18,432 | 0.155 s | 0.442 s | 0.008 ms | 0.024 ms | 0.154% | Yes |
| `_percentile` | 72 | 0.000 s | 0.015 s | 0.006 ms | 0.211 ms | <0.001% | Yes |
| cache `get` | 19 | 0.000 s | 0.009 s | 0.006 ms | 0.457 ms | <0.001% | Yes |
| cache `put` | 19 | 0.007 s | 1.535 s | 0.387 ms | 80.764 ms | 0.007% | Yes |

These cProfile absolute times are intentionally not mixed into the
non-overlapping wall table: profiling makes Python loops roughly 3.5x slower.

## BVH/query distribution and duplication

- 18 3D BVHs were built in 0.552 s total: nine originals and nine candidates.
- The 9,216 nearest queries used 5.920 s; every validated region performs 512
  forward plus 512 reverse queries.
- Canonical query fingerprints found 9,216 unique inputs, zero duplicate
  instances, and maximum multiplicity one. There is no exact intra-run query
  deduplication opportunity.
- Per-region nearest-query mean ranges from 0.401 to 0.974 ms. Per-region p95
  ranges from 0.837 to 1.942 ms; the global observed maximum is 5.391 ms.
- 2D silhouette KD preparation used 1.120 s; 120,820 root queries used 4.728 s.
  Thus search, not BVH/KD construction, is the larger spatial cost.

## Repeated and invariant work

The cold run simplified 19 regions, but only nine candidates reduced triangle
count and entered validation. Ten candidates were unchanged (34- or
36-triangle protected components) yet were still serialized into the region
cache. An exact negative/sentinel cache entry could avoid loading their full
geometry later, but this is not large enough to solve the cold target.

Within one run, each validated original reference is prepared once, each
candidate is measured once, all sample seeds are unique to their contract, and
all nearest-query fingerprints are unique. The current one-candidate search is
already avoiding repeated candidates.

One genuine but small invariant rebuild exists inside skinning:
`_pose_displacement` reconstructs `dict(pose)` on every call. It was called
18,432 times (source and target for every posed sample), with 0.155 s profiled
self / 0.442 s cumulative. Hoisting that dictionary cannot materially approach
the stage target. Normals and UVs are interpolated as part of each unique
nearest result; their scalar comparisons use only 0.053 s and 0.047 s in the
detailed run. Mesh input/output buffers are built once per one of the 19 unique
candidates, not repeatedly across metrics.

Across warm runs, however, the same 18 sample sets, 18 BVHs, 144 original and
candidate silhouette masks, metrics, and gate decisions are recomputed. The
cache authenticates candidate geometry but stores neither the metric contract
result nor the validation decision. That is why a 19-hit warm run still spends
20.632 seconds in exact validation.

## I/O, allocation, GC, and runtime boundaries

These are cross-cut overlays and are not added to the non-overlapping wall:

- cold outer run: 4.671 MB read and 7.847 MB written during the adaptive stage;
  cache writes alone used 1.745 s;
- warm outer run: 12.518 MB read, zero bytes written, and 1.592 s in cache reads;
- cold outer live allocated blocks grew by 170,630 and RSS by 16.19 MB;
- warm outer live allocated blocks grew by 311,226 and RSS by 22.50 MB;
- detailed run GC: 1.144 s total across 1,959 collections, including 0.864 s
  in six generation-2 collections. This includes the extra objects retained by
  detailed tracing, so it is an upper-bound diagnostic, not a production-wall
  category;
- the validation hot path is CPython plus Pillow native primitives. .NET is not
  involved. Blender and StudioMDL are outside the stage. The only measured
  production DLL work inside simplification was 0.050 s of meshoptimizer;
- process snapshots show no Blender or worker process before or after a run.
  One already-open `GmodAddonOptimizer.exe` kept the same PID throughout and
  did no measured-stage work. Every run used a new Python PID, so the process-
  local DLL handle cache was rebuilt; only Windows file pages could stay warm.

## Cross-check on three region types

These short detailed checks use the same 512 samples, eight views, 256px masks,
budgets, gates, and production functions.

The originally planned Ford/Ferrari/Caterham prepared work trees were no longer
present (only their frozen result/panel outputs remained), so equivalent frozen
holdout regions were selected by the requested properties instead: Toyota for
glass/multimaterial, Dodge for actual mixed weights, and Pontiac for a small
simple component. This avoids regenerating or changing a benchmark input.

| Type | Region | Triangles | Prepare | Measure | Result |
|---|---|---:|---:|---:|---|
| Body glass in a 15-material source | Toyota `supra_glass_detail` | 344 → 222 | 0.173 s | 0.412 s | Pass |
| Mixed-weight skinning, 430 blended vertices | Dodge `grey` | 464 → 394 | 0.538 s | 3.032 s | Fails exact silhouette/material boundary; local restore remains required |
| Small rigid/simple region | Pontiac `rim2` | 150 → 126 | 0.623 s | 1.192 s | Pass |

The same validation hierarchy is visible in all three. In the skinned example,
1.467 s is silhouette work and 2.028 s is the bidirectional metric pass, of
which 1.967 s is nearest search. Skinning arithmetic itself is not the cause of
the delay. The small region also pays a 1.216-second fixed silhouette cost,
showing that triangle count alone does not predict validator time.

## Exact equivalence proof

No production algorithm was modified. A production-function trace and the
detailed trace produced identical ordered SHA-256 fingerprints for:

- 18 deterministic sample arrays;
- 19 simplifier input/output array contracts;
- 19 candidate regions;
- nine complete `RegionMetrics` records;
- nine budgets, gate decisions, and margins;
- all 9,216 nearest-query identities.

All six fingerprint families match exactly. All ten unprofiled runs have one
normalized semantic hash, one composed adaptive-source hash, and one frozen
compiled-family hash. Their 19 region representations, restored regions,
selected triangles/vertices, target ratios, render escalations, and failed
gates are identical after excluding only evaluation/cache counters.

The region-detail hash is exactly the frozen hash
`05ef3eaff92109ff1cb6f62705779f8f476d2bfe0d98d5f21d54ef112f1f2f4a`.
The composed source hash is
`e8ab5f195be9954441d2a6b21dbb9b665e06f07c843fb9b38a423ea4fcbd84b8`.
It is identical to the frozen pre-compile source after excluding the compiler's
generated `_OPT.qc` and `output/*.log`. The frozen compiled-family hash is
`fb83a6b549963073245a9bb637be0de733c2fd6eba74328c322f323addf3d04b`.

| Frozen compiled artifact | Bytes | SHA-256 |
|---|---:|---|
| `wheel.dx90.vtx` | 172,821 | `ba3bc228d99b98be4fceb3e5059dbc9813131c25d93d74e2f5828f46960c9bd6` |
| `wheel.mdl` | 2,768 | `0f87981db015dd1a084c646282a12f0ea467e96e0b56686870da0f2c2cf8b153` |
| `wheel.phy` | 3,387 | `fa3e392bdd115a77570f8f323fc5f8ae28590e7843744b0eaefcd116a905cb3c` |
| `wheel.vvd` | 654,208 | `7c74961cec9ae40674eb1832385fd14f4097b5916df72a396a34ee32bc01fce2` |

The timed harness did not invoke StudioMDL. These are the deterministic frozen
artifacts tied to the byte-identical pre-compile source contract.

## Amdahl ceilings

The following ceilings apply the low-overhead category fractions to the
29.342-second cold median and make only one category infinitely fast:

| Category made free | Best possible cold median |
|---|---:|
| Candidate measurement | 14.076 s |
| Reference preparation | 21.010 s |
| Risk measurement | 27.293 s |
| Simplification | 27.359 s |
| Cache writes | 27.717 s |
| Reference + candidate validation together | 5.744 s |

If combined validation alone becomes 2x, 3x, 4x, or 5x faster, Amdahl predicts
17.543 s, 13.610 s, 11.644 s, or 10.464 s respectively. This is why the next
work must target multiple validation internals rather than meshoptimizer.

For comparison with the frozen 27.117-second adaptive / 56.012-second full run,
the table below keeps every non-targeted stage constant. Each cell is
`adaptive seconds / full-run seconds`; cumulative categories are not combined
except for the explicit validation row.

| Target | Fraction of adaptive | At 2x | At 4x | If eliminated |
|---|---:|---:|---:|---:|
| Candidate measurement | 52.029% | 20.063 / 48.957 | 16.536 / 45.430 | 13.008 / 41.903 |
| Reference preparation | 28.395% | 23.267 / 52.162 | 21.342 / 50.237 | 19.417 / 48.312 |
| Risk measurement | 6.984% | 26.170 / 55.065 | 25.697 / 54.591 | 25.223 / 54.118 |
| Simplification | 6.757% | 26.201 / 55.096 | 25.743 / 54.638 | 25.285 / 54.179 |
| Cache writes | 5.539% | 26.366 / 55.261 | 25.991 / 54.885 | 25.615 / 54.510 |
| Reference + candidate validation | 80.424% | 16.213 / 45.107 | 10.761 / 39.655 | 5.309 / 34.203 |

Even eliminating candidate measurement alone bottoms out at 13.008 seconds on
the frozen stage, while eliminating BVH construction (1.641% in the detailed
hierarchy) would save well under one second. A combined exact validation
optimization is mathematically required for the 10–12-second goal.

## Ranked opportunities

1. **Exact fused silhouette kernel from raw masks (cold):** the silhouette
   parent is 58.570% of detailed wall. Boundary extraction + 2D KD build/query
   alone account for 14.404 s (42.805%) and are almost entirely Python. This is
   the best measured cold target.
2. **Exact batched 3D nearest/attribute kernel (cold):** 9,216 queries consume
   5.920 s (17.594%). BVH construction is only 0.552 s, so replacing the builder
   alone is not useful. The batch must preserve closest-triangle tie-breaking,
   barycentrics, interpolated normal/UV/weights, and bidirectional order.
3. **Authenticated validation-result cache (warm):** exact validation repeats
   for 20.632 s in a 23.970-second warm run. Caching metrics/decision against
   source, candidate payload, profile, attribute contract, metric-code hash,
   views, samples, resolution, and pose contract could make repeat runs much
   faster, but it provides no first-run benefit.

The previously tested Python EDT, native full-grid EDT, and native compact EDT
are not recommended. They preserved output but did not improve the real stage;
the compact variant left the now-measured Python boundary extraction and point
marshalling in place.

## Exact next experiment design — not started

The recommended next experiment is a **fused native boundary-distance kernel
that consumes the existing raw `L` masks**. It is deliberately narrower than a
full validator rewrite and different from the rejected compact EDT.

1. Keep Pillow rasterization, all 512 samples, all eight views, 256px masks,
   frame math, IoU, global p95, gates, budgets, and tolerances unchanged.
2. Pass the eight original and eight candidate mask byte buffers in one batched
   call to the existing Release bridge.
3. In native code, extract the exact four-neighbour boundary in the current
   row-major order, compute exact bidirectional squared Euclidean distances,
   and return only the ordered compact distance vector plus per-view offsets.
   Do not return a 65,536-cell field and do not construct boundary tuples in
   Python.
4. Keep square-root conversion and the single global p95 in Python initially,
   so the current aggregation contract remains directly comparable.
5. First compare at least 10,000 asymmetric/random masks against the current
   `_boundary_points` + KD oracle, including empty, single-pixel, border,
   disconnected, and mirrored masks. Require exact ordered integers.
6. Then require identical Pontiac sample, array, metric, gate, region, source,
   and compiled hashes. Run five cold and five warm fresh-process measurements.
7. Instrument native compute and Python transfer separately. Promote only if
   all hashes match and cold median improves by at least 20% (29.342 → 23.474 s
   or better). Otherwise discard it without changing production.

The specific risks are changing border-pixel semantics, using eight-neighbour
instead of four-neighbour extraction, changing row-major point order, changing
empty-mask behavior, reordering distances before global p95, mishandling Pillow
buffer lifetime/stride, or introducing an ABI/package mismatch. Each risk has
an exact oracle or existing release-contract check above; any mismatch is a
hard discard, not a tolerance adjustment.

This experiment can validate the largest measured cold hypothesis. It cannot
by itself guarantee 10–12 seconds; after its result, the Amdahl table must be
recomputed before deciding whether the exact 3D batch is justified.

No experiment above is active. Await explicit approval before implementation.
