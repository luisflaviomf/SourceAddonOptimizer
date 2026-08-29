using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversal.Tests.Fixtures;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class VtfContainerLayoutReaderTests
{
    [Fact]
    public void SplitsSevenFaceRanges()
    {
        using VtfFixtureFile fixture = VtfFixtureBuilder.WriteCubemap74(8, 8, 4);

        VtfContainerLayout layout = VtfContainerLayoutReader.Read(fixture.Path);

        Assert.Equal(28, layout.Subresources.Count);
        Assert.Equal(7, layout.Document.Faces);
        Assert.Equal(4, layout.Document.MipCount);
    }

    [Fact]
    public void InlineCrcIsNotAnOffset()
    {
        using VtfFixtureFile fixture =
            VtfFixtureBuilder.WriteResource74WithInlineCrc(0x3C66AA52);

        VtfResourceChunk crc = VtfContainerLayoutReader.Read(fixture.Path)
            .Resources.Single(x => x.Tag == 0x435243);

        Assert.Equal(0x3C66AA52u, crc.InlineValue);
        Assert.Null(crc.PayloadRange);
    }

    [Fact]
    public void RejectsOverlappingRanges()
    {
        using VtfFixtureFile fixture = VtfFixtureBuilder.WriteOverlappingResources();

        Assert.Throws<InvalidDataException>(() =>
            VtfContainerLayoutReader.Read(fixture.Path));
    }

    [Fact]
    public void PartitionsEveryFrameFaceAndDepthSlice()
    {
        using VtfFixtureFile fixture = VtfFixtureBuilder.WriteVolumeWithFrames(
            width: 8,
            height: 8,
            depth: 4,
            frames: 2,
            mips: 4);

        VtfContainerLayout layout = VtfContainerLayoutReader.Read(fixture.Path);

        Assert.Equal(2 * 4 + 2 * 2 + 2 * 1 + 2 * 1, layout.Subresources.Count);
        Assert.All(layout.Subresources, x => Assert.True(x.Range.Length > 0));
        Assert.Equal(16, layout.Subresources.Select(x => x.Range.Offset).Distinct().Count());
    }
}
