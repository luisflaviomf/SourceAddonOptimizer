using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Objects;
using GmodAddonCompressor.Systems.Audio;
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
    public async Task ManualMode_LoopedSoundInAdpcm_IsWrittenForTheWayTheEngineLoops()
    {
        string root = Path.Combine(Path.GetTempPath(), "gmod-wav-manual-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            // 30000 samples at 44100 Hz mono, loop start 17640: 8820 at 22050 Hz, four blocks + 676
            string path = Path.Combine(root, "loop.wav");
            await File.WriteAllBytesAsync(path, WaveFixture.WithLoopStart(WaveFixture.Create(44100, includeLoops: true, dataBytes: 60000), 17640));
            CompressDirectoryContext.DirectoryPath = root;
            AudioContext.WavOptimization = AudioContext.WavOptimizationKind.Manual;
            AudioContext.WavCodec = AudioContext.WavCodecKind.AdpcmMs;
            AudioContext.WavSampleRate = 22050;
            AudioContext.WavChannels = 1;
            AudioContext.PreserveLoopMetadata = true;
            var editor = new WAVEdit();

            editor.Prepare();
            await editor.Compress(path);
            await editor.CompleteAsync();

            byte[] output = await File.ReadAllBytesAsync(path);
            var spec = new AudioEncodingSpec(AudioCodec.MsAdpcm, 22050, 1, 1024);
            Assert.True(RiffWaveValidator.TryValidateEncoding(output, spec, allowShortLastBlock: true, out string reason), reason);
            Assert.True(RiffWaveDuration.TryGetLogicalSampleCount(output, out ulong samples, out reason), reason);
            Assert.Equal(15000ul, samples);
            uint mark = BitConverter.ToUInt32(WaveFixture.ChunkData(output, "cue "), 24);
            Assert.Equal(4u * 1024u + 676u, mark);
            Assert.Equal(8820, MsAdpcmLoop.EngineRestart(mark, 1024, 1));
            Assert.Equal(mark, BitConverter.ToUInt32(WaveFixture.ChunkData(output, "smpl"), 44));
            Assert.Equal(15000ul, MsAdpcmLoop.DecodableSamples(WaveFixture.ChunkData(output, "data").Length, 1024, 1));
        }
        finally
        {
            AudioContext.WavCodec = AudioContext.WavCodecKind.Pcm16;
            try { Directory.Delete(root, recursive: true); } catch { }
        }
    }

    [Fact]
    public async Task ManualMode_LoopNoAdpcmBlockReaches_StaysPcm16WithItsLoopMarks()
    {
        string root = Path.Combine(Path.GetTempPath(), "gmod-wav-manual-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            uint unreachable = (uint)Enumerable.Range(1, 200000).First(s => s % 2036 >= 1024 && s % 4084 >= 2048 && s % 1012 >= 512);
            string path = Path.Combine(root, "loop.wav");
            await File.WriteAllBytesAsync(path, WaveFixture.WithLoopStart(WaveFixture.Create(44100, includeLoops: true, dataBytes: 60000), unreachable * 2));
            CompressDirectoryContext.DirectoryPath = root;
            AudioContext.WavOptimization = AudioContext.WavOptimizationKind.Manual;
            AudioContext.WavCodec = AudioContext.WavCodecKind.AdpcmMs;
            AudioContext.WavSampleRate = 22050;
            AudioContext.WavChannels = 1;
            AudioContext.PreserveLoopMetadata = true;
            var editor = new WAVEdit();

            editor.Prepare();
            await editor.Compress(path);
            await editor.CompleteAsync();

            byte[] output = await File.ReadAllBytesAsync(path);
            Assert.True(RiffWaveFormat.TryRead(output, out RiffWaveFormat? format, out string reason), reason);
            Assert.Equal(1, format!.FormatTag);
            Assert.Equal(22050, format.SampleRate);
            Assert.Equal(unreachable, BitConverter.ToUInt32(WaveFixture.ChunkData(output, "cue "), 24));
        }
        finally
        {
            AudioContext.WavCodec = AudioContext.WavCodecKind.Pcm16;
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
