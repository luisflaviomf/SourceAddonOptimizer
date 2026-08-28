using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversal.Tests.Fixtures;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class VtfContainerWriterTests
{
    private const uint ApprovedInlineCrc = 0x3C66AA52;

    [Fact]
    public async Task PreservesHeaderMetadataAndApprovedInlineCrcAtExactPath()
    {
        using VtfFixtureFile fixture = VtfFixtureBuilder.WriteResource74WithInlineCrc(
            ApprovedInlineCrc,
            mips: 4);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string source = Path.Combine(
            root.Path,
            "materials",
            "models",
            "diggercars",
            "mb_w123",
            "w123_int_envmap.vtf");
        Directory.CreateDirectory(Path.GetDirectoryName(source)!);
        File.Copy(fixture.Path, source);
        string output = Path.Combine(root.Path, "candidate.vtf");
        VtfContainerLayout before = VtfContainerLayoutReader.Read(source);

        await UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
            source, 1, output, CancellationToken.None);

        VtfContainerLayout after = VtfContainerLayoutReader.Read(output);
        Assert.Equal(before.Document.MajorVersion, after.Document.MajorVersion);
        Assert.Equal(before.Document.MinorVersion, after.Document.MinorVersion);
        Assert.Equal(before.Document.HeaderSize, after.Document.HeaderSize);
        Assert.Equal(before.Document.Flags, after.Document.Flags);
        Assert.Equal(before.Document.Frames, after.Document.Frames);
        Assert.Equal(before.Document.FirstFrame, after.Document.FirstFrame);
        Assert.Equal(before.Document.ReflectivityX, after.Document.ReflectivityX);
        Assert.Equal(before.Document.ReflectivityY, after.Document.ReflectivityY);
        Assert.Equal(before.Document.ReflectivityZ, after.Document.ReflectivityZ);
        Assert.Equal(before.Document.BumpScale, after.Document.BumpScale);
        VtfResourceChunk inline = Assert.Single(after.Resources,
            resource => resource.InlineValue.HasValue);
        Assert.Equal(0x435243, inline.Tag);
        Assert.Equal(ApprovedInlineCrc, inline.InlineValue);
    }

    [Fact]
    public async Task AnyOtherInlineTagValueOrPathFailsClosed()
    {
        using VtfFixtureFile wrongPath = VtfFixtureBuilder.WriteResource74WithInlineCrc(
            ApprovedInlineCrc,
            mips: 3);
        using VtfFixtureFile wrongValue = VtfFixtureBuilder.WriteResource74WithInlineCrc(
            0x12345678,
            mips: 3);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string wrongValuePath = Path.Combine(
            root.Path,
            "materials",
            "models",
            "diggercars",
            "mb_w123",
            "w123_int_envmap.vtf");
        Directory.CreateDirectory(Path.GetDirectoryName(wrongValuePath)!);
        File.Copy(wrongValue.Path, wrongValuePath);

        await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
                wrongPath.Path,
                1,
                Path.Combine(root.Path, "wrong-path.vtf"),
                CancellationToken.None));
        await Assert.ThrowsAsync<InvalidDataException>(() =>
            UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
                wrongValuePath,
                1,
                Path.Combine(root.Path, "wrong-value.vtf"),
                CancellationToken.None));
    }

    [Fact]
    public async Task ExistingOutputIsNeverOverwritten()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteVolumeWithFrames(
            width: 16, height: 16, depth: 1, frames: 1, mips: 4);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(root.Path, "existing.vtf");
        byte[] sentinel = { 7, 8, 9, 10 };
        File.WriteAllBytes(output, sentinel);

        await Assert.ThrowsAsync<IOException>(() =>
            UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
                source.Path, 1, output, CancellationToken.None));

        Assert.Equal(sentinel, File.ReadAllBytes(output));
        Assert.DoesNotContain(
            Directory.EnumerateFiles(root.Path),
            path => Path.GetFileName(path).Contains(".universal-vtf-", StringComparison.Ordinal));
    }

    [Theory]
    [InlineData(-1)]
    [InlineData(4)]
    public async Task MissingAuthoredLevelFailsWithoutPublishing(int level)
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteVolumeWithFrames(
            width: 16, height: 16, depth: 1, frames: 1, mips: 4);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(root.Path, "invalid.vtf");

        await Assert.ThrowsAnyAsync<ArgumentException>(() =>
            UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
                source.Path, level, output, CancellationToken.None));

        Assert.False(File.Exists(output));
    }
}
