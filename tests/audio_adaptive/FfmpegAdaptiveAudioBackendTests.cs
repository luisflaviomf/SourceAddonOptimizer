using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Audio;
using System.Security.Cryptography;
using System.Text;
using Xunit;

namespace GmodAddonOptimizer.AudioAdaptive.Tests;

public sealed class FfmpegAdaptiveAudioBackendTests
{
    [Fact]
    public async Task EncodeDecode_ProducesNativeMsAdpcmWithOfficialBlockGeometry()
    {
        string root = Path.Combine(Path.GetTempPath(), "gmod-ffmpeg-audio-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string source = Path.Combine(root, "source.wav");
            string output = Path.Combine(root, "output.wav");
            await File.WriteAllBytesAsync(source, WaveFixture.Create(44100, includeLoops: false, dataBytes: 44100 * 2));
            var spec = new AudioEncodingSpec(AudioCodec.MsAdpcm, 22050, 1, 1024);
            var backend = new FfmpegAdaptiveAudioBackend(new FFMpegSystem());

            bool encoded = await backend.EncodeCandidateAsync(source, output, spec);
            PcmAudio decoded = await backend.DecodeAsync(output, 44100, 1);

            Assert.True(encoded);
            Assert.True(RiffWaveValidator.TryValidateEncodingFile(output, spec, out string reason), reason);
            int samplesPerBlock = ((spec.BlockAlign - 7 * spec.Channels) * 2 / spec.Channels) + 2;
            Assert.InRange(decoded.FrameCount, 44090, 44110 + samplesPerBlock * 2);
        }
        finally
        {
            try { Directory.Delete(root, recursive: true); } catch { }
        }
    }

    [Theory]
    [InlineData(44100, 22050, true)]
    [InlineData(22050, 44100, true)]
    [InlineData(0, 22050, true)]
    [InlineData(44100, 44100, false)]
    [InlineData(22050, 22050, false)]
    public void ResampleFilter_UsesDoubleInternalFormatOnlyWhenTheRateChanges(int sourceRate, int targetRate, bool expectDouble)
    {
        string filter = FFMpegSystem.BuildAdaptiveResampleFilter(sourceRate, targetRate);

        Assert.StartsWith($"aresample=osr={targetRate}:resampler=soxr:precision=28:cheby=1:osf=s16:dither_method=none", filter);
        Assert.Equal(expectDouble, filter.EndsWith(":internal_sample_fmt=dblp", StringComparison.Ordinal));
    }

    // libsoxr dithers INT16 output with a time-seeded generator; before the double internal format,
    // these encodes differed by up to 2 LSB on every run.
    [Theory]
    [InlineData(44100, 2, 0, 22050, 1, 2)]
    [InlineData(44100, 2, 1, 22050, 2, 2048)]
    [InlineData(44100, 1, 1, 22050, 1, 1024)]
    [InlineData(22050, 1, 0, 44100, 1, 2)]
    public async Task EncodeCandidate_IsByteIdenticalAcrossRuns(
        int sourceRate, int sourceChannels, int codec, int rate, int channels, int blockAlign)
    {
        string root = Path.Combine(Path.GetTempPath(), "gmod-ffmpeg-audio-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string source = Path.Combine(root, "source.wav");
            await File.WriteAllBytesAsync(source, CreateNoisyPcm16(sourceRate, sourceChannels, seconds: 1.5));
            var spec = new AudioEncodingSpec((AudioCodec)codec, rate, channels, blockAlign);
            var backend = new FfmpegAdaptiveAudioBackend(new FFMpegSystem());

            var hashes = new List<string>();
            for (int run = 0; run < 3; run++)
            {
                string output = Path.Combine(root, $"output-{run}.wav");
                Assert.True(await backend.EncodeCandidateAsync(source, output, spec));
                hashes.Add(Convert.ToHexString(SHA256.HashData(await File.ReadAllBytesAsync(output))));
            }

            Assert.Single(hashes.Distinct());
        }
        finally
        {
            try { Directory.Delete(root, recursive: true); } catch { }
        }
    }

    private static byte[] CreateNoisyPcm16(int sampleRate, int channels, double seconds)
    {
        int frames = (int)(sampleRate * seconds);
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        int dataBytes = frames * channels * 2;
        writer.Write(Encoding.ASCII.GetBytes("RIFF"));
        writer.Write(4 + 8 + 16 + 8 + dataBytes);
        writer.Write(Encoding.ASCII.GetBytes("WAVE"));
        writer.Write(Encoding.ASCII.GetBytes("fmt "));
        writer.Write(16);
        writer.Write((ushort)1);
        writer.Write((ushort)channels);
        writer.Write(sampleRate);
        writer.Write(sampleRate * channels * 2);
        writer.Write((ushort)(channels * 2));
        writer.Write((ushort)16);
        writer.Write(Encoding.ASCII.GetBytes("data"));
        writer.Write(dataBytes);
        uint lcg = 12345;
        for (int frame = 0; frame < frames; frame++)
        {
            double t = (double)frame / sampleRate;
            for (int channel = 0; channel < channels; channel++)
            {
                lcg = lcg * 1664525u + 1013904223u;
                double noise = ((lcg >> 8) / (double)(1 << 24)) - 0.5;
                double value =
                    0.30 * Math.Sin(2 * Math.PI * (220 + 30 * channel) * t) +
                    0.15 * Math.Sin(2 * Math.PI * 3150 * t) +
                    0.10 * noise;
                writer.Write((short)Math.Round(value * 32767));
            }
        }
        return stream.ToArray();
    }
}
