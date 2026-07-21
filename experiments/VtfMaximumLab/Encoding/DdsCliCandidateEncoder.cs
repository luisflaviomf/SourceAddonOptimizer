using System.Diagnostics;
using VtfMaximumLab.Candidates;

namespace VtfMaximumLab.Encoding;

internal abstract class DdsCliCandidateEncoder : IVtfCandidateEncoder
{
    protected DdsCliCandidateEncoder(string name, string executablePath)
    {
        Name = name;
        ExecutablePath = executablePath;
    }

    public string Name { get; }
    protected string ExecutablePath { get; }

    public async Task<DdsBcDocument> EncodeAsync(DdsEncodingRequest request, CancellationToken cancellationToken)
    {
        string output = Path.GetFullPath(request.OutputDdsPath);
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        if (File.Exists(output))
            File.Delete(output);

        ProcessStartInfo startInfo = BuildStartInfo(request);
        using var process = new Process { StartInfo = startInfo };
        if (!process.Start())
            throw new InvalidOperationException($"Could not start encoder {Name}.");
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

        string stdoutText = await stdout.ConfigureAwait(false);
        string stderrText = await stderr.ConfigureAwait(false);
        if (process.ExitCode != 0)
            throw new InvalidOperationException($"{Name} exited with {process.ExitCode}: {stderrText}\n{stdoutText}");
        if (!File.Exists(output))
        {
            string generated = Path.Combine(
                Path.GetDirectoryName(output)!,
                Path.GetFileNameWithoutExtension(request.InputPngPath) + ".dds");
            if (File.Exists(generated))
                File.Move(generated, output, overwrite: true);
        }
        if (!File.Exists(output))
            throw new FileNotFoundException($"{Name} did not create the expected DDS.", output);
        return DdsBcReader.Read(output);
    }

    protected abstract ProcessStartInfo BuildStartInfo(DdsEncodingRequest request);

    protected ProcessStartInfo CreateStartInfo()
    {
        return new ProcessStartInfo
        {
            FileName = ExecutablePath,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
    }

    protected static string GetBcName(VtfTargetFormat format) => format switch
    {
        VtfTargetFormat.Dxt1 or VtfTargetFormat.Dxt1OneBitAlpha => "BC1",
        VtfTargetFormat.Dxt5 => "BC3",
        _ => throw new ArgumentException($"Format {format} is not BC-compressible.")
    };
}

internal sealed class TexconvCandidateEncoder : DdsCliCandidateEncoder
{
    private readonly string? _bcOptions;

    internal TexconvCandidateEncoder(string name, string executablePath, string? bcOptions = null)
        : base(name, executablePath) => _bcOptions = bcOptions;

    protected override ProcessStartInfo BuildStartInfo(DdsEncodingRequest request)
    {
        ProcessStartInfo startInfo = CreateStartInfo();
        string outputDirectory = Path.GetDirectoryName(Path.GetFullPath(request.OutputDdsPath))!;
        startInfo.ArgumentList.Add("-nologo");
        startInfo.ArgumentList.Add("-y");
        startInfo.ArgumentList.Add("-dx9");
        startInfo.ArgumentList.Add("-f");
        startInfo.ArgumentList.Add(GetBcName(request.Format) == "BC1" ? "DXT1" : "DXT5");
        startInfo.ArgumentList.Add("-m");
        startInfo.ArgumentList.Add(request.MipCount.ToString(System.Globalization.CultureInfo.InvariantCulture));
        if (request.Format != VtfTargetFormat.Dxt1OneBitAlpha)
            startInfo.ArgumentList.Add("-sepalpha");
        if (request.Format == VtfTargetFormat.Dxt1OneBitAlpha)
        {
            startInfo.ArgumentList.Add("--keep-coverage");
            startInfo.ArgumentList.Add(request.AlphaThreshold.ToString("0.######", System.Globalization.CultureInfo.InvariantCulture));
            startInfo.ArgumentList.Add("-at");
            startInfo.ArgumentList.Add(request.AlphaThreshold.ToString("0.######", System.Globalization.CultureInfo.InvariantCulture));
        }
        if (!string.IsNullOrWhiteSpace(_bcOptions))
        {
            startInfo.ArgumentList.Add("-bc");
            startInfo.ArgumentList.Add(_bcOptions);
        }
        startInfo.ArgumentList.Add("-o");
        startInfo.ArgumentList.Add(outputDirectory);
        startInfo.ArgumentList.Add(Path.GetFullPath(request.InputPngPath));
        return startInfo;
    }
}

internal sealed class CompressonatorCandidateEncoder : DdsCliCandidateEncoder
{
    internal CompressonatorCandidateEncoder(string executablePath) : base("compressonator-hq", executablePath) { }

    protected override ProcessStartInfo BuildStartInfo(DdsEncodingRequest request)
    {
        ProcessStartInfo startInfo = CreateStartInfo();
        startInfo.ArgumentList.Add("-fd");
        startInfo.ArgumentList.Add(GetBcName(request.Format));
        startInfo.ArgumentList.Add("-EncodeWith");
        startInfo.ArgumentList.Add("CPU");
        startInfo.ArgumentList.Add("-miplevels");
        startInfo.ArgumentList.Add(request.MipCount.ToString(System.Globalization.CultureInfo.InvariantCulture));
        startInfo.ArgumentList.Add("-CompressionSpeed");
        startInfo.ArgumentList.Add("0");
        if (request.Format is VtfTargetFormat.Dxt1 or VtfTargetFormat.Dxt1OneBitAlpha)
        {
            startInfo.ArgumentList.Add("-RefineSteps");
            startInfo.ArgumentList.Add("2");
        }
        if (request.Format == VtfTargetFormat.Dxt1OneBitAlpha)
        {
            startInfo.ArgumentList.Add("-AlphaThreshold");
            startInfo.ArgumentList.Add(Math.Clamp((int)Math.Round(request.AlphaThreshold * 255), 1, 255).ToString());
        }
        startInfo.ArgumentList.Add("-silent");
        startInfo.ArgumentList.Add("-noprogress");
        startInfo.ArgumentList.Add(Path.GetFullPath(request.InputPngPath));
        startInfo.ArgumentList.Add(Path.GetFullPath(request.OutputDdsPath));
        return startInfo;
    }
}
