# Companion WPF tab smoke

Date: 2026-08-16

Build under test: WPF Release (`net6.0-windows`)

## Executable smoke

- Launched the Release executable and waited for the real WPF window through Windows UI Automation.
- Opened all seven tabs: Compress, Models, Descompactar addons, Juntar addons, Optimize Maps, Pipeline, and Companion.
- Confirmed the Companion tab exposes the four approved cards: installation/compatibility, profile import, validation transfer/results, and quick start.
- Confirmed the locally detected Garry's Mod installation presented `Not installed`, enabled `Install Companion`, and kept Update/Remove disabled.
- Confirmed the tab remains independent: navigating it does not disable or alter the existing tabs.
- Captured and visually inspected the 1280×720 window. The two-column layout, typography, buttons, status text, and vertical scrolling rendered without clipping or overlap.

## Root-state coverage

- A machine with no Garry's Mod installation was not available because the benchmark workstation has a valid Steam installation. The no-root presentation and disabled install actions are covered by `CompanionContext` and path-policy contract tests rather than a false manual claim.
- Fresh temporary game roots, owned install/update, unknown destinations, incompatible ownership, cancellation-safe state, concurrent callers, and selective uninstall are exercised by `tests/wpf_companion_contract`.
- The real WPF window was not used to install into the workstation's actual Garry's Mod folder; installation remains an explicit user action.

## Regression caught by the smoke

The first launch exposed a runtime-only WPF binding failure: a read-only profile path was receiving the default TwoWay `TextBox.Text` binding. The binding is now explicitly OneWay and has a regression test in `tests/companion_addon/test_wpf_companion_xaml.py`.

## Runtime-profile import and mode gating

- Rebuilt and launched the real Release executable after adding the shared Models/Pipeline profile card.
- Selected Models Normal, Fidelity, and Maximum through Windows UI Automation. With no validated matching profile, `Use with Models Maximum v2` remained disabled in all three modes. Contract tests separately prove that an eligible profile enables the control only in Maximum and that leaving Maximum clears a checked opt-in.
- Opened Pipeline and confirmed its shared `Use with Models Maximum v2` control was present and disabled under the same state.
- Selected all four existing Compress presets (`Padrao`, `Magick`, `Maximum`, and `Magick+`) in the real window. All remained selectable; no Companion control exists in Compress.
- The profile reader/service contract tests cover valid import, stale and zero-match ineligibility, multi-profile merge, case-insensitive addon intersection with display casing preserved, external-asset filtering, closed fields, pre-parse limits, hashes, versions, finite numbers, duplicate IDs, and incomplete bundles.
