using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Companion;
using Microsoft.Win32;
using Ookii.Dialogs.Wpf;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace GmodAddonCompressor
{
    public partial class MainWindow
    {
        private const string CompanionResourceName = "GmodAddonCompressor.Resources.SourceAddonOptimizerCompanion.zip";
        private readonly CompanionContext _companionContext = new();
        private readonly CompanionResultService _companionResultService = new();
        private CancellationTokenSource? _companionCts;
        private string[] _companionProfileIndexes = Array.Empty<string>();
        private string _lastMaximumRuntimeSummaryText = string.Empty;
        private string? _lastCompanionValidationPlanRoot;

        private void InitializeCompanion()
        {
            CompanionTabRoot.DataContext = _companionContext;
            ModelsCompanionProfileCard.DataContext = _companionContext;
            PipelineCompanionProfileCard.DataContext = _companionContext;
            _companionContext.SetOptimizerMode(_context.OptimizerModeIsMaximum);
            _context.PropertyChanged += MainContext_PropertyChangedForCompanion;
            Button_CompanionBrowseGame.Click += Button_CompanionBrowseGame_Click;
            Button_CompanionDetectGame.Click += Button_CompanionDetectGame_Click;
            Button_CompanionInstall.Click += Button_CompanionInstall_Click;
            Button_CompanionUpdate.Click += Button_CompanionUpdate_Click;
            Button_CompanionRemove.Click += Button_CompanionRemove_Click;
            Button_CompanionOpenData.Click += Button_CompanionOpenData_Click;
            Button_CompanionImportProfile.Click += Button_CompanionImportProfile_Click;
            Button_CompanionGeneratePlan.Click += Button_CompanionGeneratePlan_Click;
            Button_CompanionCopyPlan.Click += Button_CompanionCopyPlan_Click;
            Button_CompanionOpenPlan.Click += Button_CompanionOpenPlan_Click;
            Button_CompanionImportResults.Click += Button_CompanionImportResults_Click;
            Button_CompanionCopyResults.Click += Button_CompanionCopyResults_Click;
            Button_CompanionExportResults.Click += Button_CompanionExportResults_Click;
            Button_CompanionOpenScreenshot.Click += Button_CompanionOpenScreenshot_Click;
            Button_CompanionCancel.Click += Button_CompanionCancel_Click;
            Button_CompanionCopyPlan.IsEnabled = false;
            Button_CompanionOpenPlan.IsEnabled = false;
            Closed += (_, _) =>
            {
                _context.PropertyChanged -= MainContext_PropertyChangedForCompanion;
                CancelCompanionOperation();
            };
        }

        private void LoadCompanionSettings()
        {
            _companionContext.SetOptimizerMode(_context.OptimizerModeIsMaximum);
            _companionProfileIndexes = Array.Empty<string>();
            _companionContext.ResetImportedProfile(_settings.CompanionLastProfilePath);
            _ = RefreshCompanionStatusAsync(_settings.CompanionGarrysModRoot, persistDetectedRoot: false);
        }

        private void SaveCompanionSettings(AppSettingsModel settings)
        {
            settings.CompanionGarrysModRoot = string.IsNullOrWhiteSpace(_companionContext.GarrysModRoot)
                ? null
                : _companionContext.GarrysModRoot;
            settings.CompanionLastProfilePath = string.IsNullOrWhiteSpace(_companionContext.LastProfilePath)
                ? null
                : _companionContext.LastProfilePath;
        }

        private async void Button_CompanionBrowseGame_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new VistaFolderBrowserDialog
            {
                Description = "Escolha a pasta do Garry's Mod (a que tem o gmod.exe).",
                UseDescriptionForTitle = true,
                SelectedPath = Directory.Exists(_companionContext.GarrysModRoot)
                    ? _companionContext.GarrysModRoot
                    : string.Empty,
            };
            if (dialog.ShowDialog(this).GetValueOrDefault())
                await RefreshCompanionStatusAsync(dialog.SelectedPath, persistDetectedRoot: true);
        }

        private async void Button_CompanionDetectGame_Click(object sender, RoutedEventArgs e) =>
            await RefreshCompanionStatusAsync(null, persistDetectedRoot: true);

        private async void Button_CompanionInstall_Click(object sender, RoutedEventArgs e) =>
            await RunCompanionInstallAsync(isUpdate: false);

        private async void Button_CompanionUpdate_Click(object sender, RoutedEventArgs e) =>
            await RunCompanionInstallAsync(isUpdate: true);

        private async void Button_CompanionRemove_Click(object sender, RoutedEventArgs e)
        {
            if (!_companionContext.CanRemove)
                return;
            MessageBoxResult confirmation = MessageBox.Show(
                "Remover o addon de teste? Só os arquivos instalados por ele são apagados; perfis e resultados em garrysmod/data ficam.",
                "Remover addon de teste",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning
            );
            if (confirmation != MessageBoxResult.Yes)
                return;

            CancellationTokenSource cts = BeginCompanionOperation("Removendo o addon de teste…", 15);
            try
            {
                string root = RequireCompanionGameRoot();
                CompanionUninstallResult result = await Task.Run(
                    () => CompanionPackageInstaller.Uninstall(root, TimeSpan.FromSeconds(30)),
                    cts.Token
                );
                cts.Token.ThrowIfCancellationRequested();
                _companionContext.ProgressValue = 80;
                byte[] package = await Task.Run(ReadEmbeddedCompanionPackage, cts.Token);
                CompanionInstallStatus status = await Task.Run(
                    () => CompanionPackageInstaller.Inspect(package, root),
                    cts.Token
                );
                _companionContext.ApplyInstallStatus(status);
                _companionContext.OperationStatusText = string.IsNullOrWhiteSpace(result.Warning)
                    ? "Addon de teste removido. Os dados do jogo foram mantidos."
                    : "Addon de teste removido; arquivos desconhecidos foram mantidos. " + result.Warning;
                _companionContext.ProgressValue = 100;
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Operação cancelada.";
            }
            catch (Exception ex)
            {
                _companionContext.OperationStatusText = "Falha ao remover: " + ex.Message;
            }
            finally
            {
                EndCompanionOperation(cts);
            }
        }

        private void Button_CompanionOpenData_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                string root = RequireCompanionGameRoot();
                string dataRoot = Path.GetFullPath(Path.Combine(root, "garrysmod", "data", "sourceaddonoptimizer"));
                Directory.CreateDirectory(dataRoot);
                OpenFolder(dataRoot, "Testar no jogo");
            }
            catch (Exception ex)
            {
                _companionContext.OperationStatusText = "Não deu para abrir a pasta de dados: " + ex.Message;
            }
        }

        private async void Button_CompanionImportProfile_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Escolha o run-index.json do perfil",
                Filter = "Índice do perfil|run-index.json|Arquivos JSON|*.json",
                CheckFileExists = true,
                Multiselect = true,
            };
            string? priorDirectory = Path.GetDirectoryName(_companionContext.LastProfilePath);
            if (!string.IsNullOrWhiteSpace(priorDirectory) && Directory.Exists(priorDirectory))
                dialog.InitialDirectory = priorDirectory;
            if (!dialog.ShowDialog(this).GetValueOrDefault())
                return;

            _companionProfileIndexes = dialog.FileNames
                .Select(Path.GetFullPath)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();
            _companionContext.ResetImportedProfile(_companionProfileIndexes[0]);
            SaveSettings();
            await ImportCompanionProfilesAsync(
                _companionProfileIndexes,
                _context.AddonDirectoryPath,
                persistSelectedPath: true
            );
        }

        private void MainContext_PropertyChangedForCompanion(object? sender, PropertyChangedEventArgs e)
        {
            if (e.PropertyName == nameof(MainWindowContext.OptimizerModeIndex))
            {
                _companionContext.SetOptimizerMode(_context.OptimizerModeIsMaximum);
                return;
            }
            if (e.PropertyName != nameof(MainWindowContext.AddonDirectoryPath))
                return;

            _companionContext.ClearForAddonChange();
            ClearCompanionValidationPlanResult();
            string[] indexes = _companionProfileIndexes.ToArray();
            string addonRoot = _context.AddonDirectoryPath;
            if (indexes.Length > 0 && Directory.Exists(addonRoot))
                _ = ImportCompanionProfilesAsync(indexes, addonRoot, persistSelectedPath: false);
        }

        private async Task ImportCompanionProfilesAsync(
            string[] profileIndexes,
            string addonRoot,
            bool persistSelectedPath)
        {
            if (profileIndexes.Length == 0)
                return;
            _companionContext.ResetImportedProfile(profileIndexes[0]);
            if (!Directory.Exists(addonRoot))
            {
                _companionContext.ProfileSummaryText =
                    "Escolha a pasta do addon antes de importar. O perfil fica desligado.";
                _companionContext.OperationStatusText = "Perfil não importado: falta a pasta do addon.";
                return;
            }

            CancellationTokenSource cts = BeginCompanionOperation("Validando o perfil e conferindo com o addon…", 10);
            try
            {
                string[] capturedProfiles = profileIndexes.ToArray();
                string capturedAddon = Path.GetFullPath(addonRoot);
                CompanionProfileImportResult result = await Task.Run(
                    () => CompanionProfileService.Import(
                        capturedProfiles,
                        capturedAddon,
                        DateTimeOffset.UtcNow,
                        cts.Token
                    ),
                    cts.Token
                );
                cts.Token.ThrowIfCancellationRequested();
                if (!Path.GetFullPath(_context.AddonDirectoryPath).Equals(capturedAddon, StringComparison.OrdinalIgnoreCase))
                    return;
                _companionContext.ApplyImportedProfile(result, capturedProfiles[0]);
                _companionContext.ProgressValue = 100;
                _companionContext.OperationStatusText = result.IsEligible
                    ? "Perfil validado. Para usar, marque a opção no modo Maximum (Avançado › Modelos)."
                    : "Perfil validado, mas não serve para o modo Maximum: " + result.CompatibilityText;
                if (persistSelectedPath)
                    SaveSettings();
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Importação cancelada; o perfil fica desligado.";
            }
            catch (Exception ex)
            {
                _companionContext.ResetImportedProfile(profileIndexes[0]);
                _companionContext.ProfileSummaryText = "Perfil recusado: " + ex.Message;
                _companionContext.OperationStatusText = "Falha ao importar o perfil; nada foi alterado.";
            }
            finally
            {
                EndCompanionOperation(cts);
            }
        }

        private async Task<string?> PrepareMaximumRuntimeProfileSnapshotAsync(
            string addonRoot,
            string workDir,
            CancellationToken cancellationToken)
        {
            if (!_context.OptimizerModeIsMaximum
                || !_companionContext.UseWithMaximumV2
                || !_companionContext.CanUseWithMaximumV2
                || _companionProfileIndexes.Length == 0)
                return null;

            string[] capturedProfiles = _companionProfileIndexes.ToArray();
            string capturedAddon = Path.GetFullPath(addonRoot);
            string workParent = Path.GetDirectoryName(Path.GetFullPath(workDir))
                ?? throw new InvalidOperationException("The Models work directory has no parent.");
            string snapshotWork = Path.Combine(
                workParent,
                "runtime-profile-inputs",
                Path.GetFileName(Path.GetFullPath(workDir))
            );
            try
            {
                CompanionRuntimeProfileSnapshot snapshot = await Task.Run(
                    () => CompanionRuntimeProfileSnapshotService.Create(
                        capturedProfiles,
                        capturedAddon,
                        snapshotWork,
                        DateTimeOffset.UtcNow,
                        cancellationToken
                    ),
                    cancellationToken
                );
                cancellationToken.ThrowIfCancellationRequested();
                bool stillSelected = _context.OptimizerModeIsMaximum
                    && _companionContext.UseWithMaximumV2
                    && _companionContext.CanUseWithMaximumV2
                    && Path.GetFullPath(_context.AddonDirectoryPath).Equals(
                        capturedAddon,
                        StringComparison.OrdinalIgnoreCase
                    )
                    && capturedProfiles.SequenceEqual(_companionProfileIndexes, StringComparer.OrdinalIgnoreCase);
                if (!stillSelected)
                    return null;
                _companionContext.OperationStatusText =
                    $"Perfil conferido para o modo Maximum ({snapshot.ImportResult.MatchedModelCount} modelos, {snapshot.ImportResult.MatchedMaterialCount} materiais).";
                return snapshot.BundleRoot;
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception ex)
            {
                _companionContext.UseWithMaximumV2 = false;
                _companionContext.ProfileSummaryText =
                    "O perfil foi recusado no início e não foi usado; o modo Maximum segue normal. "
                    + ex.Message;
                _companionContext.OperationStatusText =
                    "Falha ao preparar o perfil; seguindo sem ele.";
                return null;
            }
        }

        private async Task UpdateMaximumRuntimeSummaryAsync(
            string? workDir,
            bool runtimeProfileRequested,
            bool optimizationSucceeded,
            CancellationToken cancellationToken)
        {
            _lastMaximumRuntimeSummaryText = string.Empty;
            if (string.IsNullOrWhiteSpace(workDir))
                return;
            string reportPath = Path.Combine(workDir, "logs", "maximum_adaptive_report.json");
            try
            {
                CompanionMaximumRuntimeSummary summary = await Task.Run(
                    () => CompanionMaximumReportReader.Read(reportPath),
                    cancellationToken
                );
                cancellationToken.ThrowIfCancellationRequested();
                _lastMaximumRuntimeSummaryText = summary.DisplayText;
                _companionContext.SetOptimizationResultAvailable(
                    optimizationSucceeded
                    && !string.IsNullOrWhiteSpace(_modelsOutputPath)
                    && Directory.Exists(_modelsOutputPath)
                );
                _companionContext.ProfileSummaryText = summary.DisplayText;
                _companionContext.OperationStatusText = summary.EvidenceApplied
                    ? "Maximum concluído usando o perfil do jogo."
                    : runtimeProfileRequested
                        ? "Maximum concluído, mas o perfil do jogo não foi aplicado."
                        : "Maximum concluído sem perfil do jogo.";
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception ex)
            {
                _lastMaximumRuntimeSummaryText = runtimeProfileRequested
                    ? "O perfil foi pedido, mas o relatório final do Maximum não pôde ser conferido."
                    : string.Empty;
                if (runtimeProfileRequested)
                {
                    _companionContext.ProfileSummaryText = _lastMaximumRuntimeSummaryText + " " + ex.Message;
                    _companionContext.OperationStatusText =
                        "Maximum concluído; o perfil não conta como usado porque a conferência falhou.";
                }
            }
        }

        private async void Button_CompanionGeneratePlan_Click(object sender, RoutedEventArgs e)
        {
            if (!_companionContext.CanGeneratePlan)
                return;
            string? optimizedRoot = _modelsOutputPath;
            string? workDir = _modelsWorkDir;
            if (string.IsNullOrWhiteSpace(optimizedRoot)
                || !Directory.Exists(optimizedRoot)
                || string.IsNullOrWhiteSpace(workDir))
            {
                _companionContext.OperationStatusText = "Para gerar o plano, otimize antes os modelos no modo Maximum (Avançado › Modelos).";
                return;
            }
            string reportPath = Path.Combine(workDir, "logs", "maximum_adaptive_report.json");
            string? destinationParent = SelectValidationPlanDestination();
            if (destinationParent == null)
                return;

            CancellationTokenSource cts = BeginCompanionOperation("Gerando o plano de teste…", 10);
            try
            {
                string capturedOutput = Path.GetFullPath(optimizedRoot);
                string capturedReport = Path.GetFullPath(reportPath);
                string capturedDestination = Path.GetFullPath(destinationParent);
                string capturedOriginal = Path.GetFullPath(_context.AddonDirectoryPath);
                CompanionValidationPlanResult result = await Task.Run(
                    () => CompanionValidationPlanService.Generate(
                        capturedOutput,
                        capturedReport,
                        capturedDestination,
                        DateTimeOffset.UtcNow,
                        capturedOriginal
                    ),
                    cts.Token
                );
                cts.Token.ThrowIfCancellationRequested();
                _lastCompanionValidationPlanRoot = result.BundleRoot;
                Button_CompanionCopyPlan.IsEnabled = true;
                Button_CompanionOpenPlan.IsEnabled = true;
                _companionContext.ProgressValue = 100;
                _companionContext.ValidationSummaryText =
                    $"Plano pronto: {result.ModelCount} modelos, {result.Tests.Count} testes, {result.ScreenshotCount} capturas. "
                    + $"Execução {result.RunId:D}; SHA-256 {result.IndexSha256}. "
                    + $"Limites: {250} modelos / {1000} capturas / {result.MaximumOrdinaryViews} vistas / {result.ScreenshotRetries} nova tentativa. "
                    + $"Cortados: testes {result.TruncatedTests}, modelos {result.TruncatedModels}, capturas {result.TruncatedScreenshots}. "
                    + "DX80 fica de fora.";
                _companionContext.OperationStatusText = "Plano de teste gerado.";
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Geração do plano cancelada.";
            }
            catch (Exception ex)
            {
                _companionContext.OperationStatusText = "Falha ao gerar o plano: " + ex.Message;
            }
            finally
            {
                EndCompanionOperation(cts);
            }
        }

        private async void Button_CompanionCopyPlan_Click(object sender, RoutedEventArgs e)
        {
            if (string.IsNullOrWhiteSpace(_lastCompanionValidationPlanRoot)
                || !Directory.Exists(_lastCompanionValidationPlanRoot))
                return;
            var dialog = new VistaFolderBrowserDialog
            {
                Description = "Escolha a pasta validation_inbox (ou outra) para receber uma cópia do plano.",
                UseDescriptionForTitle = true,
            };
            if (!dialog.ShowDialog(this).GetValueOrDefault())
                return;
            CancellationTokenSource cts = BeginCompanionOperation("Copiando e conferindo o plano…", 20);
            try
            {
                string source = _lastCompanionValidationPlanRoot;
                string destination = Path.GetFullPath(dialog.SelectedPath);
                string copied = await Task.Run(
                    () => CompanionValidationPlanService.CopyBundle(source, destination),
                    cts.Token
                );
                cts.Token.ThrowIfCancellationRequested();
                _companionContext.ProgressValue = 100;
                _companionContext.OperationStatusText = "Cópia do plano criada: " + copied;
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Cópia do plano cancelada.";
            }
            catch (Exception ex)
            {
                _companionContext.OperationStatusText = "Falha ao copiar o plano: " + ex.Message;
            }
            finally
            {
                EndCompanionOperation(cts);
            }
        }

        private void Button_CompanionOpenPlan_Click(object sender, RoutedEventArgs e) =>
            OpenFolder(_lastCompanionValidationPlanRoot, "Plano de teste");

        private void ClearCompanionValidationPlanResult()
        {
            _lastCompanionValidationPlanRoot = null;
            Button_CompanionCopyPlan.IsEnabled = false;
            Button_CompanionOpenPlan.IsEnabled = false;
        }

        private string? SelectValidationPlanDestination()
        {
            string? gameRoot = CompanionPathPolicy.DetectGarrysModRoot(_companionContext.GarrysModRoot);
            if (!string.IsNullOrWhiteSpace(gameRoot))
            {
                MessageBoxResult choice = MessageBox.Show(
                    "Gerar o plano direto na pasta do Garry's Mod?\n\n"
                    + Path.Combine(gameRoot, "garrysmod", "data", "sourceaddonoptimizer", "validation_inbox")
                    + "\n\nEscolha Não para salvar em outra pasta.",
                    "Plano de teste",
                    MessageBoxButton.YesNoCancel,
                    MessageBoxImage.Question
                );
                if (choice == MessageBoxResult.Cancel)
                    return null;
                if (choice == MessageBoxResult.Yes)
                {
                    string inbox = Path.Combine(
                        gameRoot,
                        "garrysmod",
                        "data",
                        "sourceaddonoptimizer",
                        "validation_inbox"
                    );
                    Directory.CreateDirectory(inbox);
                    return inbox;
                }
            }
            var dialog = new VistaFolderBrowserDialog
            {
                Description = "Escolha a pasta onde salvar o plano de teste.",
                UseDescriptionForTitle = true,
            };
            return dialog.ShowDialog(this).GetValueOrDefault() ? dialog.SelectedPath : null;
        }

        private async void Button_CompanionImportResults_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Escolha os run-index.json dos resultados",
                Filter = "Índice de resultados|run-index.json|Arquivos JSON|*.json",
                CheckFileExists = true,
                Multiselect = true,
            };
            if (!dialog.ShowDialog(this).GetValueOrDefault())
                return;
            CancellationTokenSource cts = BeginCompanionOperation("Conferindo os resultados e as capturas…", 10);
            var rejected = new List<(string Path, string Error)>();
            int imported = 0;
            try
            {
                string[] selected = dialog.FileNames.Select(Path.GetFullPath).ToArray();
                for (int index = 0; index < selected.Length; index++)
                {
                    cts.Token.ThrowIfCancellationRequested();
                    string path = selected[index];
                    try
                    {
                        await Task.Run(() => _companionResultService.Import(path), cts.Token);
                        imported++;
                    }
                    catch (Exception ex) when (ex is InvalidOperationException
                        || ex is IOException
                        || ex is UnauthorizedAccessException
                        || ex is ArgumentException
                        || ex is NotSupportedException)
                    {
                        rejected.Add((path, ex.Message));
                    }
                    _companionContext.ProgressValue = 10 + (int)Math.Round(85d * (index + 1) / selected.Length);
                }
                _companionContext.ApplyValidationReports(_companionResultService.Reports);
                foreach ((string path, string error) in rejected)
                    _companionContext.ApplyValidationImportError(path, error);
                _companionContext.ProgressValue = 100;
                _companionContext.OperationStatusText = rejected.Count == 0
                    ? $"{imported} resultado(s) importado(s)."
                    : $"{imported} importado(s), {rejected.Count} recusado(s). Os recusados aparecem em Detalhes.";
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Importação de resultados cancelada.";
            }
            finally
            {
                EndCompanionOperation(cts);
            }
        }

        private void Button_CompanionCopyResults_Click(object sender, RoutedEventArgs e)
        {
            string summary = _companionContext.BuildValidationExportText();
            if (string.IsNullOrWhiteSpace(summary))
                return;
            Clipboard.SetText(summary);
            _companionContext.OperationStatusText = "Resumo copiado.";
        }

        private void Button_CompanionExportResults_Click(object sender, RoutedEventArgs e)
        {
            string summary = _companionContext.BuildValidationExportText();
            if (string.IsNullOrWhiteSpace(summary))
                return;
            var dialog = new SaveFileDialog
            {
                Title = "Exportar resumo dos testes",
                Filter = "Resumo em texto|*.txt",
                FileName = "companion-validation-summary.txt",
                AddExtension = true,
                OverwritePrompt = true,
            };
            if (!dialog.ShowDialog(this).GetValueOrDefault())
                return;
            File.WriteAllText(dialog.FileName, summary + Environment.NewLine, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            _companionContext.OperationStatusText = "Resumo exportado: " + dialog.FileName;
        }

        private void Button_CompanionOpenScreenshot_Click(object sender, RoutedEventArgs e)
        {
            string? path = _companionContext.SelectedValidationResource?.PrimaryScreenshotPath;
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
                return;
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
        }

        private void Button_CompanionCancel_Click(object sender, RoutedEventArgs e)
        {
            _companionContext.OperationStatusText = "Cancelando…";
            CancelCompanionOperation();
        }

        private async Task RunCompanionInstallAsync(bool isUpdate)
        {
            if (isUpdate ? !_companionContext.CanUpdate : !_companionContext.CanInstall)
                return;
            CancellationTokenSource cts = BeginCompanionOperation(
                isUpdate ? "Atualizando o addon de teste…" : "Instalando o addon de teste…",
                10
            );
            try
            {
                string root = RequireCompanionGameRoot();
                byte[] package = await Task.Run(ReadEmbeddedCompanionPackage, cts.Token);
                _companionContext.ProgressValue = 35;
                CompanionInstallResult result = await Task.Run(
                    () => CompanionPackageInstaller.Install(package, root, TimeSpan.FromSeconds(30)),
                    cts.Token
                );
                cts.Token.ThrowIfCancellationRequested();
                _companionContext.ProgressValue = 85;
                CompanionInstallStatus status = await Task.Run(
                    () => CompanionPackageInstaller.Inspect(package, root),
                    cts.Token
                );
                _companionContext.ApplyInstallStatus(status);
                _companionContext.OperationStatusText = result.Disposition switch
                {
                    CompanionInstallDisposition.Installed => "Addon de teste instalado. Se o Garry's Mod estiver aberto, reinicie.",
                    CompanionInstallDisposition.Updated => "Addon de teste atualizado. Se o Garry's Mod estiver aberto, reinicie.",
                    _ => "O addon de teste já está instalado.",
                };
                _companionContext.ProgressValue = 100;
                SaveSettings();
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Operação cancelada.";
            }
            catch (Exception ex)
            {
                _companionContext.OperationStatusText = (isUpdate ? "Falha ao atualizar: " : "Falha ao instalar: ") + ex.Message;
            }
            finally
            {
                EndCompanionOperation(cts);
            }
        }

        private async Task RefreshCompanionStatusAsync(string? explicitRoot, bool persistDetectedRoot)
        {
            CancellationTokenSource cts = BeginCompanionOperation("Procurando o Garry's Mod…", 5);
            try
            {
                string? detected = await Task.Run(
                    () => CompanionPathPolicy.DetectGarrysModRoot(explicitRoot),
                    cts.Token
                );
                cts.Token.ThrowIfCancellationRequested();
                if (string.IsNullOrWhiteSpace(detected))
                {
                    _companionContext.GarrysModRoot = string.Empty;
                    _companionContext.SetInstallationUnavailable(
                        "Não instalado",
                        "Garry's Mod não encontrado. Escolha a pasta que tem o gmod.exe."
                    );
                    _companionContext.OperationStatusText = "Garry's Mod não encontrado.";
                    return;
                }

                _companionContext.GarrysModRoot = detected;
                _companionContext.ProgressValue = 35;
                byte[] package = await Task.Run(ReadEmbeddedCompanionPackage, cts.Token);
                CompanionInstallStatus status = await Task.Run(
                    () => CompanionPackageInstaller.Inspect(package, detected),
                    cts.Token
                );
                cts.Token.ThrowIfCancellationRequested();
                _companionContext.ApplyInstallStatus(status);
                _companionContext.ProgressValue = 100;
                _companionContext.OperationStatusText = "Estado do addon de teste verificado.";
                if (persistDetectedRoot)
                    SaveSettings();
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Operação cancelada.";
            }
            catch (Exception ex)
            {
                _companionContext.SetInstallationUnavailable("Incompatível", ex.Message);
                _companionContext.OperationStatusText = "Falha ao procurar: " + ex.Message;
            }
            finally
            {
                EndCompanionOperation(cts);
            }
        }

        private CancellationTokenSource BeginCompanionOperation(string status, int progress)
        {
            CancelCompanionOperation();
            var cts = new CancellationTokenSource();
            _companionCts = cts;
            _companionContext.IsBusy = true;
            _companionContext.ProgressValue = progress;
            _companionContext.OperationStatusText = status;
            return cts;
        }

        private void EndCompanionOperation(CancellationTokenSource cts)
        {
            if (ReferenceEquals(_companionCts, cts))
            {
                _companionCts = null;
                _companionContext.IsBusy = false;
            }
            cts.Dispose();
        }

        private void CancelCompanionOperation() => _companionCts?.Cancel();

        private string RequireCompanionGameRoot() =>
            CompanionPathPolicy.DetectGarrysModRoot(_companionContext.GarrysModRoot)
            ?? throw new InvalidOperationException("Escolha antes a pasta do Garry's Mod.");

        private static byte[] ReadEmbeddedCompanionPackage()
        {
            using Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(CompanionResourceName)
                ?? throw new FileNotFoundException("O pacote do addon de teste não está neste build.");
            if (stream.Length <= 0 || stream.Length > 20_000_000)
                throw new InvalidOperationException("O pacote do addon de teste tem tamanho inválido.");
            using var output = new MemoryStream((int)stream.Length);
            stream.CopyTo(output);
            return output.ToArray();
        }
    }
}
