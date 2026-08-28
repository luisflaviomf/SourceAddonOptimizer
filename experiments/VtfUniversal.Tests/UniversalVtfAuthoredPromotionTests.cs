using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversal.Tests.Fixtures;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfAuthoredPromotionTests
{
    [Fact]
    public async Task PromotesMipTwoAndCopiesRetainedPayloadsAndOffsetResources()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteResource75WithMips(64, 32, 7);
        using var outputRoot = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(outputRoot.Path, "candidate.vtf");
        VtfContainerLayout before = VtfContainerLayoutReader.Read(source.Path);

        await UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
            source.Path,
            level: 2,
            output,
            CancellationToken.None);

        VtfContainerLayout after = VtfContainerLayoutReader.Read(output);
        Assert.Equal((16, 8, 5),
            (after.Document.Width, after.Document.Height, after.Document.MipCount));
        Assert.Equal(1, after.Document.Depth);
        AssertRetainedSubresourcesEqual(before, sourceLevel: 2, after);
        Assert.Equal(before.LowResolutionImage!.Sha256, after.LowResolutionImage!.Sha256);
        Assert.Equal(
            before.Resources.Select(resource => (resource.Tag, resource.Flags)),
            after.Resources.Select(resource => (resource.Tag, resource.Flags)));

        VtfResourceChunk oldUnknown = Assert.Single(before.Resources,
            resource => resource.Tag == VtfFixtureBuilder.UnknownOffsetResourceTag);
        VtfResourceChunk newUnknown = Assert.Single(after.Resources,
            resource => resource.Tag == VtfFixtureBuilder.UnknownOffsetResourceTag);
        Assert.Equal(oldUnknown.PayloadRange!.Sha256, newUnknown.PayloadRange!.Sha256);
        Assert.NotEqual(oldUnknown.PayloadRange.Offset, newUnknown.PayloadRange.Offset);
    }

    [Fact]
    public async Task PreservesSevenFacesPerRetainedMip()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteCubemap74(16, 16, 5);
        using var outputRoot = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(outputRoot.Path, "cubemap.vtf");

        await UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
            source.Path,
            level: 1,
            output,
            CancellationToken.None);

        VtfContainerLayout after = VtfContainerLayoutReader.Read(output);
        Assert.Equal(7, after.Document.Faces);
        Assert.All(
            after.Subresources.GroupBy(resource => resource.MipLevel),
            mip => Assert.Equal(7, mip.Count()));
        AssertRetainedSubresourcesEqual(
            VtfContainerLayoutReader.Read(source.Path),
            sourceLevel: 1,
            after);
    }

    [Fact]
    public async Task RetainsFramesAndDepthAtPromotedLevel()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteVolumeWithFrames(
            width: 16,
            height: 8,
            depth: 8,
            frames: 2,
            mips: 4);
        using var outputRoot = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(outputRoot.Path, "volume.vtf");

        await UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
            source.Path,
            level: 2,
            output,
            CancellationToken.None);

        VtfContainerLayout after = VtfContainerLayoutReader.Read(output);
        Assert.Equal((4, 2, 2, 2, 2),
            (after.Document.Width, after.Document.Height, after.Document.Depth,
                after.Document.Frames, after.Document.MipCount));
        Assert.Equal(4, after.Subresources.Count(range => range.MipLevel == 0));
        Assert.Equal(2, after.Subresources.Count(range => range.MipLevel == 1));
        AssertRetainedSubresourcesEqual(
            VtfContainerLayoutReader.Read(source.Path),
            sourceLevel: 2,
            after);
    }

    [Fact]
    public async Task PromotionIsDeterministicAndDoesNotMutateSource()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteResource75WithMips(32, 32, 6);
        using var outputRoot = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string first = Path.Combine(outputRoot.Path, "first.vtf");
        string second = Path.Combine(outputRoot.Path, "second.vtf");
        string sourceHash = VtfContainerLayoutReader.Read(source.Path).FileSha256;

        await UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
            source.Path, 2, first, CancellationToken.None);
        await UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
            source.Path, 2, second, CancellationToken.None);

        Assert.Equal(
            VtfContainerLayoutReader.Read(first).FileSha256,
            VtfContainerLayoutReader.Read(second).FileSha256);
        Assert.Equal(sourceHash, VtfContainerLayoutReader.Read(source.Path).FileSha256);
    }

    [Fact]
    public async Task CopiesUnmappedTrailingPayloadByteForByte()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteVolumeWithFrames(
            width: 32, height: 16, depth: 1, frames: 1, mips: 5);
        byte[] tail = Enumerable.Range(0, 19)
            .Select(index => checked((byte)(0xC0 + index)))
            .ToArray();
        using (var append = new FileStream(source.Path, FileMode.Append, FileAccess.Write, FileShare.None))
        {
            append.Write(tail, 0, tail.Length);
        }

        VtfContainerLayout before = VtfContainerLayoutReader.Read(source.Path);
        VtfByteRange oldTail = Assert.Single(before.UnmappedRanges);
        using var outputRoot = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        string output = Path.Combine(outputRoot.Path, "with-tail.vtf");

        await UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
            source.Path, 2, output, CancellationToken.None);

        VtfByteRange newTail = Assert.Single(
            VtfContainerLayoutReader.Read(output).UnmappedRanges);
        Assert.Equal(oldTail.Length, newTail.Length);
        Assert.Equal(oldTail.Sha256, newTail.Sha256);
    }

    private static void AssertRetainedSubresourcesEqual(
        VtfContainerLayout before,
        int sourceLevel,
        VtfContainerLayout after)
    {
        Assert.Equal(before.Document.MipCount - sourceLevel, after.Document.MipCount);
        foreach (VtfSubresourceRange candidate in after.Subresources)
        {
            VtfSubresourceRange original = Assert.Single(before.Subresources, range =>
                range.MipLevel == candidate.MipLevel + sourceLevel &&
                range.Frame == candidate.Frame &&
                range.Face == candidate.Face &&
                range.DepthSlice == candidate.DepthSlice);
            Assert.Equal((original.Width, original.Height), (candidate.Width, candidate.Height));
            Assert.Equal(original.Range.Length, candidate.Range.Length);
            Assert.Equal(original.Range.Sha256, candidate.Range.Sha256);
        }
    }
}
