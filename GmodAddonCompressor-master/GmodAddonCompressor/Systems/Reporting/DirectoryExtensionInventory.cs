using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Reporting
{
    internal sealed class DirectoryExtensionInventoryEntry
    {
        public DirectoryExtensionInventoryEntry(string extension, int fileCount, long totalBytes)
        {
            Extension = extension;
            FileCount = fileCount;
            TotalBytes = totalBytes;
        }

        public string Extension { get; }
        public int FileCount { get; }
        public long TotalBytes { get; }
    }

    internal sealed class DirectoryExtensionInventorySnapshot
    {
        public DirectoryExtensionInventorySnapshot(IReadOnlyList<string> rootPaths, int totalFiles, long totalBytes, IReadOnlyList<DirectoryExtensionInventoryEntry> entries)
        {
            RootPaths = rootPaths;
            TotalFiles = totalFiles;
            TotalBytes = totalBytes;
            Entries = entries;
        }

        public IReadOnlyList<string> RootPaths { get; }
        public int TotalFiles { get; }
        public long TotalBytes { get; }
        public IReadOnlyList<DirectoryExtensionInventoryEntry> Entries { get; }
    }

    internal static class DirectoryExtensionInventoryScanner
    {
        internal static Task<DirectoryExtensionInventorySnapshot> ScanAsync(IEnumerable<string> rootPaths, CancellationToken token)
        {
            return Task.Run(() => Scan(rootPaths, token), token);
        }

        private static DirectoryExtensionInventorySnapshot Scan(IEnumerable<string> rootPaths, CancellationToken token)
        {
            var roots = rootPaths
                .Where(path => !string.IsNullOrWhiteSpace(path) && Directory.Exists(path))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();

            var totals = new Dictionary<string, (int FileCount, long TotalBytes)>(StringComparer.OrdinalIgnoreCase);
            int totalFiles = 0;
            long totalBytes = 0;

            foreach (var root in roots)
            {
                var stack = new Stack<string>();
                stack.Push(root);

                while (stack.Count > 0)
                {
                    token.ThrowIfCancellationRequested();

                    string currentPath = stack.Pop();
                    DirectoryInfo currentDir;
                    try
                    {
                        currentDir = new DirectoryInfo(currentPath);
                    }
                    catch
                    {
                        continue;
                    }

                    if (!string.Equals(currentPath, root, StringComparison.OrdinalIgnoreCase) &&
                        currentDir.Attributes.HasFlag(FileAttributes.ReparsePoint))
                    {
                        continue;
                    }

                    FileInfo[] files;
                    try
                    {
                        files = currentDir.GetFiles();
                    }
                    catch
                    {
                        files = Array.Empty<FileInfo>();
                    }

                    foreach (var file in files)
                    {
                        try
                        {
                            long fileBytes = file.Length;
                            totalFiles++;
                            totalBytes += fileBytes;

                            string extension = string.IsNullOrWhiteSpace(file.Extension)
                                ? "(no extension)"
                                : file.Extension.ToLowerInvariant();

                            if (!totals.TryGetValue(extension, out var current))
                                current = (0, 0);

                            totals[extension] = (current.FileCount + 1, current.TotalBytes + fileBytes);
                        }
                        catch
                        {
                        }
                    }

                    DirectoryInfo[] directories;
                    try
                    {
                        directories = currentDir.GetDirectories();
                    }
                    catch
                    {
                        directories = Array.Empty<DirectoryInfo>();
                    }

                    foreach (var directory in directories)
                    {
                        try
                        {
                            if (directory.Attributes.HasFlag(FileAttributes.ReparsePoint))
                                continue;
                        }
                        catch
                        {
                            continue;
                        }

                        stack.Push(directory.FullName);
                    }
                }
            }

            var entries = totals
                .Select(kvp => new DirectoryExtensionInventoryEntry(kvp.Key, kvp.Value.FileCount, kvp.Value.TotalBytes))
                .OrderByDescending(entry => entry.TotalBytes)
                .ThenBy(entry => entry.Extension, StringComparer.OrdinalIgnoreCase)
                .ToArray();

            return new DirectoryExtensionInventorySnapshot(roots, totalFiles, totalBytes, entries);
        }
    }
}
