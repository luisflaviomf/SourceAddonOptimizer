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
            Combo_CompanionResultFilter.SelectionChanged += Combo_CompanionResultFilter_SelectionChanged;
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
                Description = "Select the Garry's Mod installation folder (the folder containing gmod.exe).",
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
                "Remove only the files declared by the Companion ownership manifest? Runtime profiles and validation results under garrysmod/data are preserved.",
                "Remove Companion",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning
            );
            if (confirmation != MessageBoxResult.Yes)
                return;

            CancellationTokenSource cts = BeginCompanionOperation("Removing declared Companion files...", 15);
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
                    ? "Companion removed. Runtime data was preserved."
                    : "Companion files were removed; unknown paths were preserved. " + result.Warning;
                _companionContext.ProgressValue = 100;
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Companion operation cancelled.";
            }
            catch (Exception ex)
            {
                _companionContext.OperationStatusText = "Remove failed: " + ex.Message;
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
                OpenFolder(dataRoot, "Companion");
            }
            catch (Exception ex)
            {
                _companionContext.OperationStatusText = "Could not open the data folder: " + ex.Message;
            }
        }

        private async void Button_CompanionImportProfile_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Select a Companion runtime profile index",
                Filter = "Companion profile index|run-index.json|JSON files|*.json",
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
                    "Select a valid addon folder before importing. The profile remains disabled.";
                _companionContext.OperationStatusText = "Profile not imported: addon folder is missing.";
                return;
            }

            CancellationTokenSource cts = BeginCompanionOperation("Validating and matching runtime profile...", 10);
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
                    ? "Profile verified and matched. Maximum v2 use remains off until you check it explicitly."
                    : "Profile verified, but it is not eligible for Maximum v2: " + result.CompatibilityText;
                if (persistSelectedPath)
                    SaveSettings();
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Profile import cancelled; Maximum v2 profile use is off.";
            }
            catch (Exception ex)
            {
                _companionContext.ResetImportedProfile(profileIndexes[0]);
                _companionContext.ProfileSummaryText = "Profile rejected: " + ex.Message;
                _companionContext.OperationStatusText = "Profile import failed; Models and Pipeline are unchanged.";
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
                    $"Runtime profile snapshot verified for Maximum v2 ({snapshot.ImportResult.MatchedModelCount} models, {snapshot.ImportResult.MatchedMaterialCount} materials).";
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
                    "Runtime evidence was rejected at run start and was not used. Ordinary Maximum v2 continues unchanged. "
                    + ex.Message;
                _companionContext.OperationStatusText =
                    "Runtime profile snapshot failed; continuing without runtime evidence.";
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
                    ? "Maximum v2 completed with verified runtime evidence."
                    : runtimeProfileRequested
                        ? "Maximum v2 completed, but the worker did not apply any runtime evidence."
                        : "Maximum v2 completed without runtime evidence.";
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception ex)
            {
                _lastMaximumRuntimeSummaryText = runtimeProfileRequested
                    ? "Runtime evidence was requested, but its final Maximum report could not be verified."
                    : string.Empty;
                if (runtimeProfileRequested)
                {
                    _companionContext.ProfileSummaryText = _lastMaximumRuntimeSummaryText + " " + ex.Message;
                    _companionContext.OperationStatusText =
                        "Maximum completed; runtime evidence is not reported as used because verification failed.";
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
                _companionContext.OperationStatusText = "Generate plan failed: no completed Maximum v2 output is available.";
                return;
            }
            string reportPath = Path.Combine(workDir, "logs", "maximum_adaptive_report.json");
            string? destinationParent = SelectValidationPlanDestination();
            if (destinationParent == null)
                return;

            CancellationTokenSource cts = BeginCompanionOperation("Generating bounded validation plan...", 10);
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
                    $"Plan ready: {result.ModelCount} models, {result.Tests.Count} tests, {result.ScreenshotCount} screenshots. "
                    + $"Run {result.RunId:D}; SHA-256 {result.IndexSha256}. "
                    + $"Caps {250} models / {1000} screenshots / {result.MaximumOrdinaryViews} views / {result.ScreenshotRetries} retry. "
                    + $"Truncated: tests {result.TruncatedTests}, models {result.TruncatedModels}, screenshots {result.TruncatedScreenshots}. "
                    + "DX80 is explicitly excluded.";
                _companionContext.OperationStatusText = "Validation plan generated. Transfer remains a manual, local action.";
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Validation plan generation cancelled.";
            }
            catch (Exception ex)
            {
                _companionContext.OperationStatusText = "Generate plan failed: " + ex.Message;
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
                Description = "Select the validation_inbox or export folder that will receive a verified copy.",
                UseDescriptionForTitle = true,
            };
            if (!dialog.ShowDialog(this).GetValueOrDefault())
                return;
            CancellationTokenSource cts = BeginCompanionOperation("Copying and revalidating the plan...", 20);
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
                _companionContext.OperationStatusText = "Verified plan copy created: " + copied;
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Validation plan copy cancelled.";
            }
            catch (Exception ex)
            {
                _companionContext.OperationStatusText = "Copy plan failed: " + ex.Message;
            }
            finally
            {
                EndCompanionOperation(cts);
            }
        }

        private void Button_CompanionOpenPlan_Click(object sender, RoutedEventArgs e) =>
            OpenFolder(_lastCompanionValidationPlanRoot, "Companion plan");

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
                    "Generate directly in this local Garry's Mod validation inbox?\n\n"
                    + Path.Combine(gameRoot, "garrysmod", "data", "sourceaddonoptimizer", "validation_inbox")
                    + "\n\nChoose No to select a manual export folder.",
                    "Validation Plan",
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
                Description = "Select a local export folder for the validation plan.",
                UseDescriptionForTitle = true,
            };
            return dialog.ShowDialog(this).GetValueOrDefault() ? dialog.SelectedPath : null;
        }

        private async void Button_CompanionImportResults_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Select Companion structural and/or visual result indexes",
                Filter = "Companion result index|run-index.json|JSON files|*.json",
                CheckFileExists = true,
                Multiselect = true,
            };
            if (!dialog.ShowDialog(this).GetValueOrDefault())
                return;
            CancellationTokenSource cts = BeginCompanionOperation("Verifying validation results and screenshot evidence...", 10);
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
                    ? $"Imported {imported} trusted validation result(s)."
                    : $"Imported {imported}; rejected {rejected.Count}. Rejected inputs remain listed for diagnosis and were not displayed as trusted evidence.";
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Validation result import cancelled.";
            }
            finally
            {
                EndCompanionOperation(cts);
            }
        }

        private void Combo_CompanionResultFilter_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (Combo_CompanionResultFilter.SelectedItem is ComboBoxItem item && item.Tag is string filter)
                _companionContext.ValidationStatusFilter = filter;
        }

        private void Button_CompanionCopyResults_Click(object sender, RoutedEventArgs e)
        {
            string summary = _companionContext.BuildValidationExportText();
            if (string.IsNullOrWhiteSpace(summary))
                return;
            Clipboard.SetText(summary);
            _companionContext.OperationStatusText = "Validation summary copied.";
        }

        private void Button_CompanionExportResults_Click(object sender, RoutedEventArgs e)
        {
            string summary = _companionContext.BuildValidationExportText();
            if (string.IsNullOrWhiteSpace(summary))
                return;
            var dialog = new SaveFileDialog
            {
                Title = "Export Companion validation summary",
                Filter = "Text summary|*.txt",
                FileName = "companion-validation-summary.txt",
                AddExtension = true,
                OverwritePrompt = true,
            };
            if (!dialog.ShowDialog(this).GetValueOrDefault())
                return;
            File.WriteAllText(dialog.FileName, summary + Environment.NewLine, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            _companionContext.OperationStatusText = "Validation summary exported: " + dialog.FileName;
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
            _companionContext.OperationStatusText = "Cancelling Companion operation...";
            CancelCompanionOperation();
        }

        private async Task RunCompanionInstallAsync(bool isUpdate)
        {
            if (isUpdate ? !_companionContext.CanUpdate : !_companionContext.CanInstall)
                return;
            CancellationTokenSource cts = BeginCompanionOperation(
                isUpdate ? "Updating the Companion..." : "Installing the Companion...",
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
                    CompanionInstallDisposition.Installed => "Companion installed. Restart Garry's Mod if it is already open.",
                    CompanionInstallDisposition.Updated => "Companion updated. Restart Garry's Mod if it is already open.",
                    _ => "The packaged Companion is already installed.",
                };
                _companionContext.ProgressValue = 100;
                SaveSettings();
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Companion operation cancelled.";
            }
            catch (Exception ex)
            {
                _companionContext.OperationStatusText = (isUpdate ? "Update failed: " : "Install failed: ") + ex.Message;
            }
            finally
            {
                EndCompanionOperation(cts);
            }
        }

        private async Task RefreshCompanionStatusAsync(string? explicitRoot, bool persistDetectedRoot)
        {
            CancellationTokenSource cts = BeginCompanionOperation("Detecting Garry's Mod...", 5);
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
                        "Not installed",
                        "Garry's Mod was not detected. Select the folder containing gmod.exe to enable installation."
                    );
                    _companionContext.OperationStatusText = "Garry's Mod not detected.";
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
                _companionContext.OperationStatusText = "Companion installation state checked.";
                if (persistDetectedRoot)
                    SaveSettings();
            }
            catch (OperationCanceledException)
            {
                _companionContext.OperationStatusText = "Companion operation cancelled.";
            }
            catch (Exception ex)
            {
                _companionContext.SetInstallationUnavailable("Incompatible", ex.Message);
                _companionContext.OperationStatusText = "Detection failed: " + ex.Message;
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
            ?? throw new InvalidOperationException("Select a valid Garry's Mod folder first.");

        private static byte[] ReadEmbeddedCompanionPackage()
        {
            using Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(CompanionResourceName)
                ?? throw new FileNotFoundException("The embedded Companion package is missing from this build.");
            if (stream.Length <= 0 || stream.Length > 20_000_000)
                throw new InvalidOperationException("The embedded Companion package has an invalid size.");
            using var output = new MemoryStream((int)stream.Length);
            stream.CopyTo(output);
            return output.ToArray();
        }
    }
}
