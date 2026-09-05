using GmodAddonCompressor.Systems.Optimizer;
using GmodAddonCompressor.Systems.Tools;
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace GmodAddonCompressor
{
    public partial class MainWindow
    {
        private readonly ExactDistributionRunner _exactRunner = new();
        private CancellationTokenSource? _exactCts;
        private bool _exactRunning;
        private bool _pipelineExactRequested;
        private string _pipelineExactEngine = string.Empty;
        private ExactDistributionResult? _exactResult;
        private ExactDistributionResult? _pipelineExactResult;
        private string _pipelineExactStatus = "Desativada";
        private string? _exactWorkDir;
        private string? _exactLogPath;

        private void InitializeExactDistribution()
        {
            _exactRunner.LogLine += line => Dispatcher.Invoke(() =>
            {
                if (TextBox_ExactLog.Text.Length > 150000) TextBox_ExactLog.Clear();
                TextBox_ExactLog.AppendText(line + Environment.NewLine);
                TextBox_ExactLog.ScrollToEnd();
                if (_exactLogPath != null)
                {
                    try { File.AppendAllText(_exactLogPath, line + Environment.NewLine); }
                    catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException) { }
                }
            });
            _exactRunner.ProgressUpdate += progress => Dispatcher.Invoke(() =>
            {
                ProgressBar_Exact.Maximum = Math.Max(1, progress.Total);
                ProgressBar_Exact.Value = progress.Index;
                TextBlock_ExactStatus.Text = $"{progress.Phase}: {progress.Index}/{progress.Total} {progress.Path}";
                if (_pipelineRunning && _pipelineStage == PipelineStage.ExactDistribution)
                {
                    _context.PipelineStatusText = "Distribuição exata: " + progress.Phase;
                    _context.PipelineProgressText = $"{progress.Index}/{progress.Total} {progress.Path}";
                    _context.PipelineProgressMaxValue = Math.Max(1, progress.Total);
                    _context.PipelineProgressValue = progress.Index;
                }
            });
        }

        private void Button_ExactBrowse_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new Ookii.Dialogs.Wpf.VistaFolderBrowserDialog();
            if (!dialog.ShowDialog(this).GetValueOrDefault()) return;
            string field = (string)((Button)sender).Tag;
            if (field == "engine") TextBox_ExactEngine.Text = dialog.SelectedPath;
            else if (field == "output") TextBox_ExactOutput.Text = FreshExactOutput(dialog.SelectedPath);
            else
            {
                TextBox_ExactInput.Text = dialog.SelectedPath;
                TextBox_ExactOutput.Text = FreshExactOutput(dialog.SelectedPath);
            }
        }

        private static string FreshExactOutput(string source) =>
            Path.Combine(Path.GetDirectoryName(Path.GetFullPath(source).TrimEnd('\\', '/'))!,
                "exact_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + "_" + Guid.NewGuid().ToString("N")[..8]);

        private ExactDistributionRunOptions ExactOptions(string source, string output, string engine) => new()
        {
            WorkerExePath = ToolPaths.WorkerExePath, SourcePath = source.Trim(), OutputPath = output.Trim(),
            EngineRoot = engine.Trim(), WorkDir = Path.Combine(ToolPaths.WorkRoot, "exact_" + Guid.NewGuid().ToString("N"))
        };

        private void Button_ExactUseAddon_Click(object sender, RoutedEventArgs e)
        {
            TextBox_ExactInput.Text = _context.AddonDirectoryPath;
            if (!string.IsNullOrWhiteSpace(_context.AddonDirectoryPath))
                TextBox_ExactOutput.Text = FreshExactOutput(_context.AddonDirectoryPath);
        }

        private async void Button_ExactExport_Click(object sender, RoutedEventArgs e)
        {
            if (_exactRunning || _pipelineRunning || !_context.UnlockedUI) return;
            try
            {
                var options = ExactOptions(TextBox_ExactInput.Text, TextBox_ExactOutput.Text, TextBox_ExactEngine.Text);
                ExactDistributionRunner.ValidatePaths(options);
                if (!EnsureToolsAvailable("Distribuição exata")) return;
                SaveSettings();
                _context.UnlockedUI = false;
                _exactCts?.Dispose();
                _exactCts = new CancellationTokenSource();
                await RunExactExportAsync(options, _exactCts.Token);
            }
            catch (OperationCanceledException) { TextBlock_ExactStatus.Text = "Cancelado. Saída parcial não aprovada; use uma nova pasta ao repetir."; }
            catch (Exception ex) { TextBlock_ExactStatus.Text = "FAIL: " + ex.Message; }
            finally { _context.UnlockedUI = true; }
        }

        private async Task<ExactDistributionResult> RunExactExportAsync(ExactDistributionRunOptions options, CancellationToken token)
        {
            if (_exactRunning) throw new InvalidOperationException("Exportação exata já em andamento.");
            _exactRunning = true;
            try
            {
                _exactResult = null;
                _exactWorkDir = options.WorkDir;
                Directory.CreateDirectory(ToolPaths.WorkRoot);
                _exactLogPath = options.WorkDir + ".log";
                Button_ExactCancel.IsEnabled = true;
                Button_ExactOpenOutput.IsEnabled = false;
                Button_ExactOpenReport.IsEnabled = false;
                TextBox_ExactLog.Clear();
                TextBox_ExactSummary.Clear();
                TextBlock_ExactStatus.Text = "Exportando candidato experimental...";
                var result = await _exactRunner.RunAsync(options, token);
                token.ThrowIfCancellationRequested();
                _exactResult = result;
                TextBox_ExactSummary.Text = result.Summary;
                TextBlock_ExactStatus.Text = result.Success ? "Exportado — OFFLINE_PENDING_GMOD" : "FAIL: " + result.Summary;
                Button_ExactOpenOutput.IsEnabled = result.Success;
                Button_ExactOpenReport.IsEnabled = result.Success;
                return result;
            }
            catch (OperationCanceledException)
            {
                TextBlock_ExactStatus.Text = "Cancelado. Candidato não aprovado.";
                throw;
            }
            catch (Exception ex)
            {
                TextBlock_ExactStatus.Text = "FAIL: " + ex.Message;
                throw;
            }
            finally
            {
                _exactRunning = false;
                Button_ExactCancel.IsEnabled = false;
                if (Directory.Exists(options.WorkDir) && File.Exists(_exactLogPath))
                {
                    try { File.Copy(_exactLogPath, Path.Combine(options.WorkDir, "wpf-worker.log"), overwrite: false); }
                    catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException) { }
                }
            }
        }

        private async Task RunPipelineExactAsync(string nativeOutput, CancellationToken token)
        {
            _pipelineStage = PipelineStage.ExactDistribution;
            _pipelineExactStatus = "Executando";
            try
            {
                _pipelineExactResult = await RunExactExportAsync(ExactOptions(nativeOutput, FreshExactOutput(nativeOutput), _pipelineExactEngine), token);
                _pipelineExactStatus = _pipelineExactResult.Success ? "OFFLINE_PENDING_GMOD" : "FAIL";
            }
            catch (OperationCanceledException) { _pipelineExactStatus = "CANCELED"; throw; }
            catch { _pipelineExactStatus = "FAIL"; throw; }
        }

        private string PipelineExactSummary() =>
            $"Distribuição exata: {_pipelineExactStatus}\nSaída exata: {_pipelineExactResult?.OutputPath ?? "-"}\n" +
            $"Relatório exato: {_pipelineExactResult?.ReportPath ?? "-"}\n" + (_pipelineExactResult?.Summary ?? string.Empty);

        private void Button_ExactCancel_Click(object sender, RoutedEventArgs e)
        {
            if (_pipelineRunning && _pipelineStage == PipelineStage.ExactDistribution)
                Button_CancelPipeline_Click(sender, e);
            else _exactCts?.Cancel();
        }

        private void Button_ExactOpenOutput_Click(object sender, RoutedEventArgs e) => OpenFolder(_exactResult?.OutputPath, "Distribuição exata");
        private void Button_ExactOpenWork_Click(object sender, RoutedEventArgs e) => OpenFolder(_exactWorkDir, "Distribuição exata");
        private void Button_ExactOpenReport_Click(object sender, RoutedEventArgs e)
        {
            if (_exactResult?.Success == true && File.Exists(_exactResult.ReportPath))
                Process.Start(new ProcessStartInfo(_exactResult.ReportPath!) { UseShellExecute = true });
        }
    }
}
