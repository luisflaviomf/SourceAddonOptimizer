using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfLegacyCheckpointImporterTests :
    IClassFixture<UniversalVtfLegacyFixture>
{
    private readonly UniversalVtfLegacyFixture _fixture;

    public UniversalVtfLegacyCheckpointImporterTests(UniversalVtfLegacyFixture fixture) =>
        _fixture = fixture;

    [Fact]
    public async Task StrictImportCanonicalizesNamedNanAndWarmBuildUsesZeroRecalculation()
    {
        string conservativeManifest = _fixture.ManifestPath(
            UniversalVtfProfileKind.Conservative);
        InvalidDataException normalError = Assert.Throws<InvalidDataException>(() =>
            UniversalVtfManifestStore.Load(
                conservativeManifest,
                _fixture.ManifestHashes[UniversalVtfProfileKind.Conservative]));
        Assert.Contains("manifest_invalid_json", normalError.Message);

        string cacheRoot = _fixture.NewCacheRoot();
        var cache = new UniversalVtfPreparationCache(cacheRoot);
        UniversalVtfLegacyImportResult imported =
            await UniversalVtfLegacyCheckpointImporter.ImportAsync(
                _fixture.Request(),
                cache,
                null,
                CancellationToken.None);

        Assert.Equal(1, imported.TotalVtfs);
        Assert.Equal(1, imported.ImportedVtfs);
        Assert.Equal(0, imported.RecalculatedVtfs);
        Assert.Equal(1, imported.UniqueFinalBlobs);
        string analysisPath = Assert.Single(Directory.GetFiles(
            Path.Combine(cacheRoot, "v1", "analysis"),
            "receipt.json",
            SearchOption.AllDirectories));
        string analysisJson = await File.ReadAllTextAsync(analysisPath);
        Assert.Contains(":null", analysisJson, StringComparison.Ordinal);
        Assert.DoesNotContain("\"NaN\"", analysisJson, StringComparison.Ordinal);
        UniversalVtfAnalysisReceipt analysis =
            UniversalVtfCanonicalJson.Deserialize<UniversalVtfAnalysisReceipt>(
                await File.ReadAllBytesAsync(analysisPath));
        string completionPath = Assert.Single(Directory.GetFiles(
            Path.Combine(cacheRoot, "v1", "completed"),
            "receipt.json",
            SearchOption.AllDirectories));
        UniversalVtfCompletionReceipt completion =
            UniversalVtfCanonicalJson.Deserialize<UniversalVtfCompletionReceipt>(
                await File.ReadAllBytesAsync(completionPath));
        UniversalVtfCacheLookup lookup = await cache.LookupAsync(
            analysis.Identity,
            completion.Identity,
            CancellationToken.None);
        Assert.Equal(UniversalVtfCacheHitKind.Completed, lookup.HitKind);

        _fixture.Corpus.RenewOutputDestinations();
        _fixture.Corpus.Request = _fixture.Corpus.Request with { CacheRoot = cacheRoot };
        UniversalVtfBuildResult warm = await UniversalVtfCorpusBuilder.BuildAsync(
            _fixture.Corpus.Request,
            null,
            CancellationToken.None);
        Assert.Equal(1, warm.Cache.CompletedHits);
        Assert.Equal(0, warm.Cache.RecalculatedVtfs);
        Assert.Equal(
            _fixture.FinalTrees[UniversalVtfProfileKind.Balanced].TreeSha256,
            warm.Balanced.TreeSha256);
    }

    [Theory]
    [InlineData("log")]
    [InlineData("conservative")]
    public async Task WrongPinnedHashRejectsBeforeImport(string authority)
    {
        UniversalVtfLegacyImportRequest request = _fixture.Request();
        request = authority == "log"
            ? request with { LegacyBuildLogSha256 = new string('0', 64) }
            : request with
            {
                ManifestSha256 = request.ManifestSha256.ToDictionary(
                    pair => pair.Key,
                    pair => pair.Key == UniversalVtfProfileKind.Conservative
                        ? new string('0', 64)
                        : pair.Value)
            };
        var cache = new UniversalVtfPreparationCache(_fixture.NewCacheRoot());

        InvalidDataException error = await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfLegacyCheckpointImporter.ImportAsync(
                request,
                cache,
                null,
                CancellationToken.None));

        Assert.Contains("sha256", error.Message, StringComparison.OrdinalIgnoreCase);
        Assert.Empty(Directory.GetFiles(
            _fixture.LastCacheRoot,
            "receipt.json",
            SearchOption.AllDirectories));
    }

    [Fact]
    public async Task CrossProfileRowDriftIsRejectedEvenWhenTamperedAuthoritiesAreRepinned()
    {
        UniversalVtfLegacyImportRequest request = _fixture.CloneRequest();
        string manifestPath = Path.Combine(
            request.LegacyRoot,
            UniversalVtfCorpusBuilder.ProfileName(UniversalVtfProfileKind.Balanced) +
            ".manifest.json");
        JsonNode manifest = JsonNode.Parse(File.ReadAllBytes(manifestPath))!;
        manifest["rows"]![0]!["routeAttempts"]![0]!["metrics"]!["planes"]![0]!
            ["semanticValues"]!["legacy_test_nan"] = 123.0;
        File.WriteAllText(manifestPath, manifest.ToJsonString());
        request = Repin(request, UniversalVtfProfileKind.Balanced, manifestPath);
        var cache = new UniversalVtfPreparationCache(_fixture.NewCacheRoot());

        InvalidDataException error = await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfLegacyCheckpointImporter.ImportAsync(
                request,
                cache,
                null,
                CancellationToken.None));

        Assert.Contains("cross_profile_row_drift", error.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task FinalTreeDriftIsRejectedBeforeAnyReceiptIsPublished()
    {
        UniversalVtfLegacyImportRequest request = _fixture.CloneRequest();
        string final = Assert.Single(Directory.GetFiles(
            Path.Combine(
                request.LegacyRoot,
                UniversalVtfCorpusBuilder.ProfileName(UniversalVtfProfileKind.Conservative)),
            "*.vtf",
            SearchOption.AllDirectories));
        await using (var stream = new FileStream(
                         final,
                         FileMode.Append,
                         FileAccess.Write,
                         FileShare.None))
        {
            await stream.WriteAsync(new byte[] { 0x7f });
        }
        var cache = new UniversalVtfPreparationCache(_fixture.NewCacheRoot());

        InvalidDataException error = await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfLegacyCheckpointImporter.ImportAsync(
                request,
                cache,
                null,
                CancellationToken.None));

        Assert.Contains("tree_drift", error.Message, StringComparison.Ordinal);
        Assert.Empty(Directory.GetFiles(
            _fixture.LastCacheRoot,
            "receipt.json",
            SearchOption.AllDirectories));
    }

    [Fact]
    public async Task HashPinnedUtf16LittleEndianBuildLogIsAccepted()
    {
        UniversalVtfLegacyImportRequest request = _fixture.CloneRequest();
        string text = File.ReadAllText(request.LegacyBuildLogPath, Encoding.UTF8);
        File.WriteAllText(request.LegacyBuildLogPath, text, Encoding.Unicode);
        request = request with
        {
            LegacyBuildLogSha256 = HashFile(request.LegacyBuildLogPath)
        };

        UniversalVtfLegacyImportResult result =
            await UniversalVtfLegacyCheckpointImporter.ImportAsync(
                request,
                new UniversalVtfPreparationCache(_fixture.NewCacheRoot()),
                null,
                CancellationToken.None);

        Assert.Equal(1, result.ImportedVtfs);
        Assert.Equal(0, result.RecalculatedVtfs);
    }

    private static UniversalVtfLegacyImportRequest Repin(
        UniversalVtfLegacyImportRequest request,
        UniversalVtfProfileKind profile,
        string manifestPath)
    {
        var hashes = request.ManifestSha256.ToDictionary(pair => pair.Key, pair => pair.Value);
        hashes[profile] = HashFile(manifestPath);
        JsonNode log = JsonNode.Parse(File.ReadAllText(request.LegacyBuildLogPath))!;
        JsonNode profileRow = log["profiles"]!.AsArray().Single(item =>
            item!["profile"]!.GetValue<string>() == profile.ToString().ToLowerInvariant())!;
        profileRow["manifestSha256"] = hashes[profile];
        File.WriteAllText(request.LegacyBuildLogPath, log.ToJsonString() + Environment.NewLine);
        return request with
        {
            ManifestSha256 = hashes,
            LegacyBuildLogSha256 = HashFile(request.LegacyBuildLogPath)
        };
    }

    private static string HashFile(string path) => Convert.ToHexString(
        SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
}

public sealed class UniversalVtfLegacyFixture : IDisposable
{
    private readonly string _buildLog;
    private readonly List<string> _clones = new();

    public UniversalVtfLegacyFixture()
    {
        Corpus = SyntheticUniversalCorpus.Create();
        UniversalVtfBuildResult built = UniversalVtfCorpusBuilder.BuildAsync(
                Corpus.Request,
                null,
                CancellationToken.None)
            .GetAwaiter()
            .GetResult();
        LegacyRoot = Corpus.OutputParent;
        var manifestHashes = new Dictionary<UniversalVtfProfileKind, string>();
        var finalTrees = new Dictionary<UniversalVtfProfileKind, UniversalVtfManifestTree>();
        foreach ((UniversalVtfProfileKind profile, UniversalVtfBuiltProfile output) in new[]
                 {
                     (UniversalVtfProfileKind.Conservative, built.Conservative),
                     (UniversalVtfProfileKind.Balanced, built.Balanced),
                     (UniversalVtfProfileKind.Aggressive, built.Aggressive)
                 })
        {
            string destination = ManifestPath(profile);
            File.Copy(output.ManifestPath, destination);
            AddLegacyNamedNan(destination);
            manifestHashes[profile] = HashFile(destination);
            UniversalVtfTreeSnapshot tree = UniversalVtfTreeInventory.Capture(output.Root);
            finalTrees[profile] = new UniversalVtfManifestTree(
                "final",
                tree.Files.Count,
                tree.TotalBytes,
                tree.TreeSha256);
        }

        _buildLog = Path.Combine(LegacyRoot, "legacy.build.jsonl");
        File.WriteAllText(_buildLog, JsonSerializer.Serialize(new
        {
            status = "CLEAN",
            schema = "universal-vtf-build-result-v1",
            policyFingerprint = Corpus.Request.PolicyFingerprint,
            toolchainSha256 = built.Cache.PreparationAlgorithmFingerprint.Length == 64
                ? ResolveToolchain()
                : throw new InvalidOperationException(),
            bcBridgeSha256 = Corpus.Request.BcBridgeSha256,
            runtimeCapsSha256 = (string?)null,
            profiles = Enum.GetValues<UniversalVtfProfileKind>().Select(profile => new
            {
                profile = profile.ToString().ToLowerInvariant(),
                manifestSha256 = manifestHashes[profile],
                bytes = finalTrees[profile].Bytes,
                treeSha256 = finalTrees[profile].TreeSha256
            })
        }) + Environment.NewLine);
        ManifestHashes = manifestHashes;
        FinalTrees = finalTrees;
        BuildLogSha256 = HashFile(_buildLog);
    }

    internal SyntheticUniversalCorpus Corpus { get; }

    internal string LegacyRoot { get; }

    internal string BuildLogSha256 { get; }

    internal IReadOnlyDictionary<UniversalVtfProfileKind, string> ManifestHashes { get; }

    internal IReadOnlyDictionary<UniversalVtfProfileKind, UniversalVtfManifestTree> FinalTrees
    {
        get;
    }

    internal string LastCacheRoot { get; private set; } = string.Empty;

    internal string ManifestPath(UniversalVtfProfileKind profile) => Path.Combine(
        LegacyRoot,
        UniversalVtfCorpusBuilder.ProfileName(profile) + ".manifest.json");

    internal string NewCacheRoot()
    {
        LastCacheRoot = Path.Combine(
            Path.GetDirectoryName(Corpus.RawRoot)!,
            $"import-cache-{Guid.NewGuid():N}");
        return LastCacheRoot;
    }

    internal UniversalVtfLegacyImportRequest Request() => new(
        Corpus.RawRoot,
        Corpus.SafeRoot,
        Corpus.Request.BcBridgePath,
        Corpus.Request.BcBridgeSha256,
        LegacyRoot,
        _buildLog,
        BuildLogSha256,
        ManifestHashes,
        Corpus.Request.Anchors!);

    internal UniversalVtfLegacyImportRequest CloneRequest()
    {
        string clone = Path.Combine(
            Path.GetTempPath(),
            "GmodAddonOptimizer",
            "UniversalVtfLegacyClones",
            $"legacy-clone-{Guid.NewGuid():N}");
        _clones.Add(clone);
        foreach (string source in Directory.GetFiles(LegacyRoot, "*", SearchOption.AllDirectories))
        {
            string destination = Path.Combine(clone, Path.GetRelativePath(LegacyRoot, source));
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.Copy(source, destination);
        }

        return Request() with
        {
            LegacyRoot = clone,
            LegacyBuildLogPath = Path.Combine(clone, Path.GetRelativePath(LegacyRoot, _buildLog))
        };
    }

    public void Dispose()
    {
        foreach (string clone in _clones.Where(Directory.Exists))
        {
            Directory.Delete(clone, recursive: true);
        }

        Corpus.Dispose();
    }

    private string ResolveToolchain() => UniversalVtfCorpusBuilder
        .ResolveEmbeddedPolicyReceipt(Corpus.Request.BcBridgeSha256)
        .ToolchainSha256;

    private static void AddLegacyNamedNan(string manifestPath)
    {
        JsonNode root = JsonNode.Parse(File.ReadAllBytes(manifestPath))
            ?? throw new InvalidDataException("synthetic_legacy_manifest_null");
        JsonArray rows = root["rows"]!.AsArray();
        foreach (JsonNode? row in rows)
        {
            JsonObject semantic = row!["routeAttempts"]![0]!["metrics"]!["planes"]![0]!
                ["semanticValues"]!.AsObject();
            semantic["legacy_test_nan"] = "NaN";
        }

        File.WriteAllText(
            manifestPath,
            root.ToJsonString(new JsonSerializerOptions { WriteIndented = false }));
    }

    private static string HashFile(string path) => Convert.ToHexString(
        SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
}
