using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace GmodAddonCompressor.Systems
{
    internal sealed class WorkshopReferenceMatch
    {
        internal WorkshopReferenceMatch(string relativePath, int lineNumber, string lineText)
        {
            RelativePath = relativePath;
            LineNumber = lineNumber;
            LineText = lineText;
        }

        internal string RelativePath { get; }
        internal int LineNumber { get; }
        internal string LineText { get; }
    }

    internal sealed class WorkshopReferenceScanResult
    {
        internal WorkshopReferenceScanResult(IReadOnlyList<WorkshopReferenceMatch> matches, int totalMatches)
        {
            Matches = matches;
            TotalMatches = totalMatches;
        }

        internal IReadOnlyList<WorkshopReferenceMatch> Matches { get; }
        internal int TotalMatches { get; }
        internal bool HasMatches => TotalMatches > 0;
    }

    internal static class WorkshopReferenceScanner
    {
        private const int MaxDisplayedMatches = 12;

        private static readonly Regex _resourceAddWorkshopRegex = new Regex(
            @"\bresource\s*\.\s*AddWorkshop\s*\(",
            RegexOptions.IgnoreCase | RegexOptions.Compiled);

        internal static string BuildWarningText(string addonDirectoryPath, CancellationToken cancellationToken = default)
        {
            var result = Scan(addonDirectoryPath, cancellationToken);
            if (!result.HasMatches)
                return string.Empty;

            var sb = new StringBuilder();
            sb.AppendLine("Warning: resource.AddWorkshop was found in this addon.");
            sb.AppendLine("If you publish the processed addon as-is, Garry's Mod can still pull the old Workshop item.");
            sb.AppendLine("Review and replace these entries before uploading:");

            foreach (var match in result.Matches)
                sb.AppendLine($"- {match.RelativePath}:{match.LineNumber} -> {match.LineText}");

            int hiddenMatches = result.TotalMatches - result.Matches.Count;
            if (hiddenMatches > 0)
                sb.AppendLine($"+ {hiddenMatches} more match(es) not shown.");

            return sb.ToString().TrimEnd();
        }

        internal static WorkshopReferenceScanResult Scan(string addonDirectoryPath, CancellationToken cancellationToken = default)
        {
            if (string.IsNullOrWhiteSpace(addonDirectoryPath) || !Directory.Exists(addonDirectoryPath))
                return new WorkshopReferenceScanResult(Array.Empty<WorkshopReferenceMatch>(), 0);

            var matches = new List<WorkshopReferenceMatch>();
            int totalMatches = 0;

            foreach (string filePath in Directory.EnumerateFiles(addonDirectoryPath, "*.lua", SearchOption.AllDirectories))
            {
                cancellationToken.ThrowIfCancellationRequested();

                try
                {
                    string text = ReadText(filePath);
                    using var reader = new StringReader(text);

                    int lineNumber = 0;
                    string? line;
                    while ((line = reader.ReadLine()) != null)
                    {
                        cancellationToken.ThrowIfCancellationRequested();
                        lineNumber++;

                        if (!_resourceAddWorkshopRegex.IsMatch(line))
                            continue;

                        totalMatches++;
                        if (matches.Count >= MaxDisplayedMatches)
                            continue;

                        string relativePath = Path.GetRelativePath(addonDirectoryPath, filePath).Replace('\\', '/');
                        matches.Add(new WorkshopReferenceMatch(relativePath, lineNumber, TrimLine(line)));
                    }
                }
                catch (OperationCanceledException)
                {
                    throw;
                }
                catch
                {
                    continue;
                }
            }

            return new WorkshopReferenceScanResult(matches, totalMatches);
        }

        private static string ReadText(string filePath)
        {
            byte[] data = File.ReadAllBytes(filePath);
            Encoding encoding = DetectEncoding(data, out int bomLength);
            return encoding.GetString(data, bomLength, data.Length - bomLength);
        }

        private static string TrimLine(string line)
        {
            const int maxLength = 180;
            string trimmed = (line ?? string.Empty).Trim();
            if (trimmed.Length <= maxLength)
                return trimmed;

            return trimmed.Substring(0, maxLength - 3) + "...";
        }

        private static Encoding DetectEncoding(byte[] data, out int bomLength)
        {
            if (data.Length >= 3 && data[0] == 0xEF && data[1] == 0xBB && data[2] == 0xBF)
            {
                bomLength = 3;
                return new UTF8Encoding(true);
            }

            if (data.Length >= 2 && data[0] == 0xFF && data[1] == 0xFE)
            {
                bomLength = 2;
                return Encoding.Unicode;
            }

            if (data.Length >= 2 && data[0] == 0xFE && data[1] == 0xFF)
            {
                bomLength = 2;
                return Encoding.BigEndianUnicode;
            }

            if (data.Length >= 4 && data[0] == 0x00 && data[1] == 0x00 && data[2] == 0xFE && data[3] == 0xFF)
            {
                bomLength = 4;
                return new UTF32Encoding(true, true);
            }

            if (data.Length >= 4 && data[0] == 0xFF && data[1] == 0xFE && data[2] == 0x00 && data[3] == 0x00)
            {
                bomLength = 4;
                return new UTF32Encoding(false, true);
            }

            bomLength = 0;
            return Encoding.Default;
        }
    }
}
