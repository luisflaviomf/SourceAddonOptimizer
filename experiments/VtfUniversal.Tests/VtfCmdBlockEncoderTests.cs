using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversal.Tests.Fixtures;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class VtfCmdBlockEncoderTests
{
    [Fact]
    public async Task ArgumentsAreCanonicalAndVersionIsClamped()
    {
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string executable = Path.Combine(root.Path, "VTFEdit", "VTFCmd.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(executable)!);
        File.WriteAllBytes(executable, new byte[] { 1 });
        var runner = new RecordingVtfRunner();
        var fingerprint = new UniversalVtfToolchainFingerprint(
            root.Path,
            Array.Empty<UniversalVtfToolBinary>(),
            "canonical",
            new string('a', 64));
        var encoder = new VtfCmdBlockEncoder(fingerprint, runner);

        VtfEncodedBlockSet result = await encoder.EncodeAsync(
            Solid8(),
            format: 13,
            sourceVersion: new Version(7, 5),
            workDirectory: root.Path,
            CancellationToken.None);

        Assert.NotNull(runner.Invocation);
        Assert.Equal(Path.GetFullPath(executable), runner.Invocation!.FileName);
        Assert.Equal(
            new[]
            {
                "-file", runner.InputPng!,
                "-output", runner.OutputDirectory!,
                "-format", "DXT1",
                "-alphaformat", "DXT1",
                "-version", "7.4"
            },
            runner.Invocation.Arguments);
        Assert.Equal(13, result.Format);
        Assert.Equal(8, result.Width);
        Assert.Equal(8, result.Height);
        Assert.Equal(32, Assert.Single(result.SubresourcePayloads).Length);
    }

    [Fact]
    public async Task NonzeroExitAndAmbiguousOutputsFailClosed()
    {
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string executable = Path.Combine(root.Path, "VTFEdit", "VTFCmd.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(executable)!);
        File.WriteAllBytes(executable, new byte[] { 1 });
        var fingerprint = new UniversalVtfToolchainFingerprint(
            root.Path,
            Array.Empty<UniversalVtfToolBinary>(),
            "canonical",
            new string('a', 64));

        await Assert.ThrowsAsync<InvalidDataException>(() =>
            new VtfCmdBlockEncoder(fingerprint, new RecordingVtfRunner(exitCode: 7))
                .EncodeAsync(Solid8(), 13, new Version(7, 2), root.Path, CancellationToken.None));
        await Assert.ThrowsAsync<InvalidDataException>(() =>
            new VtfCmdBlockEncoder(fingerprint, new RecordingVtfRunner(extraOutput: true))
                .EncodeAsync(Solid8(), 13, new Version(7, 2), root.Path, CancellationToken.None));
    }

    [Fact]
    public async Task IsolatedNonzeroFrameFaceAndSliceCanBeEncoded()
    {
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string executable = Path.Combine(root.Path, "VTFEdit", "VTFCmd.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(executable)!);
        File.WriteAllBytes(executable, new byte[] { 1 });
        var fingerprint = new UniversalVtfToolchainFingerprint(
            root.Path,
            Array.Empty<UniversalVtfToolBinary>(),
            "canonical",
            new string('a', 64));

        VtfEncodedBlockSet result = await new VtfCmdBlockEncoder(
                fingerprint,
                new RecordingVtfRunner())
            .EncodeAsync(
                Solid8() with { Frame = 3, Face = 5, DepthSlice = 2 },
                13,
                new Version(7, 4),
                root.Path,
                CancellationToken.None);

        Assert.Equal(32, Assert.Single(result.SubresourcePayloads).Length);
    }

    [Fact]
    [Trait("Category", "RealToolchain")]
    public async Task RealVtfCmdEncodesDeterministicDxt1AndDxt5Blocks()
    {
        if (Environment.GetEnvironmentVariable("VTF_UNIVERSAL_REAL_TOOLCHAIN") != "1")
        {
            return;
        }

        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        UniversalVtfToolchain.ExtractEmbeddedTo(root.Path);
        UniversalVtfToolchainFingerprint fingerprint = UniversalVtfToolchain.Verify(root.Path);
        var encoder = new VtfCmdBlockEncoder(fingerprint);

        VtfEncodedBlockSet dxt1First = await encoder.EncodeAsync(
            Solid8(), 13, new Version(7, 5), root.Path, CancellationToken.None);
        VtfEncodedBlockSet dxt1Second = await encoder.EncodeAsync(
            Solid8(), 13, new Version(7, 5), root.Path, CancellationToken.None);
        VtfEncodedBlockSet dxt5 = await encoder.EncodeAsync(
            Alpha8(), 15, new Version(7, 2), root.Path, CancellationToken.None);

        Assert.Equal(32, Assert.Single(dxt1First.SubresourcePayloads).Length);
        Assert.Equal(dxt1First.SubresourcePayloads[0], dxt1Second.SubresourcePayloads[0]);
        Assert.Equal(64, Assert.Single(dxt5.SubresourcePayloads).Length);
    }

    private static UniversalVtfImagePlane Solid8() =>
        new(
            8,
            8,
            0,
            0,
            0,
            Enumerable.Range(0, 64)
                .SelectMany(index => new byte[]
                {
                    (byte)(index * 3),
                    (byte)(255 - index * 2),
                    (byte)(index * 4),
                    255
                })
                .ToArray());

    private static UniversalVtfImagePlane Alpha8() =>
        Solid8() with
        {
            Rgba = Enumerable.Range(0, 64)
                .SelectMany(index => new byte[]
                {
                    (byte)(index * 3),
                    (byte)(255 - index * 2),
                    (byte)(index * 4),
                    (byte)(index * 4)
                })
                .ToArray()
        };

    private sealed class RecordingVtfRunner : IUniversalVtfProcessRunner
    {
        private readonly int _exitCode;
        private readonly bool _extraOutput;

        internal RecordingVtfRunner(int exitCode = 0, bool extraOutput = false)
        {
            _exitCode = exitCode;
            _extraOutput = extraOutput;
        }

        internal UniversalVtfProcessInvocation? Invocation { get; private set; }
        internal string? InputPng { get; private set; }
        internal string? OutputDirectory { get; private set; }

        public Task<UniversalVtfProcessResult> RunAsync(
            UniversalVtfProcessInvocation invocation,
            CancellationToken cancellationToken)
        {
            Invocation = invocation;
            int fileIndex = invocation.Arguments.IndexOf("-file");
            int outputIndex = invocation.Arguments.IndexOf("-output");
            InputPng = invocation.Arguments[fileIndex + 1];
            OutputDirectory = invocation.Arguments[outputIndex + 1];
            if (_exitCode == 0)
            {
                Directory.CreateDirectory(OutputDirectory);
                using VtfFixtureFile fixture = VtfFixtureBuilder.WriteVolumeWithFrames(
                    width: 8,
                    height: 8,
                    depth: 1,
                    frames: 1,
                    mips: 1);
                File.Copy(fixture.Path, Path.Combine(OutputDirectory, "input.vtf"));
                if (_extraOutput)
                {
                    File.Copy(fixture.Path, Path.Combine(OutputDirectory, "ambiguous.vtf"));
                }
            }

            return Task.FromResult(new UniversalVtfProcessResult(
                _exitCode,
                StandardOutput: "recorded stdout",
                StandardError: _exitCode == 0 ? string.Empty : "recorded stderr",
                TimedOut: false));
        }
    }
}

internal static class ReadOnlyListTestExtensions
{
    internal static int IndexOf(this IReadOnlyList<string> values, string expected)
    {
        for (int index = 0; index < values.Count; index++)
        {
            if (values[index] == expected)
            {
                return index;
            }
        }

        return -1;
    }
}
