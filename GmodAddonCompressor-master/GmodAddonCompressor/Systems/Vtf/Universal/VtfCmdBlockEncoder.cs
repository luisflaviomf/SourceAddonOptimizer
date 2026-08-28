using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using GmodAddonCompressor.Systems.Vtf;
using ImageMagick;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record VtfEncodedBlockSet(
    int Format,
    int Width,
    int Height,
    IReadOnlyList<byte[]> SubresourcePayloads);

internal sealed record UniversalVtfProcessInvocation(
    string FileName,
    IReadOnlyList<string> Arguments,
    string WorkingDirectory,
    TimeSpan Timeout);

internal sealed record UniversalVtfProcessResult(
    int ExitCode,
    string StandardOutput,
    string StandardError,
    bool TimedOut);

internal interface IUniversalVtfProcessRunner
{
    Task<UniversalVtfProcessResult> RunAsync(
        UniversalVtfProcessInvocation invocation,
        CancellationToken cancellationToken);
}

internal sealed class UniversalVtfProcessRunner : IUniversalVtfProcessRunner
{
    public async Task<UniversalVtfProcessResult> RunAsync(
        UniversalVtfProcessInvocation invocation,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(invocation);
        if (!Path.IsPathFullyQualified(invocation.FileName) ||
            !Path.IsPathFullyQualified(invocation.WorkingDirectory))
        {
            throw new ArgumentException("Process paths must be absolute.", nameof(invocation));
        }

        if (invocation.Timeout <= TimeSpan.Zero)
        {
            throw new ArgumentOutOfRangeException(nameof(invocation));
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = invocation.FileName,
            WorkingDirectory = invocation.WorkingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        foreach (string argument in invocation.Arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = new Process { StartInfo = startInfo };
        if (!process.Start())
        {
            throw new InvalidOperationException("VTFCmd process did not start.");
        }

        Task<string> stdoutTask = process.StandardOutput.ReadToEndAsync();
        Task<string> stderrTask = process.StandardError.ReadToEndAsync();
        using var timeout = new CancellationTokenSource(invocation.Timeout);
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(
            cancellationToken,
            timeout.Token);
        bool timedOut = false;
        try
        {
            await process.WaitForExitAsync(linked.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            timedOut = true;
            TryKill(process);
        }
        catch (OperationCanceledException)
        {
            TryKill(process);
            throw;
        }

        if (timedOut && !process.HasExited)
        {
            await process.WaitForExitAsync(CancellationToken.None).ConfigureAwait(false);
        }

        string stdout = await stdoutTask.ConfigureAwait(false);
        string stderr = await stderrTask.ConfigureAwait(false);
        return new UniversalVtfProcessResult(
            process.HasExited ? process.ExitCode : -1,
            stdout,
            stderr,
            timedOut);
    }

    private static void TryKill(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch (Exception error) when (error is InvalidOperationException or Win32Exception)
        {
        }
    }
}

internal sealed class VtfCmdBlockEncoder
{
    private static readonly TimeSpan ProcessTimeout = TimeSpan.FromMinutes(2);

    private readonly UniversalVtfToolchainFingerprint _toolchain;
    private readonly IUniversalVtfProcessRunner _runner;

    internal VtfCmdBlockEncoder(
        UniversalVtfToolchainFingerprint toolchain,
        IUniversalVtfProcessRunner? runner = null)
    {
        ArgumentNullException.ThrowIfNull(toolchain);
        if (string.IsNullOrWhiteSpace(toolchain.Root) || !Path.IsPathFullyQualified(toolchain.Root))
        {
            throw new ArgumentException("A verified absolute toolchain root is required.", nameof(toolchain));
        }

        if (toolchain.Sha256.Length != 64 || toolchain.Sha256.Any(character => !Uri.IsHexDigit(character)))
        {
            throw new ArgumentException("A 64-character toolchain fingerprint is required.", nameof(toolchain));
        }

        _toolchain = toolchain;
        _runner = runner ?? new UniversalVtfProcessRunner();
    }

    internal async Task<VtfEncodedBlockSet> EncodeAsync(
        UniversalVtfImagePlane plane,
        int format,
        Version sourceVersion,
        string workDirectory,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(plane);
        ArgumentNullException.ThrowIfNull(sourceVersion);
        ValidatePlane(plane);
        string formatName = format switch
        {
            13 => "DXT1",
            15 => "DXT5",
            _ => throw new ArgumentOutOfRangeException(
                nameof(format),
                format,
                "Only opaque DXT1 (13) and DXT5 (15) are supported.")
        };

        if (sourceVersion.Major != 7 || sourceVersion.Minor < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(sourceVersion));
        }

        string workRoot = RequireWorkDirectory(workDirectory);
        string operationRoot = Path.Combine(workRoot, $"vtfcmd-encode-{Guid.NewGuid():N}");
        string outputDirectory = Path.Combine(operationRoot, "output");
        Directory.CreateDirectory(outputDirectory);
        try
        {
            cancellationToken.ThrowIfCancellationRequested();
            string inputPng = Path.GetFullPath(Path.Combine(operationRoot, "input.png"));
            WritePng(plane, inputPng);

            string executable = Path.GetFullPath(Path.Combine(
                _toolchain.Root,
                "VTFEdit",
                "VTFCmd.exe"));
            if (!File.Exists(executable))
            {
                throw new FileNotFoundException("Verified VTFCmd executable is missing.", executable);
            }

            string version = $"7.{Math.Min(sourceVersion.Minor, 4).ToString(CultureInfo.InvariantCulture)}";
            string[] arguments =
            {
                "-file", inputPng,
                "-output", Path.GetFullPath(outputDirectory),
                "-format", formatName,
                "-alphaformat", formatName,
                "-version", version
            };
            var invocation = new UniversalVtfProcessInvocation(
                executable,
                arguments,
                operationRoot,
                ProcessTimeout);
            UniversalVtfProcessResult process = await _runner
                .RunAsync(invocation, cancellationToken)
                .ConfigureAwait(false);
            if (process.TimedOut)
            {
                throw new TimeoutException(
                    $"VTFCmd timed out after {ProcessTimeout}. stdout={process.StandardOutput}; " +
                    $"stderr={process.StandardError}");
            }

            if (process.ExitCode != 0)
            {
                throw new InvalidDataException(
                    $"VTFCmd exited with {process.ExitCode}. stdout={process.StandardOutput}; " +
                    $"stderr={process.StandardError}");
            }

            string[] outputs = Directory.GetFiles(outputDirectory, "*.vtf", SearchOption.TopDirectoryOnly);
            if (outputs.Length != 1)
            {
                throw new InvalidDataException(
                    $"VTFCmd produced {outputs.Length} VTF files; exactly one is required.");
            }

            return ReadAndValidateOutput(outputs[0], plane, format);
        }
        finally
        {
            DeleteOwnedOperationDirectory(workRoot, operationRoot);
        }
    }

    private static VtfEncodedBlockSet ReadAndValidateOutput(
        string outputPath,
        UniversalVtfImagePlane source,
        int expectedFormat)
    {
        VtfContainerLayout layout = VtfContainerLayoutReader.Read(outputPath);
        if (layout.Document.Width != source.Width ||
            layout.Document.Height != source.Height ||
            layout.Document.HighResFormat != expectedFormat ||
            layout.Document.Frames != 1 ||
            layout.Document.Faces != 1 ||
            layout.Document.Depth != 1)
        {
            throw new InvalidDataException(
                "VTFCmd output dimensions, format, or subresource structure changed unexpectedly.");
        }

        if (layout.Document.HighResFormat == 20)
        {
            throw new InvalidDataException("VTFCmd produced one-bit-alpha DXT1 instead of opaque format 13.");
        }

        VtfSubresourceRange top = layout.Subresources.SingleOrDefault(range =>
                range.MipLevel == 0 && range.Frame == 0 && range.Face == 0 && range.DepthSlice == 0)
            ?? throw new InvalidDataException("VTFCmd output lacks one unambiguous top-level subresource.");
        if (!VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                outputPath,
                layout.Document,
                mipLevel: 0,
                frame: 0,
                face: 0,
                depthSlice: 0,
                out byte[] decoded) ||
            decoded.Length != checked(source.Width * source.Height * 4))
        {
            throw new InvalidDataException("VTFCmd top-level block did not decode successfully.");
        }

        var payload = new byte[top.Range.Length];
        using (FileStream stream = new(
                   outputPath,
                   FileMode.Open,
                   FileAccess.Read,
                   FileShare.Read,
                   bufferSize: 64 * 1024,
                   FileOptions.SequentialScan))
        {
            stream.Position = top.Range.Offset;
            int read = 0;
            while (read < payload.Length)
            {
                int count = stream.Read(payload, read, payload.Length - read);
                if (count == 0)
                {
                    throw new EndOfStreamException("VTFCmd top-level payload is truncated.");
                }

                read += count;
            }
        }

        return new VtfEncodedBlockSet(
            expectedFormat,
            source.Width,
            source.Height,
            new[] { payload });
    }

    private static void WritePng(UniversalVtfImagePlane plane, string outputPath)
    {
        var settings = new MagickReadSettings
        {
            Width = checked((uint)plane.Width),
            Height = checked((uint)plane.Height),
            Format = MagickFormat.Rgba,
            Depth = 8
        };
        using var image = new MagickImage(plane.Rgba, settings)
        {
            Format = MagickFormat.Png32
        };
        image.Write(outputPath);
        if (!File.Exists(outputPath) || new FileInfo(outputPath).Length == 0)
        {
            throw new InvalidDataException("ImageMagick did not produce the VTFCmd input PNG.");
        }
    }

    private static void ValidatePlane(UniversalVtfImagePlane plane)
    {
        if (plane.Width <= 0 || plane.Height <= 0 ||
            plane.Rgba is null || plane.Rgba.Length != checked(plane.Width * plane.Height * 4))
        {
            throw new ArgumentException("RGBA plane dimensions do not match.", nameof(plane));
        }

        if (plane.Frame < 0 || plane.Face < 0 || plane.DepthSlice < 0)
        {
            throw new ArgumentException(
                "Plane coordinates cannot be negative.",
                nameof(plane));
        }
    }

    private static string RequireWorkDirectory(string requested)
    {
        if (string.IsNullOrWhiteSpace(requested))
        {
            throw new ArgumentException("A non-empty work directory is required.", nameof(requested));
        }
        string fullPath = Path.GetFullPath(requested);
        Directory.CreateDirectory(fullPath);
        return fullPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }

    private static void DeleteOwnedOperationDirectory(string workRoot, string operationRoot)
    {
        string resolvedWorkRoot = Path.GetFullPath(workRoot)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string resolvedOperation = Path.GetFullPath(operationRoot)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        bool directChild = string.Equals(
            Path.GetDirectoryName(resolvedOperation),
            resolvedWorkRoot,
            StringComparison.OrdinalIgnoreCase);
        bool ownedName = Path.GetFileName(resolvedOperation)
            .StartsWith("vtfcmd-encode-", StringComparison.Ordinal);
        if (!directChild || !ownedName)
        {
            throw new InvalidOperationException(
                $"Refusing to remove an unowned VTFCmd operation directory: {resolvedOperation}");
        }

        if (Directory.Exists(resolvedOperation))
        {
            Directory.Delete(resolvedOperation, recursive: true);
        }
    }
}
