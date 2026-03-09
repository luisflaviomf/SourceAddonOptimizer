namespace GmodAddonCompressor.Models
{
    internal sealed class OptimizerCustomParams
    {
        public double Ratio { get; set; } = 0.50;
        public double Merge { get; set; } = 0.0;
        public double AutoSmooth { get; set; } = 45.0;
        public string Format { get; set; } = "smd";
        public int Jobs { get; set; } = 0;
        public int DecompileJobs { get; set; } = 1;
        public int CompileJobs { get; set; } = 1;
        public bool Strict { get; set; } = false;
        public bool ResumeOpt { get; set; } = false;
        public bool Overwrite { get; set; } = false;
        public bool OverwriteWork { get; set; } = false;
    }

    internal sealed class AppSettingsModel
    {
        public int SchemaVersion { get; set; } = 1;
        public string? LastAddonPath { get; set; }
        public string? UnpackRootPath { get; set; }
        public string? MapOptimizeRootPath { get; set; }
        public string? GmadPath { get; set; }
        public string? BlenderPath { get; set; }
        public string? StudioMdlPath { get; set; }
        public string? UnpackExistingMode { get; set; }
        public bool? UnpackOpenOnFinish { get; set; }
        public bool? UnpackExtractMapPak { get; set; }
        public bool? UnpackDeleteMapBsp { get; set; }
        public string? OptimizerSuffix { get; set; }
        public string? OptimizerPreset { get; set; }
        public OptimizerCustomParams OptimizerCustom { get; set; } = new OptimizerCustomParams();
        public bool? OptimizerRestoreSkins { get; set; }
        public bool? OptimizerCompileVerbose { get; set; }
        public int? AudioWavSampleRateIndex { get; set; }
        public int? AudioWavChannelsIndex { get; set; }
        public int? AudioWavCodecIndex { get; set; }
        public bool? AudioLoopSafe { get; set; }
        public int? AudioMp3SampleRateIndex { get; set; }
        public int? AudioMp3BitrateIndex { get; set; }
        public int? AudioOggSampleRateIndex { get; set; }
        public int? AudioOggChannelsIndex { get; set; }
        public int? AudioOggQualityIndex { get; set; }
        public int? AudioOggBitrateIndex { get; set; }
    }
}
