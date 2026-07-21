namespace VtfMaximumLab.Candidates;

internal enum VtfTargetFormat
{
    Preserve,
    Dxt1,
    Dxt1OneBitAlpha,
    Dxt5
}

internal sealed record VtfCandidateSpec(
    int ScaleDivisor,
    int Width,
    int Height,
    VtfTargetFormat Format,
    string Encoder,
    bool PreserveAlpha,
    bool IsNormalMap,
    bool PreserveCutoutCoverage);
