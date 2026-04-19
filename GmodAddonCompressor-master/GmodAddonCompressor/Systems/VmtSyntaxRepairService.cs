using GmodAddonCompressor.CustomExtensions;
using Microsoft.Extensions.Logging;
using System;
using System.IO;
using System.Linq;

namespace GmodAddonCompressor.Systems
{
    internal static class VmtSyntaxRepairService
    {
        private const int MaxAutoRepairBraceDelta = 4;
        private static readonly ILogger _logger = LogSystem.LoggerFactory.CreateLogger("VmtSyntaxRepairService");

        internal static int RepairUnder(string rootPath)
        {
            if (string.IsNullOrWhiteSpace(rootPath) || !Directory.Exists(rootPath))
                return 0;

            int repairedCount = 0;

            foreach (string vmtPath in Directory.EnumerateFiles(rootPath, "*.vmt", SearchOption.AllDirectories))
            {
                try
                {
                    if (TryRepairFile(vmtPath, out int appendedClosures))
                    {
                        repairedCount++;
                        _logger.LogInformation(
                            $"[VMTREPAIR] Repaired {vmtPath.GAC_ToLocalPath()} by appending {appendedClosures} closing brace(s).");
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex.ToString());
                }
            }

            if (repairedCount > 0)
            {
                _logger.LogInformation(
                    $"[VMTREPAIR] Repaired {repairedCount} malformed VMT file(s) under {rootPath.GAC_ToLocalPath()}.");
            }

            return repairedCount;
        }

        private static bool TryRepairFile(string vmtPath, out int appendedClosures)
        {
            appendedClosures = 0;

            string content = File.ReadAllText(vmtPath);
            int openCount = content.Count(ch => ch == '{');
            int closeCount = content.Count(ch => ch == '}');
            int missingClosures = openCount - closeCount;

            if (missingClosures <= 0 || missingClosures > MaxAutoRepairBraceDelta)
                return false;

            string lineEnding = content.Contains("\r\n", StringComparison.Ordinal) ? "\r\n" : "\n";
            string repairedContent = content.TrimEnd();

            repairedContent += lineEnding;
            for (int i = 0; i < missingClosures; i++)
                repairedContent += "}" + lineEnding;

            File.WriteAllText(vmtPath, repairedContent);
            appendedClosures = missingClosures;
            return true;
        }
    }
}
