using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using GmodAddonCompressor.Systems.Optimizer;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace GmodAddonCompressor.Tests;

[TestClass]
public sealed class SourceAddonOptimizerRunnerCancellationTests
{
    [TestMethod]
    public async Task CancellationRequestsCooperationBeforeTreeKill()
    {
        var process = new FakeOptimizerProcess(exitAfterCooperativeRequest: false);
        var runner = new SourceAddonOptimizerRunner(_ => process, TimeSpan.FromMilliseconds(1));
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        await Assert.ThrowsAsync<OperationCanceledException>(() =>
            runner.RunAsync(ValidOptions(), cancellation.Token));

        CollectionAssert.AreEqual(
            new[] { "start", "read-output", "read-error", "cooperative", "wait-grace", "kill-tree", "wait-drain" },
            process.Calls);
    }

    [TestMethod]
    public async Task CancellationDoesNotKillAfterNaturalExitDuringGracePeriod()
    {
        var process = new FakeOptimizerProcess(exitAfterCooperativeRequest: true);
        var runner = new SourceAddonOptimizerRunner(_ => process, TimeSpan.FromSeconds(1));
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        await Assert.ThrowsAsync<OperationCanceledException>(() =>
            runner.RunAsync(ValidOptions(), cancellation.Token));

        CollectionAssert.AreEqual(
            new[] { "start", "read-output", "read-error", "cooperative", "wait-grace" },
            process.Calls);
    }

    [TestMethod]
    public async Task TerminalEventDuringCancellationGraceIsStillParsed()
    {
        var process = new FakeOptimizerProcess(
            exitAfterCooperativeRequest: true,
            terminalLine: "MAXIMUM_EVENT {\"schema\":1,\"kind\":\"run_cancelled\",\"report_path\":\"logs/maximum_report.json\"}");
        var runner = new SourceAddonOptimizerRunner(_ => process, TimeSpan.FromSeconds(1));
        SourceAddonOptimizerProgressUpdate? seen = null;
        runner.ProgressUpdate += update => seen = update;
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        await Assert.ThrowsAsync<OperationCanceledException>(() =>
            runner.RunAsync(ValidOptions(), cancellation.Token));

        Assert.IsNotNull(seen);
        Assert.AreEqual("run_cancelled", seen.MaximumKind);
        Assert.AreEqual("logs/maximum_report.json", seen.ReportPath);
    }

    private static SourceAddonOptimizerRunOptions ValidOptions()
    {
        return new SourceAddonOptimizerRunOptions
        {
            WorkerExePath = Environment.ProcessPath!,
            AddonPath = "addon",
            WorkDir = "work"
        };
    }

    private sealed class FakeOptimizerProcess : ISourceAddonOptimizerProcess
    {
        private readonly bool _exitAfterCooperativeRequest;
        private readonly string? _terminalLine;
        private bool _cooperativeRequested;
        private bool _killed;

        internal FakeOptimizerProcess(bool exitAfterCooperativeRequest, string? terminalLine = null)
        {
            _exitAfterCooperativeRequest = exitAfterCooperativeRequest;
            _terminalLine = terminalLine;
        }

        internal List<string> Calls { get; } = new();
        public event Action<string>? OutputLine;
        public event Action<string>? ErrorLine
        {
            add { }
            remove { }
        }
        public bool HasExited { get; private set; }
        public int ExitCode => 0;

        public bool Start()
        {
            Calls.Add("start");
            return true;
        }

        public void BeginOutputReadLine() => Calls.Add("read-output");
        public void BeginErrorReadLine() => Calls.Add("read-error");

        public Task WaitForExitAsync(CancellationToken cancellationToken)
        {
            if (_killed)
            {
                Calls.Add("wait-drain");
                return Task.CompletedTask;
            }

            if (_cooperativeRequested)
            {
                Calls.Add("wait-grace");
                if (_exitAfterCooperativeRequest)
                {
                    OutputLine?.Invoke(_terminalLine ?? string.Empty);
                    HasExited = true;
                    return Task.CompletedTask;
                }

                return Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            }

            return Task.FromCanceled(cancellationToken);
        }

        public bool TryRequestCooperativeCancellation()
        {
            Calls.Add("cooperative");
            _cooperativeRequested = true;
            return true;
        }

        public void KillEntireProcessTree()
        {
            Calls.Add("kill-tree");
            _killed = true;
            HasExited = true;
        }

        public void Dispose()
        {
        }
    }
}
