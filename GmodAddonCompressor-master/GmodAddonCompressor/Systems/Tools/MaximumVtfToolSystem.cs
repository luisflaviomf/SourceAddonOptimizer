using System;
using System.IO;
using System.Reflection;

namespace GmodAddonCompressor.Systems.Tools
{
    internal static class MaximumVtfToolSystem
    {
        private const string ToolName = "VtfMaximumEncoders";
        private const string ToolVersion = "2026.07.20.2";
        private const string ResourceName = "GmodAddonCompressor.Resources.VtfMaximumTools.zip";

        internal static string EnsureExtracted()
        {
            Assembly assembly = typeof(MaximumVtfToolSystem).Assembly;
            using Stream stream = assembly.GetManifestResourceStream(ResourceName) ??
                                  throw new FileNotFoundException("Embedded Maximum VTF tools were not found.", ResourceName);
            using var memory = new MemoryStream();
            stream.CopyTo(memory);
            return ToolExtractionSystem.EnsureExtracted(
                ToolName,
                ToolVersion,
                memory.ToArray(),
                new[]
                {
                    "texconv.exe",
                    "flip.exe",
                    Path.Combine("compressonator", "compressonatorcli.exe"),
                    Path.Combine("compressonator", "Qt5Core.dll")
                });
        }
    }
}
