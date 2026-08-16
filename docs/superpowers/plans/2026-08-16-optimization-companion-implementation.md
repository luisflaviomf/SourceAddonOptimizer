# Optimization Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the optional Source Addon Optimizer Companion: bounded in-game profiling, explicit profile-assisted Models Maximum v2, structural and visual validation in Garry's Mod, safe WPF installation/import/reporting, and a gated commercial package.

**Architecture:** Four independently bounded units communicate only through versioned file bundles: the Garry's Mod addon records evidence and validates declarative plans; the WPF owns installation, explicit opt-in, bundle import, plan generation, and result presentation; the Python worker maps validated runtime evidence onto the current regional Maximum v2 risk system; shared schemas, fixtures, and security limits keep the three implementations aligned. No-profile execution remains the exact current path, and optimized output never contains or depends on Companion code.

**Tech Stack:** Garry's Mod Lua, C#/.NET 6 WPF, Python 3.11/pytest, JSON/SHA-256, PowerShell, existing PyInstaller worker/release pipeline, Garry's Mod `util.JSONToTable`, `file.*`, `net`, `DModelPanel`, and `render.Capture` APIs.

## Global Constraints

- Work in `C:\Users\luisf\Music\teste\.worktrees\main-models-maximum-v2-integration`; preserve unrelated work and do not modify the legacy `gui/` frontend.
- Execute this plan inline without subagents, per the user's standing instruction.
- Keep the WPF as the product UI and the Python worker as the heavy optimizer backend.
- The Companion is optional. Detection, installation, or import must never enable profile use automatically.
- Only Models Maximum v2 may receive `--maximum-runtime-profile`; Models normal, Fidelity, Compress, Magick/Magick+, maps, audio, and Lua remain unchanged.
- Profile absence is neutral. Evidence may only increase regional protection and may never delete content, lower risk, add a family gate, add full-family candidates, or add more than the existing one lighter regional recovery.
- Require and validate DX90. Exclude DX80 from output comparisons and all size claims.
- Keep every exchange structure closed and bounded. Reject unknown fields, unsafe paths/operations, oversized/deep JSON, hash mismatches, unsupported contracts, and incomplete runs before application.
- Never use `RunString`, `CompileString`, `os.execute`, arbitrary console commands, URLs, downloaded native modules, telemetry, cloud services, DRM, or automatic SFTP.
- Treat `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\2912816023\lvs_framework` as optional read-only reference material only; optimized output must never depend on it.
- Run focused tests after each red/green change and commit each task separately. Do not publish or create the commercial archive until every release gate in Tasks 15-16 passes.
- Use the approved design as the authority: `docs/superpowers/specs/2026-08-16-optimization-companion-design.md`.

---

## Planned File Structure

```text
companion/
|-- contracts/
|   |-- contract-limits-v1.json
|   |-- sao-runtime-profile-v1.schema.json
|   |-- sao-validation-plan-v1.schema.json
|   |-- sao-structural-result-v1.schema.json
|   `-- sao-visual-result-v1.schema.json
`-- sourceaddonoptimizer_companion/
    |-- addon.json
    |-- lua/autorun/sourceaddonoptimizer_loader.lua
    `-- lua/sourceaddonoptimizer/
        |-- shared/{constants,contract,validation}.lua
        |-- server/{permissions,storage,profiler,error_collector,structural_validator,result_writer}.lua
        `-- client/{dashboard,profiler,model_gallery,asset_validator,screenshot_capture,result_writer}.lua

maximum_optimizer/
|-- companion_contract.py
|-- runtime_profile.py
`-- qc_runtime.py

GmodAddonCompressor-master/GmodAddonCompressor/
|-- DataContexts/CompanionContext.cs
|-- Models/CompanionModels.cs
|-- Systems/Companion/
|   |-- CompanionContract.cs
|   |-- CompanionPathPolicy.cs
|   |-- CompanionBundleReader.cs
|   |-- CompanionPackageInstaller.cs
|   |-- CompanionProfileService.cs
|   |-- CompanionValidationPlanService.cs
|   `-- CompanionResultService.cs
|-- Resources/SourceAddonOptimizerCompanion.zip
`-- MainWindow.Companion.cs

tests/
|-- companion_contract/{fixtures,test_contract_fixtures.py,test_bundle_security.py}
|-- companion_addon/{test_lua_package.py,test_lua_security.py}
`-- wpf_companion_contract/{GmodAddonOptimizer.CompanionContractTests.csproj,Program.cs}
```

The exact Lua modules may be split further only when a file exceeds a single responsibility; the one-autorun rule and public contract names must not change.

---

## Task 1: Freeze the bundle protocol, limits, and hostile fixtures

**Files:**
- Create: `companion/contracts/contract-limits-v1.json`
- Create: `companion/contracts/sao-runtime-profile-v1.schema.json`
- Create: `companion/contracts/sao-validation-plan-v1.schema.json`
- Create: `companion/contracts/sao-structural-result-v1.schema.json`
- Create: `companion/contracts/sao-visual-result-v1.schema.json`
- Create: `maximum_optimizer/companion_contract.py`
- Create: `tests/companion_contract/fixtures/valid/*`
- Create: `tests/companion_contract/fixtures/invalid/*`
- Create: `tests/companion_contract/test_contract_fixtures.py`
- Create: `tests/companion_contract/test_bundle_security.py`

- [x] Write the four closed JSON schemas and one limits file. Fix contract version at major `1`, minor `0`; cap a chunk at `2,000,000` bytes, all profile chunks at `20,000,000` bytes, nesting depth at `12`, total keys at `100,000`, models at `4,096`, materials at `16,384`, plan models at `250`, screenshots at `1,000`, and ordinary views at `4`.
- [x] Freeze the bundle naming protocol: `run-index.json`, `chunk-0001.json` through the declared count, then `complete.ready.json`. Each chunk repeats `schema`, `{major, minor}`, UUID `run_id`, RFC 3339 UTC `created_utc`, bounded `producer` and `environment`, `{sequence,total}`, and `payload`. The index records filename, byte size, and lowercase SHA-256 for every chunk. `complete.ready.json` records `run_id` and the SHA-256 of `run-index.json`. The `.json` suffix is mandatory because Garry's Mod's DATA writer refuses `.ready` files.
- [x] Add a valid fixture for every schema plus invalid fixtures for partial completion, missing/reordered/duplicate chunks, wrong size/hash, unsupported major/minor, unknown/missing fields, future timestamp, invalid number (`NaN`/infinity encoded as rejected tokens), excessive depth/keys, duplicate IDs, mixed-case/Unicode-confusable paths, traversal, drive/UNC paths, wrong extensions, unknown test kinds, and a duplicate run ID.
- [x] Add failing tests that call `read_bundle(path, expected_schema=...)` and assert that every invalid fixture fails closed before yielding payload data. Verify a newer minor exposes envelope metadata only through `inspect_bundle()` and cannot be returned by `read_bundle()`.
- [x] Run `python -m pytest tests\companion_contract -q`; expected result: collection/import failure because `maximum_optimizer.companion_contract` does not exist.
- [x] Implement a standard-library-only reader/writer with byte limits before JSON parsing, duplicate-key rejection through `object_pairs_hook`, finite-number checks, closed-field helpers, SHA-256 checks, UUID/time validation, atomic `.partial` writes plus `os.replace`, and virtual-path normalization.
- [x] Make `normalize_virtual_path()` allow only `models/`, `materials/`, `sound/`, `lua/`, or `maps/` and an explicit per-test extension set. Reject nulls, `..`, colon/drive prefixes, UNC/double-leading separators, ambiguous separators, components over 128 characters, total paths over 512 characters, and non-ASCII path code points for v1.
- [x] Re-run `python -m pytest tests\companion_contract -q`; expected result: all contract and hostile-fixture tests pass.
- [x] Commit: `feat(companion): freeze secure exchange contracts`.

Representative Python API to lock in tests:

```python
bundle = read_bundle(root, expected_schema="sao-runtime-profile-v1")
assert bundle.run_id == UUID("...")
assert bundle.payloads[0]["models"][0]["path"] == "models/lvs/example.mdl"
```

## Task 2: Add the minimal Lua package, safe storage, and authorization boundary

**Files:**
- Create: `companion/sourceaddonoptimizer_companion/addon.json`
- Create: `companion/sourceaddonoptimizer_companion/lua/autorun/sourceaddonoptimizer_loader.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/shared/constants.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/shared/contract.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/shared/validation.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/server/permissions.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/server/storage.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/server/result_writer.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/client/result_writer.lua`
- Create: `scripts/test_companion_lua.ps1`
- Create: `tests/companion_addon/test_lua_package.py`
- Create: `tests/companion_addon/test_lua_security.py`

- [x] Add failing package tests that require exactly one `lua/autorun` file, lowercase Linux-safe paths, unique `SAO.` namespace use, explicit `AddCSLuaFile`/`include` ordering, and no unexpected files outside the approved addon tree.
- [x] Add failing security tests that scan all Companion Lua for forbidden APIs and literals: `RunString`, `CompileString`, `os.execute`, `game.ConsoleCommand`, `LocalPlayer():ConCommand`, dynamic `require`, `http.`, URL schemes, or arbitrary caller-supplied filenames. Require every `net.Receive` to use a declared fixed message name and permission helper.
- [x] Run `python -m pytest tests\companion_addon -q`; expected result: failure because the addon tree is absent.
- [x] Implement the loader and shared constants. Use `sourceaddonoptimizer` as the only data root and fixed folders `profiles`, `validation_inbox`, `validation_results`, and `screenshots`; never concatenate an unvalidated client path into `file.*` calls.
- [x] Implement server permission checks for server console, single-player host, or current `IsSuperAdmin()` on every privileged action. Define session IDs, 15-minute expiry, per-player rate limits, fixed net read order, and maximum string lengths; malformed traffic is logged to Companion state and ignored.
- [x] Implement Lua closed-table validation for the envelope/index fields and the same virtual-path/test-kind allowlists. Keep Garry's Mod's normal `util.JSONToTable` depth/length protections; never pass permissive unlimited flags.
- [x] Implement atomic writers using `*.partial.json` in Garry's Mod, `file.Rename`, and `complete.ready.json` last. Preserve an existing run directory on duplicate `run_id`; write an explicit collision diagnostic instead of overwriting.
- [x] Implement `scripts/test_companion_lua.ps1` to extract the already-bundled `Resources/glualint.zip` into a unique temporary directory, lint only the Companion tree, remove only that verified temporary directory, and print `Companion Lua lint passed`.
- [x] Run `python -m pytest tests\companion_addon -q` and `powershell -ExecutionPolicy Bypass -File scripts\test_companion_lua.ps1`; expected result: all tests pass and the linter prints the success marker.
- [x] Commit: `feat(companion): add secure gmod addon foundation`.

## Task 3: Implement bounded runtime profiling and standalone audit value

**Files:**
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/server/profiler.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/server/error_collector.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/client/profiler.lua`
- Create: `tests/companion_addon/test_profiler_contract.py`
- Modify: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/shared/constants.lua`
- Modify: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer_loader.lua`

- [x] Add failing static/contract tests for lifecycle-driven queues, bounded initial inventory, amortized reconciliation, early invalid/hidden/model-less filtering, saturating evidence, deduplicated skin/bodygroup/submaterial sets, truncation counters, and caps of 4,096 models/16,384 materials/20 MiB.
- [x] Define evidence enum values in one shared module: `registered=0.10`, `instantiated=0.25`, `visible=0.50`, `repeated_visible=0.75`, and `focused=1.00`. Raw frequency may only promote to the next tier; it is not exported as importance.
- [x] Implement the server recorder around `OnEntityCreated`, an incrementally consumed initial `ents.GetAll()` queue, and a periodic bounded reconciliation queue. Record model path, skin, named bodygroups and values, submaterials, map/gamemode/build/realm/session duration, and mounted Workshop metadata exposed by the engine.
- [x] Implement the client visibility recorder with a fixed per-frame budget and round-robin candidates. Promote repeated visibility only after at least three separated samples; promote viewmodels, occupied vehicles, and explicit dashboard focus to `focused`.
- [x] Send only bounded observation records from authorized local administrators to the server, or write a separate client profile bundle when the server is remote. Never send complete profiles or screenshots through `net`.
- [x] Hook Lua errors only through engine-provided error hooks, sanitize and deduplicate message/source summaries, cap them, and exclude Steam IDs, player names, chat, IP addresses, arbitrary files, and absolute paths.
- [x] Record per-slice `SysTime()` duration into bounded histogram counters and export average, p95 approximation, maximum, queue high-water marks, and every truncation counter in the profile summary.
- [x] Run the Lua lint and `python -m pytest tests\companion_addon\test_profiler_contract.py -q`; expected result: pass.
- [x] Perform the first local game smoke test with the addon as a folder: start a session, spawn/enter one vehicle, stop/export, and verify a complete profile bundle is created without Lua errors. Record the console command transcript under `docs/companion/testing/profile-smoke.md`.
- [x] Commit: `feat(companion): record bounded runtime evidence`.

## Task 4: Build a content-verified Companion package and safe WPF installer

**Files:**
- Create: `scripts/package_companion.ps1`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Resources/SourceAddonOptimizerCompanion.zip`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Models/CompanionModels.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Companion/CompanionPackageInstaller.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Companion/CompanionPathPolicy.cs`
- Create: `tests/wpf_companion_contract/GmodAddonOptimizer.CompanionContractTests.csproj`
- Create: `tests/wpf_companion_contract/Program.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/GmodAddonCompressor.csproj`

- [x] Add failing C# contract tests that build small in-memory packages and cover fresh install, update of declared files, preservation of `garrysmod/data/sourceaddonoptimizer`, refusal to overwrite an unknown destination, tampered/missing ownership file, traversal entries, symlink/junction escape, concurrent callers, interrupted staging, uninstall of declared files only, and refusal to recursively remove non-empty unknown directories.
- [x] Define a package manifest containing schema/version plus sorted relative payload path, bytes, and SHA-256. Put the install manifest at ZIP root and the payload below `sourceaddonoptimizer_companion/`; embed the ZIP as `GmodAddonCompressor.Resources.SourceAddonOptimizerCompanion.zip`.
- [x] Implement `scripts/package_companion.ps1` with deterministic ordering, byte/hash manifest generation, a temporary ZIP, complete validation, and atomic replacement of the embedded resource. Fail on uppercase/unsafe paths, undeclared files, forbidden extensions, lint failure, or more than one autorun file.
- [x] Implement Steam/Garry's Mod path detection from explicit user path first and registry/library discovery second. Resolve `garrysmod/addons/sourceaddonoptimizer_companion` with `Path.GetFullPath` plus containment checks; never use a broad recursive delete.
- [x] Implement install/update using a sibling unique staging directory, file-by-file hash validation, an ownership marker containing the package hash, atomic directory move for a new install, and declared-file replacement for an owned update. Preserve data profiles/results because they are outside the addon directory.
- [x] Implement uninstall by deleting only manifest-declared files and known empty directories. If unknown files remain, keep the directory and return a warning listing them.
- [x] Run `powershell -ExecutionPolicy Bypass -File scripts\package_companion.ps1` and `dotnet run --project tests\wpf_companion_contract\GmodAddonOptimizer.CompanionContractTests.csproj -c Release`; expected result: package validation and all installer tests pass.
- [x] Commit: `feat(wpf): package and install companion safely`.

## Task 5: Add the WPF Companion tab without changing existing workflows

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/DataContexts/CompanionContext.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.Companion.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Models/AppSettingsModel.cs`

- [x] Add compile-time bindings/events in a new `Companion` tab with four cards: installation/compatibility, profile import/match summary, validation transfer/results, and quick start. Bind the tab root to a dedicated `CompanionContext`; do not expand the legacy Python GUI or move heavy work into `MainWindow.xaml.cs`.
- [x] Add installation tests to the C# contract project for state presentation (`Not installed`, `Compatible`, `Update available`, `Unknown folder`, `Incompatible`) and ensure every destructive action requires an owned manifest.
- [x] Implement `InitializeCompanion()` immediately after `InitializeAddonMerge()` and wire Install, Update, Remove, Open Data Folder, Import Profile, Generate Plan, Import Results, and Cancel buttons in `MainWindow.Companion.cs`.
- [x] Persist only the detected/selected Garry's Mod root and last imported profile location in `AppSettingsModel`. Never persist `UseWithMaximumV2=true`; initialize that property to false on every application launch and after every import.
- [x] Keep I/O and hashing asynchronous with cancellation and bounded progress. Any Companion error updates only the Companion card and never disables Models, Compress, Unpack, Merge, Maps, or Pipeline.
- [x] Run `dotnet build GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj -c Release` and both WPF contract projects; expected result: clean build and all contracts pass.
- [x] Launch the Release build, open every existing tab, then verify the Companion tab with no game installation and with a test game root. Record the manual result in `docs/companion/testing/wpf-tab-smoke.md`.
- [x] Commit: `feat(wpf): add optional companion workspace`.

## Task 6: Import, merge, and intersect profiles with explicit Maximum-only opt-in

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Companion/CompanionContract.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Companion/CompanionBundleReader.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Companion/CompanionProfileService.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/DataContexts/CompanionContext.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.Companion.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml`
- Modify: `tests/wpf_companion_contract/Program.cs`

- [x] Link the valid/hostile fixtures into the C# test project and add failing tests proving parity with Python: closed fields, limits before parse, hashes, versions, paths, finite numbers, duplicate IDs, incomplete runs, and metadata-only inspection for unsupported minor versions.
- [x] Add profile-merge tests proving maximum evidence tier wins, skins/bodygroups/submaterials use set union, raw counts do not add, zero matched assets yields no usable profile, and absence is never represented as negative evidence.
- [x] Implement the C# reader and profile service. Mark a profile stale and ineligible when its timestamp is more than 180 days old; reject timestamps over five minutes in the future. Age is never used as negative importance and never blocks ordinary optimization.
- [x] Intersect imported virtual model/material paths with the selected addon using normalized case-insensitive virtual identities, but preserve original display casing. Present origin, age, models/materials observed, matched assets, ignored external assets, truncations, and compatibility.
- [x] Add one compact card to Models and one to Pipeline, both bound to the same `CompanionContext`, with `[ ] Use with Models Maximum v2`. Enable it only when the imported profile is valid, non-stale, has at least one match, and the selected optimizer mode is Maximum.
- [x] Clear the checkbox when the mode leaves Maximum, profile changes, selected addon changes, or application restarts. Merely detecting a new profile never checks it.
- [x] Run the C# contract tests and WPF build. Manually prove that Normal/Fidelity and every Compress preset behave with the controls disabled and unchanged.
- [x] Commit: `feat(wpf): import profiles with explicit maximum opt-in`.

## Task 7: Parse QC runtime bindings and build a regional evidence map

**Files:**
- Create: `maximum_optimizer/runtime_profile.py`
- Create: `maximum_optimizer/qc_runtime.py`
- Create: `tests/maximum_optimizer/test_runtime_profile.py`
- Create: `tests/maximum_optimizer/fixtures/runtime_vehicle.qc`
- Modify: `maximum_optimizer/qc_graph.py` only if a shared tokenizer/helper is extracted

- [x] Add failing tests for the fixed tier mapping, merged maximum tier, model-path normalization, `$modelname`, `$body`, `$model`, bodygroup name/choice indices, `$texturegroup` skin rows, `$cdmaterials`, direct material matches, missing bindings, and deterministic results under reordered input.
- [x] Define immutable `RuntimeEvidenceMap` and `RegionRuntimeImportance` records. `for_region(occurrence, material)` must return model baseline, observed-state signal, material signal, final maximum, and human-readable contributing reasons.
- [x] Parse QC into bindings keyed by canonical `$modelname`, source occurrence, bodygroup choice, and skin material row. Reuse the existing safe tokenizer and containment behavior; malformed/ambiguous QC contributes no state signal instead of failing a model family.
- [x] Implement exactly:

```python
runtime_importance = max(
    0.40 * model_importance,
    observed_state_importance,
    region_material_importance,
)
```

- [x] Ensure a model-only `1.00` observation yields at most `0.40` for every region. A wheel/bodygroup/material observation may yield the full tier only for its mapped regional occurrence/material. Missing paths return all zeros.
- [x] Load only `sao-runtime-profile-v1` bundles through `companion_contract.read_bundle`; cap/validation failures return a typed diagnostic to the caller, never a partially built map.
- [x] Run `python -m pytest tests\maximum_optimizer\test_runtime_profile.py tests\maximum_optimizer\test_risk.py -q`; expected result: pass.
- [x] Commit: `feat(maximum): map runtime evidence to model regions`.

## Task 8: Integrate runtime importance into Maximum v2 without adding work explosion

**Files:**
- Modify: `maximum_optimizer/contracts.py`
- Modify: `maximum_optimizer/risk.py`
- Modify: `maximum_optimizer/pipeline.py`
- Modify: `maximum_optimizer/reporting.py`
- Modify: `build_optimized_addon.py`
- Modify: `tests/maximum_optimizer/test_risk.py`
- Modify: `tests/maximum_optimizer/test_pipeline_reporting.py`
- Modify: `tests/maximum_optimizer/test_worker_cli.py`

- [x] Add failing unit tests for the exact effective-risk formula, clamping, monotonicity, zero equivalence, target/budget reuse, and protection headroom:

```python
effective = risk + (1.0 - risk) * 0.35 * runtime_importance
```

- [x] Add failing pipeline tests proving no profile and an empty/disabled profile produce byte-for-byte identical selected sources and report bytes, model-only evidence cannot create a family gate, a protected failed wheel restores only that wheel, and simplifier evaluations do not exceed the current bound.
- [x] Add `--maximum-runtime-profile <bundle-directory>` to `parse_args`. Reject its use with Normal/Fidelity at argument validation; when Maximum receives malformed/incompatible/stale input, print one warning, continue with `None`, and keep ordinary optimization available.
- [x] Add `runtime_evidence: RuntimeEvidenceMap | None = None` to `MaximumRunOptions`. Do not import, parse, or initialize Companion modules in Normal/Fidelity branches.
- [x] Add `with_effective_risk(features, importance)` that copies `RiskFeatures` with effective score and recalculated `target_ratio = 0.08 + 0.57 * effective_score`; pass that copy into the existing `budget_for_risk` and search.
- [x] Let direct state/material importance `>=0.50` schedule the existing adaptive validator for regions of at least 128 triangles. The moderated model baseline alone must not switch a low-risk region into validation; it may only tighten the target/budget if that region was already being evaluated.
- [x] Preserve the existing search and compile structure: no new full-family candidate, no second lighter candidate, no extra compile, no automatic Blender render. Use importance only for score/budget/priority and the already-existing one regional recovery.
- [x] When nonzero evidence is actually applied, extend the report with a `runtime_profile` block containing bundle hash/run ID, matched model/material/region counts, `geometric_risk`, `effective_risk`, `runtime_importance`, and reasons per region. Omit that block entirely for absent, disabled, empty, zero-match, rejected, or stale profiles so the no-profile report bytes remain unchanged; retain existing `risk_score` as the geometric score.
- [x] Run `python -m pytest tests\maximum_optimizer\test_risk.py tests\maximum_optimizer\test_runtime_profile.py tests\maximum_optimizer\test_pipeline_reporting.py tests\maximum_optimizer\test_worker_cli.py -q`; expected result: all pass.
- [x] Commit: `feat(maximum): apply bounded runtime importance regionally`.

## Task 9: Bridge the explicit WPF choice to the worker and summarize its effect

**Files:**
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerRunner.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerCommandBuilder.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.Companion.cs`
- Modify: `tests/wpf_optimizer_contract/Program.cs`
- Modify: `tests/wpf_companion_contract/Program.cs`

- [x] Add failing command-builder tests: Maximum plus checked valid profile emits one `--maximum-runtime-profile` argument; unchecked, invalid, Normal, and Fidelity emit none. Paths containing spaces remain one `ArgumentList` value.
- [x] Add `MaximumRuntimeProfilePath` to `SourceAddonOptimizerRunOptions`; populate it only inside `BuildSourceAddonOptimizerOptions()` when all explicit-use conditions still hold at run start.
- [x] Snapshot the selected bundle into the run work directory using validated declared files before launching the worker. Revalidate hashes after copy and pass the snapshot directory, so changing/removing the game-side profile cannot alter an active run.
- [x] Clear opt-in and continue without the profile if the snapshot fails. Do not fail or delay the ordinary Models/Pipeline start beyond one bounded validation/copy pass.
- [x] Read `maximum_adaptive_report.json` after completion and show whether evidence was applied plus matched models/materials/regions, geometric-to-effective protection, and extra regional recoveries. Never label a detected-but-unused profile as used.
- [x] Run both C# contract projects and the focused worker CLI tests; expected result: all pass.
- [x] Commit: `feat(wpf): pass explicit profile to maximum worker`.

## Task 10: Generate a closed validation plan from the optimized result

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Companion/CompanionValidationPlanService.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.Companion.cs`
- Modify: `maximum_optimizer/reporting.py`
- Modify: `tests/wpf_companion_contract/Program.cs`
- Modify: `tests/maximum_optimizer/test_pipeline_reporting.py`

- [x] Add failing tests that build a plan from a synthetic optimized output/report and require virtual paths only; required `.mdl`, `.vvd`, `.dx90.vtx`; `.phy` only when expected; VMT/VTF dependencies; default and observed skin/bodygroup states; bounded risk-driven views; expected sizes; and explicit DX80 exclusion metadata.
- [x] Add rejection tests ensuring no field can contain an absolute path, executable, command, Lua source, URL, arguments, unknown test kind, or more than 250 models/1,000 screenshots/four ordinary views/one retry.
- [x] Extend the Maximum report only as needed to expose model virtual path, expected PHY, changed models, risk/material details, and selected states. Keep local source/work paths out of the exchange plan.
- [x] Implement plan priority in this order: structural failures, observed resources, curved/transparent regions, large geometry reduction, large texture reduction, then remaining assets. Select one isometric view for low risk, front plus isometric for medium, and front/side/rear/isometric for high or repeatedly observed.
- [x] Write `sao-validation-plan-v1` through the same chunk/index/completion protocol into a selected local `garrysmod/data/sourceaddonoptimizer/validation_inbox` or an export folder for manual remote transfer.
- [x] Bind Generate/Copy/Open actions to the Companion tab and show plan hash/run ID/caps/truncations. Never automatically copy to or control a remote server.
- [x] Run the C# and Python focused tests; expected result: pass with deterministic plan bytes for deterministic fixture input.
- [x] Commit: `feat(companion): emit bounded validation plans`.

## Task 11: Implement in-game structural validation and resumable status

**Files:**
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/server/structural_validator.lua`
- Modify: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/server/result_writer.lua`
- Modify: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/shared/validation.lua`
- Create: `tests/companion_addon/test_structural_validator_contract.py`

- [x] Add failing static contract tests for the closed test-kind dispatch table, required-sidecar checks, DX90 requirement, expected PHY behavior, `util.IsValidModel`, bounded temporary entity creation/removal, skin/bodygroup bounds, material/texture dependencies, conflict warnings, Lua-error summaries, and headless completion.
- [x] Implement a queue that reads only a fully validated `sao-validation-plan-v1` from `validation_inbox`, copies fixed test records into bounded internal state, and processes a configured small number per tick with cancellation/map-change hooks.
- [x] Use an explicit dispatch table for `model_structure`, `model_state`, `material_resolve`, and `model_render`; server code handles the first three and records visual items as pending client work. Unknown kinds reject the entire plan.
- [x] Verify `.mdl`, `.vvd`, `.dx90.vtx`, expected `.phy`, `util.IsValidModel`, temporary entity validity, skin/bodygroup range and selection, `GetMaterials()`, referenced VMT/VTF existence, basic bone/mesh plausibility, and multiple mounted providers where the engine exposes ambiguity.
- [x] Never execute addon Lua as a test. Remove every temporary entity on success, failure, cancellation, disconnect, or map change.
- [x] Emit per-resource `PASS`, `WARNING`, `FAILED`, or `INCOMPLETE` and compute overall status exactly as approved. A headless dedicated server completes structural work and emits a visual-client-missing `WARNING`, not a structural failure.
- [x] Persist bounded progress on cancellation/map change/disconnect, then write `sao-structural-result-v1` atomically. Preserve prior result folders on duplicate run IDs.
- [x] Run Lua lint and `python -m pytest tests\companion_addon\test_structural_validator_contract.py -q`; expected result: pass.
- [x] Commit: `feat(companion): validate optimized assets structurally`.

## Task 12: Implement the controlled visual gallery and screenshot evidence

**Files:**
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/client/dashboard.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/client/model_gallery.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/client/asset_validator.lua`
- Create: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/client/screenshot_capture.lua`
- Modify: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer/client/result_writer.lua`
- Modify: `companion/sourceaddonoptimizer_companion/lua/sourceaddonoptimizer_loader.lua`
- Create: `tests/companion_addon/test_visual_gallery_contract.py`

- [ ] Add failing tests for the `sao_companion` console command, Spawn Menu Utilities registration, panels `Overview/Profile/Validate/Results/Help`, authority checks, queue caps, four fixed views, one retry, cancellation, resolution-independent sizing, and text/icon status in addition to color.
- [ ] Implement a `DModelPanel`-based controlled renderer with neutral background, fixed ambient/directional lighting, bounding-box camera fitting, and canonical front/side/rear/isometric camera transforms. Apply only plan-declared skin/bodygroup/submaterial state.
- [ ] Capture only during a render-safe hook after the panel has painted, with a fixed crop and PNG output beneath `data/sourceaddonoptimizer/screenshots/<run-id>/`. Never accept a path or filename from the plan beyond validated test/run IDs.
- [ ] Enforce 250 models, 1,000 screenshots, four ordinary views, one bounded close-up for a suspected failure, and one capture retry. Save queue progress after every model and resume only when run ID plus plan hash match.
- [ ] On capture failure after retry, emit `WARNING`; on disconnect/map change/user cancel, emit `INCOMPLETE`. Never transfer screenshot bytes through `net`.
- [ ] Write `sao-visual-result-v1` containing screenshot relative path/hash/size, camera/state metadata, per-test status, timing, retries, and truncations. Keep screenshots outside JSON chunks.
- [ ] Run Lua lint and the visual contract tests; expected result: pass.
- [ ] Perform a local screenshot smoke test at 1280x720, 1920x1080, and 150% Windows scaling. Record evidence paths and UI defects in `docs/companion/testing/gallery-smoke.md`.
- [ ] Commit: `feat(companion): capture bounded visual validation evidence`.

## Task 13: Import and merge structural/visual results in WPF

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Companion/CompanionResultService.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/DataContexts/CompanionContext.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.Companion.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml`
- Modify: `tests/wpf_companion_contract/Program.cs`

- [ ] Add failing tests for structural-only import, visual-only incomplete state, merge by identical `run_id` and plan hash, mismatched hash rejection, duplicate result preservation, screenshot hash/path containment, and exact overall status precedence.
- [ ] Implement result import through the same strict bundle reader. Resolve screenshots with `Path.GetFullPath` plus containment under the selected `screenshots/<run-id>` root and verify declared size/hash before displaying/opening.
- [ ] Merge structural and visual resources only when run ID and plan SHA-256 match. Keep conflicting imports side-by-side as diagnostics; never silently replace a prior result.
- [ ] Present totals and filterable `FAILED`, `WARNING`, `INCOMPLETE`, and `PASS` groups, with model/material/test identifiers, short reasons, screenshots, truncation/performance stats, and copy/export summary actions.
- [ ] Keep structural results useful without screenshots. A headless run must show `WARNING: visual validation not run`, not `FAILED`.
- [ ] Run the C# contract tests and WPF build, then import valid and hostile fixture results manually; expected result: hostile inputs are preserved for diagnosis but never partially displayed as trusted results.
- [ ] Commit: `feat(wpf): present companion validation results`.

## Task 14: Run full automated security and regression gates

**Files:**
- Create: `scripts/test_companion.ps1`
- Modify: `tests/companion_contract/*` only for newly discovered regression fixtures
- Modify: `tests/companion_addon/*` only for newly discovered regression checks
- Modify: `tests/wpf_companion_contract/Program.cs` only for newly discovered regressions

- [ ] Build one orchestrator script that runs contract pytest, Companion-addon pytest, Maximum-focused pytest, Lua lint, existing WPF optimizer contracts, new WPF Companion contracts, and the WPF Release build. Stop on the first non-zero exit and print each exact command.
- [ ] Add Windows filesystem security tests using real symlinks/junctions when privileges allow and an explicit `SKIPPED (privilege unavailable)` result otherwise; traversal/absolute/UNC tests must never skip.
- [ ] Add net-protocol contract cases for unauthorized, replayed, expired, oversized, rate-limited, malformed, and wrong-order messages. Verify handlers discard the Companion message without disconnecting unrelated users.
- [ ] Add an artifact-dependency test that scans an optimized addon fixture and fails if any Companion Lua/material path or `sourceaddonoptimizer` runtime reference appears.
- [ ] Add no-profile golden tests comparing Maximum decisions, selected SMD serialization, report core fields, compile count, and output tree hashes before/after this feature. Verify Normal/Fidelity never import `runtime_profile` and every Compress test remains unchanged.
- [ ] Run `powershell -ExecutionPolicy Bypass -File scripts\test_companion.ps1`; expected result: all mandatory gates pass with only documented privilege skips.
- [ ] Run `git diff --check` and inspect `git status --short` for caches, data exports, screenshots, binaries, or unrelated changes.
- [ ] Commit: `test(companion): enforce security and no-profile regressions`.

## Task 15: Execute the real-game matrix and LVS benchmark before tuning

**Files:**
- Create: `scripts/benchmark_companion.py`
- Create: `scripts/measure_companion_profiler.lua`
- Create: `docs/companion/testing/real-game-matrix.md`
- Create: `docs/companion/testing/lvs-benchmark-protocol.md`
- Create after measurements: `docs/benchmarks/2026-08-optimization-companion-v1.md`
- Store raw evidence outside the repository under a mandatory `-EvidenceRoot`/`--evidence-root` path with sufficient free space.

- [ ] Make the benchmark script require explicit roots for `lvs_cars_pack`, optional `lvs_framework`, current WPF/worker, and evidence output. Refuse to write inside the repository, `%LOCALAPPDATA%\GmodAddonOptimizer\work`, or the source addon. Record environment, versions, hashes, power plan, warm/cold state, commands, and free disk space.
- [ ] Select 5-10 detailed LVS families covering wheels/tires, steering wheels, glass/lenses, sights, exhausts/pipes, lights, interiors, bodywork, skins/bodygroups, and skinned components. Write the exact list before executing either lane.
- [ ] Record a representative profile by driving/occupying/focusing the selected assets. Verify average profiler work below 0.5 ms, p95 below 2 ms, every slice below 5 ms, bounded memory/tables, and explicit truncation counts.
- [ ] Run paired/interleaved Maximum v2 lanes from identical clean inputs: A without Companion profile, B with the explicitly approved profile. Use at least three runs per lane after one warm-up and preserve raw stdout, reports, selected SMD hashes, compiled model hashes, and timings.
- [ ] Measure output bytes excluding DX80, total wall/CPU time, peak RSS, triangle/vertex totals, region decisions/recoveries, simplifier evaluations, compile count, silhouette/curvature/normal/UV/skinning/material metrics, DX90/VVD/PHY integrity, and external-reference closure.
- [ ] Run the generated plan in single-player, listen server, Windows dedicated server, Linux dedicated server, and remote-server-plus-admin-client flows. Cover folder/GMA content, original+optimized mounted conflict, missing sidecars/materials/states, cancellation, disconnect, map change, and headless structural-only mode.
- [ ] Capture matched canonical screenshots for observed rounded regions and inspect them at native resolution. Do not claim visual improvement from metrics alone.
- [ ] Accept the profile-assisted lane only if there is no no-profile regression, no family-wide preservation, no new full-family candidate, no more than 10% processing-time overhead, target no more than 5% output-size increase, no visual regression in observed regions, valid compile/closure/DX90, and explicit cap reporting.
- [ ] If a gate fails, tune only non-contract scheduling/budget parameters such as visibility promotion cadence or direct-evidence validation threshold, then rerun the complete paired set and document rejected settings. The approved `0.35` and `0.40` formulas may change only through a separately reviewed design amendment; do not add candidates, family gates, or hidden exceptions.
- [ ] Write the benchmark report with median/range, per-family table, representative images, failures, rejected trials, and an objective go/no-go decision. Commit only compact reports/scripts; keep raw models/screenshots/logs in the external evidence root.
- [ ] Commit: `perf(companion): report lvs profile-assisted maximum benchmark`.

## Task 16: Finish documentation, release gates, and official/commercial packaging

**Files:**
- Create: `docs/companion/Installation.md`
- Create: `docs/companion/QuickStart.md`
- Create: `docs/companion/Troubleshooting.md`
- Create: `docs/commercial/AI-DISCLOSURE.md`
- Create: `docs/commercial/third-party-audit.json`
- Create: `docs/commercial/release-readiness.md`
- Create: `build_release_gmodstore.ps1`
- Modify: `build_release_wpf.ps1`
- Modify: `pyinstaller/package_wpf_tools.ps1` only if the worker freshness/resource audit requires it
- Modify: `README.md`
- Modify: `AGENTS.md` only to document the final, proven build/install flow

- [ ] Write English installation, quick-start, troubleshooting, local/remote transfer, permission, privacy, and removal documentation. Add Portuguese only as supplementary text. Generate the three required PDFs from these reviewed sources and visually inspect every page.
- [ ] Complete the AI Disclosure with factual use of AI in investigation, tests, refactoring, documentation, and code work. Do not make authorship or review claims that are not true.
- [ ] Inventory every bundled executable, DLL, Python package, NuGet package, Lua file, font, image, and native library in `third-party-audit.json` with source, version, license, redistribution evidence, and destination. A missing/unknown redistribution status is a hard commercial-build failure.
- [ ] Record three explicit pre-sale decisions in `release-readiness.md`: written GmodStore approval evidence for the combined addon+desktop package, executable code-signing decision, and supported-runtime strategy replacing or formally mitigating the out-of-support .NET 6 target. These are gates, not assumptions.
- [ ] Keep `build_release_wpf.ps1` as the official application build. Ensure it invokes fresh Companion packaging before `dotnet publish`, validates the embedded addon ZIP, rebuilds stale worker sources, and still produces `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe` unless the approved runtime-migration gate deliberately changes the target path and documentation together.
- [ ] Implement `build_release_gmodstore.ps1` to invoke the official WPF build, require files proving all three pre-sale decisions and the third-party audit, assemble `Desktop/`, `GarrysModAddon/sourceaddonoptimizer_companion/`, `Documentation/`, `THIRD-PARTY-LICENSES/`, `CHANGELOG.md`, `AI-DISCLOSURE.md`, and `checksums.txt`, then create one deterministic archive under `dist-commercial/`.
- [ ] Make the commercial build fail on logs, `work/`, benchmarks, screenshots/raw evidence, `.rar`, nested archives, caches, credentials, local absolute paths, undeclared binaries, unknown files, missing English docs, missing license evidence, or a Companion package that fails lint/manifest validation. The desktop executable stays outside the Garry's Mod addon folder.
- [ ] Run `powershell -ExecutionPolicy Bypass -File scripts\test_companion.ps1`, then `powershell -ExecutionPolicy Bypass -File build_release_wpf.ps1`. Launch the published executable, open all tabs, install/update/remove the Companion in a disposable game root, run one packaged Maximum job without a profile and one with explicit opt-in, and import its validation result.
- [ ] Only after every pre-sale gate exists, run `powershell -ExecutionPolicy Bypass -File build_release_gmodstore.ps1`; validate the archive on a clean extraction and calculate SHA-256 for the EXE, Companion payload, and commercial ZIP.
- [ ] Write the final verdict in `docs/benchmarks/2026-08-optimization-companion-v1.md`: reduction, visual/structural quality, time, profiler overhead, validation throughput, limitations, benchmark evidence root, published executable path, commercial ZIP path, and hashes. Never claim a commercial release if an external pre-sale gate is absent.
- [ ] Run `git diff --check`, `git status --short`, all tests, and release smoke checks once more. Commit: `release(companion): package optional in-game validation`.

---

## Completion Gate

Implementation is complete only when all automated suites pass; the addon loads without Lua errors in the approved game matrix; the WPF remains fully usable without it; profile use is explicit and Maximum-only; a no-profile run is behaviorally unchanged; regional evidence follows the fixed formulas and never creates a family gate or extra search; profiling meets the 0.5/2/5 ms targets; output validation proves DX90 and excludes DX80; result bundles and screenshots import safely; the optimized addon contains no Companion dependency; the LVS report supports every size/time/quality claim with real numbers and images; install/update/removal are safe; and the official WPF executable is built through `build_release_wpf.ps1`.

The GmodStore archive is a separate final gate: it may be called a commercial release only after written format approval, redistribution audit, AI disclosure, English documentation, code-signing decision, and supported-runtime strategy are all documented and validated by `build_release_gmodstore.ps1`.
