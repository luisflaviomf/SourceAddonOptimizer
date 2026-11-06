using GmodAddonCompressor.Systems.Optimizer;
using GmodAddonCompressor.Systems.Tools;
using GmodAddonCompressor.Models;
using Microsoft.Win32;
using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace GmodAddonCompressor;

public partial class MainWindow
{
    private readonly FullCompressionRunner _fullRunner = new();
    private CancellationTokenSource? _fullCts;
    private bool _fullRunning;
    private FullCompressionResult? _fullResult;
    private string? _fullWork;
    private readonly FullCompressionPresentation _fullPresentation = new();
    private CancellationTokenSource? _fullInventoryCts;

    private void InitializeFullCompression()
    {
        FullWorkspace.DataContext = _fullPresentation;
        _context.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(_context.UnlockedUI))
                _fullPresentation.SetAvailable(_context.UnlockedUI);
        };
        foreach (var field in new[] { TextBox_FullInput, TextBox_FullOutput, TextBox_FullEngine, TextBox_FullBlender, TextBox_FullStudioMdl })
            field.TextChanged += FullConfigurationChanged;
        _fullRunner.LogLine += line => Dispatcher.Invoke(() =>
        {
            if (TextBox_FullLog.Text.Length > 120000) TextBox_FullLog.Clear();
            TextBox_FullLog.AppendText(line + Environment.NewLine);
            TextBox_FullLog.ScrollToEnd();
        });
        _fullRunner.Progress += progress => Dispatcher.Invoke(() =>
        {
            TextBlock_FullStatus.Text = $"{progress.Phase} {progress.Index}/{progress.Total} {progress.Path}";
            _fullPresentation.UpdatePhase(progress.Phase);
            ProgressBar_Full.IsIndeterminate = progress.Total <= 1;
            ProgressBar_Full.Maximum = Math.Max(1, progress.Total);
            ProgressBar_Full.Value = progress.Index;
        });
        Closing += (_, args) =>
        {
            _fullInventoryCts?.Cancel();
            if (!_fullRunning) return;
            args.Cancel = true;
            _fullCts?.Cancel();
            TextBlock_FullStatus.Text = "Cancelando com segurança; aguarde antes de fechar.";
        };
    }

    private void FullConfigurationChanged(object sender, TextChangedEventArgs args)
    {
        if (_fullRunning) return;
        _fullResult = null;
        Button_FullOpenOutput.IsEnabled = false;
        TextBox_FullSummary.Clear();
        _fullPresentation.Prepare(!string.IsNullOrWhiteSpace(TextBox_FullInput.Text));
        TextBlock_FullStatus.Text = "Confira a configuração antes de começar. O original permanece intacto.";
        ProgressBar_Full.Value = 0;
        if (ReferenceEquals(sender, TextBox_FullInput)) _ = RefreshFullInventoryAsync();
    }

    private async Task RefreshFullInventoryAsync()
    {
        _fullInventoryCts?.Cancel();
        var request = new CancellationTokenSource();
        _fullInventoryCts = request;
        string source = TextBox_FullInput.Text.Trim();
        _fullPresentation.Files.Clear();
        TextBlock_FullInventoryHint.Text = string.IsNullOrEmpty(source) ? "Nenhum addon selecionado." : "Lendo arquivos de origem…";
        try
        {
            await Task.Delay(300, request.Token);
            var rows = await Task.Run(() =>
            {
                if (!Directory.Exists(source)) throw new DirectoryNotFoundException("Selecione uma pasta de addon existente.");
                var result = new System.Collections.Generic.List<FullCompressionFileRow>();
                var options = new EnumerationOptions { RecurseSubdirectories = true, IgnoreInaccessible = true, AttributesToSkip = FileAttributes.ReparsePoint };
                foreach (string file in Directory.EnumerateFiles(source, "*", options))
                {
                    request.Token.ThrowIfCancellationRequested();
                    result.Add(new(Path.GetRelativePath(source, file), Path.GetExtension(file).TrimStart('.').ToUpperInvariant(), new FileInfo(file).Length));
                    if (result.Count >= 1000) break;
                }
                return result.OrderBy(row => row.Path, StringComparer.OrdinalIgnoreCase).ToArray();
            }, request.Token);
            request.Token.ThrowIfCancellationRequested();
            foreach (var row in rows) _fullPresentation.Files.Add(row);
            TextBlock_FullInventoryHint.Text = $"{rows.Length:N0} arquivos de origem exibidos · limite de 1.000 · atalhos e itens inacessíveis omitidos. Elegibilidade verificada ao processar.";
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            if (!request.IsCancellationRequested) TextBlock_FullInventoryHint.Text = "Não foi possível listar: " + ex.Message;
        }
        finally { if (ReferenceEquals(_fullInventoryCts, request)) _fullInventoryCts = null; request.Dispose(); }
    }

    private void Button_FullBrowse_Click(object sender, RoutedEventArgs args)
    {
        string field = (string)((Button)sender).Tag;
        if (field is "blender" or "studiomdl")
        {
            var file = new OpenFileDialog { Filter = "Executável (*.exe)|*.exe", CheckFileExists = true };
            if (file.ShowDialog(this) != true) return;
            if (field == "blender") TextBox_FullBlender.Text = file.FileName;
            else TextBox_FullStudioMdl.Text = file.FileName;
            return;
        }
        var folder = new Ookii.Dialogs.Wpf.VistaFolderBrowserDialog();
        if (folder.ShowDialog(this) != true) return;
        if (field == "input")
        {
            TextBox_FullInput.Text = folder.SelectedPath;
            TextBox_FullOutput.Text = FreshFullOutput(Path.GetDirectoryName(folder.SelectedPath) ?? folder.SelectedPath);
        }
        else if (field == "engine") TextBox_FullEngine.Text = folder.SelectedPath;
        else TextBox_FullOutput.Text = FreshFullOutput(folder.SelectedPath);
    }

    private static string FreshFullOutput(string parent) => Path.Combine(parent,
        "compressed_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + "_" + Guid.NewGuid().ToString("N")[..6]);

    private void Button_FullDetect_Click(object sender, RoutedEventArgs args)
    {
        FillFullInstalledTools();
    }

    private void FillFullInstalledTools()
    {
        // Saved full-flow choices win; reuse other tabs only for missing/stale paths.
        // Discovery is read-only: never install tools or infer that a missing path is usable.
        static bool IsEngine(string? path) => !string.IsNullOrWhiteSpace(path)
            && Directory.Exists(Path.Combine(path, "garrysmod")) && Directory.Exists(Path.Combine(path, "bin"));
        static string? EngineFromTool(string? path)
        {
            if (!File.Exists(path)) return null;
            var bin = Path.GetDirectoryName(path);
            var root = bin == null ? null : Path.GetDirectoryName(bin);
            return IsEngine(root) ? root : null;
        }
        if (!IsEngine(TextBox_FullEngine.Text))
        {
            string? engine = new[] { TextBox_ExactEngine.Text, EngineFromTool(TextBox_FullStudioMdl.Text),
                EngineFromTool(_context.StudioMdlPath), EngineFromTool(_settings.GmadPath) }.FirstOrDefault(IsEngine);
            engine ??= EngineFromTool(DetectGmadPath());
            if (engine != null) TextBox_FullEngine.Text = engine;
        }
        if (!File.Exists(TextBox_FullBlender.Text))
        {
            string? blender = File.Exists(_context.BlenderPath) ? _context.BlenderPath : DetectBlenderPath();
            if (blender != null) TextBox_FullBlender.Text = blender;
        }
        if (!File.Exists(TextBox_FullStudioMdl.Text))
        {
            string inEngine = Path.Combine(TextBox_FullEngine.Text, "bin", "studiomdl.exe");
            string? compiler = IsEngine(TextBox_FullEngine.Text) && File.Exists(inEngine) ? inEngine
                : File.Exists(_context.StudioMdlPath) ? _context.StudioMdlPath : DetectStudioMdlPath();
            if (compiler != null) TextBox_FullStudioMdl.Text = compiler;
        }
    }

    private async void Button_FullStart_Click(object sender, RoutedEventArgs args)
    {
        if (_fullRunning || !_context.UnlockedUI) return;
        if (string.IsNullOrWhiteSpace(TextBox_FullInput.Text))
        {
            Button_FullBrowse_Click(new Button { Tag = "input" }, args);
            return;
        }
        try
        {
            _fullResult = null;
            TextBox_FullSummary.Clear(); TextBox_FullLog.Clear();
            Button_FullOpenOutput.IsEnabled = false;
            Button_FullOpenWork.IsEnabled = false;
            _fullWork = Path.Combine(ToolPaths.WorkRoot, "complete_" + Guid.NewGuid().ToString("N"));
            var options = new FullCompressionOptions(TextBox_FullInput.Text.Trim(), TextBox_FullOutput.Text.Trim(),
                _fullWork, TextBox_FullEngine.Text.Trim(), ToolPaths.WorkerExePath,
                TextBox_FullBlender.Text.Trim(), TextBox_FullStudioMdl.Text.Trim());
            ExactDistributionRunner.ValidatePaths(FullCompressionRunner.PreflightOptions(options));
            if (!EnsureToolsAvailable("Compressão completa")) return;
            SaveSettings();
            _fullRunning = true; _context.UnlockedUI = false;
            _fullPresentation.Start(); Expander_FullActivity.IsExpanded = true;
            Button_FullCancel.IsEnabled = true; Button_FullOpenWork.IsEnabled = true;
            ProgressBar_Full.IsIndeterminate = true;
            _fullCts?.Dispose(); _fullCts = new CancellationTokenSource();
            _fullResult = await Task.Run(() => _fullRunner.RunAsync(options, _fullCts.Token));
            TextBox_FullSummary.Text = _fullResult.Summary;
            TextBlock_FullStatus.Text = "Concluído offline — candidato pronto para validação no GMod.";
            _fullPresentation.Complete($"Economia: {_fullResult.SavedBytes:N0} B · {_fullResult.Seconds:N1} s");
            Button_FullOpenOutput.IsEnabled = true;
        }
        catch (OperationCanceledException)
        {
            _fullPresentation.Cancel();
            TextBlock_FullStatus.Text = "Cancelado. Não use a saída parcial; repita com uma pasta nova.";
        }
        catch (Exception ex)
        {
            _fullPresentation.Fail(ex.Message); Expander_FullActivity.IsExpanded = true;
            TextBlock_FullStatus.Text = "Falha: " + ex.Message;
            TextBox_FullLog.AppendText(ex + Environment.NewLine);
        }
        finally
        {
            _fullRunning = false; _context.UnlockedUI = true;
            Button_FullCancel.IsEnabled = false; ProgressBar_Full.IsIndeterminate = false;
        }
    }

    private void Button_FullCancel_Click(object sender, RoutedEventArgs args)
    {
        _fullCts?.Cancel();
        TextBlock_FullStatus.Text = "Cancelando — aguardando a operação nativa atual terminar com segurança.";
    }
    private void Button_FullOpenOutput_Click(object sender, RoutedEventArgs args) => OpenFolder(_fullResult?.Exact.OutputPath, "Compressão completa");
    private void Button_FullOpenWork_Click(object sender, RoutedEventArgs args) => OpenFolder(_fullWork, "Relatórios da compressão completa");
}
