using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Audio;
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
}
