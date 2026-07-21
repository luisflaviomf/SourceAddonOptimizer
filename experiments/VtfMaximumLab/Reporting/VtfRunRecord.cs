namespace VtfMaximumLab.Reporting;

internal sealed record VtfFileSnapshot(
    long SizeBytes,
    string Sha256,
    bool IsValid,
    string ValidationError,
    int MajorVersion,
    int MinorVersion,
    int Width,
    int Height,
    int Format,
    int MipCount,
    int Frames,
    int Faces,
    int Depth,
    int Flags);

internal sealed record VtfRunRecord(
    string RelativePath,
    VtfFileSnapshot Before,
    VtfFileSnapshot After,
    double CompletionElapsedMilliseconds,
    bool Preserved,
    bool Reduced);

internal sealed record CurrentCompressBaselineReport(
    string TreeRoot,
    DateTimeOffset StartedUtc,
    double DurationMilliseconds,
    long OriginalVtfBytes,
    long ResultVtfBytes,
    long OriginalMaterialsBytes,
    long ResultMaterialsBytes,
    int PreservedCount,
    int ReducedCount,
    int InvalidCount,
    IReadOnlyList<VtfRunRecord> Records);
