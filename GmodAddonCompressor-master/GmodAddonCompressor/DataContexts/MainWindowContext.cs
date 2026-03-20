using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows;

namespace GmodAddonCompressor.DataContexts
{
    internal class MainWindowContext : INotifyPropertyChanged
    {
        private static double ClampOptimizerRatio(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value))
                return 0.50;

            return System.Math.Clamp(value, 0.01, 1.00);
        }

        private string _addonDirectoryPath = string.Empty;
        private string _addonWorkshopWarningText = string.Empty;
        private string _progressBarText = string.Empty;
        private int _progressBarMinValue = 0;
        private int _progressBarMaxValue = 100;
        private int _progressBarValue = 0;
        private string _compressSizeReportText = string.Empty;
        private string _modelsStatusText = string.Empty;
        private string _modelsProgressText = string.Empty;
        private int _modelsProgressMinValue = 0;
        private int _modelsProgressMaxValue = 100;
        private int _modelsProgressValue = 0;
        private string _modelsSizeReportText = string.Empty;
        private string _pipelineStatusText = string.Empty;
        private string _pipelineProgressText = string.Empty;
        private string _pipelineSummaryText = string.Empty;
        private string _pipelineSizeReportText = string.Empty;
        private int _pipelineProgressMinValue = 0;
        private int _pipelineProgressMaxValue = 100;
        private int _pipelineProgressValue = 0;
        private bool _pipelineIsRunning = false;
        private bool _unlockedUI = true;
        private string _blenderPath = string.Empty;
        private string _studioMdlPath = string.Empty;
        private string _optimizerSuffix = "_optimized";
        private int _optimizerPresetIndex = 0;
        private bool _optimizerPresetIsCustom = false;
        private double _optimizerRatio = 0.50;
        private double _optimizerMerge = 0.0;
        private double _optimizerAutoSmooth = 45.0;
        private bool _optimizerUsePlanar = false;
        private double _optimizerPlanarAngle = 2.0;
        private bool _optimizerUseExperimentalGroundPolicy = false;
        private bool _optimizerUseExperimentalRoundPartsPolicy = false;
        private bool _optimizerUseExperimentalSteerTurnBasisFix = false;
        private int _optimizerFormatIndex = 0;
        private int _optimizerJobs = 0;
        private int _optimizerDecompileJobs = 1;
        private int _optimizerCompileJobs = 1;
        private bool _optimizerStrict = false;
        private bool _optimizerResumeOpt = false;
        private bool _optimizerOverwrite = false;
        private bool _optimizerOverwriteWork = false;
        private bool _optimizerRestoreSkins = true;
        private bool _optimizerCompileVerbose = false;
        private bool _optimizerCleanupWorkModelArtifacts = true;
        private bool _compressVTF = true;
        private bool _compressWAV = true;
        private bool _compressMP3 = true;
        private bool _compressOGG = true;
        private bool _compressJPG = true;
        private bool _compressPNG = true;
        private bool _compressLUA = false;
        private bool _audioLoopSafe = true;
        private bool _changeOriginalCodeToMinimalistic = false;
        private bool _reduceExactlyToLimits = false;
        private bool _reduceExactlyToResolution = true;
        private bool _keepImageAspectRatio = true;
        private bool _imageMagickVTFCompress = false;
        private int _compressModeIndex = 0;
        private bool _compressMagickUseCommonVtf = true;
        private bool _compressMagickUseAggressivePng = false;
        private uint _imageSkipWidth = 0;
        private uint _imageSkipHeight = 0;
        private int _wavRate = 22050;
        private int _wavRateListIndex = 0;
        private int _wavChannelsIndex = 0;
        private int _wavCodecIndex = 0;
        private int _mp3RateIndex = 0;
        private int _mp3BitrateIndex = 0;
        private int _oggRateIndex = 1;
        private int _oggChannelsIndex = 1;
        private int _oggQualityIndex = 4;
        private int _imageReducingResolutionListIndex = 0;
        private int _imageWidthLimitIndex = 10;
        private int _imageHeightLimitIndex = 10;
        private int[] _imageReducingResolutionList = new int[]
        {
            2,
            4,
            6,
            8,
            10,
            12,
        };
        private uint[] _imageSizeLimitList = new uint[]
        {
            1,
            2,
            4,
            8,
            16,
            32,
            64,
            128,
            256,
            512,
            1024,
            2048,
            4096,
        };
        private int[] _wavRateList = new int[]
        {
            44100,
            22050,
            11025
        };
        private string[] _wavChannelList = new string[]
        {
            "Stereo",
            "Mono"
        };
        private string[] _wavCodecList = new string[]
        {
            "PCM 16-bit",
            "ADPCM (Microsoft)"
        };
        private int[] _audioBitrateList = new int[]
        {
            96,
            80,
            64,
            128
        };
        private string[] _oggQualityList = new string[]
        {
            "q-1 (ultra aggressive, experimental)",
            "q0 (very aggressive)",
            "q1 (aggressive)",
            "q2 (aggressive+)",
            "q3 (balanced, DEFAULT)",
            "q4 (quality+)",
            "q5 (high quality)",
            "q6 (very high quality)",
            "q7 (near lossless)",
            "q8 (near lossless+)",
            "q9 (transparent)",
            "q10 (max quality, overkill)"
        };
        private string[] _optimizerPresetList = new string[]
        {
            "Safe",
            "Aggressive",
            "Custom"
        };
        private string[] _optimizerFormatList = new string[]
        {
            "smd",
            "dmx"
        };
        private string[] _compressModeList = new string[]
        {
            "Padrao",
            "Magick"
        };

        public uint ImageSkipHeight
        {
            get { return _imageSkipHeight; }
            set
            {
                _imageSkipHeight = value;
                OnPropertyChanged();
            }
        }

        public uint ImageSkipWidth
        {
            get { return _imageSkipWidth; }
            set
            {
                _imageSkipWidth = value;
                OnPropertyChanged();
            }
        }

        public string[] CompressModeList
        {
            get { return _compressModeList; }
            set
            {
                _compressModeList = value;
                OnPropertyChanged();
            }
        }

        public int CompressModeIndex
        {
            get { return _compressModeIndex; }
            set
            {
                _compressModeIndex = value;
                OnPropertyChanged();
                NotifyCompressModePropertiesChanged();
            }
        }

        public bool CompressModeIsStandard => _compressModeIndex == 0;
        public bool CompressModeIsMagick => _compressModeIndex == 1;
        public Visibility CompressStandardOptionsVisibility => CompressModeIsStandard ? Visibility.Visible : Visibility.Collapsed;
        public Visibility CompressMagickOptionsVisibility => CompressModeIsMagick ? Visibility.Visible : Visibility.Collapsed;

        public bool CompressMagickUseCommonVtf
        {
            get { return _compressMagickUseCommonVtf; }
            set
            {
                _compressMagickUseCommonVtf = value;
                OnPropertyChanged();
                NotifyCompressModePropertiesChanged();
            }
        }

        public bool CompressMagickUseAggressivePng
        {
            get { return _compressMagickUseAggressivePng; }
            set
            {
                _compressMagickUseAggressivePng = value;
                OnPropertyChanged();
                NotifyCompressModePropertiesChanged();
            }
        }

        public string CompressModeDescriptionText =>
            CompressModeIsMagick
                ? "Magick mode adds a second path without replacing the current compressor. Common VTF goes through Magick first and special/problematic VTF falls back to the current standard pipeline."
                : "Standard mode keeps the current Compress behavior for every selected type. The existing pipeline stays the default.";

        public string CompressModeRoutingText
        {
            get
            {
                if (CompressModeIsStandard)
                {
                    string legacy = ImageMagickVTFCompress
                        ? " Legacy standard VTF demo is enabled for Standard mode."
                        : string.Empty;
                    return "Selected types use the current compressor. VTF, PNG, JPG/JPEG, WAV, MP3, OGG and LUA stay on the existing path." + legacy;
                }

                string vtfText = CompressMagickUseCommonVtf
                    ? "Common VTF: Magick path first, with automatic fallback to Standard for special/problematic VTF or when Magick cannot improve the file."
                    : "VTF: Standard path only.";
                string pngText = CompressMagickUseAggressivePng
                    ? "PNG: Magick q256 aggressive path first, with Standard fallback on failure or no gain."
                    : "PNG: Standard path only.";

                return $"{vtfText} {pngText} JPG/JPEG, WAV, MP3, OGG and LUA always stay on the Standard path. The legacy VTF demo checkbox is ignored while Magick mode is selected.";
            }
        }

        public uint[] ImageSizeLimitList
        {
            get { return _imageSizeLimitList; }
            set
            {
                _imageSizeLimitList = value;
                OnPropertyChanged();
            }
        }

        public int ImageWidthLimitIndex
        {
            get { return _imageWidthLimitIndex; }
            set
            {
                _imageWidthLimitIndex = value;
                OnPropertyChanged();
            }
        }

        public int ImageHeightLimitIndex
        {
            get { return _imageHeightLimitIndex; }
            set
            {
                _imageHeightLimitIndex = value;
                OnPropertyChanged();
            }
        }

        public int ImageReducingResolutionListIndex
        {
            get { return _imageReducingResolutionListIndex; }
            set
            {
                _imageReducingResolutionListIndex = value;
                OnPropertyChanged();
            }
        }

        public int[] ImageReducingResolutionList
        {
            get { return _imageReducingResolutionList; }
            set
            {
                _imageReducingResolutionList = value;
                OnPropertyChanged();
            }
        }

        public int WavRateListIndex
        {
            get { return _wavRateListIndex; }
            set
            {
                _wavRateListIndex = value;
                OnPropertyChanged();
            }
        }

        public int[] WavRateList
        {
            get { return _wavRateList; }
            set
            {
                _wavRateList = value;
                OnPropertyChanged();
            }
        }

        public string[] WavChannelList
        {
            get { return _wavChannelList; }
            set
            {
                _wavChannelList = value;
                OnPropertyChanged();
            }
        }

        public string[] WavCodecList
        {
            get { return _wavCodecList; }
            set
            {
                _wavCodecList = value;
                OnPropertyChanged();
            }
        }

        public int[] AudioBitrateList
        {
            get { return _audioBitrateList; }
            set
            {
                _audioBitrateList = value;
                OnPropertyChanged();
            }
        }

        public string[] OggQualityList
        {
            get { return _oggQualityList; }
            set
            {
                _oggQualityList = value;
                OnPropertyChanged();
            }
        }

        public int WavRate
        {
            get { return _wavRate; }
            set
            {
                _wavRate = value;
                OnPropertyChanged();
            }
        }

        public bool ImageMagickVTFCompress
        {
            get { return _imageMagickVTFCompress; }
            set
            {
                _imageMagickVTFCompress = value;
                OnPropertyChanged();
                NotifyCompressModePropertiesChanged();
            }
        }

        public bool KeepImageAspectRatio
        {
            get { return _keepImageAspectRatio; }
            set
            {
                _keepImageAspectRatio = value;
                OnPropertyChanged();
            }
        }

        public bool ReduceExactlyToResolution
        {
            get { return _reduceExactlyToResolution; }
            set
            {
                _reduceExactlyToResolution = value;
                OnPropertyChanged();
            }
        }

        public bool ReduceExactlyToLimits
        {
            get { return _reduceExactlyToLimits; }
            set
            {
                _reduceExactlyToLimits = value;
                ReduceExactlyToResolution = !_reduceExactlyToLimits;
                OnPropertyChanged();
            }
        }

        public bool ChangeOriginalCodeToMinimalistic
        {
            get { return _changeOriginalCodeToMinimalistic; }
            set
            {
                _changeOriginalCodeToMinimalistic = value;
                OnPropertyChanged();
            }
        }

        public bool AudioLoopSafe
        {
            get { return _audioLoopSafe; }
            set
            {
                _audioLoopSafe = value;
                OnPropertyChanged();
            }
        }

        public bool CompressLUA
        {
            get { return _compressLUA; }
            set
            {
                _compressLUA = value;
                OnPropertyChanged();
            }
        }

        public bool CompressPNG
        {
            get { return _compressPNG; }
            set
            {
                _compressPNG = value;
                OnPropertyChanged();
            }
        }

        public bool CompressJPG
        {
            get { return _compressJPG; }
            set
            {
                _compressJPG = value;
                OnPropertyChanged();
            }
        }

        public bool CompressVTF
        {
            get { return _compressVTF; }
            set
            {
                _compressVTF = value;
                OnPropertyChanged();
            }
        }

        public bool CompressOGG
        {
            get { return _compressOGG; }
            set
            {
                _compressOGG = value;
                OnPropertyChanged();
                NotifyCompressAudioProfilePropertiesChanged();
            }
        }

        public bool CompressMP3
        {
            get { return _compressMP3; }
            set
            {
                _compressMP3 = value;
                OnPropertyChanged();
                NotifyCompressAudioProfilePropertiesChanged();
            }
        }

        public bool CompressWAV
        {
            get { return _compressWAV; }
            set
            {
                _compressWAV = value;
                OnPropertyChanged();
                NotifyCompressAudioProfilePropertiesChanged();
            }
        }

        public int WavChannelsIndex
        {
            get { return _wavChannelsIndex; }
            set
            {
                _wavChannelsIndex = value;
                OnPropertyChanged();
            }
        }

        public int WavCodecIndex
        {
            get { return _wavCodecIndex; }
            set
            {
                _wavCodecIndex = value;
                OnPropertyChanged();
            }
        }

        public int Mp3RateIndex
        {
            get { return _mp3RateIndex; }
            set
            {
                _mp3RateIndex = value;
                OnPropertyChanged();
            }
        }

        public int Mp3BitrateIndex
        {
            get { return _mp3BitrateIndex; }
            set
            {
                _mp3BitrateIndex = value;
                OnPropertyChanged();
            }
        }

        public int OggRateIndex
        {
            get { return _oggRateIndex; }
            set
            {
                _oggRateIndex = value;
                OnPropertyChanged();
            }
        }

        public int OggChannelsIndex
        {
            get { return _oggChannelsIndex; }
            set
            {
                _oggChannelsIndex = value;
                OnPropertyChanged();
            }
        }

        public int OggQualityIndex
        {
            get { return _oggQualityIndex; }
            set
            {
                _oggQualityIndex = value;
                OnPropertyChanged();
            }
        }

        public bool UnlockedUI
        {
            get { return _unlockedUI; }
            set
            {
                _unlockedUI = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(PipelineCanStart));
            }
        }

        public bool PipelineIsRunning
        {
            get { return _pipelineIsRunning; }
            set
            {
                _pipelineIsRunning = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(PipelineCanStart));
            }
        }

        public bool PipelineCanStart => _unlockedUI && !_pipelineIsRunning;

        public string AddonDirectoryPath
        {
            get { return _addonDirectoryPath; }
            set
            {
                _addonDirectoryPath = value;
                OnPropertyChanged();
            }
        }

        public string AddonWorkshopWarningText
        {
            get { return _addonWorkshopWarningText; }
            set
            {
                _addonWorkshopWarningText = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(AddonWorkshopWarningVisibility));
                OnPropertyChanged(nameof(AddonWorkshopWarningCount));
                OnPropertyChanged(nameof(AddonWorkshopWarningHeaderText));
            }
        }

        public Visibility AddonWorkshopWarningVisibility =>
            string.IsNullOrWhiteSpace(_addonWorkshopWarningText) ? Visibility.Collapsed : Visibility.Visible;

        public int AddonWorkshopWarningCount
        {
            get
            {
                if (string.IsNullOrWhiteSpace(_addonWorkshopWarningText))
                    return 0;

                int count = 0;
                var lines = _addonWorkshopWarningText.Split(new[] { "\r\n", "\n" }, System.StringSplitOptions.None);

                foreach (string line in lines)
                {
                    string trimmed = line.Trim();
                    if (trimmed.StartsWith("- "))
                    {
                        count++;
                        continue;
                    }

                    if (!trimmed.StartsWith("+ "))
                        continue;

                    var hiddenPrefix = trimmed.Substring(2).Split(' ', 2, System.StringSplitOptions.RemoveEmptyEntries);
                    if (hiddenPrefix.Length == 0)
                        continue;

                    if (int.TryParse(hiddenPrefix[0], out int hiddenMatches))
                        count += hiddenMatches;
                }

                return count > 0 ? count : 1;
            }
        }

        public string AddonWorkshopWarningHeaderText => $"Workshop warning ({AddonWorkshopWarningCount})";

        public Visibility CompressWavProfileVisibility => _compressWAV ? Visibility.Visible : Visibility.Collapsed;
        public Visibility CompressMp3ProfileVisibility => _compressMP3 ? Visibility.Visible : Visibility.Collapsed;
        public Visibility CompressOggProfileVisibility => _compressOGG ? Visibility.Visible : Visibility.Collapsed;
        public Visibility CompressAnyAudioProfileVisibility => (_compressWAV || _compressMP3 || _compressOGG) ? Visibility.Visible : Visibility.Collapsed;
        public Visibility CompressNoAudioProfileVisibility => (_compressWAV || _compressMP3 || _compressOGG) ? Visibility.Collapsed : Visibility.Visible;

        public int ProgressBarMinValue
        {
            get { return _progressBarMinValue; }
            set
            {
                _progressBarMinValue = value;
                OnPropertyChanged();
            }
        }

        public int ProgressBarMaxValue
        {
            get { return _progressBarMaxValue; }
            set
            {
                _progressBarMaxValue = value;
                OnPropertyChanged();
            }
        }

        public int ProgressBarValue
        {
            get { return _progressBarValue; }
            set
            {
                _progressBarValue = value;
                OnPropertyChanged();
            }
        }

        public string ProgressBarText
        {
            get { return _progressBarText; }
            set
            {
                _progressBarText = value;
                OnPropertyChanged();
            }
        }

        public string CompressSizeReportText
        {
            get { return _compressSizeReportText; }
            set
            {
                _compressSizeReportText = value;
                OnPropertyChanged();
            }
        }

        public string BlenderPath
        {
            get { return _blenderPath; }
            set
            {
                _blenderPath = value;
                OnPropertyChanged();
            }
        }

        public string StudioMdlPath
        {
            get { return _studioMdlPath; }
            set
            {
                _studioMdlPath = value;
                OnPropertyChanged();
            }
        }

        public string OptimizerSuffix
        {
            get { return _optimizerSuffix; }
            set
            {
                _optimizerSuffix = value;
                OnPropertyChanged();
            }
        }

        public int OptimizerPresetIndex
        {
            get { return _optimizerPresetIndex; }
            set
            {
                _optimizerPresetIndex = value;
                OptimizerPresetIsCustom = _optimizerPresetIndex == 2;
                OnPropertyChanged();
            }
        }

        public bool OptimizerPresetIsCustom
        {
            get { return _optimizerPresetIsCustom; }
            private set
            {
                _optimizerPresetIsCustom = value;
                OnPropertyChanged();
            }
        }

        public string[] OptimizerPresetList
        {
            get { return _optimizerPresetList; }
            set
            {
                _optimizerPresetList = value;
                OnPropertyChanged();
            }
        }

        public double OptimizerRatio
        {
            get { return _optimizerRatio; }
            set
            {
                _optimizerRatio = ClampOptimizerRatio(value);
                OnPropertyChanged();
            }
        }

        public double OptimizerMerge
        {
            get { return _optimizerMerge; }
            set
            {
                _optimizerMerge = value;
                OnPropertyChanged();
            }
        }

        public double OptimizerAutoSmooth
        {
            get { return _optimizerAutoSmooth; }
            set
            {
                _optimizerAutoSmooth = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerUsePlanar
        {
            get { return _optimizerUsePlanar; }
            set
            {
                _optimizerUsePlanar = value;
                OnPropertyChanged();
            }
        }

        public double OptimizerPlanarAngle
        {
            get { return _optimizerPlanarAngle; }
            set
            {
                _optimizerPlanarAngle = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerUseExperimentalGroundPolicy
        {
            get { return _optimizerUseExperimentalGroundPolicy; }
            set
            {
                _optimizerUseExperimentalGroundPolicy = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerUseExperimentalRoundPartsPolicy
        {
            get { return _optimizerUseExperimentalRoundPartsPolicy; }
            set
            {
                _optimizerUseExperimentalRoundPartsPolicy = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerUseExperimentalSteerTurnBasisFix
        {
            get { return _optimizerUseExperimentalSteerTurnBasisFix; }
            set
            {
                _optimizerUseExperimentalSteerTurnBasisFix = value;
                OnPropertyChanged();
            }
        }

        public int OptimizerFormatIndex
        {
            get { return _optimizerFormatIndex; }
            set
            {
                _optimizerFormatIndex = value;
                OnPropertyChanged();
            }
        }

        public string[] OptimizerFormatList
        {
            get { return _optimizerFormatList; }
            set
            {
                _optimizerFormatList = value;
                OnPropertyChanged();
            }
        }

        public int OptimizerJobs
        {
            get { return _optimizerJobs; }
            set
            {
                _optimizerJobs = value;
                OnPropertyChanged();
            }
        }

        public int OptimizerDecompileJobs
        {
            get { return _optimizerDecompileJobs; }
            set
            {
                _optimizerDecompileJobs = value;
                OnPropertyChanged();
            }
        }

        public int OptimizerCompileJobs
        {
            get { return _optimizerCompileJobs; }
            set
            {
                _optimizerCompileJobs = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerStrict
        {
            get { return _optimizerStrict; }
            set
            {
                _optimizerStrict = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerResumeOpt
        {
            get { return _optimizerResumeOpt; }
            set
            {
                _optimizerResumeOpt = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerOverwrite
        {
            get { return _optimizerOverwrite; }
            set
            {
                _optimizerOverwrite = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerOverwriteWork
        {
            get { return _optimizerOverwriteWork; }
            set
            {
                _optimizerOverwriteWork = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerRestoreSkins
        {
            get { return _optimizerRestoreSkins; }
            set
            {
                _optimizerRestoreSkins = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerCompileVerbose
        {
            get { return _optimizerCompileVerbose; }
            set
            {
                _optimizerCompileVerbose = value;
                OnPropertyChanged();
            }
        }

        public bool OptimizerCleanupWorkModelArtifacts
        {
            get { return _optimizerCleanupWorkModelArtifacts; }
            set
            {
                _optimizerCleanupWorkModelArtifacts = value;
                OnPropertyChanged();
            }
        }

        public string ModelsStatusText
        {
            get { return _modelsStatusText; }
            set
            {
                _modelsStatusText = value;
                OnPropertyChanged();
            }
        }

        public string ModelsProgressText
        {
            get { return _modelsProgressText; }
            set
            {
                _modelsProgressText = value;
                OnPropertyChanged();
            }
        }

        public string ModelsSizeReportText
        {
            get { return _modelsSizeReportText; }
            set
            {
                _modelsSizeReportText = value;
                OnPropertyChanged();
            }
        }

        public int ModelsProgressMinValue
        {
            get { return _modelsProgressMinValue; }
            set
            {
                _modelsProgressMinValue = value;
                OnPropertyChanged();
            }
        }

        public int ModelsProgressMaxValue
        {
            get { return _modelsProgressMaxValue; }
            set
            {
                _modelsProgressMaxValue = value;
                OnPropertyChanged();
            }
        }

        public int ModelsProgressValue
        {
            get { return _modelsProgressValue; }
            set
            {
                _modelsProgressValue = value;
                OnPropertyChanged();
            }
        }

        public string PipelineStatusText
        {
            get { return _pipelineStatusText; }
            set
            {
                _pipelineStatusText = value;
                OnPropertyChanged();
            }
        }

        public string PipelineProgressText
        {
            get { return _pipelineProgressText; }
            set
            {
                _pipelineProgressText = value;
                OnPropertyChanged();
            }
        }

        public string PipelineSummaryText
        {
            get { return _pipelineSummaryText; }
            set
            {
                _pipelineSummaryText = value;
                OnPropertyChanged();
            }
        }

        public string PipelineSizeReportText
        {
            get { return _pipelineSizeReportText; }
            set
            {
                _pipelineSizeReportText = value;
                OnPropertyChanged();
            }
        }

        public int PipelineProgressMinValue
        {
            get { return _pipelineProgressMinValue; }
            set
            {
                _pipelineProgressMinValue = value;
                OnPropertyChanged();
            }
        }

        public int PipelineProgressMaxValue
        {
            get { return _pipelineProgressMaxValue; }
            set
            {
                _pipelineProgressMaxValue = value;
                OnPropertyChanged();
            }
        }

        public int PipelineProgressValue
        {
            get { return _pipelineProgressValue; }
            set
            {
                _pipelineProgressValue = value;
                OnPropertyChanged();
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;

        private void NotifyCompressModePropertiesChanged()
        {
            OnPropertyChanged(nameof(CompressModeIsStandard));
            OnPropertyChanged(nameof(CompressModeIsMagick));
            OnPropertyChanged(nameof(CompressStandardOptionsVisibility));
            OnPropertyChanged(nameof(CompressMagickOptionsVisibility));
            OnPropertyChanged(nameof(CompressModeDescriptionText));
            OnPropertyChanged(nameof(CompressModeRoutingText));
        }

        private void NotifyCompressAudioProfilePropertiesChanged()
        {
            OnPropertyChanged(nameof(CompressWavProfileVisibility));
            OnPropertyChanged(nameof(CompressMp3ProfileVisibility));
            OnPropertyChanged(nameof(CompressOggProfileVisibility));
            OnPropertyChanged(nameof(CompressAnyAudioProfileVisibility));
            OnPropertyChanged(nameof(CompressNoAudioProfileVisibility));
        }

        public void OnPropertyChanged([CallerMemberName] string? propertyName = null)
        {
            if (PropertyChanged != null && propertyName != null)
                PropertyChanged(this, new PropertyChangedEventArgs(propertyName));
        }
    }
}
