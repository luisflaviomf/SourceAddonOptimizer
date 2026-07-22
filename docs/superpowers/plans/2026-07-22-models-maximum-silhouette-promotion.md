# Models Maximum v2 Silhouette Kernel Promotion Implementation Plan

> Execute directly on `feature/models-maximum-adaptive-v2`. Do not use subagents, optimize `_nearest`, merge to main, publish an official release, or overwrite the previously approved package.

**Goal:** Make the proven exact raw-mask silhouette kernel the safe production backend for Models Maximum v2, with a strong ABI, content-verified packaging, exact process-sticky KD fallback, WPF integration, and a portable candidate build.

**Architecture:** The existing shared `meshopt_bridge.dll` keeps every mesh export and adds a versioned silhouette ABI-information export. Python owns package/native validation and an explicit Maximum-only controller; `metrics.py` treats the native call as an all-or-nothing acceleration of the existing exact result. The WPF installs its embedded worker ZIP into an immutable content-addressed directory using verified staging and atomic rename, then launches the worker only from that activated absolute path.

**Technology:** C++17/MSVC/CMake, Python 3.11/ctypes/pytest/Pillow/SciPy, C# .NET 6 WPF, PowerShell/PyInstaller, SHA-256 and PE/COFF validation.

---

## Task 1: Freeze the promoted native ABI contract

**Files:**
- Modify: `maximum_optimizer/native/meshopt_bridge.cpp`
- Modify: `maximum_optimizer/native/CMakeLists.txt`
- Modify: `maximum_optimizer/silhouette_native.py`
- Modify: `tests/maximum_optimizer/test_silhouette_native.py`

1. Add failing Python tests for the ABI-info ctypes layout, API/build/capability/view/calling-convention checks, wrong struct sizes, old/missing export, wrong PE architecture, wrong DLL size/hash, and absolute-path-only loading.
2. Run `python -m pytest tests/maximum_optimizer/test_silhouette_native.py -q` and retain the expected failures.
3. Add the append-only `maximum_silhouette_get_abi_info_v1` C export and fixed-width ABI struct. Preserve all old exports and the exact raw-mask kernel math.
4. Make Release use the static MSVC runtime and keep the x64/reproducible/security flags.
5. Implement manifest-driven validation in `silhouette_native.py`: resolved absolute package path, SHA-256, exact size, AMD64 PE machine, Python x64, ABI semver/build/capabilities/layouts, then metric binding.
6. Build with `maximum_optimizer/native/build.ps1`, run the focused tests, inspect exports/imports with installed Visual Studio tools, and verify only Windows system DLL dependencies.
7. Commit: `feat(maximum): add strong silhouette native ABI`.

## Task 2: Implement the process-sticky exact fallback controller

**Files:**
- Create: `maximum_optimizer/silhouette_backend.py`
- Modify: `maximum_optimizer/metrics.py`
- Modify: `tests/maximum_optimizer/test_metrics.py`
- Create: `tests/maximum_optimizer/test_silhouette_backend.py`

1. Add failing tests for states `uninitialized/native/legacy`, no retry after failure, ignored `MAXIMUM_SILHOUETTE_EXPERIMENT_DLL`, native failure after raw reference preparation, full-output discard, exact KD recomputation, later-region legacy preparation, and timing/call/fallback diagnostics.
2. Run the focused tests and retain the expected failures.
3. Implement an explicit controller configured with a validated absolute package root and injectable test factory. Production accepts no environment/PATH/current-directory discovery.
4. Replace experiment globals in `metrics.py` with the controller. Store sufficient raw originals so failure after native preparation can rebuild every KD boundary and recompute the complete silhouette result through `_silhouette_metrics_prepared`.
5. Validate all native outputs before acceptance. On any exception/error/contract violation, atomically disable native for the process, discard the whole native result, and perform one exact legacy computation without retrying native.
6. Run focused tests plus `python -m pytest tests/maximum_optimizer/test_metrics.py tests/maximum_optimizer/test_silhouette_backend.py -q`.
7. Commit: `feat(maximum): add exact native silhouette fallback`.

## Task 3: Restrict activation to Maximum and expose technical logs

**Files:**
- Modify: `build_optimized_addon.py`
- Modify: `worker/worker_main.py` only if routing needs a package-root handoff
- Modify: `tests/maximum_optimizer/test_worker_cli.py`
- Modify: `tests/maximum_optimizer/test_pipeline_reporting.py`

1. Add failing tests proving Maximum initializes the controller from its own absolute packaged root while Normal/Fidelity never initialize/import/load it, even with a fake DLL beside the executable and the old experiment environment variable set.
2. Add tests for one selection line and one final summary containing backend, API/build, calls, preparation/native/marshal times, fallback flag/stage/code/reason.
3. Configure the controller only inside the Maximum branch and always print its final summary there. Treat invalid/unavailable native package state as a logged exact-KD fallback, never as whole-job failure.
4. Run focused worker/pipeline tests and commit: `feat(maximum): activate silhouette backend in maximum only`.

## Task 4: Generate a complete signed-by-hash worker package manifest

**Files:**
- Create: `pyinstaller/New-SourceAddonOptimizerManifest.ps1`
- Modify: `pyinstaller/package_wpf_tools.ps1`
- Modify: `pyinstaller/worker.spec`
- Create: `tests/maximum_optimizer/test_tool_package_manifest.py`

1. Add failing tests that build miniature package trees and reject missing files, unsorted/duplicate paths, wrong hashes/sizes, bad DLL arch/API/build declarations, incomplete ZIPs, and path traversal.
2. Implement deterministic manifest generation at `_internal/maximum_optimizer/native/tool-package-manifest.json`, excluding only the manifest itself and declaring every other packaged file.
3. Include tool/schema version, silhouette API/build/x64, DLL relative path/hash/size, and minimum worker/WPF contract versions. Bump the package contract to `0.1.18`.
4. Make the packaging script rebuild the worker/native artifact when source inputs are newer or when manifest/ABI validation fails; validate the staged ZIP before replacing the embedded ZIP.
5. Run manifest tests and one packaging dry run. Commit: `build(maximum): manifest native worker package`.

## Task 5: Install the embedded tools atomically in WPF

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Tools/SourceAddonOptimizerPackageManifest.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Tools/SourceAddonOptimizerPackageInstaller.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Tools/ToolExtractionSystem.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Tools/ToolPaths.cs`
- Modify: `tests/wpf_optimizer_contract/GmodAddonOptimizer.ContractTests.csproj`
- Modify: `tests/wpf_optimizer_contract/Program.cs`

1. Add failing contract tests for the `0.1.18/<zip-sha256>` root, complete hash/size/AMD64 verification, tampered installed DLL re-extraction, partial directory rejection, interrupted extraction cleanup, two concurrent callers, old immutable root retained while loaded, and fake DLL beside executable ignored.
2. Implement a SourceAddonOptimizer-specific installer using an exclusive per-tool lock, same-parent unique partial directory, safe ZIP paths, complete manifest verification, atomic `Directory.Move`, and best-effort partial cleanup.
3. Activate `ToolPaths` only after a complete final root validates. Never delete or overwrite an older content-addressed root; on failure preserve the last activated root and throw an actionable error.
4. Keep the generic extractor for unrelated tools unchanged except for shared safe helpers if necessary.
5. Run `dotnet run --project tests/wpf_optimizer_contract/GmodAddonOptimizer.ContractTests.csproj -c Release` and commit: `feat(wpf): install optimizer tools atomically`.

## Task 6: Verify exactness and failure behavior before packaging

**Files:**
- Modify: `scripts/benchmark_maximum_silhouette_kernel.py`
- Create: `scripts/verify_maximum_silhouette_promotion.py`
- Create evidence beneath a new timestamped directory on `D:`; do not alter prior evidence.

1. Run all Python tests, WPF contract tests, and native ABI/dependency inspection.
2. Run the 10,000-mask oracle and require exact intersections/unions/distance counts/p95/IoU/boundary values.
3. Run frozen Pontiac, Toyota, Dodge and simple fixtures through legacy and native lanes; require identical decisions, selected regional SMDs, QC, and compiled MDL tree hashes.
4. Inject failures before load, after original preparation, during native call and during output validation; require full exact KD equality, one fallback, no retry on later regions, and a successful overall Maximum job.
5. Commit harness changes: `test(maximum): verify promoted silhouette equivalence`.

## Task 7: Build a clean candidate and audit portability

**Files:**
- Modify: `build_release_wpf.ps1`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/GmodAddonCompressor.csproj` or resources only if required by the existing embed flow
- Create evidence/hash manifests beneath the new `D:` candidate directory.

1. Add an optional candidate output parameter to the official release script without changing its default official path. In candidate mode, publish to a new versioned directory, copy the embedded tools ZIP beside it for audit only, ZIP the complete publish directory, and emit SHA-256/size manifests.
2. Force a clean native build, clean PyInstaller worker build, fresh embedded ZIP, and clean WPF single-file publish.
3. Copy the full publish to another clean directory, add fake/old DLLs beside the EXE, set the retired experiment environment variable, remove developer-tool directories from PATH for the test process, and launch it. Verify extraction and worker DLL resolution remain beneath the validated content-addressed `%LOCALAPPDATA%` root.
4. Verify missing/tampered extracted files are repaired from the embedded ZIP; an adjacent audit ZIP is never read at runtime.
5. Commit: `build(wpf): produce portable maximum candidate`.

## Task 8: Run balanced packaged/WPF benchmarks and produce the verdict

**Files:**
- Create: `docs/superpowers/specs/2026-07-22-models-maximum-silhouette-promotion-result.md`
- Store raw commands, logs, JSON/CSV, hashes, images and environment metadata in the new evidence directory.

1. Record Windows/Python/worker/WPF/native builds, power plan, CPU/load/affinity/warm state and temperature/frequency where available.
2. Run packaged-worker legacy/native lanes in interleaved balanced order: at least three cold and five warm per lane. Measure preparation, silhouette, adaptive, subprocess/compile/full wall and peak RSS; observe `_nearest` without changing it.
3. Launch one real copied candidate WPF Models Maximum job and prove its log selected the packaged native API/build and completed without worktree/dev-tool dependency.
4. Compare native and exact-fallback output hashes and visual rounded-region panels. Report speed only from same-session paired/interleaved samples.
5. Run `python -m pytest -q`, the WPF contract tests, clean candidate build checks, `git diff --check`, and `git status --short`.
6. Write the objective result report with reduction/quality/time/limitations, candidate EXE/publish/ZIP/evidence paths, and every SHA-256.
7. Commit: `docs(maximum): report controlled silhouette promotion`.

## Completion gate

The promotion candidate is acceptable only if native and KD outputs are exact, any native fault falls back once and leaves the job successful, Normal/Fidelity never initialize native, package tampering cannot redirect loading, the copied WPF candidate runs solely from embedded/extracted tools, dependency inspection is clean, warm silhouette speedup is at least 3x, adaptive improvement is at least 25%, full wall time is clearly lower in balanced A/B, memory is stable, and no prior package or main branch was replaced.
