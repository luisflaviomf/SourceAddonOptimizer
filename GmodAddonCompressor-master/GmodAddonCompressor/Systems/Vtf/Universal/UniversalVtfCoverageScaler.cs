namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfCoverageScale(
    double Scale,
    double TargetCoverage,
    double AchievedCoverage,
    IReadOnlyList<double> Candidates);

internal static class UniversalVtfCoverageScaler
{
    private const int BisectionIterations = 32;
    private const double MaximumScale = 8.0;

    internal static UniversalVtfCoverageScale FindScale(
        ReadOnlySpan<byte> alpha,
        double threshold,
        double targetCoverage)
    {
        if (alpha.IsEmpty)
        {
            throw new ArgumentException("Alpha plane cannot be empty.", nameof(alpha));
        }

        RequireUnitInterval(threshold, nameof(threshold));
        RequireUnitInterval(targetCoverage, nameof(targetCoverage));

        byte thresholdByte = ToByte(threshold);
        var candidates = new List<double>(3 + BisectionIterations)
        {
            0.0,
            1.0,
            MaximumScale
        };

        double low = 0.0;
        double high = MaximumScale;
        for (int iteration = 0; iteration < BisectionIterations; iteration++)
        {
            double scale = (low + high) * 0.5;
            candidates.Add(scale);
            double coverage = Coverage(alpha, thresholdByte, scale);
            if (coverage < targetCoverage)
            {
                low = scale;
            }
            else
            {
                high = scale;
            }
        }

        double bestScale = candidates[0];
        double bestCoverage = Coverage(alpha, thresholdByte, bestScale);
        for (int index = 1; index < candidates.Count; index++)
        {
            double scale = candidates[index];
            double coverage = Coverage(alpha, thresholdByte, scale);
            if (IsBetter(scale, coverage, bestScale, bestCoverage, targetCoverage))
            {
                bestScale = scale;
                bestCoverage = coverage;
            }
        }

        return new UniversalVtfCoverageScale(
            bestScale,
            targetCoverage,
            bestCoverage,
            candidates.AsReadOnly());
    }

    internal static double Coverage(
        ReadOnlySpan<byte> alpha,
        double threshold,
        double scale = 1.0)
    {
        if (alpha.IsEmpty)
        {
            throw new ArgumentException("Alpha plane cannot be empty.", nameof(alpha));
        }

        RequireUnitInterval(threshold, nameof(threshold));
        if (!double.IsFinite(scale) || scale < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(scale));
        }

        return Coverage(alpha, ToByte(threshold), scale);
    }

    internal static byte ApplyScale(byte alpha, double scale)
    {
        if (!double.IsFinite(scale) || scale < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(scale));
        }

        return ToByte(Math.Clamp(alpha / 255.0 * scale, 0.0, 1.0));
    }

    private static double Coverage(
        ReadOnlySpan<byte> alpha,
        byte threshold,
        double scale)
    {
        int covered = 0;
        foreach (byte value in alpha)
        {
            if (ApplyScale(value, scale) >= threshold)
            {
                covered++;
            }
        }

        return (double)covered / alpha.Length;
    }

    private static bool IsBetter(
        double candidateScale,
        double candidateCoverage,
        double incumbentScale,
        double incumbentCoverage,
        double targetCoverage)
    {
        double candidateError = Math.Abs(candidateCoverage - targetCoverage);
        double incumbentError = Math.Abs(incumbentCoverage - targetCoverage);
        int comparison = candidateError.CompareTo(incumbentError);
        if (comparison != 0)
        {
            return comparison < 0;
        }

        double candidateDistance = LogDistance(candidateScale);
        double incumbentDistance = LogDistance(incumbentScale);
        comparison = candidateDistance.CompareTo(incumbentDistance);
        if (comparison != 0)
        {
            return comparison < 0;
        }

        return candidateScale < incumbentScale;
    }

    private static double LogDistance(double scale) =>
        scale <= 0 ? double.PositiveInfinity : Math.Abs(Math.Log2(scale));

    private static void RequireUnitInterval(double value, string parameterName)
    {
        if (!double.IsFinite(value) || value is < 0 or > 1)
        {
            throw new ArgumentOutOfRangeException(parameterName);
        }
    }

    private static byte ToByte(double value) =>
        (byte)Math.Clamp(
            (int)Math.Round(value * 255.0, MidpointRounding.ToEven),
            0,
            255);
}
