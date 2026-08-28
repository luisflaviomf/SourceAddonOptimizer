using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfLevelsTests
{
    [Theory]
    [InlineData(1024, 512, 1, 512, 256)]
    [InlineData(3, 1, 1, 1, 1)]
    [InlineData(1, 9, 3, 1, 1)]
    public void ComputesPerAxisClampedDimensions(
        int width,
        int height,
        int level,
        int expectedWidth,
        int expectedHeight)
    {
        Assert.Equal(
            new UniversalVtfDimensions(expectedWidth, expectedHeight),
            UniversalVtfLevels.At(width, height, level));
    }

    [Fact]
    public void OnlyOneByOneIsIrreducible()
    {
        Assert.False(UniversalVtfLevels.IsReducible(1, 1));
        Assert.True(UniversalVtfLevels.IsReducible(1, 2));
        Assert.True(UniversalVtfLevels.IsReducible(2, 1));
    }

    [Fact]
    public void EnumeratesEveryPhysicalLevelOnce()
    {
        Assert.Equal(new[] { 1, 2, 3 }, UniversalVtfLevels.Enumerate(8, 4));
    }
}
