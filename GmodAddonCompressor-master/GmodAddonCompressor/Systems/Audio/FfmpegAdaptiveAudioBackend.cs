using GmodAddonCompressor.DataContexts;
using System;
using System.IO;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Audio
{
    internal sealed class FfmpegAdaptiveAudioBackend : IAdaptiveAudioBackend
    {
        private readonly FFMpegSystem _ffmpeg;

        internal FfmpegAdaptiveAudioBackend(FFMpegSystem ffmpeg)
        {
            _ffmpeg = ffmpeg ?? throw new ArgumentNullException(nameof(ffmpeg));
        }

        public Task<bool> EncodeBaselineAsync(string inputPath, string outputPath, AudioEncodingSpec spec) =>
            _ffmpeg.ReencodeWavAsync(
                inputPath,
                outputPath,
                spec.SampleRate,
                spec.Channels,
                ToContextCodec(spec.Codec));

        public Task<bool> EncodeCandidateAsync(string inputPath, string outputPath, AudioEncodingSpec spec) =>
            _ffmpeg.ReencodeAdaptiveWavAsync(
                inputPath,
                outputPath,
                spec.SampleRate,
                spec.Channels,
                ToContextCodec(spec.Codec),
                spec.BlockAlign);

        public async Task<PcmAudio> DecodeAsync(string path, int sampleRate, int channels)
        {
            float[]? samples = await _ffmpeg.DecodeWavFloatAsync(path, sampleRate, channels).ConfigureAwait(false);
            if (samples == null)
                throw new InvalidDataException($"FFmpeg could not decode WAV: {path}");
            return new PcmAudio(sampleRate, channels, samples);
        }

        private static AudioContext.WavCodecKind ToContextCodec(AudioCodec codec) =>
            codec == AudioCodec.MsAdpcm
                ? AudioContext.WavCodecKind.AdpcmMs
                : AudioContext.WavCodecKind.Pcm16;
    }
}
