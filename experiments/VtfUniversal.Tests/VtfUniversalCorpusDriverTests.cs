using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversalCorpusDriver;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class VtfUniversalCorpusDriverTests : IDisposable
{
    private readonly string _root = Path.Combine(
        Path.GetTempPath(),
        "GmodAddonOptimizer",
        "VtfUniversalCorpusDriverTests",
        Guid.NewGuid().ToString("N"));

    public VtfUniversalCorpusDriverTests() => Directory.CreateDirectory(_root);

    [Fact]
    public async Task RejectsSameAuthorityAndDestination()
    {
        string same = Directory.CreateDirectory(Path.Combine(_root, "same")).FullName;

        int code = await Driver.RunAsync(new[]
        {
            "build", "--raw-root", same, "--safe-root", same,
            "--output-parent", same, "--profiles", "all",
            "--bc-bridge", Path.Combine(_root, "bridge.json"),
            "--bc-bridge-sha256", Hash('a')
        }, new RecordingCorpusBuilder());

        Assert.Equal(2, code);
    }

    [Fact]
    public async Task DelegatesAllProfilesAsOneRequest()
    {
        var fake = new RecordingCorpusBuilder();
        string[] args = ValidBuildArgs();

        int code = await Driver.RunAsync(args, fake);

        Assert.Equal(0, code);
        UniversalVtfBuildRequest request = Assert.Single(fake.Requests);
        Assert.Equal(3, request.Profiles.Count);
        Assert.Equal(
            Enum.GetValues<UniversalVtfProfileKind>(),
            request.Profiles);
        Assert.Equal(Hash('f'), request.PolicyFingerprint);
    }

    [Theory]
    [InlineData("--bc-bridge-sha256")]
    [InlineData("--bc-bridge")]
    [InlineData("--runtime-caps-sha256")]
    [InlineData("--runtime-caps")]
    public async Task RejectsPathWithoutItsHashOrHashWithoutItsPath(string removed)
    {
        string[] args = ValidBuildArgs(withRuntimeCaps: true);
        args = RemoveOption(args, removed);

        int code = await Driver.RunAsync(args, new RecordingCorpusBuilder());

        Assert.Equal(2, code);
    }

    [Fact]
    public async Task RejectsAnyProfileSetOtherThanAll()
    {
        string[] args = ValidBuildArgs();
        args[Array.IndexOf(args, "--profiles") + 1] = "balanced";

        int code = await Driver.RunAsync(args, new RecordingCorpusBuilder());

        Assert.Equal(2, code);
    }

    [Fact]
    public async Task CancellationUsesExit130WithoutCallingBuilder()
    {
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();
        var fake = new RecordingCorpusBuilder();

        int code = await Driver.RunAsync(ValidBuildArgs(), fake, cancellation.Token);

        Assert.Equal(130, code);
        Assert.Empty(fake.Requests);
    }

    [Fact]
    public async Task SeedCacheDelegatesPinnedLegacyAuthorities()
    {
        var fake = new RecordingLegacyImporter();

        int code = await Driver.RunAsync(
            ValidSeedArgs(),
            null,
            CancellationToken.None,
            fake);

        Assert.Equal(0, code);
        UniversalVtfLegacyImportRequest request = Assert.Single(fake.Requests);
        Assert.Equal(Hash('a'), request.BcBridgeSha256);
        Assert.Equal(Hash('b'), request.LegacyBuildLogSha256);
        Assert.Equal(Hash('c'), request.ManifestSha256[UniversalVtfProfileKind.Conservative]);
        Assert.Equal(Hash('d'), request.ManifestSha256[UniversalVtfProfileKind.Balanced]);
        Assert.Equal(Hash('e'), request.ManifestSha256[UniversalVtfProfileKind.Aggressive]);
        Assert.Equal(UniversalVtfCorpusBuilder.DefaultAnchors, request.Anchors);
        Assert.Equal(Path.Combine(_root, "seed-cache"), fake.CacheRoots.Single());
    }

    [Fact]
    public async Task SeedCacheRejectsCacheInsideLegacyAuthority()
    {
        string[] args = ValidSeedArgs();
        string legacy = args[Array.IndexOf(args, "--legacy-root") + 1];
        args[Array.IndexOf(args, "--cache-root") + 1] = Path.Combine(legacy, "cache");
        var fake = new RecordingLegacyImporter();

        int code = await Driver.RunAsync(
            args,
            null,
            CancellationToken.None,
            fake);

        Assert.Equal(2, code);
        Assert.Empty(fake.Requests);
    }

    [Fact]
    public async Task ComposeMonotonicDelegatesPinnedIncumbentAndAlternative()
    {
        var fake = new RecordingMonotonicComposer();
        string incumbent = Directory.CreateDirectory(Path.Combine(_root, "compose-incumbent")).FullName;
        string raw = Directory.CreateDirectory(Path.Combine(_root, "compose-raw")).FullName;
        string safe = Directory.CreateDirectory(Path.Combine(_root, "compose-safe")).FullName;
        string alternative = Directory.CreateDirectory(Path.Combine(_root, "compose-alternative")).FullName;
        string alternativeManifest = Path.Combine(_root, "compose-alternative.json");
        File.WriteAllText(alternativeManifest, "{}");
        string output = Path.Combine(_root, "compose-output");
        string manifest = Path.Combine(_root, "compose-result.json");

        int code = await Driver.RunAsync(new[]
        {
            "compose-monotonic",
            "--profile", "balanced",
            "--incumbent-root", incumbent,
            "--incumbent-name", "adaptive-balanced",
            "--incumbent-file-count", "3664",
            "--incumbent-bytes", "385314332",
            "--incumbent-tree-sha256", Hash('1'),
            "--raw-root", raw,
            "--safe-root", safe,
            "--alternative-name", "universal-balanced",
            "--alternative-root", alternative,
            "--alternative-manifest", alternativeManifest,
            "--alternative-manifest-sha256", Hash('2'),
            "--output-root", output,
            "--manifest", manifest
        }, null, CancellationToken.None, null, fake);

        Assert.Equal(0, code);
        UniversalVtfMonotonicComposeRequest request = Assert.Single(fake.Requests);
        Assert.Equal(UniversalVtfProfileKind.Balanced, request.Profile);
        Assert.Equal(3_664, request.IncumbentAnchor.FileCount);
        Assert.Equal(385_314_332, request.IncumbentAnchor.TotalBytes);
        Assert.Equal(Hash('1'), request.IncumbentAnchor.TreeSha256);
        Assert.Equal(new[] { "raw", "safe" },
            request.ProtectedAuthorities!.Select(authority => authority.Name));
        Assert.Equal(UniversalVtfCorpusBuilder.DefaultAnchors.RawAnchor,
            request.ProtectedAuthorities![0].Anchor);
        Assert.Equal(UniversalVtfCorpusBuilder.DefaultAnchors.SafeAnchor,
            request.ProtectedAuthorities[1].Anchor);
        Assert.Equal("universal-balanced", Assert.Single(request.Alternatives).Name);
        Assert.Equal(output, request.OutputRoot);
        Assert.Equal(manifest, request.ManifestPath);
    }

    [Fact]
    public async Task ComposeMonotonicClassifiesRollbackAggregateAsTransformFailure()
    {
        var fake = new RecordingMonotonicComposer
        {
            Error = new AggregateException("publication and rollback failed")
        };
        string incumbent = Directory.CreateDirectory(Path.Combine(_root, "aggregate-incumbent"))
            .FullName;
        string raw = Directory.CreateDirectory(Path.Combine(_root, "aggregate-raw")).FullName;
        string safe = Directory.CreateDirectory(Path.Combine(_root, "aggregate-safe")).FullName;
        string alternative = Directory.CreateDirectory(Path.Combine(_root, "aggregate-alternative"))
            .FullName;
        string alternativeManifest = Path.Combine(_root, "aggregate-alternative.json");
        File.WriteAllText(alternativeManifest, "{}");

        int code = await Driver.RunAsync(new[]
        {
            "compose-monotonic",
            "--profile", "balanced",
            "--incumbent-root", incumbent,
            "--incumbent-name", "adaptive-balanced",
            "--incumbent-file-count", "3664",
            "--incumbent-bytes", "385314332",
            "--incumbent-tree-sha256", Hash('1'),
            "--raw-root", raw,
            "--safe-root", safe,
            "--alternative-name", "universal-balanced",
            "--alternative-root", alternative,
            "--alternative-manifest", alternativeManifest,
            "--alternative-manifest-sha256", Hash('2'),
            "--output-root", Path.Combine(_root, "aggregate-output"),
            "--manifest", Path.Combine(_root, "aggregate-result.json")
        }, null, CancellationToken.None, null, fake);

        Assert.Equal(4, code);
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }

    private string[] ValidBuildArgs(bool withRuntimeCaps = false)
    {
        string raw = Directory.CreateDirectory(Path.Combine(_root, "raw")).FullName;
        string safe = Directory.CreateDirectory(Path.Combine(_root, "safe")).FullName;
        var values = new List<string>
        {
            "build",
            "--raw-root", raw,
            "--safe-root", safe,
            "--output-parent", Path.Combine(_root, Guid.NewGuid().ToString("N")),
            "--cache-root", Path.Combine(_root, "cache"),
            "--profiles", "all",
            "--bc-bridge", Path.Combine(_root, "bridge.json"),
            "--bc-bridge-sha256", Hash('a')
        };
        if (withRuntimeCaps)
        {
            values.AddRange(new[]
            {
                "--runtime-caps", Path.Combine(_root, "caps.json"),
                "--runtime-caps-sha256", Hash('b')
            });
        }

        return values.ToArray();
    }

    private string[] ValidSeedArgs()
    {
        string raw = Directory.CreateDirectory(Path.Combine(_root, "seed-raw")).FullName;
        string safe = Directory.CreateDirectory(Path.Combine(_root, "seed-safe")).FullName;
        string legacy = Directory.CreateDirectory(Path.Combine(_root, "legacy")).FullName;
        string bridge = Path.Combine(_root, "bridge.json");
        string log = Path.Combine(legacy, "legacy.build.jsonl");
        File.WriteAllText(bridge, "{}");
        File.WriteAllText(log, "{}");
        foreach (string profile in new[] { "conservative", "balanced", "aggressive" })
        {
            File.WriteAllText(Path.Combine(legacy, profile + ".manifest.json"), "{}");
        }

        return new[]
        {
            "seed-cache",
            "--raw-root", raw,
            "--safe-root", safe,
            "--bc-bridge", bridge,
            "--bc-bridge-sha256", Hash('a'),
            "--legacy-root", legacy,
            "--legacy-build-log", log,
            "--legacy-build-log-sha256", Hash('b'),
            "--conservative-manifest-sha256", Hash('c'),
            "--balanced-manifest-sha256", Hash('d'),
            "--aggressive-manifest-sha256", Hash('e'),
            "--cache-root", Path.Combine(_root, "seed-cache")
        };
    }

    private static string[] RemoveOption(string[] args, string option)
    {
        int index = Array.IndexOf(args, option);
        Assert.True(index > 0);
        return args.Where((_, candidate) => candidate != index && candidate != index + 1).ToArray();
    }

    private static string Hash(char value) => new(value, 64);

    private sealed class RecordingCorpusBuilder : IUniversalVtfCorpusBuildService
    {
        internal List<UniversalVtfBuildRequest> Requests { get; } = new();

        public UniversalVtfPolicyReceipt ResolvePolicy(string bridgeSha256) =>
            new(Hash('f'), Hash('e'));

        public Task<UniversalVtfBuildResult> BuildAsync(
            UniversalVtfBuildRequest request,
            IProgress<UniversalVtfBuildProgress>? progress,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            Requests.Add(request);
            UniversalVtfBuiltProfile Profile(UniversalVtfProfileKind profile) =>
                new(profile, "root", "manifest", Hash('d'), 1, Hash('c'));
            return Task.FromResult(new UniversalVtfBuildResult(
                Profile(UniversalVtfProfileKind.Conservative),
                Profile(UniversalVtfProfileKind.Balanced),
                Profile(UniversalVtfProfileKind.Aggressive),
                new UniversalVtfCacheTelemetry(
                    1,
                    1,
                    0,
                    0,
                    0,
                    1,
                    0,
                    100,
                    0.1,
                    0.2,
                    "test-cache",
                    Hash('b')),
                "journal",
                Hash('a')));
        }
    }

    private sealed class RecordingLegacyImporter : IUniversalVtfLegacyImportService
    {
        internal List<UniversalVtfLegacyImportRequest> Requests { get; } = new();

        internal List<string> CacheRoots { get; } = new();

        public Task<UniversalVtfLegacyImportResult> ImportAsync(
            UniversalVtfLegacyImportRequest request,
            string cacheRoot,
            IProgress<UniversalVtfLegacyImportProgress>? progress,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            Requests.Add(request);
            CacheRoots.Add(cacheRoot);
            return Task.FromResult(new UniversalVtfLegacyImportResult(1, 1, 0, 1, 0.1));
        }
    }

    private sealed class RecordingMonotonicComposer : IUniversalVtfMonotonicComposeService
    {
        internal List<UniversalVtfMonotonicComposeRequest> Requests { get; } = new();

        internal Exception? Error { get; init; }

        public Task<UniversalVtfMonotonicComposeResult> ComposeAsync(
            UniversalVtfMonotonicComposeRequest request,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            Requests.Add(request);
            if (Error is not null)
            {
                return Task.FromException<UniversalVtfMonotonicComposeResult>(Error);
            }

            return Task.FromResult(new UniversalVtfMonotonicComposeResult(
                request.OutputRoot,
                request.IncumbentAnchor.TotalBytes - 1,
                Hash('3'),
                1,
                1,
                request.ManifestPath,
                Hash('4')));
        }
    }
}
