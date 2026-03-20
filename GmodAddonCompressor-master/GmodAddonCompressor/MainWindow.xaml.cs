using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Helpres;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Maps;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Optimizer;
using GmodAddonCompressor.Systems.Reporting;
using GmodAddonCompressor.Systems.Settings;
using GmodAddonCompressor.Systems.Tools;
using GmodAddonCompressor.Systems.Unpack;
using GmodAddonCompressor.Properties;
using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace GmodAddonCompressor
{
    public partial class MainWindow : Window
    {
        private static readonly TimeSpan SizeScanTimeout = TimeSpan.FromSeconds(8);
        private static readonly string[] ModelsAddonMarkerDirectories =
        {
            "cfg",
            "data",
            "effects",
            "entities",
            "gamemodes",
            "lua",
            "maps",
            "materials",
            "models",
            "particles",
            "resource",
            "scripts",
            "sound"
        };
        private static readonly string[] ModelsAddonMarkerFiles =
        {
            "addon.json",
            "addon.txt"
        };
        private MainWindowContext _context = new MainWindowContext();
        private const string _version = "v1.0.1";
        private readonly SourceAddonOptimizerRunner _optimizerRunner = new SourceAddonOptimizerRunner();
        private readonly AddonUnpackRunner _unpackRunner = new AddonUnpackRunner();
        private readonly MapBspAnalysisRunner _mapBspAnalysisRunner = new MapBspAnalysisRunner();
        private readonly MapBspBuildRunner _mapBspBuildRunner = new MapBspBuildRunner();
        private CancellationTokenSource? _addonWorkshopWarningCts = null;
        private CancellationTokenSource? _optimizerCts = null;
        private CancellationTokenSource? _unpackCts = null;
        private CancellationTokenSource? _mapScanCts = null;
        private CancellationTokenSource? _mapBuildCts = null;
        private string? _modelsOutputPath = null;
        private string? _modelsWorkDir = null;
        private string? _modelsLastErrorLine = null;
        private int _modelsStepIndex = 0;
        private int _modelsStepTotal = 0;
        private string _modelsPhase = string.Empty;
        private int _modelsBatchAddonIndex = 0;
        private int _modelsBatchAddonTotal = 0;
        private string _modelsBatchAddonName = string.Empty;
        private CancellationTokenSource? _pipelineCts = null;
        private CompressAddonSystem? _pipelineCompressSystem = null;
        private bool _pipelineRunning = false;
        private PipelineStage _pipelineStage = PipelineStage.None;
        private string? _pipelineOutputPath = null;
        private string? _pipelineLogsDir = null;
        private string? _addonWorkshopWarningScannedPath = null;
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
        private bool _unpackRunning = false;
        private bool _unpackCancelRequested = false;
        private bool _unpackLastActionWasRun = false;
        private int _unpackSupportedTotal = 0;
        private string? _unpackSummaryPath = null;
        private string? _unpackWorkDir = null;
        private string? _unpackCancelFile = null;
        private bool _unpackSummaryCancelled = false;
        private bool _unpackSummaryScanOnly = false;
        private int _unpackSummaryFailedCount = 0;
        private bool _mapScanRunning = false;
        private bool _mapStageOptimizeRunning = false;
        private bool _mapBuildRunning = false;
        private bool _mapScanCancelRequested = false;
        private string? _mapScanSummaryPath = null;
        private string? _mapScanWorkDir = null;
        private string? _mapScanCancelFile = null;
        private string? _mapStageOptimizeSummaryPath = null;
        private string? _mapBuildSummaryPath = null;
        private string? _mapBuildOutputPath = null;
        private string? _mapEffectiveRootPath = null;
        private string? _mapPreparedInputRootPath = null;
        private string? _mapInputModeDescription = null;
        private List<string> _mapScanStagingDirs = new List<string>();

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
            _unpackRunner.ProgressUpdate += UnpackProgressUpdate;
            _unpackRunner.SummaryPathFound += UnpackSummaryPathFound;
            _unpackRunner.WorkDirFound += UnpackWorkDirFound;
            _unpackRunner.LogLine += UnpackLogLine;
            _mapBspAnalysisRunner.ProgressUpdate += MapScanProgressUpdate;
            _mapBspAnalysisRunner.SummaryPathFound += MapScanSummaryPathFound;
            _mapBspAnalysisRunner.WorkDirFound += MapScanWorkDirFound;
            _mapBspAnalysisRunner.LogLine += MapScanLogLine;
            _mapBspBuildRunner.ProgressUpdate += MapBuildProgressUpdate;
            _mapBspBuildRunner.SummaryPathFound += MapBuildSummaryPathFound;
            _mapBspBuildRunner.WorkDirFound += MapBuildWorkDirFound;
            _mapBspBuildRunner.OutputDirFound += MapBuildOutputDirFound;
            _mapBspBuildRunner.LogLine += MapScanLogLine;
            InitializeAddonMerge();

            LoadSettings();
        }

        private void Button_SelectDirectory_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new Ookii.Dialogs.Wpf.VistaFolderBrowserDialog();
            if (dialog.ShowDialog(this).GetValueOrDefault())
            {
                _context.AddonDirectoryPath = dialog.SelectedPath;
                _ = RefreshAddonWorkshopWarningAsync(dialog.SelectedPath, force: true);
            }
        }

        private void ClearAddonWorkshopWarning()
        {
            _addonWorkshopWarningScannedPath = null;
            _context.AddonWorkshopWarningText = string.Empty;
        }

        private async Task RefreshAddonWorkshopWarningAsync(string addonDirectoryPath, bool force = false)
        {
            string normalizedPath = string.IsNullOrWhiteSpace(addonDirectoryPath)
                ? string.Empty
                : addonDirectoryPath.Trim();

            if (string.IsNullOrWhiteSpace(normalizedPath) || !Directory.Exists(normalizedPath))
            {
                _addonWorkshopWarningCts?.Cancel();
                _addonWorkshopWarningCts?.Dispose();
                _addonWorkshopWarningCts = null;
                ClearAddonWorkshopWarning();
                return;
            }

            normalizedPath = Path.GetFullPath(normalizedPath);
            if (!force &&
                string.Equals(_addonWorkshopWarningScannedPath, normalizedPath, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            _addonWorkshopWarningCts?.Cancel();
            _addonWorkshopWarningCts?.Dispose();

            var cts = new CancellationTokenSource();
            _addonWorkshopWarningCts = cts;
            ClearAddonWorkshopWarning();

            try
            {
                string warningText = await Task.Run(
                    () => WorkshopReferenceScanner.BuildWarningText(normalizedPath, cts.Token),
                    cts.Token);

                if (!ReferenceEquals(_addonWorkshopWarningCts, cts) || cts.IsCancellationRequested)
                    return;

                _addonWorkshopWarningScannedPath = normalizedPath;
                _context.AddonWorkshopWarningText = warningText;
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"Workshop warning scan failed for '{normalizedPath}': {ex}");
                if (ReferenceEquals(_addonWorkshopWarningCts, cts))
                    ClearAddonWorkshopWarning();
            }
            finally
            {
                if (ReferenceEquals(_addonWorkshopWarningCts, cts))
                    _addonWorkshopWarningCts = null;

                cts.Dispose();
            }
        }

        private void Button_SelectUnpackRoot_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new Ookii.Dialogs.Wpf.VistaFolderBrowserDialog();
            if (dialog.ShowDialog(this).GetValueOrDefault())
                TextBox_UnpackRootPath.Text = dialog.SelectedPath;
        }

        private void Button_BrowseGmad_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Select gmad.exe",
                Filter = "gmad.exe|gmad.exe|Executable|*.exe|All files|*.*",
                CheckFileExists = true
            };

            if (dialog.ShowDialog(this).GetValueOrDefault())
                TextBox_UnpackGmadPath.Text = dialog.FileName;
        }

        private void Button_AutoDetectGmad_Click(object sender, RoutedEventArgs e)
        {
            var detected = DetectGmadPath();
            if (!string.IsNullOrWhiteSpace(detected))
            {
                TextBox_UnpackGmadPath.Text = detected;
                return;
            }

            MessageBox.Show("gmad.exe not found. Please browse to gmad.exe.", "Descompactar addons", MessageBoxButton.OK, MessageBoxImage.Warning);
        }

        private void TextBox_UnpackRootPath_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (!_unpackRunning)
                ResetUnpackSummary();
            UpdateUnpackActionStates();
        }

        private void TextBox_UnpackGmadPath_TextChanged(object sender, TextChangedEventArgs e)
        {
            UpdateUnpackActionStates();
        }

        private void ResetUnpackSummary()
        {
            _unpackSupportedTotal = 0;
            _unpackSummaryPath = null;
            _unpackSummaryCancelled = false;
            _unpackSummaryScanOnly = false;
            _unpackSummaryFailedCount = 0;
            if (!_unpackRunning)
                _unpackWorkDir = null;
            TextBox_UnpackSummary.Text = "No scan executed yet.";
            TextBlock_UnpackFoundCount.Text = "0";
            TextBlock_UnpackGmaCount.Text = "0/0";
            TextBlock_UnpackBinSupportedCount.Text = "0";
            TextBlock_UnpackBinUnsupportedCount.Text = "0";
            TextBlock_UnpackOkCount.Text = "0";
            TextBlock_UnpackSkippedCount.Text = "0";
            TextBlock_UnpackFailedCount.Text = "0";
            ProgressBar_Unpack.IsIndeterminate = false;
            ProgressBar_Unpack.Minimum = 0;
            ProgressBar_Unpack.Maximum = 100;
            ProgressBar_Unpack.Value = 0;
            TextBlock_UnpackPhase.Text = "Idle";
            TextBlock_UnpackCurrent.Text = "Idle";
            TextBlock_UnpackStatus.Text = "Idle";
        }

        private void UpdateUnpackActionStates()
        {
            bool hasRoot = Directory.Exists(TextBox_UnpackRootPath.Text.Trim());
            bool hasGmad = File.Exists(TextBox_UnpackGmadPath.Text.Trim());

            Button_UnpackScan.IsEnabled = !_unpackRunning && hasRoot;
            Button_UnpackRun.IsEnabled = !_unpackRunning && hasRoot && hasGmad && _unpackSupportedTotal > 0;
            Button_UnpackCancel.IsEnabled = _unpackRunning && !_unpackCancelRequested;
            Button_UnpackOpenRoot.IsEnabled = !_unpackRunning && hasRoot;
        }

        private bool ValidateUnpackInputs(bool scanOnly, out string errorMessage)
        {
            var errors = new List<string>();
            string rootPath = TextBox_UnpackRootPath.Text.Trim();

            if (!Directory.Exists(rootPath))
                errors.Add("Root folder not found.");

            if (!CanWriteToDirectory(ToolPaths.WorkRoot))
                errors.Add("No write permission in work directory root.");

            if (!scanOnly)
            {
                string gmadPath = TextBox_UnpackGmadPath.Text.Trim();
                if (string.IsNullOrWhiteSpace(gmadPath) || !File.Exists(gmadPath))
                    errors.Add("gmad.exe path is invalid.");
            }

            errorMessage = string.Join("\n", errors);
            return errors.Count == 0;
        }

        private string GetSelectedUnpackExistingMode()
        {
            if (ComboBox_UnpackExistingMode.SelectedItem is ComboBoxItem item && item.Tag is string tag && !string.IsNullOrWhiteSpace(tag))
                return tag;
            return "skip";
        }

        private void SetSelectedUnpackExistingMode(string? mode)
        {
            string target = string.IsNullOrWhiteSpace(mode) ? "skip" : mode.Trim().ToLowerInvariant();
            for (int i = 0; i < ComboBox_UnpackExistingMode.Items.Count; i++)
            {
                if (ComboBox_UnpackExistingMode.Items[i] is ComboBoxItem item &&
                    item.Tag is string tag &&
                    string.Equals(tag, target, StringComparison.OrdinalIgnoreCase))
                {
                    ComboBox_UnpackExistingMode.SelectedIndex = i;
                    return;
                }
            }

            ComboBox_UnpackExistingMode.SelectedIndex = 0;
        }

        private static string BuildUnpackWorkDir(string rootPath)
        {
            string name = Path.GetFileName(rootPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
            if (string.IsNullOrWhiteSpace(name))
                name = "root";
            return Path.Combine(ToolPaths.WorkRoot, $"{name}_unpack_runs", DateTime.Now.ToString("yyyyMMdd_HHmmss_fff"));
        }

        private static string BuildUnpackCancelFilePath()
        {
            return Path.Combine(ToolPaths.WorkRoot, "_unpack_cancel_tokens", $"cancel_{Guid.NewGuid():N}.flag");
        }

        private void ClearUnpackCancelFile()
        {
            if (string.IsNullOrWhiteSpace(_unpackCancelFile))
                return;

            try
            {
                if (File.Exists(_unpackCancelFile))
                    File.Delete(_unpackCancelFile);
            }
            catch
            {
            }

            try
            {
                string? cancelDir = Path.GetDirectoryName(_unpackCancelFile);
                if (!string.IsNullOrWhiteSpace(cancelDir) && Directory.Exists(cancelDir) && !Directory.EnumerateFileSystemEntries(cancelDir).Any())
                    Directory.Delete(cancelDir);
            }
            catch
            {
            }

            _unpackCancelFile = null;
        }

        private void PrepareUnpackRunState(bool scanOnly)
        {
            SaveSettings();
            ClearUnpackCancelFile();

            _unpackCts?.Cancel();
            _unpackCts = new CancellationTokenSource();
            _unpackRunning = true;
            _unpackCancelRequested = false;
            _unpackLastActionWasRun = !scanOnly;
            _unpackSupportedTotal = 0;
            _unpackSummaryPath = null;
            _unpackWorkDir = BuildUnpackWorkDir(TextBox_UnpackRootPath.Text.Trim());
            _unpackCancelFile = BuildUnpackCancelFilePath();

            ProgressBar_Unpack.IsIndeterminate = true;
            ProgressBar_Unpack.Minimum = 0;
            ProgressBar_Unpack.Maximum = 100;
            ProgressBar_Unpack.Value = 0;
            TextBlock_UnpackPhase.Text = "Starting";
            TextBlock_UnpackCurrent.Text = scanOnly ? "Scanning archives..." : "Preparing extraction...";
            TextBlock_UnpackStatus.Text = "Running";
            TextBox_UnpackSummary.Text = scanOnly
                ? "Scanning addon archives..."
                : "Extracting addon archives...";

            AppendUnpackLog(string.Empty);
            AppendUnpackLog($"=== {TextBox_UnpackSummary.Text} ===");
            UpdateUnpackActionStates();
        }

        private async void Button_UnpackScan_Click(object sender, RoutedEventArgs e)
        {
            if (_unpackRunning)
                return;

            if (!ValidateUnpackInputs(scanOnly: true, out var errorMessage))
            {
                MessageBox.Show(errorMessage, "Descompactar addons", MessageBoxButton.OK, MessageBoxImage.Warning);
                TextBlock_UnpackStatus.Text = "Error";
                TextBox_UnpackSummary.Text = errorMessage;
                return;
            }

            if (!EnsureToolsAvailable("Descompactar addons"))
                return;

            PrepareUnpackRunState(scanOnly: true);
            await RunUnpackAsync(scanOnly: true);
        }

        private async void Button_UnpackRun_Click(object sender, RoutedEventArgs e)
        {
            if (_unpackRunning)
                return;

            if (!ValidateUnpackInputs(scanOnly: false, out var errorMessage))
            {
                MessageBox.Show(errorMessage, "Descompactar addons", MessageBoxButton.OK, MessageBoxImage.Warning);
                TextBlock_UnpackStatus.Text = "Error";
                TextBox_UnpackSummary.Text = errorMessage;
                return;
            }

            if (!EnsureToolsAvailable("Descompactar addons"))
                return;

            PrepareUnpackRunState(scanOnly: false);
            await RunUnpackAsync(scanOnly: false);
        }

        private async Task RunUnpackAsync(bool scanOnly)
        {
            if (!File.Exists(ToolPaths.WorkerExePath))
            {
                _unpackRunning = false;
                TextBlock_UnpackStatus.Text = "FAIL";
                TextBox_UnpackSummary.Text = "SourceAddonOptimizer worker not found.";
                UpdateUnpackActionStates();
                return;
            }

            var options = new AddonUnpackRunOptions
            {
                WorkerExePath = ToolPaths.WorkerExePath,
                RootPath = TextBox_UnpackRootPath.Text.Trim(),
                WorkDir = _unpackWorkDir ?? BuildUnpackWorkDir(TextBox_UnpackRootPath.Text.Trim()),
                GmadExePath = scanOnly ? null : TextBox_UnpackGmadPath.Text.Trim(),
                ExistingMode = GetSelectedUnpackExistingMode(),
                ScanOnly = scanOnly,
                ExtractMapPakContent = CheckBox_UnpackExtractMapPak.IsChecked == true,
                DeleteMapBspAfterExtract = CheckBox_UnpackDeleteMapBsp.IsChecked == true,
                CancelFilePath = _unpackCancelFile
            };

            int exitCode;
            try
            {
                exitCode = await _unpackRunner.RunAsync(options, _unpackCts?.Token ?? CancellationToken.None);
            }
            catch (OperationCanceledException)
            {
                exitCode = 130;
            }
            catch (Exception ex)
            {
                _unpackRunning = false;
                ProgressBar_Unpack.IsIndeterminate = false;
                ClearUnpackCancelFile();
                TextBlock_UnpackPhase.Text = "Error";
                TextBlock_UnpackStatus.Text = "FAIL";
                TextBox_UnpackSummary.Text = $"Execution failed before summary.{Environment.NewLine}{ex.Message}";
                AppendUnpackLog($"[GUI] {ex}");
                UpdateUnpackActionStates();
                return;
            }

            FinishUnpackRun(exitCode);
        }

        private void Button_UnpackCancel_Click(object sender, RoutedEventArgs e)
        {
            if (!_unpackRunning || _unpackCancelRequested)
                return;

            _unpackCancelRequested = true;
            try
            {
                if (!string.IsNullOrWhiteSpace(_unpackCancelFile))
                {
                    string? cancelDir = Path.GetDirectoryName(_unpackCancelFile);
                    if (!string.IsNullOrWhiteSpace(cancelDir))
                        Directory.CreateDirectory(cancelDir);
                    File.WriteAllText(_unpackCancelFile, "cancel");
                }
            }
            catch
            {
            }

            TextBlock_UnpackStatus.Text = "Canceling";
            TextBox_UnpackSummary.Text = "Cancellation requested. Waiting for worker cleanup...";
            AppendUnpackLog("[GUI] Cancellation requested. Waiting for safe shutdown...");
            UpdateUnpackActionStates();
            _ = ForceStopUnpackIfNeededAsync();
        }

        private async Task ForceStopUnpackIfNeededAsync()
        {
            await Task.Delay(TimeSpan.FromSeconds(5));

            if (!_unpackRunning || _unpackCts == null || _unpackCts.IsCancellationRequested)
                return;

            AppendUnpackLog("[GUI] Worker still running after timeout. Forcing stop.");
            _unpackCts.Cancel();
        }

        private void UnpackProgressUpdate(AddonUnpackProgressUpdate update)
        {
            Dispatcher.Invoke(() =>
            {
                if (!string.IsNullOrWhiteSpace(update.Phase))
                {
                    TextBlock_UnpackPhase.Text = update.Phase;
                    if (!update.ItemIndex.HasValue)
                    {
                        ProgressBar_Unpack.IsIndeterminate = true;
                        TextBlock_UnpackCurrent.Text = update.Phase;
                    }
                }

                if (update.ItemIndex.HasValue && update.ItemTotal.HasValue)
                {
                    ProgressBar_Unpack.IsIndeterminate = false;
                    ProgressBar_Unpack.Minimum = 0;
                    ProgressBar_Unpack.Maximum = update.ItemTotal.Value;
                    ProgressBar_Unpack.Value = update.ItemIndex.Value;
                    TextBlock_UnpackCurrent.Text = string.IsNullOrWhiteSpace(update.CurrentPath)
                        ? $"Current: {update.ItemIndex}/{update.ItemTotal}"
                        : $"Current: {update.ItemIndex}/{update.ItemTotal} | {update.CurrentPath}";
                }
            });
        }

        private void UnpackSummaryPathFound(string path)
        {
            _unpackSummaryPath = path;
        }

        private void UnpackWorkDirFound(string path)
        {
            _unpackWorkDir = path;
        }

        private void UnpackLogLine(string line)
        {
            Dispatcher.Invoke(() => AppendUnpackLog(line));
        }

        private void AppendUnpackLog(string line)
        {
            const int trimThreshold = 400000;
            const int trimTarget = 250000;

            if (TextBox_UnpackLog.Text.Length > trimThreshold)
                TextBox_UnpackLog.Text = TextBox_UnpackLog.Text[^trimTarget..];

            if (TextBox_UnpackLog.Text.Length > 0)
                TextBox_UnpackLog.AppendText(Environment.NewLine);

            TextBox_UnpackLog.AppendText(line);
            TextBox_UnpackLog.ScrollToEnd();
        }

        private void FinishUnpackRun(int exitCode)
        {
            _unpackRunning = false;
            ProgressBar_Unpack.IsIndeterminate = false;
            ClearUnpackCancelFile();

            bool loadedSummary = LoadUnpackSummary();
            if (loadedSummary)
            {
                if (_unpackSummaryCancelled || exitCode == 130)
                {
                    TextBlock_UnpackPhase.Text = "Canceled";
                    TextBlock_UnpackStatus.Text = "Canceled";
                }
                else if (_unpackSummaryFailedCount > 0 || exitCode != 0)
                {
                    TextBlock_UnpackPhase.Text = "Completed";
                    TextBlock_UnpackStatus.Text = "Completed with failures";
                }
                else
                {
                    TextBlock_UnpackPhase.Text = "Completed";
                    TextBlock_UnpackStatus.Text = "OK";
                    ProgressBar_Unpack.Minimum = 0;
                    ProgressBar_Unpack.Maximum = 100;
                    ProgressBar_Unpack.Value = 100;
                }
            }
            else
            {
                TextBlock_UnpackPhase.Text = exitCode == 130 ? "Canceled" : "Completed";
                TextBlock_UnpackStatus.Text = exitCode == 0 ? "OK" : exitCode == 130 ? "Canceled" : $"FAIL ({exitCode})";
                TextBox_UnpackSummary.Text = exitCode == 130
                    ? "Execution canceled before summary was generated. Check the log."
                    : "Execution ended without unpack_summary.json. Check the log.";
            }

            _unpackCancelRequested = false;
            UpdateUnpackActionStates();

            if (exitCode == 0 && _unpackLastActionWasRun && CheckBox_UnpackOpenOnFinish.IsChecked == true)
                OpenFolder(TextBox_UnpackRootPath.Text.Trim(), "Descompactar addons");
        }

        private bool LoadUnpackSummary()
        {
            if (string.IsNullOrWhiteSpace(_unpackSummaryPath) || !File.Exists(_unpackSummaryPath))
                return false;

            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(_unpackSummaryPath));
                var root = document.RootElement;
                var counts = root.TryGetProperty("counts", out var countsElement) ? countsElement : default;
                var run = root.TryGetProperty("run", out var runElement) ? runElement : default;

                int foundTotal = GetJsonInt(counts, "found_total");
                int gmaSupported = GetJsonInt(counts, "gma_supported");
                int gmaTotal = GetJsonInt(counts, "gma_total");
                int binSupported = GetJsonInt(counts, "bin_supported");
                int binUnsupported = GetJsonInt(counts, "bin_unsupported");
                int okCount = GetJsonInt(counts, "ok");
                int skippedCount = GetJsonInt(counts, "skipped");
                int failedCount = GetJsonInt(counts, "failed");
                int unsupportedCount = GetJsonInt(counts, "unsupported");
                int cancelledItems = GetJsonInt(counts, "cancelled_items");
                int scanWarningCount = GetJsonInt(counts, "scan_errors");
                int mapBspFound = GetJsonInt(counts, "map_bsp_found");
                int mapBspWithPak = GetJsonInt(counts, "map_bsp_with_pak");
                int mapPakFilesExtracted = GetJsonInt(counts, "map_pak_files_extracted");
                int mapBspDeleted = GetJsonInt(counts, "map_bsp_deleted");
                _unpackSupportedTotal = GetJsonInt(counts, "supported_total");

                bool cancelled = GetJsonBool(counts, "cancelled");
                bool scanOnly = GetJsonBool(run, "scan_only");
                bool extractMapPak = GetJsonBool(run, "extract_map_pak");
                bool deleteMapBsp = GetJsonBool(run, "delete_map_bsp");
                string rootPath = GetJsonString(run, "root") ?? TextBox_UnpackRootPath.Text.Trim();
                string gmadError = GetJsonString(run, "gmad_error") ?? string.Empty;
                _unpackSummaryCancelled = cancelled;
                _unpackSummaryScanOnly = scanOnly;
                _unpackSummaryFailedCount = failedCount;

                TextBlock_UnpackFoundCount.Text = foundTotal.ToString();
                TextBlock_UnpackGmaCount.Text = $"{gmaSupported}/{gmaTotal}";
                TextBlock_UnpackBinSupportedCount.Text = binSupported.ToString();
                TextBlock_UnpackBinUnsupportedCount.Text = binUnsupported.ToString();
                TextBlock_UnpackOkCount.Text = okCount.ToString();
                TextBlock_UnpackSkippedCount.Text = skippedCount.ToString();
                TextBlock_UnpackFailedCount.Text = failedCount.ToString();

                string statusText;
                if (scanOnly)
                {
                    if (cancelled)
                    {
                        _unpackSupportedTotal = 0;
                        statusText = "Scan canceled by user.";
                    }
                    else if (foundTotal == 0)
                    {
                        statusText = "Scan completed: no .gma or .bin files found.";
                    }
                    else if (_unpackSupportedTotal == 0)
                    {
                        statusText = $"Scan completed: {foundTotal} archive(s) found, none supported.";
                    }
                    else
                    {
                        statusText = $"Scan completed: {foundTotal} found, {_unpackSupportedTotal} supported for extraction.";
                    }
                }
                else
                {
                    if (cancelled)
                    {
                        statusText = $"Extraction canceled: ok={okCount}, skipped={skippedCount}, failed={failedCount}, cancelled={cancelledItems}.";
                    }
                    else if (failedCount > 0)
                    {
                        statusText = $"Extraction completed with failures: ok={okCount}, failed={failedCount}, skipped={skippedCount}.";
                    }
                    else if (okCount == 0 && skippedCount == 0 && unsupportedCount > 0)
                    {
                        statusText = $"Extraction completed without supported outputs: unsupported={unsupportedCount}.";
                    }
                    else
                    {
                        statusText = $"Extraction completed: ok={okCount}, skipped={skippedCount}.";
                    }
                }

                if (scanWarningCount > 0)
                    statusText += $"{Environment.NewLine}Scan warnings: {scanWarningCount}.";
                if (!scanOnly && extractMapPak && mapBspFound > 0)
                {
                    string mapStatus = $"Map content: {mapBspWithPak}/{mapBspFound} BSP pakfile(s), {mapPakFilesExtracted} file(s) extracted";
                    if (deleteMapBsp && mapBspDeleted > 0)
                        mapStatus += $", {mapBspDeleted} BSP file(s) deleted";
                    statusText += $"{Environment.NewLine}{mapStatus}.";
                }
                if (!scanOnly && !string.IsNullOrWhiteSpace(gmadError))
                    statusText += $"{Environment.NewLine}{gmadError}";
                statusText += $"{Environment.NewLine}Root: {rootPath}";

                TextBox_UnpackSummary.Text = statusText;
                return true;
            }
            catch (Exception ex)
            {
                TextBox_UnpackSummary.Text = $"Failed to read unpack_summary.json.{Environment.NewLine}{ex.Message}";
                return false;
            }
        }

        private static int GetJsonInt(JsonElement element, string propertyName)
        {
            if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(propertyName, out var value))
                return 0;

            if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
                return number;

            if (value.ValueKind == JsonValueKind.String && int.TryParse(value.GetString(), out var parsed))
                return parsed;

            return 0;
        }

        private static bool GetJsonBool(JsonElement element, string propertyName)
        {
            if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(propertyName, out var value))
                return false;

            if (value.ValueKind == JsonValueKind.True)
                return true;
            if (value.ValueKind == JsonValueKind.False)
                return false;
            if (value.ValueKind == JsonValueKind.String && bool.TryParse(value.GetString(), out var parsed))
                return parsed;
            return false;
        }

        private static string? GetJsonString(JsonElement element, string propertyName)
        {
            if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(propertyName, out var value))
                return null;
            if (value.ValueKind == JsonValueKind.String)
                return value.GetString();
            return value.ToString();
        }

        private void Button_UnpackOpenRoot_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(TextBox_UnpackRootPath.Text.Trim(), "Descompactar addons");
        }

        private void Button_SelectMapScanRoot_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new Ookii.Dialogs.Wpf.VistaFolderBrowserDialog();
            if (dialog.ShowDialog(this).GetValueOrDefault())
                TextBox_MapScanRootPath.Text = dialog.SelectedPath;
        }

        private void TextBox_MapScanRootPath_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (!_mapScanRunning && !_mapStageOptimizeRunning && !_mapBuildRunning)
                ResetMapScanSummary();
            UpdateMapScanActionStates();
        }

        private void ResetMapScanSummary()
        {
            _mapScanSummaryPath = null;
            _mapStageOptimizeSummaryPath = null;
            _mapBuildSummaryPath = null;
            _mapBuildOutputPath = null;
            _mapScanStagingDirs.Clear();
            if (!_mapScanRunning && !_mapStageOptimizeRunning && !_mapBuildRunning)
                _mapScanWorkDir = null;

            TextBox_MapScanSummary.Text = "No BSP scan executed yet.";
            TextBlock_MapScanBspCount.Text = "0";
            TextBlock_MapScanPakZipCount.Text = "0";
            TextBlock_MapScanStagedBspCount.Text = "0";
            TextBlock_MapScanStagedFileCount.Text = "0";
            TextBlock_MapScanCandidateCount.Text = "0";
            TextBlock_MapScanBlockedCount.Text = "0";
            TextBlock_MapScanAddonSize.Text = "0 B";
            TextBlock_MapScanPakTotalSize.Text = "0 B";
            ProgressBar_MapScan.IsIndeterminate = false;
            ProgressBar_MapScan.Minimum = 0;
            ProgressBar_MapScan.Maximum = 100;
            ProgressBar_MapScan.Value = 0;
            TextBlock_MapScanPhase.Text = "Idle";
            TextBlock_MapScanCurrent.Text = "Idle";
            TextBlock_MapScanStatus.Text = "Idle";
        }

        private void UpdateMapScanActionStates()
        {
            bool hasRoot = Directory.Exists(TextBox_MapScanRootPath.Text.Trim());
            bool hasWork = !string.IsNullOrWhiteSpace(_mapScanWorkDir) && Directory.Exists(_mapScanWorkDir);
            bool hasStaging = _mapScanStagingDirs.Any(Directory.Exists);
            bool hasStageOptimizeSummary = !string.IsNullOrWhiteSpace(_mapStageOptimizeSummaryPath) && File.Exists(_mapStageOptimizeSummaryPath);
            bool hasOutput = !string.IsNullOrWhiteSpace(_mapBuildOutputPath) && Directory.Exists(_mapBuildOutputPath);
            bool isBusy = _mapScanRunning || _mapStageOptimizeRunning || _mapBuildRunning;

            Button_MapScanRun.IsEnabled = !isBusy && hasRoot;
            Button_MapStageOptimize.IsEnabled = !isBusy && hasStaging;
            Button_MapBuildRun.IsEnabled = !isBusy && hasStaging && hasStageOptimizeSummary;
            Button_MapScanCancel.IsEnabled = (_mapScanRunning || _mapBuildRunning) && !_mapScanCancelRequested;
            Button_MapScanOpenRoot.IsEnabled = !isBusy && hasRoot;
            Button_MapScanOpenWork.IsEnabled = !isBusy && hasWork;
            Button_MapBuildOpenOutput.IsEnabled = !isBusy && hasOutput;
        }

        private static bool DirectoryContainsMapBsp(string rootPath)
        {
            try
            {
                foreach (string file in Directory.EnumerateFiles(rootPath, "*.bsp", SearchOption.AllDirectories))
                {
                    string? parent = Path.GetDirectoryName(file);
                    if (string.IsNullOrWhiteSpace(parent))
                        continue;

                    var parts = parent.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                    if (parts.Any(part => string.Equals(part, "maps", StringComparison.OrdinalIgnoreCase)))
                        return true;
                }
            }
            catch
            {
            }

            return false;
        }

        private static bool DirectoryContainsAddonArchives(string rootPath)
        {
            try
            {
                return Directory.EnumerateFiles(rootPath, "*.gma", SearchOption.AllDirectories).Any()
                    || Directory.EnumerateFiles(rootPath, "*.bin", SearchOption.AllDirectories).Any();
            }
            catch
            {
                return false;
            }
        }

        private static string BuildMapPreparedInputRoot(string workDir)
        {
            return Path.Combine(workDir, "prepared_input");
        }

        private async Task<string> PrepareMapInputRootAsync(string selectedRootPath, CancellationToken cancellationToken)
        {
            if (DirectoryContainsMapBsp(selectedRootPath))
            {
                _mapPreparedInputRootPath = null;
                _mapInputModeDescription = "Existing extracted addon (maps/*.bsp already present)";
                return selectedRootPath;
            }

            if (!DirectoryContainsAddonArchives(selectedRootPath))
                throw new InvalidOperationException("No maps/*.bsp or .gma/.bin archives were found under this root.");

            string gmadPath = TextBox_UnpackGmadPath.Text.Trim();
            if (!File.Exists(gmadPath))
            {
                string? detected = DetectGmadPath();
                if (!string.IsNullOrWhiteSpace(detected))
                {
                    TextBox_UnpackGmadPath.Text = detected;
                    gmadPath = detected;
                }
            }

            if (!File.Exists(gmadPath))
                throw new InvalidOperationException("This root only contains .gma/.bin archives, so gmad.exe is required. Set the GMAD path first.");

            string workDir = _mapScanWorkDir ?? BuildMapScanWorkDir(selectedRootPath);
            string preparedRoot = BuildMapPreparedInputRoot(workDir);
            string unpackWorkDir = Path.Combine(workDir, "prepare_unpack");

            _mapPreparedInputRootPath = preparedRoot;
            _mapInputModeDescription = "Raw archive root (.gma/.bin) extracted into work before map scan";

            AppendMapScanLog("[MAP-PREP] No maps/*.bsp found in the selected root. Preparing raw .gma/.bin archives into work...");
            AppendMapScanLog($"[MAP-PREP] Prepared input root: {preparedRoot}");

            var prepareRunner = new AddonUnpackRunner();
            prepareRunner.ProgressUpdate += update =>
            {
                Dispatcher.Invoke(() =>
                {
                    if (!string.IsNullOrWhiteSpace(update.Phase))
                    {
                        TextBlock_MapScanPhase.Text = $"Prepare input: {update.Phase}";
                        ProgressBar_MapScan.IsIndeterminate = true;
                    }

                    if (update.ItemIndex.HasValue && update.ItemTotal.HasValue)
                    {
                        ProgressBar_MapScan.IsIndeterminate = false;
                        ProgressBar_MapScan.Minimum = 0;
                        ProgressBar_MapScan.Maximum = update.ItemTotal.Value;
                        ProgressBar_MapScan.Value = update.ItemIndex.Value;
                        TextBlock_MapScanCurrent.Text = string.IsNullOrWhiteSpace(update.CurrentPath)
                            ? $"Current: Addon {update.ItemIndex}/{update.ItemTotal}"
                            : $"Current: Addon {update.ItemIndex}/{update.ItemTotal} | {update.CurrentPath}";
                    }
                });
            };
            prepareRunner.LogLine += line => Dispatcher.Invoke(() => AppendMapScanLog($"[MAP-PREP] {line}"));
            prepareRunner.ErrorLine += line => Dispatcher.Invoke(() => AppendMapScanLog($"[MAP-PREP][ERR] {line}"));

            var options = new AddonUnpackRunOptions
            {
                WorkerExePath = ToolPaths.WorkerExePath,
                RootPath = selectedRootPath,
                WorkDir = unpackWorkDir,
                GmadExePath = gmadPath,
                OutputRootPath = preparedRoot,
                ExistingMode = "overwrite",
                ScanOnly = false,
                ExtractMapPakContent = false,
                DeleteMapBspAfterExtract = false,
                CancelFilePath = _mapScanCancelFile
            };

            int exitCode = await prepareRunner.RunAsync(options, cancellationToken);
            if (exitCode != 0 && exitCode != 130)
                throw new InvalidOperationException($"Preparing raw .gma/.bin archives failed with exit code {exitCode}.");
            if (exitCode == 130)
                throw new OperationCanceledException("Raw addon preparation was cancelled.");
            if (!Directory.Exists(preparedRoot))
                throw new InvalidOperationException("Prepared input root was not created.");
            if (!DirectoryContainsMapBsp(preparedRoot))
                throw new InvalidOperationException("Raw addon extraction finished, but no maps/*.bsp were found in the prepared input.");

            AppendMapScanLog("[MAP-PREP] Raw archive extraction completed. Continuing with BSP scan...");
            return preparedRoot;
        }

        private bool ValidateMapScanInputs(out string errorMessage)
        {
            var errors = new List<string>();
            string rootPath = TextBox_MapScanRootPath.Text.Trim();

            if (!Directory.Exists(rootPath))
                errors.Add("Addon root folder not found.");

            if (Directory.Exists(rootPath) &&
                !DirectoryContainsMapBsp(rootPath) &&
                !DirectoryContainsAddonArchives(rootPath))
            {
                errors.Add("The selected root does not contain maps/*.bsp or .gma/.bin archives.");
            }

            if (!CanWriteToDirectory(ToolPaths.WorkRoot))
                errors.Add("No write permission in work directory root.");

            errorMessage = string.Join("\n", errors);
            return errors.Count == 0;
        }

        private static string BuildMapScanWorkDir(string rootPath)
        {
            string name = Path.GetFileName(rootPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
            if (string.IsNullOrWhiteSpace(name))
                name = "root";
            return Path.Combine(ToolPaths.WorkRoot, $"{name}_map_scan_runs", DateTime.Now.ToString("yyyyMMdd_HHmmss_fff"));
        }

        private static string BuildMapBuildOutputDir(string rootPath)
        {
            string trimmed = rootPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string name = Path.GetFileName(trimmed);
            if (string.IsNullOrWhiteSpace(name))
                name = "root";

            string? parent = Directory.GetParent(trimmed)?.FullName;
            if (string.IsNullOrWhiteSpace(parent))
                throw new InvalidOperationException("Unable to resolve addon parent folder for map output.");

            string basePath = Path.Combine(parent, $"{name}_mapassets_optimized");
            if (!Directory.Exists(basePath) && !File.Exists(basePath))
                return basePath;

            return Path.Combine(parent, $"{name}_mapassets_optimized_{DateTime.Now:yyyyMMdd_HHmmss}");
        }

        private static string BuildMapScanCancelFilePath()
        {
            return Path.Combine(ToolPaths.WorkRoot, "_map_scan_cancel_tokens", $"cancel_{Guid.NewGuid():N}.flag");
        }

        private void ClearMapScanCancelFile()
        {
            if (string.IsNullOrWhiteSpace(_mapScanCancelFile))
                return;

            try
            {
                if (File.Exists(_mapScanCancelFile))
                    File.Delete(_mapScanCancelFile);
            }
            catch
            {
            }

            try
            {
                string? cancelDir = Path.GetDirectoryName(_mapScanCancelFile);
                if (!string.IsNullOrWhiteSpace(cancelDir) && Directory.Exists(cancelDir) && !Directory.EnumerateFileSystemEntries(cancelDir).Any())
                    Directory.Delete(cancelDir);
            }
            catch
            {
            }

            _mapScanCancelFile = null;
        }

        private void PrepareMapScanRunState()
        {
            SaveSettings();
            ClearMapScanCancelFile();

            _mapScanCts?.Cancel();
            _mapScanCts = new CancellationTokenSource();
            _mapScanRunning = true;
            _mapScanCancelRequested = false;
            _mapScanSummaryPath = null;
            _mapStageOptimizeSummaryPath = null;
            _mapBuildSummaryPath = null;
            _mapBuildOutputPath = null;
            _mapEffectiveRootPath = null;
            _mapPreparedInputRootPath = null;
            _mapInputModeDescription = null;
            _mapScanStagingDirs.Clear();
            _mapScanWorkDir = BuildMapScanWorkDir(TextBox_MapScanRootPath.Text.Trim());
            _mapScanCancelFile = BuildMapScanCancelFilePath();

            ProgressBar_MapScan.IsIndeterminate = true;
            ProgressBar_MapScan.Minimum = 0;
            ProgressBar_MapScan.Maximum = 100;
            ProgressBar_MapScan.Value = 0;
            TextBlock_MapScanPhase.Text = "Starting";
            TextBlock_MapScanCurrent.Text = "Preparing map input...";
            TextBlock_MapScanStatus.Text = "Running";
            TextBox_MapScanSummary.Text = "Preparing map input and staged analysis...";

            AppendMapScanLog(string.Empty);
            AppendMapScanLog("=== Preparing input, scanning BSP files and extracting staging for Optimize Maps ===");
            UpdateMapScanActionStates();
        }

        private async void Button_MapScanRun_Click(object sender, RoutedEventArgs e)
        {
            if (_mapScanRunning)
                return;

            if (!ValidateMapScanInputs(out var errorMessage))
            {
                MessageBox.Show(errorMessage, "Optimize Maps", MessageBoxButton.OK, MessageBoxImage.Warning);
                TextBlock_MapScanStatus.Text = "Error";
                TextBox_MapScanSummary.Text = errorMessage;
                return;
            }

            if (!EnsureToolsAvailable("Optimize Maps"))
                return;

            PrepareMapScanRunState();
            await RunMapScanAsync();
        }

        private async Task RunMapScanAsync()
        {
            if (!File.Exists(ToolPaths.WorkerExePath))
            {
                _mapScanRunning = false;
                TextBlock_MapScanStatus.Text = "FAIL";
                TextBox_MapScanSummary.Text = "SourceAddonOptimizer worker not found.";
                UpdateMapScanActionStates();
                return;
            }

            int exitCode;
            try
            {
                string selectedRoot = TextBox_MapScanRootPath.Text.Trim();
                _mapEffectiveRootPath = await PrepareMapInputRootAsync(selectedRoot, _mapScanCts?.Token ?? CancellationToken.None);

                ProgressBar_MapScan.IsIndeterminate = true;
                TextBlock_MapScanPhase.Text = "Scan + Stage";
                TextBlock_MapScanCurrent.Text = "Scanning maps/*.bsp and extracting staging...";
                TextBlock_MapScanStatus.Text = "Running";

                var options = new MapBspAnalysisRunOptions
                {
                    WorkerExePath = ToolPaths.WorkerExePath,
                    RootPath = _mapEffectiveRootPath,
                    WorkDir = _mapScanWorkDir ?? BuildMapScanWorkDir(selectedRoot),
                    CancelFilePath = _mapScanCancelFile
                };

                exitCode = await _mapBspAnalysisRunner.RunAsync(options, _mapScanCts?.Token ?? CancellationToken.None);
            }
            catch (OperationCanceledException)
            {
                exitCode = 130;
            }
            catch (Exception ex)
            {
                _mapScanRunning = false;
                ProgressBar_MapScan.IsIndeterminate = false;
                ClearMapScanCancelFile();
                TextBlock_MapScanPhase.Text = "Error";
                TextBlock_MapScanStatus.Text = "FAIL";
                TextBox_MapScanSummary.Text = $"Execution failed before summary.{Environment.NewLine}{ex.Message}";
                AppendMapScanLog($"[GUI] {ex}");
                UpdateMapScanActionStates();
                return;
            }

            FinishMapScanRun(exitCode);
        }

        private void Button_MapScanCancel_Click(object sender, RoutedEventArgs e)
        {
            if ((!_mapScanRunning && !_mapBuildRunning) || _mapScanCancelRequested)
                return;

            _mapScanCancelRequested = true;
            try
            {
                if (!string.IsNullOrWhiteSpace(_mapScanCancelFile))
                {
                    string? cancelDir = Path.GetDirectoryName(_mapScanCancelFile);
                    if (!string.IsNullOrWhiteSpace(cancelDir))
                        Directory.CreateDirectory(cancelDir);
                    File.WriteAllText(_mapScanCancelFile, "cancel");
                }
            }
            catch
            {
            }

            TextBlock_MapScanStatus.Text = "Canceling";
            TextBox_MapScanSummary.Text = "Cancellation requested. Waiting for worker cleanup...";
            AppendMapScanLog("[GUI] Cancellation requested. Waiting for safe shutdown...");
            UpdateMapScanActionStates();
            _ = ForceStopMapScanIfNeededAsync();
        }

        private async Task ForceStopMapScanIfNeededAsync()
        {
            await Task.Delay(TimeSpan.FromSeconds(5));

            if ((!_mapScanRunning && !_mapBuildRunning) ||
                ((_mapScanCts == null || _mapScanCts.IsCancellationRequested) &&
                 (_mapBuildCts == null || _mapBuildCts.IsCancellationRequested)))
                return;

            AppendMapScanLog("[GUI] Worker still running after timeout. Forcing stop.");
            _mapScanCts?.Cancel();
            _mapBuildCts?.Cancel();
        }

        private void MapScanProgressUpdate(MapBspAnalysisProgressUpdate update)
        {
            Dispatcher.Invoke(() =>
            {
                if (!string.IsNullOrWhiteSpace(update.Phase))
                {
                    TextBlock_MapScanPhase.Text = update.Phase;
                    if (!update.ItemIndex.HasValue)
                    {
                        ProgressBar_MapScan.IsIndeterminate = true;
                        TextBlock_MapScanCurrent.Text = update.Phase;
                    }
                }

                if (update.ItemIndex.HasValue && update.ItemTotal.HasValue)
                {
                    ProgressBar_MapScan.IsIndeterminate = false;
                    ProgressBar_MapScan.Minimum = 0;
                    ProgressBar_MapScan.Maximum = update.ItemTotal.Value;
                    ProgressBar_MapScan.Value = update.ItemIndex.Value;
                    TextBlock_MapScanCurrent.Text = string.IsNullOrWhiteSpace(update.CurrentPath)
                        ? $"Current: {update.ItemIndex}/{update.ItemTotal}"
                        : $"Current: {update.ItemIndex}/{update.ItemTotal} | {update.CurrentPath}";
                }
            });
        }

        private void MapScanSummaryPathFound(string path)
        {
            _mapScanSummaryPath = path;
        }

        private void MapScanWorkDirFound(string path)
        {
            _mapScanWorkDir = path;
        }

        private void MapScanLogLine(string line)
        {
            Dispatcher.Invoke(() => AppendMapScanLog(line));
        }

        private void AppendMapScanLog(string line)
        {
            const int trimThreshold = 400000;
            const int trimTarget = 250000;

            if (TextBox_MapScanLog.Text.Length > trimThreshold)
                TextBox_MapScanLog.Text = TextBox_MapScanLog.Text[^trimTarget..];

            if (TextBox_MapScanLog.Text.Length > 0)
                TextBox_MapScanLog.AppendText(Environment.NewLine);

            TextBox_MapScanLog.AppendText(line);
            TextBox_MapScanLog.ScrollToEnd();
        }

        private void FinishMapScanRun(int exitCode)
        {
            _mapScanRunning = false;
            ProgressBar_MapScan.IsIndeterminate = false;
            ClearMapScanCancelFile();

            bool loadedSummary = LoadMapScanSummary();
            if (loadedSummary)
            {
                if (exitCode == 130)
                {
                    TextBlock_MapScanPhase.Text = "Canceled";
                    TextBlock_MapScanStatus.Text = "Canceled";
                }
                else if (exitCode != 0)
                {
                    TextBlock_MapScanPhase.Text = "Completed";
                    TextBlock_MapScanStatus.Text = "Completed with warnings";
                }
                else
                {
                    TextBlock_MapScanPhase.Text = "Completed";
                    TextBlock_MapScanStatus.Text = "OK";
                    ProgressBar_MapScan.Minimum = 0;
                    ProgressBar_MapScan.Maximum = 100;
                    ProgressBar_MapScan.Value = 100;
                }
            }
            else
            {
                TextBlock_MapScanPhase.Text = exitCode == 130 ? "Canceled" : "Completed";
                TextBlock_MapScanStatus.Text = exitCode == 0 ? "OK" : exitCode == 130 ? "Canceled" : $"FAIL ({exitCode})";
                TextBox_MapScanSummary.Text = exitCode == 130
                    ? "Execution canceled before summary was generated. Check the log."
                    : "Execution ended without map_bsp_scan_summary.json. Check the log.";
            }

            _mapScanCancelRequested = false;
            UpdateMapScanActionStates();
        }

        private async void Button_MapStageOptimize_Click(object sender, RoutedEventArgs e)
        {
            if (_mapScanRunning || _mapStageOptimizeRunning)
                return;

            if (!LoadMapScanSummary())
            {
                MessageBox.Show("Run Scan + Stage first so the staging inventory exists.", "Optimize Maps", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            var stageRoots = _mapScanStagingDirs
                .Where(path => !string.IsNullOrWhiteSpace(path) && Directory.Exists(path))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();

            if (stageRoots.Length == 0)
            {
                MessageBox.Show("No staged pak content was found. Run Scan + Stage first.", "Optimize Maps", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            _mapStageOptimizeRunning = true;
            _context.UnlockedUI = false;
            _mapStageOptimizeSummaryPath = Path.Combine(_mapScanWorkDir ?? ToolPaths.WorkRoot, "map_stage_optimize_summary.json");

            ProgressBar_MapScan.IsIndeterminate = true;
            ProgressBar_MapScan.Minimum = 0;
            ProgressBar_MapScan.Maximum = 100;
            ProgressBar_MapScan.Value = 0;
            TextBlock_MapScanPhase.Text = "Optimize Staging";
            TextBlock_MapScanCurrent.Text = "Preparing staged asset optimization...";
            TextBlock_MapScanStatus.Text = "Running";
            AppendMapScanLog(string.Empty);
            AppendMapScanLog("=== Optimizing staged pak assets for Optimize Maps (Phase 3) ===");
            AppendMapScanLog($"[MAP-OPT] Staging roots: {stageRoots.Length}");
            UpdateMapScanActionStates();

            bool audioAvailable = true;
            bool needsAudioProcessing = _context.CompressWAV || _context.CompressMP3 || _context.CompressOGG;
            if (needsAudioProcessing && !EnsureFfmpegAvailable("Map staging audio compression"))
                audioAvailable = false;

            try
            {
                int resolutionIndex = _context.ImageReducingResolutionListIndex;
                int targetWidth = (int)_context.ImageSizeLimitList[_context.ImageWidthLimitIndex];
                int targetHeight = (int)_context.ImageSizeLimitList[_context.ImageHeightLimitIndex];

                var options = new MapStageOptimizationOptions
                {
                    StageRoots = stageRoots,
                    WorkDir = _mapScanWorkDir ?? ToolPaths.WorkRoot,
                    SummaryPath = _mapStageOptimizeSummaryPath,
                    IncludeVtf = _context.CompressVTF,
                    IncludeWav = audioAvailable && _context.CompressWAV,
                    IncludeMp3 = audioAvailable && _context.CompressMP3,
                    IncludeOgg = audioAvailable && _context.CompressOGG,
                    IncludeJpg = _context.CompressJPG,
                    IncludePng = _context.CompressPNG,
                    IncludeLua = _context.CompressLUA,
                    WavSampleRate = _context.WavRateList[Math.Clamp(_context.WavRateListIndex, 0, _context.WavRateList.Length - 1)],
                    WavChannels = _context.WavChannelsIndex == 0 ? 2 : 1,
                    WavCodec = _context.WavCodecIndex == 1 ? AudioContext.WavCodecKind.AdpcmMs : AudioContext.WavCodecKind.Pcm16,
                    Mp3SampleRate = _context.WavRateList[Math.Clamp(_context.Mp3RateIndex, 0, _context.WavRateList.Length - 1)],
                    Mp3BitrateKbps = _context.AudioBitrateList[Math.Clamp(_context.Mp3BitrateIndex, 0, _context.AudioBitrateList.Length - 1)],
                    OggSampleRate = _context.WavRateList[Math.Clamp(_context.OggRateIndex, 0, _context.WavRateList.Length - 1)],
                    OggChannels = _context.OggChannelsIndex == 0 ? 2 : 1,
                    OggQuality = MapOggQualityFromIndex(_context.OggQualityIndex),
                    PreserveLoopMetadata = _context.AudioLoopSafe,
                    ImageResolution = _context.ImageReducingResolutionList[resolutionIndex],
                    TargetWidth = targetWidth,
                    TargetHeight = targetHeight,
                    SkipWidth = (int)_context.ImageSkipWidth,
                    SkipHeight = (int)_context.ImageSkipHeight,
                    ReduceExactlyToLimits = _context.ReduceExactlyToLimits,
                    KeepImageAspectRatio = _context.KeepImageAspectRatio,
                    ImageMagickVtfCompress = _context.ImageMagickVTFCompress,
                    LuaMinimalistic = _context.ChangeOriginalCodeToMinimalistic,
                    Log = message => Dispatcher.Invoke(() => AppendMapScanLog(message)),
                    Progress = (filePath, fileIndex, filesCount) =>
                    {
                        Dispatcher.Invoke(() =>
                        {
                            ProgressBar_MapScan.IsIndeterminate = false;
                            ProgressBar_MapScan.Minimum = 0;
                            ProgressBar_MapScan.Maximum = Math.Max(filesCount, 1);
                            ProgressBar_MapScan.Value = Math.Min(fileIndex, Math.Max(filesCount, 1));
                            TextBlock_MapScanCurrent.Text = $"Current: File {fileIndex}/{filesCount} | {filePath}";
                            TextBlock_MapScanStatus.Text = "Optimizing staged assets";
                        });
                    }
                };

                if (needsAudioProcessing)
                {
                    AppendMapScanLog(
                        $"[MAP-OPT] AudioSettings | WAV {options.WavSampleRate}Hz {options.WavChannels}ch {options.WavCodec} | " +
                        $"MP3 {options.Mp3SampleRate}Hz {options.Mp3BitrateKbps}kbps | " +
                        $"OGG {options.OggSampleRate}Hz {options.OggChannels}ch q={options.OggQuality:0.0} (VBR) | Enabled={audioAvailable}");
                }

                var result = await MapStageOptimizationService.RunAsync(options);

                ProgressBar_MapScan.IsIndeterminate = false;
                ProgressBar_MapScan.Minimum = 0;
                ProgressBar_MapScan.Maximum = 100;
                ProgressBar_MapScan.Value = 100;
                TextBlock_MapScanPhase.Text = "Optimize Staging";
                TextBlock_MapScanCurrent.Text = "Current: Completed.";
                TextBlock_MapScanStatus.Text = "OK";
                AppendMapScanLog($"[MAP-OPT] Summary: {result.SummaryPath}");

                LoadMapScanSummary();
            }
            catch (Exception ex)
            {
                ProgressBar_MapScan.IsIndeterminate = false;
                TextBlock_MapScanPhase.Text = "Optimize Staging";
                TextBlock_MapScanStatus.Text = "FAIL";
                TextBlock_MapScanCurrent.Text = "Current: Failed.";
                AppendMapScanLog($"[MAP-OPT] {ex}");
                TextBox_MapScanSummary.Text = string.Join(Environment.NewLine, new[]
                {
                    "Step: Optimize Staging",
                    $"Selected root: {TextBox_MapScanRootPath.Text.Trim()}",
                    !string.IsNullOrWhiteSpace(_mapInputModeDescription) ? $"Input mode: {_mapInputModeDescription}" : null,
                    "Result: FAIL",
                    $"Reason: {ex.Message}"
                }.Where(line => !string.IsNullOrWhiteSpace(line)));
            }
            finally
            {
                _mapStageOptimizeRunning = false;
                _context.UnlockedUI = true;
                UpdateMapScanActionStates();
            }
        }

        private async void Button_MapBuildRun_Click(object sender, RoutedEventArgs e)
        {
            if (_mapScanRunning || _mapStageOptimizeRunning || _mapBuildRunning)
                return;

            if (!LoadMapScanSummary())
            {
                MessageBox.Show("Run Scan + Stage first so the BSP summary exists.", "Optimize Maps", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (string.IsNullOrWhiteSpace(_mapStageOptimizeSummaryPath) || !File.Exists(_mapStageOptimizeSummaryPath))
            {
                MessageBox.Show("Run Optimize Staging first so the staged asset summary exists.", "Optimize Maps", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            string plannedOutputDir;
            try
            {
                plannedOutputDir = BuildMapBuildOutputDir(TextBox_MapScanRootPath.Text.Trim());
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "Optimize Maps", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            string? outputParent = Directory.GetParent(plannedOutputDir)?.FullName;
            if (string.IsNullOrWhiteSpace(outputParent) || !CanWriteToDirectory(outputParent))
            {
                MessageBox.Show("No write permission in addon parent folder (map output needs to be created there).", "Optimize Maps", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (!EnsureToolsAvailable("Optimize Maps"))
                return;

            SaveSettings();
            ClearMapScanCancelFile();
            _mapBuildCts?.Cancel();
            _mapBuildCts = new CancellationTokenSource();
            _mapBuildRunning = true;
            _mapScanCancelRequested = false;
            _mapBuildSummaryPath = null;
            _mapBuildOutputPath = plannedOutputDir;
            _mapScanCancelFile = BuildMapScanCancelFilePath();

            ProgressBar_MapScan.IsIndeterminate = true;
            ProgressBar_MapScan.Minimum = 0;
            ProgressBar_MapScan.Maximum = 100;
            ProgressBar_MapScan.Value = 0;
            TextBlock_MapScanPhase.Text = "Rebuild + Inject";
            TextBlock_MapScanCurrent.Text = "Preparing EOF-only BSP rebuild...";
            TextBlock_MapScanStatus.Text = "Running";
            AppendMapScanLog(string.Empty);
            AppendMapScanLog("=== Rebuilding staged pak ZIPs and reinjecting them into BSPs (Phase 4, EOF-only) ===");
            AppendMapScanLog($"[MAP-BUILD] Output dir: {_mapBuildOutputPath}");
            _context.UnlockedUI = false;
            UpdateMapScanActionStates();

            try
            {
                var options = new MapBspBuildRunOptions
                {
                    WorkerExePath = ToolPaths.WorkerExePath,
                    RootPath = _mapEffectiveRootPath ?? TextBox_MapScanRootPath.Text.Trim(),
                    WorkDir = _mapScanWorkDir ?? BuildMapScanWorkDir(TextBox_MapScanRootPath.Text.Trim()),
                    OutputDir = _mapBuildOutputPath ?? BuildMapBuildOutputDir(TextBox_MapScanRootPath.Text.Trim()),
                    CancelFilePath = _mapScanCancelFile
                };

                int exitCode = await _mapBspBuildRunner.RunAsync(options, _mapBuildCts.Token);
                FinishMapBuildRun(exitCode);
            }
            catch (OperationCanceledException)
            {
                FinishMapBuildRun(130);
            }
            catch (Exception ex)
            {
                _mapBuildRunning = false;
                ProgressBar_MapScan.IsIndeterminate = false;
                ClearMapScanCancelFile();
                TextBlock_MapScanPhase.Text = "Rebuild + Inject";
                TextBlock_MapScanStatus.Text = "FAIL";
                TextBlock_MapScanCurrent.Text = "Current: Failed.";
                AppendMapScanLog($"[MAP-BUILD] {ex}");
                TextBox_MapScanSummary.Text = string.Join(Environment.NewLine, new[]
                {
                    "Step: Build + Inject",
                    $"Selected root: {TextBox_MapScanRootPath.Text.Trim()}",
                    !string.IsNullOrWhiteSpace(_mapInputModeDescription) ? $"Input mode: {_mapInputModeDescription}" : null,
                    "Result: FAIL",
                    $"Reason: {ex.Message}"
                }.Where(line => !string.IsNullOrWhiteSpace(line)));
                _context.UnlockedUI = true;
                UpdateMapScanActionStates();
            }
        }

        private void MapBuildProgressUpdate(MapBspBuildProgressUpdate update)
        {
            Dispatcher.Invoke(() =>
            {
                if (!string.IsNullOrWhiteSpace(update.Phase))
                {
                    TextBlock_MapScanPhase.Text = update.Phase;
                    if (!update.ItemIndex.HasValue)
                    {
                        ProgressBar_MapScan.IsIndeterminate = true;
                        TextBlock_MapScanCurrent.Text = update.Phase;
                    }
                }

                if (update.ItemIndex.HasValue && update.ItemTotal.HasValue)
                {
                    ProgressBar_MapScan.IsIndeterminate = false;
                    ProgressBar_MapScan.Minimum = 0;
                    ProgressBar_MapScan.Maximum = update.ItemTotal.Value;
                    ProgressBar_MapScan.Value = update.ItemIndex.Value;
                    TextBlock_MapScanCurrent.Text = string.IsNullOrWhiteSpace(update.CurrentPath)
                        ? $"Current: {update.ItemIndex}/{update.ItemTotal}"
                        : $"Current: {update.ItemIndex}/{update.ItemTotal} | {update.CurrentPath}";
                }
            });
        }

        private void MapBuildSummaryPathFound(string path)
        {
            _mapBuildSummaryPath = path;
        }

        private void MapBuildWorkDirFound(string path)
        {
            _mapScanWorkDir = path;
        }

        private void MapBuildOutputDirFound(string path)
        {
            _mapBuildOutputPath = path;
        }

        private void FinishMapBuildRun(int exitCode)
        {
            _mapBuildRunning = false;
            ProgressBar_MapScan.IsIndeterminate = false;
            ClearMapScanCancelFile();

            bool loadedSummary = LoadMapScanSummary();
            if (loadedSummary)
            {
                if (exitCode == 130)
                {
                    TextBlock_MapScanPhase.Text = "Canceled";
                    TextBlock_MapScanStatus.Text = "Canceled";
                }
                else if (exitCode != 0)
                {
                    TextBlock_MapScanPhase.Text = "Completed";
                    TextBlock_MapScanStatus.Text = "Completed with warnings";
                }
                else
                {
                    TextBlock_MapScanPhase.Text = "Rebuild + Inject";
                    TextBlock_MapScanStatus.Text = "OK";
                    TextBlock_MapScanCurrent.Text = "Current: Completed.";
                    ProgressBar_MapScan.Minimum = 0;
                    ProgressBar_MapScan.Maximum = 100;
                    ProgressBar_MapScan.Value = 100;
                }
            }
            else
            {
                TextBlock_MapScanPhase.Text = exitCode == 130 ? "Canceled" : "Completed";
                TextBlock_MapScanStatus.Text = exitCode == 0 ? "OK" : exitCode == 130 ? "Canceled" : $"FAIL ({exitCode})";
                TextBox_MapScanSummary.Text = exitCode == 130
                    ? "Execution canceled before build summary was generated. Check the log."
                    : "Execution ended without map_bsp_build_summary.json. Check the log.";
            }

            _mapScanCancelRequested = false;
            _context.UnlockedUI = true;
            UpdateMapScanActionStates();
        }

        private bool LoadMapScanSummary()
        {
            if (string.IsNullOrWhiteSpace(_mapScanSummaryPath) || !File.Exists(_mapScanSummaryPath))
                return false;

            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(_mapScanSummaryPath));
                var root = document.RootElement;
                var counts = root.TryGetProperty("counts", out var countsElement) ? countsElement : default;
                var sizes = root.TryGetProperty("sizes", out var sizesElement) ? sizesElement : default;
                var run = root.TryGetProperty("run", out var runElement) ? runElement : default;
                var futureValidation = root.TryGetProperty("future_validation", out var validationElement) ? validationElement : default;

                int bspTotal = GetJsonInt(counts, "bsp_total");
                int validPakZip = GetJsonInt(counts, "bsp_with_valid_zip");
                int stagedBspCount = GetJsonInt(counts, "staged_bsp_count");
                int stagedFilesTotal = GetJsonInt(counts, "staged_files_total");
                int candidateCount = GetJsonInt(counts, "phase2_candidate_count");
                int blockedCount = GetJsonInt(counts, "phase2_blocked_count");
                int analysisErrors = GetJsonInt(counts, "analysis_errors");
                int scanErrors = GetJsonInt(counts, "scan_errors");

                long addonTotalBytes = GetJsonLong(sizes, "addon_total_bytes");
                long pakTotalBytes = GetJsonLong(sizes, "pak_total_bytes");
                long stagedTotalBytes = GetJsonLong(sizes, "staged_total_bytes");
                double pakShareOfBsp = GetJsonDouble(sizes, "pak_share_of_all_bsp_percent");
                string rootPath = GetJsonString(run, "root") ?? TextBox_MapScanRootPath.Text.Trim();
                string stagingRoot = string.Empty;
                var stagingDirs = new List<string>();
                _mapEffectiveRootPath = rootPath;
                if (root.TryGetProperty("staging", out var stagingElement) && stagingElement.ValueKind == JsonValueKind.Object)
                    stagingRoot = GetJsonString(stagingElement, "root") ?? string.Empty;

                TextBlock_MapScanBspCount.Text = bspTotal.ToString();
                TextBlock_MapScanPakZipCount.Text = validPakZip.ToString();
                TextBlock_MapScanStagedBspCount.Text = stagedBspCount.ToString();
                TextBlock_MapScanStagedFileCount.Text = stagedFilesTotal.ToString();
                TextBlock_MapScanCandidateCount.Text = candidateCount.ToString();
                TextBlock_MapScanBlockedCount.Text = blockedCount.ToString();
                TextBlock_MapScanAddonSize.Text = FormatBytes(addonTotalBytes);
                TextBlock_MapScanPakTotalSize.Text = FormatBytes(pakTotalBytes);

                if (root.TryGetProperty("items", out var itemsElement) && itemsElement.ValueKind == JsonValueKind.Array && itemsElement.GetArrayLength() > 0)
                {
                    foreach (var item in itemsElement.EnumerateArray())
                    {
                        string relativePath = GetJsonString(item, "relative_path") ?? "(unknown)";
                        long bspSize = GetJsonLong(item, "bsp_size");
                        long pakSize = GetJsonLong(item, "pak_size");
                        double pakPercent = GetJsonDouble(item, "pak_percent_of_bsp");
                        bool pakZipValid = GetJsonBool(item, "pak_zip_valid");
                        bool pakAtEof = GetJsonBool(item, "pak_at_eof");
                        string stagingStatus = GetJsonString(item, "staging_status") ?? "not_attempted";
                        string stagingDir = GetJsonString(item, "staging_dir") ?? string.Empty;
                        if (!string.IsNullOrWhiteSpace(stagingDir))
                        {
                            if (string.Equals(stagingStatus, "extracted", StringComparison.OrdinalIgnoreCase) && Directory.Exists(stagingDir))
                                stagingDirs.Add(stagingDir);
                        }
                    }
                }

                _mapScanStagingDirs = stagingDirs
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .ToList();

                List<string> summaryLines;
                if (!string.IsNullOrWhiteSpace(_mapBuildSummaryPath) && File.Exists(_mapBuildSummaryPath))
                {
                    summaryLines = BuildMapBuildSummaryLines(root, counts, sizes, run);
                }
                else if (!string.IsNullOrWhiteSpace(_mapStageOptimizeSummaryPath) && File.Exists(_mapStageOptimizeSummaryPath))
                {
                    summaryLines = BuildMapStageOptimizeSummaryLines(root, counts, sizes, run);
                }
                else
                {
                    summaryLines = BuildMapScanSummaryLines(root, counts, sizes, run, futureValidation, analysisErrors, scanErrors);
                }

                TextBox_MapScanSummary.Text = string.Join(Environment.NewLine, summaryLines.Where(line => line != null));
                UpdateMapScanActionStates();
                return true;
            }
            catch (Exception ex)
            {
                TextBox_MapScanSummary.Text = $"Failed to read map_bsp_scan_summary.json.{Environment.NewLine}{ex.Message}";
                return false;
            }
        }

        private List<string> BuildMapSummaryHeader(string stepTitle, JsonElement run)
        {
            string selectedRoot = TextBox_MapScanRootPath.Text.Trim();
            string effectiveRoot = GetJsonString(run, "root")
                ?? _mapEffectiveRootPath
                ?? selectedRoot;

            var lines = new List<string>
            {
                $"Step: {stepTitle}",
                $"Selected root: {selectedRoot}"
            };

            if (!string.IsNullOrWhiteSpace(_mapInputModeDescription))
                lines.Add($"Input mode: {_mapInputModeDescription}");

            if (!string.Equals(selectedRoot, effectiveRoot, StringComparison.OrdinalIgnoreCase))
                lines.Add($"Effective scan root: {effectiveRoot}");

            return lines;
        }

        private List<string> BuildMapScanSummaryLines(
            JsonElement root,
            JsonElement counts,
            JsonElement sizes,
            JsonElement run,
            JsonElement futureValidation,
            int analysisErrors,
            int scanErrors)
        {
            int bspTotal = GetJsonInt(counts, "bsp_total");
            int validPakZip = GetJsonInt(counts, "bsp_with_valid_zip");
            int stagedBspCount = GetJsonInt(counts, "staged_bsp_count");
            int stagedFilesTotal = GetJsonInt(counts, "staged_files_total");
            int candidateCount = GetJsonInt(counts, "phase2_candidate_count");
            int blockedCount = GetJsonInt(counts, "phase2_blocked_count");
            long addonTotalBytes = GetJsonLong(sizes, "addon_total_bytes");
            long pakTotalBytes = GetJsonLong(sizes, "pak_total_bytes");
            long stagedTotalBytes = GetJsonLong(sizes, "staged_total_bytes");
            double pakShareOfBsp = GetJsonDouble(sizes, "pak_share_of_all_bsp_percent");
            string stagingRoot = string.Empty;

            if (root.TryGetProperty("staging", out var stagingElement) && stagingElement.ValueKind == JsonValueKind.Object)
                stagingRoot = GetJsonString(stagingElement, "root") ?? string.Empty;

            var summaryLines = BuildMapSummaryHeader("Scan + Stage", run);
            summaryLines.Add($"BSP files found: {bspTotal}");
            summaryLines.Add($"Valid embedded pak ZIPs: {validPakZip}");
            summaryLines.Add($"Staging extracted: {stagedBspCount} BSP(s), {stagedFilesTotal} file(s), {FormatBytes(stagedTotalBytes)}");
            summaryLines.Add($"Future EOF-only candidates: {candidateCount}");
            summaryLines.Add($"Blocked from future reinjection: {blockedCount}");
            summaryLines.Add($"Addon total size: {FormatBytes(addonTotalBytes)}");
            summaryLines.Add($"Combined pak size: {FormatBytes(pakTotalBytes)} ({pakShareOfBsp:0.##}% of all BSP bytes)");
            if (!string.IsNullOrWhiteSpace(stagingRoot))
                summaryLines.Add($"Staging root: {stagingRoot}");

            if (analysisErrors > 0 || scanErrors > 0)
                summaryLines.Add($"Warnings: analysis={analysisErrors}, scan={scanErrors}");

            summaryLines.Add(string.Empty);
            summaryLines.Add("BSP analysis:");

            if (root.TryGetProperty("items", out var itemsElement) && itemsElement.ValueKind == JsonValueKind.Array && itemsElement.GetArrayLength() > 0)
            {
                foreach (var item in itemsElement.EnumerateArray())
                {
                    string relativePath = GetJsonString(item, "relative_path") ?? "(unknown)";
                    long bspSize = GetJsonLong(item, "bsp_size");
                    long pakSize = GetJsonLong(item, "pak_size");
                    double pakPercent = GetJsonDouble(item, "pak_percent_of_bsp");
                    bool pakZipValid = GetJsonBool(item, "pak_zip_valid");
                    bool pakAtEof = GetJsonBool(item, "pak_at_eof");
                    bool phase2Candidate = GetJsonBool(item, "phase2_candidate");
                    string message = GetJsonString(item, "message") ?? string.Empty;

                    summaryLines.Add($"- {relativePath} | BSP {FormatBytes(bspSize)} | PAK {FormatBytes(pakSize)} | {pakPercent:0.##}% | ZIP {YesNo(pakZipValid)} | EOF {YesNo(pakAtEof)} | Candidate {YesNo(phase2Candidate)}");

                    if (item.TryGetProperty("phase2_blockers", out var blockersElement) &&
                        blockersElement.ValueKind == JsonValueKind.Array &&
                        blockersElement.GetArrayLength() > 0)
                    {
                        var blockers = blockersElement
                            .EnumerateArray()
                            .Where(v => v.ValueKind == JsonValueKind.String)
                            .Select(v => v.GetString())
                            .Where(v => !string.IsNullOrWhiteSpace(v))
                            .ToArray();
                        if (blockers.Length > 0)
                            summaryLines.Add($"  Reason: {string.Join(", ", blockers)}");
                    }
                    else if (!string.IsNullOrWhiteSpace(message))
                    {
                        summaryLines.Add($"  Note: {message}");
                    }
                }
            }
            else
            {
                summaryLines.Add("- No maps/*.bsp found under the effective scan root.");
            }

            if (futureValidation.ValueKind == JsonValueKind.Object &&
                futureValidation.TryGetProperty("phase2_candidate_rule", out var candidateRuleElement) &&
                candidateRuleElement.ValueKind == JsonValueKind.String)
            {
                string candidateRule = candidateRuleElement.GetString() ?? string.Empty;
                if (!string.IsNullOrWhiteSpace(candidateRule))
                {
                    summaryLines.Add(string.Empty);
                    summaryLines.Add($"Future reinjection gate: {candidateRule}");
                }
            }

            return summaryLines;
        }

        private List<string> BuildMapStageOptimizeSummaryLines(JsonElement scanRoot, JsonElement scanCounts, JsonElement scanSizes, JsonElement scanRun)
        {
            var summaryLines = BuildMapSummaryHeader("Optimize Staging", scanRun);
            summaryLines.Add($"Staged BSPs available: {GetJsonInt(scanCounts, "staged_bsp_count")}");
            summaryLines.Add($"Current staged file count: {GetJsonInt(scanCounts, "staged_files_total")}");

            if (string.IsNullOrWhiteSpace(_mapStageOptimizeSummaryPath) || !File.Exists(_mapStageOptimizeSummaryPath))
                return summaryLines;

            using var document = JsonDocument.Parse(File.ReadAllText(_mapStageOptimizeSummaryPath));
            var root = document.RootElement;
            var run = root.TryGetProperty("run", out var runElement) ? runElement : default;
            var totals = root.TryGetProperty("totals", out var totalsElement) ? totalsElement : default;
            var vtfSafety = root.TryGetProperty("vtf_safety", out var vtfSafetyElement) ? vtfSafetyElement : default;

            long beforeBytes = GetJsonLong(totals, "before_bytes");
            long afterBytes = GetJsonLong(totals, "after_bytes");
            long deltaBytes = GetJsonLong(totals, "delta_bytes");
            int beforeFiles = GetJsonInt(totals, "before_files");
            int afterFiles = GetJsonInt(totals, "after_files");
            double deltaPercent = GetJsonDouble(totals, "delta_percent");
            int visitedRoots = GetJsonInt(run, "visited_stage_roots");

            summaryLines.Add($"Stage roots processed: {visitedRoots}");
            summaryLines.Add($"Staged size: {FormatBytes(beforeBytes)} -> {FormatBytes(afterBytes)} ({FormatSignedBytes(deltaBytes)}, {deltaPercent:0.##}%)");
            summaryLines.Add($"File count: {beforeFiles} -> {afterFiles}");

            int safeVtfCount = GetJsonInt(vtfSafety, "safe_vtf_count");
            int optimizedSpecialVtfCount = GetJsonInt(vtfSafety, "optimized_special_vtf_count");
            long optimizedSpecialVtfBeforeBytes = GetJsonLong(vtfSafety, "optimized_special_vtf_before_bytes");
            long optimizedSpecialVtfAfterBytes = GetJsonLong(vtfSafety, "optimized_special_vtf_after_bytes");
            int skippedSpecialVtfCount = GetJsonInt(vtfSafety, "skipped_special_vtf_count");
            long skippedSpecialVtfBytes = GetJsonLong(vtfSafety, "skipped_special_vtf_bytes");
            if (safeVtfCount > 0 || optimizedSpecialVtfCount > 0 || skippedSpecialVtfCount > 0)
            {
                summaryLines.Add($"Safe VTFs processed as plain textures: {safeVtfCount}");
                if (optimizedSpecialVtfCount > 0)
                    summaryLines.Add($"Special skybox VTFs optimized with preserved metadata: {optimizedSpecialVtfCount} ({FormatBytes(optimizedSpecialVtfBeforeBytes)} -> {FormatBytes(optimizedSpecialVtfAfterBytes)})");
                summaryLines.Add($"Special VTFs preserved unchanged: {skippedSpecialVtfCount} ({FormatBytes(skippedSpecialVtfBytes)})");
            }

            if (run.TryGetProperty("processed_extensions", out var processedElement) && processedElement.ValueKind == JsonValueKind.Array)
            {
                var processed = processedElement
                    .EnumerateArray()
                    .Where(value => value.ValueKind == JsonValueKind.String)
                    .Select(value => value.GetString())
                    .Where(value => !string.IsNullOrWhiteSpace(value))
                    .ToArray();
                summaryLines.Add($"Processed: {(processed.Length > 0 ? string.Join(", ", processed) : "(none)")}");
            }

            if (run.TryGetProperty("inventoried_only_extensions", out var inventoriedElement) && inventoriedElement.ValueKind == JsonValueKind.Array)
            {
                var inventoried = inventoriedElement
                    .EnumerateArray()
                    .Where(value => value.ValueKind == JsonValueKind.String)
                    .Select(value => value.GetString())
                    .Where(value => !string.IsNullOrWhiteSpace(value))
                    .ToArray();
                summaryLines.Add($"Inventoried only: {(inventoried.Length > 0 ? string.Join(", ", inventoried) : "(none)")}");
            }

            if (root.TryGetProperty("entries", out var entriesElement) && entriesElement.ValueKind == JsonValueKind.Array)
            {
                summaryLines.Add(string.Empty);
                summaryLines.Add("Top staged types:");
                foreach (var entry in entriesElement.EnumerateArray().Take(8))
                {
                    string extension = GetJsonString(entry, "extension") ?? "(unknown)";
                    bool processed = GetJsonBool(entry, "processed");
                    long extBefore = GetJsonLong(entry, "before_bytes");
                    long extAfter = GetJsonLong(entry, "after_bytes");
                    long extDelta = GetJsonLong(entry, "delta_bytes");
                    summaryLines.Add($"- {extension} {(processed ? "[processed]" : "[inventory]")} | {FormatBytes(extBefore)} -> {FormatBytes(extAfter)} ({FormatSignedBytes(extDelta)})");
                }
            }

            if (vtfSafety.ValueKind == JsonValueKind.Object &&
                vtfSafety.TryGetProperty("skipped_special_vtf_reasons", out var reasonElement) &&
                reasonElement.ValueKind == JsonValueKind.Array &&
                reasonElement.GetArrayLength() > 0)
            {
                summaryLines.Add(string.Empty);
                summaryLines.Add("Preserved VTF classes:");
                foreach (var reason in reasonElement.EnumerateArray().Take(6))
                {
                    string reasonName = GetJsonString(reason, "reason") ?? "(unknown)";
                    int reasonFileCount = GetJsonInt(reason, "file_count");
                    long reasonBytes = GetJsonLong(reason, "total_bytes");
                    summaryLines.Add($"- {reasonName}: {reasonFileCount} file(s), {FormatBytes(reasonBytes)}");
                }
            }

            return summaryLines;
        }

        private List<string> BuildMapBuildSummaryLines(JsonElement scanRoot, JsonElement scanCounts, JsonElement scanSizes, JsonElement scanRun)
        {
            var summaryLines = BuildMapSummaryHeader("Build + Inject", scanRun);
            summaryLines.Add($"Scanned BSPs: {GetJsonInt(scanCounts, "bsp_total")}");

            if (string.IsNullOrWhiteSpace(_mapBuildSummaryPath) || !File.Exists(_mapBuildSummaryPath))
                return summaryLines;

            using var document = JsonDocument.Parse(File.ReadAllText(_mapBuildSummaryPath));
            var root = document.RootElement;
            var run = root.TryGetProperty("run", out var runElement) ? runElement : default;
            var counts = root.TryGetProperty("counts", out var countsElement) ? countsElement : default;
            var sizes = root.TryGetProperty("sizes", out var sizesElement) ? sizesElement : default;

            int eligibleTotal = GetJsonInt(counts, "eligible_total");
            int reinjectedTotal = GetJsonInt(counts, "reinjected_total");
            int unsupportedTotal = GetJsonInt(counts, "unsupported_total");
            int failedTotal = GetJsonInt(counts, "failed_total");

            long inputAddonBytes = GetJsonLong(sizes, "input_addon_total_bytes");
            long outputAddonBytes = GetJsonLong(sizes, "output_addon_total_bytes");
            long addonDeltaBytes = GetJsonLong(sizes, "addon_delta_bytes");
            double addonDeltaPercent = GetJsonDouble(sizes, "addon_delta_percent");
            long sourceBspBytes = GetJsonLong(sizes, "reinjected_source_bsp_total_bytes");
            long outputBspBytes = GetJsonLong(sizes, "reinjected_output_bsp_total_bytes");
            long sourcePakBytes = GetJsonLong(sizes, "reinjected_source_pak_total_bytes");
            long outputPakBytes = GetJsonLong(sizes, "reinjected_output_pak_total_bytes");
            bool outputCreated = GetJsonBool(run, "output_created");
            string outputDir = GetJsonString(run, "output_dir") ?? string.Empty;
            string mode = GetJsonString(run, "mode") ?? "EOF-only";
            string fatalError = GetJsonString(run, "fatal_error") ?? string.Empty;

            if (!string.IsNullOrWhiteSpace(outputDir))
                _mapBuildOutputPath = outputDir;

            summaryLines.Add($"Mode: {mode}");
            summaryLines.Add($"Eligible BSPs: {eligibleTotal}");
            summaryLines.Add($"Reinjected: {reinjectedTotal}");
            summaryLines.Add($"Unsupported: {unsupportedTotal}");
            summaryLines.Add($"Failed: {failedTotal}");
            summaryLines.Add($"Output created: {YesNo(outputCreated)}");
            if (!string.IsNullOrWhiteSpace(outputDir))
                summaryLines.Add($"Output dir: {outputDir}");
            if (outputCreated)
                summaryLines.Add($"Addon size: {FormatBytes(inputAddonBytes)} -> {FormatBytes(outputAddonBytes)} ({FormatSignedBytes(addonDeltaBytes)}, {addonDeltaPercent:0.##}%)");
            summaryLines.Add($"Reinjected BSP bytes: {FormatBytes(sourceBspBytes)} -> {FormatBytes(outputBspBytes)}");
            summaryLines.Add($"Reinjected pak bytes: {FormatBytes(sourcePakBytes)} -> {FormatBytes(outputPakBytes)}");

            if (!string.IsNullOrWhiteSpace(fatalError))
                summaryLines.Add($"Fatal error: {fatalError}");

            if (root.TryGetProperty("items", out var itemsElement) && itemsElement.ValueKind == JsonValueKind.Array)
            {
                summaryLines.Add(string.Empty);
                summaryLines.Add("Per BSP result:");
                foreach (var item in itemsElement.EnumerateArray())
                {
                    string relativePath = GetJsonString(item, "relative_path") ?? "(unknown)";
                    string status = GetJsonString(item, "status") ?? "unknown";
                    string reason = GetJsonString(item, "reason") ?? string.Empty;
                    bool reinjected = GetJsonBool(item, "reinjected");
                    long sourceBspSize = GetJsonLong(item, "source_bsp_size");
                    long outputBspSize = GetJsonLong(item, "output_bsp_size");
                    long originalPakSize = GetJsonLong(item, "original_pak_size");
                    long rebuiltPakSize = GetJsonLong(item, "rebuilt_pak_size");
                    bool hashMatch = GetJsonBool(item, "pak_hash_match");
                    string rebuiltHash = GetJsonString(item, "rebuilt_pak_sha256") ?? string.Empty;
                    string outputHash = GetJsonString(item, "output_pak_sha256") ?? string.Empty;
                    summaryLines.Add($"- {relativePath} | {status} | Reinjected {YesNo(reinjected)} | Reason: {reason}");
                    if (sourceBspSize > 0 || outputBspSize > 0 || originalPakSize > 0 || rebuiltPakSize > 0)
                        summaryLines.Add($"  BSP {FormatBytes(sourceBspSize)} -> {FormatBytes(outputBspSize)} | PAK {FormatBytes(originalPakSize)} -> {FormatBytes(rebuiltPakSize)}");
                    if (!string.IsNullOrWhiteSpace(rebuiltHash) || !string.IsNullOrWhiteSpace(outputHash))
                        summaryLines.Add($"  Hash match: {YesNo(hashMatch)}");
                }
            }

            return summaryLines;
        }

        private static long GetJsonLong(JsonElement element, string propertyName)
        {
            if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(propertyName, out var value))
                return 0;

            if (value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var number))
                return number;

            if (value.ValueKind == JsonValueKind.String && long.TryParse(value.GetString(), out var parsed))
                return parsed;

            return 0;
        }

        private static double GetJsonDouble(JsonElement element, string propertyName)
        {
            if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(propertyName, out var value))
                return 0.0;

            if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
                return number;

            if (value.ValueKind == JsonValueKind.String && double.TryParse(value.GetString(), out var parsed))
                return parsed;

            return 0.0;
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

        private static string FormatSignedBytes(long bytes)
        {
            string sign = bytes > 0 ? "+" : bytes < 0 ? "-" : "";
            return $"{sign}{FormatBytes(Math.Abs(bytes))}";
        }

        private static string YesNo(bool value) => value ? "Yes" : "No";

        private void Button_MapScanOpenRoot_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(TextBox_MapScanRootPath.Text.Trim(), "Optimize Maps");
        }

        private void Button_MapScanOpenWork_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(_mapScanWorkDir, "Optimize Maps");
        }

        private void Button_MapBuildOpenOutput_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(_mapBuildOutputPath, "Optimize Maps");
        }

        private async void Button_Compress_Click(object sender, RoutedEventArgs e)
        {
            string addonDirectoryPath = _context.AddonDirectoryPath;

            if (Directory.Exists(addonDirectoryPath))
            {
                await RefreshAddonWorkshopWarningAsync(addonDirectoryPath, force: true);
                SaveSettings();
                _ = Task.Run(async () =>
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

            await RefreshAddonWorkshopWarningAsync(addonDirectoryPath, force: true);

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
            _modelsStepIndex = 0;
            _modelsStepTotal = 0;
            _modelsPhase = string.Empty;
            _modelsBatchAddonIndex = 0;
            _modelsBatchAddonTotal = 0;
            _modelsBatchAddonName = string.Empty;
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
                UsePlanar = _context.OptimizerUsePlanar,
                PlanarAngle = _context.OptimizerPlanarAngle,
                ExperimentalGroundPolicy = _context.OptimizerUseExperimentalGroundPolicy,
                ExperimentalRoundPartsPolicy = _context.OptimizerUseExperimentalRoundPartsPolicy,
                ExperimentalSteerTurnBasisFix = _context.OptimizerUseExperimentalSteerTurnBasisFix,
                Format = GetOptimizerFormat(),
                Jobs = _context.OptimizerJobs,
                DecompileJobs = _context.OptimizerDecompileJobs,
                CompileJobs = _context.OptimizerCompileJobs,
                Strict = _context.OptimizerStrict,
                ResumeOpt = _context.OptimizerResumeOpt,
                Overwrite = _context.OptimizerOverwrite,
                OverwriteWork = _context.OptimizerOverwriteWork,
                RestoreSkins = _context.OptimizerRestoreSkins,
                CompileVerbose = _context.OptimizerCompileVerbose,
                CleanupWorkModelArtifacts = _context.OptimizerCleanupWorkModelArtifacts,
                SingleAddonOnly = false
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
                    if (GetModelsBatchSummaryPath() == null)
                    {
                        _context.ModelsSizeReportText = "Size report: computing (after)...";
                        _modelsSizeAfter = await TryScanSizeAsync(_modelsOutputPath, CancellationToken.None);
                    }
                    _context.ModelsSizeReportText = BuildModelsSizeReportText();
                }
                else
                {
                    _context.ModelsSizeReportText = BuildModelsSizeReportText();
                }
            }
            else
            {
                var errorSuffix = string.IsNullOrWhiteSpace(_modelsLastErrorLine) ? string.Empty : $" | {_modelsLastErrorLine}";
                _context.ModelsStatusText = $"FAIL ({exitCode}){errorSuffix}";
                Button_OpenModelsOutput.IsEnabled = !string.IsNullOrWhiteSpace(_modelsOutputPath) && Directory.Exists(_modelsOutputPath);
                Button_OpenModelsWork.IsEnabled = !string.IsNullOrWhiteSpace(_modelsWorkDir) && Directory.Exists(_modelsWorkDir);
                _context.ModelsSizeReportText = BuildModelsSizeReportText();
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

            if (IsModelsBatchFolderInput(addonDirectoryPath))
            {
                MessageBox.Show(
                    "Pipeline currently supports only a single addon root. Use Models directly when selecting a folder of addons.",
                    "Pipeline",
                    MessageBoxButton.OK,
                    MessageBoxImage.Information);
                return;
            }

            await RefreshAddonWorkshopWarningAsync(addonDirectoryPath, force: true);

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
            _modelsStepIndex = 0;
            _modelsStepTotal = 0;
            _modelsPhase = string.Empty;
            _modelsBatchAddonIndex = 0;
            _modelsBatchAddonTotal = 0;
            _modelsBatchAddonName = string.Empty;
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
                    UsePlanar = _context.OptimizerUsePlanar,
                    PlanarAngle = _context.OptimizerPlanarAngle,
                    ExperimentalGroundPolicy = _context.OptimizerUseExperimentalGroundPolicy,
                    ExperimentalRoundPartsPolicy = _context.OptimizerUseExperimentalRoundPartsPolicy,
                    ExperimentalSteerTurnBasisFix = _context.OptimizerUseExperimentalSteerTurnBasisFix,
                    Format = GetOptimizerFormat(),
                    Jobs = _context.OptimizerJobs,
                    DecompileJobs = _context.OptimizerDecompileJobs,
                    CompileJobs = _context.OptimizerCompileJobs,
                    Strict = _context.OptimizerStrict,
                    ResumeOpt = _context.OptimizerResumeOpt,
                    Overwrite = _context.OptimizerOverwrite,
                    OverwriteWork = _context.OptimizerOverwriteWork,
                    RestoreSkins = _context.OptimizerRestoreSkins,
                    CompileVerbose = _context.OptimizerCompileVerbose,
                    CleanupWorkModelArtifacts = _context.OptimizerCleanupWorkModelArtifacts,
                    SingleAddonOnly = true
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

            string compressMode = BuildCompressPipelineOptions().ModeLabel;
            _context.PipelineSummaryText = $"Summary: Models {modelsStatus} | Output: {modelsOutput} | Compress {compressStatus} | Files: {compressCount} | Mode: {compressMode}";
            _context.PipelineSizeReportText = BuildPipelineSizeReportText(BuildCompressPipelineOptions());
        }

        private static string BuildOptimizerItemProgressText(SourceAddonOptimizerProgressUpdate update)
        {
            string itemType = string.IsNullOrWhiteSpace(update.ItemType) ? "Item" : update.ItemType!;
            string prefix = update.IsItemCompletion ? "Completed" : "Running";
            string text = $"{prefix}: {itemType} {update.ItemIndex}/{update.ItemTotal}";

            if (!string.IsNullOrWhiteSpace(update.ItemPath))
            {
                string itemName = Path.GetFileName(update.ItemPath);
                if (!string.IsNullOrWhiteSpace(itemName))
                    text += $" | {itemName}";
            }

            return text;
        }

        private string BuildModelsBatchLabel()
        {
            if (_modelsBatchAddonIndex <= 0 || _modelsBatchAddonTotal <= 0 || string.IsNullOrWhiteSpace(_modelsBatchAddonName))
                return string.Empty;

            return $"Addon {_modelsBatchAddonIndex}/{_modelsBatchAddonTotal}: {_modelsBatchAddonName}";
        }

        private string AppendModelsBatchLabel(string text)
        {
            string batchLabel = BuildModelsBatchLabel();
            if (string.IsNullOrWhiteSpace(batchLabel))
                return text;
            if (string.IsNullOrWhiteSpace(text))
                return batchLabel;
            return $"{text} | {batchLabel}";
        }

        private void OptimizerProgressUpdate(SourceAddonOptimizerProgressUpdate update)
        {
            Dispatcher.Invoke(() =>
            {
                if (!string.IsNullOrWhiteSpace(update.WorkDirPath))
                    _modelsWorkDir = update.WorkDirPath;

                if (update.BatchAddonIndex.HasValue && update.BatchAddonTotal.HasValue)
                {
                    _modelsBatchAddonIndex = update.BatchAddonIndex.Value;
                    _modelsBatchAddonTotal = update.BatchAddonTotal.Value;
                    _modelsBatchAddonName = update.BatchAddonName ?? string.Empty;
                    string batchLabel = BuildModelsBatchLabel();
                    if (!string.IsNullOrWhiteSpace(batchLabel))
                    {
                        _context.ModelsProgressText = batchLabel;
                        if (_pipelineRunning && _pipelineStage == PipelineStage.Models)
                            _context.PipelineProgressText = batchLabel;
                    }
                    return;
                }

                if (update.StepIndex.HasValue)
                {
                    _modelsStepIndex = update.StepIndex.Value;
                    _modelsStepTotal = update.StepTotal ?? 0;
                    _modelsPhase = update.Phase ?? string.Empty;
                    _context.ModelsStatusText = AppendModelsBatchLabel($"Phase: {_modelsPhase} (Step {_modelsStepIndex}/{_modelsStepTotal})");
                    _context.ModelsProgressValue = 0;
                    _context.ModelsProgressMaxValue = 1;

                    if (_pipelineRunning && _pipelineStage == PipelineStage.Models)
                        _context.PipelineStatusText = AppendModelsBatchLabel($"Models Phase: {_modelsPhase} (Step {_modelsStepIndex}/{_modelsStepTotal})");
                }

                if (update.IsPackaging)
                {
                    string phaseText = update.Phase ?? "Packaging";
                    _context.ModelsStatusText = AppendModelsBatchLabel($"Packaging: {phaseText}");
                    _context.ModelsProgressText = AppendModelsBatchLabel($"Current: {phaseText}");

                    if (_pipelineRunning && _pipelineStage == PipelineStage.Models)
                    {
                        _context.PipelineStatusText = AppendModelsBatchLabel($"Models Phase: Packaging - {phaseText}");
                        _context.PipelineProgressText = AppendModelsBatchLabel($"Current: {phaseText}");
                    }
                    return;
                }

                if (update.IsFinalize)
                {
                    string phaseText = update.Phase ?? "Finalizing";
                    _context.ModelsStatusText = AppendModelsBatchLabel($"Finalizing: {phaseText}");
                    _context.ModelsProgressText = AppendModelsBatchLabel($"Current: {phaseText}");

                    if (_pipelineRunning && _pipelineStage == PipelineStage.Models)
                    {
                        _context.PipelineStatusText = AppendModelsBatchLabel($"Models Phase: Finalizing - {phaseText}");
                        _context.PipelineProgressText = AppendModelsBatchLabel($"Current: {phaseText}");
                    }
                    return;
                }

                if (update.ItemIndex.HasValue && update.ItemTotal.HasValue)
                {
                    bool useCompletionDrivenProgress = _modelsStepIndex == 3
                        && string.Equals(update.ItemType, "QC", StringComparison.OrdinalIgnoreCase);
                    string progressText = BuildOptimizerItemProgressText(update);
                    if (!useCompletionDrivenProgress || update.IsItemCompletion)
                    {
                        _context.ModelsProgressMinValue = 0;
                        _context.ModelsProgressMaxValue = update.ItemTotal.Value;
                        _context.ModelsProgressValue = update.ItemIndex.Value;
                    }
                    _context.ModelsProgressText = AppendModelsBatchLabel(progressText);

                    if (_pipelineRunning && _pipelineStage == PipelineStage.Models)
                    {
                        if (!useCompletionDrivenProgress || update.IsItemCompletion)
                        {
                            _context.PipelineProgressMinValue = 0;
                            _context.PipelineProgressMaxValue = update.ItemTotal.Value;
                            _context.PipelineProgressValue = update.ItemIndex.Value;
                        }
                        _context.PipelineProgressText = AppendModelsBatchLabel(progressText);
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

        private string? GetModelsPolicySummaryPath()
        {
            var logsDir = GetModelsLogsDir();
            if (string.IsNullOrWhiteSpace(logsDir))
                return null;
            var summaryPath = Path.Combine(logsDir, "selective_policy_summary.json");
            return File.Exists(summaryPath) ? summaryPath : null;
        }

        private string? GetModelsRoundPartsPolicySummaryPath()
        {
            var logsDir = GetModelsLogsDir();
            if (string.IsNullOrWhiteSpace(logsDir))
                return null;
            var summaryPath = Path.Combine(logsDir, "round_parts_policy_summary.json");
            return File.Exists(summaryPath) ? summaryPath : null;
        }

        private string? GetModelsSteerTurnBasisSummaryPath()
        {
            var logsDir = GetModelsLogsDir();
            if (string.IsNullOrWhiteSpace(logsDir))
                return null;
            var summaryPath = Path.Combine(logsDir, "vehicle_steer_turn_basis_fix_summary.json");
            return File.Exists(summaryPath) ? summaryPath : null;
        }

        private string? GetModelsBatchSummaryPath()
        {
            var logsDir = GetModelsLogsDir();
            if (string.IsNullOrWhiteSpace(logsDir))
                return null;
            var summaryPath = Path.Combine(logsDir, "models_batch_summary.json");
            return File.Exists(summaryPath) ? summaryPath : null;
        }

        private string BuildModelsSizeReportText()
        {
            var batchSummaryPath = GetModelsBatchSummaryPath();
            if (!string.IsNullOrWhiteSpace(batchSummaryPath))
                return BuildModelsBatchSummaryText(batchSummaryPath);

            return AppendSteerTurnBasisSummary(AppendRoundPartsPolicySummary(AppendPolicySummary(BuildSizeReportText(_modelsSizeBefore, _modelsSizeAfter))));
        }

        private string BuildModelsBatchSummaryText(string summaryPath)
        {
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(summaryPath));
                var root = document.RootElement;
                var totals = root.TryGetProperty("totals", out var totalsElement) ? totalsElement : default;
                long beforeBytes = GetJsonLong(totals, "before_bytes");
                long afterBytes = GetJsonLong(totals, "after_bytes");
                long deltaBytes = GetJsonLong(totals, "delta_bytes");
                int totalUnits = (int)GetJsonLong(root, "total_units");
                int okCount = (int)GetJsonLong(root, "ok");
                int failCount = (int)GetJsonLong(root, "fail");
                int skippedCount = 0;
                if (root.TryGetProperty("skipped_without_models", out var skippedElement) && skippedElement.ValueKind == JsonValueKind.Array)
                    skippedCount = skippedElement.GetArrayLength();

                var lines = new List<string>
                {
                    "Batch mode: folder of addons",
                    $"Processed addons: {okCount}/{totalUnits} OK | Failed: {failCount}" +
                    (skippedCount > 0 ? $" | Skipped without models: {skippedCount}" : string.Empty)
                };

                if (beforeBytes > 0 || afterBytes > 0)
                {
                    string deltaText = FormatSignedBytes(deltaBytes);
                    if (beforeBytes > 0)
                    {
                        double deltaPercent = (double)deltaBytes / beforeBytes * 100.0;
                        lines.Add($"Total: {FormatBytes(beforeBytes)} -> {FormatBytes(afterBytes)} ({deltaText}, {deltaPercent:+0.0;-0.0;0.0}%)");
                    }
                    else
                    {
                        lines.Add($"Total: {FormatBytes(beforeBytes)} -> {FormatBytes(afterBytes)} ({deltaText})");
                    }
                }

                if (root.TryGetProperty("results", out var resultsElement) && resultsElement.ValueKind == JsonValueKind.Array)
                {
                    foreach (var item in resultsElement.EnumerateArray())
                    {
                        string name = GetJsonString(item, "name") ?? "(unknown)";
                        string status = (GetJsonString(item, "status") ?? "unknown").ToUpperInvariant();
                        int exitCode = (int)GetJsonLong(item, "exit_code");
                        long itemBeforeBytes = GetJsonLong(item, "before_bytes");
                        long itemAfterBytes = GetJsonLong(item, "after_bytes");
                        long itemDeltaBytes = itemAfterBytes - itemBeforeBytes;
                        string line;
                        if (itemBeforeBytes > 0)
                        {
                            double itemPercent = (double)itemDeltaBytes / itemBeforeBytes * 100.0;
                            line = $"{name}/: {FormatBytes(itemBeforeBytes)} -> {FormatBytes(itemAfterBytes)} ({FormatSignedBytes(itemDeltaBytes)}, {itemPercent:+0.0;-0.0;0.0}%) [{status}]";
                        }
                        else
                        {
                            line = $"{name}/: {FormatBytes(itemBeforeBytes)} -> {FormatBytes(itemAfterBytes)} ({FormatSignedBytes(itemDeltaBytes)}) [{status}]";
                        }

                        if (exitCode != 0)
                            line += $" | exit={exitCode}";

                        lines.Add(line);
                    }
                }

                return string.Join(Environment.NewLine, lines);
            }
            catch
            {
                return "Batch size report unavailable.";
            }
        }

        private string AppendPolicySummary(string baseReport)
        {
            var summaryPath = GetModelsPolicySummaryPath();
            if (string.IsNullOrWhiteSpace(summaryPath))
                return baseReport;

            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(summaryPath));
                var root = document.RootElement;
                var summary = root.TryGetProperty("summary", out var summaryElement) ? summaryElement : default;
                var interpretation = root.TryGetProperty("interpretation", out var interpretationElement) ? interpretationElement : default;
                var counts = summary.ValueKind == JsonValueKind.Object && summary.TryGetProperty("counts", out var countsElement)
                    ? countsElement
                    : default;
                var lines = new List<string>();
                lines.Add("Experimental Models policy:");
                lines.Add($"Mode: {GetJsonString(root, "mode") ?? "experimental_ground_policy"}");
                lines.Add($"Addon shape: {GetJsonString(interpretation, "label") ?? "unknown"}");
                lines.Add($"Reason: {GetJsonString(interpretation, "why") ?? "n/a"}");

                foreach (var groupName in new[]
                {
                    "experimental_ground_main",
                    "baseline_aircraft",
                    "baseline_wheel",
                    "baseline_rotor",
                    "baseline_attachment",
                    "baseline_detached",
                    "baseline_uncertain_main",
                    "baseline_small_unknown",
                    "baseline_other",
                })
                {
                    int count = GetJsonInt(counts, groupName);
                    if (count > 0)
                        lines.Add($"{groupName}: {count}");
                }

                int classified = GetJsonInt(summary, "classified_entry_count");
                int skipped = GetJsonInt(summary, "skipped_entry_count");
                if (classified > 0 || skipped > 0)
                    lines.Add($"Classified models: {classified} | skipped: {skipped}");

                return string.IsNullOrWhiteSpace(baseReport)
                    ? string.Join(Environment.NewLine, lines)
                    : $"{baseReport}{Environment.NewLine}{Environment.NewLine}{string.Join(Environment.NewLine, lines)}";
            }
            catch
            {
                return baseReport;
            }
        }

        private string AppendSteerTurnBasisSummary(string baseReport)
        {
            var summaryPath = GetModelsSteerTurnBasisSummaryPath();
            if (string.IsNullOrWhiteSpace(summaryPath))
                return baseReport;

            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(summaryPath));
                var root = document.RootElement;
                var summary = root.TryGetProperty("summary", out var summaryElement) ? summaryElement : default;
                var lines = new List<string>();
                lines.Add("Experimental steer turn-basis fix:");
                lines.Add($"Mode: {GetJsonString(root, "mode") ?? "experimental_vehicle_steer_turn_basis_fix"}");
                lines.Add($"Detected QCs: {GetJsonInt(summary, "detected_qc_count")}");
                lines.Add($"Patched turn files: {GetJsonInt(summary, "patched_turn_file_count")}");

                return string.IsNullOrWhiteSpace(baseReport)
                    ? string.Join(Environment.NewLine, lines)
                    : $"{baseReport}{Environment.NewLine}{Environment.NewLine}{string.Join(Environment.NewLine, lines)}";
            }
            catch
            {
                return baseReport;
            }
        }

        private string AppendRoundPartsPolicySummary(string baseReport)
        {
            var summaryPath = GetModelsRoundPartsPolicySummaryPath();
            if (string.IsNullOrWhiteSpace(summaryPath))
                return baseReport;

            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(summaryPath));
                var root = document.RootElement;
                var summary = root.TryGetProperty("summary", out var summaryElement) ? summaryElement : default;
                var lines = new List<string>();
                lines.Add("Experimental round-parts policy:");
                lines.Add($"Mode: {GetJsonString(root, "mode") ?? "experimental_round_parts_policy"}");
                lines.Add($"Wheel variant: {GetJsonString(root, "wheel_variant") ?? "silhouette_floor_20"}");
                lines.Add($"Embedded variant: {GetJsonString(root, "embedded_variant") ?? "floor_24"}");

                int wheelModels = GetJsonInt(summary, "wheel_models");
                int wheelRoundObjects = GetJsonInt(summary, "wheel_round_objects");
                int wheelAdaptiveFloorHits = GetJsonInt(summary, "wheel_adaptive_floor_hits");
                if (wheelModels > 0 || wheelRoundObjects > 0 || wheelAdaptiveFloorHits > 0)
                    lines.Add($"Wheel models: {wheelModels} | round objects: {wheelRoundObjects} | floor hits: {wheelAdaptiveFloorHits}");

                int embeddedCandidateObjects = GetJsonInt(summary, "embedded_candidate_objects");
                int embeddedQualifiedRoundParts = GetJsonInt(summary, "embedded_qualified_round_parts");
                int embeddedRejectedFaces = GetJsonInt(summary, "embedded_rejected_candidate_faces");
                int embeddedAdaptiveFloorHits = GetJsonInt(summary, "embedded_adaptive_floor_hits");
                if (embeddedCandidateObjects > 0 || embeddedQualifiedRoundParts > 0 || embeddedRejectedFaces > 0 || embeddedAdaptiveFloorHits > 0)
                {
                    lines.Add(
                        $"Embedded candidates: {embeddedCandidateObjects} | qualified round parts: {embeddedQualifiedRoundParts} | rejected faces: {embeddedRejectedFaces} | floor hits: {embeddedAdaptiveFloorHits}"
                    );
                }

                int rigidFixVertices = GetJsonInt(summary, "rigid_primary_bone_fix_vertices");
                if (rigidFixVertices > 0)
                    lines.Add($"Rigid primary-bone fix vertices: {rigidFixVertices}");

                return string.IsNullOrWhiteSpace(baseReport)
                    ? string.Join(Environment.NewLine, lines)
                    : $"{baseReport}{Environment.NewLine}{Environment.NewLine}{string.Join(Environment.NewLine, lines)}";
            }
            catch
            {
                return baseReport;
            }
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
                string toolsDir = ToolPaths.ToolsRoot;
                string message = $"{ex.Message}\n\nTools folder:\n{toolsDir}\n\nOpen tools folder?";
                var result = MessageBox.Show(message, title, MessageBoxButton.YesNo, MessageBoxImage.Error);
                if (result == MessageBoxResult.Yes)
                    OpenFolder(toolsDir, title);
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
            TextBox_UnpackRootPath.Text = _settings.UnpackRootPath ?? string.Empty;
            TextBox_MapScanRootPath.Text = _settings.MapOptimizeRootPath ?? string.Empty;
            TextBox_UnpackGmadPath.Text = _settings.GmadPath ?? string.Empty;
            _context.BlenderPath = _settings.BlenderPath ?? string.Empty;
            _context.StudioMdlPath = _settings.StudioMdlPath ?? string.Empty;
            CheckBox_UnpackOpenOnFinish.IsChecked = _settings.UnpackOpenOnFinish ?? false;
            CheckBox_UnpackExtractMapPak.IsChecked = _settings.UnpackExtractMapPak ?? true;
            CheckBox_UnpackDeleteMapBsp.IsChecked = _settings.UnpackDeleteMapBsp ?? false;
            SetSelectedUnpackExistingMode(_settings.UnpackExistingMode);
            _context.OptimizerSuffix = string.IsNullOrWhiteSpace(_settings.OptimizerSuffix) ? "_optimized" : _settings.OptimizerSuffix;
            _context.OptimizerPresetIndex = PresetIndexFromName(_settings.OptimizerPreset);
            if (_settings.OptimizerUsePlanar.HasValue)
                _context.OptimizerUsePlanar = _settings.OptimizerUsePlanar.Value;
            if (_settings.OptimizerPlanarAngle.HasValue)
                _context.OptimizerPlanarAngle = _settings.OptimizerPlanarAngle.Value;
            if (_settings.OptimizerUseExperimentalGroundPolicy.HasValue)
                _context.OptimizerUseExperimentalGroundPolicy = _settings.OptimizerUseExperimentalGroundPolicy.Value;
            if (_settings.OptimizerUseExperimentalRoundPartsPolicy.HasValue)
                _context.OptimizerUseExperimentalRoundPartsPolicy = _settings.OptimizerUseExperimentalRoundPartsPolicy.Value;
            if (_settings.OptimizerUseExperimentalSteerTurnBasisFix.HasValue)
                _context.OptimizerUseExperimentalSteerTurnBasisFix = _settings.OptimizerUseExperimentalSteerTurnBasisFix.Value;
            if (_settings.OptimizerRestoreSkins.HasValue)
                _context.OptimizerRestoreSkins = _settings.OptimizerRestoreSkins.Value;
            if (_settings.OptimizerCompileVerbose.HasValue)
                _context.OptimizerCompileVerbose = _settings.OptimizerCompileVerbose.Value;
            if (_settings.OptimizerCleanupWorkModelArtifacts.HasValue)
                _context.OptimizerCleanupWorkModelArtifacts = _settings.OptimizerCleanupWorkModelArtifacts.Value;
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
            if (_settings.CompressModeIndex.HasValue)
            {
                int index = _settings.CompressModeIndex.Value;
                if (index >= 0 && index < _context.CompressModeList.Length)
                    _context.CompressModeIndex = index;
            }
            if (_settings.CompressMagickUseCommonVtf.HasValue)
                _context.CompressMagickUseCommonVtf = _settings.CompressMagickUseCommonVtf.Value;
            if (_settings.CompressMagickUseAggressivePng.HasValue)
                _context.CompressMagickUseAggressivePng = _settings.CompressMagickUseAggressivePng.Value;

            if (_context.OptimizerPresetIndex == PresetCustomIndex)
            {
                ApplyCustomParams(_settings.OptimizerCustom);
            }
            else
            {
                ApplyPresetValues(_context.OptimizerPresetIndex);
            }

            ResetUnpackSummary();
            UpdateUnpackActionStates();
            LoadAddonMergeSettings();
            ResetMapScanSummary();
            UpdateMapScanActionStates();
            _ = RefreshAddonWorkshopWarningAsync(_context.AddonDirectoryPath, force: true);
        }

        private void SaveSettings()
        {
            var settings = new AppSettingsModel
            {
                LastAddonPath = _context.AddonDirectoryPath,
                UnpackRootPath = TextBox_UnpackRootPath.Text.Trim(),
                MapOptimizeRootPath = TextBox_MapScanRootPath.Text.Trim(),
                GmadPath = TextBox_UnpackGmadPath.Text.Trim(),
                BlenderPath = _context.BlenderPath,
                StudioMdlPath = _context.StudioMdlPath,
                UnpackExistingMode = GetSelectedUnpackExistingMode(),
                UnpackOpenOnFinish = CheckBox_UnpackOpenOnFinish.IsChecked == true,
                UnpackExtractMapPak = CheckBox_UnpackExtractMapPak.IsChecked == true,
                UnpackDeleteMapBsp = CheckBox_UnpackDeleteMapBsp.IsChecked == true,
                OptimizerSuffix = _context.OptimizerSuffix,
                OptimizerPreset = PresetNameFromIndex(_context.OptimizerPresetIndex),
                OptimizerUsePlanar = _context.OptimizerUsePlanar,
                OptimizerPlanarAngle = _context.OptimizerPlanarAngle,
                OptimizerUseExperimentalGroundPolicy = _context.OptimizerUseExperimentalGroundPolicy,
                OptimizerUseExperimentalRoundPartsPolicy = _context.OptimizerUseExperimentalRoundPartsPolicy,
                OptimizerUseExperimentalSteerTurnBasisFix = _context.OptimizerUseExperimentalSteerTurnBasisFix,
                OptimizerRestoreSkins = _context.OptimizerRestoreSkins,
                OptimizerCompileVerbose = _context.OptimizerCompileVerbose,
                OptimizerCleanupWorkModelArtifacts = _context.OptimizerCleanupWorkModelArtifacts,
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
                AudioOggQualityIndex = _context.OggQualityIndex,
                CompressModeIndex = _context.CompressModeIndex,
                CompressMagickUseCommonVtf = _context.CompressMagickUseCommonVtf,
                CompressMagickUseAggressivePng = _context.CompressMagickUseAggressivePng
            };
            SaveAddonMergeSettings(settings);

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
                string ffmpegRoot = ToolExtractionSystem.EnsureExtracted(
                    "ffmpeg",
                    "2022-09-22",
                    global::GmodAddonCompressor.Properties.Resources.ffmpeg,
                    new[] { "ffmpeg.exe" }
                );
                string ffmpegExePath = Path.Combine(ffmpegRoot, "ffmpeg.exe");

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

            if (_context.OptimizerRatio < 0.01 || _context.OptimizerRatio > 1.0)
                errors.Add("Ratio must be between 0.01 and 1.00.");

            if (_context.OptimizerAutoSmooth < 0 || _context.OptimizerAutoSmooth > 180)
                errors.Add("AutoSmooth must be between 0 and 180.");

            if (_context.OptimizerUsePlanar && (_context.OptimizerPlanarAngle < 0 || _context.OptimizerPlanarAngle > 180))
                errors.Add("Planar Angle must be between 0 and 180 when Use Planar is enabled.");

            string outputWriteProbePath = GetModelsOutputWriteProbePath(addonDirectoryPath);
            if (string.IsNullOrWhiteSpace(outputWriteProbePath) || !CanWriteToDirectory(outputWriteProbePath))
                errors.Add("No write permission in the folder where Models output will be created.");

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

        private static bool LooksLikeAddonRoot(string directoryPath)
        {
            if (string.IsNullOrWhiteSpace(directoryPath) || !Directory.Exists(directoryPath))
                return false;

            foreach (var markerFile in ModelsAddonMarkerFiles)
            {
                if (File.Exists(Path.Combine(directoryPath, markerFile)))
                    return true;
            }

            foreach (var markerDirectory in ModelsAddonMarkerDirectories)
            {
                if (Directory.Exists(Path.Combine(directoryPath, markerDirectory)))
                    return true;
            }

            return false;
        }

        private static bool IsModelsBatchFolderInput(string directoryPath)
        {
            if (LooksLikeAddonRoot(directoryPath))
                return false;

            try
            {
                foreach (var childDirectory in Directory.EnumerateDirectories(directoryPath))
                {
                    if (!LooksLikeAddonRoot(childDirectory))
                        continue;

                    if (Directory.Exists(Path.Combine(childDirectory, "models")))
                        return true;
                }
            }
            catch
            {
                return false;
            }

            return false;
        }

        private static string GetModelsOutputWriteProbePath(string addonDirectoryPath)
        {
            if (IsModelsBatchFolderInput(addonDirectoryPath))
                return addonDirectoryPath;

            var parentDir = Directory.GetParent(addonDirectoryPath);
            return parentDir?.FullName ?? addonDirectoryPath;
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

        private static string? DetectGmadPath()
        {
            var envCandidates = new[]
            {
                Environment.GetEnvironmentVariable("GMAD_EXE"),
                Environment.GetEnvironmentVariable("GMAD_PATH"),
                Environment.GetEnvironmentVariable("GARRYSMOD_DIR")
            };

            foreach (var candidate in envCandidates)
            {
                var resolved = ResolveGmadCandidate(candidate);
                if (!string.IsNullOrWhiteSpace(resolved))
                    return resolved;
            }

            var fromPath = FindOnPath("gmad.exe");
            if (!string.IsNullOrWhiteSpace(fromPath))
                return fromPath;

            var directCandidates = new[]
            {
                @"C:\Program Files (x86)\Steam\steamapps\common\GarrysMod\bin\gmad.exe",
                @"C:\Program Files\Steam\steamapps\common\GarrysMod\bin\gmad.exe",
            };

            var direct = directCandidates.FirstOrDefault(File.Exists);
            if (!string.IsNullOrWhiteSpace(direct))
                return direct;

            foreach (var steamRoot in GetSteamRootCandidates())
            {
                string? resolved = ResolveGmadCandidate(Path.Combine(steamRoot, "steamapps", "common", "GarrysMod"));
                if (!string.IsNullOrWhiteSpace(resolved))
                    return resolved;

                string libraryVdf = Path.Combine(steamRoot, "steamapps", "libraryfolders.vdf");
                if (!File.Exists(libraryVdf))
                    continue;

                foreach (var libraryPath in ParseSteamLibraryFolders(libraryVdf))
                {
                    resolved = ResolveGmadCandidate(Path.Combine(libraryPath, "steamapps", "common", "GarrysMod"));
                    if (!string.IsNullOrWhiteSpace(resolved))
                        return resolved;
                }
            }

            return null;
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

        private static IEnumerable<string> GetSteamRootCandidates()
        {
            var candidates = new[]
            {
                @"C:\Program Files (x86)\Steam",
                @"C:\Program Files\Steam",
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Steam"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Steam")
            };

            return candidates
                .Where(path => !string.IsNullOrWhiteSpace(path) && Directory.Exists(path))
                .Distinct(StringComparer.OrdinalIgnoreCase);
        }

        private static IEnumerable<string> ParseSteamLibraryFolders(string vdfPath)
        {
            var results = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (var rawLine in File.ReadLines(vdfPath))
            {
                string line = rawLine.Trim();
                if (string.IsNullOrWhiteSpace(line))
                    continue;

                if (line.IndexOf("\"path\"", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    var parts = line.Split('"', StringSplitOptions.RemoveEmptyEntries);
                    if (parts.Length >= 2)
                    {
                        string candidate = parts[^1].Replace(@"\\", @"\");
                        if (Directory.Exists(candidate))
                            results.Add(candidate);
                    }
                    continue;
                }

                if (line.StartsWith("\"") && line.Count(ch => ch == '"') >= 4)
                {
                    var parts = line.Split('"', StringSplitOptions.RemoveEmptyEntries);
                    if (parts.Length >= 2 && parts[0].All(char.IsDigit))
                    {
                        string candidate = parts[1].Replace(@"\\", @"\");
                        if (Directory.Exists(candidate))
                            results.Add(candidate);
                    }
                }
            }

            return results;
        }

        private static string? ResolveGmadCandidate(string? rawPath)
        {
            if (string.IsNullOrWhiteSpace(rawPath))
                return null;

            if (File.Exists(rawPath) && string.Equals(Path.GetFileName(rawPath), "gmad.exe", StringComparison.OrdinalIgnoreCase))
                return rawPath;

            if (Directory.Exists(rawPath))
            {
                string candidate = Path.Combine(rawPath, "bin", "gmad.exe");
                if (File.Exists(candidate))
                    return candidate;
            }

            return null;
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

            CompressPipelineOptions compressOptions = BuildCompressPipelineOptions();
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
            ImageContext.ImageMagickVTFCompress = compressOptions.UseLegacyStandardVtfDemo;
            LuaContext.ChangeOriginalCodeToMinimalistic = _context.ChangeOriginalCodeToMinimalistic;

            bool isPipelineCompress = _pipelineRunning && _pipelineStage == PipelineStage.Compress;
            var sizeToken = isPipelineCompress ? _pipelineCts?.Token ?? CancellationToken.None : CancellationToken.None;
            if (isPipelineCompress)
            {
                _pipelineCompressSizeAfter = null;
                _context.PipelineSizeReportText =
                    $"Compress mode: {compressOptions.ModeLabel}{Environment.NewLine}" +
                    $"{compressOptions.BuildRoutingSummary()}{Environment.NewLine}{Environment.NewLine}" +
                    "Size report: computing (before compress)...";
                _pipelineCompressSizeBefore = await TryScanSizeAsync(addonDirectoryPath, sizeToken);
            }
            else
            {
                _compressSizeAfter = null;
                _context.CompressSizeReportText =
                    $"Compress mode: {compressOptions.ModeLabel}{Environment.NewLine}" +
                    $"{compressOptions.BuildRoutingSummary()}{Environment.NewLine}{Environment.NewLine}" +
                    "Size report: computing (before)...";
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

            var compressSystem = new CompressAddonSystem(addonDirectoryPath, pipelineOptions: compressOptions);

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
                    _context.PipelineSizeReportText = BuildPipelineSizeReportText(compressOptions);
                }
                else
                {
                    _context.CompressSizeReportText = "Size report: computing (after)...";
                    _compressSizeAfter = await TryScanSizeAsync(addonDirectoryPath, CancellationToken.None);
                    _context.CompressSizeReportText = BuildCompressSizeReportText(compressOptions, _compressSizeBefore, _compressSizeAfter);
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

        private static string BuildCompressSizeReportText(CompressPipelineOptions options, DirectorySizeSnapshot? before, DirectorySizeSnapshot? after)
        {
            return
                $"Compress mode: {options.ModeLabel}{Environment.NewLine}" +
                $"{options.BuildRoutingSummary()}{Environment.NewLine}{Environment.NewLine}" +
                DirectorySizeReportFormatter.BuildReport(before, after);
        }

        private CompressPipelineOptions BuildCompressPipelineOptions()
        {
            bool isMagickMode = _context.CompressModeIsMagick;

            return new CompressPipelineOptions
            {
                Mode = isMagickMode ? CompressPipelineMode.Magick : CompressPipelineMode.Standard,
                UseLegacyStandardVtfDemo = !isMagickMode && _context.ImageMagickVTFCompress,
                UseMagickForCommonVtf = isMagickMode && _context.CompressMagickUseCommonVtf,
                UseMagickForAggressivePng = isMagickMode && _context.CompressMagickUseAggressivePng
            };
        }

        private string BuildPipelineSizeReportText(CompressPipelineOptions? compressOptions = null)
        {
            var sb = new StringBuilder();

            if (_pipelineModelsSizeBefore != null || _pipelineModelsSizeAfter != null)
            {
                sb.AppendLine("Models size report:");
                sb.AppendLine(AppendSteerTurnBasisSummary(AppendRoundPartsPolicySummary(AppendPolicySummary(BuildSizeReportText(_pipelineModelsSizeBefore, _pipelineModelsSizeAfter)))));
            }

            if (_pipelineCompressSizeBefore != null || _pipelineCompressSizeAfter != null)
            {
                if (sb.Length > 0)
                    sb.AppendLine().AppendLine();
                sb.AppendLine("Compress size report:");
                if (compressOptions == null)
                    sb.AppendLine(BuildSizeReportText(_pipelineCompressSizeBefore, _pipelineCompressSizeAfter));
                else
                    sb.AppendLine(BuildCompressSizeReportText(compressOptions, _pipelineCompressSizeBefore, _pipelineCompressSizeAfter));
            }

            return sb.ToString().TrimEnd();
        }

        private static async Task<DirectorySizeSnapshot?> TryScanSizeAsync(string rootPath, CancellationToken token)
        {
            if (string.IsNullOrWhiteSpace(rootPath) || !Directory.Exists(rootPath))
                return null;

            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(token);
            timeoutCts.CancelAfter(SizeScanTimeout);

            try
            {
                return await DirectorySizeScanner.ScanAsync(rootPath, timeoutCts.Token);
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
