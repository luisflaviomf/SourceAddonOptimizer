using VtfMaximumLab.Candidates;
using VtfMaximumLab.Metrics;

namespace VtfMaximumLab.Selection;

internal sealed record VtfCandidateAssessment(
    VtfCandidateSpec Spec,
    string CandidatePath,
    long SizeBytes,
    long OriginalSizeBytes,
    bool StructurallyValid,
    bool VersionMatches,
    bool RequiresAlpha,
    bool IsCutout,
    VtfQualityMetrics Metrics,
    VtfQualityMetrics? CurrentMetrics = null);

internal sealed record VtfAcceptanceResult(bool Accepted, IReadOnlyList<string> Reasons);

internal static class VtfQualityGate
{
    internal static VtfAcceptanceResult Accept(VtfCandidateAssessment assessment)
    {
        var reasons = new List<string>();
        if (!assessment.StructurallyValid)
            reasons.Add("structure");
        if (!assessment.VersionMatches)
            reasons.Add("version");
        if (assessment.SizeBytes >= assessment.OriginalSizeBytes)
            reasons.Add("no_gain");
        if (assessment.IsCutout)
        {
            if (assessment.Metrics.CutoutCoverageErrorTop > 0.01)
                reasons.Add("cutout_top_coverage");
            if (assessment.Metrics.CutoutMaxMipCoverageError > 0.02)
                reasons.Add("cutout_mip_coverage");
            if (assessment.Metrics.CutoutIou < 0.98)
                reasons.Add("cutout_iou");
        }
        if (assessment.RequiresAlpha && !assessment.IsCutout)
        {
            if (assessment.Metrics.AlphaSsim < 0.98)
                reasons.Add("alpha_ssim");
            if (assessment.Metrics.AlphaMae > 0.02)
                reasons.Add("alpha_mae");
            if (assessment.Metrics.AlphaP99 > 0.08)
                reasons.Add("alpha_p99");
        }
        if (assessment.Spec.IsNormalMap)
        {
            if (assessment.Metrics.NormalMeanDegrees > 3)
                reasons.Add("normal_mean");
            if (assessment.Metrics.NormalP95Degrees > 8)
                reasons.Add("normal_p95");
            if (assessment.Metrics.NormalMaxDegrees > 25)
                reasons.Add("normal_max");
        }
        if (reasons.Count > 0)
            return new VtfAcceptanceResult(false, reasons);

        List<string> absoluteReasons = GetAbsoluteReasons(assessment);
        if (absoluteReasons.Count == 0)
            return new VtfAcceptanceResult(true, Array.Empty<string>());

        if (assessment.Spec.ScaleDivisor <= 2 && assessment.CurrentMetrics is VtfQualityMetrics)
        {
            List<string> relativeReasons = GetRelativeReasons(assessment);
            if (relativeReasons.Count == 0)
                return new VtfAcceptanceResult(true, Array.Empty<string>());
            absoluteReasons.AddRange(relativeReasons);
        }

        return new VtfAcceptanceResult(false, absoluteReasons.Distinct(StringComparer.Ordinal).ToArray());
    }

    private static List<string> GetAbsoluteReasons(VtfCandidateAssessment assessment)
    {
        var reasons = new List<string>();
        if (assessment.Metrics.RgbSsim < 0.95)
            reasons.Add("rgb_ssim");
        if (assessment.Metrics.RgbPsnr < 30)
            reasons.Add("rgb_psnr");
        if (assessment.Metrics.FlipMean > 0.05)
            reasons.Add("flip_mean");
        if (assessment.Metrics.FlipP95 > 0.15)
            reasons.Add("flip_p95");

        if (assessment.RequiresAlpha)
        {
            if (assessment.Metrics.AlphaSsim < 0.98)
                reasons.Add("alpha_ssim");
            if (assessment.Metrics.AlphaMae > 0.02)
                reasons.Add("alpha_mae");
            if (assessment.Metrics.AlphaP99 > 0.08)
                reasons.Add("alpha_p99");
        }

        if (assessment.IsCutout)
        {
            if (assessment.Metrics.CutoutCoverageErrorTop > 0.01)
                reasons.Add("cutout_top_coverage");
            if (assessment.Metrics.CutoutMaxMipCoverageError > 0.02)
                reasons.Add("cutout_mip_coverage");
            if (assessment.Metrics.CutoutIou < 0.98)
                reasons.Add("cutout_iou");
        }

        if (assessment.Spec.IsNormalMap)
        {
            if (assessment.Metrics.NormalMeanDegrees > 3)
                reasons.Add("normal_mean");
            if (assessment.Metrics.NormalP95Degrees > 8)
                reasons.Add("normal_p95");
            if (assessment.Metrics.NormalMaxDegrees > 25)
                reasons.Add("normal_max");
        }

        return reasons;
    }

    private static List<string> GetRelativeReasons(VtfCandidateAssessment assessment)
    {
        VtfQualityMetrics current = assessment.CurrentMetrics!;
        var reasons = new List<string>();
        if (assessment.Metrics.RgbSsim < current.RgbSsim - 0.005)
            reasons.Add("relative_rgb_ssim");
        if (assessment.Metrics.RgbPsnr < current.RgbPsnr - 0.5)
            reasons.Add("relative_rgb_psnr");
        if (assessment.Metrics.FlipMean > current.FlipMean + 0.005)
            reasons.Add("relative_flip_mean");
        if (assessment.Metrics.FlipP95 > current.FlipP95 + 0.01)
            reasons.Add("relative_flip_p95");
        if (assessment.RequiresAlpha)
        {
            if (assessment.Metrics.AlphaSsim < current.AlphaSsim - 0.005)
                reasons.Add("relative_alpha_ssim");
            if (assessment.Metrics.AlphaMae > current.AlphaMae + 0.005)
                reasons.Add("relative_alpha_mae");
            if (assessment.Metrics.AlphaP99 > current.AlphaP99 + 0.02)
                reasons.Add("relative_alpha_p99");
        }
        if (assessment.IsCutout)
        {
            if (assessment.Metrics.CutoutCoverageErrorTop > current.CutoutCoverageErrorTop + 0.005)
                reasons.Add("relative_cutout_coverage");
            if (assessment.Metrics.CutoutIou < current.CutoutIou - 0.005)
                reasons.Add("relative_cutout_iou");
        }
        if (assessment.Spec.IsNormalMap)
        {
            if (assessment.Metrics.NormalMeanDegrees > current.NormalMeanDegrees + 0.5)
                reasons.Add("relative_normal_mean");
            if (assessment.Metrics.NormalP95Degrees > current.NormalP95Degrees + 1)
                reasons.Add("relative_normal_p95");
            if (assessment.Metrics.NormalMaxDegrees > current.NormalMaxDegrees + 5)
                reasons.Add("relative_normal_max");
        }
        return reasons;
    }
}

internal static class VtfCandidateSelector
{
    internal static VtfCandidateAssessment? Select(IEnumerable<VtfCandidateAssessment> assessments)
    {
        return assessments
            .Where(assessment => VtfQualityGate.Accept(assessment).Accepted)
            .OrderBy(assessment => assessment.SizeBytes)
            .ThenBy(assessment => assessment.Spec.ScaleDivisor)
            .ThenBy(assessment => assessment.Metrics.CompositeError)
            .ThenBy(assessment => assessment.Spec.Encoder, StringComparer.OrdinalIgnoreCase)
            .FirstOrDefault();
    }
}
