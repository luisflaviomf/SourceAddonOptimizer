using GmodAddonCompressor.Systems.Optimizer;
using System.Text.Json;
using Xunit;

namespace VtfUniversal.Tests;

[CollectionDefinition("FullCompression", DisableParallelization = true)]
public sealed class FullCompressionCollection { }

[Collection("FullCompression")]
public sealed class FullCompressionTests
{
    [Fact]
    public async Task RealMediaConversionPreservesMonoAndScaledCue()
    {
        using var f = new Fixture();
        byte[] source = Wave(44100, 100);
        string path = Path.Combine(f.Source, "loop.wav"); File.WriteAllBytes(path, source);
        await FullCompressionMedia.RunAsync(f.Source, _ => { }, CancellationToken.None);
        byte[] output = File.ReadAllBytes(path);
        FullCompressionWav.Validate(source, output);
        Assert.True(output.Length < source.Length);
    }

    [Fact]
    public async Task CompiledWpfTabLoadsWithItsOwnControlsAndExistingTabs()
    {
        var done = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            try
            {
                var app = new GmodAddonCompressor.App(); app.InitializeComponent();
                var window = new GmodAddonCompressor.MainWindow();
                var tab = Assert.IsType<System.Windows.Controls.TabItem>(window.FindName("Tab_FullCompression"));
                Assert.Equal("Compressão completa", tab.Header);
                Assert.IsType<System.Windows.Controls.TextBox>(window.FindName("TextBox_FullInput"));
                Assert.IsType<System.Windows.Controls.TextBox>(window.FindName("TextBox_FullEngine"));
                Assert.IsType<System.Windows.Controls.Button>(window.FindName("Button_FullStart"));
                Assert.IsType<System.Windows.Controls.TextBox>(window.FindName("TextBox_ExactInput"));
                Assert.IsType<System.Windows.Controls.Button>(window.FindName("Button_StartPipeline"));
                window.Measure(new System.Windows.Size(1280, 720));
                window.Arrange(new System.Windows.Rect(0, 0, 1280, 720));
                Assert.True(tab.IsSelected);
                window.Close(); app.Shutdown();
                done.SetResult(true);
            }
            catch (Exception ex) { done.SetException(ex); }
        });
        thread.SetApartmentState(ApartmentState.STA); thread.Start();
        await done.Task.WaitAsync(TimeSpan.FromSeconds(30));
    }

    [Theory]
    [InlineData(50, true)]
    [InlineData(51, false)]
    [InlineData(-1, false)]
    public void WavContractRequiresPreservedScaledCuePoints(int outputCue, bool valid)
    {
        byte[] source = Wave(44100, 100), target = Wave(22050, outputCue);
        if (valid) FullCompressionWav.Validate(source, target);
        else Assert.Throws<IOException>(() => FullCompressionWav.Validate(source, target));
    }

    private static byte[] Wave(int rate, int cue)
    {
        using var stream = new MemoryStream(); using var writer = new BinaryWriter(stream);
        void Tag(string tag) => writer.Write(System.Text.Encoding.ASCII.GetBytes(tag));
        Tag("RIFF"); writer.Write(0); Tag("WAVE");
        Tag("fmt "); writer.Write(16); writer.Write((short)1); writer.Write((short)1);
        writer.Write(rate); writer.Write(rate * 2); writer.Write((short)2); writer.Write((short)16);
        Tag("data"); writer.Write(rate * 2); writer.Write(new byte[rate * 2]);
        if (cue >= 0)
        {
            Tag("cue "); writer.Write(28); writer.Write(1); writer.Write(7); writer.Write(cue);
            Tag("data"); writer.Write(0); writer.Write(0); writer.Write(cue);
        }
        writer.Seek(4, SeekOrigin.Begin); writer.Write((int)stream.Length - 8);
        return stream.ToArray();
    }

    [Theory]
    [InlineData(1, false)]
    [InlineData(0, true)]
    public async Task WavRejectsPartialOutputFromFailedOrTimedOutFfmpeg(int exitCode, bool timedOut)
    {
        using var f = new Fixture(); string output = Path.Combine(f.Root, "partial.wav");
        var ffmpeg = new GmodAddonCompressor.Systems.FFMpegSystem(_ =>
        {
            File.WriteAllText(output, "nonempty partial output");
            return Task.FromResult(new GmodAddonCompressor.Systems.FFMpegSystem.FfmpegRunResult { ExitCode = exitCode, TimedOut = timedOut });
        });
        Assert.False(await ffmpeg.ReencodeWavAsync("input.wav", output, 22050, 1,
            GmodAddonCompressor.DataContexts.AudioContext.WavCodecKind.Pcm16));
    }

    [LiveFact]
    public async Task RealPackagedToolsRunCompleteChainOnPinnedCopy()
    {
        string source = Environment.GetEnvironmentVariable("FULL_COMPRESSION_SMOKE_SOURCE")!;
        string work = Path.Combine("C:/GModLab/research-cache/full-compression-tab-20260905", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(work);
        var options = new FullCompressionOptions(source, Path.Combine(work, "compressed_smoke"), Path.Combine(work, "work"),
            "C:/Program Files (x86)/Steam/steamapps/common/GarrysMod",
            "C:/GModLab/research-cache/product-exact-integration-20260905/packaged-tools-r1/SourceAddonOptimizerWorker.exe",
            "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe",
            "C:/Program Files (x86)/Steam/steamapps/common/GarrysMod/bin/studiomdl.exe");
        var runner = new FullCompressionRunner();
        runner.LogLine += Console.WriteLine;
        var result = await runner.RunAsync(options, CancellationToken.None);
        Assert.True(result.Exact.Success);
        Assert.True(result.FinalBytes < result.SourceBytes);
        Assert.False(Directory.EnumerateFiles(options.OutputPath, "*.dx80.vtx", SearchOption.AllDirectories).Any());
        Assert.False(Directory.EnumerateFiles(options.OutputPath, "*.sw.vtx", SearchOption.AllDirectories).Any());
        Console.WriteLine("COMPLETE_SMOKE_REPORT: " + result.ReportPath);
    }

    private sealed class LiveFactAttribute : FactAttribute
    {
        public LiveFactAttribute()
        {
            if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("FULL_COMPRESSION_SMOKE_SOURCE")))
                Skip = "Explicit real-addon smoke; set FULL_COMPRESSION_SMOKE_SOURCE to a read-only native fixture.";
        }
    }
    [Fact]
    public async Task MediaUsesOwnImageProfileAndRestoresPreviousSettings()
    {
        using var f = new Fixture();
        string png = Path.Combine(f.Source, "icon.png");
        using (var image = new ImageMagick.MagickImage(ImageMagick.MagickColors.Red, 64, 64)) image.Write(png);
        GmodAddonCompressor.DataContexts.ImageContext.Resolution = 0; // would break legacy resize if inherited
        GmodAddonCompressor.DataContexts.ImageContext.TaargetWidth = 4096;
        await FullCompressionMedia.RunAsync(f.Source, _ => { }, CancellationToken.None);
        using var output = new ImageMagick.MagickImage(png);
        Assert.True(output.Width < 64);
        Assert.Equal(0, GmodAddonCompressor.DataContexts.ImageContext.Resolution);
        Assert.Equal(4096, GmodAddonCompressor.DataContexts.ImageContext.TaargetWidth);
        Assert.Equal("original model", File.ReadAllText(Path.Combine(f.Source, "models/a.mdl")));
    }

    [Fact]
    public async Task InvalidImageCannotSilentlyBecomeSuccessfulMediaStage()
    {
        using var f = new Fixture(); File.WriteAllText(Path.Combine(f.Source, "broken.png"), "not a PNG");
        await Assert.ThrowsAsync<IOException>(() => FullCompressionMedia.RunAsync(f.Source, _ => { }, CancellationToken.None));
    }

    [Fact]
    public async Task RunsAllStagesOnCopiesAndReportsOriginalToFinalSaving()
    {
        using var f = new Fixture();
        var calls = new List<string>();
        var runner = new FullCompressionRunner(
            (o, t) => { calls.Add("models"); Assert.NotEqual(f.Source, o.AddonPath);
                Assert.Equal(.75, o.Ratio); Assert.False(o.ExperimentalGroundPolicy);
                Assert.False(o.Overwrite); Assert.True(o.Strict);
                File.WriteAllText(Path.Combine(o.AddonPath, "models/a.mdl"), "native");
                return Task.FromResult(o.AddonPath); },
            (p, t) => { calls.Add("media"); Assert.Equal("native", File.ReadAllText(Path.Combine(p, "models/a.mdl"))); return Task.CompletedTask; },
            (o, t) => { calls.Add("exact"); return f.Export(o); });
        var result = await runner.RunAsync(f.Options, CancellationToken.None);
        Assert.Equal(new[] { "models", "media", "exact" }, calls);
        Assert.Equal("original model", File.ReadAllText(Path.Combine(f.Source, "models/a.mdl")));
        Assert.True(result.Exact.Success);
        Assert.Equal(14, result.SourceBytes);
        Assert.Equal(3, result.FinalBytes);
        Assert.Equal(11, result.SavedBytes);
        Assert.True(File.Exists(result.ReportPath));
        using var report = JsonDocument.Parse(File.ReadAllText(result.ReportPath));
        Assert.False(report.RootElement.GetProperty("whole_pipeline_byte_exact").GetBoolean());
    }

    [Theory]
    [InlineData("models")]
    [InlineData("media")]
    [InlineData("exact")]
    public async Task FailureCannotReachLaterStagesOrCreateSuccessReport(string failing)
    {
        using var f = new Fixture();
        var calls = new List<string>();
        void Step(string name) { calls.Add(name); if (failing == name) throw new IOException(name); }
        var runner = new FullCompressionRunner(
            (o, t) => { Step("models"); return Task.FromResult(o.AddonPath); },
            (p, t) => { Step("media"); return Task.CompletedTask; },
            (o, t) => { Step("exact"); return f.Export(o); });
        await Assert.ThrowsAsync<IOException>(() => runner.RunAsync(f.Options, CancellationToken.None));
        Assert.Equal(failing, calls.Last());
        Assert.False(File.Exists(Path.Combine(f.Options.WorkDir, "full-compression-report.json")));
        Assert.Equal("original model", File.ReadAllText(Path.Combine(f.Source, "models/a.mdl")));
    }

    [Fact]
    public async Task CancellationAfterNativeStageNeverExports()
    {
        using var f = new Fixture(); using var cts = new CancellationTokenSource();
        var runner = new FullCompressionRunner((o, t) => Task.FromResult(o.AddonPath),
            (p, t) => { cts.Cancel(); return Task.CompletedTask; },
            (o, t) => throw new Xunit.Sdk.XunitException("Must not export after cancellation"));
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => runner.RunAsync(f.Options, cts.Token));
        Assert.False(Directory.Exists(f.Options.OutputPath));
    }

    [Fact]
    public async Task SourceDriftPreventsCompletion()
    {
        using var f = new Fixture();
        var runner = new FullCompressionRunner((o, t) => Task.FromResult(o.AddonPath),
            (p, t) => { File.WriteAllText(Path.Combine(f.Source, "new.txt"), "external drift"); return Task.CompletedTask; },
            (o, t) => f.Export(o));
        await Assert.ThrowsAsync<IOException>(() => runner.RunAsync(f.Options, CancellationToken.None));
        Assert.False(File.Exists(Path.Combine(f.Options.WorkDir, "full-compression-report.json")));
    }

    [Fact]
    public async Task ExistingOutputIsRejectedBeforeAnyStaging()
    {
        using var f = new Fixture(); Directory.CreateDirectory(f.Options.OutputPath);
        var runner = new FullCompressionRunner();
        await Assert.ThrowsAsync<IOException>(() => runner.RunAsync(f.Options, CancellationToken.None));
        Assert.False(Directory.Exists(f.Options.WorkDir));
    }

    [Fact]
    public async Task NoModelsAddonDoesNotRequireOrRunBlender()
    {
        using var f = new Fixture(); File.Move(Path.Combine(f.Source, "models/a.mdl"), Path.Combine(f.Source, "content.txt"));
        var runner = new FullCompressionRunner((o, t) => throw new Xunit.Sdk.XunitException("No model stage expected"),
            (p, t) => Task.CompletedTask, (o, t) => f.Export(o));
        var result = await runner.RunAsync(f.Options, CancellationToken.None);
        Assert.True(result.Exact.Success);
    }

    private sealed class Fixture : IDisposable
    {
        internal string Root = Path.Combine(Path.GetTempPath(), "full-compression-" + Guid.NewGuid().ToString("N"));
        internal string Source => Path.Combine(Root, "source");
        internal FullCompressionOptions Options;
        internal Fixture()
        {
            Directory.CreateDirectory(Path.Combine(Source, "models"));
            File.WriteAllText(Path.Combine(Source, "models/a.mdl"), "original model");
            Directory.CreateDirectory(Path.Combine(Root, "engine"));
            Options = new(Source, Path.Combine(Root, "candidate"), Path.Combine(Root, "work"),
                Path.Combine(Root, "engine"), "worker", "blender", "studiomdl");
        }
        internal Task<ExactDistributionResult> Export(ExactDistributionRunOptions o)
        {
            Directory.CreateDirectory(o.OutputPath); Directory.CreateDirectory(o.WorkDir);
            File.WriteAllText(Path.Combine(o.OutputPath, "payload.txt"), "out");
            string report = Path.Combine(o.WorkDir, "candidate-report.json"); File.WriteAllText(report, "{}");
            return Task.FromResult(new ExactDistributionResult(0, o.OutputPath, report, "offline"));
        }
        public void Dispose() => Directory.Delete(Root, true);
    }
}
