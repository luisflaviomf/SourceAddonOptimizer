using System.Security.Cryptography;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfCacheTelemetry(
    int TotalVtfs,
    int CompletedHits,
    int AnalysisHits,
    int Misses,
    int InvalidEntries,
    int ReusedVtfs,
    int RecalculatedVtfs,
    double HitRatePercent,
    double PreparationSeconds,
    double TotalBuildSeconds,
    string CacheSchema,
    string PreparationAlgorithmFingerprint);

internal sealed record UniversalVtfRunJournalFrozen(
    string Path,
    string Sha256);

internal sealed class UniversalVtfCacheRunJournal : IDisposable
{
    private readonly object _sync = new();
    private readonly FileStream _stream;
    private readonly CancellationToken _buildCancellation;
    private bool _finished;
    private bool _disposed;

    internal UniversalVtfCacheRunJournal(
        string cacheRoot,
        int totalVtfs,
        string policyFingerprint,
        string toolchainSha256,
        string bridgeSha256,
        string runtimeCapsSha256,
        CancellationToken buildCancellation)
    {
        string root = System.IO.Path.GetFullPath(cacheRoot).TrimEnd(
            System.IO.Path.DirectorySeparatorChar,
            System.IO.Path.AltDirectorySeparatorChar);
        string runs = System.IO.Path.Combine(root, "v1", "runs");
        Directory.CreateDirectory(runs);
        Path = System.IO.Path.Combine(runs, $"run-{Guid.NewGuid():N}.jsonl");
        _stream = new FileStream(
            Path,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.Read,
            4096,
            FileOptions.WriteThrough);
        _buildCancellation = buildCancellation;
        Append(new
        {
            schema = "universal-vtf-cache-run-journal-v1",
            type = "start",
            totalVtfs,
            policyFingerprint,
            toolchainSha256,
            bridgeSha256,
            runtimeCapsSha256,
            preparationAlgorithmFingerprint = UniversalVtfPreparationFingerprint.Current,
            startedUtc = DateTimeOffset.UtcNow.ToString("O")
        });
    }

    internal string Path { get; }

    internal void RecordAsset(
        string relativePath,
        UniversalVtfCacheAssetStatus status,
        string analysisKey,
        string completionKey,
        double elapsedMilliseconds)
    {
        if (!double.IsFinite(elapsedMilliseconds) || elapsedMilliseconds < 0)
        {
            throw new InvalidDataException("cache_journal_invalid_elapsed");
        }

        Append(new
        {
            schema = "universal-vtf-cache-run-journal-v1",
            type = "asset",
            relativePath,
            status,
            analysisKey,
            completionKey,
            elapsedMilliseconds
        });
    }

    internal UniversalVtfRunJournalFrozen Complete(UniversalVtfCacheTelemetry telemetry)
    {
        ArgumentNullException.ThrowIfNull(telemetry);
        lock (_sync)
        {
            ThrowIfDisposed();
            if (_finished)
            {
                throw new InvalidOperationException("cache_journal_already_finished");
            }

            AppendLocked(new
            {
                schema = "universal-vtf-cache-run-journal-v1",
                type = "complete",
                telemetry,
                completedUtc = DateTimeOffset.UtcNow.ToString("O")
            });
            _finished = true;
            _stream.Dispose();
            _disposed = true;
        }

        return new UniversalVtfRunJournalFrozen(Path, HashFile(Path));
    }

    public void Dispose()
    {
        lock (_sync)
        {
            if (_disposed)
            {
                return;
            }

            if (!_finished)
            {
                AppendLocked(new
                {
                    schema = "universal-vtf-cache-run-journal-v1",
                    type = _buildCancellation.IsCancellationRequested ? "cancelled" : "failed",
                    finishedUtc = DateTimeOffset.UtcNow.ToString("O")
                });
                _finished = true;
            }

            _stream.Dispose();
            _disposed = true;
        }
    }

    private void Append<T>(T value)
    {
        lock (_sync)
        {
            ThrowIfDisposed();
            if (_finished)
            {
                throw new InvalidOperationException("cache_journal_already_finished");
            }

            AppendLocked(value);
        }
    }

    private void AppendLocked<T>(T value)
    {
        byte[] bytes = UniversalVtfCanonicalJson.Serialize(value);
        _stream.Write(bytes);
        _stream.WriteByte((byte)'\n');
        _stream.Flush(flushToDisk: true);
    }

    private void ThrowIfDisposed()
    {
        if (_disposed)
        {
            throw new ObjectDisposedException(nameof(UniversalVtfCacheRunJournal));
        }
    }

    private static string HashFile(string path) => Convert.ToHexString(
        SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
}
