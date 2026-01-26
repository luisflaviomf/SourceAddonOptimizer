using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Helpres;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Optimizer;
using GmodAddonCompressor.Systems.Reporting;
using GmodAddonCompressor.Systems.Settings;
using GmodAddonCompressor.Systems.Tools;
using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

namespace GmodAddonCompressor
{
    public partial class MainWindow : Window
    {
        private MainWindowContext _context = new MainWindowContext();
        private const string _version = "v1.0.1";
        private readonly SourceAddonOptimizerRunner _optimizerRunner = new SourceAddonOptimizerRunner();
        private CancellationTokenSource? _optimizerCts = null;
        private string? _modelsOutputPath = null;
        private string? _modelsWorkDir = null;
        private string? _modelsLastErrorLine = null;
        private int _modelsStepIndex = 0;
        private int _modelsStepTotal = 0;
        private string _modelsPhase = string.Empty;
        private CancellationTokenSource? _pipelineCts = null;
        private CompressAddonSystem? _pipelineCompressSystem = null;
        private bool _pipelineRunning = false;
        private PipelineStage _pipelineStage = PipelineStage.None;
        private string? _pipelineOutputPath = null;
        private string? _pipelineLogsDir = null;
        private bool _pipelineModelsOk = false;
        private bool _pipelineCompressOk = false;
        private int _pipelineCompressFilesProcessed = 0;
        private int _pipelineCompressFilesTotal = 0;
        private bool _pipelineCancelRequested = false;
        private int? _pipelineModelsExitCode = null;
        private int? _pipelineCompressExitCode = null;
        private AppSettingsModel _settings = new AppSettingsModel();
        private DirectorySizeSnapshot? _compressSizeBefore = null;
        private DirectorySizeSnapshot? _compressSizeAfter = null;
        private DirectorySizeSnapshot? _modelsSizeBefore = null;
        private DirectorySizeSnapshot? _modelsSizeAfter = null;
        private DirectorySizeSnapshot? _pipelineModelsSizeBefore = null;
        private DirectorySizeSnapshot? _pipelineModelsSizeAfter = null;
        private DirectorySizeSnapshot? _pipelineCompressSizeBefore = null;
        private DirectorySizeSnapshot? _pipelineCompressSizeAfter = null;

        private const int PresetSafeIndex = 0;
        private const int PresetAggressiveIndex = 1;
        private const int PresetCustomIndex = 2;

        private enum PipelineStage
        {
            None,
            Models,
            Compress
        }

        public MainWindow()
        {
            InitializeComponent();

            DataContext = _context;
            VersionId.Text = _version;

            Button_Compress.Click += Button_Compress_Click;
            Button_SelectDirectory.Click += Button_SelectDirectory_Click;
            Button_SelectDirectoryModels.Click += Button_SelectDirectory_Click;
            Button_SelectDirectoryPipeline.Click += Button_SelectDirectory_Click;
            ComboBox_OptimizerPreset.SelectionChanged += ComboBox_OptimizerPreset_SelectionChanged;
            Button_BrowseBlender.Click += Button_BrowseBlender_Click;
            Button_AutoDetectBlender.Click += Button_AutoDetectBlender_Click;
            Button_BrowseStudioMdl.Click += Button_BrowseStudioMdl_Click;
            Button_AutoDetectStudioMdl.Click += Button_AutoDetectStudioMdl_Click;
            Button_OptimizeModels.Click += Button_OptimizeModels_Click;
            Button_OpenModelsOutput.Click += Button_OpenModelsOutput_Click;
            Button_OpenModelsWork.Click += Button_OpenModelsWork_Click;
            Button_OpenModelsLogs.Click += Button_OpenModelsLogs_Click;
            Button_StartPipeline.Click += Button_StartPipeline_Click;
            Button_CancelPipeline.Click += Button_CancelPipeline_Click;
            Button_OpenPipelineOutput.Click += Button_OpenPipelineOutput_Click;
            Button_OpenPipelineWork.Click += Button_OpenPipelineWork_Click;
            Button_OpenPipelineLogs.Click += Button_OpenPipelineLogs_Click;
            Button_CopyPipelineSummary.Click += Button_CopyPipelineSummary_Click;

            _optimizerRunner.ProgressUpdate += OptimizerProgressUpdate;
            _optimizerRunner.OutputPathFound += OptimizerOutputPathFound;
            _optimizerRunner.ErrorLine += OptimizerErrorLine;

            LoadSettings();
        }

        private void Button_SelectDirectory_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new Ookii.Dialogs.Wpf.VistaFolderBrowserDialog();
            if (dialog.ShowDialog(this).GetValueOrDefault())
            {
                _context.AddonDirectoryPath = dialog.SelectedPath;
            }
        }

        private void Button_Compress_Click(object sender, RoutedEventArgs e)
        {
            string addonDirectoryPath = _context.AddonDirectoryPath;

            if (Directory.Exists(addonDirectoryPath))
            {
                SaveSettings();
                Task.Run(async () =>
                {
                    await StartCompressProcessAsync(addonDirectoryPath);
                });
            }
        }

        private void Button_BrowseBlender_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Select blender.exe",
                Filter = "blender.exe|blender.exe|Executable|*.exe|All files|*.*",
                CheckFileExists = true
            };

            if (dialog.ShowDialog(this).GetValueOrDefault())
                _context.BlenderPath = dialog.FileName;
        }

        private void Button_AutoDetectBlender_Click(object sender, RoutedEventArgs e)
        {
            var detected = DetectBlenderPath();
            if (!string.IsNullOrWhiteSpace(detected))
            {
                _context.BlenderPath = detected;
                return;
            }

            MessageBox.Show("Blender not found. Please browse to blender.exe.", "Models", MessageBoxButton.OK, MessageBoxImage.Warning);
        }

        private void Button_BrowseStudioMdl_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Select studiomdl.exe",
                Filter = "studiomdl.exe|studiomdl.exe|Executable|*.exe|All files|*.*",
                CheckFileExists = true
            };

            if (dialog.ShowDialog(this).GetValueOrDefault())
                _context.StudioMdlPath = dialog.FileName;
        }

        private void Button_AutoDetectStudioMdl_Click(object sender, RoutedEventArgs e)
        {
            var detected = DetectStudioMdlPath();
            if (!string.IsNullOrWhiteSpace(detected))
            {
                _context.StudioMdlPath = detected;
                return;
            }

            MessageBox.Show("studiomdl.exe not found. Please browse to studiomdl.exe.", "Models", MessageBoxButton.OK, MessageBoxImage.Warning);
        }

        private void ComboBox_OptimizerPreset_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
        {
            ApplyPresetValues(_context.OptimizerPresetIndex);
        }

        private async void Button_OptimizeModels_Click(object sender, RoutedEventArgs e)
        {
            string addonDirectoryPath = _context.AddonDirectoryPath;
            if (!ValidateModelsInputs(addonDirectoryPath, out var errorMessage))
            {
                MessageBox.Show(errorMessage, "Models", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (!EnsureToolsAvailable("Models"))
                return;

            if (!File.Exists(ToolPaths.WorkerExePath))
            {
                MessageBox.Show("SourceAddonOptimizer worker not found.", "Models", MessageBoxButton.OK, MessageBoxImage.Error);
                return;
            }

            _context.ModelsSizeReportText = "Size report: computing (before)...";
            _modelsSizeBefore = await TryScanSizeAsync(addonDirectoryPath, CancellationToken.None);

            _modelsOutputPath = null;
            _modelsLastErrorLine = null;
            _modelsSizeAfter = null;
            SaveSettings();
            _modelsWorkDir = ToolPaths.GetWorkDir(addonDirectoryPath, _context.OptimizerSuffix);
            Button_OpenModelsOutput.IsEnabled = false;
            Button_OpenModelsWork.IsEnabled = false;
            Button_OpenModelsLogs.IsEnabled = false;

            _context.ModelsStatusText = "Phase: Starting";
            _context.ModelsProgressText = string.Empty;
            _context.ModelsProgressMinValue = 0;
            _context.ModelsProgressMaxValue = 100;
            _context.ModelsProgressValue = 0;
            _context.UnlockedUI = false;

            var options = new SourceAddonOptimizerRunOptions
            {
                WorkerExePath = ToolPaths.WorkerExePath,
                AddonPath = addonDirectoryPath,
                WorkDir = _modelsWorkDir,
                Suffix = _context.OptimizerSuffix,
                BlenderPath = string.IsNullOrWhiteSpace(_context.BlenderPath) ? null : _context.BlenderPath.Trim(),
                StudioMdlPath = string.IsNullOrWhiteSpace(_context.StudioMdlPath) ? null : _context.StudioMdlPath.Trim(),
                Ratio = _context.OptimizerRatio,
                Merge = _context.OptimizerMerge,
                AutoSmooth = _context.OptimizerAutoSmooth,
                Format = GetOptimizerFormat(),
                Jobs = _context.OptimizerJobs,
                DecompileJobs = _context.OptimizerDecompileJobs,
                CompileJobs = _context.OptimizerCompileJobs,
                Strict = _context.OptimizerStrict,
                ResumeOpt = _context.OptimizerResumeOpt,
                Overwrite = _context.OptimizerOverwrite,
                OverwriteWork = _context.OptimizerOverwriteWork,
                RestoreSkins = _context.OptimizerRestoreSkins,
                CompileVerbose = _context.OptimizerCompileVerbose
            };

            _optimizerCts?.Cancel();
            _optimizerCts = new CancellationTokenSource();

            int exitCode;
            try
            {
                exitCode = await _optimizerRunner.RunAsync(options, _optimizerCts.Token);
            }
            catch (Exception ex)
            {
                _context.ModelsStatusText = $"FAIL: {ex.Message}";
                _context.UnlockedUI = true;
                return;
            }

            _context.UnlockedUI = true;

            if (exitCode == 0)
            {
                _context.ModelsStatusText = "OK";
                if (!string.IsNullOrWhiteSpace(_modelsOutputPath))
                {
                    Button_OpenModelsOutput.IsEnabled = true;
                    Button_OpenModelsWork.IsEnabled = true;
                    _context.ModelsSizeReportText = "Size report: computing (after)...";
                    _modelsSizeAfter = await TryScanSizeAsync(_modelsOutputPath, CancellationToken.None);
                    _context.ModelsSizeReportText = BuildSizeReportText(_modelsSizeBefore, _modelsSizeAfter);
                }
                else
                {
                    _context.ModelsSizeReportText = BuildSizeReportText(_modelsSizeBefore, _modelsSizeAfter);
                }
            }
            else
            {
                var errorSuffix = string.IsNullOrWhiteSpace(_modelsLastErrorLine) ? string.Empty : $" | {_modelsLastErrorLine}";
                _context.ModelsStatusText = $"FAIL ({exitCode}){errorSuffix}";
                Button_OpenModelsOutput.IsEnabled = false;
                Button_OpenModelsWork.IsEnabled = false;
                _context.ModelsSizeReportText = BuildSizeReportText(_modelsSizeBefore, _modelsSizeAfter);
                if (!string.IsNullOrWhiteSpace(_modelsWorkDir) && Directory.Exists(_modelsWorkDir))
                    OpenFolder(_modelsWorkDir, "Models");
            }

            var logsDir = GetModelsLogsDir();
            Button_OpenModelsLogs.IsEnabled = !string.IsNullOrWhiteSpace(logsDir) && Directory.Exists(logsDir);
        }

        private async void Button_StartPipeline_Click(object sender, RoutedEventArgs e)
        {
            if (_pipelineRunning)
                return;

            string addonDirectoryPath = _context.AddonDirectoryPath;
            if (!ValidateModelsInputs(addonDirectoryPath, out var errorMessage))
            {
                MessageBox.Show(errorMessage, "Pipeline", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (!EnsureToolsAvailable("Pipeline"))
                return;

            if (!File.Exists(ToolPaths.WorkerExePath))
            {
                MessageBox.Show("SourceAddonOptimizer worker not found.", "Pipeline", MessageBoxButton.OK, MessageBoxImage.Error);
                return;
            }

            SaveSettings();

            _pipelineRunning = true;
            _context.PipelineIsRunning = true;
            _pipelineStage = PipelineStage.Models;
            _pipelineOutputPath = null;
            _pipelineLogsDir = null;
            _pipelineModelsOk = false;
            _pipelineCompressOk = false;
            _pipelineCompressFilesProcessed = 0;
            _pipelineCompressFilesTotal = 0;
            _pipelineCancelRequested = false;
            _pipelineModelsExitCode = null;
            _pipelineCompressExitCode = null;
            _modelsLastErrorLine = null;
            _modelsOutputPath = null;
            _modelsWorkDir = ToolPaths.GetWorkDir(addonDirectoryPath, _context.OptimizerSuffix);
            _context.PipelineSummaryText = string.Empty;
            _context.PipelineSizeReportText = string.Empty;
            _pipelineModelsSizeBefore = null;
            _pipelineModelsSizeAfter = null;
            _pipelineCompressSizeBefore = null;
            _pipelineCompressSizeAfter = null;

            _context.UnlockedUI = false;
            _context.PipelineStatusText = "Models Phase: Starting";
            _context.PipelineProgressText = string.Empty;
            _context.PipelineProgressMinValue = 0;
            _context.PipelineProgressMaxValue = 100;
            _context.PipelineProgressValue = 0;
            Button_CancelPipeline.IsEnabled = true;
            Button_OpenPipelineOutput.IsEnabled = false;
            Button_OpenPipelineWork.IsEnabled = false;
            Button_OpenPipelineLogs.IsEnabled = false;
            Button_CopyPipelineSummary.IsEnabled = false;

            _pipelineCts?.Cancel();
            _pipelineCts = new CancellationTokenSource();

            await RunPipelineAsync(addonDirectoryPath, _pipelineCts.Token);
        }

        private void Button_CancelPipeline_Click(object sender, RoutedEventArgs e)
        {
            if (!_pipelineRunning)
                return;

            _pipelineCancelRequested = true;
            _context.PipelineStatusText = "Canceling...";
            if (_pipelineStage == PipelineStage.Models)
            {
                _pipelineModelsExitCode = 130;
                _pipelineCts?.Cancel();
                return;
            }

            if (_pipelineStage == PipelineStage.Compress)
            {
                _pipelineCompressExitCode = 130;
                _pipelineCompressSystem?.StopCompress();
            }
        }

        private async Task RunPipelineAsync(string addonDirectoryPath, CancellationToken token)
        {
            try
            {
                _context.PipelineStatusText = "Models Phase: Running";
                _context.PipelineSizeReportText = "Size report: computing (models before)...";
                _pipelineModelsSizeBefore = await TryScanSizeAsync(addonDirectoryPath, token);
                var options = new SourceAddonOptimizerRunOptions
                {
                    WorkerExePath = ToolPaths.WorkerExePath,
                    AddonPath = addonDirectoryPath,
                    WorkDir = _modelsWorkDir ?? ToolPaths.GetWorkDir(addonDirectoryPath, _context.OptimizerSuffix),
                    Suffix = _context.OptimizerSuffix,
                    BlenderPath = string.IsNullOrWhiteSpace(_context.BlenderPath) ? null : _context.BlenderPath.Trim(),
                    StudioMdlPath = string.IsNullOrWhiteSpace(_context.StudioMdlPath) ? null : _context.StudioMdlPath.Trim(),
                    Ratio = _context.OptimizerRatio,
                    Merge = _context.OptimizerMerge,
                    AutoSmooth = _context.OptimizerAutoSmooth,
                    Format = GetOptimizerFormat(),
                    Jobs = _context.OptimizerJobs,
                    DecompileJobs = _context.OptimizerDecompileJobs,
                    CompileJobs = _context.OptimizerCompileJobs,
                    Strict = _context.OptimizerStrict,
                    ResumeOpt = _context.OptimizerResumeOpt,
                    Overwrite = _context.OptimizerOverwrite,
                    OverwriteWork = _context.OptimizerOverwriteWork,
                    RestoreSkins = _context.OptimizerRestoreSkins,
                    CompileVerbose = _context.OptimizerCompileVerbose
                };

                int exitCode = await _optimizerRunner.RunAsync(options, token);
                _pipelineModelsExitCode = exitCode;
                if (token.IsCancellationRequested)
                {
                    _context.PipelineStatusText = "Canceled.";
                    return;
                }

                _pipelineModelsOk = exitCode == 0 && !string.IsNullOrWhiteSpace(_pipelineOutputPath);
                _pipelineLogsDir = GetModelsLogsDir();

                if (!string.IsNullOrWhiteSpace(_pipelineOutputPath))
                {
                    _context.PipelineSizeReportText = "Size report: computing (models after)...";
                    _pipelineModelsSizeAfter = await TryScanSizeAsync(_pipelineOutputPath, token);
                    _context.PipelineSizeReportText = BuildPipelineSizeReportText();
                }

                if (!_pipelineModelsOk)
                {
                    var errorSuffix = string.IsNullOrWhiteSpace(_modelsLastErrorLine) ? string.Empty : $" | {_modelsLastErrorLine}";
                    _context.PipelineStatusText = $"Models failed ({exitCode}){errorSuffix}";
                    return;
                }

                _pipelineStage = PipelineStage.Compress;
                _pipelineCompressSystem = null;
                _context.PipelineStatusText = "Compress Phase: Starting";
                _context.PipelineProgressText = string.Empty;
                _context.PipelineProgressMinValue = 0;
                _context.PipelineProgressMaxValue = 100;
                _context.PipelineProgressValue = 0;
                _context.UnlockedUI = false;

                string outputPath = _pipelineOutputPath!;
                await StartCompressProcessAsync(outputPath, cs => _pipelineCompressSystem = cs, unlockUiOnComplete: false);

                if (token.IsCancellationRequested || _pipelineCancelRequested)
                {
                    _context.PipelineStatusText = "Canceled.";
                    return;
                }

                if (!_pipelineCompressOk)
                    _pipelineCompressExitCode = 1;
                else if (!_pipelineCompressExitCode.HasValue)
                    _pipelineCompressExitCode = 0;

                _context.PipelineStatusText = "OK";
            }
            catch (OperationCanceledException)
            {
                if (_pipelineStage == PipelineStage.Models && !_pipelineModelsExitCode.HasValue)
                    _pipelineModelsExitCode = 130;
                if (_pipelineStage == PipelineStage.Compress && !_pipelineCompressExitCode.HasValue)
                    _pipelineCompressExitCode = 130;
                _context.PipelineStatusText = "Canceled.";
            }
            catch (Exception ex)
            {
                _context.PipelineStatusText = $"FAIL: {ex.Message}";
            }
            finally
            {
                _pipelineRunning = false;
                _pipelineStage = PipelineStage.None;
                _pipelineCompressSystem = null;
                UpdatePipelineSummary();
                Button_OpenPipelineOutput.IsEnabled = true;
                Button_OpenPipelineWork.IsEnabled = true;
                Button_OpenPipelineLogs.IsEnabled = true;
                Button_CopyPipelineSummary.IsEnabled = true;
                _context.PipelineProgressValue = 0;
                _context.PipelineProgressMinValue = 0;
                _context.PipelineProgressMaxValue = 100;
                if (_pipelineCancelRequested)
                {
                    _context.PipelineStatusText = "Ready";
                    _context.PipelineProgressText = string.Empty;
                }
                _context.PipelineIsRunning = false;
                _context.UnlockedUI = true;
                Button_CancelPipeline.IsEnabled = false;
            }
        }

        private void UpdatePipelineSummary()
        {
            string modelsStatus = _pipelineModelsOk ? "OK" : (_pipelineCancelRequested ? "CANCELED" : "FAIL");
            string modelsOutput = string.IsNullOrWhiteSpace(_pipelineOutputPath) ? "-" : _pipelineOutputPath;

            string compressStatus;
            if (!_pipelineModelsOk)
                compressStatus = "SKIPPED";
            else if (_pipelineCancelRequested)
                compressStatus = "CANCELED";
            else
                compressStatus = _pipelineCompressOk ? "OK" : "FAIL";

            string compressCount = _pipelineCompressFilesTotal > 0
                ? $"{_pipelineCompressFilesProcessed}/{_pipelineCompressFilesTotal}"
                : "n/a";

            _context.PipelineSummaryText = $"Summary: Models {modelsStatus} | Output: {modelsOutput} | Compress {compressStatus} | Files: {compressCount}";
            _context.PipelineSizeReportText = BuildPipelineSizeReportText();
        }

        private void OptimizerProgressUpdate(SourceAddonOptimizerProgressUpdate update)
        {
            Dispatcher.Invoke(() =>
            {
                if (update.StepIndex.HasValue)
                {
                    _modelsStepIndex = update.StepIndex.Value;
                    _modelsStepTotal = update.StepTotal ?? 0;
                    _modelsPhase = update.Phase ?? string.Empty;
                    _context.ModelsStatusText = $"Phase: {_modelsPhase} (Step {_modelsStepIndex}/{_modelsStepTotal})";
                    _context.ModelsProgressValue = 0;
                    _context.ModelsProgressMaxValue = 1;

                    if (_pipelineRunning && _pipelineStage == PipelineStage.Models)
                        _context.PipelineStatusText = $"Models Phase: {_modelsPhase} (Step {_modelsStepIndex}/{_modelsStepTotal})";
                }

                if (update.IsPackaging)
                {
                    _context.ModelsStatusText = $"Packaging: {update.Phase}";
                    return;
                }

                if (update.ItemIndex.HasValue && update.ItemTotal.HasValue)
                {
                    _context.ModelsProgressMinValue = 0;
                    _context.ModelsProgressMaxValue = update.ItemTotal.Value;
                    _context.ModelsProgressValue = update.ItemIndex.Value;
                    _context.ModelsProgressText = $"Current: {update.ItemType} {update.ItemIndex}/{update.ItemTotal}";

                    if (_pipelineRunning && _pipelineStage == PipelineStage.Models)
                    {
                        _context.PipelineProgressMinValue = 0;
                        _context.PipelineProgressMaxValue = update.ItemTotal.Value;
                        _context.PipelineProgressValue = update.ItemIndex.Value;
                        _context.PipelineProgressText = $"Current: {update.ItemType} {update.ItemIndex}/{update.ItemTotal}";
                    }
                }
            });
        }

        private void OptimizerOutputPathFound(string path)
        {
            Dispatcher.Invoke(() =>
            {
                _modelsOutputPath = path;
                Button_OpenModelsOutput.IsEnabled = true;
                Button_OpenModelsWork.IsEnabled = true;
                if (_pipelineRunning)
                    _pipelineOutputPath = path;
            });
        }

        private void OptimizerErrorLine(string line)
        {
            _modelsLastErrorLine = line;
        }

        private void Button_OpenModelsOutput_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(_modelsOutputPath, "Models");
        }

        private void Button_OpenModelsWork_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(_modelsWorkDir, "Models");
        }

        private void Button_OpenModelsLogs_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(GetModelsLogsDir(), "Models");
        }

        private void Button_OpenPipelineOutput_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(_pipelineOutputPath, "Pipeline");
        }

        private void Button_OpenPipelineWork_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(_modelsWorkDir, "Pipeline");
        }

        private void Button_OpenPipelineLogs_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(_pipelineLogsDir ?? GetModelsLogsDir(), "Pipeline");
        }

        private void Button_CopyPipelineSummary_Click(object sender, RoutedEventArgs e)
        {
            if (string.IsNullOrWhiteSpace(_context.PipelineSummaryText))
            {
                MessageBox.Show("No pipeline summary available yet.", "Pipeline", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            var summary = BuildPipelineSummaryDetails();
            Clipboard.SetText(summary);
            MessageBox.Show("Pipeline summary copied to clipboard.", "Pipeline", MessageBoxButton.OK, MessageBoxImage.Information);
        }

        private string? GetModelsLogsDir()
        {
            if (string.IsNullOrWhiteSpace(_modelsWorkDir))
                return null;
            var logsDir = Path.Combine(_modelsWorkDir, "logs");
            if (Directory.Exists(logsDir))
                return logsDir;
            return _modelsWorkDir;
        }

        private void OpenFolder(string? path, string title)
        {
            if (string.IsNullOrWhiteSpace(path) || !Directory.Exists(path))
            {
                MessageBox.Show($"Folder not available: {path ?? "(empty)"}", title, MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            Process.Start(new ProcessStartInfo
            {
                FileName = path,
                UseShellExecute = true
            });
        }

        private bool EnsureToolsAvailable(string title)
        {
            try
            {
                ToolExtractionSystem.EnsureSourceAddonOptimizerExtracted();
                return true;
            }
            catch (FileNotFoundException ex)
            {
                string resourcesDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Resources");
                string message = $"{ex.Message}\n\nExpected zip:\n{ToolPaths.ZipPath}\n\nOpen Resources folder?";
                var result = MessageBox.Show(message, title, MessageBoxButton.YesNo, MessageBoxImage.Error);
                if (result == MessageBoxResult.Yes)
                    OpenFolder(resourcesDir, title);
                return false;
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, title, MessageBoxButton.OK, MessageBoxImage.Error);
                return false;
            }
        }

        private void LoadSettings()
        {
            _settings = SettingsSystem.Load();

            _context.AddonDirectoryPath = _settings.LastAddonPath ?? string.Empty;
            _context.BlenderPath = _settings.BlenderPath ?? string.Empty;
            _context.StudioMdlPath = _settings.StudioMdlPath ?? string.Empty;
            _context.OptimizerSuffix = string.IsNullOrWhiteSpace(_settings.OptimizerSuffix) ? "_optimized" : _settings.OptimizerSuffix;
            _context.OptimizerPresetIndex = PresetIndexFromName(_settings.OptimizerPreset);
            if (_settings.OptimizerRestoreSkins.HasValue)
                _context.OptimizerRestoreSkins = _settings.OptimizerRestoreSkins.Value;
            if (_settings.OptimizerCompileVerbose.HasValue)
                _context.OptimizerCompileVerbose = _settings.OptimizerCompileVerbose.Value;
            if (_settings.AudioWavSampleRateIndex.HasValue)
            {
                int index = _settings.AudioWavSampleRateIndex.Value;
                if (index >= 0 && index < _context.WavRateList.Length)
                    _context.WavRateListIndex = index;
            }
            if (_settings.AudioWavChannelsIndex.HasValue)
            {
                int index = _settings.AudioWavChannelsIndex.Value;
                if (index >= 0 && index < _context.WavChannelList.Length)
                    _context.WavChannelsIndex = index;
            }
            if (_settings.AudioWavCodecIndex.HasValue)
            {
                int index = _settings.AudioWavCodecIndex.Value;
                if (index >= 0 && index < _context.WavCodecList.Length)
                    _context.WavCodecIndex = index;
            }
            if (_settings.AudioLoopSafe.HasValue)
                _context.AudioLoopSafe = _settings.AudioLoopSafe.Value;
            if (_settings.AudioMp3SampleRateIndex.HasValue)
            {
                int index = _settings.AudioMp3SampleRateIndex.Value;
                if (index >= 0 && index < _context.WavRateList.Length)
                    _context.Mp3RateIndex = index;
            }
            if (_settings.AudioMp3BitrateIndex.HasValue)
            {
                int index = _settings.AudioMp3BitrateIndex.Value;
                if (index >= 0 && index < _context.AudioBitrateList.Length)
                    _context.Mp3BitrateIndex = index;
            }
            if (_settings.AudioOggSampleRateIndex.HasValue)
            {
                int index = _settings.AudioOggSampleRateIndex.Value;
                if (index >= 0 && index < _context.WavRateList.Length)
                    _context.OggRateIndex = index;
            }
            if (_settings.AudioOggChannelsIndex.HasValue)
            {
                int index = _settings.AudioOggChannelsIndex.Value;
                if (index >= 0 && index < _context.WavChannelList.Length)
                    _context.OggChannelsIndex = index;
            }
            if (_settings.AudioOggQualityIndex.HasValue)
            {
                int index = _settings.AudioOggQualityIndex.Value;
                if (index >= 0 && index < _context.OggQualityList.Length)
                    _context.OggQualityIndex = index;
            }
            else if (_settings.AudioOggBitrateIndex.HasValue)
            {
                int legacyIndex = _settings.AudioOggBitrateIndex.Value;
                if (legacyIndex >= 0 && legacyIndex < _context.AudioBitrateList.Length)
                    _context.OggQualityIndex = MapOggQualityIndexFromBitrate(_context.AudioBitrateList[legacyIndex]);
            }

            if (_context.OptimizerPresetIndex == PresetCustomIndex)
            {
                ApplyCustomParams(_settings.OptimizerCustom);
            }
            else
            {
                ApplyPresetValues(_context.OptimizerPresetIndex);
            }
        }

        private void SaveSettings()
        {
            var settings = new AppSettingsModel
            {
                LastAddonPath = _context.AddonDirectoryPath,
                BlenderPath = _context.BlenderPath,
                StudioMdlPath = _context.StudioMdlPath,
                OptimizerSuffix = _context.OptimizerSuffix,
                OptimizerPreset = PresetNameFromIndex(_context.OptimizerPresetIndex),
                OptimizerRestoreSkins = _context.OptimizerRestoreSkins,
                OptimizerCompileVerbose = _context.OptimizerCompileVerbose,
                OptimizerCustom = new OptimizerCustomParams
                {
                    Ratio = _context.OptimizerRatio,
                    Merge = _context.OptimizerMerge,
                    AutoSmooth = _context.OptimizerAutoSmooth,
                    Format = GetOptimizerFormat(),
                    Jobs = _context.OptimizerJobs,
                    DecompileJobs = _context.OptimizerDecompileJobs,
                    CompileJobs = _context.OptimizerCompileJobs,
                    Strict = _context.OptimizerStrict,
                    ResumeOpt = _context.OptimizerResumeOpt,
                    Overwrite = _context.OptimizerOverwrite,
                    OverwriteWork = _context.OptimizerOverwriteWork
                },
                AudioWavSampleRateIndex = _context.WavRateListIndex,
                AudioWavChannelsIndex = _context.WavChannelsIndex,
                AudioWavCodecIndex = _context.WavCodecIndex,
                AudioLoopSafe = _context.AudioLoopSafe,
                AudioMp3SampleRateIndex = _context.Mp3RateIndex,
                AudioMp3BitrateIndex = _context.Mp3BitrateIndex,
                AudioOggSampleRateIndex = _context.OggRateIndex,
                AudioOggChannelsIndex = _context.OggChannelsIndex,
                AudioOggQualityIndex = _context.OggQualityIndex
            };

            SettingsSystem.Save(settings);
        }

        private static string PresetNameFromIndex(int index)
        {
            return index switch
            {
                PresetAggressiveIndex => "Aggressive",
                PresetCustomIndex => "Custom",
                _ => "Safe",
            };
        }

        private static int MapOggQualityIndexFromBitrate(int bitrateKbps)
        {
            if (bitrateKbps <= 64)
                return 3; // q2
            if (bitrateKbps <= 80)
                return 4; // q3
            if (bitrateKbps <= 96)
                return 5; // q4
            return 6; // q5
        }

        private static double MapOggQualityFromIndex(int index)
        {
            return index switch
            {
                0 => -1.0,
                1 => 0.0,
                2 => 1.0,
                3 => 2.0,
                4 => 3.0,
                5 => 4.0,
                6 => 5.0,
                7 => 6.0,
                8 => 7.0,
                9 => 8.0,
                10 => 9.0,
                11 => 10.0,
                _ => 3.0
            };
        }

        private static int PresetIndexFromName(string? name)
        {
            if (string.IsNullOrWhiteSpace(name))
                return PresetSafeIndex;

            return name.Trim().ToLowerInvariant() switch
            {
                "aggressive" => PresetAggressiveIndex,
                "custom" => PresetCustomIndex,
                _ => PresetSafeIndex,
            };
        }

        private bool EnsureFfmpegAvailable(string featureName)
        {
            try
            {
                string baseDirectory = AppDomain.CurrentDomain.BaseDirectory;
                string ffmpegDirectory = Path.Combine(baseDirectory, "ffmpeg");
                string ffmpegExePath = Path.Combine(ffmpegDirectory, "ffmpeg.exe");

                if (!File.Exists(ffmpegExePath))
                    _ = new FFMpegSystem();

                if (!File.Exists(ffmpegExePath))
                {
                    MessageBox.Show($"FFmpeg was not found or could not be extracted. {featureName} will be disabled.",
                        "Compress - Audio",
                        MessageBoxButton.OK,
                        MessageBoxImage.Warning);
                    return false;
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Failed to initialize FFmpeg. {featureName} will be disabled.{Environment.NewLine}{ex.Message}",
                    "Compress - Audio",
                    MessageBoxButton.OK,
                    MessageBoxImage.Warning);
                return false;
            }

            return true;
        }

        private void ApplyPresetValues(int presetIndex)
        {
            if (presetIndex == PresetCustomIndex)
                return;

            if (presetIndex == PresetAggressiveIndex)
            {
                _context.OptimizerRatio = 0.35;
                _context.OptimizerMerge = 0.0001;
                _context.OptimizerAutoSmooth = 35.0;
                _context.OptimizerFormatIndex = 0;
                return;
            }

            _context.OptimizerRatio = 0.75;
            _context.OptimizerMerge = 0.0;
            _context.OptimizerAutoSmooth = 45.0;
            _context.OptimizerFormatIndex = 0;
        }

        private void ApplyCustomParams(OptimizerCustomParams custom)
        {
            _context.OptimizerRatio = custom.Ratio;
            _context.OptimizerMerge = custom.Merge;
            _context.OptimizerAutoSmooth = custom.AutoSmooth;
            _context.OptimizerFormatIndex = FormatIndexFromName(custom.Format);
            _context.OptimizerJobs = custom.Jobs;
            _context.OptimizerDecompileJobs = custom.DecompileJobs;
            _context.OptimizerCompileJobs = custom.CompileJobs;
            _context.OptimizerStrict = custom.Strict;
            _context.OptimizerResumeOpt = custom.ResumeOpt;
            _context.OptimizerOverwrite = custom.Overwrite;
            _context.OptimizerOverwriteWork = custom.OverwriteWork;
        }

        private string GetOptimizerFormat()
        {
            if (_context.OptimizerFormatList == null || _context.OptimizerFormatList.Length == 0)
                return "smd";
            if (_context.OptimizerFormatIndex < 0 || _context.OptimizerFormatIndex >= _context.OptimizerFormatList.Length)
                return "smd";
            return _context.OptimizerFormatList[_context.OptimizerFormatIndex];
        }

        private static int FormatIndexFromName(string? name)
        {
            if (string.IsNullOrWhiteSpace(name))
                return 0;

            return name.Trim().ToLowerInvariant() switch
            {
                "dmx" => 1,
                _ => 0,
            };
        }

        private bool ValidateModelsInputs(string addonDirectoryPath, out string errorMessage)
        {
            var errors = new List<string>();

            if (!Directory.Exists(addonDirectoryPath))
                errors.Add("Addon folder not found.");

            if (string.IsNullOrWhiteSpace(_context.BlenderPath) || !File.Exists(_context.BlenderPath))
                errors.Add("Blender path is invalid.");

            if (string.IsNullOrWhiteSpace(_context.StudioMdlPath) || !File.Exists(_context.StudioMdlPath))
                errors.Add("StudioMDL path is invalid.");

            if (string.IsNullOrWhiteSpace(_context.OptimizerSuffix))
                errors.Add("Suffix is required.");

            if (_context.OptimizerCompileJobs < 0)
                errors.Add("Compile jobs must be 0 (auto) or greater.");

            if (_context.OptimizerRatio <= 0 || _context.OptimizerRatio > 1.0)
                errors.Add("Ratio must be between 0 and 1.");

            if (_context.OptimizerAutoSmooth < 0 || _context.OptimizerAutoSmooth > 180)
                errors.Add("AutoSmooth must be between 0 and 180.");

            var parentDir = Directory.GetParent(addonDirectoryPath);
            if (parentDir == null || !CanWriteToDirectory(parentDir.FullName))
                errors.Add("No write permission in addon parent folder (output needs to be created there).");

            if (!CanWriteToDirectory(ToolPaths.WorkRoot))
                errors.Add("No write permission in work directory root.");

            errorMessage = string.Join("\n", errors);
            return errors.Count == 0;
        }

        private static bool CanWriteToDirectory(string directoryPath)
        {
            try
            {
                Directory.CreateDirectory(directoryPath);
                string testFile = Path.Combine(directoryPath, $"__write_test_{Guid.NewGuid():N}.tmp");
                File.WriteAllText(testFile, "ok");
                File.Delete(testFile);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static string? DetectBlenderPath()
        {
            var fromPath = FindOnPath("blender.exe");
            if (!string.IsNullOrWhiteSpace(fromPath))
                return fromPath;

            var candidates = new[]
            {
                @"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
                @"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
                @"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
                @"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
                @"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
            };

            return candidates.FirstOrDefault(File.Exists);
        }

        private static string? DetectStudioMdlPath()
        {
            var candidates = new[]
            {
                @"C:\Program Files (x86)\Steam\steamapps\common\GarrysMod\bin\studiomdl.exe",
                @"C:\Program Files\Steam\steamapps\common\GarrysMod\bin\studiomdl.exe",
            };

            return candidates.FirstOrDefault(File.Exists);
        }

        private static string? FindOnPath(string exeName)
        {
            var path = Environment.GetEnvironmentVariable("PATH");
            if (string.IsNullOrWhiteSpace(path))
                return null;

            foreach (var dir in path.Split(Path.PathSeparator))
            {
                var candidate = Path.Combine(dir.Trim(), exeName);
                if (File.Exists(candidate))
                    return candidate;
            }
            return null;
        }

        private async Task StartCompressProcessAsync(string addonDirectoryPath, Action<CompressAddonSystem>? onStart = null, bool unlockUiOnComplete = true)
        {
            _context.UnlockedUI = false;

            await Task.Delay(500);

            int rateIndex = _context.WavRateListIndex;
            int resolutionIndex = _context.ImageReducingResolutionListIndex;
            int targetWidth = (int)_context.ImageSizeLimitList[_context.ImageWidthLimitIndex];
            int targetHeight = (int)_context.ImageSizeLimitList[_context.ImageHeightLimitIndex];

            AudioContext.SamplingFrequency = _context.WavRateList[rateIndex];
            AudioContext.WavSampleRate = _context.WavRateList[Math.Clamp(_context.WavRateListIndex, 0, _context.WavRateList.Length - 1)];
            AudioContext.WavChannels = _context.WavChannelsIndex == 0 ? 2 : 1;
            AudioContext.WavCodec = _context.WavCodecIndex == 1
                ? AudioContext.WavCodecKind.AdpcmMs
                : AudioContext.WavCodecKind.Pcm16;

            AudioContext.Mp3SampleRate = _context.WavRateList[Math.Clamp(_context.Mp3RateIndex, 0, _context.WavRateList.Length - 1)];
            AudioContext.Mp3BitrateKbps = _context.AudioBitrateList[Math.Clamp(_context.Mp3BitrateIndex, 0, _context.AudioBitrateList.Length - 1)];
            AudioContext.OggSampleRate = _context.WavRateList[Math.Clamp(_context.OggRateIndex, 0, _context.WavRateList.Length - 1)];
            AudioContext.OggChannels = _context.OggChannelsIndex == 0 ? 2 : 1;
            AudioContext.OggQuality = MapOggQualityFromIndex(_context.OggQualityIndex);
            AudioContext.PreserveLoopMetadata = _context.AudioLoopSafe;
            ImageContext.Resolution = _context.ImageReducingResolutionList[resolutionIndex];
            ImageContext.TaargetWidth = targetWidth;
            ImageContext.TargetHeight = targetHeight;
            ImageContext.SkipWidth = (int)_context.ImageSkipWidth;
            ImageContext.SkipHeight = (int)_context.ImageSkipHeight;
            ImageContext.ReduceExactlyToLimits = _context.ReduceExactlyToLimits;
            ImageContext.KeepImageAspectRatio = _context.KeepImageAspectRatio;
            ImageContext.ImageMagickVTFCompress = _context.ImageMagickVTFCompress;
            LuaContext.ChangeOriginalCodeToMinimalistic = _context.ChangeOriginalCodeToMinimalistic;

            bool isPipelineCompress = _pipelineRunning && _pipelineStage == PipelineStage.Compress;
            var sizeToken = isPipelineCompress ? _pipelineCts?.Token ?? CancellationToken.None : CancellationToken.None;
            if (isPipelineCompress)
            {
                _pipelineCompressSizeAfter = null;
                _context.PipelineSizeReportText = "Size report: computing (before compress)...";
                _pipelineCompressSizeBefore = await TryScanSizeAsync(addonDirectoryPath, sizeToken);
            }
            else
            {
                _compressSizeAfter = null;
                _context.CompressSizeReportText = "Size report: computing (before)...";
                _compressSizeBefore = await TryScanSizeAsync(addonDirectoryPath, CancellationToken.None);
            }

            bool needsAudioProcessing = _context.CompressWAV || _context.CompressMP3 || _context.CompressOGG;
            bool audioAvailable = true;
            if (needsAudioProcessing && !EnsureFfmpegAvailable("Audio compression"))
                audioAvailable = false;
            if (needsAudioProcessing)
            {
                FFMpegSystem.AppendAudioLog(
                    $"AudioSettings | WAV {AudioContext.WavSampleRate}Hz {AudioContext.WavChannels}ch {AudioContext.WavCodec} | " +
                    $"MP3 {AudioContext.Mp3SampleRate}Hz {AudioContext.Mp3BitrateKbps}kbps | " +
                    $"OGG {AudioContext.OggSampleRate}Hz {AudioContext.OggChannels}ch q={AudioContext.OggQuality:0.0} (VBR) | Enabled={audioAvailable}");
            }

            var compressSystem = new CompressAddonSystem(addonDirectoryPath);

            if (_context.CompressVTF) compressSystem.IncludeVTF();
            if (audioAvailable)
            {
                if (_context.CompressWAV) compressSystem.IncludeWAV();
                if (_context.CompressMP3) compressSystem.IncludeMP3();
                if (_context.CompressOGG) compressSystem.IncludeOGG();
            }
            if (_context.CompressJPG) compressSystem.IncludeJPG();
            if (_context.CompressPNG) compressSystem.IncludePNG();
            if (_context.CompressLUA) compressSystem.IncludeLUA();

            onStart?.Invoke(compressSystem);

            var tcs = new TaskCompletionSource<bool>();
            compressSystem.e_ProgressChanged += CompressProgress;
            compressSystem.e_CompletedCompress += async () =>
            {
                if (isPipelineCompress)
                {
                    _context.PipelineSizeReportText = "Size report: computing (after compress)...";
                    _pipelineCompressSizeAfter = await TryScanSizeAsync(addonDirectoryPath, sizeToken);
                    _context.PipelineSizeReportText = BuildPipelineSizeReportText();
                }
                else
                {
                    _context.CompressSizeReportText = "Size report: computing (after)...";
                    _compressSizeAfter = await TryScanSizeAsync(addonDirectoryPath, CancellationToken.None);
                    _context.CompressSizeReportText = DirectorySizeReportFormatter.BuildReport(_compressSizeBefore, _compressSizeAfter);
                }

                CompressCompleted(unlockUiOnComplete);
                tcs.TrySetResult(true);
            };
            compressSystem.StartCompress();
            await tcs.Task;

        }

        private void CompressProgress(string filePath, int fileIndex, int filesCount)
        {
            double difference = (double)100 / (double)filesCount;
            double percent = (double)difference * (double)fileIndex;

            _context.ProgressBarMinValue = 0;
            _context.ProgressBarMaxValue = filesCount;
            _context.ProgressBarValue = fileIndex;
            _context.ProgressBarText = $"{(int)percent} % | Files: {fileIndex} / {filesCount}";

            if (_pipelineRunning && _pipelineStage == PipelineStage.Compress)
            {
                _context.PipelineProgressMinValue = 0;
                _context.PipelineProgressMaxValue = filesCount;
                _context.PipelineProgressValue = fileIndex;
                _context.PipelineProgressText = $"Current: File {fileIndex}/{filesCount}";
                _context.PipelineStatusText = "Compress Phase: Processing files";
                _pipelineCompressFilesProcessed = fileIndex;
                _pipelineCompressFilesTotal = filesCount;
            }
        }

        private void CompressCompleted(bool unlockUi)
        {
            _context.ProgressBarMinValue = 0;
            _context.ProgressBarMaxValue = 100;
            _context.ProgressBarValue = 0;
            _context.ProgressBarText = string.Empty;

            if (unlockUi)
                _context.UnlockedUI = true;

            if (_pipelineRunning && _pipelineStage == PipelineStage.Compress)
            {
                _context.PipelineProgressText = "Current: Completed.";
                _context.PipelineStatusText = "Compress Phase: Completed";
                _pipelineCompressOk = true;
                _pipelineCompressExitCode = 0;
            }
        }

        private static string BuildSizeReportText(DirectorySizeSnapshot? before, DirectorySizeSnapshot? after)
        {
            return DirectorySizeReportFormatter.BuildReport(before, after);
        }

        private string BuildPipelineSizeReportText()
        {
            var sb = new StringBuilder();

            if (_pipelineModelsSizeBefore != null || _pipelineModelsSizeAfter != null)
            {
                sb.AppendLine("Models size report:");
                sb.AppendLine(BuildSizeReportText(_pipelineModelsSizeBefore, _pipelineModelsSizeAfter));
            }

            if (_pipelineCompressSizeBefore != null || _pipelineCompressSizeAfter != null)
            {
                if (sb.Length > 0)
                    sb.AppendLine().AppendLine();
                sb.AppendLine("Compress size report:");
                sb.AppendLine(BuildSizeReportText(_pipelineCompressSizeBefore, _pipelineCompressSizeAfter));
            }

            return sb.ToString().TrimEnd();
        }

        private static async Task<DirectorySizeSnapshot?> TryScanSizeAsync(string rootPath, CancellationToken token)
        {
            try
            {
                return await DirectorySizeScanner.ScanAsync(rootPath, token);
            }
            catch (OperationCanceledException)
            {
                return null;
            }
            catch
            {
                return null;
            }
        }

        private string BuildPipelineSummaryDetails()
        {
            var modelsExit = FormatExitCode(_pipelineModelsExitCode);
            var compressExit = FormatExitCode(_pipelineCompressExitCode);
            var outputPath = string.IsNullOrWhiteSpace(_pipelineOutputPath) ? "(not found)" : _pipelineOutputPath;
            var workPath = string.IsNullOrWhiteSpace(_modelsWorkDir) ? "(not found)" : _modelsWorkDir;
            var lastError = string.IsNullOrWhiteSpace(_modelsLastErrorLine) ? "(none)" : _modelsLastErrorLine;

            var newline = Environment.NewLine;
            var sizeReport = string.IsNullOrWhiteSpace(_context.PipelineSizeReportText)
                ? "(none)"
                : _context.PipelineSizeReportText;
            return $"ModelsExitCode: {modelsExit}{newline}CompressExitCode: {compressExit}{newline}OutputPath: {outputPath}{newline}WorkPath: {workPath}{newline}LastErrorLine: {lastError}{newline}{newline}SizeReport:{newline}{sizeReport}";
        }

        private static string FormatExitCode(int? exitCode)
        {
            return exitCode.HasValue ? exitCode.Value.ToString() : "n/a";
        }

        private void CheckBox_EnableDebugConsole(object sender, RoutedEventArgs e)
        {
            if (!ConsoleHelper.TryAllocConsole(
                    out var error,
                    owner: this,
                    onHideRequested: () => Dispatcher.Invoke(() => CheckBox_DebugConsole.IsChecked = false)))
            {
                MessageBox.Show($"Failed to open debug console.{Environment.NewLine}{error}", "Debug Console", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }

        private void CheckBox_DisableDebugConsole(object sender, RoutedEventArgs e)
        {
            if (!ConsoleHelper.TryFreeConsole(out var error))
            {
                MessageBox.Show($"Failed to close debug console.{Environment.NewLine}{error}", "Debug Console", MessageBoxButton.OK, MessageBoxImage.Warning);
            }
        }
    }
}
