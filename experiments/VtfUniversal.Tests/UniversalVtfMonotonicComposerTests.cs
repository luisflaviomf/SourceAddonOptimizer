using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfMonotonicComposerTests : IDisposable
{
    private readonly string _root = Path.Combine(
        Path.GetTempPath(),
        "GmodAddonOptimizer",
        "UniversalVtfMonotonicComposerTests",
        Guid.NewGuid().ToString("N"));

    public UniversalVtfMonotonicComposerTests() => Directory.CreateDirectory(_root);

    [Fact]
    public async Task PublishesOnlySmallerAcceptedFinalArtifacts()
    {
        string incumbentRoot = Path.Combine(_root, "incumbent");
        string alternativeRoot = Path.Combine(_root, "alternative");
        string rawRoot = Path.Combine(_root, "protected-raw");
        string safeRoot = Path.Combine(_root, "protected-safe");
        Write(incumbentRoot, "materials/a.vtf", 100, 0x11);
        Write(incumbentRoot, "materials/b.vtf", 100, 0x22);
        Write(incumbentRoot, "lua/shared.lua", 12, 0x33);
        Write(alternativeRoot, "materials/a.vtf", 80, 0x44);
        Write(alternativeRoot, "materials/b.vtf", 120, 0x55);
        Write(alternativeRoot, "lua/shared.lua", 12, 0x33);
        Write(rawRoot, "authority.bin", 1_000, 0x66);
        Write(rawRoot, "authority-2.bin", 1, 0x66);
        Write(safeRoot, "authority.bin", 500, 0x77);
        Write(safeRoot, "authority-2.bin", 1, 0x77);

        UniversalVtfTreeSnapshot incumbent = UniversalVtfTreeInventory.Capture(incumbentRoot);
        UniversalVtfTreeSnapshot alternative = UniversalVtfTreeInventory.Capture(alternativeRoot);
        UniversalVtfTreeSnapshot raw = UniversalVtfTreeInventory.Capture(rawRoot);
        UniversalVtfTreeSnapshot safe = UniversalVtfTreeInventory.Capture(safeRoot);
        string alternativeManifestPath = Path.Combine(_root, "alternative.manifest.json");
        UniversalVtfManifestFrozen alternativeManifest =
            await UniversalVtfManifestWriter.WriteAsync(
                alternativeManifestPath,
                Receipt(alternative, raw, safe, new[]
                {
                    Row(alternative, "materials/a.vtf", 200),
                    Row(alternative, "materials/b.vtf", 200)
                }),
                CancellationToken.None);
        string outputRoot = Path.Combine(_root, "output");
        string manifestPath = Path.Combine(_root, "monotonic.manifest.json");

        UniversalVtfMonotonicComposeResult result =
            await UniversalVtfMonotonicComposer.ComposeAsync(
                new UniversalVtfMonotonicComposeRequest(
                    UniversalVtfProfileKind.Balanced,
                    incumbentRoot,
                    Anchor("adaptive-test", incumbent),
                    new[]
                    {
                        new UniversalVtfMonotonicAlternativeAuthority(
                            "universal-balanced",
                            alternativeRoot,
                            alternativeManifestPath,
                            alternativeManifest.Sha256)
                    },
                    outputRoot,
                    manifestPath,
                    new[]
                    {
                        Protected("raw", rawRoot),
                        Protected("safe", safeRoot)
                    }),
                CancellationToken.None);

        Assert.Equal(20, result.SavedBytes);
        Assert.Equal(File.ReadAllBytes(Path.Combine(alternativeRoot, "materials/a.vtf")),
            File.ReadAllBytes(Path.Combine(outputRoot, "materials/a.vtf")));
        Assert.Equal(File.ReadAllBytes(Path.Combine(incumbentRoot, "materials/b.vtf")),
            File.ReadAllBytes(Path.Combine(outputRoot, "materials/b.vtf")));
        Assert.Equal(File.ReadAllBytes(Path.Combine(incumbentRoot, "lua/shared.lua")),
            File.ReadAllBytes(Path.Combine(outputRoot, "lua/shared.lua")));
        Assert.Equal(incumbent.TreeSha256,
            UniversalVtfTreeInventory.Capture(incumbentRoot).TreeSha256);
        Assert.Equal(alternative.TreeSha256,
            UniversalVtfTreeInventory.Capture(alternativeRoot).TreeSha256);

        UniversalVtfMonotonicManifest manifest =
            UniversalVtfMonotonicManifestStore.Load(manifestPath, result.ManifestSha256);
        Assert.Equal("universal-balanced", manifest.Row("materials/a.vtf").Winner);
        Assert.Equal("incumbent", manifest.Row("materials/b.vtf").Winner);
        Assert.All(manifest.Rows,
            row => Assert.True(row.FinalBytes <= row.IncumbentBytes));
    }

    [Fact]
    public async Task RejectsOutputNestedInsideAlternativeBeforeMutatingIt()
    {
        CorpusFixture fixture = await CreateCorpusAsync("output-overlap");
        UniversalVtfTreeSnapshot before = UniversalVtfTreeInventory.Capture(fixture.AlternativeRoot);
        string output = Path.Combine(fixture.AlternativeRoot, "nested-output");

        await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfMonotonicComposer.ComposeAsync(
                Request(fixture, output, Path.Combine(_root, "output-overlap.json")),
                CancellationToken.None));

        AssertTree(before, UniversalVtfTreeInventory.Capture(fixture.AlternativeRoot));
    }

    [Fact]
    public async Task RejectsManifestNestedInsideIncumbentBeforeMutatingIt()
    {
        CorpusFixture fixture = await CreateCorpusAsync("manifest-overlap");
        UniversalVtfTreeSnapshot before = UniversalVtfTreeInventory.Capture(fixture.IncumbentRoot);
        string manifest = Path.Combine(fixture.IncumbentRoot, "reports", "result.json");

        await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfMonotonicComposer.ComposeAsync(
                Request(fixture, Path.Combine(_root, "manifest-overlap-output"), manifest),
                CancellationToken.None));

        AssertTree(before, UniversalVtfTreeInventory.Capture(fixture.IncumbentRoot));
    }

    [Fact]
    public async Task RejectsManifestNestedInsideOutputBeforeCreatingEitherDestination()
    {
        CorpusFixture fixture = await CreateCorpusAsync("manifest-output-overlap");
        string output = Path.Combine(_root, "manifest-output-overlap-result");
        string manifest = Path.Combine(output, "reports", "result.json");

        await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfMonotonicComposer.ComposeAsync(
                Request(fixture, output, manifest),
                CancellationToken.None));

        Assert.False(Directory.Exists(output));
        Assert.False(File.Exists(manifest));
    }

    [Fact]
    public async Task RejectsDestinationsInsideRawAndSafeProtectedAuthorities()
    {
        CorpusFixture fixture = await CreateCorpusAsync("protected-overlap");
        foreach ((string root, string label) in new[]
                 {
                     (fixture.RawRoot, "raw"),
                     (fixture.SafeRoot, "safe")
                 })
        {
            UniversalVtfTreeSnapshot before = UniversalVtfTreeInventory.Capture(root);
            string output = Path.Combine(root, "nested-output");
            string manifest = Path.Combine(_root, $"protected-{label}.json");

            await Assert.ThrowsAsync<InvalidDataException>(() =>
                UniversalVtfMonotonicComposer.ComposeAsync(
                    Request(fixture, output, manifest),
                    CancellationToken.None));

            AssertTree(before, UniversalVtfTreeInventory.Capture(root));
            Assert.False(Directory.Exists(output));
            Assert.False(File.Exists(manifest));
        }
    }

    [Fact]
    public async Task RejectsAlternativeManifestBoundToDifferentProtectedRawTree()
    {
        CorpusFixture fixture = await CreateCorpusAsync("protected-provenance");
        string wrongRaw = Path.Combine(_root, "protected-provenance-wrong-raw");
        Write(wrongRaw, "authority.bin", 1_000, 0x88);
        Write(wrongRaw, "authority-2.bin", 1, 0x88);
        string output = Path.Combine(_root, "protected-provenance-output");
        string manifest = Path.Combine(_root, "protected-provenance.json");
        UniversalVtfMonotonicComposeRequest request = Request(fixture, output, manifest) with
        {
            ProtectedAuthorities = new[]
            {
                Protected("raw", wrongRaw),
                Protected("safe", fixture.SafeRoot)
            }
        };

        InvalidDataException error = await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfMonotonicComposer.ComposeAsync(request, CancellationToken.None));

        Assert.Contains("universal-balanced:raw", error.Message);
        Assert.False(Directory.Exists(output));
        Assert.False(File.Exists(manifest));
    }

    [Fact]
    public async Task ManifestValidationReplaysTheSmallestSafeWinner()
    {
        CorpusFixture fixture = await CreateCorpusAsync("manifest-replay");
        string output = Path.Combine(_root, "manifest-replay-output");
        string path = Path.Combine(_root, "manifest-replay.json");
        UniversalVtfMonotonicComposeResult result =
            await UniversalVtfMonotonicComposer.ComposeAsync(
                Request(fixture, output, path),
                CancellationToken.None);
        UniversalVtfMonotonicManifest manifest =
            UniversalVtfMonotonicManifestStore.Load(path, result.ManifestSha256);
        UniversalVtfMonotonicManifestRow first = manifest.Row("materials/a.vtf");
        UniversalVtfMonotonicManifestRow forged = first with
        {
            Winner = "incumbent",
            FinalBytes = first.IncumbentBytes,
            FinalSha256 = first.IncumbentSha256
        };
        UniversalVtfMonotonicManifestRow[] rows = manifest.Rows
            .Select(row => row.RelativePath == first.RelativePath ? forged : row)
            .ToArray();
        long forgedFinalBytes = manifest.FinalTree.Bytes + first.SavedBytes;
        var forgedAggregate = new UniversalVtfMonotonicManifestAggregate(
            rows.Length,
            ReplacedVtfs: 0,
            PreservedVtfs: rows.Length,
            manifest.IncumbentTree.Bytes,
            forgedFinalBytes,
            SavedBytes: 0);
        UniversalVtfMonotonicManifest forgedManifest = manifest with
        {
            FinalTree = manifest.FinalTree with
            {
                Bytes = forgedFinalBytes,
                TreeSha256 = Hash('9')
            },
            Aggregate = forgedAggregate,
            Rows = rows
        };

        Assert.Throws<InvalidDataException>(() =>
            UniversalVtfMonotonicManifestStore.Validate(forgedManifest));
    }

    [Fact]
    public async Task ReadOnlyIncumbentCanBeReplacedInsideOwnedStaging()
    {
        CorpusFixture fixture = await CreateCorpusAsync("read-only");
        string incumbent = Path.Combine(fixture.IncumbentRoot, "materials", "a.vtf");
        File.SetAttributes(incumbent, File.GetAttributes(incumbent) | FileAttributes.ReadOnly);
        try
        {
            UniversalVtfMonotonicComposeResult result =
                await UniversalVtfMonotonicComposer.ComposeAsync(
                    Request(
                        fixture,
                        Path.Combine(_root, "read-only-output"),
                        Path.Combine(_root, "read-only.json")),
                    CancellationToken.None);

            Assert.Equal(20, result.SavedBytes);
        }
        finally
        {
            File.SetAttributes(incumbent, FileAttributes.Normal);
        }
    }

    [Fact]
    public async Task RejectsRawIncumbentAuthorityBeforeCreatingDestinations()
    {
        CorpusFixture fixture = await CreateCorpusAsync("raw-incumbent");
        string output = Path.Combine(_root, "raw-incumbent-output");
        string manifest = Path.Combine(_root, "raw-incumbent.json");
        UniversalVtfMonotonicComposeRequest request = Request(fixture, output, manifest) with
        {
            IncumbentAnchor = Anchor("lvs_cars_pack_raw", fixture.Incumbent)
        };

        InvalidDataException error = await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfMonotonicComposer.ComposeAsync(request, CancellationToken.None));

        Assert.Contains("invalid_incumbent_authority", error.Message);
        Assert.False(Directory.Exists(output));
        Assert.False(File.Exists(manifest));
    }

    [Fact]
    public async Task RejectsExactRawFingerprintEvenWhenAuthorityNameIsDisguised()
    {
        CorpusFixture fixture = await CreateCorpusAsync("raw-fingerprint");
        UniversalVtfMonotonicComposeRequest request = Request(
            fixture,
            Path.Combine(_root, "raw-fingerprint-output"),
            Path.Combine(_root, "raw-fingerprint.json")) with
        {
            IncumbentAnchor = UniversalVtfCorpusExpectations.RawTreeAnchor with
            {
                Name = "adaptive-disguised"
            }
        };

        InvalidDataException error = await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfMonotonicComposer.ComposeAsync(request, CancellationToken.None));

        Assert.Equal("monotonic_raw_incumbent_forbidden", error.Message);
    }

    [Fact]
    public async Task MalformedManifestMembersFailClosedAsInvalidData()
    {
        CorpusFixture fixture = await CreateCorpusAsync("malformed-manifest");
        string output = Path.Combine(_root, "malformed-manifest-output");
        string path = Path.Combine(_root, "malformed-manifest.json");
        UniversalVtfMonotonicComposeResult result =
            await UniversalVtfMonotonicComposer.ComposeAsync(
                Request(fixture, output, path),
                CancellationToken.None);
        UniversalVtfMonotonicManifest manifest =
            UniversalVtfMonotonicManifestStore.Load(path, result.ManifestSha256);

        UniversalVtfMonotonicManifestRow row = manifest.Rows[0];
        UniversalVtfMonotonicManifest[] malformed =
        {
            manifest with { Schema = null! },
            manifest with { IncumbentTree = null! },
            manifest with
            {
                FinalTree = manifest.FinalTree with
                {
                    FileCount = manifest.FinalTree.FileCount + 1
                }
            },
            manifest with
            {
                Rows = new[] { row with { Alternatives = null! } }
            }
        };
        foreach (UniversalVtfMonotonicManifest candidate in malformed)
        {
            Assert.Throws<InvalidDataException>(() =>
                UniversalVtfMonotonicManifestStore.Validate(candidate));
        }
    }

    [Fact]
    public async Task ManifestRejectsRawTreeEvenWhenAuthorityIsAliasedAsOptimized()
    {
        CorpusFixture fixture = await CreateCorpusAsync("raw-manifest");
        string output = Path.Combine(_root, "raw-manifest-output");
        string path = Path.Combine(_root, "raw-manifest.json");
        UniversalVtfMonotonicComposeResult result =
            await UniversalVtfMonotonicComposer.ComposeAsync(
                Request(fixture, output, path),
                CancellationToken.None);
        UniversalVtfMonotonicManifest manifest =
            UniversalVtfMonotonicManifestStore.Load(path, result.ManifestSha256);
        UniversalVtfTreeAnchor raw = UniversalVtfCorpusExpectations.RawTreeAnchor;
        UniversalVtfMonotonicManifest forged = manifest with
        {
            IncumbentAuthority = "adaptive-balanced",
            IncumbentTree = new UniversalVtfManifestTree(
                "incumbent",
                raw.FileCount,
                raw.TotalBytes,
                raw.TreeSha256)
        };

        InvalidDataException error = Assert.Throws<InvalidDataException>(() =>
            UniversalVtfMonotonicManifestStore.Validate(forged));

        Assert.Equal("monotonic_raw_incumbent_forbidden", error.Message);
    }

    [Fact]
    public async Task CancellationAtCommitBoundaryPublishesNothing()
    {
        CorpusFixture fixture = await CreateCorpusAsync("cancel-before-commit");
        string output = Path.Combine(_root, "cancel-before-commit-output");
        string manifest = Path.Combine(_root, "cancel-before-commit.json");
        using var cancellation = new CancellationTokenSource();
        UniversalVtfMonotonicComposeRequest request = Request(fixture, output, manifest) with
        {
            Observer = new ActionObserver(phase =>
            {
                if (phase == UniversalVtfMonotonicComposePhase.BeforeCommit)
                {
                    cancellation.Cancel();
                }
            })
        };

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            UniversalVtfMonotonicComposer.ComposeAsync(request, cancellation.Token));

        Assert.False(Directory.Exists(output));
        Assert.False(File.Exists(manifest));
        Assert.Single(Directory.EnumerateDirectories(
            _root,
            "cancel-before-commit-output.monotonic-owned-*"));
    }

    [Fact]
    public async Task CancellationAfterTreeCommitFinishesManifestCommit()
    {
        CorpusFixture fixture = await CreateCorpusAsync("cancel-after-tree");
        string output = Path.Combine(_root, "cancel-after-tree-output");
        string manifest = Path.Combine(_root, "cancel-after-tree.json");
        using var cancellation = new CancellationTokenSource();
        UniversalVtfMonotonicComposeRequest request = Request(fixture, output, manifest) with
        {
            Observer = new ActionObserver(phase =>
            {
                if (phase == UniversalVtfMonotonicComposePhase.AfterTreeCommit)
                {
                    cancellation.Cancel();
                }
            })
        };

        UniversalVtfMonotonicComposeResult result =
            await UniversalVtfMonotonicComposer.ComposeAsync(request, cancellation.Token);

        Assert.True(Directory.Exists(output));
        Assert.True(File.Exists(manifest));
        _ = UniversalVtfMonotonicManifestStore.Load(manifest, result.ManifestSha256);
    }

    [Fact]
    public async Task FailureAfterManifestCommitRollsManifestBackAndPublishesNoTree()
    {
        CorpusFixture fixture = await CreateCorpusAsync("rollback");
        string output = Path.Combine(_root, "rollback-output");
        string manifest = Path.Combine(_root, "rollback.json");
        UniversalVtfMonotonicComposeRequest request = Request(fixture, output, manifest) with
        {
            Observer = new ActionObserver(phase =>
            {
                if (phase == UniversalVtfMonotonicComposePhase.AfterManifestCommit)
                {
                    throw new InjectedPublicationException();
                }
            })
        };

        await Assert.ThrowsAsync<InjectedPublicationException>(() =>
            UniversalVtfMonotonicComposer.ComposeAsync(request, CancellationToken.None));

        Assert.False(Directory.Exists(output));
        Assert.False(File.Exists(manifest));
        Assert.Single(Directory.EnumerateDirectories(_root, "rollback-output.monotonic-owned-*"));
        Assert.Single(Directory.EnumerateFiles(_root, "rollback.json.monotonic-owned-*.tmp"));
    }

    [Fact]
    public async Task LateManifestCollisionRollsTreeBackAndPreservesExternalCollision()
    {
        CorpusFixture fixture = await CreateCorpusAsync("late-collision");
        string output = Path.Combine(_root, "late-collision-output");
        string manifest = Path.Combine(_root, "late-collision.json");
        UniversalVtfMonotonicComposeRequest request = Request(fixture, output, manifest) with
        {
            Observer = new ActionObserver(phase =>
            {
                if (phase == UniversalVtfMonotonicComposePhase.AfterTreeCommit)
                {
                    File.WriteAllText(manifest, "owned elsewhere");
                }
            })
        };

        await Assert.ThrowsAnyAsync<IOException>(() =>
            UniversalVtfMonotonicComposer.ComposeAsync(request, CancellationToken.None));

        Assert.False(Directory.Exists(output));
        Assert.Equal("owned elsewhere", File.ReadAllText(manifest));
        Assert.Single(Directory.EnumerateDirectories(
            _root,
            "late-collision-output.monotonic-owned-*"));
    }

    [Fact]
    public async Task StagingMutationAtCommitBoundaryFailsBeforePublication()
    {
        CorpusFixture fixture = await CreateCorpusAsync("staging-drift");
        string output = Path.Combine(_root, "staging-drift-output");
        string manifest = Path.Combine(_root, "staging-drift.json");
        UniversalVtfMonotonicComposeRequest request = Request(fixture, output, manifest) with
        {
            Observer = new ActionObserver(phase =>
            {
                if (phase != UniversalVtfMonotonicComposePhase.BeforeCommit)
                {
                    return;
                }

                string staging = Assert.Single(Directory.EnumerateDirectories(
                    _root,
                    "staging-drift-output.monotonic-owned-*"));
                File.WriteAllBytes(
                    Path.Combine(staging, "materials", "a.vtf"),
                    Enumerable.Repeat((byte)0x99, 80).ToArray());
            })
        };

        InvalidDataException error = await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfMonotonicComposer.ComposeAsync(request, CancellationToken.None));

        Assert.Equal("staging_changed_before_commit", error.Message);
        Assert.False(Directory.Exists(output));
        Assert.False(File.Exists(manifest));
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }

    private static UniversalVtfBuildReceipt Receipt(
        UniversalVtfTreeSnapshot final,
        UniversalVtfTreeSnapshot raw,
        UniversalVtfTreeSnapshot safe,
        IReadOnlyList<UniversalVtfManifestRow> rows) =>
        new(
            UniversalVtfProfileKind.Balanced,
            Hash('a'),
            Hash('b'),
            Hash('c'),
            Hash('d'),
            null,
            new UniversalVtfManifestTree(
                "raw",
                raw.Files.Count,
                raw.TotalBytes,
                raw.TreeSha256),
            new UniversalVtfManifestTree(
                "safe",
                safe.Files.Count,
                safe.TotalBytes,
                safe.TreeSha256),
            new UniversalVtfManifestTree(
                "final",
                final.Files.Count,
                final.TotalBytes,
                final.TreeSha256),
            rows);

    private static UniversalVtfManifestRow Row(
        UniversalVtfTreeSnapshot tree,
        string path,
        long rawBytes)
    {
        UniversalVtfTreeFile file = tree.Files.Single(item =>
            item.RelativePath.Equals(path, StringComparison.OrdinalIgnoreCase));
        var passingGate = new UniversalVtfGateResult(
            "common_rgb_psnr",
            30,
            ">=",
            25,
            0.2,
            Passed: true,
            Reason: "passed",
            Plane: new UniversalVtfPlaneKey(0, 0, 0),
            PlanePair: null);
        return new UniversalVtfManifestRow(
            path,
            "test",
            new[] { "diffuse" },
            Hash('1'),
            8,
            8,
            1,
            1,
            1,
            4,
            15,
            4,
            4,
            1,
            3,
            15,
            Authored: true,
            Generated: false,
            SelectedLevel: 1,
            ConservativeLevel: 1,
            BalancedLevel: 1,
            AggressiveLevel: 1,
            UniversalVtfFilterRoute.SrgbColor,
            new Dictionary<UniversalVtfProfileKind,
                IReadOnlyList<UniversalVtfProfileAttempt>>(),
            Array.Empty<UniversalVtfManifestRouteAttempt>(),
            passingGate,
            rawBytes,
            Hash('2'),
            100,
            Hash('3'),
            file.Length,
            file.Sha256,
            file.Length,
            file.Sha256,
            MandatoryFloorApplied: true,
            ByteFloor: false,
            Blocker: null,
            BcEligibilityMode: null,
            BcReceiptSha256: null,
            LowResolutionImageRegenerated: false,
            ResourceCount: 0);
    }

    private static UniversalVtfTreeAnchor Anchor(
        string name,
        UniversalVtfTreeSnapshot tree) =>
        new(name, tree.Files.Count, tree.TotalBytes, tree.TreeSha256);

    private static void AssertTree(
        UniversalVtfTreeSnapshot expected,
        UniversalVtfTreeSnapshot actual)
    {
        Assert.Equal(expected.Files.Count, actual.Files.Count);
        Assert.Equal(expected.TotalBytes, actual.TotalBytes);
        Assert.Equal(expected.TreeSha256, actual.TreeSha256);
    }

    private async Task<CorpusFixture> CreateCorpusAsync(string name)
    {
        string incumbentRoot = Path.Combine(_root, name + "-incumbent");
        string alternativeRoot = Path.Combine(_root, name + "-alternative");
        string rawRoot = Path.Combine(_root, name + "-protected-raw");
        string safeRoot = Path.Combine(_root, name + "-protected-safe");
        Write(incumbentRoot, "materials/a.vtf", 100, 0x11);
        Write(incumbentRoot, "materials/b.vtf", 100, 0x22);
        Write(incumbentRoot, "lua/shared.lua", 12, 0x33);
        Write(alternativeRoot, "materials/a.vtf", 80, 0x44);
        Write(alternativeRoot, "materials/b.vtf", 120, 0x55);
        Write(alternativeRoot, "lua/shared.lua", 12, 0x33);
        Write(rawRoot, "authority.bin", 1_000, 0x66);
        Write(rawRoot, "authority-2.bin", 1, 0x66);
        Write(safeRoot, "authority.bin", 500, 0x77);
        Write(safeRoot, "authority-2.bin", 1, 0x77);
        UniversalVtfTreeSnapshot incumbent = UniversalVtfTreeInventory.Capture(incumbentRoot);
        UniversalVtfTreeSnapshot alternative = UniversalVtfTreeInventory.Capture(alternativeRoot);
        UniversalVtfTreeSnapshot raw = UniversalVtfTreeInventory.Capture(rawRoot);
        UniversalVtfTreeSnapshot safe = UniversalVtfTreeInventory.Capture(safeRoot);
        string manifestPath = Path.Combine(_root, name + "-alternative.json");
        UniversalVtfManifestFrozen manifest = await UniversalVtfManifestWriter.WriteAsync(
            manifestPath,
            Receipt(alternative, raw, safe, new[]
            {
                Row(alternative, "materials/a.vtf", 200),
                Row(alternative, "materials/b.vtf", 200)
            }),
            CancellationToken.None);
        return new CorpusFixture(
            incumbentRoot,
            alternativeRoot,
            rawRoot,
            safeRoot,
            incumbent,
            manifestPath,
            manifest.Sha256);
    }

    private static UniversalVtfMonotonicComposeRequest Request(
        CorpusFixture fixture,
        string output,
        string manifest) =>
        new(
            UniversalVtfProfileKind.Balanced,
            fixture.IncumbentRoot,
            Anchor("adaptive-test", fixture.Incumbent),
            new[]
            {
                new UniversalVtfMonotonicAlternativeAuthority(
                    "universal-balanced",
                    fixture.AlternativeRoot,
                    fixture.AlternativeManifestPath,
                    fixture.AlternativeManifestSha256)
            },
            output,
            manifest,
            new[]
            {
                Protected("raw", fixture.RawRoot),
                Protected("safe", fixture.SafeRoot)
            });

    private static UniversalVtfMonotonicProtectedAuthority Protected(
        string name,
        string root)
    {
        UniversalVtfTreeSnapshot tree = UniversalVtfTreeInventory.Capture(root);
        return new UniversalVtfMonotonicProtectedAuthority(
            name,
            root,
            Anchor(name, tree));
    }

    private static void Write(string root, string relativePath, int count, byte value)
    {
        string path = Path.Combine(root,
            relativePath.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path, Enumerable.Repeat(value, count).ToArray());
    }

    private static string Hash(char value) => new(value, 64);

    private sealed record CorpusFixture(
        string IncumbentRoot,
        string AlternativeRoot,
        string RawRoot,
        string SafeRoot,
        UniversalVtfTreeSnapshot Incumbent,
        string AlternativeManifestPath,
        string AlternativeManifestSha256);

    private sealed class ActionObserver : IUniversalVtfMonotonicComposeObserver
    {
        private readonly Action<UniversalVtfMonotonicComposePhase> _action;

        internal ActionObserver(Action<UniversalVtfMonotonicComposePhase> action) =>
            _action = action;

        public void Observe(UniversalVtfMonotonicComposePhase phase) => _action(phase);
    }

    private sealed class InjectedPublicationException : Exception
    {
    }
}
