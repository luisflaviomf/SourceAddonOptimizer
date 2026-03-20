using System;
using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Merge
{
    internal sealed class AddonMergeRunOptions
    {
        internal string WorkerExePath { get; init; } = string.Empty;
        internal string RootPath { get; init; } = string.Empty;
        internal string WorkDir { get; init; } = string.Empty;
        internal string? GmadExePath { get; init; }
        internal string ConflictPolicy { get; init; } = "first";
        internal string PriorityMode { get; init; } = "name-asc";
        internal string PackageMode { get; init; } = "strict";
        internal bool ScanOnly { get; init; }
        internal bool Recursive { get; init; }
        internal bool AllowContentOnly { get; init; }
        internal bool RespectIgnore { get; init; } = true;
        internal string? TitleOverride { get; init; }
        internal string? AddonTypeOverride { get; init; }
        internal string? BundleName { get; init; }
        internal string? OutputRoot { get; init; }
        internal string? ReuseScanReportPath { get; init; }
        internal int HashWorkers { get; init; }
    }

    internal sealed class AddonMergeProgressUpdate
    {
        internal string? Phase { get; init; }
        internal string? ProgressKind { get; init; }
        internal int? StepIndex { get; init; }
        internal int? StepTotal { get; init; }
        internal long? ItemIndex { get; init; }
        internal long? ItemTotal { get; init; }
        internal string? CurrentPath { get; init; }
        internal string? SummaryPath { get; init; }
        internal string? WorkDir { get; init; }
        internal string? MergedRoot { get; init; }
        internal string? GmaPath { get; init; }
    }

    internal sealed class AddonMergeRunner
    {
        private static readonly Regex StepRegex = new(@"^== Step (\d+)/(\d+): (.+) ==$", RegexOptions.Compiled);
        private static readonly Regex ItemRegex = new(@"^=== \((\d+)/(\d+)\) ADDON: (.+)$", RegexOptions.Compiled);
        private static readonly Regex ProgressRegex = new(@"^ADDON_MERGE_PROGRESS:\s+([^|]+)\|(\d+)\|(\d+)\|(.*)$", RegexOptions.Compiled);
        private static readonly Regex SummaryRegex = new(@"^ADDON_MERGE_SUMMARY:\s+(.+)$", RegexOptions.Compiled);
        private static readonly Regex WorkDirRegex = new(@"^ADDON_MERGE_WORK_DIR:\s+(.+)$", RegexOptions.Compiled);
        private static readonly Regex MergedRootRegex = new(@"^ADDON_MERGED_ROOT:\s+(.+)$", RegexOptions.Compiled);
        private static readonly Regex GmaRegex = new(@"^ADDON_MERGED_GMA:\s+(.+)$", RegexOptions.Compiled);

        internal event Action<string>? LogLine;
        internal event Action<string>? ErrorLine;
        internal event Action<AddonMergeProgressUpdate>? ProgressUpdate;
        internal event Action<string>? SummaryPathFound;
        internal event Action<string>? WorkDirFound;
        internal event Action<string>? MergedRootFound;
        internal event Action<string>? GmaPathFound;

        internal async Task<int> RunAsync(AddonMergeRunOptions options, CancellationToken cancellationToken)
        {
            if (string.IsNullOrWhiteSpace(options.WorkerExePath))
                throw new ArgumentException("Worker exe path is required.", nameof(options.WorkerExePath));
            if (!File.Exists(options.WorkerExePath))
                throw new FileNotFoundException("Worker exe not found.", options.WorkerExePath);
            if (string.IsNullOrWhiteSpace(options.RootPath))
                throw new ArgumentException("Root path is required.", nameof(options.RootPath));
            if (string.IsNullOrWhiteSpace(options.WorkDir))
                throw new ArgumentException("Work dir is required.", nameof(options.WorkDir));

            var startInfo = new ProcessStartInfo
            {
                FileName = options.WorkerExePath,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetDirectoryName(options.WorkerExePath) ?? string.Empty,
            };

            startInfo.ArgumentList.Add("addonmerge");
            startInfo.ArgumentList.Add(options.RootPath);
            startInfo.ArgumentList.Add("--work");
            startInfo.ArgumentList.Add(options.WorkDir);
            startInfo.ArgumentList.Add("--conflict-policy");
            startInfo.ArgumentList.Add(string.IsNullOrWhiteSpace(options.ConflictPolicy) ? "first" : options.ConflictPolicy);
            startInfo.ArgumentList.Add("--priority-mode");
            startInfo.ArgumentList.Add(string.IsNullOrWhiteSpace(options.PriorityMode) ? "name-asc" : options.PriorityMode);
            startInfo.ArgumentList.Add("--package-mode");
            startInfo.ArgumentList.Add(string.IsNullOrWhiteSpace(options.PackageMode) ? "strict" : options.PackageMode);

            if (options.ScanOnly)
                startInfo.ArgumentList.Add("--scan-only");
            if (options.Recursive)
                startInfo.ArgumentList.Add("--recursive");
            if (options.AllowContentOnly)
                startInfo.ArgumentList.Add("--allow-content-only");
            if (!options.RespectIgnore)
                startInfo.ArgumentList.Add("--no-respect-ignore");
            if (!string.IsNullOrWhiteSpace(options.GmadExePath))
            {
                startInfo.ArgumentList.Add("--gmad");
                startInfo.ArgumentList.Add(options.GmadExePath);
            }
            if (!string.IsNullOrWhiteSpace(options.TitleOverride))
            {
                startInfo.ArgumentList.Add("--title");
                startInfo.ArgumentList.Add(options.TitleOverride);
            }
            if (!string.IsNullOrWhiteSpace(options.AddonTypeOverride))
            {
                startInfo.ArgumentList.Add("--addon-type");
                startInfo.ArgumentList.Add(options.AddonTypeOverride);
            }
            if (!string.IsNullOrWhiteSpace(options.BundleName))
            {
                startInfo.ArgumentList.Add("--bundle-name");
                startInfo.ArgumentList.Add(options.BundleName);
            }
            if (!string.IsNullOrWhiteSpace(options.OutputRoot))
            {
                startInfo.ArgumentList.Add("--output-root");
                startInfo.ArgumentList.Add(options.OutputRoot);
            }
            if (!string.IsNullOrWhiteSpace(options.ReuseScanReportPath))
            {
                startInfo.ArgumentList.Add("--reuse-scan-report");
                startInfo.ArgumentList.Add(options.ReuseScanReportPath);
            }
            if (options.HashWorkers > 0)
            {
                startInfo.ArgumentList.Add("--hash-workers");
                startInfo.ArgumentList.Add(options.HashWorkers.ToString());
            }

            using var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
            process.OutputDataReceived += (_, args) =>
            {
                if (args.Data != null)
                    HandleLine(args.Data, false);
            };
            process.ErrorDataReceived += (_, args) =>
            {
                if (args.Data != null)
                    HandleLine(args.Data, true);
            };

            if (!process.Start())
                throw new InvalidOperationException("Failed to start addon merge worker.");

            process.BeginOutputReadLine();
            process.BeginErrorReadLine();

            try
            {
                await process.WaitForExitAsync(cancellationToken);
            }
            catch (OperationCanceledException)
            {
                if (!process.HasExited)
                    process.Kill(true);
                throw;
            }

            return process.ExitCode;
        }

        private void HandleLine(string line, bool isError)
        {
            var progressMatch = ProgressRegex.Match(line);
            if (progressMatch.Success)
            {
                ProgressUpdate?.Invoke(
                    new AddonMergeProgressUpdate
                    {
                        ProgressKind = progressMatch.Groups[1].Value.Trim(),
                        ItemIndex = long.Parse(progressMatch.Groups[2].Value),
                        ItemTotal = long.Parse(progressMatch.Groups[3].Value),
                        CurrentPath = progressMatch.Groups[4].Value.Trim(),
                    });
                return;
            }

            LogLine?.Invoke(line);
            if (isError)
                ErrorLine?.Invoke(line);

            var stepMatch = StepRegex.Match(line);
            if (stepMatch.Success)
            {
                ProgressUpdate?.Invoke(
                    new AddonMergeProgressUpdate
                    {
                        StepIndex = int.Parse(stepMatch.Groups[1].Value),
                        StepTotal = int.Parse(stepMatch.Groups[2].Value),
                        Phase = stepMatch.Groups[3].Value.Trim(),
                    });
                return;
            }

            var itemMatch = ItemRegex.Match(line);
            if (itemMatch.Success)
            {
                ProgressUpdate?.Invoke(
                    new AddonMergeProgressUpdate
                    {
                        ItemIndex = long.Parse(itemMatch.Groups[1].Value),
                        ItemTotal = long.Parse(itemMatch.Groups[2].Value),
                        CurrentPath = itemMatch.Groups[3].Value.Trim(),
                    });
                return;
            }

            var summaryMatch = SummaryRegex.Match(line);
            if (summaryMatch.Success)
            {
                string summaryPath = summaryMatch.Groups[1].Value.Trim();
                SummaryPathFound?.Invoke(summaryPath);
                ProgressUpdate?.Invoke(new AddonMergeProgressUpdate { SummaryPath = summaryPath });
                return;
            }

            var workDirMatch = WorkDirRegex.Match(line);
            if (workDirMatch.Success)
            {
                string workDir = workDirMatch.Groups[1].Value.Trim();
                WorkDirFound?.Invoke(workDir);
                ProgressUpdate?.Invoke(new AddonMergeProgressUpdate { WorkDir = workDir });
                return;
            }

            var mergedRootMatch = MergedRootRegex.Match(line);
            if (mergedRootMatch.Success)
            {
                string mergedRoot = mergedRootMatch.Groups[1].Value.Trim();
                MergedRootFound?.Invoke(mergedRoot);
                ProgressUpdate?.Invoke(new AddonMergeProgressUpdate { MergedRoot = mergedRoot });
                return;
            }

            var gmaMatch = GmaRegex.Match(line);
            if (gmaMatch.Success)
            {
                string gmaPath = gmaMatch.Groups[1].Value.Trim();
                GmaPathFound?.Invoke(gmaPath);
                ProgressUpdate?.Invoke(new AddonMergeProgressUpdate { GmaPath = gmaPath });
            }
        }
    }
}
