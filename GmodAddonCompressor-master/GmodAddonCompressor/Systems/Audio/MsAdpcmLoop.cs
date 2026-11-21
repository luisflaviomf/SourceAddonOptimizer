using System;
using System.IO;
using System.Linq;
using System.Text;

namespace GmodAddonCompressor.Systems.Audio
{
    /// <summary>
    /// Writing of looped MS-ADPCM WAVs so that Garry's Mod plays the loop as the PCM original does.
    ///
    /// Measured in the game (docs/sons-2026-11.md, client 2026.04.29), the engine does two things with an
    /// MS-ADPCM sound that has a loop mark (cue or smpl):
    ///  1. it plays the whole data chunk, so the padding of the last block is heard on every pass
    ///     (the fact chunk is not used);
    ///  2. at the restart it divides the loop start, a sample position, by the block size in BYTES:
    ///     restart = (mark / blockAlign) * samplesPerBlock + (mark % blockAlign).
    ///     A mono loop that does not start at zero restarts at about twice the position, and when that is
    ///     past the end the sound plays once and stops.
    /// Both are answered in the file, which stays standard MS-ADPCM: the data ends in a short last block
    /// that holds exactly the last sample, and the mark is written as the value the engine turns into the
    /// real loop start. A mono block reaches only its first blockAlign samples, so another block size is
    /// used when the standard one cannot reach the loop start.
    /// </summary>
    internal static class MsAdpcmLoop
    {
        // bytes per channel; 1024 is the official geometry, the others were played by the game as loops
        private static readonly int[] BlockBytesPerChannel = { 1024, 2048, 512 };

        internal static int SamplesPerBlock(int blockAlign, int channels) =>
            ((blockAlign - 7 * channels) * 2 / channels) + 2;

        /// <summary>Where the engine restarts a loop whose mark holds <paramref name="mark"/>.</summary>
        internal static long EngineRestart(long mark, int blockAlign, int channels) =>
            (mark / blockAlign) * SamplesPerBlock(blockAlign, channels) + (mark % blockAlign);

        /// <summary>The mark that makes the engine restart at <paramref name="loopStart"/>, when this block size reaches it.</summary>
        internal static bool TryCompensate(long loopStart, int blockAlign, int channels, out uint mark)
        {
            mark = 0;
            if (loopStart < 0 || blockAlign <= 7 * channels)
                return false;
            int samplesPerBlock = SamplesPerBlock(blockAlign, channels);
            long block = loopStart / samplesPerBlock;
            long inside = loopStart % samplesPerBlock;
            long value = block * blockAlign + inside;
            if (inside >= blockAlign || value > uint.MaxValue)
                return false;
            mark = (uint)value;
            return true;
        }

        /// <summary>Block size (bytes, all channels) that reaches the loop start, the official one first; 0 when none does.</summary>
        internal static int ChooseBlockAlign(long loopStart, int channels)
        {
            foreach (int perChannel in BlockBytesPerChannel)
            {
                int blockAlign = perChannel * channels;
                if (TryCompensate(loopStart, blockAlign, channels, out _))
                    return blockAlign;
            }
            return 0;
        }

        /// <summary>Samples a decoder (and the engine) gets from a data chunk that may end in a short block.</summary>
        internal static ulong DecodableSamples(int dataBytes, int blockAlign, int channels)
        {
            int samplesPerBlock = SamplesPerBlock(blockAlign, channels);
            ulong samples = (ulong)(dataBytes / blockAlign) * (ulong)samplesPerBlock;
            int rest = dataBytes % blockAlign;
            if (rest >= 7 * channels)
                samples += (ulong)(2 + (rest - 7 * channels) * 2 / channels);
            return samples;
        }

        /// <summary>
        /// The same sound with the padded last block cut to the bytes that hold <paramref name="logicalSamples"/>
        /// (a mono block holds an even count: an odd sound keeps one sample more) and the fact chunk set to it.
        /// </summary>
        internal static byte[] TruncateLastBlock(byte[] wave, ulong logicalSamples)
        {
            if (!RiffWaveParser.TryParse(wave, out RiffWaveLayout? layout, out string reason) ||
                !RiffWaveFormat.TryRead(wave, out RiffWaveFormat? format, out reason))
            {
                throw new InvalidDataException(reason);
            }
            if (format!.FormatTag != 2 || format.Channels < 1 || format.BlockAlign <= 7 * format.Channels)
                throw new InvalidDataException("Only MS ADPCM WAV files end in a short block.");
            RiffChunk? data = layout!.Chunks.FirstOrDefault(chunk => chunk.Id == "data");
            RiffChunk? fact = layout.Chunks.FirstOrDefault(chunk => chunk.Id == "fact");
            if (data == null || fact == null || fact.DataSize < 4 || logicalSamples > uint.MaxValue)
                throw new InvalidDataException("MS ADPCM WAV has no data or fact chunk to shorten.");

            int channels = format.Channels;
            int samplesPerBlock = SamplesPerBlock(format.BlockAlign, channels);
            ulong fullBlocks = logicalSamples / (ulong)samplesPerBlock;
            ulong rest = logicalSamples % (ulong)samplesPerBlock;
            ulong keep = fullBlocks * (ulong)format.BlockAlign;
            if (rest > 0)
            {
                ulong nibbles = (rest > 2 ? rest - 2 : 0) * (ulong)channels;
                keep += (ulong)(7 * channels) + (nibbles + 1) / 2;
            }
            if (keep > (ulong)data.DataSize)
                throw new InvalidDataException("MS ADPCM data is shorter than its logical sample count.");

            using var output = new MemoryStream(wave.Length);
            using var writer = new BinaryWriter(output, Encoding.ASCII, leaveOpen: true);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));
            writer.Write(0);
            writer.Write(Encoding.ASCII.GetBytes("WAVE"));
            foreach (RiffChunk chunk in layout.Chunks)
            {
                if (ReferenceEquals(chunk, data))
                {
                    writer.Write(Encoding.ASCII.GetBytes("data"));
                    writer.Write((uint)keep);
                    writer.Write(wave, data.DataOffset, (int)keep);
                    if ((keep & 1) != 0)
                        writer.Write((byte)0);
                }
                else if (ReferenceEquals(chunk, fact))
                {
                    byte[] bytes = chunk.FullBytes(wave);
                    Buffer.BlockCopy(BitConverter.GetBytes((uint)logicalSamples), 0, bytes, 8, 4);
                    writer.Write(bytes);
                }
                else
                {
                    writer.Write(chunk.FullBytes(wave));
                }
            }
            output.Position = 4;
            writer.Write((uint)(output.Length - 8));
            return output.ToArray();
        }

        /// <summary>
        /// Turns the encoder's file (padded last block, no loop marks) into the looped file the engine needs:
        /// the data ends at the last sample and the loop mark holds the value the engine turns into the real
        /// loop start. The file on disk is replaced only when the result is valid; throws otherwise.
        /// </summary>
        internal static void WriteLoopedFile(string path, AudioEncodingSpec spec, WaveLoopMetadata loopMetadata, ulong logicalSamples)
        {
            if (!loopMetadata.TryGetEngineLoopStart(spec.SampleRate, out long loopStart) ||
                !TryCompensate(loopStart, spec.BlockAlign, spec.Channels, out uint mark))
            {
                throw new InvalidDataException($"MS ADPCM block size {spec.BlockAlign} does not reach the loop start.");
            }
            byte[] wave = TruncateLastBlock(File.ReadAllBytes(path), logicalSamples);
            wave = loopMetadata.Restore(wave, spec.SampleRate, mark);
            if (!RiffWaveValidator.TryValidateEncoding(wave, spec, allowShortLastBlock: true, out string reason))
                throw new InvalidDataException(reason);
            File.WriteAllBytes(path, wave);
        }
    }
}
