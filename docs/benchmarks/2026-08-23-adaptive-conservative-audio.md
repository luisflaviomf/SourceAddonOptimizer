# Adaptive conservative WAV profile (2026-08-23)

## Result

The production implementation reproduced the validated conservative spike on all 77 LVS WAV files:

| Measurement | Current manual baseline A | Adaptive conservative |
|---|---:|---:|
| WAV files | 77 | 77 |
| Bytes | 11,958,402 | 9,541,240 |
| Selected adaptive candidates | - | 36 |
| Saved versus A | - | 2,417,162 (20.2131%) |
| Wall clock, two WAV workers | - | 36.839 s |

Original input was 28,271,118 bytes. SHA-256 manifests of the original and A remained unchanged. The implementation output and detailed per-file report are under:

`C:\Users\luisf\Music\vtf_next_lab_20260823\audio-production-benchmark\adaptive-audio-conservative-run-3`

Independent reinspection reported 77/77 valid headers, 77/77 decodes, 77/77 logical durations within one output sample, and 77/77 loop-metadata comparisons passing. Seventy-three files contain cue or sampler-loop metadata; maximum marker-time error was 1/44,100 second (0.000022676 s).

## Policy

The manual WAV path remains the default and is unchanged. The new saved UI option uses the selected manual settings as its byte-identical fallback, then searches native Source/GMod PCM16 and MS-ADPCM candidates at 22,050/44,100 Hz. It uses official block geometry (1,024 mono, 2,048 stereo), SOXR precision 28, and no dither; the spike found no aggregate benefit from the larger dither grid.

Stereo becomes mono only at correlation >= 0.9999 and side/mid energy <= -40 dB. A candidate must be smaller than the manual baseline and simultaneously meet or beat its SDR and segmental SNR and not increase log-spectral distance, all measured against the original. Otherwise the exact baseline bytes are committed.

RIFF `cue `, `LIST/adtl` (`labl` and `ltxt`), and `smpl` chunks are restored transactionally and sample positions are scaled with the output rate. MS-ADPCM `fact` is corrected from the original logical duration because the embedded older FFmpeg otherwise reports padded block duration. Outputs also validate format tag, channels, rate, block alignment, complete blocks, `fact`, padding, duration, and decode before promotion.

## Reproduction

```powershell
dotnet run --project tests/audio_adaptive_corpus/AudioAdaptiveCorpus.csproj -c Release -- `
  <original-addon-root> <manual-baseline-root> <fresh-output-root>
```

## Primary compatibility references

- [Facepunch Sound Reference Basics](https://wiki.facepunch.com/gmod/sound_basics)
- [Facepunch Creating Looping Sounds](https://wiki.facepunch.com/gmod/Creating_Looping_Sounds)
- [Valve Source SDK `riff.h`](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/public/tier2/riff.h)
- [FFmpeg MS-ADPCM encoder](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/adpcmenc.c)
- [FFmpeg resampler documentation](https://ffmpeg.org/ffmpeg-resampler.html)
- [IBM/Microsoft RIFF 1.0 specification](https://www.mmsp.ece.mcgill.ca/Documents/AudioFormats/WAVE/Docs/riffmci.pdf)

## In the product (2026-11-07)

Ported from `feature/adaptive-audio-conservative` (12636fa) onto the unified line. The profile is
"Perfil WAV" in the Mídia e recursos tab (and the Mapas asset settings): Manual (default) or
Adaptativo conservador. Differences from the branch:

- The JSON report is written to `%LOCALAPPDATA%\GmodAddonOptimizer\work\audio\adaptive_audio_<time>_<id>.json`
  (`WAVEdit.LastAdaptiveReportPath`), never inside the addon: gmad rejects loose files at the addon root.
- UI labels are in Portuguese.

Reproduced on the unified line, byte totals identical to run 3: 77 files, 11,958,402 -> 9,541,240 bytes
(36 candidates), both with the corpus driver and with the real "Comprimir" button (22050 Hz, mono,
PCM 16-bit + Adaptativo conservador: 28,271,118 -> 9,541,240 bytes).

LVS cars pack with Tela textures + Tela Forte models + these sounds: 402.8 MB on disk, 94.9 MB
download (per-file LZMA-6; the Steam log of the original pack shows 334,319,264 bytes downloaded for a
1,442,181,498-byte .gma, matching the estimate of 321 MiB).
