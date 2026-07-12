# LVS R&D Task 6 report

## Outcome

`meshopt-direct-position-v1` removes false wedge-border saturation on the Pontiac wheel while
preserving the Task 5 direct/no-update tuple contract. It is not a winner and has no quality claim.
The wheel compiles substantially smaller than `meshopt-direct-v1`, but remains larger than Blender;
the first full-car probe compiles but remains slightly larger than Blender, so the pressure set was
not run.

## Topology and ownership

Exact float32 positions form canonical IDs with the same first-representative behavior as
`meshopt_generatePositionRemap`. Canonical edge ownership locks true open edges and all incident
positions of nonmanifold edges. UV, normal, material and skin transitions are counted separately and
use `Protect`; the old dominant-skin-signature `Lock` rule is bypassed. Flags are propagated to every
wedge at a canonical position before meshoptimizer v1.2 permissive simplification.

The native bridge ABI is 3. The new mode simplifies one global index buffer instead of per-material
subsets. Output triangle material is accepted only when the intersection of the three source-index
material provenance sets contains exactly one value; empty or ambiguous ownership returns native
error `-7`. A synthetic unsplit shared-material mesh proves that fail-closed behavior. The legacy
`meshopt-direct-v1` execution mode remains available and its historical evidence builder now reads
the attested Task 5 DLL rather than silently rebinding that evidence to the current bridge.

## Wheel benchmark

Both requested ratios passed source/material/bone/animation/collision audits and StudioMDL compile.

| requested | achieved | locks | triangles | output vertices | compiled vertices | bytes |
| --- | --- | --- | --- | --- | --- | --- |
| 0.25 | 0.599135 | 421/12,090 (3.48%) | 9,694 | 8,121 | 8,060 | 784,269 |
| 0.40 | 0.598764 | 421/12,090 (3.48%) | 9,688 | 8,125 | 8,064 | 784,525 |

For comparison: strict control is 1,187,559 bytes, Task 5 best is 1,034,283 bytes and Blender best
is 633,089 bytes. Thus r0.25 is 34.0% below control and 24.2% below Task 5, but 23.9% above Blender.
Requested r0.25 saturates at the configured error bound, not at false locks.

## Car probe and decision

Dodge Charger completed all direct SMDs and StudioMDL at r0.40: 295,861→136,541 triangles,
60,901/275,151 locked wedges (22.13%), 163,383 output vertices, 158,648 compiled vertices and
14,797,755 bytes. That is 2,893 bytes below Fidelity (14,800,648), but 58,771 bytes above Blender
(14,738,984). The direct serializer was required because global simplification creates legitimate
new triangles: it copies each retained original SMD corner tuple exactly and emits only the new
connectivity/material ownership. No clamp, guessed material, reprojection or approximate normal
repair was used. Since both wheel and car still lose to Blender, the five-family pressure set was
not plausible.

Textures remain unavailable, so visual quality is unverified. Portable measured evidence is in
`benchmarks/lvs_models/meshopt_direct_position_v1.json`. Two clean `/Brepro` builds matched SHA-256
`173e1b86dad4e553d702ab3586ac8d4f77e167090f4f5abada2f09f3f3594c3c`.

## Verification

The TDD REDs covered the absent canonical topology API, absent immutable strategy and absent native
position-remap option. Focused tests cover duplicated UV/normal/material wedges, real borders,
nonmanifold fail-closed behavior, skin transitions, exact material ownership and ambiguous ownership.
Final discovery: 381 tests passed with 12 existing environment skips.
