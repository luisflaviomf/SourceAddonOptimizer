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
        private Thread? _compressThread = null;
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
                var maximum = new MaximumVTFEdit(_directoryPath);
                maximum.ProgressChanged += progress => e_MaximumProgress?.Invoke(progress);
                _compressServices.Add(extension, maximum);
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
            if (_pipelineOptions.ShouldUseMagickForAggressivePng)
                _compressServices.Add(extension, new MagickPngEdit());
            else
                _compressServices.Add(extension, new PNGEdit());
        }

        internal void StartCompress()
        {
            _hasStarted = true;

            ParseDirectory(_directoryPath);
            PrepareServices();

            _compressThread = new Thread(CompressThread);
            _compressThread.IsBackground = true;
            _compressThread.Priority = ThreadPriority.AboveNormal;
            _compressThread.Start();
        }

        internal void StopCompress()
        {
            if (_compressThread != null && _compressThread.IsAlive)
                _compressThread.Interrupt();
        }

        internal bool HasStarted() => _hasStarted;

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
                    _logger.LogError(ex.ToString());
                }
            }

            _hasStarted = false;

            e_CompletedCompress?.Invoke();
        }

        private async Task ProcessBucketAsync(CompressionBucket bucket, int filesCount, ProgressCounter progressCounter)
        {
            var options = new ParallelOptions
            {
                MaxDegreeOfParallelism = bucket.MaxDegreeOfParallelism
            };

            await Parallel.ForEachAsync(bucket.Files, options, async (FileInfo file, CancellationToken cancellationToken) =>
            {
                ICompress? service = GetService(file.Extension);
                if (service != null)
                {
                    try
                    {
                        await Task.WhenAny(service.Compress(file.FullName), Task.Delay(TimeSpan.FromMinutes(2)));
                    }
                    catch (Exception ex)
                    {
                        _logger.LogError(ex.ToString());
                    }
                }

                int fileIndex = Interlocked.Increment(ref progressCounter.Value);
                e_ProgressChanged?.Invoke(file.FullName, fileIndex, filesCount);
            });
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
                "audio" => Math.Max(1, Math.Min(2, cpuCount / 4)),
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
