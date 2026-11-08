using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems.Companion
{
    internal enum CompanionValidationStatus
    {
        Pass = 0,
        Incomplete = 1,
        Warning = 2,
        Failed = 3,
    }

    internal sealed record CompanionValidationResource(
        string Id,
        string TestId,
        string Path,
        CompanionValidationStatus Status,
        string Reason,
        IReadOnlyList<string> Warnings
    );

    internal sealed record CompanionValidationScreenshot(
        string Id,
        string TestId,
        string RelativePath,
        string Sha256,
        long Bytes,
        string View,
        int RetryCount,
        CompanionValidationStatus Status,
        double Fov,
        double Distance,
        int CropWidth,
        int CropHeight,
        int Skin,
        IReadOnlyList<string> Bodygroups,
        string FullPath
    );

    internal sealed record CompanionValidationBundle(
        string Root,
        string IndexPath,
        string Schema,
        Guid RunId,
        DateTimeOffset CreatedUtc,
        string IndexSha256,
        string PlanSha256,
        CompanionValidationStatus OverallStatus,
        IReadOnlyList<CompanionValidationResource> Resources,
        IReadOnlyList<CompanionValidationScreenshot> Screenshots,
        long PrimaryCount,
        long SecondaryCount,
        double WallSeconds,
        long TruncatedPrimary,
        long TruncatedSecondary
    )
    {
        public bool IsStructural => Schema == CompanionResultService.StructuralSchema;
        public bool IsVisual => Schema == CompanionResultService.VisualSchema;
        public string Channel => IsStructural ? "Structural" : "Visual";
    }

    internal sealed record CompanionValidationReportItem(
        string PlanSha256,
        string Channel,
        string Id,
        string TestId,
        string Path,
        CompanionValidationStatus ValidationStatus,
        string Reason,
        IReadOnlyList<string> Warnings,
        IReadOnlyList<CompanionValidationScreenshot> Screenshots
    )
    {
        public string Status => CompanionResultService.StatusText(ValidationStatus);
        /// <summary>Portuguese status shown in the "Testar no jogo" results list.</summary>
        public string StatusLabel => ValidationStatus switch
        {
            CompanionValidationStatus.Pass => "Passou",
            CompanionValidationStatus.Warning => "Atenção",
            CompanionValidationStatus.Failed => "Falhou",
            _ => "Incompleto",
        };
        public string WarningText => string.Join(Environment.NewLine, Warnings);
        public string PrimaryScreenshotPath => Screenshots.FirstOrDefault()?.FullPath ?? string.Empty;
        public bool HasScreenshot => !string.IsNullOrWhiteSpace(PrimaryScreenshotPath);
        public string DetailText => $"{Channel} / {TestId} / {Path}{Environment.NewLine}{Reason}";
    }

    internal sealed record CompanionValidationReport(
        Guid RunId,
        string PlanSha256,
        bool HasStructural,
        bool HasVisual,
        bool HasPlanConflict,
        CompanionValidationStatus OverallStatus,
        IReadOnlyList<CompanionValidationBundle> Sources,
        IReadOnlyList<CompanionValidationReportItem> Items,
        IReadOnlyList<CompanionValidationScreenshot> Screenshots,
        int PassCount,
        int WarningCount,
        int FailedCount,
        int IncompleteCount,
        long TruncatedItems,
        double WallSeconds,
        string SummaryText
    )
    {
        public string OverallStatusText => CompanionResultService.StatusText(OverallStatus);
        public string ShortPlanSha256 => PlanSha256[..Math.Min(12, PlanSha256.Length)];
    }

    internal sealed class CompanionResultService
    {
        public const string StructuralSchema = "sao-structural-result-v1";
        public const string VisualSchema = "sao-visual-result-v1";

        private static readonly Regex ScreenshotFilename = new(
            "^[a-z0-9][a-z0-9._-]{0,126}\\.png$",
            RegexOptions.CultureInvariant
        );
        private readonly List<CompanionValidationBundle> _imports = new();
        private readonly List<CompanionValidationReport> _reports = new();

        public IReadOnlyList<CompanionValidationBundle> Imports => new ReadOnlyCollection<CompanionValidationBundle>(_imports);
        public IReadOnlyList<CompanionValidationReport> Reports => new ReadOnlyCollection<CompanionValidationReport>(_reports);

        public CompanionValidationReport Import(
            string indexPath,
            string? dataRoot = null,
            DateTimeOffset? now = null)
        {
            CompanionValidationBundle bundle = CompanionBundleReader.ReadValidationResult(indexPath, now);
            if (bundle.IsVisual && bundle.Screenshots.Count > 0)
                bundle = bundle with { Screenshots = VerifyScreenshots(bundle, ResolveDataRoot(bundle, dataRoot)) };
            _imports.Add(bundle);
            RebuildReports();
            return _reports.Single(report =>
                report.RunId == bundle.RunId
                && report.PlanSha256.Equals(bundle.PlanSha256, StringComparison.Ordinal));
        }

        public static CompanionValidationStatus CombineStatusForContract(
            CompanionValidationStatus left,
            CompanionValidationStatus right) =>
            StatusRank(left) >= StatusRank(right) ? left : right;

        public static string StatusText(CompanionValidationStatus status) => status switch
        {
            CompanionValidationStatus.Pass => "PASS",
            CompanionValidationStatus.Warning => "WARNING",
            CompanionValidationStatus.Failed => "FAILED",
            CompanionValidationStatus.Incomplete => "INCOMPLETE",
            _ => throw new InvalidOperationException("Unknown validation status."),
        };

        public static string ExportSummary(IEnumerable<CompanionValidationReport> reports)
        {
            var output = new StringBuilder();
            foreach (CompanionValidationReport report in reports)
            {
                if (output.Length > 0)
                    output.AppendLine().AppendLine();
                output.AppendLine(report.SummaryText);
                foreach (CompanionValidationReportItem item in report.Items)
                    output.AppendLine($"{item.Status}\t{item.Channel}\t{item.TestId}\t{item.Path}\t{item.Reason}");
                foreach (CompanionValidationScreenshot screenshot in report.Screenshots)
                    output.AppendLine($"SCREENSHOT\t{screenshot.TestId}\t{screenshot.View}\t{screenshot.FullPath}\t{screenshot.Sha256}");
            }
            return output.ToString().TrimEnd();
        }

        private void RebuildReports()
        {
            _reports.Clear();
            var hashCounts = _imports
                .GroupBy(item => item.RunId)
                .ToDictionary(
                    group => group.Key,
                    group => group.Select(item => item.PlanSha256).Distinct(StringComparer.Ordinal).Count()
                );
            foreach (IGrouping<(Guid RunId, string PlanSha256), CompanionValidationBundle> group in _imports
                .GroupBy(item => (item.RunId, item.PlanSha256))
                .OrderBy(group => group.Min(item => item.CreatedUtc)))
            {
                CompanionValidationBundle[] sources = group.ToArray();
                bool structural = sources.Any(item => item.IsStructural);
                bool visual = sources.Any(item => item.IsVisual);
                CompanionValidationStatus overall = CompanionValidationStatus.Pass;
                foreach (CompanionValidationBundle source in sources)
                    overall = CombineStatusForContract(overall, source.OverallStatus);
                if (!structural)
                    overall = CombineStatusForContract(overall, CompanionValidationStatus.Incomplete);
                else if (!visual)
                    overall = CombineStatusForContract(overall, CompanionValidationStatus.Warning);

                CompanionValidationScreenshot[] screenshots = sources.SelectMany(item => item.Screenshots).ToArray();
                CompanionValidationReportItem[] items = sources.SelectMany(source => source.Resources.Select(resource =>
                    new CompanionValidationReportItem(
                        source.PlanSha256,
                        source.Channel,
                        resource.Id,
                        resource.TestId,
                        resource.Path,
                        resource.Status,
                        resource.Reason,
                        resource.Warnings,
                        source.Screenshots.Where(shot => shot.TestId == resource.TestId).ToArray()
                    ))).ToArray();
                bool conflict = hashCounts[group.Key.RunId] > 1;
                int pass = items.Count(item => item.ValidationStatus == CompanionValidationStatus.Pass);
                int warning = items.Count(item => item.ValidationStatus == CompanionValidationStatus.Warning);
                int failed = items.Count(item => item.ValidationStatus == CompanionValidationStatus.Failed);
                int incomplete = items.Count(item => item.ValidationStatus == CompanionValidationStatus.Incomplete);
                long truncated = sources.Sum(item => checked(item.TruncatedPrimary + item.TruncatedSecondary));
                double wallSeconds = sources.Sum(item => item.WallSeconds);
                string missing = !structural
                    ? " Structural validation not imported; the decision is incomplete."
                    : !visual
                        ? " WARNING: visual validation not run."
                        : string.Empty;
                string conflictText = conflict
                    ? " PLAN HASH CONFLICT: incompatible imports are shown separately and were not merged."
                    : string.Empty;
                string summary =
                    $"{StatusText(overall)} - run {group.Key.RunId:D}, plan {group.Key.PlanSha256}. "
                    + $"Resources PASS {pass}, WARNING {warning}, FAILED {failed}, INCOMPLETE {incomplete}; "
                    + $"screenshots {screenshots.Length}; sources {sources.Length}; truncated {truncated}; wall {wallSeconds:0.###} s."
                    + missing
                    + conflictText;
                _reports.Add(new CompanionValidationReport(
                    group.Key.RunId,
                    group.Key.PlanSha256,
                    structural,
                    visual,
                    conflict,
                    overall,
                    sources,
                    items,
                    screenshots,
                    pass,
                    warning,
                    failed,
                    incomplete,
                    truncated,
                    wallSeconds,
                    summary
                ));
            }
        }

        private static IReadOnlyList<CompanionValidationScreenshot> VerifyScreenshots(
            CompanionValidationBundle bundle,
            string dataRoot)
        {
            string normalizedRoot = Path.GetFullPath(dataRoot)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            CompanionPathPolicy.AssertNotReparsePoint(normalizedRoot, "Companion data root");
            string screenshotsRoot = Path.GetFullPath(Path.Combine(normalizedRoot, "screenshots"));
            if (Directory.Exists(screenshotsRoot))
                CompanionPathPolicy.AssertNotReparsePoint(screenshotsRoot, "screenshot directory");
            string expectedRoot = Path.GetFullPath(Path.Combine(screenshotsRoot, bundle.RunId.ToString("D")))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string containment = expectedRoot + Path.DirectorySeparatorChar;
            if (Directory.Exists(expectedRoot))
                CompanionPathPolicy.AssertNotReparsePoint(expectedRoot, "screenshot run directory");

            var verified = new List<CompanionValidationScreenshot>(bundle.Screenshots.Count);
            foreach (CompanionValidationScreenshot screenshot in bundle.Screenshots)
            {
                string expectedPrefix = $"screenshots/{bundle.RunId:D}/";
                string relative = screenshot.RelativePath;
                if (relative.Any(character => character > 0x7f)
                    || relative.Contains('\\')
                    || !relative.StartsWith(expectedPrefix, StringComparison.Ordinal)
                    || relative.Length > CompanionContract.MaxPathBytes)
                    throw new InvalidOperationException("Screenshot path is not a canonical run-relative PNG path.");
                string filename = relative[expectedPrefix.Length..];
                if (filename.Contains('/') || !ScreenshotFilename.IsMatch(filename))
                    throw new InvalidOperationException("Screenshot path contains an invalid filename or traversal.");

                string fullPath = Path.GetFullPath(Path.Combine(normalizedRoot, relative.Replace('/', Path.DirectorySeparatorChar)));
                if (!fullPath.StartsWith(containment, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Screenshot path escapes the selected screenshot root.");
                bool exists = File.Exists(fullPath);
                if (screenshot.Status == CompanionValidationStatus.Pass || screenshot.Bytes > 0 || exists)
                {
                    if (!exists)
                        throw new InvalidOperationException("Declared screenshot is missing.");
                    CompanionPathPolicy.AssertNotReparsePoint(fullPath, "screenshot file");
                    var info = new FileInfo(fullPath);
                    if (info.Length != screenshot.Bytes)
                        throw new InvalidOperationException("Screenshot size does not match its declaration.");
                    byte[] bytes = File.ReadAllBytes(fullPath);
                    if (bytes.LongLength != screenshot.Bytes)
                        throw new InvalidOperationException("Screenshot changed while it was being verified.");
                    string hash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
                    if (!hash.Equals(screenshot.Sha256, StringComparison.Ordinal))
                        throw new InvalidOperationException("Screenshot hash does not match its declaration.");
                }
                verified.Add(screenshot with { FullPath = exists ? fullPath : string.Empty });
            }
            return verified;
        }

        private static string ResolveDataRoot(CompanionValidationBundle bundle, string? explicitRoot)
        {
            if (!string.IsNullOrWhiteSpace(explicitRoot))
                return Path.GetFullPath(explicitRoot);
            var attempt = new DirectoryInfo(bundle.Root);
            DirectoryInfo? run = attempt.Parent;
            DirectoryInfo? validationResults = run?.Parent;
            if (run != null
                && validationResults != null
                && validationResults.Name.Equals("validation_results", StringComparison.OrdinalIgnoreCase)
                && run.Name.Equals(bundle.RunId.ToString("D"), StringComparison.OrdinalIgnoreCase)
                && validationResults.Parent != null)
                return validationResults.Parent.FullName;
            throw new InvalidOperationException("Visual results require the selected sourceaddonoptimizer data root so screenshot paths can be verified.");
        }

        private static int StatusRank(CompanionValidationStatus status) => status switch
        {
            CompanionValidationStatus.Failed => 4,
            CompanionValidationStatus.Warning => 3,
            CompanionValidationStatus.Incomplete => 2,
            CompanionValidationStatus.Pass => 1,
            _ => 0,
        };
    }
}
