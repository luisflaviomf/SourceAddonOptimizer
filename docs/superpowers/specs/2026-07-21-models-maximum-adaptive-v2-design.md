# Models Maximum Adaptive v2 Design

**Date:** 2026-07-21

**Status:** Approved architecture; implementation requires this written specification to pass review.

## Objective

Rebuild Models Maximum as an adaptive layer over the established Models pipeline. Maximum must start from a real Models normal result, simplify low-risk geometry more aggressively, and recover only the regions that fail local fidelity checks. It must not reject or preserve an entire model family because one wheel, lens, window, bodygroup, or control roundtrip is problematic.

The implementation belongs to the Python worker and the WPF product in `GmodAddonCompressor-master/GmodAddonCompressor/`. The legacy Python GUI under `gui/` is out of scope.

## Evidence that invalidates the current architecture

The current Maximum implementation at `8812c7a` performs a mandatory Blender ratio-1 control build and full visual validation before searching candidates. A failed control prevents every candidate from being tried. Candidate generation, validation, selection, caching, and final promotion remain family-centered even where focused recovery exists.

The published five-family result also counts removal of `.dx80.vtx` as optimization:

| Measurement | Original bytes | Final bytes | Reduction |
|---|---:|---:|---:|
| Published current Maximum | 41,624,655, including DX80 | 26,221,342, excluding DX80 | 37.01% |
| Correct comparable measurement | 34,773,943, excluding DX80 | 26,221,342, excluding DX80 | 24.59% |

On the same stored families, the historical Blender `b050` lane is 20,028,805 bytes excluding DX80, or a 42.40% reduction. That historical lane does not have equivalent visual evidence and is not accepted as the new winner, but it proves that the existing Maximum result is not a compression improvement over the ordinary topology pipeline.

Every v2 report must exclude DX80 from both the original and final denominators. DX80 removal is reported separately as an output-compatibility action and can never contribute to the claimed reduction.

## Research basis

The design follows these primary sources and implementations:

- Garland and Heckbert's QEM and attribute-aware extension: <https://www.cs.cmu.edu/afs/cs.cmu.edu/user/garland/www/quadrics/index.html>
- Hoppe's progressive meshes and selective refinement: <https://www.microsoft.com/en-us/research/publication/progressive-meshes/> and <https://hhoppe.com/proj/vdrpm/>
- Lindstrom and Turk's image-driven simplification: <https://repository.gatech.edu/entities/publication/05cee22c-43b5-4c96-9b42-965a58c926fa>
- meshoptimizer simplification, attributes, vertex locks, regularization, and update mode: <https://github.com/zeux/meshoptimizer>
- Blender Decimate behavior and its seam/material/UV delimiters: <https://docs.blender.org/manual/en/latest/modeling/modifiers/generate/decimate.html>
- CGAL bounded normal-change and polyhedral-envelope filters: <https://doc.cgal.org/latest/Surface_mesh_simplification/>
- OpenMesh QEM, Hausdorff, normal-deviation, and progressive-mesh modules: <https://www.graphics.rwth-aachen.de/media/openmesh_static/Documentations/OpenMesh-6.2-Documentation/a00004.html>
- Deformation-sensitive and pose-independent skinned-mesh simplification: <https://research.cs.wisc.edu/graphics/Gallery/DSD/dsd.pdf> and <https://gfx.cs.princeton.edu/pubs/DeCoro_2005_PSO/skeleton.pdf>
- Valve's Source model constants and VTX layout: <https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/public/studio.h> and <https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/public/optimize.h>
- StudioMDL inputs, outputs, and DX90-only `-fastbuild`: <https://developer.valvesoftware.com/wiki/StudioMDL_(Source)>

The Source v48 constraints enforced by v2 are 65,536 triangles, 65,536 vertices, 128 used bones, and at most three bone influences per compiled vertex. The optimizer may use a four-slot internal representation only while computing; export and compiled validation must enforce the three-influence Source limit.

## Considered architectures

### 1. Adaptive regional overlay using meshoptimizer — selected

Run Models normal once, create stable source regions, apply attribute-aware QEM independently behind locked boundaries, validate each region in memory, and compile the composed family once. Use a bounded regional search only where the first candidate fails.

This reuses the tested meshoptimizer v1.2 bridge already present on the experimental branch, keeps the native dependency MIT-licensed and small, and removes repeated Blender imports, full renders, and StudioMDL compiles from the candidate loop.

### 2. Custom progressive QEM or OpenMesh collapse history — not selected initially

A recorded edge-collapse history provides elegant local vertex-split recovery and exact progressive refinement. It would require a second topology engine, Source-specific attribute transfer, a larger native interface, and substantially more verification before it could safely replace the existing direct path.

This remains a future option only if the selected design cannot reach the measured compression/quality frontier.

### 3. Improve current whole-family renders and gates — rejected

Reusing reference renders, adding binary search, and parallelizing families reduce elapsed time but retain the incorrect mandatory control and family-wide rejection unit. This cannot satisfy the local fallback requirement.

## Architecture

### Pipeline overview

1. Decompile and inventory once using the established Models worker flow.
2. Run the current Models normal optimizer once with the Maximum aggressive seed settings.
3. Preserve immutable original SMD/QC sources and the normal optimized SMD/QC sources.
4. Build a stable regional graph for every render mesh source.
5. Measure visual risk and source/normal deltas per region.
6. Select a starting representation per region: normal, more aggressive QEM, lighter QEM, or original.
7. Validate candidate regions without compiling or rendering complete families.
8. Refine only failed regions with a bounded search.
9. Compose the accepted regions into one source tree.
10. Compile the family once and perform compiled structural validation.
11. Retry only the implicated source/bodygroup if compilation or compiled validation fails.
12. Package DX90 artifacts, remove DX80, and publish an atomic report and output.

### Isolation from the normal pipeline

The normal pipeline remains a callable baseline and fallback. Maximum must not fork or duplicate its decompile, QC repair, normal optimization, compile, packaging, cancellation, or WPF integration behavior. New adaptive logic operates after the normal source candidate exists and before the final compile.

Normal mode and Fidelity mode must remain byte-for-byte behaviorally unchanged when Maximum is not selected.

## Stable regional graph

### Region identity

A region is the smallest safely replaceable surface unit defined by:

1. QC graph occurrence and source SMD identity;
2. material slot;
3. topological connected component within that material;
4. deterministic local ordinal derived from canonical geometry, not Blender object order or a generated filename.

Region IDs are SHA-256 hashes of the normalized source path, QC occurrence, material, component signature, and local ordinal. The graph is regenerated independently for original and normal sources and must map one-to-one before regional replacement is allowed.

### Hard boundaries

The following boundaries are never collapsed across:

- material changes;
- UV seams or mirrored-UV discontinuities;
- hard-normal and smoothing discontinuities;
- open and non-manifold borders;
- changes in bone-influence sets;
- QC source/bodygroup occurrence boundaries;
- flex/VTA participation boundaries;
- vertices required to preserve Source triangle/material ownership.

Boundary vertices are locked or protected in the meshoptimizer request. Independent material/component results can therefore be recomposed without cracks or material migration.

### Ambiguous sources

DMX, flex meshes, malformed/non-manifold SMDs, duplicate ambiguous components, and source graphs that cannot be mapped exactly are never modified with best-effort heuristics. Their fallback is the corresponding Models normal SMD/bodygroup. Other mapped regions in the family remain eligible for Maximum.

If the Models normal unit itself cannot compile, the original unit is used. Whole-family original fallback is reserved for a QC graph that StudioMDL cannot compile after every changed local unit has been reverted.

## Visual-risk classification

Risk is a continuous feature vector, not a filename category. The vector contains:

- area-weighted mean and percentile dihedral curvature;
- density and magnitude of curvature extrema;
- projected silhouette membership over a fixed canonical direction set;
- projected size and screen-space boundary length;
- open borders, hard edges, UV seams, and material-boundary density;
- VMT transparency, alpha-test, additive, refract, and two-sided semantics;
- UV stretch and texture-coordinate discontinuity;
- bone-weight entropy and influence-gradient magnitude;
- displacement across representative source animation poses;
- component size, enclosure evidence, and visibility confidence;
- source-to-normal geometric and attribute errors.

Low-risk planar, small, enclosed, and low-screen-contribution regions receive a more aggressive target. Curved silhouettes, transparent surfaces, high-contrast material borders, and deforming regions receive a tighter error budget. Low confidence increases protection; it never authorizes more compression.

The classifier produces evidence and target budgets only. It does not decide acceptance; measured post-simplification gates do.

## Candidate generation

### Starting candidate

Maximum runs the established normal optimizer with an aggressive seed ratio of 0.35 and the existing normal repairs. The result is the first full candidate and local fallback.

For each mapped region:

- if the normal region passes with a large margin, test one more aggressive QEM target;
- if it passes near a limit, keep the normal region;
- if it fails, start from the original region and test a lighter QEM target;
- if no simplified representation passes, restore the original region.

### meshoptimizer modes

- Static regions and rigid regions with a single bone at weight 1 may test `meshopt_simplifyWithUpdate` with position, normal, and UV attributes. Non-rigid skinned regions never use the update path.
- Skinned regions default to no-update `meshopt_simplifyWithAttributes` plus `meshopt_SimplifyRegularizeLight` so original vertex attributes remain authoritative.
- Permissive collapses are forbidden across protected discontinuities.
- Target ratio is a lower-bound objective; normalized geometric error is the hard simplification stop.
- Material regions and connected components are simplified separately behind locked shared boundaries.

### Bounded adaptive search

The local search is monotonic and limited to three simplifier evaluations per failed region:

1. classified target;
2. midpoint between the last passing and failing target;
3. conservative boundary target, only if the midpoint leaves a meaningful interval.

No region can trigger dozens of complete candidates. Simplifier evaluations operate in memory and do not invoke Blender or StudioMDL. Accepted results are cached by source hash, region contract, meshoptimizer version, attributes, risk profile, and target.

## Fast regional validation

### Structural gates

These are exact gates:

- valid finite triangles and no new degenerates;
- unchanged material ownership and boundary membership;
- unchanged QC/bodygroup/source occurrence;
- UV seam, hard-normal, and protected-border survival;
- normalized skin weights with at most three positive Source-export influences;
- no new bone indices and no missing required bones;
- no Source vertex/triangle/skin/material limit violation;
- deterministic output for identical input and profile.

### Geometric and appearance gates

The regional validator compares original, normal, and candidate using:

- symmetric sampled surface distance, including p95 and maximum;
- normal angular deviation p95 and maximum;
- projected silhouette IoU loss and boundary distance in pixels over canonical views;
- UV barycentric transfer error and triangle stretch change;
- material-boundary displacement;
- skinning surface displacement over representative poses;
- triangle and vertex counts, compiled-byte estimate, and measured saving.

Sampling is deterministic and area-weighted. Distances are normalized by the region bounding-box diagonal and also converted to screen pixels using the benchmark camera contract.

Thresholds are stored in a versioned profile. The development partition may tune the profile once; the holdout partition cannot change it. A profile is production-eligible only after its frozen hash passes every holdout structural gate and produces no accepted visual regression in the comparison panels. Failure to freeze a passing profile blocks release rather than silently weakening a gate.

### Render escalation

Blender rendering is escalated only for:

- transparent, alpha-tested, refractive, or two-sided material regions;
- regions whose geometric or screen-space metric is within 15% of a limit;
- regions where correspondence or visibility confidence is low;
- benchmark evidence and an explicit diagnostic/audit mode.

Reference renders are generated once per original region/state and cached. Candidate renders use the same camera, pose, bodygroup state, materials, lighting, crop bounds, and resolution. A failed targeted render affects only that region.

Full-family multi-state rendering is not part of the default candidate loop.

## Composition and compile fallback

Accepted region outputs are composed into a fresh source tree. The original and normal trees remain immutable.

The first composition is compiled once per family. After compilation, v2 validates:

- StudioMDL success and parse-completion output;
- `.mdl`, `.vvd`, and `.dx90.vtx` presence;
- matching Source checksums and valid sidecar membership;
- MDL/VVD/VTX vertex and index bounds;
- expected bodyparts, bodygroups, skins, materials, bones, attachments, animations, and collision artifacts;
- DX90 load/report compatibility;
- absence of `.dx80.vtx` from final output and final metrics.

If compilation identifies a changed source or QC occurrence, only that unit is replaced with its normal version and the family is compiled once more. If the compiler does not identify the source, changed sources are isolated with a bounded binary grouping test; the first failing group is reverted to normal. The isolation budget is capped at `ceil(log2(changed_source_count)) + 1` compiles.

If all changed units have been reverted and the normal family still fails, the original family is restored and the failure is reported. This is a genuine normal/control failure, not a reason to skip candidate evaluation before it occurs.

## Dependency and output closure

The framework root at
`C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\2912816023\lvs_framework`
is read-only and may be used only to resolve materials, textures, and authored references during analysis and validation.

The adaptive output may not create a new QC, model, material, texture, include-model, or animation reference outside the original addon's reference closure. No absolute path, work path, framework filesystem path, or generated temporary path may enter an SMD, QC, MDL, cache record, or published addon.

The final closure audit runs with the framework root removed from the resolver. A candidate that requires the framework to resolve a reference not already owned by the input addon fails output promotion. The optimizer must still run when the user chooses not to optimize the framework.

## Benchmark protocol

### Corpus

The frozen eight-family corpus is:

Development partition:

1. VW Beetle
2. Caterham 620R
3. Ferrari 365 full rig
4. Ford Fairlane
5. VW Touareg

Holdout partition:

6. Dodge Charger
7. Toyota Supra
8. Pontiac TransAm wheel

The holdout partition targets dense bodygroups, deformation, curved vehicle shells, and a wheel-specific silhouette. Family manifests include relative paths, sizes, SHA-256 hashes, exact Source sidecars, source files, and required material inputs.

### Mandatory lanes

Each lane uses an isolated copy, clean work/cache/output directories, the same tools, and identical input manifests:

1. Models normal current WPF Safe preset (`ratio=0.75`);
2. current Maximum at exact commit `8812c7a`, default production profile and automatic jobs;
3. Maximum Adaptive v2 with the frozen profile.

The historical `b050` and Fidelity records remain diagnostic only. They do not replace a fresh Models normal current run.

The two ratios serve different purposes: `0.75` measures the current user-facing Normal Safe preset, while the new Maximum may internally start from a more aggressive `0.35` normal candidate and recover only the regions that fail.

### Measurements

For every family and aggregate, record:

- original and final `models` bytes with DX80 excluded from both;
- DX80 bytes removed, in a separate non-savings field;
- wall-clock time, process-tree CPU time, average CPU, and peak working set;
- decompile, normal seed, regional analysis, simplification, render, compile, and packaging times;
- number of full builds, regional evaluations, renders, and StudioMDL compiles;
- optimized, normal-fallback, original-fallback, failed, and ambiguous region counts;
- optimized, preserved, and failed source/family counts;
- triangles and vertices before/after by region, source, family, and compiled LOD;
- surface, silhouette, normal, UV, material-boundary, and skinning metrics;
- compiled artifact inventory, checksum integrity, DX90 proof, and DX80 absence;
- reference/candidate/diff images for every rounded focus region.

### Visual evidence

Panels use identical 1024-pixel cameras and include textured, clay, normal-deviation, silhouette-difference, and UV-checker views. Rounded focus crops must include wheels/tires, steering wheels, glass/lenses, sights, pipes/exhausts, headlights, arches, cylinders, and curved exterior panels where present.

No aggregate reduction claim is allowed unless every promoted region has reproducible source/candidate evidence and every final MDL passes structural validation. Rejected approaches remain recorded with their measurements.

## Promotion criteria

Maximum Adaptive v2 can replace the current Maximum only if all conditions hold on fresh runs:

1. aggregate comparable reduction exceeds both mandatory baselines;
2. no holdout family is worse than Models normal in both bytes and fidelity;
3. every promoted region passes its frozen structural and fidelity profile;
4. every family compiles and passes DX90 integrity checks;
5. final output contains zero `.dx80.vtx` files;
6. no family is preserved solely because a local region failed;
7. the number of full renders and full compiles is materially lower than current Maximum;
8. output promotion passes the dependency/reference closure audit;
9. Normal and Pipeline behavior remains unchanged outside Maximum selection;
10. the official WPF release build succeeds and embeds the tested worker and native bridge.

The 50% reduction mentioned in the request is a reference, not a pass/fail threshold. The report must prefer a smaller verified reduction over an inflated or visibly damaged result.

## Worker integration

The public CLI remains `--optimizer-mode maximum` so WPF and Pipeline compatibility do not require a second user-visible mode. Internally the profile/report schema identifies `maximum-adaptive-v2`.

New worker modules have narrow responsibilities:

- source/normal region graph and correspondence;
- continuous visual-risk features;
- meshoptimizer regional requests and cache;
- regional geometric/attribute validation;
- targeted render escalation;
- source composition and compile isolation;
- benchmark/report schema.

The existing experimental Maximum package is reused only where a module is independently useful and testable, such as SMD parsing, meshoptimizer FFI, compiled-size scanning, and atomic reporting. The 6,000-line orchestrator is not ported wholesale into the current product branch.

## WPF and Pipeline integration

WPF exposes Maximum alongside the existing Models modes and persists the selected mode and Maximum job policy. The runner passes `--optimizer-mode maximum` and parses structured progress for:

- normal seed;
- regional inventory;
- adaptive simplification;
- targeted validation;
- compile/fallback;
- packaging;
- final regional and size summary.

Pipeline continues to invoke the same Models runner followed by Compress. Cancellation, logging, size reports, tool extraction under `%LOCALAPPDATA%`, and output paths follow existing application behavior.

The feature branch cannot be merged wholesale because the current product branch contains later unrelated WPF/VTF work. Models Maximum files and integration changes must be ported incrementally with file-level review.

## Testing strategy

Implementation follows test-first development.

Unit tests cover deterministic region IDs, connected/material partitioning, boundary locks, risk features, Source three-weight export, candidate search bounds, local fallback, metric calculations, DX80-neutral accounting, dependency closure, and report serialization.

Integration tests cover original/normal region correspondence, regional composition, a candidate with one rejected wheel and accepted car body, compile-source isolation, worker CLI routing, WPF arguments/progress parsing, Pipeline propagation, package contents, and cancellation.

Native bridge tests cover ABI/version, attribute buffers, material subsets, boundary flags, skinned regularization, update/no-update modes, deterministic concurrent calls, invalid Source counts, and owned-output lifetime.

Benchmark tests verify immutable corpus hashes, lane isolation, tool hashes, cold-cache enforcement, identical inputs, DX80 exclusion, image manifests, and recomputable aggregate values.

## Release

After the selected implementation passes its unit, integration, benchmark, visual, and compiled integrity gates:

1. rebuild the packaged Python worker if any worker/backend file changed;
2. run `./build_release_wpf.ps1` from the repository root;
3. verify the embedded worker/native bridge hashes match the benchmarked artifacts;
4. launch the published WPF executable and exercise Models Maximum and Pipeline;
5. report the exact executable path:
   `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`.

No release-success or quality-improvement claim is made until those fresh verification steps and the three-lane benchmark complete.
