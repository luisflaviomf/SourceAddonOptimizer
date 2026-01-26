using GmodAddonCompressor.CustomExtensions;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Systems;
using Microsoft.Extensions.Logging;
using NAudio.Wave;
using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Objects
{
    internal class WAVEdit : ICompress
    {
        private readonly ILogger _logger = LogSystem.CreateLogger<WAVEdit>();

        private sealed class LoopChunks
        {
            public byte[]? Smpl;
            public byte[]? Cue;
            public byte[]? ListAdtl;
            public int SourceSampleRate;
            public int TargetSampleRate;
            public bool HasAny => Smpl != null || Cue != null || ListAdtl != null;
        }

        private sealed class LoopPresence
        {
            public bool HasSmpl;
            public bool HasCue;
            public bool HasAdtl;
            public bool HasAny => HasSmpl || HasCue || HasAdtl;
        }

        private sealed class WaveInfo
        {
            public int SampleRate;
            public int Channels;
            public WaveFormatEncoding Encoding;
            public int BitsPerSample;
        }

        public async Task Compress(string wavFilePath)
        {
            string newWavFilePath = wavFilePath + "____TEMP.wav";

            if (File.Exists(newWavFilePath))
                File.Delete(newWavFilePath);

            bool wavIsLooping = false;
            LoopChunks? loopChunks = null;

            try
            {
                using (var fs = new FileStream(wavFilePath, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    while (fs.Position < fs.Length)
                    {
                        long chunkStartPos = fs.Position;
                        string chunkId = ReadBytesToString(fs, 4);
                        uint chunkSize = ReadBytesToInt(fs, 4);
                        long chunkEndPos = chunkStartPos + chunkSize + 8;

                        switch (chunkId.ToUpper().Trim())
                        {
                            case "RIFF":
                                ReadBytes(fs, 4);
                                break;
                            case "SMPL":
                                wavIsLooping = true;
                                break;
                            case "CUE":
                                wavIsLooping = true;
                                break;
                            default:
                                fs.Position = chunkEndPos;
                                break;
                        }

                        if (wavIsLooping)
                            break;
                    }
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
            }

            try
            {
                if (!TryGetWaveInfo(wavFilePath, out var info))
                    return;

                if (!NeedsReencode(info))
                    return;

                if (wavIsLooping)
                {
                    if (AudioContext.PreserveLoopMetadata)
                        loopChunks = ExtractLoopChunks(wavFilePath, info.SampleRate, AudioContext.WavSampleRate);
                    else
                        _logger.LogWarning($"Loop metadata may be lost: {wavFilePath.GAC_ToLocalPath()}");
                }

                bool converted = await new FFMpegSystem().ReencodeWavAsync(
                    wavFilePath,
                    newWavFilePath,
                    AudioContext.WavSampleRate,
                    AudioContext.WavChannels,
                    AudioContext.WavCodec);

                if (!converted)
                {
                    _logger.LogError($"WAV compression failed: {wavFilePath.GAC_ToLocalPath()}");
                }
                else
                {
                    File.Delete(wavFilePath);
                    File.Move(newWavFilePath, wavFilePath);
                    _logger.LogInformation($"Successful WAV re-encode: {wavFilePath.GAC_ToLocalPath()}");

                    if (loopChunks != null && loopChunks.HasAny)
                    {
                        bool applied = ApplyLoopChunks(wavFilePath, loopChunks);
                        if (applied)
                            _logger.LogInformation($"Loop points restored: {wavFilePath.GAC_ToLocalPath()}");
                        else
                            _logger.LogWarning($"Loop points not restored: {wavFilePath.GAC_ToLocalPath()}");
                    }
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
            }
            finally
            {
                if (File.Exists(newWavFilePath))
                    File.Delete(newWavFilePath);
            }
        }

        private bool TryGetWaveInfo(string wavFilePath, out WaveInfo info)
        {
            info = new WaveInfo
            {
                SampleRate = AudioContext.WavSampleRate,
                Channels = AudioContext.WavChannels,
                Encoding = WaveFormatEncoding.Pcm,
                BitsPerSample = 16
            };

            try
            {
                using (var reader = new WaveFileReader(wavFilePath))
                {
                    info.SampleRate = reader.WaveFormat.SampleRate;
                    info.Channels = reader.WaveFormat.Channels;
                    info.Encoding = reader.WaveFormat.Encoding;
                    info.BitsPerSample = reader.WaveFormat.BitsPerSample;
                }
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex.ToString());
                return false;
            }

            return true;
        }

        private bool NeedsReencode(WaveInfo info)
        {
            int targetRate = AudioContext.WavSampleRate;
            int targetChannels = AudioContext.WavChannels;
            var targetCodec = AudioContext.WavCodec;

            bool isPcm16 = info.Encoding == WaveFormatEncoding.Pcm && info.BitsPerSample == 16;
            bool isAdpcm = info.Encoding == WaveFormatEncoding.Adpcm;

            if (info.SampleRate == targetRate && info.Channels == targetChannels)
            {
                if (targetCodec == AudioContext.WavCodecKind.Pcm16 && isPcm16)
                    return false;
                if (targetCodec == AudioContext.WavCodecKind.AdpcmMs && isAdpcm)
                    return false;
            }

            return true;
        }

        private static LoopChunks ExtractLoopChunks(string wavFilePath, int sourceSampleRate, int targetSampleRate)
        {
            var result = new LoopChunks();
            result.SourceSampleRate = sourceSampleRate;
            result.TargetSampleRate = targetSampleRate;

            try
            {
                using var fs = new FileStream(wavFilePath, FileMode.Open, FileAccess.Read, FileShare.Read);
                using var reader = new BinaryReader(fs, Encoding.ASCII, leaveOpen: true);

                if (!TryReadFourCC(reader, out var riff) || !riff.Equals("RIFF", StringComparison.OrdinalIgnoreCase))
                    return result;

                _ = reader.ReadInt32();

                if (!TryReadFourCC(reader, out var wave) || !wave.Equals("WAVE", StringComparison.OrdinalIgnoreCase))
                    return result;

                while (fs.Position + 8 <= fs.Length)
                {
                    if (!TryReadFourCC(reader, out var chunkId))
                        break;

                    int size = reader.ReadInt32();
                    long dataStart = fs.Position;
                    if (size < 0 || dataStart + size > fs.Length)
                        break;

                    if (chunkId.Equals("smpl", StringComparison.OrdinalIgnoreCase) ||
                        chunkId.Equals("cue ", StringComparison.OrdinalIgnoreCase) ||
                        chunkId.Equals("LIST", StringComparison.OrdinalIgnoreCase))
                    {
                        byte[] data = reader.ReadBytes(size);
                        if (data.Length < size)
                            break;

                        byte[] chunk = BuildChunk(chunkId, size, data, reader, size);

                        if (chunkId.Equals("smpl", StringComparison.OrdinalIgnoreCase))
                        {
                            if (sourceSampleRate != targetSampleRate)
                                AdjustSmplChunk(chunk, sourceSampleRate, targetSampleRate);
                            result.Smpl = chunk;
                        }
                        else if (chunkId.Equals("cue ", StringComparison.OrdinalIgnoreCase))
                        {
                            if (sourceSampleRate != targetSampleRate)
                                AdjustCueChunk(chunk, sourceSampleRate, targetSampleRate);
                            result.Cue = chunk;
                        }
                        else if (chunkId.Equals("LIST", StringComparison.OrdinalIgnoreCase) && size >= 4)
                        {
                            string listType = Encoding.ASCII.GetString(data, 0, 4);
                            if (listType.Equals("adtl", StringComparison.OrdinalIgnoreCase))
                                result.ListAdtl = chunk;
                        }
                    }
                    else
                    {
                        fs.Position = dataStart + size + (size % 2);
                    }
                }
            }
            catch
            {
                return result;
            }

            return result;
        }

        private static bool ApplyLoopChunks(string wavFilePath, LoopChunks chunks)
        {
            if (!chunks.HasAny)
                return false;

            try
            {
                var presence = ScanLoopPresence(wavFilePath);
                bool needsAny = (!presence.HasSmpl && chunks.Smpl != null) ||
                                (!presence.HasCue && chunks.Cue != null) ||
                                (!presence.HasAdtl && chunks.ListAdtl != null);

                if (!needsAny)
                    return false;

                using var fs = new FileStream(wavFilePath, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
                using var reader = new BinaryReader(fs, Encoding.ASCII, leaveOpen: true);

                if (!TryReadFourCC(reader, out var riff) || !riff.Equals("RIFF", StringComparison.OrdinalIgnoreCase))
                    return false;

                fs.Position = fs.Length;

                if (!presence.HasSmpl && chunks.Smpl != null)
                    fs.Write(chunks.Smpl, 0, chunks.Smpl.Length);
                if (!presence.HasCue && chunks.Cue != null)
                    fs.Write(chunks.Cue, 0, chunks.Cue.Length);
                if (!presence.HasAdtl && chunks.ListAdtl != null)
                    fs.Write(chunks.ListAdtl, 0, chunks.ListAdtl.Length);

                long newSize = fs.Length - 8;
                if (newSize > 0 && newSize <= int.MaxValue)
                {
                    fs.Position = 4;
                    byte[] sizeBytes = BitConverter.GetBytes((int)newSize);
                    fs.Write(sizeBytes, 0, sizeBytes.Length);
                }

                return true;
            }
            catch
            {
                return false;
            }
        }

        private static LoopPresence ScanLoopPresence(string wavFilePath)
        {
            var presence = new LoopPresence();

            try
            {
                using var fs = new FileStream(wavFilePath, FileMode.Open, FileAccess.Read, FileShare.Read);
                using var reader = new BinaryReader(fs, Encoding.ASCII, leaveOpen: true);

                if (!TryReadFourCC(reader, out var riff) || !riff.Equals("RIFF", StringComparison.OrdinalIgnoreCase))
                    return presence;

                _ = reader.ReadInt32();
                if (!TryReadFourCC(reader, out var wave) || !wave.Equals("WAVE", StringComparison.OrdinalIgnoreCase))
                    return presence;

                while (fs.Position + 8 <= fs.Length)
                {
                    if (!TryReadFourCC(reader, out var chunkId))
                        break;

                    int size = reader.ReadInt32();
                    long dataStart = fs.Position;
                    if (size < 0 || dataStart + size > fs.Length)
                        break;

                    if (chunkId.Equals("smpl", StringComparison.OrdinalIgnoreCase))
                        presence.HasSmpl = true;
                    else if (chunkId.Equals("cue ", StringComparison.OrdinalIgnoreCase))
                        presence.HasCue = true;
                    else if (chunkId.Equals("LIST", StringComparison.OrdinalIgnoreCase) && size >= 4)
                    {
                        byte[] listTypeBytes = reader.ReadBytes(4);
                        if (listTypeBytes.Length == 4)
                        {
                            string listType = Encoding.ASCII.GetString(listTypeBytes);
                            if (listType.Equals("adtl", StringComparison.OrdinalIgnoreCase))
                                presence.HasAdtl = true;
                        }

                        fs.Position = dataStart + size + (size % 2);
                        continue;
                    }

                    fs.Position = dataStart + size + (size % 2);
                }
            }
            catch
            {
                return presence;
            }

            return presence;
        }

        private static bool TryReadFourCC(BinaryReader reader, out string id)
        {
            id = string.Empty;
            byte[] bytes = reader.ReadBytes(4);
            if (bytes.Length < 4)
                return false;
            id = Encoding.ASCII.GetString(bytes);
            return true;
        }

        private static byte[] BuildChunk(string chunkId, int size, byte[] data, BinaryReader reader, int dataSize)
        {
            int pad = dataSize % 2;
            byte[] chunk = new byte[8 + dataSize + pad];
            Encoding.ASCII.GetBytes(chunkId).CopyTo(chunk, 0);
            BitConverter.GetBytes(size).CopyTo(chunk, 4);
            Buffer.BlockCopy(data, 0, chunk, 8, dataSize);
            if (pad == 1)
            {
                int padByte = reader.ReadByte();
                chunk[8 + dataSize] = padByte == -1 ? (byte)0 : (byte)padByte;
            }
            return chunk;
        }

        private static void AdjustSmplChunk(byte[] chunk, int sourceSampleRate, int targetSampleRate)
        {
            if (chunk.Length < 8 + 36)
                return;

            int dataOffset = 8;
            uint samplePeriod = (uint)Math.Round(1_000_000_000.0 / targetSampleRate);
            WriteUInt32(chunk, dataOffset + 8, samplePeriod);

            uint loopCount = ReadUInt32(chunk, dataOffset + 28);
            int loopOffset = dataOffset + 36;
            double scale = (double)targetSampleRate / sourceSampleRate;

            for (uint i = 0; i < loopCount; i++)
            {
                int entryOffset = loopOffset + (int)i * 24;
                if (entryOffset + 16 > chunk.Length)
                    break;

                uint start = ReadUInt32(chunk, entryOffset + 8);
                uint end = ReadUInt32(chunk, entryOffset + 12);

                uint newStart = ScaleSample(start, scale);
                uint newEnd = ScaleSample(end, scale);

                WriteUInt32(chunk, entryOffset + 8, newStart);
                WriteUInt32(chunk, entryOffset + 12, newEnd);
            }
        }

        private static void AdjustCueChunk(byte[] chunk, int sourceSampleRate, int targetSampleRate)
        {
            if (chunk.Length < 12)
                return;

            int dataOffset = 8;
            uint cueCount = ReadUInt32(chunk, dataOffset);
            int entryOffset = dataOffset + 4;
            double scale = (double)targetSampleRate / sourceSampleRate;

            for (uint i = 0; i < cueCount; i++)
            {
                int baseOffset = entryOffset + (int)i * 24;
                if (baseOffset + 24 > chunk.Length)
                    break;

                uint position = ReadUInt32(chunk, baseOffset + 4);
                uint sampleOffset = ReadUInt32(chunk, baseOffset + 20);

                WriteUInt32(chunk, baseOffset + 4, ScaleSample(position, scale));
                WriteUInt32(chunk, baseOffset + 20, ScaleSample(sampleOffset, scale));
            }
        }

        private static uint ScaleSample(uint value, double scale)
        {
            return (uint)Math.Round(value * scale);
        }

        private static uint ReadUInt32(byte[] data, int offset)
        {
            if (offset + 4 > data.Length)
                return 0;
            return BitConverter.ToUInt32(data, offset);
        }

        private static void WriteUInt32(byte[] data, int offset, uint value)
        {
            if (offset + 4 > data.Length)
                return;
            byte[] bytes = BitConverter.GetBytes(value);
            Buffer.BlockCopy(bytes, 0, data, offset, 4);
        }

        private uint ReadBytesToInt(FileStream fs, int byteNum)
        {
            return BitConverter.ToUInt32(ReadBytes(fs, byteNum), 0);
        }

        private string ReadBytesToString(FileStream fs, int byteNum)
        {
            return System.Text.Encoding.UTF8.GetString(ReadBytes(fs, byteNum)).Trim('\0');
        }

        private byte[] ReadBytes(FileStream fs, int num)
        {
            byte[] bytes = new byte[num < 4 ? 4 : num];
            for (int i = 0; i < num; i++)
            {
                int b = fs.ReadByte();
                bytes[i] = (byte)b;
            }
            return bytes;
        }
    }
}
