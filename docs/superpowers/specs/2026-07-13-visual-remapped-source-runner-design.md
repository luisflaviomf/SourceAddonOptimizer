# Visual-Remapped Source Runner Design

## Scope and identity

This design extends the R&D contract introduced by commit `6238897` with an isolated production-grade candidate generator. It does not grant production authority. The only accepted identity is:

- strategy `meshopt-remapped-visual-v1`;
- transfer `visual-remapped-topology-v1`;
- quality status `unverified`;
- authorizing flag `false`.

The existing `BlenderDirectSourceRunner`, its request/result types, scheduler wiring, WPF, worker routing, production adapters, and authority bundle remain unchanged. The visual runner lives in a new module and may reuse hardened filesystem helpers without accepting or returning direct-runner types as its own discriminated result.

## Request boundary

`VisualRemappedSourceRequest` wraps one sealed `DirectSourceBuildRequest` solely as the already-established family/source/material/prefilter trust root. It additionally seals the exact post-prefilter input byte size/hash, requested ratio, visual strategy/transfer, non-authorizing state, and its own request hash. The requested ratio must equal the wrapped source request ratio. A distinct candidate id and cache digest derive only from the visual request seal.

This composition avoids copying the large source authority schema while preventing a direct request or result from being mistaken for a visual one. The runner rejects input bytes, material inventory, request identity, candidate identity, and cache identity that do not match the sealed request.

## Execution boundary

Each call creates a fresh, exclusively owned mini-root below a pinned work root. It copies one SMD, creates one QC and one exact-field candidate JSON, and launches Blender once with the repository batch script and pinned meshoptimizer library. The candidate JSON calls the batch path with `meshopt-remapped-visual-v1`, `visual-remapped-topology-v1`, no vertex updates, the sealed ratio, and the existing degenerate prefilter discriminator.

The runner reuses or matches the direct runner protections:

1. absolute non-overlapping source, output, repository, and work boundaries;
2. no symlink/reparse ancestors, files, directories, or artifact entries;
3. regular-file identities and byte hashes for Blender, batch script, and meshoptimizer before and after the process;
4. bounded source bytes, artifact files/bytes, and process duration;
5. cancellation checks before acquisition, after process, before evidence, and before publication;
6. one fresh run root, exclusive file creation, atomic no-replace output publication, and identity-aware quarantine on every failure;
7. two artifact inventories plus publication-copy hashing so mutation, TOCTOU, stale run data, and output collisions fail before publication.

The existing direct runner file is not modified.

## Validation and evidence

The output is read once into immutable bytes for UTF-8 decoding and hashing, then passed to `validate_visual_remapped_topology_smd`. The resulting sealed structural proof must bind the request ratio and the exact copied source/output hashes. Batch metrics are reparsed with exact identity, totals, file coverage, visual strategy/transfer, provenance hashes, region-manifest hash, and engine version checks.

`VisualRemappedSourceEvidence` is an exact-field, canonically sealed, reparsable payload. It includes:

- visual request, candidate id, and cache digest;
- fixed `unverified` / non-authorizing state;
- source/output byte proofs;
- complete `VisualRemappedTopologyProof`, including requested/achieved ratios and boundary deltas;
- canonical bounded run artifacts;
- canonical Blender/script/meshoptimizer tool proofs and aggregate toolchain hash;
- batch engine version and evidence seal.

Loading evidence recomputes all derived identities and relationships. A sealed payload from another request, candidate, strategy, cache key, artifact set, toolchain, source, or output is rejected. A structural pass is not a visual-quality or scheduler-selection claim.

## Future regional adapter, not activated

A future adapter can accept a sealed regional source request plus ratio, construct `VisualRemappedSourceRequest`, invoke the runner, and return its evidence to a competition layer. That layer must independently bind coherent StudioMDL compile evidence and calibrated whole/focused visual gates to the same source/output hashes before comparing aggregate reduction. This task defines no scheduler registration, preference, winner field, production adapter, WPF control, or worker command.

