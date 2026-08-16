using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Companion;
using Microsoft.Win32;
using Ookii.Dialogs.Wpf;
using System;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

namespace GmodAddonCompressor
{
    public partial class MainWindow
    {
        private const string CompanionResourceName = "GmodAddonCompressor.Resources.SourceAddonOptimizerCompanion.zip";
        private readonly CompanionContext _companionContext = new();
        private CancellationTokenSource? _companionCts;

        private void InitializeCompanion()
        {
            CompanionTabRoot.DataContext = _companionContext;
            Button_CompanionBrowseGame.Click += Button_CompanionBrowseGame_Click;
            Button_CompanionDetectGame.Click += Button_CompanionDetectGame_Click;
            Button_CompanionInstall.Click += Button_CompanionInstall_Click;
            Button_CompanionUpdate.Click += Button_CompanionUpdate_Click;
            Button_CompanionRemove.Click += Button_CompanionRemove_Click;
            Button_CompanionOpenData.Click += Button_CompanionOpenData_Click;
            Button_CompanionImportProfile.Click += Button_CompanionImportProfile_Click;
            Button_CompanionGeneratePlan.Click += Button_CompanionGeneratePlan_Click;
            Button_CompanionImportResults.Click += Button_CompanionImportResults_Click;
            Button_CompanionCancel.Click += Button_CompanionCancel_Click;
            Closed += (_, _) => CancelCompanionOperation();
        }

        private void LoadCompanionSettings()
        {
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

        private void Button_CompanionImportProfile_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Select a Companion runtime profile index",
                Filter = "Companion profile index|run-index.json|JSON files|*.json",
                CheckFileExists = true,
                Multiselect = false,
            };
            string? priorDirectory = Path.GetDirectoryName(_companionContext.LastProfilePath);
            if (!string.IsNullOrWhiteSpace(priorDirectory) && Directory.Exists(priorDirectory))
                dialog.InitialDirectory = priorDirectory;
            if (!dialog.ShowDialog(this).GetValueOrDefault())
                return;

            _companionContext.ResetImportedProfile(dialog.FileName);
            _companionContext.OperationStatusText = "Profile selected; Maximum v2 remains disabled until explicit opt-in.";
            SaveSettings();
        }

        private void Button_CompanionGeneratePlan_Click(object sender, RoutedEventArgs e)
        {
            _companionContext.ValidationSummaryText =
                "The profile is selected. Validation-plan generation becomes available after its contract and addon match are verified.";
            _companionContext.OperationStatusText = "Waiting for a validated imported profile.";
        }

        private void Button_CompanionImportResults_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Select a Companion validation result index",
                Filter = "Companion result index|run-index.json|JSON files|*.json",
                CheckFileExists = true,
                Multiselect = false,
            };
            if (!dialog.ShowDialog(this).GetValueOrDefault())
                return;
            _companionContext.ValidationSummaryText =
                "Result selected: " + dialog.FileName + Environment.NewLine
                + "Result contract verification and merge are handled by the validation-result import stage.";
            _companionContext.OperationStatusText = "Validation result selected.";
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
