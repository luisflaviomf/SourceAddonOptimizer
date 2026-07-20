# Maximum Balanced Parallelism Design

## Objective

Reduce Maximum model-optimization wall-clock time without changing candidate
eligibility, visual thresholds, structural gates, selected compiled bytes, DX80
policy, or output transaction safety. The default execution policy targets roughly
50% of the machine's logical CPU capacity and adapts downward when available memory
cannot safely support that concurrency.

On the reference machine, which has 20 logical processors and 32 GB of RAM, Auto
targets ten concurrent family slots before the memory guard is applied. The user can
override Auto with an explicit family-job count. A value of one remains the serial
reference mode used to prove result equivalence.

## Root cause

The WPF runner already sends `--jobs 8`, `--decompile-jobs 8`, and
`--compile-jobs 8`, but Maximum only consumes the decompile value. The Maximum
orchestrator constructs `CandidateTools` with its default `compile_jobs=1`, iterates
all family manifests in one outer `for` loop, and evaluates each family's adaptive
candidate sequence synchronously. Runtime inspection confirmed one worker and one
Blender child at a time on a 20-logical-CPU system.

Maximum also repeats reference work. Its visual adapter copies the original source,
regenerates the original region manifest, and renders the original side for every
candidate. After a state has failed a hard whole-model visual gate, it continues
rendering later states even though the candidate can no longer become eligible.

## Approaches considered

### Parallel candidates within one family

This maximizes immediate fan-out but conflicts with adaptive search: later
candidates depend on earlier pass/fail and marginal-saving evidence. Speculatively
starting all candidates wastes CPU, defeats early stopping, increases memory use,
and can do more total work. This approach is rejected.

### Parallel families only

Families have disjoint workspaces and independent candidate searches, so they can be
processed concurrently without changing their winners. This is the low-risk core of
the design, but it leaves repeated reference rendering untouched.

### Parallel families plus safe work elimination

This is the selected approach. It combines bounded family concurrency with a sealed
per-family reference-render cache and immediate rejection after a hard whole-state
visual failure. It preserves the adaptive sequence inside each family while removing
work that cannot affect selection.

## Execution model

### Coordinator ownership

`run_maximum_addon` remains the sole run coordinator. It owns:

- ordered final outcomes and selected snapshots;
- event numbering and the durable event history;
- partial and terminal report writes;
- fidelity-selection audit writes;
- final staging, DX80 removal, verification, and atomic output promotion.

Workers never mutate these coordinator-owned collections. Each worker receives one
immutable family manifest and returns a complete family result containing its
outcome, control snapshot, selected snapshot, selected build authorization,
selection audit record, and locally ordered events. The coordinator merges results
by original manifest index, regardless of completion order.

### Family scheduler

A bounded `ThreadPoolExecutor` schedules independent family jobs. Threads coordinate
external Blender and StudioMDL processes; CPU-heavy Python work that does not release
the GIL is not the primary workload. A family keeps its current adaptive candidate
loop in serial order.

Before submission, families receive a stable work estimate derived from source bytes,
visual-state count, and original compiled-family bytes. Larger estimates are
submitted first to reduce the long-tail makespan. Stable manifest index is the
tie-breaker. Reports and final output remain in canonical manifest order.

Missing or invalid family manifests remain coordinator-handled preservation cases;
they do not consume worker slots.

### Resource policy

The new CLI option is `--maximum-jobs`:

- `0`: Auto balanced, the default;
- `1`: serial reference execution;
- `2..N`: explicit maximum concurrent families.

Auto computes a CPU target as `max(1, floor(logical_processors * 0.50))`. It then
applies a memory ceiling. The memory guard reserves the larger of 2 GiB or 10% of
physical RAM for the OS and other applications, and budgets 768 MiB for each active
family based on the measured Blender render working set. If reliable memory data is
unavailable, CPU capacity is used with a conservative cap of four jobs. Explicit job
counts are still bounded to the logical processor count and may be throttled by the
same emergency memory guard; the UI explains this behavior.

Every Blender invocation made by Maximum includes `--threads 1` while more than one
family slot is enabled. This prevents each Blender child from independently claiming
all CPU threads. Serial mode retains Blender's current thread behavior. Decompilation
continues to use the existing `--decompile-jobs` setting. Per-candidate StudioMDL
compilation stays at one internal job because a family candidate contains one target
QC; concurrency comes from compiling different families in parallel.

The memory guard is checked before starting a new family. It delays submission rather
than cancelling running work. No busy loop is permitted: the coordinator waits for a
completion, cancellation, or a bounded resource-recheck interval.

## Sealed reference-render reuse

The exact ratio-1 control remains mandatory for every family. Its original-side
render output becomes the family reference bundle. The bundle contains:

- the copied original render sources and source-region manifest;
- each canonical bodygroup/LOD state's configuration manifest;
- original textured and clay images for all required angles and poses;
- original geometry measurements and render manifests;
- hashes of the original family input, renderer, profile, material roots, VTF tool,
  animation inputs, and all bundle files.

Candidate validation reuses a bundle only when its complete identity and file hashes
match current inputs. Otherwise it regenerates the bundle. Candidate-side renders
remain fresh and are never authorized from another candidate. The comparison stage
combines the sealed original manifest with the new candidate manifest and applies the
same calibrated gates as serial mode.

Reference publication uses a private temporary directory followed by an atomic
rename. A per-family lock prevents duplicate publication. Reparse points, missing
files, case collisions, unexpected membership, or hash changes invalidate the bundle
and fail closed to regeneration.

## Safe early rejection

Structural validation continues before any visual render. During whole-model visual
validation, each state is compared immediately after its candidate render. If that
state fails any calibrated hard gate, the candidate is terminally rejected and later
states are not rendered. This does not change eligibility because whole-model gates
require every reachable state to pass, and focused recovery is entered only after the
whole-model gate passes.

Passing candidates still render every required state and run the existing focused
region validation and recovery flow. The report records `early_rejected=true`, the
failing state, completed-state count, and exact failures so reduced diagnostic work
is explicit.

## Cancellation and failures

One shared cancellation event is visible to the coordinator, workers, and child
process runner. Cancellation stops new submissions, terminates active process trees
through the existing Windows Job Object handling, waits for worker cleanup, and
produces one canonical cancelled report. No output is promoted after cancellation.

A family exception is converted into that family's existing fail-closed preservation
outcome; it does not cancel unrelated families. Coordinator or report-journal failure
remains run-fatal. Final promotion still occurs only after all scheduled families
reach terminal outcomes and all selected artifact hashes are revalidated. Every
produced Maximum output continues to exclude `.dx80.vtx`, including preserved-family
fallbacks.

## WPF behavior

The Models and Pipeline tabs gain a Maximum-only execution setting:

- label: `Parallel families`;
- default: `0 (Auto — balanced 50%)`;
- accepted manual range: 1 through the detected logical processor count;
- helper text: actual concurrency can temporarily decrease to protect available RAM.

The value persists in settings and is passed as `--maximum-jobs`. Normal and Fidelity
retain their existing Jobs controls and behavior.

Progress adds aggregate fields without removing the current family/candidate detail:

- active family count and effective job limit;
- completed/total families;
- most recently updated family and candidate;
- whether Auto is memory-throttled.

Events from parallel workers are serialized by the coordinator before being printed,
so WPF never receives interleaved JSON lines.

## Determinism and compatibility

For identical inputs, tools, profile, and budget, `--maximum-jobs 1` and Auto must
produce identical per-family selected candidate IDs, compiled artifact hashes,
selected byte counts, fidelity decisions, and final output tree hashes. Timing,
event arrival order, and `cache_hit` metadata may differ. The final report stores
families in canonical order and explicitly records requested jobs, effective CPU
target, memory ceiling, peak active families, and elapsed time.

Existing work directories remain resumable. New reference bundles are additive and
content-bound; their absence only causes regeneration. Cache keys for candidate
geometry do not change solely because job count changes.

## Testing strategy

Implementation follows test-first development.

Unit tests cover:

- Auto CPU calculation, explicit bounds, memory throttling, and unavailable metrics;
- stable longest-estimated-work-first scheduling;
- no more than the effective family limit active at once;
- deterministic coordinator merge despite reverse completion order;
- serialized event indexes and non-interleaved event lines;
- cancellation stopping submission and draining active workers;
- family failure isolation;
- reference bundle identity, atomic publication, corruption invalidation, and
  cross-candidate reuse;
- early rejection skipping later states while preserving the same failure;
- Blender thread arguments in serial and parallel modes;
- CLI, frozen worker, WPF runner, settings, and progress-parser propagation.

Integration tests compare serial and parallel runs over deterministic fake adapters,
then over a small real LVS sample. They assert identical candidate selection, output
hashes, DX80 absence, and visual/structural decisions.

## Performance acceptance

Benchmark the same representative LVS sample twice from cold work directories:

1. `--maximum-jobs 1`;
2. `--maximum-jobs 0` on the 20-logical-CPU reference machine.

Both runs must produce identical selected candidates and final hashes. The balanced
run must achieve at least a 3x wall-clock speedup, average total CPU utilization in a
35% to 65% band during the candidate phase, no out-of-memory failure, and no new
fidelity or structural rejection. The expected range is 4x to 8x, but it is not an
acceptance promise. If the 3x target is missed, profiling evidence must identify the
remaining bottleneck before further concurrency is added.

## Out of scope

- Parallel candidate speculation inside one family.
- Relaxing or approximating any fidelity or structural gate.
- GPU-specific rendering requirements.
- Changing the calibrated Maximum profile or candidate ladder.
- Altering Normal or Fidelity optimization semantics.
