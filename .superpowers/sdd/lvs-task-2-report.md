# LVS R&D Task 2 report

## Outcome

The fixed LVS corpus and benchmark harness are implemented. The committed corpus has exactly the
approved ten ordered families, with the pressure partition equal to the first five. It imports the
existing original, Blender 0.50 and Fidelity 0.50 artifacts by verified size and SHA-256 without
rerunning those stages. The benchmark record and report schemas are immutable/strict in Python and
serialized as canonical JSON.

No committed JSON contains an absolute workstation path. Roots are expanded only from
`LVS_SOURCE_ROOT`, `LVS_ORIGINAL_MODELS_ROOT`, `LVS_BLENDER_MODELS_ROOT` and
`LVS_FIDELITY_MODELS_ROOT` at validation time.

## Exact baseline generation

`corpus.json` was frozen from these supplied trees:

- the successful `manual_lvs_prune_probe/work/decompile/src` tree;
- the original `manual_lvs_prune_probe/input/lvs_cars_pack/models` tree;
- the existing `mixedfinal50h8/.../b050/compiled/models` tree;
- the existing `mixedfinal50h8/.../f050/compiled/models` tree.

The command used was `python benchmark_lvs_models.py freeze` with those four paths passed through
the corresponding CLI options. The paths are intentionally described here by experiment-relative
identity rather than copied into machine-dependent JSON. After setting the four documented
environment variables, this command generated the committed report:

```text
python benchmark_lvs_models.py import-baselines --output benchmarks/lvs_models/baseline.json
```

The import re-read every declared file and verified its exact size, SHA-256 and sidecar membership.
It produced 30 records: ten original, ten Blender and ten Fidelity. The experiment lane remains
empty. Control is kept separate in `control.json` because one strict roundtrip failed and must not
be silently substituted.

The byte evidence is:

| Lane | Median geometry-comparable bytes | Worst geometry-comparable bytes | DX80 optional bytes |
| --- | ---: | ---: | ---: |
| original | 8,302,033.5 | 21,590,901 | 20,503,604 |
| blender | 4,815,538 | 12,431,766 | 11,002,947 |
| fidelity | 4,848,935.5 | 12,486,662 | 11,058,633 |

These are size measurements only. `baseline.json` has `quality_status: unverified` and
`quality_claim: null`; no visual or structural quality claim is made.

## Control evidence

The strict control adapter creates private copies under the ignored `.superpowers` run root and
invokes StudioMDL directly against the unchanged decompiled QC. It exposes no autofix flag and
records `autofixes: false` and `source_mutated: false`. It never calls `batch_compile_opt_qc.py` or
the prior helpers that repair QCs/SMDs.

The real control run used Garry's Mod StudioMDL version `2026.04.29`, size `2,255,696` bytes and
SHA-256 `9e40454499890d0de941068f2b47db4d3be69ab7213f95e4d9c09f255a7230cf`. Its machine
location was supplied through `STUDIOMDL_EXE` and is not committed.

Nine unchanged QCs compiled successfully: Charger, Monaco Police, Supra, Skyline GTR32, Beetle,
Touareg, Ferrari 365 full rig, Caterham 620R and TransAm wheel. Ford Fairlane failed explicitly
with StudioMDL return code `0xFFFFFFFF`: QC line 2053 declares duplicate animation name
`turn_left_corrective_animation`, followed by `Aborted Processing`. `control.json` commits the
successful artifact hashes/sizes and the exact failure, but no binaries. The command returned exit
1 because the lane is intentionally fail-closed. No success record was fabricated and no source
was touched.

## Tests and safety evidence

The focused TDD suite covers fixed partitions, environment-only roots, containment, hash and size
drift, exact sidecars, immutable record roundtripping, strict unknown-field rejection, compiled-byte
decomposition, cache invalidation by source/tool/script/settings, canonical JSON, lane separation,
median/worst summaries and absence of quality claims without gates. The real-symlink test is skipped
on this Windows host because creating symlinks returns WinError 1314; deterministic reparse checks
remain active in production code.

Final verification: `python -m unittest tests.maximum_optimizer.test_benchmarking` ran 11 tests
successfully with the one environment symlink skip. `python -m unittest discover -s tests -t .`
ran the full 321-test suite successfully with 11 environment skips. Python byte-compilation of the
harness and benchmarking module also passed.
