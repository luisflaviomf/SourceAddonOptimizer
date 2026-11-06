# Maximum v2: exact raw-mask silhouette kernel result

Date: 2026-07-21  
Branch: `feature/models-maximum-adaptive-v2`  
Verdict: **promote the implementation technically, but keep it opt-in and unshipped until explicit integration approval**.

## Executive result

The fused raw-mask kernel met every promotion gate without changing a mask,
metric, gate, candidate, region, source array, compiled byte, or canonical hash.
On the balanced warm Pontiac benchmark, silhouette work fell from 4.945754 s
to 1.430013 s (3.459x, -71.09%) and the complete adaptive stage fell from
8.831737 s to 5.329917 s (-39.65%). A paired real render/StudioMDL run fell
from 23.490257 s to 19.398184 s (-17.42%).

This is not in the approved WPF executable. The default Python KD path is
unchanged; the experiment is selected only with
`MAXIMUM_SILHOUETTE_EXPERIMENT_DLL`. No WPF code, worker command, packaged
resource, approved DLL, or release executable was changed.

The absolute baseline in this session is much faster than the previous
24.931 s warm profile. Cross-session absolute values are therefore not used
to claim the gain. The result is based on fresh-process A/B runs interleaved
on the same frozen input and cache state, with exact semantic hashes checked
after every run.

## Why this is structurally different from the rejected EDT

The rejected compact EDT still made Python extract boundary tuples, allocate
point arrays, marshal them into native code once per view, and receive all
distances back for Python percentile work. It changed the distance primitive
but retained most of the pipeline and boundary-crossing overhead.

The new path changes that data flow:

1. Python supplies the original and candidate raw `L` masks as contiguous
   buffers with explicit fixed-width dimensions and strides.
2. One native call processes all eight views (16 masks) for a regional
   comparison.
3. The native call extracts exact row-major four-neighbour boundaries,
   computes both ordered squared-distance directions, counts intersection and
   union, and selects the exact p95 squared value.
4. Thread-local vectors reuse boundary, EDT, and distance storage.
5. The normal path returns only eight intersection/union pairs and one p95
   integer. Full boundaries and ordered distances use optional caller-owned
   buffers only in oracle tests.
6. Python preserves its original float64 division, `max` order, and
   `math.sqrt` operation on the selected integer.

The gain therefore comes from eliminating repeated Python boundary/KD work,
large intermediate objects, per-view native crossings, and large return
buffers—not from substituting one EDT implementation for another.

## Implementation

- `maximum_optimizer/native/meshopt_bridge.cpp` adds experimental export
  `maximum_silhouette_metrics_raw_batch_v1`; native ABI/version remain 3/10200.
- `maximum_optimizer/silhouette_native.py` defines the fixed-width ctypes ABI,
  validates layout/binary masks, packs reusable buffers, and reconstructs the
  original float64 result order.
- `maximum_optimizer/metrics.py` retains the approved KD implementation and
  selects the raw-mask path only through the explicit environment variable.
  Experimental prepared references retain contiguous original masks in fields
  excluded from equality/repr semantics.
- `tests/maximum_optimizer/test_silhouette_native.py` contains seven test
  methods covering the exact contract and prepared-metric integration.
- `benchmarks/lvs_models_adaptive/_raw_mask_silhouette_experiment.py` contains
  the deterministic oracle, frozen pipeline checks, balanced A/B runner,
  cross-model runner, full compile verifier, memory sampler, and the isolated
  `_nearest` diagnostic.

The performance path makes nine regional native calls on this warm pipeline;
each call batches all eight canonical views.

## Test and equivalence evidence

No tolerance was introduced. All floating results compared bit-for-bit.

| Check | Baseline / frozen | Experiment | Result |
|---|---|---|---|
| Deterministic mask oracle | Existing boundary/KD semantics | 10,000 asymmetric/random and edge pairs | Exact; contract `c3370f5d...19c9` |
| Cold pipeline semantics | `5335138a...086` | `5335138a...086` in every run | Exact |
| Warm pipeline semantics | `7d237847...7f1` | `7d237847...7f1` in every run | Exact |
| Toyota body/glass/material metrics | `f64a8cdf...c6b4` | `f64a8cdf...c6b4` | Bitwise exact, same pass |
| Dodge bones/mixed weights metrics | `49762fe4...fbe8` | `49762fe4...fbe8` | Bitwise exact, same silhouette/material failure |
| Pontiac simple region metrics | `b48bd5c...077` | `b48bd5c...077` | Bitwise exact, same pass |
| Region/array payloads | Frozen positions, indices, normals, UVs, weights | Same SHA-256 values | Exact |
| Final family | 13,530 triangles, 19 evaluations, 5 lighter + 14 restored | Same | Exact |
| Compiled DX90 tree | `fb83a6b5...04b` | `fb83a6b5...04b` | Byte-identical MDL/VVD/DX90/PHY |
| Comparable bytes | 833,184 | 833,184 | Exact |
| DX80 / integrity failures | 0 final DX80 / 0 failures | 0 / 0 | Exact |

The compiled Pontiac family retains the existing output quality and size:
981,876 original comparable bytes become 833,184 bytes, saving 148,692 bytes
(15.144%) after always excluding/removing 32,980 DX80 bytes. This experiment
only accelerates validation; it intentionally does not change compression.

The native edge tests explicitly cover empty/full masks, one pixel, image-edge
contact, disconnected components, tiny and odd dimensions, tied distances,
padded row/view strides and offsets, maximum squared distance, horizontal and
vertical orientation, float64 versus float32 sensitivity, non-binary input,
invalid layouts, and multi-view batching.

## Benchmark method

- Frozen Pontiac input and profile hash
  `6029d5a8...c9d9`.
- Fresh Python/DLL process for every run.
- Cold: five runs per lane, unique initially empty cache, balanced order
  `A,B,B,A,A,B,B,A,A,B`.
- Warm: ten runs per lane, the same populated cache, balanced order
  `(A,B,B,A) x 5`.
- No heavy profiler in final timing. The outer silhouette timer and 20 ms RSS
  sampler are low overhead.
- No samples or outliers were removed.
- `wall` below is the isolated pipeline with compile/render stubs; the separate
  real full-family comparison follows.

## All cold measurements (seconds)

| Run | Baseline adaptive | Baseline silhouette | Baseline wall | Experiment adaptive | Experiment silhouette | Experiment wall |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 9.202641 | 4.797379 | 11.709276 | 5.798596 | 1.403716 | 8.278992 |
| 2 | 9.232114 | 4.799093 | 11.697892 | 5.884935 | 1.396001 | 8.359256 |
| 3 | 9.264402 | 4.819571 | 11.714471 | 5.816554 | 1.403370 | 8.316678 |
| 4 | 9.194294 | 4.780297 | 11.672620 | 5.804444 | 1.397924 | 8.244653 |
| 5 | 9.311211 | 4.849376 | 11.795845 | 5.823054 | 1.404903 | 8.335917 |

## All warm measurements (seconds)

| Run | Baseline adaptive | Baseline silhouette | Baseline wall | Experiment adaptive | Experiment silhouette | Experiment wall |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 9.206269 | 4.930367 | 11.730213 | 5.336225 | 1.443235 | 7.801374 |
| 2 | 8.800894 | 4.945113 | 11.272984 | 5.462087 | 1.459338 | 7.909394 |
| 3 | 8.846485 | 4.973579 | 11.295332 | 5.244174 | 1.413413 | 7.670514 |
| 4 | 8.825650 | 4.946396 | 11.299399 | 5.327360 | 1.445347 | 7.829518 |
| 5 | 8.769458 | 4.927473 | 11.256122 | 5.359408 | 1.441003 | 7.858338 |
| 6 | 8.837825 | 4.981266 | 11.369562 | 5.332474 | 1.432171 | 7.783143 |
| 7 | 9.042885 | 5.117148 | 11.531143 | 5.310447 | 1.424223 | 7.757775 |
| 8 | 8.774508 | 4.926673 | 11.274880 | 5.287997 | 1.419598 | 7.739694 |
| 9 | 8.999165 | 5.028910 | 11.470888 | 5.322772 | 1.426012 | 7.776176 |
| 10 | 8.742553 | 4.905422 | 11.195876 | 5.357252 | 1.427856 | 7.697034 |

## Distribution and A/B result

Sample standard deviation and coefficient of variation (CV) include every run.

| State / metric | Baseline median (min-max; SD; CV) | Experiment median (min-max; SD; CV) | Absolute gain | Improvement / speedup |
|---|---:|---:|---:|---:|
| Cold silhouette | 4.799093 (4.780297-4.849376; 0.026453; 0.550%) | 1.403370 (1.396001-1.404903; 0.003953; 0.282%) | 3.395723 s | 70.76%; 3.420x |
| Cold adaptive | 9.232114 (9.194294-9.311211; 0.047982; 0.519%) | 5.816554 (5.798596-5.884935; 0.034589; 0.594%) | 3.415560 s | 37.00%; 1.587x |
| Cold wall | 11.709276 (11.672620-11.795845; 0.046401; 0.396%) | 8.316678 (8.244653-8.359256; 0.045640; 0.549%) | 3.392598 s | 28.97%; 1.408x |
| Warm silhouette | 4.945754 (4.905422-5.117148; 0.063100; 1.270%) | 1.430013 (1.413413-1.459338; 0.013867; 0.968%) | 3.515741 s | 71.09%; 3.459x |
| Warm adaptive | 8.831737 (8.742553-9.206269; 0.149565; 1.683%) | 5.329917 (5.244174-5.462087; 0.056283; 1.055%) | 3.501820 s | 39.65%; 1.657x |
| Warm wall | 11.297366 (11.195876-11.730213; 0.162687; 1.431%) | 7.779660 (7.670514-7.909394; 0.072072; 0.926%) | 3.517706 s | 31.14%; 1.452x |

The maximum warm result (5.462087 s adaptive) is still 37.25% faster than the
minimum warm baseline (8.742553 s). The gain is larger than the complete
observed distributions and is not an outlier artifact.

## Cross-model performance

Each region used five measurements per lane in balanced order. Metrics and
decisions were exact in every run.

| Model type | Whole metric median A -> B | Whole improvement | Silhouette median A -> B | Silhouette speedup |
|---|---:|---:|---:|---:|
| Toyota curved body/glass/multi-material, 344 -> 222 triangles | 0.504899 -> 0.194307 s | 61.52%; 2.598x | 0.353989 -> 0.040321 s | 8.779x |
| Dodge bones/mixed weights, 464 -> 394 triangles | 1.183941 -> 0.828605 s | 30.01%; 1.429x | 0.408640 -> 0.054834 s | 7.452x |
| Pontiac small/simple, 150 -> 126 triangles | 0.629200 -> 0.275001 s | 56.29%; 2.288x | 0.383641 -> 0.026818 s | 14.306x |

The primary Pontiac wheel full pipeline is the cold/warm benchmark above. The
gain therefore appears in all four requested categories, including skinning
and the simple model; no simple-model regression exists.

## Real full-family impact

This paired run includes inventory, the adaptive stage, one targeted Blender
render, one StudioMDL compile, fallback, packaging, and compiled-hash checks.

| Metric | Baseline | Experiment | Difference |
|---|---:|---:|---:|
| Full wall | 23.490257 s | 19.398184 s | -4.092073 s (-17.42%, 1.211x) |
| Adaptive stage | 9.354342 s | 5.762743 s | -3.591599 s (-38.39%, 1.623x) |
| Sampled CPU | 16.031250 s | 12.609375 s | -3.421875 s |
| Peak working set | 680,886,272 B | 679,378,944 B | -1,507,328 B (-0.221%) |
| Compiled result | 833,184 B / `fb83a6b5...04b` | same | Exact |

The paired current-session full total is used instead of the older 56 s full
run because Blender/StudioMDL and machine conditions drifted substantially.

## New silhouette decomposition

Medians of ten warm experiment processes. Child native phases are contained in
the native-call row and are shown to explain it, not added twice. The
`silhouette_total_ns` diagnostic (0.461724 s median) covers candidate calls;
the outer 1.430013 s measurement also covers original reference projection.

| Phase | Median | Share of outer silhouette |
|---|---:|---:|
| Original + candidate mask projection/packing | 1.328491 s | 92.901% |
| Input marshaling | 0.000588 s | 0.041% |
| Native call total | 0.098453 s | 6.885% |
| - boundary extraction | 0.012884 s | 0.901% |
| - exact distances | 0.085219 s | 5.959% |
| - metric selection/counting | 0.000149 s | 0.010% |
| Output marshaling | 0.000145 s | 0.010% |
| Caller postprocessing | 0.000089 s | 0.006% |
| Outer residual/timer boundaries | 0.002248 s | 0.157% |

The accounted non-child phases cover 99.843% of outer silhouette time.
Marshaling plus caller postprocessing is only 0.822 ms. After fusion, mask
projection—not native distance work or boundary crossings—is the remaining
silhouette cost.

## Memory and allocations

| Measurement | Baseline | Experiment | Delta |
|---|---:|---:|---:|
| Cold peak RSS median | 134,623,232 B | 135,421,952 B | +798,720 B (+0.593%) |
| Warm peak RSS median | 140,367,872 B | 141,473,792 B | +1,105,920 B (+0.788%) |
| Warm surviving Python block delta median | 6,879 | 7,781 | +902 (+13.11%) |
| Real full-run peak RSS | 680,886,272 B | 679,378,944 B | -1,507,328 B (-0.221%) |

Every fresh experimental process grew native scratch capacity eight times for
5,511,176 bytes (5.256 MiB peak reusable scratch) and grew ten Python buffers
by 5,242,880 bytes (5.000 MiB). Those counts/bytes were identical across all
ten warm runs. `sys.getallocatedblocks()` is a count of surviving blocks at
the end, not total allocation traffic; it is reported only as a directional
indicator. RSS remained stable and the real full run did not increase peak
memory. Thread-local native scratch and the reusable Python bytearray are not
freed between regional calls; there are no per-call scratch releases, and both
are released when the fresh benchmark process exits.

## Updated Amdahl analysis and `_nearest`

Using the balanced warm medians:

- Baseline silhouette share: 4.945754 / 8.831737 = 56.00%.
- Observed silhouette speedup: 3.4585x.
- Amdahl prediction with only silhouette changed:
  `8.831737 - 4.945754 + 1.430013 = 5.315996 s`.
- Observed adaptive median: 5.329917 s, only 0.013921 s from the prediction.
- Remaining silhouette: 1.430013 s, or 26.83% of the new adaptive stage.
- Non-silhouette adaptive floor if silhouette became free: 3.899904 s;
  maximum further speedup from silhouette alone is only 1.367x.

A separate low-overhead diagnostic wrapped the unchanged `_nearest` function
without entering the final A/B medians. Both lanes made exactly 9,216 calls
and retained semantic SHA `7d237847...7f1`:

| Lane | `_nearest` | Adaptive | Share of adaptive |
|---|---:|---:|---:|
| Baseline | 1.977563 s | 8.644930 s | 22.88% |
| Experiment | 1.999822 s | 5.288637 s | 37.81% |

`_nearest` is now the next justified adaptive target because its absolute cost
did not change while its share rose to 37.8%. It was not optimized here.

The new paired real full time is 19.398184 s: 7.398184 s above 12 s and
9.398184 s above 10 s. Its measured non-adaptive portion is already
13.635441 s, so eliminating `_nearest` alone cannot reach a 10-12 s full-family
target. Future work would also have to address inventory/render/compile fixed
costs. The isolated stubbed pipeline is already 7.779660 s warm.

## Promotion gates

| Gate | Required | Measured | Result |
|---|---:|---:|---|
| Output/decision equality | Exact | Bitwise/canonical/compiled exact | Pass |
| Warm silhouette | >=3x | 3.459x | Pass |
| Warm adaptive stage | >=25% | 39.65% | Pass |
| More than one model type | Yes | Four requested categories | Pass |
| Simple-model regression | None relevant | 56.29% faster whole metric | Pass |
| Memory | Stable/acceptable | +0.788% isolated warm; -0.221% full | Pass |
| Stability | Reproducible | CV 0.968% silhouette / 1.055% adaptive | Pass |

Conclusion: **promote**. Here “promote” means retain the exact opt-in source as
a successful experiment and request integration approval. It does not mean
the WPF/release currently uses it.

## Limitations

- The experimental DLL is external to the repository/release and must not be
  mistaken for the approved runtime DLL.
- The kernel makes validation faster but cannot improve compression or visual
  quality because all outputs are deliberately identical.
- Mask projection now dominates silhouette time; further silhouette work would
  need to accelerate that exact projection without changing raster semantics.
- `_nearest` is the largest newly exposed adaptive target, but full-family
  10-12 s also requires non-adaptive work.
- Cross-session absolute timing drift is large; only paired/interleaved runs
  from this session support the performance claim.

## Files changed

- `maximum_optimizer/native/meshopt_bridge.cpp`
- `maximum_optimizer/silhouette_native.py`
- `maximum_optimizer/metrics.py`
- `tests/maximum_optimizer/test_silhouette_native.py`
- `benchmarks/lvs_models_adaptive/_raw_mask_silhouette_experiment.py`
- `docs/superpowers/plans/2026-07-21-models-maximum-raw-mask-silhouette-kernel.md`
- this report and the benchmark-results index

No file under `gui/` or the WPF project was changed.

## Artifact locations

- Evidence root:
  `D:\gaco-max-v2-raw-mask-silhouette-20260721`
- 10,000-mask oracle:
  `equivalence\mask-oracle-10000.json`
- Frozen pipeline A/B:
  `equivalence\pipeline-baseline-02.json`,
  `equivalence\pipeline-experiment-01.json`
- Cross-model exactness:
  `equivalence\cross-models.json`
- Full compile verification:
  `equivalence\full-compile.json`
- Thirty final benchmark records and order:
  `benchmark-final\runs\*.json`, `benchmark-final\run-index.json`
- `_nearest` diagnostic:
  `benchmark-final\breakdown-nearest-{baseline,experiment}.json`
- Cross-model repeated timings:
  `benchmark-final\cross-model-performance.json`
- Paired real full run:
  `full-ab-final\full-ab.json`
- Experimental DLL (not packaged), SHA-256
  `740436f6147c189e0cd9deef9d7fffd1d136da018b54789f6416cc284db55e47`:
  `bin\meshopt_bridge.raw-mask-experiment.dll`

The approved repository DLL remains SHA-256
`d5c944b8eabeaa17bea055aaa7a098cd11500725c37410e24a1b2eb61490da45`.

## Final verification

- Fresh native rebuild with the Visual Studio CMake tool completed with exit
  code 0; its DLL is byte-identical to the benchmark DLL at SHA-256
  `740436f...e47`.
- Default environment: `python -m unittest discover -s tests -t . -v` ran 89
  discovered tests in 4.073 s: the 82 pre-existing tests passed and the seven
  explicitly experimental tests skipped because no experiment DLL was set.
- Explicit experiment DLL: the same command ran all 89/89 tests in 3.431 s,
  with zero failures and zero skips.
- The approved DLL/profile hashes remained respectively `d5c944b8...a45` and
  `6029d5a8...c9d9`.
- The WPF tree has no experiment diff, and the resource ZIP contains no entry
  whose name includes `raw-mask` or `experiment`.
- `build_release_wpf.ps1` was intentionally not run.
