using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Audio;
using Xunit;

namespace GmodAddonOptimizer.AudioAdaptive.Tests;

/// <summary>
/// Looped MS-ADPCM as the engine plays it (measured in the game, docs/sons-2026-11.md): the padding of the
/// last block is heard on every pass, and the loop restarts at
/// (mark / blockAlign) * samplesPerBlock + mark % blockAlign.
/// </summary>
public sealed class MsAdpcmLoopTests
{
    // the engine's arithmetic, written here independently of the product code
    private static long EngineRestart(long mark, int blockAlign, int channels)
    {
        int samplesPerBlock = 2 + (blockAlign - 7 * channels) * 2 / channels;
        return (mark / blockAlign) * samplesPerBlock + mark % blockAlign;
    }

    [Theory]
    [InlineData(0, 1)]
    [InlineData(676, 1)]
    [InlineData(8820, 1)]
    [InlineData(17640, 1)]
    [InlineData(438656, 1)]
    [InlineData(8820, 2)]
    [InlineData(123457, 2)]
    public void ChosenBlockAndMark_MakeTheEngineRestartAtTheLoopStart(long loopStart, int channels)
    {
        int blockAlign = MsAdpcmLoop.ChooseBlockAlign(loopStart, channels);

        Assert.NotEqual(0, blockAlign);
        Assert.True(MsAdpcmLoop.TryCompensate(loopStart, blockAlign, channels, out uint mark));
        Assert.Equal(loopStart, EngineRestart(mark, blockAlign, channels));
        Assert.Equal(loopStart, MsAdpcmLoop.EngineRestart(mark, blockAlign, channels));
    }

    [Fact]
    public void ChooseBlockAlign_KeepsTheOfficialSizeWhenItReachesTheLoopStart()
    {
        // mono: a block reaches only its first 1024 of 2036 samples
        Assert.Equal(1024, MsAdpcmLoop.ChooseBlockAlign(8820, 1));       // 8820 = 4 blocks + 676
        Assert.Equal(2048, MsAdpcmLoop.ChooseBlockAlign(17640, 1));      // 17640 = 8 blocks + 1352: next size
        // stereo: 2048 bytes hold 2036 samples, every position is reachable
        Assert.Equal(2048, MsAdpcmLoop.ChooseBlockAlign(17640, 2));
        Assert.Equal(2048, MsAdpcmLoop.ChooseBlockAlign(2035, 2));
    }

    [Fact]
    public void ChooseBlockAlign_GivesUpWhenNoTestedMonoBlockReachesTheLoopStart()
    {
        long unreachable = -1;
        for (long start = 1; start < 200000 && unreachable < 0; start++)
        {
            if (start % 2036 >= 1024 && start % 4084 >= 2048 && start % 1012 >= 512)
                unreachable = start;
        }

        Assert.True(unreachable > 0);
        Assert.Equal(0, MsAdpcmLoop.ChooseBlockAlign(unreachable, 1));
        Assert.False(MsAdpcmLoop.TryCompensate(unreachable, 1024, 1, out _));
    }

    [Theory]
    [InlineData(1, 1024, 3, 346)]      // even count
    [InlineData(1, 1024, 3, 345)]      // odd count: a mono block holds one sample more
    [InlineData(1, 512, 4, 1)]
    [InlineData(2, 2048, 2, 100)]
    [InlineData(2, 2048, 2, 101)]
    [InlineData(1, 1024, 2, 0)]        // already ends on a block
    public void TruncateLastBlock_EndsTheDataAtTheLastSample(int channels, int blockAlign, int blocks, int paddingSamples)
    {
        byte[] padded = WaveFixture.CreateAdpcm(22050, channels, blockAlign, blocks, paddingSamples);
        int samplesPerBlock = MsAdpcmLoop.SamplesPerBlock(blockAlign, channels);
        ulong logical = (ulong)(blocks * samplesPerBlock - paddingSamples);
        var spec = new AudioEncodingSpec(AudioCodec.MsAdpcm, 22050, channels, blockAlign);

        byte[] cut = MsAdpcmLoop.TruncateLastBlock(padded, logical);

        Assert.True(RiffWaveValidator.TryValidate(cut, out string reason), reason);
        Assert.True(RiffWaveDuration.TryGetLogicalSampleCount(cut, out ulong fact, out reason), reason);
        Assert.Equal(logical, fact);
        int dataBytes = WaveFixture.ChunkData(cut, "data").Length;
        ulong decodable = MsAdpcmLoop.DecodableSamples(dataBytes, blockAlign, channels);
        ulong extra = channels == 1 && logical % 2 == 1 ? 1ul : 0ul;
        Assert.Equal(logical + extra, decodable);
        Assert.True(dataBytes <= blocks * blockAlign);
        Assert.True(RiffWaveValidator.TryValidateEncoding(cut, spec, allowShortLastBlock: true, out reason), reason);
    }

    [Fact]
    public void Validator_AcceptsAShortLastBlockOnlyWhereALoopAsksForIt()
    {
        byte[] padded = WaveFixture.CreateAdpcm(22050, 1, 1024, blocks: 3, paddingSamples: 346);
        byte[] cut = MsAdpcmLoop.TruncateLastBlock(padded, (ulong)(3 * 2036 - 346));
        var spec = new AudioEncodingSpec(AudioCodec.MsAdpcm, 22050, 1, 1024);

        Assert.False(RiffWaveValidator.TryValidateEncoding(cut, spec, out string strictReason));
        Assert.Contains("block", strictReason, StringComparison.OrdinalIgnoreCase);
        // a padded file is not what a loop needs
        Assert.False(RiffWaveValidator.TryValidateEncoding(padded, spec, allowShortLastBlock: true, out string loopReason));
        Assert.Contains("last sample", loopReason, StringComparison.OrdinalIgnoreCase);
        Assert.True(RiffWaveValidator.TryValidateEncoding(padded, spec, out string paddedReason), paddedReason);
    }

    [Fact]
    public void Restore_WithAnEngineMark_WritesItInTheFirstCueAndTheFirstSamplerLoop()
    {
        byte[] source = WaveFixture.Create(44100, includeLoops: true);
        byte[] encoded = WaveFixture.Create(22050, includeLoops: false);
        WaveLoopMetadata metadata = WaveLoopMetadata.Capture(source, 44100);

        Assert.True(metadata.TryGetEngineLoopStart(22050, out long loopStart));
        Assert.Equal(500, loopStart);
        byte[] restored = metadata.Restore(encoded, 22050, engineLoopMark: 4321u);

        byte[] cue = WaveFixture.ChunkData(restored, "cue ");
        Assert.Equal(4321u, BitConverter.ToUInt32(cue, 8));
        Assert.Equal(4321u, BitConverter.ToUInt32(cue, 24));
        byte[] smpl = WaveFixture.ChunkData(restored, "smpl");
        Assert.Equal(4321u, BitConverter.ToUInt32(smpl, 44));
        Assert.Equal(750u, BitConverter.ToUInt32(smpl, 48));           // the loop end keeps its scaled value
        Assert.Equal(metadata.Restore(encoded, 22050), metadata.Restore(encoded, 22050, engineLoopMark: null));
    }

    [Fact]
    public void EngineLoopStart_IsAbsentWithoutLoopMarks()
    {
        WaveLoopMetadata metadata = WaveLoopMetadata.Capture(WaveFixture.Create(44100, includeLoops: false), 44100);

        Assert.False(metadata.TryGetEngineLoopStart(44100, out _));
    }

    [Fact]
    public void Plan_LoopedSound_GivesEachAdpcmCandidateABlockThatReachesTheLoopStart()
    {
        var source = new PcmAudio(44100, 1, Enumerable.Range(0, 44100).Select(i => MathF.Sin(i * 0.03f)).ToArray());
        var baseline = new AudioEncodingSpec(AudioCodec.Pcm16, 22050, 1, 2);
        // loop start 17640 at 44100 Hz (8 blocks + 1352: not reachable with 1024), 8820 at 22050 Hz (reachable)
        WaveLoopMetadata loops = WaveLoopMetadata.Capture(WaveFixture.WithLoopStart(WaveFixture.Create(44100, includeLoops: true), 17640), 44100);

        IReadOnlyList<AudioEncodingSpec> plan = AdaptiveAudioPlan.Build(source, baseline, loops);
        IReadOnlyList<AudioEncodingSpec> unlooped = AdaptiveAudioPlan.Build(source, baseline);

        Assert.Contains(plan, spec => spec.Codec == AudioCodec.MsAdpcm && spec.SampleRate == 44100 && spec.BlockAlign == 2048);
        Assert.Contains(plan, spec => spec.Codec == AudioCodec.MsAdpcm && spec.SampleRate == 22050 && spec.BlockAlign == 1024);
        Assert.Equal(unlooped.Where(spec => spec.Codec == AudioCodec.Pcm16), plan.Where(spec => spec.Codec == AudioCodec.Pcm16));
        Assert.All(unlooped.Where(spec => spec.Codec == AudioCodec.MsAdpcm), spec => Assert.Equal(1024, spec.BlockAlign));
    }

    [Fact]
    public void Plan_LoopedSound_LeavesOutAdpcmWhenNoBlockReachesTheLoopStart()
    {
        var source = new PcmAudio(22050, 1, Enumerable.Range(0, 22050).Select(i => MathF.Sin(i * 0.03f)).ToArray());
        var baseline = new AudioEncodingSpec(AudioCodec.Pcm16, 22050, 1, 2);
        long unreachable = Enumerable.Range(1, 200000).First(s => s % 2036 >= 1024 && s % 4084 >= 2048 && s % 1012 >= 512);
        WaveLoopMetadata loops = WaveLoopMetadata.Capture(WaveFixture.WithLoopStart(WaveFixture.Create(22050, includeLoops: true), (uint)unreachable), 22050);

        IReadOnlyList<AudioEncodingSpec> plan = AdaptiveAudioPlan.Build(source, baseline, loops);

        Assert.DoesNotContain(plan, spec => spec.Codec == AudioCodec.MsAdpcm);
    }

    [Fact]
    public async Task RealEncoder_LoopedCandidate_DecodesToTheExactLengthAndRestartsAtTheLoopStart()
    {
        string root = Path.Combine(Path.GetTempPath(), "gmod-adpcm-loop-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            // 0.7 s at 44100 Hz mono (30871 samples, an odd count), loop start at 17640
            const int frames = 30871;
            byte[] pcm = WaveFixture.WithLoopStart(WaveFixture.Create(44100, includeLoops: true, dataBytes: frames * 2), 17640);
            FillWithTone(pcm);
            string source = Path.Combine(root, "source.wav");
            await File.WriteAllBytesAsync(source, pcm);
            WaveLoopMetadata loops = WaveLoopMetadata.Capture(pcm, 44100);
            var backend = new FfmpegAdaptiveAudioBackend(new FFMpegSystem());

            foreach ((int rate, int expectedBlock) in new[] { (44100, 2048), (22050, 1024) })
            {
                Assert.True(loops.TryGetEngineLoopStart(rate, out long loopStart));
                int blockAlign = MsAdpcmLoop.ChooseBlockAlign(loopStart, 1);
                Assert.Equal(expectedBlock, blockAlign);
                var spec = new AudioEncodingSpec(AudioCodec.MsAdpcm, rate, 1, blockAlign);
                string output = Path.Combine(root, $"loop-{rate}.wav");
                Assert.True(await backend.EncodeCandidateAsync(source, output, spec));
                ulong logical = RiffWaveDuration.ScaleSampleCount(frames, 44100, rate);

                MsAdpcmLoop.WriteLoopedFile(output, spec, loops, logical);

                byte[] written = await File.ReadAllBytesAsync(output);
                Assert.True(RiffWaveValidator.TryValidateEncoding(written, spec, allowShortLastBlock: true, out string reason), reason);
                uint mark = BitConverter.ToUInt32(WaveFixture.ChunkData(written, "cue "), 24);
                Assert.Equal(loopStart, EngineRestart(mark, blockAlign, 1));
                Assert.Equal(mark, BitConverter.ToUInt32(WaveFixture.ChunkData(written, "smpl"), 44));
                PcmAudio decoded = await backend.DecodeAsync(output, rate, 1);
                // an odd mono count keeps one sample more; nothing of the padded block is left
                Assert.InRange((ulong)decoded.FrameCount, logical, logical + 1);
            }
        }
        finally
        {
            try { Directory.Delete(root, recursive: true); } catch { }
        }
    }

    [Fact]
    public async Task Optimizer_LoopedSound_CommitsAdpcmWrittenForTheEngine()
    {
        string root = Path.Combine(Path.GetTempPath(), "gmod-adpcm-loop-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string input = Path.Combine(root, "input.wav");
            // 600 samples at 44100 Hz; loop start 5000 there, 2500 at 22050 Hz (one block + 464)
            await File.WriteAllBytesAsync(input, WaveFixture.WithLoopStart(WaveFixture.Create(44100, includeLoops: true, dataBytes: 1200), 5000));
            var backend = new LoopBackend();
            var optimizer = new AdaptiveAudioOptimizer(backend);
            var options = new AdaptiveAudioOptions(new AudioEncodingSpec(AudioCodec.Pcm16, 22050, 1, 2), PreserveLoopMetadata: true);

            AdaptiveAudioResult result = await optimizer.OptimizeAsync(input, options);

            Assert.True(result.Replaced);
            Assert.False(result.UsedBaselineFallback);
            Assert.Equal(AudioCodec.MsAdpcm, result.SelectedSpec.Codec);
            Assert.Equal(22050, result.SelectedSpec.SampleRate);
            byte[] output = await File.ReadAllBytesAsync(input);
            Assert.True(RiffWaveValidator.TryValidateEncoding(output, result.SelectedSpec, allowShortLastBlock: true, out string reason), reason);
            uint mark = BitConverter.ToUInt32(WaveFixture.ChunkData(output, "cue "), 24);
            Assert.Equal(1024u + 464u, mark);
            Assert.Equal(2500, EngineRestart(mark, result.SelectedSpec.BlockAlign, 1));
            Assert.True(RiffWaveDuration.TryGetLogicalSampleCount(output, out ulong samples, out reason), reason);
            Assert.Equal(300ul, samples);
            Assert.Equal(300ul, MsAdpcmLoop.DecodableSamples(WaveFixture.ChunkData(output, "data").Length, 1024, 1));
        }
        finally
        {
            try { Directory.Delete(root, recursive: true); } catch { }
        }
    }

    private static void FillWithTone(byte[] wave)
    {
        int data = WaveFixture.FindFourCc(wave, "data", 12);
        int size = BitConverter.ToInt32(wave, data + 4);
        for (int i = 0; i < size / 2; i++)
        {
            short sample = (short)(12000 * Math.Sin(i * 0.05) + 3000 * Math.Sin(i * 0.31));
            BitConverter.GetBytes(sample).CopyTo(wave, data + 8 + i * 2);
        }
    }

    /// <summary>Hands back a padded one-block MS-ADPCM file for the 22050 Hz candidate, as the encoder would.</summary>
    private sealed class LoopBackend : IAdaptiveAudioBackend
    {
        private readonly PcmAudio _pcm = new(44100, 1, Enumerable.Range(0, 600).Select(i => MathF.Sin(i * 0.04f)).ToArray());

        public Task<bool> EncodeBaselineAsync(string inputPath, string outputPath, AudioEncodingSpec spec)
        {
            File.WriteAllBytes(outputPath, WaveFixture.Create(22050, includeLoops: false, dataBytes: 2000));
            return Task.FromResult(true);
        }

        public Task<bool> EncodeCandidateAsync(string inputPath, string outputPath, AudioEncodingSpec spec)
        {
            if (spec.Codec == AudioCodec.MsAdpcm && spec.SampleRate == 22050)
            {
                File.WriteAllBytes(outputPath, WaveFixture.CreateAdpcm(22050, 1, spec.BlockAlign, blocks: 1, paddingSamples: 20));
                return Task.FromResult(true);
            }
            return Task.FromResult(false);
        }

        public Task<PcmAudio> DecodeAsync(string path, int sampleRate, int channels) => Task.FromResult(_pcm);
    }
}
