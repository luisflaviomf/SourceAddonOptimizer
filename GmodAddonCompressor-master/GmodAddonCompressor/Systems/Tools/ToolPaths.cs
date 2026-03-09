using System;
using System.IO;

namespace GmodAddonCompressor.Systems.Tools
{
        internal static class ToolPaths
        {
            internal const string ToolName = "SourceAddonOptimizer";
        internal const string ToolVersion = "0.1.14";

        internal static string AppDataRoot =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GmodAddonOptimizer");

        internal static string ToolsRoot => Path.Combine(AppDataRoot, "tools");

        internal static string ToolRoot => Path.Combine(ToolsRoot, ToolName, ToolVersion);

        internal static string WorkRoot => Path.Combine(AppDataRoot, "work");

        internal static string WorkerExePath => Path.Combine(ToolRoot, "SourceAddonOptimizerWorker.exe");

        internal static string CrowbarExePath => Path.Combine(ToolRoot, "CrowbarCommandLineDecomp.exe");

        internal static string ManifestPath => Path.Combine(ToolRoot, "manifest.json");

        internal static string ExtractLockPath => Path.Combine(ToolsRoot, ToolName, "extract.lock");

        internal static string GetWorkDir(string addonPath, string suffix)
        {
            string name = Path.GetFileName(addonPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
            if (string.IsNullOrWhiteSpace(suffix))
                suffix = "_optimized";
            return Path.Combine(WorkRoot, $"{name}{suffix}");
        }
    }
}
