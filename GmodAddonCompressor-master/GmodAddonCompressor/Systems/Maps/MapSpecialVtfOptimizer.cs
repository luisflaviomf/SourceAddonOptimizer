using GmodAddonCompressor.Bases;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Properties;
using GmodAddonCompressor.Systems.Tools;
using Microsoft.Extensions.Logging;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Maps
{
    internal sealed class MapSpecialVtfOptimizationSummary
    {
        public int CandidateCount { get; init; }
        public long CandidateBytes { get; init; }
        public int OptimizedCount { get; init; }
        public long BeforeBytes { get; init; }
        public long AfterBytes { get; init; }
        public IReadOnlyList<string> OptimizedPaths { get; init; } = Array.Empty<string>();
    }

    internal sealed class MapSpecialVtfOptimizer : ImageEditBase
    {
        private const string ToolName = "VTFEdit";
        private const string ToolVersion = "1";

        private readonly ILogger _logger = LogSystem.CreateLogger<MapSpecialVtfOptimizer>();
        private readonly string _vtfCmdFilePath;

        public MapSpecialVtfOptimizer()
        {
            string toolRoot = ToolExtractionSystem.EnsureExtracted(
                ToolName,
                ToolVersion,
                Resources.VTFEdit,
                new[] { Path.Combine("VTFEdit", "VTFCmd.exe") });

            _vtfCmdFilePath = Path.Combine(toolRoot, "VTFEdit", "VTFCmd.exe");
            SetImageFileExtension(".png");
        }

        internal async Task<MapSpecialVtfOptimizationSummary> OptimizeSkyboxAsync(
            IReadOnlyList<MapVtfSkippedFile> skippedFiles,
            string workDir,
            Action<string>? log,
            Action<string, int, int>? progress)
        {
            var candidates = skippedFiles
                .Where(IsSkyboxCandidate)
                .OrderBy(file => file.RelativePath, StringComparer.OrdinalIgnoreCase)
                .ToArray();

            if (candidates.Length == 0)
            {
                return new MapSpecialVtfOptimizationSummary();
            }

            string tempRoot = Path.Combine(workDir, "special_vtf_opt");
            if (Directory.Exists(tempRoot))
                Directory.Delete(tempRoot, true);
            Directory.CreateDirectory(tempRoot);

            long beforeBytes = 0;
            long afterBytes = 0;
            int optimizedCount = 0;
            long candidateBytes = candidates.Sum(file => file.TotalBytes);
            var optimizedPaths = new List<string>();

            for (int index = 0; index < candidates.Length; index++)
            {
                var file = candidates[index];
                progress?.Invoke(file.FullPath, index + 1, candidates.Length);

                if (!TryReadMetadata(file.FullPath, out var metadata))
                {
                    log?.Invoke($"[MAP-OPT][SKYBOX] Preserved {file.RelativePath}: metadata unreadable.");
                    continue;
                }

                if (!TryGetFormatName(metadata.HighResFormat, out string? formatName) || string.IsNullOrWhiteSpace(formatName))
                {
                    log?.Invoke($"[MAP-OPT][SKYBOX] Preserved {file.RelativePath}: unsupported format id {metadata.HighResFormat}.");
                    continue;
                }

                string fileTempDir = Path.Combine(tempRoot, $"{index + 1:000}");
                Directory.CreateDirectory(fileTempDir);

                string exportedPngPath = Path.Combine(fileTempDir, Path.GetFileNameWithoutExtension(file.FullPath) + ".png");
                string rebuiltVtfPath = Path.Combine(fileTempDir, Path.GetFileName(file.FullPath));

                try
                {
                    bool exported = await ExportToPngAsync(file.FullPath, fileTempDir);
                    if (!exported || !File.Exists(exportedPngPath))
                    {
                        log?.Invoke($"[MAP-OPT][SKYBOX] Preserved {file.RelativePath}: export to PNG failed.");
                        continue;
                    }

                    string previousDirectoryContext = CompressDirectoryContext.DirectoryPath;
                    CompressDirectoryContext.DirectoryPath = fileTempDir;
                    try
                    {
                        await ImageCompress(exportedPngPath);
                    }
                    finally
                    {
                        if (!string.IsNullOrWhiteSpace(previousDirectoryContext))
                            CompressDirectoryContext.DirectoryPath = previousDirectoryContext;
                    }

                    bool rebuilt = await ImportFromPngAsync(exportedPngPath, fileTempDir, formatName, metadata);
                    if (!rebuilt || !File.Exists(rebuiltVtfPath))
                    {
                        log?.Invoke($"[MAP-OPT][SKYBOX] Preserved {file.RelativePath}: rebuild to VTF failed.");
                        continue;
                    }

                    PatchFlags(rebuiltVtfPath, metadata.Flags);

                    long rebuiltBytes = new FileInfo(rebuiltVtfPath).Length;
                    if (rebuiltBytes >= file.TotalBytes)
                    {
                        afterBytes += file.TotalBytes;
                        log?.Invoke($"[MAP-OPT][SKYBOX] Preserved {file.RelativePath}: rebuilt VTF was not smaller.");
                        continue;
                    }

                    File.Copy(rebuiltVtfPath, file.FullPath, true);
                    optimizedCount++;
                    optimizedPaths.Add(file.FullPath);
                    beforeBytes += file.TotalBytes;
                    afterBytes += rebuiltBytes;
                    log?.Invoke(
                        $"[MAP-OPT][SKYBOX] Optimized {file.RelativePath}: " +
                        $"{FormatBytes(file.TotalBytes)} -> {FormatBytes(rebuiltBytes)}");
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex.ToString());
                    log?.Invoke($"[MAP-OPT][SKYBOX] Preserved {file.RelativePath}: {ex.Message}");
                }
                finally
                {
                    try
                    {
                        if (Directory.Exists(fileTempDir))
                            Directory.Delete(fileTempDir, true);
                    }
                    catch
                    {
                    }
                }
            }

            if (Directory.Exists(tempRoot))
            {
                try
                {
                    Directory.Delete(tempRoot, true);
                }
                catch
                {
                }
            }

            return new MapSpecialVtfOptimizationSummary
            {
                CandidateCount = candidates.Length,
                CandidateBytes = candidateBytes,
                OptimizedCount = optimizedCount,
                BeforeBytes = beforeBytes,
                AfterBytes = afterBytes,
                OptimizedPaths = optimizedPaths
            };
        }

        private static bool IsSkyboxCandidate(MapVtfSkippedFile file)
        {
            bool hasSkyboxReason = file.Reasons.Contains("skybox_namespace", StringComparer.OrdinalIgnoreCase);
            if (!hasSkyboxReason)
                return false;

            foreach (string reason in file.Reasons)
            {
                if (!string.Equals(reason, "skybox_namespace", StringComparison.OrdinalIgnoreCase) &&
                    !string.Equals(reason, "skybox_hdr_name", StringComparison.OrdinalIgnoreCase))
                {
                    return false;
                }
            }

            return true;
        }

        private async Task<bool> ExportToPngAsync(string vtfFilePath, string outputDir)
        {
            string arguments = $" -file \"{vtfFilePath}\" -output \"{outputDir}\" -exportformat \"png\"";
            return await RunVtfCmdAsync(arguments);
        }

        private async Task<bool> ImportFromPngAsync(string pngFilePath, string outputDir, string formatName, MapSpecialVtfMetadata metadata)
        {
            string version = $"{metadata.MajorVersion}.{metadata.MinorVersion}";
            string arguments =
                $" -file \"{pngFilePath}\"" +
                $" -output \"{outputDir}\"" +
                $" -format \"{formatName}\"" +
                $" -alphaformat \"{formatName}\"" +
                $" -version \"{version}\"";

            return await RunVtfCmdAsync(arguments);
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

        private static void PatchFlags(string vtfFilePath, int originalFlags)
        {
            using FileStream stream = new FileStream(vtfFilePath, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            using BinaryWriter writer = new BinaryWriter(stream);
            writer.Seek(20, SeekOrigin.Begin);
            writer.Write(originalFlags);
        }

        private static bool TryGetFormatName(int formatId, out string? formatName)
        {
            formatName = formatId switch
            {
                3 => "BGR888",
                13 => "DXT1",
                15 => "DXT5",
                24 => "RGBA16161616F",
                _ => null
            };

            return !string.IsNullOrWhiteSpace(formatName);
        }

        private static bool TryReadMetadata(string vtfPath, out MapSpecialVtfMetadata metadata)
        {
            metadata = default;

            try
            {
                using FileStream stream = File.OpenRead(vtfPath);
                using BinaryReader reader = new BinaryReader(stream);

                byte[] signature = reader.ReadBytes(4);
                if (signature.Length != 4 || signature[0] != (byte)'V' || signature[1] != (byte)'T' || signature[2] != (byte)'F')
                    return false;

                int majorVersion = reader.ReadInt32();
                int minorVersion = reader.ReadInt32();
                _ = reader.ReadInt32(); // header size
                _ = reader.ReadInt16(); // width
                _ = reader.ReadInt16(); // height
                int flags = reader.ReadInt32();
                int frames = reader.ReadInt16();
                _ = reader.ReadInt16(); // first frame
                _ = reader.ReadBytes(4); // padding
                _ = reader.ReadSingle();
                _ = reader.ReadSingle();
                _ = reader.ReadSingle();
                _ = reader.ReadBytes(4); // padding
                _ = reader.ReadSingle(); // bumpmap scale
                int highResFormat = reader.ReadInt32();
                _ = reader.ReadByte(); // mip count
                _ = reader.ReadInt32(); // low res image format
                _ = reader.ReadByte(); // low res width
                _ = reader.ReadByte(); // low res height
                int depth = (majorVersion > 7 || (majorVersion == 7 && minorVersion >= 2)) ? reader.ReadInt16() : 1;

                metadata = new MapSpecialVtfMetadata(
                    majorVersion,
                    minorVersion,
                    flags,
                    frames,
                    depth,
                    highResFormat);
                return true;
            }
            catch
            {
                return false;
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

        private readonly struct MapSpecialVtfMetadata
        {
            public MapSpecialVtfMetadata(int majorVersion, int minorVersion, int flags, int frames, int depth, int highResFormat)
            {
                MajorVersion = majorVersion;
                MinorVersion = minorVersion;
                Flags = flags;
                Frames = frames;
                Depth = depth;
                HighResFormat = highResFormat;
            }

            public int MajorVersion { get; }
            public int MinorVersion { get; }
            public int Flags { get; }
            public int Frames { get; }
            public int Depth { get; }
            public int HighResFormat { get; }
        }
    }
}
