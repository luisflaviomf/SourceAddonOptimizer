using VtfMaximumLab.Metrics;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class FlipMetricRunnerTests
{
    [Fact]
    public void ParsesMachineReadableMetricsFromNativeEvaluatorOutput()
    {
        const string output = "GMO_FLIP_METRICS {\"mean\":0.025927413254976273,\"p95\":0.095416484773158949,\"maximum\":0.58126407861709595}\r\nTotal time: 0.0385 seconds\r\n";

        FlipMetrics metrics = FlipMetricRunner.ParseMetrics(output);

        Assert.Equal(0.025927413254976273, metrics.Mean, 15);
        Assert.Equal(0.095416484773158949, metrics.P95, 15);
        Assert.Equal(0.58126407861709595, metrics.Maximum, 15);
    }

    [Fact]
    public void RejectsOutputWithoutMetricsMarker()
    {
        Assert.Throws<InvalidDataException>(() => FlipMetricRunner.ParseMetrics("Mean: 0.1"));
    }
}
