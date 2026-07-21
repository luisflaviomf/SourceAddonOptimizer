using System.Text.Json;

namespace VtfMaximumLab.Sampling;

internal static class VtfSampleTreeCopier
{
    private static readonly string[] TreeNames = { "original", "current-2x-8x8", "maximum" };

    internal static void Copy(VtfSampleManifest manifest, string outputRoot)
    {
        string sourceRoot = Path.GetFullPath(manifest.SourceRoot);
        string destinationRoot = Path.GetFullPath(outputRoot);
        if (IsInside(destinationRoot, sourceRoot) || string.Equals(destinationRoot, sourceRoot, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("The sample destination must not be inside the source addon.");
        if (string.Equals(Path.TrimEndingDirectorySeparator(destinationRoot), Path.GetPathRoot(destinationRoot), StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("A drive root cannot be used as the sample destination.");

        Directory.CreateDirectory(destinationRoot);
        foreach (string treeName in TreeNames)
        {
            string treeRoot = Path.Combine(destinationRoot, treeName);
            if (!IsInside(treeRoot, destinationRoot))
                throw new InvalidOperationException("Computed sample tree escaped the destination root.");
            if (Directory.Exists(treeRoot))
                Directory.Delete(treeRoot, recursive: true);
            Directory.CreateDirectory(treeRoot);
            foreach (VtfSampleItem item in manifest.Items)
            {
                CopyVerified(sourceRoot, treeRoot, item.RelativePath, item.SourceSha256);
                foreach (string vmtPath in item.RelatedVmtPaths)
                    CopyVerified(sourceRoot, treeRoot, vmtPath, expectedHash: null);
            }
        }

        var options = new JsonSerializerOptions { WriteIndented = true };
        File.WriteAllText(
            Path.Combine(destinationRoot, "sample-manifest.json"),
            JsonSerializer.Serialize(manifest, options));
    }

    private static void CopyVerified(string sourceRoot, string treeRoot, string relativePath, string? expectedHash)
    {
        string source = Path.GetFullPath(Path.Combine(sourceRoot, relativePath.Replace('/', Path.DirectorySeparatorChar)));
        if (!IsInside(source, sourceRoot) || !File.Exists(source))
            throw new FileNotFoundException($"Manifest source is missing or outside the addon: {relativePath}", source);
        if (!string.IsNullOrEmpty(expectedHash) &&
            !string.Equals(VtfSampleSelector.Sha256File(source), expectedHash, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"Source hash changed before copy: {relativePath}");
        }

        string destination = Path.Combine(treeRoot, relativePath.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        File.Copy(source, destination, overwrite: true);
        if (!string.IsNullOrEmpty(expectedHash) &&
            !string.Equals(VtfSampleSelector.Sha256File(destination), expectedHash, StringComparison.OrdinalIgnoreCase))
        {
            throw new IOException($"Copied file failed hash verification: {relativePath}");
        }
    }

    private static bool IsInside(string candidate, string parent)
    {
        string normalizedParent = Path.TrimEndingDirectorySeparator(Path.GetFullPath(parent)) + Path.DirectorySeparatorChar;
        string normalizedCandidate = Path.GetFullPath(candidate);
        return normalizedCandidate.StartsWith(normalizedParent, StringComparison.OrdinalIgnoreCase);
    }
}
