using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace GmodAddonCompressor.Systems.Audio
{
    internal sealed class WaveLoopMetadata
    {
        private readonly byte[]? _cue;
        private readonly byte[]? _listAdtl;
        private readonly byte[]? _smpl;

        private WaveLoopMetadata(int sourceSampleRate, byte[]? cue, byte[]? listAdtl, byte[]? smpl)
        {
            SourceSampleRate = sourceSampleRate;
            _cue = cue;
            _listAdtl = listAdtl;
            _smpl = smpl;
        }

        internal int SourceSampleRate { get; }
        internal bool HasAny => _cue != null || _listAdtl != null || _smpl != null;

        internal static WaveLoopMetadata Capture(byte[] wave, int sourceSampleRate)
        {
            if (sourceSampleRate <= 0)
                throw new ArgumentOutOfRangeException(nameof(sourceSampleRate));
            if (!RiffWaveParser.TryParse(wave, out RiffWaveLayout? layout, out string reason))
                throw new InvalidDataException(reason);

            byte[]? cue = null;
            byte[]? listAdtl = null;
            byte[]? smpl = null;
            foreach (RiffChunk chunk in layout!.Chunks)
            {
                if (chunk.Id == "cue ")
                    cue = chunk.FullBytes(wave);
                else if (chunk.Id == "smpl")
                    smpl = chunk.FullBytes(wave);
                else if (chunk.Id == "LIST" && chunk.DataSize >= 4 &&
                    Encoding.ASCII.GetString(wave, chunk.DataOffset, 4) == "adtl")
                {
                    listAdtl = chunk.FullBytes(wave);
                }
            }

            return new WaveLoopMetadata(sourceSampleRate, cue, listAdtl, smpl);
        }

        internal static WaveLoopMetadata Capture(string path, int sourceSampleRate) =>
            Capture(File.ReadAllBytes(path), sourceSampleRate);

        internal byte[] Restore(byte[] encodedWave, int targetSampleRate)
        {
            if (targetSampleRate <= 0)
                throw new ArgumentOutOfRangeException(nameof(targetSampleRate));
            if (!HasAny)
                return encodedWave.ToArray();
            if (!RiffWaveParser.TryParse(encodedWave, out RiffWaveLayout? layout, out string reason))
                throw new InvalidDataException(reason);

            var preserved = layout!.Chunks.Where(chunk => !IsReplacedLoopChunk(encodedWave, chunk)).ToArray();
            using var output = new MemoryStream(encodedWave.Length + 1024);
            using var writer = new BinaryWriter(output, Encoding.ASCII, leaveOpen: true);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));
            writer.Write(0);
            writer.Write(Encoding.ASCII.GetBytes("WAVE"));

            foreach (RiffChunk chunk in preserved)
                writer.Write(chunk.FullBytes(encodedWave));
            WriteScaled(writer, _cue, "cue ", targetSampleRate);
            WriteScaled(writer, _listAdtl, "LIST", targetSampleRate);
            WriteScaled(writer, _smpl, "smpl", targetSampleRate);

            if (output.Length - 8 > uint.MaxValue)
                throw new InvalidDataException("RIFF output exceeds the 32-bit RIFF size limit.");
            output.Position = 4;
            writer.Write((uint)(output.Length - 8));
            return output.ToArray();
        }

        internal void RestoreFile(string encodedPath, int targetSampleRate)
        {
            byte[] restored = Restore(File.ReadAllBytes(encodedPath), targetSampleRate);
            File.WriteAllBytes(encodedPath, restored);
        }

        private static bool IsReplacedLoopChunk(byte[] wave, RiffChunk chunk)
        {
            if (chunk.Id == "cue " || chunk.Id == "smpl")
                return true;
            return chunk.Id == "LIST" && chunk.DataSize >= 4 &&
                Encoding.ASCII.GetString(wave, chunk.DataOffset, 4) == "adtl";
        }

        private void WriteScaled(BinaryWriter writer, byte[]? chunk, string id, int targetSampleRate)
        {
            if (chunk == null)
                return;
            byte[] copy = chunk.ToArray();
            if (SourceSampleRate != targetSampleRate)
            {
                if (id == "cue ")
                    ScaleCue(copy, SourceSampleRate, targetSampleRate);
                else if (id == "LIST")
                    ScaleListAdtl(copy, SourceSampleRate, targetSampleRate);
                else if (id == "smpl")
                    ScaleSmpl(copy, SourceSampleRate, targetSampleRate);
            }
            writer.Write(copy);
        }

        private static void ScaleCue(byte[] chunk, int sourceRate, int targetRate)
        {
            if (chunk.Length < 12)
                return;
            uint count = ReadUInt32(chunk, 8);
            for (uint index = 0; index < count; index++)
            {
                int entry = 12 + checked((int)index * 24);
                if (entry + 24 > chunk.Length)
                    break;
                WriteUInt32(chunk, entry + 4, ScaleSample(ReadUInt32(chunk, entry + 4), sourceRate, targetRate));
                WriteUInt32(chunk, entry + 20, ScaleSample(ReadUInt32(chunk, entry + 20), sourceRate, targetRate));
            }
        }

        private static void ScaleListAdtl(byte[] chunk, int sourceRate, int targetRate)
        {
            if (chunk.Length < 12 || Encoding.ASCII.GetString(chunk, 8, 4) != "adtl")
                return;
            int offset = 12;
            int limit = 8 + checked((int)ReadUInt32(chunk, 4));
            while (offset + 8 <= limit && offset + 8 <= chunk.Length)
            {
                string id = Encoding.ASCII.GetString(chunk, offset, 4);
                uint sizeValue = ReadUInt32(chunk, offset + 4);
                if (sizeValue > int.MaxValue)
                    break;
                int size = (int)sizeValue;
                int dataOffset = offset + 8;
                if (dataOffset + size > limit || dataOffset + size > chunk.Length)
                    break;
                if (id == "ltxt" && size >= 8)
                    WriteUInt32(chunk, dataOffset + 4, ScaleSample(ReadUInt32(chunk, dataOffset + 4), sourceRate, targetRate));
                offset = dataOffset + size + (size & 1);
            }
        }

        private static void ScaleSmpl(byte[] chunk, int sourceRate, int targetRate)
        {
            if (chunk.Length < 44)
                return;
            WriteUInt32(chunk, 16, (uint)Math.Round(1_000_000_000d / targetRate));
            uint loopCount = ReadUInt32(chunk, 36);
            for (uint index = 0; index < loopCount; index++)
            {
                int entry = 44 + checked((int)index * 24);
                if (entry + 24 > chunk.Length)
                    break;
                WriteUInt32(chunk, entry + 8, ScaleSample(ReadUInt32(chunk, entry + 8), sourceRate, targetRate));
                WriteUInt32(chunk, entry + 12, ScaleSample(ReadUInt32(chunk, entry + 12), sourceRate, targetRate));
            }
        }

        private static uint ScaleSample(uint value, int sourceRate, int targetRate)
        {
            double scaled = value * ((double)targetRate / sourceRate);
            return scaled >= uint.MaxValue ? uint.MaxValue : (uint)Math.Round(scaled);
        }

        private static uint ReadUInt32(byte[] data, int offset) => BitConverter.ToUInt32(data, offset);

        private static void WriteUInt32(byte[] data, int offset, uint value) =>
            Buffer.BlockCopy(BitConverter.GetBytes(value), 0, data, offset, 4);
    }

    internal static class RiffWaveValidator
    {
        internal static bool TryValidate(byte[] wave, out string reason)
        {
            if (!RiffWaveParser.TryParse(wave, out RiffWaveLayout? layout, out reason))
                return false;
            if (!layout!.Chunks.Any(chunk => chunk.Id == "fmt "))
            {
                reason = "RIFF/WAVE has no fmt chunk.";
                return false;
            }
            if (!layout.Chunks.Any(chunk => chunk.Id == "data"))
            {
                reason = "RIFF/WAVE has no data chunk.";
                return false;
            }
            reason = string.Empty;
            return true;
        }

        internal static bool TryValidateFile(string path, out string reason)
        {
            try
            {
                return TryValidate(File.ReadAllBytes(path), out reason);
            }
            catch (Exception ex)
            {
                reason = ex.Message;
                return false;
            }
        }

        internal static bool TryValidateEncoding(byte[] wave, AudioEncodingSpec spec, out string reason)
        {
            if (!TryValidate(wave, out reason))
                return false;
            if (!RiffWaveParser.TryParse(wave, out RiffWaveLayout? layout, out reason) ||
                !RiffWaveFormat.TryRead(wave, out RiffWaveFormat? format, out reason))
            {
                return false;
            }
            if (!format!.Matches(spec))
            {
                reason = $"WAV encoding does not match {spec.Id}.";
                return false;
            }

            RiffChunk data = layout!.Chunks.First(chunk => chunk.Id == "data");
            if (data.DataSize % spec.BlockAlign != 0)
            {
                reason = $"WAV data contains a partial {spec.BlockAlign}-byte block.";
                return false;
            }
            if (spec.Codec == AudioCodec.Pcm16)
            {
                reason = string.Empty;
                return true;
            }

            RiffChunk? fmt = layout.Chunks.FirstOrDefault(chunk => chunk.Id == "fmt ");
            if (fmt == null || fmt.DataSize < 22)
            {
                reason = "MS ADPCM fmt extension is truncated.";
                return false;
            }
            int samplesPerBlock = BitConverter.ToUInt16(wave, fmt.DataOffset + 18);
            int expectedSamplesPerBlock = ((spec.BlockAlign - 7 * spec.Channels) * 2 / spec.Channels) + 2;
            if (samplesPerBlock != expectedSamplesPerBlock)
            {
                reason = $"MS ADPCM samples-per-block {samplesPerBlock} does not match block geometry {expectedSamplesPerBlock}.";
                return false;
            }
            RiffChunk? fact = layout.Chunks.FirstOrDefault(chunk => chunk.Id == "fact");
            if (fact == null || fact.DataSize < 4)
            {
                reason = "MS ADPCM WAV has no logical sample count in a fact chunk.";
                return false;
            }
            ulong logicalSamples = BitConverter.ToUInt32(wave, fact.DataOffset);
            ulong blockCount = (ulong)data.DataSize / (uint)spec.BlockAlign;
            ulong fullBlockSamples = blockCount * (uint)samplesPerBlock;
            if (logicalSamples > fullBlockSamples || fullBlockSamples - logicalSamples >= (uint)samplesPerBlock)
            {
                reason = "MS ADPCM fact count has invalid block padding.";
                return false;
            }
            reason = string.Empty;
            return true;
        }

        internal static bool TryValidateEncodingFile(string path, AudioEncodingSpec spec, out string reason)
        {
            try
            {
                return TryValidateEncoding(File.ReadAllBytes(path), spec, out reason);
            }
            catch (Exception ex)
            {
                reason = ex.Message;
                return false;
            }
        }

        internal static bool TryValidateBaselineEncodingFile(string path, AudioEncodingSpec spec, out string reason)
        {
            try
            {
                byte[] wave = File.ReadAllBytes(path);
                if (!TryValidate(wave, out reason) ||
                    !RiffWaveFormat.TryRead(wave, out RiffWaveFormat? format, out reason))
                {
                    return false;
                }
                if (!format!.MatchesManual(spec))
                {
                    reason = $"Baseline WAV encoding does not match {spec.Codec}/{spec.SampleRate}/{spec.Channels}.";
                    return false;
                }
                if (format.BlockAlign <= 0)
                {
                    reason = "Baseline WAV has an invalid block alignment.";
                    return false;
                }
                var actualSpec = spec with { BlockAlign = format.BlockAlign };
                return TryValidateEncoding(wave, actualSpec, out reason);
            }
            catch (Exception ex)
            {
                reason = ex.Message;
                return false;
            }
        }
    }

    internal sealed record RiffChunk(string Id, int HeaderOffset, int DataOffset, int DataSize, int TotalSize)
    {
        internal byte[] FullBytes(byte[] wave) => wave.AsSpan(HeaderOffset, TotalSize).ToArray();
    }

    internal sealed record RiffWaveLayout(IReadOnlyList<RiffChunk> Chunks, int DeclaredEnd);

    internal static class RiffWaveDuration
    {
        internal static bool TryGetLogicalSampleCount(byte[] wave, out ulong samples, out string reason)
        {
            samples = 0;
            if (!RiffWaveParser.TryParse(wave, out RiffWaveLayout? layout, out reason) ||
                !RiffWaveFormat.TryRead(wave, out RiffWaveFormat? format, out reason))
            {
                return false;
            }

            if (format!.FormatTag == 2)
            {
                RiffChunk? fact = layout!.Chunks.FirstOrDefault(chunk => chunk.Id == "fact");
                if (fact == null || fact.DataSize < 4)
                {
                    reason = "MS ADPCM WAV has no logical sample count in a fact chunk.";
                    return false;
                }
                samples = BitConverter.ToUInt32(wave, fact.DataOffset);
                reason = string.Empty;
                return true;
            }

            RiffChunk? data = layout!.Chunks.FirstOrDefault(chunk => chunk.Id == "data");
            if (data == null || format.BlockAlign <= 0 || data.DataSize % format.BlockAlign != 0)
            {
                reason = "PCM WAV data is not aligned to complete sample frames.";
                return false;
            }
            samples = (ulong)data.DataSize / (uint)format.BlockAlign;
            reason = string.Empty;
            return true;
        }

        internal static bool TryGetLogicalSampleCountFile(string path, out ulong samples, out string reason)
        {
            try
            {
                return TryGetLogicalSampleCount(File.ReadAllBytes(path), out samples, out reason);
            }
            catch (Exception ex)
            {
                samples = 0;
                reason = ex.Message;
                return false;
            }
        }

        internal static bool TrySetAdpcmFactSampleCount(byte[] wave, ulong logicalSamples, out string reason)
        {
            if (logicalSamples > uint.MaxValue)
            {
                reason = "Logical WAV sample count exceeds the RIFF fact limit.";
                return false;
            }
            if (!RiffWaveParser.TryParse(wave, out RiffWaveLayout? layout, out reason) ||
                !RiffWaveFormat.TryRead(wave, out RiffWaveFormat? format, out reason))
            {
                return false;
            }
            if (format!.FormatTag != 2)
            {
                reason = "Only MS ADPCM WAV files use the adaptive fact patch.";
                return false;
            }
            RiffChunk? fact = layout!.Chunks.FirstOrDefault(chunk => chunk.Id == "fact");
            if (fact == null || fact.DataSize < 4)
            {
                reason = "MS ADPCM WAV has no writable fact chunk.";
                return false;
            }
            Buffer.BlockCopy(BitConverter.GetBytes((uint)logicalSamples), 0, wave, fact.DataOffset, 4);
            reason = string.Empty;
            return true;
        }

        internal static void SetAdpcmFactSampleCountFile(string path, ulong logicalSamples)
        {
            byte[] wave = File.ReadAllBytes(path);
            if (!TrySetAdpcmFactSampleCount(wave, logicalSamples, out string reason))
                throw new InvalidDataException(reason);
            File.WriteAllBytes(path, wave);
        }

        internal static ulong ScaleSampleCount(ulong samples, int sourceRate, int targetRate)
        {
            if (sourceRate <= 0)
                throw new ArgumentOutOfRangeException(nameof(sourceRate));
            if (targetRate <= 0)
                throw new ArgumentOutOfRangeException(nameof(targetRate));
            decimal scaled = (decimal)samples * targetRate / sourceRate;
            return checked((ulong)Math.Floor(scaled + 0.5m));
        }
    }

    internal static class RiffWaveParser
    {
        internal static bool TryParse(byte[] wave, out RiffWaveLayout? layout, out string reason)
        {
            layout = null;
            if (wave == null || wave.Length < 12)
            {
                reason = "RIFF/WAVE header is truncated.";
                return false;
            }
            if (Encoding.ASCII.GetString(wave, 0, 4) != "RIFF" || Encoding.ASCII.GetString(wave, 8, 4) != "WAVE")
            {
                reason = "File is not RIFF/WAVE.";
                return false;
            }

            uint declaredSize = BitConverter.ToUInt32(wave, 4);
            long declaredEndLong = 8L + declaredSize;
            if (declaredEndLong < 12 || declaredEndLong > wave.Length)
            {
                reason = "RIFF size exceeds the available bytes.";
                return false;
            }
            int declaredEnd = (int)declaredEndLong;
            var chunks = new List<RiffChunk>();
            int offset = 12;
            while (offset < declaredEnd)
            {
                if (offset + 8 > declaredEnd)
                {
                    reason = "RIFF chunk header is truncated.";
                    return false;
                }
                string id = Encoding.ASCII.GetString(wave, offset, 4);
                uint sizeValue = BitConverter.ToUInt32(wave, offset + 4);
                if (sizeValue > int.MaxValue)
                {
                    reason = $"RIFF chunk {id} is too large.";
                    return false;
                }
                int size = (int)sizeValue;
                long totalLong = 8L + size + (size & 1);
                if (offset + totalLong > declaredEnd)
                {
                    reason = $"RIFF chunk {id} exceeds the RIFF boundary.";
                    return false;
                }
                int total = (int)totalLong;
                chunks.Add(new RiffChunk(id, offset, offset + 8, size, total));
                offset += total;
            }

            layout = new RiffWaveLayout(chunks, declaredEnd);
            reason = string.Empty;
            return true;
        }
    }
}
