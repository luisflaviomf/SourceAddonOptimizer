using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text.Json;

namespace GmodAddonCompressor.Systems.Tools
{
    internal static class ToolExtractionSystem
    {
        internal static void EnsureSourceAddonOptimizerExtracted()
        {
            Directory.CreateDirectory(ToolPaths.ToolRoot);
            Directory.CreateDirectory(ToolPaths.WorkRoot);

            using var lockStream = AcquireLock(ToolPaths.ExtractLockPath);
            if (IsExtracted())
                return;

            RecreateToolRoot();
            ExtractZip(ToolPaths.ZipPath, ToolPaths.ToolRoot);
            WriteManifest(ToolPaths.ToolRoot);

            if (!File.Exists(ToolPaths.WorkerExePath))
                throw new FileNotFoundException("Worker exe not found after extraction.", ToolPaths.WorkerExePath);
            if (!File.Exists(ToolPaths.CrowbarExePath))
                throw new FileNotFoundException("Crowbar exe not found after extraction.", ToolPaths.CrowbarExePath);
        }

        private static FileStream AcquireLock(string lockPath)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(lockPath) ?? ".");
            return new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        }

        private static bool IsExtracted()
        {
            if (!File.Exists(ToolPaths.WorkerExePath))
                return false;
            if (!File.Exists(ToolPaths.ManifestPath))
                return false;
            return true;
        }

        private static void RecreateToolRoot()
        {
            if (Directory.Exists(ToolPaths.ToolRoot))
                Directory.Delete(ToolPaths.ToolRoot, true);
            Directory.CreateDirectory(ToolPaths.ToolRoot);
        }

        private static void ExtractZip(string zipPath, string destination)
        {
            if (!File.Exists(zipPath))
                throw new FileNotFoundException(
                    $"Tool zip not found: {zipPath}. Ensure SourceAddonOptimizer.win-x64.zip is present.",
                    zipPath
                );

            ZipFile.ExtractToDirectory(zipPath, destination, true);
        }

        private static void WriteManifest(string toolRoot)
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
                toolName = ToolPaths.ToolName,
                toolVersion = ToolPaths.ToolVersion,
                extractedAt = DateTimeOffset.UtcNow.ToString("o"),
                files
            };

            var json = JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(ToolPaths.ManifestPath, json);
        }
    }
}
