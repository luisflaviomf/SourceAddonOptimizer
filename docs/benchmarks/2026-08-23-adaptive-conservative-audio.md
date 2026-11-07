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

### Speed and reproducibility (2026-11-07)

The "audio" bucket of `CompressAddonSystem` now runs up to 8 sounds at once (half the logical
CPUs) instead of 2. Real Compress button, 77 LVS WAVs, 22050 Hz mono PCM16 + adaptive:
25.6 / 29.9 s with the old limit, 15.7 / 9.5 s with the new one; same candidates chosen, same
bytes per file and in total (9,541,240).

Finding: the soxr resampler of the bundled ffmpeg (2022-09-22) is not deterministic. Running the
same `aresample=...:resampler=soxr:precision=28:...` command twice gives samples that differ by up to
2 LSB in ~15% of positions (mono and stereo, one process, `OMP_NUM_THREADS=1` makes no difference).
So the 8 LVS sounds whose chosen candidate is resampled differ byte-wise between any two runs, with
the old limit too; their size and the choice do not change. Fixed the same day, see below.

### Byte-identical resampled candidates (2026-11-07)

Cause: with s16 input and `osf=s16`, swr picks an S16P internal format, so FFmpeg creates the soxr
context with INT16 input and output (`soxr_io_spec(type, type)` in `libswresample/soxr_resample.c`,
never with `SOXR_NO_DITHER`). libsoxr then adds its own TPDF dither to INT16 output, from a generator
seeded with `time(0) ^ <heap address>` in `soxr_create`. `dither_method=none` only turns off swr's
dither, not soxr's. It is not threading: libsoxr runs one thread unless told otherwise.

Fix (`FFMpegSystem.BuildAdaptiveResampleFilter`): when a candidate changes the sample rate, the
filter adds `internal_sample_fmt=dblp`. soxr then works on float64 input and output (same filter:
precision 28, Chebyshev, no dither), and swr rounds once to s16 at the end with no dither. A candidate
at the source rate never creates a soxr context, so it keeps the old filter and its old bytes. The
manual WAV path is not touched.

Checked:

- `ae85_mid.wav`, 44.1 kHz stereo -> 22050 Hz mono PCM16, ffmpeg alone: the old command gave 3
  different md5s in 3 runs; with `internal_sample_fmt=dblp` (or `fltp`) all 3 runs are identical.
- Corpus driver, 77 LVS WAVs (22050 Hz mono PCM16 + adaptive), two runs: 77/77 files identical and
  every candidate score identical. Old code: the same 8 files differed between its first run and each
  of its 4 other runs.
- Size: unchanged, 9,541,240 bytes, the same 36 candidates chosen. Compared with the old output, only
  those 8 files changed bytes (all `adpcm_ms` 22050 Hz stereo, same sizes). The 2 chosen same-rate
  downmixes (`ae86_high`, `gtr_high2`, 44.1 kHz mono ADPCM) and all other files are byte-identical.
- Quality, 95 resampled candidates with a score, new value minus the mean of 5 old (dithered) runs:
  SDR +0.0007 dB, segmental SNR -0.0007 dB, LSD -0.0010 on average (largest single change 0.13 dB).
  The old code moved by 0.04 dB / 0.05 LSD on average between its own runs (up to 0.22 dB), so the
  change is far below the old run-to-run noise. No candidate changed accept/reject against any of
  the 5 old runs. Removing the dither does not measurably improve these candidates: the MS-ADPCM
  error is far larger than a 16-bit dither.
- Applying `dblp` to every candidate was also tried. It is just as deterministic, but it changes
  the 2 same-rate downmixes as well: the S16P downmix rounds halves up and the double path rounds to
  even. Same size, and one unused candidate (`ae86_low` 44.1 kHz mono ADPCM) went from accepted to
  rejected. So `dblp` is used only where soxr actually runs.
- Tests: `FfmpegAdaptiveAudioBackendTests.EncodeCandidate_IsByteIdenticalAcrossRuns` encodes a noisy
  signal 3 times (44.1 -> 22.05 kHz PCM16 and MS-ADPCM, mono and stereo, and 22.05 -> 44.1 kHz). It
  fails 4/4 with the old filter and passes with the new one.

Real Compress button (published exe from `build_release_wpf.ps1`, Mídia e recursos tab, 22050 Hz,
mono, PCM 16-bit + Adaptativo conservador, fresh copy of the 77 LVS WAVs each time), two runs:
77/77 WAVs byte-identical, reports identical, 28,271,118 -> 9,541,240 bytes, 12.0 / 13.1 s. The
output is also byte-identical to the corpus driver's.

Remaining limit: libsoxr can take a different SIMD code path on another CPU, so a different machine
may still round differently. The goal here is the same bytes when the same PC optimizes the same addon again.
