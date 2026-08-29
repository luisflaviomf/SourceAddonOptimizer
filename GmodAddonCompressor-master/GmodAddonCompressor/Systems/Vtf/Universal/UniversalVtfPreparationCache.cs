using System.Collections.Concurrent;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal enum UniversalVtfCacheHitKind
{
    Miss,
    Analysis,
    Completed
}

internal sealed record UniversalVtfCacheLookup(
    UniversalVtfCacheHitKind HitKind,
    UniversalVtfAnalysisReceipt? Analysis,
    UniversalVtfCompletionReceipt? Completion,
    IReadOnlyList<string> Issues,
    bool Quarantined = false);

internal sealed record UniversalVtfCachePublishResult(
    bool AnalysisPublished,
    bool CompletionPublished,
    int ObjectsPublished);

internal enum UniversalVtfCacheCommitPhase
{
    BeforeReceiptCommit,
    AfterReceiptCommit
}

internal interface IUniversalVtfCacheCommitObserver
{
    void Observe(UniversalVtfCacheCommitPhase phase);
}

internal sealed class UniversalVtfPreparationCache
{
    private const string OwnedStagingPrefix = "owned-";

    private static readonly ConcurrentDictionary<
        string,
        Lazy<Task<UniversalVtfCacheLookup>>> Flights = new(StringComparer.OrdinalIgnoreCase);

    private readonly string _root;
    private readonly string _versionRoot;
    private readonly string _objectsRoot;
    private readonly string _analysisRoot;
    private readonly string _completionRoot;
    private readonly string _stagingRoot;
    private readonly string _quarantineRoot;
    private readonly string _locksRoot;
    private readonly IUniversalVtfCacheCommitObserver? _observer;

    internal UniversalVtfPreparationCache(string cacheRoot)
        : this(cacheRoot, null)
    {
    }

    internal UniversalVtfPreparationCache(
        string cacheRoot,
        IUniversalVtfCacheCommitObserver? observer)
    {
        if (string.IsNullOrWhiteSpace(cacheRoot))
        {
            throw new ArgumentException("Cache root cannot be empty.", nameof(cacheRoot));
        }

        _root = NormalizeRoot(cacheRoot);
        _versionRoot = Path.Combine(_root, "v1");
        _objectsRoot = Path.Combine(_versionRoot, "objects", "sha256");
        _analysisRoot = Path.Combine(_versionRoot, "analysis");
        _completionRoot = Path.Combine(_versionRoot, "completed");
        _stagingRoot = Path.Combine(_versionRoot, "staging");
        _quarantineRoot = Path.Combine(_versionRoot, "quarantine");
        _locksRoot = Path.Combine(_versionRoot, "locks");
        _observer = observer;
        InitializeLayout();
    }

    internal async Task<UniversalVtfCacheLookup> GetOrCreateAsync(
        UniversalVtfAnalysisIdentity analysis,
        UniversalVtfCompletionIdentity completion,
        Func<CancellationToken, Task<UniversalVtfCachedAsset>> factory,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(analysis);
        ArgumentNullException.ThrowIfNull(completion);
        ArgumentNullException.ThrowIfNull(factory);
        string analysisKey = UniversalVtfCacheKey.Analysis(analysis);
        string completionKey = UniversalVtfCacheKey.Completion(completion);
        if (!completion.AnalysisKey.Equals(analysisKey, StringComparison.Ordinal))
        {
            throw new InvalidDataException("cache_factory_analysis_completion_identity_mismatch");
        }

        string flightKey = $"{_root}\0{completionKey}";
        var candidate = new Lazy<Task<UniversalVtfCacheLookup>>(
            () => GetOrCreateCoreAsync(
                analysis,
                completion,
                completionKey,
                factory,
                cancellationToken),
            LazyThreadSafetyMode.ExecutionAndPublication);
        Lazy<Task<UniversalVtfCacheLookup>> flight = Flights.GetOrAdd(flightKey, candidate);
        Task<UniversalVtfCacheLookup> task = flight.Value;
        try
        {
            return await task.WaitAsync(cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            if (task.IsCompleted)
            {
                Flights.TryRemove(
                    new KeyValuePair<string, Lazy<Task<UniversalVtfCacheLookup>>>(flightKey, flight));
            }
        }
    }

    private async Task<UniversalVtfCacheLookup> GetOrCreateCoreAsync(
        UniversalVtfAnalysisIdentity analysis,
        UniversalVtfCompletionIdentity completion,
        string completionKey,
        Func<CancellationToken, Task<UniversalVtfCachedAsset>> factory,
        CancellationToken cancellationToken)
    {
        UniversalVtfCacheLookup initial = await LookupAsync(
            analysis,
            completion,
            cancellationToken).ConfigureAwait(false);
        if (initial.HitKind == UniversalVtfCacheHitKind.Completed)
        {
            return initial;
        }

        await using FileStream cacheLock = await AcquireLockAsync(
            completionKey,
            cancellationToken).ConfigureAwait(false);
        UniversalVtfCacheLookup afterLock = await LookupAsync(
            analysis,
            completion,
            cancellationToken).ConfigureAwait(false);
        if (afterLock.HitKind == UniversalVtfCacheHitKind.Completed)
        {
            return afterLock;
        }

        UniversalVtfCachedAsset asset = await factory(cancellationToken).ConfigureAwait(false);
        UniversalVtfCacheContracts.Validate(asset);
        if (!CanonicalEquals(asset.Analysis.Identity, analysis) ||
            !CanonicalEquals(asset.Completion.Identity, completion))
        {
            throw new InvalidDataException("cache_factory_identity_drift");
        }

        await PublishAsync(asset, cancellationToken).ConfigureAwait(false);
        UniversalVtfCacheLookup published = await LookupAsync(
            analysis,
            completion,
            cancellationToken).ConfigureAwait(false);
        if (published.HitKind != UniversalVtfCacheHitKind.Completed)
        {
            throw new InvalidDataException(
                $"cache_publish_not_replayable:{string.Join('|', published.Issues)}");
        }

        return published;
    }

    internal async Task<UniversalVtfCacheLookup> LookupAsync(
        UniversalVtfAnalysisIdentity analysis,
        UniversalVtfCompletionIdentity? completion,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(analysis);
        cancellationToken.ThrowIfCancellationRequested();
        string analysisKey = UniversalVtfCacheKey.Analysis(analysis);
        string? completionKey = null;
        if (completion is not null)
        {
            completionKey = UniversalVtfCacheKey.Completion(completion);
            if (!completion.AnalysisKey.Equals(analysisKey, StringComparison.Ordinal))
            {
                return Miss("cache_lookup_analysis_completion_identity_mismatch");
            }
        }

        string analysisDirectory = EntryDirectory(_analysisRoot, analysisKey);
        string analysisReceiptPath = Path.Combine(analysisDirectory, "receipt.json");
        if (completionKey is not null)
        {
            string completionDirectory = EntryDirectory(_completionRoot, completionKey);
            string completionReceiptPath = Path.Combine(completionDirectory, "receipt.json");
            if (File.Exists(completionReceiptPath))
            {
                UniversalVtfCompletionReceipt completionReceipt;
                try
                {
                    completionReceipt = await ReadCompletionAsync(
                        completionReceiptPath,
                        completion!,
                        cancellationToken).ConfigureAwait(false);
                }
                catch (Exception error) when (IsCacheEvidenceError(error))
                {
                    return InvalidEntry(
                        completionDirectory,
                        "completed",
                        completionKey,
                        "cache_completed_invalid",
                        error);
                }

                UniversalVtfAnalysisReceipt analysisReceipt;
                try
                {
                    analysisReceipt = await ReadAnalysisAsync(
                        analysisReceiptPath,
                        analysis,
                        cancellationToken).ConfigureAwait(false);
                    await ValidateArtifactsAsync(
                        analysisReceipt.Artifacts.Values,
                        cancellationToken).ConfigureAwait(false);
                }
                catch (Exception error) when (IsCacheEvidenceError(error))
                {
                    return InvalidEntry(
                        analysisDirectory,
                        "analysis",
                        analysisKey,
                        "cache_analysis_invalid",
                        error);
                }

                try
                {
                    await ValidateArtifactsAsync(
                        completionReceipt.Profiles.Values.Select(profile => profile.FinalArtifact),
                        cancellationToken).ConfigureAwait(false);
                }
                catch (Exception error) when (IsCacheEvidenceError(error))
                {
                    return InvalidEntry(
                        completionDirectory,
                        "completed",
                        completionKey,
                        "cache_completed_artifact_invalid",
                        error);
                }

                return new UniversalVtfCacheLookup(
                    UniversalVtfCacheHitKind.Completed,
                    analysisReceipt,
                    completionReceipt,
                    Array.Empty<string>());
            }
        }

        if (!File.Exists(analysisReceiptPath))
        {
            return Miss();
        }

        try
        {
            UniversalVtfAnalysisReceipt analysisReceipt = await ReadAnalysisAsync(
                analysisReceiptPath,
                analysis,
                cancellationToken).ConfigureAwait(false);
            await ValidateArtifactsAsync(
                analysisReceipt.Artifacts.Values,
                cancellationToken).ConfigureAwait(false);
            return new UniversalVtfCacheLookup(
                UniversalVtfCacheHitKind.Analysis,
                analysisReceipt,
                null,
                Array.Empty<string>());
        }
        catch (Exception error) when (IsCacheEvidenceError(error))
        {
            return InvalidEntry(
                analysisDirectory,
                "analysis",
                analysisKey,
                "cache_analysis_invalid",
                error);
        }
    }

    internal async Task<UniversalVtfCachePublishResult> PublishAsync(
        UniversalVtfCachedAsset asset,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(asset);
        cancellationToken.ThrowIfCancellationRequested();
        UniversalVtfCacheContracts.Validate(asset);
        IReadOnlyDictionary<string, UniversalVtfArtifactReference> references =
            CollectArtifactReferences(asset);

        string staging = Path.Combine(
            _stagingRoot,
            $"{OwnedStagingPrefix}{Guid.NewGuid():N}");
        EnsureContained(staging);
        Directory.CreateDirectory(staging);
        bool cleanStaging = false;
        try
        {
            string stagedObjects = Path.Combine(staging, "objects");
            string stagedAnalysis = Path.Combine(staging, "analysis");
            string stagedCompletion = Path.Combine(staging, "completed");
            Directory.CreateDirectory(stagedObjects);
            Directory.CreateDirectory(stagedAnalysis);
            Directory.CreateDirectory(stagedCompletion);

            foreach ((string sha256, UniversalVtfArtifactReference reference) in references)
            {
                cancellationToken.ThrowIfCancellationRequested();
                string source = asset.ArtifactSources[sha256];
                string destination = Path.Combine(stagedObjects, $"{sha256}.vtf");
                await CopyAndFlushAsync(source, destination, cancellationToken)
                    .ConfigureAwait(false);
                ValidateBlob(destination, reference);
            }

            string stagedAnalysisReceipt = Path.Combine(stagedAnalysis, "receipt.json");
            string stagedCompletionReceipt = Path.Combine(stagedCompletion, "receipt.json");
            byte[] analysisBytes = UniversalVtfCanonicalJson.Serialize(asset.Analysis);
            byte[] completionBytes = UniversalVtfCanonicalJson.Serialize(asset.Completion);
            WriteAndFlush(stagedAnalysisReceipt, analysisBytes);
            WriteAndFlush(stagedCompletionReceipt, completionBytes);

            UniversalVtfAnalysisReceipt stagedAnalysisValue =
                UniversalVtfCanonicalJson.Deserialize<UniversalVtfAnalysisReceipt>(
                    await File.ReadAllBytesAsync(stagedAnalysisReceipt, cancellationToken)
                        .ConfigureAwait(false));
            UniversalVtfCompletionReceipt stagedCompletionValue =
                UniversalVtfCanonicalJson.Deserialize<UniversalVtfCompletionReceipt>(
                    await File.ReadAllBytesAsync(stagedCompletionReceipt, cancellationToken)
                        .ConfigureAwait(false));
            UniversalVtfCacheContracts.Validate(stagedAnalysisValue);
            UniversalVtfCacheContracts.Validate(stagedCompletionValue);

            _observer?.Observe(UniversalVtfCacheCommitPhase.BeforeReceiptCommit);
            cancellationToken.ThrowIfCancellationRequested();

            int objectsPublished = 0;
            foreach ((string sha256, UniversalVtfArtifactReference reference) in references)
            {
                string staged = Path.Combine(stagedObjects, $"{sha256}.vtf");
                if (PublishObject(staged, reference))
                {
                    objectsPublished++;
                }
            }

            bool analysisPublished = PublishReceiptDirectory(
                stagedAnalysis,
                EntryDirectory(_analysisRoot, asset.Analysis.AnalysisKey),
                analysisBytes);
            bool completionPublished = PublishReceiptDirectory(
                stagedCompletion,
                EntryDirectory(_completionRoot, asset.Completion.CompletionKey),
                completionBytes);

            _observer?.Observe(UniversalVtfCacheCommitPhase.AfterReceiptCommit);
            cancellationToken.ThrowIfCancellationRequested();
            cleanStaging = true;
            return new UniversalVtfCachePublishResult(
                analysisPublished,
                completionPublished,
                objectsPublished);
        }
        finally
        {
            if (cleanStaging)
            {
                DeleteOwnedStaging(staging);
            }
        }
    }

    internal string ResolveBlob(UniversalVtfArtifactReference reference)
    {
        UniversalVtfCacheContracts.Validate(reference);
        string path = BlobPath(reference.Sha256);
        EnsureSafeExistingPath(path);
        if (!File.Exists(path))
        {
            throw new FileNotFoundException($"cache_missing_blob:{reference.Sha256}", path);
        }

        return path;
    }

    private static IReadOnlyDictionary<string, UniversalVtfArtifactReference>
        CollectArtifactReferences(UniversalVtfCachedAsset asset)
    {
        var result = new Dictionary<string, UniversalVtfArtifactReference>(StringComparer.Ordinal);
        IEnumerable<UniversalVtfArtifactReference> all = asset.Analysis.Artifacts.Values
            .Concat(asset.Completion.Profiles.Values.Select(profile => profile.FinalArtifact));
        foreach (UniversalVtfArtifactReference reference in all)
        {
            if (result.TryGetValue(reference.Sha256, out UniversalVtfArtifactReference? existing) &&
                existing != reference)
            {
                throw new InvalidDataException(
                    $"cache_conflicting_artifact_metadata:{reference.Sha256}");
            }

            result[reference.Sha256] = reference;
        }

        return result;
    }

    private async Task<UniversalVtfAnalysisReceipt> ReadAnalysisAsync(
        string path,
        UniversalVtfAnalysisIdentity expected,
        CancellationToken cancellationToken)
    {
        if (!File.Exists(path))
        {
            throw new FileNotFoundException("cache_missing_analysis_receipt", path);
        }

        EnsureSafeExistingPath(path);
        byte[] bytes = await File.ReadAllBytesAsync(path, cancellationToken).ConfigureAwait(false);
        UniversalVtfAnalysisReceipt receipt =
            UniversalVtfCanonicalJson.Deserialize<UniversalVtfAnalysisReceipt>(bytes);
        UniversalVtfCacheContracts.Validate(receipt);
        if (!CanonicalEquals(receipt.Identity, expected))
        {
            throw new InvalidDataException("cache_analysis_identity_drift");
        }

        return receipt;
    }

    private async Task<UniversalVtfCompletionReceipt> ReadCompletionAsync(
        string path,
        UniversalVtfCompletionIdentity expected,
        CancellationToken cancellationToken)
    {
        if (!File.Exists(path))
        {
            throw new FileNotFoundException("cache_missing_completion_receipt", path);
        }

        EnsureSafeExistingPath(path);
        byte[] bytes = await File.ReadAllBytesAsync(path, cancellationToken).ConfigureAwait(false);
        UniversalVtfCompletionReceipt receipt =
            UniversalVtfCanonicalJson.Deserialize<UniversalVtfCompletionReceipt>(bytes);
        UniversalVtfCacheContracts.Validate(receipt);
        if (!CanonicalEquals(receipt.Identity, expected))
        {
            throw new InvalidDataException("cache_completion_identity_drift");
        }

        return receipt;
    }

    private async Task ValidateArtifactsAsync(
        IEnumerable<UniversalVtfArtifactReference> references,
        CancellationToken cancellationToken)
    {
        foreach (UniversalVtfArtifactReference reference in references
                     .DistinctBy(value => value.Sha256))
        {
            cancellationToken.ThrowIfCancellationRequested();
            string path = BlobPath(reference.Sha256);
            if (!File.Exists(path))
            {
                throw new FileNotFoundException($"cache_missing_blob:{reference.Sha256}", path);
            }

            EnsureSafeExistingPath(path);
            await Task.Run(() => ValidateBlob(path, reference), cancellationToken)
                .ConfigureAwait(false);
        }
    }

    private bool PublishObject(
        string staged,
        UniversalVtfArtifactReference reference)
    {
        string destination = BlobPath(reference.Sha256);
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        EnsureSafeExistingPath(Path.GetDirectoryName(destination)!);
        if (File.Exists(destination))
        {
            ValidateBlob(destination, reference);
            return false;
        }

        try
        {
            File.Move(staged, destination);
            ValidateBlob(destination, reference);
            return true;
        }
        catch (IOException) when (File.Exists(destination))
        {
            ValidateBlob(destination, reference);
            return false;
        }
    }

    private bool PublishReceiptDirectory(
        string stagedDirectory,
        string destinationDirectory,
        byte[] expectedBytes)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(destinationDirectory)!);
        EnsureSafeExistingPath(Path.GetDirectoryName(destinationDirectory)!);
        if (Directory.Exists(destinationDirectory))
        {
            string existingReceipt = Path.Combine(destinationDirectory, "receipt.json");
            EnsureSafeExistingPath(existingReceipt);
            if (!File.Exists(existingReceipt) ||
                !File.ReadAllBytes(existingReceipt).AsSpan().SequenceEqual(expectedBytes))
            {
                throw new InvalidDataException(
                    $"cache_determinism_conflict:{Path.GetFileName(destinationDirectory)}");
            }

            return false;
        }

        try
        {
            Directory.Move(stagedDirectory, destinationDirectory);
            byte[] committed = File.ReadAllBytes(
                Path.Combine(destinationDirectory, "receipt.json"));
            if (!committed.AsSpan().SequenceEqual(expectedBytes))
            {
                throw new InvalidDataException("cache_committed_receipt_drift");
            }

            return true;
        }
        catch (IOException) when (Directory.Exists(destinationDirectory))
        {
            string existingReceipt = Path.Combine(destinationDirectory, "receipt.json");
            if (!File.Exists(existingReceipt) ||
                !File.ReadAllBytes(existingReceipt).AsSpan().SequenceEqual(expectedBytes))
            {
                throw new InvalidDataException(
                    $"cache_determinism_conflict:{Path.GetFileName(destinationDirectory)}");
            }

            return false;
        }
    }

    private static async Task CopyAndFlushAsync(
        string source,
        string destination,
        CancellationToken cancellationToken)
    {
        string fullSource = Path.GetFullPath(source);
        if (!File.Exists(fullSource))
        {
            throw new FileNotFoundException("cache_artifact_source_missing", fullSource);
        }

        if ((File.GetAttributes(fullSource) & FileAttributes.ReparsePoint) != 0)
        {
            throw new InvalidDataException($"cache_artifact_source_reparse:{fullSource}");
        }

        await using var input = new FileStream(
            fullSource,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read,
            1024 * 1024,
            FileOptions.Asynchronous | FileOptions.SequentialScan);
        await using var output = new FileStream(
            destination,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None,
            1024 * 1024,
            FileOptions.Asynchronous | FileOptions.SequentialScan);
        await input.CopyToAsync(output, 1024 * 1024, cancellationToken).ConfigureAwait(false);
        await output.FlushAsync(cancellationToken).ConfigureAwait(false);
        output.Flush(flushToDisk: true);
    }

    private static void WriteAndFlush(string path, byte[] bytes)
    {
        using var stream = new FileStream(
            path,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None);
        stream.Write(bytes);
        stream.Flush(flushToDisk: true);
    }

    private static void ValidateBlob(
        string path,
        UniversalVtfArtifactReference reference)
    {
        try
        {
            VtfContainerLayout layout = VtfContainerLayoutReader.Read(path);
            if (!layout.FileSha256.Equals(reference.Sha256, StringComparison.Ordinal) ||
                layout.Document.FileLength != reference.Bytes ||
                layout.Document.Width != reference.Width ||
                layout.Document.Height != reference.Height ||
                layout.Document.Depth != reference.Depth ||
                layout.Document.Frames != reference.Frames ||
                layout.Document.Faces != reference.Faces ||
                layout.Document.MipCount != reference.MipCount ||
                layout.Document.HighResFormat != reference.Format)
            {
                throw new InvalidDataException($"cache_blob_metadata_drift:{reference.Sha256}");
            }
        }
        catch (Exception error) when (error is IOException or InvalidDataException or
                                      UnauthorizedAccessException or NotSupportedException)
        {
            throw new InvalidDataException(
                $"cache_blob_invalid:{reference.Sha256}:{error.Message}",
                error);
        }
    }

    private static bool CanonicalEquals<T>(T left, T right) =>
        UniversalVtfCanonicalJson.Serialize(left).AsSpan()
            .SequenceEqual(UniversalVtfCanonicalJson.Serialize(right));

    private async Task<FileStream> AcquireLockAsync(
        string key,
        CancellationToken cancellationToken)
    {
        UniversalVtfCacheKey.RequireSha(key, nameof(key));
        string directory = Path.Combine(_locksRoot, key[..2]);
        Directory.CreateDirectory(directory);
        EnsureSafeExistingPath(directory);
        string path = Path.Combine(directory, $"{key}.lock");
        EnsureContained(path);
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            try
            {
                var stream = new FileStream(
                    path,
                    FileMode.OpenOrCreate,
                    FileAccess.ReadWrite,
                    FileShare.None,
                    4096,
                    FileOptions.Asynchronous | FileOptions.WriteThrough);
                if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                {
                    await stream.DisposeAsync().ConfigureAwait(false);
                    throw new InvalidDataException($"cache_lock_reparse_point:{path}");
                }

                return stream;
            }
            catch (IOException)
            {
                await Task.Delay(25, cancellationToken).ConfigureAwait(false);
            }
        }
    }

    private string BlobPath(string sha256)
    {
        UniversalVtfCacheKey.RequireSha(sha256, nameof(sha256));
        string path = Path.Combine(_objectsRoot, sha256[..2], $"{sha256}.vtf");
        EnsureContained(path);
        return path;
    }

    private string EntryDirectory(string categoryRoot, string key)
    {
        UniversalVtfCacheKey.RequireSha(key, nameof(key));
        string path = Path.Combine(categoryRoot, key[..2], key);
        EnsureContained(path);
        return path;
    }

    private string ReceiptPath(string categoryRoot, string key) =>
        Path.Combine(EntryDirectory(categoryRoot, key), "receipt.json");

    private void InitializeLayout()
    {
        if (File.Exists(_root))
        {
            throw new IOException($"Cache root is a file: {_root}");
        }

        Directory.CreateDirectory(_root);
        EnsureSafeExistingPath(_root);
        foreach (string path in new[]
                 {
                     _versionRoot,
                     _objectsRoot,
                     _analysisRoot,
                     _completionRoot,
                     _stagingRoot,
                     _quarantineRoot,
                     _locksRoot
                 })
        {
            Directory.CreateDirectory(path);
            EnsureSafeExistingPath(path);
        }
    }

    private void EnsureSafeExistingPath(string path)
    {
        string full = EnsureContained(path);
        string relative = Path.GetRelativePath(_root, full);
        string current = _root;
        CheckNotReparse(current);
        if (relative == ".")
        {
            return;
        }

        foreach (string segment in relative.Split(
                     new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
                     StringSplitOptions.RemoveEmptyEntries))
        {
            current = Path.Combine(current, segment);
            if (File.Exists(current) || Directory.Exists(current))
            {
                CheckNotReparse(current);
            }
        }
    }

    private static void CheckNotReparse(string path)
    {
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
        {
            throw new InvalidDataException($"cache_reparse_point:{path}");
        }
    }

    private string EnsureContained(string path)
    {
        string full = Path.GetFullPath(path);
        string prefix = _root + Path.DirectorySeparatorChar;
        if (!full.Equals(_root, StringComparison.OrdinalIgnoreCase) &&
            !full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"cache_path_escape:{full}");
        }

        return full;
    }

    private void DeleteOwnedStaging(string staging)
    {
        string full = EnsureContained(staging);
        string? parent = Path.GetDirectoryName(full);
        if (!string.Equals(parent, _stagingRoot, StringComparison.OrdinalIgnoreCase) ||
            !Path.GetFileName(full).StartsWith(OwnedStagingPrefix, StringComparison.Ordinal) ||
            !Directory.Exists(full))
        {
            return;
        }

        Directory.Delete(full, recursive: true);
    }

    private UniversalVtfCacheLookup InvalidEntry(
        string entryDirectory,
        string kind,
        string key,
        string prefix,
        Exception error)
    {
        var issues = new List<string> { $"{prefix}:{error.Message}" };
        bool quarantined = false;
        try
        {
            quarantined = QuarantineEntry(entryDirectory, kind, key, issues[0]);
            if (quarantined)
            {
                issues.Add($"cache_quarantined:{kind}:{key}");
            }
        }
        catch (Exception quarantineError) when (IsCacheEvidenceError(quarantineError))
        {
            issues.Add($"cache_quarantine_failed:{quarantineError.Message}");
        }

        return new UniversalVtfCacheLookup(
            UniversalVtfCacheHitKind.Miss,
            null,
            null,
            issues,
            quarantined);
    }

    private bool QuarantineEntry(
        string entryDirectory,
        string kind,
        string key,
        string reason)
    {
        string source = EnsureContained(entryDirectory);
        if (!Directory.Exists(source))
        {
            return false;
        }

        EnsureSafeExistingPath(source);
        string quarantine = Path.Combine(
            _quarantineRoot,
            $"owned-{Guid.NewGuid():N}");
        EnsureContained(quarantine);
        Directory.CreateDirectory(quarantine);
        string evidence = Path.Combine(quarantine, $"{kind}-{key}");
        Directory.Move(source, evidence);
        var receipt = new UniversalVtfQuarantineReceipt(
            "universal-vtf-cache-quarantine-v1",
            kind,
            key,
            reason,
            DateTimeOffset.UtcNow.ToString("O"));
        WriteAndFlush(
            Path.Combine(quarantine, "quarantine.json"),
            UniversalVtfCanonicalJson.Serialize(receipt));
        return true;
    }

    private static string NormalizeRoot(string path) => Path.GetFullPath(path)
        .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);

    private static UniversalVtfCacheLookup Miss(params string[] issues) => new(
        UniversalVtfCacheHitKind.Miss,
        null,
        null,
        issues);

    private static bool IsCacheEvidenceError(Exception error) =>
        error is IOException or InvalidDataException or UnauthorizedAccessException or
            NotSupportedException;

    private sealed record UniversalVtfQuarantineReceipt(
        string Schema,
        string Kind,
        string Key,
        string Reason,
        string QuarantinedUtc);
}
