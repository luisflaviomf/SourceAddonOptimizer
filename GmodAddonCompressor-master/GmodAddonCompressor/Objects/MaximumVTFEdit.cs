using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Tools;
using GmodAddonCompressor.Systems.Vtf.Universal;
using Microsoft.Extensions.Logging;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Objects
{
    internal sealed record MaximumVtfProgress(
        string RelativePath,
        string Stage,
        string Candidate,
        long OriginalBytes,
        long ResultBytes,
        int AcceptedCandidates,
        int RejectedCandidates,
        double DurationMilliseconds);

    internal sealed record MaximumVtfBatchRow(
        string RelativePath,
        UniversalVtfProfileKind Profile,
        int SelectedLevel,
        UniversalVtfFilterRoute Route,
        string Reason,
        IReadOnlyList<string> SemanticClasses,
        bool HasUnknownConsumer,
        long OriginalBytes,
        long ResultBytes,
        long SavedBytes,
        string OriginalSha256,
        string ResultSha256,
        int OriginalWidth,
        int OriginalHeight,
        int ResultWidth,
        int ResultHeight,
        string CandidatePath);

    internal sealed class MaximumVTFEdit : ICompress, ICompressPreparation, ICompressBatch
    {
        private static readonly HashSet<string> SnapshotExtensions = new(
            new[] { ".vtf", ".vmt", ".lua", ".pcf" },
            StringComparer.OrdinalIgnoreCase);

        private static readonly JsonSerializerOptions ReportJsonOptions = CreateJsonOptions();

        private readonly string _addonRoot;
        private readonly UniversalVtfProfileKind _profile;
        private readonly int _parallelism;
        private readonly ILogger _logger = LogSystem.CreateLogger<MaximumVTFEdit>();
        private readonly Stopwatch _stopwatch = new();
        private readonly string _workRoot;
        private readonly string _snapshotRoot;
        private readonly string _candidateRoot;
        private UniversalVtfTreeSnapshot? _addonSnapshot;
        private UniversalVtfTreeSnapshot? _externalSnapshot;
        private IReadOnlyDictionary<string, UniversalVtfTreeFile>? _snapshotVtfs;
        private Exception? _prepareFailure;
        private bool _prepared;

        internal MaximumVTFEdit(
            string addonRoot,
            UniversalVtfProfileKind profile = UniversalVtfProfileKind.Balanced,
            int parallelism = 10)
        {
            _addonRoot = Path.GetFullPath(addonRoot);
            if (!Directory.Exists(_addonRoot))
            {
                throw new DirectoryNotFoundException(_addonRoot);
            }

            if (!Enum.IsDefined(typeof(UniversalVtfProfileKind), profile))
            {
                throw new ArgumentOutOfRangeException(nameof(profile));
            }

            _profile = profile;
            _parallelism = Math.Clamp(parallelism, 1, 10);
            string addonName = Path.GetFileName(_addonRoot.TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar));
            _workRoot = Path.Combine(
                ToolPaths.WorkRoot,
                "universal-vtf-maximum",
                $"{addonName}_{DateTime.UtcNow:yyyyMMdd_HHmmss}_{Guid.NewGuid():N}");
            _snapshotRoot = Path.Combine(_workRoot, "source-snapshot");
            _candidateRoot = Path.Combine(_workRoot, "candidates");
        }

        internal event Action<MaximumVtfProgress>? ProgressChanged;

        internal string ReportPath => Path.Combine(
            _workRoot,
            "maximum-universal-vtf-report.json");

        internal string RollbackRoot => _snapshotRoot;

        public void Prepare()
        {
            if (_prepared)
            {
                return;
            }

            try
            {
                EnsureExternalWorkRoot();
                ProgressChanged?.Invoke(new MaximumVtfProgress(
                    "snapshot",
                    "analyzing",
                    "external immutable source snapshot",
                    0,
                    0,
                    0,
                    0,
                    0));
                _addonSnapshot = UniversalVtfTreeInventory.Capture(_addonRoot);
                CopyRelevantSnapshot(_addonSnapshot);
                _externalSnapshot = UniversalVtfTreeInventory.Capture(_snapshotRoot);
                _snapshotVtfs = _externalSnapshot.Files
                    .Where(file => Path.GetExtension(file.RelativePath)
                        .Equals(".vtf", StringComparison.OrdinalIgnoreCase))
                    .ToDictionary(
                        file => file.RelativePath,
                        StringComparer.OrdinalIgnoreCase);
                if (_snapshotVtfs.Count == 0)
                {
                    throw new InvalidDataException("maximum_batch_snapshot_has_no_vtfs");
                }

                Directory.CreateDirectory(_candidateRoot);
                _prepared = true;
            }
            catch (Exception error)
            {
                _prepareFailure = error;
                throw;
            }
        }

        public Task Compress(string filePath) => Task.FromException(
            new InvalidOperationException(
                "Maximum VTF must be invoked through CompressBatchAsync, never per file."));

        public async Task CompressBatchAsync(
            IReadOnlyList<FileInfo> files,
            CancellationToken cancellationToken)
        {
            ArgumentNullException.ThrowIfNull(files);
            EnsurePrepared();
            cancellationToken.ThrowIfCancellationRequested();
            string[] relativePaths = ValidateBatch(files);
            if (relativePaths.Length == 0)
            {
                return;
            }

            _stopwatch.Restart();
            try
            {
                RequireTargetsUnchanged(relativePaths);
                UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(_snapshotRoot);
                if (!inventory.IsComplete)
                {
                    throw new InvalidDataException(
                        "maximum_batch_inventory_incomplete:" +
                        string.Join('|', inventory.Failures.Select(failure =>
                            $"{failure.RelativePath}:{failure.Stage}:{failure.Error}")));
                }

                var inventoryByPath = inventory.Entries.ToDictionary(
                    entry => entry.RelativePath,
                    StringComparer.OrdinalIgnoreCase);
                foreach (string relativePath in relativePaths)
                {
                    if (!inventoryByPath.ContainsKey(relativePath))
                    {
                        throw new InvalidDataException(
                            $"maximum_batch_inventory_missing:{relativePath}");
                    }
                }

                VtfConsumerGraph graph = VtfConsumerGraphBuilder.Build(
                    _snapshotRoot,
                    _snapshotRoot,
                    Array.Empty<string>());
                using UniversalVtfEncodingSession session =
                    UniversalVtfCandidateMaterializer.CreateEncodingSession();
                var prepared = new PreparedMaximumAsset[relativePaths.Length];
                int completed = 0;
                var parallelOptions = new ParallelOptions
                {
                    CancellationToken = cancellationToken,
                    MaxDegreeOfParallelism = _parallelism
                };
                await Parallel.ForEachAsync(
                    Enumerable.Range(0, relativePaths.Length),
                    parallelOptions,
                    async (index, token) =>
                    {
                        string relativePath = relativePaths[index];
                        prepared[index] = await PrepareAssetAsync(
                            index,
                            inventoryByPath[relativePath],
                            graph,
                            session,
                            token).ConfigureAwait(false);
                        int value = Interlocked.Increment(ref completed);
                        MaximumVtfBatchRow row = prepared[index].Row;
                        ProgressChanged?.Invoke(new MaximumVtfProgress(
                            row.RelativePath,
                            "accepted",
                            $"{row.Profile} level {row.SelectedLevel} ({row.Route})",
                            row.OriginalBytes,
                            row.ResultBytes,
                            value,
                            0,
                            _stopwatch.Elapsed.TotalMilliseconds));
                    }).ConfigureAwait(false);

                cancellationToken.ThrowIfCancellationRequested();
                PreparedMaximumAsset[] ordered = prepared
                    .OrderBy(asset => asset.Row.RelativePath, StringComparer.Ordinal)
                    .ToArray();
                AuditPreparedAssets(ordered);
                RequireTargetsUnchanged(relativePaths);
                PublishTransactional(ordered, cancellationToken);
                AuditPublishedAssets(ordered);
                _stopwatch.Stop();
                WriteCleanReport(ordered, session.Fingerprint);

                long originalBytes = ordered.Sum(asset => asset.Row.OriginalBytes);
                long resultBytes = ordered.Sum(asset => asset.Row.ResultBytes);
                ProgressChanged?.Invoke(new MaximumVtfProgress(
                    "summary",
                    "completed",
                    $"{ordered.Length} VTFs; rollback={RollbackRoot}; report={ReportPath}",
                    originalBytes,
                    resultBytes,
                    ordered.Length,
                    0,
                    _stopwatch.Elapsed.TotalMilliseconds));
            }
            catch (Exception error)
            {
                _stopwatch.Stop();
                TryWriteFailureReport(error, relativePaths);
                _logger.LogError(
                    error,
                    $"[MAXIMUM] Universal VTF batch failed closed. Snapshot preserved at {RollbackRoot}");
                long originalBytes = relativePaths.Sum(path =>
                    _snapshotVtfs?.GetValueOrDefault(path)?.Length ?? 0);
                ProgressChanged?.Invoke(new MaximumVtfProgress(
                    "batch",
                    "rejected",
                    $"{error.GetType().Name}; rollback={RollbackRoot}",
                    originalBytes,
                    originalBytes,
                    0,
                    relativePaths.Length,
                    _stopwatch.Elapsed.TotalMilliseconds));
                throw;
            }
        }

        private async Task<PreparedMaximumAsset> PrepareAssetAsync(
            int index,
            UniversalVtfInventoryEntry entry,
            VtfConsumerGraph graph,
            UniversalVtfEncodingSession session,
            CancellationToken cancellationToken)
        {
            string source = CombineContained(_snapshotRoot, entry.RelativePath);
            string assetRoot = Path.Combine(_candidateRoot, index.ToString("D6"));
            Directory.CreateDirectory(assetRoot);
            UniversalVtfSemanticDecision semantics = UniversalVtfSemanticResolver.Resolve(
                entry,
                graph);
            IReadOnlyList<UniversalVtfCandidateArtifact> candidates =
                await UniversalVtfCorpusBuilder.MaterializeCandidatesAsync(
                    source,
                    entry,
                    semantics,
                    assetRoot,
                    session,
                    cancellationToken).ConfigureAwait(false);
            UniversalVtfAttemptMatrix matrix = UniversalVtfSelector.BuildAttempts(
                entry,
                candidates,
                semantics);
            UniversalVtfProfileSelection selection = UniversalVtfSelector.Select(
                matrix,
                UniversalVtfRuntimeCaps.Empty);
            UniversalVtfSelectedLevel selected = Select(selection, _profile);
            VtfContainerLayout final = VtfContainerLayoutReader.Read(selected.CandidatePath);
            var row = new MaximumVtfBatchRow(
                entry.RelativePath,
                _profile,
                selected.Level,
                selected.Route,
                selected.Reason,
                semantics.Classes
                    .Select(value => value.ToString())
                    .OrderBy(value => value, StringComparer.Ordinal)
                    .ToArray(),
                semantics.HasUnknownConsumer,
                entry.Bytes,
                final.Document.FileLength,
                checked(entry.Bytes - final.Document.FileLength),
                entry.Sha256,
                final.FileSha256,
                entry.Width,
                entry.Height,
                final.Document.Width,
                final.Document.Height,
                Path.GetFullPath(selected.CandidatePath));
            return new PreparedMaximumAsset(row, source);
        }

        private void AuditPreparedAssets(IReadOnlyList<PreparedMaximumAsset> assets)
        {
            foreach (PreparedMaximumAsset asset in assets)
            {
                VtfContainerLayout layout = VtfContainerLayoutReader.Read(asset.Row.CandidatePath);
                if (!layout.FileSha256.Equals(
                        asset.Row.ResultSha256,
                        StringComparison.OrdinalIgnoreCase) ||
                    layout.Document.FileLength != asset.Row.ResultBytes ||
                    layout.Document.Width != asset.Row.ResultWidth ||
                    layout.Document.Height != asset.Row.ResultHeight ||
                    layout.Document.HighResFormat is not (13 or 15) ||
                    asset.Row.SelectedLevel < 1)
                {
                    throw new InvalidDataException(
                        $"maximum_batch_candidate_audit:{asset.Row.RelativePath}");
                }
            }
        }

        private void PublishTransactional(
            IReadOnlyList<PreparedMaximumAsset> assets,
            CancellationToken cancellationToken)
        {
            var published = new List<PreparedMaximumAsset>();
            try
            {
                foreach (PreparedMaximumAsset asset in assets)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    string destination = CombineContained(_addonRoot, asset.Row.RelativePath);
                    ReplaceFromExternalFile(asset.Row.CandidatePath, destination);
                    published.Add(asset);
                }
            }
            catch
            {
                RollBack(published);
                throw;
            }
        }

        private void AuditPublishedAssets(IReadOnlyList<PreparedMaximumAsset> assets)
        {
            try
            {
                foreach (PreparedMaximumAsset asset in assets)
                {
                    string destination = CombineContained(_addonRoot, asset.Row.RelativePath);
                    var file = new FileInfo(destination);
                    if (!file.Exists || file.Length != asset.Row.ResultBytes ||
                        !Sha256(destination).Equals(
                            asset.Row.ResultSha256,
                            StringComparison.OrdinalIgnoreCase))
                    {
                        throw new InvalidDataException(
                            $"maximum_batch_post_publish_audit:{asset.Row.RelativePath}");
                    }
                }
            }
            catch
            {
                RollBack(assets);
                throw;
            }
        }

        private void RollBack(IEnumerable<PreparedMaximumAsset> assets)
        {
            Exception? rollbackFailure = null;
            foreach (PreparedMaximumAsset asset in assets.Reverse())
            {
                try
                {
                    string destination = CombineContained(_addonRoot, asset.Row.RelativePath);
                    ReplaceFromExternalFile(asset.SnapshotPath, destination);
                }
                catch (Exception error)
                {
                    rollbackFailure ??= error;
                    _logger.LogError(
                        error,
                        $"[MAXIMUM] Rollback failed for {asset.Row.RelativePath}; " +
                        $"external source remains at {asset.SnapshotPath}");
                }
            }

            if (rollbackFailure is not null)
            {
                throw new IOException(
                    $"Maximum VTF rollback incomplete; recover from {RollbackRoot}.",
                    rollbackFailure);
            }
        }

        private void RequireTargetsUnchanged(IEnumerable<string> relativePaths)
        {
            if (_snapshotVtfs is null)
            {
                throw new InvalidOperationException("Maximum VTF snapshot was not initialized.");
            }

            foreach (string relativePath in relativePaths)
            {
                if (!_snapshotVtfs.TryGetValue(
                        relativePath,
                        out UniversalVtfTreeFile? expected))
                {
                    throw new InvalidDataException(
                        $"maximum_batch_path_not_snapshotted:{relativePath}");
                }

                string current = CombineContained(_addonRoot, relativePath);
                var file = new FileInfo(current);
                if (!file.Exists || file.Length != expected.Length ||
                    !Sha256(current).Equals(expected.Sha256, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException(
                        $"maximum_batch_source_changed:{relativePath}");
                }
            }
        }

        private string[] ValidateBatch(IReadOnlyList<FileInfo> files)
        {
            var paths = new List<string>(files.Count);
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (FileInfo file in files)
            {
                if (file is null)
                {
                    throw new ArgumentException("Batch contains a null file.", nameof(files));
                }

                string fullPath = Path.GetFullPath(file.FullName);
                string relativePath = RelativeContained(_addonRoot, fullPath);
                if (!Path.GetExtension(relativePath)
                        .Equals(".vtf", StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException(
                        $"maximum_batch_non_vtf:{relativePath}");
                }

                if (!seen.Add(relativePath))
                {
                    throw new InvalidDataException(
                        $"maximum_batch_duplicate_path:{relativePath}");
                }

                paths.Add(relativePath);
            }

            return paths.OrderBy(path => path, StringComparer.Ordinal).ToArray();
        }

        private void CopyRelevantSnapshot(UniversalVtfTreeSnapshot snapshot)
        {
            Directory.CreateDirectory(_snapshotRoot);
            foreach (UniversalVtfTreeFile file in snapshot.Files.Where(file =>
                         SnapshotExtensions.Contains(Path.GetExtension(file.RelativePath))))
            {
                string source = CombineContained(_addonRoot, file.RelativePath);
                string destination = CombineContained(_snapshotRoot, file.RelativePath);
                Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
                File.Copy(source, destination, overwrite: false);
                var copied = new FileInfo(destination);
                if (copied.Length != file.Length ||
                    !Sha256(destination).Equals(file.Sha256, StringComparison.OrdinalIgnoreCase) ||
                    !Sha256(source).Equals(file.Sha256, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException(
                        $"maximum_batch_snapshot_copy_mismatch:{file.RelativePath}");
                }
            }
        }

        private void EnsureExternalWorkRoot()
        {
            Directory.CreateDirectory(ToolPaths.WorkRoot);
            string addonPrefix = _addonRoot.TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            string workPrefix = Path.GetFullPath(_workRoot).TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (workPrefix.StartsWith(addonPrefix, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Maximum VTF work root must be external to the addon authority.");
            }

            if (Directory.Exists(_workRoot) || File.Exists(_workRoot))
            {
                throw new IOException($"Maximum VTF work root already exists: {_workRoot}");
            }

            Directory.CreateDirectory(_workRoot);
        }

        private void EnsurePrepared()
        {
            if (_prepareFailure is not null)
            {
                throw new InvalidOperationException(
                    "Maximum VTF external snapshot failed; no files may be replaced.",
                    _prepareFailure);
            }

            if (!_prepared || _addonSnapshot is null || _externalSnapshot is null ||
                _snapshotVtfs is null)
            {
                throw new InvalidOperationException(
                    "Maximum VTF must be prepared before its batch is run.");
            }
        }

        private void WriteCleanReport(
            IReadOnlyList<PreparedMaximumAsset> assets,
            UniversalVtfToolchainFingerprint fingerprint)
        {
            long originalBytes = assets.Sum(asset => asset.Row.OriginalBytes);
            long resultBytes = assets.Sum(asset => asset.Row.ResultBytes);
            WriteReport(new
            {
                schema = "maximum-universal-vtf-batch-v1",
                status = "CLEAN",
                profile = _profile,
                addonRoot = _addonRoot,
                workRoot = _workRoot,
                rollbackRoot = RollbackRoot,
                reportPath = ReportPath,
                durationMilliseconds = _stopwatch.Elapsed.TotalMilliseconds,
                toolchainSha256 = fingerprint.Sha256,
                policyFixtureSha256 = UniversalVtfPolicy.V1.FixtureSha256,
                sourceTree = _addonSnapshot,
                externalSnapshotTree = _externalSnapshot,
                files = assets.Count,
                originalBytes,
                resultBytes,
                savedBytes = checked(originalBytes - resultBytes),
                rows = assets.Select(asset => asset.Row).ToArray()
            });
        }

        private void TryWriteFailureReport(Exception error, IReadOnlyList<string> relativePaths)
        {
            try
            {
                WriteReport(new
                {
                    schema = "maximum-universal-vtf-batch-v1",
                    status = "DIRTY",
                    profile = _profile,
                    addonRoot = _addonRoot,
                    workRoot = _workRoot,
                    rollbackRoot = RollbackRoot,
                    durationMilliseconds = _stopwatch.Elapsed.TotalMilliseconds,
                    error = $"{error.GetType().Name}:{error.Message}",
                    files = relativePaths
                });
            }
            catch (Exception reportError)
            {
                _logger.LogWarning(
                    reportError,
                    $"[MAXIMUM] Could not write failure report under {_workRoot}");
            }
        }

        private void WriteReport(object report)
        {
            string path = ReportPath;
            string target = File.Exists(path)
                ? Path.Combine(
                    _workRoot,
                    $"maximum-universal-vtf-report-{DateTime.UtcNow:yyyyMMdd_HHmmssfff}.json")
                : path;
            byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(report, ReportJsonOptions);
            using var stream = new FileStream(
                target,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.Read);
            stream.Write(bytes);
            stream.Flush(flushToDisk: true);
        }

        private static void ReplaceFromExternalFile(string source, string destination)
        {
            string fullDestination = Path.GetFullPath(destination);
            string directory = Path.GetDirectoryName(fullDestination)
                ?? throw new InvalidDataException("Replacement destination has no parent.");
            string staging = Path.Combine(
                directory,
                $".{Path.GetFileName(destination)}.universal-vtf-{Guid.NewGuid():N}.tmp");
            try
            {
                File.Copy(Path.GetFullPath(source), staging, overwrite: false);
                File.Move(staging, fullDestination, overwrite: true);
            }
            finally
            {
                if (File.Exists(staging))
                {
                    File.Delete(staging);
                }
            }
        }

        private static UniversalVtfSelectedLevel Select(
            UniversalVtfProfileSelection selection,
            UniversalVtfProfileKind profile) => profile switch
        {
            UniversalVtfProfileKind.Conservative => selection.Conservative,
            UniversalVtfProfileKind.Balanced => selection.Balanced,
            UniversalVtfProfileKind.Aggressive => selection.Aggressive,
            _ => throw new ArgumentOutOfRangeException(nameof(profile))
        };

        private static string CombineContained(string root, string relativePath)
        {
            string fullRoot = Path.GetFullPath(root).TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar);
            string combined = Path.GetFullPath(Path.Combine(
                fullRoot,
                relativePath.Replace('/', Path.DirectorySeparatorChar)));
            string prefix = fullRoot + Path.DirectorySeparatorChar;
            if (!combined.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException($"Path escaped authority: {relativePath}");
            }

            return combined;
        }

        private static string RelativeContained(string root, string fullPath)
        {
            string canonicalRoot = Path.GetFullPath(root).TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar);
            string canonicalPath = Path.GetFullPath(fullPath);
            string prefix = canonicalRoot + Path.DirectorySeparatorChar;
            if (!canonicalPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException(
                    $"Batch path is outside addon authority: {canonicalPath}");
            }

            return Path.GetRelativePath(canonicalRoot, canonicalPath).Replace('\\', '/');
        }

        private static string Sha256(string path)
        {
            using var stream = new FileStream(
                path,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read,
                1024 * 1024,
                FileOptions.SequentialScan);
            using SHA256 sha256 = SHA256.Create();
            return Convert.ToHexString(sha256.ComputeHash(stream)).ToLowerInvariant();
        }

        private static JsonSerializerOptions CreateJsonOptions()
        {
            var options = new JsonSerializerOptions
            {
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
                WriteIndented = true
            };
            options.Converters.Add(new JsonStringEnumConverter(JsonNamingPolicy.CamelCase));
            return options;
        }

        private sealed record PreparedMaximumAsset(
            MaximumVtfBatchRow Row,
            string SnapshotPath);
    }
}
