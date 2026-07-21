using VtfMaximumLab.Metrics;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class VtfMetricCalculatorTests
{
    [Fact]
    public void IdentityHasPerfectRgbAlphaAndNormalMetrics()
    {
        byte[] rgba = Enumerable.Repeat(new byte[] { 128, 128, 255, 200 }, 64).SelectMany(value => value).ToArray();

        VtfQualityMetrics metrics = VtfQualityMetricCalculator.Compare(
            rgba, 8, 8, rgba, 8, 8, requiresAlpha: true, isCutout: false, isNormalMap: true, 0.5);

        Assert.Equal(1, metrics.RgbSsim, 10);
        Assert.True(metrics.RgbPsnr >= 99);
        Assert.Equal(1, metrics.AlphaSsim, 10);
        Assert.Equal(0, metrics.NormalMaxDegrees, 6);
    }

    [Fact]
    public void DetectsAlphaAndCutoutCoverageLossSeparately()
    {
        byte[] reference = Enumerable.Repeat(new byte[] { 255, 255, 255, 255 }, 64).SelectMany(value => value).ToArray();
        byte[] candidate = (byte[])reference.Clone();
        for (int pixel = 0; pixel < 16; pixel++)
            candidate[pixel * 4 + 3] = 0;

        VtfQualityMetrics metrics = VtfQualityMetricCalculator.Compare(
            reference, 8, 8, candidate, 8, 8, requiresAlpha: true, isCutout: true, isNormalMap: false, 0.5);

        Assert.True(metrics.AlphaMae > 0.2);
        Assert.True(metrics.CutoutCoverageErrorTop > 0.2);
        Assert.True(metrics.CutoutIou < 0.8);
    }

    [Fact]
    public void InvisibleRgbDifferencesDoNotCountAsVisualErrorWhenAlphaIsRequired()
    {
        byte[] reference = Enumerable.Repeat(new byte[] { 255, 0, 0, 0 }, 64).SelectMany(value => value).ToArray();
        byte[] candidate = Enumerable.Repeat(new byte[] { 0, 255, 255, 0 }, 64).SelectMany(value => value).ToArray();

        VtfQualityMetrics metrics = VtfQualityMetricCalculator.Compare(
            reference, 8, 8, candidate, 8, 8, requiresAlpha: true, isCutout: false, isNormalMap: false, 0.5);

        Assert.Equal(1, metrics.RgbSsim, 10);
        Assert.True(metrics.RgbPsnr >= 99);
    }

    [Fact]
    public void Dxt5NmAngularMetricUsesAlphaForXAndGreenForY()
    {
        byte[] reference = Enumerable.Repeat(new byte[] { 128, 128, 255, 255 }, 64).SelectMany(value => value).ToArray();
        byte[] candidate = Enumerable.Repeat(new byte[] { 128, 128, 255, 128 }, 64).SelectMany(value => value).ToArray();

        VtfQualityMetrics metrics = VtfQualityMetricCalculator.Compare(
            reference, 8, 8, candidate, 8, 8,
            requiresAlpha: false, isCutout: false, isNormalMap: true, 0.5, isDxt5NormalMap: true);

        Assert.InRange(metrics.NormalMeanDegrees, 89, 91);
    }

    [Fact]
    public void ReducedNormalIsMeasuredAgainstIdealNativeResolutionReference()
    {
        byte[] reference = new byte[8 * 8 * 4];
        for (int y = 0; y < 8; y++)
        for (int x = 0; x < 8; x++)
        {
            int offset = (y * 8 + x) * 4;
            reference[offset] = (byte)(96 + x * 8);
            reference[offset + 1] = (byte)(96 + y * 8);
            reference[offset + 2] = 240;
            reference[offset + 3] = 255;
        }
        byte[] candidate = Images.MaximumImagePreprocessor.Resize(
            reference, 8, 8, 4, 4, isNormalMap: true, isCutout: false, alphaThreshold: 0.5).Rgba;

        VtfQualityMetrics metrics = VtfQualityMetricCalculator.Compare(
            reference, 8, 8, candidate, 4, 4,
            requiresAlpha: false, isCutout: false, isNormalMap: true, 0.5);

        Assert.InRange(metrics.NormalMeanDegrees, 0, 0.00001);
        Assert.InRange(metrics.NormalMaxDegrees, 0, 0.00001);
    }

    [Fact]
    public void ReducedAlphaIsMeasuredAgainstIdealNativeResolutionReference()
    {
        byte[] reference = new byte[8 * 8 * 4];
        for (int pixel = 0; pixel < 64; pixel++)
        {
            reference[pixel * 4] = 220;
            reference[pixel * 4 + 1] = 180;
            reference[pixel * 4 + 2] = 140;
            reference[pixel * 4 + 3] = (byte)(pixel * 4);
        }
        byte[] candidate = Images.MaximumImagePreprocessor.Resize(
            reference, 8, 8, 4, 4, isNormalMap: false, isCutout: false, alphaThreshold: 0.5).Rgba;

        VtfQualityMetrics metrics = VtfQualityMetricCalculator.Compare(
            reference, 8, 8, candidate, 4, 4,
            requiresAlpha: true, isCutout: false, isNormalMap: false, 0.5);

        Assert.Equal(1, metrics.AlphaSsim, 10);
        Assert.Equal(0, metrics.AlphaMae, 10);
        Assert.Equal(0, metrics.AlphaP99, 10);
    }
}
