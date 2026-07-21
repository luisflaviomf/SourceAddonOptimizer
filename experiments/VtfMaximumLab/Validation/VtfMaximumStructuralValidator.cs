using System.Security.Cryptography;
using System.Text.Json;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;

namespace VtfMaximumLab.Validation;

internal sealed record VtfStructuralValidationItem(
    string RelativePath,
    bool Valid,
    IReadOnlyList<string> Errors,
    string OriginalVersion,
    string MaximumVersion,
    int OriginalWidth,
    int OriginalHeight,
    int MaximumWidth,
    int MaximumHeight,
    int OriginalMipCount,
    int MaximumMipCount,
    long OriginalBytes,
    long MaximumBytes);

internal sealed record VtfStructuralValidationReport(
    int TextureCount,
    int ValidCount,
    int InvalidCount,
    bool SourceAddonUnchanged,
    bool RelatedFilesUnchanged,
    IReadOnlyList<string> GlobalErrors,
    IReadOnlyList<VtfStructuralValidationItem> Textures);

internal static class VtfMaximumStructuralValidator
{
    private const int AlphaFlags = 0x00001000 | 0x00002000;
    private sealed record ManifestItem(string RelativePath, string SourceSha256);
    private sealed record ManifestFile(string SourceRoot, IReadOnlyList<ManifestItem> Items);

    internal static VtfStructuralValidationReport Validate(string experimentRoot)
    {
        string root = Path.GetFullPath(experimentRoot);
        string originalTree = Path.Combine(root, "original");
        string maximumTree = Path.Combine(root, "maximum");
        string manifestPath = Path.Combine(root, "sample-manifest.json");
        ManifestFile manifest = JsonSerializer.Deserialize<ManifestFile>(File.ReadAllText(manifestPath)) ??
                                throw new InvalidDataException("Sample manifest is invalid.");
        var globalErrors = new List<string>();
        string[] originalVtfs = Directory.EnumerateFiles(originalTree, "*.vtf", SearchOption.AllDirectories).ToArray();
        string[] maximumVtfs = Directory.EnumerateFiles(maximumTree, "*.vtf", SearchOption.AllDirectories).ToArray();
        if (originalVtfs.Length != 50 || maximumVtfs.Length != 50)
            globalErrors.Add($"tree_count:{originalVtfs.Length}:{maximumVtfs.Length}");

        bool sourceUnchanged = true;
        var items = new List<VtfStructuralValidationItem>();
        foreach (ManifestItem manifestItem in manifest.Items.OrderBy(item => item.RelativePath, StringComparer.OrdinalIgnoreCase))
        {
            string relative = manifestItem.RelativePath.Replace('/', Path.DirectorySeparatorChar);
            string originalPath = Path.Combine(originalTree, relative);
            string maximumPath = Path.Combine(maximumTree, relative);
            string sourcePath = Path.Combine(manifest.SourceRoot, relative);
            if (!File.Exists(sourcePath) || !Hash(sourcePath).Equals(manifestItem.SourceSha256, StringComparison.OrdinalIgnoreCase))
                sourceUnchanged = false;

            var errors = new List<string>();
            if (!VtfDocumentReader.TryRead(originalPath, out VtfDocumentInfo original, out string originalError))
                throw new InvalidDataException($"Original sample VTF became invalid: {originalError}");
            if (!VtfDocumentReader.TryRead(maximumPath, out VtfDocumentInfo maximum, out string maximumError))
            {
                errors.Add($"parse:{maximumError}");
                maximum = original;
            }
            else
            {
                errors.AddRange(ValidatePair(original, maximum));
            }
            if (new FileInfo(maximumPath).Length > new FileInfo(originalPath).Length)
                errors.Add("larger_than_original");

            items.Add(new VtfStructuralValidationItem(
                manifestItem.RelativePath,
                errors.Count == 0,
                errors,
                $"{original.MajorVersion}.{original.MinorVersion}",
                $"{maximum.MajorVersion}.{maximum.MinorVersion}",
                original.Width,
                original.Height,
                maximum.Width,
                maximum.Height,
                original.MipCount,
                maximum.MipCount,
                new FileInfo(originalPath).Length,
                new FileInfo(maximumPath).Length));
        }

        bool relatedFilesUnchanged = CompareNonVtfFiles(originalTree, maximumTree);
        if (!sourceUnchanged)
            globalErrors.Add("source_addon_changed");
        if (!relatedFilesUnchanged)
            globalErrors.Add("related_files_changed");
        return new VtfStructuralValidationReport(
            items.Count,
            items.Count(item => item.Valid),
            items.Count(item => !item.Valid),
            sourceUnchanged,
            relatedFilesUnchanged,
            globalErrors,
            items);
    }

    internal static IReadOnlyList<string> ValidatePair(VtfDocumentInfo original, VtfDocumentInfo maximum)
    {
        var errors = new List<string>();
        if (maximum.MajorVersion != original.MajorVersion || maximum.MinorVersion != original.MinorVersion)
            errors.Add("version");
        if (maximum.Frames != original.Frames)
            errors.Add("frames");
        if (maximum.Faces != original.Faces)
            errors.Add("faces");
        if (maximum.Depth != original.Depth)
            errors.Add("depth");
        if ((maximum.Flags & ~AlphaFlags) != (original.Flags & ~AlphaFlags))
            errors.Add("flags");
        if (!IsAllowedScale(original.Width, maximum.Width) || !IsAllowedScale(original.Height, maximum.Height))
            errors.Add("dimensions");
        if (original.MipCount == 1 ? maximum.MipCount != 1 : maximum.MipCount != FullMipCount(maximum.Width, maximum.Height))
            errors.Add("mip_count");
        if (maximum.HighResFormat is not (13 or 15 or 20))
            errors.Add("format");
        if (maximum.LowResFormat != original.LowResFormat ||
            maximum.LowResWidth != original.LowResWidth ||
            maximum.LowResHeight != original.LowResHeight)
            errors.Add("thumbnail");
        if (!maximum.Resources.Select(resource => (resource.Tag, resource.Flags))
                .SequenceEqual(original.Resources.Select(resource => (resource.Tag, resource.Flags))))
            errors.Add("resources");
        return errors;
    }

    private static bool IsAllowedScale(int original, int candidate) =>
        candidate == original || candidate * 2 == original || candidate * 4 == original;

    private static int FullMipCount(int width, int height)
    {
        int count = 1;
        while (width > 1 || height > 1)
        {
            width = Math.Max(1, width / 2);
            height = Math.Max(1, height / 2);
            count++;
        }
        return count;
    }

    private static bool CompareNonVtfFiles(string originalRoot, string maximumRoot)
    {
        Dictionary<string, string> original = Directory.EnumerateFiles(originalRoot, "*", SearchOption.AllDirectories)
            .Where(path => !path.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
            .ToDictionary(path => Path.GetRelativePath(originalRoot, path), Hash, StringComparer.OrdinalIgnoreCase);
        Dictionary<string, string> maximum = Directory.EnumerateFiles(maximumRoot, "*", SearchOption.AllDirectories)
            .Where(path => !path.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
            .ToDictionary(path => Path.GetRelativePath(maximumRoot, path), Hash, StringComparer.OrdinalIgnoreCase);
        return original.Count == maximum.Count && original.All(pair =>
            maximum.TryGetValue(pair.Key, out string? hash) && hash == pair.Value);
    }

    private static string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
}
