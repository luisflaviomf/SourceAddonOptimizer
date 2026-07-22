# Models Maximum v2 Rigorous Performance Investigation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Explain at least 95% of the Pontiac Maximum v2 adaptive-stage wall time without double counting, verify exact output identity, and rank the next exact optimization experiments without changing production algorithms.

**Architecture:** Run the real adaptive pipeline against the frozen Pontiac original/Normal source trees with production profile and Release x64 native DLL. A temporary, isolated Python harness will collect one adaptive-only cProfile and ten low-overhead process-isolated runs; all raw evidence stays outside the repository, and the temporary harness is deleted before completion.

**Tech Stack:** CPython 3.11 `cProfile`/`pstats`, `perf_counter`, `process_time`, `gc.callbacks`, `ctypes`, Pillow, Release MSVC x64 meshoptimizer bridge, existing Maximum v2 pipeline and benchmark corpus.

## Global Constraints

- Do not change samples, precision, silhouette resolution, gates, tolerances, selected regions, restored regions, or the calibrated profile SHA-256 `6029d5a8c80ec2b7caf6e50e82035b3aeb21103d6609c1d7345a3a3ff9e6c9d9`.
- Do not change or publish the WPF executable, worker package, production Python modules, native DLL, or legacy `gui/`.
- Do not merge or push.
- Do not use subagents.
- Do not implement an optimization in this investigation.
- Keep temporary instrumentation under `benchmarks/lvs_models_adaptive/_profile_maximum_v2_temp.py` and delete it after producing evidence.
- Store raw evidence under `D:\gaco-max-v2-rigorous-profile-20260721`. The
  planned `C:` root could not be used because that volume had zero free bytes
  at measurement time; the evidence volume had 371 GB free.
- Treat cache-empty fresh-process runs as cold at the application level; state explicitly that Windows page cache cannot be force-cleared with the available tools.
- Treat persistent-region-cache fresh-process runs as warm; DLL and Python interpreter reload in every run, and Blender is not part of the adaptive stage.
- Close at least 95% of adaptive wall time with mutually exclusive top-level categories. Report GC and I/O again only as marked cross-cut overlays.

---

### Task 1: Freeze the measurement and equivalence contract

**Files:**
- Read: `C:\gaco-max-v2-bench\holdout\pontiac_transam_wheel\maximum-adaptive-v2\result.json`
- Read: `C:\gaco-max-v2-bench\holdout\pontiac_transam_wheel\maximum-adaptive-v2\work\logs\maximum_adaptive_report.json`
- Create outside repository: `C:\gaco-max-v2-rigorous-profile-20260721\baseline-contract.json`

**Interfaces:**
- Produces: immutable hashes and semantic fields consumed by every later comparison.
- Requires: clean worktree and Release bridge hash `d5c944b8eabeaa17bea055aaa7a098cd11500725c37410e24a1b2eb61490da45`.

- [ ] **Step 1: Record source, profile, bridge, code, and frozen-result hashes**

Write canonical JSON containing repository HEAD, dirty status, Python version,
OS version, CPU count, profile/DLL/script hashes, input tree hash, result hash,
adaptive wall/CPU, all 19 region-report semantic fields, and compiled MDL/VVD/
DX90/PHY hashes.

- [ ] **Step 2: Assert the frozen result contract**

Require 16,304 original triangles, 13,530 final triangles, 833,184 final
comparable bytes, 19 simplifier evaluations, one targeted render, one compile,
zero integrity failures, zero final DX80 bytes, and profile hash equality.

- [ ] **Step 3: Commit only the investigation plan**

Run:

```powershell
git add docs/superpowers/plans/2026-07-21-models-maximum-v2-rigorous-performance-investigation.md
git commit -m "docs(maximum): plan rigorous adaptive performance investigation"
```

Expected: clean worktree after the plan commit.

### Task 2: Build isolated temporary instrumentation

**Files:**
- Create temporarily: `benchmarks/lvs_models_adaptive/_profile_maximum_v2_temp.py`
- Generate outside repository: `C:\gaco-max-v2-rigorous-profile-20260721\runs\`

**Interfaces:**
- Produces CLI: `python benchmarks/lvs_models_adaptive/_profile_maximum_v2_temp.py --run-id ID --cache-root PATH --staging-root PATH --out PATH [--cprofile PATH]`.
- Produces one JSON per run with `stages`, `timers`, `distributions`, `gc`, `cache`, `fingerprints`, `environment`, and `report`.

- [ ] **Step 1: Implement a depth-aware timer registry**

Use this contract so recursive BVH/KD calls count once inclusively while still
recording internal call count:

```python
@dataclass
class TimerStat:
    calls: int = 0
    top_calls: int = 0
    seconds: float = 0.0
    samples: list[float] = field(default_factory=list)

class TimerBook:
    def record(self, name: str, elapsed: float, *, top: bool = True) -> None:
        stat = self.stats.setdefault(name, TimerStat())
        stat.calls += 1
        if top:
            stat.top_calls += 1
            stat.seconds += elapsed
            stat.samples.append(elapsed)
```

Wrap `_build_bvh`, `_kd_tree`, and `_kd_distance` with per-function depth
counters. Wrap `_nearest` without a depth guard and retain every query duration
plus a SHA-256 key of target identity and IEEE-754 point bytes.

- [ ] **Step 2: Replace only `_direction_metrics` at runtime with an identical timed copy**

Time these non-overlapping blocks around the unchanged expressions:

```python
nearest = metrics._nearest(sample.point, target_data, target_bvh)
surfaces.append(nearest.distance / diagonal)
normals.append(math.degrees(math.acos(max(-1.0, min(1.0, metrics._dot(sample.normal, nearest.sample.normal))))))
uvs.append(metrics._length(metrics._sub(sample.uv, nearest.sample.uv)))
for pose in poses:
    source_displacement = metrics._pose_displacement(sample, pose)
    target_displacement = metrics._pose_displacement(nearest.sample, pose)
    skinning.append(metrics._length(metrics._sub(source_displacement, target_displacement)) / diagonal)
```

Record `nearest-inclusive`, `surface-difference`, `normal-difference`,
`uv-difference`, and `skinning` separately. Assert the timed copy returns the
same four lists as the original on the 64-to-48 disc fixture before a real run.

- [ ] **Step 3: Instrument the remaining hierarchy**

Record inclusive timers for material resolution, risk classification, cache
get/put, source hashing, topology classification, `simplify_smd_region`, Python
`simplify_mesh`, native `maximum_meshopt_simplify`, reference preparation,
candidate measurement, triangle-data conversion, sampling, silhouette
preparation/measurement, projection, boundary extraction, validation, and
percentiles. Proxy the loaded DLL only to time the native call; delegate every
other attribute unchanged.

- [ ] **Step 4: Record cross-cut GC, memory, I/O, and residency evidence**

Use `gc.callbacks` to record collection duration/generation, `psutil` for RSS
start/peak/end and process children, `sys.getallocatedblocks()` for block delta,
and cache get/put timers for JSON/file work. Record running Blender/Python/WPF
processes before and after. Mark these values as overlays because they occur
inside hierarchy timers.

- [ ] **Step 5: Freeze exact semantic and numeric fingerprints**

Hash every region/candidate with material, triangle order, indices, IEEE-754
positions/normals/UVs/weights/bones; hash each generated sample in order; record
every `RegionMetrics` field and `ValidationDecision`; hash meshoptimizer input
and output arrays separately; record selected region order and restored region
order; hash the composed staging SMD tree. Use the frozen compiled binary hashes
from Task 1 because the timed harness stubs only post-adaptive render/compile.

- [ ] **Step 6: Use production inputs with post-adaptive stubs**

Run `run_maximum_adaptive` using the frozen Pontiac original/Normal trees,
production addon/framework/profile/DLL, a passing deterministic render stub,
and a compile stub pointing at the already frozen compiled models. This keeps
the adaptive stage byte-for-byte real while excluding Blender and StudioMDL,
which are separate stages.

- [ ] **Step 7: Smoke-test and verify no production diff**

Run one unprofiled cache-empty smoke run. Require the full semantic/fingerprint
contract and then run `git diff --exit-code -- maximum_optimizer worker GmodAddonCompressor-master gui`.

Expected: only the isolated temporary harness is untracked/modified.

### Task 3: Capture heavy profiler and call-tree evidence

**Files:**
- Generate: `C:\gaco-max-v2-rigorous-profile-20260721\profile\adaptive.pstats`
- Generate: `C:\gaco-max-v2-rigorous-profile-20260721\profile\function-table.json`
- Generate: `C:\gaco-max-v2-rigorous-profile-20260721\profile\call-tree.md`

**Interfaces:**
- Consumes: Task 2 harness.
- Produces: self/tottime and inclusive/cumtime evidence for each relevant function.

- [ ] **Step 1: Profile only `adaptive-simplification`**

Patch `_run_stage` in the temporary harness so `cProfile.Profile.enable()` and
`disable()` surround only the adaptive stage function. Dump pstats after the
pipeline returns successfully.

- [ ] **Step 2: Export the full function table**

For every pstats entry write primitive calls, total calls, self seconds,
cumulative seconds, self/call, cumulative/call, and percent of profiled
adaptive time. Preserve caller/callee relationships.

- [ ] **Step 3: Produce the call-tree equivalent**

Write a Markdown tree rooted at `pipeline.py:optimize_all`, showing the top
three levels and every descendant with at least 0.25% cumulative time. Label
shared descendants as inclusive/overlapping rather than summing siblings.

- [ ] **Step 4: Cross-check profiler and lightweight identities**

Require identical metric/gate/sample/candidate/selection fingerprints between
the profiled run and smoke reference. Record profiler slowdown separately; do
not use profiled wall time as the performance result.

### Task 4: Run five cold and five warm lightweight measurements

**Files:**
- Generate: `C:\gaco-max-v2-rigorous-profile-20260721\runs\cold-01..05.json`
- Generate: `C:\gaco-max-v2-rigorous-profile-20260721\runs\warm-01..05.json`
- Generate: `C:\gaco-max-v2-rigorous-profile-20260721\runs\summary.json`

**Interfaces:**
- Consumes: the same production code/profile/DLL and Task 2 harness.
- Produces: final profiler-free distribution used in the report.

- [ ] **Step 1: Execute five application-cold runs**

Use a fresh process, empty cache root, and unique staging root for every run.
Keep cold-01 identified separately. Do not claim that Windows page cache was
cleared.

- [ ] **Step 2: Execute five application-warm runs**

Seed one shared region cache from a completed cold run, then use it in five new
processes with unique staging roots. Require 19 cache hits and zero simplifier
evaluations in each warm run. Record that DLL/Python reload per process while
file data may remain in Windows cache.

- [ ] **Step 3: Summarize distributions**

For adaptive wall/CPU and every mutually exclusive category calculate first,
median, minimum, maximum, call count, mean per call, and coefficient of
variation. Report nearest query p50/p95/p99/max and exact duplicate/equivalent
query counts.

- [ ] **Step 4: Verify all ten result identities**

Require identical metrics, gates, sample identities, candidate arrays,
selected/restored order, staging tree hash, final triangles, and size contract.
Allow only expected cold/warm cache counters and timing/memory fields to differ.

### Task 5: Check three additional model types briefly

**Files:**
- Read: frozen benchmark work trees under `C:\gaco-max-v2-bench\development\`
- Generate: `C:\gaco-max-v2-rigorous-profile-20260721\cross-family\summary.json`

**Interfaces:**
- Produces: one representative regional measurement per model type, not a new corpus campaign.

- [ ] **Step 1: Select evidence-backed regions**

Choose one Ford Fairlane body/glass or equivalent multi-material curved region,
one Ferrari full-rig region with more than one effective bone/weight pattern,
and one Caterham region with at most 150 triangles and no skinning gradient.
Record exact source, material, key, triangle count, vertices, bones, and why it
matches the requested type.

- [ ] **Step 2: Measure one production candidate per selected region**

Use the frozen original/Normal correspondence and production contract. Capture
the same category timers and fingerprints, but do not render, compile, or run
whole families.

- [ ] **Step 3: Compare shares with Pontiac**

Report whether silhouette, nearest/BVH, sampling, risk, skinning, or conversion
shares differ materially. Do not generalize from a single region without the
explicit limitation.

### Task 6: Close time, calculate Amdahl ceilings, and report

**Files:**
- Modify: `benchmarks/lvs_models_adaptive/results-2026-07-21.md`
- Create: `docs/superpowers/specs/2026-07-21-models-maximum-v2-performance-investigation.md`
- Delete: `benchmarks/lvs_models_adaptive/_profile_maximum_v2_temp.py`

**Interfaces:**
- Produces: final evidence-backed report and exact next-experiment design; no active experiment code.

- [ ] **Step 1: Build the mutually exclusive adaptive-time table**

Use median cold wall time as 100%. Top-level rows must sum to the stage within
5%; show self and inclusive columns, calls, mean, percent, and overlap notes.
Residual above 5% blocks completion and requires another instrumentation pass.

- [ ] **Step 2: Calculate optimization ceilings**

For each candidate with fraction `p`, calculate unlimited ceiling
`1 / (1 - p)` and realistic stage speedups `1 / ((1 - p) + p / 2)` and
`1 / ((1 - p) + p / 4)`. Convert each to seconds saved in adaptive and total
wall. Calculate the combined fraction required to reach 12s and 10s.

- [ ] **Step 3: Rank three exact targets and design only the first experiment**

Reject any isolated target whose ceiling cannot meet the goal. State expected
gain, exact equivalence contract, risks, and promotion/discard thresholds.
Do not implement it; explicitly await user approval.

- [ ] **Step 4: Remove instrumentation and verify repository state**

Delete the temporary harness with `apply_patch`. Run all 82 Python tests,
confirm the production code and DLL match the pre-investigation hashes, verify
the published executable was not modified, and confirm no worker/WPF/gui code
diff remains.

- [ ] **Step 5: Commit documentation only**

```powershell
git add benchmarks/lvs_models_adaptive/results-2026-07-21.md docs/superpowers/specs/2026-07-21-models-maximum-v2-performance-investigation.md
git commit -m "docs(maximum): report rigorous adaptive performance profile"
```

Expected: clean worktree, no release build, no merge, and no active algorithm or instrumentation change.
