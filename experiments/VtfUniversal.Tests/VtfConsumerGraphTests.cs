using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class VtfConsumerGraphTests
{
    [Fact]
    public void ParserPreservesDuplicatesConditionsProxiesCommentsAndSpans()
    {
        const string content =
            "// leading comment\n" +
            "\"VertexLitGeneric\"\n" +
            "{\n" +
            "  \"$basetexture\" \"models\\car\\body\" [$WIN32]\n" +
            "  \"$basetexture\" \"models/car/body_alt\" [$LINUX]\n" +
            "  \"$phongfresnelranges\" \"[.3 .5 1]\"\n" +
            "  Proxies\n" +
            "  {\n" +
            "    AnimatedTexture { animatedtexturevar \"$basetexture\" }\n" +
            "  }\n" +
            "}\n";

        VmtDocument document = VmtKeyValuesParser.Parse(content, "materials/car.vmt");

        Assert.Equal("VertexLitGeneric", document.Shader);
        VmtKeyValuesNode[] baseTextures = document.Entries
            .Where(entry => entry.Key.Equals("$basetexture", StringComparison.OrdinalIgnoreCase))
            .ToArray();
        Assert.Equal(2, baseTextures.Length);
        Assert.Equal("[$WIN32]", baseTextures[0].Condition);
        Assert.Equal("[$LINUX]", baseTextures[1].Condition);
        Assert.Equal("models\\car\\body", baseTextures[0].Value!.Text);
        Assert.True(baseTextures[0].Value!.WasQuoted);
        Assert.True(baseTextures[0].Span.Line >= 4);
        VmtKeyValuesNode vector = Assert.Single(document.Entries.Where(
            entry => entry.Key.Equals("$phongfresnelranges", StringComparison.OrdinalIgnoreCase)));
        Assert.Equal("[.3 .5 1]", vector.Value!.Text);
        Assert.Null(vector.Condition);
        Assert.Single(document.Comments);
        VmtKeyValuesNode proxies = Assert.Single(document.Entries.Where(
            entry => entry.Key.Equals("Proxies", StringComparison.OrdinalIgnoreCase)));
        Assert.Single(proxies.Children);
    }

    [Fact]
    public void PatchUnionAndDynamicReferenceBecomeComposite()
    {
        using var addon = new TestMaterialTree();
        addon.Vmt("base", "VertexLitGeneric { \"$basetexture\" \"models/car/body\" }");
        addon.Vmt(
            "paint",
            "Patch { \"include\" \"base\" \"insert\" { \"$selfillum\" \"1\" } }");
        addon.Lua("runtime.lua", "local m = Material(prefix .. skin)");

        VtfConsumerGraph graph = VtfConsumerGraphBuilder.Build(
            addon.Raw,
            addon.Safe,
            Array.Empty<string>());
        UniversalVtfSemanticDecision decision = UniversalVtfSemanticResolver.Resolve(
            addon.Entry("models/car/body"),
            graph);

        Assert.Contains(UniversalVtfSemanticClass.Diffuse, decision.Classes);
        Assert.Contains(UniversalVtfSemanticClass.Emissive, decision.Classes);
        Assert.True(decision.HasUnknownConsumer);
        Assert.Equal(UniversalVtfFilterRoute.Composite, decision.Route);
        Assert.Contains(decision.Uncertainties, value => value.Contains(
            "dynamic_lua_material",
            StringComparison.Ordinal));
    }

    [Fact]
    public void IncludeCycleNeverProvesAlphaUnused()
    {
        using var addon = new TestMaterialTree();
        addon.Vmt("a", "Patch { \"include\" \"b\" }");
        addon.Vmt("b", "Patch { \"include\" \"a\" }");

        VtfConsumerGraph graph = VtfConsumerGraphBuilder.Build(
            addon.Raw,
            addon.Safe,
            Array.Empty<string>());

        Assert.False(graph.IsClosed);
        Assert.Contains(graph.Failures, failure => failure.Code == "include_cycle");
    }

    [Fact]
    public void NestedPatchKeepsBaseShaderAndUnionsEveryOverlay()
    {
        using var addon = new TestMaterialTree();
        addon.Vmt("base", "VertexLitGeneric { \"$basetexture\" \"models/car/body\" }");
        addon.Vmt(
            "middle",
            "Patch { \"include\" \"base\" \"insert\" { \"$selfillum\" \"1\" } }");
        addon.Vmt(
            "top",
            "Patch { \"include\" \"middle\" \"replace\" { \"$alphatest\" \"1\" } }");

        VtfConsumerGraph graph = VtfConsumerGraphBuilder.Build(
            addon.Raw,
            addon.Safe,
            Array.Empty<string>());
        VtfConsumerEvidence top = Assert.Single(
            graph.ConsumersFor("models/car/body"),
            consumer => consumer.ConsumerPath.EndsWith("top.vmt", StringComparison.Ordinal));

        Assert.Equal("VertexLitGeneric", top.Shader);
        Assert.Equal("1", Assert.Single(top.Parameters["$selfillum"]));
        Assert.Equal("1", Assert.Single(top.Parameters["$alphatest"]));
    }

    [Fact]
    public void MissingFrameworkIncludeIsRecordedFailClosed()
    {
        using var addon = new TestMaterialTree();
        addon.Vmt("paint", "Patch { \"include\" \"framework/missing\" }");

        VtfConsumerGraph graph = VtfConsumerGraphBuilder.Build(
            addon.Raw,
            addon.Safe,
            Array.Empty<string>());

        Assert.False(graph.IsClosed);
        Assert.Contains(graph.Failures, failure => failure.Code == "include_missing");
    }
}

internal sealed class TestMaterialTree : IDisposable
{
    internal TestMaterialTree()
    {
        Root = Path.Combine(
            Path.GetTempPath(),
            "VtfUniversal.Tests",
            "Consumers",
            Guid.NewGuid().ToString("N"));
        Raw = Path.Combine(Root, "raw");
        Safe = Path.Combine(Root, "safe");
        Directory.CreateDirectory(Raw);
        Directory.CreateDirectory(Safe);
    }

    internal string Root { get; }

    internal string Raw { get; }

    internal string Safe { get; }

    internal TestMaterialTree Vmt(string materialName, string content)
    {
        string relative = $"materials/{materialName}.vmt";
        Write(Raw, relative, content);
        Write(Safe, relative, content);
        return this;
    }

    internal TestMaterialTree Lua(string relativePath, string content)
    {
        Write(Safe, $"lua/{relativePath}", content);
        return this;
    }

    internal UniversalVtfInventoryEntry Entry(
        string textureName,
        int highResFormat = 15,
        int flags = 0,
        int frames = 1) =>
        new(
            $"materials/{textureName}.vtf",
            Bytes: 128,
            Sha256: new string('0', 64),
            MajorVersion: 7,
            MinorVersion: 2,
            HeaderSize: 80,
            Flags: flags,
            Width: 8,
            Height: 8,
            Depth: 1,
            Frames: frames,
            FirstFrame: 0,
            Faces: 1,
            MipCount: 4,
            HighResFormat: highResFormat,
            LowResFormat: 13,
            LowResWidth: 4,
            LowResHeight: 4,
            IsNomip: false,
            Resources: Array.Empty<VtfResourceChunk>(),
            Subresources: Array.Empty<VtfSubresourceRange>(),
            UnmappedRanges: Array.Empty<VtfByteRange>());

    public void Dispose()
    {
        if (Directory.Exists(Root))
        {
            Directory.Delete(Root, recursive: true);
        }
    }

    private static void Write(string root, string relativePath, string content)
    {
        string rootPrefix = Path.GetFullPath(root) + Path.DirectorySeparatorChar;
        string path = Path.GetFullPath(Path.Combine(
            root,
            relativePath.Replace('/', Path.DirectorySeparatorChar)));
        if (!path.StartsWith(rootPrefix, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException("Fixture path escaped its root.");
        }

        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, content);
    }
}
