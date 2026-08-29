# Universal VTF Persistent Cache Design

**Status:** Approved design, implementation pending

**Date:** 2026-08-29

## Goal

Eliminate repeated Universal VTF image generation and metric calculation when every relevant input is identical, while preserving exactly the existing compression decisions, fail-closed safety criteria, manifests, and candidate bytes.

The first warm corpus build must reuse all 1,152 VTF results from the preserved production, recalculate zero VTFs, and publish the same three candidate tree hashes after canonicalizing invalid floating-point metrics from JSON string `"NaN"` to JSON `null`.

## Non-goals

- Do not change compression policy, thresholds, semantic classification, downsampling, encoding, BC3-to-BC1 behavior, or selected levels.
- Do not promote a candidate over the current safe baseline.
- Do not expand the GMod runtime harness.
- Do not treat a partial, malformed, stale, or unverifiable cache artifact as a hit.
- Do not cache timing or hit-rate fields inside official candidate manifests.
- Do not delete the previous production, interrupted recovery, corrupted cache evidence, or current safe baseline.

## Frozen authorities and evidence

The importer and benchmark operate only against these frozen inputs:

- Raw tree: 3,827 files, 1,441,918,651 bytes, SHA-256 `5f7b31c77e34608c4a40c6b945a7ad24b151be4ad067761c24aa1f93ed908758`.
- Safe tree: 3,664 files, 438,230,044 bytes, SHA-256 `8e60b8b46744a192fd5ab1ab77848ab9fd4123f1dd042c31d365e16505a698af`.
- VTF count: 1,152.
- Policy: `de1fd1acd3434d34d6365aec224d56e85a4a319ff935ab708ffa93f87be14266`.
- Fixture: `9adb529e565f9e1ba54f0833af21c59db8674b5bed286d9cac37c6f8ace13d7b`.
- Toolchain: `415bd8ae0217aea0f85037eb5004fd3b03246229cfbff43ed28d12e086f0a4c6`.
- BC bridge: `9bf65c2b404365c23836b6eb2ff206a0fed6d8c217a71792406b775c8fb6dad1`.
- Runtime caps: absent, represented by a canonical `none` value.
- Previous build log: SHA-256 `b8d26aa00828d948d5df25c46db22090e48d7fe69c78b40419407509f12c67b2`, observed duration 27,055.6239045 seconds.

The frozen legacy outputs are:

| Profile | Bytes | Tree SHA-256 | Legacy manifest SHA-256 |
|---|---:|---|---|
| Conservative | 471,027,900 | `ca20f2d97e397a2f421bcc81c812a491779c8e2c0014d34cd73c0107f4afac15` | `d272ffde9b06012eee262d6ee866b8031a09b73a8bd76123a0784140711f7926` |
| Balanced | 467,567,820 | `409ea63136540d6dcd6a6f69e03aa516ed55653bef5f22af53df83ab65da850e` | `03a76b08b6116517ef6072b674e7f70b21df3eb887ba5ce58db9434403ef2961` |
| Aggressive | 457,213,132 | `5751602cc4dd3a1ca0c3e21d4e4d92500ebe60fca77e28919de6bf0a19797573` | `cd088b66d02311b22847f9e1f3408d9cd8be6d260acce09b23e236516d6a5d49` |

The interrupted pre-cache run remains evidence only. It stopped after 173 logged completions and preserved 181 candidate directories, 3,192 VTF artifacts, and 90,593,664 bytes. It is not needed to seed the complete cache because the frozen completed production covers all 1,152 inputs.

## Architecture

The cache has two immutable checkpoint layers and a content-addressed blob store.

### Analysis checkpoint

An analysis checkpoint represents expensive raw-derived work:

- the complete level-by-route attempt matrix;
- structural validity and deterministic failure reasons;
- every metric plane and cross-plane metric;
- PSNR, SSIM, edge retention, alpha, cutout, normals, detail, glass, emissive, temporal, cubemap, and mip diagnostics;
- selected route at each level;
- references to available raw-derived artifacts;
- the input identity that produced the analysis.

Runtime caps and safe-tree identity do not invalidate an analysis checkpoint because neither changes decoded raw pixels or measured quality. They can invalidate completion and cause the selector to run again over cached metrics.

### Completed checkpoint

A completed checkpoint represents exact outputs for the three profiles:

- conservative, balanced, and aggressive selected levels and routes;
- the profile attempts and worst gates;
- exact final VTF blob references, byte lengths, SHA-256 hashes, and container shapes;
- shared manifest-row data and the per-profile output fields;
- BC eligibility and bridge receipt;
- the completion identity, including safe VTF and runtime caps.

A completed checkpoint is sufficient to compose all three trees without invoking the image materializer, encoder, decoder, or metric calculator.

### Legacy imported completion

The completed old production no longer contains every raw-derived intermediate or selected spatial BC3 payload. It does contain all metrics, decisions, and exact final profile outputs. The cache therefore supports the explicit coverage type `imported-exact-profile-finals`.

This coverage type is valid only for its exact analysis and completion identities. It may replay final profile blobs and regenerate canonical manifests, but it must become a miss when any identity component changes. New cache misses produced normally use `full-attempts` coverage.

## Cache identity and invalidation

All keys are SHA-256 hashes of canonical JSON. Timestamps, absolute temporary paths, run IDs, and output destinations never enter a key.

### Analysis identity

The analysis identity contains:

- cache schema `universal-vtf-preparation-cache-v1`;
- explicit preparation algorithm version;
- canonical relative path;
- raw VTF SHA-256, bytes, version, flags, format, width, height, depth, frames, faces, and mip count;
- authored/generated mode;
- expected levels and routes;
- semantic consumer-proof SHA-256, semantic classes, and chosen semantic route;
- policy fingerprint;
- fixture SHA-256;
- toolchain SHA-256;
- BC bridge SHA-256 and the path's bridge-row digest or canonical absence;
- all materialization, thumbnail, downsampling, comparison, and encoding parameter identifiers.

Raw SHA alone is insufficient: the corpus has only 1,077 unique raw hashes, and duplicate byte groups can have different consumers and semantic proofs.

The explicit preparation algorithm version must change whenever materializer, downsampler, decoder, metric calculation, route scoring, selector semantics, thumbnail generation, container writing, or another result-relevant implementation changes. Manifest-only, telemetry-only, and cache-storage-only changes do not change this version.

### Completion identity

The completion identity contains:

- the analysis key;
- safe VTF SHA-256 and bytes;
- runtime-caps SHA-256 or canonical `none`;
- the selected profile levels and routes;
- BC completion parameters.

A changed safe VTF or runtime cap can reuse the analysis checkpoint but cannot reuse the prior completion unchecked.

### Fail-closed behavior

Any unknown schema, noncanonical JSON, key mismatch, identity mismatch, missing field, incomplete matrix, invalid enum, unsupported coverage, changed input, blob drift, layout mismatch, missing object, conflicting payload, reparse point, path escape, or parser failure is not a hit.

Corrupt evidence is moved to an owned quarantine path and preserved. The affected VTF is recalculated; unrelated valid cache entries remain reusable.

## Physical layout

```text
cache-root/
  v1/
    objects/sha256/ab/<artifact-sha256>.vtf
    analysis/ab/<analysis-key>/receipt.json
    completed/ab/<completion-key>/receipt.json
    runs/<run-id>.jsonl
    staging/<owned-guid>/
    quarantine/<owned-guid>/
```

Objects are deduplicated by bytes. The frozen outputs contain 3,456 profile references but only 1,113 unique final VTF blobs totaling 200,632,728 bytes.

The builder physically copies from immutable cache blobs into candidate staging trees. It does not hardlink files that the existing composition path could overwrite.

## Atomicity, concurrency, and resume

An entry is visible only after all referenced objects exist and validate.

1. Acquire an in-process single-flight task for the cache key.
2. Use a per-key cross-process lock opened with exclusive sharing.
3. Write owned temporary objects and receipt under `staging`.
4. Flush file contents and metadata to disk.
5. Re-read and validate SHA-256, size, canonical JSON, VTF layout, and entry key.
6. Atomically rename objects and then the receipt directory on the same volume.
7. Append a completed per-file event to the run journal.

Cancellation or a crash before the final rename publishes no hit. A closed stale lock file does not block a new process. If another process wins the same key, its entry is revalidated and used. Two different valid payloads for the same key are a deterministic-conflict error and fail the build.

Each atomically published VTF entry is the checkpoint. A restarted build creates a new run journal, finds completed entries, and resumes with hits. No output tree is trusted as a checkpoint by itself.

## Legacy importer

The legacy importer is a separate command and the only component allowed to read named `"NaN"` strings.

It requires explicit expected SHA-256 values for:

- the legacy build log;
- all three legacy manifests;
- raw, safe, bridge, policy, fixture, and toolchain authorities.

Import sequence:

1. Verify all frozen hashes before parsing.
2. Parse only the expected legacy manifest schema with a dedicated converter.
3. Accept `"NaN"`, `"Infinity"`, and `"-Infinity"` only where the typed schema requires a floating-point value.
4. Convert NaN to in-memory `double.NaN`; canonical output writes it as JSON `null`.
5. Validate aggregates, path sets, raw and safe identities, bridge rows, and all three final tree hashes.
6. Verify every one of the 3,456 final VTF files against the row SHA-256 and byte length and parse its container layout.
7. Require shared metrics and decisions to agree across the three manifests.
8. Recompute current semantic proofs.
9. Reconstruct attempt matrices, rerun the current selector, and require identical profile decisions.
10. Publish 1,152 immutable `imported-exact-profile-finals` checkpoints.

The normal `UniversalVtfManifestStore` continues rejecting legacy `"NaN"`. The importer does not modify the old files.

## Builder integration

The lookup occurs immediately after semantic resolution and before `MaterializeCandidatesAsync`.

```text
resolve semantics
→ construct and validate identity
→ completed hit: load metrics/decisions/final blobs
→ analysis hit: rerun selector and construct completion
→ miss: materialize, measure, select, finalize, and publish checkpoints
→ report per-file checkpoint
→ compose three physical trees
→ write canonical manifests
→ run both existing transactional audits
→ publish or preserve recovery
```

The corpus driver requires an explicit `--cache-root`. Cache, raw, safe, output, and report authorities may not overlap.

## Telemetry and reporting

Cache telemetry is emitted separately from official manifests:

- total VTFs;
- completed checkpoint hits;
- analysis checkpoint hits;
- misses/recalculated VTFs;
- invalid or corrupt entries;
- reused VTFs;
- hit rate;
- seed duration;
- preparation duration;
- total build duration;
- cache schema and preparation algorithm fingerprint;
- run journal path and SHA-256.

The final report compares the old 27,055.6239045-second production with the slower of two warm builds:

```text
speedup = 27055.6239045 / max(warm_build_1_seconds, warm_build_2_seconds)
```

## Verification strategy

Implementation follows test-driven development. Tests must first fail and then cover:

- canonical identity stability;
- invalidation for raw, path, structure, algorithm, policy, toolchain, bridge, semantics, parameters, safe VTF, and runtime caps;
- timestamp-only changes remaining hits;
- every metric round-tripping with null/NaN and infinities;
- atomic commit and cancellation boundaries;
- truncated/noncanonical receipts;
- missing and mutated blobs;
- incomplete matrices;
- same-key and different-key concurrency;
- deterministic-conflict rejection;
- reparse-point and path-escape rejection;
- interruption followed by per-file resume;
- exact cold/warm tree, manifest, selection, and artifact equality;
- strict legacy hash requirements;
- named-NaN import without metric recalculation;
- cross-profile metric/decision drift rejection;
- normal manifest loader continuing to reject named NaN;
- progress and telemetry counts matching actual materializer calls.

After seeding the frozen production, two new processes build to separate destinations. Both must report:

- reused VTFs: 1,152;
- recalculated VTFs: 0;
- hit rate: 100%;
- final tree hashes equal to the frozen values;
- canonical warm manifests byte-identical to one another;
- all C# and independent Python audits CLEAN.

## Rollback and preservation

- The safe baseline remains untouched.
- The legacy DIRTY_NAN production remains untouched and clearly labeled.
- The interrupted pre-cache recovery remains untouched.
- Candidate output destinations must not exist before a run.
- Cache corruption affects only its owned entry and is quarantined rather than deleted.
- No cached candidate is promoted over the safe baseline by this feature.

