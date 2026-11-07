using GmodAddonCompressor.Systems.Audio;
using Xunit;

namespace GmodAddonOptimizer.AudioAdaptive.Tests;

public sealed class AudioQualityTests
{
    [Fact]
    public void StereoGate_AllowsOnlyNearIdenticalSides()
    {
        PcmAudio dualMono = Stereo(4096, i => MathF.Sin(i * 0.05f), i => MathF.Sin(i * 0.05f));
        PcmAudio phaseOpposed = Stereo(4096, i => MathF.Sin(i * 0.05f), i => -MathF.Sin(i * 0.05f));

        StereoSafety safe = AudioSignalAnalyzer.AnalyzeStereo(dualMono);
        StereoSafety unsafeResult = AudioSignalAnalyzer.AnalyzeStereo(phaseOpposed);

        Assert.True(safe.CanDownmixConservatively);
        Assert.True(safe.Correlation >= 0.9999);
        Assert.True(safe.SideToMidDecibels <= -40);
        Assert.False(unsafeResult.CanDownmixConservatively);
    }

    [Fact]
    public void QualityMetrics_RankIdenticalAboveNoisyAudio()
    {
        PcmAudio reference = Mono(8192, i => 0.7f * MathF.Sin(i * 0.027f));
        PcmAudio identical = Mono(8192, i => 0.7f * MathF.Sin(i * 0.027f));
        PcmAudio noisy = Mono(8192, i => 0.7f * MathF.Sin(i * 0.027f) + ((i % 7) - 3) * 0.015f);

        AudioQualityScore exact = AudioQualityMetrics.Measure(reference, identical);
        AudioQualityScore degraded = AudioQualityMetrics.Measure(reference, noisy);

        Assert.True(exact.SdrDecibels > degraded.SdrDecibels);
        Assert.True(exact.SegmentalSnrDecibels > degraded.SegmentalSnrDecibels);
        Assert.True(exact.LogSpectralDistance < degraded.LogSpectralDistance);
    }

    [Fact]
    public void Selector_AcceptsOnlySmallerCandidateNoWorseThanBaseline()
    {
        var baseline = new AdaptiveAudioCandidate(
            "baseline", 1000, new AudioQualityScore(18, 17, 2.0), isBaseline: true, payload: "base");
        var tooLarge = new AdaptiveAudioCandidate(
            "larger", 1100, new AudioQualityScore(30, 30, 0.1), isBaseline: false, payload: "large");
        var worse = new AdaptiveAudioCandidate(
            "worse", 500, new AudioQualityScore(17.99, 17, 2.0), isBaseline: false, payload: "worse");
        var safe = new AdaptiveAudioCandidate(
            "safe", 700, new AudioQualityScore(18, 17.2, 1.9), isBaseline: false, payload: "safe");

        AdaptiveAudioCandidate selected = AdaptiveAudioSelector.Select(baseline, new[] { tooLarge, worse, safe });

        Assert.Equal("safe", selected.Name);
        Assert.Equal("safe", selected.Payload);
    }

    [Fact]
    public void Selector_FallsBackToBaselineWhenEveryCandidateRegresses()
    {
        var baseline = new AdaptiveAudioCandidate(
            "baseline", 1000, new AudioQualityScore(18, 17, 2.0), isBaseline: true, payload: "base");
        var worse = new AdaptiveAudioCandidate(
            "worse", 500, new AudioQualityScore(18, 16.99, 2.0), isBaseline: false, payload: "worse");

        AdaptiveAudioCandidate selected = AdaptiveAudioSelector.Select(baseline, new[] { worse });

        Assert.Same(baseline, selected);
    }

    [Fact]
    public void SegmentalSnr_IncludesAndClampsFinalPartialFrameLikeValidatedSpike()
    {
        PcmAudio shortReference = Mono(1000, i => 0.5f * MathF.Sin(i * 0.07f));

        AudioQualityScore score = AudioQualityMetrics.Measure(shortReference, shortReference);

        Assert.Equal(60.0, score.SegmentalSnrDecibels, precision: 8);
    }

    private static PcmAudio Mono(int frames, Func<int, float> value)
    {
        float[] samples = Enumerable.Range(0, frames).Select(value).ToArray();
        return new PcmAudio(44100, 1, samples);
    }

    private static PcmAudio Stereo(int frames, Func<int, float> left, Func<int, float> right)
    {
        float[] samples = new float[frames * 2];
        for (int frame = 0; frame < frames; frame++)
        {
            samples[frame * 2] = left(frame);
            samples[frame * 2 + 1] = right(frame);
        }
        return new PcmAudio(44100, 2, samples);
    }
}
