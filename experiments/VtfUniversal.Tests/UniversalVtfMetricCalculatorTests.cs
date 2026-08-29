using System.Numerics;
using System.Text.Json;
using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfMetricCalculatorTests
{
    [Fact]
    public void IndependentAuditorSentinelsMatchTheFrozenProducerInputs()
    {
        const string bridgeSha256 =
            "9bf65c2b404365c23836b6eb2ff206a0fed6d8c217a71792406b775c8fb6dad1";
        UniversalVtfPolicyReceipt receipt =
            UniversalVtfCorpusBuilder.ResolveEmbeddedPolicyReceipt(bridgeSha256);

        Assert.Equal(
            "de1fd1acd3434d34d6365aec224d56e85a4a319ff935ab708ffa93f87be14266",
            receipt.PolicyFingerprint);
        Assert.Equal(
            "415bd8ae0217aea0f85037eb5004fd3b03246229cfbff43ed28d12e086f0a4c6",
            receipt.ToolchainSha256);
    }

    [Fact]
    public void FrozenFixtureExposesEveryIndependentParityDefinition()
    {
        using JsonDocument document = JsonDocument.Parse(UniversalVtfPolicy.V1.LoadFixtureBytes());
        JsonElement definitions = document.RootElement.GetProperty("definitions");
        JsonElement cases = document.RootElement.GetProperty("cases");

        Assert.Equal("decoded-uint8", definitions.GetProperty("sampleDomain").GetString());
        Assert.Equal("reflect-101",
            definitions.GetProperty("ssim").GetProperty("border").GetString());
        Assert.Equal("population",
            definitions.GetProperty("ssim").GetProperty("covariance").GetString());
        Assert.Equal("nearest-rank-ceil-pn-minus-one",
            definitions.GetProperty("percentile").GetString());
        Assert.Equal("round-to-nearest-even",
            definitions.GetProperty("coverageQuantization").GetString());
        Assert.Equal(32, definitions.GetProperty("coverageBisectionSteps").GetInt32());

        string[] requiredCases =
        {
            "identityRgba", "halfPlaneRgb", "alphaClassification", "normalAngles",
            "normalLayouts", "nearestRank", "reflect101", "retentionZero", "routeTie",
            "coverageScale32", "temporalFrames", "cubemapSeam"
        };
        Assert.All(requiredCases, name => Assert.True(cases.TryGetProperty(name, out _), name));

        JsonElement coverage = cases.GetProperty("coverageScale32");
        Assert.Equal(3, coverage.GetProperty("seedCandidates").GetArrayLength());
        Assert.Equal(32, coverage.GetProperty("bisectionCandidates").GetArrayLength());
        Assert.Equal(
            1.1590909101068974,
            coverage.GetProperty("expectedQuantizedWinningScale").GetDouble(),
            12);
    }

    [Fact]
    public void EmbeddedFrozenFixtureDrivesMetricPrimitives()
    {
        using JsonDocument document = JsonDocument.Parse(UniversalVtfPolicy.V1.LoadFixtureBytes());
        JsonElement cases = document.RootElement.GetProperty("cases");
        JsonElement alpha = cases.GetProperty("alphaClassification");
        Assert.Equal(
            alpha.GetProperty("cutoutLike").GetProperty("expected").GetBoolean(),
            UniversalVtfMetricCalculator.IsCutoutLike(
                alpha.GetProperty("cutoutLike").GetProperty("alpha")
                    .EnumerateArray().Select(value => value.GetByte()).ToArray()));
        Assert.Equal(
            alpha.GetProperty("continuous").GetProperty("expected").GetBoolean(),
            UniversalVtfMetricCalculator.IsCutoutLike(
                alpha.GetProperty("continuous").GetProperty("alpha")
                    .EnumerateArray().Select(value => value.GetByte()).ToArray()));

        foreach (JsonElement pair in cases.GetProperty("normalAngles").GetProperty("pairs").EnumerateArray())
        {
            float[] source = pair.GetProperty("source").EnumerateArray()
                .Select(value => value.GetSingle()).ToArray();
            float[] candidate = pair.GetProperty("candidate").EnumerateArray()
                .Select(value => value.GetSingle()).ToArray();
            Assert.Equal(
                pair.GetProperty("expectedDegrees").GetDouble(),
                UniversalVtfMetricCalculator.AngularErrorDegrees(
                    new Vector3(source[0], source[1], source[2]),
                    new Vector3(candidate[0], candidate[1], candidate[2])),
                12);
        }

        JsonElement rank = cases.GetProperty("nearestRank");
        double[] ascending = rank.GetProperty("ascending").EnumerateArray()
            .Select(value => value.GetDouble()).ToArray();
        Assert.Equal(rank.GetProperty("expectedP95").GetDouble(),
            UniversalVtfMetricCalculator.NearestRank(ascending, .95));
        Assert.Equal(rank.GetProperty("expectedP99").GetDouble(),
            UniversalVtfMetricCalculator.NearestRank(ascending, .99));
    }

    [Fact]
    public void MatchesFrozenIdentityFixture()
    {
        UniversalVtfImagePlane plane = Solid(16, 16, 64, 128, 192, 255);

        UniversalVtfPlaneMetricSet metrics = UniversalVtfMetricCalculator.MeasurePlane(
            plane,
            plane,
            Decision(UniversalVtfFilterRoute.SrgbColor, UniversalVtfSemanticClass.Unknown));

        Assert.True(double.IsPositiveInfinity(metrics.Common.RgbPsnr));
        Assert.Equal(1, metrics.Common.RgbSsim, 12);
        Assert.Equal(1, metrics.Common.EdgeRetention, 12);
        Assert.Equal(1, metrics.SemanticValues[UniversalVtfMetricNames.AlphaSsim], 12);
        Assert.Equal(0, metrics.SemanticValues[UniversalVtfMetricNames.AlphaMae], 12);
        Assert.Equal(0, metrics.SemanticValues[UniversalVtfMetricNames.AlphaP99], 12);
    }

    [Fact]
    public void LocksFrozenHalfPlaneValues()
    {
        UniversalVtfImagePlane reference = GrayRows(
            16,
            16,
            Enumerable.Repeat((byte)0, 8).Concat(Enumerable.Repeat((byte)255, 8)).ToArray());
        UniversalVtfImagePlane candidate = GrayRows(
            16,
            16,
            Enumerable.Repeat((byte)128, 16).ToArray());

        UniversalVtfPlaneMetricSet metrics = UniversalVtfMetricCalculator.MeasurePlane(
            reference,
            candidate,
            Decision(UniversalVtfFilterRoute.SrgbColor, UniversalVtfSemanticClass.Diffuse));

        Assert.Equal(6.020533124945522, metrics.Common.RgbPsnr, 12);
        Assert.Equal(0, metrics.Common.EdgeRetention, 12);
    }

    [Fact]
    public void LocksFrozenAlphaClassificationAndBinaryMetrics()
    {
        byte[] cutout = { 0, 255, 0, 255, 0, 255, 0, 255 };
        byte[] continuous = { 0, 128, 255, 0, 128, 255, 0, 128, 255 };
        byte[] half = { 0, 0, 0, 0, 255, 255, 255, 255 };

        Assert.True(UniversalVtfMetricCalculator.IsCutoutLike(cutout));
        Assert.False(UniversalVtfMetricCalculator.IsCutoutLike(continuous));

        UniversalVtfImagePlane plane = AlphaPlane(half);
        UniversalVtfPlaneMetricSet metrics = UniversalVtfMetricCalculator.MeasurePlane(
            plane,
            plane,
            Decision(
                UniversalVtfFilterRoute.SrgbColor,
                UniversalVtfSemanticClass.Diffuse,
                UniversalVtfSemanticClass.Cutout));

        Assert.Equal(0, metrics.SemanticValues[UniversalVtfMetricNames.CutoutCoverageDrift], 12);
        Assert.Equal(1, metrics.SemanticValues[UniversalVtfMetricNames.CutoutIou], 12);
        Assert.Equal(0, metrics.SemanticValues[UniversalVtfMetricNames.CutoutHausdorff], 12);
    }

    [Fact]
    public void UnknownCompositeInfersCutoutOnlyFromTheOriginalEightBitHistogram()
    {
        UniversalVtfSemanticDecision unknown = Decision(
            UniversalVtfFilterRoute.Composite,
            UniversalVtfSemanticClass.Unknown);
        UniversalVtfPlaneMetricSet binary = UniversalVtfMetricCalculator.MeasurePlane(
            AlphaPlane(new byte[] { 0, 255, 0, 255, 0, 255, 0, 255 }),
            AlphaPlane(new byte[] { 0, 255, 0, 255, 0, 255, 0, 255 }),
            unknown);
        UniversalVtfPlaneMetricSet continuous = UniversalVtfMetricCalculator.MeasurePlane(
            AlphaPlane(new byte[] { 0, 128, 255, 0, 128, 255, 0, 128, 255 }),
            AlphaPlane(new byte[] { 0, 128, 255, 0, 128, 255, 0, 128, 255 }),
            unknown);

        Assert.True(binary.SemanticValues.ContainsKey(UniversalVtfMetricNames.CutoutIou));
        Assert.False(continuous.SemanticValues.ContainsKey(UniversalVtfMetricNames.CutoutIou));
    }

    [Theory]
    [InlineData(1, 0, 0, 1, 0, 0, 0)]
    [InlineData(1, 0, 0, 0, 1, 0, 90)]
    [InlineData(1, 0, 0, -1, 0, 0, 180)]
    public void LocksFrozenNormalAngles(
        double ax,
        double ay,
        double az,
        double bx,
        double by,
        double bz,
        double expected)
    {
        double actual = UniversalVtfMetricCalculator.AngularErrorDegrees(
            new Vector3((float)ax, (float)ay, (float)az),
            new Vector3((float)bx, (float)by, (float)bz));

        Assert.Equal(expected, actual, 12);
    }

    [Fact]
    public void LocksNormalLayoutBranchesAndConsumedAlpha()
    {
        UniversalVtfImagePlane rgb = Solid(2, 2, 255, 128, 128, 64);
        UniversalVtfImagePlane dxt5 = Solid(2, 2, 64, 128, 192, 255);

        UniversalVtfPlaneMetricSet rgbMetrics = UniversalVtfMetricCalculator.MeasurePlane(
            rgb,
            rgb,
            Decision(
                UniversalVtfFilterRoute.RgbNormal,
                UniversalVtfSemanticClass.Normal,
                UniversalVtfSemanticClass.ScalarMask));
        UniversalVtfPlaneMetricSet dxt5Metrics = UniversalVtfMetricCalculator.MeasurePlane(
            dxt5,
            dxt5,
            Decision(UniversalVtfFilterRoute.Dxt5NmAg, UniversalVtfSemanticClass.Normal));

        Assert.Equal(0, rgbMetrics.SemanticValues[UniversalVtfMetricNames.NormalMaximumDegrees], 12);
        Assert.Equal(1, rgbMetrics.SemanticValues[UniversalVtfMetricNames.AlphaSsim], 12);
        Assert.Equal(0, dxt5Metrics.SemanticValues[UniversalVtfMetricNames.NormalMeanDegrees], 12);
        Assert.False(dxt5Metrics.SemanticValues.ContainsKey(UniversalVtfMetricNames.AlphaMae));
    }

    [Fact]
    public void SsbumpEvaluatesRgbAsIndependentScalars()
    {
        UniversalVtfImagePlane reference = Solid(2, 2, 32, 128, 224, 255);
        UniversalVtfImagePlane candidate = Solid(2, 2, 33, 126, 220, 255);

        UniversalVtfPlaneMetricSet metrics = UniversalVtfMetricCalculator.MeasurePlane(
            reference,
            candidate,
            Decision(
                UniversalVtfFilterRoute.SsbumpScalars,
                UniversalVtfSemanticClass.Normal,
                UniversalVtfSemanticClass.Ssbump));

        Assert.False(metrics.SemanticValues.ContainsKey(UniversalVtfMetricNames.NormalMeanDegrees));
        Assert.True(metrics.SemanticValues[UniversalVtfMetricNames.AlphaMae] > 0);
        Assert.Equal(4d / 255, metrics.SemanticValues[UniversalVtfMetricNames.AlphaP99], 12);
    }

    [Fact]
    public void LocksNearestRankReflect101AndRetentionZeroRules()
    {
        double[] ascending = Enumerable.Range(1, 20).Select(value => (double)value).ToArray();
        int[] indices = { -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8 };

        Assert.Equal(19, UniversalVtfMetricCalculator.NearestRank(ascending, .95));
        Assert.Equal(20, UniversalVtfMetricCalculator.NearestRank(ascending, .99));
        Assert.Equal(
            new[] { 1, 2, 3, 2, 1, 0, 1, 2, 3, 2, 1, 0, 1, 2 },
            indices.Select(index => UniversalVtfMetricCalculator.Reflect101(index, 4)));
        Assert.Equal(1, UniversalVtfMetricCalculator.Retention(0, 0), 12);
        Assert.True(double.IsNaN(UniversalVtfMetricCalculator.Retention(0, 1)));
    }

    [Fact]
    public void CieDe2000MatchesPublishedReferencePair()
    {
        double actual = UniversalVtfMetricCalculator.CieDe2000(
            new UniversalVtfLab(50, 2.6772, -79.7751),
            new UniversalVtfLab(50, 0, -82.7485));

        Assert.Equal(2.0424596801565738, actual, 12);
    }

    [Fact]
    public void SpecializedIdentityMetricsAreExact()
    {
        UniversalVtfImagePlane reference = Pattern(16, 16);
        UniversalVtfSemanticDecision semantics = Decision(
            UniversalVtfFilterRoute.SrgbColor,
            UniversalVtfSemanticClass.Diffuse,
            UniversalVtfSemanticClass.Emissive,
            UniversalVtfSemanticClass.Glass,
            UniversalVtfSemanticClass.Detail,
            UniversalVtfSemanticClass.Text);

        UniversalVtfPlaneMetricSet metrics = UniversalVtfMetricCalculator.MeasurePlane(
            reference,
            reference,
            semantics);

        Assert.Equal(0, metrics.SemanticValues[UniversalVtfMetricNames.EmissiveEnergyDrift], 12);
        Assert.Equal(1, metrics.SemanticValues[UniversalVtfMetricNames.EmissiveHotspotIou], 12);
        Assert.Equal(0, metrics.SemanticValues[UniversalVtfMetricNames.GlassMeanDeltaE], 12);
        Assert.Equal(1, metrics.SemanticValues[UniversalVtfMetricNames.GlassEdgeRetention], 12);
        Assert.Equal(1, metrics.SemanticValues[UniversalVtfMetricNames.DetailFrequencyRetention], 12);
        Assert.Equal(0, metrics.SemanticValues[UniversalVtfMetricNames.DetailSeamIncrease], 12);
    }

    [Fact]
    public void CutoutTracksComponentsAndSymmetricHausdorff()
    {
        byte[] sourceAlpha = new byte[8 * 4];
        byte[] candidateAlpha = new byte[8 * 4];
        for (int y = 0; y < 4; y++)
        {
            for (int x = 0; x < 4; x++)
            {
                sourceAlpha[y * 8 + x] = 255;
                candidateAlpha[y * 8 + x + 1] = 255;
            }
        }

        UniversalVtfSemanticDecision semantics = Decision(
            UniversalVtfFilterRoute.SrgbColor,
            UniversalVtfSemanticClass.Diffuse,
            UniversalVtfSemanticClass.Cutout);
        UniversalVtfPlaneMetricSet metrics = UniversalVtfMetricCalculator.MeasurePlane(
            AlphaPlane(sourceAlpha, 8, 4),
            AlphaPlane(candidateAlpha, 8, 4),
            semantics);

        Assert.Equal(1, metrics.SemanticValues[UniversalVtfMetricNames.CutoutComponentRetention], 12);
        Assert.Equal(1, metrics.SemanticValues[UniversalVtfMetricNames.CutoutHausdorff], 12);
    }

    [Fact]
    public void FrozenTemporalFixtureProducesInfiniteSignedDifferencePsnr()
    {
        byte[] first = { 0, 64, 128, 255 };
        byte[] second = { 16, 80, 144, 239 };
        UniversalVtfSemanticDecision semantics = Decision(
            UniversalVtfFilterRoute.SrgbColor,
            UniversalVtfSemanticClass.Diffuse,
            UniversalVtfSemanticClass.Animated);
        UniversalVtfMetricSet metrics = UniversalVtfMetricCalculator.MeasureAllPlanes(
            new[] { GrayRow(first, frame: 0), GrayRow(second, frame: 1) },
            new[] { GrayRow(first, frame: 0), GrayRow(second, frame: 1) },
            semantics);

        double temporal = Assert.Single(metrics.CrossPlaneValues
            .Where(pair => pair.Key.StartsWith(UniversalVtfMetricNames.TemporalPsnr, StringComparison.Ordinal)))
            .Value;
        Assert.True(double.IsPositiveInfinity(temporal));
    }

    [Fact]
    public void CubemapIdentityCarriesEveryPairAndOrientation()
    {
        UniversalVtfSemanticDecision semantics = Decision(
            UniversalVtfFilterRoute.SrgbColor,
            UniversalVtfSemanticClass.Cubemap);
        UniversalVtfImagePlane[] faces = Enumerable.Range(0, 6)
            .Select(face => Pattern(4, 4) with { Face = face })
            .ToArray();

        UniversalVtfMetricSet metrics = UniversalVtfMetricCalculator.MeasureAllPlanes(
            faces,
            faces,
            semantics);

        KeyValuePair<string, double>[] seams = metrics.CrossPlaneValues
            .Where(pair => pair.Key.StartsWith(UniversalVtfMetricNames.CubemapSeamIncrease, StringComparison.Ordinal))
            .ToArray();
        Assert.Equal(12, seams.Length);
        Assert.All(seams, pair => Assert.Equal(0, pair.Value, 12));
        Assert.All(seams, pair => Assert.Contains("edge=", pair.Key, StringComparison.Ordinal));
        string[] sourceOrientations =
        {
            "f0:Left-f4:Right:forward",
            "f0:Right-f5:Left:forward",
            "f0:Top-f2:Right:reversed",
            "f0:Bottom-f3:Right:forward",
            "f1:Left-f5:Right:forward",
            "f1:Right-f4:Left:forward",
            "f1:Top-f2:Left:forward",
            "f1:Bottom-f3:Left:reversed",
            "f2:Top-f5:Top:reversed",
            "f2:Bottom-f4:Top:forward",
            "f3:Top-f4:Bottom:forward",
            "f3:Bottom-f5:Bottom:reversed"
        };
        Assert.All(sourceOrientations, orientation =>
            Assert.Contains(seams, pair => pair.Key.Contains(orientation, StringComparison.Ordinal)));
    }

    [Fact]
    public void WorstFrameFailsEvenWhenAnotherFrameIsPerfect()
    {
        UniversalVtfImagePlane good = Solid(16, 16, 128, 128, 128, 255, frame: 0);
        UniversalVtfImagePlane badReference = Solid(16, 16, 0, 0, 0, 255, frame: 1);
        UniversalVtfImagePlane badCandidate = Solid(16, 16, 255, 255, 255, 255, frame: 1);
        UniversalVtfSemanticDecision semantics = Decision(
            UniversalVtfFilterRoute.SrgbColor,
            UniversalVtfSemanticClass.Diffuse);

        UniversalVtfMetricSet metrics = UniversalVtfMetricCalculator.MeasureAllPlanes(
            new[] { good, badReference },
            new[] { good, badCandidate },
            semantics);
        UniversalVtfEvaluation result = UniversalVtfMetricCalculator.EvaluateWorstPlane(
            metrics,
            UniversalVtfPolicy.V1.GetThresholds(UniversalVtfProfileKind.Balanced));

        Assert.False(result.Passed);
        Assert.Equal(new UniversalVtfPlaneKey(Frame: 1, Face: 0, DepthSlice: 0), result.WorstGate.Plane);
        Assert.Equal(2, metrics.Planes.Count);
    }

    [Theory]
    [InlineData(0, 6, 0)]
    [InlineData(0, 0, 2)]
    public void WorstFaceOrDepthSliceCannotBeHidden(int frame, int face, int depth)
    {
        UniversalVtfImagePlane good = Solid(16, 16, 128, 128, 128, 255);
        UniversalVtfImagePlane referenceBad = Solid(16, 16, 0, 0, 0, 255, frame, face, depth);
        UniversalVtfImagePlane candidateBad = Solid(16, 16, 255, 255, 255, 255, frame, face, depth);
        UniversalVtfSemanticDecision semantics = Decision(
            UniversalVtfFilterRoute.SrgbColor,
            UniversalVtfSemanticClass.Diffuse);

        UniversalVtfEvaluation result = UniversalVtfMetricCalculator.EvaluateWorstPlane(
            UniversalVtfMetricCalculator.MeasureAllPlanes(
                new[] { good, referenceBad },
                new[] { good, candidateBad },
                semantics),
            UniversalVtfPolicy.V1.GetThresholds(UniversalVtfProfileKind.Balanced));

        Assert.False(result.Passed);
        Assert.Equal(new UniversalVtfPlaneKey(frame, face, depth), result.WorstGate.Plane);
    }

    [Fact]
    public void PlaneKeySetsMustMatchExactly()
    {
        UniversalVtfImagePlane reference = Solid(2, 2, 0, 0, 0, 255, frame: 0);
        UniversalVtfImagePlane candidate = reference with { Frame = 1 };

        Assert.Throws<InvalidDataException>(() =>
            UniversalVtfMetricCalculator.MeasureAllPlanes(
                new[] { reference },
                new[] { candidate },
                Decision(UniversalVtfFilterRoute.SrgbColor, UniversalVtfSemanticClass.Diffuse)));
    }

    [Fact]
    public void AnimatedAndCubemapCrossPlaneGatesFailClosedOnIncompleteSets()
    {
        UniversalVtfImagePlane plane = Solid(2, 2, 0, 0, 0, 255);

        Assert.Throws<InvalidDataException>(() =>
            UniversalVtfMetricCalculator.MeasureAllPlanes(
                new[] { plane },
                new[] { plane },
                Decision(
                    UniversalVtfFilterRoute.SrgbColor,
                    UniversalVtfSemanticClass.Diffuse,
                    UniversalVtfSemanticClass.Animated)));
        Assert.Throws<InvalidDataException>(() =>
            UniversalVtfMetricCalculator.MeasureAllPlanes(
                new[] { plane },
                new[] { plane },
                Decision(
                    UniversalVtfFilterRoute.SrgbColor,
                    UniversalVtfSemanticClass.Cubemap)));
    }

    private static UniversalVtfSemanticDecision Decision(
        UniversalVtfFilterRoute route,
        params UniversalVtfSemanticClass[] classes) =>
        new(
            new HashSet<UniversalVtfSemanticClass>(classes),
            route,
            HasUnknownConsumer: classes.Contains(UniversalVtfSemanticClass.Unknown),
            AlphaTestReference: 0.5,
            Consumers: Array.Empty<VtfConsumerEvidence>(),
            Uncertainties: Array.Empty<string>());

    private static UniversalVtfImagePlane Solid(
        int width,
        int height,
        byte red,
        byte green,
        byte blue,
        byte alpha,
        int frame = 0,
        int face = 0,
        int depth = 0)
    {
        var rgba = new byte[checked(width * height * 4)];
        for (int offset = 0; offset < rgba.Length; offset += 4)
        {
            rgba[offset] = red;
            rgba[offset + 1] = green;
            rgba[offset + 2] = blue;
            rgba[offset + 3] = alpha;
        }

        return new UniversalVtfImagePlane(width, height, frame, face, depth, rgba);
    }

    private static UniversalVtfImagePlane GrayRows(
        int width,
        int height,
        byte[] row)
    {
        Assert.Equal(width, row.Length);
        var rgba = new byte[checked(width * height * 4)];
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                byte value = row[x];
                int offset = (y * width + x) * 4;
                rgba[offset] = value;
                rgba[offset + 1] = value;
                rgba[offset + 2] = value;
                rgba[offset + 3] = 255;
            }
        }

        return new UniversalVtfImagePlane(width, height, 0, 0, 0, rgba);
    }

    private static UniversalVtfImagePlane GrayRow(byte[] row, int frame)
    {
        var rgba = new byte[row.Length * 4];
        for (int index = 0; index < row.Length; index++)
        {
            rgba[index * 4] = row[index];
            rgba[index * 4 + 1] = row[index];
            rgba[index * 4 + 2] = row[index];
            rgba[index * 4 + 3] = 255;
        }

        return new UniversalVtfImagePlane(row.Length, 1, frame, 0, 0, rgba);
    }

    private static UniversalVtfImagePlane AlphaPlane(byte[] alpha, int? width = null, int height = 1)
    {
        int actualWidth = width ?? alpha.Length;
        Assert.Equal(alpha.Length, actualWidth * height);
        var rgba = new byte[alpha.Length * 4];
        for (int index = 0; index < alpha.Length; index++)
        {
            rgba[index * 4] = 255;
            rgba[index * 4 + 1] = 255;
            rgba[index * 4 + 2] = 255;
            rgba[index * 4 + 3] = alpha[index];
        }

        return new UniversalVtfImagePlane(actualWidth, height, 0, 0, 0, rgba);
    }

    private static UniversalVtfImagePlane Pattern(int width, int height)
    {
        var rgba = new byte[checked(width * height * 4)];
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                int offset = (y * width + x) * 4;
                rgba[offset] = (byte)((x * 31 + y * 7) & 255);
                rgba[offset + 1] = (byte)((x * 13 + y * 47) & 255);
                rgba[offset + 2] = (byte)(((x ^ y) * 29) & 255);
                rgba[offset + 3] = (byte)((x * 17 + y * 19) & 255);
            }
        }

        return new UniversalVtfImagePlane(width, height, 0, 0, 0, rgba);
    }
}
