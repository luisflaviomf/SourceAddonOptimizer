using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversal.Tests.Fixtures;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfGeneratedCandidateTests
{
    [Fact]
    public async Task GeneratedNomipRemainsSingleMipAndSourceIsUnchanged()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteNomip72(
            width: 128,
            height: 64,
            highResolutionFormat: 15);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(root.Path, "nomip.vtf");
        string sourceHash = VtfContainerLayoutReader.Read(source.Path).FileSha256;

        await UniversalVtfCandidateMaterializer.MaterializeGeneratedAsync(
            source.Path,
            level: 3,
            UniversalVtfFilterRoute.NumericLinear,
            output,
            CancellationToken.None);

        VtfContainerLayout candidate = VtfContainerLayoutReader.Read(output);
        Assert.Equal((16, 8, 1, 15),
            (candidate.Document.Width, candidate.Document.Height,
                candidate.Document.MipCount, candidate.Document.HighResFormat));
        Assert.True((candidate.Document.Flags & VtfFlags.NoMip) != 0);
        Assert.Single(candidate.Subresources);
        Assert.Equal(sourceHash, VtfContainerLayoutReader.Read(source.Path).FileSha256);
    }

    [Theory]
    [InlineData(16, 16, 4, 4, 1, 8, 8, 4, 4, true)]
    [InlineData(16, 8, 16, 16, 1, 8, 4, 8, 4, false)]
    public async Task AppliesThumbnailCopyOrRegenerationRule(
        int width,
        int height,
        int lowWidth,
        int lowHeight,
        int level,
        int finalWidth,
        int finalHeight,
        int expectedLowWidth,
        int expectedLowHeight,
        bool expectedCopied)
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteNomip74(
            width,
            height,
            highResolutionFormat: 13,
            lowWidth: lowWidth,
            lowHeight: lowHeight);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(root.Path, "thumbnail.vtf");
        VtfContainerLayout before = VtfContainerLayoutReader.Read(source.Path);

        await UniversalVtfCandidateMaterializer.MaterializeGeneratedAsync(
            source.Path,
            level,
            UniversalVtfFilterRoute.SrgbColor,
            output,
            CancellationToken.None);

        VtfContainerLayout after = VtfContainerLayoutReader.Read(output);
        Assert.Equal((finalWidth, finalHeight),
            (after.Document.Width, after.Document.Height));
        Assert.Equal((expectedLowWidth, expectedLowHeight),
            (after.Document.LowResWidth, after.Document.LowResHeight));
        Assert.NotNull(after.LowResolutionImage);
        Assert.Equal(
            expectedCopied,
            before.LowResolutionImage!.Sha256 == after.LowResolutionImage!.Sha256);
        Assert.Contains(after.Resources,
            resource => resource.Tag == VtfContainerLayoutReader.LowResolutionImageResourceTag);
    }

    [Fact]
    public async Task AbsentThumbnailRemainsAbsentAndNoResourceIsInvented()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteNomip74(
            width: 64,
            height: 64,
            highResolutionFormat: 13);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(root.Path, "no-thumbnail.vtf");

        await UniversalVtfCandidateMaterializer.MaterializeGeneratedAsync(
            source.Path,
            level: 2,
            UniversalVtfFilterRoute.SrgbColor,
            output,
            CancellationToken.None);

        VtfContainerLayout after = VtfContainerLayoutReader.Read(output);
        Assert.Equal((0, 0),
            (after.Document.LowResWidth, after.Document.LowResHeight));
        Assert.Null(after.LowResolutionImage);
        Assert.DoesNotContain(after.Resources,
            resource => resource.Tag == VtfContainerLayoutReader.LowResolutionImageResourceTag);
    }

    [Fact]
    public async Task GeneratesEveryFrameAndAllSevenFaces()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteNomip74(
            width: 16,
            height: 8,
            highResolutionFormat: 13,
            frames: 2,
            envMap: true);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(root.Path, "animated-cubemap.vtf");

        await UniversalVtfCandidateMaterializer.MaterializeGeneratedAsync(
            source.Path,
            level: 1,
            UniversalVtfFilterRoute.NumericLinear,
            output,
            CancellationToken.None);

        VtfContainerLayout after = VtfContainerLayoutReader.Read(output);
        Assert.Equal((2, 7, 14),
            (after.Document.Frames, after.Document.Faces, after.Subresources.Count));
        Assert.Equal(
            Enumerable.Range(0, 2)
                .SelectMany(frame => Enumerable.Range(0, 7)
                    .Select(face => (Frame: frame, Face: face, DepthSlice: 0))),
            after.Subresources
                .OrderBy(range => range.Frame)
                .ThenBy(range => range.Face)
                .Select(range => (range.Frame, range.Face, range.DepthSlice)));
    }

    [Fact]
    public async Task GeneratesEveryDepthSliceWithoutFlatteningVolume()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteNomip72(
            width: 16,
            height: 8,
            highResolutionFormat: 13,
            frames: 2,
            depth: 3);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(root.Path, "volume.vtf");

        await UniversalVtfCandidateMaterializer.MaterializeGeneratedAsync(
            source.Path,
            level: 2,
            UniversalVtfFilterRoute.NumericLinear,
            output,
            CancellationToken.None);

        VtfContainerLayout after = VtfContainerLayoutReader.Read(output);
        Assert.Equal((4, 2, 3, 2, 6),
            (after.Document.Width, after.Document.Height, after.Document.Depth,
                after.Document.Frames, after.Subresources.Count));
    }

    [Fact]
    public async Task UnsupportedGeneratedFormatFailsWithoutPublishing()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteNomip72(
            width: 16,
            height: 16,
            highResolutionFormat: 20);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(root.Path, "one-bit-alpha.vtf");

        await Assert.ThrowsAsync<NotSupportedException>(() =>
            UniversalVtfCandidateMaterializer.MaterializeGeneratedAsync(
                source.Path,
                level: 1,
                UniversalVtfFilterRoute.SrgbColor,
                output,
                CancellationToken.None));

        Assert.False(File.Exists(output));
    }

    [Fact]
    public async Task AuthoredPromotionRegeneratesOversizedThumbnailWithoutReencodingMips()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteResource75WithMips(
            width: 64,
            height: 32,
            mips: 7);
        using var root = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        using UniversalVtfEncodingSession session =
            UniversalVtfCandidateMaterializer.CreateEncodingSession();
        string output = Path.Combine(root.Path, "authored-thumbnail.vtf");
        VtfContainerLayout before = VtfContainerLayoutReader.Read(source.Path);

        await session.MaterializeAuthoredAsync(
            source.Path,
            level: 4,
            UniversalVtfFilterRoute.SrgbColor,
            output,
            CancellationToken.None);

        VtfContainerLayout after = VtfContainerLayoutReader.Read(output);
        Assert.Equal((4, 2, 3, 4, 2),
            (after.Document.Width, after.Document.Height, after.Document.MipCount,
                after.Document.LowResWidth, after.Document.LowResHeight));
        Assert.NotEqual(
            before.LowResolutionImage!.Sha256,
            after.LowResolutionImage!.Sha256);
        foreach (VtfSubresourceRange candidate in after.Subresources)
        {
            VtfSubresourceRange original = Assert.Single(before.Subresources, range =>
                range.MipLevel == candidate.MipLevel + 4 &&
                range.Frame == candidate.Frame &&
                range.Face == candidate.Face &&
                range.DepthSlice == candidate.DepthSlice);
            Assert.Equal(original.Range.Sha256, candidate.Range.Sha256);
        }
    }
}
