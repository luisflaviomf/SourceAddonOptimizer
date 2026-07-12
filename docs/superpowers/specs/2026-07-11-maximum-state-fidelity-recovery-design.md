# Maximum State Fidelity and Crash Recovery Design

## Scope

This delta closes three authorization gaps in Maximum mode without changing Normal or Fidelity behavior:

1. each base/LOD render validates against a canonical region manifest containing only that state's source descriptors;
2. representative animation is required only for meshes with real deformation evidence;
3. interrupted output promotion is recovered before logs, decompilation, or other run mutation.

## Per-state region manifests

`maximum_optimizer.regions.filter_region_manifest()` consumes a validated `RegionManifest` and an exact set of normalized source identities. It reconstructs the subset through `build_region_manifest`, preserving occurrences while recalculating canonical descriptor keys and schema. Unknown, duplicate, or empty identity sets fail closed.

Production visual validation writes one subset manifest per render state beside the full source manifest. Keeping the same parent preserves source-relative path validation in `render_previews.py`. The renderer continues using `resolve_region_assignments(..., require_complete=True)`, so a descriptor missing within the selected base/LOD subset remains a hard failure.

## Animation classification

Animation requirement is based on source evidence, not merely `$sequence` or a bone list. A visual SMD is deformable only when it defines at least two bones and its triangle vertices show meaningful positive influence from at least two distinct bones, either on one multi-weight vertex or across separately controlled vertices. Invalid or ambiguous weight syntax does not authorize animation bypass.

A representative pose is available only when the paired original/candidate animation reference has identical canonical family-relative identity and identical frame evidence containing a positive frame. Outcomes are:

- deformable mesh plus paired positive animation frame: validate bind and representative poses;
- rigid mesh or bind-only one-frame sequence: validate bind only and record `rigid-or-bind-only` explicitly;
- deformable mesh without a paired real animation: fail with `representative-animation-unavailable`.

The classification and selected evidence are persisted as canonical JSON in candidate logs.

## Crash-safe promotion recovery

Promotion uses a sibling marker named for the exact destination. The marker contains only strict schema-validated strings and booleans: destination name, staging name, backup name or null, nonce, phase, and whether a destination existed. Stored paths must equal the expected lexical sibling paths and resolve within the non-reparse destination parent.

Protocol:

1. exclusively create and fsync the marker, then fsync its directory;
2. rename existing destination to the nonce-bound backup;
3. update/fsync the marker and parent directory;
4. rename staging to destination;
5. update/fsync the marker and parent directory;
6. verify the committed destination;
7. remove safe backup and marker, fsyncing the directory after each cleanup boundary.

Crash injection hooks exist after marker creation and each rename/phase update. Recovery runs after lexical path validation but before work/log creation or decompilation:

- destination absent plus exactly one safe verified backup: restore it atomically;
- destination present: never overwrite it; remove only marker-associated safe stale backup/staging artifacts;
- multiple matching backups or invalid marker/path/reparse state: fail closed;
- staging is never promoted by recovery.

Legacy orphan backups without a marker are recoverable only when exactly one safe direct sibling matches the exact destination prefix. All filesystem operations remain within the validated parent.

## Cancellation

If cancellation is already set after configuration/path validation, the API still creates its durable report journal, emits `run_started`, then emits `run_cancelled` without inventory, candidate work, or promotion. The atomic rename transaction remains non-interruptible after its final pre-rename cancellation barrier.

## Verification

Tests must exercise real region assignment, real SMD evidence parsing, every promotion crash barrier, ambiguous backup states, destination-preserving cleanup, lexical/reparse rejection, pre-set cancellation events, Normal/Fidelity regression coverage, full pytest discovery, Python compilation, and `git diff --check`.
