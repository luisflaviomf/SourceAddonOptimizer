using GmodAddonCompressor.Systems.Optimizer;
using GmodAddonCompressor.Systems.Reporting;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.CompilerServices;

namespace GmodAddonCompressor.DataContexts
{
    internal enum WizardRunState
    {
        Review,
        Running,
        Done,
        Failed
    }

    internal enum WizardStageState
    {
        Pending,
        Running,
        Done
    }

    internal sealed class WizardStageRow : INotifyPropertyChanged
    {
        private WizardStageState _state;
        private string _detail = string.Empty;

        public WizardStageRow(string name) => Name = name;

        public string Name { get; }

        public WizardStageState State
        {
            get => _state;
            set
            {
                _state = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(Glyph));
                OnPropertyChanged(nameof(IsDone));
                OnPropertyChanged(nameof(IsPending));
            }
        }

        public string Detail
        {
            get => _detail;
            set { _detail = value; OnPropertyChanged(); }
        }

        // Segoe Fluent Icons / MDL2: CheckMark, Sync, Clock
        public string Glyph => _state switch
        {
            WizardStageState.Done => "",
            WizardStageState.Running => "",
            _ => ""
        };

        public bool IsDone => _state == WizardStageState.Done;
        public bool IsPending => _state == WizardStageState.Pending;

        public event PropertyChangedEventHandler? PropertyChanged;
        private void OnPropertyChanged([CallerMemberName] string? name = null) =>
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }

    internal sealed record WizardResultRow(
        string Name,
        string SizesText,
        string ReductionText,
        double BeforeWidth,
        double AfterWidth,
        bool IsTotal);

    internal sealed record WizardRecentAddon(string Path, string Name, string Detail);

    /// <summary>View model of the "Otimizar" assistant (Addon → Objetivo → Otimizar).</summary>
    internal sealed class OptimizeWizardContext : INotifyPropertyChanged
    {
        internal static readonly CultureInfo PtBr = CultureInfo.GetCultureInfo("pt-BR");
        internal const double BarTrackWidth = 320;

        private int _step = 1;
        private string _addonPath = string.Empty;
        private string _outputPath = string.Empty;
        private bool _outputExists;
        private bool _isScanning;
        private bool _addonLooksValid = true;
        private AddonInventory? _inventory;
        private WizardGoal _goal = WizardGoal.Full;
        private WizardProfile _profile = WizardProfile.Balanced;
        private WizardRunState _runState = WizardRunState.Review;
        private bool _available = true;
        private double _percent;
        private string _stageText = string.Empty;
        private string _currentItem = string.Empty;
        private string _elapsedText = string.Empty;
        private string _resultBefore = string.Empty;
        private string _resultAfter = string.Empty;
        private string _resultReduction = string.Empty;
        private string _resultSubtitle = string.Empty;
        private string _errorTitle = string.Empty;
        private string _errorText = string.Empty;
        private bool _logExpanded;

        public ObservableCollection<WizardStageRow> Stages { get; } = new();
        public ObservableCollection<WizardResultRow> ResultRows { get; } = new();
        public ObservableCollection<WizardRecentAddon> Recent { get; } = new();

        // ---------- steps ----------
        public int Step
        {
            get => _step;
            set
            {
                _step = value < 1 ? 1 : value > 3 ? 3 : value;
                OnPropertyChanged();
                RaiseStepState();
            }
        }

        public bool IsStep1 => _step == 1;
        public bool IsStep2 => _step == 2;
        public bool IsStep3 => _step == 3;
        public bool Step1Done => _step > 1;
        public bool Step2Done => _step > 2;
        public bool Step3Done => _step == 3 && _runState == WizardRunState.Done;
        public bool Step1Current => _step == 1;
        public bool Step2Current => _step == 2;
        public bool Step3Current => _step == 3 && _runState != WizardRunState.Done;

        // footer buttons
        public bool FooterBack => _step == 2 || (_step == 3 && (_runState == WizardRunState.Review || _runState == WizardRunState.Failed));
        public bool FooterContinue => _step < 3;
        public bool FooterStart => _step == 3 && _runState == WizardRunState.Review;
        public bool FooterCancel => _step == 3 && _runState == WizardRunState.Running;
        public bool FooterDone => _step == 3 && _runState == WizardRunState.Done;
        public bool FooterRetry => _step == 3 && _runState == WizardRunState.Failed;
        public bool ShowRunPanels => _step == 3 && _runState != WizardRunState.Review;

        // ---------- step 1: addon ----------
        public string AddonPath
        {
            get => _addonPath;
            set
            {
                _addonPath = value ?? string.Empty;
                OnPropertyChanged();
                OnPropertyChanged(nameof(AddonName));
                OnPropertyChanged(nameof(HasAddon));
                OnPropertyChanged(nameof(ShowDropZone));
                OnPropertyChanged(nameof(ShowRecent));
                OnPropertyChanged(nameof(ShowNotAddonWarning));
                OnPropertyChanged(nameof(CanContinue));
            }
        }

        public string AddonName => string.IsNullOrWhiteSpace(_addonPath)
            ? string.Empty
            : Path.GetFileName(_addonPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));

        public bool HasAddon => !string.IsNullOrWhiteSpace(_addonPath);
        public bool ShowDropZone => !HasAddon;
        public bool ShowRecent => !HasAddon && Recent.Count > 0;

        public string OutputPath
        {
            get => _outputPath;
            set { _outputPath = value ?? string.Empty; OnPropertyChanged(); }
        }

        public bool OutputExists
        {
            get => _outputExists;
            set { _outputExists = value; OnPropertyChanged(); }
        }

        public bool IsScanning
        {
            get => _isScanning;
            set
            {
                _isScanning = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(HasInventory));
                OnPropertyChanged(nameof(CanContinue));
            }
        }

        public bool AddonLooksValid
        {
            get => _addonLooksValid;
            set { _addonLooksValid = value; OnPropertyChanged(); OnPropertyChanged(nameof(ShowNotAddonWarning)); }
        }

        public bool ShowNotAddonWarning => HasAddon && !_addonLooksValid;

        public AddonInventory? Inventory
        {
            get => _inventory;
            set
            {
                _inventory = value;
                foreach (string name in new[]
                {
                    nameof(Inventory), nameof(HasInventory), nameof(TotalSizeText), nameof(TotalFilesText),
                    nameof(ModelsFilesText), nameof(ModelsSizeText), nameof(TexturesFilesText), nameof(TexturesSizeText),
                    nameof(SoundsFilesText), nameof(SoundsSizeText), nameof(OthersFilesText), nameof(OthersSizeText),
                    nameof(CanGoalModels), nameof(CanGoalMedia), nameof(CanGoalFull), nameof(GoalModelsHint),
                    nameof(GoalMediaHint), nameof(CanContinue)
                })
                    OnPropertyChanged(name);
                RaiseProfileLines();
            }
        }

        public bool HasInventory => _inventory != null && !_isScanning;
        public string TotalSizeText => _inventory == null ? string.Empty : FormatMiB(_inventory.TotalBytes);
        public string TotalFilesText => _inventory == null ? string.Empty : FilesText(_inventory.TotalFiles);
        public string ModelsFilesText => FilesText(_inventory?.Models.Files ?? 0);
        public string ModelsSizeText => FormatMiB(_inventory?.Models.Bytes ?? 0);
        public string TexturesFilesText => FilesText(_inventory?.Textures.Files ?? 0);
        public string TexturesSizeText => FormatMiB(_inventory?.Textures.Bytes ?? 0);
        public string SoundsFilesText => FilesText(_inventory?.Sounds.Files ?? 0);
        public string SoundsSizeText => FormatMiB(_inventory?.Sounds.Bytes ?? 0);
        public string OthersFilesText => FilesText(_inventory?.Others.Files ?? 0);
        public string OthersSizeText => FormatMiB(_inventory?.Others.Bytes ?? 0);

        // ---------- step 2: goal and profile ----------
        public bool CanGoalModels => _inventory?.HasModels ?? false;
        public bool CanGoalMedia => _inventory?.HasMedia ?? false;
        public bool CanGoalFull => CanGoalModels || CanGoalMedia;
        public string GoalModelsHint => CanGoalModels ? "Só os arquivos de modelo (.mdl)." : "Este addon não tem modelos.";
        public string GoalMediaHint => CanGoalMedia ? "Texturas e sons." : "Este addon não tem texturas nem sons.";

        public WizardGoal Goal
        {
            get => _goal;
            set
            {
                _goal = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(GoalFull));
                OnPropertyChanged(nameof(GoalModels));
                OnPropertyChanged(nameof(GoalMedia));
                OnPropertyChanged(nameof(GoalTitle));
                OnPropertyChanged(nameof(CanContinue));
                RaiseProfileLines();
            }
        }

        public bool GoalFull { get => _goal == WizardGoal.Full; set { if (value) Goal = WizardGoal.Full; } }
        public bool GoalModels { get => _goal == WizardGoal.Models; set { if (value) Goal = WizardGoal.Models; } }
        public bool GoalMedia { get => _goal == WizardGoal.Media; set { if (value) Goal = WizardGoal.Media; } }
        public string GoalTitle => WizardProfiles.GoalTitle(_goal);

        public WizardProfile Profile
        {
            get => _profile;
            set
            {
                _profile = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(ProfileLossless));
                OnPropertyChanged(nameof(ProfileBalanced));
                OnPropertyChanged(nameof(ProfileMax));
                OnPropertyChanged(nameof(ProfileCustom));
                OnPropertyChanged(nameof(IsCustom));
                OnPropertyChanged(nameof(ShowPresetSummary));
                OnPropertyChanged(nameof(ProfileTitle));
                RaiseProfileLines();
            }
        }

        public bool ProfileLossless { get => _profile == WizardProfile.Lossless; set { if (value) Profile = WizardProfile.Lossless; } }
        public bool ProfileBalanced { get => _profile == WizardProfile.Balanced; set { if (value) Profile = WizardProfile.Balanced; } }
        public bool ProfileMax { get => _profile == WizardProfile.Max; set { if (value) Profile = WizardProfile.Max; } }
        public bool ProfileCustom { get => _profile == WizardProfile.Custom; set { if (value) Profile = WizardProfile.Custom; } }
        public string ProfileTitle => WizardProfiles.Title(_profile);
        public bool IsCustom => _profile == WizardProfile.Custom;
        public bool ShowPresetSummary => !IsCustom;

        // ---------- Personalizado ----------
        private string _customModels = "stream";
        private string _customTextures = "stream";
        private string _customSounds = "adaptive";
        private int _customWavRateIndex = 1;
        private int _customWavChannelsIndex = 1;
        private int _customWavCodecIndex;
        private bool _customLua;

        public string CustomModels { get => _customModels; set { _customModels = value ?? WizardProfiles.None; RaiseCustom(); } }
        public string CustomTextures { get => _customTextures; set { _customTextures = value ?? WizardProfiles.None; RaiseCustom(); } }
        public string CustomSounds { get => _customSounds; set { _customSounds = value ?? WizardProfiles.None; RaiseCustom(); } }
        public int CustomWavRateIndex { get => _customWavRateIndex; set { _customWavRateIndex = value; RaiseCustom(); } }
        public int CustomWavChannelsIndex { get => _customWavChannelsIndex; set { _customWavChannelsIndex = value; RaiseCustom(); } }
        public int CustomWavCodecIndex { get => _customWavCodecIndex; set { _customWavCodecIndex = value; RaiseCustom(); } }
        public bool CustomLua { get => _customLua; set { _customLua = value; RaiseCustom(); } }

        public string CustomModelsHint => WizardProfiles.Find(WizardProfiles.ModelChoices, _customModels).Description;
        public string CustomTexturesHint => WizardProfiles.Find(WizardProfiles.TextureChoices, _customTextures).Description;
        public string CustomSoundsHint => WizardProfiles.Find(WizardProfiles.SoundChoices, _customSounds).Description;
        public bool ShowCustomManualWav => _customSounds == "manual";
        public string[] WavRates => WizardProfiles.WavRates;
        public string[] WavChannels => WizardProfiles.WavChannels;
        public string[] WavCodecs => WizardProfiles.WavCodecs;
        public bool ShowCustomModels => IsCustom && _goal != WizardGoal.Media && CanGoalModels;
        public bool ShowCustomMedia => IsCustom && _goal != WizardGoal.Models && CanGoalMedia;
        public bool ShowNothingHint => !HasWork;

        /// <summary>What the run will apply: a preset profile, or the Personalizado chips.</summary>
        public WizardSettings CurrentSettings() => IsCustom
            ? WizardProfiles.ForCustom(_customModels, _customTextures, _customSounds,
                _customWavRateIndex, _customWavChannelsIndex, _customWavCodecIndex, _customLua)
            : WizardProfiles.For(_profile);

        private void RaiseCustom()
        {
            foreach (string name in new[]
            {
                nameof(CustomModels), nameof(CustomTextures), nameof(CustomSounds), nameof(CustomWavRateIndex),
                nameof(CustomWavChannelsIndex), nameof(CustomWavCodecIndex), nameof(CustomLua),
                nameof(CustomModelsHint), nameof(CustomTexturesHint), nameof(CustomSoundsHint), nameof(ShowCustomManualWav)
            })
                OnPropertyChanged(name);
            RaiseProfileLines();
        }

        /// <summary>The goal that will really run: an addon without models runs only the media part.</summary>
        public WizardGoal EffectiveGoal
        {
            get
            {
                var (models, media) = WorkParts();
                if (models && media) return WizardGoal.Full;
                if (models) return WizardGoal.Models;
                if (media) return WizardGoal.Media;
                return _goal;
            }
        }

        /// <summary>True when the chosen goal and profile still leave something to do in this addon.</summary>
        public bool HasWork
        {
            get
            {
                var (models, media) = WorkParts();
                return models || media;
            }
        }

        private (bool Models, bool Media) WorkParts()
        {
            var s = CurrentSettings();
            bool models = _goal != WizardGoal.Media && (_inventory?.HasModels ?? true) && s.Models;
            bool media = _goal != WizardGoal.Models && (_inventory?.HasMedia ?? true) && (s.Textures || s.IncludeWav || s.Lua);
            return (models, media);
        }

        public bool ShowModelsLine => EffectiveGoal != WizardGoal.Media;
        public bool ShowMediaLines => EffectiveGoal != WizardGoal.Models;
        public string ProfileModelsLine => CurrentSettings().ModelsLine;
        public string ProfileTexturesLine => CurrentSettings().TexturesLine;
        public string ProfileSoundsLine => CurrentSettings().SoundsLine;

        public bool CanContinue => _step switch
        {
            1 => HasAddon && !_isScanning && _inventory != null && CanGoalFull,
            2 => HasWork && _goal switch
            {
                WizardGoal.Models => CanGoalModels,
                WizardGoal.Media => CanGoalMedia,
                _ => CanGoalFull
            },
            _ => true
        };

        // ---------- step 3: run ----------
        public bool Available
        {
            get => _available;
            set { _available = value; OnPropertyChanged(); }
        }

        public WizardRunState RunState
        {
            get => _runState;
            set
            {
                _runState = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(IsReview));
                OnPropertyChanged(nameof(IsRunning));
                OnPropertyChanged(nameof(IsDone));
                OnPropertyChanged(nameof(IsFailed));
                OnPropertyChanged(nameof(IsNotRunning));
                RaiseStepState();
            }
        }

        public bool IsReview => _runState == WizardRunState.Review;
        public bool IsRunning => _runState == WizardRunState.Running;
        public bool IsDone => _runState == WizardRunState.Done;
        public bool IsFailed => _runState == WizardRunState.Failed;
        public bool IsNotRunning => _runState != WizardRunState.Running;

        public double Percent
        {
            get => _percent;
            set
            {
                _percent = value < 0 ? 0 : value > 100 ? 100 : value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(PercentText));
            }
        }

        public string PercentText => $"{(int)_percent}%";

        public string StageText
        {
            get => _stageText;
            set { _stageText = value; OnPropertyChanged(); }
        }

        public string CurrentItem
        {
            get => _currentItem;
            set { _currentItem = value; OnPropertyChanged(); OnPropertyChanged(nameof(HasCurrentItem)); }
        }

        public bool HasCurrentItem => !string.IsNullOrWhiteSpace(_currentItem);

        public string ElapsedText
        {
            get => _elapsedText;
            set { _elapsedText = value; OnPropertyChanged(); }
        }

        public bool LogExpanded
        {
            get => _logExpanded;
            set { _logExpanded = value; OnPropertyChanged(); OnPropertyChanged(nameof(LogToggleText)); }
        }

        public string LogToggleText => _logExpanded ? "Ocultar detalhes" : "Mostrar detalhes";

        public string ResultBefore { get => _resultBefore; set { _resultBefore = value; OnPropertyChanged(); } }
        public string ResultAfter { get => _resultAfter; set { _resultAfter = value; OnPropertyChanged(); } }
        public string ResultReduction { get => _resultReduction; set { _resultReduction = value; OnPropertyChanged(); } }
        public string ResultSubtitle { get => _resultSubtitle; set { _resultSubtitle = value; OnPropertyChanged(); } }
        public string ErrorTitle { get => _errorTitle; set { _errorTitle = value; OnPropertyChanged(); } }
        public string ErrorText { get => _errorText; set { _errorText = value; OnPropertyChanged(); } }

        public void SetStages(params string[] names)
        {
            Stages.Clear();
            foreach (string name in names)
                Stages.Add(new WizardStageRow(name));
        }

        public void RaiseRecent() => OnPropertyChanged(nameof(ShowRecent));

        // ---------- formatting ----------
        internal static string FormatMiB(long bytes)
        {
            double mib = bytes / 1048576.0;
            if (bytes > 0 && mib < 0.1)
                return $"{Math.Max(1, (int)Math.Round(bytes / 1024.0)).ToString("N0", PtBr)} KiB";
            return mib >= 100 || mib == 0
                ? $"{mib.ToString("N0", PtBr)} MiB"
                : $"{mib.ToString("N1", PtBr)} MiB";
        }

        internal static string FilesText(int files) =>
            files == 1 ? "1 arquivo" : $"{files.ToString("N0", PtBr)} arquivos";

        internal static int ReductionPercent(long before, long after) =>
            before <= 0 ? 0 : (int)System.Math.Round((1.0 - (double)after / before) * 100.0 - 0.0001);

        private void RaiseProfileLines()
        {
            OnPropertyChanged(nameof(EffectiveGoal));
            OnPropertyChanged(nameof(ShowModelsLine));
            OnPropertyChanged(nameof(ShowMediaLines));
            OnPropertyChanged(nameof(ProfileModelsLine));
            OnPropertyChanged(nameof(ProfileTexturesLine));
            OnPropertyChanged(nameof(ProfileSoundsLine));
            OnPropertyChanged(nameof(HasWork));
            OnPropertyChanged(nameof(ShowNothingHint));
            OnPropertyChanged(nameof(ShowCustomModels));
            OnPropertyChanged(nameof(ShowCustomMedia));
            OnPropertyChanged(nameof(CanContinue));
        }

        private void RaiseStepState()
        {
            foreach (string name in new[]
            {
                nameof(IsStep1), nameof(IsStep2), nameof(IsStep3), nameof(Step1Done), nameof(Step2Done), nameof(Step3Done),
                nameof(Step1Current), nameof(Step2Current), nameof(Step3Current), nameof(CanContinue),
                nameof(FooterBack), nameof(FooterContinue), nameof(FooterStart), nameof(FooterCancel),
                nameof(FooterDone), nameof(FooterRetry), nameof(ShowRunPanels)
            })
                OnPropertyChanged(name);
        }

        public event PropertyChangedEventHandler? PropertyChanged;

        private void OnPropertyChanged([CallerMemberName] string? name = null) =>
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
