# Companion WPF result-import smoke test

Date: 2026-08-16 (America/Sao_Paulo)

## Production reader and real evidence

The C# contract harness links the same `CompanionBundleReader`, `CompanionResultService`, path policy, and view-model context compiled into the WPF application. It imported the real Garry's Mod visual bundle:

```text
run: 10800000-0000-4000-8000-000000000001
plan: 0370e893b6849125a06afc2f71ad768f1e364cd6489c9f43bd1b4d16eb7a5532
result: INCOMPLETE (expected: structural channel not imported)
resources: PASS 1 / WARNING 0 / FAILED 0 / INCOMPLETE 0
screenshots: 4 verified
visual wall time: 0.304 s
```

The importer resolved each image beneath the selected `sourceaddonoptimizer/screenshots/<run-id>` root and rechecked its declared byte count and SHA-256 before exposing the full path.

## Hostile and merge cases

The same executable test run verified:

- structural-only PASS becomes `WARNING: visual validation not run`;
- visual-only interrupted evidence remains `INCOMPLETE`;
- structural and visual bundles merge only for identical run ID and plan SHA-256;
- conflicting plan hashes stay in separate reports with an explicit diagnostic;
- duplicate imports remain preserved as separate sources;
- traversal in screenshot paths is rejected;
- screenshot hash mismatch is rejected;
- a declared `PASS` that understates a failed resource is rejected;
- exact precedence is `FAILED`, `WARNING`, `INCOMPLETE`, `PASS`.

Rejected inputs remain on disk and their path/error is shown diagnostically; their untrusted partial payload is never added to the result list.

## WPF window smoke

The Release WPF executable was launched and inspected through Windows UI Automation. The `Companion` tab loaded without an unhandled exception and exposed `Import Results`, `Copy Summary`, `Export Summary`, and `Open Screenshot`. The process launched for this smoke was then stopped.

The build completed with zero errors. Existing dependency-audit warnings for the out-of-support .NET 6 target and the current Magick.NET version remain visible release risks; this result-import change did not suppress them.
