# Maximum optimizer validation

## Corrected production frontier

The production profile `maximum-lvs10-conservative-v1` was validated again with
the real material roots from both `lvs_cars_pack` and `lvs_framework`. Every family
first passed an exact ratio-1 roundtrip control. Candidate renders covered textured
and clay passes, bind and representative poses, canonical angles, and reachable
bodygroup states. Missing texture evidence was zero.

The earlier 42.13% result is **rejected evidence**. Its render path did not resolve
the complete shared material set, so its low RGB and edge errors could not authorize
production. The table below replaces that claim with results from the corrected
frozen-worker pipeline.

Original size is the real Workshop model-family size including DX80. Final size is
the promoted candidate, or the original family when geometry is not smaller, with
`.dx80.vtx` always excluded as required by the Maximum output contract.

| Family | Selected result | Original bytes | Final bytes | Reduction |
|---|---|---:|---:|---:|
| VW Beetle | direct position error `0.00625` | 6,318,214 | 4,362,035 | 30.96% |
| Caterham 620R | direct position error `0.01` | 3,733,632 | 3,027,319 | 18.92% |
| Ferrari 365 Fullrig | direct position error `0.0075` | 5,801,070 | 3,558,115 | 38.66% |
| Ford Fairlane | direct position error `0.00625` | 17,440,001 | 9,924,552 | 43.09% |
| VW Touareg | direct position error `0.005` | 8,331,738 | 5,349,321 | 35.80% |
| **Total** | per-family safe selection | **41,624,655** | **26,221,342** | **37.01%** |

Median family reduction is 35.80%. All five families promoted a geometry candidate
in the 2026-07-20 frozen-worker rerun. Caterham illustrates why the optimizer must
search a measured frontier instead of accepting the first passing candidate: its
passing `0.005` result saved only 0.55% of the complete family, whereas `0.01`
passed the same gates and produced a real 18.92% final reduction.

## Worst promoted metrics

The worst values across the five promoted families remained below every calibrated
hard limit:

| Metric | Measured worst | Limit |
|---|---:|---:|
| silhouette error | 0.006600 | 0.0125 |
| RGB MAE | 0.001392 | 0.0015 |
| edge error | 0.094319 | 0.105 |
| bidirectional surface p95 | 0.001559 | 0.0018 |
| surface maximum | 0.004459 | 0.0125 |
| normal angle p95 | 90.000188 | 95.0 |
| UV error p95 | 0.462965 | 0.8 |
| skinning error p95 | 0.002046 | 0.00225 |

The Beetle `0.0075` candidate was correctly rejected even though its size was
smaller: RGB MAE reached 0.001675 against the 0.0015 limit. The more conservative
`0.00625` candidate passed at 0.001392 and became the selected Beetle frontier.

## Production behavior

Maximum searches the direct error ladder beginning at `0.005`, then `0.00625`, and
continues toward more aggressive candidates. It promotes only the smallest compiled
candidate that passes all structural and visual gates and produces a strictly
positive saving. A rejected or non-beneficial family falls back to the original
artifacts. In every outcome, `.dx80.vtx` is removed from the final output and final
size accounting.

The corrected evidence runs were:

- Beetle conservative and rejection boundary: `maximum-e2e-mini-beetle-v10`
- Ferrari, Fairlane, and Touareg: `maximum-e2e-lvs-four-v11`
- Caterham corrected material-root rerun: `maximum-e2e-caterham-v12`
- Beetle intermediate frontier: `maximum-e2e-mini-beetle-v13`
- Frozen serial/Auto equivalence rerun: `C:\mbp0719` (2026-07-20)

This is evidence for the tested LVS car families, not a promise that every addon
will lose 37.01%. Safety remains per-family and fail-closed.

## Balanced parallel validation

Performance validation compares cold `--maximum-jobs 1` and
`--maximum-jobs 0` runs over identical copies of the same five-family LVS sample.
The two runs use distinct work, output, candidate-cache, reference-cache, and VTF
cache directories so neither run inherits evidence produced by the other.

The measured run used an Intel Core i7-13650HX (20 logical processors), 31.73 GiB
RAM, Blender 5.0.1, and the frozen worker built from this branch. Blender's embedded
Python could not read very long state-manifest paths on Windows, so the cold work
directories were deliberately short: `C:\mbp0719\s` and `C:\mbp0719\a`.

The two commands differed only in `--maximum-jobs` and their isolated input, work,
and output paths:

```text
GModAddonOptimizerWorker.exe <serial-copy> --work C:\mbp0719\s --optimizer-mode maximum --maximum-jobs 1 ...
GModAddonOptimizerWorker.exe <auto-copy>   --work C:\mbp0719\a --optimizer-mode maximum --maximum-jobs 0 ...
```

Input inventory was 25 files / 41,624,655 bytes in both copies. Its canonical
path-to-SHA-256 map digest was
`653d6dadeb5ddb2f78a918dd026fc26d0c01e472e6f58de1d80d3806c6950cd3`.
The worker SHA-256 was
`bafc96f797901ecb1b9debe1187f57a04c8129d322de02d75f6d40f7418ed511`;
the fidelity profile SHA-256 was
`79e4bdc17639e1ab6f98e6c0b5af9baec5ab0ebafab9fc0987c11609c9f739c7`.

| Run | Elapsed | Average process-tree CPU | Peak working set | Peak active families |
|---|---:|---:|---:|---:|
| Serial, `--maximum-jobs 1` | 12,644.92 s (3:30:44.92) | 5.53% | 2.12 GiB | 1 |
| Auto, `--maximum-jobs 0` | 5,897.10 s (1:38:17.10) | 15.77% | 4.88 GiB | 5 |

Auto was **2.144x faster** and removed 6,747.82 seconds, a **53.36% elapsed-time
reduction**. It produced the same five selected candidate IDs, attempt/gate
decisions, 26,221,342 final bytes, relative artifact paths, and artifact SHA-256
values as serial. The canonical final path-to-SHA-256 map digest was
`bdfd242ab8fa93c1f3fd2986d64eda877bab1e24d871c8094c940385dd4f564d`.
Both outputs contained 20 files and zero `.dx80.vtx` files.

This five-family measurement did not reach the plan's aspirational 3x speedup or
35-65% average CPU range, so no such claim is made. The measured bottleneck was
bounded parallel supply plus a long tail: a 20-thread machine targets 10 balanced
jobs, but this corpus exposed only five independent families, and after four
families completed the Fairlane tail ran alone. CPU was around 22-23% while all
five families were busy and fell during the tail, yielding 15.77% overall. The RAM
guard also reported transient capacities from 3 to 10 and remained fail-safe at a
4.88 GiB measured peak. A real 163-family addon can supply all 10 CPU-target jobs,
but its speed and CPU utilization remain unmeasured and are not extrapolated here.
