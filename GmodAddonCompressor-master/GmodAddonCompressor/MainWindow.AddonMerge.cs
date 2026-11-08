using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Merge;
using GmodAddonCompressor.Systems.Tools;
using Microsoft.Win32;
using Ookii.Dialogs.Wpf;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace GmodAddonCompressor
{
    public partial class MainWindow
    {
        private readonly AddonMergeRunner _addonMergeRunner = new AddonMergeRunner();
        private CancellationTokenSource? _addonMergeCts = null;
        private bool _addonMergeRunning = false;
        private bool _addonMergeScanReady = false;
        private string? _addonMergeSummaryPath = null;
        private string? _addonMergeScanReportPath = null;
        private string? _addonMergeWorkDir = null;
        private string? _addonMergeMergedRoot = null;
        private string? _addonMergeGmaPath = null;
        private string? _addonMergeLastScanFingerprint = null;
        private string _addonMergeSuggestedOutputRoot = string.Empty;
        private string _addonMergeSuggestedBundleName = "merged_addon";
        private bool _addonMergeActiveScanOnly = false;

        private void InitializeAddonMerge()
        {
            _addonMergeRunner.ProgressUpdate += AddonMergeProgressChanged;
            _addonMergeRunner.SummaryPathFound += path => _addonMergeSummaryPath = path;
            _addonMergeRunner.WorkDirFound += path => _addonMergeWorkDir = path;
            _addonMergeRunner.MergedRootFound += path => _addonMergeMergedRoot = path;
            _addonMergeRunner.GmaPathFound += path => _addonMergeGmaPath = path;
            _addonMergeRunner.LogLine += line => Dispatcher.Invoke(() => AppendAddonMergeLog(line));
        }

        private void Button_SelectAddonMergeRoot_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new VistaFolderBrowserDialog();
            if (dialog.ShowDialog(this).GetValueOrDefault())
                TextBox_AddonMergeRootPath.Text = dialog.SelectedPath;
        }

        private void Button_SelectAddonMergeOutputRoot_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new VistaFolderBrowserDialog();
            if (dialog.ShowDialog(this).GetValueOrDefault())
                TextBox_AddonMergeOutputRoot.Text = dialog.SelectedPath;
        }

        private void Button_AddonMergeUseRootOutput_Click(object sender, RoutedEventArgs e)
        {
            TextBox_AddonMergeOutputRoot.Text = TextBox_AddonMergeRootPath.Text.Trim();
        }

        private void Button_AddonMergeBrowseGmad_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Escolha o gmad.exe",
                Filter = "gmad.exe|gmad.exe|Executável|*.exe|Todos os arquivos|*.*",
                CheckFileExists = true,
            };

            if (dialog.ShowDialog(this).GetValueOrDefault())
                TextBox_AddonMergeGmadPath.Text = dialog.FileName;
        }

        private void Button_AddonMergeAutoDetectGmad_Click(object sender, RoutedEventArgs e)
        {
            var detected = DetectGmadPath();
            if (!string.IsNullOrWhiteSpace(detected))
            {
                TextBox_AddonMergeGmadPath.Text = detected;
                return;
            }

            MessageBox.Show("gmad.exe não encontrado. Escolha o gmad.exe da pasta bin do Garry's Mod.", "Juntar addons", MessageBoxButton.OK, MessageBoxImage.Warning);
        }

        private void TextBox_AddonMergeRootPath_TextChanged(object sender, TextChangedEventArgs e)
        {
            UpdateAddonMergeDefaultsFromRoot();
            if (!_addonMergeRunning)
                ResetAddonMergeSummary();
            UpdateAddonMergeActionStates();
        }

        private void TextBox_AddonMergeBundleName_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (!_addonMergeRunning)
                ResetAddonMergeSummary();
            UpdateAddonMergeActionStates();
        }

        private void TextBox_AddonMergeOutputRoot_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (!_addonMergeRunning)
                ResetAddonMergeSummary();
            UpdateAddonMergeActionStates();
        }

        private void TextBox_AddonMergeGmadPath_TextChanged(object sender, TextChangedEventArgs e)
        {
            UpdateAddonMergeActionStates();
        }

        private void ComboBox_AddonMergePackageMode_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (!_addonMergeRunning)
                ResetAddonMergeSummary();
            UpdateAddonMergeActionStates();
        }

        private void CheckBox_AddonMergeGenerateGma_Changed(object sender, RoutedEventArgs e)
        {
            if (!_addonMergeRunning)
                ResetAddonMergeSummary();
            UpdateAddonMergeActionStates();
        }

        private void ResetAddonMergeSummary()
        {
            _addonMergeScanReady = false;
            _addonMergeSummaryPath = null;
            _addonMergeScanReportPath = null;
            _addonMergeLastScanFingerprint = null;
            _addonMergeActiveScanOnly = false;
            if (!_addonMergeRunning)
            {
                _addonMergeWorkDir = null;
                _addonMergeMergedRoot = null;
                _addonMergeGmaPath = null;
            }

            TextBox_AddonMergeSummary.Text = "Nenhuma busca ainda.";
            TextBox_AddonMergeDiagnostics.Text = "Sem diagnóstico ainda.";
            TextBlock_AddonMergeAddonCount.Text = "0";
            TextBlock_AddonMergeFileCount.Text = "0";
            TextBlock_AddonMergeUniquePathCount.Text = "0";
            TextBlock_AddonMergeConflictCount.Text = "0";
            TextBlock_AddonMergeDiscardedCount.Text = "0";
            TextBlock_AddonMergeMergedFileCount.Text = "0";
            TextBlock_AddonMergeMergeStatus.Text = "—";
            TextBlock_AddonMergeValidationStatus.Text = "—";
            ProgressBar_AddonMerge.IsIndeterminate = false;
            ProgressBar_AddonMerge.Minimum = 0;
            ProgressBar_AddonMerge.Maximum = 1000;
            ProgressBar_AddonMerge.Value = 0;
            TextBlock_AddonMergePhase.Text = "—";
            TextBlock_AddonMergeCurrent.Text = "—";
            TextBlock_AddonMergeProgressPercent.Text = "0%";
            TextBlock_AddonMergeProgressDetail.Text = "Procure os addons para começar.";
            TextBlock_AddonMergeStatus.Text = "Aguardando";
        }

        private void UpdateAddonMergeActionStates()
        {
            bool hasRoot = Directory.Exists(TextBox_AddonMergeRootPath.Text.Trim());
            bool hasBundleName = ValidateAddonMergeBundleName(GetAddonMergeBundleName(), out _);
            bool hasOutputRoot = ValidateAddonMergeOutputRoot(GetAddonMergeOutputRootPath(), createIfMissing: false, out _);
            bool packagingEnabled = IsAddonMergePackagingEnabled();
            bool needsGmad = GetSelectedAddonMergePackageMode() != "local-only";
            bool hasGmad = File.Exists(TextBox_AddonMergeGmadPath.Text.Trim());
            bool hasWork = !string.IsNullOrWhiteSpace(_addonMergeWorkDir) && Directory.Exists(_addonMergeWorkDir);
            bool hasMergedRoot = !string.IsNullOrWhiteSpace(_addonMergeMergedRoot) && Directory.Exists(_addonMergeMergedRoot);
            bool hasReports = hasWork && Directory.Exists(Path.Combine(_addonMergeWorkDir!, "reports"));

            Button_AddonMergeScan.IsEnabled = !_addonMergeRunning && hasRoot;
            Button_AddonMergeRun.IsEnabled = !_addonMergeRunning && hasRoot && hasBundleName && hasOutputRoot && _addonMergeScanReady && (!needsGmad || hasGmad);
            Button_AddonMergeCancel.IsEnabled = _addonMergeRunning;
            Button_AddonMergeOpenRoot.IsEnabled = !_addonMergeRunning && hasRoot;
            Button_AddonMergeOpenMerged.IsEnabled = !_addonMergeRunning && hasMergedRoot;
            Button_AddonMergeOpenWork.IsEnabled = !_addonMergeRunning && hasWork;
            Button_AddonMergeOpenReports.IsEnabled = !_addonMergeRunning && hasReports;
            CheckBox_AddonMergeGenerateGma.IsEnabled = !_addonMergeRunning;
            ComboBox_AddonMergePackageMode.IsEnabled = !_addonMergeRunning && packagingEnabled;
            TextBox_AddonMergeGmadPath.IsEnabled = !_addonMergeRunning && packagingEnabled;
            Button_AddonMergeAutoDetectGmad.IsEnabled = !_addonMergeRunning && packagingEnabled;
            Button_AddonMergeBrowseGmad.IsEnabled = !_addonMergeRunning && packagingEnabled;
        }

        private void UpdateAddonMergeDefaultsFromRoot(bool force = false)
        {
            string rootPath = TextBox_AddonMergeRootPath.Text.Trim();
            string suggestedOutput = rootPath;
            string suggestedName = SuggestAddonMergeBundleName(rootPath);

            if (force || string.IsNullOrWhiteSpace(TextBox_AddonMergeOutputRoot.Text) || ArePathsEquivalent(TextBox_AddonMergeOutputRoot.Text, _addonMergeSuggestedOutputRoot))
                TextBox_AddonMergeOutputRoot.Text = suggestedOutput;

            if (force || string.IsNullOrWhiteSpace(TextBox_AddonMergeBundleName.Text) || string.Equals(TextBox_AddonMergeBundleName.Text.Trim(), _addonMergeSuggestedBundleName, StringComparison.OrdinalIgnoreCase))
                TextBox_AddonMergeBundleName.Text = suggestedName;

            _addonMergeSuggestedOutputRoot = suggestedOutput;
            _addonMergeSuggestedBundleName = suggestedName;
        }

        private static string SuggestAddonMergeBundleName(string rootPath)
        {
            if (string.IsNullOrWhiteSpace(rootPath))
                return "merged_addon";

            string name = Path.GetFileName(rootPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
            if (string.IsNullOrWhiteSpace(name))
                return "merged_addon";

            return $"{name}_merged";
        }

        private static bool ArePathsEquivalent(string? left, string? right)
        {
            if (string.IsNullOrWhiteSpace(left) || string.IsNullOrWhiteSpace(right))
                return false;

            try
            {
                return string.Equals(
                    Path.GetFullPath(left.Trim()),
                    Path.GetFullPath(right.Trim()),
                    StringComparison.OrdinalIgnoreCase);
            }
            catch
            {
                return string.Equals(left.Trim(), right.Trim(), StringComparison.OrdinalIgnoreCase);
            }
        }

        private string GetAddonMergeBundleName()
        {
            return TextBox_AddonMergeBundleName.Text.Trim();
        }

        private string GetAddonMergeOutputRootPath()
        {
            string value = TextBox_AddonMergeOutputRoot.Text.Trim();
            return string.IsNullOrWhiteSpace(value) ? TextBox_AddonMergeRootPath.Text.Trim() : value;
        }

        private string GetAddonMergeTargetFolderPath()
        {
            return Path.Combine(GetAddonMergeOutputRootPath(), GetAddonMergeBundleName());
        }

        private string GetAddonMergeTargetGmaPath()
        {
            return Path.Combine(GetAddonMergeOutputRootPath(), $"{GetAddonMergeBundleName()}.gma");
        }

        private bool IsAddonMergePackagingEnabled()
        {
            return CheckBox_AddonMergeGenerateGma.IsChecked == true;
        }

        private string BuildAddonMergeScanFingerprint()
        {
            return string.Join("|",
                TextBox_AddonMergeRootPath.Text.Trim(),
                GetAddonMergeOutputRootPath(),
                GetAddonMergeBundleName(),
                GetSelectedAddonMergePackageMode()).ToLowerInvariant();
        }

        private string? GetReusableAddonMergeScanReportPath()
        {
            if (!_addonMergeScanReady || string.IsNullOrWhiteSpace(_addonMergeScanReportPath) || !File.Exists(_addonMergeScanReportPath))
                return null;

            return string.Equals(_addonMergeLastScanFingerprint, BuildAddonMergeScanFingerprint(), StringComparison.OrdinalIgnoreCase)
                ? _addonMergeScanReportPath
                : null;
        }

        private static bool ValidateAddonMergeBundleName(string bundleName, out string error)
        {
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(bundleName))
            {
                error = "Informe o nome do novo addon.";
                return false;
            }

            if (bundleName.EndsWith(' ') || bundleName.EndsWith('.'))
            {
                error = "O nome do novo addon não pode terminar com espaço ou ponto.";
                return false;
            }

            if (bundleName.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
            {
                error = "O nome do novo addon tem caracteres inválidos para pasta.";
                return false;
            }

            return true;
        }

        private static bool ValidateAddonMergeOutputRoot(string outputRoot, bool createIfMissing, out string error)
        {
            error = string.Empty;

            if (string.IsNullOrWhiteSpace(outputRoot))
            {
                error = "Escolha onde salvar.";
                return false;
            }

            try
            {
                string fullPath = Path.GetFullPath(outputRoot);
                if (File.Exists(fullPath))
                {
                    error = "O destino aponta para um arquivo.";
                    return false;
                }

                if (createIfMissing)
                    Directory.CreateDirectory(fullPath);

                return true;
            }
            catch (Exception ex)
            {
                error = $"Destino inválido. {ex.Message}";
                return false;
            }
        }

        private bool ValidateAddonMergeInputs(bool scanOnly, out string errorMessage)
        {
            var errors = new List<string>();
            string rootPath = TextBox_AddonMergeRootPath.Text.Trim();
            if (!Directory.Exists(rootPath))
                errors.Add("Pasta não encontrada.");
            if (!ValidateAddonMergeBundleName(GetAddonMergeBundleName(), out string bundleError))
                errors.Add(bundleError);
            if (!ValidateAddonMergeOutputRoot(GetAddonMergeOutputRootPath(), createIfMissing: !scanOnly, out string outputError))
                errors.Add(outputError);
            if (!CanWriteToDirectory(ToolPaths.WorkRoot))
                errors.Add("Sem permissão de escrita na pasta de trabalho.");

            bool needsGmad = !scanOnly && GetSelectedAddonMergePackageMode() != "local-only";
            if (needsGmad)
            {
                string gmadPath = TextBox_AddonMergeGmadPath.Text.Trim();
                if (string.IsNullOrWhiteSpace(gmadPath) || !File.Exists(gmadPath))
                    errors.Add("Caminho do gmad.exe inválido.");
            }

            if (errors.Count == 0)
            {
                string targetFolder = GetAddonMergeTargetFolderPath();
                if (string.Equals(Path.GetFullPath(rootPath), Path.GetFullPath(targetFolder), StringComparison.OrdinalIgnoreCase))
                    errors.Add("A pasta de saída não pode ser a própria pasta de origem.");
            }

            errorMessage = string.Join(Environment.NewLine, errors);
            return errors.Count == 0;
        }

        private static string BuildAddonMergeWorkDir(string rootPath)
        {
            string name = Path.GetFileName(rootPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
            if (string.IsNullOrWhiteSpace(name))
                name = "root";
            return Path.Combine(ToolPaths.WorkRoot, $"{name}_addonmerge_runs", DateTime.Now.ToString("yyyyMMdd_HHmmss_fff"));
        }

        private void PrepareAddonMergeRunState(bool scanOnly, bool reusingScan)
        {
            SaveSettings();
            _addonMergeCts?.Cancel();
            _addonMergeCts = new CancellationTokenSource();
            _addonMergeRunning = true;
            _addonMergeScanReady = false;
            _addonMergeActiveScanOnly = scanOnly;
            _addonMergeSummaryPath = null;
            _addonMergeScanReportPath = null;
            _addonMergeMergedRoot = null;
            _addonMergeGmaPath = null;
            _addonMergeWorkDir = BuildAddonMergeWorkDir(TextBox_AddonMergeRootPath.Text.Trim());

            ProgressBar_AddonMerge.IsIndeterminate = true;
            ProgressBar_AddonMerge.Minimum = 0;
            ProgressBar_AddonMerge.Maximum = 1000;
            ProgressBar_AddonMerge.Value = 0;
            TextBlock_AddonMergePhase.Text = "Iniciando";
            TextBlock_AddonMergeCurrent.Text = scanOnly ? "Procurando addons…" : reusingScan ? "Usando a última busca…" : "Preparando…";
            TextBlock_AddonMergeProgressPercent.Text = "0%";
            TextBlock_AddonMergeProgressDetail.Text = scanOnly
                ? "Listando os arquivos…"
                : reusingScan ? "Usando a última busca e juntando…" : "Procurando, juntando e validando…";
            TextBlock_AddonMergeStatus.Text = "Rodando";
            TextBox_AddonMergeSummary.Text = scanOnly ? "Procurando addons…" : "Procurando, juntando e validando…";
            TextBox_AddonMergeDiagnostics.Text = "O diagnóstico aparece ao final.";

            AppendAddonMergeLog(string.Empty);
            AppendAddonMergeLog(scanOnly
                ? "=== Procurando addons para juntar ==="
                : "=== Procurando, juntando e validando ===");
            UpdateAddonMergeActionStates();
        }

        private async void Button_AddonMergeScan_Click(object sender, RoutedEventArgs e)
        {
            if (_addonMergeRunning)
                return;

            if (!ValidateAddonMergeInputs(scanOnly: true, out var errorMessage))
            {
                MessageBox.Show(errorMessage, "Juntar addons", MessageBoxButton.OK, MessageBoxImage.Warning);
                TextBlock_AddonMergeStatus.Text = "Erro";
                TextBox_AddonMergeSummary.Text = errorMessage;
                return;
            }

            if (!EnsureToolsAvailable("Juntar addons"))
                return;

            PrepareAddonMergeRunState(scanOnly: true, reusingScan: false);
            await RunAddonMergeAsync(scanOnly: true, reuseScanReportPath: null);
        }

        private async void Button_AddonMergeRun_Click(object sender, RoutedEventArgs e)
        {
            if (_addonMergeRunning)
                return;

            if (!ValidateAddonMergeInputs(scanOnly: false, out var errorMessage))
            {
                MessageBox.Show(errorMessage, "Juntar addons", MessageBoxButton.OK, MessageBoxImage.Warning);
                TextBlock_AddonMergeStatus.Text = "Erro";
                TextBox_AddonMergeSummary.Text = errorMessage;
                return;
            }

            if (!EnsureToolsAvailable("Juntar addons"))
                return;

            string? reuseScanReportPath = GetReusableAddonMergeScanReportPath();
            PrepareAddonMergeRunState(scanOnly: false, reusingScan: !string.IsNullOrWhiteSpace(reuseScanReportPath));
            await RunAddonMergeAsync(scanOnly: false, reuseScanReportPath);
        }

        private async Task RunAddonMergeAsync(bool scanOnly, string? reuseScanReportPath)
        {
            if (!File.Exists(ToolPaths.WorkerExePath))
            {
                _addonMergeRunning = false;
                TextBlock_AddonMergeStatus.Text = "Falhou";
                TextBox_AddonMergeSummary.Text = "Worker do otimizador não encontrado.";
                UpdateAddonMergeActionStates();
                return;
            }

            var options = new AddonMergeRunOptions
            {
                WorkerExePath = ToolPaths.WorkerExePath,
                RootPath = TextBox_AddonMergeRootPath.Text.Trim(),
                WorkDir = _addonMergeWorkDir ?? BuildAddonMergeWorkDir(TextBox_AddonMergeRootPath.Text.Trim()),
                GmadExePath = scanOnly || GetSelectedAddonMergePackageMode() == "local-only"
                    ? null
                    : TextBox_AddonMergeGmadPath.Text.Trim(),
                ConflictPolicy = GetSelectedAddonMergeConflictPolicy(),
                PriorityMode = GetSelectedAddonMergePriorityMode(),
                PackageMode = GetSelectedAddonMergePackageMode(),
                ScanOnly = scanOnly,
                Recursive = true,
                AllowContentOnly = true,
                BundleName = GetAddonMergeBundleName(),
                OutputRoot = GetAddonMergeOutputRootPath(),
                ReuseScanReportPath = reuseScanReportPath,
                TitleOverride = GetAddonMergeBundleName(),
            };

            try
            {
                int exitCode = await _addonMergeRunner.RunAsync(options, _addonMergeCts?.Token ?? CancellationToken.None);
                FinishAddonMergeRun(exitCode);
            }
            catch (OperationCanceledException)
            {
                FinishAddonMergeRun(130);
            }
            catch (Exception ex)
            {
                _addonMergeRunning = false;
                ProgressBar_AddonMerge.IsIndeterminate = false;
                TextBlock_AddonMergePhase.Text = "Erro";
                TextBlock_AddonMergeStatus.Text = "Falhou";
                TextBox_AddonMergeSummary.Text = $"Falhou antes do resumo.{Environment.NewLine}{ex.Message}";
                AppendAddonMergeLog($"[GUI] {ex}");
                UpdateAddonMergeActionStates();
            }
        }

        private void Button_AddonMergeCancel_Click(object sender, RoutedEventArgs e)
        {
            if (!_addonMergeRunning)
                return;

            _addonMergeCts?.Cancel();
            TextBlock_AddonMergeStatus.Text = "Cancelando";
            TextBox_AddonMergeSummary.Text = "Cancelamento pedido. Aguardando o worker parar…";
            AppendAddonMergeLog("[GUI] Cancellation requested.");
            UpdateAddonMergeActionStates();
        }

        private void AddonMergeProgressChanged(AddonMergeProgressUpdate update)
        {
            Dispatcher.Invoke(() =>
            {
                if (!string.IsNullOrWhiteSpace(update.Phase))
                {
                    TextBlock_AddonMergePhase.Text = update.StepIndex.HasValue && update.StepTotal.HasValue
                        ? $"Etapa {update.StepIndex}/{update.StepTotal} · {update.Phase}"
                        : update.Phase;
                    if (!update.ItemTotal.HasValue)
                    {
                        if (ProgressBar_AddonMerge.Value <= 0)
                            ProgressBar_AddonMerge.IsIndeterminate = true;
                        TextBlock_AddonMergeCurrent.Text = update.Phase;
                    }
                }

                if (!string.IsNullOrWhiteSpace(update.ProgressKind) && update.ItemIndex.HasValue && update.ItemTotal.HasValue)
                {
                    ApplyAddonMergeDetailedProgress(update.ProgressKind, update.ItemIndex.Value, update.ItemTotal.Value, update.CurrentPath);
                    return;
                }

                if (update.ItemIndex.HasValue && update.ItemTotal.HasValue)
                {
                    ApplyAddonMergeDetailedProgress("scan-addons", update.ItemIndex.Value, update.ItemTotal.Value, update.CurrentPath);
                    TextBlock_AddonMergeCurrent.Text = string.IsNullOrWhiteSpace(update.CurrentPath)
                        ? $"Addon {update.ItemIndex}/{update.ItemTotal}"
                        : update.CurrentPath;
                }
            });
        }

        private void ApplyAddonMergeDetailedProgress(string kind, long current, long total, string? label)
        {
            double stageRatio = ComputeAddonMergeStageRatio(current, total);
            double overallRatio = ComputeAddonMergeOverallRatio(kind, current, total);
            ProgressBar_AddonMerge.IsIndeterminate = false;
            ProgressBar_AddonMerge.Minimum = 0;
            ProgressBar_AddonMerge.Maximum = 1000;
            ProgressBar_AddonMerge.Value = Math.Max(0, Math.Min(1000, overallRatio * 1000.0));
            TextBlock_AddonMergeProgressPercent.Text = overallRatio.ToString("P0");
            TextBlock_AddonMergeProgressDetail.Text = BuildAddonMergeProgressDetail(kind, current, total, stageRatio, overallRatio);
            if (!string.IsNullOrWhiteSpace(label))
                TextBlock_AddonMergeCurrent.Text = label;
        }

        private string BuildAddonMergeProgressDetail(string kind, long current, long total, double stageRatio, double overallRatio)
        {
            return kind switch
            {
                "scan-addons" => $"Addons descobertos: {current}/{Math.Max(total, 1L)} | etapa {stageRatio:P0} | total {overallRatio:P0}",
                "scan-files" => $"Arquivos analisados: {current:N0}/{Math.Max(total, 1L):N0} | etapa {stageRatio:P0} | total {overallRatio:P0}",
                "merge-plan" => $"Resolvendo conflitos e planejando a cópia: {current:N0}/{Math.Max(total, 1L):N0} | etapa {stageRatio:P0} | total {overallRatio:P0}",
                "merge-files" => $"Arquivos copiados: {current:N0}/{Math.Max(total, 1L):N0} | etapa {stageRatio:P0} | total {overallRatio:P0}",
                "merge-bytes" => $"Dados copiados: {FormatBytes(current)} / {FormatBytes(Math.Max(total, 1L))} | etapa {stageRatio:P0} | total {overallRatio:P0}",
                "validate-preflight" => $"Conferindo antes de empacotar: {stageRatio:P0} | total {overallRatio:P0}",
                "validate-step" => $"Validação/empacotamento: etapa {current}/{Math.Max(total, 1L)} | total {overallRatio:P0}",
                "validate-files" => $"Arquivos conferidos no roundtrip: {current:N0}/{Math.Max(total, 1L):N0} | etapa {stageRatio:P0} | total {overallRatio:P0}",
                _ => $"Progresso: {current:N0}/{Math.Max(total, 1L):N0} | etapa {stageRatio:P0} | total {overallRatio:P0}",
            };
        }

        private static double ComputeAddonMergeStageRatio(long current, long total)
        {
            return total <= 0 ? 0.0 : Math.Clamp((double)current / total, 0.0, 1.0);
        }

        private double ComputeAddonMergeOverallRatio(string kind, long current, long total)
        {
            double ratio = ComputeAddonMergeStageRatio(current, total);
            if (_addonMergeActiveScanOnly)
            {
                return kind switch
                {
                    "scan-addons" => ratio * 0.08,
                    "scan-files" => 0.08 + (ratio * 0.92),
                    _ => ratio,
                };
            }

            return kind switch
            {
                "scan-addons" => ratio * 0.05,
                "scan-files" => 0.05 + (ratio * 0.25),
                "merge-plan" => 0.30 + (ratio * 0.05),
                "merge-files" => 0.35 + (ratio * 0.40),
                "merge-bytes" => 0.35 + (ratio * 0.40),
                "validate-preflight" => 0.75 + (ratio * 0.05),
                "validate-step" => 0.80 + (ratio * 0.20),
                "validate-files" => 0.90 + (ratio * 0.10),
                _ => ratio,
            };
        }

        private void AppendAddonMergeLog(string line)
        {
            const int trimThreshold = 160000;
            const int trimTarget = 120000;

            if (TextBox_AddonMergeLog.Text.Length > trimThreshold)
                TextBox_AddonMergeLog.Text = TextBox_AddonMergeLog.Text[^trimTarget..];

            if (TextBox_AddonMergeLog.Text.Length > 0)
                TextBox_AddonMergeLog.AppendText(Environment.NewLine);

            TextBox_AddonMergeLog.AppendText(line);
            TextBox_AddonMergeLog.ScrollToEnd();
        }

        private void FinishAddonMergeRun(int exitCode)
        {
            _addonMergeRunning = false;
            ProgressBar_AddonMerge.IsIndeterminate = false;

            bool loadedSummary = LoadAddonMergeSummary();
            if (loadedSummary)
            {
                if (exitCode == 130)
                {
                    TextBlock_AddonMergePhase.Text = "Cancelado";
                    TextBlock_AddonMergeStatus.Text = "Cancelado";
                }
                else
                {
                    TextBlock_AddonMergePhase.Text = "Concluído";
                    TextBlock_AddonMergeStatus.Text = exitCode == 0 ? "Pronto" : exitCode == 2 ? "Bloqueado" : "Concluído com avisos";
                    ProgressBar_AddonMerge.Minimum = 0;
                    ProgressBar_AddonMerge.Maximum = 1000;
                    ProgressBar_AddonMerge.Value = 1000;
                    TextBlock_AddonMergeProgressPercent.Text = "100%";
                    TextBlock_AddonMergeProgressDetail.Text = exitCode == 0 ? "Concluído" : "Terminou com avisos; veja o diagnóstico em Detalhes";
                }
            }
            else
            {
                TextBlock_AddonMergePhase.Text = exitCode == 130 ? "Cancelado" : "Concluído";
                TextBlock_AddonMergeStatus.Text = exitCode == 0 ? "Pronto" : exitCode == 130 ? "Cancelado" : $"Falhou ({exitCode})";
                TextBlock_AddonMergeProgressPercent.Text = exitCode == 130 ? "0%" : "100%";
                TextBlock_AddonMergeProgressDetail.Text = exitCode == 130 ? "Cancelado antes do resumo" : "Sem resumo";
                TextBox_AddonMergeSummary.Text = exitCode == 130
                    ? "Cancelado antes do resumo."
                    : "Terminou sem o resumo (addon_merge_summary.json). Veja o log em Detalhes.";
            }

            UpdateAddonMergeActionStates();
        }

        private bool LoadAddonMergeSummary()
        {
            if (string.IsNullOrWhiteSpace(_addonMergeSummaryPath) || !File.Exists(_addonMergeSummaryPath))
                return false;

            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(_addonMergeSummaryPath));
                JsonElement root = document.RootElement;
                JsonElement run = root.TryGetProperty("run", out var runElement) ? runElement : default;
                JsonElement status = root.TryGetProperty("status", out var statusElement) ? statusElement : default;
                JsonElement scan = root.TryGetProperty("scan", out var scanElement) ? scanElement : default;
                JsonElement merge = root.TryGetProperty("merge", out var mergeElement) ? mergeElement : default;
                JsonElement validation = root.TryGetProperty("validation", out var validationElement) ? validationElement : default;
                JsonElement paths = root.TryGetProperty("paths", out var pathsElement) ? pathsElement : default;
                JsonElement details = root.TryGetProperty("details", out var detailsElement) ? detailsElement : default;

                int addonCount = GetJsonInt(scan, "addon_count");
                int fileCount = GetJsonInt(scan, "total_file_count");
                int uniquePathCount = GetJsonInt(scan, "unique_rel_path_count");
                int conflictCount = GetJsonInt(scan, "same_path_different_collisions");
                int discardedCount = GetJsonInt(details, "discarded_file_count");
                int mergedFileCount = GetJsonInt(merge, "merged_file_count");
                string mergeStatus = GetJsonString(merge, "status") ?? "scan-only";
                string validationStatus = GetJsonString(validation, "status") ?? "not-run";
                string overallStatus = GetJsonString(status, "overall_status") ?? "unknown";
                bool scanOnly = GetJsonBool(run, "scan_only");
                string packageMode = GetJsonString(run, "package_mode") ?? "strict";
                string rootPath = GetJsonString(run, "root") ?? TextBox_AddonMergeRootPath.Text.Trim();
                string outputRoot = GetJsonString(run, "output_root") ?? GetAddonMergeOutputRootPath();
                string bundleName = GetJsonString(run, "bundle_name") ?? GetAddonMergeBundleName();
                string? workDir = GetJsonString(run, "work_dir");
                string? scanReportPath = GetJsonString(paths, "scan_report");
                string? mergedRoot = GetJsonString(paths, "merged_root");
                string? gmaPath = GetJsonString(paths, "gma_path");

                _addonMergeWorkDir = workDir;
                _addonMergeScanReportPath = scanReportPath;
                _addonMergeMergedRoot = mergedRoot;
                _addonMergeGmaPath = gmaPath;
                _addonMergeScanReady = addonCount > 0;
                _addonMergeActiveScanOnly = scanOnly;
                _addonMergeLastScanFingerprint = BuildAddonMergeScanFingerprint();

                TextBlock_AddonMergeAddonCount.Text = addonCount.ToString();
                TextBlock_AddonMergeFileCount.Text = fileCount.ToString();
                TextBlock_AddonMergeUniquePathCount.Text = uniquePathCount.ToString();
                TextBlock_AddonMergeConflictCount.Text = conflictCount.ToString();
                TextBlock_AddonMergeDiscardedCount.Text = discardedCount.ToString();
                TextBlock_AddonMergeMergedFileCount.Text = mergedFileCount.ToString();
                TextBlock_AddonMergeMergeStatus.Text = mergeStatus;
                TextBlock_AddonMergeValidationStatus.Text = validationStatus;

                var summaryLines = new List<string>
                {
                    $"Origem: {rootPath}",
                    $"Nome do novo addon: {bundleName}",
                    $"Salvar em: {outputRoot}",
                    $"Pasta final: {Path.Combine(outputRoot, bundleName)}",
                    $"Pacote: {packageMode}",
                    $"Addons encontrados: {addonCount}",
                    $"Arquivos listados: {fileCount}",
                    $"Caminhos únicos: {uniquePathCount}",
                    $"Conflitos reais (mesmo caminho, conteúdo diferente): {conflictCount}",
                };

                if (discardedCount > 0)
                    summaryLines.Add($"Descartados pelo modo compatível: {discardedCount} arquivo(s)");

                if (scanOnly)
                {
                    summaryLines.Add("Só busca: nada foi juntado ainda.");
                }
                else
                {
                    summaryLines.Add($"Junção: {mergeStatus}");
                    if (mergedFileCount > 0)
                        summaryLines.Add($"Arquivos juntados: {mergedFileCount}");
                    summaryLines.Add($"Validação: {validationStatus}");

                    if (string.Equals(overallStatus, "merge-blocked", StringComparison.OrdinalIgnoreCase))
                        summaryLines.Add("A regra de conflito parou a junção: o mesmo caminho aparece com conteúdo diferente.");
                    else if (string.Equals(validationStatus, "packaging-blocked-invalid-files", StringComparison.OrdinalIgnoreCase))
                        summaryLines.Add("O pacote estrito foi bloqueado antes do gmad.exe: há arquivos que o gmad recusa. Veja o diagnóstico em Detalhes.");
                    else if (string.Equals(overallStatus, "validation-failed", StringComparison.OrdinalIgnoreCase))
                        summaryLines.Add("Juntou, mas a validação ou o pacote teve avisos. Veja o diagnóstico em Detalhes.");
                    else if (string.Equals(overallStatus, "ok", StringComparison.OrdinalIgnoreCase))
                        summaryLines.Add("Busca, junção e validação concluídas.");
                }

                if (!string.IsNullOrWhiteSpace(_addonMergeMergedRoot))
                    summaryLines.Add($"Addon unido: {_addonMergeMergedRoot}");
                if (!string.IsNullOrWhiteSpace(_addonMergeGmaPath))
                    summaryLines.Add($"Pacote: {_addonMergeGmaPath}");

                TextBox_AddonMergeSummary.Text = string.Join(Environment.NewLine, summaryLines);
                TextBlock_AddonMergeProgressDetail.Text = scanOnly ? "Busca concluída" : "Concluído";
                TextBox_AddonMergeDiagnostics.Text = BuildAddonMergeDiagnostics(details, validation);
                return true;
            }
            catch (Exception ex)
            {
                TextBox_AddonMergeSummary.Text = $"Não foi possível ler addon_merge_summary.json.{Environment.NewLine}{ex.Message}";
                return false;
            }
        }

        private string BuildAddonMergeDiagnostics(JsonElement details, JsonElement validation)
        {
            var lines = new List<string>();

            AppendPreviewSection(lines, "Conflitos reais", details, "same_path_different_conflict_preview",
                item => $"- {GetJsonString(item, "rel_path")} | addons: {string.Join(", ", GetJsonStringArray(item, "addon_names"))} | hashes: {GetJsonInt(item, "hash_count")}");

            AppendPreviewSection(lines, "Bloqueados pela regra de conflito", details, "blocked_conflict_preview",
                item => $"- {GetJsonString(item, "rel_key")} | addons: {string.Join(", ", GetJsonStringArray(item, "addon_names"))}");

            AppendPreviewSection(lines, "Descartados pelo modo compatível", details, "discarded_files_preview",
                item => $"- {GetJsonString(item, "addon_name")} | {GetJsonString(item, "rel_path")} | pattern: {GetJsonString(item, "matched_pattern")}");

            AppendPreviewSection(lines, "Conflitos resolvidos", details, "overridden_conflict_preview",
                item => $"- {GetJsonString(item, "rel_key")} | {GetJsonString(item, "resolution")} | winner: {GetJsonString(item, "winner")}");

            AppendPreviewSection(lines, "Arquivos que bloqueiam o pacote estrito", details, "packaging_preflight_invalid_files_preview",
                item => $"- {GetJsonString(item, "rel_path")} | pattern: {GetJsonString(item, "matched_pattern")} | size: {FormatBytes(GetJsonLong(item, "size"))}");

            string? packagingExcerpt = GetJsonString(details, "packaging_output_excerpt");
            if (!string.IsNullOrWhiteSpace(packagingExcerpt))
            {
                lines.Add("Saída do empacotamento:");
                lines.Add(packagingExcerpt);
            }

            JsonElement warninvalid = validation.ValueKind == JsonValueKind.Object && validation.TryGetProperty("packaging", out var packaging) &&
                                      packaging.ValueKind == JsonValueKind.Object && packaging.TryGetProperty("warninvalid_diagnostic", out var warninvalidElement)
                ? warninvalidElement
                : default;
            if (warninvalid.ValueKind == JsonValueKind.Object && GetJsonBool(warninvalid, "skipped"))
            {
                if (lines.Count > 0)
                    lines.Add(string.Empty);
                lines.Add($"Diagnóstico warninvalid pulado: {GetJsonString(warninvalid, "reason") ?? "sem motivo"}");
            }

            string? warninvalidExcerpt = GetJsonString(details, "warninvalid_output_excerpt");
            if (!string.IsNullOrWhiteSpace(warninvalidExcerpt))
            {
                if (lines.Count > 0)
                    lines.Add(string.Empty);
                lines.Add("Diagnóstico warninvalid:");
                lines.Add(warninvalidExcerpt);
            }

            if (lines.Count == 0)
                return "Nenhum conflito, descarte ou aviso de empacotamento.";

            return string.Join(Environment.NewLine, lines);
        }

        private static void AppendPreviewSection(
            List<string> lines,
            string title,
            JsonElement parent,
            string propertyName,
            Func<JsonElement, string> formatter)
        {
            if (parent.ValueKind != JsonValueKind.Object || !parent.TryGetProperty(propertyName, out var array) || array.ValueKind != JsonValueKind.Array || array.GetArrayLength() == 0)
                return;

            if (lines.Count > 0)
                lines.Add(string.Empty);

            lines.Add(title + ":");
            foreach (JsonElement item in array.EnumerateArray())
                lines.Add(formatter(item));
        }

        private static string[] GetJsonStringArray(JsonElement element, string propertyName)
        {
            if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(propertyName, out var array) || array.ValueKind != JsonValueKind.Array)
                return Array.Empty<string>();

            var values = new List<string>();
            foreach (JsonElement item in array.EnumerateArray())
            {
                string? value = item.ValueKind == JsonValueKind.String ? item.GetString() : item.ToString();
                if (!string.IsNullOrWhiteSpace(value))
                    values.Add(value);
            }
            return values.ToArray();
        }

        private string GetSelectedAddonMergeConflictPolicy()
        {
            if (ComboBox_AddonMergeConflictPolicy.SelectedItem is ComboBoxItem item && item.Tag is string tag && !string.IsNullOrWhiteSpace(tag))
                return tag;
            return "first";
        }

        private void SetSelectedAddonMergeConflictPolicy(string? mode)
        {
            SelectComboTag(ComboBox_AddonMergeConflictPolicy, mode, "first");
        }

        private string GetSelectedAddonMergePriorityMode()
        {
            if (ComboBox_AddonMergePriorityMode.SelectedItem is ComboBoxItem item && item.Tag is string tag && !string.IsNullOrWhiteSpace(tag))
                return tag;
            return "name-asc";
        }

        private void SetSelectedAddonMergePriorityMode(string? mode)
        {
            SelectComboTag(ComboBox_AddonMergePriorityMode, mode, "name-asc");
        }

        private string GetSelectedAddonMergePackagingStrategy()
        {
            if (ComboBox_AddonMergePackageMode.SelectedItem is ComboBoxItem item && item.Tag is string tag && !string.IsNullOrWhiteSpace(tag))
                return tag;
            return "strict";
        }

        private string GetSelectedAddonMergePackageMode()
        {
            return IsAddonMergePackagingEnabled() ? GetSelectedAddonMergePackagingStrategy() : "local-only";
        }

        private void SetSelectedAddonMergePackageMode(string? mode)
        {
            string normalized = string.IsNullOrWhiteSpace(mode) ? "strict" : mode.Trim().ToLowerInvariant();
            if (string.Equals(normalized, "local-only", StringComparison.OrdinalIgnoreCase))
                normalized = "strict";
            SelectComboTag(ComboBox_AddonMergePackageMode, normalized, "strict");
        }

        private static void SelectComboTag(ComboBox comboBox, string? value, string fallbackTag)
        {
            string target = string.IsNullOrWhiteSpace(value) ? fallbackTag : value.Trim().ToLowerInvariant();
            for (int i = 0; i < comboBox.Items.Count; i++)
            {
                if (comboBox.Items[i] is ComboBoxItem item &&
                    item.Tag is string tag &&
                    string.Equals(tag, target, StringComparison.OrdinalIgnoreCase))
                {
                    comboBox.SelectedIndex = i;
                    return;
                }
            }

            comboBox.SelectedIndex = 0;
        }

        private void LoadAddonMergeSettings()
        {
            TextBox_AddonMergeRootPath.Text = _settings.AddonMergeRootPath ?? string.Empty;
            TextBox_AddonMergeOutputRoot.Text = _settings.AddonMergeOutputRootPath ?? string.Empty;
            TextBox_AddonMergeBundleName.Text = _settings.AddonMergeBundleName ?? string.Empty;
            SetSelectedAddonMergeConflictPolicy(_settings.AddonMergeConflictPolicy);
            SetSelectedAddonMergePriorityMode(_settings.AddonMergePriorityMode);
            SetSelectedAddonMergePackageMode(_settings.AddonMergePackageMode);
            CheckBox_AddonMergeGenerateGma.IsChecked = _settings.AddonMergeGenerateGma ?? false;
            UpdateAddonMergeDefaultsFromRoot(force: false);
            ResetAddonMergeSummary();
            UpdateAddonMergeActionStates();
        }

        private void SaveAddonMergeSettings(AppSettingsModel settings)
        {
            settings.AddonMergeRootPath = TextBox_AddonMergeRootPath.Text.Trim();
            settings.AddonMergeOutputRootPath = GetAddonMergeOutputRootPath();
            settings.AddonMergeBundleName = GetAddonMergeBundleName();
            settings.AddonMergeConflictPolicy = GetSelectedAddonMergeConflictPolicy();
            settings.AddonMergePriorityMode = GetSelectedAddonMergePriorityMode();
            settings.AddonMergeGenerateGma = IsAddonMergePackagingEnabled();
            settings.AddonMergePackageMode = GetSelectedAddonMergePackagingStrategy();
        }

        private void Button_AddonMergeOpenRoot_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(TextBox_AddonMergeRootPath.Text.Trim(), "Juntar addons");
        }

        private void Button_AddonMergeOpenMerged_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(_addonMergeMergedRoot, "Juntar addons");
        }

        private void Button_AddonMergeOpenWork_Click(object sender, RoutedEventArgs e)
        {
            OpenFolder(_addonMergeWorkDir, "Juntar addons");
        }

        private void Button_AddonMergeOpenReports_Click(object sender, RoutedEventArgs e)
        {
            string? reportsDir = string.IsNullOrWhiteSpace(_addonMergeWorkDir) ? null : Path.Combine(_addonMergeWorkDir, "reports");
            OpenFolder(reportsDir, "Juntar addons");
        }
    }
}
