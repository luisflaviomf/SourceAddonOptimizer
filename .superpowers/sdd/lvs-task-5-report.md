# LVS R&D Task 5 report

## Outcome

Task 5 implements the immutable `meshopt-direct-v1` contract, but the first real wheel experiment
compiled smaller than control but remained far larger than the best Blender wheel. The strategy
remains `unverified`, has no quality claim and is not a winner. All five wheel ratios were compiled;
the one-car and five-family stages were not authorized after byte saturation.

## Strategy, cache and direct tuples

`CandidateSpec`, cache payloads and Blender command JSON carry `strategy`, `update_vertices` and
`transfer`. The direct combination is exactly `meshopt-direct-v1`, `false`, `direct-v1`; missing,
unknown or inconsistent execution payloads fail closed. Search trails preserve all three fields and
each independently changes the cache key.

The no-update C++ bridge returns the original packed float32 normal, UV, weight and bone slots.
`compact_direct_result` validates every referenced position, normal, UV, weight and bone-ID tuple
bitwise, validates triangle material ownership and compacts only referenced indices. The Blender
branch bypasses BVH projection, barycentric interpolation and attribute recombination. It installs
the retained normals as exact custom loop normals via the reusable Task 4 Blender helper, but never
uses Task 4's fixed-topology post-export SMD normal restore.

QC graph rewriting and preserved-source handling are unchanged. The smoke retained the declared QC,
animation and collision hashes; none was promoted.

## Real Blender/Source Tools smoke

Blender 5.0.1 and the rebuilt x64 meshoptimizer v1.2 bridge processed Pontiac TransAm wheel
`wh.smd` at requested r0.85:

- triangles: 1,976 -> 1,712 (achieved ratio 0.8663967611);
- source/wedge/output vertices: 1,041 / 1,385 / 1,253;
- locked wedges: 728 / 1,385 (52.5631769%);
- exact position-normal keys after Source Tools export: 1,137 -> 1,253;
- exact-position normal drift: p95 0.0081714 degrees, maximum 0.0480167 degrees.

An initial export RED amplified exact normal keys. The explicit output-vertex-to-original-wedge
provenance then restored normal tokens only after a bijective material/position/UV/bone/weight
match; all three SMDs passed. StudioMDL compiled every ratio. Totals were 1,070,535 (r0.85),
1,056,967 (r0.70), 1,043,081 (r0.55), 1,034,389 (r0.40) and 1,034,283 bytes (r0.25), versus
1,187,559 control and 633,089 best Blender. The 106-byte r0.40-to-r0.25 gain proves lock saturation.
Texture-missing and uncalibrated visual quality remain unverified; no winner claim is made. Evidence is in
`benchmarks/lvs_models/meshopt_direct_v1.json`.

Two clean `/Brepro` x64 native rebuilds matched at SHA-256
`c91d98949cee5488ce038776edfd9156db4103dea67f2e6d6dd6cbf0b5352afa`; ABI remains v2.

The final schema-v2 evidence was regenerated only from five fresh hardened-provenance Blender runs
and five fresh StudioMDL compiles. It binds all 15 visual SMD source/raw/restored hashes and per-mesh
locked/achieved metrics, all 25 candidate and repeated verified control sidecars by portable
path/size/SHA-256, exact tool/runner/source hashes, byte accounting, decision and a canonical
top-level digest. The strict parser rejects incomplete sets, ordering/path drift, invalid or
cross-ratio control hashes, metric non-monotonicity, byte-accounting changes and digest tampering.
The committed builder independently rehashes every ignored artifact and must reproduce the committed
payload byte-for-structure in the focused test.

Final canonical evidence digest:
`3d8854e868ad7cbbb53eb880089f7929c9530cb6fde589a0219f0e5c216cd0dd`.
Final verification after evidence hardening: 2/2 focused evidence tests and 370 full-discovery tests
passed; 12 full-suite skips are the existing environment symlink-privilege skips.

## TDD

Observed REDs covered absent strategy fields, implicit `from_search => true`, missing direct
compaction, native no-update normalization/reordering, mutated direct attributes and mutated material
ownership. Focused GREEN covers payload/command propagation, cache separation, exact tuple
compaction and both native update modes. The real export supplied the appearance-gate RED; applying
exact loop normals did not eliminate serialized-normal drift, so the fail-closed rejection remains.
