using GmodAddonCompressor.Properties;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text.Json;

namespace GmodAddonCompressor.Systems.Tools
{
    internal static class ToolExtractionSystem
    {
        internal static void EnsureSourceAddonOptimizerExtracted()
        {
            Directory.CreateDirectory(ToolPaths.ToolRoot);
            Directory.CreateDirectory(ToolPaths.WorkRoot);

            EnsureExtracted(
                ToolPaths.ToolName,
                ToolPaths.ToolVersion,
                Resources.SourceAddonOptimizer_win_x64,
                new[]
                {
                    "SourceAddonOptimizerWorker.exe",
                    "CrowbarCommandLineDecomp.exe",
                    "_internal/base_library.zip",
                    "_internal/python311.dll"
                }
            );
        }

        internal static string EnsureExtracted(string toolName, string toolVersion, byte[] zipBytes, IReadOnlyCollection<string> expectedFiles)
        {
            string toolRoot = Path.Combine(ToolPaths.ToolsRoot, toolName, toolVersion);
            string lockPath = Path.Combine(ToolPaths.ToolsRoot, toolName, "extract.lock");

            Directory.CreateDirectory(ToolPaths.ToolsRoot);
            using var lockStream = AcquireLock(lockPath);

            if (IsExtracted(toolRoot, expectedFiles))
                return toolRoot;

            RecreateToolRoot(toolRoot);
            ExtractZipBytes(zipBytes, toolRoot);
            WriteManifest(toolName, toolVersion, toolRoot);

            if (expectedFiles != null && expectedFiles.Count > 0)
            {
                foreach (var expected in expectedFiles)
                {
                    string path = Path.Combine(toolRoot, expected);
                    if (!File.Exists(path))
                        throw new FileNotFoundException($"Expected tool file missing after extraction: {expected}", path);
                }
            }

            return toolRoot;
        }

        private static FileStream AcquireLock(string lockPath)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(lockPath) ?? ".");
            return new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        }

        private static bool IsExtracted(string toolRoot, IReadOnlyCollection<string> expectedFiles)
        {
            if (!File.Exists(Path.Combine(toolRoot, "manifest.json")))
                return false;

            if (expectedFiles == null || expectedFiles.Count == 0)
                return Directory.Exists(toolRoot);

            return expectedFiles.All(file => File.Exists(Path.Combine(toolRoot, file)));
        }

        private static void RecreateToolRoot(string toolRoot)
        {
            if (Directory.Exists(toolRoot))
                Directory.Delete(toolRoot, true);
            Directory.CreateDirectory(toolRoot);
        }

        private static void ExtractZipBytes(byte[] zipBytes, string destination)
        {
            using var memoryStream = new MemoryStream(zipBytes, writable: false);
            using var archive = new ZipArchive(memoryStream, ZipArchiveMode.Read, leaveOpen: false);
            string destinationRoot = Path.GetFullPath(destination);

            foreach (var entry in archive.Entries)
            {
                string normalized = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                string fullPath = Path.GetFullPath(Path.Combine(destinationRoot, normalized));
                if (!fullPath.StartsWith(destinationRoot, StringComparison.OrdinalIgnoreCase))
                    continue;

                if (string.IsNullOrEmpty(entry.Name))
                {
                    Directory.CreateDirectory(fullPath);
                    continue;
                }

                Directory.CreateDirectory(Path.GetDirectoryName(fullPath) ?? destinationRoot);
                entry.ExtractToFile(fullPath, true);
            }
        }

        private static void WriteManifest(string toolName, string toolVersion, string toolRoot)
        {
            var files = new List<object>();
            foreach (var file in Directory.GetFiles(toolRoot, "*", SearchOption.AllDirectories))
            {
                var info = new FileInfo(file);
                files.Add(new
                {
                    path = Path.GetRelativePath(toolRoot, file).Replace("\\", "/"),
                    size = info.Length
                });
            }

            var manifest = new
            {
                toolName,
                toolVersion,
                extractedAt = DateTimeOffset.UtcNow.ToString("o"),
                files
            };

            var json = JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(Path.Combine(toolRoot, "manifest.json"), json);
        }
    }
}
