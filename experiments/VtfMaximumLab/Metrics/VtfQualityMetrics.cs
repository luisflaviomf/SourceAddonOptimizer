namespace VtfMaximumLab.Metrics;

internal sealed record VtfQualityMetrics(
    double RgbSsim,
    double RgbPsnr,
    double FlipMean,
    double FlipP95,
    double AlphaSsim,
    double AlphaMae,
    double AlphaP99,
    double CutoutCoverageErrorTop,
    double CutoutMaxMipCoverageError,
    double CutoutIou,
    double NormalMeanDegrees,
    double NormalP95Degrees,
    double NormalMaxDegrees,
    double CompositeError);
