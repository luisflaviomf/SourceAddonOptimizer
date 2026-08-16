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
