using VtfMaximumLab.Metrics;

namespace VtfMaximumLab.Reporting;

internal sealed record VtfCandidateRunRecord(
    string Encoder,
    int ScaleDivisor,
    int Width,
    int Height,
    string Format,
    long SizeBytes,
    double DurationMilliseconds,
    bool StructurallyValid,
    bool Accepted,
    IReadOnlyList<string> RejectionReasons,
    VtfQualityMetrics? Metrics,
    string Error);

internal sealed record VtfTextureExperimentRecord(
    string RelativePath,
    long OriginalBytes,
    long CurrentBytes,
    long MaximumBytes,
    string Decision,
    string WinnerEncoder,
    int WinnerScaleDivisor,
    VtfQualityMetrics CurrentMetrics,
    VtfQualityMetrics? MaximumMetrics,
    double DurationMilliseconds,
    IReadOnlySet<string> Tags,
    IReadOnlyList<VtfCandidateRunRecord> Candidates);

internal sealed record VtfMaximumExperimentReport(
    string Root,
    DateTimeOffset StartedUtc,
    double DurationMilliseconds,
    long OriginalVtfBytes,
    long CurrentVtfBytes,
    long MaximumVtfBytes,
    long OriginalMaterialsBytes,
    long CurrentMaterialsBytes,
    long MaximumMaterialsBytes,
    int PreservedTextures,
    int ReducedTextures,
    int TexturesWithoutAcceptedCandidate,
    int AcceptedCandidates,
    int RejectedCandidates,
    IReadOnlyList<VtfTextureExperimentRecord> Textures);
