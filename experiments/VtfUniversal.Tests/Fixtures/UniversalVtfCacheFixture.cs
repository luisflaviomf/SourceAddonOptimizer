using GmodAddonCompressor.Systems.Vtf.Universal;

namespace VtfUniversal.Tests.Fixtures;

internal static class UniversalVtfCacheFixture
{
    internal const string RelativePath = "materials/models/diggercars/cache/body.vtf";

    internal static UniversalVtfAttemptMatrix Matrix(
        string root,
        string relativePath = RelativePath)
    {
        UniversalVtfMetricSet floor = Metrics(
            double.PositiveInfinity,
            1.0,
            1.0,
            includeSpecialValues: true);
        UniversalVtfMetricSet second = Metrics(25.0, 0.94, 0.45);
        return new UniversalVtfAttemptMatrix(
            relativePath,
            new[]
            {
                Attempt(
                    root,
                    level: 1,
                    UniversalVtfFilterRoute.SrgbColor,
                    floor,
                    new[]
                    {
                        Route(root, 1, UniversalVtfFilterRoute.SrgbColor, floor),
                        new UniversalVtfRouteAttempt(
                            UniversalVtfFilterRoute.NumericLinear,
                            CandidatePath(root, 1, UniversalVtfFilterRoute.NumericLinear),
                            false,
                            null,
                            "synthetic_failure")
                    }),
                Attempt(
                    root,
                    level: 2,
                    UniversalVtfFilterRoute.NumericLinear,
                    second,
                    new[]
                    {
                        Route(root, 2, UniversalVtfFilterRoute.SrgbColor, second),
                        Route(root, 2, UniversalVtfFilterRoute.NumericLinear, second)
                    })
            });
    }

    internal static string CandidatePath(
        string root,
        int level,
        UniversalVtfFilterRoute route) => Path.GetFullPath(Path.Combine(
        root,
        $"level-{level}-{route.ToString().ToLowerInvariant()}.vtf"));

    internal static IEqualityComparer<UniversalVtfProfileSelection> SelectionComparer { get; } =
        new ProfileSelectionComparer();

    internal static UniversalVtfAnalysisIdentity AnalysisIdentity(
        string relativePath = RelativePath) => new(
        UniversalVtfCacheKey.AnalysisSchema,
        Sha('a'),
        relativePath,
        Sha('0'),
        2_048,
        new UniversalVtfStructuralIdentity(
            7,
            5,
            96,
            0x40,
            512,
            512,
            1,
            1,
            0,
            1,
            10,
            15,
            13,
            16,
            16,
            false,
            Sha('7'),
            Sha('8'),
            Sha('9')),
        Sha('b'),
        new[] { "diffuse" },
        UniversalVtfFilterRoute.SrgbColor,
        Sha('c'),
        Sha('d'),
        Sha('e'),
        Sha('f'),
        UniversalVtfCacheKey.None,
        Sha('1'));

    internal static UniversalVtfCachedAsset Asset(
        string sourceVtf,
        string relativePath = RelativePath)
    {
        string source = Path.GetFullPath(sourceVtf);
        UniversalVtfArtifactReference artifact = UniversalVtfArtifactReference.FromVtf(source);
        UniversalVtfAnalysisIdentity template = AnalysisIdentity(relativePath);
        UniversalVtfAnalysisIdentity analysisIdentity = template with
        {
            RawSha256 = artifact.Sha256,
            RawBytes = artifact.Bytes,
            Structure = template.Structure with
            {
                Width = artifact.Width,
                Height = artifact.Height,
                Depth = artifact.Depth,
                Frames = artifact.Frames,
                Faces = artifact.Faces,
                MipCount = artifact.MipCount,
                HighResFormat = artifact.Format
            }
        };
        UniversalVtfAttemptMatrix runtime = Matrix(
            Path.GetDirectoryName(source)!,
            relativePath);
        UniversalVtfCachedAttemptMatrix matrix =
            UniversalVtfCachedAttemptMatrix.FromRuntime(runtime);
        string analysisKey = UniversalVtfCacheKey.Analysis(analysisIdentity);
        var analysis = new UniversalVtfAnalysisReceipt(
            UniversalVtfCacheContracts.AnalysisReceiptSchema,
            analysisKey,
            analysisIdentity,
            UniversalVtfCacheCoverage.FullAttempts,
            matrix,
            new Dictionary<UniversalVtfCachedArtifactKey, UniversalVtfArtifactReference>
            {
                [new(1, UniversalVtfFilterRoute.SrgbColor)] = artifact,
                [new(2, UniversalVtfFilterRoute.SrgbColor)] = artifact,
                [new(2, UniversalVtfFilterRoute.NumericLinear)] = artifact
            });
        UniversalVtfProfileSelection selected =
            UniversalVtfSelector.Select(runtime, UniversalVtfRuntimeCaps.Empty);
        var selections = new Dictionary<UniversalVtfProfileKind, UniversalVtfCachedSelection>
        {
            [UniversalVtfProfileKind.Conservative] = Cached(
                UniversalVtfProfileKind.Conservative,
                selected.Conservative),
            [UniversalVtfProfileKind.Balanced] = Cached(
                UniversalVtfProfileKind.Balanced,
                selected.Balanced),
            [UniversalVtfProfileKind.Aggressive] = Cached(
                UniversalVtfProfileKind.Aggressive,
                selected.Aggressive)
        };
        var completionIdentity = new UniversalVtfCompletionIdentity(
            UniversalVtfCacheKey.CompletionSchema,
            analysisKey,
            artifact.Sha256,
            artifact.Bytes,
            UniversalVtfCacheKey.None,
            selections);
        string completionKey = UniversalVtfCacheKey.Completion(completionIdentity);
        var completion = new UniversalVtfCompletionReceipt(
            UniversalVtfCacheContracts.CompletionReceiptSchema,
            completionKey,
            completionIdentity,
            selections.ToDictionary(
                pair => pair.Key,
                pair => new UniversalVtfCompletedProfile(
                    pair.Key,
                    pair.Value,
                    null,
                    artifact,
                    artifact.Bytes,
                    artifact.Sha256,
                    false,
                    null,
                    false,
                    1)));
        return new UniversalVtfCachedAsset(
            analysis,
            completion,
            new Dictionary<string, string>(StringComparer.Ordinal)
            {
                [artifact.Sha256] = source
            });
    }

    internal static string Sha(char value) => new(value, 64);

    private static UniversalVtfCachedSelection Cached(
        UniversalVtfProfileKind profile,
        UniversalVtfSelectedLevel selected) => new(
        profile,
        selected.Level,
        selected.Route);

    private static UniversalVtfAttempt Attempt(
        string root,
        int level,
        UniversalVtfFilterRoute route,
        UniversalVtfMetricSet metrics,
        IReadOnlyList<UniversalVtfRouteAttempt> routeAttempts) => new(
        level,
        true,
        metrics,
        "ok",
        route,
        CandidatePath(root, level, route),
        routeAttempts);

    private static UniversalVtfRouteAttempt Route(
        string root,
        int level,
        UniversalVtfFilterRoute route,
        UniversalVtfMetricSet metrics) => new(
        route,
        CandidatePath(root, level, route),
        true,
        metrics,
        "ok");

    private static UniversalVtfMetricSet Metrics(
        double psnr,
        double ssim,
        double edges,
        bool includeSpecialValues = false)
    {
        var semantic = new Dictionary<string, double>(StringComparer.Ordinal)
        {
            [UniversalVtfMetricNames.DiagnosticColorDeltaE] = 0.25,
            [UniversalVtfMetricNames.DiagnosticFrequencyRetention] = 0.95
        };
        var cross = new Dictionary<string, double>(StringComparer.Ordinal);
        if (includeSpecialValues)
        {
            semantic["diagnostic_nan"] = double.NaN;
            semantic["diagnostic_positive_infinity"] = double.PositiveInfinity;
            semantic["diagnostic_negative_infinity"] = double.NegativeInfinity;
            cross["diagnostic_cross_nan"] = double.NaN;
        }

        return new UniversalVtfMetricSet(
            new[]
            {
                new UniversalVtfPlaneMetricSet(
                    new UniversalVtfPlaneKey(0, 0, 0),
                    new UniversalVtfCommonMetrics(psnr, ssim, edges),
                    semantic)
            },
            cross);
    }

    private sealed class ProfileSelectionComparer :
        IEqualityComparer<UniversalVtfProfileSelection>
    {
        public bool Equals(UniversalVtfProfileSelection? x, UniversalVtfProfileSelection? y)
        {
            if (ReferenceEquals(x, y))
            {
                return true;
            }

            if (x is null || y is null ||
                !SelectedEquals(x.Conservative, y.Conservative) ||
                !SelectedEquals(x.Balanced, y.Balanced) ||
                !SelectedEquals(x.Aggressive, y.Aggressive))
            {
                return false;
            }

            foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
            {
                if (!x.Attempts.TryGetValue(profile, out IReadOnlyList<UniversalVtfProfileAttempt>? left) ||
                    !y.Attempts.TryGetValue(profile, out IReadOnlyList<UniversalVtfProfileAttempt>? right) ||
                    !UniversalVtfCanonicalJson.Serialize(left).AsSpan()
                        .SequenceEqual(UniversalVtfCanonicalJson.Serialize(right)))
                {
                    return false;
                }
            }

            return true;
        }

        public int GetHashCode(UniversalVtfProfileSelection obj) => HashCode.Combine(
            obj.Conservative.Level,
            obj.Balanced.Level,
            obj.Aggressive.Level);

        private static bool SelectedEquals(
            UniversalVtfSelectedLevel left,
            UniversalVtfSelectedLevel right) =>
            left.Level == right.Level && left.Reason == right.Reason &&
            left.Route == right.Route && left.CandidatePath == right.CandidatePath;
    }
}
