using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Objects;
using Xunit;

namespace GmodAddonOptimizer.AudioAdaptive.Tests;

public sealed class WavEditCompatibilityTests
{
    [Fact]
    public async Task ManualMode_AlreadyMatchingWavRemainsByteIdentical()
    {
        string root = Path.Combine(Path.GetTempPath(), "gmod-wav-manual-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string path = Path.Combine(root, "manual.wav");
            byte[] original = WaveFixture.Create(44100, includeLoops: true, dataBytes: 4000, trailingJunk: true);
            await File.WriteAllBytesAsync(path, original);
            CompressDirectoryContext.DirectoryPath = root;
            AudioContext.WavOptimization = AudioContext.WavOptimizationKind.Manual;
            AudioContext.WavCodec = AudioContext.WavCodecKind.Pcm16;
            AudioContext.WavSampleRate = 44100;
            AudioContext.WavChannels = 1;
            AudioContext.PreserveLoopMetadata = true;
            var editor = new WAVEdit();

            editor.Prepare();
            await editor.Compress(path);
            await editor.CompleteAsync();

            Assert.Equal(original, await File.ReadAllBytesAsync(path));
        }
        finally
        {
            try { Directory.Delete(root, recursive: true); } catch { }
        }
    }

    [Fact]
    public void UiContext_DefaultsToManualButExposesAdaptiveProfile()
    {
        var context = new MainWindowContext();

        Assert.Equal(0, context.WavOptimizationIndex);
        Assert.Equal(2, context.WavOptimizationList.Length);
        Assert.Contains("Adaptativo", context.WavOptimizationList[1], StringComparison.OrdinalIgnoreCase);
    }
}
