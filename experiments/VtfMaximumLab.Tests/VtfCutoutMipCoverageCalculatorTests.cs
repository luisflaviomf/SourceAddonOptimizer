using VtfMaximumLab.Metrics;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class VtfCutoutMipCoverageCalculatorTests
{
    [Fact]
    public void CoverageErrorUsesAlphaThresholdIndependentlyOfRgb()
    {
        byte[] reference =
        {
            255, 0, 0, 255, 0, 255, 0, 255,
            0, 0, 255, 0, 255, 255, 255, 0
        };
        byte[] candidate =
        {
            0, 0, 0, 255, 0, 0, 0, 0,
            0, 0, 0, 0, 0, 0, 0, 0
        };

        double error = VtfCutoutMipCoverageCalculator.ComputeCoverageError(reference, candidate, 0.5);

        Assert.Equal(0.25, error, 10);
    }
}
