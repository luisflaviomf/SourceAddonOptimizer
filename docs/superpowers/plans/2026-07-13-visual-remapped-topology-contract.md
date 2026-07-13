# Visual-Remapped Topology R&D Implementation Plan

**Goal:** Add a separate sealed structural contract that records boundary deltas without weakening either existing direct/remapped contract.

**Architecture:** Refactor the current remapped validator into one shared internal analysis path with two proof policies. Exact remapped keeps boundary equality. Visual-remapped emits a distinct proof identity and complete per-component/aggregate boundary delta. Batch generation receives a separate typed R&D discriminator using the existing position-remap path.

## Constraints

- Preserve `direct-position-v1` and `remapped-topology-v1` behavior byte-for-byte at their public interfaces.
- Do not duplicate the parser/topology engine.
- Visual-remapped remains `quality_status=unverified` and non-authorizing.
- Do not integrate WPF, worker production routing, scheduler preference, production adapters, or authority bundles.
- Compile/render only structurally accepted real cases.

## Task 1: RED contract and isolation tests

**Files:**

- Create `tests/maximum_optimizer/test_visual_remapped_topology.py`.
- Modify `tests/maximum_optimizer/test_maximum_blender_args.py` only for the new typed candidate.

1. Add a boundary-retriangulation fixture that the exact validator rejects and the new import/function initially lacks.
2. Specify proof identity, unverified quality, target fields, exact provenance, and per-component/aggregate retained/added/removed boundary hashes/counts.
3. Port adversarial structural fixtures: synthesized/cross-material payload, bridge/deletion, ambiguous duplicate fans, duplicate/reverse, degenerate, nonmanifold/valence/direction/orientation regressions, caps, unknown/tampered proof fields.
4. Assert the old exact validator still rejects the boundary fixture and accepts its existing happy path unchanged.
5. Add the exact new batch tuple and assert every mixed/legacy spelling is rejected.

Run focused tests and confirm RED before implementation.

## Task 2: Shared analysis and distinct sealed proof

**Files:**

- Modify `maximum_optimizer/remapped_topology.py` only to expose shared immutable analysis helpers without changing exact behavior.
- Create `maximum_optimizer/visual_remapped_topology.py`.

1. Extract the source/output parsing, provenance, component, boundary-set, and topology-stat analysis used by exact validation.
2. Keep exact proof construction and exact boundary rejection on the existing public path.
3. Implement visual proof/component dataclasses, strict payload loader, canonical set/delta hashes, aggregate relationships, and seal verification.
4. Ensure every non-boundary invariant is shared and identical.
5. Run exact, visual, direct-builder, prefilter, bridge, and batch tests.

## Task 3: Batch R&D discriminator

**Files:**

- Modify `batch_optimize_maximum.py`.
- Modify focused batch tests.

Add only `meshopt-remapped-visual-v1` / `visual-remapped-topology-v1` to the explicit position-topology/no-update/exact-corner R&D routing sets. Do not add authority or production selection.

## Task 4: Real LVS structural/compile/render observations

1. Validate Skyline trunk under both contracts: exact must reject its boundary change; visual-remapped may pass only if all other invariants hold.
2. Run glass/wheel as controls and Monaco/body cases as honest rejection probes.
3. Pair-compile and render only visual-structural passes with the hardened smoke/evidence flow.
4. Record target/achieved reduction, exact boundary delta, compiled delta, and descriptive render metrics as `local_experiment`, `unverified`, never winner.

## Task 5: Verification and review

1. Run focused regression modules.
2. Run full `tests/maximum_optimizer` discovery.
3. Request independent review of contract isolation, boundary delta relationships, caps, and fail-closed loaders.
4. Commit validator/discriminator and smoke evidence in separate checkpoints; never stage `.superpowers/sdd/progress.md`.
