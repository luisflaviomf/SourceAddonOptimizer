# Task 2 report

## Status

Partial implementation. The candidate filesystem/export API, CLI protocol, path/exclusion gates,
input pinning, option selection, blob deduplication, measured loose-artifact overhead gate, and
whole-candidate fallback are implemented. This is **not ready for engine validation or activation**.

## TDD evidence

RED command:

`python -m pytest -q tests/test_exact_distribution_builder.py tests/test_exact_distribution_runtime.py`

Result: collection failed with two expected `ModuleNotFoundError` errors for the not-yet-created
`exact_distribution.builder` and `exact_distribution.runtime` modules.

GREEN/full focused command:

`python -m pytest -q tests/test_exact_distribution_builder.py tests/test_exact_distribution_runtime.py tests/test_exact_distribution_codecs.py`

Result: `13 passed in 0.33s`.

CLI fixture command executed through `exact_distribution.cli.main`; result emitted all three required
protocol lines and returned zero. Offline status was `OFFLINE_PENDING_GMOD`.

## Interfaces and report schema

- `build_candidate(source: Path, output: Path, work: Path, engine_root: Path, progress=None) -> dict`
- `render_runtime(contract: dict) -> bytes`
- `cli.main(argv: list[str] | None = None) -> int`
- Report schema: `exact-distribution-report/v1`; fields include status, absolute candidate,
  content ID, source/candidate byte totals, encoded/native/unique-blob counts, excluded paths/count/bytes,
  fallback reason, byte-exact and quality flags, installed flag, and returned absolute report path.

## Changed files

- `exact_distribution/builder.py`
- `exact_distribution/runtime.py`
- `exact_distribution/cli.py`
- `tests/test_exact_distribution_builder.py`
- `tests/test_exact_distribution_runtime.py`
- this report

## Limitations and self-review

- The generated Lua is a fail-closed product wrapper around a required host bootstrap; it does not yet
  consume the frozen fullstack decoders itself. Therefore the requested realistic Lua cold/warm/corrupt,
  two-candidate execution tests and actual frozen VVD decoder consumption remain missing.
- Support blobs are currently loose `.dat` files, not frozen-writer support GMAs. Engine-overlap scanning,
  cache GMA headers, mounted pre/post identity checks, sharding limits, and unknown external engine mount
  conflict handling remain missing.
- The builder does not catch decode errors as a native fallback, but it currently catches adapter encode
  `ValueError` as unsupported. A stricter distinction between size/unsupported preflight and independent
  verification failure is still needed.
- Input drift is checked during copy and at close, but the supplied test does not induce a mid-build race.
- Windows cannot create two same-directory names differing only by case, so the case-fold collision branch
  is implemented but not exercised by this focused Windows test suite.
- No GMod/game launch, install, baseline promotion, or source mutation was performed.
