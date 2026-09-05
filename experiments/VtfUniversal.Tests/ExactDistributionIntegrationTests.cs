using System.Diagnostics;
using System.Reflection;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Systems.Optimizer;
using System.Text;
using System.Text.Json;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class ExactDistributionIntegrationTests
{
    [Fact]
    public void ArgumentsPreserveUnicodeAndSpacesAsSeparateValues()
    {
        using var fixture = new ExactFixture();
        var info = ExactDistributionRunner.CreateStartInfo(fixture.Options);
        Assert.Equal(new[] { "exactpack", fixture.Options.SourcePath, "--output", fixture.Options.OutputPath,
            "--work", fixture.Options.WorkDir, "--engine-root", fixture.Options.EngineRoot }, info.ArgumentList);
        Assert.False(info.UseShellExecute);
        Assert.Equal(Encoding.UTF8, info.StandardOutputEncoding);
    }

    [Theory]
    [InlineData("EXACT_PROGRESS: encoding|1|3|models/ação file.vvd", true)]
    [InlineData("EXACT_PROGRESS: encoding|4|3|models/a.vvd", false)]
    [InlineData("EXACT_PROGRESS: encoding|oops|3|models/a.vvd", false)]
    [InlineData("EXACT_PROGRESS: encoding|-1|3|models/a.vvd", false)]
    [InlineData("ordinary log line", false)]
    public void ProgressRejectsMalformedCounters(string line, bool accepted)
    {
        var result = ExactDistributionRunner.ParseProgress(line);
        Assert.Equal(accepted, result != null);
        if (accepted) Assert.Equal("models/ação file.vvd", result!.Path);
    }

    [Fact]
    public void ExistingOrNestedOutputIsRejectedBeforeWorkerLaunch()
    {
        using var fixture = new ExactFixture();
        Directory.CreateDirectory(fixture.Options.OutputPath);
        Assert.Throws<IOException>(() => ExactDistributionRunner.CreateStartInfo(fixture.Options));
        var nested = new ExactDistributionRunOptions { SourcePath = fixture.Options.SourcePath,
            OutputPath = Path.Combine(fixture.Options.SourcePath, "out"), WorkDir = fixture.Options.WorkDir,
            EngineRoot = fixture.Options.EngineRoot };
        Assert.Throws<IOException>(() => ExactDistributionRunner.ValidatePaths(nested));
    }

    [Fact]
    public void OutputLeafMustMatchRuntimeAddonNameContract()
    {
        using var fixture = new ExactFixture();
        var options = new ExactDistributionRunOptions { SourcePath = fixture.Options.SourcePath,
            OutputPath = Path.Combine(fixture.Root, "Invalid Output"), WorkDir = fixture.Options.WorkDir,
            EngineRoot = fixture.Options.EngineRoot };
        Assert.Throws<ArgumentException>(() => ExactDistributionRunner.ValidatePaths(options));
    }

    [Fact]
    public void InstalledAddonCanBeReadWhenOutputAndWorkAreOutsideEngine()
    {
        using var fixture = new ExactFixture();
        string source = Path.Combine(fixture.Options.EngineRoot, "garrysmod/addons/source");
        Directory.CreateDirectory(source);
        var options = new ExactDistributionRunOptions { SourcePath = source, OutputPath = fixture.Options.OutputPath,
            WorkDir = fixture.Options.WorkDir, EngineRoot = fixture.Options.EngineRoot };
        ExactDistributionRunner.ValidatePaths(options);
    }

    [Theory]
    [InlineData(false, true, false, true, "Failed")]
    [InlineData(true, false, false, true, "Failed")]
    [InlineData(true, true, true, true, "Canceled")]
    [InlineData(true, true, false, true, "ReadyForExact")]
    [InlineData(true, true, false, false, "Success")]
    public void PipelineOnlyExportsAfterBothNativeStagesSucceed(bool models, bool compress, bool canceled, bool requested, string expected)
    {
        Assert.Equal(expected, PipelineCompletion.Evaluate(models, compress, canceled, requested, null).ToString());
    }

    [Fact]
    public void PipelineSelectsExactOutputOnlyAfterSuccessfulExport()
    {
        var failure = new ExactDistributionResult(7, null, null, "failed");
        Assert.Equal(PipelineOutcome.Failed, PipelineCompletion.Evaluate(true, true, false, true, failure));
        Assert.Equal("native", PipelineCompletion.FinalOutput("native", failure));
        var success = new ExactDistributionResult(0, "exact", "report", "offline");
        Assert.Equal(PipelineOutcome.Success, PipelineCompletion.Evaluate(true, true, false, true, success));
        Assert.Equal("exact", PipelineCompletion.FinalOutput("native", success));
    }

    [Fact]
    public async Task ZeroExitWithoutExpectedReportIsNotSuccess()
    {
        using var fixture = new ExactFixture();
        var runner = new ExactDistributionRunner();
        await Assert.ThrowsAsync<InvalidDataException>(() => runner.RunProcessAsync(Powershell("exit 0"), fixture.Options, CancellationToken.None));
    }

    [Fact]
    public async Task NonzeroExitCannotBeOverriddenBySuccessMarkers()
    {
        using var fixture = new ExactFixture();
        var runner = new ExactDistributionRunner();
        var result = await runner.RunProcessAsync(Powershell("Write-Output 'EXACT_OUTPUT: fake'; exit 7"), fixture.Options, CancellationToken.None);
        Assert.False(result.Success);
        Assert.Equal(7, result.ExitCode);
    }

    [Fact]
    public void ReportMustConfirmExactIdentityAndPendingRuntimeStatus()
    {
        using var fixture = new ExactFixture();
        string report = fixture.WriteReport("OFFLINE_PENDING_GMOD");
        var result = ExactDistributionRunner.ReadResult(fixture.Options, fixture.Options.OutputPath, report);
        Assert.True(result.Success);
        Assert.Contains("OFFLINE_PENDING_GMOD", result.Summary);
        fixture.WriteReport("APPROVED");
        Assert.Throws<InvalidDataException>(() => ExactDistributionRunner.ReadResult(fixture.Options, fixture.Options.OutputPath, report));
    }

    [Fact]
    public async Task CancellationKillsOnlyOwnedWorkerTreeAndRejectsConcurrentExport()
    {
        using var fixture = new ExactFixture();
        using var unrelated = Process.Start(Powershell("Start-Sleep -Seconds 60"))!;
        var runner = new ExactDistributionRunner();
        using var cancellation = new CancellationTokenSource();
        var childSeen = new TaskCompletionSource<int>(TaskCreationOptions.RunContinuationsAsynchronously);
        runner.LogLine += line => { if (line.StartsWith("CHILD:")) childSeen.TrySetResult(int.Parse(line[6..])); };
        string command = "$child = Start-Process powershell.exe -ArgumentList '-NoProfile -NonInteractive -Command Start-Sleep -Seconds 60' -PassThru -WindowStyle Hidden; Write-Output ('CHILD:' + $child.Id); Start-Sleep -Seconds 60";
        Task<ExactDistributionResult> running = runner.RunProcessAsync(Powershell(command), fixture.Options, cancellation.Token);
        try
        {
            int childId = await childSeen.Task.WaitAsync(TimeSpan.FromSeconds(15));
            using var child = Process.GetProcessById(childId);
            await Assert.ThrowsAsync<InvalidOperationException>(() => runner.RunProcessAsync(Powershell("exit 0"), fixture.Options, CancellationToken.None));
            cancellation.Cancel();
            await Assert.ThrowsAnyAsync<OperationCanceledException>(() => running);
            Assert.True(child.HasExited);
            Assert.False(unrelated.HasExited);
            Assert.False(Directory.Exists(fixture.Options.OutputPath));
        }
        finally
        {
            cancellation.Cancel();
            try { await running; } catch (OperationCanceledException) { }
            if (!unrelated.HasExited) unrelated.Kill(true);
            await unrelated.WaitForExitAsync();
        }
    }

    private static ProcessStartInfo Powershell(string command)
    {
        var info = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell/v1.0/powershell.exe"))
        { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
        foreach (string arg in new[] { "-NoProfile", "-NonInteractive", "-Command", command }) info.ArgumentList.Add(arg);
        return info;
    }

    private sealed class ExactFixture : IDisposable
    {
        internal string Root { get; } = Path.Combine(Path.GetTempPath(), "exact ação " + Guid.NewGuid().ToString("N"));
        internal ExactDistributionRunOptions Options { get; }
        internal ExactFixture()
        {
            Options = new() { SourcePath = Path.Combine(Root, "entrada espaço"), OutputPath = Path.Combine(Root, "candidate"),
                WorkDir = Path.Combine(Root, "relatório"), EngineRoot = Path.Combine(Root, "engine"),
                WorkerExePath = Powershell("exit 0").FileName };
            Directory.CreateDirectory(Options.SourcePath);
            Directory.CreateDirectory(Options.EngineRoot);
        }
        internal string WriteReport(string status)
        {
            Directory.CreateDirectory(Options.OutputPath); Directory.CreateDirectory(Options.WorkDir);
            string report = Path.Combine(Options.WorkDir, "candidate-report.json");
            File.WriteAllText(report, JsonSerializer.Serialize(new { schema = "exact-distribution-report/v1", status,
                candidate = Options.OutputPath, byte_exact = true, new_quality_loss = false, installed = false,
                source_bytes = 100, candidate_bytes = 80, codec_net_savings_bytes = 14, excluded = new { count = 2, bytes = 6 } }));
            return report;
        }
        public void Dispose() => Directory.Delete(Root, true);
    }

    [Fact]
    public async Task NativeCompressionFailureCannotBeMistakenForSuccessfulCompletion()
    {
        string root = Path.Combine(Path.GetTempPath(), "exact-native-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            File.WriteAllText(Path.Combine(root, "test.lua"), "print(1)");
            var compressor = new CompressAddonSystem(root);
            compressor.IncludeLUA();
            var services = (Dictionary<string, ICompress>)typeof(CompressAddonSystem)
                .GetField("_compressServices", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(compressor)!;
            services[".lua"] = new FailingCompressor();
            var completed = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            compressor.e_CompletedCompress += () => completed.TrySetResult(true);
            compressor.StartCompress();
            await completed.Task.WaitAsync(TimeSpan.FromSeconds(15));
            var failure = typeof(CompressAddonSystem).GetProperty("HasFailures", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.NotNull(failure);
            Assert.True((bool)failure!.GetValue(compressor)!);
        }
        finally { Directory.Delete(root, true); }
    }

    private sealed class FailingCompressor : ICompress
    {
        public Task Compress(string filePath) => Task.FromException(new IOException("fixture compression failure"));
    }
}
