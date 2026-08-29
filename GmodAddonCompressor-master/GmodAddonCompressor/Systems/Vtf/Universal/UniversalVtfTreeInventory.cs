using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfTreeFile(
    string RelativePath,
    long Length,
    string Sha256,
    DateTime LastWriteTimeUtc);

internal sealed record UniversalVtfTreeSnapshot(
    string Root,
    IReadOnlyList<UniversalVtfTreeFile> Files,
    long TotalBytes,
    string TreeSha256);

internal sealed record UniversalVtfTreeAnchor(
    string Name,
    int FileCount,
    long TotalBytes,
    string TreeSha256);

internal static class UniversalVtfTreeInventory
{
    internal static UniversalVtfTreeSnapshot Capture(string root)
    {
        RequireText(root, nameof(root));

        string fullRoot = Path.GetFullPath(root);
        if (!Directory.Exists(fullRoot))
        {
            throw new DirectoryNotFoundException(fullRoot);
        }

        RejectReparsePoint(new DirectoryInfo(fullRoot), "root");

        var paths = new List<string>();
        var physicalFiles = new List<(string FullPath, string RelativePath)>();
        var pending = new Stack<string>();
        pending.Push(fullRoot);

        while (pending.Count > 0)
        {
            string directory = pending.Pop();
            foreach (string entryPath in Directory.EnumerateFileSystemEntries(
                         directory,
                         "*",
                         SearchOption.TopDirectoryOnly))
            {
                var entry = new FileInfo(entryPath);
                FileAttributes attributes = entry.Attributes;
                if ((attributes & FileAttributes.ReparsePoint) != 0)
                {
                    throw new InvalidDataException($"reparse_point:{entryPath}");
                }

                string relativePath = NormalizeRelativePath(
                    Path.GetRelativePath(fullRoot, entryPath));

                if ((attributes & FileAttributes.Directory) != 0)
                {
                    pending.Push(entryPath);
                    continue;
                }

                paths.Add(relativePath);
                physicalFiles.Add((entryPath, relativePath));
            }
        }

        ValidateRelativePaths(paths);

        var files = new List<UniversalVtfTreeFile>(physicalFiles.Count);
        foreach ((string fullPath, string relativePath) in physicalFiles
                     .OrderBy(
                         x => x.RelativePath.ToLowerInvariant(),
                         StringComparer.Ordinal)
                     .ThenBy(x => x.RelativePath, StringComparer.Ordinal))
        {
            files.Add(CaptureFile(fullPath, relativePath));
        }

        long totalBytes = checked(files.Sum(x => x.Length));
        string treeSha256 = ComputeTreeSha256(files);
        return new UniversalVtfTreeSnapshot(
            fullRoot,
            files.AsReadOnly(),
            totalBytes,
            treeSha256);
    }

    internal static void RequireAnchor(
        UniversalVtfTreeSnapshot snapshot,
        UniversalVtfTreeAnchor expected)
    {
        ArgumentNullException.ThrowIfNull(snapshot);
        ArgumentNullException.ThrowIfNull(expected);

        if (snapshot.Files.Count != expected.FileCount)
        {
            throw new InvalidDataException(
                $"anchor:{expected.Name}:file_count:{snapshot.Files.Count}!={expected.FileCount}");
        }

        if (snapshot.TotalBytes != expected.TotalBytes)
        {
            throw new InvalidDataException(
                $"anchor:{expected.Name}:total_bytes:{snapshot.TotalBytes}!={expected.TotalBytes}");
        }

        if (!snapshot.TreeSha256.Equals(expected.TreeSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException(
                $"anchor:{expected.Name}:tree_sha256:{snapshot.TreeSha256}!={expected.TreeSha256}");
        }
    }

    internal static string ResolveNewDestination(string parent, string name)
    {
        RequireText(parent, nameof(parent));
        RequireText(name, nameof(name));

        string fullParent = Path.GetFullPath(parent);
        if (!Directory.Exists(fullParent))
        {
            throw new DirectoryNotFoundException(fullParent);
        }

        RejectReparsePoint(new DirectoryInfo(fullParent), "destination_parent");

        if (Path.IsPathRooted(name) ||
            name is "." or ".." ||
            name.IndexOf(Path.DirectorySeparatorChar) >= 0 ||
            name.IndexOf(Path.AltDirectorySeparatorChar) >= 0)
        {
            throw new InvalidDataException($"invalid_destination_name:{name}");
        }

        string destination = Path.GetFullPath(Path.Combine(fullParent, name));
        string? actualParent = Path.GetDirectoryName(destination);
        if (!fullParent.Equals(actualParent, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"destination_escaped_parent:{name}");
        }

        bool collision = Directory.EnumerateFileSystemEntries(
                fullParent,
                "*",
                SearchOption.TopDirectoryOnly)
            .Any(x => Path.GetFileName(x).Equals(name, StringComparison.OrdinalIgnoreCase));
        if (collision)
        {
            throw new IOException($"destination_exists:{destination}");
        }

        return destination;
    }

    internal static void ValidateRelativePaths(IEnumerable<string> relativePaths)
    {
        ArgumentNullException.ThrowIfNull(relativePaths);

        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (string relativePath in relativePaths)
        {
            string normalized = NormalizeRelativePath(relativePath);
            if (!seen.Add(normalized))
            {
                throw new InvalidDataException($"casefold_path_collision:{normalized}");
            }
        }
    }

    private static UniversalVtfTreeFile CaptureFile(string fullPath, string relativePath)
    {
        var before = new FileInfo(fullPath);
        long lengthBefore = before.Length;
        DateTime writeBefore = before.LastWriteTimeUtc;

        byte[] digest;
        using (var stream = new FileStream(
                   fullPath,
                   FileMode.Open,
                   FileAccess.Read,
                   FileShare.Read,
                   bufferSize: 1024 * 1024,
                   FileOptions.SequentialScan))
        {
            using SHA256 sha256 = SHA256.Create();
            digest = sha256.ComputeHash(stream);
            if (stream.Length != lengthBefore)
            {
                throw new InvalidDataException($"file_changed_during_capture:{relativePath}:length");
            }
        }

        before.Refresh();
        if (!before.Exists || before.Length != lengthBefore || before.LastWriteTimeUtc != writeBefore)
        {
            throw new InvalidDataException($"file_changed_during_capture:{relativePath}:metadata");
        }

        return new UniversalVtfTreeFile(
            relativePath,
            lengthBefore,
            Convert.ToHexString(digest).ToLowerInvariant(),
            writeBefore);
    }

    private static string ComputeTreeSha256(IReadOnlyList<UniversalVtfTreeFile> files)
    {
        using IncrementalHash tree = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        Span<byte> scalar = stackalloc byte[8];

        foreach (UniversalVtfTreeFile file in files)
        {
            byte[] pathBytes = Encoding.UTF8.GetBytes(file.RelativePath);
            BinaryPrimitives.WriteUInt32LittleEndian(
                scalar,
                checked((uint)pathBytes.Length));
            tree.AppendData(scalar[..4]);
            tree.AppendData(pathBytes);

            BinaryPrimitives.WriteUInt64LittleEndian(
                scalar,
                checked((ulong)file.Length));
            tree.AppendData(scalar);
            tree.AppendData(Convert.FromHexString(file.Sha256));
        }

        return Convert.ToHexString(tree.GetHashAndReset()).ToLowerInvariant();
    }

    private static string NormalizeRelativePath(string relativePath)
    {
        RequireText(relativePath, nameof(relativePath));
        if (Path.IsPathRooted(relativePath))
        {
            throw new InvalidDataException($"rooted_relative_path:{relativePath}");
        }

        string normalized = relativePath.Replace('\\', '/');
        string[] segments = normalized.Split('/');
        if (segments.Any(segment => string.IsNullOrEmpty(segment) || segment is "." or ".."))
        {
            throw new InvalidDataException($"relative_path_traversal:{relativePath}");
        }

        return string.Join('/', segments);
    }

    private static void RejectReparsePoint(FileSystemInfo entry, string context)
    {
        if ((entry.Attributes & FileAttributes.ReparsePoint) != 0)
        {
            throw new InvalidDataException($"reparse_point:{context}:{entry.FullName}");
        }
    }

    private static void RequireText(string? value, string parameterName)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new ArgumentException("Value cannot be null or whitespace.", parameterName);
        }
    }
}
