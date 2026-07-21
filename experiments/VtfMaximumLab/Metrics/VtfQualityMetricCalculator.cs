using VtfMaximumLab.Images;

namespace VtfMaximumLab.Metrics;

internal static class VtfQualityMetricCalculator
{
    internal static VtfQualityMetrics Compare(
        byte[] referenceRgba,
        int referenceWidth,
        int referenceHeight,
        byte[] candidateRgba,
        int candidateWidth,
        int candidateHeight,
        bool requiresAlpha,
        bool isCutout,
        bool isNormalMap,
        double alphaThreshold,
        bool isDxt5NormalMap = false)
    {
        Validate(referenceRgba, referenceWidth, referenceHeight);
        Validate(candidateRgba, candidateWidth, candidateHeight);
        byte[] aligned = candidateWidth == referenceWidth && candidateHeight == referenceHeight
            ? candidateRgba
            : MaximumImagePreprocessor.Resize(
                candidateRgba,
                candidateWidth,
                candidateHeight,
                referenceWidth,
                referenceHeight,
                isNormalMap,
                isCutout: false,
                alphaThreshold,
                isDxt5NormalMap).Rgba;

        byte[] semanticReference = referenceRgba;
        int semanticWidth = referenceWidth;
        int semanticHeight = referenceHeight;
        if (candidateWidth != referenceWidth || candidateHeight != referenceHeight)
        {
            semanticReference = MaximumImagePreprocessor.Resize(
                referenceRgba,
                referenceWidth,
                referenceHeight,
                candidateWidth,
                candidateHeight,
                isNormalMap,
                isCutout,
                alphaThreshold,
                isDxt5NormalMap).Rgba;
            semanticWidth = candidateWidth;
            semanticHeight = candidateHeight;
        }

        int pixelCount = referenceWidth * referenceHeight;
        bool compareCompositedRgb = requiresAlpha && !isNormalMap;
        int comparedChannelCount = compareCompositedRgb ? 6 : 3;
        var referenceChannels = Enumerable.Range(0, comparedChannelCount).Select(_ => new double[pixelCount]).ToArray();
        var candidateChannels = Enumerable.Range(0, comparedChannelCount).Select(_ => new double[pixelCount]).ToArray();
        var rgbErrors = new double[pixelCount];
        double rgbSquaredError = 0;

        for (int pixel = 0; pixel < pixelCount; pixel++)
        {
            int offset = pixel * 4;
            double referencePixelAlpha = referenceRgba[offset + 3] / 255.0;
            double candidatePixelAlpha = aligned[offset + 3] / 255.0;
            double pixelSquaredError = 0;
            for (int channel = 0; channel < 3; channel++)
            {
                double reference = SrgbToLinear(referenceRgba[offset + channel] / 255.0);
                double candidate = SrgbToLinear(aligned[offset + channel] / 255.0);
                if (compareCompositedRgb)
                {
                    double referenceBlack = reference * referencePixelAlpha;
                    double candidateBlack = candidate * candidatePixelAlpha;
                    double referenceWhite = referenceBlack + 1 - referencePixelAlpha;
                    double candidateWhite = candidateBlack + 1 - candidatePixelAlpha;
                    referenceChannels[channel][pixel] = referenceBlack;
                    candidateChannels[channel][pixel] = candidateBlack;
                    referenceChannels[channel + 3][pixel] = referenceWhite;
                    candidateChannels[channel + 3][pixel] = candidateWhite;
                    Accumulate(referenceBlack - candidateBlack);
                    Accumulate(referenceWhite - candidateWhite);
                }
                else
                {
                    referenceChannels[channel][pixel] = reference;
                    candidateChannels[channel][pixel] = candidate;
                    Accumulate(reference - candidate);
                }

                void Accumulate(double delta)
                {
                    rgbSquaredError += delta * delta;
                    pixelSquaredError += delta * delta;
                }
            }
            rgbErrors[pixel] = Math.Sqrt(pixelSquaredError / comparedChannelCount);
        }

        double mse = rgbSquaredError / (pixelCount * comparedChannelCount);
        double psnr = mse <= 1e-12 ? 100 : 10 * Math.Log10(1.0 / mse);
        double rgbSsim = Enumerable.Range(0, comparedChannelCount)
            .Average(channel => WindowedSsim(referenceChannels[channel], candidateChannels[channel], referenceWidth, referenceHeight));

        int semanticPixelCount = semanticWidth * semanticHeight;
        var referenceAlpha = new double[semanticPixelCount];
        var candidateAlpha = new double[semanticPixelCount];
        for (int pixel = 0; pixel < semanticPixelCount; pixel++)
        {
            referenceAlpha[pixel] = semanticReference[pixel * 4 + 3] / 255.0;
            candidateAlpha[pixel] = candidateRgba[pixel * 4 + 3] / 255.0;
        }
        double[] alphaErrors = referenceAlpha.Zip(candidateAlpha, (reference, candidate) => Math.Abs(reference - candidate)).ToArray();
        double alphaSsim = requiresAlpha
            ? WindowedSsim(referenceAlpha, candidateAlpha, semanticWidth, semanticHeight)
            : 1;
        double alphaMae = requiresAlpha ? alphaErrors.Average() : 0;
        double alphaP99 = requiresAlpha ? Percentile(alphaErrors, 0.99) : 0;

        double referenceCoverage = Coverage(referenceAlpha, alphaThreshold);
        double candidateCoverage = Coverage(candidateAlpha, alphaThreshold);
        double coverageError = isCutout ? Math.Abs(referenceCoverage - candidateCoverage) : 0;
        double cutoutIou = isCutout ? IntersectionOverUnion(referenceAlpha, candidateAlpha, alphaThreshold) : 1;

        double normalMean = 0, normalP95 = 0, normalMax = 0;
        if (isNormalMap)
        {
            var angles = new double[semanticPixelCount];
            for (int pixel = 0; pixel < semanticPixelCount; pixel++)
            {
                int offset = pixel * 4;
                (double rx, double ry, double rz) = DecodeNormal(semanticReference, offset, isDxt5NormalMap);
                (double cx, double cy, double cz) = DecodeNormal(candidateRgba, offset, isDxt5NormalMap);
                double dot = Math.Clamp(rx * cx + ry * cy + rz * cz, -1, 1);
                angles[pixel] = Math.Acos(dot) * 180.0 / Math.PI;
            }
            normalMean = angles.Average();
            normalP95 = Percentile(angles, 0.95);
            normalMax = angles.Max();
        }

        double flipProxyMean = rgbErrors.Average();
        double flipProxyP95 = Percentile(rgbErrors, 0.95);
        double composite = (1 - rgbSsim) + Math.Min(1, mse * 10) + alphaMae +
                           normalMean / 180.0 + coverageError;
        return new VtfQualityMetrics(
            rgbSsim,
            psnr,
            flipProxyMean,
            flipProxyP95,
            alphaSsim,
            alphaMae,
            alphaP99,
            coverageError,
            coverageError,
            cutoutIou,
            normalMean,
            normalP95,
            normalMax,
            composite);
    }

    private static double WindowedSsim(double[] reference, double[] candidate, int width, int height)
    {
        const int window = 8;
        const double c1 = 0.0001;
        const double c2 = 0.0009;
        double total = 0;
        int windows = 0;
        for (int startY = 0; startY < height; startY += window)
        {
            for (int startX = 0; startX < width; startX += window)
            {
                int endX = Math.Min(width, startX + window);
                int endY = Math.Min(height, startY + window);
                int count = (endX - startX) * (endY - startY);
                double meanReference = 0, meanCandidate = 0;
                for (int y = startY; y < endY; y++)
                for (int x = startX; x < endX; x++)
                {
                    int index = y * width + x;
                    meanReference += reference[index];
                    meanCandidate += candidate[index];
                }
                meanReference /= count;
                meanCandidate /= count;

                double varianceReference = 0, varianceCandidate = 0, covariance = 0;
                for (int y = startY; y < endY; y++)
                for (int x = startX; x < endX; x++)
                {
                    int index = y * width + x;
                    double referenceDelta = reference[index] - meanReference;
                    double candidateDelta = candidate[index] - meanCandidate;
                    varianceReference += referenceDelta * referenceDelta;
                    varianceCandidate += candidateDelta * candidateDelta;
                    covariance += referenceDelta * candidateDelta;
                }
                double denominator = Math.Max(1, count - 1);
                varianceReference /= denominator;
                varianceCandidate /= denominator;
                covariance /= denominator;
                total += ((2 * meanReference * meanCandidate + c1) * (2 * covariance + c2)) /
                         ((meanReference * meanReference + meanCandidate * meanCandidate + c1) *
                          (varianceReference + varianceCandidate + c2));
                windows++;
            }
        }
        return Math.Clamp(total / windows, -1, 1);
    }

    private static double Coverage(double[] alpha, double threshold) =>
        (double)alpha.Count(value => value >= threshold) / alpha.Length;

    private static double IntersectionOverUnion(double[] reference, double[] candidate, double threshold)
    {
        int intersection = 0, union = 0;
        for (int index = 0; index < reference.Length; index++)
        {
            bool a = reference[index] >= threshold;
            bool b = candidate[index] >= threshold;
            if (a && b)
                intersection++;
            if (a || b)
                union++;
        }
        return union == 0 ? 1 : (double)intersection / union;
    }

    private static (double X, double Y, double Z) DecodeNormal(byte[] rgba, int offset, bool isDxt5NormalMap)
    {
        double x = rgba[offset + (isDxt5NormalMap ? 3 : 0)] / 127.5 - 1;
        double y = rgba[offset + 1] / 127.5 - 1;
        double z = isDxt5NormalMap
            ? Math.Sqrt(Math.Max(0, 1 - x * x - y * y))
            : rgba[offset + 2] / 127.5 - 1;
        double length = Math.Sqrt(x * x + y * y + z * z);
        return length < 1e-12 ? (0, 0, 1) : (x / length, y / length, z / length);
    }

    private static double Percentile(double[] values, double percentile)
    {
        double[] sorted = (double[])values.Clone();
        Array.Sort(sorted);
        int index = Math.Clamp((int)Math.Ceiling(percentile * sorted.Length) - 1, 0, sorted.Length - 1);
        return sorted[index];
    }

    private static void Validate(byte[] rgba, int width, int height)
    {
        if (width <= 0 || height <= 0 || rgba.Length != checked(width * height * 4))
            throw new ArgumentException("RGBA buffer and dimensions do not match.");
    }

    private static double SrgbToLinear(double value) =>
        value <= 0.04045 ? value / 12.92 : Math.Pow((value + 0.055) / 1.055, 2.4);
}
