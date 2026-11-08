using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems.Optimizer
{
    /// <summary>
    /// Disables <c>resource.AddWorkshop("id")</c> lines in an optimized copy. Addons use it to make every
    /// player of a server download the addon from the Workshop; left in an optimized re-upload it points at the
    /// ORIGINAL item (e.g. the LVS pack's 3027256228), so players download the original 1.3 GB as well and both
    /// copies mount the same files. Only lines whose code is that single call are touched; they are commented
    /// out (same line count, original bytes everywhere else).
    /// </summary>
    internal static class WorkshopReferenceCleaner
    {
        internal sealed record Disabled(string File, string Id);

        internal sealed record Result(IReadOnlyList<Disabled> Lines, IReadOnlyList<string> LeftInFiles);

        private const string Marker = " -- disabled by GMod Addon Optimizer: players must not download the original addon";

        private static readonly Regex AddWorkshopLine = new(
            @"^(?<indent>[ \t]*)(?<call>resource\.AddWorkshop[ \t]*\([ \t]*(?:""(?<id>\d+)""|'(?<id>\d+)'|(?<id>\d+))[ \t]*\)[ \t]*;?)(?<tail>[ \t]*(?:--[^\r\n]*)?)(?=\r?$)",
            RegexOptions.Multiline | RegexOptions.CultureInvariant);

        private static readonly Regex ActiveCall = new(
            @"^(?![ \t]*--).*resource\.AddWorkshop", RegexOptions.Multiline | RegexOptions.CultureInvariant);

        internal static Result Disable(string addonRoot)
        {
            var disabled = new List<Disabled>();
            var left = new List<string>();
            if (!Directory.Exists(addonRoot))
                return new Result(disabled, left);

            // Latin-1 maps every byte to one char and back, so the rest of the file keeps its exact bytes
            Encoding bytes = Encoding.Latin1;
            foreach (string path in Directory.EnumerateFiles(addonRoot, "*.lua", SearchOption.AllDirectories))
            {
                string text = bytes.GetString(File.ReadAllBytes(path));
                if (!text.Contains("AddWorkshop", StringComparison.Ordinal))
                    continue;

                string relative = Path.GetRelativePath(addonRoot, path).Replace('\\', '/');
                string updated = AddWorkshopLine.Replace(text, m =>
                {
                    disabled.Add(new Disabled(relative, m.Groups["id"].Value));
                    return m.Groups["indent"].Value + "-- " + m.Groups["call"].Value + m.Groups["tail"].Value + Marker;
                });
                if (updated != text)
                    File.WriteAllBytes(path, bytes.GetBytes(updated));
                if (ActiveCall.IsMatch(updated))
                    left.Add(relative);
            }
            return new Result(disabled, left);
        }
    }
}
