# Models Maximum Adaptive v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. This project explicitly forbids subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the experimental whole-family Models Maximum with a measured regional overlay on Models Normal that compresses low-risk geometry aggressively, restores only failed regions, validates DX90 output, and ships through the WPF product.

**Architecture:** The existing decompile and Blender Models Normal path produces an immutable original tree and a ratio-0.35 normal seed. A focused Python package maps SMD/QC regions, classifies risk, invokes a pinned meshoptimizer bridge, validates candidates in memory, performs at most three simplifier evaluations per failed region, composes accepted regions, and enters the existing compile/package flow. WPF continues to invoke the same worker and Pipeline runner with `--optimizer-mode maximum`.

**Tech Stack:** Python 3.11 standard library and Pillow, SMD/QC/Source v48 formats, meshoptimizer v1.2 at commit `9d9890c73011d75920af614485296d1e03e95448`, C++17/MSVC x64, Blender 5.0, StudioMDL/Crowbar, WPF/.NET 6, PowerShell, `unittest`.

## Global Constraints

- Work in `C:\Users\luisf\Music\teste`; preserve unrelated dirty files and never modify `gui/`.
- The WPF product is `GmodAddonCompressor-master/GmodAddonCompressor/`; heavy processing remains in the Python worker.
- Normal and Fidelity behavior must remain unchanged unless `--optimizer-mode maximum` is selected.
- Maximum uses Models Normal ratio `0.35` as its internal seed; the mandatory fresh Normal comparison uses the WPF Safe ratio `0.75`.
- Region replacement requires deterministic QC occurrence, source SMD, material, and connected-component correspondence; ambiguity falls back locally to Normal.
- Never collapse across material, UV seam, hard-normal, open/non-manifold, bone-influence, flex/VTA, or QC occurrence boundaries.
- Non-rigid skinned regions use no-update simplification. Only static or single-bone weight-1 regions may use vertex update.
- A failed region may trigger at most three in-memory simplifier evaluations. Blender and StudioMDL are forbidden inside that candidate loop.
- Final Source limits are 65,536 vertices, 65,536 triangles, 128 used bones, and at most three positive bone influences per compiled vertex.
- Original and final reduction denominators always exclude `.dx80.vtx`; removed DX80 bytes are reported separately.
- The framework root is read-only resolver input and may not become a published dependency or introduce a new reference.
- The frozen benchmark corpus contains the five development and three holdout families named in the approved design.
- The official release command is `.\build_release_wpf.ps1`; the published executable must be `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`.

---

## File structure

The new `maximum_optimizer` package is deliberately smaller than the experimental branch:

- `contracts.py`: immutable region, risk, metric, decision, and report contracts.
- `profile.py` and `profiles/maximum-adaptive-v2.json`: strict profile loading and frozen threshold hash.
- `smd.py`: lossless-enough SMD parsing/serialization and Source three-weight export.
- `qc_graph.py`: render-mesh occurrence inventory without following external writes.
- `regions.py`: material/component partitioning, hard-boundary flags, and original/normal correspondence.
- `mesh_attributes.py`: wedge construction and discontinuity classification.
- `meshopt_bridge.py` plus `native/`: narrow, owned-output x64 meshoptimizer FFI.
- `risk.py`: continuous curvature, silhouette, UV, material, skinning, and visibility features.
- `metrics.py`: deterministic surface, normal, UV, silhouette, material-boundary, and pose metrics.
- `search.py` and `cache.py`: bounded regional candidate schedule and content-addressed results.
- `rendering.py`: targeted render escalation only.
- `composition.py`: immutable-tree regional composition.
- `compiled_validation.py` and `closure.py`: DX90/Source integrity and dependency-closure checks.
- `pipeline.py`: adaptive orchestration with injectable compile/render callbacks.
- `reporting.py`: atomic v2 report, progress protocol, and DX80-neutral accounting.
- `benchmarking.py` and `benchmarks/lvs_models_adaptive/`: frozen corpus, lane harness, panels, and promotion audit.

The existing integration points remain `build_optimized_addon.py`, `worker/worker_main.py`, the WPF optimizer runner/parser, settings/context, `MainWindow`, and the PyInstaller/release scripts.

### Task 1: Contracts, frozen profile, and DX80-neutral accounting

**Files:**
- Create: `maximum_optimizer/__init__.py`
- Create: `maximum_optimizer/contracts.py`
- Create: `maximum_optimizer/profile.py`
- Create: `maximum_optimizer/profiles/maximum-adaptive-v2.json`
- Create: `maximum_optimizer/reporting.py`
- Create: `tests/maximum_optimizer/__init__.py`
- Create: `tests/maximum_optimizer/test_contracts_profile_reporting.py`

**Interfaces:**
- Produces: `RegionKey`, `RiskFeatures`, `RegionBudget`, `RegionMetrics`, `RegionDecision`, `MaximumProfile`, `load_profile(path: Path) -> MaximumProfile`, and `comparable_model_bytes(root: Path) -> SizeAccounting`.
- Consumes: only Python standard-library types.

- [ ] **Step 1: Write failing profile and accounting tests**

```python
class ProfileAndAccountingTests(unittest.TestCase):
    def test_profile_is_hash_sealed_and_limits_are_strict(self):
        profile = load_profile(PROFILE)
        self.assertEqual(profile.version, "maximum-adaptive-v2")
        self.assertEqual(profile.max_simplifier_evaluations, 3)
        self.assertLess(profile.limits.normal_p95_degrees, 10.0)
        self.assertLessEqual(profile.limits.silhouette_boundary_p95_px, 1.5)
        self.assertEqual(profile.sha256, hashlib.sha256(PROFILE.read_bytes()).hexdigest())

    def test_dx80_is_never_counted_as_saving(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "a.mdl").write_bytes(b"m" * 10)
            (root / "a.vvd").write_bytes(b"v" * 20)
            (root / "a.dx90.vtx").write_bytes(b"9" * 30)
            (root / "a.dx80.vtx").write_bytes(b"8" * 40)
            size = comparable_model_bytes(root)
        self.assertEqual(size.comparable_bytes, 60)
        self.assertEqual(size.dx80_bytes, 40)
```

- [ ] **Step 2: Run tests and verify the package is absent**

Run: `python -m unittest tests.maximum_optimizer.test_contracts_profile_reporting -v`

Expected: `ModuleNotFoundError: No module named 'maximum_optimizer'`.

- [ ] **Step 3: Implement immutable contracts, strict JSON loading, and accounting**

```python
@dataclass(frozen=True)
class RegionKey:
    value: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"r-[0-9a-f]{64}", self.value) is None:
            raise ValueError("invalid region key")

@dataclass(frozen=True)
class RiskFeatures:
    curvature_p95_norm: float
    silhouette_fraction: float
    hard_boundary_density: float
    uv_seam_density: float
    material_semantic_risk: float
    skinning_risk: float
    visibility_confidence: float
    score: float
    target_ratio: float

@dataclass(frozen=True)
class RegionBudget:
    surface_p95: float
    surface_max: float
    normal_p95_degrees: float
    normal_max_degrees: float
    silhouette_iou_loss: float
    silhouette_boundary_p95_px: float
    uv_p95: float
    material_boundary_p95_px: float
    skinning_p95: float
    skinning_max: float

@dataclass(frozen=True)
class RegionMetrics:
    surface_p95: float
    surface_max: float
    normal_p95_degrees: float
    normal_max_degrees: float
    silhouette_iou_loss: float
    silhouette_boundary_p95_px: float
    uv_p95: float
    material_boundary_p95_px: float
    skinning_p95: float
    skinning_max: float

@dataclass(frozen=True)
class ValidationDecision:
    passed: bool
    failed_gates: tuple[str, ...]
    margin_fraction: float

@dataclass(frozen=True)
class RegionDecision:
    region_key: RegionKey
    representation: Literal["aggressive", "lighter", "normal", "original"]
    ratio: float
    evaluations: int
    validation: ValidationDecision

@dataclass(frozen=True)
class SizeAccounting:
    comparable_bytes: int
    dx80_bytes: int

@dataclass(frozen=True)
class MaximumProfile:
    schema: int
    version: str
    calibrated: bool
    max_simplifier_evaluations: int
    limits: RegionBudget
    sha256: str

def comparable_model_bytes(root: Path) -> SizeAccounting:
    comparable = dx80 = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.casefold().endswith(".dx80.vtx"):
            dx80 += path.stat().st_size
        else:
            comparable += path.stat().st_size
    return SizeAccounting(comparable, dx80)
```

The JSON profile contains these initial development limits: surface p95 `0.0015`, surface max `0.006`, normal p95 `8` degrees, normal max `35` degrees, silhouette IoU loss `0.003`, silhouette boundary p95 `1.5` pixels at 1024, UV p95 `0.002`, material-boundary p95 `1.0` pixel, skinning p95 `0.0015`, skinning max `0.006`, near-limit fraction `0.85`, and `max_simplifier_evaluations: 3`. `load_profile` rejects unknown/missing fields, non-finite values, a different schema/version, and a value outside its declared domain.

- [ ] **Step 4: Run the focused tests**

Run: `python -m unittest tests.maximum_optimizer.test_contracts_profile_reporting -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the contract slice**

```powershell
git add maximum_optimizer tests/maximum_optimizer/test_contracts_profile_reporting.py
git commit -m "feat(maximum): add adaptive contracts and accounting"
```

### Task 2: SMD parsing, QC occurrences, regional partitioning, and correspondence

**Files:**
- Create: `maximum_optimizer/smd.py`
- Create: `maximum_optimizer/qc_graph.py`
- Create: `maximum_optimizer/regions.py`
- Create: `tests/maximum_optimizer/fixtures/two_components.smd`
- Create: `tests/maximum_optimizer/fixtures/two_components_OPT.smd`
- Create: `tests/maximum_optimizer/fixtures/vehicle.qc`
- Create: `tests/maximum_optimizer/test_smd_regions.py`

**Interfaces:**
- Consumes: `RegionKey` from Task 1.
- Produces: `parse_smd(text: str) -> SmdDocument`, `serialize_smd(document: SmdDocument) -> str`, `scan_qc_occurrences(root: Path) -> tuple[QcOccurrence, ...]`, `build_region_graph(document, occurrence) -> RegionGraph`, and `correspond_graphs(original, normal) -> RegionCorrespondence`.

- [ ] **Step 1: Write failing roundtrip, three-weight, component, and ambiguity tests**

```python
class SmdRegionTests(unittest.TestCase):
    def test_roundtrip_and_source_weight_limit(self):
        source = FIXTURES.joinpath("two_components.smd").read_text(encoding="utf-8")
        document = parse_smd(source)
        self.assertEqual(parse_smd(serialize_smd(document)), document)
        self.assertTrue(all(len(v.influences) <= 3 for t in document.triangles for v in t.vertices))

    def test_material_and_connected_components_are_distinct_regions(self):
        graph = build_region_graph(parse_smd(ORIGINAL.read_text()), OCCURRENCE)
        self.assertEqual([(r.material, r.local_ordinal) for r in graph.regions], [("glass", 0), ("paint", 0), ("paint", 1)])
        self.assertEqual(len({r.key.value for r in graph.regions}), 3)

    def test_ambiguous_component_mapping_falls_back_only_that_source(self):
        normal = parse_smd(NORMAL.read_text())
        correspondence = correspond_graphs(build_region_graph(parse_smd(ORIGINAL.read_text()), OCCURRENCE), build_region_graph(normal, OCCURRENCE))
        self.assertEqual(correspondence.status, "ambiguous")
        self.assertEqual(correspondence.fallback, "normal-source")
```

- [ ] **Step 2: Run tests and verify missing APIs**

Run: `python -m unittest tests.maximum_optimizer.test_smd_regions -v`

Expected: import failure for `maximum_optimizer.smd`.

- [ ] **Step 3: Implement the SMD/QC contracts and deterministic region keys**

```python
def region_key(occurrence: QcOccurrence, material: str, signature: ComponentSignature, ordinal: int) -> RegionKey:
    payload = {
        "qc": occurrence.qc_path.as_posix().casefold(),
        "directive": occurrence.directive.casefold(),
        "line": occurrence.line,
        "source": occurrence.source_path.as_posix().casefold(),
        "material": material.replace("\\", "/").casefold(),
        "component": signature.to_payload(),
        "ordinal": ordinal,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return RegionKey(f"r-{digest}")
```

Parsing preserves node names, skeleton frames, triangle order, materials, normals, UVs, and weights. Export merges duplicate influences, takes the three largest positive weights, renormalizes, and records dropped weight mass. Components connect triangles only through identical float32 positions within the same material. Correspondence requires a unique material match and unique normalized centroid/bounds/bone-set match inside the same QC occurrence; a tie or non-manifold component marks that source ambiguous without affecting sibling sources.

- [ ] **Step 4: Run focused tests and an existing real-SMD smoke parse**

Run: `python -m unittest tests.maximum_optimizer.test_smd_regions -v`

Expected: all tests pass.

Run: `python -c "from pathlib import Path; from maximum_optimizer.smd import parse_smd; p=next(Path(r'C:\mbp0719').rglob('*.smd')); print(len(parse_smd(p.read_text(encoding='utf-8', errors='strict')).triangles))"`

Expected: a positive integer and exit code 0.

- [ ] **Step 5: Commit regional source mapping**

```powershell
git add maximum_optimizer/smd.py maximum_optimizer/qc_graph.py maximum_optimizer/regions.py tests/maximum_optimizer
git commit -m "feat(maximum): map deterministic SMD regions"
```

### Task 3: Pinned meshoptimizer bridge and protected topology

**Files:**
- Create: `maximum_optimizer/mesh_attributes.py`
- Create: `maximum_optimizer/meshopt_bridge.py`
- Create: `maximum_optimizer/native/CMakeLists.txt`
- Create: `maximum_optimizer/native/build.ps1`
- Create: `maximum_optimizer/native/meshopt_bridge.cpp`
- Create/build: `maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll`
- Vendor exact text: `third_party/meshoptimizer/LICENSE.md`, `third_party/meshoptimizer/VERSION`, `third_party/meshoptimizer/src/meshoptimizer.h`, `third_party/meshoptimizer/src/allocator.cpp`, and `third_party/meshoptimizer/src/simplifier.cpp`
- Create: `tests/maximum_optimizer/test_meshopt_bridge.py`

**Interfaces:**
- Consumes: `SmdRegion` from Task 2.
- Produces: `build_mesh_input(region: SmdRegion) -> MeshInput`, `simplify_mesh(mesh: MeshInput, request: SimplifyRequest) -> SimplifiedMesh`, and `write_simplified_region(region, mesh) -> SmdRegion`.

- [ ] **Step 1: Write failing ABI, boundary, material, skinning, and determinism tests**

```python
class MeshoptBridgeTests(unittest.TestCase):
    def test_locked_border_material_and_uv_seam_survive(self):
        mesh = make_curved_grid_with_seam()
        result = simplify_mesh(mesh, SimplifyRequest(0.25, 0.002, update_vertices=False))
        self.assertLess(result.triangle_count, mesh.triangle_count)
        self.assertTrue(mesh.locked_position_words.issubset(result.used_position_words))
        self.assertEqual(set(result.material_ids), {0, 1})

    def test_non_rigid_skinning_forbids_update(self):
        with self.assertRaisesRegex(ValueError, "non-rigid"):
            simplify_mesh(make_two_bone_strip(), SimplifyRequest(0.5, 0.001, update_vertices=True))

    def test_parallel_calls_are_byte_deterministic(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = tuple(pool.map(lambda _: simplify_mesh(GRID, REQUEST), range(32)))
        self.assertTrue(all(result == results[0] for result in results))
```

- [ ] **Step 2: Run tests and verify the native bridge is missing**

Run: `python -m unittest tests.maximum_optimizer.test_meshopt_bridge -v`

Expected: import or DLL-not-built failure.

- [ ] **Step 3: Port only the reviewed bridge core from `8812c7a` and narrow its defaults**

```python
@dataclass(frozen=True)
class SimplifyRequest:
    target_ratio: float
    target_error: float
    update_vertices: bool
    regularize_light: bool = True

def simplify_mesh(mesh: MeshInput, request: SimplifyRequest) -> SimplifiedMesh:
    validate_source_limits(mesh)
    if request.update_vertices and not mesh.is_static_or_rigid:
        raise ValueError("non-rigid skinned regions cannot update vertices")
    options = SIMPLIFY_LOCK_BORDER | (SIMPLIFY_REGULARIZE_LIGHT if request.regularize_light else 0)
    return _call_owned_output_abi(mesh, request, options)
```

The bridge pins the reviewed ABI `3` and meshoptimizer version `10200`, uses `meshopt_simplifyWithAttributes` for no-update requests and `meshopt_simplifyWithUpdate` only for static/rigid requests, validates all buffer sizes and finite values, splits material subsets, propagates locked/protected vertices, exposes result error, and frees output through an ownership cookie. Do not port the experimental orchestrator or permissive default.

- [ ] **Step 4: Build the x64 DLL and run bridge tests**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File maximum_optimizer/native/build.ps1`

Expected: `Built x64 meshopt bridge:` followed by the target DLL path.

Run: `python -m unittest tests.maximum_optimizer.test_meshopt_bridge -v`

Expected: all tests pass, including 32 concurrent deterministic calls.

- [ ] **Step 5: Commit native bridge and license**

```powershell
git add maximum_optimizer/mesh_attributes.py maximum_optimizer/meshopt_bridge.py maximum_optimizer/native third_party/meshoptimizer tests/maximum_optimizer/test_meshopt_bridge.py
git commit -m "feat(maximum): add protected meshoptimizer bridge"
```

### Task 4: Continuous visual-risk classification and material semantics

**Files:**
- Create: `maximum_optimizer/materials.py`
- Create: `maximum_optimizer/risk.py`
- Create: `tests/maximum_optimizer/test_risk.py`

**Interfaces:**
- Consumes: `SmdRegion`, `RegionBudget`, optional addon/framework roots.
- Produces: `resolve_material_semantics(material, addon_root, framework_root) -> MaterialSemantics`, `measure_risk(region, semantics, canonical_views) -> RiskFeatures`, and `budget_for_risk(profile, features) -> RegionBudget`.

- [ ] **Step 1: Write failing curvature, silhouette, transparency, and skin tests**

```python
class RiskTests(unittest.TestCase):
    def test_curved_silhouette_is_riskier_than_hidden_plane(self):
        curved = measure_risk(make_cylinder(), OPAQUE, CANONICAL_VIEWS)
        plane = measure_risk(make_enclosed_plane(), OPAQUE, CANONICAL_VIEWS)
        self.assertGreater(curved.score, plane.score)
        self.assertGreater(curved.curvature_p95, plane.curvature_p95)

    def test_transparency_and_weight_gradient_tighten_budget(self):
        plain = budget_for_risk(PROFILE, measure_risk(MESH, OPAQUE, CANONICAL_VIEWS))
        risky = budget_for_risk(PROFILE, measure_risk(make_two_bone_strip(), ALPHATEST, CANONICAL_VIEWS))
        self.assertLess(risky.silhouette_iou_loss, plain.silhouette_iou_loss)
        self.assertLess(risky.skinning_p95, plain.skinning_p95)
```

- [ ] **Step 2: Run tests and verify missing classifier**

Run: `python -m unittest tests.maximum_optimizer.test_risk -v`

Expected: import failure for `maximum_optimizer.risk`.

- [ ] **Step 3: Implement evidence-based score and budgets**

```python
@dataclass(frozen=True)
class MaterialSemantics:
    translucent: bool = False
    alpha_test: bool = False
    additive: bool = False
    refractive: bool = False
    two_sided: bool = False

    @property
    def requires_render(self) -> bool:
        return self.translucent or self.alpha_test or self.additive or self.refractive or self.two_sided

score = clamp01(
    0.22 * features.curvature_p95_norm
    + 0.18 * features.silhouette_fraction
    + 0.12 * features.hard_boundary_density
    + 0.12 * features.uv_seam_density
    + 0.14 * features.material_semantic_risk
    + 0.14 * features.skinning_risk
    + 0.08 * (1.0 - features.visibility_confidence)
)
target_ratio = 0.08 + 0.57 * score
```

Curvature is area-weighted dihedral angle; silhouette membership is evaluated over the fixed directions `±X`, `±Y`, `±Z`, and four normalized XY diagonals; transparency semantics recognize `$translucent`, `$alphatest`, `$additive`, `$refract`, and `$nocull`. The optional framework resolver can explain an already-authored VMT but never changes the material identity or output closure.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.maximum_optimizer.test_risk -v`

Expected: all tests pass.

- [ ] **Step 5: Commit classifier**

```powershell
git add maximum_optimizer/materials.py maximum_optimizer/risk.py tests/maximum_optimizer/test_risk.py
git commit -m "feat(maximum): classify regional visual risk"
```

### Task 5: Deterministic regional fidelity metrics

**Files:**
- Create: `maximum_optimizer/metrics.py`
- Create: `tests/maximum_optimizer/test_metrics.py`

**Interfaces:**
- Consumes: original and candidate `SmdRegion`, `RegionBudget`, canonical views and optional pose matrices.
- Produces: `measure_region(original, candidate, contract) -> RegionMetrics` and `validate_region(metrics, budget) -> ValidationDecision`.

- [ ] **Step 1: Write failing surface, silhouette, normal, UV, boundary, and pose tests**

```python
class RegionMetricsTests(unittest.TestCase):
    def test_hexagonal_wheel_fails_silhouette_while_dense_wheel_passes(self):
        dense = measure_region(WHEEL_64, WHEEL_48, CONTRACT)
        hexagon = measure_region(WHEEL_64, WHEEL_6, CONTRACT)
        self.assertTrue(validate_region(dense, BUDGET).passed)
        self.assertFalse(validate_region(hexagon, BUDGET).passed)
        self.assertIn("silhouette", validate_region(hexagon, BUDGET).failed_gates)

    def test_uv_and_skinning_damage_are_independent_gates(self):
        self.assertIn("uv", validate_region(measure_region(MESH, shift_uv(MESH), CONTRACT), BUDGET).failed_gates)
        self.assertIn("skinning", validate_region(measure_region(MESH, alter_weights(MESH), CONTRACT), BUDGET).failed_gates)
```

- [ ] **Step 2: Run tests and verify metrics are absent**

Run: `python -m unittest tests.maximum_optimizer.test_metrics -v`

Expected: import failure for `maximum_optimizer.metrics`.

- [ ] **Step 3: Implement deterministic sampling and raster metrics**

```python
def deterministic_barycentric_samples(region: SmdRegion, count: int, seed: bytes) -> tuple[SurfaceSample, ...]:
    rng = random.Random(int.from_bytes(hashlib.sha256(seed).digest()[:8], "big"))
    areas = tuple(triangle_area(t) for t in region.triangles)
    chosen = rng.choices(range(len(areas)), weights=areas, k=count)
    return tuple(sample_triangle(region.triangles[index], rng) for index in chosen)

def validate_region(metrics: RegionMetrics, budget: RegionBudget) -> ValidationDecision:
    gates = {
        "surface": metrics.surface_p95 <= budget.surface_p95 and metrics.surface_max <= budget.surface_max,
        "normal": metrics.normal_p95_degrees <= budget.normal_p95_degrees and metrics.normal_max_degrees <= budget.normal_max_degrees,
        "silhouette": metrics.silhouette_iou_loss <= budget.silhouette_iou_loss and metrics.silhouette_boundary_p95_px <= budget.silhouette_boundary_p95_px,
        "uv": metrics.uv_p95 <= budget.uv_p95,
        "material-boundary": metrics.material_boundary_p95_px <= budget.material_boundary_p95_px,
        "skinning": metrics.skinning_p95 <= budget.skinning_p95 and metrics.skinning_max <= budget.skinning_max,
    }
    remaining = (
        1.0 - metrics.surface_p95 / budget.surface_p95,
        1.0 - metrics.normal_p95_degrees / budget.normal_p95_degrees,
        1.0 - metrics.silhouette_boundary_p95_px / budget.silhouette_boundary_p95_px,
        1.0 - metrics.uv_p95 / budget.uv_p95,
        1.0 - metrics.skinning_p95 / budget.skinning_p95,
    )
    return ValidationDecision(
        all(gates.values()),
        tuple(name for name, passed in gates.items() if not passed),
        max(0.0, min(remaining)),
    )
```

Use symmetric nearest-triangle distances with a deterministic BVH, area-weighted p95/max, angular normal deviation, 1024-pixel orthographic masks for canonical views, distance-transform boundary error, barycentric UV transfer, and representative bind/steer/suspension pose matrices. Add exact structural gates before approximate metrics.

- [ ] **Step 4: Run tests and timing bound**

Run: `python -m unittest tests.maximum_optimizer.test_metrics -v`

Expected: all tests pass.

Run: `python -m unittest tests.maximum_optimizer.test_metrics.RegionMetricsTests.test_real_wheel_completes_under_bound -v`

Expected: pass with recorded elapsed time under 5 seconds for the fixture.

- [ ] **Step 5: Commit metrics**

```powershell
git add maximum_optimizer/metrics.py tests/maximum_optimizer/test_metrics.py
git commit -m "feat(maximum): validate regional mesh fidelity"
```

### Task 6: Bounded adaptive search, cache, and local fallback

**Files:**
- Create: `maximum_optimizer/cache.py`
- Create: `maximum_optimizer/search.py`
- Create: `tests/maximum_optimizer/test_search_cache.py`

**Interfaces:**
- Consumes: original/normal regions, risk budget, simplifier and validator callables.
- Produces: `optimize_region(request: RegionRequest, simplify, validate, cache) -> RegionDecision` and `candidate_ratios(request) -> tuple[float, ...]`.

- [ ] **Step 1: Write failing evaluation-bound and wheel/body isolation tests**

```python
class AdaptiveSearchTests(unittest.TestCase):
    def test_failed_region_never_exceeds_three_simplifier_calls(self):
        calls = []
        decision = optimize_region(REQUEST, lambda region, ratio: calls.append(ratio) or BAD, always_fail, CACHE)
        self.assertEqual(decision.representation, "original")
        self.assertLessEqual(len(calls), 3)

    def test_failed_wheel_does_not_revert_passing_body(self):
        body = optimize_region(BODY_REQUEST, simplify_ok, validate_ok, CACHE)
        wheel = optimize_region(WHEEL_REQUEST, simplify_bad, validate_fail, CACHE)
        self.assertEqual(body.representation, "aggressive")
        self.assertEqual(wheel.representation, "original")
```

- [ ] **Step 2: Run tests and verify missing scheduler**

Run: `python -m unittest tests.maximum_optimizer.test_search_cache -v`

Expected: import failure for `maximum_optimizer.search`.

- [ ] **Step 3: Implement monotonic ratios and sealed cache keys**

```python
@dataclass(frozen=True)
class RegionRequest:
    region_key: RegionKey
    original: SmdRegion
    normal: SmdRegion
    classified_ratio: float
    normal_validation: ValidationDecision
    original_triangle_count: int
    normal_triangle_count: int
    source_sha256: str
    profile_sha256: str
    attribute_contract_sha256: str
    engine_version: int

def candidate_ratios(request: RegionRequest) -> tuple[float, ...]:
    target = request.classified_ratio
    if request.normal_validation.passed:
        if request.normal_validation.margin_fraction < 0.15:
            return ()
        normal_ratio = request.normal_triangle_count / request.original_triangle_count
        return tuple(r for r in (target, (target + normal_ratio) / 2.0) if r < normal_ratio - 0.02)[:2]
    return (target, (target + 1.0) / 2.0, 0.85)

def cache_key(request: RegionRequest, ratio: float) -> str:
    payload = (request.source_sha256, request.region_key.value, request.profile_sha256, request.engine_version, ratio, request.attribute_contract_sha256)
    return hashlib.sha256(repr(payload).encode("ascii")).hexdigest()
```

The decision order is aggressive candidate, lighter candidate, Normal region, Original region. Normal is retained without a simplifier call when already close to a gate. Cache writes use a temporary file plus `os.replace`; invalid schema/profile/engine hashes are misses.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.maximum_optimizer.test_search_cache -v`

Expected: all tests pass and every test asserts no more than three simplifier calls.

- [ ] **Step 5: Commit search and cache**

```powershell
git add maximum_optimizer/cache.py maximum_optimizer/search.py tests/maximum_optimizer/test_search_cache.py
git commit -m "feat(maximum): add bounded regional recovery"
```

### Task 7: Targeted render escalation

**Files:**
- Create: `maximum_optimizer/rendering.py`
- Modify: `render_previews.py`
- Create: `tests/maximum_optimizer/test_rendering.py`

**Interfaces:**
- Consumes: region decision, material semantics, metric margins, Blender path.
- Produces: `requires_targeted_render(...) -> bool` and `render_region_comparison(request: RenderRequest) -> RenderEvidence`.

- [ ] **Step 1: Write failing escalation and command-shape tests**

```python
class TargetedRenderingTests(unittest.TestCase):
    def test_only_semantic_near_limit_or_low_confidence_regions_escalate(self):
        self.assertFalse(requires_targeted_render(OPAQUE, margin=0.40, confidence=1.0))
        self.assertTrue(requires_targeted_render(TRANSLUCENT, margin=0.40, confidence=1.0))
        self.assertTrue(requires_targeted_render(OPAQUE, margin=0.10, confidence=1.0))
        self.assertTrue(requires_targeted_render(OPAQUE, margin=0.40, confidence=0.6))

    def test_render_request_contains_one_region_not_family_states(self):
        command = build_render_command(REQUEST)
        self.assertEqual(command.count("--before"), 1)
        self.assertEqual(command.count("--after"), 1)
        self.assertNotIn("--all-bodygroups", command)
```

- [ ] **Step 2: Run tests and verify missing render contract**

Run: `python -m unittest tests.maximum_optimizer.test_rendering -v`

Expected: import failure for `maximum_optimizer.rendering`.

- [ ] **Step 3: Implement the 15-percent escalation rule and cached region renders**

```python
@dataclass(frozen=True)
class RenderRequest:
    original_region_smd: Path
    candidate_region_smd: Path
    camera_json: Path
    output_dir: Path
    blender: Path
    pose: str

@dataclass(frozen=True)
class RenderEvidence:
    passed: bool
    rgb_mae: float
    edge_error: float
    output_dir: Path
    cache_key: str

def requires_targeted_render(semantics: MaterialSemantics, margin: float, confidence: float) -> bool:
    return semantics.requires_render or margin <= 0.15 or confidence < 0.75
```

`render_previews.py` receives region-only SMDs, an explicit camera JSON, pose, material roots, 1024 size, and output path. Cache identity includes original/candidate hashes, camera, pose, materials, Blender version, and render script hash. A failed render rejects only that region.

- [ ] **Step 4: Run unit and Blender smoke tests**

Run: `python -m unittest tests.maximum_optimizer.test_rendering -v`

Expected: all unit tests pass.

Run: `& 'C:\Program Files\Blender Foundation\Blender 5.0\blender.exe' --background --python render_previews.py -- --before tests\maximum_optimizer\fixtures\two_components.smd --after tests\maximum_optimizer\fixtures\two_components_OPT.smd --out .superpowers\maximum-render-smoke --size 1024`

Expected: exit code 0 and `preview_summary.json` under the smoke output.

- [ ] **Step 5: Commit targeted rendering**

```powershell
git add maximum_optimizer/rendering.py render_previews.py tests/maximum_optimizer/test_rendering.py
git commit -m "feat(maximum): escalate only targeted region renders"
```

### Task 8: Composition, compile isolation, compiled integrity, and dependency closure

**Files:**
- Create: `maximum_optimizer/composition.py`
- Create: `maximum_optimizer/compiled_validation.py`
- Create: `maximum_optimizer/closure.py`
- Create: `tests/maximum_optimizer/test_composition_compile_closure.py`

**Interfaces:**
- Consumes: region decisions, immutable original/normal trees, compile callback, original addon reference inventory.
- Produces: `compose_source_tree(...) -> CompositionResult`, `isolate_compile_failure(...) -> IsolationResult`, `validate_compiled_family(...) -> CompiledValidation`, and `audit_reference_closure(...) -> ClosureAudit`.

- [ ] **Step 1: Write failing local composition, binary isolation, DX90, and closure tests**

```python
class CompositionIntegrityTests(unittest.TestCase):
    def test_original_wheel_and_aggressive_body_share_one_composition(self):
        result = compose_source_tree(ORIGINAL_TREE, NORMAL_TREE, {BODY: AGGRESSIVE, WHEEL: ORIGINAL}, OUT)
        self.assertEqual(parse_smd(result.sources[BODY.source]).region(BODY), AGGRESSIVE.region)
        self.assertEqual(parse_smd(result.sources[WHEEL.source]).region(WHEEL), ORIGINAL.region)

    def test_compile_isolation_is_logarithmic_and_local(self):
        result = isolate_compile_failure(tuple(f"s{i}.smd" for i in range(8)), compile_fails_only_s5)
        self.assertEqual(result.reverted_sources, ("s5.smd",))
        self.assertLessEqual(result.compile_count, math.ceil(math.log2(8)) + 1)

    def test_dx90_required_dx80_forbidden_and_new_framework_reference_fails(self):
        self.assertFalse(validate_compiled_family(MISSING_DX90, EXPECTED).passed)
        self.assertFalse(audit_reference_closure(ORIGINAL_REFS, CANDIDATE_WITH_NEW_FRAMEWORK_REF, None).passed)
```

- [ ] **Step 2: Run tests and verify missing composition APIs**

Run: `python -m unittest tests.maximum_optimizer.test_composition_compile_closure -v`

Expected: import failure for `maximum_optimizer.composition`.

- [ ] **Step 3: Implement immutable composition and bounded isolation**

```python
@dataclass(frozen=True)
class IsolationResult:
    reverted_sources: tuple[str, ...]
    compile_count: int

def isolate_compile_failure(changed_sources: tuple[str, ...], compile_group: CompileGroup) -> IsolationResult:
    remaining = tuple(sorted(changed_sources, key=str.casefold))
    compiles = 0
    while len(remaining) > 1:
        midpoint = len(remaining) // 2
        left, right = remaining[:midpoint], remaining[midpoint:]
        compiles += 1
        remaining = left if not compile_group(left) else right
    return IsolationResult(remaining, compiles + 1)
```

Composition copies to a fresh staging tree, replaces only mapped triangle records, retains original/normal trees read-only, and atomically promotes the finished tree. Compiled validation checks StudioMDL completion, MDL/VVD/DX90 presence and checksums, VTX/VVD bounds, expected bodyparts/skins/materials/bones/attachments/animations/collision sidecars, Source limits, and zero DX80. Closure is recomputed without the framework resolver.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.maximum_optimizer.test_composition_compile_closure -v`

Expected: all tests pass.

- [ ] **Step 5: Commit composition and integrity**

```powershell
git add maximum_optimizer/composition.py maximum_optimizer/compiled_validation.py maximum_optimizer/closure.py tests/maximum_optimizer/test_composition_compile_closure.py
git commit -m "feat(maximum): compose and validate local fallbacks"
```

### Task 9: Adaptive pipeline and structured reporting

**Files:**
- Create: `maximum_optimizer/pipeline.py`
- Extend: `maximum_optimizer/reporting.py`
- Create: `tests/maximum_optimizer/test_pipeline_reporting.py`

**Interfaces:**
- Consumes: original source root, normal source root, addon/framework roots, profile, Blender/compile callbacks, cancellation callback.
- Produces: `run_maximum_adaptive(options: MaximumRunOptions) -> MaximumRunReport` and stdout lines `[MAXIMUM] stage=<stage> current=<n> total=<n> detail=<text>`.

- [ ] **Step 1: Write failing end-to-end fake-family test**

```python
class AdaptivePipelineTests(unittest.TestCase):
    def test_one_failed_wheel_does_not_preserve_family(self):
        report = run_maximum_adaptive(fake_options(reject_regions={WHEEL_KEY}))
        self.assertEqual(report.family_status, "optimized")
        self.assertGreater(report.regions.aggressive, 0)
        self.assertEqual(report.regions.original_fallback, 1)
        self.assertEqual(report.full_family_renders, 0)
        self.assertLessEqual(report.studiomdl_compiles, 2)

    def test_report_is_atomic_recomputable_and_dx80_neutral(self):
        report = run_maximum_adaptive(fake_options())
        self.assertEqual(report.sizes.original_comparable - report.sizes.final_comparable, report.sizes.saved_comparable)
        self.assertNotEqual(report.sizes.dx80_removed, report.sizes.saved_comparable)
```

- [ ] **Step 2: Run tests and verify orchestrator is absent**

Run: `python -m unittest tests.maximum_optimizer.test_pipeline_reporting -v`

Expected: import failure for `maximum_optimizer.pipeline`.

- [ ] **Step 3: Implement the stage sequence and cancellation points**

```python
STAGES = ("normal-seed", "regional-inventory", "adaptive-simplification", "targeted-validation", "compile-fallback", "packaging")

@dataclass(frozen=True)
class MaximumRunOptions:
    addon_root: Path
    original_source_root: Path
    normal_source_root: Path
    staging_root: Path
    cache_root: Path
    report_path: Path
    profile: MaximumProfile
    framework_resolver_root: Path | None
    blender: Path
    compile_family: Callable[[Path], CompileResult]
    render_region: Callable[[RenderRequest], RenderEvidence]
    cancel: CancellationProbe

@dataclass(frozen=True)
class CompileResult:
    success: bool
    compiled_models_root: Path
    changed_source: str | None
    log_path: Path

class CancellationProbe(Protocol):
    def throw_if_cancelled(self) -> None: ...

@dataclass(frozen=True)
class RegionStatusCounts:
    aggressive: int
    lighter: int
    normal_fallback: int
    original_fallback: int
    ambiguous: int
    failed: int

@dataclass(frozen=True)
class ComparableSizeReport:
    original_comparable: int
    final_comparable: int
    saved_comparable: int
    dx80_removed: int

@dataclass(frozen=True)
class MaximumRunReport:
    family_status: Literal["optimized", "preserved", "failed", "cancelled"]
    regions: RegionStatusCounts
    sizes: ComparableSizeReport
    full_family_renders: int
    targeted_renders: int
    studiomdl_compiles: int
    report_path: Path

def run_maximum_adaptive(options: MaximumRunOptions) -> MaximumRunReport:
    options.cancel.throw_if_cancelled()
    graphs = inventory_and_correspond(options)
    decisions = optimize_mapped_regions(graphs, options)
    composition = compose_decisions(decisions, options)
    compiled = compile_and_isolate(composition, options)
    validation = validate_and_audit(compiled, options)
    return write_atomic_report(build_report(graphs, decisions, compiled, validation, options))
```

Every stage records wall/CPU time, peak working set, counts, cache hits, simplifier evaluations, targeted renders, StudioMDL compiles, region/source/family statuses, triangles/vertices, metrics, hashes, and artifact inventory. Exceptions are converted to source-local Normal fallback where safe and retain a machine-readable failure reason.

- [ ] **Step 4: Run pipeline tests and complete suite**

Run: `python -m unittest tests.maximum_optimizer.test_pipeline_reporting -v`

Expected: all tests pass.

Run: `python -m unittest discover -s tests/maximum_optimizer -p 'test_*.py' -v`

Expected: all Maximum tests pass.

- [ ] **Step 5: Commit adaptive pipeline**

```powershell
git add maximum_optimizer/pipeline.py maximum_optimizer/reporting.py tests/maximum_optimizer/test_pipeline_reporting.py
git commit -m "feat(maximum): orchestrate adaptive regional pipeline"
```

### Task 10: Integrate Maximum into the current Models worker without changing Normal/Fidelity

**Files:**
- Modify: `build_optimized_addon.py:19-20,349-829,830-1119`
- Modify: `worker/worker_main.py:490-529`
- Modify: `requirements.txt`
- Modify: `pyinstaller/worker.spec:15-33`
- Modify: `pyinstaller/package_wpf_tools.ps1:8-42,97-103`
- Create: `tests/maximum_optimizer/test_worker_cli.py`

**Interfaces:**
- Consumes: `run_maximum_adaptive` from Task 9.
- Produces: public `--optimizer-mode maximum`; unchanged normal/fidelity command behavior; packaged bridge/profile.

- [ ] **Step 1: Write failing CLI isolation tests**

```python
class WorkerCliTests(unittest.TestCase):
    def test_maximum_is_public_mode_and_forces_internal_seed_only(self):
        args = build_optimized_addon.parse_args(["addon", "--optimizer-mode", "maximum", "--ratio", "0.75"])
        self.assertEqual(args.optimizer_mode, "maximum")
        self.assertEqual(build_optimized_addon.effective_normal_ratio(args), 0.35)

    def test_normal_and_fidelity_ratios_remain_user_values(self):
        for mode in ("normal", "fidelity"):
            args = build_optimized_addon.parse_args(["addon", "--optimizer-mode", mode, "--ratio", "0.75"])
            self.assertEqual(build_optimized_addon.effective_normal_ratio(args), 0.75)
```

- [ ] **Step 2: Run tests and verify `maximum` is rejected**

Run: `python -m unittest tests.maximum_optimizer.test_worker_cli -v`

Expected: argparse rejects `maximum`.

- [ ] **Step 3: Add a post-Normal/pre-compile Maximum hook**

```python
OPTIMIZER_MODE_MAXIMUM = "maximum"

def effective_normal_ratio(args: argparse.Namespace) -> float:
    return 0.35 if args.optimizer_mode == OPTIMIZER_MODE_MAXIMUM else float(args.ratio)
```

Split argument parsing into `parse_args`. After the existing Blender optimizer generates `*_OPT.qc`, invoke `run_maximum_adaptive` only for Maximum, passing immutable original sources, normal `_OPT` sources, addon root, optional resolver root, profile/cache/log paths, Blender path, and compiler callback. Keep the existing final compiler and packaging code. Renumber displayed steps only for Maximum via structured sub-stages; legacy `== Step 1/3`, `2/3`, `3/3` lines remain parse-compatible.

- [ ] **Step 4: Package package/profile/DLL and test CLI/help**

Add `Pillow>=12.3,<13` to `requirements.txt`. `worker.spec` collects the package, Pillow submodules, and native DLL; `Get-WorkerSourceFiles` recursively includes `maximum_optimizer`, the native DLL, and its profile so stale detection is correct. The ZIP assertion adds `"_internal/maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll"` and `"_internal/maximum_optimizer/profiles/maximum-adaptive-v2.json"`.

Run: `python -m unittest tests.maximum_optimizer.test_worker_cli -v`

Expected: all tests pass.

Run: `python worker/worker_main.py --help | Select-String 'normal,fidelity,maximum'`

Expected: one matching choices line.

- [ ] **Step 5: Commit worker integration**

```powershell
git add build_optimized_addon.py worker/worker_main.py requirements.txt pyinstaller/worker.spec pyinstaller/package_wpf_tools.ps1 tests/maximum_optimizer/test_worker_cli.py
git commit -m "feat(maximum): integrate adaptive mode with Models worker"
```

### Task 11: WPF Models and Pipeline integration with testable command/progress contracts

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerCommandBuilder.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerRunner.cs:50-184`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerProgressParser.cs:5-128`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/DataContexts/MainWindowContext.cs:164-281,1194-1201`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml:748-761`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml.cs:123,2310-2339,2501-2530,2670-2770,3405-3410`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/AssemblyInfo.cs`
- Create: `tests/wpf_optimizer_contract/GmodAddonOptimizer.ContractTests.csproj`
- Create: `tests/wpf_optimizer_contract/Program.cs`

**Interfaces:**
- Consumes: `[MAXIMUM]` progress lines and `--optimizer-mode maximum`.
- Produces: `SourceAddonOptimizerCommandBuilder.BuildArguments(options)`, WPF `OptimizerModeIsMaximum`, persisted mode index `2`, Models/Pipeline status updates.

- [ ] **Step 1: Create a no-external-package .NET contract test executable**

```csharp
static void Assert(bool condition, string message)
{
    if (!condition) throw new InvalidOperationException(message);
}

var options = new SourceAddonOptimizerRunOptions
{
    AddonPath = @"C:\addon", WorkDir = @"C:\work", OptimizerMode = "maximum"
};
var arguments = SourceAddonOptimizerCommandBuilder.BuildArguments(options);
Assert(arguments.Contains("maximum"), "Maximum mode missing from worker arguments.");
var update = new SourceAddonOptimizerProgressParser().Parse("[MAXIMUM] stage=adaptive-simplification current=7 total=19 detail=wheel");
Assert(update?.MaximumStage == "adaptive-simplification", "Maximum stage was not parsed.");
Assert(update.ItemIndex == 7 && update.ItemTotal == 19, "Maximum counts were not parsed.");
```

- [ ] **Step 2: Run contract test and verify missing builder/progress field**

Run: `dotnet run --project tests/wpf_optimizer_contract/GmodAddonOptimizer.ContractTests.csproj -c Release`

Expected: compile failure naming `SourceAddonOptimizerCommandBuilder` or `MaximumStage`.

- [ ] **Step 3: Refactor command construction and expose Maximum in WPF**

```csharp
private const int OptimizerModeFidelityIndex = 1;
private const int OptimizerModeMaximumIndex = 2;

private string GetOptimizerModeArgument() => _context.OptimizerModeIndex switch
{
    OptimizerModeFidelityIndex => "fidelity",
    OptimizerModeMaximumIndex => "maximum",
    _ => "normal"
};
```

Add `Maximum` beside Normal/Fidelity, describe regional recovery, and retain settings through the existing `OptimizerModeIndex`. `SourceAddonOptimizerRunner` delegates argument creation to the builder. The parser accepts only the six approved Maximum stages and rejects malformed/non-numeric counts. Both direct Models and Pipeline use the same options factory and progress handler.

- [ ] **Step 4: Run WPF contract and publish compile tests**

Run: `dotnet run --project tests/wpf_optimizer_contract/GmodAddonOptimizer.ContractTests.csproj -c Release`

Expected: `WPF optimizer contract tests passed.`

Run: `dotnet build GmodAddonCompressor-master/GmodAddonCompressor/GmodAddonCompressor.csproj -c Release -r win-x64`

Expected: build succeeds with zero errors.

- [ ] **Step 5: Commit WPF integration**

```powershell
git add GmodAddonCompressor-master/GmodAddonCompressor tests/wpf_optimizer_contract
git commit -m "feat(wpf): expose adaptive Models Maximum"
```

### Task 12: Frozen eight-family benchmark harness and visual panels

**Files:**
- Create: `maximum_optimizer/benchmarking.py`
- Create: `benchmarks/lvs_models_adaptive/corpus.json`
- Create: `benchmarks/lvs_models_adaptive/run_benchmark.py`
- Create: `benchmarks/lvs_models_adaptive/render_panels.py`
- Create: `benchmarks/lvs_models_adaptive/audit_results.py`
- Create: `benchmarks/lvs_models_adaptive/README.md`
- Create: `tests/maximum_optimizer/test_benchmarking.py`

**Interfaces:**
- Consumes: original addon path, framework resolver path, exact lane commands/workers, Maximum reports.
- Produces: immutable input manifest, per-lane result JSON, per-family image manifests/panels, aggregate comparison, and nonzero audit exit on any promotion failure.

- [ ] **Step 1: Write failing corpus, DX80, lane-isolation, and arithmetic tests**

```python
class BenchmarkingTests(unittest.TestCase):
    def test_corpus_has_five_development_and_three_holdout_families(self):
        corpus = load_corpus(CORPUS)
        self.assertEqual(len(corpus.development), 5)
        self.assertEqual(len(corpus.holdout), 3)
        self.assertEqual(set(corpus.holdout), {"dodge_charger", "toyota_supra", "pontiac_transam_wheel"})

    def test_aggregate_recomputes_and_excludes_dx80(self):
        result = aggregate_results(FIXTURE_RESULTS)
        self.assertEqual(result.original_comparable, sum(f.original_comparable for f in result.families))
        self.assertNotIn(result.dx80_removed, (result.original_comparable, result.final_comparable))
```

- [ ] **Step 2: Run tests and verify harness is absent**

Run: `python -m unittest tests.maximum_optimizer.test_benchmarking -v`

Expected: import failure for `maximum_optimizer.benchmarking`.

- [ ] **Step 3: Implement frozen corpus and three isolated lanes**

The corpus paths and SHA-256 values are derived once from `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack` for VW Beetle, Caterham 620R, Ferrari 365 full rig, Ford Fairlane, VW Touareg, Dodge Charger, Toyota Supra, and Pontiac TransAm wheel. Each lane receives a fresh copied input, empty work/cache/output directories, the same Blender/StudioMDL hashes, process-tree monitoring, and no mutable shared cache.

```python
LANES = (
    Lane("normal-safe", optimizer_mode="normal", ratio=0.75),
    Lane("maximum-8812c7a", optimizer_mode="maximum", ratio=None),
    Lane("maximum-adaptive-v2", optimizer_mode="maximum", ratio=None),
)
```

- [ ] **Step 4: Implement image panels and promotion audit**

Panels contain original, Normal, current Maximum, v2, clay, textured, normal-deviation, silhouette-difference, and UV-checker crops at 1024 pixels for rounded focus regions. `audit_results.py` verifies input/tool hashes, complete images, recomputable sizes/triangles/vertices/times, DX90 integrity, zero final DX80, no new references, frozen profile hash, and every promotion criterion from the spec.

Run: `python -m unittest tests.maximum_optimizer.test_benchmarking -v`

Expected: all tests pass.

- [ ] **Step 5: Commit benchmark tooling and frozen input manifest**

```powershell
git add maximum_optimizer/benchmarking.py benchmarks/lvs_models_adaptive tests/maximum_optimizer/test_benchmarking.py
git commit -m "test(maximum): add reproducible LVS benchmark"
```

### Task 13: Run development calibration, freeze the profile, and run untouched holdout

**Files:**
- Modify once after development only: `maximum_optimizer/profiles/maximum-adaptive-v2.json`
- Generate: `.superpowers/maximum-adaptive-v2/benchmark/**`
- Create after successful audit: `benchmarks/lvs_models_adaptive/results/maximum-adaptive-v2.json`
- Create after successful audit: `benchmarks/lvs_models_adaptive/results/visual-manifest.json`

**Interfaces:**
- Consumes: benchmark harness from Task 12 and exact `8812c7a` detached worktree.
- Produces: fresh comparable numbers and visual evidence for all three lanes.

- [ ] **Step 1: Create the detached historical worktree and verify commit**

Run: `git worktree add --detach .worktrees/maximum-8812c7a 8812c7a`

Expected: worktree prepared at commit `8812c7a`.

Run: `git -C .worktrees/maximum-8812c7a rev-parse HEAD`

Expected: `8812c7a` expanded to its full commit hash.

- [ ] **Step 2: Run all three lanes on the development partition**

Run: `python benchmarks/lvs_models_adaptive/run_benchmark.py --partition development --addon-root 'C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack' --framework-root 'C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\2912816023\lvs_framework' --historical-root '.worktrees\maximum-8812c7a' --out '.superpowers\maximum-adaptive-v2\benchmark' --cold-cache`

Expected: five families × three successful or explicitly preserved lane records, with no missing artifact inventory.

- [ ] **Step 3: Tune only declared profile thresholds against development evidence and freeze its hash**

For each proposed profile change, rerun the development v2 lane and record the rejected profile hash. Accept only a profile that passes every structural gate, improves aggregate comparable bytes over both baselines, and has no visually accepted rounded-region regression. After selection, write the selected development evidence hash and set `calibrated: true`; no code or threshold change is permitted after the holdout starts.

Run: `python benchmarks/lvs_models_adaptive/audit_results.py --partition development --root '.superpowers\maximum-adaptive-v2\benchmark' --require-images`

Expected: `DEVELOPMENT AUDIT PASSED` and a printed frozen profile SHA-256.

- [ ] **Step 4: Run the untouched holdout and render all required panels**

Run: `python benchmarks/lvs_models_adaptive/run_benchmark.py --partition holdout --addon-root 'C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack' --framework-root 'C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\2912816023\lvs_framework' --historical-root '.worktrees\maximum-8812c7a' --out '.superpowers\maximum-adaptive-v2\benchmark' --cold-cache --frozen-profile`

Expected: three families × three completed lane records with the development profile hash unchanged.

Run: `python benchmarks/lvs_models_adaptive/render_panels.py --results '.superpowers\maximum-adaptive-v2\benchmark' --out '.superpowers\maximum-adaptive-v2\benchmark\panels' --blender 'C:\Program Files\Blender Foundation\Blender 5.0\blender.exe'`

Expected: complete image manifest and focus panels for wheels/tires, steering wheels, glass/lenses, sights, pipes/exhausts, headlights, arches/cylinders, and curved panels present in the corpus.

- [ ] **Step 5: Audit, reject if necessary, or record the proven winner**

Run: `python benchmarks/lvs_models_adaptive/audit_results.py --partition all --root '.superpowers\maximum-adaptive-v2\benchmark' --require-images --require-promotion`

Expected for integration: `PROMOTION AUDIT PASSED`. If it fails, do not weaken holdout gates; record the failed approach, return to an earlier implementation task, rerun development with a new profile version, and repeat a fresh holdout.

When passed, copy only compact reports/manifests and comparison panels into `benchmarks/lvs_models_adaptive/results/`, verify `git diff --check`, then commit:

```powershell
git add maximum_optimizer/profiles/maximum-adaptive-v2.json benchmarks/lvs_models_adaptive/results
git commit -m "bench(maximum): record adaptive v2 validation"
```

### Task 14: Full regression, packaged worker, official WPF release, and manual smoke

**Files:**
- Modify only if packaging validation requires it: `build_release_wpf.ps1`
- Generate: `dist/GModAddonOptimizerWorker/**`
- Generate: `GmodAddonCompressor-master/GmodAddonCompressor/Resources/SourceAddonOptimizer.win-x64.zip`
- Generate: `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/**`
- Create: `docs/models-maximum-adaptive-v2-verdict.md`

**Interfaces:**
- Consumes: benchmarked profile, worker, bridge, WPF integration.
- Produces: tested official executable and objective verdict with exact reduction, quality, time, limitations, and path.

- [ ] **Step 1: Run fresh Python, native, WPF, and benchmark audits**

Run: `python -m unittest discover -s tests/maximum_optimizer -p 'test_*.py' -v`

Expected: all tests pass.

Run: `dotnet run --project tests/wpf_optimizer_contract/GmodAddonOptimizer.ContractTests.csproj -c Release`

Expected: `WPF optimizer contract tests passed.`

Run: `python benchmarks/lvs_models_adaptive/audit_results.py --partition all --root '.superpowers\maximum-adaptive-v2\benchmark' --require-images --require-promotion`

Expected: `PROMOTION AUDIT PASSED`.

- [ ] **Step 2: Force a fresh worker build and verify embedded native/profile hashes**

Rename the old worker directory to a PID-stamped backup rather than deleting it, run `pyinstaller --noconfirm --clean pyinstaller/worker.spec`, and restore the backup only if the build fails. Compare SHA-256 of the built DLL/profile to the benchmark report before packaging.

Run: `Get-FileHash dist\GModAddonOptimizerWorker\_internal\maximum_optimizer\native\bin\win-x64\meshopt_bridge.dll, maximum_optimizer\native\bin\win-x64\meshopt_bridge.dll -Algorithm SHA256`

Expected: identical hashes.

- [ ] **Step 3: Generate the official release**

Run: `.\build_release_wpf.ps1`

Expected: successful tool ZIP validation, successful WPF publish, and the final executable exists.

- [ ] **Step 4: Smoke Models Maximum and Pipeline in the published app**

Launch `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`. Run Models Maximum on one development family and Pipeline on a clean copy. Confirm progress stages, cancellation behavior, output path, report link, DX90 presence, zero DX80, and that Compress follows Models in Pipeline. Open the Normal and Fidelity selections to confirm their settings and routing remain unchanged.

- [ ] **Step 5: Write the objective verdict and commit release metadata**

`docs/models-maximum-adaptive-v2-verdict.md` contains the exact eight-family aggregate and per-family table, correct no-DX80 denominator, wall/CPU time, compile/render/evaluation counts, optimized/fallback counts, triangle/vertex deltas, worst metric margins, panel locations, compiled integrity, dependency audit, limitations, rejected approaches, worker/profile/DLL hashes, and exact executable path.

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git status --short`

Expected: only intended Maximum/WPF/benchmark/release-report changes plus the user's pre-existing unrelated dirty files.

```powershell
git add docs/models-maximum-adaptive-v2-verdict.md GmodAddonCompressor-master/GmodAddonCompressor/Resources/SourceAddonOptimizer.win-x64.zip
git commit -m "release: ship Models Maximum adaptive v2"
```
