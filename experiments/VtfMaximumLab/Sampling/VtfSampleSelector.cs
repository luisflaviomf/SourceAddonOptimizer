using System.Security.Cryptography;
using System.Text;
using VtfMaximumLab.Inventory;

namespace VtfMaximumLab.Sampling;

internal static class VtfSampleSelector
{
    internal const string DefaultSeed = "compress-maximum-vtf-v1";

    private static readonly string[] CoverageTags =
    {
        "semantic:normal", "alpha:cutout", "alpha:gradual", "semantic:glass",
        "semantic:phong-mask", "semantic:envmap-mask", "semantic:emissive",
        "semantic:phong", "semantic:normal-alpha-mask", "semantic:decal", "semantic:effect", "semantic:translucent",
        "size:large", "size:small", "format:DXT1", "format:DXT5",
        "mipmaps:none", "mipmaps:present", "resource:thumbnail",
        "structure:animated", "structure:cubemap", "structure:volume"
    };

    internal static VtfSampleManifest Select(
        IEnumerable<VtfInventoryEntry> sourceEntries,
        int count,
        string seed,
        string sourceRoot)
    {
        VtfInventoryEntry[] entries = sourceEntries
            .OrderBy(entry => entry.RelativePath, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        if (count <= 0 || entries.Length < count)
            throw new ArgumentOutOfRangeException(nameof(count), $"Requested {count} items from an inventory of {entries.Length}.");

        var stableKeys = entries.ToDictionary(
            entry => entry,
            entry => Sha256Text(seed + "\n" + entry.RelativePath.ToLowerInvariant()));
        var availableCoverage = CoverageTags
            .Where(tag => entries.Any(entry => entry.Tags.Contains(tag)))
            .ToHashSet(StringComparer.Ordinal);
        var uncovered = new HashSet<string>(availableCoverage, StringComparer.Ordinal);
        var selected = new List<VtfInventoryEntry>(count);

        while (uncovered.Count > 0 && selected.Count < count)
        {
            VtfInventoryEntry? winner = entries
                .Where(entry => !selected.Contains(entry))
                .Select(entry => new
                {
                    Entry = entry,
                    Coverage = entry.Tags.Count(uncovered.Contains),
                    StableKey = stableKeys[entry]
                })
                .Where(candidate => candidate.Coverage > 0)
                .OrderByDescending(candidate => candidate.Coverage)
                .ThenBy(candidate => candidate.StableKey, StringComparer.Ordinal)
                .Select(candidate => candidate.Entry)
                .FirstOrDefault();

            if (winner == null)
                break;

            selected.Add(winner);
            uncovered.ExceptWith(winner.Tags);
        }

        selected.AddRange(entries
            .Where(entry => !selected.Contains(entry))
            .OrderBy(entry => stableKeys[entry], StringComparer.Ordinal)
            .Take(count - selected.Count));

        var items = selected.Select(entry => new VtfSampleItem(
                entry.RelativePath,
                entry.SizeBytes,
                File.Exists(entry.AbsolutePath) ? Sha256File(entry.AbsolutePath) : string.Empty,
                entry.RelatedVmtPaths,
                entry.Tags))
            .ToArray();
        string selectionHash = Sha256Text(seed + "\n" + string.Join("\n", items.Select(item => item.RelativePath)));
        return new VtfSampleManifest(Path.GetFullPath(sourceRoot), seed, selectionHash, items);
    }

    internal static string Sha256File(string path)
    {
        using FileStream stream = File.OpenRead(path);
        using SHA256 sha256 = SHA256.Create();
        return Convert.ToHexString(sha256.ComputeHash(stream)).ToLowerInvariant();
    }

    private static string Sha256Text(string value) =>
        Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(value))).ToLowerInvariant();
}
