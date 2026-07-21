using GmodAddonCompressor.Models;

namespace VtfMaximumLab.Inventory;

internal enum VtfAlphaClass
{
    Unknown,
    Opaque,
    Cutout,
    Gradual
}

internal sealed record VtfInventoryEntry(
    string AbsolutePath,
    string RelativePath,
    long SizeBytes,
    int Width,
    int Height,
    int Format,
    int MajorVersion,
    int MinorVersion,
    int MipCount,
    int Frames,
    int Faces,
    int Depth,
    int Flags,
    VtfAlphaClass AlphaClass,
    VtfSemanticProfile SemanticProfile,
    IReadOnlyList<string> RelatedVmtPaths,
    IReadOnlySet<string> Tags);
