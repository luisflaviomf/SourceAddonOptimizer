using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Systems.Tools;
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Audio
{
    /// <summary>
    /// The adaptive WAV backend with the ffmpeg work of many sounds put in the same process.
    ///
    /// Starting ffmpeg costs about 150 ms on a desktop, whatever it is asked to do, and a sound needs about
    /// eight encodes and decodes: with one process per step the whole audio stage was process start-up
    /// (2 s of CPU per sound on the 2026-11-10 test bench, 42% of the optimization time). Here every step
    /// asked for within a few milliseconds, by any sound, becomes one input and one output of the same
    /// command line. Each output keeps the arguments of its one-sound command (FFMpegSystem.Build*), so the
    /// bytes are the same; a command that fails is redone one step at a time with the one-sound commands,
    /// so one unreadable file behaves exactly as before.
    /// </summary>
    internal sealed class BatchedFfmpegAdaptiveAudioBackend : IAdaptiveAudioBackend, IDisposable
    {
        // Windows takes 32767 characters in a command line; inputs are full paths inside the addon
        private const int MaxCommandCharacters = 24000;
        private const int MaxStepsPerProcess = 48;
        private const int CollectMilliseconds = 15;

        private readonly FFMpegSystem _ffmpeg;
        private readonly FfmpegAdaptiveAudioBackend _single;
        private readonly int _maxProcesses;
        private readonly object _sync = new();
        private readonly string _decodeRoot;
        private readonly Queue<Step> _pending = new();
        private int _runningProcesses;
        private bool _pumpScheduled;
        private long _decodeCounter;
        private long _processCount;
        private long _stepCount;
        private long _redoneStepCount;

        private sealed class Step
        {
            internal string InputPath = string.Empty;
            internal string OutputPath = string.Empty;
            internal string OutputArguments = string.Empty;
            /// <summary>The one-sound baseline command copies the input's global metadata (ffmpeg's default).</summary>
            internal bool CopiesInputMetadata;
            internal Func<Task<bool>> RunAlone = () => Task.FromResult(false);
            internal readonly TaskCompletionSource<bool> Done = new(TaskCreationOptions.RunContinuationsAsynchronously);
            internal int Characters => InputPath.Length + OutputPath.Length + OutputArguments.Length + 48;
        }

        internal BatchedFfmpegAdaptiveAudioBackend(FFMpegSystem ffmpeg, int maxProcesses = 0)
        {
            _ffmpeg = ffmpeg ?? throw new ArgumentNullException(nameof(ffmpeg));
            _single = new FfmpegAdaptiveAudioBackend(ffmpeg);
            _maxProcesses = maxProcesses > 0 ? maxProcesses : Math.Max(2, Environment.ProcessorCount / 2);
            _decodeRoot = Path.Combine(ToolPaths.WorkRoot, "audio", "decode-" + Guid.NewGuid().ToString("N"));
        }

        /// <summary>ffmpeg processes started, steps they carried and steps redone alone after a failed process.</summary>
        internal (long Processes, long Steps, long RedoneSteps) Statistics =>
            (Interlocked.Read(ref _processCount), Interlocked.Read(ref _stepCount), Interlocked.Read(ref _redoneStepCount));

        public Task<bool> EncodeBaselineAsync(string inputPath, string outputPath, AudioEncodingSpec spec)
        {
            AudioContext.WavCodecKind codec = ToContextCodec(spec.Codec);
            return Enqueue(new Step
            {
                InputPath = inputPath,
                OutputPath = outputPath,
                OutputArguments = FFMpegSystem.BuildWavOutputArguments(spec.SampleRate, spec.Channels, codec),
                CopiesInputMetadata = true,
                RunAlone = () => _single.EncodeBaselineAsync(inputPath, outputPath, spec)
            });
        }

        public Task<bool> EncodeCandidateAsync(string inputPath, string outputPath, AudioEncodingSpec spec)
        {
            // 0 (unreadable header) counts as "resampled": the deterministic double path is used.
            int sourceSampleRate = RiffWaveFormat.TryReadFile(inputPath, out RiffWaveFormat? format, out _)
                ? format!.SampleRate
                : 0;
            return Enqueue(new Step
            {
                InputPath = inputPath,
                OutputPath = outputPath,
                OutputArguments = FFMpegSystem.BuildAdaptiveWavOutputArguments(
                    spec.SampleRate, spec.Channels, ToContextCodec(spec.Codec), spec.BlockAlign, sourceSampleRate),
                RunAlone = () => _single.EncodeCandidateAsync(inputPath, outputPath, spec)
            });
        }

        public async Task<PcmAudio> DecodeAsync(string path, int sampleRate, int channels)
        {
            Directory.CreateDirectory(_decodeRoot);
            string rawPath = Path.Combine(_decodeRoot, Interlocked.Increment(ref _decodeCounter) + ".f32");
            PcmAudio? alone = null;
            try
            {
                bool decoded = await Enqueue(new Step
                {
                    InputPath = path,
                    OutputPath = rawPath,
                    OutputArguments = FFMpegSystem.BuildDecodeFloatOutputArguments(sampleRate, channels),
                    RunAlone = async () =>
                    {
                        // the one-sound decode reads ffmpeg's pipe; an exception here is the caller's, as before
                        alone = await _single.DecodeAsync(path, sampleRate, channels).ConfigureAwait(false);
                        return true;
                    }
                }).ConfigureAwait(false);
                if (alone != null)
                    return alone;
                if (!decoded)
                    throw new InvalidDataException($"FFmpeg could not decode WAV: {path}");

                byte[] bytes = await File.ReadAllBytesAsync(rawPath).ConfigureAwait(false);
                if (bytes.Length == 0 || bytes.Length % (sizeof(float) * channels) != 0)
                    throw new InvalidDataException($"FFmpeg could not decode WAV: {path}");
                var samples = new float[bytes.Length / sizeof(float)];
                Buffer.BlockCopy(bytes, 0, samples, 0, bytes.Length);
                return new PcmAudio(sampleRate, channels, samples);
            }
            finally
            {
                try { if (File.Exists(rawPath)) File.Delete(rawPath); } catch { }
            }
        }

        public void Dispose()
        {
            try { if (Directory.Exists(_decodeRoot)) Directory.Delete(_decodeRoot, recursive: true); } catch { }
        }

        private Task<bool> Enqueue(Step step)
        {
            bool pumpNow;
            lock (_sync)
            {
                _pending.Enqueue(step);
                pumpNow = _pending.Count >= MaxStepsPerProcess;
                if (!pumpNow && !_pumpScheduled)
                {
                    // wait a moment for the steps the other sounds are about to ask for
                    _pumpScheduled = true;
                    _ = Task.Delay(CollectMilliseconds).ContinueWith(
                        _ =>
                        {
                            lock (_sync)
                                _pumpScheduled = false;
                            Pump();
                        },
                        TaskScheduler.Default);
                }
            }
            if (pumpNow)
                Pump();
            return step.Done.Task;
        }

        /// <summary>
        /// Starts processes while there are waiting steps and free process slots. With every slot busy the
        /// steps keep gathering, so the busier the stage the more steps each process carries.
        /// </summary>
        private void Pump()
        {
            while (true)
            {
                List<Step> steps;
                lock (_sync)
                {
                    if (_pending.Count == 0 || _runningProcesses >= _maxProcesses)
                        return;
                    steps = new List<Step>();
                    int characters = 0;
                    while (_pending.Count > 0 && steps.Count < MaxStepsPerProcess)
                    {
                        int next = _pending.Peek().Characters;
                        if (steps.Count > 0 && characters + next > MaxCommandCharacters)
                            break;
                        characters += next;
                        steps.Add(_pending.Dequeue());
                    }
                    _runningProcesses++;
                }
                _ = RunProcessAsync(steps);
            }
        }

        private async Task RunProcessAsync(List<Step> steps)
        {
            await Task.Yield();
            try
            {
                bool processOk = false;
                try
                {
                    foreach (Step step in steps)
                    {
                        try { if (File.Exists(step.OutputPath)) File.Delete(step.OutputPath); } catch { }
                    }
                    Interlocked.Increment(ref _processCount);
                    Interlocked.Add(ref _stepCount, steps.Count);
                    FFMpegSystem.FfmpegRunResult run = await _ffmpeg
                        .RunBatchAsync(BuildArguments(steps), TimeSpan.FromMinutes(2 + steps.Count / 4), "wav-adaptive-batch")
                        .ConfigureAwait(false);
                    processOk = !run.TimedOut && run.ExitCode == 0;
                }
                catch
                {
                    processOk = false;
                }

                foreach (Step step in steps)
                {
                    try
                    {
                        if (processOk && FFMpegSystem.OutputFileLooksValid(step.OutputPath))
                        {
                            step.Done.TrySetResult(true);
                            continue;
                        }
                        // the process failed (one bad input stops them all) or left this output out: this step
                        // alone, with the one-sound command and its own failure handling
                        Interlocked.Increment(ref _redoneStepCount);
                        try { if (File.Exists(step.OutputPath)) File.Delete(step.OutputPath); } catch { }
                        step.Done.TrySetResult(await step.RunAlone().ConfigureAwait(false));
                    }
                    catch (Exception ex)
                    {
                        step.Done.TrySetException(ex);
                    }
                }
            }
            finally
            {
                lock (_sync)
                    _runningProcesses--;
                Pump();
            }
        }

        private static string BuildArguments(List<Step> steps)
        {
            var inputs = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var inputArguments = new StringBuilder();
            var outputArguments = new StringBuilder();
            foreach (Step step in steps)
            {
                if (!inputs.TryGetValue(step.InputPath, out int index))
                {
                    index = inputs.Count;
                    inputs.Add(step.InputPath, index);
                    inputArguments.Append($" -i \"{step.InputPath}\"");
                }
                // A WAV has one audio stream; the one-sound commands pick it by default. With several inputs
                // the default global metadata comes from the first one, so the output that copies metadata
                // names its own input.
                outputArguments.Append($" -map {index}:a:0");
                if (step.CopiesInputMetadata)
                    outputArguments.Append($" -map_metadata {index}");
                outputArguments.Append($" {step.OutputArguments} \"{step.OutputPath}\"");
            }
            return "-hide_banner -nostdin -y -loglevel error" + inputArguments + outputArguments;
        }

        private static AudioContext.WavCodecKind ToContextCodec(AudioCodec codec) =>
            codec == AudioCodec.MsAdpcm
                ? AudioContext.WavCodecKind.AdpcmMs
                : AudioContext.WavCodecKind.Pcm16;
    }
}
