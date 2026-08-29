using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfMonotonicSelectorTests
{
    [Fact]
    public void LargerPreparedAlternativeCannotReplaceTheIncumbent()
    {
        var incumbent = Option("incumbent", 100, 'a', eligible: true);
        var prepared = Option("prepared", 101, 'b', eligible: true);

        UniversalVtfMonotonicDecision decision =
            UniversalVtfMonotonicSelector.Select(incumbent, new[] { prepared });

        Assert.Equal("incumbent", decision.Winner.Name);
        Assert.Equal(100, decision.Winner.Bytes);
        Assert.Equal("not_smaller_than_incumbent", decision.Alternatives.Single().Reason);
    }

    [Fact]
    public void SmallestSemanticallySafeAlternativeWins()
    {
        var incumbent = Option("incumbent", 100, 'a', eligible: true);
        var first = Option("first", 80, 'b', eligible: true);
        var smallest = Option("smallest", 60, 'c', eligible: true);

        UniversalVtfMonotonicDecision decision =
            UniversalVtfMonotonicSelector.Select(incumbent, new[] { first, smallest });

        Assert.Equal("smallest", decision.Winner.Name);
        Assert.Equal(40, decision.SavedBytes);
        Assert.Equal(60, decision.Winner.Bytes);
    }

    [Fact]
    public void SmallerBlockedAlternativeCannotReplaceTheIncumbent()
    {
        var incumbent = Option("incumbent", 100, 'a', eligible: true);
        var blocked = Option("blocked", 20, 'b', eligible: false, "quality_gate_failed");

        UniversalVtfMonotonicDecision decision =
            UniversalVtfMonotonicSelector.Select(incumbent, new[] { blocked });

        Assert.Equal("incumbent", decision.Winner.Name);
        Assert.Equal("quality_gate_failed", decision.Alternatives.Single().Reason);
    }

    [Fact]
    public void EqualSizedAlternativeKeepsTheIncumbent()
    {
        var incumbent = Option("incumbent", 100, 'a', eligible: true);
        var equal = Option("equal", 100, 'b', eligible: true);

        UniversalVtfMonotonicDecision decision =
            UniversalVtfMonotonicSelector.Select(incumbent, new[] { equal });

        Assert.Equal("incumbent", decision.Winner.Name);
        Assert.Equal(0, decision.SavedBytes);
    }

    private static UniversalVtfMonotonicOption Option(
        string name,
        long bytes,
        char hashCharacter,
        bool eligible,
        string? reason = null) =>
        new(name, bytes, new string(hashCharacter, 64), eligible, reason);
}
