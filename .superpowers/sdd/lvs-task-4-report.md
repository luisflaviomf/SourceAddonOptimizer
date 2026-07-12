# LVS R&D Task 4 report

## Outcome

Task 4 implemented deterministic smoothing reconstruction after Blender mesh rebuild, but the
strategy is **not a winner** and remains `unverified`. The fixed-topology Pontiac TransAm wheel
roundtrip compiled byte-for-byte equal to the strict control. Dodge Charger failed the stronger
ordered corner contract before compilation because Blender Source Tools changed the primary bone
ID at `body.smd`, triangle 0, corner 0. The experiment therefore stopped at the requested wheel +
one-car subset and did not expand to the full pressure corpus.

No texture, Task 8 render or Garry's Mod runtime gate was run. The committed lane has
`quality_claim: null`, records every missing gate explicitly and makes no visual-quality claim.

## Smoothing implementation

`maximum_optimizer/smoothing.py` reconstructs adjacency from exact canonical positions rather than
appearance-wedge IDs. UV and material seams with equal intended corner normals do not become false
creases. True corner-normal discontinuities, open edges, non-manifold edges and zero-length edges
remain sharp/fail-closed. Pre-existing degenerate triangles are retained because Task 4 forbids
topology changes.

Every polygon is marked `use_smooth=True` only so Blender Source Tools consumes the supplied custom
split normals. This is not visual global smoothing: every loop receives its explicit intended
normal and sharp boundaries remain encoded independently. The Blender 5 path calls
`mesh.normals_split_custom_set` after `from_pydata` and after exact reconstruction of positions,
triangle indices, UVs, materials and vertex-group weights.

## Ordered SMD contract

The review hardening replaced aggregate-only acceptance and attribute-nearest normal repair with
`maximum_optimizer/smd_contract.py`. Original and exported SMDs are parsed into ordered triangle and
corner records. Acceptance requires:

- identical triangle count, ordinal and winding;
- identical material at each triangle ordinal;
- identical corner ordinal, primary bone, links, link order, weights, UV and every non-normal
  numeric value;
- position equality with only the documented Source Tools float32 serialization allowance of
  `0.000002` per component;
- one-to-one normal restoration from the corresponding original corner, within the strict `0.02`
  gross-drift guard;
- exact ordered intended normals after rewrite, so neither key amplification nor collapse can pass.

Only the three normal tokens on the corresponding exported corner line are rewritten. Triangle or
corner reordering, winding changes, payload substitution, true-normal collapse and ambiguous
correspondence fail closed. No nearest-normal or many-to-one attribute mapping remains in the active
path.

## Real wheel evidence

The clean Blender 5.0.1/Source Tools rerun preserved all three visual SMDs:

| Source | Triangles before/after | Position-normal keys before/after | Hard-normal positions before/after |
| --- | ---: | ---: | ---: |
| `wh.smd` | 1,976 / 1,976 | 1,137 / 1,137 | 96 / 96 |
| `wh1.smd` | 7,230 / 7,230 | 5,239 / 5,239 | 1,221 / 1,221 |
| `wh2.smd` | 6,974 / 6,974 | 5,405 / 5,405 | 1,501 / 1,501 |
| **Total** | **16,180 / 16,180** | **11,781 / 11,781** | **2,818 / 2,818** |

The fresh StudioMDL compile produced 12,029 LOD0 vertices. Every candidate sidecar equals the
strict control in both size and SHA-256:

| Artifact | Bytes |
| --- | ---: |
| `.mdl` | 2,768 |
| `.vvd` | 769,920 |
| `.dx80.vtx` | 205,737 |
| `.dx90.vtx` | 205,737 |
| `.phy` | 3,397 |

`benchmarks/lvs_models/smoothing_fixed_v1.json` stores the portable candidate and control path,
size and SHA-256 for all five artifacts. `maximum_optimizer/smoothing_evidence.py` strictly
recomputes equality and canonical bundle digest
`2a4ab2442eed5aa7410a44ba5af80298e62f9984cc3cb8d142532ac4566f9669`.
It rejects unknown/missing kinds, duplicates, reordering, non-portable paths, size/hash tampering,
false equality and digest drift.

## Charger rejection

The clean Charger review rerun rejected `body.smd` at the first corner: Blender Source Tools changed
the ordered primary bone ID. Because topology, corner payload and skin identity are immutable in
this strategy, no normal rewrite or StudioMDL compile was allowed. This portable rejection is
recorded without workstation paths in the benchmark lane.

## TDD and verification

Initial RED reproduced Blender 5 `from_pydata` flat defaults and failed on the absent smoothing API.
Review-fix RED failed because ordered SMD and portable artifact-evidence modules did not exist.
Adversarial RED/GREEN coverage now includes triangle reorder, winding reversal, position/UV/bone/
weight replacement, normal collapse, close distinct positions, bundle path/size/hash/digest
tampering, duplicate/missing artifacts, flat/curved regions, true creases, seams, degenerate/open/
non-manifold topology and the real Blender Source Tools SMD export.

Final verification:

- focused smoothing/evidence/Blender arguments: 52 passed, 1 environment symlink skip;
- full discovery: 360 passed, 12 environment skips;
- real Blender 5.0.1 wheel smoke and clean family rebuild: passed;
- fresh wheel StudioMDL compile and strict artifact comparison: passed;
- Charger ordered-corner fail-closed rejection: passed;
- Python byte-compilation, JSON parsing and `git diff --check`: passed.

## Reuse for Task 5

Task 5 can reuse the pure smoothing-island/sharp-boundary helpers, Blender 5 per-loop custom-normal
application, fixed mesh payload rebuild, degenerate/open/non-manifold handling and position-normal
amplification gate. The ordered fixed-topology SMD normal restore must not be applied to a topology-
changing candidate; Task 5 must instead prove the attributes of retained direct vertices and use its
own correspondence contract.
