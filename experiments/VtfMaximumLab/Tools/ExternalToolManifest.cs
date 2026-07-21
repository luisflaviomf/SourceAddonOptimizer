namespace VtfMaximumLab.Tools;

internal sealed record ExternalToolPaths(string TexconvPath, string CompressonatorPath, string FlipPath)
{
    internal static ExternalToolPaths Resolve(string repositoryRoot)
    {
        string toolsRoot = Path.Combine(Path.GetFullPath(repositoryRoot), "experiments", "_tools");
        string texconv = Path.Combine(toolsRoot, "texconv-may2026.exe");
        string compressonator = Path.Combine(
            toolsRoot,
            "compressonator-4.5.52",
            "compressonatorcli-4.5.52-win64",
            "compressonatorcli.exe");
        string flip = Path.Combine(toolsRoot, "flip-v1.7.exe");
        if (!File.Exists(texconv) || !File.Exists(compressonator) || !File.Exists(flip))
        {
            string extractedRoot = GmodAddonCompressor.Systems.Tools.MaximumVtfToolSystem.EnsureExtracted();
            texconv = Path.Combine(extractedRoot, "texconv.exe");
            compressonator = Path.Combine(extractedRoot, "compressonator", "compressonatorcli.exe");
            flip = Path.Combine(extractedRoot, "flip.exe");
        }
        if (!File.Exists(texconv) || !File.Exists(compressonator) || !File.Exists(flip))
            throw new FileNotFoundException("Maximum encoder tools are missing or incomplete.");
        return new ExternalToolPaths(texconv, compressonator, flip);
    }
}
