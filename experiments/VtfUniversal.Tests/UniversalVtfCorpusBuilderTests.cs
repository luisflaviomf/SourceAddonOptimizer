using System.Buffers.Binary;
using System.Security.Cryptography;
using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversal.Tests.Fixtures;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfCorpusBuilderTests
{
    [Fact]
    public async Task UsesSafeNonVtfAndRawVtf()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();

        UniversalVtfBuildResult result = await UniversalVtfCorpusBuilder.BuildAsync(
            corpus.Request,
            progress: null,
            CancellationToken.None);

        Assert.Equal(corpus.SafeModelSha256, HashFile(Path.Combine(
            result.Conservative.Root,
            "models",
            "a.mdl")));
        UniversalVtfManifest manifest = UniversalVtfManifestStore.Load(
            result.Conservative.ManifestPath,
            result.Conservative.ManifestSha256);
        UniversalVtfManifestRow row = manifest.Row("materials/a.vtf");
        Assert.Equal(corpus.RawVtfSha256, row.RawSha256);
        Assert.Equal(row.FinalSha256, HashFile(Path.Combine(
            result.Conservative.Root,
            "materials",
            "a.vtf")));
        Assert.InRange(row.FinalWidth, 1, 8);
        Assert.NotEqual(corpus.SafeVtfSha256, row.FinalSha256);
    }

    [Fact]
    public async Task MutationPublishesNothing()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        corpus.Request = corpus.Request with
        {
            Observer = new DelegateBuildObserver((phase, _) =>
            {
                if (phase == UniversalVtfBuildPhase.AfterSourceSnapshots)
                {
                    File.AppendAllText(Path.Combine(corpus.RawRoot, "materials", "a.vmt"), "\n// drift");
                }
            })
        };

        await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfCorpusBuilder.BuildAsync(corpus.Request, null, CancellationToken.None));
        Assert.All(corpus.FinalDestinations, path => Assert.False(Directory.Exists(path)));
        Assert.All(corpus.FinalManifests, path => Assert.False(File.Exists(path)));
    }

    [Theory]
    [InlineData("bridge")]
    [InlineData("runtime-caps")]
    public async Task WrongDeclaredInputHashPublishesNothing(string input)
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create(withRuntimeCaps: true);
        corpus.Request = input == "bridge"
            ? corpus.Request with { BcBridgeSha256 = new string('0', 64) }
            : corpus.Request with { RuntimeCapsSha256 = new string('0', 64) };

        await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfCorpusBuilder.BuildAsync(corpus.Request, null, CancellationToken.None));
        Assert.All(corpus.FinalDestinations, path => Assert.False(Directory.Exists(path)));
    }

    [Fact]
    public async Task VerifiedRuntimeCapsAreAppliedAndRecorded()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create(withRuntimeCaps: true);

        UniversalVtfBuildResult result = await UniversalVtfCorpusBuilder.BuildAsync(
            corpus.Request,
            null,
            CancellationToken.None);
        UniversalVtfManifest balanced = UniversalVtfManifestStore.Load(
            result.Balanced.ManifestPath,
            result.Balanced.ManifestSha256);
        UniversalVtfManifest aggressive = UniversalVtfManifestStore.Load(
            result.Aggressive.ManifestPath,
            result.Aggressive.ManifestSha256);

        Assert.Equal(2, balanced.Row("materials/a.vtf").SelectedLevel);
        Assert.Equal(2, aggressive.Row("materials/a.vtf").SelectedLevel);
        Assert.Equal(corpus.RuntimeCapsSha256, balanced.RuntimeCapsSha256);
    }

    [Fact]
    public async Task VerifiedBc3BridgeIsComposedAfterSpatialSelection()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create(
            withBc3Bridge: true);

        UniversalVtfBuildResult result = await UniversalVtfCorpusBuilder.BuildAsync(
            corpus.Request,
            null,
            CancellationToken.None);
        UniversalVtfManifest manifest = UniversalVtfManifestStore.Load(
            result.Balanced.ManifestPath,
            result.Balanced.ManifestSha256);
        UniversalVtfManifestRow row = manifest.Row("materials/a.vtf");
        VtfContainerLayout final = VtfContainerLayoutReader.Read(Path.Combine(
            result.Balanced.Root,
            "materials",
            "a.vtf"));

        Assert.Equal(Bc3EligibilityMode.PhysicalOpaque, row.BcEligibilityMode);
        Assert.Equal(corpus.Request.BcBridgeSha256, row.BcReceiptSha256);
        Assert.Equal(15, row.RawFormat);
        Assert.Equal(13, row.FinalFormat);
        Assert.Equal(13, final.Document.HighResFormat);
        Assert.NotEqual(20, final.Document.HighResFormat);
        Assert.True(row.FormatSavedBytes > 0);
    }

    [Fact]
    public async Task AuditRejectionPublishesNothingAndPreservesRecovery()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        string? recoveryRoot = null;
        corpus.Request = corpus.Request with
        {
            Observer = new DelegateBuildObserver((phase, context) =>
            {
                if (phase == UniversalVtfBuildPhase.BeforeAudit)
                {
                    recoveryRoot = context.ProfileStagingRoots.Values.First();
                    File.AppendAllText(Path.Combine(recoveryRoot, "models", "a.mdl"), "corrupt");
                }
            })
        };

        await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfCorpusBuilder.BuildAsync(corpus.Request, null, CancellationToken.None));
        Assert.All(corpus.FinalDestinations, path => Assert.False(Directory.Exists(path)));
        Assert.NotNull(recoveryRoot);
        Assert.True(Directory.Exists(recoveryRoot));
    }

    [Fact]
    public async Task LatePublicationCollisionRollsBackEveryProfile()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        string collision = corpus.FinalDestinations[0];
        corpus.Request = corpus.Request with
        {
            Observer = new DelegateBuildObserver((phase, _) =>
            {
                if (phase == UniversalVtfBuildPhase.BeforePublication &&
                    !Directory.Exists(collision))
                {
                    Directory.CreateDirectory(collision);
                }
            })
        };

        await Assert.ThrowsAsync<IOException>(() =>
            UniversalVtfCorpusBuilder.BuildAsync(corpus.Request, null, CancellationToken.None));
        Assert.True(Directory.Exists(collision));
        Assert.All(corpus.FinalDestinations.Skip(1), path => Assert.False(Directory.Exists(path)));
        Assert.All(corpus.FinalManifests, path => Assert.False(File.Exists(path)));
    }

    [Fact]
    public async Task PreCancelledBuildPublishesNothing()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            UniversalVtfCorpusBuilder.BuildAsync(corpus.Request, null, cancellation.Token));
        Assert.All(corpus.FinalDestinations, path => Assert.False(Directory.Exists(path)));
        Assert.All(corpus.FinalManifests, path => Assert.False(File.Exists(path)));
    }

    [Fact]
    public async Task InvalidOutputParentPublishesNothing()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        corpus.Request = corpus.Request with
        {
            OutputParent = Path.Combine(corpus.OutputParent, "missing")
        };

        await Assert.ThrowsAsync<DirectoryNotFoundException>(() =>
            UniversalVtfCorpusBuilder.BuildAsync(corpus.Request, null, CancellationToken.None));
        Assert.All(corpus.FinalDestinations, path => Assert.False(Directory.Exists(path)));
    }

    private static string HashFile(string path) => Convert.ToHexString(
        SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
}

internal sealed class SyntheticUniversalCorpus : IDisposable
{
    private readonly string _root;

    private SyntheticUniversalCorpus(string root) => _root = root;

    internal string RawRoot => Path.Combine(_root, "raw");
    internal string SafeRoot => Path.Combine(_root, "safe");
    internal string OutputParent => Path.Combine(_root, "output");
    internal string ReportRoot => Path.Combine(_root, "reports");
    internal string SafeModelSha256 { get; private set; } = string.Empty;
    internal string RawVtfSha256 { get; private set; } = string.Empty;
    internal string SafeVtfSha256 { get; private set; } = string.Empty;
    internal string? RuntimeCapsSha256 { get; private set; }
    internal UniversalVtfBuildRequest Request { get; set; } = null!;

    internal IReadOnlyList<string> FinalDestinations =>
        Enum.GetValues<UniversalVtfProfileKind>()
            .Select(profile => Path.Combine(OutputParent, UniversalVtfCorpusBuilder.ProfileName(profile)))
            .ToArray();

    internal IReadOnlyList<string> FinalManifests =>
        Enum.GetValues<UniversalVtfProfileKind>()
            .Select(profile => Path.Combine(
                ReportRoot,
                UniversalVtfCorpusBuilder.ProfileName(profile) + ".manifest.json"))
            .ToArray();

    internal static SyntheticUniversalCorpus Create(
        bool withRuntimeCaps = false,
        bool withBc3Bridge = false)
    {
        string root = Path.Combine(
            Path.GetTempPath(),
            "GmodAddonOptimizer",
            "SyntheticUniversalCorpus",
            Guid.NewGuid().ToString("N"));
        var corpus = new SyntheticUniversalCorpus(root);
        Directory.CreateDirectory(corpus.RawRoot);
        Directory.CreateDirectory(corpus.SafeRoot);
        Directory.CreateDirectory(corpus.OutputParent);
        Directory.CreateDirectory(corpus.ReportRoot);
        corpus.PopulateAuthorities(withBc3Bridge ? 15 : 13);

        UniversalVtfTreeSnapshot rawTree = UniversalVtfTreeInventory.Capture(corpus.RawRoot);
        UniversalVtfTreeSnapshot safeTree = UniversalVtfTreeInventory.Capture(corpus.SafeRoot);
        Bc3RawBridgeRow[] bridgeRows = withBc3Bridge
            ? new[] { corpus.CreateBc3BridgeRow() }
            : Array.Empty<Bc3RawBridgeRow>();
        var bridge = new Bc3RawBridge(
            Bc3RawBridgeStore.Schema,
            Bc3ApprovedReceipt.RequiredPriorManifestSha256,
            rawTree.TreeSha256,
            rawTree.TotalBytes,
            rawTree.Files.Count,
            safeTree.TreeSha256,
            safeTree.TotalBytes,
            safeTree.Files.Count,
            bridgeRows.Length,
            0,
            0,
            0,
            bridgeRows);
        Bc3RawBridgeFrozen frozenBridge = Bc3RawBridgeStore.WriteCanonical(
            Path.Combine(corpus.ReportRoot, "bridge.json"),
            bridge);

        string? capsPath = null;
        if (withRuntimeCaps)
        {
            capsPath = Path.Combine(corpus.ReportRoot, "caps.json");
            UniversalVtfRuntimeCapsFrozen caps = UniversalVtfRuntimeCaps.WriteCanonical(
                capsPath,
                new[]
                {
                    new UniversalVtfRuntimeCap(
                        "materials/a.vtf",
                        UniversalVtfProfileKind.Conservative,
                        2,
                        "synthetic-runtime-regression")
                });
            corpus.RuntimeCapsSha256 = caps.Sha256;
        }

        UniversalVtfPolicyReceipt policy =
            UniversalVtfCorpusBuilder.ResolveEmbeddedPolicyReceipt(frozenBridge.Sha256);
        corpus.Request = new UniversalVtfBuildRequest(
            corpus.RawRoot,
            corpus.SafeRoot,
            corpus.OutputParent,
            corpus.ReportRoot,
            frozenBridge.Path,
            frozenBridge.Sha256,
            capsPath,
            corpus.RuntimeCapsSha256,
            Enum.GetValues<UniversalVtfProfileKind>(),
            policy.PolicyFingerprint,
            new UniversalVtfBuildAnchors(
                new UniversalVtfTreeAnchor(
                    "synthetic_raw",
                    rawTree.Files.Count,
                    rawTree.TotalBytes,
                    rawTree.TreeSha256),
                new UniversalVtfTreeAnchor(
                    "synthetic_safe",
                    safeTree.Files.Count,
                    safeTree.TotalBytes,
                    safeTree.TreeSha256),
                ExpectedVtfCount: 1,
                RequireFrozenBcPartition: false));
        return corpus;
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }

    private void PopulateAuthorities(int highResolutionFormat)
    {
        string rawMaterials = Path.Combine(RawRoot, "materials");
        string safeMaterials = Path.Combine(SafeRoot, "materials");
        string rawModels = Path.Combine(RawRoot, "models");
        string safeModels = Path.Combine(SafeRoot, "models");
        Directory.CreateDirectory(rawMaterials);
        Directory.CreateDirectory(safeMaterials);
        Directory.CreateDirectory(rawModels);
        Directory.CreateDirectory(safeModels);

        using VtfFixtureFile fixture = VtfFixtureBuilder.WriteNomip72(
            16,
            16,
            highResolutionFormat);
        string rawVtf = Path.Combine(rawMaterials, "a.vtf");
        File.Copy(fixture.Path, rawVtf);
        MakeSolid(rawVtf);
        string safeVtf = Path.Combine(safeMaterials, "a.vtf");
        File.Copy(rawVtf, safeVtf);
        File.WriteAllText(
            Path.Combine(rawMaterials, "a.vmt"),
            "\"VertexLitGeneric\" { \"$basetexture\" \"a\" }");
        File.Copy(Path.Combine(rawMaterials, "a.vmt"), Path.Combine(safeMaterials, "a.vmt"));
        File.WriteAllBytes(Path.Combine(rawModels, "a.mdl"), new byte[] { 1, 2, 3, 4 });
        File.WriteAllBytes(Path.Combine(safeModels, "a.mdl"), new byte[] { 9, 8, 7, 6 });

        RawVtfSha256 = HashFile(rawVtf);
        SafeVtfSha256 = HashFile(safeVtf);
        SafeModelSha256 = HashFile(Path.Combine(safeModels, "a.mdl"));
    }

    private Bc3RawBridgeRow CreateBc3BridgeRow()
    {
        UniversalVtfInventoryEntry raw = UniversalVtfInventoryScanner.Scan(RawRoot)
            .Entries.Single();
        UniversalVtfTreeFile safe = UniversalVtfTreeInventory.Capture(SafeRoot)
            .Files.Single(file => file.RelativePath.Equals(
                "materials/a.vtf",
                StringComparison.OrdinalIgnoreCase));
        Bc3RawMipReceipt[] mips = UniversalVtfLevels.Enumerate(raw.Width, raw.Height)
            .Select(level =>
            {
                UniversalVtfDimensions dimensions = UniversalVtfLevels.At(
                    raw.Width,
                    raw.Height,
                    level);
                long samples = checked((long)dimensions.Width * dimensions.Height);
                return new Bc3RawMipReceipt(
                    level,
                    dimensions.Width,
                    dimensions.Height,
                    1,
                    1,
                    Math.Max(16, samples),
                    new string('a', 64),
                    new Bc3AlphaReceipt(samples, 255, 255, 1, true));
            })
            .ToArray();
        return new Bc3RawBridgeRow(
            "materials/a.vtf",
            raw.Bytes,
            raw.Sha256,
            safe.Length,
            safe.Sha256,
            safe.Length,
            safe.Sha256,
            new string('b', 64),
            15,
            Bc3EligibilityMode.PhysicalOpaque,
            "materials/a.vtf",
            PriorSafeColorBlocksEquivalent: false,
            mips);
    }

    private static void MakeSolid(string path)
    {
        VtfContainerLayout layout = VtfContainerLayoutReader.Read(path);
        byte[] bytes = File.ReadAllBytes(path);
        foreach (VtfSubresourceRange range in layout.Subresources)
        {
            int blockBytes = layout.Document.HighResFormat == 15 ? 16 : 8;
            for (long offset = range.Range.Offset; offset < range.Range.End; offset += blockBytes)
            {
                int colorOffset = checked((int)offset + (blockBytes == 16 ? 8 : 0));
                if (blockBytes == 16)
                {
                    bytes[checked((int)offset)] = 255;
                    bytes[checked((int)offset + 1)] = 255;
                    bytes.AsSpan(checked((int)offset + 2), 6).Clear();
                }

                BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(colorOffset), 0xf800);
                BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(colorOffset + 2), 0x07e0);
                BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(colorOffset + 4), 0);
            }
        }

        File.WriteAllBytes(path, bytes);
    }

    private static string HashFile(string path) => Convert.ToHexString(
        SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
}

internal sealed class DelegateBuildObserver : IUniversalVtfBuildObserver
{
    private readonly Action<UniversalVtfBuildPhase, UniversalVtfBuildObservation> _action;

    internal DelegateBuildObserver(
        Action<UniversalVtfBuildPhase, UniversalVtfBuildObservation> action) => _action = action;

    public void Observe(UniversalVtfBuildPhase phase, UniversalVtfBuildObservation observation) =>
        _action(phase, observation);
}
