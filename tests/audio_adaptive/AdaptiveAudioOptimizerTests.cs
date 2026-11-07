using GmodAddonCompressor.Systems.Audio;
using Xunit;

namespace GmodAddonOptimizer.AudioAdaptive.Tests;

public sealed class AdaptiveAudioOptimizerTests
{
    [Fact]
    public async Task Optimize_PromotesSmallestQualitySafeCandidate()
    {
        using var fixture = new OptimizerFixture();
        var backend = new FakeBackend(
            WaveFixture.Create(22050, includeLoops: false, dataBytes: 2000),
            WaveFixture.CreateAdpcm(22050, 1, 1024, blocks: 1, paddingSamples: 20));
        var optimizer = new AdaptiveAudioOptimizer(backend);

        AdaptiveAudioResult result = await optimizer.OptimizeAsync(fixture.InputPath, fixture.Options);

        Assert.True(result.Replaced);
        Assert.False(result.UsedBaselineFallback);
        Assert.Equal(AudioCodec.MsAdpcm, result.SelectedSpec.Codec);
        byte[] output = await File.ReadAllBytesAsync(fixture.InputPath);
        Assert.True(RiffWaveDuration.TryGetLogicalSampleCount(output, out ulong samples, out string reason), reason);
        Assert.Equal(
            RiffWaveDuration.ScaleSampleCount(600, 44100, result.SelectedSpec.SampleRate),
            samples);
        Assert.True(result.OutputBytes < result.BaselineBytes);
    }

    [Fact]
    public async Task Optimize_CandidateEvaluationFailure_CommitsBaselineByteIdentically()
    {
        using var fixture = new OptimizerFixture();
        var backend = new FakeBackend(
            WaveFixture.Create(22050, includeLoops: false, dataBytes: 2000),
            WaveFixture.CreateAdpcm(22050, 1, 1024, blocks: 1, paddingSamples: 20))
        {
            FailAdpcmDecode = true
        };
        var optimizer = new AdaptiveAudioOptimizer(backend);

        AdaptiveAudioResult result = await optimizer.OptimizeAsync(fixture.InputPath, fixture.Options);

        Assert.True(result.Replaced);
        Assert.True(result.UsedBaselineFallback);
        Assert.Equal(backend.BaselineBytes, await File.ReadAllBytesAsync(fixture.InputPath));
    }

    [Fact]
    public async Task Optimize_BaselineEncodeFailure_LeavesOriginalByteIdentical()
    {
        using var fixture = new OptimizerFixture();
        byte[] original = await File.ReadAllBytesAsync(fixture.InputPath);
        var backend = new FakeBackend(
            WaveFixture.Create(22050, includeLoops: false, dataBytes: 2000),
            WaveFixture.CreateAdpcm(22050, 1, 1024, blocks: 1, paddingSamples: 20))
        {
            FailBaselineEncode = true
        };
        var optimizer = new AdaptiveAudioOptimizer(backend);

        AdaptiveAudioResult result = await optimizer.OptimizeAsync(fixture.InputPath, fixture.Options);

        Assert.False(result.Replaced);
        Assert.Equal(original, await File.ReadAllBytesAsync(fixture.InputPath));
    }

    [Fact]
    public async Task Optimize_AlreadyAtBaseline_FallbackPreservesEveryOriginalByte()
    {
        string root = Path.Combine(Path.GetTempPath(), "gmod-audio-exact-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string input = Path.Combine(root, "input.wav");
            byte[] original = WaveFixture.Create(44100, includeLoops: true, dataBytes: 2000, trailingJunk: true);
            await File.WriteAllBytesAsync(input, original);
            var backend = new FakeBackend(
                WaveFixture.Create(44100, includeLoops: false, dataBytes: 2000),
                WaveFixture.CreateAdpcm(22050, 1, 1024, blocks: 1, paddingSamples: 20))
            {
                FailAdpcmDecode = true
            };
            var optimizer = new AdaptiveAudioOptimizer(backend);
            var options = new AdaptiveAudioOptions(
                new AudioEncodingSpec(AudioCodec.Pcm16, 44100, 1, 2),
                PreserveLoopMetadata: true);

            AdaptiveAudioResult result = await optimizer.OptimizeAsync(input, options);

            Assert.True(result.UsedBaselineFallback);
            Assert.Equal(original, await File.ReadAllBytesAsync(input));
        }
        finally
        {
            try { Directory.Delete(root, recursive: true); } catch { }
        }
    }

    [Fact]
    public void Plan_UsesOnlyNativeRatesOfficialBlocksAndConservativeDownmix()
    {
        float[] dualMono = new float[44100 * 2];
        for (int frame = 0; frame < 44100; frame++)
        {
            float value = MathF.Sin(frame * 0.03f);
            dualMono[frame * 2] = value;
            dualMono[frame * 2 + 1] = value;
        }
        var source = new PcmAudio(44100, 2, dualMono);

        IReadOnlyList<AudioEncodingSpec> plan = AdaptiveAudioPlan.Build(
            source,
            new AudioEncodingSpec(AudioCodec.Pcm16, 22050, 2, 4));

        Assert.All(plan, spec => Assert.Contains(spec.SampleRate, new[] { 22050, 44100 }));
        Assert.Contains(plan, spec => spec.Codec == AudioCodec.MsAdpcm && spec.Channels == 1 && spec.BlockAlign == 1024);
        Assert.Contains(plan, spec => spec.Codec == AudioCodec.MsAdpcm && spec.Channels == 2 && spec.BlockAlign == 2048);
    }

    private sealed class OptimizerFixture : IDisposable
    {
        private readonly string _root = Path.Combine(Path.GetTempPath(), "gmod-audio-test-" + Guid.NewGuid().ToString("N"));

        internal OptimizerFixture()
        {
            Directory.CreateDirectory(_root);
            InputPath = Path.Combine(_root, "input.wav");
            File.WriteAllBytes(InputPath, WaveFixture.Create(44100, includeLoops: false, dataBytes: 1200));
            Options = new AdaptiveAudioOptions(
                new AudioEncodingSpec(AudioCodec.Pcm16, 22050, 1, 2),
                PreserveLoopMetadata: true);
        }

        internal string InputPath { get; }
        internal AdaptiveAudioOptions Options { get; }

        public void Dispose()
        {
            try { Directory.Delete(_root, recursive: true); } catch { }
        }
    }

    private sealed class FakeBackend : IAdaptiveAudioBackend
    {
        private readonly Dictionary<string, AudioCodec> _outputs = new(StringComparer.OrdinalIgnoreCase);
        private readonly PcmAudio _pcm = new(44100, 1, Enumerable.Range(0, 1200).Select(i => MathF.Sin(i * 0.04f)).ToArray());

        internal FakeBackend(byte[] baselineBytes, byte[] adpcmBytes)
        {
            BaselineBytes = baselineBytes;
            AdpcmBytes = adpcmBytes;
        }

        internal byte[] BaselineBytes { get; }
        internal byte[] AdpcmBytes { get; }
        internal bool FailBaselineEncode { get; init; }
        internal bool FailAdpcmDecode { get; init; }

        public Task<bool> EncodeBaselineAsync(string inputPath, string outputPath, AudioEncodingSpec spec)
        {
            if (FailBaselineEncode)
                return Task.FromResult(false);
            return WriteOutput(outputPath, spec);
        }

        public Task<bool> EncodeCandidateAsync(string inputPath, string outputPath, AudioEncodingSpec spec) =>
            WriteOutput(outputPath, spec);

        private Task<bool> WriteOutput(string outputPath, AudioEncodingSpec spec)
        {
            byte[] bytes = spec.Codec == AudioCodec.MsAdpcm ? AdpcmBytes : BaselineBytes;
            File.WriteAllBytes(outputPath, bytes);
            _outputs[outputPath] = spec.Codec;
            return Task.FromResult(true);
        }

        public Task<PcmAudio> DecodeAsync(string path, int sampleRate, int channels)
        {
            if (_outputs.TryGetValue(path, out AudioCodec codec) && codec == AudioCodec.MsAdpcm && FailAdpcmDecode)
                throw new InvalidDataException("synthetic candidate decode failure");
            return Task.FromResult(_pcm);
        }
    }
}
