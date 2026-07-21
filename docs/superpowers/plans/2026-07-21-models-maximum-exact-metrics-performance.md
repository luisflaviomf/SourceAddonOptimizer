# Models Maximum Exact Metrics Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the exact silhouette KD-tree queries with an exact linear squared Euclidean distance field, preserving every Maximum v2 quality decision while reducing cold-run time.

**Architecture:** Keep rasterization, four-neighbour boundary extraction, views, resolution, samples, p95 calculation, and budgets unchanged. Store an exact squared-distance field in each prepared silhouette and use direct indexed lookups for both directions of the boundary comparison.

**Tech Stack:** Python 3.11 standard library, Pillow, `unittest`, existing Maximum Adaptive v2 benchmark harness, Blender 5.0, StudioMDL, PyInstaller, WPF/.NET 6.

## Global Constraints

- Do not change the calibrated profile or SHA-256 `6029d5a8c80ec2b7caf6e50e82035b3aeb21103d6609c1d7345a3a3ff9e6c9d9`.
- Keep 512 samples, eight canonical views, and 256px silhouettes.
- Preserve all 19 Pontiac region representations, selected triangle counts, failed gates, and targeted-render decisions.
- Preserve 13,530 final Pontiac triangles, final comparable bytes, DX90 integrity, and zero DX80 output.
- Promote only at `adaptive-simplification <= 20.338s`, at least 25% faster than 27.117s.
- Do not modify WPF behavior or `gui/`.
- Do not use subagents.

---

### Task 1: Exact squared Euclidean distance field

**Files:**
- Modify: `tests/maximum_optimizer/test_metrics.py`
- Modify: `maximum_optimizer/metrics.py:79-88,399-478`

**Interfaces:**
- Produces: `_squared_euclidean_distance_field(width: int, height: int, points: Sequence[tuple[int, int]]) -> tuple[int, ...]`
- Produces: `_SilhouetteReference.boundary_distance_squared: tuple[int, ...]`
- Consumes: the existing ordered four-neighbour boundary point tuples.

- [ ] **Step 1: Write the failing distance-field test**

Add a brute-force oracle and a test covering asymmetric, edge, and single-point grids:

```python
def _brute_squared_distance(width, height, points):
    return tuple(
        min((x - px) ** 2 + (y - py) ** 2 for px, py in points)
        for y in range(height)
        for x in range(width)
    )

def test_squared_distance_field_matches_exact_brute_force(self):
    for width, height, points in (
        (7, 5, ((0, 0), (5, 1), (2, 4))),
        (4, 6, ((3, 5),)),
        (9, 3, ((0, 1), (8, 1))),
    ):
        self.assertEqual(
            metrics_module._squared_euclidean_distance_field(width, height, points),
            _brute_squared_distance(width, height, points),
        )
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m unittest tests.maximum_optimizer.test_metrics.RegionMetricsTests.test_squared_distance_field_matches_exact_brute_force -v`

Expected: `ERROR` because `_squared_euclidean_distance_field` does not exist.

- [ ] **Step 3: Implement the exact separable transform**

Implement a private one-dimensional lower-envelope transform and apply it to rows then columns. Validate positive dimensions, a non-empty point set, and in-range coordinates. Use integer squared costs and return a row-major immutable tuple.

```python
def _squared_distance_transform_1d(values: Sequence[int], infinity: int) -> list[int]:
    finite = [index for index, value in enumerate(values) if value < infinity]
    if not finite:
        return [infinity] * len(values)
    sites = [0] * len(finite)
    intersections = [0.0] * (len(finite) + 1)
    envelope = 0
    sites[0] = finite[0]
    intersections[0] = float("-inf")
    intersections[1] = float("inf")
    for coordinate in finite[1:]:
        site = sites[envelope]
        crossing = (
            (values[coordinate] + coordinate * coordinate)
            - (values[site] + site * site)
        ) / (2 * (coordinate - site))
        while crossing <= intersections[envelope]:
            envelope -= 1
            site = sites[envelope]
            crossing = (
                (values[coordinate] + coordinate * coordinate)
                - (values[site] + site * site)
            ) / (2 * (coordinate - site))
        envelope += 1
        sites[envelope] = coordinate
        intersections[envelope] = crossing
        intersections[envelope + 1] = float("inf")
    result = [infinity] * len(values)
    envelope = 0
    for coordinate in range(len(values)):
        while intersections[envelope + 1] < coordinate:
            envelope += 1
        delta = coordinate - sites[envelope]
        result[coordinate] = values[sites[envelope]] + delta * delta
    return result

def _squared_euclidean_distance_field(
    width: int,
    height: int,
    points: Sequence[tuple[int, int]],
) -> tuple[int, ...]:
    if width <= 0 or height <= 0:
        raise ValueError("distance-field dimensions must be positive")
    if not points:
        raise ValueError("distance-field points must not be empty")
    if any(x < 0 or x >= width or y < 0 or y >= height for x, y in points):
        raise ValueError("distance-field point is outside the grid")
    infinity = width * width + height * height + 1
    grid = [infinity] * (width * height)
    for x, y in points:
        grid[y * width + x] = 0
    for y in range(height):
        start = y * width
        grid[start : start + width] = _squared_distance_transform_1d(
            grid[start : start + width], infinity
        )
    for x in range(width):
        column = _squared_distance_transform_1d(
            [grid[y * width + x] for y in range(height)], infinity
        )
        for y, value in enumerate(column):
            grid[y * width + x] = value
    return tuple(grid)
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python -m unittest tests.maximum_optimizer.test_metrics.RegionMetricsTests.test_squared_distance_field_matches_exact_brute_force -v`

Expected: one passing test.

- [ ] **Step 5: Add failing validation tests**

Test zero dimensions, empty points, and an out-of-range point with `assertRaises(ValueError)`.

- [ ] **Step 6: Run validation tests and verify RED, then implement the minimal validation**

Run: `python -m unittest tests.maximum_optimizer.test_metrics.RegionMetricsTests.test_squared_distance_field_rejects_invalid_contract -v`

Expected before implementation: failure; expected after minimal validation: pass.

- [ ] **Step 7: Commit the independent distance-field primitive**

```powershell
git add maximum_optimizer/metrics.py tests/maximum_optimizer/test_metrics.py
git commit -m "perf(maximum): add exact silhouette distance field"
```

### Task 2: Replace silhouette KD queries without changing metrics

**Files:**
- Modify: `tests/maximum_optimizer/test_metrics.py`
- Modify: `maximum_optimizer/metrics.py:79-88,399-478`

**Interfaces:**
- Consumes: `_squared_euclidean_distance_field(...)` from Task 1.
- Preserves: `prepare_region_reference(...)`, `measure_region_prepared(...)`, and `RegionMetrics` public contracts.

- [ ] **Step 1: Freeze the current silhouette metric output in a failing integration test**

Use `make_disc(64)` and `make_disc(6)` with the existing `CONTRACT`. Assert the frozen current values `silhouette_iou_loss == 0.17125728716750455` and `silhouette_boundary_p95_px == 15.0`. Patch `_kd_tree` with `side_effect=AssertionError` so the test fails until the new path no longer calls it.

- [ ] **Step 2: Run the integration test and verify RED**

Run: `python -m unittest tests.maximum_optimizer.test_metrics.RegionMetricsTests.test_silhouette_field_preserves_exact_metric_without_kd_queries -v`

Expected: failure from the patched `_kd_tree` call.

- [ ] **Step 3: Replace KD storage and lookup**

Change `_SilhouetteReference` to store `boundary_distance_squared`. In `_prepare_silhouettes`, build the original field once. In `_silhouette_metrics_prepared`, build the candidate field once and perform row-major direct lookups in both directions before the existing `sqrt` and p95 operations. Remove `_KdNode`, `_kd_tree`, and `_kd_distance` after all callers are gone.

- [ ] **Step 4: Run the focused integration and full metrics tests**

Run:

```powershell
python -m unittest tests.maximum_optimizer.test_metrics -v
python -m unittest discover -s tests/maximum_optimizer -p "test_*.py" -v
```

Expected: exact frozen values pass; all 80 Maximum tests pass.

- [ ] **Step 5: Commit the exact silhouette replacement**

```powershell
git add maximum_optimizer/metrics.py tests/maximum_optimizer/test_metrics.py
git commit -m "perf(maximum): replace silhouette kd queries with exact edt"
```

### Task 3: Real Pontiac equivalence and timing gate

**Files:**
- Read: `C:\gaco-max-v2-bench\holdout\pontiac_transam_wheel\maximum-adaptive-v2\result.json`
- Read: `C:\gaco-max-v2-bench\holdout\pontiac_transam_wheel\maximum-adaptive-v2\work\logs\maximum_adaptive_report.json`
- Generate outside repository: `C:\gaco-max-v2-perf-pontiac-exact\`

**Interfaces:**
- Consumes: the unchanged public worker CLI and frozen profile.
- Produces: a cold-cache result and `maximum_adaptive_report.json` comparable to the frozen baseline.

- [ ] **Step 1: Run one cold isolated Maximum benchmark**

Invoke `build_optimized_addon.py` against the frozen Pontiac source with a new suffix and work root, keeping the exact Blender, StudioMDL, jobs, framework-resolver, skin, strict, and Maximum arguments from the frozen `run-manifest.json`.

- [ ] **Step 2: Compare semantic output exactly**

Compare baseline and candidate reports after excluding timing/path fields. Require the same region keys, representations, original/normal/selected triangle counts, selected vertices, targeted-render flags, failed gates, reasons, profile hash, final triangle count, render count, compile count, and simplifier evaluation count.

- [ ] **Step 3: Verify compiled output and performance**

Require:

```text
final_triangles = 13530
targeted_renders = 1
studiomdl_compiles = 1
integrity_failures = []
final_dx80_bytes = 0
adaptive-simplification <= 20.338 seconds
total wall < 56.012 seconds
```

If any semantic condition changes, revert the implementation. If semantics match but timing misses, retain the primitive only if unused and move the active path to a separate native-bridge experiment.

- [ ] **Step 4: Run full repository tests**

Run:

```powershell
python -m unittest discover -s tests -t . -p "test_*.py"
dotnet run --project tests/wpf_optimizer_contract/GmodAddonOptimizer.ContractTests.csproj -c Release --no-restore
dotnet build GmodAddonCompressor-master/GmodAddonCompressor.sln -c Release --no-restore -v minimal
```

Expected: 82 or more Python tests pass, WPF contract passes, WPF build has zero errors.

- [ ] **Step 5: Record and commit the verified result**

Add a short measured-performance section to `benchmarks/lvs_models_adaptive/results-2026-07-21.md`, including baseline and candidate stage times, exact-output verdict, and remaining bottleneck.

### Task 4: Rebuild worker and official WPF release

**Files:**
- Regenerate: `dist/GModAddonOptimizerWorker/`
- Modify generated resource: `GmodAddonCompressor-master/GmodAddonCompressor/Resources/SourceAddonOptimizer.win-x64.zip`
- Regenerate publish output: `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/`

**Interfaces:**
- Consumes: the verified exact metric implementation.
- Produces: the official WPF publish folder with matching embedded worker.

- [ ] **Step 1: Rebuild and smoke-test the PyInstaller worker**

Run:

```powershell
pyinstaller --noconfirm --clean pyinstaller/worker.spec
.\dist\GModAddonOptimizerWorker\GModAddonOptimizerWorker.exe --help
```

Expected: build exit 0 and help includes `{normal,fidelity,maximum}`.

- [ ] **Step 2: Generate the official release**

Run: `.\build_release_wpf.ps1`

Expected: exit 0 and published `GmodAddonOptimizer.exe` exists.

- [ ] **Step 3: Verify the embedded worker and published application**

Require the embedded worker SHA-256 to match the rebuilt worker, required native/profile/runtime entries to exist, and the published WPF process to start and remain responsive.

- [ ] **Step 4: Commit the final release resource**

```powershell
git add benchmarks/lvs_models_adaptive/results-2026-07-21.md GmodAddonCompressor-master/GmodAddonCompressor/Resources/SourceAddonOptimizer.win-x64.zip
git commit -m "build(release): publish faster exact Maximum metrics"
```
