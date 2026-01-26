namespace GmodAddonCompressor.DataContexts
{
    internal class AudioContext
    {
        internal enum WavCodecKind
        {
            Pcm16 = 0,
            AdpcmMs = 1
        }

        private const int MinSampleRate = 11025;
        private const int MaxSampleRate = 44100;

        private static int _samplingFrequency = 22050;
        private static int _wavSampleRate = 44100;
        private static int _wavChannels = 2;
        private static WavCodecKind _wavCodec = WavCodecKind.Pcm16;
        private static int _mp3SampleRate = 44100;
        private static int _mp3BitrateKbps = 96;
        private static int _oggSampleRate = 22050;
        private static int _oggChannels = 1;
        private static double _oggQuality = 3.0;

        internal static bool PreserveLoopMetadata = true;

        internal static int SamplingFrequency
        {
            get { return _samplingFrequency; }
            set
            {
                _samplingFrequency = value < MinSampleRate ? MinSampleRate : value > MaxSampleRate ? MaxSampleRate : value;
            }
        }

        internal static int WavSampleRate
        {
            get { return _wavSampleRate; }
            set
            {
                _wavSampleRate = value < MinSampleRate ? MinSampleRate : value > MaxSampleRate ? MaxSampleRate : value;
            }
        }

        internal static int WavChannels
        {
            get { return _wavChannels; }
            set
            {
                _wavChannels = value < 1 ? 1 : value > 2 ? 2 : value;
            }
        }

        internal static WavCodecKind WavCodec
        {
            get { return _wavCodec; }
            set { _wavCodec = value; }
        }

        internal static int Mp3SampleRate
        {
            get { return _mp3SampleRate; }
            set
            {
                _mp3SampleRate = value < MinSampleRate ? MinSampleRate : value > MaxSampleRate ? MaxSampleRate : value;
            }
        }

        internal static int Mp3BitrateKbps
        {
            get { return _mp3BitrateKbps; }
            set
            {
                _mp3BitrateKbps = value < 32 ? 32 : value > 320 ? 320 : value;
            }
        }

        internal static int OggSampleRate
        {
            get { return _oggSampleRate; }
            set
            {
                _oggSampleRate = value < MinSampleRate ? MinSampleRate : value > MaxSampleRate ? MaxSampleRate : value;
            }
        }

        internal static int OggChannels
        {
            get { return _oggChannels; }
            set
            {
                _oggChannels = value < 1 ? 1 : value > 2 ? 2 : value;
            }
        }

        internal static double OggQuality
        {
            get { return _oggQuality; }
            set
            {
                _oggQuality = value < -1 ? -1 : value > 10 ? 10 : value;
            }
        }
    }
}
