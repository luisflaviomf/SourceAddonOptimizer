using System.Numerics;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal static class UniversalVtfMetricCalculator
{
    private const double Epsilon = 1e-12;
    private const double SsimK1 = 0.01;
    private const double SsimK2 = 0.03;
    private const int SsimRadius = 5;
    private const double SsimSigma = 1.5;

    private static readonly double[] SsimKernel = BuildGaussianKernel();

    private static readonly CubeEdgePair[] CubeEdges =
    {
        // Source order: right(+X), left(-X), back(+Y), front(-Y), up(+Z), down(-Z).
        new(0, BorderEdge.Left, 4, BorderEdge.Right, false),
        new(0, BorderEdge.Right, 5, BorderEdge.Left, false),
        new(0, BorderEdge.Top, 2, BorderEdge.Right, true),
        new(0, BorderEdge.Bottom, 3, BorderEdge.Right, false),
        new(1, BorderEdge.Left, 5, BorderEdge.Right, false),
        new(1, BorderEdge.Right, 4, BorderEdge.Left, false),
        new(1, BorderEdge.Top, 2, BorderEdge.Left, false),
        new(1, BorderEdge.Bottom, 3, BorderEdge.Left, true),
        new(2, BorderEdge.Top, 5, BorderEdge.Top, true),
        new(2, BorderEdge.Bottom, 4, BorderEdge.Top, false),
        new(3, BorderEdge.Top, 4, BorderEdge.Bottom, false),
        new(3, BorderEdge.Bottom, 5, BorderEdge.Bottom, true)
    };

    internal static UniversalVtfPlaneMetricSet MeasurePlane(
        UniversalVtfImagePlane reference,
        UniversalVtfImagePlane candidate,
        UniversalVtfSemanticDecision semanticDecision)
    {
        ArgumentNullException.ThrowIfNull(reference);
        ArgumentNullException.ThrowIfNull(candidate);
        ArgumentNullException.ThrowIfNull(semanticDecision);
        ValidatePlane(reference, nameof(reference));
        ValidatePlane(candidate, nameof(candidate));
        if (PlaneKey(reference) != PlaneKey(candidate))
        {
            throw new InvalidDataException(
                $"Plane key mismatch: {PlaneKey(reference)} != {PlaneKey(candidate)}.");
        }

        double[][] referenceChannels = ExtractChannels(reference);
        double[][] candidateChannels = AlignChannels(candidate, reference.Width, reference.Height);
        double rgbPsnr = Psnr(referenceChannels, candidateChannels, new[] { 0, 1, 2 }, 255);
        double rgbSsim = new[] { 0, 1, 2 }
            .Min(channel => Ssim(
                referenceChannels[channel],
                candidateChannels[channel],
                reference.Width,
                reference.Height));
        double edgeRetention = Retention(
            EdgeEnergy(referenceChannels, reference.Width, reference.Height),
            EdgeEnergy(candidateChannels, reference.Width, reference.Height));

        var semantic = new SortedDictionary<string, double>(StringComparer.Ordinal);
        IReadOnlySet<UniversalVtfSemanticClass> classes = semanticDecision.Classes ??
            throw new InvalidDataException("Semantic classes cannot be null.");
        semantic[UniversalVtfMetricNames.DiagnosticColorDeltaE] =
            MeanDeltaE(referenceChannels, candidateChannels);
        semantic[UniversalVtfMetricNames.DiagnosticFrequencyRetention] = Retention(
            HighFrequencyEnergy(referenceChannels, reference.Width, reference.Height),
            HighFrequencyEnergy(candidateChannels, reference.Width, reference.Height));

        if (classes.Contains(UniversalVtfSemanticClass.Normal) &&
            !classes.Contains(UniversalVtfSemanticClass.Ssbump))
        {
            AddNormalMetrics(reference, candidate, semanticDecision.Route, semantic);
        }

        int[] scalarChannels = ScalarChannels(referenceChannels, candidateChannels, semanticDecision);
        if (scalarChannels.Length > 0)
        {
            AddScalarMetrics(
                referenceChannels,
                candidateChannels,
                reference.Width,
                reference.Height,
                scalarChannels,
                semantic);
        }

        bool explicitCutout = classes.Contains(UniversalVtfSemanticClass.Cutout);
        bool inferredCutout = classes.Contains(UniversalVtfSemanticClass.Unknown) &&
                              IsCutoutLike(referenceChannels[3].Select(ToSampleByte).ToArray());
        if (explicitCutout || inferredCutout)
        {
            AddCutoutMetrics(
                referenceChannels[3],
                candidateChannels[3],
                reference.Width,
                reference.Height,
                semanticDecision.AlphaTestReference,
                semantic);
        }

        if (classes.Contains(UniversalVtfSemanticClass.Emissive))
        {
            AddEmissiveMetrics(referenceChannels, candidateChannels, semantic);
        }

        if (classes.Contains(UniversalVtfSemanticClass.Glass))
        {
            AddGlassMetrics(
                referenceChannels,
                candidateChannels,
                reference.Width,
                reference.Height,
                semantic);
        }

        if (classes.Contains(UniversalVtfSemanticClass.Detail))
        {
            AddDetailMetrics(
                referenceChannels,
                candidateChannels,
                reference.Width,
                reference.Height,
                semantic);
        }

        if (classes.Contains(UniversalVtfSemanticClass.Text) ||
            classes.Contains(UniversalVtfSemanticClass.Unknown))
        {
            semantic[UniversalVtfMetricNames.MarkerTextStrict] = 1;
        }

        return new UniversalVtfPlaneMetricSet(
            PlaneKey(reference),
            new UniversalVtfCommonMetrics(rgbPsnr, rgbSsim, edgeRetention),
            semantic);
    }

    internal static UniversalVtfMetricSet MeasureAllPlanes(
        IReadOnlyList<UniversalVtfImagePlane> referencePlanes,
        IReadOnlyList<UniversalVtfImagePlane> candidatePlanes,
        UniversalVtfSemanticDecision semanticDecision)
    {
        ArgumentNullException.ThrowIfNull(referencePlanes);
        ArgumentNullException.ThrowIfNull(candidatePlanes);
        ArgumentNullException.ThrowIfNull(semanticDecision);
        Dictionary<UniversalVtfPlaneKey, UniversalVtfImagePlane> references = IndexPlanes(
            referencePlanes,
            "reference");
        Dictionary<UniversalVtfPlaneKey, UniversalVtfImagePlane> candidates = IndexPlanes(
            candidatePlanes,
            "candidate");

        if (!references.Keys.ToHashSet().SetEquals(candidates.Keys))
        {
            string missing = string.Join(',', OrderKeys(references.Keys.Except(candidates.Keys)));
            string extra = string.Join(',', OrderKeys(candidates.Keys.Except(references.Keys)));
            throw new InvalidDataException($"Plane key sets differ; missing=[{missing}], extra=[{extra}].");
        }

        UniversalVtfPlaneMetricSet[] planes = OrderKeys(references.Keys)
            .Select(key => MeasurePlane(references[key], candidates[key], semanticDecision))
            .ToArray();
        var cross = new SortedDictionary<string, double>(StringComparer.Ordinal);
        if (semanticDecision.Classes.Contains(UniversalVtfSemanticClass.Animated))
        {
            AddTemporalMetrics(references, candidates, cross);
        }

        if (semanticDecision.Classes.Contains(UniversalVtfSemanticClass.Cubemap))
        {
            AddCubemapMetrics(references, candidates, cross);
        }

        return new UniversalVtfMetricSet(planes, cross);
    }

    internal static UniversalVtfEvaluation EvaluateWorstPlane(
        UniversalVtfMetricSet metrics,
        UniversalVtfThresholds thresholds)
    {
        ArgumentNullException.ThrowIfNull(metrics);
        ArgumentNullException.ThrowIfNull(thresholds);
        if (metrics.Planes.Count == 0)
        {
            throw new ArgumentException("At least one plane metric is required.", nameof(metrics));
        }

        var gates = new List<UniversalVtfGateResult>();
        foreach (UniversalVtfPlaneMetricSet plane in metrics.Planes)
        {
            AddMinimumGate(gates, "common_rgb_psnr", plane.Common.RgbPsnr,
                thresholds.CommonRgbPsnrMin, plane.Key);
            AddMinimumGate(gates, "common_rgb_ssim", plane.Common.RgbSsim,
                thresholds.CommonRgbSsimMin, plane.Key);
            AddMinimumGate(gates, "common_edge_retention", plane.Common.EdgeRetention,
                thresholds.EdgeRetentionMin, plane.Key);

            IReadOnlyDictionary<string, double> values = plane.SemanticValues;
            AddIfPresentMinimum(gates, values, UniversalVtfMetricNames.AlphaSsim,
                thresholds.AlphaSsimMin, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.AlphaMae,
                thresholds.AlphaMaeMax, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.AlphaP99,
                thresholds.AlphaP99Max, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.NormalMeanDegrees,
                thresholds.NormalMeanMax, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.NormalP95Degrees,
                thresholds.NormalP95Max, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.NormalMaximumDegrees,
                thresholds.NormalMax, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.CutoutCoverageDrift,
                thresholds.CutoutCoverageTopMax, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.CutoutRetainedCoverageDrift,
                thresholds.CutoutCoverageMipsMax, plane.Key);
            AddIfPresentMinimum(gates, values, UniversalVtfMetricNames.CutoutIou,
                thresholds.CutoutIouMin, plane.Key);
            AddIfPresentMinimum(gates, values, UniversalVtfMetricNames.CutoutComponentRetention,
                thresholds.ComponentRetentionMin, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.CutoutHausdorff,
                thresholds.HausdorffMax, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.EmissiveEnergyDrift,
                thresholds.EmissiveEnergyDriftMax, plane.Key);
            AddIfPresentMinimum(gates, values, UniversalVtfMetricNames.EmissiveHotspotIou,
                thresholds.EmissiveHotspotIouMin, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.GlassMeanDeltaE,
                thresholds.GlassDeltaEMax, plane.Key);
            AddIfPresentMinimum(gates, values, UniversalVtfMetricNames.GlassEdgeRetention,
                thresholds.GlassEdgeRetentionMin, plane.Key);
            AddIfPresentMinimum(gates, values, UniversalVtfMetricNames.DetailFrequencyRetention,
                thresholds.DetailFrequencyRetentionMin, plane.Key);
            AddIfPresentMaximum(gates, values, UniversalVtfMetricNames.DetailSeamIncrease,
                thresholds.DetailSeamIncreaseMax, plane.Key);

            if (values.ContainsKey(UniversalVtfMetricNames.MarkerTextStrict))
            {
                AddMinimumGate(gates, "text_rgb_psnr", plane.Common.RgbPsnr,
                    thresholds.TextPsnrMin, plane.Key);
                AddMinimumGate(gates, "text_rgb_ssim", plane.Common.RgbSsim,
                    thresholds.TextSsimMin, plane.Key);
                AddMinimumGate(gates, "text_edge_retention", plane.Common.EdgeRetention,
                    thresholds.TextEdgeRetentionMin, plane.Key);
            }
        }

        foreach ((string name, double value) in metrics.CrossPlaneValues)
        {
            string pair = name.Contains('|', StringComparison.Ordinal)
                ? name[(name.IndexOf('|') + 1)..]
                : name;
            if (name.StartsWith(UniversalVtfMetricNames.TemporalPsnr, StringComparison.Ordinal))
            {
                AddMinimumGate(gates, UniversalVtfMetricNames.TemporalPsnr,
                    value, thresholds.TemporalPsnrMin, null, pair);
            }
            else if (name.StartsWith(UniversalVtfMetricNames.CubemapSeamIncrease, StringComparison.Ordinal))
            {
                AddMaximumGate(gates, UniversalVtfMetricNames.CubemapSeamIncrease,
                    value, thresholds.CubemapSeamIncreaseMax, null, pair);
            }
        }

        UniversalVtfGateResult worst = gates
            .OrderBy(gate => gate.NormalizedMargin)
            .ThenBy(gate => gate.Name, StringComparer.Ordinal)
            .ThenBy(gate => gate.Plane?.Frame ?? int.MaxValue)
            .ThenBy(gate => gate.Plane?.Face ?? int.MaxValue)
            .ThenBy(gate => gate.Plane?.DepthSlice ?? int.MaxValue)
            .ThenBy(gate => gate.PlanePair, StringComparer.Ordinal)
            .First();
        return new UniversalVtfEvaluation(gates, worst, gates.All(gate => gate.Passed));
    }

    internal static bool IsCutoutLike(ReadOnlySpan<byte> alpha)
    {
        if (alpha.IsEmpty)
        {
            throw new ArgumentException("Alpha samples cannot be empty.", nameof(alpha));
        }

        int low = 0;
        int high = 0;
        foreach (byte value in alpha)
        {
            double normalized = value / 255.0;
            if (normalized <= 0.1)
            {
                low++;
            }

            if (normalized >= 0.9)
            {
                high++;
            }
        }

        double count = alpha.Length;
        return (low + high) / count >= 0.95 && low / count >= 0.01 && high / count >= 0.01;
    }

    internal static double AngularErrorDegrees(Vector3 reference, Vector3 candidate)
        => AngularErrorDegrees(
            new DoubleVector3(reference.X, reference.Y, reference.Z),
            new DoubleVector3(candidate.X, candidate.Y, candidate.Z));

    private static double AngularErrorDegrees(
        DoubleVector3 reference,
        DoubleVector3 candidate)
    {
        double referenceLength = reference.Length;
        double candidateLength = candidate.Length;
        if (!double.IsFinite(referenceLength) || !double.IsFinite(candidateLength) ||
            referenceLength <= Epsilon || candidateLength <= Epsilon)
        {
            return double.NaN;
        }

        double dot = reference.Dot(candidate) / (referenceLength * candidateLength);
        return Math.Acos(Math.Clamp(dot, -1, 1)) * 180.0 / Math.PI;
    }

    internal static double NearestRank(IReadOnlyList<double> values, double percentile)
    {
        ArgumentNullException.ThrowIfNull(values);
        if (values.Count == 0)
        {
            throw new ArgumentException("Percentiles require at least one value.", nameof(values));
        }

        if (!double.IsFinite(percentile) || percentile is <= 0 or > 1)
        {
            throw new ArgumentOutOfRangeException(nameof(percentile));
        }

        double[] sorted = values.ToArray();
        if (sorted.Any(value => double.IsNaN(value)))
        {
            return double.NaN;
        }

        Array.Sort(sorted);
        int index = Math.Clamp((int)Math.Ceiling(percentile * sorted.Length) - 1, 0, sorted.Length - 1);
        return sorted[index];
    }

    internal static int Reflect101(int index, int length)
    {
        if (length <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(length));
        }

        if (length == 1)
        {
            return 0;
        }

        int period = checked(2 * length - 2);
        int folded = index % period;
        if (folded < 0)
        {
            folded += period;
        }

        return folded < length ? folded : period - folded;
    }

    internal static double Retention(double source, double candidate)
    {
        if (double.IsNaN(source) || double.IsNaN(candidate) || source < 0 || candidate < 0)
        {
            return double.NaN;
        }

        if (Math.Abs(source) <= Epsilon)
        {
            return Math.Abs(candidate) <= Epsilon ? 1.0 : double.NaN;
        }

        return candidate / source;
    }

    internal static double CieDe2000(UniversalVtfLab first, UniversalVtfLab second)
    {
        double c1 = Math.Sqrt(first.A * first.A + first.B * first.B);
        double c2 = Math.Sqrt(second.A * second.A + second.B * second.B);
        double meanC = (c1 + c2) * 0.5;
        double meanC7 = Math.Pow(meanC, 7);
        double g = 0.5 * (1 - Math.Sqrt(meanC7 / (meanC7 + Math.Pow(25.0, 7))));
        double a1Prime = (1 + g) * first.A;
        double a2Prime = (1 + g) * second.A;
        double c1Prime = Math.Sqrt(a1Prime * a1Prime + first.B * first.B);
        double c2Prime = Math.Sqrt(a2Prime * a2Prime + second.B * second.B);
        double h1Prime = HueDegrees(first.B, a1Prime);
        double h2Prime = HueDegrees(second.B, a2Prime);

        double deltaLPrime = second.L - first.L;
        double deltaCPrime = c2Prime - c1Prime;
        double deltaHPrimeDegrees;
        if (c1Prime * c2Prime <= Epsilon)
        {
            deltaHPrimeDegrees = 0;
        }
        else
        {
            double difference = h2Prime - h1Prime;
            deltaHPrimeDegrees = Math.Abs(difference) <= 180
                ? difference
                : difference > 180 ? difference - 360 : difference + 360;
        }

        double deltaHPrime = 2 * Math.Sqrt(c1Prime * c2Prime) *
                             Math.Sin(DegreesToRadians(deltaHPrimeDegrees * 0.5));
        double meanLPrime = (first.L + second.L) * 0.5;
        double meanCPrime = (c1Prime + c2Prime) * 0.5;
        double meanHPrime;
        if (c1Prime * c2Prime <= Epsilon)
        {
            meanHPrime = h1Prime + h2Prime;
        }
        else if (Math.Abs(h1Prime - h2Prime) <= 180)
        {
            meanHPrime = (h1Prime + h2Prime) * 0.5;
        }
        else if (h1Prime + h2Prime < 360)
        {
            meanHPrime = (h1Prime + h2Prime + 360) * 0.5;
        }
        else
        {
            meanHPrime = (h1Prime + h2Prime - 360) * 0.5;
        }

        double t = 1
                   - 0.17 * Math.Cos(DegreesToRadians(meanHPrime - 30))
                   + 0.24 * Math.Cos(DegreesToRadians(2 * meanHPrime))
                   + 0.32 * Math.Cos(DegreesToRadians(3 * meanHPrime + 6))
                   - 0.20 * Math.Cos(DegreesToRadians(4 * meanHPrime - 63));
        double deltaTheta = 30 * Math.Exp(-Math.Pow((meanHPrime - 275) / 25, 2));
        double meanCPrime7 = Math.Pow(meanCPrime, 7);
        double rc = 2 * Math.Sqrt(meanCPrime7 / (meanCPrime7 + Math.Pow(25.0, 7)));
        double sl = 1 + 0.015 * Math.Pow(meanLPrime - 50, 2) /
                    Math.Sqrt(20 + Math.Pow(meanLPrime - 50, 2));
        double sc = 1 + 0.045 * meanCPrime;
        double sh = 1 + 0.015 * meanCPrime * t;
        double rt = -Math.Sin(DegreesToRadians(2 * deltaTheta)) * rc;
        double l = deltaLPrime / sl;
        double c = deltaCPrime / sc;
        double h = deltaHPrime / sh;
        return Math.Sqrt(l * l + c * c + h * h + rt * c * h);
    }

    internal static double Margin(double value, double threshold, bool minimum)
    {
        if (double.IsNaN(value) || double.IsNaN(threshold))
        {
            return double.NegativeInfinity;
        }

        double denominator = Math.Max(Math.Abs(threshold), Epsilon);
        return minimum
            ? (value - threshold) / denominator
            : (threshold - value) / denominator;
    }

    private static void AddNormalMetrics(
        UniversalVtfImagePlane reference,
        UniversalVtfImagePlane candidate,
        UniversalVtfFilterRoute route,
        IDictionary<string, double> values)
    {
        if (route is not (UniversalVtfFilterRoute.RgbNormal or UniversalVtfFilterRoute.Dxt5NmAg))
        {
            values[UniversalVtfMetricNames.NormalMeanDegrees] = double.NaN;
            values[UniversalVtfMetricNames.NormalP95Degrees] = double.NaN;
            values[UniversalVtfMetricNames.NormalMaximumDegrees] = double.NaN;
            return;
        }

        int count = checked(reference.Width * reference.Height);
        var angles = new double[count];
        for (int y = 0; y < reference.Height; y++)
        {
            for (int x = 0; x < reference.Width; x++)
            {
                int pixel = y * reference.Width + x;
                DoubleVector3 sourceVector = DecodeNormal(reference.Rgba, pixel, route);
                DoubleVector3 candidateVector = BilinearNormal(
                    candidate,
                    x,
                    y,
                    reference.Width,
                    reference.Height,
                    route);
                angles[pixel] = AngularErrorDegrees(sourceVector, candidateVector);
            }
        }

        values[UniversalVtfMetricNames.NormalMeanDegrees] = angles.Any(double.IsNaN)
            ? double.NaN
            : angles.Average();
        values[UniversalVtfMetricNames.NormalP95Degrees] = NearestRank(angles, 0.95);
        values[UniversalVtfMetricNames.NormalMaximumDegrees] = angles.Any(double.IsNaN)
            ? double.NaN
            : angles.Max();
    }

    private static int[] ScalarChannels(
        double[][] reference,
        double[][] candidate,
        UniversalVtfSemanticDecision semantics)
    {
        IReadOnlySet<UniversalVtfSemanticClass> classes = semantics.Classes;
        if (classes.Contains(UniversalVtfSemanticClass.Ssbump))
        {
            return new[] { 0, 1, 2 };
        }

        var channels = new SortedSet<int>();
        bool scalar = classes.Contains(UniversalVtfSemanticClass.ScalarMask);
        foreach (VtfConsumerEvidence consumer in semantics.Consumers)
        {
            foreach (int channel in consumer.ReadChannels)
            {
                bool vectorChannel = classes.Contains(UniversalVtfSemanticClass.Normal) &&
                                     (semantics.Route == UniversalVtfFilterRoute.RgbNormal
                                         ? channel is 0 or 1 or 2
                                         : channel is 1 or 3);
                if (!vectorChannel && channel is >= 0 and <= 3)
                {
                    channels.Add(channel);
                }
            }
        }

        if (scalar && channels.Count == 0)
        {
            channels.Add(classes.Contains(UniversalVtfSemanticClass.Normal) ? 3 : 0);
        }

        bool alphaSemantic = classes.Contains(UniversalVtfSemanticClass.Cutout) ||
                             classes.Contains(UniversalVtfSemanticClass.Glass) ||
                             classes.Contains(UniversalVtfSemanticClass.Unknown);
        bool alphaHasInformation = reference[3].Any(value => value < 255 - Epsilon) ||
                                   candidate[3].Any(value => value < 255 - Epsilon);
        if (alphaSemantic &&
            (!classes.Contains(UniversalVtfSemanticClass.Unknown) || alphaHasInformation ||
             classes.Count == 1))
        {
            channels.Add(3);
        }

        return channels.ToArray();
    }

    private static void AddScalarMetrics(
        double[][] reference,
        double[][] candidate,
        int width,
        int height,
        IReadOnlyList<int> channels,
        IDictionary<string, double> values)
    {
        double ssim = double.PositiveInfinity;
        double mae = 0;
        double p99 = 0;
        foreach (int channel in channels)
        {
            ssim = Math.Min(ssim, Ssim(reference[channel], candidate[channel], width, height));
            double[] errors = reference[channel]
                .Zip(candidate[channel], (first, second) => Math.Abs(first - second) / 255.0)
                .ToArray();
            mae = Math.Max(mae, errors.Average());
            p99 = Math.Max(p99, NearestRank(errors, 0.99));
        }

        values[UniversalVtfMetricNames.AlphaSsim] = ssim;
        values[UniversalVtfMetricNames.AlphaMae] = mae;
        values[UniversalVtfMetricNames.AlphaP99] = p99;
    }

    private static void AddCutoutMetrics(
        double[] reference,
        double[] candidate,
        int width,
        int height,
        double threshold,
        IDictionary<string, double> values)
    {
        if (!double.IsFinite(threshold) || threshold is < 0 or > 1)
        {
            values[UniversalVtfMetricNames.CutoutCoverageDrift] = double.NaN;
            values[UniversalVtfMetricNames.CutoutIou] = double.NaN;
            values[UniversalVtfMetricNames.CutoutComponentRetention] = double.NaN;
            values[UniversalVtfMetricNames.CutoutHausdorff] = double.NaN;
            return;
        }

        double thresholdSample = ToByte(threshold);
        bool[] sourceMask = reference.Select(value => value >= thresholdSample).ToArray();
        bool[] candidateMask = candidate.Select(value => value >= thresholdSample).ToArray();
        double sourceCoverage = sourceMask.Count(value => value) / (double)sourceMask.Length;
        double candidateCoverage = candidateMask.Count(value => value) / (double)candidateMask.Length;
        values[UniversalVtfMetricNames.CutoutCoverageDrift] =
            Math.Abs(sourceCoverage - candidateCoverage);
        values[UniversalVtfMetricNames.CutoutIou] = IntersectionOverUnion(sourceMask, candidateMask);
        values[UniversalVtfMetricNames.CutoutComponentRetention] =
            ComponentRetention(sourceMask, candidateMask, width, height);
        values[UniversalVtfMetricNames.CutoutHausdorff] =
            SymmetricBoundaryHausdorff(sourceMask, candidateMask, width, height);
    }

    private static void AddEmissiveMetrics(
        double[][] reference,
        double[][] candidate,
        IDictionary<string, double> values)
    {
        double[] sourceLuma = LinearLuminance(reference);
        double[] candidateLuma = LinearLuminance(candidate);
        double sourceEnergy = sourceLuma.Sum();
        double candidateEnergy = candidateLuma.Sum();
        double energyDrift = Math.Abs(sourceEnergy) <= Epsilon
            ? Math.Abs(candidateEnergy) <= Epsilon ? 0 : double.NaN
            : Math.Abs(candidateEnergy / sourceEnergy - 1);
        double sourcePeak = sourceLuma.Max();
        double candidatePeak = candidateLuma.Max();
        double threshold = Math.Max(0.8 * sourcePeak, NearestRank(sourceLuma, 0.95));
        bool[] sourceHotspots = sourceLuma.Select(value => value >= threshold && sourcePeak > Epsilon).ToArray();
        bool[] candidateHotspots = candidateLuma.Select(value => value >= threshold && sourcePeak > Epsilon).ToArray();
        double hotspotIou;
        if (sourcePeak <= Epsilon)
        {
            hotspotIou = candidatePeak <= Epsilon ? 1 : 0;
        }
        else
        {
            hotspotIou = IntersectionOverUnion(sourceHotspots, candidateHotspots);
        }

        values[UniversalVtfMetricNames.EmissiveEnergyDrift] = energyDrift;
        values[UniversalVtfMetricNames.EmissiveHotspotIou] = hotspotIou;
        values[UniversalVtfMetricNames.EmissivePeakLoss] = sourcePeak <= Epsilon
            ? candidatePeak <= Epsilon ? 0 : double.NaN
            : Math.Max(0, 1 - candidatePeak / sourcePeak);
        values[UniversalVtfMetricNames.EmissiveChromaDrift] = MeanChromaDistance(reference, candidate);
    }

    private static void AddGlassMetrics(
        double[][] reference,
        double[][] candidate,
        int width,
        int height,
        IDictionary<string, double> values)
    {
        values[UniversalVtfMetricNames.GlassMeanDeltaE] = MeanDeltaE(reference, candidate);
        values[UniversalVtfMetricNames.GlassEdgeRetention] = Retention(
            ScalarEdgeEnergy(reference[3], width, height),
            ScalarEdgeEnergy(candidate[3], width, height));
    }

    private static void AddDetailMetrics(
        double[][] reference,
        double[][] candidate,
        int width,
        int height,
        IDictionary<string, double> values)
    {
        values[UniversalVtfMetricNames.DetailFrequencyRetention] =
            values[UniversalVtfMetricNames.DiagnosticFrequencyRetention];
        values[UniversalVtfMetricNames.DetailSeamIncrease] =
            WrapSeamError(candidate, width, height) - WrapSeamError(reference, width, height);
    }

    private static void AddTemporalMetrics(
        IReadOnlyDictionary<UniversalVtfPlaneKey, UniversalVtfImagePlane> references,
        IReadOnlyDictionary<UniversalVtfPlaneKey, UniversalVtfImagePlane> candidates,
        IDictionary<string, double> values)
    {
        foreach (IGrouping<(int Face, int Depth), UniversalVtfPlaneKey> group in references.Keys
                     .GroupBy(key => (key.Face, key.DepthSlice)))
        {
            UniversalVtfPlaneKey[] keys = group.OrderBy(key => key.Frame).ToArray();
            if (keys.Length < 2)
            {
                throw new InvalidDataException(
                    $"Animated face={group.Key.Face}, depth={group.Key.Depth} has fewer than two frames.");
            }

            for (int index = 1; index < keys.Length; index++)
            {
                UniversalVtfPlaneKey first = keys[index - 1];
                UniversalVtfPlaneKey second = keys[index];
                if (second.Frame != first.Frame + 1)
                {
                    throw new InvalidDataException(
                        $"Animated plane sequence is not consecutive: {first} -> {second}.");
                }

                double psnr = TemporalPsnr(
                    references[first],
                    references[second],
                    candidates[first],
                    candidates[second]);
                values[$"{UniversalVtfMetricNames.TemporalPsnr}|" +
                       $"frame={first.Frame}->{second.Frame};face={first.Face};depth={first.DepthSlice}"] = psnr;
            }
        }
    }

    private static void AddCubemapMetrics(
        IReadOnlyDictionary<UniversalVtfPlaneKey, UniversalVtfImagePlane> references,
        IReadOnlyDictionary<UniversalVtfPlaneKey, UniversalVtfImagePlane> candidates,
        IDictionary<string, double> values)
    {
        IGrouping<(int Frame, int Depth), UniversalVtfPlaneKey>[] groups = references.Keys
            .Where(key => key.Face is >= 0 and <= 5)
            .GroupBy(key => (key.Frame, key.DepthSlice))
            .ToArray();
        if (groups.Length == 0)
        {
            throw new InvalidDataException("Cubemap semantics require faces 0..5.");
        }

        foreach (IGrouping<(int Frame, int Depth), UniversalVtfPlaneKey> group in groups)
        {
            Dictionary<int, UniversalVtfPlaneKey> faces = group.ToDictionary(key => key.Face);
            if (!Enumerable.Range(0, 6).All(faces.ContainsKey))
            {
                throw new InvalidDataException(
                    $"Cubemap frame={group.Key.Frame}, depth={group.Key.Depth} lacks one of faces 0..5.");
            }

            foreach (CubeEdgePair pair in CubeEdges)
            {
                UniversalVtfImagePlane sourceA = references[faces[pair.FaceA]];
                UniversalVtfImagePlane sourceB = references[faces[pair.FaceB]];
                UniversalVtfImagePlane candidateA = candidates[faces[pair.FaceA]];
                UniversalVtfImagePlane candidateB = candidates[faces[pair.FaceB]];
                double sourceSeam = CubeSeamError(
                    sourceA,
                    pair.EdgeA,
                    sourceB,
                    pair.EdgeB,
                    pair.ReverseB,
                    sourceA.Width,
                    sourceA.Height,
                    sourceB.Width,
                    sourceB.Height);
                double candidateSeam = CubeSeamError(
                    candidateA,
                    pair.EdgeA,
                    candidateB,
                    pair.EdgeB,
                    pair.ReverseB,
                    sourceA.Width,
                    sourceA.Height,
                    sourceB.Width,
                    sourceB.Height);
                string orientation = $"f{pair.FaceA}:{pair.EdgeA}-f{pair.FaceB}:{pair.EdgeB}" +
                                     (pair.ReverseB ? ":reversed" : ":forward");
                values[$"{UniversalVtfMetricNames.CubemapSeamIncrease}|" +
                       $"frame={group.Key.Frame};depth={group.Key.Depth};edge={orientation}"] =
                    candidateSeam - sourceSeam;
            }
        }
    }

    private static double TemporalPsnr(
        UniversalVtfImagePlane referenceFirst,
        UniversalVtfImagePlane referenceSecond,
        UniversalVtfImagePlane candidateFirst,
        UniversalVtfImagePlane candidateSecond)
    {
        if (referenceFirst.Width != referenceSecond.Width ||
            referenceFirst.Height != referenceSecond.Height)
        {
            throw new InvalidDataException("Successive reference frames have different dimensions.");
        }

        double[][] sourceA = ExtractChannels(referenceFirst);
        double[][] sourceB = ExtractChannels(referenceSecond);
        double[][] candidateA = AlignChannels(candidateFirst, referenceFirst.Width, referenceFirst.Height);
        double[][] candidateB = AlignChannels(candidateSecond, referenceFirst.Width, referenceFirst.Height);
        double squared = 0;
        long count = 0;
        for (int channel = 0; channel < 3; channel++)
        {
            for (int pixel = 0; pixel < sourceA[channel].Length; pixel++)
            {
                double sourceDifference = sourceB[channel][pixel] - sourceA[channel][pixel];
                double candidateDifference = candidateB[channel][pixel] - candidateA[channel][pixel];
                double delta = sourceDifference - candidateDifference;
                squared += delta * delta;
                count++;
            }
        }

        return PsnrFromError(squared / count, 510);
    }

    private static double CubeSeamError(
        UniversalVtfImagePlane first,
        BorderEdge firstEdge,
        UniversalVtfImagePlane second,
        BorderEdge secondEdge,
        bool reverseSecond,
        int firstTargetWidth,
        int firstTargetHeight,
        int secondTargetWidth,
        int secondTargetHeight)
    {
        double[][] a = Border(
            first,
            firstEdge,
            reverse: false,
            firstTargetWidth,
            firstTargetHeight);
        double[][] b = Border(
            second,
            secondEdge,
            reverseSecond,
            secondTargetWidth,
            secondTargetHeight);
        if (a[0].Length != b[0].Length)
        {
            throw new InvalidDataException("Adjacent cubemap borders have different sample counts.");
        }

        double error = 0;
        int count = checked(a[0].Length * 3);
        for (int channel = 0; channel < 3; channel++)
        {
            for (int sample = 0; sample < a[channel].Length; sample++)
            {
                error += Math.Abs(a[channel][sample] - b[channel][sample]) / 255.0;
            }
        }

        return error / count;
    }

    private static double[][] Border(
        UniversalVtfImagePlane plane,
        BorderEdge edge,
        bool reverse,
        int targetWidth,
        int targetHeight)
    {
        int length = edge is BorderEdge.Left or BorderEdge.Right ? targetHeight : targetWidth;
        var result = new[] { new double[length], new double[length], new double[length] };
        for (int index = 0; index < length; index++)
        {
            int x = edge switch
            {
                BorderEdge.Left => 0,
                BorderEdge.Right => targetWidth - 1,
                _ => index
            };
            int y = edge switch
            {
                BorderEdge.Top => 0,
                BorderEdge.Bottom => targetHeight - 1,
                _ => index
            };
            int target = reverse ? length - 1 - index : index;
            BilinearCoordinates coordinates = Coordinates(
                x,
                y,
                targetWidth,
                targetHeight,
                plane.Width,
                plane.Height);
            for (int channel = 0; channel < 3; channel++)
            {
                double top = Lerp(
                    plane.Rgba[(coordinates.Y0 * plane.Width + coordinates.X0) * 4 + channel],
                    plane.Rgba[(coordinates.Y0 * plane.Width + coordinates.X1) * 4 + channel],
                    coordinates.Tx);
                double bottom = Lerp(
                    plane.Rgba[(coordinates.Y1 * plane.Width + coordinates.X0) * 4 + channel],
                    plane.Rgba[(coordinates.Y1 * plane.Width + coordinates.X1) * 4 + channel],
                    coordinates.Tx);
                result[channel][target] = Lerp(top, bottom, coordinates.Ty);
            }
        }

        return result;
    }

    private static double Psnr(
        double[][] reference,
        double[][] candidate,
        IReadOnlyList<int> channels,
        double peak)
    {
        double squared = 0;
        long count = 0;
        foreach (int channel in channels)
        {
            for (int pixel = 0; pixel < reference[channel].Length; pixel++)
            {
                double delta = reference[channel][pixel] - candidate[channel][pixel];
                squared += delta * delta;
                count++;
            }
        }

        return PsnrFromError(squared / count, peak);
    }

    private static double PsnrFromError(double mse, double peak) =>
        mse <= Epsilon ? double.PositiveInfinity : 10 * Math.Log10(peak * peak / mse);

    private static double Ssim(double[] reference, double[] candidate, int width, int height)
    {
        if (reference.Length != checked(width * height) || candidate.Length != reference.Length)
        {
            throw new ArgumentException("SSIM plane dimensions do not match.");
        }

        double c1 = Math.Pow(SsimK1 * 255, 2);
        double c2 = Math.Pow(SsimK2 * 255, 2);
        double total = 0;
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                double meanA = 0;
                double meanB = 0;
                for (int ky = -SsimRadius; ky <= SsimRadius; ky++)
                {
                    int sampleY = Reflect101(y + ky, height);
                    double wy = SsimKernel[ky + SsimRadius];
                    for (int kx = -SsimRadius; kx <= SsimRadius; kx++)
                    {
                        int sampleX = Reflect101(x + kx, width);
                        double weight = wy * SsimKernel[kx + SsimRadius];
                        int sample = sampleY * width + sampleX;
                        meanA += reference[sample] * weight;
                        meanB += candidate[sample] * weight;
                    }
                }

                double varianceA = 0;
                double varianceB = 0;
                double covariance = 0;
                for (int ky = -SsimRadius; ky <= SsimRadius; ky++)
                {
                    int sampleY = Reflect101(y + ky, height);
                    double wy = SsimKernel[ky + SsimRadius];
                    for (int kx = -SsimRadius; kx <= SsimRadius; kx++)
                    {
                        int sampleX = Reflect101(x + kx, width);
                        double weight = wy * SsimKernel[kx + SsimRadius];
                        int sample = sampleY * width + sampleX;
                        double deltaA = reference[sample] - meanA;
                        double deltaB = candidate[sample] - meanB;
                        varianceA += deltaA * deltaA * weight;
                        varianceB += deltaB * deltaB * weight;
                        covariance += deltaA * deltaB * weight;
                    }
                }

                double numerator = (2 * meanA * meanB + c1) * (2 * covariance + c2);
                double denominator = (meanA * meanA + meanB * meanB + c1) *
                                     (varianceA + varianceB + c2);
                total += denominator <= Epsilon ? 1 : numerator / denominator;
            }
        }

        return Math.Clamp(total / (width * height), -1, 1);
    }

    private static double EdgeEnergy(double[][] channels, int width, int height)
    {
        var luma = new double[channels[0].Length];
        for (int pixel = 0; pixel < luma.Length; pixel++)
        {
            luma[pixel] = 0.2126 * channels[0][pixel] +
                          0.7152 * channels[1][pixel] +
                          0.0722 * channels[2][pixel];
        }

        return ScalarEdgeEnergy(luma, width, height);
    }

    private static double ScalarEdgeEnergy(double[] values, int width, int height)
    {
        double horizontal = 0;
        int horizontalCount = 0;
        if (width > 1)
        {
            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width - 1; x++)
                {
                    int index = y * width + x;
                    horizontal += Math.Abs(values[index + 1] - values[index]);
                    horizontalCount++;
                }
            }
        }

        double vertical = 0;
        int verticalCount = 0;
        if (height > 1)
        {
            for (int y = 0; y < height - 1; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    int index = y * width + x;
                    vertical += Math.Abs(values[index + width] - values[index]);
                    verticalCount++;
                }
            }
        }

        return (horizontalCount == 0 ? 0 : horizontal / horizontalCount) +
               (verticalCount == 0 ? 0 : vertical / verticalCount);
    }

    private static double[] LinearLuminance(double[][] channels)
    {
        var result = new double[channels[0].Length];
        for (int pixel = 0; pixel < result.Length; pixel++)
        {
            result[pixel] = 0.2126 * SrgbToLinear(channels[0][pixel] / 255.0) +
                            0.7152 * SrgbToLinear(channels[1][pixel] / 255.0) +
                            0.0722 * SrgbToLinear(channels[2][pixel] / 255.0);
        }

        return result;
    }

    private static double MeanChromaDistance(double[][] reference, double[][] candidate)
    {
        double total = 0;
        for (int pixel = 0; pixel < reference[0].Length; pixel++)
        {
            UniversalVtfLab a = RgbToLab(reference[0][pixel], reference[1][pixel], reference[2][pixel]);
            UniversalVtfLab b = RgbToLab(candidate[0][pixel], candidate[1][pixel], candidate[2][pixel]);
            total += Math.Sqrt(Math.Pow(a.A - b.A, 2) + Math.Pow(a.B - b.B, 2));
        }

        return total / reference[0].Length;
    }

    private static double MeanDeltaE(double[][] reference, double[][] candidate)
    {
        double total = 0;
        for (int pixel = 0; pixel < reference[0].Length; pixel++)
        {
            total += CieDe2000(
                RgbToLab(reference[0][pixel], reference[1][pixel], reference[2][pixel]),
                RgbToLab(candidate[0][pixel], candidate[1][pixel], candidate[2][pixel]));
        }

        return total / reference[0].Length;
    }

    private static UniversalVtfLab RgbToLab(double red, double green, double blue)
    {
        double r = SrgbToLinear(Math.Clamp(red / 255.0, 0, 1));
        double g = SrgbToLinear(Math.Clamp(green / 255.0, 0, 1));
        double b = SrgbToLinear(Math.Clamp(blue / 255.0, 0, 1));
        double x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047;
        double y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b;
        double z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883;
        double fx = LabFunction(x);
        double fy = LabFunction(y);
        double fz = LabFunction(z);
        return new UniversalVtfLab(116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz));
    }

    private static double LabFunction(double value)
    {
        const double epsilon = 216.0 / 24389.0;
        const double kappa = 24389.0 / 27.0;
        return value > epsilon ? Math.Cbrt(value) : (kappa * value + 16) / 116;
    }

    private static double HighFrequencyEnergy(double[][] channels, int width, int height)
    {
        var values = new Complex[checked(width * height)];
        for (int pixel = 0; pixel < values.Length; pixel++)
        {
            values[pixel] = new Complex(
                (0.2126 * channels[0][pixel] +
                 0.7152 * channels[1][pixel] +
                 0.0722 * channels[2][pixel]) / 255.0,
                0);
        }

        Fft2D(values, width, height);
        double energy = 0;
        for (int y = 0; y < height; y++)
        {
            int shiftedY = (y + height / 2) % height;
            bool centralY = shiftedY >= height / 4 && shiftedY < height - height / 4;
            for (int x = 0; x < width; x++)
            {
                if (x == 0 && y == 0)
                {
                    continue;
                }

                int shiftedX = (x + width / 2) % width;
                bool centralX = shiftedX >= width / 4 && shiftedX < width - width / 4;
                bool outsideCentralHalf = !(centralX && centralY);
                if (outsideCentralHalf)
                {
                    energy += values[y * width + x].Magnitude * values[y * width + x].Magnitude;
                }
            }
        }

        return energy;
    }

    private static void Fft2D(Complex[] values, int width, int height)
    {
        var row = new Complex[width];
        for (int y = 0; y < height; y++)
        {
            Array.Copy(values, y * width, row, 0, width);
            Transform(row);
            Array.Copy(row, 0, values, y * width, width);
        }

        var column = new Complex[height];
        for (int x = 0; x < width; x++)
        {
            for (int y = 0; y < height; y++)
            {
                column[y] = values[y * width + x];
            }

            Transform(column);
            for (int y = 0; y < height; y++)
            {
                values[y * width + x] = column[y];
            }
        }
    }

    private static void Transform(Complex[] values)
    {
        int length = values.Length;
        if ((length & (length - 1)) != 0)
        {
            Complex[] source = (Complex[])values.Clone();
            for (int k = 0; k < length; k++)
            {
                Complex sum = Complex.Zero;
                for (int n = 0; n < length; n++)
                {
                    double angle = -2 * Math.PI * k * n / length;
                    sum += source[n] * Complex.FromPolarCoordinates(1, angle);
                }

                values[k] = sum;
            }

            return;
        }

        for (int i = 1, j = 0; i < length; i++)
        {
            int bit = length >> 1;
            for (; (j & bit) != 0; bit >>= 1)
            {
                j ^= bit;
            }

            j ^= bit;
            if (i < j)
            {
                (values[i], values[j]) = (values[j], values[i]);
            }
        }

        for (int block = 2; block <= length; block <<= 1)
        {
            double angle = -2 * Math.PI / block;
            Complex root = Complex.FromPolarCoordinates(1, angle);
            for (int start = 0; start < length; start += block)
            {
                Complex factor = Complex.One;
                int half = block >> 1;
                for (int offset = 0; offset < half; offset++)
                {
                    Complex even = values[start + offset];
                    Complex odd = values[start + offset + half] * factor;
                    values[start + offset] = even + odd;
                    values[start + offset + half] = even - odd;
                    factor *= root;
                }
            }
        }
    }

    private static double WrapSeamError(double[][] channels, int width, int height)
    {
        double error = 0;
        long count = 0;
        if (width > 1)
        {
            for (int y = 0; y < height; y++)
            {
                for (int channel = 0; channel < 3; channel++)
                {
                    error += Math.Abs(
                        channels[channel][y * width] -
                        channels[channel][y * width + width - 1]) / 255.0;
                    count++;
                }
            }
        }

        if (height > 1)
        {
            for (int x = 0; x < width; x++)
            {
                for (int channel = 0; channel < 3; channel++)
                {
                    error += Math.Abs(
                        channels[channel][x] -
                        channels[channel][(height - 1) * width + x]) / 255.0;
                    count++;
                }
            }
        }

        return count == 0 ? 0 : error / count;
    }

    private static double IntersectionOverUnion(bool[] first, bool[] second)
    {
        int intersection = 0;
        int union = 0;
        for (int index = 0; index < first.Length; index++)
        {
            if (first[index] && second[index])
            {
                intersection++;
            }

            if (first[index] || second[index])
            {
                union++;
            }
        }

        return union == 0 ? 1 : intersection / (double)union;
    }

    private static double ComponentRetention(
        bool[] source,
        bool[] candidate,
        int width,
        int height)
    {
        var visited = new bool[source.Length];
        int qualifying = 0;
        int retained = 0;
        for (int start = 0; start < source.Length; start++)
        {
            if (!source[start] || visited[start])
            {
                continue;
            }

            var component = new List<int>();
            var queue = new Queue<int>();
            queue.Enqueue(start);
            visited[start] = true;
            while (queue.Count > 0)
            {
                int current = queue.Dequeue();
                component.Add(current);
                int x = current % width;
                int y = current / width;
                Visit(x - 1, y);
                Visit(x + 1, y);
                Visit(x, y - 1);
                Visit(x, y + 1);

                void Visit(int nextX, int nextY)
                {
                    if (nextX < 0 || nextX >= width || nextY < 0 || nextY >= height)
                    {
                        return;
                    }

                    int next = nextY * width + nextX;
                    if (source[next] && !visited[next])
                    {
                        visited[next] = true;
                        queue.Enqueue(next);
                    }
                }
            }

            if (component.Count < 16)
            {
                continue;
            }

            qualifying++;
            if (component.Any(index => candidate[index]))
            {
                retained++;
            }
        }

        return qualifying == 0 ? 1 : retained / (double)qualifying;
    }

    private static double SymmetricBoundaryHausdorff(
        bool[] first,
        bool[] second,
        int width,
        int height)
    {
        (int X, int Y)[] a = Boundary(first, width, height);
        (int X, int Y)[] b = Boundary(second, width, height);
        if (a.Length == 0 || b.Length == 0)
        {
            return a.Length == b.Length ? 0 : double.PositiveInfinity;
        }

        return Math.Max(Directed(a, b), Directed(b, a));

        static double Directed((int X, int Y)[] source, (int X, int Y)[] target)
        {
            double maximum = 0;
            foreach ((int x, int y) in source)
            {
                double minimumSquared = double.PositiveInfinity;
                foreach ((int targetX, int targetY) in target)
                {
                    double dx = x - targetX;
                    double dy = y - targetY;
                    minimumSquared = Math.Min(minimumSquared, dx * dx + dy * dy);
                }

                maximum = Math.Max(maximum, Math.Sqrt(minimumSquared));
            }

            return maximum;
        }
    }

    private static (int X, int Y)[] Boundary(bool[] mask, int width, int height)
    {
        var result = new List<(int X, int Y)>();
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                int index = y * width + x;
                if (!mask[index])
                {
                    continue;
                }

                if (x == 0 || x == width - 1 || y == 0 || y == height - 1 ||
                    !mask[index - 1] || !mask[index + 1] ||
                    !mask[index - width] || !mask[index + width])
                {
                    result.Add((x, y));
                }
            }
        }

        return result.ToArray();
    }

    private static DoubleVector3 DecodeNormal(
        byte[] rgba,
        int pixel,
        UniversalVtfFilterRoute route)
    {
        int offset = pixel * 4;
        double x = DecodeSigned(rgba[offset + (route == UniversalVtfFilterRoute.Dxt5NmAg ? 3 : 0)]);
        double y = DecodeSigned(rgba[offset + 1]);
        double z = route == UniversalVtfFilterRoute.Dxt5NmAg
            ? Math.Sqrt(Math.Max(0, 1 - x * x - y * y))
            : DecodeSigned(rgba[offset + 2]);
        return new DoubleVector3(x, y, z).Normalize();
    }

    private static DoubleVector3 BilinearNormal(
        UniversalVtfImagePlane candidate,
        int targetX,
        int targetY,
        int targetWidth,
        int targetHeight,
        UniversalVtfFilterRoute route)
    {
        BilinearCoordinates coordinates = Coordinates(
            targetX,
            targetY,
            targetWidth,
            targetHeight,
            candidate.Width,
            candidate.Height);
        DoubleVector3 top = DoubleVector3.Lerp(
            DecodeNormal(candidate.Rgba, coordinates.Y0 * candidate.Width + coordinates.X0, route),
            DecodeNormal(candidate.Rgba, coordinates.Y0 * candidate.Width + coordinates.X1, route),
            coordinates.Tx);
        DoubleVector3 bottom = DoubleVector3.Lerp(
            DecodeNormal(candidate.Rgba, coordinates.Y1 * candidate.Width + coordinates.X0, route),
            DecodeNormal(candidate.Rgba, coordinates.Y1 * candidate.Width + coordinates.X1, route),
            coordinates.Tx);
        return DoubleVector3.Lerp(top, bottom, coordinates.Ty).Normalize();
    }

    private static double[][] ExtractChannels(UniversalVtfImagePlane plane)
    {
        int pixels = checked(plane.Width * plane.Height);
        var channels = Enumerable.Range(0, 4).Select(_ => new double[pixels]).ToArray();
        for (int pixel = 0; pixel < pixels; pixel++)
        {
            for (int channel = 0; channel < 4; channel++)
            {
                channels[channel][pixel] = plane.Rgba[pixel * 4 + channel];
            }
        }

        return channels;
    }

    private static double[][] AlignChannels(
        UniversalVtfImagePlane candidate,
        int width,
        int height)
    {
        if (candidate.Width == width && candidate.Height == height)
        {
            return ExtractChannels(candidate);
        }

        var channels = Enumerable.Range(0, 4)
            .Select(_ => new double[checked(width * height)])
            .ToArray();
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                BilinearCoordinates coordinates = Coordinates(
                    x, y, width, height, candidate.Width, candidate.Height);
                int output = y * width + x;
                for (int channel = 0; channel < 4; channel++)
                {
                    double top = Lerp(
                        candidate.Rgba[(coordinates.Y0 * candidate.Width + coordinates.X0) * 4 + channel],
                        candidate.Rgba[(coordinates.Y0 * candidate.Width + coordinates.X1) * 4 + channel],
                        coordinates.Tx);
                    double bottom = Lerp(
                        candidate.Rgba[(coordinates.Y1 * candidate.Width + coordinates.X0) * 4 + channel],
                        candidate.Rgba[(coordinates.Y1 * candidate.Width + coordinates.X1) * 4 + channel],
                        coordinates.Tx);
                    channels[channel][output] = Lerp(top, bottom, coordinates.Ty);
                }
            }
        }

        return channels;
    }

    private static BilinearCoordinates Coordinates(
        int targetX,
        int targetY,
        int targetWidth,
        int targetHeight,
        int sourceWidth,
        int sourceHeight)
    {
        double sourceX = (targetX + 0.5) * sourceWidth / targetWidth - 0.5;
        double sourceY = (targetY + 0.5) * sourceHeight / targetHeight - 0.5;
        int floorX = (int)Math.Floor(sourceX);
        int floorY = (int)Math.Floor(sourceY);
        return new BilinearCoordinates(
            Math.Clamp(floorX, 0, sourceWidth - 1),
            Math.Clamp(floorX + 1, 0, sourceWidth - 1),
            Math.Clamp(floorY, 0, sourceHeight - 1),
            Math.Clamp(floorY + 1, 0, sourceHeight - 1),
            Math.Clamp(sourceX - floorX, 0, 1),
            Math.Clamp(sourceY - floorY, 0, 1));
    }

    private static Dictionary<UniversalVtfPlaneKey, UniversalVtfImagePlane> IndexPlanes(
        IReadOnlyList<UniversalVtfImagePlane> planes,
        string label)
    {
        var result = new Dictionary<UniversalVtfPlaneKey, UniversalVtfImagePlane>();
        foreach (UniversalVtfImagePlane plane in planes)
        {
            ValidatePlane(plane, label);
            if (!result.TryAdd(PlaneKey(plane), plane))
            {
                throw new InvalidDataException($"Duplicate {label} plane key: {PlaneKey(plane)}.");
            }
        }

        if (result.Count == 0)
        {
            throw new ArgumentException($"At least one {label} plane is required.", nameof(planes));
        }

        return result;
    }

    private static void ValidatePlane(UniversalVtfImagePlane plane, string label)
    {
        if (plane.Width <= 0 || plane.Height <= 0 || plane.Rgba is null ||
            plane.Rgba.Length != checked(plane.Width * plane.Height * 4))
        {
            throw new ArgumentException($"Invalid {label} RGBA plane.", label);
        }

        if (plane.Frame < 0 || plane.Face < 0 || plane.DepthSlice < 0)
        {
            throw new ArgumentOutOfRangeException(label, "Plane coordinates cannot be negative.");
        }
    }

    private static void AddIfPresentMinimum(
        ICollection<UniversalVtfGateResult> gates,
        IReadOnlyDictionary<string, double> values,
        string name,
        double threshold,
        UniversalVtfPlaneKey plane)
    {
        if (values.TryGetValue(name, out double value))
        {
            AddMinimumGate(gates, name, value, threshold, plane);
        }
    }

    private static void AddIfPresentMaximum(
        ICollection<UniversalVtfGateResult> gates,
        IReadOnlyDictionary<string, double> values,
        string name,
        double threshold,
        UniversalVtfPlaneKey plane)
    {
        if (values.TryGetValue(name, out double value))
        {
            AddMaximumGate(gates, name, value, threshold, plane);
        }
    }

    private static void AddMinimumGate(
        ICollection<UniversalVtfGateResult> gates,
        string name,
        double value,
        double threshold,
        UniversalVtfPlaneKey? plane,
        string? pair = null) =>
        AddGate(gates, name, value, threshold, minimum: true, plane, pair);

    private static void AddMaximumGate(
        ICollection<UniversalVtfGateResult> gates,
        string name,
        double value,
        double threshold,
        UniversalVtfPlaneKey? plane,
        string? pair = null) =>
        AddGate(gates, name, value, threshold, minimum: false, plane, pair);

    private static void AddGate(
        ICollection<UniversalVtfGateResult> gates,
        string name,
        double value,
        double threshold,
        bool minimum,
        UniversalVtfPlaneKey? plane,
        string? pair)
    {
        bool passed = !double.IsNaN(value) && (minimum ? value >= threshold : value <= threshold);
        string reason = double.IsNaN(value)
            ? "metric_nan"
            : passed ? "passed" : "threshold_failed";
        gates.Add(new UniversalVtfGateResult(
            name,
            value,
            minimum ? ">=" : "<=",
            threshold,
            Margin(value, threshold, minimum),
            passed,
            reason,
            plane,
            pair));
    }

    private static double[] BuildGaussianKernel()
    {
        var kernel = new double[SsimRadius * 2 + 1];
        double sum = 0;
        for (int offset = -SsimRadius; offset <= SsimRadius; offset++)
        {
            double value = Math.Exp(-(offset * offset) / (2 * SsimSigma * SsimSigma));
            kernel[offset + SsimRadius] = value;
            sum += value;
        }

        for (int index = 0; index < kernel.Length; index++)
        {
            kernel[index] /= sum;
        }

        return kernel;
    }

    private static double HueDegrees(double b, double aPrime)
    {
        double hue = Math.Atan2(b, aPrime) * 180.0 / Math.PI;
        return hue < 0 ? hue + 360 : hue;
    }

    private static double DegreesToRadians(double degrees) => degrees * Math.PI / 180.0;

    private static double SrgbToLinear(double value) =>
        value <= 0.04045 ? value / 12.92 : Math.Pow((value + 0.055) / 1.055, 2.4);

    private static double DecodeSigned(byte value) => value / 127.5 - 1.0;

    private static byte ToByte(double value) =>
        (byte)Math.Clamp((int)Math.Round(value * 255.0, MidpointRounding.ToEven), 0, 255);

    private static byte ToSampleByte(double value) =>
        (byte)Math.Clamp((int)Math.Round(value, MidpointRounding.ToEven), 0, 255);

    private static double Lerp(double first, double second, double amount) =>
        first + (second - first) * amount;

    private static UniversalVtfPlaneKey PlaneKey(UniversalVtfImagePlane plane) =>
        new(plane.Frame, plane.Face, plane.DepthSlice);

    private static IOrderedEnumerable<UniversalVtfPlaneKey> OrderKeys(
        IEnumerable<UniversalVtfPlaneKey> keys) =>
        keys.OrderBy(key => key.Frame)
            .ThenBy(key => key.Face)
            .ThenBy(key => key.DepthSlice);

    private readonly record struct BilinearCoordinates(
        int X0,
        int X1,
        int Y0,
        int Y1,
        double Tx,
        double Ty);

    private readonly record struct DoubleVector3(double X, double Y, double Z)
    {
        internal double Length => Math.Sqrt(X * X + Y * Y + Z * Z);

        internal double Dot(DoubleVector3 other) => X * other.X + Y * other.Y + Z * other.Z;

        internal DoubleVector3 Normalize()
        {
            double length = Length;
            return !double.IsFinite(length) || length <= Epsilon
                ? new DoubleVector3(double.NaN, double.NaN, double.NaN)
                : new DoubleVector3(X / length, Y / length, Z / length);
        }

        internal static DoubleVector3 Lerp(
            DoubleVector3 first,
            DoubleVector3 second,
            double amount) =>
            new(
                UniversalVtfMetricCalculator.Lerp(first.X, second.X, amount),
                UniversalVtfMetricCalculator.Lerp(first.Y, second.Y, amount),
                UniversalVtfMetricCalculator.Lerp(first.Z, second.Z, amount));
    }

    private enum BorderEdge
    {
        Left,
        Right,
        Top,
        Bottom
    }

    private sealed record CubeEdgePair(
        int FaceA,
        BorderEdge EdgeA,
        int FaceB,
        BorderEdge EdgeB,
        bool ReverseB);
}
