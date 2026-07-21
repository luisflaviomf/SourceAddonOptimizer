using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Optimizer
{
    internal sealed class SourceAddonOptimizerRunOptions
    {
        internal string WorkerExePath { get; init; } = string.Empty;
        internal string AddonPath { get; init; } = string.Empty;
        internal string WorkDir { get; init; } = string.Empty;
        internal string? BlenderPath { get; init; }
        internal string? StudioMdlPath { get; init; }
        internal string? Suffix { get; init; }
        internal string? OptimizerMode { get; init; }
        internal double? Ratio { get; init; }
        internal double? Merge { get; init; }
        internal double? AutoSmooth { get; init; }
        internal bool UsePlanar { get; init; }
        internal double? PlanarAngle { get; init; }
        internal bool ExperimentalGroundPolicy { get; init; }
        internal bool ExperimentalRoundPartsPolicy { get; init; }
        internal bool ExperimentalSteerTurnBasisFix { get; init; }
        internal string? Format { get; init; }
        internal int? Jobs { get; init; }
        internal int? DecompileJobs { get; init; }
        internal int? CompileJobs { get; init; }
        internal bool Strict { get; init; }
        internal bool ResumeOpt { get; init; }
        internal bool Overwrite { get; init; }
        internal bool OverwriteWork { get; init; }
        internal bool RestoreSkins { get; init; }
        internal bool CompileVerbose { get; init; }
        internal bool CleanupWorkModelArtifacts { get; init; }
        internal bool SingleAddonOnly { get; init; }
    }

    internal sealed class SourceAddonOptimizerRunner
    {
        private readonly SourceAddonOptimizerProgressParser _parser = new SourceAddonOptimizerProgressParser();

        internal event Action<string>? LogLine;
        internal event Action<string>? ErrorLine;
        internal event Action<SourceAddonOptimizerProgressUpdate>? ProgressUpdate;
        internal event Action<string>? OutputPathFound;

        internal async Task<int> RunAsync(SourceAddonOptimizerRunOptions options, CancellationToken cancellationToken)
        {
            if (string.IsNullOrWhiteSpace(options.WorkerExePath))
                throw new ArgumentException("Worker exe path is required.", nameof(options.WorkerExePath));
            if (!File.Exists(options.WorkerExePath))
                throw new FileNotFoundException("Worker exe not found.", options.WorkerExePath);
            if (string.IsNullOrWhiteSpace(options.AddonPath))
                throw new ArgumentException("Addon path is required.", nameof(options.AddonPath));
            if (string.IsNullOrWhiteSpace(options.WorkDir))
                throw new ArgumentException("Work dir is required.", nameof(options.WorkDir));

            var startInfo = new ProcessStartInfo
            {
                FileName = options.WorkerExePath,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetDirectoryName(options.WorkerExePath) ?? string.Empty
            };

            foreach (string argument in SourceAddonOptimizerCommandBuilder.BuildArguments(options))
                startInfo.ArgumentList.Add(argument);

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
                throw new InvalidOperationException("Failed to start SourceAddonOptimizer worker.");

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
            LogLine?.Invoke(line);
            if (isError)
                ErrorLine?.Invoke(line);
            var update = _parser.Parse(line);
            if (update == null)
                return;

            if (!string.IsNullOrWhiteSpace(update.OutputAddonPath))
                OutputPathFound?.Invoke(update.OutputAddonPath);

            ProgressUpdate?.Invoke(update);
        }
    }
}
