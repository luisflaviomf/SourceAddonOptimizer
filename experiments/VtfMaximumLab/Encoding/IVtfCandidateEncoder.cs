using VtfMaximumLab.Candidates;

namespace VtfMaximumLab.Encoding;

internal sealed record DdsEncodingRequest(
    string InputPngPath,
    string OutputDdsPath,
    VtfTargetFormat Format,
    int MipCount,
    double AlphaThreshold);

internal interface IVtfCandidateEncoder
{
    string Name { get; }
    Task<DdsBcDocument> EncodeAsync(DdsEncodingRequest request, CancellationToken cancellationToken);
}
