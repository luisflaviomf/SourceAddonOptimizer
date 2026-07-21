using System.Diagnostics;
using System.Text.Json;
using ImageMagick;

namespace VtfMaximumLab.Metrics;

internal sealed record FlipMetrics(double Mean, double P95, double Maximum);

internal sealed class FlipMetricRunner
{
    private const string MetricsMarker = "GMO_FLIP_METRICS ";
    private readonly string _executablePath;

    internal FlipMetricRunner(string executablePath)
    {
        _executablePath = Path.GetFullPath(executablePath);
        if (!File.Exists(_executablePath))
            throw new FileNotFoundException("The NVIDIA FLIP evaluator is missing.", _executablePath);
    }

    internal async Task<FlipMetrics> RunAsync(
        string referencePng,
        string testPng,
        CancellationToken cancellationToken,
        bool compositeAlpha = false)
    {
        if (!compositeAlpha)
            return await RunPairAsync(referencePng, testPng, cancellationToken).ConfigureAwait(false);

        string temporary = Path.Combine(Path.GetTempPath(), "gmod-optimizer-flip-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(temporary);
        try
        {
            FlipMetrics black = await RunCompositePairAsync(
                referencePng, testPng, temporary, 0, cancellationToken).ConfigureAwait(false);
            FlipMetrics white = await RunCompositePairAsync(
                referencePng, testPng, temporary, 255, cancellationToken).ConfigureAwait(false);
            return new FlipMetrics(
                Math.Max(black.Mean, white.Mean),
                Math.Max(black.P95, white.P95),
                Math.Max(black.Maximum, white.Maximum));
        }
        finally
        {
            try { Directory.Delete(temporary, recursive: true); }
            catch { /* Best-effort cleanup; metric result remains valid. */ }
        }
    }

    private async Task<FlipMetrics> RunCompositePairAsync(
        string referencePng,
        string testPng,
        string temporary,
        byte background,
        CancellationToken cancellationToken)
    {
        string suffix = background == 0 ? "black" : "white";
        string referenceComposite = Path.Combine(temporary, $"reference-{suffix}.png");
        string testComposite = Path.Combine(temporary, $"test-{suffix}.png");
        WriteComposite(referencePng, referenceComposite, background);
        WriteComposite(testPng, testComposite, background);
        return await RunPairAsync(referenceComposite, testComposite, cancellationToken).ConfigureAwait(false);
    }

    private async Task<FlipMetrics> RunPairAsync(
        string referencePng,
        string testPng,
        CancellationToken cancellationToken)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = _executablePath,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        startInfo.ArgumentList.Add("--reference");
        startInfo.ArgumentList.Add(Path.GetFullPath(referencePng));
        startInfo.ArgumentList.Add("--test");
        startInfo.ArgumentList.Add(Path.GetFullPath(testPng));
        startInfo.ArgumentList.Add("--verbosity");
        startInfo.ArgumentList.Add("0");
        startInfo.ArgumentList.Add("--no-error-map");

        using var process = new Process { StartInfo = startInfo };
        if (!process.Start())
            throw new InvalidOperationException("Could not start the NVIDIA FLIP evaluator.");
        Task<string> stdout = process.StandardOutput.ReadToEndAsync();
        Task<string> stderr = process.StandardError.ReadToEndAsync();
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(2));
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, timeout.Token);
        try
        {
            await process.WaitForExitAsync(linked.Token).ConfigureAwait(false);
        }
        catch
        {
            if (!process.HasExited)
                process.Kill(entireProcessTree: true);
            throw;
        }

        string output = await stdout.ConfigureAwait(false);
        string error = await stderr.ConfigureAwait(false);
        if (process.ExitCode != 0)
            throw new InvalidOperationException($"FLIP failed with {process.ExitCode}: {error}");
        return ParseMetrics(output);
    }

    internal static FlipMetrics ParseMetrics(string output)
    {
        int marker = output.IndexOf(MetricsMarker, StringComparison.Ordinal);
        if (marker < 0)
            throw new InvalidDataException("FLIP did not return its machine-readable metrics marker.");
        int jsonStart = marker + MetricsMarker.Length;
        int jsonEnd = output.IndexOfAny(new[] { '\r', '\n' }, jsonStart);
        string json = (jsonEnd < 0 ? output[jsonStart..] : output[jsonStart..jsonEnd]).Trim();
        FlipMetrics? metrics = JsonSerializer.Deserialize<FlipMetrics>(json, new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true
        });
        return metrics ?? throw new InvalidDataException("FLIP returned invalid machine-readable metrics.");
    }

    private static void WriteComposite(string inputPath, string outputPath, byte background)
    {
        using var input = new MagickImage(inputPath);
        byte[] rgba = input.GetPixelsUnsafe().ToByteArray(PixelMapping.RGBA) ??
                      throw new InvalidDataException("Could not read pixels for FLIP alpha compositing.");
        var rgb = new byte[checked(rgba.Length / 4 * 3)];
        for (int pixel = 0; pixel < rgba.Length / 4; pixel++)
        {
            int source = pixel * 4;
            int destination = pixel * 3;
            int alpha = rgba[source + 3];
            for (int channel = 0; channel < 3; channel++)
                rgb[destination + channel] = checked((byte)(
                    (rgba[source + channel] * alpha + background * (255 - alpha) + 127) / 255));
        }
        using var composite = new MagickImage(MagickColors.Black, input.Width, input.Height);
        composite.ReadPixels(rgb, new PixelReadSettings(input.Width, input.Height, StorageType.Char, PixelMapping.RGB));
        composite.Format = MagickFormat.Png;
        composite.Write(outputPath);
    }
}
