# LVS R&D Task 3 report

## Outcome

The DX90-only candidate is implemented as an explicitly optional Garry's Mod dynamic-runtime policy.
It is not a default, a Source-engine-wide policy, or a release-policy change. Calls without the exact
`gmod_dynamic_runtime` target fail closed. Static props and map/VRAD use are deliberately outside the
allowed target because their DX80 dependency has not been disproved.

Runtime evidence remains **pending**. A DX90-only model decompiled successfully with Crowbar, but that
is static-tool evidence only. The prior real Garry's Mod e46 baseline load passed with the normal
sidecar set; the DX90-only textmode attempt created a visible window and did not establish the required
bodygroup, animation, physics and dynamic-load capabilities. No fake green status was recorded.

## Policy and integrity contract

`maximum_optimizer/dx90_optional.py` provides the fail-closed builder, validator, experiment recorder
and runtime-evidence schema. The builder:

- requires `.mdl`, `.vvd`, `.dx90.vtx` and exactly one `.dx80.vtx` for the exact model stem;
- verifies the declared source set, sizes and SHA-256 values before creating output;
- copies every declared sidecar except the exact `.dx80.vtx` into an immutable candidate root;
- re-enumerates the candidate, rejects missing/extra/reparse artifacts and verifies every copied hash;
- proves `source_bytes - candidate_bytes == saved_dx80_bytes` exactly;
- removes partial output on any failure.

The `dx90_optional` benchmark lane is accepted by benchmark records but is not a baseline/geometry
lane. Its records contain only the delivered DX90 candidate bytes; the omitted DX80 saving is reported
separately in experiment accounting. Existing baseline lanes and the default product/release policy
are unchanged.

## Real pressure-set candidate

The ignored immutable candidate tree is under `.superpowers/lvs-task3-dx90/candidates`. The committed
portable record is `benchmarks/lvs_models/dx90_optional.json`. It covers the five fixed pressure
families and contains 20 copied artifacts, zero DX80 files, and exact original hashes for all copied
artifacts.

| Metric | Bytes |
| --- | ---: |
| Original pressure sidecars | 97,941,514 |
| DX90-only pressure candidate | 81,553,621 |
| Exact omitted DX80 saving | 16,387,893 |

For context only, the complete original LVS `models/` tree contains 163 `.dx80.vtx` files totaling
96,743,775 bytes out of 575,666,722 bytes (16.8%). This full-tree inventory is not represented as a
geometry saving and was not promoted as a universal omission candidate.

## Runtime evidence schema

`RuntimeEvidence` has strict `pending` and `proven` states. Pending evidence requires a reason and a
non-empty manual procedure, and rejects proof fields. Proven evidence requires:

- exact tool name `Garry's Mod`;
- lowercase SHA-256 for the executable and complete runtime log;
- a non-empty bounded build identifier;
- exactly `dynamic_model_load`, `bodygroups`, `animation` and `physics` capabilities.

Crowbar or any other static tool is rejected as proof. The committed evidence is pending and includes
the exact five-step manual procedure: install the immutable candidates without original fallback,
launch the identified Garry's Mod build with logging, exercise bodygroups/skins and animations, verify
physics plus console load diagnostics across the five representatives, then hash both executable and
complete log before constructing proven evidence. It explicitly excludes static-prop/VRAD use.

## TDD evidence

Initial RED failed because `maximum_optimizer.dx90_optional` did not exist. The experiment aggregation
RED then failed because `build_dx90_optional_experiment` did not exist. The canonical immutable writer
RED failed because `write_dx90_optional_experiment` did not exist. The lane-separation RED showed
`dx90_optional` incorrectly present in `BASELINE_LANES`; the follow-up split introduced
`BENCHMARK_LANES` while preserving the baseline tuple.

Focused GREEN after the first policy implementation ran 28 tests with two Windows symlink-privilege
skips. The added deterministic set/hash/tamper checks remain active even where real symlink creation
returns WinError 1314.

## Manual proof procedure

The authoritative procedure is serialized verbatim in `benchmarks/lvs_models/dx90_optional.json`.
It must be performed in a normal, user-visible Garry's Mod session because safe hidden automation was
not available. A static decompile, process start, window creation, or normal-sidecar baseline load is
insufficient. Until the resulting executable/build/log hashes and all four runtime capabilities are
recorded, status must remain `pending` and the policy must remain opt-in only.

## Verification

- Focused policy plus benchmark suite: 29 tests passed, 2 environment symlink skips.
- Strict canonical JSON, record/evidence re-import, script provenance and all five candidate
  manifests/hashes: passed.
- Python byte-compilation: passed.
- Full discovery: 339 tests passed, 12 environment skips.
- `git diff --check`: passed (Git emitted only the repository's existing LF/CRLF conversion notice).
