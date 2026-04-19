using GmodAddonCompressor.Bases;
using GmodAddonCompressor.CustomExtensions;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Properties;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Tools;
using GmodAddonCompressor.Systems.Vtf;
using ImageMagick;
using Microsoft.Extensions.Logging;
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Objects
{
    internal class VTFEdit : ImageEditBase, ICompress, ICompressPreparation
    {
        private const string ToolName = "VTFEdit";
        private const string ToolVersion = "1";

        private readonly string _vtfCmdFilePath;
        private readonly ILogger _logger = LogSystem.CreateLogger<VTFEdit>();
        private readonly Lazy<AddonVtfCompressionAnalysis> _analysisSnapshot;
        private readonly string _addonRoot;

        public VTFEdit()
            : this(CompressDirectoryContext.DirectoryPath)
        {
        }

        public VTFEdit(string addonRoot)
        {
            _addonRoot = addonRoot;
            string toolRoot = ToolExtractionSystem.EnsureExtracted(
                ToolName,
                ToolVersion,
                Resources.VTFEdit,
                new[] { Path.Combine("VTFEdit", "VTFCmd.exe") });

            _vtfCmdFilePath = Path.Combine(toolRoot, "VTFEdit", "VTFCmd.exe");
            _analysisSnapshot = new Lazy<AddonVtfCompressionAnalysis>(
                () => AddonVtfCompressionPlanner.Analyze(addonRoot),
                LazyThreadSafetyMode.ExecutionAndPublication);

            SetImageFileExtension(".png");
        }

        public void Prepare()
        {
            VmtSyntaxRepairService.RepairUnder(_addonRoot);
            _ = _analysisSnapshot.Value;
        }

        public async Task Compress(string vtfFilePath)
        {
            VtfFileModel? vtfInfoObject = GetVtfFileInfo(vtfFilePath);
            if (vtfInfoObject == null)
                return;

            VtfPipelineResult pipelineResult = await TryCompressWithSplitRebuildAsync(
                vtfFilePath,
                vtfInfoObject.Value,
                logPrefix: "[VTF]");

            if (pipelineResult.Applied)
                return;

            if (pipelineResult.IsOperationalFailure)
            {
                _logger.LogWarning(
                    $"[VTF] Preserving {vtfFilePath.GAC_ToLocalPath()} unchanged after unified pipeline issue: {pipelineResult.Reason}{BuildTelemetryFields(pipelineResult)}");
                return;
            }

            _logger.LogInformation(
                $"[VTF] Preserving {vtfFilePath.GAC_ToLocalPath()} unchanged: {pipelineResult.Reason}{BuildTelemetryFields(pipelineResult)}");
        }

        internal async Task<VtfPipelineResult> TryCompressWithSplitRebuildAsync(
            string vtfFilePath,
            VtfFileModel vtfInfo,
            string logPrefix)
        {
            if (vtfInfo.Frames > 1 || vtfInfo.Depth > 1)
                return new VtfPipelineResult(false, "animated_or_volume");

            string? relativePath = TryGetMaterialRelativePath(vtfFilePath, out string? parsedRelativePath)
                ? parsedRelativePath
                : null;
            string? textureKey = GetTextureKey(vtfFilePath, relativePath);
            AddonVtfFxProfile preliminaryFxProfile = _analysisSnapshot.Value.GetFxProfile(textureKey, relativePath);
            bool preferExportSplit =
                vtfInfo.HighResImageFormat == 15 &&
                preliminaryFxProfile.PreferredSourceRoute == AddonVtfSourceRoutePreference.ExportSplitFirst;

            long oldFileSize = new FileInfo(vtfFilePath).Length;
            string tempRoot = Path.Combine(
                Path.GetDirectoryName(vtfFilePath) ?? Path.GetTempPath(),
                "__split_vtf_tmp",
                $"{Path.GetFileNameWithoutExtension(vtfFilePath)}_{Guid.NewGuid():N}");
            Directory.CreateDirectory(tempRoot);

            try
            {
                (MagickImage? sourceImage, string sourceKind) = await TryCreateSourceImageAsync(
                    vtfFilePath,
                    vtfInfo,
                    tempRoot,
                    preferExportSplit);
                if (sourceImage == null)
                    return new VtfPipelineResult(false, sourceKind, SourceKind: sourceKind, IsOperationalFailure: true);

                using (sourceImage)
                {
                    if (ImageIsFullTransparent(sourceImage))
                        return new VtfPipelineResult(false, "fully_transparent_source", SourceKind: sourceKind);

                    bool fullyOpaqueAlpha = IsAlphaFullyOpaque(sourceImage);
                    if (!AddonVtfCompressionPlanner.TryCreatePlan(
                            vtfFilePath,
                            vtfInfo,
                            _analysisSnapshot.Value,
                            fullyOpaqueAlpha,
                            out AddonVtfCompressionPlan plan))
                    {
                        return new VtfPipelineResult(
                            false,
                            plan.Reason,
                            SourceKind: sourceKind,
                            TargetFormat: plan.TargetFormat,
                            IsFxSensitive: plan.FxProfile.IsSensitive,
                            FxGroup: plan.FxProfile.Group,
                            FxScore: plan.FxProfile.Score,
                            FxSignals: plan.FxProfile.SignalSummary);
                    }

                    bool isSingleColor = ImageIsSingleColor(sourceImage);
                    if (!TryGetResizeBounds(
                            (int)sourceImage.Width,
                            (int)sourceImage.Height,
                            isSingleColor,
                            out int resizeWidth,
                            out int resizeHeight))
                    {
                        return new VtfPipelineResult(
                            false,
                            "resize_guardrail",
                            SourceKind: sourceKind,
                            TargetFormat: plan.TargetFormat,
                            IsFxSensitive: plan.FxProfile.IsSensitive,
                            FxGroup: plan.FxProfile.Group,
                            FxScore: plan.FxProfile.Score,
                            FxSignals: plan.FxProfile.SignalSummary);
                    }

                    string splitPngPath = Path.Combine(tempRoot, "split_source.png");
                    bool preserveAlpha = !string.Equals(plan.TargetFormat, "DXT1", StringComparison.OrdinalIgnoreCase);
                    bool ignoreAspectRatio = isSingleColor || !ImageContext.KeepImageAspectRatio;
                    ApplyFxResizeFloor(
                        (int)sourceImage.Width,
                        (int)sourceImage.Height,
                        ignoreAspectRatio,
                        plan,
                        ref resizeWidth,
                        ref resizeHeight);

                    if (!VtfSplitChannelImageWriter.TryWriteResizedPng(
                            sourceImage,
                            splitPngPath,
                            resizeWidth,
                            resizeHeight,
                            ignoreAspectRatio,
                            preserveAlpha,
                            useAlphaAwareResize: preserveAlpha && plan.FxProfile.IsSensitive))
                    {
                        return new VtfPipelineResult(
                            false,
                            "split_write_failed",
                            SourceKind: sourceKind,
                            TargetFormat: plan.TargetFormat,
                            IsOperationalFailure: true,
                            IsFxSensitive: plan.FxProfile.IsSensitive,
                            FxGroup: plan.FxProfile.Group,
                            FxScore: plan.FxProfile.Score,
                            FxSignals: plan.FxProfile.SignalSummary,
                            ResizeWidth: resizeWidth,
                            ResizeHeight: resizeHeight);
                    }

                    await ImageToVtf(vtfInfo, splitPngPath, plan.TargetFormat, tempRoot);

                    string rebuiltVtfPath = Path.Combine(tempRoot, Path.GetFileNameWithoutExtension(splitPngPath) + ".vtf");
                    if (!File.Exists(rebuiltVtfPath))
                    {
                        return new VtfPipelineResult(
                            false,
                            "rebuilt_vtf_missing",
                            SourceKind: sourceKind,
                            TargetFormat: plan.TargetFormat,
                            IsOperationalFailure: true,
                            IsFxSensitive: plan.FxProfile.IsSensitive,
                            FxGroup: plan.FxProfile.Group,
                            FxScore: plan.FxProfile.Score,
                            FxSignals: plan.FxProfile.SignalSummary,
                            ResizeWidth: resizeWidth,
                            ResizeHeight: resizeHeight);
                    }

                    AddonVtfCompressionPlanner.PatchFlags(rebuiltVtfPath, vtfInfo.Flags);

                    long newFileSize = new FileInfo(rebuiltVtfPath).Length;
                    if (newFileSize >= oldFileSize)
                    {
                        return new VtfPipelineResult(
                            false,
                            "no_split_gain",
                            SourceKind: sourceKind,
                            TargetFormat: plan.TargetFormat,
                            IsFxSensitive: plan.FxProfile.IsSensitive,
                            FxGroup: plan.FxProfile.Group,
                            FxScore: plan.FxProfile.Score,
                            FxSignals: plan.FxProfile.SignalSummary,
                            ResizeWidth: resizeWidth,
                            ResizeHeight: resizeHeight);
                    }

                    File.Copy(rebuiltVtfPath, vtfFilePath, true);
                    _logger.LogInformation(
                        $"{logPrefix} Optimized {vtfFilePath.GAC_ToLocalPath()}: {FormatBytes(oldFileSize)} -> {FormatBytes(newFileSize)}" +
                        BuildTelemetryFields(plan, sourceKind, resizeWidth, resizeHeight));

                    return new VtfPipelineResult(true, "optimized");
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return new VtfPipelineResult(false, $"split_exception_{ex.GetType().Name}", IsOperationalFailure: true);
            }
            finally
            {
                TryDelete(tempRoot);
            }
        }

        private async Task<(MagickImage? Image, string SourceKind)> TryCreateSourceImageAsync(
            string vtfFilePath,
            VtfFileModel vtfInfo,
            string tempRoot,
            bool preferExportSplit)
        {
            if (!preferExportSplit &&
                VtfHighResImageDecoder.TryDecodeHighResRgba(vtfFilePath, vtfInfo, out byte[] rgba))
            {
                try
                {
                    var settings = new MagickReadSettings
                    {
                        Width = (uint)vtfInfo.Width,
                        Height = (uint)vtfInfo.Height,
                        Format = MagickFormat.Rgba,
                        Depth = 8
                    };
                    return (new MagickImage(rgba, settings), "raw_split");
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(
                        $"[VTF] Raw split source image creation failed for {vtfFilePath.GAC_ToLocalPath()}, falling back to export split: {ex.GetType().Name}");
                }
            }

            string exportSourceVtf = Path.Combine(tempRoot, Path.GetFileName(vtfFilePath));
            string exportedPng = Path.Combine(tempRoot, Path.GetFileNameWithoutExtension(vtfFilePath) + ".png");
            File.Copy(vtfFilePath, exportSourceVtf, true);
            await NormalizeVtfVersionForVtfCmdAsync(exportSourceVtf, vtfInfo);

            if (!await RunVtfCmdAsync($" -file \"{exportSourceVtf}\" -output \"{tempRoot}\" -exportformat \"png\""))
                return (null, "export_fallback_failed");

            if (!File.Exists(exportedPng))
                return (null, "export_fallback_missing_png");

            return (new MagickImage(exportedPng), "export_split");
        }

        private static bool TryGetMaterialRelativePath(string fullPath, out string? relativePath)
        {
            relativePath = null;

            string normalized = Path.GetFullPath(fullPath).Replace('\\', '/');
            int index = normalized.LastIndexOf("/materials/", StringComparison.OrdinalIgnoreCase);
            if (index < 0)
                return false;

            relativePath = normalized.Substring(index + 1);
            return true;
        }

        private static string? GetTextureKey(string fullPath, string? relativePath)
        {
            if (string.IsNullOrWhiteSpace(relativePath) &&
                !TryGetMaterialRelativePath(fullPath, out relativePath))
            {
                return null;
            }

            if (string.IsNullOrWhiteSpace(relativePath) ||
                !relativePath.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            string key = relativePath.Substring("materials/".Length, relativePath.Length - "materials/".Length - 4);
            return key.Trim('/').ToLowerInvariant();
        }

        private async Task<bool> RunVtfCmdAsync(string arguments)
        {
            Process? vtfCmdProcess = null;

            try
            {
                vtfCmdProcess = new Process();
                vtfCmdProcess.StartInfo.FileName = _vtfCmdFilePath;
                vtfCmdProcess.StartInfo.Arguments = arguments;
                vtfCmdProcess.StartInfo.UseShellExecute = false;
                vtfCmdProcess.StartInfo.CreateNoWindow = true;
                vtfCmdProcess.StartInfo.RedirectStandardOutput = true;
                vtfCmdProcess.StartInfo.RedirectStandardError = true;
                vtfCmdProcess.OutputDataReceived += (sender, args) => _logger.LogDebug(args.Data);
                vtfCmdProcess.ErrorDataReceived += (sender, args) => _logger.LogDebug(args.Data);
                vtfCmdProcess.Start();
                vtfCmdProcess.BeginOutputReadLine();
                vtfCmdProcess.BeginErrorReadLine();
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return false;
            }

            if (vtfCmdProcess == null)
                return false;

            await Task.WhenAny(vtfCmdProcess.WaitForExitAsync(), Task.Delay(TimeSpan.FromMinutes(2)));
            return vtfCmdProcess.HasExited && vtfCmdProcess.ExitCode == 0;
        }

        private static VtfFileModel? GetVtfFileInfo(string vtfFilePath)
        {
            return AddonVtfCompressionPlanner.TryReadMetadata(vtfFilePath, out VtfFileModel metadata)
                ? metadata
                : null;
        }

        private static string GetCompatibleVtfCmdVersion(VtfFileModel vtfInfo)
        {
            if (vtfInfo.MajorVersion == 7 && vtfInfo.MinorVersion > 4)
                return "7.4";

            return $"{vtfInfo.MajorVersion}.{vtfInfo.MinorVersion}";
        }

        private async Task NormalizeVtfVersionForVtfCmdAsync(string vtfFilePath, VtfFileModel vtfInfo)
        {
            if (GetCompatibleVtfCmdVersion(vtfInfo) == $"{vtfInfo.MajorVersion}.{vtfInfo.MinorVersion}")
                return;

            await Task.Yield();

            using FileStream stream = File.OpenWrite(vtfFilePath);
            using BinaryWriter writer = new BinaryWriter(stream);
            writer.Seek(8, SeekOrigin.Begin);
            writer.Write(4);
        }

        private async Task ImageToVtf(VtfFileModel vtfInfo, string imageFilePath, string targetFormat, string? pngDirectory = null)
        {
            if (string.IsNullOrEmpty(pngDirectory))
                pngDirectory = Path.GetDirectoryName(imageFilePath);

            string arguments = string.Empty;
            arguments += $" -file \"{imageFilePath}\"";
            arguments += $" -output \"{pngDirectory}\"";
            arguments += $" -format \"{targetFormat}\" -alphaformat \"{targetFormat}\"";
            arguments += $" -version \"{GetCompatibleVtfCmdVersion(vtfInfo)}\"";

            await RunVtfCmdAsync(arguments);
        }

        private static void ApplyFxResizeFloor(
            int originalWidth,
            int originalHeight,
            bool ignoreAspectRatio,
            AddonVtfCompressionPlan plan,
            ref int resizeWidth,
            ref int resizeHeight)
        {
            int minimumShortSide = plan.FxProfile.MinimumShortSide;
            if (!plan.FxProfile.IsSensitive ||
                minimumShortSide <= 0 ||
                originalWidth <= 0 ||
                originalHeight <= 0)
            {
                return;
            }

            if (ignoreAspectRatio)
            {
                resizeWidth = Math.Min(originalWidth, Math.Max(resizeWidth, minimumShortSide));
                resizeHeight = Math.Min(originalHeight, Math.Max(resizeHeight, minimumShortSide));
                return;
            }

            int originalShortSide = Math.Min(originalWidth, originalHeight);
            int resizedShortSide = Math.Min(resizeWidth, resizeHeight);
            if (resizedShortSide >= minimumShortSide || originalShortSide <= minimumShortSide)
                return;

            double scale = (double)minimumShortSide / originalShortSide;
            int desiredWidth = RoundDimensionUp((int)Math.Ceiling(originalWidth * scale), originalWidth);
            int desiredHeight = RoundDimensionUp((int)Math.Ceiling(originalHeight * scale), originalHeight);

            resizeWidth = Math.Max(resizeWidth, desiredWidth);
            resizeHeight = Math.Max(resizeHeight, desiredHeight);
        }

        private static int RoundDimensionUp(int desired, int original)
        {
            if (desired <= 0)
                return 1;

            if (desired >= original)
                return original;

            int candidate = 1;
            while (candidate < desired && candidate < original)
                candidate <<= 1;

            if (candidate > original)
                return original;

            return candidate;
        }

        private static string BuildTelemetryFields(
            AddonVtfCompressionPlan plan,
            string sourceKind,
            int resizeWidth,
            int resizeHeight)
        {
            return BuildTelemetryFields(
                reason: plan.Reason,
                targetFormat: plan.TargetFormat,
                sourceKind: sourceKind,
                isFxSensitive: plan.FxProfile.IsSensitive,
                fxGroup: plan.FxProfile.Group,
                fxScore: plan.FxProfile.Score,
                fxSignals: plan.FxProfile.SignalSummary,
                resizeWidth: resizeWidth,
                resizeHeight: resizeHeight);
        }

        private static string BuildTelemetryFields(VtfPipelineResult result)
        {
            return BuildTelemetryFields(
                reason: result.Reason,
                targetFormat: result.TargetFormat,
                sourceKind: result.SourceKind,
                isFxSensitive: result.IsFxSensitive,
                fxGroup: result.FxGroup,
                fxScore: result.FxScore,
                fxSignals: result.FxSignals,
                resizeWidth: result.ResizeWidth,
                resizeHeight: result.ResizeHeight);
        }

        private static string BuildTelemetryFields(
            string reason,
            string targetFormat,
            string sourceKind,
            bool isFxSensitive,
            string fxGroup,
            int fxScore,
            string fxSignals,
            int resizeWidth,
            int resizeHeight)
        {
            var sb = new StringBuilder();
            sb.Append(" | reason=").Append(NormalizeTelemetryValue(reason));
            sb.Append(" target=").Append(NormalizeTelemetryValue(targetFormat));
            sb.Append(" source=").Append(NormalizeTelemetryValue(sourceKind));
            sb.Append(" fx_sensitive=").Append(isFxSensitive ? "true" : "false");
            sb.Append(" fx_group=").Append(NormalizeTelemetryValue(fxGroup));
            sb.Append(" fx_score=").Append(fxScore);
            sb.Append(" resize=").Append(resizeWidth).Append('x').Append(resizeHeight);
            sb.Append(" fx_signals=").Append(NormalizeTelemetryValue(fxSignals));
            return sb.ToString();
        }

        private static string NormalizeTelemetryValue(string? value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return "none";

            return value.Trim().Replace(' ', '_');
        }

        private static void TryDelete(string path)
        {
            try
            {
                if (Directory.Exists(path))
                {
                    Directory.Delete(path, true);
                    TryDeleteEmptySplitRoot(Path.GetDirectoryName(path));
                }
                else if (File.Exists(path))
                    File.Delete(path);
            }
            catch
            {
            }
        }

        private static void TryDeleteEmptySplitRoot(string? path)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(path) || !Directory.Exists(path))
                    return;

                if (!string.Equals(Path.GetFileName(path), "__split_vtf_tmp", StringComparison.OrdinalIgnoreCase))
                    return;

                if (Directory.EnumerateFileSystemEntries(path).Any())
                    return;

                Directory.Delete(path, false);
            }
            catch
            {
            }
        }

        private static string FormatBytes(long value)
        {
            string[] units = { "B", "KB", "MB", "GB" };
            double scaled = value;
            int unitIndex = 0;
            while (scaled >= 1024 && unitIndex < units.Length - 1)
            {
                scaled /= 1024;
                unitIndex++;
            }

            return $"{scaled:0.##} {units[unitIndex]}";
        }

        internal readonly record struct VtfPipelineResult(
            bool Applied,
            string Reason,
            string SourceKind = "",
            string TargetFormat = "",
            bool IsOperationalFailure = false,
            bool IsFxSensitive = false,
            string FxGroup = "",
            int FxScore = 0,
            string FxSignals = "",
            int ResizeWidth = 0,
            int ResizeHeight = 0);
    }
}
