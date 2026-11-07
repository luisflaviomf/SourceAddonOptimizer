using GmodAddonCompressor.Systems.Audio;
using System.Text;
using Xunit;

namespace GmodAddonOptimizer.AudioAdaptive.Tests;

public sealed class RiffLoopMetadataTests
{
    [Fact]
    public void Restore_SameRate_PreservesLoopChunksByteForByte()
    {
        byte[] source = WaveFixture.Create(44100, includeLoops: true);
        byte[] encoded = WaveFixture.Create(44100, includeLoops: false);

        WaveLoopMetadata metadata = WaveLoopMetadata.Capture(source, 44100);
        byte[] restored = metadata.Restore(encoded, 44100);

        Assert.Equal(WaveFixture.Chunk(source, "cue "), WaveFixture.Chunk(restored, "cue "));
        Assert.Equal(WaveFixture.Chunk(source, "LIST"), WaveFixture.Chunk(restored, "LIST"));
        Assert.Equal(WaveFixture.Chunk(source, "smpl"), WaveFixture.Chunk(restored, "smpl"));
        Assert.True(RiffWaveValidator.TryValidate(restored, out string reason), reason);
    }

    [Fact]
    public void Restore_HalfRate_ScalesCueLtxtAndSamplerLoop()
    {
        byte[] source = WaveFixture.Create(44100, includeLoops: true);
        byte[] encoded = WaveFixture.Create(22050, includeLoops: false);

        WaveLoopMetadata metadata = WaveLoopMetadata.Capture(source, 44100);
        byte[] restored = metadata.Restore(encoded, 22050);

        byte[] cue = WaveFixture.ChunkData(restored, "cue ");
        Assert.Equal(500u, BitConverter.ToUInt32(cue, 8));
        Assert.Equal(500u, BitConverter.ToUInt32(cue, 24));

        byte[] list = WaveFixture.ChunkData(restored, "LIST");
        int ltxtOffset = WaveFixture.FindFourCc(list, "ltxt");
        Assert.Equal(250u, BitConverter.ToUInt32(list, ltxtOffset + 12));

        byte[] smpl = WaveFixture.ChunkData(restored, "smpl");
        Assert.Equal((uint)Math.Round(1_000_000_000d / 22050d), BitConverter.ToUInt32(smpl, 8));
        Assert.Equal(500u, BitConverter.ToUInt32(smpl, 44));
        Assert.Equal(750u, BitConverter.ToUInt32(smpl, 48));
        Assert.True(RiffWaveValidator.TryValidate(restored, out string reason), reason);
    }

    [Fact]
    public void Validator_RejectsChunkOutsideRiffBoundary()
    {
        byte[] malformed = WaveFixture.Create(44100, includeLoops: false);
        BitConverter.GetBytes(int.MaxValue).CopyTo(malformed, 16);

        Assert.False(RiffWaveValidator.TryValidate(malformed, out string reason));
        Assert.Contains("chunk", reason, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void EncodingValidator_AcceptsOfficialMsAdpcmGeometryAndFactPadding()
    {
        byte[] wave = WaveFixture.CreateAdpcm(22050, channels: 1, blockAlign: 1024, blocks: 2, paddingSamples: 37);
        var spec = new AudioEncodingSpec(AudioCodec.MsAdpcm, 22050, 1, 1024);

        Assert.True(RiffWaveValidator.TryValidateEncoding(wave, spec, out string reason), reason);
    }

    [Fact]
    public void EncodingValidator_RejectsPartialMsAdpcmBlock()
    {
        byte[] wave = WaveFixture.CreateAdpcm(44100, channels: 2, blockAlign: 2048, blocks: 2, paddingSamples: 20);
        int dataOffset = WaveFixture.FindFourCc(wave, "data", 12);
        BitConverter.GetBytes(4095).CopyTo(wave, dataOffset + 4);
        var spec = new AudioEncodingSpec(AudioCodec.MsAdpcm, 44100, 2, 2048);

        Assert.False(RiffWaveValidator.TryValidateEncoding(wave, spec, out string reason));
        Assert.Contains("block", reason, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void LogicalDuration_PatchesAdpcmFactFromOriginalPcmDuration()
    {
        byte[] original = WaveFixture.Create(44100, includeLoops: false, dataBytes: 44100 * 2);
        byte[] candidate = WaveFixture.CreateAdpcm(22050, 1, 1024, blocks: 12, paddingSamples: 0);

        Assert.True(RiffWaveDuration.TryGetLogicalSampleCount(original, out ulong sourceSamples, out string sourceReason), sourceReason);
        ulong expected = RiffWaveDuration.ScaleSampleCount(sourceSamples, 44100, 22050);
        Assert.True(RiffWaveDuration.TrySetAdpcmFactSampleCount(candidate, expected, out string patchReason), patchReason);
        Assert.True(RiffWaveDuration.TryGetLogicalSampleCount(candidate, out ulong candidateSamples, out string candidateReason), candidateReason);

        Assert.Equal(44100ul, sourceSamples);
        Assert.Equal(22050ul, candidateSamples);
    }
}

internal static class WaveFixture
{
    internal static byte[] Create(int sampleRate, bool includeLoops, int dataBytes = 400, bool trailingJunk = false)
    {
        var chunks = new List<byte[]>
        {
            MakeChunk("fmt ", Format(sampleRate)),
            MakeChunk("data", new byte[dataBytes])
        };

        if (includeLoops)
        {
            chunks.Add(MakeChunk("cue ", Cue()));
            chunks.Add(MakeChunk("LIST", ListAdtl()));
            chunks.Add(MakeChunk("smpl", Sampler(sampleRate)));
        }
        if (trailingJunk)
            chunks.Add(MakeChunk("JUNK", new byte[] { 1, 2, 3, 4, 5 }));

        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        writer.Write(Encoding.ASCII.GetBytes("RIFF"));
        writer.Write(4 + chunks.Sum(chunk => chunk.Length));
        writer.Write(Encoding.ASCII.GetBytes("WAVE"));
        foreach (byte[] chunk in chunks)
            writer.Write(chunk);
        return stream.ToArray();
    }

    internal static byte[] CreateAdpcm(int sampleRate, int channels, int blockAlign, int blocks, int paddingSamples)
    {
        int samplesPerBlock = ((blockAlign - 7 * channels) * 2 / channels) + 2;
        uint logicalSamples = checked((uint)(blocks * samplesPerBlock - paddingSamples));
        short[] coefficients = { 256, 0, 512, -256, 0, 0, 192, 64, 240, 0, 460, -208, 392, -232 };
        using var formatStream = new MemoryStream();
        using (var writer = new BinaryWriter(formatStream, Encoding.ASCII, leaveOpen: true))
        {
            writer.Write((ushort)2);
            writer.Write((ushort)channels);
            writer.Write(sampleRate);
            writer.Write(blockAlign * sampleRate / samplesPerBlock);
            writer.Write((ushort)blockAlign);
            writer.Write((ushort)4);
            writer.Write((ushort)32);
            writer.Write((ushort)samplesPerBlock);
            writer.Write((ushort)7);
            foreach (short coefficient in coefficients)
                writer.Write(coefficient);
        }
        byte[] fmt = MakeChunk("fmt ", formatStream.ToArray());
        byte[] fact = MakeChunk("fact", BitConverter.GetBytes(logicalSamples));
        byte[] data = MakeChunk("data", new byte[blockAlign * blocks]);
        using var stream = new MemoryStream();
        using var output = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        output.Write(Encoding.ASCII.GetBytes("RIFF"));
        output.Write(4 + fmt.Length + fact.Length + data.Length);
        output.Write(Encoding.ASCII.GetBytes("WAVE"));
        output.Write(fmt);
        output.Write(fact);
        output.Write(data);
        return stream.ToArray();
    }

    internal static byte[] Chunk(byte[] wave, string id)
    {
        int offset = FindFourCc(wave, id, 12);
        int size = BitConverter.ToInt32(wave, offset + 4);
        return wave.AsSpan(offset, 8 + size + (size & 1)).ToArray();
    }

    internal static byte[] ChunkData(byte[] wave, string id)
    {
        byte[] chunk = Chunk(wave, id);
        return chunk.AsSpan(8, BitConverter.ToInt32(chunk, 4)).ToArray();
    }

    internal static int FindFourCc(byte[] data, string id, int start = 0)
    {
        byte[] target = Encoding.ASCII.GetBytes(id);
        for (int i = start; i <= data.Length - 4; i++)
        {
            if (data.AsSpan(i, 4).SequenceEqual(target))
                return i;
        }
        throw new InvalidOperationException($"Chunk {id} not found.");
    }

    private static byte[] Format(int sampleRate)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream);
        writer.Write((ushort)1);
        writer.Write((ushort)1);
        writer.Write(sampleRate);
        writer.Write(sampleRate * 2);
        writer.Write((ushort)2);
        writer.Write((ushort)16);
        return stream.ToArray();
    }

    private static byte[] Cue()
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        writer.Write(1u);
        writer.Write(7u);
        writer.Write(1000u);
        writer.Write(Encoding.ASCII.GetBytes("data"));
        writer.Write(0u);
        writer.Write(0u);
        writer.Write(1000u);
        return stream.ToArray();
    }

    private static byte[] ListAdtl()
    {
        using var body = new MemoryStream();
        using (var writer = new BinaryWriter(body, Encoding.ASCII, leaveOpen: true))
        {
            writer.Write(Encoding.ASCII.GetBytes("adtl"));
            writer.Write(MakeChunk("labl", LabelBody()));
            writer.Write(MakeChunk("ltxt", LtxtBody()));
        }
        return body.ToArray();
    }

    private static byte[] LabelBody()
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        writer.Write(7u);
        writer.Write(Encoding.ASCII.GetBytes("loop\0"));
        return stream.ToArray();
    }

    private static byte[] LtxtBody()
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        writer.Write(7u);
        writer.Write(500u);
        writer.Write(0u);
        writer.Write((ushort)0);
        writer.Write((ushort)0);
        writer.Write((ushort)0);
        writer.Write((ushort)0);
        return stream.ToArray();
    }

    private static byte[] Sampler(int sampleRate)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        writer.Write(0u);
        writer.Write(0u);
        writer.Write((uint)Math.Round(1_000_000_000d / sampleRate));
        writer.Write(60u);
        writer.Write(0u);
        writer.Write(0u);
        writer.Write(0u);
        writer.Write(1u);
        writer.Write(0u);
        writer.Write(7u);
        writer.Write(0u);
        writer.Write(1000u);
        writer.Write(1500u);
        writer.Write(0u);
        writer.Write(0u);
        return stream.ToArray();
    }

    private static byte[] MakeChunk(string id, byte[] body)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        writer.Write(Encoding.ASCII.GetBytes(id));
        writer.Write(body.Length);
        writer.Write(body);
        if ((body.Length & 1) != 0)
            writer.Write((byte)0);
        return stream.ToArray();
    }
}
