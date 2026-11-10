using GmodAddonCompressor.CustomExtensions;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Objects;
using Microsoft.Extensions.Logging;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems
{
    internal class CompressAddonSystem
    {
        internal delegate void ProgressChangedEvent(string filePath, int fileIndex, int filesCount);
        internal delegate void CompletedCompressEvent();
        internal delegate void MaximumProgressEvent(MaximumVtfProgress progress);

        private sealed class CompressionBucket
        {
            public CompressionBucket(string name, int maxDegreeOfParallelism, List<FileInfo> files)
            {
                Name = name;
                MaxDegreeOfParallelism = maxDegreeOfParallelism;
                Files = files;
            }

            public string Name { get; }
            public int MaxDegreeOfParallelism { get; }
            public List<FileInfo> Files { get; }
        }

        private sealed class ProgressCounter
        {
            public int Value;
        }

        internal ProgressChangedEvent? e_ProgressChanged;
        internal CompletedCompressEvent? e_CompletedCompress;
        internal MaximumProgressEvent? e_MaximumProgress;

        private Queue<FileInfo> _registredFiles = new Queue<FileInfo>();
        private bool _hasStarted = false;
        private int _failureCount;
        internal bool HasFailures => Volatile.Read(ref _failureCount) != 0;
        private Thread? _compressThread = null;
        private CancellationTokenSource? _maximumBatchCancellation;
        private List<string> _validFileExtensions = new List<string>();
        private string _directoryPath;
        private Dictionary<string, ICompress> _compressServices = new Dictionary<string, ICompress>();
        private readonly ILogger _logger = LogSystem.CreateLogger<CompressAddonSystem>();
        private readonly Func<FileInfo, bool>? _fileFilter;
        private readonly CompressPipelineOptions _pipelineOptions;

        public CompressAddonSystem(string directoryPath, Func<FileInfo, bool>? fileFilter = null, CompressPipelineOptions? pipelineOptions = null)
        {
            _directoryPath = directoryPath;
            _fileFilter = fileFilter;
            _pipelineOptions = pipelineOptions ?? new CompressPipelineOptions();
            CompressDirectoryContext.DirectoryPath = _directoryPath;
        }

        internal void IncludeLUA()
        {
            string extension = ".lua";
            AddValidFileExtensions(extension);
            _compressServices.Add(extension, new LUAEdit());
        }

        internal void IncludeOGG()
        {
            string extension = ".ogg";
            AddValidFileExtensions(extension);
            _compressServices.Add(extension, new OGGEdit());
        }

        internal void IncludeMP3()
        {
            string extension = ".mp3";
            AddValidFileExtensions(extension);
            _compressServices.Add(extension, new MP3Edit());
        }

        internal void IncludeWAV()
        {
            string extension = ".wav";
            AddValidFileExtensions(extension);
            _compressServices.Add(extension, new WAVEdit());
        }

        internal void IncludeVTF()
        {
            string extension = ".vtf";
            AddValidFileExtensions(extension);
            if (_pipelineOptions.IsMaximumMode)
            {
                var maximum = new MaximumVTFEdit(
                    _directoryPath,
                    _pipelineOptions.MaximumVtfProfile,
                    _pipelineOptions.MaximumVtfParallelism);
                maximum.ProgressChanged += progress => e_MaximumProgress?.Invoke(progress);
                _compressServices.Add(extension, maximum);
            }
            else if (_pipelineOptions.IsScreenMode)
            {
                _compressServices.Add(extension, new ScreenVTFEdit(_directoryPath, _pipelineOptions.ScreenProfile,
                    _pipelineOptions.KeepScreenFallbacks));
            }
            else if (_pipelineOptions.IsMagickPlusMode)
            {
                _compressServices.Add(extension, new MagickPlusVTFEdit(_directoryPath));
            }
            else
            {
                _compressServices.Add(extension, new VTFEdit(_directoryPath));
            }
        }

        internal void IncludeJPG()
        {
            string extension = ".jpg";
            AddValidFileExtensions(extension);
            _compressServices.Add(extension, new JPGEdit());

            extension = ".jpeg";
            AddValidFileExtensions(extension);
            _compressServices.Add(extension, new JPEGEdit());
        }

        internal void IncludePNG()
        {
            string extension = ".png";
            AddValidFileExtensions(extension);
            if (_pipelineOptions.LosslessPng)
                _compressServices.Add(extension, new LosslessPngEdit(_directoryPath));
            else if (_pipelineOptions.ShouldUseMagickForAggressivePng)
                _compressServices.Add(extension, new MagickPngEdit());
            else
                _compressServices.Add(extension, new PNGEdit());
        }

        internal void StartCompress()
        {
            _hasStarted = true;
            Interlocked.Exchange(ref _failureCount, 0);

            _maximumBatchCancellation?.Dispose();
            _maximumBatchCancellation = new CancellationTokenSource();

            ParseDirectory(_directoryPath);
            PrepareServices();

            _compressThread = new Thread(CompressThread);
            _compressThread.IsBackground = true;
            _compressThread.Priority = ThreadPriority.AboveNormal;
            _compressThread.Start();
        }

        internal void StopCompress()
        {
            _maximumBatchCancellation?.Cancel();
            if (_compressThread != null && _compressThread.IsAlive)
                _compressThread.Interrupt();
        }

        internal bool HasStarted() => _hasStarted;

        /// <summary>One line per service that reports its result (e.g. lossless PNG), after the run.</summary>
        internal IReadOnlyList<string> GetSummaries() =>
            _compressServices.Values.OfType<ICompressSummary>()
                .Select(service => service.Summary)
                .Where(summary => !string.IsNullOrWhiteSpace(summary))
                .Select(summary => summary!)
                .Distinct()
                .ToArray();

        private void AddValidFileExtensions(string extension)
        {
            if (!_validFileExtensions.Exists(x => x == extension))
                _validFileExtensions.Add(extension);
        }

        private ICompress? GetService(string extension)
        {
            if (_compressServices.TryGetValue(extension, out var service))
                return service;
            return null;
        }

        private void PrepareServices()
        {
            foreach (ICompress service in _compressServices.Values)
            {
                if (service is ICompressPreparation preparation)
                {
                    try
                    {
                        preparation.Prepare();
                    }
                    catch (Exception ex)
                    {
                        Interlocked.Increment(ref _failureCount);
                        _logger.LogError(ex.ToString());
                    }
                }
            }
        }

        private void CompressThread()
        {
            try
            {
                CompressThreadAsync().GetAwaiter().GetResult();
            }
            catch (Exception ex)
            {
                Interlocked.Increment(ref _failureCount);
                _logger.LogError(ex.ToString());
                _hasStarted = false;
                e_CompletedCompress?.Invoke();
            }
        }

        private async Task CompressThreadAsync()
        {
            int filesCount = _registredFiles.Count;
            if (filesCount == 0)
            {
                _hasStarted = false;
                e_CompletedCompress?.Invoke();
                return;
            }

            var progressCounter = new ProgressCounter();
            IReadOnlyList<CompressionBucket> buckets = BuildCompressionBuckets();
            _logger.LogInformation(
                "Compress scheduling: " +
                string.Join(", ", buckets.Select(bucket => $"{bucket.Name}={bucket.Files.Count}@{bucket.MaxDegreeOfParallelism}")));

            await Task.WhenAll(buckets.Select(bucket => ProcessBucketAsync(bucket, filesCount, progressCounter)));

            foreach (ICompressFinalizer finalizer in _compressServices.Values.OfType<ICompressFinalizer>().Distinct())
            {
                try
                {
                    await finalizer.CompleteAsync().ConfigureAwait(false);
                }
                catch (Exception ex)
                {
                    Interlocked.Increment(ref _failureCount);
                    _logger.LogError(ex.ToString());
                }
            }

            _hasStarted = false;

            e_CompletedCompress?.Invoke();
        }

        private async Task ProcessBucketAsync(CompressionBucket bucket, int filesCount, ProgressCounter progressCounter)
        {
            // Services that take the whole list at once (worker engines: the VTF modes, lossless PNG) get
            // their files in one call; the rest of the bucket goes file by file.
            var batches = new List<(ICompressBatch Service, List<FileInfo> Files)>();
            var singleFiles = new List<FileInfo>();
            foreach (FileInfo file in bucket.Files)
            {
                if (GetService(file.Extension) is ICompressBatch batchService)
                {
                    int index = batches.FindIndex(batch => ReferenceEquals(batch.Service, batchService));
                    if (index < 0)
                    {
                        batches.Add((batchService, new List<FileInfo>()));
                        index = batches.Count - 1;
                    }
                    batches[index].Files.Add(file);
                }
                else
                {
                    singleFiles.Add(file);
                }
            }

            foreach (var (batchService, batchFiles) in batches)
            {
                try
                {
                    await RunBatchServiceAsync(
                        batchService,
                        batchFiles,
                        _maximumBatchCancellation?.Token ?? CancellationToken.None)
                        .ConfigureAwait(false);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex.ToString());
                    throw;
                }

                foreach (FileInfo file in batchFiles)
                {
                    int fileIndex = Interlocked.Increment(ref progressCounter.Value);
                    e_ProgressChanged?.Invoke(file.FullName, fileIndex, filesCount);
                }
            }

            if (singleFiles.Count == 0)
                return;

            var options = new ParallelOptions
            {
                MaxDegreeOfParallelism = bucket.MaxDegreeOfParallelism
            };

            await Parallel.ForEachAsync(singleFiles, options, async (FileInfo file, CancellationToken cancellationToken) =>
            {
                ICompress? service = GetService(file.Extension);
                if (service != null)
                {
                    try
                    {
                        // Observe task faults/timeouts so Pipeline cannot export a failed native stage.
                        await service.Compress(file.FullName).WaitAsync(TimeSpan.FromMinutes(2));
                    }
                    catch (Exception ex)
                    {
                        Interlocked.Increment(ref _failureCount);
                        _logger.LogError(ex.ToString());
                    }
                }

                int fileIndex = Interlocked.Increment(ref progressCounter.Value);
                e_ProgressChanged?.Invoke(file.FullName, fileIndex, filesCount);
            });
        }

        internal static Task RunBatchServiceAsync(
            ICompressBatch service,
            IReadOnlyList<FileInfo> files,
            CancellationToken cancellationToken)
        {
            ArgumentNullException.ThrowIfNull(service);
            ArgumentNullException.ThrowIfNull(files);
            return service.CompressBatchAsync(files, cancellationToken);
        }

        private IReadOnlyList<CompressionBucket> BuildCompressionBuckets()
        {
            var buckets = new Dictionary<string, List<FileInfo>>(StringComparer.OrdinalIgnoreCase);
            foreach (FileInfo file in _registredFiles)
            {
                string bucketName = GetBucketName(file.Extension);
                if (!buckets.TryGetValue(bucketName, out List<FileInfo>? files))
                {
                    files = new List<FileInfo>();
                    buckets[bucketName] = files;
                }

                files.Add(file);
            }

            return buckets
                .OrderBy(pair => GetBucketOrder(pair.Key))
                .Select(pair => new CompressionBucket(
                    pair.Key,
                    GetMaxDegreeOfParallelism(pair.Key),
                    pair.Value))
                .ToArray();
        }

        private static string GetBucketName(string extension)
        {
            return extension.ToLowerInvariant() switch
            {
                ".vtf" => "vtf",
                ".wav" or ".mp3" or ".ogg" => "audio",
                ".png" or ".jpg" or ".jpeg" => "image",
                ".lua" => "script",
                _ => "other"
            };
        }

        private static int GetBucketOrder(string bucketName)
        {
            return bucketName switch
            {
                "vtf" => 0,
                "audio" => 1,
                "image" => 2,
                "script" => 3,
                _ => 4
            };
        }

        internal int GetMaxDegreeOfParallelism(string bucketName)
        {
            int cpuCount = Math.Max(1, Environment.ProcessorCount);
            return bucketName switch
            {
                "vtf" when _pipelineOptions.IsMaximumMode => Math.Clamp(_pipelineOptions.MaximumVtfParallelism, 1, 10),
                "vtf" => Math.Max(1, Math.Min(4, cpuCount / 2)),
                // Each sound is independent (own ffmpeg processes and temp names), so more at once
                // gives the same files; the adaptive WAV profile runs several encodes per sound.
                "audio" => Math.Max(1, Math.Min(8, cpuCount / 2)),
                "image" => Math.Max(2, Math.Min(8, cpuCount)),
                "script" => 1,
                _ => Math.Max(1, Math.Min(2, cpuCount / 4))
            };
        }

        private void ParseDirectory(string directoryPath)
        {
            var currentDirectory = new DirectoryInfo(directoryPath);
            FileInfo[] files = currentDirectory.GetFiles();

            foreach (FileInfo file in files)
            {
                if (_validFileExtensions.Contains(file.Extension) &&
                    (_fileFilter == null || _fileFilter(file)))
                {
                    _registredFiles.Enqueue(file);
                }
            }

            foreach (DirectoryInfo directory in currentDirectory.GetDirectories())
                ParseDirectory(directory.FullName);
        }
    }
}
