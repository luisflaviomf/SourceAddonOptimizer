using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace GmodAddonCompressor.Systems.Optimizer;

internal static class FullCompressionWav
{
    private sealed record Wave(Dictionary<string, byte[]> Chunks, int Rate, int Channels, int Frames);

    private static Wave Read(byte[] data)
    {
        if (data.Length < 12 || Encoding.ASCII.GetString(data, 0, 4) != "RIFF" ||
            Encoding.ASCII.GetString(data, 8, 4) != "WAVE" || BitConverter.ToUInt32(data, 4) != data.Length - 8)
            throw new IOException("Contrato WAV: RIFF truncado/inválido.");
        var chunks = new Dictionary<string, byte[]>();
        int cursor = 12;
        while (cursor < data.Length)
        {
            if (cursor + 8 > data.Length) throw new IOException("Contrato WAV: chunk truncado.");
            string tag = Encoding.ASCII.GetString(data, cursor, 4);
            uint size = BitConverter.ToUInt32(data, cursor + 4);
            long end = cursor + 8L + size;
            if (end + size % 2 > data.Length) throw new IOException("Contrato WAV: extensão inválida.");
            byte[] payload = data.AsSpan(cursor + 8, (int)size).ToArray();
            if (tag == "LIST" && size >= 4) tag += Encoding.ASCII.GetString(payload, 0, 4);
            if (!chunks.TryAdd(tag, payload)) throw new IOException("Contrato WAV: chunk duplicado.");
            cursor = (int)(end + size % 2);
        }
        if (!chunks.TryGetValue("fmt ", out var fmt) || fmt.Length < 16 || !chunks.TryGetValue("data", out var audio))
            throw new IOException("Contrato WAV: fmt/data ausente.");
        int channels = BitConverter.ToUInt16(fmt, 2), rate = BitConverter.ToInt32(fmt, 4);
        if (BitConverter.ToUInt16(fmt, 0) != 1 || BitConverter.ToUInt16(fmt, 14) != 16 ||
            channels is < 1 or > 2 || rate <= 0 || BitConverter.ToUInt16(fmt, 12) != channels * 2 ||
            BitConverter.ToInt32(fmt, 8) != rate * channels * 2 || audio.Length == 0 || audio.Length % (channels * 2) != 0)
            throw new IOException("Contrato WAV: não é PCM16 canônico.");
        return new(chunks, rate, channels, audio.Length / (channels * 2));
    }

    internal static bool HasSupportedMetadata(byte[] source)
    {
        var wave = Read(source);
        string[] allowed = { "fmt ", "data", "smpl", "cue ", "LISTadtl", "LISTINFO", "JUNK", "PAD ", "fact" };
        return wave.Chunks.Keys.All(allowed.Contains);
    }

    internal static void Validate(byte[] source, byte[] target)
    {
        Wave a = Read(source), b = Read(target);
        if (a.Rate != 44100 || b.Rate != 22050 || a.Channels != b.Channels ||
            Math.Abs(a.Frames / 2.0 - b.Frames) > 1)
            throw new IOException("Contrato WAV: taxa/canais/duração divergentes.");
        foreach (string tag in new[] { "smpl", "cue ", "LISTadtl" })
        {
            bool original = a.Chunks.TryGetValue(tag, out var old), final = b.Chunks.TryGetValue(tag, out var current);
            if (original != final) throw new IOException("Contrato WAV: metadado de loop perdido: " + tag);
            if (!original) continue;
            byte[] expected = (byte[])old!.Clone();
            void Scale(int offset)
            {
                uint value = BitConverter.ToUInt32(expected, offset);
                BitConverter.GetBytes((uint)Math.Round(value / 2.0)).CopyTo(expected, offset);
            }
            if (tag == "smpl")
            {
                if (expected.Length < 36) throw new IOException("Contrato WAV: smpl truncado.");
                uint count = BitConverter.ToUInt32(expected, 28);
                if (36L + 24L * count > expected.Length) throw new IOException("Contrato WAV: loops truncados.");
                BitConverter.GetBytes((uint)Math.Round(1e9 / 22050)).CopyTo(expected, 8);
                for (int i = 0; i < count; i++) { Scale(36 + 24 * i + 8); Scale(36 + 24 * i + 12); }
            }
            else if (tag == "cue ")
            {
                if (expected.Length < 4 || 4L + 24L * BitConverter.ToUInt32(expected, 0) != expected.Length)
                    throw new IOException("Contrato WAV: cue truncado.");
                for (int i = 4; i < expected.Length; i += 24) { Scale(i + 4); Scale(i + 20); }
            }
            if (!expected.SequenceEqual(current!)) throw new IOException("Contrato WAV: loop alterado: " + tag);
        }
    }
}
