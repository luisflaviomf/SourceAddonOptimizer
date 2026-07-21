# Maximum Forced 2x Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run an isolated comparison of Magick 2x against Maximum constrained to 2x or quality-approved 4x on exactly 100 representative VTFs.

**Architecture:** Add orchestration under `experiments/VtfMaximumLab/ForcedComparison`, compiled only by the laboratory executable. Reuse production inventory, encoders, semantic analysis, VTF construction, validation, and metrics while leaving published Maximum unchanged. One CLI command creates three isolated trees, executes both variants, validates decoded outputs, and writes reports and contact sheets.

**Tech Stack:** C# 10, .NET 6, xUnit, Magick.NET, texconv, Compressonator, FLIP, existing VTF utilities.

## Global Constraints

- Source: `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack`; never modify it.
- Select exactly 100 eligible VTFs deterministically and copy related VMTs.
- Magick uses divisor 2 and minimum dimensions 8x8.
- Maximum considers only divisors 2 and 4; 4x must pass every existing quality gate.
- A structurally and semantically valid 2x candidate may be used below the image-quality gate only when reported as `forced_below_quality_gate`.
- Measure decoded final VTFs, not intermediate files.
- Do not change WPF behavior, Models Maximum, or `gui/`.
- Work without subagents and preserve unrelated changes.

---

### Task 1: Eligible sampling and forced selection policy

**Files:**
- Create: `experiments/VtfMaximumLab/ForcedComparison/ForcedComparisonPolicy.cs`
- Modify: `experiments/VtfMaximumLab/VtfMaximumLab.csproj`
- Test: `experiments/VtfMaximumLab.Tests/ForcedComparisonPolicyTests.cs`

**Interfaces:**
- Consumes: `VtfInventoryEntry`, `VtfCandidateAssessment`, `VtfQualityGate.Accept`.
- Produces: `ForcedComparisonPolicy.IsEligible(VtfInventoryEntry)` and `ForcedComparisonPolicy.Select(IReadOnlyList<VtfCandidateAssessment>)` returning `ForcedCandidateChoice`.

- [ ] **Step 1: Write failing tests for eligibility and fallback selection**

```csharp
[Fact]
public void Eligible_requires_known_single_image_texture()
{
    Assert.True(ForcedComparisonPolicy.IsEligible(Entry(1, 1, 1, VtfAlphaClass.Opaque)));
    Assert.False(ForcedComparisonPolicy.IsEligible(Entry(2, 1, 1, VtfAlphaClass.Opaque)));
    Assert.False(ForcedComparisonPolicy.IsEligible(Entry(1, 1, 1, VtfAlphaClass.Unknown)));
}

[Fact]
public void Select_uses_best_valid_x2_when_quality_gate_rejects_all()
{
    ForcedCandidateChoice result = ForcedComparisonPolicy.Select(new[]
    {
        RejectedQuality(scale: 2, compositeError: .08),
        RejectedQuality(scale: 2, compositeError: .04)
    });
    Assert.Equal(.04, result.Assessment.Metrics.CompositeError, 6);
    Assert.True(result.ForcedBelowQualityGate);
}
```

- [ ] **Step 2: Run test and confirm it fails because policy types are absent**

Run: `dotnet test experiments/VtfMaximumLab.Tests/VtfMaximumLab.Tests.csproj --filter FullyQualifiedName~ForcedComparisonPolicyTests`

Expected: build failure naming `ForcedComparisonPolicy`.

- [ ] **Step 3: Compile experiment-only files and implement policy**

Add to the lab project:

```xml
<Compile Include="ForcedComparison\*.cs" />
```

Implement:

```csharp
internal sealed record ForcedCandidateChoice(VtfCandidateAssessment Assessment, bool ForcedBelowQualityGate, IReadOnlyList<string> RejectionReasons);

internal static bool IsEligible(VtfInventoryEntry entry) =>
    entry.Frames == 1 && entry.Faces == 1 && entry.Depth == 1 && entry.AlphaClass != VtfAlphaClass.Unknown;
```

`Select` first orders gate-accepted 2x/4x candidates by bytes, composite error, scale, and encoder. If none pass, it filters 2x candidates to structural/version/semantic validity, then orders by composite error, bytes, and encoder and returns a forced choice with the original gate reasons.

- [ ] **Step 4: Run policy, candidate-matrix, and quality-gate tests**

Run: `dotnet test experiments/VtfMaximumLab.Tests/VtfMaximumLab.Tests.csproj --filter "FullyQualifiedName~ForcedComparisonPolicyTests|FullyQualifiedName~VtfCandidateMatrixBuilderTests|FullyQualifiedName~VtfQualityGateTests"`

Expected: all selected tests pass.

- [ ] **Step 5: Commit only Task 1 files**

Run: `git add -- experiments/VtfMaximumLab/ForcedComparison/ForcedComparisonPolicy.cs experiments/VtfMaximumLab/VtfMaximumLab.csproj experiments/VtfMaximumLab.Tests/ForcedComparisonPolicyTests.cs`

Run: `git commit -m "test(vtf): define forced 2x comparison policy"`

### Task 2: Isolated runner and machine-readable report

**Files:**
- Create: `experiments/VtfMaximumLab/ForcedComparison/ForcedComparisonReport.cs`
- Create: `experiments/VtfMaximumLab/ForcedComparison/ForcedComparisonRunner.cs`
- Test: `experiments/VtfMaximumLab.Tests/ForcedComparisonRunnerTests.cs`

**Interfaces:**
- Consumes: policy from Task 1, `VtfSampleSelector`, `VtfSampleTreeCopier`, production Magick compression, Maximum encoders/builders, and metric calculators.
- Produces: `RunAsync(string addonRoot, string outputRoot, int count, int parallelism, CancellationToken)` and `sample-manifest.json` plus `comparison-results.json`.

- [ ] **Step 1: Write a failing synthetic-tree test**

```csharp
[Fact]
public async Task Run_creates_exact_isolated_tree_counts()
{
    using TempDirectory source = FixtureAddon.Create(textureCount: 4);
    using TempDirectory output = new();
    ForcedComparisonReport report = await FixtureRunner().RunAsync(source.Path, output.Path, 3, 2, CancellationToken.None);
    Assert.Equal(3, report.TextureCount);
    foreach (string tree in new[] { "original", "magick-2x", "maximum-forced" })
        Assert.Equal(3, Directory.GetFiles(Path.Combine(output.Path, tree), "*.vtf", SearchOption.AllDirectories).Length);
}
```

- [ ] **Step 2: Run the focused test and confirm missing runner/report failure**

Run: `dotnet test experiments/VtfMaximumLab.Tests/VtfMaximumLab.Tests.csproj --filter FullyQualifiedName~ForcedComparisonRunnerTests`

Expected: build failure naming `ForcedComparisonRunner`.

- [ ] **Step 3: Implement deterministic setup and processing**

The runner must execute:

```csharp
VtfInventoryEntry[] eligible = VtfInventoryScanner.Scan(addonRoot).Where(ForcedComparisonPolicy.IsEligible).ToArray();
VtfSampleManifest manifest = VtfSampleSelector.Select(eligible, count, "maximum-forced-2x-v1", addonRoot);
VtfSampleTreeCopier.Copy(manifest, outputRoot);
```

Copy `original` to `magick-2x` and `maximum-forced`. Configure Magick divisor 2, target width 8, target height 8. Build Maximum candidates only for 2x and 4x, preserve VTF version/flags/mip policy, decode every candidate, calculate semantic metrics, and apply Task 1 selection. Use bounded `Parallel.ForEachAsync`. Store pre/post source hashes and every candidate rejection reason.

The report records actual VTF/material byte totals, durations, output dimensions/format/mips/structure, all visual metrics, winner encoder/scale, x2/x4/forced/failed counts, and excluded structural-category counts.

- [ ] **Step 4: Run runner tests and full laboratory suite**

Run: `dotnet test experiments/VtfMaximumLab.Tests/VtfMaximumLab.Tests.csproj`

Expected: all tests pass.

- [ ] **Step 5: Commit only Task 2 files**

Run: `git add -- experiments/VtfMaximumLab/ForcedComparison/ForcedComparisonReport.cs experiments/VtfMaximumLab/ForcedComparison/ForcedComparisonRunner.cs experiments/VtfMaximumLab.Tests/ForcedComparisonRunnerTests.cs`

Run: `git commit -m "feat(vtf): add isolated forced 2x runner"`

### Task 3: Summary, CLI, and contact sheets

**Files:**
- Create: `experiments/VtfMaximumLab/ForcedComparison/ForcedComparisonSummaryWriter.cs`
- Create: `experiments/VtfMaximumLab/ForcedComparison/ForcedComparisonContactSheetBuilder.cs`
- Modify: `experiments/VtfMaximumLab/Program.cs`
- Test: `experiments/VtfMaximumLab.Tests/ForcedComparisonSummaryTests.cs`

**Interfaces:**
- Consumes: Task 2 report and decoded original/Magick/Maximum VTF files.
- Produces: command `forced-2x --addon <path> --out <path> --count 100 --parallel 10`, `summary.md`, and `previews/*.png`.

- [ ] **Step 1: Write a failing summary calculation test**

```csharp
[Fact]
public void Summary_reports_real_delta_and_forced_count()
{
    string markdown = ForcedComparisonSummaryWriter.ToMarkdown(Report(original: 1000, magick: 300, maximum: 250, forced: 5));
    Assert.Contains("Magick VTF: 300 bytes", markdown);
    Assert.Contains("Maximum VTF: 250 bytes", markdown);
    Assert.Contains("16.67% smaller than Magick", markdown);
    Assert.Contains("Forced below quality gate: 5", markdown);
}
```

- [ ] **Step 2: Run summary test and confirm missing writer failure**

Run: `dotnet test experiments/VtfMaximumLab.Tests/VtfMaximumLab.Tests.csproj --filter FullyQualifiedName~ForcedComparisonSummaryTests`

Expected: build failure naming `ForcedComparisonSummaryWriter`.

- [ ] **Step 3: Implement summary, CLI, and deterministic previews**

Wire the command:

```csharp
"forced-2x" => await RunForced2xAsync(args.Skip(1).ToArray()),
```

Parse `--addon`, `--out`, `--count`, and `--parallel`, run `ForcedComparisonRunner`, write Markdown/JSON, and return nonzero when `Failed > 0` or source hashes changed.

The contact-sheet builder selects best, median, and worst Maximum-versus-Magick composite deltas plus available alpha/cutout and normal cases. Each row shows decoded original, Magick, and Maximum with path, dimensions, bytes, scale, SSIM/FLIP, and semantic metrics.

- [ ] **Step 4: Run all tests and CLI help smoke test**

Run: `dotnet test experiments/VtfMaximumLab.Tests/VtfMaximumLab.Tests.csproj`

Run: `dotnet run --project experiments/VtfMaximumLab/VtfMaximumLab.csproj -- --help`

Expected: tests pass and help lists `forced-2x`.

- [ ] **Step 5: Commit only Task 3 files**

Run: `git add -- experiments/VtfMaximumLab/ForcedComparison/ForcedComparisonSummaryWriter.cs experiments/VtfMaximumLab/ForcedComparison/ForcedComparisonContactSheetBuilder.cs experiments/VtfMaximumLab/Program.cs experiments/VtfMaximumLab.Tests/ForcedComparisonSummaryTests.cs`

Run: `git commit -m "feat(vtf): report forced 2x comparison"`

### Task 4: Execute and validate the real 100-texture comparison

**Files:**
- Generate outside repository: `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack_forced2x_100\**`

**Interfaces:**
- Consumes: Task 3 CLI and immutable source addon.
- Produces: three sample trees, manifest, JSON/Markdown reports, previews, and final findings.

- [ ] **Step 1: Run with parallelism 10**

Run: `dotnet run --project experiments/VtfMaximumLab/VtfMaximumLab.csproj -c Release -- forced-2x --addon "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack" --out "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack_forced2x_100" --count 100 --parallel 10`

Expected: exit 0 and progress reaches 100/100.

- [ ] **Step 2: Validate counts, decoding, structure, related VMTs, and source hashes**

Run: `dotnet run --project experiments/VtfMaximumLab/VtfMaximumLab.csproj -c Release -- validate --root "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack_forced2x_100"`

Expected: 100 valid textures, zero invalid outputs, related files unchanged, source unchanged.

- [ ] **Step 3: Inspect every generated contact sheet**

Open all PNG files under `lvs_cars_pack_forced2x_100\previews`. Confirm readable labels, correct channels, and no corrupted image in best, median, worst, alpha/cutout, and normal cases.

- [ ] **Step 4: Run release-configuration regression tests**

Run: `dotnet test experiments/VtfMaximumLab.Tests/VtfMaximumLab.Tests.csproj -c Release`

Expected: all tests pass.

- [ ] **Step 5: Deliver exact findings**

Report original/Magick/Maximum VTF and materials bytes, reductions, duration, aggregate metrics, better/equal/worse counts, x2/x4/forced/failed counts, selection hash, and absolute paths. State plainly whether Maximum is smaller and whether it is visually better, equal, or worse; never hide a quality tradeoff.
