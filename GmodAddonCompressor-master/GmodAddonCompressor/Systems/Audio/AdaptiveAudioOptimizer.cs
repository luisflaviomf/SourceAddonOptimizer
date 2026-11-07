using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Audio
{
    internal enum AudioCodec
    {
        Pcm16 = 0,
        MsAdpcm = 1
    }

    internal sealed record AudioEncodingSpec(AudioCodec Codec, int SampleRate, int Channels, int BlockAlign)
    {
        internal string Id => $"{(Codec == AudioCodec.Pcm16 ? "pcm16" : "adpcm_ms")}_r{SampleRate}_c{Channels}_b{BlockAlign}";
    }

    internal sealed record AdaptiveAudioOptions(AudioEncodingSpec BaselineSpec, bool PreserveLoopMetadata);

    internal interface IAdaptiveAudioBackend
    {
        Task<bool> EncodeBaselineAsync(string inputPath, string outputPath, AudioEncodingSpec spec);
        Task<bool> EncodeCandidateAsync(string inputPath, string outputPath, AudioEncodingSpec spec);
        Task<PcmAudio> DecodeAsync(string path, int sampleRate, int channels);
    }

    internal sealed record AdaptiveAudioCandidateResult(
        AudioEncodingSpec Spec,
        long Bytes,
        AudioQualityScore? Score,
        bool StructurallyValid,
        bool QualityAccepted,
        string Note);

    internal sealed record AdaptiveAudioResult(
        bool Replaced,
        bool BaselineCreated,
        bool UsedBaselineFallback,
        AudioEncodingSpec SelectedSpec,
        long OriginalBytes,
        long BaselineBytes,
        long OutputBytes,
        TimeSpan Elapsed,
        IReadOnlyList<AdaptiveAudioCandidateResult> Candidates,
        string Note);

    internal static class AdaptiveAudioPlan
    {
        internal static IReadOnlyList<AudioEncodingSpec> Build(PcmAudio source, AudioEncodingSpec baseline)
        {
            IReadOnlyList<int> rates = AudioSignalAnalyzer.OrderConservativeSampleRates(source);
            var channels = new List<int> { source.Channels };
            if (source.Channels == 2 && AudioSignalAnalyzer.AnalyzeStereo(source).CanDownmixConservatively)
                channels.Add(1);

            var result = new List<AudioEncodingSpec>();
            foreach (int rate in rates)
            {
                foreach (int channelCount in channels.Distinct())
                {
                    AddIfDifferent(result, new AudioEncodingSpec(AudioCodec.Pcm16, rate, channelCount, channelCount * 2), baseline);
                    AddIfDifferent(result, new AudioEncodingSpec(AudioCodec.MsAdpcm, rate, channelCount, channelCount == 1 ? 1024 : 2048), baseline);
                }
            }
            return result;
        }

        private static void AddIfDifferent(List<AudioEncodingSpec> result, AudioEncodingSpec candidate, AudioEncodingSpec baseline)
        {
            if (candidate != baseline && !result.Contains(candidate))
                result.Add(candidate);
        }
    }

    internal sealed class AdaptiveAudioOptimizer
    {
        private readonly IAdaptiveAudioBackend _backend;

        internal AdaptiveAudioOptimizer(IAdaptiveAudioBackend backend)
        {
            _backend = backend ?? throw new ArgumentNullException(nameof(backend));
        }

        internal async Task<AdaptiveAudioResult> OptimizeAsync(string inputPath, AdaptiveAudioOptions options)
        {
            var stopwatch = Stopwatch.StartNew();
            string fullPath = Path.GetFullPath(inputPath);
            long originalBytes = new FileInfo(fullPath).Length;
            string workRoot = fullPath + ".adaptive-" + Guid.NewGuid().ToString("N");
            string baselinePath = Path.Combine(workRoot, "baseline.wav");
            var candidateResults = new List<AdaptiveAudioCandidateResult>();
            bool baselineCreated = false;
            bool baselineCopiedByteIdentically = false;

            try
            {
                if (!RiffWaveFormat.TryReadFile(fullPath, out RiffWaveFormat? sourceFormat, out string formatReason))
                    return Result(false, false, true, options.BaselineSpec, originalBytes, originalBytes, stopwatch, candidateResults, formatReason);
                if (!RiffWaveDuration.TryGetLogicalSampleCountFile(fullPath, out ulong sourceLogicalSamples, out string durationReason))
                    return Result(false, false, true, options.BaselineSpec, originalBytes, originalBytes, stopwatch, candidateResults, durationReason);

                Directory.CreateDirectory(workRoot);
                WaveLoopMetadata? loopMetadata = null;
                if (options.PreserveLoopMetadata)
                    loopMetadata = WaveLoopMetadata.Capture(fullPath, sourceFormat!.SampleRate);

                if (sourceFormat!.MatchesManual(options.BaselineSpec))
                {
                    File.Copy(fullPath, baselinePath, overwrite: true);
                    baselineCreated = true;
                    baselineCopiedByteIdentically = true;
                }
                else
                {
                    baselineCreated = await _backend.EncodeBaselineAsync(fullPath, baselinePath, options.BaselineSpec).ConfigureAwait(false);
                }
                if (!baselineCreated || !File.Exists(baselinePath))
                    return Result(false, false, true, options.BaselineSpec, originalBytes, originalBytes, stopwatch, candidateResults, "Baseline encode failed; original preserved.");

                RestoreAndValidate(
                    baselinePath,
                    options.BaselineSpec,
                    baselineCopiedByteIdentically ? null : loopMetadata,
                    strictBlockGeometry: false);
                long baselineBytes = new FileInfo(baselinePath).Length;

                PcmAudio reference;
                PcmAudio baselinePcm;
                AudioQualityScore baselineScore;
                try
                {
                    reference = await _backend.DecodeAsync(fullPath, sourceFormat.SampleRate, sourceFormat.Channels).ConfigureAwait(false);
                    baselinePcm = await _backend.DecodeAsync(baselinePath, sourceFormat.SampleRate, sourceFormat.Channels).ConfigureAwait(false);
                    baselineScore = AudioQualityMetrics.Measure(reference, baselinePcm);
                }
                catch (Exception ex)
                {
                    CommitSelected(baselinePath, fullPath);
                    return Result(true, true, true, options.BaselineSpec, originalBytes, baselineBytes, stopwatch, candidateResults, "Quality decode failed; committed byte-identical baseline: " + ex.Message);
                }

                var baseline = new AdaptiveAudioCandidate(
                    "baseline", baselineBytes, baselineScore, isBaseline: true, payload: baselinePath);
                var selectable = new List<AdaptiveAudioCandidate>();
                IReadOnlyList<AudioEncodingSpec> plan = AdaptiveAudioPlan.Build(reference, options.BaselineSpec);
                for (int index = 0; index < plan.Count; index++)
                {
                    AudioEncodingSpec spec = plan[index];
                    string candidatePath = Path.Combine(workRoot, $"candidate-{index:D2}-{spec.Id}.wav");
                    try
                    {
                        bool encoded = await _backend.EncodeCandidateAsync(fullPath, candidatePath, spec).ConfigureAwait(false);
                        if (!encoded || !File.Exists(candidatePath))
                        {
                            candidateResults.Add(new AdaptiveAudioCandidateResult(spec, 0, null, false, false, "encode-failed"));
                            continue;
                        }

                        ulong expectedLogicalSamples = RiffWaveDuration.ScaleSampleCount(
                            sourceLogicalSamples,
                            sourceFormat.SampleRate,
                            spec.SampleRate);
                        if (spec.Codec == AudioCodec.MsAdpcm)
                            RiffWaveDuration.SetAdpcmFactSampleCountFile(candidatePath, expectedLogicalSamples);
                        RestoreAndValidate(candidatePath, spec, loopMetadata, strictBlockGeometry: true);
                        ValidateLogicalDuration(
                            candidatePath,
                            expectedLogicalSamples,
                            sourceLogicalSamples,
                            sourceFormat.SampleRate,
                            spec.SampleRate);
                        long candidateBytes = new FileInfo(candidatePath).Length;
                        if (candidateBytes >= baselineBytes)
                        {
                            candidateResults.Add(new AdaptiveAudioCandidateResult(spec, candidateBytes, null, true, false, "not-smaller-than-baseline"));
                            continue;
                        }

                        PcmAudio decoded = await _backend.DecodeAsync(candidatePath, sourceFormat.SampleRate, sourceFormat.Channels).ConfigureAwait(false);
                        AudioQualityScore score = AudioQualityMetrics.Measure(reference, decoded);
                        bool accepted = score.IsNoWorseThan(baselineScore);
                        candidateResults.Add(new AdaptiveAudioCandidateResult(
                            spec, candidateBytes, score, true, accepted, accepted ? "quality-safe" : "quality-regression"));
                        selectable.Add(new AdaptiveAudioCandidate(
                            spec.Id, candidateBytes, score, isBaseline: false, payload: candidatePath));
                    }
                    catch (Exception ex)
                    {
                        candidateResults.Add(new AdaptiveAudioCandidateResult(spec, FileLengthOrZero(candidatePath), null, false, false, ex.Message));
                    }
                }

                AdaptiveAudioCandidate selected = AdaptiveAudioSelector.Select(baseline, selectable);
                string selectedPath = (string)selected.Payload!;
                AudioEncodingSpec selectedSpec = selected == baseline
                    ? options.BaselineSpec
                    : plan.First(spec => spec.Id == selected.Name);
                CommitSelected(selectedPath, fullPath);
                long outputBytes = new FileInfo(fullPath).Length;
                return new AdaptiveAudioResult(
                    Replaced: true,
                    BaselineCreated: true,
                    UsedBaselineFallback: selected == baseline,
                    SelectedSpec: selectedSpec,
                    OriginalBytes: originalBytes,
                    BaselineBytes: baselineBytes,
                    OutputBytes: outputBytes,
                    Elapsed: stopwatch.Elapsed,
                    Candidates: candidateResults,
                    Note: selected == baseline ? "No smaller quality-safe candidate; baseline retained." : "Smaller quality-safe candidate selected.");
            }
            catch (Exception ex)
            {
                if (baselineCreated && File.Exists(baselinePath))
                {
                    try
                    {
                        long baselineBytes = new FileInfo(baselinePath).Length;
                        CommitSelected(baselinePath, fullPath);
                        return Result(true, true, true, options.BaselineSpec, originalBytes, baselineBytes, stopwatch, candidateResults, "Adaptive failure; committed byte-identical baseline: " + ex.Message);
                    }
                    catch
                    {
                        // The original remains protected by CommitSelected's backup path.
                    }
                }
                return Result(false, baselineCreated, true, options.BaselineSpec, originalBytes, originalBytes, stopwatch, candidateResults, "Adaptive and baseline fallback failed; original preserved: " + ex.Message);
            }
            finally
            {
                try { if (Directory.Exists(workRoot)) Directory.Delete(workRoot, recursive: true); } catch { }
            }
        }

        private static void RestoreAndValidate(
            string path,
            AudioEncodingSpec spec,
            WaveLoopMetadata? loopMetadata,
            bool strictBlockGeometry)
        {
            if (loopMetadata?.HasAny == true)
                loopMetadata.RestoreFile(path, spec.SampleRate);
            bool valid = strictBlockGeometry
                ? RiffWaveValidator.TryValidateEncodingFile(path, spec, out string reason)
                : RiffWaveValidator.TryValidateBaselineEncodingFile(path, spec, out reason);
            if (!valid)
                throw new InvalidDataException(reason);
        }

        private static void CommitSelected(string selectedPath, string targetPath)
        {
            string commitPath = targetPath + ".adaptive-commit-" + Guid.NewGuid().ToString("N");
            string backupPath = targetPath + ".adaptive-backup-" + Guid.NewGuid().ToString("N");
            File.Copy(selectedPath, commitPath, overwrite: false);
            try
            {
                File.Replace(commitPath, targetPath, backupPath, ignoreMetadataErrors: true);
                try { File.Delete(backupPath); } catch { }
            }
            catch
            {
                try { if (File.Exists(commitPath)) File.Delete(commitPath); } catch { }
                if (File.Exists(backupPath))
                {
                    try
                    {
                        if (File.Exists(targetPath)) File.Delete(targetPath);
                        File.Move(backupPath, targetPath);
                    }
                    catch { }
                }
                throw;
            }
        }

        private static void ValidateLogicalDuration(
            string path,
            ulong expectedSamples,
            ulong sourceSamples,
            int sourceRate,
            int targetRate)
        {
            if (!RiffWaveDuration.TryGetLogicalSampleCountFile(path, out ulong actualSamples, out string reason))
                throw new InvalidDataException(reason);
            double sourceDuration = (double)sourceSamples / sourceRate;
            double actualDuration = (double)actualSamples / targetRate;
            if (actualSamples != expectedSamples || Math.Abs(actualDuration - sourceDuration) > 1.0 / targetRate + 1e-9)
            {
                throw new InvalidDataException(
                    $"Candidate logical duration mismatch: {actualSamples} samples, expected {expectedSamples}.");
            }
        }

        private static AdaptiveAudioResult Result(
            bool replaced,
            bool baselineCreated,
            bool usedBaselineFallback,
            AudioEncodingSpec selectedSpec,
            long originalBytes,
            long outputBytes,
            Stopwatch stopwatch,
            IReadOnlyList<AdaptiveAudioCandidateResult> candidates,
            string note) =>
            new(
                replaced,
                baselineCreated,
                usedBaselineFallback,
                selectedSpec,
                originalBytes,
                baselineCreated ? outputBytes : originalBytes,
                outputBytes,
                stopwatch.Elapsed,
                candidates,
                note);

        private static long FileLengthOrZero(string path) => File.Exists(path) ? new FileInfo(path).Length : 0;
    }

    internal sealed record RiffWaveFormat(ushort FormatTag, int SampleRate, int Channels, int BitsPerSample, int BlockAlign)
    {
        internal bool Matches(AudioEncodingSpec spec) =>
            FormatTag == (spec.Codec == AudioCodec.Pcm16 ? 1 : 2) &&
            SampleRate == spec.SampleRate &&
            Channels == spec.Channels &&
            BlockAlign == spec.BlockAlign &&
            BitsPerSample == (spec.Codec == AudioCodec.Pcm16 ? 16 : 4);

        internal bool MatchesManual(AudioEncodingSpec spec) =>
            FormatTag == (spec.Codec == AudioCodec.Pcm16 ? 1 : 2) &&
            SampleRate == spec.SampleRate &&
            Channels == spec.Channels &&
            BitsPerSample == (spec.Codec == AudioCodec.Pcm16 ? 16 : 4);

        internal static bool TryReadFile(string path, out RiffWaveFormat? format, out string reason)
        {
            try
            {
                return TryRead(File.ReadAllBytes(path), out format, out reason);
            }
            catch (Exception ex)
            {
                format = null;
                reason = ex.Message;
                return false;
            }
        }

        internal static bool TryRead(byte[] wave, out RiffWaveFormat? format, out string reason)
        {
            format = null;
            if (!RiffWaveParser.TryParse(wave, out RiffWaveLayout? layout, out reason))
                return false;
            RiffChunk? chunk = layout!.Chunks.FirstOrDefault(candidate => candidate.Id == "fmt ");
            if (chunk == null || chunk.DataSize < 16)
            {
                reason = "RIFF/WAVE fmt chunk is missing or truncated.";
                return false;
            }
            int offset = chunk.DataOffset;
            format = new RiffWaveFormat(
                BitConverter.ToUInt16(wave, offset),
                checked((int)BitConverter.ToUInt32(wave, offset + 4)),
                BitConverter.ToUInt16(wave, offset + 2),
                BitConverter.ToUInt16(wave, offset + 14),
                BitConverter.ToUInt16(wave, offset + 12));
            reason = string.Empty;
            return true;
        }
    }
}
