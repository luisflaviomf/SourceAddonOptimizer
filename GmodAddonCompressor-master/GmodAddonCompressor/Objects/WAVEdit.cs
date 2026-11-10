using GmodAddonCompressor.CustomExtensions;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Audio;
using GmodAddonCompressor.Systems.Tools;
using Microsoft.Extensions.Logging;
using NAudio.Wave;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Objects
{
    internal class WAVEdit : ICompress, ICompressPreparation, ICompressFinalizer, ICompressBatchSelective
    {
        // Sounds in flight in the adaptive batch: enough for their ffmpeg steps to share processes, bounded by
        // the decoded audio they hold (about 2 bytes of float per byte of 16-bit sound, for the original, the
        // baseline and each candidate).
        private const int AdaptiveBatchMaxSounds = 128;
        private const long AdaptiveBatchMaxSourceBytes = 128L * 1024 * 1024;

        private readonly ILogger _logger = LogSystem.CreateLogger<WAVEdit>();
        private readonly FFMpegSystem _ffmpegSystem;
        private readonly AdaptiveAudioOptimizer _adaptiveOptimizer;
        private readonly BatchedFfmpegAdaptiveAudioBackend _adaptiveBackend;
        private readonly Lazy<AudioWebSafetyReport> _webAudioSafetyReport;
        private readonly ConcurrentBag<AdaptiveWavFileRecord> _adaptiveRecords = new();
        private readonly Stopwatch _adaptiveWallClock = new();

        /// <summary>Report of the latest adaptive WAV run (under %LOCALAPPDATA%\GmodAddonOptimizer\work\audio).</summary>
        internal static string? LastAdaptiveReportPath { get; private set; }

        private sealed record AdaptiveWavFileRecord(string RelativePath, AdaptiveAudioResult Result);

        public WAVEdit()
        {
            _ffmpegSystem = new FFMpegSystem();
            _adaptiveBackend = new BatchedFfmpegAdaptiveAudioBackend(_ffmpegSystem);
            _adaptiveOptimizer = new AdaptiveAudioOptimizer(_adaptiveBackend);
            string addonRoot = CompressDirectoryContext.DirectoryPath;
            _webAudioSafetyReport = new Lazy<AudioWebSafetyReport>(
                () => AudioWebSafetyClassifier.Analyze(addonRoot),
                isThreadSafe: true);
        }

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

        private sealed class LoopMetadataSummary
        {
            public bool HasSmpl;
            public int CueCount;
            public List<string> Labels { get; } = new List<string>();
            public bool HasAny => HasSmpl || CueCount > 0;
            public bool IsSingleCueLoopLabel =>
                !HasSmpl &&
                CueCount == 1 &&
                Labels.Count == 1 &&
                Labels[0].Equals("loop", StringComparison.OrdinalIgnoreCase);
        }

        private sealed class WaveInfo
        {
            public int SampleRate;
            public int Channels;
            public WaveFormatEncoding Encoding;
            public int BitsPerSample;
        }

        public void Prepare()
        {
            _ = _webAudioSafetyReport.Value;
            if (AudioContext.WavOptimization == AudioContext.WavOptimizationKind.AdaptiveConservative)
                _adaptiveWallClock.Restart();
        }

        /// <summary>The adaptive profile takes the whole list: the sounds run together and share ffmpeg processes.</summary>
        public bool UseBatch => AudioContext.WavOptimization == AudioContext.WavOptimizationKind.AdaptiveConservative;

        public Action<FileInfo>? FileCompleted { get; set; }

        public async Task CompressBatchAsync(IReadOnlyList<FileInfo> files, System.Threading.CancellationToken cancellationToken)
        {
            // biggest first: a long sound does not end up alone at the end of the batch
            FileInfo[] ordered = files.OrderByDescending(file => file.Exists ? file.Length : 0).ToArray();
            long bytesInFlight = 0;
            int soundsInFlight = 0;
            var gate = new object();
            var slotFreed = new System.Threading.SemaphoreSlim(0);
            var running = new List<Task>(ordered.Length);
            foreach (FileInfo file in ordered)
            {
                long weight = Math.Min(file.Exists ? file.Length : 0, AdaptiveBatchMaxSourceBytes);
                while (true)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    lock (gate)
                    {
                        if (soundsInFlight == 0 ||
                            (soundsInFlight < AdaptiveBatchMaxSounds && bytesInFlight + weight <= AdaptiveBatchMaxSourceBytes))
                        {
                            soundsInFlight++;
                            bytesInFlight += weight;
                            break;
                        }
                    }
                    await slotFreed.WaitAsync(cancellationToken).ConfigureAwait(false);
                }

                running.Add(Task.Run(async () =>
                {
                    try
                    {
                        await Compress(file.FullName).ConfigureAwait(false);
                    }
                    catch (Exception ex)
                    {
                        _logger.LogError(ex.ToString());
                    }
                    finally
                    {
                        lock (gate)
                        {
                            soundsInFlight--;
                            bytesInFlight -= weight;
                        }
                        slotFreed.Release();
                        try { FileCompleted?.Invoke(file); } catch { }
                    }
                }));
            }
            await Task.WhenAll(running).ConfigureAwait(false);
        }

        public async Task Compress(string wavFilePath)
        {
            if (AudioContext.WavOptimization == AudioContext.WavOptimizationKind.AdaptiveConservative)
            {
                await CompressAdaptive(wavFilePath).ConfigureAwait(false);
                return;
            }

            await CompressManual(wavFilePath).ConfigureAwait(false);
        }

        private async Task CompressAdaptive(string wavFilePath)
        {
            try
            {
                if (!TryGetWaveInfo(wavFilePath, out WaveInfo info))
                    return;

                LoopMetadataSummary loopMetadata = AnalyzeLoopMetadata(wavFilePath);
                int targetSampleRate = AudioContext.WavSampleRate;
                int targetChannels = AudioContext.WavChannels;
                AudioContext.WavCodecKind targetCodec = GetTargetCodec(wavFilePath);
                if (AudioContext.PreserveLoopMetadata && loopMetadata.IsSingleCueLoopLabel)
                {
                    targetCodec = AudioContext.WavCodecKind.Pcm16;
                    targetChannels = info.Channels;
                }

                AudioCodec codec = targetCodec == AudioContext.WavCodecKind.AdpcmMs
                    ? AudioCodec.MsAdpcm
                    : AudioCodec.Pcm16;
                int blockAlign = codec == AudioCodec.MsAdpcm
                    ? (targetChannels == 1 ? 1024 : 2048)
                    : targetChannels * 2;
                var options = new AdaptiveAudioOptions(
                    new AudioEncodingSpec(codec, targetSampleRate, targetChannels, blockAlign),
                    AudioContext.PreserveLoopMetadata);
                AdaptiveAudioResult result = await _adaptiveOptimizer
                    .OptimizeAsync(wavFilePath, options)
                    .ConfigureAwait(false);

                string relativePath = wavFilePath.GAC_ToLocalPath();
                if (!result.BaselineCreated || !result.Replaced)
                {
                    _logger.LogWarning($"Adaptive WAV could not establish its baseline; using manual path: {relativePath}");
                    await CompressManual(wavFilePath).ConfigureAwait(false);
                    long manualBytes = File.Exists(wavFilePath) ? new FileInfo(wavFilePath).Length : result.OriginalBytes;
                    result = result with
                    {
                        Replaced = File.Exists(wavFilePath),
                        BaselineCreated = File.Exists(wavFilePath),
                        UsedBaselineFallback = true,
                        BaselineBytes = manualBytes,
                        OutputBytes = manualBytes,
                        Note = result.Note + " Manual fallback path completed."
                    };
                }

                _adaptiveRecords.Add(new AdaptiveWavFileRecord(relativePath, result));
                FFMpegSystem.AppendAudioLog(
                    $"WAV-ADAPTIVE | {wavFilePath} | selected={result.SelectedSpec.Id} | " +
                    $"original={result.OriginalBytes} | baseline={result.BaselineBytes} | output={result.OutputBytes} | " +
                    $"candidates={result.Candidates.Count} | elapsed_ms={result.Elapsed.TotalMilliseconds:0} | {result.Note}");
                string selection = result.UsedBaselineFallback ? "manual baseline" : result.SelectedSpec.Id;
                _logger.LogInformation(
                    $"Adaptive WAV selected {selection}: {relativePath} " +
                    $"({result.BaselineBytes} -> {result.OutputBytes} bytes, {result.Elapsed.TotalMilliseconds:0} ms)");
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                await CompressManual(wavFilePath).ConfigureAwait(false);
            }
        }

        private async Task CompressManual(string wavFilePath)
        {
            string newWavFilePath = wavFilePath + "____TEMP.wav";

            if (File.Exists(newWavFilePath))
                File.Delete(newWavFilePath);

            var loopMetadata = AnalyzeLoopMetadata(wavFilePath);
            bool wavIsLooping = loopMetadata.HasAny;
            LoopChunks? loopChunks = null;

            try
            {
                if (!TryGetWaveInfo(wavFilePath, out var info))
                    return;

                int targetSampleRate = AudioContext.WavSampleRate;
                int targetChannels = AudioContext.WavChannels;
                AudioContext.WavCodecKind targetCodec = GetTargetCodec(wavFilePath);

                if (AudioContext.PreserveLoopMetadata && loopMetadata.IsSingleCueLoopLabel)
                {
                    targetCodec = AudioContext.WavCodecKind.Pcm16;
                    targetChannels = info.Channels;

                    string localPath = wavFilePath.GAC_ToLocalPath();
                    _logger.LogInformation($"Protecting single-cue loop WAV with PCM16/original channels: {localPath}");
                    FFMpegSystem.AppendAudioLog(
                        $"WAV-SINGLE-CUE-LOOP-PROTECT | {wavFilePath} | forcing pcm_s16le | keep-channels={targetChannels} | sample-rate={targetSampleRate}");
                }

                if (!NeedsReencode(info, targetSampleRate, targetChannels, targetCodec))
                    return;

                if (wavIsLooping)
                {
                    if (AudioContext.PreserveLoopMetadata)
                        loopChunks = ExtractLoopChunks(wavFilePath, info.SampleRate, targetSampleRate);
                    else
                        _logger.LogWarning($"Loop metadata may be lost: {wavFilePath.GAC_ToLocalPath()}");
                }

                bool converted = await _ffmpegSystem.ReencodeWavAsync(
                    wavFilePath,
                    newWavFilePath,
                    targetSampleRate,
                    targetChannels,
                    targetCodec);

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

        public Task CompleteAsync()
        {
            if (AudioContext.WavOptimization != AudioContext.WavOptimizationKind.AdaptiveConservative)
                return Task.CompletedTask;

            _adaptiveWallClock.Stop();
            (long ffmpegProcesses, long ffmpegSteps, long redoneSteps) = _adaptiveBackend.Statistics;
            _adaptiveBackend.Dispose();
            AdaptiveWavFileRecord[] records = _adaptiveRecords
                .OrderBy(record => record.RelativePath, StringComparer.OrdinalIgnoreCase)
                .ToArray();
            long originalBytes = records.Sum(record => record.Result.OriginalBytes);
            long baselineBytes = records.Sum(record => record.Result.BaselineBytes);
            long outputBytes = records.Sum(record => record.Result.OutputBytes);
            int selectedCandidates = records.Count(record => !record.Result.UsedBaselineFallback && record.Result.Replaced);
            int baselineFallbacks = records.Count(record => record.Result.UsedBaselineFallback);
            // The report never goes inside the addon: gmad rejects loose files at the addon root.
            string reportDirectory = Path.Combine(ToolPaths.WorkRoot, "audio");
            string reportPath = Path.Combine(
                reportDirectory,
                $"adaptive_audio_{DateTime.Now:yyyyMMdd_HHmmss}_{Guid.NewGuid():N}.json");
            LastAdaptiveReportPath = reportPath;

            try
            {
                Directory.CreateDirectory(reportDirectory);
                var report = new
                {
                    SchemaVersion = 1,
                    Profile = "adaptive-conservative",
                    Baseline = new
                    {
                        SampleRate = AudioContext.WavSampleRate,
                        Channels = AudioContext.WavChannels,
                        Codec = AudioContext.WavCodec.ToString()
                    },
                    PreserveLoopMetadata = AudioContext.PreserveLoopMetadata,
                    FileCount = records.Length,
                    SelectedCandidateCount = selectedCandidates,
                    BaselineFallbackCount = baselineFallbacks,
                    OriginalBytes = originalBytes,
                    BaselineBytes = baselineBytes,
                    OutputBytes = outputBytes,
                    SavedVsBaselineBytes = baselineBytes - outputBytes,
                    SavedVsBaselinePercent = baselineBytes == 0 ? 0 : 100.0 * (baselineBytes - outputBytes) / baselineBytes,
                    WallClockMilliseconds = _adaptiveWallClock.Elapsed.TotalMilliseconds,
                    SumFileMilliseconds = records.Sum(record => record.Result.Elapsed.TotalMilliseconds),
                    FfmpegProcesses = ffmpegProcesses,
                    FfmpegSteps = ffmpegSteps,
                    FfmpegStepsRedoneAlone = redoneSteps,
                    Files = records
                };
                File.WriteAllText(
                    reportPath,
                    JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }),
                    Encoding.UTF8);
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex.ToString());
            }

            _logger.LogInformation(
                $"Adaptive WAV summary: {records.Length} files, {selectedCandidates} candidates selected, " +
                $"{baselineBytes} -> {outputBytes} bytes versus manual baseline " +
                $"({baselineBytes - outputBytes} saved), wall {_adaptiveWallClock.Elapsed}. Report: {reportPath}");
            FFMpegSystem.AppendAudioLog(
                $"WAV-ADAPTIVE-SUMMARY | files={records.Length} | selected={selectedCandidates} | " +
                $"fallbacks={baselineFallbacks} | baseline={baselineBytes} | output={outputBytes} | " +
                $"saved={baselineBytes - outputBytes} | wall_ms={_adaptiveWallClock.Elapsed.TotalMilliseconds:0} | report={reportPath}");
            return Task.CompletedTask;
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

        private bool NeedsReencode(WaveInfo info, int targetRate, int targetChannels, AudioContext.WavCodecKind targetCodec)
        {
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

        private AudioContext.WavCodecKind GetTargetCodec(string wavFilePath)
        {
            if (AudioContext.WavCodec != AudioContext.WavCodecKind.AdpcmMs)
                return AudioContext.WavCodec;

            try
            {
                if (_webAudioSafetyReport.Value.IsProtectedWebAudioWav(wavFilePath))
                {
                    string localPath = wavFilePath.GAC_ToLocalPath();
                    _logger.LogInformation($"Preserving PCM16 for WebAudio WAV: {localPath}");
                    FFMpegSystem.AppendAudioLog($"WAV-WEBAUDIO-PROTECT | {wavFilePath} | forcing pcm_s16le");
                    return AudioContext.WavCodecKind.Pcm16;
                }
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex.ToString());
            }

            return AudioContext.WavCodec;
        }

        private static LoopMetadataSummary AnalyzeLoopMetadata(string wavFilePath)
        {
            var result = new LoopMetadataSummary();

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

                    if (chunkId.Equals("smpl", StringComparison.OrdinalIgnoreCase))
                    {
                        result.HasSmpl = true;
                    }
                    else if (chunkId.Equals("cue ", StringComparison.OrdinalIgnoreCase) && size >= 4)
                    {
                        result.CueCount = reader.ReadInt32();
                    }
                    else if (chunkId.Equals("LIST", StringComparison.OrdinalIgnoreCase) && size >= 4)
                    {
                        string listType = Encoding.ASCII.GetString(reader.ReadBytes(4));
                        if (listType.Equals("adtl", StringComparison.OrdinalIgnoreCase))
                        {
                            long listEnd = dataStart + size;
                            while (fs.Position + 8 <= listEnd)
                            {
                                if (!TryReadFourCC(reader, out var subChunkId))
                                    break;

                                int subChunkSize = reader.ReadInt32();
                                long subChunkStart = fs.Position;
                                if (subChunkSize < 0 || subChunkStart + subChunkSize > listEnd)
                                    break;

                                if (subChunkId.Equals("labl", StringComparison.OrdinalIgnoreCase) && subChunkSize >= 4)
                                {
                                    _ = reader.ReadInt32();
                                    string label = Encoding.ASCII
                                        .GetString(reader.ReadBytes(subChunkSize - 4))
                                        .TrimEnd('\0')
                                        .Trim();

                                    if (!string.IsNullOrWhiteSpace(label))
                                        result.Labels.Add(label);
                                }

                                fs.Position = subChunkStart + subChunkSize + (subChunkSize % 2);
                            }

                            continue;
                        }
                    }

                    fs.Position = dataStart + size + (size % 2);
                }
            }
            catch
            {
                return result;
            }

            return result;
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
