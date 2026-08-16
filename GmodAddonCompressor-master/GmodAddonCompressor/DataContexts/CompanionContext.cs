using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Companion;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace GmodAddonCompressor.DataContexts
{
    internal sealed class CompanionContext : INotifyPropertyChanged
    {
        private CompanionInstallStatus? _installStatus;
        private string? _installationStatusOverride;
        private string? _installationDetailOverride;
        private string _garrysModRoot = string.Empty;
        private string _lastProfilePath = string.Empty;
        private string _profileSummaryText = "No runtime profile imported. Models and Pipeline are unchanged.";
        private string _validationSummaryText = "No validation plan or result is active.";
        private string _operationStatusText = "Ready";
        private bool _useWithMaximumV2;
        private bool _isBusy;
        private int _progressValue;
        private CompanionProfileImportResult? _importedProfile;
        private bool _optimizerIsMaximum;
        private bool _hasOptimizationResult;

        public string GarrysModRoot
        {
            get => _garrysModRoot;
            set => Set(ref _garrysModRoot, value ?? string.Empty);
        }

        public string LastProfilePath
        {
            get => _lastProfilePath;
            private set => Set(ref _lastProfilePath, value);
        }

        public string ProfileSummaryText
        {
            get => _profileSummaryText;
            set => Set(ref _profileSummaryText, value);
        }

        public string ValidationSummaryText
        {
            get => _validationSummaryText;
            set => Set(ref _validationSummaryText, value);
        }

        public string OperationStatusText
        {
            get => _operationStatusText;
            set => Set(ref _operationStatusText, value);
        }

        public bool UseWithMaximumV2
        {
            get => _useWithMaximumV2;
            set => Set(ref _useWithMaximumV2, value && CanUseWithMaximumV2);
        }

        public bool CanUseWithMaximumV2 => _optimizerIsMaximum && _importedProfile?.IsEligible == true;
        public string ProfileOriginText => _importedProfile?.OriginText ?? "—";
        public string ProfileAgeText => _importedProfile?.AgeText ?? "—";
        public string ProfileMatchText => _importedProfile == null
            ? "No addon intersection available"
            : $"Observed {_importedProfile.ObservedModelCount} models / {_importedProfile.ObservedMaterialCount} materials · matched {_importedProfile.MatchedModelCount} / {_importedProfile.MatchedMaterialCount} · ignored external {_importedProfile.IgnoredExternalAssetCount}";
        public string ProfileCompatibilityText => _importedProfile?.CompatibilityText ?? "No validated runtime profile";
        public string ProfileTruncationText => _importedProfile?.TruncationText ?? "—";
        public CompanionProfileImportResult? ImportedProfile => _importedProfile;

        public bool IsBusy
        {
            get => _isBusy;
            set
            {
                if (!Set(ref _isBusy, value))
                    return;
                NotifyActionProperties();
            }
        }

        public int ProgressValue
        {
            get => _progressValue;
            set => Set(ref _progressValue, System.Math.Clamp(value, 0, 100));
        }

        public string InstallationStatusText => _installationStatusOverride ?? _installStatus?.StatusText ?? "Checking...";
        public string InstallationDetailText => _installationDetailOverride ?? _installStatus?.Detail ?? "Detecting Garry's Mod and the optional Companion addon.";
        public string InstalledVersionText => string.IsNullOrWhiteSpace(_installStatus?.InstalledVersion)
            ? "—"
            : _installStatus!.InstalledVersion;
        public string PackagedVersionText => string.IsNullOrWhiteSpace(_installStatus?.PackagedVersion)
            ? "—"
            : _installStatus!.PackagedVersion;
        public string InstallDestinationText => _installStatus?.Destination ?? "—";
        public bool CanInstall => !IsBusy && !string.IsNullOrWhiteSpace(GarrysModRoot) && _installStatus?.CanInstall == true;
        public bool CanUpdate => !IsBusy && _installStatus?.CanUpdate == true;
        public bool CanRemove => !IsBusy && _installStatus?.CanRemove == true;
        public bool CanCancel => IsBusy;
        public bool CanOpenDataFolder => !IsBusy && !string.IsNullOrWhiteSpace(GarrysModRoot);
        public bool CanImportProfile => !IsBusy;
        public bool CanGeneratePlan => !IsBusy && _hasOptimizationResult;
        public bool CanImportResults => !IsBusy;

        public void ApplyInstallStatus(CompanionInstallStatus status)
        {
            _installStatus = status;
            _installationStatusOverride = null;
            _installationDetailOverride = null;
            OnPropertyChanged(nameof(InstallationStatusText));
            OnPropertyChanged(nameof(InstallationDetailText));
            OnPropertyChanged(nameof(InstalledVersionText));
            OnPropertyChanged(nameof(PackagedVersionText));
            OnPropertyChanged(nameof(InstallDestinationText));
            NotifyActionProperties();
        }

        public void SetInstallationUnavailable(string status, string detail)
        {
            _installStatus = null;
            _installationStatusOverride = status;
            _installationDetailOverride = detail;
            OnPropertyChanged(nameof(InstallationStatusText));
            OnPropertyChanged(nameof(InstallationDetailText));
            OnPropertyChanged(nameof(InstalledVersionText));
            OnPropertyChanged(nameof(PackagedVersionText));
            OnPropertyChanged(nameof(InstallDestinationText));
            NotifyActionProperties();
        }

        public void ResetImportedProfile(string? profilePath)
        {
            _importedProfile = null;
            LastProfilePath = profilePath ?? string.Empty;
            UseWithMaximumV2 = false;
            ProfileSummaryText = string.IsNullOrWhiteSpace(LastProfilePath)
                ? "No runtime profile imported. Models and Pipeline are unchanged."
                : "Profile selected. Contract validation and addon matching are completed by the import step.";
            NotifyProfileProperties();
            OnPropertyChanged(nameof(CanGeneratePlan));
        }

        public void ApplyImportedProfile(CompanionProfileImportResult result, string? profilePath)
        {
            _importedProfile = result ?? throw new System.ArgumentNullException(nameof(result));
            LastProfilePath = profilePath ?? string.Empty;
            Set(ref _useWithMaximumV2, false, nameof(UseWithMaximumV2));
            ProfileSummaryText = result.IsEligible
                ? $"Ready for explicit Maximum v2 opt-in. {result.MatchedModelCount} models and {result.MatchedMaterialCount} materials matched this addon."
                : result.CompatibilityText;
            NotifyProfileProperties();
            OnPropertyChanged(nameof(CanGeneratePlan));
        }

        public void SetOptimizerMode(bool isMaximum)
        {
            if (_optimizerIsMaximum == isMaximum)
                return;
            _optimizerIsMaximum = isMaximum;
            if (!isMaximum)
                Set(ref _useWithMaximumV2, false, nameof(UseWithMaximumV2));
            OnPropertyChanged(nameof(CanUseWithMaximumV2));
        }

        public void ClearForAddonChange()
        {
            _importedProfile = null;
            Set(ref _useWithMaximumV2, false, nameof(UseWithMaximumV2));
            ProfileSummaryText = string.IsNullOrWhiteSpace(LastProfilePath)
                ? "No runtime profile imported. Models and Pipeline are unchanged."
                : "The selected addon changed. Revalidating the imported profile before Maximum v2 can use it.";
            NotifyProfileProperties();
            SetOptimizationResultAvailable(false);
        }

        public void SetOptimizationResultAvailable(bool available)
        {
            if (_hasOptimizationResult == available)
                return;
            _hasOptimizationResult = available;
            OnPropertyChanged(nameof(CanGeneratePlan));
        }

        public event PropertyChangedEventHandler? PropertyChanged;

        private bool Set<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
        {
            if (EqualityComparer<T>.Default.Equals(field, value))
                return false;
            field = value;
            OnPropertyChanged(propertyName);
            if (propertyName == nameof(GarrysModRoot))
                NotifyActionProperties();
            return true;
        }

        private void NotifyActionProperties()
        {
            OnPropertyChanged(nameof(CanInstall));
            OnPropertyChanged(nameof(CanUpdate));
            OnPropertyChanged(nameof(CanRemove));
            OnPropertyChanged(nameof(CanCancel));
            OnPropertyChanged(nameof(CanOpenDataFolder));
            OnPropertyChanged(nameof(CanImportProfile));
            OnPropertyChanged(nameof(CanGeneratePlan));
            OnPropertyChanged(nameof(CanImportResults));
        }

        private void NotifyProfileProperties()
        {
            OnPropertyChanged(nameof(ImportedProfile));
            OnPropertyChanged(nameof(CanUseWithMaximumV2));
            OnPropertyChanged(nameof(ProfileOriginText));
            OnPropertyChanged(nameof(ProfileAgeText));
            OnPropertyChanged(nameof(ProfileMatchText));
            OnPropertyChanged(nameof(ProfileCompatibilityText));
            OnPropertyChanged(nameof(ProfileTruncationText));
        }

        private void OnPropertyChanged([CallerMemberName] string? propertyName = null) =>
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}
