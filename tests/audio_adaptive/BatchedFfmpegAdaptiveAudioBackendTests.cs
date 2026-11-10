using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Audio;
using System.Text;
using System.Text.Json;
using Xunit;

namespace GmodAddonOptimizer.AudioAdaptive.Tests;

/// <summary>
/// The batched backend puts the ffmpeg steps of many sounds in the same process. Every sound must come out
/// with the bytes and the decisions of the one-sound backend.
/// </summary>
public sealed class BatchedFfmpegAdaptiveAudioBackendTests
{
    private static readonly AdaptiveAudioOptions Options = new(
        new AudioEncodingSpec(AudioCodec.Pcm16, 22050, 1, 2),
        PreserveLoopMetadata: true);

    [Fact]
    public async Task ManySoundsAtOnce_GetTheBytesAndDecisionsOfTheOneSoundBackend()
    {
        string root = NewRoot();
        try
        {
            var sounds = new Dictionary<string, byte[]>
            {
                ["mono44.wav"] = CreatePcm16(44100, 1, 1.2, seed: 1),
                ["stereo44.wav"] = CreatePcm16(44100, 2, 0.9, seed: 2),
                ["mono22.wav"] = CreatePcm16(22050, 1, 1.6, seed: 3),
                ["mono11.wav"] = CreatePcm16(11025, 1, 2.0, seed: 4),
                ["stereo_same.wav"] = CreatePcm16(44100, 2, 0.7, seed: 5, sameChannels: true),
                // global metadata: the baseline command copies it, and with several inputs in one command
                // ffmpeg's default would take it from the first input instead
                ["tagged.wav"] = CreatePcm16(44100, 1, 0.8, seed: 6, title: "engine idle", artist: "someone"),
                ["tagged2.wav"] = CreatePcm16(44100, 1, 0.5, seed: 7, title: "another title", artist: "other"),
                ["loop.wav"] = WaveFixture.Create(44100, includeLoops: true, dataBytes: 44100 * 2),
                ["tone8k.wav"] = CreatePcm16(48000, 1, 1.0, seed: 8),
            };
            string singleRoot = Path.Combine(root, "single");
            string batchRoot = Path.Combine(root, "batch");
            foreach (string folder in new[] { singleRoot, batchRoot })
            {
                Directory.CreateDirectory(folder);
                foreach ((string name, byte[] bytes) in sounds)
                    await File.WriteAllBytesAsync(Path.Combine(folder, name), bytes);
            }

            var ffmpeg = new FFMpegSystem();
            var single = new AdaptiveAudioOptimizer(new FfmpegAdaptiveAudioBackend(ffmpeg));
            var singleResults = new Dictionary<string, AdaptiveAudioResult>();
            foreach (string name in sounds.Keys)
                singleResults[name] = await single.OptimizeAsync(Path.Combine(singleRoot, name), Options);

            using var backend = new BatchedFfmpegAdaptiveAudioBackend(ffmpeg);
            var batched = new AdaptiveAudioOptimizer(backend);
            AdaptiveAudioResult[] batchResults = await Task.WhenAll(
                sounds.Keys.Select(name => batched.OptimizeAsync(Path.Combine(batchRoot, name), Options)));

            int index = 0;
            foreach (string name in sounds.Keys)
            {
                Assert.Equal(
                    await File.ReadAllBytesAsync(Path.Combine(singleRoot, name)),
                    await File.ReadAllBytesAsync(Path.Combine(batchRoot, name)));
                Assert.Equal(Describe(singleResults[name]), Describe(batchResults[index++]));
            }
            Assert.Contains(singleResults.Values, result => !result.UsedBaselineFallback);      // candidates were chosen
            Assert.Empty(Directory.GetFileSystemEntries(batchRoot, "*.adaptive-*"));
            (long processes, long steps, long redone) = backend.Statistics;
            Assert.True(steps >= sounds.Count * 3 && processes * 3 <= steps, $"{steps} steps in {processes} processes");
            Assert.Equal(0, redone);
        }
        finally
        {
            try { Directory.Delete(root, recursive: true); } catch { }
        }
    }

    [Fact]
    public async Task AnUnreadableSound_DoesNotChangeTheOthers()
    {
        string root = NewRoot();
        try
        {
            var sounds = new Dictionary<string, byte[]>
            {
                ["good1.wav"] = CreatePcm16(44100, 1, 1.0, seed: 11),
                // a valid header announcing a codec ffmpeg cannot open: every command with this input fails
                ["broken.wav"] = CreateUnknownCodecWave(),
                ["good2.wav"] = CreatePcm16(44100, 2, 0.8, seed: 12),
                ["good3.wav"] = CreatePcm16(22050, 1, 1.1, seed: 13),
            };
            string singleRoot = Path.Combine(root, "single");
            string batchRoot = Path.Combine(root, "batch");
            foreach (string folder in new[] { singleRoot, batchRoot })
            {
                Directory.CreateDirectory(folder);
                foreach ((string name, byte[] bytes) in sounds)
                    await File.WriteAllBytesAsync(Path.Combine(folder, name), bytes);
            }

            var ffmpeg = new FFMpegSystem();
            var single = new AdaptiveAudioOptimizer(new FfmpegAdaptiveAudioBackend(ffmpeg));
            var singleResults = new Dictionary<string, AdaptiveAudioResult>();
            foreach (string name in sounds.Keys)
                singleResults[name] = await single.OptimizeAsync(Path.Combine(singleRoot, name), Options);

            using var backend = new BatchedFfmpegAdaptiveAudioBackend(ffmpeg);
            var batched = new AdaptiveAudioOptimizer(backend);
            AdaptiveAudioResult[] batchResults = await Task.WhenAll(
                sounds.Keys.Select(name => batched.OptimizeAsync(Path.Combine(batchRoot, name), Options)));

            int index = 0;
            foreach (string name in sounds.Keys)
            {
                Assert.Equal(
                    await File.ReadAllBytesAsync(Path.Combine(singleRoot, name)),
                    await File.ReadAllBytesAsync(Path.Combine(batchRoot, name)));
                Assert.Equal(Describe(singleResults[name]), Describe(batchResults[index++]));
            }
            Assert.False(singleResults["broken.wav"].Replaced);
            Assert.Equal(sounds["broken.wav"], await File.ReadAllBytesAsync(Path.Combine(batchRoot, "broken.wav")));
            Assert.True(backend.Statistics.RedoneSteps > 0);          // the failed process was redone step by step
            Assert.Empty(Directory.GetFileSystemEntries(batchRoot, "*.adaptive-*"));
        }
        finally
        {
            try { Directory.Delete(root, recursive: true); } catch { }
        }
    }

    /// <summary>Everything a result says except how long it took.</summary>
    private static string Describe(AdaptiveAudioResult result) =>
        JsonSerializer.Serialize(new
        {
            result.Replaced,
            result.BaselineCreated,
            result.UsedBaselineFallback,
            result.SelectedSpec,
            result.OriginalBytes,
            result.BaselineBytes,
            result.OutputBytes,
            Candidates = result.Candidates.Select(candidate => new
            {
                candidate.Spec,
                candidate.Bytes,
                candidate.Score,
                candidate.StructurallyValid,
                candidate.QualityAccepted,
                // failure texts carry the temporary file names
                Note = candidate.Note is "quality-safe" or "quality-regression" or "not-smaller-than-baseline" or "encode-failed"
                    ? candidate.Note
                    : "error"
            }),
            Note = result.Replaced ? result.Note : "not replaced"
        });

    private static string NewRoot()
    {
        string root = Path.Combine(Path.GetTempPath(), "gmod-ffmpeg-batch-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }

    private static byte[] CreatePcm16(
        int sampleRate, int channels, double seconds, uint seed, bool sameChannels = false,
        string? title = null, string? artist = null)
    {
        int frames = (int)(sampleRate * seconds);
        int dataBytes = frames * channels * 2;
        byte[] info = BuildInfoList(title, artist);
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        writer.Write(Encoding.ASCII.GetBytes("RIFF"));
        writer.Write(4 + 8 + 16 + info.Length + 8 + dataBytes);
        writer.Write(Encoding.ASCII.GetBytes("WAVE"));
        writer.Write(Encoding.ASCII.GetBytes("fmt "));
        writer.Write(16);
        writer.Write((ushort)1);
        writer.Write((ushort)channels);
        writer.Write(sampleRate);
        writer.Write(sampleRate * channels * 2);
        writer.Write((ushort)(channels * 2));
        writer.Write((ushort)16);
        writer.Write(info);
        writer.Write(Encoding.ASCII.GetBytes("data"));
        writer.Write(dataBytes);
        uint lcg = seed * 2654435761u + 12345u;
        for (int frame = 0; frame < frames; frame++)
        {
            double t = (double)frame / sampleRate;
            double shared = 0;
            for (int channel = 0; channel < channels; channel++)
            {
                lcg = lcg * 1664525u + 1013904223u;
                double noise = ((lcg >> 8) / (double)(1 << 24)) - 0.5;
                double value =
                    0.30 * Math.Sin(2 * Math.PI * (180 + 40 * seed + (sameChannels ? 0 : 30 * channel)) * t) +
                    0.12 * Math.Sin(2 * Math.PI * (900 + 350 * seed) * t) +
                    0.04 * noise;
                if (sameChannels)
                {
                    if (channel == 0)
                        shared = value;
                    value = shared;
                }
                writer.Write((short)Math.Round(value * 32767));
            }
        }
        return stream.ToArray();
    }

    private static byte[] BuildInfoList(string? title, string? artist)
    {
        if (title == null && artist == null)
            return Array.Empty<byte>();
        using var body = new MemoryStream();
        using (var writer = new BinaryWriter(body, Encoding.ASCII, leaveOpen: true))
        {
            writer.Write(Encoding.ASCII.GetBytes("INFO"));
            foreach ((string id, string? text) in new[] { ("INAM", title), ("IART", artist) })
            {
                if (text == null)
                    continue;
                byte[] bytes = Encoding.ASCII.GetBytes(text + "\0");
                writer.Write(Encoding.ASCII.GetBytes(id));
                writer.Write(bytes.Length);
                writer.Write(bytes);
                if (bytes.Length % 2 == 1)
                    writer.Write((byte)0);
            }
        }
        using var list = new MemoryStream();
        using (var writer = new BinaryWriter(list, Encoding.ASCII, leaveOpen: true))
        {
            writer.Write(Encoding.ASCII.GetBytes("LIST"));
            writer.Write((int)body.Length);
            writer.Write(body.ToArray());
        }
        return list.ToArray();
    }

    private static byte[] CreateUnknownCodecWave()
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        const int dataBytes = 4096;
        writer.Write(Encoding.ASCII.GetBytes("RIFF"));
        writer.Write(4 + 8 + 16 + 8 + dataBytes);
        writer.Write(Encoding.ASCII.GetBytes("WAVE"));
        writer.Write(Encoding.ASCII.GetBytes("fmt "));
        writer.Write(16);
        writer.Write((ushort)0x7A21);      // a format tag no decoder is registered for
        writer.Write((ushort)1);
        writer.Write(44100);
        writer.Write(44100 * 2);
        writer.Write((ushort)2);
        writer.Write((ushort)16);
        writer.Write(Encoding.ASCII.GetBytes("data"));
        writer.Write(dataBytes);
        writer.Write(new byte[dataBytes]);
        return stream.ToArray();
    }
}
