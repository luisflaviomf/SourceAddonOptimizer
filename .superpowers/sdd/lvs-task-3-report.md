# LVS R&D Task 3 report

## Outcome

The DX90-only candidate is implemented as an explicitly optional Garry's Mod dynamic-runtime policy.
It is not a default, a Source-engine-wide policy, or a release-policy change. Calls without the exact
`gmod_dynamic_runtime` target fail closed. Static props and map/VRAD use are deliberately outside the
allowed target because their DX80 dependency has not been disproved.

Runtime evidence remains **pending**. A DX90-only model decompiled successfully with Crowbar, but that
is static-tool evidence only. The prior real Garry's Mod e46 baseline load passed with the normal
sidecar set; the DX90-only textmode attempt created a visible window and did not establish the required
dynamic-load, rendering, bodygroup/skin, animation, physics and damage capabilities. No fake green
status was recorded.

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

`RuntimeEvidence` has self-validating strict `pending` and `proven` states. Direct dataclass
construction cannot bypass `__post_init__`; every experiment also forces a canonical dict roundtrip.
Every state is bound to the exact corpus, ordered family IDs and candidate-manifest SHA-256. Pending
evidence requires a reason and a non-empty procedure covering rendering and damage, and rejects proof
fields. Proven evidence requires:

- exact engine name `Garry's Mod`;
- lowercase SHA-256 for the engine executable, installed-build manifest and complete runtime log;
- a non-empty bounded build identifier;
- exactly `dynamic_model_load`, `rendering`, `bodygroups_skins`, `animation`, `physics` and `damage`.

Crowbar or any other static tool is rejected as proof. The committed evidence is pending and includes
the exact five-step manual procedure: install the immutable candidates without original fallback,
launch the identified Garry's Mod build with logging, exercise bodygroups/skins and animations, verify
rendering, physics, damage and console load diagnostics across the five representatives, then hash the
executable, installed-build manifest and complete log before constructing bound proven evidence. It
explicitly excludes static-prop/VRAD use.

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
insufficient. Until the resulting executable/build/log hashes and all six runtime capabilities are
recorded, status must remain `pending` and the policy must remain opt-in only.

## Review hardening

The follow-up review added a strict portable `candidate_manifest` to the experiment JSON. Each family
now records its compiled stem, every kept artifact path/size/SHA-256, the exact omitted DX80
path/size/SHA-256, per-family byte accounting and a source-manifest digest. The top-level digest covers
the ordered corpus/family manifest. The strict parser rejects unknown/missing keys, scalar drift,
wrong stems, duplicates, non-exact omission, missing required sidecars, byte/hash/digest changes,
record/summary drift and unrelated evidence.

The runtime-evidence digest and candidate-manifest digest are both embedded in each record's
provenance settings and therefore in its cache key. Parser re-computation proves that evidence from a
different corpus, family order or candidate cannot be reused. Static Crowbar evidence remains
structurally incapable of producing `proven`.

Review-fix RED failed first because the manifest/binding APIs did not exist. Focused GREEN then ran 30
tests with two Windows symlink-privilege skips.

## Verification

- Focused policy plus benchmark suite after review hardening: 30 tests passed, 2 environment symlink skips.
- Strict canonical JSON, record/evidence re-import, script provenance and all five candidate
  manifests/hashes: passed.
- Python byte-compilation: passed.
- Full discovery after review hardening: 340 tests passed, 12 environment skips.
- `git diff --check`: passed (Git emitted only the repository's existing LF/CRLF conversion notice).
