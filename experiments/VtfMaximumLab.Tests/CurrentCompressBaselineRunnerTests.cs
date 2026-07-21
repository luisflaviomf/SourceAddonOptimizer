using VtfMaximumLab.Baseline;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class CurrentCompressBaselineRunnerTests
{
    [Fact]
    public async Task ConfiguresTwoXWithEightPixelFloorsAndWaits()
    {
        string root = Path.Combine(Path.GetTempPath(), $"vtf_baseline_{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        try
        {
            var fake = new RecordingCurrentCompressor();
            var runner = new CurrentCompressBaselineRunner(fake);

            _ = await runner.RunAsync(root, CancellationToken.None);

            Assert.NotNull(fake.Settings);
            Assert.Equal(2, fake.Settings!.ResolutionFactor);
            Assert.Equal(8, fake.Settings.MinimumWidth);
            Assert.Equal(8, fake.Settings.MinimumHeight);
            Assert.True(fake.Settings.KeepAspectRatio);
            Assert.True(fake.Completed);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    private sealed class RecordingCurrentCompressor : ICurrentVtfCompressor
    {
        public CurrentCompressSettings? Settings { get; private set; }
        public bool Completed { get; private set; }

        public Task RunAsync(
            string treeRoot,
            CurrentCompressSettings settings,
            Action<string> onFileCompleted,
            CancellationToken cancellationToken)
        {
            Settings = settings;
            Completed = true;
            return Task.CompletedTask;
        }
    }
}
