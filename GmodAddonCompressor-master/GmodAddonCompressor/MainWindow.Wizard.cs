using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Systems.Optimizer;
using GmodAddonCompressor.Systems.Reporting;
using GmodAddonCompressor.Systems.Tools;
using Ookii.Dialogs.Wpf;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Threading;

namespace GmodAddonCompressor;

/// <summary>
/// Navigation rail (Otimizar, Avançado, Ferramentas, Testar no jogo) and the "Otimizar" assistant:
/// Addon → Objetivo → Otimizar. The assistant reuses the Models ("Tela") worker and the Mídia compress
/// path exactly as the Pipeline does; its profile is applied to the shared settings only for the length
/// of a run and the user's own Avançado settings are restored afterwards.
/// </summary>
public partial class MainWindow
{
    private const int WizardRecentMax = 5;
    private const int WizardLogMaxChars = 200_000;

    private readonly OptimizeWizardContext _wizard = new();
    private CancellationTokenSource? _wizardScanCts;
    private bool _wizardRunning;
    private bool _wizardSettingsApplied;
    private WizardSettingsSnapshot? _wizardSnapshot;
    private readonly Stopwatch _wizardClock = new();
    private DispatcherTimer? _wizardTimer;
    private double[] _wizardStageWeights = Array.Empty<double>();
    private int _wizardStageIndex;
    private double _wizardStageFraction;
    private string? _wizardOutput;
    private string? _wizardReportDir;

    private static string WizardRecentPath => Path.Combine(ToolPaths.AppDataRoot, "wizard-recent.json");

    // ---------------------------------------------------------------- navigation

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    /// <summary>Dark Windows title bar to match the Material 3 dark scheme (Windows 10 2004+ / 11).</summary>
    private void UseDarkTitleBar()
    {
        try
        {
            IntPtr hwnd = new WindowInteropHelper(this).Handle;
            int on = 1;
            if (DwmSetWindowAttribute(hwnd, 20, ref on, sizeof(int)) != 0)
                DwmSetWindowAttribute(hwnd, 19, ref on, sizeof(int)); // older Windows 10 builds
        }
        catch
        {
            // cosmetic only
        }
    }

    private void InitializeNavigation()
    {
        SourceInitialized += (_, _) => UseDarkTitleBar();
        Rail_Optimize.Checked += (_, _) => ShowSection("optimize");
        Rail_Advanced.Checked += (_, _) => ShowSection("advanced");
        Rail_Tools.Checked += (_, _) => ShowSection("tools");
        Rail_Test.Checked += (_, _) => ShowSection("test");
        foreach (var panel in new[] { AdvancedTabsPanel, ToolsTabsPanel })
        {
            foreach (var tab in panel.Children.OfType<RadioButton>())
                tab.Checked += (_, _) => SelectMainTab(tab.Tag as string);
        }
        Rail_Optimize.IsChecked = true;
    }

    private void ShowSection(string section)
    {
        bool advanced = section == "advanced";
        bool tools = section == "tools";
        AdvancedTabsPanel.Visibility = advanced ? Visibility.Visible : Visibility.Collapsed;
        ToolsTabsPanel.Visibility = tools ? Visibility.Visible : Visibility.Collapsed;
        SecondaryNavBar.Visibility = advanced || tools ? Visibility.Visible : Visibility.Collapsed;

        var rail = section switch
        {
            "advanced" => Rail_Advanced,
            "tools" => Rail_Tools,
            "test" => Rail_Test,
            _ => Rail_Optimize
        };
        if (rail.IsChecked != true)
            rail.IsChecked = true;

        switch (section)
        {
            case "advanced":
            case "tools":
                var panel = advanced ? AdvancedTabsPanel : ToolsTabsPanel;
                var selected = panel.Children.OfType<RadioButton>().FirstOrDefault(r => r.IsChecked == true)
                               ?? panel.Children.OfType<RadioButton>().First();
                selected.IsChecked = true;
                SelectMainTab(selected.Tag as string);
                break;
            case "test":
                MainTabs.SelectedItem = Tab_Companion;
                break;
            default:
                MainTabs.SelectedItem = Tab_Wizard;
                break;
        }
    }

    private void SelectMainTab(string? tabName)
    {
        if (string.IsNullOrWhiteSpace(tabName))
            return;
        if (FindName(tabName) is TabItem tab)
            MainTabs.SelectedItem = tab;
    }

    // ---------------------------------------------------------------- setup

    private void InitializeWizard()
    {
        WizardRoot.DataContext = _wizard;
        LoadWizardRecent();
        _wizard.Available = _context.UnlockedUI;
        _context.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(_context.UnlockedUI))
                _wizard.Available = _context.UnlockedUI && !_wizardRunning;
        };
        _optimizerRunner.LogLine += line =>
        {
            if (_wizardRunning)
                Dispatcher.BeginInvoke(() => AppendWizardLog(line));
        };
        _optimizerRunner.ProgressUpdate += update =>
        {
            if (_wizardRunning)
                Dispatcher.BeginInvoke(() => WizardModelsProgress(update));
        };
        Closing += (_, args) =>
        {
            _wizardScanCts?.Cancel();
            if (!_wizardRunning)
                return;
            args.Cancel = true;
            RequestWizardCancel();
            _wizard.StageText = "Cancelando com segurança; aguarde antes de fechar.";
        };
        InitializeNavigation();
    }

    // ---------------------------------------------------------------- step 1: addon

    private void WizardBrowse_Click(object sender, RoutedEventArgs e)
    {
        if (_wizardRunning)
            return;
        var dialog = new VistaFolderBrowserDialog
        {
            Description = "Escolha a pasta do addon",
            UseDescriptionForTitle = true
        };
        if (!string.IsNullOrWhiteSpace(_wizard.AddonPath) && Directory.Exists(_wizard.AddonPath))
            dialog.SelectedPath = _wizard.AddonPath;
        if (dialog.ShowDialog(this).GetValueOrDefault())
            SetWizardAddon(dialog.SelectedPath);
    }

    private void WizardRecent_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement { Tag: string path } && Directory.Exists(path))
            SetWizardAddon(path);
    }

    private void WizardDropZone_DragOver(object sender, DragEventArgs e)
    {
        e.Effects = TryGetDroppedFolder(e, out _) ? DragDropEffects.Copy : DragDropEffects.None;
        e.Handled = true;
    }

    private void WizardDropZone_Drop(object sender, DragEventArgs e)
    {
        if (TryGetDroppedFolder(e, out string folder))
            SetWizardAddon(folder);
        e.Handled = true;
    }

    private static bool TryGetDroppedFolder(DragEventArgs e, out string folder)
    {
        folder = string.Empty;
        if (!e.Data.GetDataPresent(DataFormats.FileDrop) || e.Data.GetData(DataFormats.FileDrop) is not string[] paths)
            return false;
        string? first = paths.FirstOrDefault(Directory.Exists);
        if (first == null)
            return false;
        folder = first;
        return true;
    }

    private void SetWizardAddon(string path)
    {
        string full = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        _wizard.AddonPath = full;
        _wizard.AddonLooksValid = LooksLikeAddonRoot(full);
        RefreshWizardOutput();
        _wizard.Inventory = null;
        _ = ScanWizardAddonAsync(full);
    }

    private void RefreshWizardOutput()
    {
        if (!_wizard.HasAddon)
        {
            _wizard.OutputPath = string.Empty;
            _wizard.OutputExists = false;
            return;
        }
        _wizard.OutputPath = GetWizardOutputPath(_wizard.AddonPath);
        _wizard.OutputExists = Directory.Exists(_wizard.OutputPath);
    }

    private string GetWizardOutputPath(string addonPath)
    {
        string suffix = string.IsNullOrWhiteSpace(_context.OptimizerSuffix) ? "_optimized" : _context.OptimizerSuffix.Trim();
        string parent = Path.GetDirectoryName(addonPath) ?? addonPath;
        return Path.Combine(parent, Path.GetFileName(addonPath) + suffix);
    }

    private async Task ScanWizardAddonAsync(string path)
    {
        _wizardScanCts?.Cancel();
        var cts = new CancellationTokenSource();
        _wizardScanCts = cts;
        _wizard.IsScanning = true;
        try
        {
            var inventory = await Task.Run(() => AddonInventory.Scan(path, cts.Token), cts.Token);
            if (cts.IsCancellationRequested || _wizard.AddonPath != path)
                return;
            _wizard.Inventory = inventory;
            if (_wizard.Goal == WizardGoal.Models && !inventory.HasModels)
                _wizard.Goal = WizardGoal.Full;
            if (_wizard.Goal == WizardGoal.Media && !inventory.HasMedia)
                _wizard.Goal = WizardGoal.Full;
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            if (_wizard.AddonPath == path)
                MessageBox.Show($"Não foi possível ler a pasta.\n\n{ex.Message}", "Otimizar", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        finally
        {
            if (ReferenceEquals(_wizardScanCts, cts))
                _wizard.IsScanning = false;
        }
    }

    // ---------------------------------------------------------------- step navigation

    private void WizardContinue_Click(object sender, RoutedEventArgs e)
    {
        if (!_wizard.CanContinue)
            return;
        _wizard.Step += 1;
        if (_wizard.Step == 3)
        {
            RefreshWizardOutput();
            _wizard.RunState = WizardRunState.Review;
        }
        WizardScroll.ScrollToTop();
    }

    private void WizardBack_Click(object sender, RoutedEventArgs e)
    {
        if (_wizardRunning)
            return;
        if (_wizard.Step == 3)
            _wizard.RunState = WizardRunState.Review;
        _wizard.Step -= 1;
        WizardScroll.ScrollToTop();
    }

    private void WizardRestart_Click(object sender, RoutedEventArgs e)
    {
        if (_wizardRunning)
            return;
        _wizard.RunState = WizardRunState.Review;
        _wizard.AddonPath = string.Empty;
        _wizard.Inventory = null;
        _wizard.OutputPath = string.Empty;
        _wizard.OutputExists = false;
        _wizard.Step = 1;
        _wizard.RaiseRecent();
        WizardScroll.ScrollToTop();
    }

    private void WizardRetry_Click(object sender, RoutedEventArgs e)
    {
        if (_wizardRunning)
            return;
        RefreshWizardOutput();
        _wizard.RunState = WizardRunState.Review;
    }

    private void WizardAdvanced_Click(object sender, RoutedEventArgs e)
    {
        if (_wizard.HasAddon && !_wizardRunning && _context.UnlockedUI)
            _context.AddonDirectoryPath = _wizard.AddonPath;
        ShowSection("advanced");
    }

    private void WizardTestInGame_Click(object sender, RoutedEventArgs e) => ShowSection("test");

    private void WizardOpenOutput_Click(object sender, RoutedEventArgs e) => OpenFolder(_wizardOutput, "Otimizar");

    private void WizardReport_Click(object sender, RoutedEventArgs e)
    {
        string? dir = _wizardReportDir;
        if (string.IsNullOrWhiteSpace(dir) || !Directory.Exists(dir))
            dir = ToolPaths.WorkRoot;
        OpenFolder(dir, "Otimizar");
    }

    private void WizardToggleLog_Click(object sender, RoutedEventArgs e)
    {
        _wizard.LogExpanded = !_wizard.LogExpanded;
        if (_wizard.LogExpanded)
            TextBox_WizardLog.ScrollToEnd();
    }

    private void WizardCancel_Click(object sender, RoutedEventArgs e) => RequestWizardCancel();

    private void RequestWizardCancel()
    {
        if (!_wizardRunning)
            return;
        _pipelineCancelRequested = true;
        _wizard.StageText = "Cancelando…";
        if (_pipelineStage == PipelineStage.Compress)
            _pipelineCompressSystem?.StopCompress();
        _pipelineCts?.Cancel();
    }

    // ---------------------------------------------------------------- run

    private async void WizardStart_Click(object sender, RoutedEventArgs e)
    {
        if (_wizardRunning || _pipelineRunning || _exactRunning || _fullRunning || !_context.UnlockedUI)
            return;

        string addon = _wizard.AddonPath;
        if (!Directory.Exists(addon))
        {
            ShowWizardFailure("Pasta não encontrada", "A pasta do addon não existe mais. Escolha outra.");
            return;
        }

        string output = GetWizardOutputPath(addon);
        if (string.Equals(Path.GetFullPath(output), Path.GetFullPath(addon), StringComparison.OrdinalIgnoreCase))
        {
            ShowWizardFailure("Pasta de saída inválida", "A pasta de saída seria a mesma do addon. Defina um sufixo em Avançado › Modelos.");
            return;
        }

        if (!EnsureToolsAvailable("Otimizar"))
            return;
        if (!File.Exists(ToolPaths.WorkerExePath))
        {
            ShowWizardFailure("Ferramentas não encontradas", "O worker do otimizador não foi encontrado.");
            return;
        }

        WizardGoal goal = _wizard.EffectiveGoal;
        SaveSettings();
        ApplyWizardSettings(_wizard.CurrentSettings());

        _wizardRunning = true;
        _wizard.Available = false;
        _pipelineRunning = true;
        _context.PipelineIsRunning = true;
        _context.UnlockedUI = false;
        _pipelineStage = PipelineStage.None;
        _pipelineOutputPath = null;
        _pipelineCancelRequested = false;
        _pipelineModelsOk = false;
        _pipelineCompressOk = false;
        _pipelineModelsExitCode = null;
        _pipelineCompressExitCode = null;
        _modelsLastErrorLine = null;
        _modelsWorkDir = null;
        _wizardOutput = output;
        _wizardReportDir = null;
        _pipelineCts?.Cancel();
        _pipelineCts = new CancellationTokenSource();

        await RunWizardAsync(addon, output, goal, _pipelineCts.Token);
    }

    private async Task RunWizardAsync(string addon, string output, WizardGoal goal, CancellationToken token)
    {
        var settings = _wizard.CurrentSettings();
        string[] stages = goal switch
        {
            WizardGoal.Models => new[] { "Modelos" },
            WizardGoal.Media => new[] { "Cópia do addon", "Texturas e sons" },
            _ => new[] { "Modelos", "Texturas e sons" }
        };
        _wizardStageWeights = goal switch
        {
            WizardGoal.Models => new[] { 1.0 },
            WizardGoal.Media => new[] { 0.15, 0.85 },
            _ => new[] { 0.5, 0.5 }
        };
        _wizard.SetStages(stages);
        _wizard.Percent = 0;
        _wizard.CurrentItem = string.Empty;
        _wizard.ElapsedText = string.Empty;
        _wizard.LogExpanded = false;
        TextBox_WizardLog.Clear();
        _wizard.RunState = WizardRunState.Running;
        _wizardClock.Restart();
        _wizardTimer?.Stop();
        _wizardTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        _wizardTimer.Tick += (_, _) => _wizard.ElapsedText = $"{FormatDuration(_wizardClock.Elapsed)} decorridos";
        _wizardTimer.Start();

        AppendWizardLog($"Addon: {addon}");
        AppendWizardLog($"Salvar em: {output}");
        AppendWizardLog($"Objetivo: {WizardProfiles.GoalTitle(goal)} · Perfil: {WizardProfiles.Title(_wizard.Profile)}");

        try
        {
            AddonInventory before = _wizard.Inventory ?? await Task.Run(() => AddonInventory.Scan(addon, token), token);
            int stage = 0;

            if (goal == WizardGoal.Media)
            {
                BeginWizardStage(stage, "Copiando o addon para a pasta nova");
                await CopyAddonForWizardAsync(addon, output, token);
                _pipelineOutputPath = output;
                CompleteWizardStage(stage, string.Empty);
                stage++;
            }
            else
            {
                BeginWizardStage(stage, $"Modelos · {settings.ModelsLine}");
                AppendWizardLog($"== Modelos · {settings.ModelsLine} ==");
                _pipelineStage = PipelineStage.Models;
                _modelsWorkDir = ToolPaths.GetWorkDir(addon, _context.OptimizerSuffix);
                _wizardReportDir = _modelsWorkDir;
                var options = BuildSourceAddonOptimizerOptions(addon, _modelsWorkDir, true);
                int exitCode = await _optimizerRunner.RunAsync(options, token);
                _pipelineModelsExitCode = exitCode;
                if (token.IsCancellationRequested || _pipelineCancelRequested)
                    throw new OperationCanceledException(token);
                if (exitCode != 0 || string.IsNullOrWhiteSpace(_pipelineOutputPath) || !Directory.Exists(_pipelineOutputPath))
                    throw new WizardStepException("Os modelos não foram otimizados",
                        string.IsNullOrWhiteSpace(_modelsLastErrorLine) ? $"O otimizador terminou com código {exitCode}." : _modelsLastErrorLine!);
                _pipelineModelsOk = true;
                output = _pipelineOutputPath!;
                _wizardOutput = output;
                CompleteWizardStage(stage, string.Empty);
                stage++;
            }

            // An optimized copy must not make the players of a server download the original addon too
            // (before the Lua minifier can join lines).
            var workshop = await Task.Run(() => WorkshopReferenceCleaner.Disable(output), token);
            foreach (var line in workshop.Lines)
                AppendWizardLog($"resource.AddWorkshop(\"{line.Id}\") desativado em {line.File}: os jogadores não baixam mais o addon original.");
            foreach (string file in workshop.LeftInFiles)
                AppendWizardLog($"[AVISO] {file} ainda chama resource.AddWorkshop no meio de outro código; confira antes de publicar.");

            if (goal != WizardGoal.Models)
            {
                BeginWizardStage(stage, $"Texturas · {settings.TexturesLine}");
                AppendWizardLog($"== Texturas e sons · {settings.TexturesLine} · {settings.SoundsLine} ==");
                _pipelineStage = PipelineStage.Compress;
                await StartCompressProcessAsync(output, cs =>
                {
                    _pipelineCompressSystem = cs;
                    cs.e_ProgressChanged += (filePath, index, total) =>
                        Dispatcher.BeginInvoke(() => WizardCompressProgress(output, filePath, index, total));
                }, unlockUiOnComplete: false);
                if (token.IsCancellationRequested || _pipelineCancelRequested)
                    throw new OperationCanceledException(token);
                if (!_pipelineCompressOk)
                    throw new WizardStepException("Texturas e sons não terminaram",
                        "Algum arquivo falhou. Abra os detalhes ou o relatório para ver qual.");
                CompleteWizardStage(stage, string.Empty);
            }

            _wizard.StageText = "Medindo o resultado…";
            AddonInventory after = await Task.Run(() => AddonInventory.Scan(output, CancellationToken.None));
            ShowWizardResult(before, after, goal, output);
            AddWizardRecent(addon);
            AppendWizardLog($"Pronto · {OptimizeWizardContext.FormatMiB(before.TotalBytes)} → {OptimizeWizardContext.FormatMiB(after.TotalBytes)} · {FormatDuration(_wizardClock.Elapsed)}");
        }
        catch (OperationCanceledException)
        {
            AppendWizardLog("Cancelado.");
            ShowWizardFailure("Cancelado", "A otimização foi interrompida. O addon original não foi alterado; a pasta de saída pode estar incompleta.");
        }
        catch (WizardStepException ex)
        {
            AppendWizardLog($"[ERRO] {ex.Title}: {ex.Message}");
            ShowWizardFailure(ex.Title, ex.Message);
        }
        catch (Exception ex)
        {
            AppendWizardLog($"[ERRO] {ex}");
            ShowWizardFailure("Não deu certo", ex.Message);
        }
        finally
        {
            _wizardTimer?.Stop();
            _wizardClock.Stop();
            RestoreWizardSettings();
            _wizardRunning = false;
            _pipelineRunning = false;
            _pipelineStage = PipelineStage.None;
            _pipelineCompressSystem = null;
            _context.PipelineIsRunning = false;
            _context.UnlockedUI = true;
            _wizard.Available = true;
            _wizard.CurrentItem = string.Empty;
        }
    }

    private void ShowWizardFailure(string title, string text)
    {
        _wizard.ErrorTitle = title;
        _wizard.ErrorText = text;
        _wizard.Step = 3;
        _wizard.RunState = WizardRunState.Failed;
    }

    private void ShowWizardResult(AddonInventory before, AddonInventory after, WizardGoal goal, string output)
    {
        var kinds = new List<(string Name, AddonFileKind Kind)>();
        if (goal != WizardGoal.Media) kinds.Add(("Modelos", AddonFileKind.Models));
        if (goal != WizardGoal.Models)
        {
            kinds.Add(("Texturas", AddonFileKind.Textures));
            kinds.Add(("Sons", AddonFileKind.Sounds));
        }
        kinds.Add(("Outros", AddonFileKind.Others));

        long max = Math.Max(1, kinds.Max(k => Math.Max(before[k.Kind].Bytes, after[k.Kind].Bytes)));
        _wizard.ResultRows.Clear();
        foreach (var (name, kind) in kinds)
        {
            long b = before[kind].Bytes;
            long a = after[kind].Bytes;
            if (b == 0 && a == 0)
                continue;
            _wizard.ResultRows.Add(new WizardResultRow(
                name,
                $"{OptimizeWizardContext.FormatMiB(b)} → {OptimizeWizardContext.FormatMiB(a)}",
                ReductionLabel(b, a),
                OptimizeWizardContext.BarTrackWidth * b / max,
                OptimizeWizardContext.BarTrackWidth * Math.Min(a, b) / max,
                false));
        }
        _wizard.ResultRows.Add(new WizardResultRow(
            "Total",
            $"{OptimizeWizardContext.FormatMiB(before.TotalBytes)} → {OptimizeWizardContext.FormatMiB(after.TotalBytes)}",
            ReductionLabel(before.TotalBytes, after.TotalBytes),
            0, 0, true));

        int total = OptimizeWizardContext.ReductionPercent(before.TotalBytes, after.TotalBytes);
        _wizard.ResultBefore = OptimizeWizardContext.FormatMiB(before.TotalBytes);
        _wizard.ResultAfter = OptimizeWizardContext.FormatMiB(after.TotalBytes);
        _wizard.ResultReduction = total > 0 ? $"{total}% menor" : total < 0 ? $"{-total}% maior" : "Mesmo tamanho";
        _wizard.ResultSubtitle = $"{Path.GetFileName(output)} · {FormatDuration(_wizardClock.Elapsed)}";
        _wizard.Percent = 100;
        _wizard.RunState = WizardRunState.Done;
    }

    private static string ReductionLabel(long before, long after)
    {
        if (after > before)
            return $"+{OptimizeWizardContext.FormatMiB(after - before)}"; // e.g. the Lua runtime the stream adds
        if (before <= 0) return "—";
        int pct = OptimizeWizardContext.ReductionPercent(before, after);
        return pct == 0 ? "0%" : $"−{pct}%";
    }

    private static string FormatDuration(TimeSpan elapsed)
    {
        if (elapsed.TotalHours >= 1)
            return $"{(int)elapsed.TotalHours} h {elapsed.Minutes} min";
        if (elapsed.TotalMinutes >= 1)
            return $"{(int)elapsed.TotalMinutes} min {elapsed.Seconds} s";
        return $"{Math.Max(0, elapsed.Seconds)} s";
    }

    // ---------------------------------------------------------------- progress

    private void BeginWizardStage(int index, string stageText)
    {
        _wizardStageIndex = index;
        _wizardStageFraction = 0;
        _wizard.CurrentItem = string.Empty;
        for (int i = 0; i < _wizard.Stages.Count; i++)
        {
            if (i < index) _wizard.Stages[i].State = WizardStageState.Done;
            else if (i == index) _wizard.Stages[i].State = WizardStageState.Running;
        }
        _wizard.StageText = $"Etapa {index + 1} de {_wizard.Stages.Count} · {stageText}";
        UpdateWizardPercent();
    }

    private void CompleteWizardStage(int index, string detail)
    {
        if (index >= _wizard.Stages.Count)
            return;
        _wizard.Stages[index].State = WizardStageState.Done;
        _wizard.Stages[index].Detail = detail;
        _wizardStageFraction = 1;
        UpdateWizardPercent();
    }

    private void SetWizardStageFraction(double fraction, string detail)
    {
        if (double.IsNaN(fraction))
            return;
        fraction = Math.Clamp(fraction, 0, 1);
        if (fraction > _wizardStageFraction)
            _wizardStageFraction = fraction;
        if (_wizardStageIndex < _wizard.Stages.Count && !string.IsNullOrEmpty(detail))
            _wizard.Stages[_wizardStageIndex].Detail = detail;
        UpdateWizardPercent();
    }

    private void UpdateWizardPercent()
    {
        double done = 0;
        for (int i = 0; i < _wizardStageWeights.Length && i < _wizardStageIndex; i++)
            done += _wizardStageWeights[i];
        double weight = _wizardStageIndex < _wizardStageWeights.Length ? _wizardStageWeights[_wizardStageIndex] : 0;
        _wizard.Percent = (done + weight * _wizardStageFraction) * 100.0;
    }

    private void WizardModelsProgress(SourceAddonOptimizerProgressUpdate update)
    {
        if (!_wizardRunning || _pipelineStage != PipelineStage.Models)
            return;
        // modscreen: step 1 copy, step 2 simplify (one item per model), step 3 packaging
        int stepTotal = Math.Max(1, _modelsStepTotal);
        int step = Math.Clamp(_modelsStepIndex, 1, stepTotal);
        double itemFraction = 0;
        string detail = string.Empty;
        if (update.ItemIndex.HasValue && update.ItemTotal.HasValue && update.ItemTotal.Value > 0)
        {
            itemFraction = (double)update.ItemIndex.Value / update.ItemTotal.Value;
            detail = $"{update.ItemIndex.Value} de {update.ItemTotal.Value}";
        }
        if (!string.IsNullOrWhiteSpace(update.ItemPath))
            _wizard.CurrentItem = update.ItemPath!.Replace('\\', '/');
        else if (update.IsPackaging && !string.IsNullOrWhiteSpace(update.Phase))
            _wizard.CurrentItem = update.Phase!;
        SetWizardStageFraction((step - 1 + itemFraction) / stepTotal, detail);
    }

    private void WizardCompressProgress(string root, string filePath, int index, int total)
    {
        if (!_wizardRunning || _pipelineStage != PipelineStage.Compress || total <= 0)
            return;
        string rel = filePath;
        try { rel = Path.GetRelativePath(root, filePath).Replace('\\', '/'); }
        catch { }
        _wizard.CurrentItem = rel;
        SetWizardStageFraction((double)index / total, $"{index} de {total}");
        if (index == total || index % 25 == 0)
            AppendWizardLog($"Texturas e sons {index}/{total} · {rel}");
    }

    private void AppendWizardLog(string line)
    {
        if (TextBox_WizardLog.Text.Length > WizardLogMaxChars)
            TextBox_WizardLog.Text = TextBox_WizardLog.Text[^(WizardLogMaxChars / 2)..];
        TextBox_WizardLog.AppendText($"{DateTime.Now:HH:mm:ss}  {line}{Environment.NewLine}");
        if (_wizard.LogExpanded)
            TextBox_WizardLog.ScrollToEnd();
    }

    private Task CopyAddonForWizardAsync(string source, string destination, CancellationToken token)
    {
        var progress = new Progress<(int Index, int Total, string Rel)>(p =>
        {
            _wizard.CurrentItem = p.Rel;
            SetWizardStageFraction((double)p.Index / Math.Max(1, p.Total), $"{p.Index} de {p.Total}");
        });
        IProgress<(int, int, string)> report = progress;
        return Task.Run(() =>
        {
            string src = Path.GetFullPath(source);
            string dst = Path.GetFullPath(destination);
            if (dst.StartsWith(src.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase)
                || string.Equals(src, dst, StringComparison.OrdinalIgnoreCase))
                throw new WizardStepException("Pasta de saída inválida", "A pasta de saída não pode ficar dentro do addon.");
            if (Directory.Exists(dst))
                Directory.Delete(dst, recursive: true);
            var options = new EnumerationOptions { RecurseSubdirectories = true, AttributesToSkip = FileAttributes.ReparsePoint };
            var files = Directory.EnumerateFiles(src, "*", options).ToList();
            for (int i = 0; i < files.Count; i++)
            {
                token.ThrowIfCancellationRequested();
                string rel = Path.GetRelativePath(src, files[i]);
                string target = Path.Combine(dst, rel);
                Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                File.Copy(files[i], target, overwrite: true);
                if (i % 20 == 0 || i == files.Count - 1)
                    report.Report((i + 1, files.Count, rel.Replace('\\', '/')));
            }
        }, token);
    }

    // ---------------------------------------------------------------- profile settings

    private void ApplyWizardSettings(WizardSettings s)
    {
        _wizardSnapshot = WizardSettingsSnapshot.Capture(_context);
        _wizardSettingsApplied = true;
        _context.OptimizerModeIndex = WizardProfiles.ModelsScreenModeIndex;
        _context.ModelsScreenProfileIndex = s.ModelsScreenProfileIndex;
        _context.OptimizerOverwrite = true; // the review step says the output folder is replaced
        _context.CompressModeIndex = WizardProfiles.CompressScreenModeIndex;
        _context.CompressScreenProfileIndex = s.CompressScreenProfileIndex;
        _context.CompressVTF = s.Textures;
        _context.CompressWAV = s.IncludeWav;
        _context.CompressMP3 = false;
        _context.CompressOGG = false;
        _context.CompressPNG = false;
        _context.CompressJPG = false;
        _context.CompressLUA = s.Lua;
        _context.WavOptimizationIndex = s.WavOptimizationIndex;
        _context.WavRateListIndex = s.WavRateIndex;
        _context.WavChannelsIndex = s.WavChannelsIndex;
        _context.WavCodecIndex = s.WavCodecIndex;
        _context.AudioLoopSafe = true;
    }

    private void RestoreWizardSettings()
    {
        if (!_wizardSettingsApplied)
            return;
        _wizardSnapshot?.Restore(_context);
        _wizardSnapshot = null;
        _wizardSettingsApplied = false;
    }

    private sealed record WizardSettingsSnapshot(
        int OptimizerModeIndex, int ModelsScreenProfileIndex, bool OptimizerOverwrite,
        int CompressModeIndex, int CompressScreenProfileIndex,
        bool Vtf, bool Wav, bool Mp3, bool Ogg, bool Png, bool Jpg, bool Lua,
        int WavOptimizationIndex, int WavRateIndex, int WavChannelsIndex, int WavCodecIndex, bool AudioLoopSafe)
    {
        internal static WizardSettingsSnapshot Capture(MainWindowContext c) => new(
            c.OptimizerModeIndex, c.ModelsScreenProfileIndex, c.OptimizerOverwrite,
            c.CompressModeIndex, c.CompressScreenProfileIndex,
            c.CompressVTF, c.CompressWAV, c.CompressMP3, c.CompressOGG, c.CompressPNG, c.CompressJPG, c.CompressLUA,
            c.WavOptimizationIndex, c.WavRateListIndex, c.WavChannelsIndex, c.WavCodecIndex, c.AudioLoopSafe);

        internal void Restore(MainWindowContext c)
        {
            c.OptimizerModeIndex = OptimizerModeIndex;
            c.ModelsScreenProfileIndex = ModelsScreenProfileIndex;
            c.OptimizerOverwrite = OptimizerOverwrite;
            c.CompressModeIndex = CompressModeIndex;
            c.CompressScreenProfileIndex = CompressScreenProfileIndex;
            c.CompressVTF = Vtf;
            c.CompressWAV = Wav;
            c.CompressMP3 = Mp3;
            c.CompressOGG = Ogg;
            c.CompressPNG = Png;
            c.CompressJPG = Jpg;
            c.CompressLUA = Lua;
            c.WavOptimizationIndex = WavOptimizationIndex;
            c.WavRateListIndex = WavRateIndex;
            c.WavChannelsIndex = WavChannelsIndex;
            c.WavCodecIndex = WavCodecIndex;
            c.AudioLoopSafe = AudioLoopSafe;
        }
    }

    private sealed class WizardStepException : Exception
    {
        public WizardStepException(string title, string message) : base(message) => Title = title;
        public string Title { get; }
    }

    // ---------------------------------------------------------------- recent addons

    private void LoadWizardRecent()
    {
        _wizard.Recent.Clear();
        try
        {
            if (!File.Exists(WizardRecentPath))
                return;
            var paths = JsonSerializer.Deserialize<List<string>>(File.ReadAllText(WizardRecentPath)) ?? new List<string>();
            foreach (string path in paths.Where(Directory.Exists).Take(WizardRecentMax))
                _wizard.Recent.Add(new WizardRecentAddon(path, Path.GetFileName(path), Path.GetDirectoryName(path) ?? path));
        }
        catch
        {
            // a broken recent list only loses the shortcuts
        }
        finally
        {
            _wizard.RaiseRecent();
        }
    }

    private void AddWizardRecent(string path)
    {
        try
        {
            var paths = _wizard.Recent.Select(r => r.Path)
                .Where(p => !string.Equals(p, path, StringComparison.OrdinalIgnoreCase))
                .Prepend(path)
                .Take(WizardRecentMax)
                .ToList();
            Directory.CreateDirectory(ToolPaths.AppDataRoot);
            File.WriteAllText(WizardRecentPath, JsonSerializer.Serialize(paths));
        }
        catch
        {
        }
        LoadWizardRecent();
    }
}
