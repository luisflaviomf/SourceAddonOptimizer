namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal static class UniversalVtfMetricNames
{
    internal const string AlphaSsim = "alpha_ssim";
    internal const string AlphaMae = "alpha_mae";
    internal const string AlphaP99 = "alpha_p99";
    internal const string NormalMeanDegrees = "normal_mean_degrees";
    internal const string NormalP95Degrees = "normal_p95_degrees";
    internal const string NormalMaximumDegrees = "normal_maximum_degrees";
    internal const string CutoutCoverageDrift = "cutout_coverage_top_drift";
    internal const string CutoutRetainedCoverageDrift = "cutout_coverage_retained_drift";
    internal const string CutoutIou = "cutout_iou";
    internal const string CutoutComponentRetention = "cutout_component_retention";
    internal const string CutoutHausdorff = "cutout_hausdorff";
    internal const string EmissiveEnergyDrift = "emissive_energy_drift";
    internal const string EmissiveHotspotIou = "emissive_hotspot_iou";
    internal const string EmissivePeakLoss = "diagnostic_emissive_peak_loss";
    internal const string EmissiveChromaDrift = "diagnostic_emissive_chroma_drift";
    internal const string GlassMeanDeltaE = "glass_mean_delta_e_2000";
    internal const string GlassEdgeRetention = "glass_silhouette_edge_retention";
    internal const string DetailFrequencyRetention = "detail_frequency_energy_retention";
    internal const string DetailSeamIncrease = "detail_wrap_seam_increase";
    internal const string TemporalPsnr = "temporal_signed_difference_psnr";
    internal const string CubemapSeamIncrease = "cubemap_cross_face_seam_increase";
    internal const string MarkerTextStrict = "marker_text_strict";
    internal const string DiagnosticColorDeltaE = "diagnostic_common_mean_delta_e_2000";
    internal const string DiagnosticFrequencyRetention = "diagnostic_common_frequency_retention";
}

internal readonly record struct UniversalVtfPlaneKey(
    int Frame,
    int Face,
    int DepthSlice);

internal readonly record struct UniversalVtfLab(double L, double A, double B);

internal sealed record UniversalVtfCommonMetrics(
    double RgbPsnr,
    double RgbSsim,
    double EdgeRetention);

internal sealed record UniversalVtfPlaneMetricSet(
    UniversalVtfPlaneKey Key,
    UniversalVtfCommonMetrics Common,
    IReadOnlyDictionary<string, double> SemanticValues);

internal sealed record UniversalVtfMetricSet(
    IReadOnlyList<UniversalVtfPlaneMetricSet> Planes,
    IReadOnlyDictionary<string, double> CrossPlaneValues);

internal sealed record UniversalVtfGateResult(
    string Name,
    double Value,
    string Operator,
    double Threshold,
    double NormalizedMargin,
    bool Passed,
    string Reason,
    UniversalVtfPlaneKey? Plane,
    string? PlanePair);

internal sealed record UniversalVtfEvaluation(
    IReadOnlyList<UniversalVtfGateResult> Gates,
    UniversalVtfGateResult WorstGate,
    bool Passed);
