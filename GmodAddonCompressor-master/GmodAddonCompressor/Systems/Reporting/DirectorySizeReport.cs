using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Reporting
{
    internal sealed class DirectorySizeSnapshot
    {
        public DirectorySizeSnapshot(string rootPath, long totalBytes, Dictionary<string, long> topLevelBytes)
        {
            RootPath = rootPath;
            TotalBytes = totalBytes;
            TopLevelBytes = topLevelBytes;
        }

        public string RootPath { get; }
        public long TotalBytes { get; }
        public IReadOnlyDictionary<string, long> TopLevelBytes { get; }
    }

    internal static class DirectorySizeScanner
    {
        private const string RootBucketName = "(root)";

        internal static Task<DirectorySizeSnapshot?> ScanAsync(string rootPath, CancellationToken token)
        {
            return Task.Run(() => Scan(rootPath, token), token);
        }

        private static DirectorySizeSnapshot? Scan(string rootPath, CancellationToken token)
        {
            if (string.IsNullOrWhiteSpace(rootPath) || !Directory.Exists(rootPath))
                return null;

            var totalsByTop = new Dictionary<string, long>(StringComparer.OrdinalIgnoreCase);
            long totalBytes = 0;

            var stack = new Stack<(string Path, string TopLevel)>();
            stack.Push((rootPath, RootBucketName));

            while (stack.Count > 0)
            {
                token.ThrowIfCancellationRequested();

                var (currentPath, topLevel) = stack.Pop();
                DirectoryInfo currentDir;
                try
                {
                    currentDir = new DirectoryInfo(currentPath);
                }
                catch
                {
                    continue;
                }

                if (!string.Equals(currentPath, rootPath, StringComparison.OrdinalIgnoreCase) &&
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
                        long length = file.Length;
                        totalBytes += length;
                        if (!totalsByTop.TryGetValue(topLevel, out var bucketTotal))
                            bucketTotal = 0;
                        totalsByTop[topLevel] = bucketTotal + length;
                    }
                    catch
                    {
                        // Ignore individual file errors.
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

                    string nextTop = topLevel;
                    if (string.Equals(currentPath, rootPath, StringComparison.OrdinalIgnoreCase))
                        nextTop = directory.Name;

                    stack.Push((directory.FullName, nextTop));
                }
            }

            return new DirectorySizeSnapshot(rootPath, totalBytes, totalsByTop);
        }
    }

    internal static class DirectorySizeReportFormatter
    {
        private const string RootBucketName = "(root)";

        internal static string BuildReport(DirectorySizeSnapshot? before, DirectorySizeSnapshot? after)
        {
            if (before == null && after == null)
                return "Size report unavailable.";

            var sb = new StringBuilder();

            if (before != null && after != null)
            {
                sb.AppendLine($"Total: {FormatBytes(before.TotalBytes)} -> {FormatBytes(after.TotalBytes)} ({FormatDelta(before.TotalBytes, after.TotalBytes)})");
            }
            else if (before != null)
            {
                sb.AppendLine($"Total: {FormatBytes(before.TotalBytes)}");
            }
            else if (after != null)
            {
                sb.AppendLine($"Total: {FormatBytes(after.TotalBytes)}");
            }

            var beforeBuckets = before?.TopLevelBytes ?? new Dictionary<string, long>();
            var afterBuckets = after?.TopLevelBytes ?? new Dictionary<string, long>();

            var keys = new HashSet<string>(beforeBuckets.Keys, StringComparer.OrdinalIgnoreCase);
            foreach (var key in afterBuckets.Keys)
                keys.Add(key);

            var orderedKeys = keys
                .OrderByDescending(key =>
                {
                    long beforeValue = beforeBuckets.TryGetValue(key, out var b) ? b : 0;
                    long afterValue = afterBuckets.TryGetValue(key, out var a) ? a : 0;
                    return Math.Max(beforeValue, afterValue);
                })
                .ThenBy(key => key, StringComparer.OrdinalIgnoreCase);

            foreach (var key in orderedKeys)
            {
                long beforeValue = beforeBuckets.TryGetValue(key, out var b) ? b : 0;
                long afterValue = afterBuckets.TryGetValue(key, out var a) ? a : 0;
                if (beforeValue == 0 && afterValue == 0)
                    continue;

                string label = key;
                if (string.Equals(key, RootBucketName, StringComparison.OrdinalIgnoreCase))
                    label = "(root files)";
                else
                    label = $"{key}/";

                if (before != null && after != null)
                {
                    sb.AppendLine($"{label}: {FormatBytes(beforeValue)} -> {FormatBytes(afterValue)} ({FormatDelta(beforeValue, afterValue)})");
                }
                else if (before != null)
                {
                    sb.AppendLine($"{label}: {FormatBytes(beforeValue)}");
                }
                else
                {
                    sb.AppendLine($"{label}: {FormatBytes(afterValue)}");
                }
            }

            return sb.ToString().TrimEnd();
        }

        private static string FormatDelta(long beforeBytes, long afterBytes)
        {
            long delta = afterBytes - beforeBytes;
            string deltaText = FormatBytes(Math.Abs(delta));
            string sign = delta > 0 ? "+" : delta < 0 ? "-" : string.Empty;

            if (beforeBytes <= 0)
                return $"{sign}{deltaText}";

            double percent = (double)delta / beforeBytes * 100.0;
            string percentText = percent.ToString("+0.0;-0.0;0.0");
            return $"{sign}{deltaText} ({percentText}%)";
        }

        private static string FormatBytes(long bytes)
        {
            double value = bytes;
            string[] units = { "B", "KB", "MB", "GB", "TB" };
            int unitIndex = 0;

            while (value >= 1024 && unitIndex < units.Length - 1)
            {
                value /= 1024;
                unitIndex++;
            }

            return $"{value:0.##} {units[unitIndex]}";
        }
    }
}
