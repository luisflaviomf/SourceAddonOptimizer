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
                Profile(UniversalVtfProfileKind.Aggressive)));
        }
    }
}
