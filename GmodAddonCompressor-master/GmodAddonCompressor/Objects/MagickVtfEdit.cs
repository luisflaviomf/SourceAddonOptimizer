using GmodAddonCompressor.Bases;
using GmodAddonCompressor.CustomExtensions;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Properties;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Maps;
using GmodAddonCompressor.Systems.Tools;
using ImageMagick;
using Microsoft.Extensions.Logging;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Objects
{
    internal sealed class MagickVtfEdit : ImageEditBase, ICompress, ICompressPreparation
    {
        private const string ToolName = "VTFEdit";
        private const string ToolVersion = "1";
        private const double SparseAlphaMeanThreshold = 8.0;
        private const double SparseAlphaCoverageThreshold = 10.0;

        private readonly ILogger _logger = LogSystem.CreateLogger<MagickVtfEdit>();
        private readonly VTFEdit _standardFallback;
        private readonly string _vtfCmdFilePath;
        private readonly Lazy<SafetySnapshot> _safetySnapshot;

        public MagickVtfEdit(string addonRoot)
            : this(addonRoot, new VTFEdit())
        {
        }

        internal MagickVtfEdit(string addonRoot, VTFEdit standardFallback)
        {
            _standardFallback = standardFallback;

            string toolRoot = ToolExtractionSystem.EnsureExtracted(
                ToolName,
                ToolVersion,
                Resources.VTFEdit,
                new[] { Path.Combine("VTFEdit", "VTFCmd.exe") });

            _vtfCmdFilePath = Path.Combine(toolRoot, "VTFEdit", "VTFCmd.exe");
            _safetySnapshot = new Lazy<SafetySnapshot>(() => BuildSafetySnapshot(addonRoot), LazyThreadSafetyMode.ExecutionAndPublication);
        }

        public void Prepare()
        {
            _ = _safetySnapshot.Value;
        }

        public async Task Compress(string vtfFilePath)
        {
            try
            {
                if (!ShouldUseMagick(vtfFilePath, out string reason))
                {
                    if (ShouldPreserveWithoutCompression(reason))
                    {
                        _logger.LogInformation(
                            $"[MAGICK][VTF] Preserving {vtfFilePath.GAC_ToLocalPath()} unchanged: {reason}");
                        return;
                    }

                    _logger.LogInformation(
                        $"[MAGICK][VTF] Standard fallback for {vtfFilePath.GAC_ToLocalPath()}: {reason}");
                    await _standardFallback.Compress(vtfFilePath);
                    return;
                }

                MagickVtfResult result = await TryCompressWithMagickAsync(vtfFilePath);
                if (result.Applied)
                    return;

                _logger.LogInformation(
                    $"[MAGICK][VTF] Standard fallback for {vtfFilePath.GAC_ToLocalPath()}: {result.Reason}");
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                _logger.LogInformation(
                    $"[MAGICK][VTF] Exception, falling back to Standard pipeline: {vtfFilePath.GAC_ToLocalPath()}");
            }

            await _standardFallback.Compress(vtfFilePath);
        }

        private static bool ShouldPreserveWithoutCompression(string reason)
        {
            return string.Equals(
                reason,
                "translucent_basetexture_reference",
                StringComparison.OrdinalIgnoreCase);
        }

        private bool ShouldUseMagick(string vtfFilePath, out string reason)
        {
            var snapshot = _safetySnapshot.Value;
            string fullPath = Path.GetFullPath(vtfFilePath);

            if (snapshot.SafeReport.IsSafeVtf(fullPath))
            {
                reason = string.Empty;
                return true;
            }

            if (snapshot.SkipReasons.TryGetValue(fullPath, out string? skipReason))
            {
                reason = skipReason ?? "special_or_unreadable_vtf";
                return false;
            }

            reason = "special_or_unreadable_vtf";
            return false;
        }

        private async Task<MagickVtfResult> TryCompressWithMagickAsync(string vtfFilePath)
        {
            if (!TryReadHeader(vtfFilePath, out VtfHeader header) || header.Frames > 1)
                return new MagickVtfResult(false, "metadata_unreadable_or_animated");

            string tempRoot = Path.Combine(
                Path.GetDirectoryName(vtfFilePath) ?? Path.GetTempPath(),
                "__magick_vtf_tmp",
                $"{Path.GetFileNameWithoutExtension(vtfFilePath)}_{Guid.NewGuid():N}");
            Directory.CreateDirectory(tempRoot);

            string exportSourceVtf = Path.Combine(tempRoot, Path.GetFileName(vtfFilePath));
            string exportedPng = Path.Combine(tempRoot, Path.GetFileNameWithoutExtension(vtfFilePath) + ".png");
            string magickPng = Path.Combine(tempRoot, "magick_source.png");
            string rebuiltVtf = Path.Combine(tempRoot, "magick_source.vtf");
            long originalBytes = new FileInfo(vtfFilePath).Length;

            try
            {
                File.Copy(vtfFilePath, exportSourceVtf, true);
                await ChangeVtfVersionTo74Async(exportSourceVtf, header);

                if (!await RunVtfCmdAsync($" -file \"{exportSourceVtf}\" -output \"{tempRoot}\" -exportformat \"png\""))
                    return new MagickVtfResult(false, "vtf_export_failed");

                if (!File.Exists(exportedPng))
                    return new MagickVtfResult(false, "vtf_export_png_missing");

                if (ImageIsFullTransparent(exportedPng))
                    return new MagickVtfResult(false, "fully_transparent_export");

                bool fullyOpaqueAlpha = IsAlphaFullyOpaque(exportedPng);
                if (!fullyOpaqueAlpha && HasSparseAlphaCoverage(exportedPng))
                    return new MagickVtfResult(false, "sparse_alpha_guardrail");

                using (MagickImage image = new MagickImage(exportedPng))
                {
                    if (!TryApplyResize(image, exportedPng))
                        return new MagickVtfResult(false, "resize_guardrail");

                    if (fullyOpaqueAlpha && image.HasAlpha)
                        image.Alpha(AlphaOption.Remove);

                    image.Strip();
                    image.Write(magickPng);
                }

                if (!await RunVtfCmdAsync($" -file \"{magickPng}\" -output \"{tempRoot}\" -format \"DXT1\" -alphaformat \"DXT5\""))
                    return new MagickVtfResult(false, "vtf_import_failed");

                if (!File.Exists(rebuiltVtf))
                    return new MagickVtfResult(false, "rebuilt_vtf_missing");

                long newBytes = new FileInfo(rebuiltVtf).Length;
                if (newBytes >= originalBytes)
                    return new MagickVtfResult(false, "no_magick_gain");

                File.Copy(rebuiltVtf, vtfFilePath, true);
                _logger.LogInformation(
                    $"[MAGICK][VTF] Optimized {vtfFilePath.GAC_ToLocalPath()}: {FormatBytes(originalBytes)} -> {FormatBytes(newBytes)}");
                return new MagickVtfResult(true, "optimized");
            }
            finally
            {
                TryDelete(tempRoot);
            }
        }

        private bool TryApplyResize(MagickImage image, string sourcePath)
        {
            int originalWidth = (int)image.Width;
            int originalHeight = (int)image.Height;
            int[] reducedSize = GetReduceResolutionSize(originalWidth, originalHeight);
            int newWidth = reducedSize[0];
            int newHeight = reducedSize[1];
            bool isSingleColor = ImageIsSingleColor(sourcePath);

            newWidth = isSingleColor ? 1 : Math.Max(ImageContext.TaargetWidth, newWidth);
            newHeight = isSingleColor ? 1 : Math.Max(ImageContext.TargetHeight, newHeight);

            if (newWidth <= 0 || newHeight <= 0)
                return false;

            if (newWidth > originalWidth || newHeight > originalHeight)
                return false;

            if (newWidth == originalWidth && newHeight == originalHeight)
                return true;

            image.FilterType = FilterType.LanczosSharp;
            var geometry = new MagickGeometry((uint)newWidth, (uint)newHeight)
            {
                IgnoreAspectRatio = isSingleColor || !ImageContext.KeepImageAspectRatio
            };
            image.Resize(geometry);
            return true;
        }

        private SafetySnapshot BuildSafetySnapshot(string addonRoot)
        {
            try
            {
                MapVtfSafetyReport report = MapVtfSafetyClassifier.Analyze(new[] { addonRoot });
                var reasons = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                foreach (MapVtfSkippedFile skipped in report.SkippedFiles)
                    reasons[Path.GetFullPath(skipped.FullPath)] = skipped.PrimaryReason;

                _logger.LogInformation(
                    $"[MAGICK][VTF] Safety scan for {CompressDirectoryContext.ToLocal(addonRoot)}: {report.SafeVtfCount} common VTF, {report.SkippedVtfCount} protected/special VTF.");

                return new SafetySnapshot(report, reasons);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return new SafetySnapshot(
                    new MapVtfSafetyReport(
                        Array.Empty<string>(),
                        0,
                        Array.Empty<MapVtfSkippedFile>(),
                        Array.Empty<MapVtfSkipReasonSummary>()),
                    new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase));
            }
        }

        private async Task<bool> RunVtfCmdAsync(string arguments)
        {
            try
            {
                using var process = new Process();
                process.StartInfo.FileName = _vtfCmdFilePath;
                process.StartInfo.Arguments = arguments;
                process.StartInfo.UseShellExecute = false;
                process.StartInfo.CreateNoWindow = true;
                process.StartInfo.RedirectStandardOutput = true;
                process.StartInfo.RedirectStandardError = true;
                process.Start();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                await Task.WhenAny(process.WaitForExitAsync(), Task.Delay(TimeSpan.FromMinutes(2)));
                return process.HasExited && process.ExitCode == 0;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return false;
            }
        }

        private static bool TryReadHeader(string filePath, out VtfHeader header)
        {
            header = default;

            try
            {
                using FileStream stream = File.OpenRead(filePath);
                using BinaryReader reader = new BinaryReader(stream);

                if (reader.ReadInt32() != 0x465456)
                    return false;

                header = new VtfHeader
                {
                    MajorVersion = reader.ReadInt32(),
                    MinorVersion = reader.ReadInt32(),
                    HeaderSize = reader.ReadInt32(),
                    Width = reader.ReadInt16(),
                    Height = reader.ReadInt16(),
                    Flags = reader.ReadInt32(),
                    Frames = reader.ReadInt16(),
                    FirstFrame = reader.ReadInt16()
                };
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static async Task ChangeVtfVersionTo74Async(string vtfFilePath, VtfHeader header)
        {
            if (header.MinorVersion != 5)
                return;

            await Task.Yield();

            using FileStream stream = File.Open(vtfFilePath, FileMode.Open, FileAccess.Write, FileShare.None);
            using BinaryWriter writer = new BinaryWriter(stream);
            writer.Seek(8, SeekOrigin.Begin);
            writer.Write(4);
        }

        private static bool HasSparseAlphaCoverage(string imagePath)
        {
            try
            {
                using FileStream stream = new FileStream(imagePath, FileMode.Open, FileAccess.Read, FileShare.Read);
                using Image image = Image.FromStream(stream);
                using Bitmap bitmap = new Bitmap(image);

                double sumAlpha = 0;
                int nonZeroAlphaPixels = 0;
                int totalPixels = bitmap.Width * bitmap.Height;
                if (totalPixels == 0)
                    return false;

                for (int x = 0; x < bitmap.Width; x++)
                {
                    for (int y = 0; y < bitmap.Height; y++)
                    {
                        byte alpha = bitmap.GetPixel(x, y).A;
                        sumAlpha += alpha;
                        if (alpha > 0)
                            nonZeroAlphaPixels++;
                    }
                }

                double meanAlpha = sumAlpha / totalPixels;
                double nonZeroAlphaCoverage = (nonZeroAlphaPixels * 100.0) / totalPixels;
                return meanAlpha <= SparseAlphaMeanThreshold &&
                       nonZeroAlphaCoverage <= SparseAlphaCoverageThreshold;
            }
            catch
            {
                return false;
            }
        }

        private static bool IsAlphaFullyOpaque(string imagePath)
        {
            try
            {
                using FileStream stream = new FileStream(imagePath, FileMode.Open, FileAccess.Read, FileShare.Read);
                using Image image = Image.FromStream(stream);
                using Bitmap bitmap = new Bitmap(image);

                for (int x = 0; x < bitmap.Width; x++)
                {
                    for (int y = 0; y < bitmap.Height; y++)
                    {
                        if (bitmap.GetPixel(x, y).A < byte.MaxValue)
                            return false;
                    }
                }

                return true;
            }
            catch
            {
                return false;
            }
        }

        private static void TryDelete(string path)
        {
            try
            {
                if (Directory.Exists(path))
                    Directory.Delete(path, true);
            }
            catch
            {
            }
        }

        private static string FormatBytes(long bytes)
        {
            double value = bytes;
            string[] units = { "B", "KB", "MB", "GB", "TB" };
            int unitIndex = 0;

            while (value >= 1024 && unitIndex < units.Length - 1)
            {
                value /= 1024;
                unitIndex++;
            }

            return $"{value:0.##} {units[unitIndex]}";
        }

        private sealed record SafetySnapshot(
            MapVtfSafetyReport SafeReport,
            IReadOnlyDictionary<string, string> SkipReasons);

        private readonly record struct MagickVtfResult(bool Applied, string Reason);

        private struct VtfHeader
        {
            public int MajorVersion { get; init; }
            public int MinorVersion { get; init; }
            public int HeaderSize { get; init; }
            public short Width { get; init; }
            public short Height { get; init; }
            public int Flags { get; init; }
            public short Frames { get; init; }
            public short FirstFrame { get; init; }
        }
    }
}
