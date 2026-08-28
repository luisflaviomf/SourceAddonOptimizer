using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfDownsamplerTests
{
    [Fact]
    public void GeneratedLevelUsesSourceZeroInsteadOfRecursiveOutput()
    {
        UniversalVtfImagePlane source = HighFrequencyColor(32, 32);
        UniversalVtfSemanticDecision semantics = Decision(UniversalVtfSemanticClass.Diffuse);

        UniversalVtfImagePlane direct = UniversalVtfDownsampler.Downsample(
            source,
            new UniversalVtfDimensions(8, 8),
            UniversalVtfFilterRoute.SrgbColor,
            semantics);
        UniversalVtfImagePlane recursive = UniversalVtfDownsampler.Downsample(
            UniversalVtfDownsampler.Downsample(
                source,
                new UniversalVtfDimensions(16, 16),
                UniversalVtfFilterRoute.SrgbColor,
                semantics),
            new UniversalVtfDimensions(8, 8),
            UniversalVtfFilterRoute.SrgbColor,
            semantics);

        Assert.NotEqual(direct.Rgba, recursive.Rgba);
    }

    [Fact]
    public void SrgbColorFiltersInLinearLightAndPremultipliesAlpha()
    {
        UniversalVtfSemanticDecision semantics = Decision(UniversalVtfSemanticClass.Diffuse);
        var blackWhite = new UniversalVtfImagePlane(
            2, 1, 0, 0, 0,
            new byte[] { 0, 0, 0, 255, 255, 255, 255, 255 });
        var transparentBlue = new UniversalVtfImagePlane(
            2, 1, 0, 0, 0,
            new byte[] { 255, 0, 0, 255, 0, 0, 255, 0 });

        UniversalVtfImagePlane linearLight = UniversalVtfDownsampler.Downsample(
            blackWhite,
            new UniversalVtfDimensions(1, 1),
            UniversalVtfFilterRoute.SrgbColor,
            semantics);
        UniversalVtfImagePlane premultiplied = UniversalVtfDownsampler.Downsample(
            transparentBlue,
            new UniversalVtfDimensions(1, 1),
            UniversalVtfFilterRoute.SrgbColor,
            semantics);

        Assert.InRange(linearLight.Rgba[0], 187, 189);
        Assert.Equal(linearLight.Rgba[0], linearLight.Rgba[1]);
        Assert.Equal(linearLight.Rgba[1], linearLight.Rgba[2]);
        Assert.Equal(255, linearLight.Rgba[3]);
        Assert.InRange(premultiplied.Rgba[0], 254, 255);
        Assert.InRange(premultiplied.Rgba[1], 0, 1);
        Assert.InRange(premultiplied.Rgba[2], 0, 1);
        Assert.InRange(premultiplied.Rgba[3], 127, 128);
    }

    [Fact]
    public void NumericLinearFiltersChannelsWithoutSrgbTransfer()
    {
        var source = new UniversalVtfImagePlane(
            2, 1, 0, 0, 0,
            new byte[] { 0, 32, 64, 96, 255, 224, 192, 160 });

        UniversalVtfImagePlane actual = UniversalVtfDownsampler.Downsample(
            source,
            new UniversalVtfDimensions(1, 1),
            UniversalVtfFilterRoute.NumericLinear,
            Decision(UniversalVtfSemanticClass.ScalarMask));

        Assert.Equal(new byte[] { 128, 128, 128, 128 }, actual.Rgba);
    }

    [Fact]
    public void VectorNormalUsesExactAreaFootprintInsteadOfAColorFilter()
    {
        var source = new UniversalVtfImagePlane(
            3, 1, 0, 0, 0,
            new byte[]
            {
                255, 128, 128, 255,
                128, 255, 128, 255,
                128, 128, 255, 255
            });

        UniversalVtfImagePlane actual = UniversalVtfDownsampler.Downsample(
            source,
            new UniversalVtfDimensions(2, 1),
            UniversalVtfFilterRoute.RgbNormal,
            Decision(UniversalVtfSemanticClass.Normal));

        Assert.Equal(
            new byte[] { 241, 185, 128, 255, 128, 185, 241, 255 },
            actual.Rgba);
    }

    [Fact]
    public void ZeroLengthNormalAverageRejectsTheCandidate()
    {
        var source = new UniversalVtfImagePlane(
            2, 1, 0, 0, 0,
            new byte[] { 255, 0, 0, 255, 0, 255, 255, 255 });

        Assert.Throws<InvalidDataException>(() =>
            UniversalVtfDownsampler.Downsample(
                source,
                new UniversalVtfDimensions(1, 1),
                UniversalVtfFilterRoute.RgbNormal,
                Decision(UniversalVtfSemanticClass.Normal)));
    }

    [Fact]
    public void Dxt5NmWritesAgVectorAndFiltersAuxiliaryChannels()
    {
        var source = new UniversalVtfImagePlane(
            2, 1, 0, 0, 0,
            new byte[]
            {
                0, 128, 64, 255,
                255, 128, 192, 128
            });

        UniversalVtfImagePlane actual = UniversalVtfDownsampler.Downsample(
            source,
            new UniversalVtfDimensions(1, 1),
            UniversalVtfFilterRoute.Dxt5NmAg,
            Decision(UniversalVtfSemanticClass.Normal));

        Assert.Equal(128, actual.Rgba[0]);
        Assert.InRange(actual.Rgba[1], 127, 129);
        Assert.Equal(128, actual.Rgba[2]);
        Assert.Equal(218, actual.Rgba[3]);
    }

    [Fact]
    public void CoverageScaleUsesFrozenSeedsAndThirtyTwoBisections()
    {
        byte[] filteredAlpha = { 0, 40, 80, 100, 110, 120, 127, 255 };

        UniversalVtfCoverageScale actual = UniversalVtfCoverageScaler.FindScale(
            filteredAlpha,
            threshold: 0.5,
            targetCoverage: 0.5);

        Assert.Equal(35, actual.Candidates.Count);
        Assert.Equal(new[] { 0.0, 1.0, 8.0 }, actual.Candidates.Take(3));
        Assert.Equal(1.1590909101068974, actual.Scale, precision: 15);
        Assert.Equal(0.5, actual.AchievedCoverage, precision: 15);
    }

    [Fact]
    public void CutoutDownsampleAppliesCoverageScaleToOutputAlpha()
    {
        byte[] rgba = new byte[8 * 4];
        byte[] alpha = { 0, 0, 0, 0, 255, 255, 255, 255 };
        for (int index = 0; index < alpha.Length; index++)
        {
            rgba[index * 4] = 255;
            rgba[index * 4 + 3] = alpha[index];
        }

        var source = new UniversalVtfImagePlane(8, 1, 0, 0, 0, rgba);
        UniversalVtfImagePlane actual = UniversalVtfDownsampler.Downsample(
            source,
            new UniversalVtfDimensions(4, 1),
            UniversalVtfFilterRoute.SrgbColor,
            Decision(UniversalVtfSemanticClass.Diffuse, UniversalVtfSemanticClass.Cutout));

        double coverage = actual.Rgba
            .Where((_, index) => index % 4 == 3)
            .Count(value => value >= 128) / 4d;
        Assert.Equal(0.5, coverage);
    }

    [Fact]
    public void CompositeRouteCannotSilentlyGuessAFilter()
    {
        var source = new UniversalVtfImagePlane(
            2, 2, 0, 0, 0,
            Enumerable.Repeat((byte)128, 16).ToArray());

        Assert.Throws<InvalidOperationException>(() =>
            UniversalVtfDownsampler.Downsample(
                source,
                new UniversalVtfDimensions(1, 1),
                UniversalVtfFilterRoute.Composite,
                Decision(UniversalVtfSemanticClass.Unknown)));
    }

    private static UniversalVtfSemanticDecision Decision(
        params UniversalVtfSemanticClass[] classes) =>
        new(
            new HashSet<UniversalVtfSemanticClass>(classes),
            UniversalVtfFilterRoute.Composite,
            HasUnknownConsumer: false,
            AlphaTestReference: 0.5,
            Consumers: Array.Empty<VtfConsumerEvidence>(),
            Uncertainties: Array.Empty<string>());

    private static UniversalVtfImagePlane HighFrequencyColor(int width, int height)
    {
        var rgba = new byte[checked(width * height * 4)];
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                int offset = (y * width + x) * 4;
                rgba[offset] = (byte)((x * 73 + y * 19 + x * y * 3) & 0xff);
                rgba[offset + 1] = (byte)((x * 11 + y * 101 + x * y * 5) & 0xff);
                rgba[offset + 2] = (byte)(((x ^ y) * 47 + x * 7) & 0xff);
                rgba[offset + 3] = 255;
            }
        }

        return new UniversalVtfImagePlane(width, height, 0, 0, 0, rgba);
    }
}
