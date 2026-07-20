# Maximum Balanced Parallelism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Maximum use roughly 50% of available CPU by default and finish materially faster while producing the same selected candidates, artifact hashes, fidelity decisions, and DX80-free output as serial execution.

**Architecture:** Keep each family's adaptive search serial and move independent families behind a bounded coordinator-owned scheduler. Resolve a CPU/memory-aware resource plan once per run, cap each Blender child to one thread during parallel execution, reuse a sealed original-render bundle across candidates, and stop a candidate after its first hard whole-state visual failure. The coordinator alone serializes events, journals progress, merges canonical results, and promotes the final output.

**Tech Stack:** Python 3.11 (`concurrent.futures`, `threading`, `ctypes`, `unittest`), Blender 5 CLI, StudioMDL, C#/.NET 6 WPF, MSTest, PyInstaller, PowerShell.

## Global Constraints

- Default `--maximum-jobs 0` targets `max(1, floor(logical_processors * 0.50))` family slots.
- Reserve the larger of 2 GiB or 10% of physical RAM and budget 768 MiB per active family.
- If memory metrics are unavailable, Auto is capped at four jobs.
- Explicit jobs remain bounded by logical processors and the emergency memory guard.
- With more than one family slot, every Maximum Blender child receives `--threads 1`.
- Candidate order inside one family, profiles, search budget, gates, recovery, and winner rules must not change.
- Serial and parallel runs must select identical candidate IDs and produce identical final hashes.
- Every produced Maximum output excludes `.dx80.vtx`, including preserved-family fallback.
- UI changes go only to the WPF product; `gui/` remains untouched.
- Never stage or modify `.superpowers/sdd/progress.md`.

## File Map

- Create `maximum_optimizer/parallelism.py` for hardware/resource policy.
- Create `maximum_optimizer/family_scheduler.py` for bounded family scheduling.
- Create `maximum_optimizer/progress_journal.py` for synchronized, coalesced partial reports.
- Create `maximum_optimizer/reference_bundle.py` for sealed original render evidence.
- Modify `maximum_optimizer/orchestrator.py` to isolate one-family execution and coordinate it.
- Modify `maximum_optimizer/candidates.py` and `build_optimized_addon.py` for thread/job propagation.
- Modify `render_previews.py` so candidate validation can skip repeated original-side image rendering.
- Modify WPF settings, context, runner, parser, XAML, and code-behind for the new control/status.
- Extend Python, WPF, packaging, equivalence, and real LVS benchmark coverage.

---

### Task 1: Resolve balanced CPU and memory capacity

**Files:**
- Create: `maximum_optimizer/parallelism.py`
- Create: `tests/maximum_optimizer/test_parallelism.py`

**Interfaces:**
- Produces: `MemorySnapshot`, `MaximumParallelismPlan`, `detect_memory_snapshot()`, `resolve_maximum_parallelism()`, and `current_memory_job_limit()`.
- Consumes: Python standard library only.

- [ ] **Step 1: Write failing resource-policy tests**

```python
from maximum_optimizer.parallelism import (
    GIB, MemorySnapshot, current_memory_job_limit, resolve_maximum_parallelism,
)


def test_auto_targets_half_of_twenty_processors():
    plan = resolve_maximum_parallelism(
        0, logical_processors=20,
        memory=MemorySnapshot(32 * GIB, 16 * GIB),
    )
    assert (plan.cpu_target_jobs, plan.effective_jobs) == (10, 10)
    assert plan.blender_threads == 1
    assert plan.memory_throttled is False


def test_auto_throttles_when_only_eight_gib_are_available():
    plan = resolve_maximum_parallelism(
        0, logical_processors=20,
        memory=MemorySnapshot(32 * GIB, 8 * GIB),
    )
    assert (plan.cpu_target_jobs, plan.effective_jobs) == (10, 6)
    assert plan.memory_throttled is True


def test_unknown_memory_caps_auto_but_serial_keeps_native_blender_threads():
    auto = resolve_maximum_parallelism(0, logical_processors=20, memory=None)
    serial = resolve_maximum_parallelism(1, logical_processors=20, memory=None)
    assert (auto.effective_jobs, auto.blender_threads) == (4, 1)
    assert (serial.effective_jobs, serial.blender_threads) == (1, 0)


def test_dynamic_guard_does_not_return_zero_slots():
    plan = resolve_maximum_parallelism(
        8, logical_processors=20,
        memory=MemorySnapshot(32 * GIB, 16 * GIB),
    )
    assert current_memory_job_limit(plan, MemorySnapshot(32 * GIB, 4 * GIB)) == 1
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.maximum_optimizer.test_parallelism -v`

Expected: import failure for `maximum_optimizer.parallelism`.

- [ ] **Step 3: Implement the resolver**

```python
GIB = 1024 ** 3
FAMILY_MEMORY_BYTES = 768 * 1024 ** 2
UNKNOWN_MEMORY_AUTO_CAP = 4


@dataclass(frozen=True)
class MemorySnapshot:
    total_bytes: int
    available_bytes: int


@dataclass(frozen=True)
class MaximumParallelismPlan:
    requested_jobs: int
    logical_processors: int
    cpu_target_jobs: int
    effective_jobs: int
    memory_limit_jobs: int
    memory_throttled: bool
    blender_threads: int

    @classmethod
    def serial(cls):
        return cls(1, 1, 1, 1, 1, False, 0)


def _memory_limit(memory):
    reserve = max(2 * GIB, math.ceil(memory.total_bytes * 0.10))
    return max(1, max(0, memory.available_bytes - reserve) // FAMILY_MEMORY_BYTES)


def resolve_maximum_parallelism(requested_jobs, *, logical_processors=None, memory=None):
    logical = max(1, int(logical_processors or os.cpu_count() or 1))
    if type(requested_jobs) is not int or requested_jobs < 0:
        raise ValueError("maximum jobs must be zero or a positive integer")
    cpu_target = max(1, logical // 2) if requested_jobs == 0 else min(requested_jobs, logical)
    memory_limit = _memory_limit(memory) if memory is not None else (
        UNKNOWN_MEMORY_AUTO_CAP if requested_jobs == 0 else logical
    )
    effective = max(1, min(cpu_target, memory_limit))
    return MaximumParallelismPlan(
        requested_jobs, logical, cpu_target, effective, memory_limit,
        effective < cpu_target, 1 if effective > 1 else 0,
    )


def current_memory_job_limit(plan, memory):
    return plan.effective_jobs if memory is None else max(
        1, min(plan.effective_jobs, _memory_limit(memory))
    )
```

Implement `detect_memory_snapshot()` with `GlobalMemoryStatusEx` on Windows and
`os.sysconf` on POSIX. Return `None` on unsupported/error paths; never invent values.

- [ ] **Step 4: Run GREEN and regression tests**

Run: `python -m unittest tests.maximum_optimizer.test_parallelism tests.maximum_optimizer.test_orchestrator -v`

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add maximum_optimizer/parallelism.py tests/maximum_optimizer/test_parallelism.py
git commit -m "feat(maximum): resolve balanced worker capacity"
```

---

### Task 2: Propagate Maximum jobs and Blender thread limits

**Files:**
- Modify: `build_optimized_addon.py`
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `maximum_optimizer/candidates.py`
- Modify: `tests/maximum_optimizer/test_cli.py`
- Modify: `tests/maximum_optimizer/test_candidates.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- Consumes: `MaximumParallelismPlan` from Task 1.
- Produces: `MaximumRunConfig.parallelism`, `CandidateTools.blender_threads`, and CLI `--maximum-jobs`.

- [ ] **Step 1: Write failing CLI and command tests**

```python
def test_parser_defaults_maximum_jobs_to_auto(self):
    args = build_parser().parse_args([str(self.addon)])
    self.assertEqual(args.maximum_jobs, 0)


def test_parallel_candidate_blender_command_uses_one_thread(self):
    tools = replace(self.tools, blender_threads=1)
    command = self.adapter._optimize_command(self.source, self.spec, self.workspace, tools)
    self.assertEqual(command[1:3], ("--threads", "1"))


def test_serial_candidate_does_not_force_blender_threads(self):
    tools = replace(self.tools, blender_threads=0)
    command = self.adapter._optimize_command(self.source, self.spec, self.workspace, tools)
    self.assertNotIn("--threads", command)
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.maximum_optimizer.test_cli tests.maximum_optimizer.test_candidates -v`

Expected: missing `maximum_jobs` and `blender_threads` failures.

- [ ] **Step 3: Implement propagation**

Add to the parser:

```python
ap.add_argument(
    "--maximum-jobs", type=int, default=0,
    help="Concurrent Maximum families (0 = Auto balanced at about 50% CPU).",
)
```

Add the final defaulted config field:

```python
parallelism: MaximumParallelismPlan = field(default_factory=MaximumParallelismPlan.serial)
```

Resolve it once in `run_maximum_from_existing_args`:

```python
parallelism = resolve_maximum_parallelism(int(getattr(args, "maximum_jobs", 0)))
config = MaximumRunConfig(
    addon_path, out_addon_dir, work_dir, blender, studiomdl, repo_root,
    budget, profile_path, maximum_resume, bool(args.overwrite), parallelism,
)
```

Add `blender_threads: int = 0` to `CandidateTools` and use:

```python
def blender_command(tools, *arguments):
    prefix = [str(tools.blender_exe)]
    if tools.blender_threads > 0:
        prefix.extend(("--threads", str(tools.blender_threads)))
    return (*prefix, *arguments)
```

Replace every Maximum Blender prefix in candidate adapters and `ProductionAdapters`.

- [ ] **Step 4: Run GREEN**

Run: `python -m unittest tests.maximum_optimizer.test_cli tests.maximum_optimizer.test_candidates tests.maximum_optimizer.test_orchestrator -v`

Expected: all pass; serial fixture commands remain unchanged.

- [ ] **Step 5: Commit**

```powershell
git add build_optimized_addon.py maximum_optimizer/orchestrator.py maximum_optimizer/candidates.py tests/maximum_optimizer/test_cli.py tests/maximum_optimizer/test_candidates.py tests/maximum_optimizer/test_orchestrator.py
git commit -m "feat(maximum): propagate balanced job policy"
```

---

### Task 3: Add the generic scheduler and coalesced journal

**Files:**
- Create: `maximum_optimizer/family_scheduler.py`
- Create: `maximum_optimizer/progress_journal.py`
- Create: `tests/maximum_optimizer/test_family_scheduler.py`
- Create: `tests/maximum_optimizer/test_progress_journal.py`

**Interfaces:**
- Produces: `FamilyWorkItem`, `FamilySchedulerUpdate`, `run_family_jobs()`, and `DurableProgressJournal.publish()`.
- Consumes: resource limits from Task 1.

- [ ] **Step 1: Write failing scheduler/journal tests**

```python
def test_scheduler_bounds_active_jobs_and_returns_canonical_order(self):
    active = peak = 0
    lock = threading.Lock()
    def worker(value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep((4 - value) * 0.01)
        with lock:
            active -= 1
        return value * 10
    items = tuple(FamilyWorkItem(i, i, i) for i in range(4))
    self.assertEqual(run_family_jobs(items, max_workers=2, worker=worker), (0, 10, 20, 30))
    self.assertEqual(peak, 2)


def test_scheduler_submits_largest_estimate_first_with_stable_ties(self):
    submitted = []
    items = (FamilyWorkItem(0, 10, "a"), FamilyWorkItem(1, 30, "b"), FamilyWorkItem(2, 30, "c"))
    run_family_jobs(items, max_workers=1, worker=lambda value: submitted.append(value) or value)
    self.assertEqual(submitted, ["b", "c", "a"])


def test_journal_coalesces_and_force_flushes(self):
    writes, clock = [], FakeClock()
    journal = DurableProgressJournal(writes.append, min_interval=0.25, clock=clock)
    self.assertTrue(journal.publish(lambda: {"n": 1}))
    self.assertFalse(journal.publish(lambda: {"n": 2}))
    self.assertTrue(journal.publish(lambda: {"n": 3}, force=True))
    self.assertEqual(writes, [{"n": 1}, {"n": 3}])
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.maximum_optimizer.test_family_scheduler tests.maximum_optimizer.test_progress_journal -v`

Expected: imports fail.

- [ ] **Step 3: Implement scheduler and journal**

```python
@dataclass(frozen=True)
class FamilyWorkItem(Generic[T]):
    index: int
    estimate: int
    value: T


def run_family_jobs(items, *, max_workers, worker, cancel_event=None,
                    memory_limit=None, on_update=None):
    ordered = deque(sorted(items, key=lambda item: (-item.estimate, item.index)))
    results, pending = {}, {}
    cancel = cancel_event or threading.Event()
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="maximum-family") as pool:
        while ordered or pending:
            limit = min(max_workers, max(1, memory_limit() if memory_limit else max_workers))
            while ordered and len(pending) < limit and not cancel.is_set():
                item = ordered.popleft()
                pending[pool.submit(worker, item.value)] = item
            if not pending:
                break
            done, _ = wait(tuple(pending), timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                item = pending.pop(future)
                results[item.index] = future.result()
            if on_update:
                on_update(FamilySchedulerUpdate(len(pending), len(results), len(items), limit, limit < max_workers))
    return tuple(results[index] for index in sorted(results))
```

Cancel unstarted futures and drain active workers after cancellation. Implement the
journal with an `RLock`, injected monotonic clock, minimum interval, and force flag.

- [ ] **Step 4: Run GREEN**

Run: `python -m unittest tests.maximum_optimizer.test_family_scheduler tests.maximum_optimizer.test_progress_journal -v`

Expected: all concurrency, cancellation, ordering, and force-flush tests pass.

- [ ] **Step 5: Commit**

```powershell
git add maximum_optimizer/family_scheduler.py maximum_optimizer/progress_journal.py tests/maximum_optimizer/test_family_scheduler.py tests/maximum_optimizer/test_progress_journal.py
git commit -m "feat(maximum): add bounded family scheduler"
```

---

### Task 4: Extract one-family execution without behavior changes

**Files:**
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- Produces: private `_FamilyExecutionResult` and `_execute_family()`.
- Consumes: existing manifests, adapters, cache, profiles, validators, and event callback.

- [ ] **Step 1: Add a serial equivalence baseline**

```python
def test_extracted_execution_preserves_serial_selection_and_hashes(self):
    first = self.run_isolated(maximum_jobs=1)
    second = self.run_isolated(maximum_jobs=1)
    self.assertEqual(self.selection_and_hashes(first), self.selection_and_hashes(second))
```

- [ ] **Step 2: Run the baseline before refactoring**

Run: `python -m unittest tests.maximum_optimizer.test_orchestrator -v`

Expected: PASS.

- [ ] **Step 3: Define the exact family result contract**

```python
@dataclass(frozen=True)
class _FamilyExecutionResult:
    index: int
    outcome: FamilyRunOutcome
    control_snapshot: CompiledSizeSnapshot | None
    selected_snapshot: CompiledSizeSnapshot
    selected_build: CandidateBuild | None
    recovery_authorization: tuple[FocusedRecoveryAdapterResult, object, CacheKey, Path] | None
    selection_record: Mapping[str, Any]
```

- [ ] **Step 4: Mechanically extract the current family-loop body**

```python
def _execute_family(
    *, index, manifest, config, original, profile_set, adapters,
    structural_validator, cache, versions, dependency, tools, cancel,
    emit, profile_selector,
) -> _FamilyExecutionResult:
    # Move the existing body of lines 2211-3077 here unchanged first.
    # Keep attempts/evaluations/candidate_builds/recovery state function-local.
    # Replace coordinator collection appends with one terminal return value.
```

The completed code must contain the moved implementation, not comments or ellipses.
Every non-cancellation path returns one result; cancellation propagates through the
existing shared event behavior.

- [ ] **Step 5: Consume results serially in the coordinator**

```python
for family_index, manifest in enumerate(manifests):
    result = _execute_family(index=family_index, manifest=manifest, **family_context)
    outcomes.append(result.outcome)
    selected_snapshots.append(result.selected_snapshot)
    if result.control_snapshot is not None:
        control_snapshots.append(result.control_snapshot)
    selection_records.append(dict(result.selection_record))
    if result.selected_build is not None:
        selected_builds[result.outcome.family_id] = result.selected_build
```

- [ ] **Step 6: Run GREEN and commit**

Run: `python -m unittest tests.maximum_optimizer.test_orchestrator -v`

Expected: all existing events, reports, winners, cancellation cases, and hashes pass.

```powershell
git add maximum_optimizer/orchestrator.py tests/maximum_optimizer/test_orchestrator.py
git commit -m "refactor(maximum): isolate family execution"
```

---

### Task 5: Enable parallel coordination and deterministic merging

**Files:**
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `maximum_optimizer/family_scheduler.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`
- Modify: `tests/maximum_optimizer/test_family_scheduler.py`

**Interfaces:**
- Consumes: `_execute_family()`, `run_family_jobs()`, and `MaximumParallelismPlan`.
- Produces: production parallelism, scheduler events, and report resource metadata.

- [ ] **Step 1: Write failing overlap/equivalence/failure-isolation tests**

```python
def test_parallel_families_overlap_but_merge_in_manifest_order(self):
    report = self.run_with_barrier_adapter(maximum_jobs=2, family_count=3)
    self.assertGreaterEqual(self.adapters.peak_active_families, 2)
    self.assertEqual(
        [item.model_rel for item in report.families],
        self.adapters.model_rels_in_manifest_order,
    )


def test_serial_and_parallel_outputs_are_identical(self):
    serial = self.run_isolated(maximum_jobs=1)
    parallel = self.run_isolated(maximum_jobs=2)
    self.assertEqual(self.selection_and_hashes(serial), self.selection_and_hashes(parallel))


def test_one_family_failure_does_not_cancel_good_family(self):
    report = self.run_with_failing_family("models/bad.mdl", maximum_jobs=2)
    self.assertEqual(self.outcome(report, "models/bad.mdl").status, "preserved")
    self.assertEqual(self.outcome(report, "models/good.mdl").status, "optimized")
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.maximum_optimizer.test_orchestrator -v`

Expected: peak active families remains one.

- [ ] **Step 3: Isolate mutable production adapter state**

```python
class ProductionAdapters:
    def fork_for_family(self):
        return ProductionAdapters(self.config, self.cancel_event)
```

When effective jobs exceed one, injected adapters must implement
`fork_for_family()`. Fail before starting families if they do not. Serial adapters
keep existing behavior.

- [ ] **Step 4: Schedule work and serialize events/reports**

```python
work_items = tuple(
    FamilyWorkItem(index, _family_work_estimate(manifest, original), (index, manifest))
    for index, manifest in enumerate(manifests)
)
results = run_family_jobs(
    work_items,
    max_workers=config.parallelism.effective_jobs,
    worker=lambda item: _execute_family(
        index=item[0], manifest=item[1],
        adapters=adapter_set.fork_for_family(), **family_context,
    ),
    cancel_event=cancel,
    memory_limit=lambda: current_memory_job_limit(
        config.parallelism, detect_memory_snapshot()
    ),
    on_update=emit_scheduler_status,
)
```

Protect event numbering, event history, sink calls, and journal snapshots with one
`RLock`. Replace per-event full-report writes with `DurableProgressJournal.publish`.
Force writes on `family_finished` and all terminal run events. Merge returned results
on the coordinator thread in manifest index order.

Define the work estimate without timing history so ordering stays reproducible:

```python
def _family_work_estimate(manifest, original):
    source_bytes = sum(
        path.stat().st_size for path in manifest.source_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".smd", ".dmx", ".qc"}
    )
    original_bytes = _family_snapshot(original, manifest.model_rel).total_bytes
    original_qc = _matching_qcs(manifest.source_dir, manifest.model_rel, optimized=False)[0]
    visual_states = len(_graph_visual_configurations(
        parse_qc_graph(original_qc, manifest.source_dir)
    ))
    return max(1, source_bytes) * max(1, visual_states) + original_bytes
```

Add `scheduler_status` to `EVENT_KINDS`. Emit it after submissions and completions
with `active_families`, `completed_families`, `family_total`, `effective_jobs`, and
`memory_throttled`. On cancellation, identify unstarted families by missing manifest
index rather than by `len(outcomes)`, because parallel completions are not prefix
ordered.

Add report metadata:

```python
"parallelism": {
    "requested_jobs": config.parallelism.requested_jobs,
    "cpu_target_jobs": config.parallelism.cpu_target_jobs,
    "effective_jobs": config.parallelism.effective_jobs,
    "memory_limit_jobs": config.parallelism.memory_limit_jobs,
    "memory_throttled": config.parallelism.memory_throttled,
    "peak_active_families": peak_active_families,
}
```

- [ ] **Step 5: Run GREEN and commit**

Run: `python -m unittest tests.maximum_optimizer.test_family_scheduler tests.maximum_optimizer.test_progress_journal tests.maximum_optimizer.test_orchestrator -v`

Expected: overlap reaches the bound, output equivalence passes, cancellation drains,
and no report temp files remain.

```powershell
git add maximum_optimizer/orchestrator.py maximum_optimizer/family_scheduler.py tests/maximum_optimizer/test_orchestrator.py tests/maximum_optimizer/test_family_scheduler.py
git commit -m "feat(maximum): optimize families in parallel"
```

---

### Task 6: Seal and reuse original-side render evidence

**Files:**
- Create: `maximum_optimizer/reference_bundle.py`
- Create: `tests/maximum_optimizer/test_reference_bundle.py`
- Create: `tests/maximum_optimizer/test_render_reference_mode.py`
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `render_previews.py`
- Modify: `tests/maximum_optimizer/test_visual_validation.py`

**Interfaces:**
- Produces: `ReferenceBundleIdentity`, `ReferenceBundle`, and `ReferenceBundleStore`.
- Consumes: existing canonical JSON, safe file proofs, renderer/profile/dependency hashes, and ratio-1 control evidence.

- [ ] **Step 1: Write failing security and reuse tests**

```python
def test_bundle_reopens_only_for_exact_identity(self):
    identity = self.identity(family_hash="a" * 64)
    published = self.store.publish(identity, self.make_complete_source())
    self.assertEqual(self.store.lookup(identity).root, published.root)
    self.assertIsNone(self.store.lookup(replace(identity, family_hash="b" * 64)))
    self.assertFalse(tuple(self.store.root.glob(".pending-*")))


def test_corrupt_bundle_is_not_reused(self):
    bundle = self.store.publish(self.identity(), self.make_complete_source())
    (bundle.root / "original/render_manifest.json").write_bytes(b"corrupt")
    self.assertIsNone(self.store.lookup(self.identity()))


def test_two_candidates_share_one_reference_render(self):
    self.run_candidate("candidate-a")
    self.run_candidate("candidate-b")
    self.assertEqual(self.renderer.reference_render_calls, 1)
    self.assertEqual(self.renderer.candidate_render_calls, 2)


def test_candidate_only_mode_loads_both_geometries_but_renders_only_optimized_side(self):
    result = run_renderer(self.args(render_side="candidate"))
    self.assertEqual(result.geometry_inputs, (self.before, self.after))
    self.assertEqual(result.image_render_sides, ("optimized",))
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.maximum_optimizer.test_reference_bundle tests.maximum_optimizer.test_render_reference_mode tests.maximum_optimizer.test_visual_validation -v`

Expected: missing module and repeated reference renders.

- [ ] **Step 3: Implement sealed identity and publication**

```python
@dataclass(frozen=True)
class ReferenceBundleIdentity:
    family_id: str
    family_hash: str
    renderer_sha256: str
    profile_sha256: str
    dependency_digest: str
    material_roots_sha256: str
    vtfcmd_sha256: str

    @property
    def digest(self):
        return hashlib.sha256(canonical_json(asdict(self)).encode("utf-8")).hexdigest()


class ReferenceBundleStore:
    def lookup(self, identity):
        return _validated_bundle(self.root / identity.family_id / identity.digest, identity)

    def publish(self, identity, source):
        family_root = self.root / identity.family_id
        family_root.mkdir(parents=True, exist_ok=True)
        staging = family_root / f".pending-{uuid.uuid4().hex}"
        _copy_regular_tree_with_hash_manifest(source, staging, identity)
        final = family_root / identity.digest
        os.replace(staging, final)
        bundle = _validated_bundle(final, identity)
        if bundle is None:
            raise ValueError("published reference bundle failed verification")
        return bundle
```

Reuse the project's no-follow, reparse, case-collision, membership, and hash rules.
Use a per-identity lock and atomic rename.

- [ ] **Step 4: Add an explicit renderer side mode**

Add `--render-side {both,reference,candidate}` with default `both` to
`render_previews.py`. Both source sets remain required so geometry, UV, normal,
skinning, and animation comparisons are unchanged. Gate only image rendering and
manifest publication:

```python
render_reference = settings.render_side in {"both", "reference"}
render_candidate = settings.render_side in {"both", "candidate"}
if render_reference:
    render_source_union(original_scene, original_dir, settings)
if render_candidate:
    render_source_union(optimized_scene, optimized_dir, settings)
```

`candidate` mode must consume the sealed original manifest supplied by the
orchestrator and emit the same candidate geometry rows as `both` mode.

- [ ] **Step 5: Split reference and candidate rendering in the adapter**

Add concrete private methods to `ProductionAdapters`:

```python
def _reference_bundle(self, manifest, control, profile) -> ReferenceBundle:
    return self._reference_store.get_or_create(
        self._reference_identity(manifest, profile),
        lambda staging: self._render_original_states(manifest, control, profile, staging),
    )


def _render_candidate_against_reference(self, manifest, candidate, profile, bundle):
    return self._render_and_compare_candidate_states(
        manifest, candidate, profile, bundle
    )
```

Invoke candidate renders with `--render-side candidate`. Keep exactly the existing
states, angles, poses, passes, animation pairing, material
roots, geometry measurements, and `compare_render_sets` gates. Candidate-side
manifests always remain fresh and candidate-owned.

- [ ] **Step 6: Run GREEN and commit**

Run: `python -m unittest tests.maximum_optimizer.test_reference_bundle tests.maximum_optimizer.test_render_reference_mode tests.maximum_optimizer.test_visual_validation tests.maximum_optimizer.test_orchestrator -v`

Expected: all pass; second candidate makes zero original-side render calls.

```powershell
git add maximum_optimizer/reference_bundle.py maximum_optimizer/orchestrator.py render_previews.py tests/maximum_optimizer/test_reference_bundle.py tests/maximum_optimizer/test_render_reference_mode.py tests/maximum_optimizer/test_visual_validation.py
git commit -m "perf(maximum): reuse sealed reference renders"
```

---

### Task 7: Reject failed visual candidates immediately

**Files:**
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `tests/maximum_optimizer/test_visual_validation.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- Consumes: each state's `ValidationResult`.
- Produces: safe early rejection and explicit completion metrics.

- [ ] **Step 1: Write a failing short-circuit test**

```python
def test_first_hard_state_failure_skips_later_states(self):
    self.renderer.fail_state("state-000", self.rgb_failure())
    result = self.run_visual(total_states=3)
    self.assertFalse(result.passed)
    self.assertEqual(self.renderer.rendered_states, ["state-000"])
    self.assertEqual(result.metrics["early_rejected"], 1.0)
    self.assertEqual(result.metrics["completed_state_count"], 1.0)
    self.assertEqual(result.metrics["total_state_count"], 3.0)
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.maximum_optimizer.test_visual_validation -v`

Expected: all three states are rendered.

- [ ] **Step 3: Return after the first failed whole-state gate**

```python
state_result = compare_render_sets(reference_root, candidate_root, profile)
state_results.append((state_name, state_result))
if not state_result.passed:
    aggregate = _aggregate_visual_results(state_results)
    metrics = dict(aggregate.metrics)
    metrics.update({
        "early_rejected": 1.0,
        "completed_state_count": float(len(state_results)),
        "total_state_count": float(len(original_states)),
    })
    return ValidationResult(False, aggregate.failures, metrics, aggregate.worst_scope)
```

Passing candidates record `early_rejected=0.0` and equal completed/total counts.
Focused validation remains reachable only after every whole state passes.

- [ ] **Step 4: Run GREEN and commit**

Run: `python -m unittest tests.maximum_optimizer.test_visual_validation tests.maximum_optimizer.test_orchestrator -v`

Expected: same winner/failure decisions with fewer rendered states for rejected candidates.

```powershell
git add maximum_optimizer/orchestrator.py tests/maximum_optimizer/test_visual_validation.py tests/maximum_optimizer/test_orchestrator.py
git commit -m "perf(maximum): reject failed visual states early"
```

---

### Task 8: Expose Auto 50% and aggregate progress in WPF

**Files:**
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Models/AppSettingsModel.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/DataContexts/MainWindowContext.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerRunner.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerProgressParser.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor.Tests/SourceAddonOptimizerRunnerArgumentTests.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor.Tests/SourceAddonOptimizerProgressParserTests.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor.Tests/MainWindowContextOptimizerModeTests.cs`

**Interfaces:**
- Consumes: `--maximum-jobs` and `scheduler_status` events.
- Produces: persisted Maximum jobs and aggregate live status.

- [ ] **Step 1: Write failing WPF tests**

```csharp
[TestMethod]
public void MaximumJobsArePassedAsAutoByDefault()
{
    var options = new SourceAddonOptimizerRunOptions {
        WorkerExePath = @"C:\tools\worker.exe", AddonPath = @"C:\addon",
        WorkDir = @"C:\work", OptimizerMode = "maximum", MaximumJobs = 0
    };
    var args = SourceAddonOptimizerRunner.BuildStartInfo(options).ArgumentList.ToList();
    int index = args.IndexOf("--maximum-jobs");
    Assert.IsTrue(index >= 0);
    Assert.AreEqual("0", args[index + 1]);
}

[TestMethod]
public void SchedulerStatusParsesAggregateProgress()
{
    var update = parser.Parse("MAXIMUM_EVENT {\"schema\":1,\"kind\":\"scheduler_status\",\"active_families\":8,\"completed_families\":24,\"family_total\":163,\"effective_jobs\":10,\"memory_throttled\":false}");
    Assert.AreEqual(8, update!.ActiveFamilies);
    Assert.AreEqual(24, update.CompletedFamilies);
    Assert.AreEqual(10, update.EffectiveMaximumJobs);
}
```

- [ ] **Step 2: Run RED**

Run: `dotnet test GmodAddonCompressor-master/GmodAddonCompressor.Tests/GmodAddonCompressor.Tests.csproj -c Release`

Expected: compile failures for new fields.

- [ ] **Step 3: Implement settings, runner, parser, and validation**

Add `MaximumJobs = 0` to optimizer settings, `OptimizerMaximumJobs` to the context,
and `MaximumJobs` to runner options. Load/save it and pass it in both Models and
Pipeline. Validate `0..Environment.ProcessorCount`.

Extract the existing `ProcessStartInfo` and argument construction from `RunAsync`
into `internal static ProcessStartInfo BuildStartInfo(SourceAddonOptimizerRunOptions
options)`. `RunAsync` performs its existing input checks, calls this method, and then
uses the unchanged process/cancellation flow. This creates a real argument seam for
the new test without mocking process behavior.

```csharp
if (string.Equals(options.OptimizerMode, "maximum", StringComparison.OrdinalIgnoreCase))
{
    startInfo.ArgumentList.Add("--maximum-jobs");
    startInfo.ArgumentList.Add((options.MaximumJobs ?? 0).ToString(CultureInfo.InvariantCulture));
}
```

Extend progress updates with nullable `ActiveFamilies`, `CompletedFamilies`,
`EffectiveMaximumJobs`, and `MemoryThrottled`; reject malformed scheduler fields.

- [ ] **Step 4: Add the Maximum-only control and status**

```xml
<Border Style="{StaticResource CardInnerPanel}"
        Margin="0,12,0,0"
        Visibility="{Binding OptimizerMaximumVisibility}">
    <StackPanel>
        <TextBlock Style="{StaticResource LabelText}" Text="Parallel families" />
        <TextBox Margin="0,6,0,0"
                 Text="{Binding OptimizerMaximumJobs, UpdateSourceTrigger=LostFocus}" />
        <TextBlock Style="{StaticResource MutedText}" Margin="0,6,0,0"
                   Text="0 = Auto — balanced 50%. Available RAM may reduce concurrency." />
    </StackPanel>
</Border>
```

Render aggregate status as `Active families: 8/10 | Completed: 24/163`, append
`RAM limited` when applicable, and preserve the latest candidate line.

- [ ] **Step 5: Run GREEN and commit**

Run: `dotnet test GmodAddonCompressor-master/GmodAddonCompressor.Tests/GmodAddonCompressor.Tests.csproj -c Release`

Expected: all WPF tests pass.

```powershell
git add GmodAddonCompressor-master/GmodAddonCompressor GmodAddonCompressor-master/GmodAddonCompressor.Tests
git commit -m "feat(wpf): control Maximum family parallelism"
```

---

### Task 9: Run full regression and frozen-worker packaging

**Files:**
- Modify: `tests/test_package_wpf_tools.ps1`
- Modify: `docs/maximum-optimizer-validation.md`

**Interfaces:**
- Consumes: all preceding implementation.
- Produces: packaged new modules and an honest performance-validation section.

- [ ] **Step 1: Assert all new sources participate in worker freshness**

```powershell
$parallelSources = @(
    'maximum_optimizer\parallelism.py',
    'maximum_optimizer\family_scheduler.py',
    'maximum_optimizer\progress_journal.py',
    'maximum_optimizer\reference_bundle.py'
)
foreach ($relative in $parallelSources) {
    $expected = (Join-Path $repoRoot $relative)
    Assert-True ((Get-WorkerSourceFiles).FullName -contains $expected) `
        "Worker freshness must include $relative"
}
```

- [ ] **Step 2: Run packaging test**

Run: `powershell -ExecutionPolicy Bypass -File tests/test_package_wpf_tools.ps1`

Expected: PASS because recursive Maximum discovery includes every module; otherwise
fix `Get-WorkerSourceFiles` and re-run.

- [ ] **Step 3: Run all automated verification**

```powershell
python -m unittest tests.maximum_optimizer.test_parallelism tests.maximum_optimizer.test_family_scheduler tests.maximum_optimizer.test_progress_journal tests.maximum_optimizer.test_reference_bundle tests.maximum_optimizer.test_cli tests.maximum_optimizer.test_candidates tests.maximum_optimizer.test_visual_validation tests.maximum_optimizer.test_orchestrator
python -m unittest discover -s tests -t . -p 'test_*.py' -q
dotnet test GmodAddonCompressor-master/GmodAddonCompressor.Tests/GmodAddonCompressor.Tests.csproj -c Release
powershell -ExecutionPolicy Bypass -File tests/test_package_wpf_tools.ps1
git diff --check
```

Expected: zero failures; report environment-dependent skips explicitly.

- [ ] **Step 4: Document the benchmark procedure without unmeasured claims**

```markdown
### Balanced parallel validation

Compare cold `--maximum-jobs 1` and `--maximum-jobs 0` runs over the same copied
addon. Candidate IDs, final hashes, DX80 absence, and fidelity decisions must match.
Record elapsed seconds, average CPU, peak RAM, and peak active families.
```

- [ ] **Step 5: Commit**

```powershell
git add tests/test_package_wpf_tools.ps1 docs/maximum-optimizer-validation.md
git commit -m "test(maximum): validate parallel release packaging"
```

---

### Task 10: Prove real speed/equivalence, build, and publish

**Files:**
- Modify: `docs/maximum-optimizer-validation.md`
- Generated release: `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`

**Interfaces:**
- Consumes: completed code, LVS cars addon, and LVS framework materials.
- Produces: measured evidence, official release, final commit, push, and updated draft PR.

- [ ] **Step 1: Prepare two cold five-family samples**

Use Beetle, Caterham 620R, Ferrari 365 Fullrig, Ford Fairlane, and VW Touareg from:

```text
C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack
C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\2912816023\lvs_framework
```

Use distinct serial/Auto work and output directories with no shared candidate or
reference cache.

- [ ] **Step 2: Run serial and Auto with identical settings**

Run the frozen worker twice, changing only:

```text
--maximum-jobs 1
--maximum-jobs 0
```

Sample total process-tree CPU and working set each second. Record elapsed seconds,
average CPU, peak RAM, peak active families, selected candidates, and terminal status.

- [ ] **Step 3: Assert equivalence and acceptance**

```python
assert serial.selected_candidate_ids == auto.selected_candidate_ids
assert serial.final_artifact_hashes == auto.final_artifact_hashes
assert serial.fidelity_decisions == auto.fidelity_decisions
assert not any(path.lower().endswith(".dx80.vtx") for path in auto.output_paths)
assert serial.elapsed_seconds / auto.elapsed_seconds >= 3.0
assert 35.0 <= auto.average_cpu_percent <= 65.0
```

Stop before release if equivalence fails. If speed is below 3x, profile and report the
actual bottleneck instead of claiming success.

- [ ] **Step 4: Record evidence and build the official release**

Update the validation document with hardware, commands, hashes, timings, CPU/RAM,
speedup, and equivalence. Preserve the existing corrected fidelity table. Then run:

```powershell
.\build_release_wpf.ps1
& .\dist\GmodAddonOptimizerWorker\GModAddonOptimizerWorker.exe --help
powershell -ExecutionPolicy Bypass -File tests/test_package_wpf_tools.ps1
Get-Item GmodAddonCompressor-master\GmodAddonCompressor\bin\Release\net6.0-windows\win-x64\publish\GmodAddonOptimizer.exe
git diff --check
```

Expected: build exit 0, worker help contains `--maximum-jobs`, package test passes,
and the executable has a fresh timestamp.

- [ ] **Step 5: Commit intended files and push**

```powershell
git status -sb
git add maximum_optimizer build_optimized_addon.py worker pyinstaller tests docs/maximum-optimizer-validation.md GmodAddonCompressor-master/GmodAddonCompressor GmodAddonCompressor-master/GmodAddonCompressor.Tests
git restore --staged .superpowers/sdd/progress.md 2>$null
git commit -m "feat(maximum): accelerate balanced model search"
git push -u origin feature/maximum-optimizer
```

Verify `.superpowers/sdd/progress.md` is absent from the staged file list. Update draft
PR #4 with measured results and keep it draft until explicit user acceptance.
