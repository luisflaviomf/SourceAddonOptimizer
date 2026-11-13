using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Systems.Maps;
using GmodAddonCompressor.Systems.Tools;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

namespace GmodAddonCompressor
{
    /// <summary>
    /// Mapas › "Otimizar mapa": the whole flow in one click, for a .gma, a legacy .bin or an extracted folder.
    /// 1. Preparar (the archive is extracted first: a .bin is unpacked to a .gma, then to a folder) and the
    ///    content stored inside each .bsp separated;
    /// 2. that content optimized with the chosen profile (MapProfiles);
    /// 3. every .bsp rebuilt into &lt;name&gt;_optimized in the engine's own compressed format;
    /// 4. the files outside the .bsp (materials, sounds of the addon folder) optimized with the same profile;
    /// 5. optionally the result packed into a .gma, ready for the Workshop.
    /// The three step buttons stay under "Etapas separadas" and run the same code one step at a time.
    /// </summary>
    public partial class MainWindow
    {
        private bool _mapAllRunning;
        private static readonly Regex MapGmaLineRegex = new(@"^MAPBUILD_GMA:\s+(.+)$", RegexOptions.Compiled);

        private async void Button_MapOptimizeAll_Click(object sender, RoutedEventArgs e)
        {
            if (_mapAllRunning || _mapScanRunning || _mapStageOptimizeRunning || _mapBuildRunning)
                return;

            if (!ValidateMapScanInputs(out var errorMessage))
            {
                MessageBox.Show(errorMessage, "Mapas", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (!EnsureToolsAvailable("Mapas"))
                return;

            string input = TextBox_MapScanRootPath.Text.Trim();
            MapProfile profile = MapProfiles.At(_context.MapProfileIndex);
            string? gmad = null;
            if (_context.MapMakeGma)
            {
                gmad = ResolveMapGmadPath();
                if (gmad == null)
                {
                    MessageBox.Show("Para gerar o .gma o programa precisa do gmad.exe (fica na pasta bin do Garry's Mod). " +
                                    "Escolha o gmad.exe ou desmarque \"Gerar também o .gma\".",
                                    "Mapas", MessageBoxButton.OK, MessageBoxImage.Warning);
                    return;
                }
            }

            DateTime started = DateTime.Now;
            _mapAllRunning = true;
            _context.UnlockedUI = false;
            try
            {
                PrepareMapScanRunState();
                AppendMapScanLog($"=== Otimizar mapa · {profile.Name} ===");
                await RunMapScanAsync();
                if (StopMapRun("1. Preparar") || !LoadMapScanSummary())
                    return;
                // "before" is the addon's content: the game reads a .bin decompressed, so its compressed
                // size is no base for the comparison (it is shown next to it).
                long before = MeasureMapInput(_mapEffectiveRootPath ?? input);

                if (!await RunMapStageOptimizeAsync() || StopMapRun("2. Otimizar conteúdo"))
                    return;

                int buildCode = await RunMapBuildAsync();
                if (buildCode != 0 || string.IsNullOrWhiteSpace(_mapBuildOutputPath) || !Directory.Exists(_mapBuildOutputPath))
                {
                    if (buildCode == 0)
                    {
                        TextBlock_MapScanStatus.Text = "Nada a reconstruir";
                        TextBox_MapScanSummary.Text = "Nenhum .bsp pôde ser reconstruído. Veja o log em Detalhes.";
                    }
                    return;
                }

                string output = _mapBuildOutputPath!;
                if (StopMapRun("3. Reconstruir mapa"))
                    return;
                await OptimizeMapLooseFilesAsync(output, profile);

                var gmaPaths = new List<string>();
                if (gmad != null && !StopMapRun("4. Arquivos fora do mapa"))
                    gmaPaths = await PackMapGmaAsync(output, gmad);

                ShowMapRunResult(input, before, output, gmaPaths, profile, DateTime.Now - started);
            }
            catch (Exception ex)
            {
                ProgressBar_MapScan.IsIndeterminate = false;
                TextBlock_MapScanStatus.Text = "Falhou";
                TextBlock_MapScanCurrent.Text = "Falhou.";
                AppendMapScanLog($"[MAP] {ex}");
                TextBox_MapScanSummary.Text = $"Falhou: {ex.Message}";
            }
            finally
            {
                _mapAllRunning = false;
                _mapScanCancelRequested = false;
                _context.UnlockedUI = true;
                UpdateMapScanActionStates();
            }
        }

        /// <summary>True (and the status says so) when the user asked to cancel: the next step does not start.</summary>
        private bool StopMapRun(string step)
        {
            if (!_mapScanCancelRequested)
                return false;
            TextBlock_MapScanPhase.Text = step;
            TextBlock_MapScanStatus.Text = "Cancelado";
            TextBlock_MapScanCurrent.Text = "Cancelado; as etapas seguintes não rodaram.";
            AppendMapScanLog("[MAP] Cancelled by the user.");
            return true;
        }

        private string? ResolveMapGmadPath()
        {
            string gmadPath = TextBox_UnpackGmadPath.Text.Trim();
            if (File.Exists(gmadPath))
                return gmadPath;
            string? detected = DetectGmadPath();
            if (!string.IsNullOrWhiteSpace(detected) && File.Exists(detected))
            {
                TextBox_UnpackGmadPath.Text = detected;
                return detected;
            }
            return null;
        }

        private static long MeasureMapInput(string input)
        {
            try
            {
                if (File.Exists(input))
                    return new FileInfo(input).Length;
                if (Directory.Exists(input))
                    return new DirectoryInfo(input).EnumerateFiles("*", SearchOption.AllDirectories).Sum(f => f.Length);
            }
            catch
            {
            }
            return 0;
        }

        /// <summary>The addon folders of a rebuilt result: the folder itself when it holds maps/, else the folders
        /// below it that do (an unpacked .gma lands in &lt;archive&gt;_extraido).</summary>
        private static List<string> MapAddonFolders(string root)
        {
            var found = new List<string>();
            if (Directory.Exists(Path.Combine(root, "maps")))
            {
                found.Add(root);
                return found;
            }
            var level = new List<string> { root };
            for (int depth = 0; depth < 3 && level.Count > 0; depth++)
            {
                var next = new List<string>();
                foreach (string dir in level)
                {
                    foreach (string child in Directory.EnumerateDirectories(dir).OrderBy(d => d, StringComparer.OrdinalIgnoreCase))
                    {
                        if (Directory.Exists(Path.Combine(child, "maps")))
                            found.Add(child);
                        else
                            next.Add(child);
                    }
                }
                level = next;
            }
            return found;
        }

        /// <summary>Step 4: textures, PNG and sounds of the addon folder itself (outside the .bsp files), same profile.</summary>
        private async Task OptimizeMapLooseFilesAsync(string output, MapProfile profile)
        {
            var roots = MapAddonFolders(output);
            if (roots.Count == 0)
                return;

            TextBlock_MapScanPhase.Text = "4. Arquivos fora do mapa";
            TextBlock_MapScanCurrent.Text = "Texturas e sons da pasta do addon…";
            TextBlock_MapScanStatus.Text = "Rodando";
            ProgressBar_MapScan.IsIndeterminate = true;
            AppendMapScanLog(string.Empty);
            AppendMapScanLog($"=== Optimizing the addon files outside the BSP ({profile.Name}) ===");

            bool audio = profile.AdaptiveWav && EnsureFfmpegAvailable("Map addon audio compression");
            var options = new MapStageOptimizationOptions
            {
                StageRoots = roots,
                WorkDir = _mapScanWorkDir ?? ToolPaths.WorkRoot,
                SummaryPath = Path.Combine(_mapScanWorkDir ?? ToolPaths.WorkRoot, "map_loose_optimize_summary.json"),
                PipelineOptions = profile.PipelineOptions(),
                IncludeVtf = true,
                IncludeWav = audio,
                IncludePng = true,
                WavSampleRate = MapProfile.WavReferenceRate,
                WavChannels = MapProfile.WavReferenceChannels,
                WavCodec = AudioContext.WavCodecKind.Pcm16,
                WavOptimization = MapProfiles.WavOptimization(profile),
                PreserveLoopMetadata = true,
                ImageResolution = 2,
                TargetWidth = 1024,
                TargetHeight = 1024,
                KeepImageAspectRatio = true,
                Log = message => Dispatcher.Invoke(() => AppendMapScanLog(message)),
                Progress = (filePath, fileIndex, filesCount) => Dispatcher.Invoke(() =>
                {
                    ProgressBar_MapScan.IsIndeterminate = false;
                    ProgressBar_MapScan.Minimum = 0;
                    ProgressBar_MapScan.Maximum = Math.Max(filesCount, 1);
                    ProgressBar_MapScan.Value = Math.Min(fileIndex, Math.Max(filesCount, 1));
                    TextBlock_MapScanCurrent.Text = $"{fileIndex}/{filesCount} · {filePath}";
                })
            };
            try
            {
                await MapStageOptimizationService.RunAsync(options);
            }
            catch (InvalidOperationException ex)
            {
                AppendMapScanLog($"[MAP-LOOSE] {ex.Message}");
            }
        }

        /// <summary>Step 5: every addon folder of the result packed into &lt;folder&gt;.gma (worker mappack).</summary>
        private async Task<List<string>> PackMapGmaAsync(string output, string gmad)
        {
            TextBlock_MapScanPhase.Text = "5. Gerar .gma";
            TextBlock_MapScanCurrent.Text = "Empacotando com o gmad…";
            ProgressBar_MapScan.IsIndeterminate = true;
            AppendMapScanLog(string.Empty);
            AppendMapScanLog("=== Packing the result into .gma ===");

            var made = new List<string>();
            var runner = new MapBspBuildRunner();
            runner.LogLine += line => Dispatcher.Invoke(() =>
            {
                AppendMapScanLog(line);
                var match = MapGmaLineRegex.Match(line);
                if (match.Success)
                    made.Add(match.Groups[1].Value.Trim());
            });
            int code = await runner.RunAsync(new MapBspBuildRunOptions
            {
                WorkerExePath = ToolPaths.WorkerExePath,
                Command = "mappack",
                RootPath = output,
                GmadPath = gmad
            }, CancellationToken.None);
            if (code != 0)
                AppendMapScanLog($"[MAP-PACK] gmad did not pack every folder (exit {code}); the folder result is complete.");
            return made;
        }

        private void ShowMapRunResult(string input, long before, string output, List<string> gmaPaths, MapProfile profile, TimeSpan elapsed)
        {
            // content against content: the folder result, without the .gma files packed from it
            long after = 0;
            try
            {
                after = new DirectoryInfo(output).EnumerateFiles("*", SearchOption.AllDirectories)
                    .Where(f => !f.Extension.Equals(".gma", StringComparison.OrdinalIgnoreCase))
                    .Sum(f => f.Length);
            }
            catch
            {
            }
            var gmas = gmaPaths.Where(File.Exists).ToList();
            string change = before > 0 ? $"{(after - before) * 100.0 / before:+0.#;-0.#;0}%" : "—";
            string antes = $"Antes: {FormatBytes(before)}";
            if (File.Exists(input))
            {
                string ext = Path.GetExtension(input).ToLowerInvariant();
                long archive = new FileInfo(input).Length;
                antes += ext == ".bin"
                    ? $" (conteúdo do .bin; o arquivo .bin tem {FormatBytes(archive)} comprimido)"
                    : $" ({ext})";
            }

            var lines = new List<string>
            {
                $"Perfil: {profile.Name}",
                antes,
                $"Depois: {FormatBytes(after)} ({change})",
                $"Pasta: {output}"
            };
            lines.AddRange(gmas.Select(p => $".gma: {p} ({FormatBytes(new FileInfo(p).Length)})"));
            lines.Add($"Tempo: {(int)elapsed.TotalMinutes} min {elapsed.Seconds} s");

            TextBox_MapScanSummary.Text = string.Join(Environment.NewLine, lines);
            TextBlock_MapResult.Text = string.Join(Environment.NewLine, lines);
            _mapResultPath = gmas.Count > 0 ? gmas[0] : output;
            Panel_MapResult.Visibility = Visibility.Visible;
            TextBlock_MapScanPhase.Text = "Concluído";
            TextBlock_MapScanStatus.Text = before > 0 ? $"Pronto: {FormatBytes(before)} → {FormatBytes(after)} ({change})" : "Pronto";
            TextBlock_MapScanCurrent.Text = output;
            ProgressBar_MapScan.IsIndeterminate = false;
            ProgressBar_MapScan.Minimum = 0;
            ProgressBar_MapScan.Maximum = 100;
            ProgressBar_MapScan.Value = 100;
            AppendMapScanLog($"[MAP] {string.Join(" | ", lines)}");
        }

        private string? _mapResultPath;

        /// <summary>Explorer on the result: the .gma selected when there is one, else the output folder selected.</summary>
        private void Button_MapOpenResult_Click(object sender, RoutedEventArgs e)
        {
            string? path = _mapResultPath;
            if (string.IsNullOrWhiteSpace(path) || (!File.Exists(path) && !Directory.Exists(path)))
            {
                MessageBox.Show("O resultado não está mais lá.", "Mapas", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }
            System.Diagnostics.Process.Start("explorer.exe", $"/select,\"{path}\"");
        }
    }
}
