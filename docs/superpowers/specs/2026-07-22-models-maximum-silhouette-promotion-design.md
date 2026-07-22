# Models Maximum v2 Silhouette Kernel Promotion Design

**Date:** 2026-07-22  
**Status:** approved for controlled implementation  
**Scope:** promote only the exact raw-mask silhouette kernel; `_nearest` is excluded

## Benchmark reconciliation

The previous 24.931-second warm median and the later 8.832-second warm
baseline measure the same internal stage and frozen Pontiac wheel family, but
they were collected in different sessions under materially different host
execution conditions. They must not be divided to claim a speedup. Only
balanced A/B lanes collected in the same session are valid performance
comparisons.

| Metric | Measurement starts | Measurement ends | Included work | Model | Condition |
|---|---|---|---|---|---|
| Previous warm adaptive, 24.931 s | `_run_stage` immediately before `optimize_all` | return from `optimize_all` | risk, 19 cache reads, nine exact candidate validations, composition decisions | frozen Pontiac Trans Am wheel | fresh CPython process, populated candidate cache, compile/render stubs, no profiler/wrappers |
| Kernel experiment warm baseline, 8.832 s | same `_run_stage` boundary | same return boundary | same risk/cache/nine-validation work | same frozen Pontiac family, same regions and hashes | fresh CPython process, same populated cache, balanced A/B; two low-overhead silhouette wrappers |
| Current full baseline, 23.490 s | immediately before launching `build_optimized_addon.py` | child process exit | process startup, decompile, Normal seed, inventory, cold 19-evaluation Maximum, one targeted Blender render, one StudioMDL compile, packaging | same Pontiac family copied to a full-run source root | source Python worker command, empty work/cache root, real external tools |

The two warm adaptive runs both used 19 cache hits, zero simplifier calls and
nine validations. The stage timer implementation, profile hash, canonical
views, samples, model regions and approved baseline DLL hash were unchanged.
The median adaptive CPU time itself changed from 18.781 s to 8.602 s, while a
same-session real cold adaptive baseline was 9.354 s. This excludes a timer-only
explanation and points to host execution drift. Historical telemetry is not
sufficient to attribute it to one power, thermal, scheduler or background-load
cause. Future evidence records power plan, CPU frequency/temperature when
available, load, affinity, warm-up state, Windows version, Python build and
worker build, but this metadata never replaces interleaved A/B.

## Selected architecture

The production `meshopt_bridge.dll` retains all meshoptimizer exports and adds
the already proven raw-mask kernel plus a separate strong silhouette ABI v1.
The kernel remains mathematically unchanged. A compact ABI-information export
reports semantic version 1.0.0, an immutable build ID, x64 architecture,
capabilities, canonical eight-view count, calling convention, pointer and
`size_t` widths, and the size/alignment of every public silhouette struct.

The Python wrapper is split into three responsibilities:

1. `silhouette_native.py` owns fixed-width ctypes layouts, absolute-path DLL
   loading, manifest/hash/PE/ABI validation and one native call.
2. `metrics.py` owns exact native-versus-KD selection, buffers, timing and
   all-or-nothing fallback for a regional measurement.
3. `build_optimized_addon.py` enables the production controller only for
   `--optimizer-mode maximum`, supplies the package runtime root and prints the
   final technical summary. Normal and Fidelity never initialize the backend.

`MAXIMUM_SILHOUETTE_EXPERIMENT_DLL` is ignored and removed from production
selection. No current-directory, executable-directory, PATH or generic-temp
search occurs. The only production path is the absolute resolved path beneath
the validated PyInstaller runtime package:

`<validated tool root>/_internal/maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll`

In source/test execution, the equivalent absolute repository path is used only
through explicit controller configuration. Tests may inject a kernel factory;
production does not accept an arbitrary DLL environment override.

## Native compatibility contract

The ABI information includes:

- API version 1.0.0;
- build ID `maximum-silhouette-raw-v1-20260722`;
- architecture `x64`;
- capability bit for exact raw-mask batch metrics;
- canonical view count 8 and binary `L` mask format identifier;
- Windows x64 unified calling convention identifier;
- widths of pointers, `size_t`, `uint32` and `uint64`;
- sizes and alignments of input, output and ABI-info structs.

The wrapper checks its local ctypes facts against every native value before it
binds the metric export. It also verifies Python is 64-bit, the DLL PE machine
is AMD64, the package manifest build/API values match the native values, and
the SHA-256/size match the manifest. An old DLL that exposes only the metric
symbol is incompatible. The validated experimental DLL is also incompatible
because it lacks this version contract.

## Manifest and packaging

`package_wpf_tools.ps1` generates
`_internal/maximum_optimizer/native/tool-package-manifest.json` after building
the worker and before creating the embedded ZIP. It contains:

- tool version and package schema;
- silhouette API version/build ID/architecture;
- DLL relative path, SHA-256 and exact byte size;
- minimum compatible worker and WPF contract versions;
- a sorted manifest of SHA-256 and size for every packaged file except the
  manifest itself.

The WPF tool contract increments from 0.1.17 to 0.1.18. The embedded ZIP is the
authoritative source. The candidate publish also receives an audit-only copy
named `SourceAddonOptimizer.win-x64.zip`; neither WPF nor worker resolves files
from that adjacent copy.

PyInstaller continues to place the shared DLL inside `_internal`. The native
Release build statically links the MSVC runtime (`/MT`) so a clean supported
Windows installation does not rely on a developer-installed VC runtime.
Dependency inspection must show only Windows system DLL imports.

## Atomic extraction and integrity

The embedded ZIP SHA-256 selects an immutable content-addressed root:

`%LOCALAPPDATA%/GmodAddonOptimizer/tools/SourceAddonOptimizer/0.1.18/<zip-sha256>/`

The WPF takes the existing per-tool exclusive lock, reads and validates the
embedded manifest, then extracts to a sibling
`.<hash>.<pid>.<guid>.partial` directory. It verifies every declared file,
including native DLL hash, size and PE architecture, and atomically renames the
complete directory to the content-addressed final path. Only after the rename
does `ToolPaths` activate that root. Interrupted partial directories are never
selected and are cleaned best-effort under the lock.

Two WPF instances serialize extraction. If another instance completes first,
the waiting instance validates and reuses the final root. A previous loaded
DLL stays in its immutable old root; no active folder is overwritten or
deleted. Package changes naturally choose a new hash directory. Permission,
space, lock and rename failures leave the prior version untouched and produce
an actionable error rather than exposing a partial install.

On every tool request, a missing or altered final file invalidates the root and
causes re-extraction from the embedded package. The worker repeats native
manifest/hash/PE/ABI validation immediately before loading, closing the
post-extraction tamper window. A malformed embedded core package is rejected;
a runtime native failure degrades only Maximum silhouette validation through
the exact fallback.

## Failure and fallback policy

The controller starts in `uninitialized`, becomes `native` only after all
checks pass, and becomes permanently `legacy` after any load, compatibility,
input, native-return or result-contract error. It never retries a failed DLL
in the same worker process.

If failure happens before native preparation, the existing KD reference is
built normally. If it happens after raw original masks or candidate masks were
prepared, every partial native output is discarded; the complete silhouette
comparison is recomputed from the original/candidate regions through the
existing exact KD implementation. No native intersection, distance or p95 is
combined with legacy data. Later regions use legacy preparation directly.

The fallback records function/stage, error code, API/build identity when known
and reason. C++ exceptions remain contained by the `noexcept` boundary and map
to an error return. Buffers are Python-owned or thread-local and are not reused
as results after failure.

## Logs and WPF flow

The existing WPF Models flow already passes `--optimizer-mode maximum` and
streams worker stdout. No new control is added. The final flow is:

1. WPF validates/extracts the embedded tools package atomically.
2. Models/Maximum launches the worker from the activated absolute tool root.
3. The worker enables and validates the silhouette backend.
4. Maximum runs with native metrics or exact KD fallback.
5. The worker emits one selection line and one final summary line containing
   backend, API/build ID, calls, preparation/native/marshaling milliseconds,
   fallback state and reason.
6. The existing WPF log displays those lines and final files are packaged.

Normal and Fidelity do not import/initialize/load the silhouette backend and
emit no silhouette-kernel selection line.

## Validation strategy

TDD adds coverage before each production change:

- exact ABI/version/build/layout checks and rejection of missing/old/wrong ABI;
- missing, wrong hash, wrong size and non-AMD64 DLLs;
- fake DLL beside WPF/worker and legacy experiment environment variable ignored;
- invalid buffers, native error after preparation, one-time disable and exact
  full KD recomputation;
- repeated optimization after fallback and no partial staging corruption;
- Normal/Fidelity no initialization, Maximum automatic initialization;
- manifest completeness, interrupted partial extraction, concurrent extraction
  and immutable update while an earlier directory exists;
- clean publish copied elsewhere, without worktree/PATH/developer tools;
- existing 82 tests, all promoted kernel tests, 10,000-mask oracle and frozen
  Pontiac/Toyota/Dodge/simple equivalence;
- real packaged worker A/B: at least three cold and five warm per lane in
  balanced order, plus one WPF-launched Maximum run;
- preparation, silhouette, `_nearest` observation only, adaptive, subprocess,
  compile/full wall, RSS and environment metadata.

Promotion remains gated on bitwise/canonical/compiled equality, at least 3x
warm silhouette speedup, at least 25% adaptive improvement, clearly lower full
time, stable memory, multiple model types, functional fallback, WPF use and a
publish independent from the worktree.

## Deliverables and boundaries

The final deliverables are a clean WPF publish directory, embedded tools ZIP,
audit-only adjacent tools ZIP, a candidate ZIP containing the entire publish
directory, a hash manifest, integrated benchmark evidence and a report. The
candidate does not replace the approved release. The work remains on
`feature/models-maximum-adaptive-v2`; there is no merge, official publication
or `_nearest` optimization.
