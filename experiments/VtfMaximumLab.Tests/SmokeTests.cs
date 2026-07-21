using GmodAddonCompressor.Systems.Vtf;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class SmokeTests
{
    [Fact]
    public void ExistingPlannerRejectsMissingVtf()
    {
        Assert.False(AddonVtfCompressionPlanner.TryReadMetadata("missing.vtf", out _));
    }
}
