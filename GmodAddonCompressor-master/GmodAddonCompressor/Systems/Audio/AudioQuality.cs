using NAudio.Dsp;
using System;
using System.Collections.Generic;
using System.Linq;

namespace GmodAddonCompressor.Systems.Audio
{
    internal sealed class PcmAudio
    {
        internal PcmAudio(int sampleRate, int channels, float[] samples)
        {
            if (sampleRate <= 0)
                throw new ArgumentOutOfRangeException(nameof(sampleRate));
            if (channels is < 1 or > 2)
                throw new ArgumentOutOfRangeException(nameof(channels));
            if (samples == null || samples.Length % channels != 0)
                throw new ArgumentException("Samples must contain complete interleaved frames.", nameof(samples));
            SampleRate = sampleRate;
            Channels = channels;
            Samples = samples;
        }

        internal int SampleRate { get; }
        internal int Channels { get; }
        internal float[] Samples { get; }
        internal int FrameCount => Samples.Length / Channels;
    }

    internal sealed record StereoSafety(double Correlation, double SideToMidDecibels)
    {
        internal bool CanDownmixConservatively => Correlation >= 0.9999 && SideToMidDecibels <= -40.0;
    }

    internal static class AudioSignalAnalyzer
    {
        internal static StereoSafety AnalyzeStereo(PcmAudio audio)
        {
            if (audio.Channels == 1)
                return new StereoSafety(1.0, -300.0);
            if (audio.FrameCount == 0)
                return new StereoSafety(1.0, -300.0);

            double leftMean = 0;
            double rightMean = 0;
            for (int frame = 0; frame < audio.FrameCount; frame++)
            {
                leftMean += audio.Samples[frame * 2];
                rightMean += audio.Samples[frame * 2 + 1];
            }
            leftMean /= audio.FrameCount;
            rightMean /= audio.FrameCount;

            double covariance = 0;
            double leftVariance = 0;
            double rightVariance = 0;
            double midEnergy = 0;
            double sideEnergy = 0;
            for (int frame = 0; frame < audio.FrameCount; frame++)
            {
                double left = audio.Samples[frame * 2];
                double right = audio.Samples[frame * 2 + 1];
                double leftCentered = left - leftMean;
                double rightCentered = right - rightMean;
                covariance += leftCentered * rightCentered;
                leftVariance += leftCentered * leftCentered;
                rightVariance += rightCentered * rightCentered;
                double mid = 0.5 * (left + right);
                double side = 0.5 * (left - right);
                midEnergy += mid * mid;
                sideEnergy += side * side;
            }

            double denominator = Math.Sqrt(leftVariance * rightVariance);
            double correlation = denominator <= 1e-30 ? 1.0 : covariance / denominator;
            double sideToMid = 10.0 * Math.Log10((sideEnergy + 1e-30) / (midEnergy + 1e-30));
            return new StereoSafety(correlation, sideToMid);
        }

        internal static IReadOnlyList<int> OrderConservativeSampleRates(PcmAudio audio)
        {
            if (audio.SampleRate <= 22050)
                return new[] { 22050 };

            double highBandRatio = EstimateHighBandEnergyRatio(audio, 9500.0);
            return highBandRatio <= 0.001
                ? new[] { 22050, 44100 }
                : new[] { 44100, 22050 };
        }

        private static double EstimateHighBandEnergyRatio(PcmAudio audio, double thresholdHz)
        {
            int frameSize = LargestPowerOfTwoAtMost(Math.Min(4096, audio.FrameCount));
            if (frameSize < 64)
                return 0;
            var fft = new Complex[frameSize];
            int exponent = (int)Math.Log(frameSize, 2);
            double total = 0;
            double high = 0;
            int start = Math.Max(0, (audio.FrameCount - frameSize) / 2);
            for (int index = 0; index < frameSize; index++)
            {
                double mono = 0;
                for (int channel = 0; channel < audio.Channels; channel++)
                    mono += audio.Samples[(start + index) * audio.Channels + channel];
                mono /= audio.Channels;
                double window = Hann(index, frameSize);
                fft[index].X = (float)(mono * window);
                fft[index].Y = 0;
            }
            FastFourierTransform.FFT(true, exponent, fft);
            for (int bin = 0; bin <= frameSize / 2; bin++)
            {
                double energy = fft[bin].X * fft[bin].X + fft[bin].Y * fft[bin].Y;
                total += energy;
                if ((double)bin * audio.SampleRate / frameSize >= thresholdHz)
                    high += energy;
            }
            return high / (total + 1e-30);
        }

        private static int LargestPowerOfTwoAtMost(int value)
        {
            int result = 1;
            while (result <= value / 2)
                result *= 2;
            return result;
        }

        internal static double Hann(int index, int length) =>
            length <= 1 ? 1.0 : 0.5 - 0.5 * Math.Cos(2.0 * Math.PI * index / (length - 1));
    }

    internal sealed record AudioQualityScore(
        double SdrDecibels,
        double SegmentalSnrDecibels,
        double LogSpectralDistance)
    {
        internal bool IsNoWorseThan(AudioQualityScore baseline) =>
            SdrDecibels >= baseline.SdrDecibels &&
            SegmentalSnrDecibels >= baseline.SegmentalSnrDecibels &&
            LogSpectralDistance <= baseline.LogSpectralDistance;
    }

    internal static class AudioQualityMetrics
    {
        private const int FrameSize = 2048;

        internal static AudioQualityScore Measure(PcmAudio reference, PcmAudio candidate)
        {
            if (reference.SampleRate != candidate.SampleRate || reference.Channels != candidate.Channels)
                throw new ArgumentException("Reference and candidate PCM layouts must match.");

            float[] aligned = Align(candidate.Samples, reference.Samples.Length);
            return new AudioQualityScore(
                MeasureSdr(reference.Samples, aligned),
                MeasureSegmentalSnr(reference.Samples, aligned, reference.Channels),
                MeasureLogSpectralDistance(reference.Samples, aligned, reference.Channels));
        }

        private static float[] Align(float[] samples, int length)
        {
            if (samples.Length == length)
                return samples;
            var aligned = new float[length];
            Array.Copy(samples, aligned, Math.Min(samples.Length, length));
            return aligned;
        }

        private static double MeasureSdr(float[] reference, float[] candidate)
        {
            double signal = 0;
            double error = 0;
            for (int index = 0; index < reference.Length; index++)
            {
                double value = reference[index];
                double difference = value - candidate[index];
                signal += value * value;
                error += difference * difference;
            }
            return 10.0 * Math.Log10((signal + 1e-30) / (error + 1e-30));
        }

        private static double MeasureSegmentalSnr(float[] reference, float[] candidate, int channels)
        {
            int frames = reference.Length / channels;
            var values = new List<double>();
            for (int start = 0; start < frames; start += FrameSize)
            {
                double signal = 0;
                double error = 0;
                int sampleStart = start * channels;
                int sampleEnd = Math.Min(frames, start + FrameSize) * channels;
                for (int index = sampleStart; index < sampleEnd; index++)
                {
                    double value = reference[index];
                    double difference = value - candidate[index];
                    signal += value * value;
                    error += difference * difference;
                }
                if (signal < 1e-10)
                    continue;
                double snr = 10.0 * Math.Log10((signal + 1e-30) / (error + 1e-30));
                values.Add(Math.Clamp(snr, -10.0, 60.0));
            }
            return values.Count == 0 ? MeasureSdr(reference, candidate) : values.Average();
        }

        private static double MeasureLogSpectralDistance(float[] reference, float[] candidate, int channels)
        {
            int frames = reference.Length / channels;
            int frameSize = Math.Min(FrameSize, LargestPowerOfTwoAtMost(frames));
            if (frameSize < 64)
                return 0;

            int available = Math.Max(1, frames / frameSize);
            int count = Math.Min(32, available);
            int lastStart = Math.Max(0, frames - frameSize);
            var frameValues = new List<double>(count);
            for (int ordinal = 0; ordinal < count; ordinal++)
            {
                int start = count == 1 ? 0 : (int)Math.Round((double)ordinal * lastStart / (count - 1));
                double squaredDbError = 0;
                int includedBins = 0;
                for (int channel = 0; channel < channels; channel++)
                {
                    double[] referenceDb = SpectrumDb(reference, channels, channel, start, frameSize);
                    double[] candidateDb = SpectrumDb(candidate, channels, channel, start, frameSize);
                    double maximum = referenceDb.Max();
                    double threshold = maximum - 60.0;
                    for (int bin = 0; bin < referenceDb.Length; bin++)
                    {
                        if (referenceDb[bin] < threshold)
                            continue;
                        double difference = referenceDb[bin] - candidateDb[bin];
                        squaredDbError += difference * difference;
                        includedBins++;
                    }
                }
                if (includedBins > 0)
                    frameValues.Add(Math.Sqrt(squaredDbError / includedBins));
            }
            return frameValues.Count == 0 ? 0 : frameValues.Average();
        }

        private static double[] SpectrumDb(float[] samples, int channels, int channel, int start, int frameSize)
        {
            var fft = new Complex[frameSize];
            int exponent = (int)Math.Log(frameSize, 2);
            for (int index = 0; index < frameSize; index++)
            {
                fft[index].X = (float)(samples[(start + index) * channels + channel] * AudioSignalAnalyzer.Hann(index, frameSize));
                fft[index].Y = 0;
            }
            FastFourierTransform.FFT(true, exponent, fft);
            var result = new double[frameSize / 2 + 1];
            for (int bin = 0; bin < result.Length; bin++)
            {
                double magnitude = Math.Sqrt(fft[bin].X * fft[bin].X + fft[bin].Y * fft[bin].Y);
                result[bin] = 20.0 * Math.Log10(Math.Max(1e-7, magnitude));
            }
            return result;
        }

        private static int LargestPowerOfTwoAtMost(int value)
        {
            if (value <= 0)
                return 0;
            int result = 1;
            while (result <= value / 2)
                result *= 2;
            return result;
        }
    }

    internal sealed class AdaptiveAudioCandidate
    {
        internal AdaptiveAudioCandidate(
            string name,
            long bytes,
            AudioQualityScore score,
            bool isBaseline,
            object? payload)
        {
            Name = name;
            Bytes = bytes;
            Score = score;
            IsBaseline = isBaseline;
            Payload = payload;
        }

        internal string Name { get; }
        internal long Bytes { get; }
        internal AudioQualityScore Score { get; }
        internal bool IsBaseline { get; }
        internal object? Payload { get; }
    }

    internal static class AdaptiveAudioSelector
    {
        internal static AdaptiveAudioCandidate Select(
            AdaptiveAudioCandidate baseline,
            IEnumerable<AdaptiveAudioCandidate> candidates)
        {
            if (!baseline.IsBaseline)
                throw new ArgumentException("The reference candidate must be marked as the baseline.", nameof(baseline));

            return candidates
                .Where(candidate => candidate.Bytes < baseline.Bytes && candidate.Score.IsNoWorseThan(baseline.Score))
                .OrderBy(candidate => candidate.Bytes)
                .ThenByDescending(candidate => candidate.Score.SdrDecibels)
                .ThenByDescending(candidate => candidate.Score.SegmentalSnrDecibels)
                .ThenBy(candidate => candidate.Score.LogSpectralDistance)
                .ThenBy(candidate => candidate.Name, StringComparer.Ordinal)
                .FirstOrDefault() ?? baseline;
        }
    }
}
