# LVS Compiled Model Optimization Design

## Objective

Find a reproducible Garry's Mod model pipeline that beats the current safe Blender/Fidelity baseline in final compiled bytes while preserving structural, material, skinning, animation, physics and measured visual fidelity. UI and release work remain paused until a winning pipeline exists.

## Corpus

Use ten fixed families from `lvs_cars_pack`: Dodge Charger, Dodge Monaco Police, Toyota Supra, Ford Fairlane, Nissan Skyline GTR32, VW Beetle, VW Touareg, Ferrari 365 full rig, Caterham 620R and Pontiac TransAm wheel. Sources come from the already decompiled 163/163 successful experiment. The first five are the pressure set; all ten are the validation set.

For every family record original, control-roundtrip, existing Fidelity/Blender and experimental results: compiled bytes per extension, VVD vertex count, triangles, position/normal and position/UV amplification, materials, bones, bodyparts, skins, sequences, collision hash, render metrics, runtime result and elapsed time.

## Correct objective

The corpus has exactly one LOD and compiled size is dominated by VVD and VTX. Observed formulas are `VVD = 64 + 64*vertices` and approximately `9*vertices + 6*triangles` per VTX. The search objective is final compiled bytes, with compiled unique vertices weighted far more heavily than triangle count.

DX80 removal is a separate GMod-only, lossless packaging candidate. Its savings never count as geometric improvement. Geometry experiments are compared both with and without DX80 so the source of every byte is explicit.

## Acceptance

A candidate is eligible only if control, structural and visual gates pass; physics and preserved animation sources remain byte-identical; no missing textures are tolerated; and final staged hashes match the selected artifacts. A winning geometry strategy must beat the best approved existing baseline on at least 8/10 families, have positive median savings, and have no family regression promoted. Target is at least 30% median geometric compiled-byte reduction; stretch target is about 40% geometry plus the separately validated 16.8% DX80 saving, sufficient to approach or exceed 50% of the models directory.

No result based only on triangle reduction is accepted. No uncalibrated visual thresholds may authorize promotion. Raw metrics and renders may rank experiments, but final acceptance uses calibrated/control-derived gates and in-game validation.

## Required gate repair

Fix the QC tokenizer bug that skips the directive following a non-block directive. Real Monaco regressions must prove skins, attachments, sequences and collision are inventoried. Benchmarks cannot start with incomplete fingerprints.

## Experiment tracks

1. **Lossless GMod packaging:** omit `.dx80.vtx`/compile with DX90-only behavior; validate loading, bodygroups, animation, damage, physics and rendering in Garry's Mod. Keep opt-in until runtime proof.
2. **Normal/smoothing repair:** keep topology fixed, reconstruct smooth islands/custom normals after `from_pydata`, and prove hard-normal/VVD amplification falls without visual change.
3. **`meshopt-direct-v1`:** make `update_vertices=false` a real strategy and cache-key field. Use returned original attributes directly, compact referenced vertices only, and bypass BVH reprojection. Output vertex tuples must never exceed input tuples.
4. **Position-remapped permissive simplification:** calculate geometric borders using canonical positions, not wedge IDs; treat UV/normal/material discontinuities as selective protection rather than locks; simplify the whole compatible mesh rather than creating artificial material borders; protect only skin-influence boundaries.
5. **Compiler-aware hybrid:** retain Blender as a competing topology generator, rebuild smoothing and attributes compiler-aware, score `82*compiled_vertices + 12*triangles`, and search ratios per region.
6. **Progressive canonicalization:** only after the prior tracks, test duplicate/degenerate removal, exact float canonicalization, tolerance-bounded normal/UV/weight canonicalization and planar attribute-affine reduction. Any lossy canonicalization requires its own measured gate.
7. **Escalation:** if these do not win, prototype wedge-aware QEM based on appearance quadrics; compare external Simplygon only as a research ceiling, not a redistributable dependency.

## Iteration protocol

Every experiment uses a named immutable strategy/version and the same corpus/baselines. Run the pressure set first; reject quickly if compiled bytes or raw fidelity are worse. Promote to ten models only after pressure-set improvement. Persist every result so unchanged stages are cached. Continue with the next ranked hypothesis until a winning strategy meets acceptance or all technically plausible tracks have reproducible rejection evidence.

## Primary references

- meshoptimizer v1.2 pinned documentation and permissive simplification: https://github.com/zeux/meshoptimizer/blob/9d9890c73011d75920af614485296d1e03e95448/README.md
- Valve Source SDK `studio.h` and `optimize.h`: https://github.com/ValveSoftware/source-sdk-2013/tree/88fa198fba3fb85d46d4c95018254693fdc3af0a/src/public
- Garry's Mod file types: https://wiki.facepunch.com/gmod/Important_Filetypes
- Blender Decimate: https://docs.blender.org/manual/en/5.0/modeling/modifiers/generate/decimate.html
- Hoppe appearance quadrics: https://www.hhoppe.com/newqem.pdf

