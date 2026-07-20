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
| Caterham 620R | original preserved, DX80 removed | 3,733,632 | 3,148,070 | 15.68% |
| Ferrari 365 Fullrig | direct position error `0.005` | 5,801,070 | 3,827,338 | 34.02% |
| Ford Fairlane | direct position error `0.005` | 17,440,001 | 10,402,479 | 40.35% |
| VW Touareg | direct position error `0.005` | 8,331,738 | 5,349,321 | 35.80% |
| **Total** | per-family safe selection | **41,624,655** | **27,089,243** | **34.92%** |

Median family reduction is 34.02%. Four families promoted geometry candidates. The
Caterham `0.005` candidate passed the visual gates but was not promoted because its
non-DX80 compiled result was 1,787 bytes larger than preserving the original
non-DX80 artifacts. This is the intended safety behavior, not a failed run.

## Worst promoted metrics

The worst values across the four promoted families remained below every calibrated
hard limit:

| Metric | Measured worst | Limit |
|---|---:|---:|
| silhouette error | 0.003973 | 0.0125 |
| RGB MAE | 0.001392 | 0.0015 |
| edge error | 0.066157 | 0.105 |
| bidirectional surface p95 | 0.001466 | 0.0018 |
| surface maximum | 0.004085 | 0.0125 |
| normal angle p95 | 89.999898 | 95.0 |
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

This is evidence for the tested LVS car families, not a promise that every addon
will lose 34.92%. Safety remains per-family and fail-closed.
