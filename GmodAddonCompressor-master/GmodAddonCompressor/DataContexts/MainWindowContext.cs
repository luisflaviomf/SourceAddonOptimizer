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
        private string _compressMaximumStatusText = string.Empty;
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
        private int _optimizerModeIndex = 0;
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
        private bool _optimizerUseVtxTriStrips = true;
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
        private int _compressModeIndex = 0;
        private bool _compressMagickUseAggressivePng = false;
        private uint _imageSkipWidth = 0;
        private uint _imageSkipHeight = 0;
        private int _wavRate = 22050;
        private int _wavRateListIndex = 0;
        private int _wavChannelsIndex = 0;
        private int _wavCodecIndex = 0;
        private int _wavOptimizationIndex = 0;
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
            "Estéreo",
            "Mono"
        };
        private string[] _wavCodecList = new string[]
        {
            "PCM 16-bit",
            "ADPCM (Microsoft)"
        };
        private string[] _wavOptimizationList = new string[]
        {
            "Manual",
            "Adaptativo conservador"
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
            "q-1 (mínima, experimental)",
            "q0 (muito baixa)",
            "q1 (baixa)",
            "q2 (baixa+)",
            "q3 (equilibrada, padrão)",
            "q4 (boa)",
            "q5 (alta)",
            "q6 (muito alta)",
            "q7 (quase sem perda)",
            "q8 (quase sem perda+)",
            "q9 (transparente)",
            "q10 (máxima)"
        };
        private string[] _optimizerPresetList = new string[]
        {
            "Seguro",
            "Agressivo",
            "Personalizado"
        };
        private string[] _optimizerFormatList = new string[]
        {
            "smd",
            "dmx"
        };
        private string[] _optimizerModeList = new string[]
        {
            "Normal (Blender)",
            "Fidelidade (Blender)",
            "Maximum (Blender)",
            "Padrão"
        };
        private string[] _modelsScreenProfileList = new string[]
        {
            "Equilibrado",
            "Forte",
            "Forte + espelho",
            "Stream (experimental)",
            "Sem perda"
        };
        // Default "Padrão" (screen-aware, worker modscreen) profile: Forte + espelho (index 2): Forte's limits with the remesh engine and
        // mirror instancing, chosen after in-game tests on the LVS cars pack (models -61% vs -53%).
        internal const int DefaultModelsScreenProfileIndex = 2;
        private int _modelsScreenProfileIndex = DefaultModelsScreenProfileIndex;
        private readonly string[] _compressScreenProfileList = new string[]
        {
            "Equilibrado",
            "Forte",
            "Máximo",
            "Sem perda"
        };
        private int _compressScreenProfileIndex;
        private string[] _compressModeList = new string[]
        {
            "Clássico",
            "Magick",
            "Maximum",
            "Magick+",
            "Padrão"
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

        public string[] OptimizerModeList
        {
            get { return _optimizerModeList; }
            set
            {
                _optimizerModeList = value;
                OnPropertyChanged();
            }
        }

        public int OptimizerModeIndex
        {
            get { return _optimizerModeIndex; }
            set
            {
                _optimizerModeIndex = value;
                OnPropertyChanged();
                NotifyOptimizerModePropertiesChanged();
            }
        }

        public bool OptimizerModeIsNormal => _optimizerModeIndex == 0;
        public bool OptimizerModeIsFidelity => _optimizerModeIndex == 1;
        public bool OptimizerModeIsMaximum => _optimizerModeIndex == 2;
        public bool OptimizerModeIsScreen => _optimizerModeIndex == 3;

        public bool OptimizerModeScreenChecked
        {
            get { return _optimizerModeIndex == 3; }
            set
            {
                if (value)
                {
                    OptimizerModeIndex = 3;
                    return;
                }

                if (_optimizerModeIndex == 3)
                    OnPropertyChanged();
            }
        }

        public string[] ModelsScreenProfileList => _modelsScreenProfileList;

        public int ModelsScreenProfileIndex
        {
            get { return _modelsScreenProfileIndex; }
            set
            {
                _modelsScreenProfileIndex = value < 0 || value >= _modelsScreenProfileList.Length ? DefaultModelsScreenProfileIndex : value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(ModelsLevel));
                OnPropertyChanged(nameof(OptimizerModeDescriptionText));
            }
        }
        public bool OptimizerModeNormalChecked
        {
            get { return _optimizerModeIndex == 0; }
            set
            {
                if (value)
                {
                    OptimizerModeIndex = 0;
                    return;
                }

                if (_optimizerModeIndex == 0)
                    OnPropertyChanged();
            }
        }

        public bool OptimizerModeFidelityChecked
        {
            get { return _optimizerModeIndex == 1; }
            set
            {
                if (value)
                {
                    OptimizerModeIndex = 1;
                    return;
                }

                if (_optimizerModeIndex == 1)
                    OnPropertyChanged();
            }
        }

        public bool OptimizerModeMaximumChecked
        {
            get { return _optimizerModeIndex == 2; }
            set
            {
                if (value)
                {
                    OptimizerModeIndex = 2;
                    return;
                }

                if (_optimizerModeIndex == 2)
                    OnPropertyChanged();
            }
        }

        public string OptimizerModeDescriptionText =>
            OptimizerModeIsScreen
                ? (_modelsScreenProfileIndex == 4
                    ? "Modelos intactos; só o empacotamento que não muda a aparência (VTX antigos removidos, normais exatas, VTX compactado)."
                    : _modelsScreenProfileIndex == 3
                    ? "Maior redução: os modelos grandes guardam a geometria original comprimida e são desenhados por Lua. Experimental; exige Lua do cliente ligada."
                    : _modelsScreenProfileIndex == 2
                    ? "Como o Forte, usando a simetria do modelo: o lado direito vira espelho do esquerdo. Cerca de 60% menor."
                    : _modelsScreenProfileIndex == 1
                    ? "Cerca de 50% menor; pequenas diferenças de perto."
                    : "Quase idêntico ao original.")
                : OptimizerModeIsMaximum
                ? "Parte do Normal agressivo e devolve as regiões que falham nas checagens de forma, normais, UV e materiais."
                : OptimizerModeIsFidelity
                ? "Como o Normal, com proteções extras para carroceria, peças redondas e volante."
                : "Descompila, reduz pela proporção do preset e recompila com studiomdl.";

        /// <summary>True when one of the old Blender recompile modes (Normal, Fidelity, Maximum) is selected.</summary>
        public bool OptimizerModeIsLegacy => !OptimizerModeIsScreen;

        /// <summary>
        /// Modelos › "Quanto reduzir": the profile of the Padrão (screen) mode, or -1 while a Blender method
        /// is selected instead. Picking a level also switches the method back to Padrão.
        /// </summary>
        public int ModelsLevel
        {
            get => OptimizerModeIsScreen ? _modelsScreenProfileIndex : -1;
            set
            {
                if (value < 0)
                    return;
                ModelsScreenProfileIndex = value;
                if (!OptimizerModeIsScreen)
                    OptimizerModeIndex = 3;
                OnPropertyChanged();
            }
        }

        /// <summary>Texturas › "Quanto reduzir": same idea for the Padrão (screen) texture mode.</summary>
        public int TexturesLevel
        {
            get => CompressModeIsScreen ? _compressScreenProfileIndex : -1;
            set
            {
                if (value < 0)
                    return;
                CompressScreenProfileIndex = value;
                if (!CompressModeIsScreen)
                    CompressModeIndex = 4;
                OnPropertyChanged();
            }
        }

        public string OptimizerLegacyModeNotice => OptimizerModeIsLegacy
            ? $"Usando {_optimizerModeList[Math.Clamp(_optimizerModeIndex, 0, _optimizerModeList.Length - 1)]}. Para voltar ao normal, escolha um nível acima."
            : string.Empty;

        public bool CompressModeIsStandard => _compressModeIndex == 0;
        public bool CompressModeIsMagick => _compressModeIndex == 1;
        public bool CompressModeIsMaximum => _compressModeIndex == 2;
        public bool CompressModeIsMagickPlus => _compressModeIndex == 3;
        public bool CompressModeIsScreen => _compressModeIndex == 4;
        public Visibility CompressScreenOptionsVisibility => CompressModeIsScreen ? Visibility.Visible : Visibility.Collapsed;
        public string[] CompressScreenProfileList => _compressScreenProfileList;

        public int CompressScreenProfileIndex
        {
            get { return _compressScreenProfileIndex; }
            set
            {
                _compressScreenProfileIndex = value < 0 || value >= _compressScreenProfileList.Length ? 0 : value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(CompressScreenProfileName));
                OnPropertyChanged(nameof(CompressScreenProfileHint));
                OnPropertyChanged(nameof(TexturesLevel));
                OnPropertyChanged(nameof(CompressModeRoutingText));
            }
        }

        // texscreen profile names (texlab/screen_compress.py PROFILES)
        public string CompressScreenProfileName => _compressScreenProfileIndex switch
        {
            1 => "strong",
            2 => "max",
            3 => "exact",
            _ => "q50"
        };
        public Visibility CompressStandardOptionsVisibility => CompressModeIsStandard ? Visibility.Visible : Visibility.Collapsed;
        public Visibility CompressMagickOptionsVisibility => CompressModeIsMagick || CompressModeIsMagickPlus ? Visibility.Visible : Visibility.Collapsed;
        public Visibility CompressMaximumOptionsVisibility => CompressModeIsMaximum ? Visibility.Visible : Visibility.Collapsed;

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
            CompressModeIsScreen
                ? "Reduz cada textura conforme o quanto ela aparece na tela do jogo."
                : CompressModeIsMaximum
                ? "Reduz cada VTF ao menos 2x e continua enquanto as checagens de qualidade passam."
                : CompressModeIsMagickPlus
                ? "Magick com conversão exata BC3→BC1 quando o alpha não é usado."
                : CompressModeIsMagick
                ? "Como o Clássico; PNG pode usar o modo agressivo do ImageMagick."
                : "Reduz cada textura pelo fator escolhido em Redução de imagens, respeitando os limites.";

        /// <summary>One line about the selected "Padrão" texture profile.</summary>
        public string CompressScreenProfileHint => _compressScreenProfileIndex switch
        {
            1 => "Até 8x onde a textura nunca aparece de perto; textos e texturas pequenas ficam em 2x.",
            2 => "No mínimo 4x, até 8x. Perda visível de perto.",
            3 => "Não reduz; só converte BC3→BC1 quando o alpha não é usado, sem mudar nenhuma cor.",
            _ => "Reduz só onde a perda é menor que a de um 2x."
        };

        /// <summary>True when an old texture mode (Clássico, Magick, Maximum, Magick+) is selected.</summary>
        public bool CompressModeIsLegacy => !CompressModeIsScreen;

        public string CompressLegacyModeNotice => CompressModeIsLegacy
            ? $"Usando o método {_compressModeList[Math.Clamp(_compressModeIndex, 0, _compressModeList.Length - 1)]}. Para voltar ao normal, escolha um nível acima."
            : string.Empty;

        public string CompressModeRoutingText
        {
            get
            {
                string pngText = (CompressModeIsMagick || CompressModeIsMagickPlus) && CompressMagickUseAggressivePng
                    ? "PNG: ImageMagick agressivo (q256), com o Clássico se falhar."
                    : "PNG: Clássico.";

                if (CompressModeIsScreen)
                    return $"VTF: motor Padrão, perfil {CompressScreenProfileName}. VTF fora de DXT ou sem mipmaps usam o Clássico. PNG, JPG, sons e Lua usam o Clássico.";

                if (CompressModeIsMaximum)
                    return "VTF: lote Maximum (VTF universal) com checagens de qualidade por nível. Os outros tipos usam o Clássico.";

                if (CompressModeIsMagickPlus)
                    return $"VTF: Magick e depois conversão exata BC3→BC1 quando o alpha não é usado. {pngText} JPG, sons e Lua usam o Clássico.";

                if (CompressModeIsMagick)
                    return $"VTF: mesmo caminho do Clássico. {pngText} JPG, sons e Lua usam o Clássico.";

                return "Todos os tipos marcados usam o Clássico.";
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

        public string[] WavOptimizationList
        {
            get { return _wavOptimizationList; }
            set
            {
                _wavOptimizationList = value;
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

        public int WavOptimizationIndex
        {
            get { return _wavOptimizationIndex; }
            set
            {
                _wavOptimizationIndex = value;
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

        public string AddonWorkshopWarningHeaderText => $"Referências à Workshop ({AddonWorkshopWarningCount})";

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

        public string CompressMaximumStatusText
        {
            get { return _compressMaximumStatusText; }
            set
            {
                _compressMaximumStatusText = value;
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

        public bool OptimizerUseVtxTriStrips
        {
            get { return _optimizerUseVtxTriStrips; }
            set
            {
                _optimizerUseVtxTriStrips = value;
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
            OnPropertyChanged(nameof(CompressScreenOptionsVisibility));
            OnPropertyChanged(nameof(CompressModeIsStandard));
            OnPropertyChanged(nameof(CompressModeIsMagick));
            OnPropertyChanged(nameof(CompressModeIsMaximum));
            OnPropertyChanged(nameof(CompressModeIsMagickPlus));
            OnPropertyChanged(nameof(CompressModeIsScreen));
            OnPropertyChanged(nameof(CompressStandardOptionsVisibility));
            OnPropertyChanged(nameof(CompressMagickOptionsVisibility));
            OnPropertyChanged(nameof(CompressMaximumOptionsVisibility));
            OnPropertyChanged(nameof(CompressModeDescriptionText));
            OnPropertyChanged(nameof(CompressModeRoutingText));
            OnPropertyChanged(nameof(CompressModeIsLegacy));
            OnPropertyChanged(nameof(CompressLegacyModeNotice));
            OnPropertyChanged(nameof(TexturesLevel));
        }

        private void NotifyOptimizerModePropertiesChanged()
        {
            OnPropertyChanged(nameof(OptimizerModeIsNormal));
            OnPropertyChanged(nameof(OptimizerModeIsFidelity));
            OnPropertyChanged(nameof(OptimizerModeIsMaximum));
            OnPropertyChanged(nameof(OptimizerModeNormalChecked));
            OnPropertyChanged(nameof(OptimizerModeFidelityChecked));
            OnPropertyChanged(nameof(OptimizerModeMaximumChecked));
            OnPropertyChanged(nameof(OptimizerModeIsScreen));
            OnPropertyChanged(nameof(OptimizerModeScreenChecked));
            OnPropertyChanged(nameof(OptimizerModeDescriptionText));
            OnPropertyChanged(nameof(OptimizerModeIsLegacy));
            OnPropertyChanged(nameof(OptimizerLegacyModeNotice));
            OnPropertyChanged(nameof(ModelsLevel));
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
