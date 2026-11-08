using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Companion;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Linq;
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
        private string _profileSummaryText = "Nenhum perfil importado. O modo Maximum continua igual.";
        private string _validationSummaryText = "Nenhum plano ou resultado ativo.";
        private string _operationStatusText = "Pronto";
        private bool _useWithMaximumV2;
        private bool _isBusy;
        private int _progressValue;
        private CompanionProfileImportResult? _importedProfile;
        private bool _optimizerIsMaximum;
        private bool _hasOptimizationResult;
        private IReadOnlyList<CompanionValidationReport> _validationReports = System.Array.Empty<CompanionValidationReport>();
        private IReadOnlyList<CompanionValidationReportItem> _allValidationResources = System.Array.Empty<CompanionValidationReportItem>();
        private readonly ObservableCollection<CompanionValidationReportItem> _validationResources = new();
        private string _validationStatusFilter = "ALL";
        private CompanionValidationReportItem? _selectedValidationResource;
        private readonly List<string> _validationDiagnostics = new();

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
            ? "Nada em comum com o addon selecionado"
            : $"Vistos {_importedProfile.ObservedModelCount} modelos / {_importedProfile.ObservedMaterialCount} materiais · deste addon {_importedProfile.MatchedModelCount} / {_importedProfile.MatchedMaterialCount} · de fora ignorados {_importedProfile.IgnoredExternalAssetCount}";
        public string ProfileCompatibilityText => _importedProfile?.CompatibilityText ?? "Nenhum perfil validado";
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

        public string InstallationStatusText => _installationStatusOverride ?? _installStatus?.StatusText ?? "Verificando…";
        /// <summary>Short Portuguese state shown on the "Testar no jogo" screen.</summary>
        public string InstallationStatusLabel => _installationStatusOverride ?? (_installStatus?.State switch
        {
            CompanionInstallState.NotInstalled => "Não instalado",
            CompanionInstallState.Compatible => "Instalado",
            CompanionInstallState.UpdateAvailable => "Atualização disponível",
            CompanionInstallState.UnknownFolder => "Pasta desconhecida",
            CompanionInstallState.Incompatible => "Incompatível",
            _ => "Verificando…",
        });
        public string InstallationDetailText => _installationDetailOverride ?? _installStatus?.Detail ?? "Procurando o Garry's Mod e o addon de teste.";
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
        public ObservableCollection<CompanionValidationReportItem> ValidationResources => _validationResources;
        public IReadOnlyList<CompanionValidationReport> ValidationReports => _validationReports;
        public bool HasValidationResults => _validationReports.Count > 0;
        public bool HasNoValidationResults => _validationReports.Count == 0;
        public bool CanCopyValidationSummary => !IsBusy && HasValidationResults;
        public bool CanExportValidationSummary => !IsBusy && HasValidationResults;
        public bool CanOpenValidationScreenshot => !IsBusy && _selectedValidationResource?.HasScreenshot == true;

        public string ValidationStatusFilter
        {
            get => _validationStatusFilter;
            set
            {
                string normalized = (value ?? "ALL").Trim().ToUpperInvariant();
                if (normalized != "ALL" && normalized != "FAILED" && normalized != "WARNING" && normalized != "INCOMPLETE" && normalized != "PASS")
                    normalized = "ALL";
                if (!Set(ref _validationStatusFilter, normalized))
                    return;
                RefreshValidationResources();
            }
        }

        public CompanionValidationReportItem? SelectedValidationResource
        {
            get => _selectedValidationResource;
            set
            {
                if (!Set(ref _selectedValidationResource, value))
                    return;
                OnPropertyChanged(nameof(CanOpenValidationScreenshot));
            }
        }

        public void ApplyInstallStatus(CompanionInstallStatus status)
        {
            _installStatus = status;
            _installationStatusOverride = null;
            _installationDetailOverride = null;
            OnPropertyChanged(nameof(InstallationStatusText));
            OnPropertyChanged(nameof(InstallationStatusLabel));
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
            OnPropertyChanged(nameof(InstallationStatusLabel));
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
                ? "Nenhum perfil importado. O modo Maximum continua igual."
                : "Perfil escolhido. A importação valida o arquivo e confere com o addon.";
            NotifyProfileProperties();
            OnPropertyChanged(nameof(CanGeneratePlan));
        }

        public void ApplyImportedProfile(CompanionProfileImportResult result, string? profilePath)
        {
            _importedProfile = result ?? throw new System.ArgumentNullException(nameof(result));
            LastProfilePath = profilePath ?? string.Empty;
            Set(ref _useWithMaximumV2, false, nameof(UseWithMaximumV2));
            ProfileSummaryText = result.IsEligible
                ? $"Pronto para usar no modo Maximum: {result.MatchedModelCount} modelos e {result.MatchedMaterialCount} materiais deste addon."
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
                ? "Nenhum perfil importado. O modo Maximum continua igual."
                : "O addon mudou. O perfil precisa ser validado de novo antes de ser usado.";
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

        public void ApplyValidationReports(IReadOnlyList<CompanionValidationReport> reports)
        {
            _validationReports = reports?.ToArray() ?? System.Array.Empty<CompanionValidationReport>();
            _allValidationResources = _validationReports.SelectMany(report => report.Items).ToArray();
            SelectedValidationResource = null;
            RefreshValidationResources();
            RefreshValidationSummary();
            OnPropertyChanged(nameof(ValidationReports));
            OnPropertyChanged(nameof(HasValidationResults));
            OnPropertyChanged(nameof(HasNoValidationResults));
            OnPropertyChanged(nameof(CanCopyValidationSummary));
            OnPropertyChanged(nameof(CanExportValidationSummary));
            OnPropertyChanged(nameof(CanOpenValidationScreenshot));
        }

        public void ApplyValidationImportError(string path, string error)
        {
            _validationDiagnostics.Add($"REJECTED - {path}{System.Environment.NewLine}{error}");
            RefreshValidationSummary();
        }

        public string BuildValidationExportText() => CompanionResultService.ExportSummary(_validationReports);

        private void RefreshValidationResources()
        {
            _validationResources.Clear();
            foreach (CompanionValidationReportItem item in _allValidationResources.Where(item =>
                _validationStatusFilter == "ALL" || item.Status == _validationStatusFilter))
                _validationResources.Add(item);
            if (_selectedValidationResource != null && !_validationResources.Contains(_selectedValidationResource))
                SelectedValidationResource = null;
            OnPropertyChanged(nameof(ValidationResources));
        }

        private void RefreshValidationSummary()
        {
            var sections = new List<string>(_validationReports.Select(report => report.SummaryText));
            sections.AddRange(_validationDiagnostics);
            ValidationSummaryText = sections.Count == 0
                ? "Nenhum resultado importado."
                : string.Join(System.Environment.NewLine + System.Environment.NewLine, sections);
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
            OnPropertyChanged(nameof(CanCopyValidationSummary));
            OnPropertyChanged(nameof(CanExportValidationSummary));
            OnPropertyChanged(nameof(CanOpenValidationScreenshot));
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
