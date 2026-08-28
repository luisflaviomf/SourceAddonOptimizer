using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfSemanticResolverTests
{
    [Fact]
    public void SsbumpNormalTakesTheSpecializedRoute()
    {
        using var addon = new TestMaterialTree();
        addon.Vmt(
            "normal",
            "VertexLitGeneric { \"$bumpmap\" \"models/car/norm\" \"$ssbump\" \"1\" }");
        VtfConsumerGraph graph = Build(addon);

        UniversalVtfSemanticDecision decision = UniversalVtfSemanticResolver.Resolve(
            addon.Entry("models/car/norm"),
            graph);

        Assert.Contains(UniversalVtfSemanticClass.Normal, decision.Classes);
        Assert.Contains(UniversalVtfSemanticClass.Ssbump, decision.Classes);
        Assert.Equal(UniversalVtfFilterRoute.SsbumpScalars, decision.Route);
        Assert.False(decision.HasUnknownConsumer);
    }

    [Fact]
    public void CutoutCarriesResolvedAlphaTestReference()
    {
        using var addon = new TestMaterialTree();
        addon.Vmt(
            "leaves",
            "VertexLitGeneric { \"$basetexture\" \"models/car/leaves\" " +
            "\"$alphatest\" \"1\" \"$alphatestreference\" \"0.35\" }");
        VtfConsumerGraph graph = Build(addon);

        UniversalVtfSemanticDecision decision = UniversalVtfSemanticResolver.Resolve(
            addon.Entry("models/car/leaves"),
            graph);

        Assert.Contains(UniversalVtfSemanticClass.Diffuse, decision.Classes);
        Assert.Contains(UniversalVtfSemanticClass.Cutout, decision.Classes);
        Assert.Equal(0.35, decision.AlphaTestReference, precision: 12);
        Assert.Contains(decision.Consumers, consumer => consumer.ReadChannels.Contains(3));
    }

    [Fact]
    public void ConflictingColorAndMaskConsumersUseComposite()
    {
        using var addon = new TestMaterialTree();
        addon.Vmt("paint", "VertexLitGeneric { \"$basetexture\" \"models/car/shared\" }");
        addon.Vmt("mask", "VertexLitGeneric { \"$envmapmask\" \"models/car/shared\" }");
        VtfConsumerGraph graph = Build(addon);

        UniversalVtfSemanticDecision decision = UniversalVtfSemanticResolver.Resolve(
            addon.Entry("models/car/shared"),
            graph);

        Assert.Contains(UniversalVtfSemanticClass.Diffuse, decision.Classes);
        Assert.Contains(UniversalVtfSemanticClass.ScalarMask, decision.Classes);
        Assert.Equal(UniversalVtfFilterRoute.Composite, decision.Route);
    }

    [Fact]
    public void MissingConsumerIsUnknownCompositeButStillClassified()
    {
        using var addon = new TestMaterialTree();
        VtfConsumerGraph graph = Build(addon);

        UniversalVtfSemanticDecision decision = UniversalVtfSemanticResolver.Resolve(
            addon.Entry("models/car/orphan", frames: 2),
            graph);

        Assert.Contains(UniversalVtfSemanticClass.Unknown, decision.Classes);
        Assert.Contains(UniversalVtfSemanticClass.Animated, decision.Classes);
        Assert.True(decision.HasUnknownConsumer);
        Assert.Equal(UniversalVtfFilterRoute.Composite, decision.Route);
    }

    private static VtfConsumerGraph Build(TestMaterialTree addon) =>
        VtfConsumerGraphBuilder.Build(addon.Raw, addon.Safe, Array.Empty<string>());
}
