# Optimization Companion design

Date: 2026-08-16

Status: approved for implementation planning

Product: Source Addon Optimizer / GMod Addon Optimizer

## 1. Decision summary

Build an optional Garry's Mod addon named **Source Addon Optimizer Companion**. It closes the loop around the existing Windows optimizer without becoming a runtime dependency:

1. Garry's Mod records a bounded runtime-usage profile.
2. The WPF application optionally imports that profile.
3. Models Maximum v2 uses the profile as a limited positive importance signal.
4. The WPF application emits a declarative validation plan after optimization.
5. The Companion validates the optimized content inside Garry's Mod and exports structural and visual evidence.
6. The WPF application imports and presents the result.

The existing WPF application remains the primary product. Heavy processing remains in the Python worker. The legacy Python GUI in `gui/` receives no new feature.

## 2. Commercial rationale and constraints

Pivity's general marketplace terms cover scripts, tools, digital assets, and related digital products. GmodStore's product-submission guidance is nevertheless centered on substantial Garry's Mod addons, and its denial guidance warns that very small scripts and simple utility libraries may be rejected. The Companion must therefore solve a real in-game problem; it must not exist merely to make a desktop executable look like an addon.

The public rules do not explicitly guarantee acceptance of a ZIP that contains both a Windows application and a Garry's Mod addon. Written confirmation from GmodStore is a pre-submission gate, not an assumption made by this design.

Relevant sources:

- [Pivity Marketplace Terms](https://pivity.com/legal/market)
- [GmodStore selling guidance](https://www.gmodstore.com/help/getting-started/topics/selling-scripts-guidlines-and-tips)
- [GmodStore common denial reasons](https://www.gmodstore.com/help/script-submissions/topics/frequently-used-denial-reasons)
- [GmodStore development guidelines](https://www.gmodstore.com/help/script-submissions/topics/development-guidelines)
- [GmodStore presentation requirements](https://www.gmodstore.com/help/script-submissions/topics/presentation-requirements-and-standards)
- [GmodStore AI usage policy](https://www.gmodstore.com/help/script-submissions/topics/ai-usage-policy)
- [GmodStore external-service sunsetting policy](https://www.gmodstore.com/help/developers/topics/external-service-sunsetting-policy)
- [Facepunch Workshop addon creation and whitelist](https://wiki.facepunch.com/gmod/Workshop_Addon_Creation)

Commercial consequences:

- The addon must provide useful auditing and validation even when the WPF application is not open.
- The executable must remain outside the Garry's Mod addon folder. Workshop/GMA packaging restricts executables.
- The product must have a visible AI Disclosure describing the use of AI for tests, refactoring, investigation, documentation, and other applicable work.
- The product must be reviewable and maintainable; the original unobfuscated code must be available to curators.
- Third-party redistribution rights must be audited before sale.

## 3. Goals

- Give Models Maximum v2 evidence of assets and states observed in the real engine.
- Improve protection of important curved, transparent, skinned, and silhouette-sensitive regions without introducing family-wide gates.
- Detect broken models, missing DX90 files, missing materials, invalid skins/bodygroups, and related regressions in Garry's Mod.
- Produce bounded, reproducible visual evidence for changed and high-risk assets.
- Make local use straightforward and remote-server use possible through manual file transfer.
- Keep every existing WPF workflow unchanged when the Companion is absent or disabled.
- Provide enough independent in-game value to be a legitimate GmodStore addon.

## 4. Non-goals for version 1

- Automatic SFTP or storage of server credentials.
- Cloud services, telemetry, online DRM, or download webhooks.
- Localhost HTTP control or launching the desktop application from Lua.
- Workshop upload or deployment automation.
- A two-run in-game baseline-versus-optimized screenshot comparison.
- Runtime audio profiling.
- Changing Models normal, Magick, Magick+, maps, audio, or Lua optimization based on a runtime profile.
- Making the optimized addon depend on the Companion.
- Adding features to the legacy Python GUI.

## 5. Non-negotiable invariants

- The WPF application works normally without the Companion.
- The optimized addon contains no Companion code and has no Companion runtime dependency.
- Installing, importing, or detecting a profile never enables it silently.
- A profile is used only after an explicit `Use with Models Maximum v2` choice.
- Absence from a profile is never evidence that an asset is unused or safe to delete.
- Profile evidence can only increase protection; it cannot lower the existing geometric risk.
- Profile evidence cannot create a family-wide preservation gate.
- A failed region can trigger at most the existing single lighter regional recovery evaluation.
- A malformed, incompatible, stale, or oversized profile cannot block ordinary optimization.
- DX90 remains required and validated. DX80 remains excluded from results and metrics.
- No JSON field can execute code, launch a process, issue an arbitrary console command, or name an arbitrary filesystem location.

## 6. Architecture

```text
Garry's Mod Companion
  runtime profile
        |
        | profile chunks
        v
WPF Companion integration
  validate/import/intersect with selected addon
        |
        | optional RuntimeImportance map
        v
Python Models Maximum v2 worker
  current geometric risk + bounded usage importance
        |
        | optimized addon + validation plan
        v
Garry's Mod Companion
  structural audit + bounded visual gallery
        |
        | structural/visual result chunks + screenshots
        v
WPF result viewer and export
```

### 6.1 Components

The design introduces four bounded units:

1. **WPF Companion integration**: installation, profile import, plan generation, result import, and reporting.
2. **Worker runtime-importance adapter**: turns validated evidence into region-local risk adjustments for Maximum v2.
3. **Garry's Mod Companion addon**: profiler, permissions, structural validator, visual gallery, and result writer.
4. **Contract library and fixtures**: versioned schemas, strict validation, chunking, hashing, and cross-language tests.

No unit can invoke an arbitrary operation in another unit. Communication is declarative and file-based.

### 6.2 Companion addon layout

```text
sourceaddonoptimizer_companion/
|-- addon.json
|-- lua/
|   |-- autorun/
|   |   `-- sourceaddonoptimizer_loader.lua
|   `-- sourceaddonoptimizer/
|       |-- shared/
|       |   |-- contract.lua
|       |   `-- validation.lua
|       |-- server/
|       |   |-- profiler.lua
|       |   |-- permissions.lua
|       |   `-- error_collector.lua
|       `-- client/
|           |-- dashboard.lua
|           |-- model_gallery.lua
|           |-- asset_validator.lua
|           `-- screenshot_capture.lua
`-- materials/
    `-- sourceaddonoptimizer/
```

There is one autorun file, limited to loading the remaining files. Names and paths are unique, lowercase, and Linux-safe.

### 6.3 Exchange folders

```text
garrysmod/data/sourceaddonoptimizer/
|-- profiles/
|-- validation_inbox/
|-- validation_results/
`-- screenshots/
```

The WPF application and game do not need to run simultaneously. A remote dedicated-server flow uses manual SFTP for the server-side plan and structural result. The visual plan and screenshots remain on the administrator's client machine and are merged by `run_id` and plan hash.

## 7. Runtime profiling

### 7.1 User flow

The administrator explicitly starts and stops a profiling session:

```text
Start Profiling -> play/test normally -> Stop and Export
```

The profiler records:

- map, gamemode, game build, realm, and session duration;
- mounted Workshop addon metadata exposed by the engine;
- models instantiated during the session;
- conservative samples of models visible to the local client;
- observed skins, bodygroup states, and submaterials;
- viewmodels, occupied vehicles, players, weapons, and explicit focus states;
- deduplicated and sanitized Lua errors attributed by the engine when possible.

It does not record Steam IDs, player names, chat, IP addresses, credentials, databases, arbitrary files, or absolute machine paths.

### 7.2 Sampling strategy and budgets

The profiler must not run a full entity scan every frame.

- `OnEntityCreated` and equivalent lifecycle signals add new work.
- The initial entity inventory is processed incrementally.
- Periodic reconciliation is amortized across frames.
- Dormant, hidden, invalid, and model-less entities are discarded early.
- Paths and state tuples are normalized and deduplicated.
- Frequency saturates after enough evidence; long sessions cannot inflate importance indefinitely.

Initial performance targets:

- average profiling overhead below 0.5 ms on the benchmark environment;
- 95th-percentile profiler work below 2 ms and no individual profiler slice above 5 ms on the benchmark environment;
- no unbounded table growth;
- at most 4,096 unique models and 16,384 unique materials per session;
- a hard 20 MiB maximum exported profile size across all chunks;
- explicit truncation counters when a safety cap is reached.

Large profiles are split into bounded chunks rather than bypassing Garry's Mod JSON parser limits.

### 7.3 Evidence tiers

The addon records evidence, not a trusted importance number. The WPF application derives importance using this fixed mapping:

| Evidence | Derived importance |
| --- | ---: |
| Registered or mounted only | 0.10 |
| Instantiated | 0.25 |
| Sampled as visible | 0.50 |
| Sampled as visible repeatedly | 0.75 |
| Viewmodel, occupied vehicle, or explicit focus | 1.00 |

When profiles are merged, importance uses the maximum evidence tier, while observed skins and bodygroup states use a set union. Raw observation counts are not added across profiles.

## 8. Maximum v2 integration

The existing `RiskFeatures.score` remains authoritative. Runtime importance increases only the remaining score headroom:

```text
effective_score = risk_score + (1 - risk_score) * 0.35 * runtime_importance
```

The effective score is clamped to `[0, 1]` and is used for the existing target-ratio and validation-budget calculations. An unobserved region has `runtime_importance = 0` and therefore retains the current behavior.

Region importance is resolved from the maximum of:

- `0.40 * model_importance`, which provides only a moderated family baseline;
- the observed skin/bodygroup state mapped through the decompiled QC/SMD structure;
- the material signal for the specific region.

In formula form:

```text
runtime_importance = max(
    0.40 * model_importance,
    observed_state_importance,
    region_material_importance
)
```

This mapping keeps the decision regional. Observing a car may moderately raise the model baseline, but an observed wheel/bodygroup/material can receive higher protection without preserving the full family.

Profile use can:

- tighten existing regional metrics within the formula above;
- ensure observed skins/bodygroups enter the bounded validation set;
- affect regional validation priority;
- select the existing lighter regional recovery when a protected region fails.

Profile use cannot:

- introduce additional full-family candidates;
- add a family gate;
- increase the one-recovery regional search limit;
- change Models normal or any Compress preset;
- treat absence as negative importance.

No-profile and disabled-profile executions must preserve current behavior. Where external tools are deterministic, their outputs must be bytewise equivalent.

## 9. Post-optimization validation

### 9.1 Validation plan

The WPF application emits a versioned plan containing virtual paths and declarative test cases for:

- changed models;
- required `.mdl`, `.vvd`, `.dx90.vtx`, and expected `.phy` files;
- expected absence from DX80 accounting;
- related VMT/VTF resources;
- observed and default skins/bodygroups;
- high-risk regions and bounded camera views;
- expected sizes and safe metadata;
- test and screenshot limits.

The plan never contains source code, shell commands, console commands, URLs, or absolute paths.

### 9.2 Structural audit

The structural audit verifies, as applicable:

- required model files and DX90 sidecars exist;
- `.phy` remains when the source contract requires it;
- `util.IsValidModel` succeeds;
- a temporary model instance can be created safely;
- expected skins and bodygroups can be selected;
- model materials resolve and required textures exist;
- material dependencies such as base, bump, detail, and masks resolve;
- basic bone/mesh metadata is plausible;
- multiple mounted addons appear to provide the same target path;
- Lua errors occur during the normal validation session.

It does not artificially execute arbitrary addon Lua.

### 9.3 Visual gallery

The client-side gallery renders temporary models in a controlled panel with a neutral background, fixed lighting, bounding-box camera fitting, and canonical views.

Default capture policy:

- low risk: one isometric view;
- medium risk: front and isometric views;
- high risk or repeatedly observed: front, side, rear, and isometric views;
- suspected visual failure: one bounded close-up;
- unobserved states: a limited representative sample.

Initial run caps:

- 250 models;
- 1,000 screenshots;
- four canonical views per ordinary state;
- one screenshot retry;
- resumable queue and explicit cancellation.

Priority order is structural failures, observed resources, curved/transparent regions, large geometry reductions, large texture reductions, then remaining assets.

`render.Capture` output is written under the Companion data folder. A headless dedicated server performs structural validation only; absence of a visual client is a warning rather than a structural failure.

### 9.4 Status model

Per-resource states:

- `PASS`: all required and scheduled checks completed successfully.
- `WARNING`: no confirmed breakage, but a check was skipped, incomplete, ambiguous, near a limit, or affected by environment/conflict.
- `FAILED`: a required file, model, material, state, or confirmed visual gate failed.
- `INCOMPLETE`: cancellation, disconnect, map change, missing chunk, or interrupted execution prevented a complete decision.

Overall status:

```text
any FAILED              -> FAILED
no FAILED, any WARNING  -> WARNING
no FAILED/WARNING, all complete -> PASS
interrupted before a decision   -> INCOMPLETE
```

## 10. WPF experience

### 10.1 Companion tab

The primary WPF receives a `Companion` tab with four bounded sections:

1. Garry's Mod path, addon installation, installed version, and compatibility.
2. Profile detection/import and matched-asset summary.
3. Validation plan generation, local transfer, remote instructions, and result import.
4. Contextual quick-start guidance.

The application may auto-detect Garry's Mod, but installation happens only after the user presses `Install Companion`.

The installer:

- refuses to overwrite an unknown directory;
- requires a Companion ownership marker and file manifest;
- updates only declared files;
- preserves profiles and results;
- removes only declared Companion files;
- never performs a broad recursive deletion.

### 10.2 Models and Pipeline

Models and Pipeline receive a compact optional-profile card. It shows profile origin, matched assets, and an explicit checkbox:

```text
[ ] Use with Models Maximum v2
```

The control explains that version 1 does not affect other modes. The run summary reports whether a profile was used and how many models, regions, and materials received additional protection.

### 10.3 Garry's Mod interface

The Companion is opened through:

```text
Spawn Menu -> Utilities -> Source Addon Optimizer
```

or:

```text
sao_companion
```

Panels are `Overview`, `Profile`, `Validate`, `Results`, and `Help`. All privileged actions require server console, superadmin, or single-player authority. The interface is resolution-aware, uses text/icons in addition to color, emits no chat spam, and reports technical initialization only to the console and its own UI.

The commercial page and primary documentation are English. Portuguese may be an additional localization.

## 11. Contract and file security

### 11.1 Schemas

Version 1 defines four top-level schemas:

- `sao-runtime-profile-v1`
- `sao-validation-plan-v1`
- `sao-structural-result-v1`
- `sao-visual-result-v1`

Each envelope includes exact schema, contract major/minor, UUID run ID, RFC 3339 UTC timestamp, producer name/version, bounded environment metadata, and payload.

Structures are closed: missing required fields and unknown fields are rejected. A newer unsupported minor version may expose safe envelope metadata in the UI, but it cannot influence optimization or execute validation. A different major version is incompatible.

### 11.2 Chunking and atomic completion

Each chunk is at most 2 MB, has a bounded depth/key count, declares its sequence and total, and has a SHA-256 recorded in the run index. The game uses its safe default JSON parser limits; it does not enable unlimited parsing.

Writers use this protocol:

1. Write a temporary file ending in `.partial.json` inside Garry's Mod (desktop writers may use an equivalent private `.partial` name).
2. Calculate SHA-256 of its bytes.
3. Rename it to its final chunk name.
4. Repeat for all chunks.
5. Write and atomically rename `complete.ready.json` last.

Consumers ignore runs without `complete.ready.json`, validate sizes/hashes before parsing, and preserve rejected input for diagnosis without partially applying it. The `.json` suffix is part of the protocol because Garry's Mod's DATA sandbox refuses files ending in `.partial` or `.ready`; this was verified in the local engine smoke test.

SHA-256 here detects corruption and binds chunks to a run. It is not DRM or a claim of authenticity.

### 11.3 Path and operation allowlists

Accepted virtual roots are `models/`, `materials/`, `sound/`, `lua/`, and `maps/`, with an extension allowlist per test kind. Paths are lowercase-normalized with forward slashes before matching.

Reject absolute paths, drive prefixes, UNC paths, `..`, null bytes, overlong components, ambiguous separators, unsafe Unicode, and unexpected extensions. WPF path resolution uses `Path.GetFullPath` followed by a containment check against the intended root.

Test kinds are closed enums such as `model_structure`, `material_resolve`, and `model_render`. Unknown kinds reject the plan. There are no fields for executable, command, Lua source, URL, or arguments.

The implementation must not use `RunString`, `CompileString`, `os.execute`, arbitrary console commands, or downloaded native modules.

### 11.4 Network boundaries

Garry's Mod `net` traffic carries only authorization, bounded progress, test identifiers, and small structured results. It does not carry screenshots or complete plans.

Every message has fixed read/write order, explicit maximum lengths, session ID, expiry, rate limiting, and a fresh server-side permission check. Replayed, oversized, malformed, unauthorized, or expired messages are discarded and logged without disconnecting unrelated users.

## 12. Failure behavior

| Condition | Required behavior |
| --- | --- |
| Partial file | Ignore until complete |
| Hash mismatch | Reject as corrupt |
| Missing chunk | `INCOMPLETE` |
| Unknown schema | Reject with update guidance |
| Oversized input | Reject before JSON parsing |
| Unsafe path or operation | Reject the complete plan |
| Screenshot capture failure | Retry once, then `WARNING` |
| Client disconnect | Save bounded progress as `INCOMPLETE` |
| Map change | Stop safely and persist incomplete state |
| WPF closes | Companion continues and saves normally |
| Game closes | WPF retains the pending plan |
| Duplicate run ID | Preserve prior result; do not overwrite |
| Profile invalid/stale | Do not use it; ordinary optimization remains available |

## 13. Testing strategy

### 13.1 Automated tests

Cross-language fixtures cover valid profiles/plans/results and malformed variants: truncation, reordered/missing chunks, hash mismatch, version mismatch, unknown fields, excessive depth/keys, invalid numbers, duplicate IDs, casing/Unicode, and unsafe paths.

WPF tests cover detection, safe install/update/remove, unknown-folder refusal, profile import, result merge, incompatible versions, and no-Companion behavior.

Maximum v2 tests prove:

- no profile preserves current behavior;
- an empty/disabled profile is equivalent to no profile;
- importance never reduces risk;
- profile evidence cannot create a family gate;
- only the failing region is recovered;
- one lighter regional recovery remains the maximum;
- Models normal and Compress are unchanged.

Security tests cover traversal, absolute/UNC paths, symlinks/junctions on the WPF side, size mismatches, extension smuggling, unknown test kinds, unauthorized/replayed/rate-limited net messages, and session expiry.

### 13.2 Real-game matrix

- Windows single-player
- Windows listen server
- Windows dedicated server
- Linux dedicated server
- Remote server plus administrator client
- Multiple UI resolutions/scales
- Folder addon and GMA/Workshop content
- Original and optimized content mounted together
- Missing DX90/VVD/PHY/material cases
- Invalid skin/bodygroup and Lua-error cases
- Cancellation, disconnect, map change, and headless validation

## 14. Benchmark and acceptance gates

Use 5-10 detailed families from `lvs_cars_pack`. Record a real profile while driving and inspecting representative wheels, tires, glass, lights, steering wheels, exhausts, interiors, and bodywork.

Compare Maximum v2 without the Companion against Maximum v2 with the approved profile. Measure:

- output size excluding DX80;
- total processing time;
- triangles and vertices;
- number of regional recoveries;
- silhouette, normal, UV, skinning, and material metrics;
- in-game screenshots;
- compiled MDL integrity;
- profiler overhead and validation throughput.

Initial acceptance gates:

- no measurable no-profile regression;
- no new full-family candidates;
- no more than 10% Maximum processing-time overhead from profile use;
- target no more than 5% representative output-size increase from profile protection; exceeding it requires evidence and explicit release approval;
- no visual regression in observed regions relative to Maximum v2 without a profile;
- no family-wide preservation caused by the profile;
- profiler average below 0.5 ms on the benchmark system;
- every cap/truncation appears explicitly in the report.

An output-size increase over 5% requires an evidence-backed report identifying the protected regions and cannot be presented as an unexplained quality improvement.

## 15. Delivery and commercial packaging

The official application build remains:

```powershell
.\build_release_wpf.ps1
```

A separate commercial packaging script will invoke the official build and assemble:

```text
dist-commercial/
`-- SourceAddonOptimizer-GmodStore-<version>.zip
```

Package layout:

```text
SourceAddonOptimizer/
|-- Desktop/
|-- GarrysModAddon/
|   `-- sourceaddonoptimizer_companion/
|-- Documentation/
|   |-- Installation.pdf
|   |-- QuickStart.pdf
|   `-- Troubleshooting.pdf
|-- THIRD-PARTY-LICENSES/
|-- CHANGELOG.md
|-- AI-DISCLOSURE.md
`-- checksums.txt
```

The commercial build fails on development logs, `work/`, benchmarks, archives such as `.rar`, caches, credentials, local absolute paths, undeclared binaries, or third-party files without documented redistribution status.

Pre-sale gates:

1. Written GmodStore confirmation for the addon-plus-Windows-application package.
2. Third-party license/redistribution audit for every bundled tool and library.
3. Visible and accurate AI Disclosure.
4. English installation, support, media, and configuration documentation.
5. Real screenshots/video and factual benchmark claims.
6. A decision on executable code signing.
7. Migration from the out-of-support .NET 6 target, or another documented supported-runtime release strategy.

Framework migration and code signing do not block the Companion prototype, but a commercial release is not final until the pre-sale gates are resolved.

## 16. Implementation order

1. Add schema definitions, validators, chunk protocol, and malicious fixtures.
2. Implement the minimal Lua loader, permissions, storage, and contract reader/writer.
3. Implement bounded runtime profiling and standalone in-game audit summary.
4. Add the WPF Companion tab and safe installer/import flow.
5. Add the worker runtime-importance adapter behind an explicit option.
6. Implement structural validation and result import.
7. Implement the bounded visual gallery and screenshot result import.
8. Run real-game matrix tests and the LVS benchmark.
9. Tune only with measured evidence; preserve the search and family-gate invariants.
10. Assemble the commercial package after all pre-sale gates pass.

## 17. Definition of done

The feature is complete only when:

- automated contract, security, WPF, worker, and regression tests pass;
- the addon loads without Lua errors in the supported game matrix;
- profiling meets its measured performance budget;
- the WPF behaves normally when the addon is absent;
- an optimized addon has no Companion dependency;
- DX90 is validated and DX80 remains excluded;
- structural and visual results import correctly;
- real benchmark numbers and screenshots support every quality/performance claim;
- safe installation and removal are verified;
- release documentation and third-party notices are complete;
- the commercial archive contains no development debris;
- the GmodStore package format has written approval.
