using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfCorpusAuditorTests
{
    [Fact]
    public async Task CleanSyntheticBuildPassesIndependentReaudit()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        UniversalVtfBuildResult result = await UniversalVtfCorpusBuilder.BuildAsync(
            corpus.Request,
            null,
            CancellationToken.None);

        UniversalVtfAuditResult audit = UniversalVtfCorpusAuditor.Audit(
            new UniversalVtfAuditRequest(
                corpus.RawRoot,
                corpus.SafeRoot,
                result.Conservative.Root,
                result.Conservative.ManifestPath,
                result.Conservative.ManifestSha256,
                corpus.Request.BcBridgePath,
                corpus.Request.BcBridgeSha256,
                corpus.Request.Anchors!));

        Assert.True(audit.Clean, string.Join(Environment.NewLine, audit.Issues));
    }

    [Fact]
    public async Task NonVtfMutationIsDetected()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        UniversalVtfBuildResult result = await UniversalVtfCorpusBuilder.BuildAsync(
            corpus.Request,
            null,
            CancellationToken.None);
        File.AppendAllText(Path.Combine(result.Conservative.Root, "models", "a.mdl"), "drift");

        UniversalVtfAuditResult audit = UniversalVtfCorpusAuditor.Audit(
            new UniversalVtfAuditRequest(
                corpus.RawRoot,
                corpus.SafeRoot,
                result.Conservative.Root,
                result.Conservative.ManifestPath,
                result.Conservative.ManifestSha256,
                corpus.Request.BcBridgePath,
                corpus.Request.BcBridgeSha256,
                corpus.Request.Anchors!));

        Assert.False(audit.Clean);
        Assert.Contains(audit.Issues, issue => issue.Contains("non_vtf", StringComparison.Ordinal));
    }
}
