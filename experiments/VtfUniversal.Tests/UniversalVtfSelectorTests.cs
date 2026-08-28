using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversal.Tests.Fixtures;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfSelectorTests : IDisposable
{
    private readonly string _root = Path.Combine(
        Path.GetTempPath(),
        "GmodAddonOptimizer",
        "UniversalVtfSelectorTests",
        Guid.NewGuid().ToString("N"));

    public UniversalVtfSelectorTests() => Directory.CreateDirectory(_root);

    [Fact]
    public void LevelOneDeficitDoesNotStopHigherAttempts()
    {
        UniversalVtfAttemptMatrix matrix = Matrix(
            Attempt(1, 20),
            Attempt(2, 31));

        UniversalVtfProfileSelection selection =
            UniversalVtfSelector.Select(matrix, UniversalVtfRuntimeCaps.Empty);

        Assert.Equal(2, selection.Conservative.Level);
        Assert.Contains(selection.Attempts[UniversalVtfProfileKind.Conservative],
            attempt => attempt.Level == 1 && attempt.Reason == "forced_floor_quality_deficit");
        Assert.Contains(selection.Attempts[UniversalVtfProfileKind.Conservative],
            attempt => attempt.Level == 2 && attempt.WasConsidered);
    }

    [Fact]
    public void ProfilesAreNested()
    {
        UniversalVtfAttemptMatrix matrix = Matrix(
            Attempt(1, 40),
            Attempt(2, 27),
            Attempt(3, 25),
            Attempt(4, 23));

        UniversalVtfProfileSelection selection =
            UniversalVtfSelector.Select(matrix, UniversalVtfRuntimeCaps.Empty);

        Assert.Equal((2, 3, 4),
            (selection.Conservative.Level,
                selection.Balanced.Level,
                selection.Aggressive.Level));
    }

    [Fact]
    public void BalancedRuntimeCapAlsoCapsAggressiveButNotConservative()
    {
        UniversalVtfAttemptMatrix matrix = Matrix(
            Attempt(1, 40),
            Attempt(2, 25),
            Attempt(3, 24.5),
            Attempt(4, 22.5));
        UniversalVtfRuntimeCaps caps = UniversalVtfRuntimeCaps.FromVerifiedEntries(new[]
        {
            new UniversalVtfRuntimeCap(
                "materials/a.vtf",
                UniversalVtfProfileKind.Balanced,
                2,
                "manual-gmod-regression")
        });

        UniversalVtfProfileSelection selection = UniversalVtfSelector.Select(matrix, caps);

        Assert.Equal((1, 2, 2),
            (selection.Conservative.Level,
                selection.Balanced.Level,
                selection.Aggressive.Level));
    }

    [Fact]
    public void CapBelowUniversalFloorIsRejected()
    {
        Assert.Throws<InvalidDataException>(() =>
            UniversalVtfRuntimeCaps.FromVerifiedEntries(new[]
            {
                new UniversalVtfRuntimeCap(
                    "materials/a.vtf",
                    UniversalVtfProfileKind.Conservative,
                    0,
                    "invalid")
            }));
    }

    [Fact]
    public void RuntimeCapThatBreaksProfileNestingIsRejected()
    {
        UniversalVtfAttemptMatrix matrix = Matrix(
            Attempt(1, 40),
            Attempt(2, 27),
            Attempt(3, 25));
        UniversalVtfRuntimeCaps caps = UniversalVtfRuntimeCaps.FromVerifiedEntries(new[]
        {
            new UniversalVtfRuntimeCap(
                "materials/a.vtf",
                UniversalVtfProfileKind.Balanced,
                1,
                "manual-gmod-regression")
        });

        Assert.Throws<InvalidDataException>(() => UniversalVtfSelector.Select(matrix, caps));
    }

    [Fact]
    public void LaterLevelsAreNotConsideredAfterFirstFailure()
    {
        UniversalVtfAttemptMatrix matrix = Matrix(
            Attempt(1, 40),
            Attempt(2, 10),
            Attempt(3, 40));

        UniversalVtfProfileSelection selection =
            UniversalVtfSelector.Select(matrix, UniversalVtfRuntimeCaps.Empty);

        Assert.Equal(1, selection.Conservative.Level);
        Assert.Contains(selection.Attempts[UniversalVtfProfileKind.Conservative],
            attempt => attempt.Level == 3 && !attempt.WasConsidered &&
                       attempt.Reason == "not_considered_after_first_failure");
    }

    [Fact]
    public void StructuralFailureAtMandatoryFloorBlocksSelection()
    {
        UniversalVtfAttempt invalid = Attempt(1, 40) with
        {
            StructurallyValid = false,
            StructuralReason = "decode_failed"
        };

        Assert.Throws<InvalidDataException>(() =>
            UniversalVtfSelector.Select(Matrix(invalid), UniversalVtfRuntimeCaps.Empty));
    }

    [Fact]
    public void RuntimeCapsRequireExplicitHashAndCanonicalBytes()
    {
        string path = Path.Combine(_root, "caps.json");
        UniversalVtfRuntimeCapsFrozen frozen = UniversalVtfRuntimeCaps.WriteCanonical(
            path,
            new[]
            {
                new UniversalVtfRuntimeCap(
                    "materials/a.vtf",
                    UniversalVtfProfileKind.Balanced,
                    2,
                    "manual-gmod-regression")
            });
        File.AppendAllText(path, " ");

        Assert.Throws<InvalidDataException>(() =>
            UniversalVtfRuntimeCaps.LoadCanonical(path, frozen.Sha256));
    }

    [Fact]
    public async Task BuildAttemptsMeasuresFinalizedCandidateAgainstRawMipZero()
    {
        using VtfFixtureFile source = VtfFixtureBuilder.WriteResource75WithMips(8, 8, 4);
        string raw = source.Path;
        string candidate = Path.Combine(_root, "promoted.vtf");
        await UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(
            raw,
            1,
            candidate,
            CancellationToken.None);
        UniversalVtfInventoryEntry entry = UniversalVtfInventoryScanner
            .Scan(Path.GetDirectoryName(raw)!)
            .Entries.Single();
        var semantic = new UniversalVtfSemanticDecision(
            new HashSet<UniversalVtfSemanticClass> { UniversalVtfSemanticClass.Diffuse },
            UniversalVtfFilterRoute.Composite,
            false,
            .5,
            Array.Empty<VtfConsumerEvidence>(),
            Array.Empty<string>());

        UniversalVtfAttemptMatrix matrix = UniversalVtfSelector.BuildAttempts(
            entry,
            new[]
            {
                new UniversalVtfCandidateArtifact(
                    1,
                    raw,
                    candidate,
                    UniversalVtfFilterRoute.SrgbColor),
                new UniversalVtfCandidateArtifact(
                    1,
                    raw,
                    candidate,
                    UniversalVtfFilterRoute.NumericLinear)
            },
            semantic);

        UniversalVtfAttempt attempt = Assert.Single(matrix.Attempts);
        Assert.True(attempt.StructurallyValid, attempt.StructuralReason);
        Assert.NotNull(attempt.Metrics);
        Assert.Equal(UniversalVtfFilterRoute.NumericLinear, attempt.Route);
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }

    private static UniversalVtfAttemptMatrix Matrix(params UniversalVtfAttempt[] attempts) =>
        new("materials/a.vtf", attempts);

    private static UniversalVtfAttempt Attempt(int level, double psnr)
    {
        var metrics = new UniversalVtfMetricSet(
            new[]
            {
                new UniversalVtfPlaneMetricSet(
                    new UniversalVtfPlaneKey(0, 0, 0),
                    new UniversalVtfCommonMetrics(psnr, .999, 1),
                    new Dictionary<string, double>())
            },
            new Dictionary<string, double>());
        return new UniversalVtfAttempt(
            level,
            true,
            metrics,
            "ok",
            UniversalVtfFilterRoute.NumericLinear,
            $"candidate-{level}.vtf",
            Array.Empty<UniversalVtfRouteAttempt>());
    }
}
