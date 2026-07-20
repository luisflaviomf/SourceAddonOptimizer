using System;
using System.Diagnostics;
using System.Globalization;
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
        internal int MaximumJobs { get; init; } = 0;
        internal bool Strict { get; init; }
        internal bool ResumeOpt { get; init; }
        internal bool Overwrite { get; init; }
        internal bool OverwriteWork { get; init; }
        internal bool RestoreSkins { get; init; }
        internal bool CompileVerbose { get; init; }
        internal bool CleanupWorkModelArtifacts { get; init; }
        internal bool SingleAddonOnly { get; init; }
    }

    internal interface ISourceAddonOptimizerProcess : IDisposable
    {
        event Action<string>? OutputLine;
        event Action<string>? ErrorLine;
        bool HasExited { get; }
        int ExitCode { get; }
        bool Start();
        void BeginOutputReadLine();
        void BeginErrorReadLine();
        Task WaitForExitAsync(CancellationToken cancellationToken);
        bool TryRequestCooperativeCancellation();
        void KillEntireProcessTree();
    }

    internal sealed class SystemSourceAddonOptimizerProcess : ISourceAddonOptimizerProcess
    {
        private readonly Process _process;

        internal SystemSourceAddonOptimizerProcess(ProcessStartInfo startInfo)
        {
            _process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
            _process.OutputDataReceived += (_, args) =>
            {
                if (args.Data != null)
                    OutputLine?.Invoke(args.Data);
            };
            _process.ErrorDataReceived += (_, args) =>
            {
                if (args.Data != null)
                    ErrorLine?.Invoke(args.Data);
            };
        }

        public event Action<string>? OutputLine;
        public event Action<string>? ErrorLine;
        public bool HasExited => _process.HasExited;
        public int ExitCode => _process.ExitCode;
        public bool Start() => _process.Start();
        public void BeginOutputReadLine() => _process.BeginOutputReadLine();
        public void BeginErrorReadLine() => _process.BeginErrorReadLine();
        public Task WaitForExitAsync(CancellationToken cancellationToken) =>
            _process.WaitForExitAsync(cancellationToken);

        public bool TryRequestCooperativeCancellation()
        {
            // The worker currently has no cancellation channel. With CreateNoWindow,
            // redirected streams, and a WPF parent that normally owns no console,
            // GenerateConsoleCtrlEvent cannot be delivered reliably. Keep this
            // explicit hook so a future documented worker channel can be added
            // without changing the cancellation ordering in the runner.
            return false;
        }

        public void KillEntireProcessTree() => _process.Kill(entireProcessTree: true);
        public void Dispose() => _process.Dispose();
    }

    internal sealed class SourceAddonOptimizerRunner
    {
        private readonly SourceAddonOptimizerProgressParser _parser = new SourceAddonOptimizerProgressParser();
        private readonly Func<ProcessStartInfo, ISourceAddonOptimizerProcess> _processFactory;
        private readonly TimeSpan _cancellationGracePeriod;

        internal SourceAddonOptimizerRunner()
            : this(
                startInfo => new SystemSourceAddonOptimizerProcess(startInfo),
                TimeSpan.FromSeconds(2))
        {
        }

        internal SourceAddonOptimizerRunner(
            Func<ProcessStartInfo, ISourceAddonOptimizerProcess> processFactory,
            TimeSpan cancellationGracePeriod)
        {
            _processFactory = processFactory ?? throw new ArgumentNullException(nameof(processFactory));
            if (cancellationGracePeriod <= TimeSpan.Zero)
                throw new ArgumentOutOfRangeException(nameof(cancellationGracePeriod));
            _cancellationGracePeriod = cancellationGracePeriod;
        }

        internal event Action<string>? LogLine;
        internal event Action<string>? ErrorLine;
        internal event Action<SourceAddonOptimizerProgressUpdate>? ProgressUpdate;
        internal event Action<string>? OutputPathFound;

        internal static ProcessStartInfo BuildStartInfo(SourceAddonOptimizerRunOptions options)
        {
            if (options == null)
                throw new ArgumentNullException(nameof(options));
            if (options.MaximumJobs < 0 || options.MaximumJobs > Environment.ProcessorCount)
                throw new ArgumentOutOfRangeException(nameof(options.MaximumJobs));

            var startInfo = new ProcessStartInfo
            {
                FileName = options.WorkerExePath,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetDirectoryName(options.WorkerExePath) ?? string.Empty
            };

            startInfo.ArgumentList.Add(options.AddonPath);
            startInfo.ArgumentList.Add("--work");
            startInfo.ArgumentList.Add(options.WorkDir);

            static void AddValue(ProcessStartInfo info, string name, string? value)
            {
                if (string.IsNullOrWhiteSpace(value))
                    return;
                info.ArgumentList.Add(name);
                info.ArgumentList.Add(value);
            }

            AddValue(startInfo, "--suffix", options.Suffix);
            AddValue(startInfo, "--optimizer-mode", options.OptimizerMode);
            AddValue(startInfo, "--blender", options.BlenderPath);
            AddValue(startInfo, "--studiomdl", options.StudioMdlPath);

            static void AddNumber(ProcessStartInfo info, string name, double? value)
            {
                if (!value.HasValue)
                    return;
                info.ArgumentList.Add(name);
                info.ArgumentList.Add(value.Value.ToString(CultureInfo.InvariantCulture));
            }

            AddNumber(startInfo, "--ratio", options.Ratio);
            AddNumber(startInfo, "--merge", options.Merge);
            AddNumber(startInfo, "--autosmooth", options.AutoSmooth);
            if (options.UsePlanar)
            {
                startInfo.ArgumentList.Add("--use-planar");
                AddNumber(startInfo, "--planar-angle", options.PlanarAngle);
            }
            if (options.ExperimentalGroundPolicy)
                startInfo.ArgumentList.Add("--experimental-ground-policy");
            if (options.ExperimentalRoundPartsPolicy)
                startInfo.ArgumentList.Add("--experimental-round-parts-policy");
            if (options.ExperimentalSteerTurnBasisFix)
                startInfo.ArgumentList.Add("--experimental-steer-turn-basis-fix");
            AddValue(startInfo, "--format", options.Format);

            static void AddInteger(ProcessStartInfo info, string name, int? value)
            {
                if (!value.HasValue)
                    return;
                info.ArgumentList.Add(name);
                info.ArgumentList.Add(value.Value.ToString(CultureInfo.InvariantCulture));
            }

            AddInteger(startInfo, "--jobs", options.Jobs);
            AddInteger(startInfo, "--decompile-jobs", options.DecompileJobs);
            AddInteger(startInfo, "--compile-jobs", options.CompileJobs);
            if (string.Equals(
                options.OptimizerMode, "maximum", StringComparison.OrdinalIgnoreCase))
            {
                startInfo.ArgumentList.Add("--maximum-jobs");
                startInfo.ArgumentList.Add(
                    options.MaximumJobs.ToString(CultureInfo.InvariantCulture));
            }
            if (options.Strict)
                startInfo.ArgumentList.Add("--strict");
            if (options.ResumeOpt)
                startInfo.ArgumentList.Add("--resume-opt");
            if (options.Overwrite)
                startInfo.ArgumentList.Add("--overwrite");
            if (options.OverwriteWork)
                startInfo.ArgumentList.Add("--overwrite-work");
            if (!options.RestoreSkins)
                startInfo.ArgumentList.Add("--no-restore-skins");
            if (options.CompileVerbose)
                startInfo.ArgumentList.Add("--compile-verbose");
            if (options.CleanupWorkModelArtifacts)
                startInfo.ArgumentList.Add("--cleanup-work-model-artifacts");
            if (options.SingleAddonOnly)
                startInfo.ArgumentList.Add("--single-addon-only");
            return startInfo;
        }

        internal async Task<int> RunAsync(SourceAddonOptimizerRunOptions options, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (string.IsNullOrWhiteSpace(options.WorkerExePath))
                throw new ArgumentException("Worker exe path is required.", nameof(options.WorkerExePath));
            if (!File.Exists(options.WorkerExePath))
                throw new FileNotFoundException("Worker exe not found.", options.WorkerExePath);
            if (string.IsNullOrWhiteSpace(options.AddonPath))
                throw new ArgumentException("Addon path is required.", nameof(options.AddonPath));
            if (string.IsNullOrWhiteSpace(options.WorkDir))
                throw new ArgumentException("Work dir is required.", nameof(options.WorkDir));

            var startInfo = BuildStartInfo(options);

            using var process = _processFactory(startInfo);
            process.OutputLine += line => HandleLine(line, false);
            process.ErrorLine += line => HandleLine(line, true);

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
                await CancelProcessAsync(process);
                throw;
            }

            return process.ExitCode;
        }

        private async Task CancelProcessAsync(ISourceAddonOptimizerProcess process)
        {
            if (process.HasExited)
                return;

            // Always attempt the cooperative hook first. The current production
            // adapter honestly returns false because the worker exposes no safe
            // Ctrl+Break/stdin protocol, but test and future adapters can support it.
            try
            {
                process.TryRequestCooperativeCancellation();
            }
            catch (Exception ex)
            {
                ReportCancellationDiagnostic(
                    $"SourceAddonOptimizer cooperative cancellation failed; using hard-kill fallback: {ex.Message}");
            }

            if (!process.HasExited)
            {
                using var grace = new CancellationTokenSource(_cancellationGracePeriod);
                try
                {
                    await process.WaitForExitAsync(grace.Token);
                }
                catch (OperationCanceledException) when (grace.IsCancellationRequested)
                {
                    // Bounded grace expired; Task 10 recovery makes hard-kill safe.
                }
            }

            if (process.HasExited)
                return;

            try
            {
                process.KillEntireProcessTree();
            }
            catch (InvalidOperationException) when (process.HasExited)
            {
                // The worker exited between the final check and Kill().
                return;
            }

            // Let redirected stdout/stderr finish dispatching terminal events after
            // the root process is killed, also with a bounded asynchronous wait.
            using var drain = new CancellationTokenSource(_cancellationGracePeriod);
            try
            {
                await process.WaitForExitAsync(drain.Token);
            }
            catch (OperationCanceledException) when (drain.IsCancellationRequested)
            {
            }
        }

        private void ReportCancellationDiagnostic(string message)
        {
            // Diagnostics must never replace the original cancellation exception.
            try
            {
                LogLine?.Invoke(message);
            }
            catch (Exception)
            {
            }
            try
            {
                ErrorLine?.Invoke(message);
            }
            catch (Exception)
            {
            }
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
