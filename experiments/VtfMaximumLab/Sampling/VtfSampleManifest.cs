namespace VtfMaximumLab.Sampling;

internal sealed record VtfSampleItem(
    string RelativePath,
    long SizeBytes,
    string SourceSha256,
    IReadOnlyList<string> RelatedVmtPaths,
    IReadOnlySet<string> Tags);

internal sealed record VtfSampleManifest(
    string SourceRoot,
    string Seed,
    string SelectionSha256,
    IReadOnlyList<VtfSampleItem> Items);
