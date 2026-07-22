using GmodAddonCompressor.CustomExtensions;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Systems;
using Microsoft.Extensions.Logging;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using VtfMaximumLab.Encoding;
using VtfMaximumLab.Inventory;

namespace GmodAddonCompressor.Objects
{
    internal sealed class MagickPlusVTFEdit : ICompress, ICompressPreparation, ICompressFinalizer
    {
        private const string TempDirectoryName = "__magick_plus_vtf_tmp";

        private readonly string _addonRoot;
        private readonly VTFEdit _magickCompressor;
        private readonly ILogger _logger = LogSystem.CreateLogger<MagickPlusVTFEdit>();
        private IReadOnlyDictionary<string, VtfInventoryEntry> _inventory =
            new Dictionary<string, VtfInventoryEntry>(StringComparer.OrdinalIgnoreCase);
        private long _originalBytes;
        private long _magickBytes;
        private long _finalBytes;
        private int _processed;
        private int _eligible;
        private int _repacked;
        private int _repackFailures;

        internal MagickPlusVTFEdit(string addonRoot)
        {
            _addonRoot = Path.GetFullPath(addonRoot);
            _magickCompressor = new VTFEdit(_addonRoot);
        }

        public void Prepare()
        {
            _magickCompressor.Prepare();
            try
            {
                _inventory = VtfInventoryScanner.Scan(_addonRoot)
                    .ToDictionary(entry => Normalize(entry.RelativePath), StringComparer.OrdinalIgnoreCase);
                _logger.LogInformation($"[MAGICK+] Indexed {_inventory.Count} VTF files for lossless format opportunities.");
            }
            catch (Exception exception)
            {
                _inventory = new Dictionary<string, VtfInventoryEntry>(StringComparer.OrdinalIgnoreCase);
                _logger.LogWarning(
                    exception,
                    "[MAGICK+] Semantic inventory failed; continuing with the unchanged Magick pipeline.");
            }
        }

        public async Task Compress(string filePath)
        {
            string fullPath = Path.GetFullPath(filePath);
            long originalBytes = SafeLength(fullPath);
            string relativePath = Normalize(Path.GetRelativePath(_addonRoot, fullPath));
            string? textureTempRoot = null;
            string? originalSnapshotPath = null;

            if (_inventory.TryGetValue(relativePath, out VtfInventoryEntry? entry) &&
                VtfLosslessBc3Repack.CanRepack(entry))
            {
                Interlocked.Increment(ref _eligible);
                try
                {
                    string tempParent = Path.Combine(Path.GetDirectoryName(fullPath)!, TempDirectoryName);
                    textureTempRoot = Path.Combine(
                        tempParent,
                        $"{Path.GetFileNameWithoutExtension(fullPath)}_{Guid.NewGuid():N}");
                    Directory.CreateDirectory(textureTempRoot);
                    originalSnapshotPath = Path.Combine(textureTempRoot, "original.vtf");
                    File.Copy(fullPath, originalSnapshotPath, overwrite: true);
                }
                catch (Exception exception)
                {
                    Interlocked.Increment(ref _repackFailures);
                    originalSnapshotPath = null;
                    _logger.LogWarning(
                        exception,
                        $"[MAGICK+] Could not stage a lossless candidate for {fullPath.GAC_ToLocalPath()}; Magick fallback remains active.");
                }
            }

            try
            {
                await _magickCompressor.Compress(fullPath).ConfigureAwait(false);
                long magickBytes = SafeLength(fullPath);
                long finalBytes = magickBytes;

                if (!string.IsNullOrWhiteSpace(originalSnapshotPath) &&
                    File.Exists(originalSnapshotPath) &&
                    VtfLosslessBc3Repack.TryEstimateOutputBytes(originalSnapshotPath, out long estimatedBytes) &&
                    estimatedBytes < magickBytes)
                {
                    try
                    {
                        string losslessCandidatePath = Path.Combine(textureTempRoot!, "lossless.vtf");
                        VtfLosslessBc3Repack.Build(originalSnapshotPath, losslessCandidatePath);
                        long candidateBytes = SafeLength(losslessCandidatePath);
                        if (candidateBytes > 0 && candidateBytes < magickBytes)
                        {
                            File.Copy(losslessCandidatePath, fullPath, overwrite: true);
                            finalBytes = candidateBytes;
                            Interlocked.Increment(ref _repacked);
                            _logger.LogInformation(
                                $"[MAGICK+] Lossless BC3->BC1 repack selected for {fullPath.GAC_ToLocalPath()}: " +
                                $"{FormatBytes(magickBytes)} -> {FormatBytes(finalBytes)}; RGB, resolution and mipmaps identical.");
                        }
                    }
                    catch (Exception exception)
                    {
                        Interlocked.Increment(ref _repackFailures);
                        _logger.LogWarning(
                            exception,
                            $"[MAGICK+] Lossless candidate rejected for {fullPath.GAC_ToLocalPath()}; the Magick result was kept.");
                    }
                }

                Interlocked.Add(ref _originalBytes, originalBytes);
                Interlocked.Add(ref _magickBytes, magickBytes);
                Interlocked.Add(ref _finalBytes, finalBytes);
                Interlocked.Increment(ref _processed);
            }
            finally
            {
                TryDeleteTextureTemp(textureTempRoot);
            }
        }

        public Task CompleteAsync()
        {
            long savedVsMagick = Math.Max(0, _magickBytes - _finalBytes);
            _logger.LogInformation(
                $"[MAGICK+] Completed: processed={_processed}, eligible={_eligible}, repacked={_repacked}, " +
                $"candidate_failures={_repackFailures}, original={FormatBytes(_originalBytes)}, " +
                $"magick={FormatBytes(_magickBytes)}, final={FormatBytes(_finalBytes)}, " +
                $"saved_vs_magick={FormatBytes(savedVsMagick)} ({Percent(_magickBytes, _finalBytes):0.00}%).");
            return Task.CompletedTask;
        }

        private static long SafeLength(string path)
        {
            try
            {
                return File.Exists(path) ? new FileInfo(path).Length : 0;
            }
            catch
            {
                return 0;
            }
        }

        private void TryDeleteTextureTemp(string? textureTempRoot)
        {
            if (string.IsNullOrWhiteSpace(textureTempRoot))
                return;

            try
            {
                string tempParent = Path.GetFullPath(Path.Combine(
                    Path.GetDirectoryName(Path.GetDirectoryName(textureTempRoot)!)!,
                    TempDirectoryName));
                string target = Path.GetFullPath(textureTempRoot);
                string expectedPrefix = tempParent.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                if (!target.StartsWith(expectedPrefix, StringComparison.OrdinalIgnoreCase))
                {
                    _logger.LogWarning($"[MAGICK+] Refused to clean unexpected temporary path: {textureTempRoot}");
                    return;
                }

                if (Directory.Exists(target))
                    Directory.Delete(target, recursive: true);
                if (Directory.Exists(tempParent) && !Directory.EnumerateFileSystemEntries(tempParent).Any())
                    Directory.Delete(tempParent, recursive: false);
            }
            catch (Exception exception)
            {
                _logger.LogWarning(exception, $"[MAGICK+] Could not clean temporary files: {textureTempRoot}");
            }
        }

        private static string Normalize(string path) => path.Replace('\\', '/');
        private static double Percent(long before, long after) => before <= 0 ? 0 : (1.0 - (double)after / before) * 100;
        private static string FormatBytes(long bytes) => bytes >= 1024 * 1024
            ? $"{bytes / 1024d / 1024d:0.00} MB"
            : bytes >= 1024 ? $"{bytes / 1024d:0.00} KB" : $"{bytes} B";
    }
}
