using VtfMaximumLab.Images;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class MaximumImagePreprocessorTests
{
    [Fact]
    public void CutoutResizePreservesThresholdCoverage()
    {
        byte[] rgba = new byte[8 * 8 * 4];
        for (int pixel = 0; pixel < 64; pixel++)
        {
            rgba[pixel * 4] = 255;
            rgba[pixel * 4 + 3] = pixel < 32 ? (byte)255 : (byte)0;
        }

        PreparedImage resized = MaximumImagePreprocessor.Resize(
            rgba, 8, 8, 4, 4, isNormalMap: false, isCutout: true, alphaThreshold: 0.5);

        double error = Math.Abs(resized.OutputAlphaCoverage - resized.SourceAlphaCoverage);
        Assert.True(error <= 0.01,
            $"source={resized.SourceAlphaCoverage}, output={resized.OutputAlphaCoverage}, alpha={string.Join(',', resized.Rgba.Where((_, index) => index % 4 == 3).Distinct())}");
    }

    [Fact]
    public void NormalResizeRenormalizesVectors()
    {
        byte[] rgba = Enumerable.Repeat(new byte[] { 200, 128, 230, 255 }, 64).SelectMany(value => value).ToArray();

        PreparedImage resized = MaximumImagePreprocessor.Resize(
            rgba, 8, 8, 4, 4, isNormalMap: true, isCutout: false, alphaThreshold: 0.5);

        for (int offset = 0; offset < resized.Rgba.Length; offset += 4)
        {
            double x = resized.Rgba[offset] / 127.5 - 1.0;
            double y = resized.Rgba[offset + 1] / 127.5 - 1.0;
            double z = resized.Rgba[offset + 2] / 127.5 - 1.0;
            Assert.InRange(Math.Sqrt(x * x + y * y + z * z), 0.99, 1.01);
        }
    }
}
