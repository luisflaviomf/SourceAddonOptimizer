# Optimization Companion real-game matrix

This matrix separates locally proven behavior from environments that require a
different host. A row is never promoted from `NOT EXECUTED` by inference.

| Environment | Profile | Structural | Visual | Status/evidence |
|---|---:|---:|---:|---|
| Windows single-player, folder addons | Yes | Yes | Yes | PASS — Garry's Mod 2026.08.13 (10119), real profile run plus prior 1280×720 and 1920×1080 gallery runs |
| Windows listen server | Pending | Pending | Pending | NOT EXECUTED separately from single-player |
| Windows dedicated server | Pending | Pending | N/A headless | NOT EXECUTED — dedicated binary not present in the installed client tree |
| Linux dedicated server | Pending | Pending | N/A headless | NOT EXECUTED — no approved Linux Garry's Mod server host is available locally |
| Remote server + admin client | Pending | Pending | Pending | NOT EXECUTED — requires a second reachable host/client and admin identity |

## Scenario matrix

| Scenario | Status |
|---|---|
| Folder-installed Companion and content | PASS in Windows single-player |
| GMA-installed Companion/content | Pending |
| Original and optimized addons mounted together | Pending conflict test; normal use must mount only the intended output |
| Missing model sidecars/materials/states | PASS in automated structural contract tests; real-game fault injection pending |
| Cancellation | PASS in automated WPF/worker tests; real-game dashboard cancellation pending |
| Client disconnect | PASS in session cleanup contract tests; multi-host real-game run pending |
| Map change | PASS in bounded session/storage contract tests; real-game run pending |
| Headless structural-only validation | Automated contract PASS; Windows/Linux dedicated runs pending |

The incomplete rows are release limitations, not hidden passes. The profile-
assisted optimizer remains optional, and the WPF/worker still runs without the
Companion or a profile.

Environment setup follows the current Facepunch documentation for
[Windows/Linux dedicated servers](https://wiki.facepunch.com/gmod/Downloading_a_Dedicated_Server)
and the shipped `gmad` workflow in
[Workshop Addon Creation](https://wiki.facepunch.com/gmod/Workshop_Addon_Creation).
