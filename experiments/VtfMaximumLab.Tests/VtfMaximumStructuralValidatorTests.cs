using GmodAddonCompressor.Systems.Vtf;
using VtfMaximumLab.Experiment;
using VtfMaximumLab.Validation;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class VtfMaximumStructuralValidatorTests
{
    [Fact]
    public void PairValidationRejectsVersionChangeEvenWhenBothVtfsParse()
    {
        string originalPath = VtfFixtureBuilder.WriteResource75(8, 8, 15, 4);
        string candidatePath = VtfFixtureBuilder.WriteLegacy72(4, 4, 15, 3);
        Assert.True(VtfDocumentReader.TryRead(originalPath, out var original, out _));
        Assert.True(VtfDocumentReader.TryRead(candidatePath, out var candidate, out _));

        IReadOnlyList<string> errors = VtfMaximumStructuralValidator.ValidatePair(original, candidate);

        Assert.Contains("version", errors);
    }

    [Fact]
    public void MaximumRejectsAddingMipmapsWhenOriginalHasNone()
    {
        Assert.False(VtfMaximumExperimentRunner.MipPolicyMatches(1, 10, 512, 512));
        Assert.True(VtfMaximumExperimentRunner.MipPolicyMatches(1, 1, 512, 512));
        Assert.True(VtfMaximumExperimentRunner.MipPolicyMatches(11, 10, 512, 512));
    }
}
