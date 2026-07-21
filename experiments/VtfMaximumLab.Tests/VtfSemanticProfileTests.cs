using GmodAddonCompressor.Systems.Vtf;
using GmodAddonCompressor.Models;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class VtfSemanticProfileTests
{
    [Theory]
    [InlineData("$translucent 1", true, false)]
    [InlineData("$alphatest 1\n$alphatestreference 0.35", false, true)]
    public void ExposesAlphaSemantics(string body, bool translucent, bool cutout)
    {
        using var addon = TestAddon.WithMaterial("models/car/glass", body);

        var profile = AddonVtfCompressionPlanner.Analyze(addon.Root)
            .GetSemanticProfile("models/car/glass", "materials/models/car/glass.vtf");

        Assert.Equal(translucent, profile.IsTranslucent);
        Assert.Equal(cutout, profile.IsCutout);
        if (cutout)
            Assert.Equal(0.35, profile.AlphaTestReference, precision: 6);
    }

    [Fact]
    public void ExposesNormalPhongEmissiveAndDecalEvidence()
    {
        using var addon = TestAddon.WithMaterial(
            "models/car/body",
            "$bumpmap models/car/body_n\n$phong 1\n$basemapalphaphongmask 1\n$selfillum 1\n$decal 1");
        var analysis = AddonVtfCompressionPlanner.Analyze(addon.Root);

        var baseProfile = analysis.GetSemanticProfile("models/car/body", "materials/models/car/body.vtf");
        var normalProfile = analysis.GetSemanticProfile("models/car/body_n", "materials/models/car/body_n.vtf");

        Assert.True(baseProfile.UsesBaseTextureAsPhongMask);
        Assert.True(baseProfile.HasPhong);
        Assert.True(baseProfile.IsEmissive);
        Assert.True(baseProfile.IsDecal);
        Assert.True(normalProfile.IsNormalMap);
    }

    [Fact]
    public void NormalTextureCarriesVmtEvidenceAndAlphaMaskSemantics()
    {
        using var addon = TestAddon.WithMaterial(
            "models/car/chrome_rusty",
            "$bumpmap models/car/chrome_rusty_nm\n$normalmapalphaenvmapmask 1");

        var profile = AddonVtfCompressionPlanner.Analyze(addon.Root)
            .GetSemanticProfile("models/car/chrome_rusty_nm", "materials/models/car/chrome_rusty_nm.vtf");

        Assert.True(profile.IsNormalMap);
        Assert.True(profile.UsesNormalAlpha);
        Assert.Contains("vmt:models/car/chrome_rusty", profile.Evidence);
    }

    [Fact]
    public void CurrentPlannerPreservesCubemapsInsteadOfRebuildingOneFace()
    {
        var metadata = new VtfFileModel
        {
            Width = 256,
            Height = 256,
            Frames = 1,
            Depth = 1,
            Flags = 0x00004000,
            HighResImageFormat = 15
        };

        Assert.False(AddonVtfCompressionPlanner.TryCreatePlan(
            "materials/test/envmap.vtf",
            metadata,
            AddonVtfCompressionAnalysis.Empty,
            fullyOpaqueAlpha: true,
            out AddonVtfCompressionPlan plan));
        Assert.Equal("cubemap_faces", plan.Reason);
    }

    private sealed class TestAddon : IDisposable
    {
        private TestAddon(string root) => Root = root;

        public string Root { get; }

        public static TestAddon WithMaterial(string materialKey, string body)
        {
            string root = Path.Combine(Path.GetTempPath(), $"vtf_semantics_{Guid.NewGuid():N}");
            string path = Path.Combine(root, "materials", materialKey.Replace('/', Path.DirectorySeparatorChar) + ".vmt");
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, $"VertexLitGeneric\n{{\n$basetexture {materialKey}\n{body}\n}}");
            return new TestAddon(root);
        }

        public void Dispose()
        {
            if (Directory.Exists(Root))
                Directory.Delete(Root, recursive: true);
        }
    }
}
