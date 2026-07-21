using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using VtfMaximumLab.Candidates;
using VtfMaximumLab.Encoding;
using VtfMaximumLab.Images;
using VtfMaximumLab.Inventory;
using VtfMaximumLab.Metrics;
using VtfMaximumLab.Reporting;
using VtfMaximumLab.Selection;
using VtfMaximumLab.Tools;

namespace VtfMaximumLab.Experiment;

internal sealed class VtfMaximumExperimentRunner
{
    private readonly string _repositoryRoot;
    private readonly IReadOnlyDictionary<string, IVtfCandidateEncoder> _encoders;
    private readonly FlipMetricRunner _flip;

    internal VtfMaximumExperimentRunner(string repositoryRoot)
    {
        _repositoryRoot = Path.GetFullPath(repositoryRoot);
        ExternalToolPaths tools = ExternalToolPaths.Resolve(_repositoryRoot);
        IVtfCandidateEncoder[] encoders =
        {
            new TexconvCandidateEncoder("texconv-perceptual", tools.TexconvPath),
            new TexconvCandidateEncoder("texconv-uniform", tools.TexconvPath, "u"),
            new TexconvCandidateEncoder("texconv-dither", tools.TexconvPath, "d"),
            new CompressonatorCandidateEncoder(tools.CompressonatorPath)
        };
        _encoders = encoders.ToDictionary(encoder => encoder.Name, StringComparer.OrdinalIgnoreCase);
        _flip = new FlipMetricRunner(tools.FlipPath);
    }

    internal async Task<VtfMaximumExperimentReport> RunAsync(string experimentRoot, CancellationToken cancellationToken)
    {
        string root = Path.GetFullPath(experimentRoot);
        string originalTree = Path.Combine(root, "original");
        string currentTree = Path.Combine(root, "current-2x-8x8");
        string maximumTree = Path.Combine(root, "maximum");
        IReadOnlyList<VtfInventoryEntry> inventory = VtfInventoryScanner.Scan(originalTree);
        if (inventory.Count == 0)
            throw new InvalidDataException("The Maximum input tree contains no VTF files.");
        foreach (VtfInventoryEntry entry in inventory)
        {
            string source = Path.Combine(originalTree, ToPlatformPath(entry.RelativePath));
            string destination = Path.Combine(maximumTree, ToPlatformPath(entry.RelativePath));
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.Copy(source, destination, overwrite: true);
        }

        DateTimeOffset startedUtc = DateTimeOffset.UtcNow;
        var stopwatch = Stopwatch.StartNew();
        var records = new ConcurrentBag<VtfTextureExperimentRecord>();
        int completed = 0;
        var parallelOptions = new ParallelOptions
        {
            MaxDegreeOfParallelism = 2,
            CancellationToken = cancellationToken
        };

        await Parallel.ForEachAsync(inventory, parallelOptions, async (entry, token) =>
        {
            string originalPath = Path.Combine(originalTree, ToPlatformPath(entry.RelativePath));
            string currentPath = Path.Combine(currentTree, ToPlatformPath(entry.RelativePath));
            string maximumPath = Path.Combine(maximumTree, ToPlatformPath(entry.RelativePath));
            VtfTextureExperimentRecord record = await ProcessTextureAsync(
                entry, originalPath, currentPath, maximumPath, root, token).ConfigureAwait(false);
            records.Add(record);
            int index = Interlocked.Increment(ref completed);
            lock (Console.Out)
            {
                Console.WriteLine($"[{index}/{inventory.Count}] {record.RelativePath} => {record.Decision}, {record.OriginalBytes} -> {record.MaximumBytes} bytes, {record.DurationMilliseconds:0} ms");
            }
        }).ConfigureAwait(false);
        stopwatch.Stop();

        VtfTextureExperimentRecord[] ordered = records.OrderBy(record => record.RelativePath, StringComparer.OrdinalIgnoreCase).ToArray();
        var report = new VtfMaximumExperimentReport(
            root,
            startedUtc,
            stopwatch.Elapsed.TotalMilliseconds,
            ordered.Sum(record => record.OriginalBytes),
            ordered.Sum(record => record.CurrentBytes),
            ordered.Sum(record => record.MaximumBytes),
            SumFileBytes(originalTree),
            SumFileBytes(currentTree),
            SumFileBytes(maximumTree),
            ordered.Count(record => record.Decision == "preserved"),
            ordered.Count(record => record.Decision == "reduced"),
            ordered.Count(record => record.WinnerEncoder.Length == 0),
            ordered.Sum(record => record.Candidates.Count(candidate => candidate.Accepted)),
            ordered.Sum(record => record.Candidates.Count(candidate => !candidate.Accepted)),
            ordered);
        File.WriteAllText(
            Path.Combine(root, "maximum-results.json"),
            JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
        return report;
    }

    internal async Task<VtfTextureExperimentRecord> ProcessTextureAsync(
        VtfInventoryEntry entry,
        string originalPath,
        string currentPath,
        string maximumPath,
        string experimentRoot,
        CancellationToken cancellationToken)
    {
        var stopwatch = Stopwatch.StartNew();
        DecodedVtfImage original = DecodedVtfImageLoader.LoadTopFace(originalPath);
        DecodedVtfImage current = DecodedVtfImageLoader.LoadTopFace(currentPath);
        bool requiresAlpha = VtfInventorySemantics.RequiresAlpha(entry);
        bool isCutout = requiresAlpha &&
                        (entry.AlphaClass == VtfAlphaClass.Cutout || entry.SemanticProfile.IsCutout);
        bool isNormal = VtfInventorySemantics.IsNormalMap(entry);
        bool isDxt5Normal = VtfInventorySemantics.IsDxt5NormalMap(entry, original.Rgba);
        bool measureAlpha = requiresAlpha && !isDxt5Normal;
        double alphaThreshold = entry.SemanticProfile.AlphaTestReference;
        string id = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(
            System.Text.Encoding.UTF8.GetBytes(entry.RelativePath))).Substring(0, 16).ToLowerInvariant();
        string textureWork = Path.Combine(experimentRoot, "candidate-work", id);
        Directory.CreateDirectory(textureWork);
        string referencePng = Path.Combine(textureWork, "reference.png");
        PreparedImagePngWriter.Write(new PreparedImage(original.Rgba, original.Width, original.Height, 0, 0), referencePng);

        VtfQualityMetrics currentMetrics = VtfQualityMetricCalculator.Compare(
            original.Rgba, original.Width, original.Height,
            current.Rgba, current.Width, current.Height,
            measureAlpha, isCutout, isNormal, alphaThreshold, isDxt5Normal);
        string currentAlignedPng = Path.Combine(textureWork, "current-aligned.png");
        WriteAligned(current, original.Width, original.Height, alphaThreshold, currentAlignedPng, isNormal, isDxt5Normal);
        FlipMetrics currentFlip = await _flip.RunAsync(
            referencePng,
            currentAlignedPng,
            cancellationToken,
            compositeAlpha: measureAlpha && !isNormal).ConfigureAwait(false);
        currentMetrics = currentMetrics with { FlipMean = currentFlip.Mean, FlipP95 = currentFlip.P95 };
        if (isCutout)
        {
            currentMetrics = currentMetrics with
            {
                CutoutMaxMipCoverageError = VtfCutoutMipCoverageCalculator.ComputeMaxError(
                    originalPath, currentPath, alphaThreshold)
            };
        }

        var runRecords = new List<VtfCandidateRunRecord>();
        var assessments = new List<VtfCandidateAssessment>();
        AddCurrentExactCandidate(
            entry,
            originalPath,
            currentPath,
            textureWork,
            current,
            currentMetrics,
            measureAlpha,
            isCutout,
            isNormal,
            isDxt5Normal,
            alphaThreshold,
            assessments,
            runRecords);
        VtfCandidateAssessment? currentExact = assessments.FirstOrDefault(
            assessment => assessment.Spec.Encoder == "current-exact" && VtfQualityGate.Accept(assessment).Accepted);
        if (isCutout)
        {
            AddCutoutHybridCandidate(
                entry,
                originalPath,
                currentPath,
                textureWork,
                current,
                currentMetrics,
                measureAlpha,
                alphaThreshold,
                assessments,
                runRecords);
        }
        if (isCutout || isNormal)
        {
            foreach (int divisor in new[] { 2, 4 })
            {
                await AddOriginalMipCandidateAsync(
                    entry,
                    originalPath,
                    textureWork,
                    referencePng,
                    original,
                    currentMetrics,
                    measureAlpha,
                    isCutout,
                    isNormal,
                    isDxt5Normal,
                    alphaThreshold,
                    divisor,
                    assessments,
                    runRecords,
                    cancellationToken).ConfigureAwait(false);
            }
        }
        IReadOnlyList<VtfCandidateSpec> specs = VtfCandidateMatrixBuilder.Build(entry, _encoders.Keys.ToArray());
        if (specs.All(spec => spec.Format == VtfTargetFormat.Preserve))
        {
            stopwatch.Stop();
            return CreateRecord(entry, currentPath, maximumPath, "preserved", string.Empty, 1,
                currentMetrics, null, stopwatch.Elapsed.TotalMilliseconds, runRecords);
        }

        foreach (IGrouping<int, VtfCandidateSpec> scaleGroup in specs.GroupBy(spec => spec.ScaleDivisor).OrderBy(group => group.Key))
        {
            if (currentExact != null &&
                currentExact.Spec.ScaleDivisor == scaleGroup.Key &&
                scaleGroup.All(spec => spec.Format == currentExact.Spec.Format))
            {
                continue;
            }
            VtfCandidateSpec representative = scaleGroup.First();
            PreparedImage prepared = MaximumImagePreprocessor.Resize(
                original.Rgba,
                original.Width,
                original.Height,
                representative.Width,
                representative.Height,
                isNormal,
                isCutout,
                alphaThreshold,
                isDxt5Normal);
            int mipCount = entry.MipCount == 1 ? 1 : GetFullMipCount(representative.Width, representative.Height);
            var pending = new List<(VtfCandidateAssessment Assessment, int RecordIndex, double EncodeMilliseconds)>();

            foreach (VtfCandidateSpec spec in scaleGroup)
            {
                var candidateStopwatch = Stopwatch.StartNew();
                string work = Path.Combine(textureWork, $"x{spec.ScaleDivisor}", spec.Encoder);
                Directory.CreateDirectory(work);
                string pngPath = Path.Combine(work, "input.png");
                string ddsPath = Path.Combine(work, "input.dds");
                string candidatePath = Path.Combine(work, "candidate.vtf");
                try
                {
                    PreparedImagePngWriter.Write(prepared, pngPath);
                    DdsBcDocument dds = await _encoders[spec.Encoder].EncodeAsync(
                        new DdsEncodingRequest(pngPath, ddsPath, spec.Format, mipCount, alphaThreshold),
                        cancellationToken).ConfigureAwait(false);
                    VtfBcFileBuilder.Build(
                        originalPath,
                        dds,
                        candidatePath,
                        oneBitAlpha: spec.Format == VtfTargetFormat.Dxt1OneBitAlpha,
                        preserveAlpha: spec.Format == VtfTargetFormat.Dxt5);
                    DecodedVtfImage candidate = DecodedVtfImageLoader.LoadTopFace(candidatePath);
                    VtfQualityMetrics metrics = VtfQualityMetricCalculator.Compare(
                        original.Rgba, original.Width, original.Height,
                        candidate.Rgba, candidate.Width, candidate.Height,
                        measureAlpha, isCutout, isNormal, alphaThreshold, isDxt5Normal);
                    if (isCutout)
                    {
                        metrics = metrics with
                        {
                            CutoutMaxMipCoverageError = VtfCutoutMipCoverageCalculator.ComputeMaxError(
                                originalPath, candidatePath, alphaThreshold)
                        };
                    }
                    long candidateBytes = new FileInfo(candidatePath).Length;
                    var assessment = new VtfCandidateAssessment(
                        spec,
                        candidatePath,
                        candidateBytes,
                        entry.SizeBytes,
                        StructurallyValid: true,
                        VersionMatches: candidate.Document.MajorVersion == entry.MajorVersion && candidate.Document.MinorVersion == entry.MinorVersion,
                        RequiresAlpha: measureAlpha,
                        IsCutout: isCutout,
                        Metrics: metrics,
                        CurrentMetrics: currentMetrics);

                    candidateStopwatch.Stop();
                    int recordIndex = runRecords.Count;
                    runRecords.Add(new VtfCandidateRunRecord(
                        spec.Encoder, spec.ScaleDivisor, spec.Width, spec.Height, spec.Format.ToString(),
                        candidateBytes, candidateStopwatch.Elapsed.TotalMilliseconds, true,
                        false, new[] { "pending_flip" }, metrics, string.Empty));
                    pending.Add((assessment, recordIndex, candidateStopwatch.Elapsed.TotalMilliseconds));
                }
                catch (Exception exception) when (exception is not OperationCanceledException)
                {
                    candidateStopwatch.Stop();
                    runRecords.Add(new VtfCandidateRunRecord(
                        spec.Encoder, spec.ScaleDivisor, spec.Width, spec.Height, spec.Format.ToString(),
                        0, candidateStopwatch.Elapsed.TotalMilliseconds, false, false,
                        new[] { "encoder_or_structure" }, null, exception.Message));
                }
            }

            var eligible = pending
                .Where(item => ShouldRunFlip(item.Assessment))
                .OrderBy(item => item.Assessment.Metrics.CompositeError)
                .ThenBy(item => item.Assessment.Spec.Encoder, StringComparer.OrdinalIgnoreCase)
                .ToArray();
            var evaluatedIndexes = new HashSet<int>();
            bool acceptedEncoderAtScale = false;
            foreach ((VtfCandidateAssessment pendingAssessment, int recordIndex, double encodeMilliseconds) in eligible)
            {
                var flipStopwatch = Stopwatch.StartNew();
                try
                {
                    DecodedVtfImage candidate = DecodedVtfImageLoader.LoadTopFace(pendingAssessment.CandidatePath);
                    string alignedPng = Path.Combine(Path.GetDirectoryName(pendingAssessment.CandidatePath)!, "aligned.png");
                    WriteAligned(candidate, original.Width, original.Height, alphaThreshold, alignedPng, isNormal, isDxt5Normal);
                    FlipMetrics flip = await _flip.RunAsync(
                        referencePng,
                        alignedPng,
                        cancellationToken,
                        compositeAlpha: measureAlpha && !isNormal).ConfigureAwait(false);
                    VtfQualityMetrics actualMetrics = pendingAssessment.Metrics with
                    {
                        FlipMean = flip.Mean,
                        FlipP95 = flip.P95
                    };
                    VtfCandidateAssessment evaluated = pendingAssessment with { Metrics = actualMetrics };
                    VtfAcceptanceResult acceptance = VtfQualityGate.Accept(evaluated);
                    assessments.Add(evaluated);
                    evaluatedIndexes.Add(recordIndex);
                    flipStopwatch.Stop();
                    VtfCandidateRunRecord prior = runRecords[recordIndex];
                    runRecords[recordIndex] = prior with
                    {
                        DurationMilliseconds = encodeMilliseconds + flipStopwatch.Elapsed.TotalMilliseconds,
                        Accepted = acceptance.Accepted,
                        RejectionReasons = acceptance.Reasons,
                        Metrics = actualMetrics
                    };
                    if (acceptance.Accepted)
                    {
                        acceptedEncoderAtScale = true;
                        break;
                    }
                }
                catch (Exception exception) when (exception is not OperationCanceledException)
                {
                    flipStopwatch.Stop();
                    evaluatedIndexes.Add(recordIndex);
                    VtfCandidateRunRecord prior = runRecords[recordIndex];
                    runRecords[recordIndex] = prior with
                    {
                        DurationMilliseconds = encodeMilliseconds + flipStopwatch.Elapsed.TotalMilliseconds,
                        Accepted = false,
                        RejectionReasons = new[] { "flip_failure" },
                        Error = exception.Message
                    };
                }
            }

            foreach (var item in pending)
            {
                VtfCandidateAssessment pendingAssessment = item.Assessment;
                int recordIndex = item.RecordIndex;
                if (evaluatedIndexes.Contains(recordIndex))
                    continue;
                VtfCandidateRunRecord prior = runRecords[recordIndex];
                if (acceptedEncoderAtScale && ShouldRunFlip(pendingAssessment))
                {
                    runRecords[recordIndex] = prior with { RejectionReasons = new[] { "encoder_dominated" } };
                }
                else
                {
                    VtfAcceptanceResult rejection = VtfQualityGate.Accept(pendingAssessment);
                    runRecords[recordIndex] = prior with
                    {
                        RejectionReasons = rejection.Reasons.Count > 0 ? rejection.Reasons : new[] { "flip_not_evaluated" }
                    };
                }
            }
        }

        VtfCandidateAssessment? winner = VtfCandidateSelector.Select(assessments);
        VtfQualityMetrics? maximumMetrics = null;
        string decision = "preserved";
        string winnerEncoder = string.Empty;
        int winnerScale = 1;
        if (winner != null)
        {
            File.Copy(winner.CandidatePath, maximumPath, overwrite: true);
            decision = "reduced";
            winnerEncoder = winner.Spec.Encoder;
            winnerScale = winner.Spec.ScaleDivisor;
            maximumMetrics = winner.Metrics;
        }

        stopwatch.Stop();
        return CreateRecord(entry, currentPath, maximumPath, decision, winnerEncoder, winnerScale,
            currentMetrics, maximumMetrics, stopwatch.Elapsed.TotalMilliseconds, runRecords);
    }

    private static void AddCurrentExactCandidate(
        VtfInventoryEntry entry,
        string originalPath,
        string currentPath,
        string textureWork,
        DecodedVtfImage current,
        VtfQualityMetrics currentMetrics,
        bool requiresAlpha,
        bool isCutout,
        bool isNormal,
        bool isDxt5Normal,
        double alphaThreshold,
        ICollection<VtfCandidateAssessment> assessments,
        ICollection<VtfCandidateRunRecord> runRecords)
    {
        var stopwatch = Stopwatch.StartNew();
        try
        {
            DdsBcDocument payload = VtfBcPayloadExtractor.Extract(currentPath);
            string work = Path.Combine(textureWork, "current-exact");
            string candidatePath = Path.Combine(work, "candidate.vtf");
            Directory.CreateDirectory(work);
            bool oneBitAlpha = current.Document.HighResFormat == 20;
            bool preserveAlpha = current.Document.HighResFormat == 15;
            VtfBcFileBuilder.Build(originalPath, payload, candidatePath, oneBitAlpha, preserveAlpha);
            DecodedVtfImage candidate = DecodedVtfImageLoader.LoadTopFace(candidatePath);
            VtfQualityMetrics metrics = VtfQualityMetricCalculator.Compare(
                current.Rgba, current.Width, current.Height,
                candidate.Rgba, candidate.Width, candidate.Height,
                requiresAlpha, isCutout, isNormal, alphaThreshold, isDxt5Normal) with
            {
                // The BC payload is byte-identical to current. Reuse current's
                // already measured error against the original reference.
                RgbSsim = currentMetrics.RgbSsim,
                RgbPsnr = currentMetrics.RgbPsnr,
                FlipMean = currentMetrics.FlipMean,
                FlipP95 = currentMetrics.FlipP95,
                AlphaSsim = currentMetrics.AlphaSsim,
                AlphaMae = currentMetrics.AlphaMae,
                AlphaP99 = currentMetrics.AlphaP99,
                CutoutCoverageErrorTop = currentMetrics.CutoutCoverageErrorTop,
                CutoutMaxMipCoverageError = currentMetrics.CutoutMaxMipCoverageError,
                CutoutIou = currentMetrics.CutoutIou,
                NormalMeanDegrees = currentMetrics.NormalMeanDegrees,
                NormalP95Degrees = currentMetrics.NormalP95Degrees,
                NormalMaxDegrees = currentMetrics.NormalMaxDegrees,
                CompositeError = currentMetrics.CompositeError
            };
            int scaleDivisor = GetScaleDivisor(entry.Width, entry.Height, candidate.Width, candidate.Height);
            VtfTargetFormat targetFormat = candidate.Document.HighResFormat switch
            {
                13 => VtfTargetFormat.Dxt1,
                20 => VtfTargetFormat.Dxt1OneBitAlpha,
                15 => VtfTargetFormat.Dxt5,
                _ => throw new InvalidDataException("Current exact candidate is not BC1/BC3.")
            };
            var spec = new VtfCandidateSpec(
                scaleDivisor,
                candidate.Width,
                candidate.Height,
                targetFormat,
                "current-exact",
                preserveAlpha || oneBitAlpha,
                isNormal,
                oneBitAlpha);
            long sizeBytes = new FileInfo(candidatePath).Length;
            var assessment = new VtfCandidateAssessment(
                spec,
                candidatePath,
                sizeBytes,
                entry.SizeBytes,
                MipPolicyMatches(entry.MipCount, candidate.Document.MipCount, candidate.Width, candidate.Height),
                candidate.Document.MajorVersion == entry.MajorVersion && candidate.Document.MinorVersion == entry.MinorVersion,
                requiresAlpha,
                isCutout,
                metrics,
                currentMetrics);
            assessments.Add(assessment);
            VtfAcceptanceResult acceptance = VtfQualityGate.Accept(assessment);
            stopwatch.Stop();
            runRecords.Add(new VtfCandidateRunRecord(
                spec.Encoder, spec.ScaleDivisor, spec.Width, spec.Height, spec.Format.ToString(),
                sizeBytes, stopwatch.Elapsed.TotalMilliseconds, true, acceptance.Accepted,
                acceptance.Reasons, metrics, string.Empty));
        }
        catch (Exception exception)
        {
            stopwatch.Stop();
            runRecords.Add(new VtfCandidateRunRecord(
                "current-exact", 1, current.Width, current.Height, current.Document.HighResFormat.ToString(),
                0, stopwatch.Elapsed.TotalMilliseconds, false, false,
                new[] { "current_exact_structure" }, null, exception.Message));
        }
    }

    private async Task AddOriginalMipCandidateAsync(
        VtfInventoryEntry entry,
        string originalPath,
        string textureWork,
        string referencePng,
        DecodedVtfImage original,
        VtfQualityMetrics currentMetrics,
        bool requiresAlpha,
        bool isCutout,
        bool isNormal,
        bool isDxt5Normal,
        double alphaThreshold,
        int scaleDivisor,
        ICollection<VtfCandidateAssessment> assessments,
        ICollection<VtfCandidateRunRecord> runRecords,
        CancellationToken cancellationToken)
    {
        var stopwatch = Stopwatch.StartNew();
        string encoderName = $"original-mip-x{scaleDivisor}";
        try
        {
            int firstMipLevel = scaleDivisor == 2 ? 1 : 2;
            DdsBcDocument payload = VtfBcPayloadExtractor.Extract(originalPath, firstMipLevel);
            string work = Path.Combine(textureWork, encoderName);
            string candidatePath = Path.Combine(work, "candidate.vtf");
            Directory.CreateDirectory(work);
            bool oneBitAlpha = original.Document.HighResFormat == 20;
            bool preserveAlpha = original.Document.HighResFormat == 15;
            VtfBcFileBuilder.Build(originalPath, payload, candidatePath, oneBitAlpha, preserveAlpha);
            DecodedVtfImage candidate = DecodedVtfImageLoader.LoadTopFace(candidatePath);
            VtfQualityMetrics metrics = VtfQualityMetricCalculator.Compare(
                original.Rgba, original.Width, original.Height,
                candidate.Rgba, candidate.Width, candidate.Height,
                requiresAlpha, isCutout, isNormal, alphaThreshold, isDxt5Normal);
            if (isCutout)
            {
                metrics = metrics with
                {
                    CutoutMaxMipCoverageError = VtfCutoutMipCoverageCalculator.ComputeMaxError(
                        originalPath, candidatePath, alphaThreshold)
                };
            }
            string alignedPng = Path.Combine(work, "aligned.png");
            WriteAligned(candidate, original.Width, original.Height, alphaThreshold, alignedPng, isNormal, isDxt5Normal);
            FlipMetrics flip = await _flip.RunAsync(
                referencePng, alignedPng, cancellationToken,
                compositeAlpha: requiresAlpha && !isNormal).ConfigureAwait(false);
            metrics = metrics with { FlipMean = flip.Mean, FlipP95 = flip.P95 };
            VtfTargetFormat targetFormat = candidate.Document.HighResFormat switch
            {
                13 => VtfTargetFormat.Dxt1,
                20 => VtfTargetFormat.Dxt1OneBitAlpha,
                15 => VtfTargetFormat.Dxt5,
                _ => throw new InvalidDataException("Promoted original mip is not BC1/BC3.")
            };
            var spec = new VtfCandidateSpec(
                scaleDivisor,
                candidate.Width,
                candidate.Height,
                targetFormat,
                encoderName,
                preserveAlpha || oneBitAlpha,
                isNormal,
                isCutout);
            long sizeBytes = new FileInfo(candidatePath).Length;
            var assessment = new VtfCandidateAssessment(
                spec,
                candidatePath,
                sizeBytes,
                entry.SizeBytes,
                true,
                candidate.Document.MajorVersion == entry.MajorVersion && candidate.Document.MinorVersion == entry.MinorVersion,
                requiresAlpha,
                isCutout,
                metrics,
                currentMetrics);
            assessments.Add(assessment);
            VtfAcceptanceResult acceptance = VtfQualityGate.Accept(assessment);
            stopwatch.Stop();
            runRecords.Add(new VtfCandidateRunRecord(
                spec.Encoder, spec.ScaleDivisor, spec.Width, spec.Height, spec.Format.ToString(),
                sizeBytes, stopwatch.Elapsed.TotalMilliseconds, true, acceptance.Accepted,
                acceptance.Reasons, metrics, string.Empty));
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            stopwatch.Stop();
            runRecords.Add(new VtfCandidateRunRecord(
                encoderName, scaleDivisor, 0, 0, "promoted-original-mip",
                0, stopwatch.Elapsed.TotalMilliseconds, false, false,
                new[] { "original_mip_structure" }, null, exception.Message));
        }
    }

    private static void AddCutoutHybridCandidate(
        VtfInventoryEntry entry,
        string originalPath,
        string currentPath,
        string textureWork,
        DecodedVtfImage current,
        VtfQualityMetrics currentMetrics,
        bool requiresAlpha,
        double alphaThreshold,
        ICollection<VtfCandidateAssessment> assessments,
        ICollection<VtfCandidateRunRecord> runRecords)
    {
        const string encoderName = "current-top-original-mips";
        var stopwatch = Stopwatch.StartNew();
        try
        {
            DdsBcDocument currentPayload = VtfBcPayloadExtractor.Extract(currentPath);
            DdsBcDocument originalTail = VtfBcPayloadExtractor.Extract(originalPath, firstMipLevel: 2);
            DdsBcDocument hybrid = DdsBcDocumentComposer.WithTopMipAndTail(currentPayload, originalTail);
            string work = Path.Combine(textureWork, encoderName);
            string candidatePath = Path.Combine(work, "candidate.vtf");
            Directory.CreateDirectory(work);
            bool oneBitAlpha = current.Document.HighResFormat == 20;
            bool preserveAlpha = current.Document.HighResFormat == 15;
            VtfBcFileBuilder.Build(originalPath, hybrid, candidatePath, oneBitAlpha, preserveAlpha);
            DecodedVtfImage candidate = DecodedVtfImageLoader.LoadTopFace(candidatePath);
            if (candidate.Width * 2 != entry.Width || candidate.Height * 2 != entry.Height)
                throw new InvalidDataException("Hybrid cutout top mip is not an exact 2x reduction.");
            VtfQualityMetrics metrics = currentMetrics with
            {
                CutoutMaxMipCoverageError = VtfCutoutMipCoverageCalculator.ComputeMaxError(
                    originalPath, candidatePath, alphaThreshold)
            };
            VtfTargetFormat targetFormat = candidate.Document.HighResFormat switch
            {
                13 => VtfTargetFormat.Dxt1,
                20 => VtfTargetFormat.Dxt1OneBitAlpha,
                15 => VtfTargetFormat.Dxt5,
                _ => throw new InvalidDataException("Hybrid cutout is not BC1/BC3.")
            };
            var spec = new VtfCandidateSpec(
                2, candidate.Width, candidate.Height, targetFormat, encoderName,
                preserveAlpha || oneBitAlpha, false, true);
            long sizeBytes = new FileInfo(candidatePath).Length;
            var assessment = new VtfCandidateAssessment(
                spec, candidatePath, sizeBytes, entry.SizeBytes, true,
                candidate.Document.MajorVersion == entry.MajorVersion && candidate.Document.MinorVersion == entry.MinorVersion,
                requiresAlpha, true, metrics, currentMetrics);
            assessments.Add(assessment);
            VtfAcceptanceResult acceptance = VtfQualityGate.Accept(assessment);
            stopwatch.Stop();
            runRecords.Add(new VtfCandidateRunRecord(
                encoderName, 2, candidate.Width, candidate.Height, targetFormat.ToString(),
                sizeBytes, stopwatch.Elapsed.TotalMilliseconds, true, acceptance.Accepted,
                acceptance.Reasons, metrics, string.Empty));
        }
        catch (Exception exception)
        {
            stopwatch.Stop();
            runRecords.Add(new VtfCandidateRunRecord(
                encoderName, 2, current.Width, current.Height, "hybrid-bc",
                0, stopwatch.Elapsed.TotalMilliseconds, false, false,
                new[] { "hybrid_structure" }, null, exception.Message));
        }
    }

    private static int GetScaleDivisor(int originalWidth, int originalHeight, int width, int height)
    {
        if (width == originalWidth && height == originalHeight)
            return 1;
        if (width * 2 == originalWidth && height * 2 == originalHeight)
            return 2;
        if (width * 4 == originalWidth && height * 4 == originalHeight)
            return 4;
        throw new InvalidDataException("Current candidate dimensions are not an exact 1x, 2x, or 4x scale.");
    }

    private static VtfTextureExperimentRecord CreateRecord(
        VtfInventoryEntry entry,
        string currentPath,
        string maximumPath,
        string decision,
        string winnerEncoder,
        int winnerScale,
        VtfQualityMetrics currentMetrics,
        VtfQualityMetrics? maximumMetrics,
        double duration,
        IReadOnlyList<VtfCandidateRunRecord> candidates)
    {
        return new VtfTextureExperimentRecord(
            entry.RelativePath,
            entry.SizeBytes,
            new FileInfo(currentPath).Length,
            new FileInfo(maximumPath).Length,
            decision,
            winnerEncoder,
            winnerScale,
            currentMetrics,
            maximumMetrics,
            duration,
            entry.Tags,
            candidates);
    }

    private static bool ShouldRunFlip(VtfCandidateAssessment assessment)
    {
        VtfQualityMetrics metrics = assessment.Metrics;
        if (!assessment.StructurallyValid || !assessment.VersionMatches || assessment.SizeBytes >= assessment.OriginalSizeBytes)
            return false;
        if (assessment.Spec.ScaleDivisor <= 2)
            return true;
        if (metrics.RgbSsim < 0.95 || metrics.RgbPsnr < 30)
            return false;
        if (assessment.RequiresAlpha && (metrics.AlphaSsim < 0.98 || metrics.AlphaMae > 0.02 || metrics.AlphaP99 > 0.08))
            return false;
        if (assessment.IsCutout && (metrics.CutoutCoverageErrorTop > 0.01 || metrics.CutoutIou < 0.98))
            return false;
        if (assessment.Spec.IsNormalMap &&
            (metrics.NormalMeanDegrees > 3 || metrics.NormalP95Degrees > 8 || metrics.NormalMaxDegrees > 25))
            return false;
        return true;
    }

    private static void WriteAligned(
        DecodedVtfImage image,
        int width,
        int height,
        double alphaThreshold,
        string path,
        bool isNormalMap,
        bool isDxt5NormalMap)
    {
        PreparedImage aligned = image.Width == width && image.Height == height
            ? new PreparedImage(image.Rgba, width, height, 0, 0)
            : MaximumImagePreprocessor.Resize(
                image.Rgba, image.Width, image.Height, width, height,
                isNormalMap, false, alphaThreshold, isDxt5NormalMap);
        PreparedImagePngWriter.Write(aligned, path);
    }

    private static int GetFullMipCount(int width, int height)
    {
        int count = 1;
        while (width > 1 || height > 1)
        {
            width = Math.Max(1, width / 2);
            height = Math.Max(1, height / 2);
            count++;
        }
        return count;
    }

    internal static bool MipPolicyMatches(
        int originalMipCount,
        int candidateMipCount,
        int candidateWidth,
        int candidateHeight) =>
        originalMipCount == 1
            ? candidateMipCount == 1
            : candidateMipCount == GetFullMipCount(candidateWidth, candidateHeight);

    private static long SumFileBytes(string root) =>
        Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories).Sum(path => new FileInfo(path).Length);

    private static string ToPlatformPath(string relativePath) => relativePath.Replace('/', Path.DirectorySeparatorChar);
}
