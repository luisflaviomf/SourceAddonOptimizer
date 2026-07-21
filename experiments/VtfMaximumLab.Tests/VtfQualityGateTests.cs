using VtfMaximumLab.Candidates;
using VtfMaximumLab.Metrics;
using VtfMaximumLab.Selection;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class VtfQualityGateTests
{
    [Fact]
    public void SmallerCandidateIsRejectedWhenRgbGateFails()
    {
        VtfCandidateAssessment assessment = Assessment("small", 90, 1, rgbSsim: 0.94);

        VtfAcceptanceResult result = VtfQualityGate.Accept(assessment);

        Assert.False(result.Accepted);
        Assert.Contains("rgb_ssim", result.Reasons);
    }

    [Fact]
    public void EqualSizePrefersHigherResolutionThenLowerError()
    {
        VtfCandidateAssessment[] candidates =
        {
            Assessment("quarter", 50, 4, rgbSsim: 0.99),
            Assessment("full", 50, 1, rgbSsim: 0.97)
        };

        VtfCandidateAssessment winner = VtfCandidateSelector.Select(candidates)!;

        Assert.Equal(1, winner.Spec.ScaleDivisor);
    }

    [Fact]
    public void HalfResolutionMayPassByNonInferiorityWhenBothCurrentAndCandidateMissAbsoluteGate()
    {
        VtfCandidateAssessment candidate = Assessment("candidate", 50, 2, rgbSsim: 0.93) with
        {
            CurrentMetrics = Assessment("current", 60, 2, rgbSsim: 0.92).Metrics
        };

        Assert.True(VtfQualityGate.Accept(candidate).Accepted);
    }

    [Fact]
    public void QuarterResolutionCannotUseNonInferiorityToBypassAbsoluteGate()
    {
        VtfCandidateAssessment candidate = Assessment("candidate", 25, 4, rgbSsim: 0.93) with
        {
            CurrentMetrics = Assessment("current", 60, 2, rgbSsim: 0.92).Metrics
        };

        VtfAcceptanceResult result = VtfQualityGate.Accept(candidate);

        Assert.False(result.Accepted);
        Assert.Contains("rgb_ssim", result.Reasons);
    }

    [Fact]
    public void NonInferiorityCannotBypassPerMipCutoutCoverageGate()
    {
        VtfCandidateAssessment baseline = Assessment("current", 60, 2, rgbSsim: 0.92);
        VtfQualityMetrics badMipCoverage = baseline.Metrics with { CutoutMaxMipCoverageError = 0.03 };
        VtfCandidateAssessment candidate = Assessment("candidate", 50, 2, rgbSsim: 0.93) with
        {
            IsCutout = true,
            Metrics = badMipCoverage,
            CurrentMetrics = badMipCoverage
        };

        VtfAcceptanceResult result = VtfQualityGate.Accept(candidate);

        Assert.False(result.Accepted);
        Assert.Contains("cutout_mip_coverage", result.Reasons);
    }

    [Fact]
    public void NonInferiorityCannotBypassNormalAngularGate()
    {
        VtfCandidateAssessment baseline = Assessment("current", 60, 2, rgbSsim: 0.92);
        VtfQualityMetrics badNormal = baseline.Metrics with { NormalP95Degrees = 12 };
        VtfCandidateAssessment candidate = baseline with
        {
            Spec = baseline.Spec with { IsNormalMap = true },
            SizeBytes = 50,
            Metrics = badNormal,
            CurrentMetrics = badNormal
        };

        VtfAcceptanceResult result = VtfQualityGate.Accept(candidate);

        Assert.False(result.Accepted);
        Assert.Contains("normal_p95", result.Reasons);
    }

    [Fact]
    public void NonInferiorityCannotBypassGradualAlphaGate()
    {
        VtfCandidateAssessment baseline = Assessment("current", 60, 2, rgbSsim: 0.92);
        VtfQualityMetrics badAlpha = baseline.Metrics with { AlphaMae = 0.1 };
        VtfCandidateAssessment candidate = baseline with
        {
            SizeBytes = 50,
            RequiresAlpha = true,
            Metrics = badAlpha,
            CurrentMetrics = badAlpha
        };

        VtfAcceptanceResult result = VtfQualityGate.Accept(candidate);

        Assert.False(result.Accepted);
        Assert.Contains("alpha_mae", result.Reasons);
    }

    private static VtfCandidateAssessment Assessment(string encoder, long size, int divisor, double rgbSsim)
    {
        var spec = new VtfCandidateSpec(divisor, 256 / divisor, 256 / divisor, VtfTargetFormat.Dxt1,
            encoder, false, false, false);
        var metrics = new VtfQualityMetrics(
            rgbSsim, 35, 0.01, 0.03, 1, 0, 0,
            0, 1, 0, 0, 0, 0, 0);
        return new VtfCandidateAssessment(spec, "candidate.vtf", size, 100, true, true, false, false, metrics);
    }
}
