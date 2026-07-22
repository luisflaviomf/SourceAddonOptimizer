using System;
using System.IO;
using System.Threading;

namespace GmodAddonCompressor.Systems.Tools
{
        internal static class ToolPaths
        {
        internal const string ToolName = "SourceAddonOptimizer";
        internal const string ToolVersion = "0.1.18";
        private static string? _activeToolRoot;

        internal static string AppDataRoot =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GmodAddonOptimizer");

        internal static string ToolsRoot => Path.Combine(AppDataRoot, "tools");

        internal static string VersionRoot => Path.Combine(ToolsRoot, ToolName, ToolVersion);

        internal static string ToolRoot => Volatile.Read(ref _activeToolRoot) ?? VersionRoot;

        internal static string WorkRoot => Path.Combine(AppDataRoot, "work");

        internal static string WorkerExePath => Path.Combine(ToolRoot, "SourceAddonOptimizerWorker.exe");

        internal static string CrowbarExePath => Path.Combine(ToolRoot, "CrowbarCommandLineDecomp.exe");

        internal static string ManifestPath => Path.Combine(
            ToolRoot,
            SourceAddonOptimizerPackageManifest.RelativePath.Replace('/', Path.DirectorySeparatorChar)
        );

        internal static string ExtractLockPath => Path.Combine(ToolsRoot, ToolName, "extract.lock");

        internal static void ActivateToolRoot(string toolRoot)
        {
            string fullRoot = Path.GetFullPath(toolRoot);
            string allowedRoot = Path.GetFullPath(VersionRoot).TrimEnd(Path.DirectorySeparatorChar)
                + Path.DirectorySeparatorChar;
            if (!fullRoot.StartsWith(allowedRoot, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException($"Optimizer tool root is outside the versioned tools directory: {fullRoot}");
            Interlocked.Exchange(ref _activeToolRoot, fullRoot);
        }

        internal static string GetWorkDir(string addonPath, string suffix)
        {
            string name = Path.GetFileName(addonPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
            if (string.IsNullOrWhiteSpace(suffix))
                suffix = "_optimized";
            return Path.Combine(WorkRoot, $"{name}{suffix}");
        }
    }
}
