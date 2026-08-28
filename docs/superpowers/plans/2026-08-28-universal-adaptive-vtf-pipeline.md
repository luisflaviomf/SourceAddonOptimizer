# Universal Adaptive VTF Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar o pipeline `universal-vtf-v1` que reduz obrigatoriamente todos os VTFs espacialmente redutíveis em pelo menos um nível, procura níveis superiores com métricas semânticas determinísticas e constrói três candidatos corpus-wide a partir das autoridades raw e safe sem modificá-las.

**Architecture:** O núcleo de produção ficará em `GmodAddonCompressor/Systems/Vtf/Universal` e será a única autoridade de decisão usada tanto pelo WPF quanto por um driver corpus-wide fino. O pipeline separa inventário/semântica, transformação de container, métricas, seleção, composição transacional e auditoria; um auditor Python independente verifica fixtures, hashes, containers e manifestos, mas nunca escolhe candidatos.

**Tech Stack:** C# 10, .NET 6 Windows, WPF, Magick.NET Q16 AnyCPU 14.10.4, VTFCmd 1.0.10, xUnit 2.6.6, Python 3.11 standard library e PowerShell.

**Spec:** `docs/superpowers/specs/2026-08-28-universal-adaptive-vtf-pipeline-design.md`

## Global Constraints

- Executar primeiro `superpowers:using-git-worktrees` e criar um worktree limpo a partir do commit `207ca92`; não desenvolver nem empacotar na árvore principal suja.
- Não restaurar integralmente, copiar em massa ou mesclar as worktrees divergentes de hipóteses antigas. Portar somente primitivas identificadas neste plano e revalidá-las por testes.
- Não modificar `gui/`, o frontend Python legado, o worker Python, models, VVD, VTX, áudio ou o harness do GMod.
- `lvs_cars_pack` é a autoridade imutável de todos os VTFs: 3.827 arquivos, 1.441.918.651 bytes, tree SHA-256 `5f7b31c77e34608c4a40c6b945a7ad24b151be4ad067761c24aa1f93ed908758`.
- `_runtime_validation/source/addons/lvs_cars_pack_CORPUS_BC3_TO_BC1_SAFE_FINAL_V1` é a autoridade imutável de todos os arquivos não-VTF: 3.664 arquivos, 438.230.044 bytes, tree SHA-256 `8e60b8b46744a192fd5ab1ab77848ab9fd4123f1dd042c31d365e16505a698af`.
- O censo raw obrigatório é 1.152 VTFs/835.331.232 bytes: 200 DXT1, 952 DXT5; versões 7.1=4, 7.2=1.145, 7.4=1, 7.5=2; 1.145 cadeias authored, sete NOMIP; 1.151 arquivos de uma face e um cubemap de sete faces.
- Cada VTF com largura > 1 ou altura > 1 termina em nível selecionado >= 1. Incerteza semântica escolhe o avaliador composite; nunca autoriza nível 0.
- Dimensões do nível N são `max(1, floor(original_axis / 2^N))`. Perfis obedecem `conservative <= balanced <= aggressive` e param no primeiro nível reprovado.
- VTFs e pixels vêm sempre do raw. Arquivos não-VTF vêm sempre da safe baseline. VTFs antigos da safe baseline nunca são autoridade de pixels.
- A cópia byte a byte dos não-VTF deve preservar e auditar as regras runtime já aprovadas: VTX flexado DX9 permanece no caminho seguro `0x07`, e static-flex/cache permanece no limite de 65.536 vértices por modelo com o rollback mínimo por mesh já incorporado à safe baseline.
- Preservar Standard e Magick sem mudanças de comportamento. O novo código entra somente pelo modo Maximum e pelo driver corpus-wide.
- Magick.NET deve permanecer 14.10.4. Assembly managed esperado: `5e2ff85094dfd8fa9e15c3f884c3e3ef483bc074538b8c4fc8e1e0aaa643dfd6`. Registrar também hashes dos binários native carregados.
- VTFCmd esperado: versão 1.0.10, 102.400 bytes, SHA-256 `30d185a6df81d0c5d09c7ecda91a3f49ff183c14a2db12aaa845dc88059ee11e`, extraído de `VTFEdit.zip` SHA-256 `87d0280223f60be947723cb67076ed47db55221ee69ca60a7f773e4926f11c3c`.
- O receipt BC3→BC1 tem exatamente 193 caminhos: 155 raw BC3 fisicamente opacos, 32 raw DXT1 e seis exceções raw BC3 semânticas. A aprovação antiga restringe caminho/consumidor, não aprova novos pixels raw-derived.
- Nunca escrever relatório dentro de uma árvore candidata. Destinos devem ser inexistentes; staging, auditoria e rename ocorrem antes da publicação.
- Não criar hardlinks entre autoridades, staging ou candidatos. Não seguir reparse points.
- Não promover automaticamente candidato sobre uma baseline. Preservar manifests, hashes, receipts, logs e rollback.
- Cada task termina com testes verdes e um commit contendo apenas seus arquivos declarados.

---

## File map

### Production core

- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfContracts.cs` — enums e records compartilhados.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfLevels.cs` — dimensões, piso físico e enumeração consecutiva.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfTreeInventory.cs` — path guard, inventário e tree hash canônico.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfContainerLayoutReader.cs` — ranges completos de header, LRS, HRS, subresources, resources e trailing bytes.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfContainerWriter.cs` — reconstrução determinística e relocação de ranges.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfInventoryScanner.cs` — censo estrutural por VTF.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VmtKeyValuesParser.cs` — AST KeyValues sem perda de includes/Patch/proxies.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfConsumerGraphBuilder.cs` — fechamento de consumidores VMT/Lua/PCF/framework.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfSemanticResolver.cs` — classes, canais, incertezas e rotas.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfPolicy.cs` — thresholds congelados e fingerprint.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfDownsampler.cs` — sRGB, linear, alpha, cutout, normal e SSBump.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfMetrics.cs` e `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfMetricCalculator.cs` — métricas e gates.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfCmdBlockEncoder.cs` — geração determinística BC para NOMIP/thumbnail.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCandidateMaterializer.cs` — promoção authored ou geração direta do mip 0.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/Bc3ApprovedReceipt.cs`, `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/Bc3RawBridgeStore.cs` e `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/Bc3ColorBlockTranscoder.cs` — bridge 155/32/6 canônico e hashado.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfSelector.cs` — attempt matrix e três seleções monotônicas.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfManifestWriter.cs` — 1.152 rows e contabilidade.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusBuilder.cs` e `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusAuditor.cs` — composição transacional.

### Integration, driver and tests

- `.gitignore` — libera somente os dois novos projetos Universal sob `experiments/` e continua ignorando seus `bin/obj`.
- `Objects/MaximumVTFEdit.cs`, `Systems/CompressAddonSystem.cs`, `Models/CompressPipelineOptions.cs` — integração Maximum batch.
- `experiments/VtfUniversalCorpusDriver/` — CLI fino chamando produção.
- `experiments/VtfUniversal.Tests/` — suíte xUnit nova e fixtures sintéticas.
- `Resources/universal-vtf-v1-fixtures.json` — vetores congelados.
- `scripts/audit_universal_vtf_v1.py` e `tests/test_audit_universal_vtf_v1.py` — auditor independente.
- `docs/universal-vtf-v1.md` — operação e rollback.

---

### Task 1: Criar contratos, driver e suíte de testes independentes do laboratório antigo

**Files:**
- Modify: `.gitignore`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfContracts.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfLevels.cs`
- Create: `experiments/VtfUniversal.Tests/VtfUniversal.Tests.csproj`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfLevelsTests.cs`
- Create: `experiments/VtfUniversalCorpusDriver/VtfUniversalCorpusDriver.csproj`
- Create: `experiments/VtfUniversalCorpusDriver/Program.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/AssemblyInfo.cs`

**Interfaces:**
- Produces: `UniversalVtfProfileKind`, `UniversalVtfSemanticClass`, `UniversalVtfFilterRoute`, `UniversalVtfDimensions`.
- Produces: `UniversalVtfLevels.At(int,int,int)`, `IsReducible(int,int)` e `Enumerate(int,int)`.
- Versiona somente `experiments/VtfUniversal.Tests/**` e `experiments/VtfUniversalCorpusDriver/**`; `bin/obj` continuam ignorados.

- [ ] **Step 1: Escrever o teste RED do piso universal**

~~~csharp
[Theory]
[InlineData(1024, 512, 1, 512, 256)]
[InlineData(3, 1, 1, 1, 1)]
[InlineData(1, 9, 3, 1, 1)]
public void ComputesPerAxisClampedDimensions(
    int width, int height, int level, int expectedWidth, int expectedHeight)
{
    Assert.Equal(new UniversalVtfDimensions(expectedWidth, expectedHeight),
        UniversalVtfLevels.At(width, height, level));
}

[Fact]
public void OnlyOneByOneIsIrreducible()
{
    Assert.False(UniversalVtfLevels.IsReducible(1, 1));
    Assert.True(UniversalVtfLevels.IsReducible(1, 2));
    Assert.True(UniversalVtfLevels.IsReducible(2, 1));
}
~~~

- [ ] **Step 2: Criar csproj e confirmar RED**

Ambos usam `net6.0-windows`; testes usam Microsoft.NET.Test.Sdk 17.8.0 e xUnit 2.6.6; ambos referenciam o WPF.

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfLevelsTests -v minimal
~~~

Expected: FAIL por tipos ausentes.

- [ ] **Step 3: Implementar contratos mínimos**

~~~csharp
internal enum UniversalVtfProfileKind { Conservative, Balanced, Aggressive }
[Flags]
internal enum UniversalVtfSemanticClass
{
    Unknown=1, Diffuse=2, Detail=4, Normal=8, ScalarMask=16, Cutout=32,
    Glass=64, Emissive=128, Text=256, Cubemap=512, Animated=1024, Ssbump=2048
}
internal enum UniversalVtfFilterRoute
{
    SrgbColor, NumericLinear, RgbNormal, Dxt5NmAg, SsbumpScalars, Composite
}
internal static class VtfFlags { internal const int NoMip=0x00000100; }
internal readonly record struct UniversalVtfDimensions(int Width, int Height);

internal static UniversalVtfDimensions At(int width, int height, int level)
{
    if (width < 1 || height < 1 || level < 0) throw new ArgumentOutOfRangeException();
    int divisor = level >= 30 ? int.MaxValue : 1 << level;
    return new(Math.Max(1, width / divisor), Math.Max(1, height / divisor));
}

internal static bool IsReducible(int width,int height) => width>1 || height>1;

internal static IReadOnlyList<int> Enumerate(int width,int height)
{
    var levels=new List<int>();
    for(int level=1;IsReducible(At(width,height,level-1).Width,
                               At(width,height,level-1).Height);level++)
        levels.Add(level);
    return levels;
}
~~~

Adicionar `InternalsVisibleTo("VtfUniversal.Tests")` e `InternalsVisibleTo("VtfUniversalCorpusDriver")`.

Adicionar ao `.gitignore`, depois das exceções dos labs antigos:

~~~gitignore
!/experiments/VtfUniversal.Tests/
!/experiments/VtfUniversal.Tests/**
!/experiments/VtfUniversalCorpusDriver/
!/experiments/VtfUniversalCorpusDriver/**
/experiments/VtfUniversal.Tests/**/bin/
/experiments/VtfUniversal.Tests/**/obj/
/experiments/VtfUniversalCorpusDriver/**/bin/
/experiments/VtfUniversalCorpusDriver/**/obj/
~~~

- [ ] **Step 4: Rodar GREEN e driver smoke**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release -v minimal
dotnet run --project .\experiments\VtfUniversalCorpusDriver\VtfUniversalCorpusDriver.csproj -c Release -- --help
git check-ignore .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj
if($LASTEXITCODE-eq 0){throw "Universal test project is still ignored"}
git check-ignore .\experiments\VtfUniversalCorpusDriver\VtfUniversalCorpusDriver.csproj
if($LASTEXITCODE-eq 0){throw "Universal driver project is still ignored"}
~~~

Expected: PASS; help retorna 0.

- [ ] **Step 5: Commit**

~~~powershell
git add .gitignore `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal `
        GmodAddonCompressor-master/GmodAddonCompressor/AssemblyInfo.cs `
        experiments/VtfUniversal.Tests experiments/VtfUniversalCorpusDriver
git commit -m "test: establish universal VTF contracts"
~~~

### Task 2: Inventariar árvores, conter paths e verificar anchors

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfTreeInventory.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfTreeInventoryTests.cs`

**Interfaces:**
- `Capture(string root) -> UniversalVtfTreeSnapshot`.
- `RequireAnchor(snapshot, expected)` e `ResolveNewDestination(parent,name)`.
- Hash: path-length LE32 + UTF-8 path + size LE64 + SHA-256 bytes, path casefold-sorted.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public void ComputesCanonicalDigest()
{
    using var tree = TestTree.Create(
        ("a.txt", new byte[]{0x41}), ("sub/b.bin", new byte[]{0x00,0xff}));
    var actual = UniversalVtfTreeInventory.Capture(tree.Root);
    Assert.Equal("75e21d8d75cff49a5579a92b2f881167a5cb57ee7fa08ba2602ee84458b424eb",
        actual.TreeSha256);
}

[Fact]
public void RejectsCollisionReparseAndExistingDestination()
{
    using var tree = TestTree.WithUnsafeCases();
    Assert.Throws<InvalidDataException>(() => UniversalVtfTreeInventory.Capture(tree.Root));
    Assert.Throws<IOException>(() =>
        UniversalVtfTreeInventory.ResolveNewDestination(tree.Root, "already-exists"));
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfTreeInventoryTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar algoritmo exato**

~~~csharp
byte[] pathBytes = Encoding.UTF8.GetBytes(relativePath.Replace('\\','/'));
Span<byte> scalar = stackalloc byte[8];
BinaryPrimitives.WriteUInt32LittleEndian(scalar, checked((uint)pathBytes.Length));
tree.AppendData(scalar[..4]);
tree.AppendData(pathBytes);
BinaryPrimitives.WriteUInt64LittleEndian(scalar, checked((ulong)length));
tree.AppendData(scalar);
tree.AppendData(fileSha256Bytes);
~~~

Rejeitar reparse points, traversal, case collisions, arquivo que muda tamanho/mtime/hash durante captura e qualquer destination existente.

- [ ] **Step 4: Rodar GREEN**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfTreeInventoryTests -v minimal
~~~

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfTreeInventory.cs `
        experiments/VtfUniversal.Tests/UniversalVtfTreeInventoryTests.cs
git commit -m "feat: anchor universal VTF source trees"
~~~

### Task 3: Parsear ranges completos e resources VTF

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfContainerLayoutReader.cs`
- Create: `experiments/VtfUniversal.Tests/Fixtures/VtfFixtureBuilder.cs`
- Create: `experiments/VtfUniversal.Tests/VtfContainerLayoutReaderTests.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Models/VtfDocumentInfo.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/VtfDocumentReader.cs`

**Interfaces:**
- Preserva `VtfDocumentReader.TryRead`.
- Produces `VtfContainerLayoutReader.Read(string) -> VtfContainerLayout`.
- Um range por mip/frame/face/depth, LRS, HRS, resources offset/inline e trailing/unmapped.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public void SplitsSevenFaceRanges()
{
    string path = VtfFixtureBuilder.WriteCubemap74(8,8,4);
    var layout = VtfContainerLayoutReader.Read(path);
    Assert.Equal(28, layout.Subresources.Count);
}

[Fact]
public void InlineCrcIsNotAnOffset()
{
    string path = VtfFixtureBuilder.WriteResource74WithInlineCrc(0x3C66AA52);
    var crc = VtfContainerLayoutReader.Read(path).Resources.Single(x => x.Tag == 0x435243);
    Assert.Equal(0x3C66AA52u, crc.InlineValue);
    Assert.Null(crc.PayloadRange);
}

[Fact]
public void RejectsOverlappingRanges() =>
    Assert.Throws<InvalidDataException>(() =>
        VtfContainerLayoutReader.Read(VtfFixtureBuilder.WriteOverlappingResources()));

[Fact]
public void PartitionsEveryFrameFaceAndDepthSlice()
{
    string path=VtfFixtureBuilder.WriteVolumeWithFrames(
        width:8,height:8,depth:4,frames:2,mips:4);
    var layout=VtfContainerLayoutReader.Read(path);
    Assert.Equal(2*4 + 2*2 + 2*1 + 2*1,layout.Subresources.Count);
    Assert.All(layout.Subresources,x=>Assert.True(x.Range.Length>0));
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter VtfContainerLayoutReaderTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar records e parser checked**

~~~csharp
internal sealed record VtfByteRange(long Offset, int Length, string Sha256);
internal sealed record VtfSubresourceRange(
    int MipLevel, int Frame, int Face, int DepthSlice,
    int Width, int Height, VtfByteRange Range);
internal sealed record VtfResourceChunk(
    int Tag, byte Flags, uint? InlineValue, VtfByteRange? PayloadRange);
internal sealed record VtfContainerLayout(
    VtfDocumentInfo Document, VtfByteRange Header, VtfByteRange? LowResolutionImage,
    IReadOnlyList<VtfSubresourceRange> Subresources,
    IReadOnlyList<VtfResourceChunk> Resources,
    IReadOnlyList<VtfByteRange> UnmappedRanges, string FileSha256);
~~~

Particionar cada mip por `frames*faces*depth`, derivar chunk offset pelo próximo offset/EOF e rejeitar overlap, overflow, truncamento, offset em header ou gap sem classificação.

- [ ] **Step 4: Rodar GREEN e build WPF**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter VtfContainerLayoutReaderTests -v minimal
dotnet build .\GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj -c Release
~~~

Expected: PASS/build 0.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Models/VtfDocumentInfo.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/VtfDocumentReader.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfContainerLayoutReader.cs `
        experiments/VtfUniversal.Tests/Fixtures/VtfFixtureBuilder.cs `
        experiments/VtfUniversal.Tests/VtfContainerLayoutReaderTests.cs
git commit -m "feat: parse complete VTF container layouts"
~~~

### Task 4: Reproduzir o censo raw e bloquear drift

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfInventoryScanner.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfInventoryScannerTests.cs`
- Modify: `experiments/VtfUniversalCorpusDriver/Program.cs`

**Interfaces:**
- `Scan(rawRoot) -> UniversalVtfInventory`.
- `UniversalVtfCorpusExpectations.LvsV1.Verify(inventory,snapshot)`.
- CLI `inventory --raw-root --output`.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public void RecordsEveryParseFailure()
{
    using var tree = TestTree.CreateVtfs(validCount:2, malformedCount:1);
    var inventory = UniversalVtfInventoryScanner.Scan(tree.Root);
    Assert.Equal(3, inventory.DiscoveredCount);
    Assert.Equal(2, inventory.Entries.Count);
    Assert.Single(inventory.Failures);
    Assert.False(inventory.IsComplete);
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfInventoryScannerTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar censo e expectativa exata**

~~~csharp
internal static readonly UniversalVtfCorpusExpectation LvsV1 = new(
    VtfCount:1152, VtfBytes:835_331_232,
    Formats:new Dictionary<int,int>{{13,200},{15,952}},
    Versions:new Dictionary<string,int>{{"7.1",4},{"7.2",1145},{"7.4",1},{"7.5",2}},
    AuthoredChains:1145, NomipFiles:7, SingleFaceFiles:1151, SevenFaceFiles:1,
    SingleFrameFiles:1152, DepthOneFiles:1152, DimensionallyIrreducible:0,
    ResourceDirectoryFiles:3);
~~~

Cada entry guarda hash/bytes, versão/header/flags, dimensões/depth/mips, formatos, frames/faces, NOMIP, resources e subresource ranges. Os três resource files precisam decompor em dois VTF 7.5 LRS+HRS offset e um VTF 7.4 HRS offset+CRC inline. Nenhuma parse failure pode ser omitida.

- [ ] **Step 4: Rodar GREEN e censo real read-only**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfInventoryScannerTests -v minimal
dotnet run --project .\experiments\VtfUniversalCorpusDriver\VtfUniversalCorpusDriver.csproj -c Release -- inventory `
  --raw-root "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack" `
  --output "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\raw-inventory.json"
~~~

Expected: 1.152/835.331.232, censo/anchor exatos, zero failures.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfInventoryScanner.cs `
        experiments/VtfUniversal.Tests/UniversalVtfInventoryScannerTests.cs `
        experiments/VtfUniversalCorpusDriver/Program.cs
git commit -m "feat: freeze the raw VTF corpus inventory"
~~~

### Task 5: Fechar o grafo de consumidores e resolver semântica

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VmtKeyValuesParser.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfConsumerGraphBuilder.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfSemanticResolver.cs`
- Create: `experiments/VtfUniversal.Tests/VtfConsumerGraphTests.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfSemanticResolverTests.cs`

**Interfaces:**
- `Parse(content,sourcePath) -> VmtDocument`.
- `Build(rawRoot,effectiveSafeRoot,frameworkRoots) -> VtfConsumerGraph`.
- `Resolve(entry,graph) -> UniversalVtfSemanticDecision`.

- [ ] **Step 1: Escrever RED para Patch/include/proxy/ciclo/dinâmico**

~~~csharp
[Fact]
public void PatchUnionAndDynamicReferenceBecomeComposite()
{
    using var addon = TestMaterialTree.Create()
        .Vmt("base","VertexLitGeneric { \"$basetexture\" \"models/car/body\" }")
        .Vmt("paint","Patch { \"include\" \"base\" \"insert\" { \"$selfillum\" \"1\" } }")
        .Lua("runtime.lua","Material(prefix .. skin)");
    var graph = VtfConsumerGraphBuilder.Build(addon.Raw,addon.Safe,Array.Empty<string>());
    var d = UniversalVtfSemanticResolver.Resolve(addon.Entry("models/car/body"),graph);
    Assert.Contains(UniversalVtfSemanticClass.Diffuse,d.Classes);
    Assert.Contains(UniversalVtfSemanticClass.Emissive,d.Classes);
    Assert.True(d.HasUnknownConsumer);
    Assert.Equal(UniversalVtfFilterRoute.Composite,d.Route);
}

[Fact]
public void IncludeCycleNeverProvesAlphaUnused()
{
    using var addon=TestMaterialTree.IncludeCycle();
    var graph=VtfConsumerGraphBuilder.Build(
        addon.Raw,addon.Safe,Array.Empty<string>());
    Assert.False(graph.IsClosed);
    Assert.Contains(graph.Failures,x => x.Code=="include_cycle");
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "VtfConsumerGraphTests|UniversalVtfSemanticResolverTests" -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar AST e resolução fail-closed**

Lexer preserva quoted/bare, escapes, comments, condicionais duplicadas e source spans. DFS expande `Patch include/insert/replace`, detecta ciclos, une ramos condicionais e registra proxies/dynamic Lua/PCF como uncertainty.

~~~csharp
internal sealed record VtfConsumerEvidence(
    string ConsumerPath,string Shader,string Key,string TextureKey,
    IReadOnlySet<int> ReadChannels,bool Dynamic,string ResolutionTrace);
internal sealed record UniversalVtfSemanticDecision(
    IReadOnlySet<UniversalVtfSemanticClass> Classes,UniversalVtfFilterRoute Route,
    bool HasUnknownConsumer,double AlphaTestReference,
    IReadOnlyList<VtfConsumerEvidence> Consumers,IReadOnlyList<string> Uncertainties);
~~~

Ordem: SSBump; normal RGB/DXT5nm; scalar; sRGB color; flag fallback; conflito = Composite. Incerteza ainda permite level 1. O corpus real precisa ainda reconciliar os 1.525 VMT paths raw contra VMT/Lua efetivamente enviados pela safe tree; include/framework não resolvido permanece no receipt como unknown consumer.

- [ ] **Step 4: Rodar GREEN e build**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "VtfConsumerGraphTests|UniversalVtfSemanticResolverTests" -v minimal
dotnet build .\GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj -c Release
~~~

Expected: PASS/build 0.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VmtKeyValuesParser.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfConsumerGraphBuilder.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfSemanticResolver.cs `
        experiments/VtfUniversal.Tests/VtfConsumerGraphTests.cs `
        experiments/VtfUniversal.Tests/UniversalVtfSemanticResolverTests.cs
git commit -m "feat: resolve VTF consumers fail closed"
~~~

### Task 6: Congelar política, thresholds, fixtures e fingerprint

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfPolicy.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Resources/universal-vtf-v1-fixtures.json`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfPolicyTests.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/GmodAddonCompressor.csproj`

**Interfaces:**
- `UniversalVtfPolicy.V1`, `GetThresholds(profile)`, `ComputeFingerprint(toolchain,bcBridgeSha256)`.
- Fingerprint inclui fixture SHA, policy canônica, Magick managed/native, VTFCmd, args e o SHA-256 explícito do mapping `bc3-raw-to-approved-v1`.

- [ ] **Step 1: Escrever RED de nesting/resource**

~~~csharp
[Fact]
public void ThresholdsAreNested()
{
    var c=UniversalVtfPolicy.V1.GetThresholds(UniversalVtfProfileKind.Conservative);
    var b=UniversalVtfPolicy.V1.GetThresholds(UniversalVtfProfileKind.Balanced);
    var a=UniversalVtfPolicy.V1.GetThresholds(UniversalVtfProfileKind.Aggressive);
    Assert.True(c.CommonRgbPsnrMin>=b.CommonRgbPsnrMin && b.CommonRgbPsnrMin>=a.CommonRgbPsnrMin);
    Assert.True(c.NormalP95Max<=b.NormalP95Max && b.NormalP95Max<=a.NormalP95Max);
}

[Fact]
public void BcBridgeHashIsARequiredPolicyInput()
{
    string a=UniversalVtfPolicy.V1.ComputeFingerprint(FakeToolchain.Valid,"00"+new string('1',62));
    string b=UniversalVtfPolicy.V1.ComputeFingerprint(FakeToolchain.Valid,"00"+new string('2',62));
    Assert.NotEqual(a,b);
    Assert.Throws<ArgumentException>(()=>
        UniversalVtfPolicy.V1.ComputeFingerprint(FakeToolchain.Valid,"not-a-sha256"));
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfPolicyTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar a tabela inteira da spec**

~~~csharp
internal sealed record UniversalVtfThresholds(
    double CommonRgbPsnrMin,double CommonRgbSsimMin,double EdgeRetentionMin,
    double NormalMeanMax,double NormalP95Max,double NormalMax,
    double AlphaSsimMin,double AlphaMaeMax,double AlphaP99Max,
    double CutoutCoverageTopMax,double CutoutCoverageMipsMax,double CutoutIouMin,
    double EmissiveEnergyDriftMax,double EmissiveHotspotIouMin,
    double GlassDeltaEMax,double GlassEdgeRetentionMin,
    double TextPsnrMin,double TextSsimMin,double TextEdgeRetentionMin,
    double DetailFrequencyRetentionMin,double DetailSeamIncreaseMax,
    double CubemapSeamIncreaseMax,double TemporalPsnrMin,
    double ComponentRetentionMin,double HausdorffMax);

internal static readonly UniversalVtfThresholds Conservative = new(
    CommonRgbPsnrMin:26,CommonRgbSsimMin:.950,EdgeRetentionMin:.50,
    NormalMeanMax:3,NormalP95Max:8,NormalMax:25,
    AlphaSsimMin:.980,AlphaMaeMax:.020,AlphaP99Max:.080,
    CutoutCoverageTopMax:.01,CutoutCoverageMipsMax:.02,CutoutIouMin:.980,
    EmissiveEnergyDriftMax:.05,EmissiveHotspotIouMin:.950,
    GlassDeltaEMax:2,GlassEdgeRetentionMin:.65,
    TextPsnrMin:30,TextSsimMin:.970,TextEdgeRetentionMin:.70,
    DetailFrequencyRetentionMin:.70,DetailSeamIncreaseMax:1d/255,
    CubemapSeamIncreaseMax:2d/255,TemporalPsnrMin:26,
    ComponentRetentionMin:1.00,HausdorffMax:2);

internal static readonly UniversalVtfThresholds Balanced = new(
    CommonRgbPsnrMin:24,CommonRgbSsimMin:.925,EdgeRetentionMin:.40,
    NormalMeanMax:5,NormalP95Max:14,NormalMax:40,
    AlphaSsimMin:.960,AlphaMaeMax:.040,AlphaP99Max:.140,
    CutoutCoverageTopMax:.02,CutoutCoverageMipsMax:.04,CutoutIouMin:.950,
    EmissiveEnergyDriftMax:.10,EmissiveHotspotIouMin:.900,
    GlassDeltaEMax:4,GlassEdgeRetentionMin:.55,
    TextPsnrMin:27,TextSsimMin:.950,TextEdgeRetentionMin:.55,
    DetailFrequencyRetentionMin:.55,DetailSeamIncreaseMax:2d/255,
    CubemapSeamIncreaseMax:4d/255,TemporalPsnrMin:24,
    ComponentRetentionMin:.99,HausdorffMax:4);

internal static readonly UniversalVtfThresholds Aggressive = new(
    CommonRgbPsnrMin:22,CommonRgbSsimMin:.900,EdgeRetentionMin:.30,
    NormalMeanMax:8,NormalP95Max:22,NormalMax:60,
    AlphaSsimMin:.930,AlphaMaeMax:.070,AlphaP99Max:.240,
    CutoutCoverageTopMax:.04,CutoutCoverageMipsMax:.07,CutoutIouMin:.900,
    EmissiveEnergyDriftMax:.20,EmissiveHotspotIouMin:.800,
    GlassDeltaEMax:6,GlassEdgeRetentionMin:.45,
    TextPsnrMin:24,TextSsimMin:.920,TextEdgeRetentionMin:.40,
    DetailFrequencyRetentionMin:.40,DetailSeamIncreaseMax:4d/255,
    CubemapSeamIncreaseMax:8d/255,TemporalPsnrMin:22,
    ComponentRetentionMin:.97,HausdorffMax:8);
~~~

As fixtures incluem identidade, half-plane RGB, alpha bimodal/não bimodal, normals 0/90/180, RGB/DXT5nm/normal-alpha/SSBump, percentis, reflect-101, zero handling, route tie, 32 bisseções, frames e cubemap. A serialização canônica exige um SHA-256 de bridge de 64 hex; nenhum default, path ou conteúdo não hashado entra silenciosamente.

- [ ] **Step 4: Embutir fixture e rodar GREEN**

~~~xml
<EmbeddedResource Include="Resources\universal-vtf-v1-fixtures.json"
 LogicalName="GmodAddonCompressor.Resources.universal-vtf-v1-fixtures.json" />
~~~

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfPolicyTests -v minimal
~~~

Expected: PASS; JSON canônico sem timestamp.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfPolicy.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Resources/universal-vtf-v1-fixtures.json `
        GmodAddonCompressor-master/GmodAddonCompressor/GmodAddonCompressor.csproj `
        experiments/VtfUniversal.Tests/UniversalVtfPolicyTests.cs
git commit -m "feat: freeze universal VTF policy v1"
~~~

### Task 7: Downsample semântico direto do mip 0

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfDownsampler.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCoverageScaler.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfDownsamplerTests.cs`

**Interfaces:**
- `Downsample(source,target,route,semantics) -> UniversalVtfImagePlane`.
- `FindScale(alpha,threshold,targetCoverage)`.
- Cada nível N recebe mip 0, nunca N-1.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public void GeneratedLevelUsesSourceZero()
{
    var source=ImageFixtures.HighFrequencyColor(32,32);
    var direct=UniversalVtfDownsampler.Downsample(source,new(8,8),
        UniversalVtfFilterRoute.SrgbColor,SemanticFixtures.Diffuse);
    var recursive=UniversalVtfDownsampler.Downsample(
        UniversalVtfDownsampler.Downsample(source,new(16,16),
            UniversalVtfFilterRoute.SrgbColor,SemanticFixtures.Diffuse),
        new(8,8),UniversalVtfFilterRoute.SrgbColor,SemanticFixtures.Diffuse);
    Assert.NotEqual(recursive.Rgba,direct.Rgba);
}

[Fact]
public void VectorNormalUsesExactAreaFootprintInsteadOfLanczos()
{
    var source=NormalFixtures.AsymmetricThreeByOneRgb();
    var actual=UniversalVtfDownsampler.Downsample(source,new(2,1),
        UniversalVtfFilterRoute.RgbNormal,SemanticFixtures.Normal);
    Assert.Equal(NormalFixtures.ExactFootprintThreeToTwo,actual.Rgba);
    Assert.NotEqual(NormalFixtures.LanczosThenNormalizeThreeToTwo,actual.Rgba);
}

[Fact]
public void ZeroLengthAverageRejectsTheCandidate() =>
    Assert.Throws<InvalidDataException>(()=>
        UniversalVtfDownsampler.Downsample(
            NormalFixtures.OpposingPair(),new(1,1),
            UniversalVtfFilterRoute.RgbNormal,SemanticFixtures.Normal));
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfDownsamplerTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar rotas**

sRGB IEC 61966-2-1→linear→LanczosSharp→sRGB; alpha contínuo premultiplied; scalars independentes; SSBump três scalars. Para normals, não usar Lanczos: para cada pixel de destino integrar os vetores decodificados com pesos de interseção sobre o footprint fonte exato `[x*srcW/dstW,(x+1)*srcW/dstW) × [y*srcH/dstH,(y+1)*srcH/dstH)`, dividir pela área total e só então renormalizar. Média de comprimento zero invalida o candidato. RGB normals reescrevem XYZ; DXT5nm usa X=A/Y=G e filtra R/B auxiliares separadamente como scalars lineares.

~~~csharp
internal sealed record UniversalVtfImagePlane(
    int Width,int Height,int Frame,int Face,int DepthSlice,byte[] Rgba);
~~~

Coverage usa `{0,1,8}` + 32 bisseções, round-to-even, menor erro, tie por `abs(log2(scale))` e menor scale:

~~~csharp
for(int i=0;i<32;i++){
    double mid=(low+high)/2;
    candidates.Add(mid);
    if(Coverage(alpha,mid,threshold)<target) low=mid; else high=mid;
}
~~~

- [ ] **Step 4: Rodar GREEN duas vezes**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfDownsamplerTests -v minimal
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfDownsamplerTests -v minimal
~~~

Expected: PASS e buffers idênticos.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfDownsampler.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCoverageScaler.cs `
        experiments/VtfUniversal.Tests/UniversalVtfDownsamplerTests.cs
git commit -m "feat: downsample VTF planes by semantics"
~~~

### Task 8: Implementar métricas comuns e especializadas

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfMetrics.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfMetricCalculator.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfMetricCalculatorTests.cs`

**Interfaces:**
- `MeasurePlane(reference,candidate,semanticDecision) -> UniversalVtfPlaneMetricSet`.
- `MeasureAllPlanes(referencePlanes,candidatePlanes,semanticDecision) -> UniversalVtfMetricSet`.
- `EvaluateWorstPlane(metrics,thresholds) -> UniversalVtfEvaluation`; o mesmo metric set sem thresholds alimenta os três perfis e nenhum gate usa média global entre planos.

- [ ] **Step 1: Escrever RED a partir das fixtures**

~~~csharp
[Fact]
public void MatchesEveryFrozenFixture()
{
    foreach(var f in FixtureData.MetricFixtures)
        FixtureAssert.EqualWithin(
            UniversalVtfMetricCalculator.MeasurePlane(
                f.Reference,f.Candidate,f.Semantics),f.Expected,1e-12);
}

[Fact]
public void LocksHalfPlaneValues()
{
    var f=FixtureData.HalfPlaneVs128;
    var m=UniversalVtfMetricCalculator.MeasurePlane(
        f.Reference,f.Candidate,f.Semantics);
    Assert.Equal(6.020533124945522,m.Common.RgbPsnr,12);
    Assert.Equal(0,m.Common.EdgeRetention);
}


[Fact]
public void WorstFrameFailsEvenWhenGlobalAverageWouldPass()
{
    var fixture=FixtureData.TwoFramesWithOneCorrupt;
    var metrics=UniversalVtfMetricCalculator.MeasureAllPlanes(
        fixture.Reference,fixture.Candidate,fixture.Semantics);
    var result=UniversalVtfMetricCalculator.EvaluateWorstPlane(metrics,
        UniversalVtfPolicy.V1.GetThresholds(UniversalVtfProfileKind.Balanced));
    Assert.False(result.Passed);
    Assert.Equal(new UniversalVtfPlaneKey(Frame:1,Face:0,DepthSlice:0),
        result.WorstGate.Plane);
    Assert.Equal(2,metrics.Planes.Count);
}

[Theory]
[InlineData(0,6,0)]
[InlineData(0,0,2)]
public void WorstFaceOrDepthSliceCannotBeHidden(int frame,int face,int depth)
{
    var result=MetricFixtures.OneBadPlane(frame,face,depth).EvaluateBalanced();
    Assert.False(result.Passed);
    Assert.Equal(new UniversalVtfPlaneKey(frame,face,depth),result.WorstGate.Plane);
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfMetricCalculatorTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar fórmulas exatas**

Bilinear no domínio correto; PSNR sobre samples decodificados 8-bit com peak255; SSIM min/canal Gaussian11 sigma1.5 K1=.01 K2=.03 população reflect-101; edge Rec.709; nearest-rank; normal angles; CIEDE2000 D65/2°; coverage/IoU/componentes/Hausdorff; temporal signed-difference peak510; zero/empty/NaN rules. Exigir conjuntos idênticos de `(frame,face,depthSlice)`, medir cada plano isoladamente e aprovar somente quando cada gate de cada plano passar. O `WorstGate` é a menor margem normalizada, com key do plano; global averaging é proibido. Gates temporais carregam o par de frames e gates de cubemap carregam o par/orientação de faces, somando-se — nunca substituindo — aos gates individuais.

Alpha é cutout-like somente quando >=95% cai em [0,.1] ou [.9,1] e cada polo tem >=1%. Hotspot emissive usa o mesmo threshold absoluto `max(.8*sourcePeak,sourceP95)` nos dois lados. Detail FFT usa magnitude quadrada fora da meia banda central, sem window e excluindo DC; seams medem aumento candidato-fonte. Cubemap usa a tabela de orientação Source.

~~~csharp
internal sealed record UniversalVtfGateResult(
    string Name,double Value,string Operator,double Threshold,
    double NormalizedMargin,bool Passed,string Reason,
    UniversalVtfPlaneKey? Plane,string? PlanePair);
internal readonly record struct UniversalVtfPlaneKey(
    int Frame,int Face,int DepthSlice);
internal sealed record UniversalVtfCommonMetrics(
    double RgbPsnr,double RgbSsim,double EdgeRetention);
internal sealed record UniversalVtfPlaneMetricSet(
    UniversalVtfPlaneKey Key,
    UniversalVtfCommonMetrics Common,
    IReadOnlyDictionary<string,double> SemanticValues);
internal sealed record UniversalVtfMetricSet(
    IReadOnlyList<UniversalVtfPlaneMetricSet> Planes,
    IReadOnlyDictionary<string,double> CrossPlaneValues);
internal sealed record UniversalVtfEvaluation(
    IReadOnlyList<UniversalVtfGateResult> Gates,
    UniversalVtfGateResult WorstGate,bool Passed);

internal static double Margin(double value,double threshold,bool minimum) =>
    minimum ? (value-threshold)/Math.Max(Math.Abs(threshold),1e-12)
            : (threshold-value)/Math.Max(Math.Abs(threshold),1e-12);
~~~

- [ ] **Step 4: Rodar GREEN**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfMetricCalculatorTests -v minimal
~~~

Expected: PASS; doubles <=1e-12, counts/classes exact.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfMetrics.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfMetricCalculator.cs `
        experiments/VtfUniversal.Tests/UniversalVtfMetricCalculatorTests.cs
git commit -m "feat: implement universal VTF quality metrics"
~~~

### Task 9: Verificar toolchain e gerar BC determinístico

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfToolchain.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfCmdBlockEncoder.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfToolchainTests.cs`
- Create: `experiments/VtfUniversal.Tests/VtfCmdBlockEncoderTests.cs`

**Interfaces:**
- `Verify() -> UniversalVtfToolchainFingerprint`.
- `EncodeAsync(plane,format,sourceVersion,workDir,token) -> VtfEncodedBlockSet`.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public void WrongVtfCmdHashFailsBeforeExecution() =>
    Assert.Throws<InvalidDataException>(() =>
        UniversalVtfToolchain.Verify(FakeToolchain.WrongHash.Root));

[Fact]
public async Task ArgumentsAreCanonical()
{
    var runner=new RecordingProcessRunner();
    await new VtfCmdBlockEncoder(FakeToolchain.Valid,runner)
        .EncodeAsync(ImageFixtures.Opaque8,13,new Version(7,5),Temp.NewDir(),default);
    Assert.Equal(new[]{"-file",runner.InputPng,"-output",runner.OutputDirectory,
        "-format","DXT1","-alphaformat","DXT1","-version","7.4"},runner.Arguments);
}

[Fact]
[Trait("Category","RealToolchain")]
public void EmbeddedToolsMatchFrozenHashes()
{
    if(Environment.GetEnvironmentVariable("VTF_UNIVERSAL_REAL_TOOLCHAIN")!="1") return;
    var f=UniversalVtfToolchain.Verify(ToolchainPaths.ExtractEmbeddedTo(Temp.NewDir()));
    Assert.Contains(f.Binaries,x=>x.Name=="VTFCmd.exe" &&
        x.Sha256=="30d185a6df81d0c5d09c7ecda91a3f49ff183c14a2db12aaa845dc88059ee11e");
    Assert.Contains(f.Binaries,x=>x.Name.StartsWith("Magick.NET",StringComparison.Ordinal) &&
        x.Sha256=="5e2ff85094dfd8fa9e15c3f884c3e3ef483bc074538b8c4fc8e1e0aaa643dfd6");
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "UniversalVtfToolchainTests|VtfCmdBlockEncoderTests" -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar verificação/encoder**

Paths absolutos, culture invariant, output dir exclusivo, stdout/stderr/exit capturados, um VTF produzido, parse/decode obrigatórios. VTFCmd version é `min(source,7.4)`; writer restaura container autoritativo. Magick fingerprint enumera managed e native carregados por `name|bytes|sha`.

~~~csharp
internal sealed record UniversalVtfToolBinary(
    string Name,string Version,long Bytes,string Sha256);
internal sealed record UniversalVtfToolchainFingerprint(
    IReadOnlyList<UniversalVtfToolBinary> Binaries,
    string CanonicalArguments,string Sha256);
internal sealed record VtfEncodedBlockSet(
    int Format,int Width,int Height,IReadOnlyList<byte[]> SubresourcePayloads);
~~~

- [ ] **Step 4: GREEN e smoke real**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "UniversalVtfToolchainTests|VtfCmdBlockEncoderTests" -v minimal
$env:VTF_UNIVERSAL_REAL_TOOLCHAIN="1"
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "Category=RealToolchain" -v minimal
Remove-Item Env:\VTF_UNIVERSAL_REAL_TOOLCHAIN
~~~

Expected: hashes exatos e DXT1/DXT5 encode/parse/decode válidos usando os resources reais; nenhum comando futuro do driver é chamado nesta task.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfToolchain.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfCmdBlockEncoder.cs `
        experiments/VtfUniversal.Tests/UniversalVtfToolchainTests.cs `
        experiments/VtfUniversal.Tests/VtfCmdBlockEncoderTests.cs
git commit -m "feat: freeze the universal VTF encoder toolchain"
~~~

### Task 10: Reconstruir containers e promover authored mips

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfContainerWriter.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCandidateMaterializer.cs`
- Create: `experiments/VtfUniversal.Tests/VtfContainerWriterTests.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfAuthoredPromotionTests.cs`

**Interfaces:**
- `MaterializeAuthoredAsync(source,level,output,token)`.
- Retained mip/resource payloads byte-idênticos; offsets/header declarados podem mudar.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public async Task PromotesMipTwoAndCopiesTail()
{
    string src=VtfFixtureBuilder.WriteResource75WithMips(64,32,7);
    string dst=Temp.File("candidate.vtf");
    await UniversalVtfCandidateMaterializer.MaterializeAuthoredAsync(src,2,dst,default);
    var before=VtfContainerLayoutReader.Read(src);
    var after=VtfContainerLayoutReader.Read(dst);
    Assert.Equal((16,8,5),(after.Document.Width,after.Document.Height,after.Document.MipCount));
    FixtureAssert.RetainedSubresourcesEqual(before,2,after);
    FixtureAssert.OffsetResourcesEqual(before,after);
}

[Fact]
public async Task PreservesSevenFacesPerRetainedMip()
{
    string dst=await FixtureTransforms.PromoteCubemap(level:1);
    Assert.All(VtfContainerLayoutReader.Read(dst).Document.MipLevels,
        mip=>Assert.Equal(7,FixtureAssert.CountFaces(dst,mip.Level)));
}

[Fact]
public async Task RetainsDepthAtPromotedLevel()
{
    string dst=await FixtureTransforms.PromoteVolume(
        width:16,height:8,depth:8,level:2);
    Assert.Equal(2,VtfContainerLayoutReader.Read(dst).Document.Depth);
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "VtfContainerWriterTests|UniversalVtfAuthoredPromotionTests" -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar writer explícito**

Preservar entry order/flags, reflectivity, bump scale, frames/first frame/flags; realocar LRS/HRS/offset chunks; copiar unknown chunks; inline CRC `0x3C66AA52` somente em `materials/models/diggercars/mb_w123/w123_int_envmap.vtf`; qualquer outro inline tag bloqueia. Depth final é `max(1,sourceDepth>>level)`; cadeia authored incompleta ou dimensionalmente inconsistente bloqueia. Escrever sibling temp, parse/decode/audit e somente então mover para output inexistente.

- [ ] **Step 4: GREEN e determinismo**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "VtfContainerWriterTests|UniversalVtfAuthoredPromotionTests" -v minimal
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "VtfContainerWriterTests|UniversalVtfAuthoredPromotionTests" -v minimal
~~~

Expected: PASS e hashes idênticos.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfContainerWriter.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCandidateMaterializer.cs `
        experiments/VtfUniversal.Tests/VtfContainerWriterTests.cs `
        experiments/VtfUniversal.Tests/UniversalVtfAuthoredPromotionTests.cs
git commit -m "feat: promote authored VTF mip chains safely"
~~~

### Task 11: Gerar NOMIP e thumbnails pelas regras aprovadas

**Files:**
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCandidateMaterializer.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfContainerWriter.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfGeneratedCandidateTests.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfGeneratedCorpusSmokeTests.cs`

**Interfaces:**
- `MaterializeGeneratedAsync(source,level,route,output,token)`.
- NOMIP permanece `mip_count=1`/NOMIP; cada nível vem diretamente do raw mip 0.
- LRS ausente permanece ausente; LRS existente é copiado ou regenerado conforme dimensões finais.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public async Task GeneratedNomipRemainsSingleMip()
{
    string src=VtfFixtureBuilder.WriteNomip72(128,64,15);
    string dst=Temp.File("nomip.vtf");
    await FixtureTransforms.Generate(src,3,dst);
    var doc=VtfContainerLayoutReader.Read(dst).Document;
    Assert.Equal((16,8,1),(doc.Width,doc.Height,doc.MipCount));
    Assert.True((doc.Flags & VtfFlags.NoMip)!=0);
}

[Theory]
[InlineData(4,4,8,8,4,4,true)]
[InlineData(16,16,8,4,8,4,false)]
public async Task AppliesThumbnailRule(
    int lowW,int lowH,int finalW,int finalH,int expectedW,int expectedH,bool copied)
{
    var r=await FixtureTransforms.TransformThumbnail(lowW,lowH,finalW,finalH);
    Assert.Equal((expectedW,expectedH),(r.Width,r.Height));
    Assert.Equal(copied,r.PayloadWasCopied);
}

[Fact]
public async Task AbsentThumbnailRemainsAbsent()
{
    string src=VtfFixtureBuilder.WriteWithoutLowResolutionImage(64,64);
    string dst=Temp.File("no-lrs.vtf");
    await FixtureTransforms.Generate(src,2,dst);
    var doc=VtfContainerLayoutReader.Read(dst).Document;
    Assert.Equal((0,0),(doc.LowResWidth,doc.LowResHeight));
    Assert.DoesNotContain(doc.Resources,x=>x.Tag==VtfResourceTags.LowResImage);
}

[Fact]
public async Task EveryRawNomipGeneratesAllPhysicalLevelsWithoutSourceMutation()
{
    string raw=RequireEnvironment.Path("VTF_UNIVERSAL_RAW_ROOT");
    var before=UniversalVtfTreeInventory.Capture(raw);
    var entries=UniversalVtfInventoryScanner.Scan(raw).Entries
        .Where(x=>x.MipCount==1 && (x.Flags & VtfFlags.NoMip)!=0).ToArray();
    Assert.Equal(7,entries.Length);
    foreach(var entry in entries)
    foreach(int level in UniversalVtfLevels.Enumerate(entry.Width,entry.Height))
        await FixtureAssert.MaterializesParsesAndDecodes(raw,entry,level);
    Assert.Equal(before,UniversalVtfTreeInventory.Capture(raw));
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfGeneratedCandidateTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar geração/LRS**

Para cada frame/face/depth, decodificar raw mip 0, aplicar rota, codificar formato original e montar somente o nível. LRS ausente permanece ausente e nenhum tag/payload é inventado. Se LRS existente cabe nos dois eixos, copiar payload/dimensões/formato; senão gerar do top selecionado em `max(1,min(oldLow,final))` por eixo e formato LRS original. Falha encode/parse/decode bloqueia; nunca remover LRS existente.

- [ ] **Step 4: GREEN e smoke nos sete NOMIP**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfGeneratedCandidateTests -v minimal
$env:VTF_UNIVERSAL_RAW_ROOT="C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack"
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfGeneratedCorpusSmokeTests -v minimal
Remove-Item Env:\VTF_UNIVERSAL_RAW_ROOT
~~~

Expected: teste de integração enumera exatamente os sete arquivos NOMIP, prova todos os níveis geráveis, parse/decode válidos e raw unchanged; nenhum comando futuro do driver é chamado nesta task.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCandidateMaterializer.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/VtfContainerWriter.cs `
        experiments/VtfUniversal.Tests/UniversalVtfGeneratedCandidateTests.cs `
        experiments/VtfUniversal.Tests/UniversalVtfGeneratedCorpusSmokeTests.cs
git commit -m "feat: generate NOMIP and thumbnail candidates"
~~~

### Task 12: Reaplicar BC3→BC1 com bridge raw independente

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/Bc3ApprovedReceipt.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/Bc3RawBridgeStore.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/Bc3ColorBlockTranscoder.cs`
- Create: `experiments/VtfUniversal.Tests/Bc3ApprovedReceiptTests.cs`
- Create: `experiments/VtfUniversal.Tests/Bc3ColorBlockTranscoderTests.cs`
- Modify: `experiments/VtfUniversalCorpusDriver/Program.cs`

**Interfaces:**
- `Bc3ApprovedReceipt.Build(rawInventory,priorManifest,safeTree) -> Bc3RawBridge`.
- `Bc3RawBridgeStore.WriteCanonical(output,bridge) -> SHA-256`; `LoadAndVerify(path,expectedSha256)` falha antes da seleção se um byte divergir.
- `Bc3ColorBlockTranscoder.TranscodeBlock(ReadOnlySpan<byte>)`.
- CLI `bc3-bridge` grava mapping imutável e SHA.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Theory]
[MemberData(nameof(BcBlocks.BothEndpointOrders))]
public void TranscodePreservesDecodedRgb(byte[] bc3)
{
    byte[] bc1=Bc3ColorBlockTranscoder.TranscodeBlock(bc3);
    Assert.Equal(BcDecoder.RgbFromBc3(bc3),BcDecoder.RgbFromOpaqueBc1(bc1));
}

[Fact]
public void RejectsSafeHashAsRawAuthority() =>
    Assert.Throws<InvalidDataException>(() =>
        Bc3ApprovedReceipt.Build(Fixtures.MappingWithSafeHashInRawColumn));

[Fact]
public void CanonicalBridgeRequiresItsExplicitHash()
{
    var frozen=Bc3RawBridgeStore.WriteCanonical(Temp.File("bridge.json"),Fixtures.ValidBridge);
    File.AppendAllText(frozen.Path," ");
    Assert.Throws<InvalidDataException>(()=>
        Bc3RawBridgeStore.LoadAndVerify(frozen.Path,frozen.Sha256));
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "Bc3ApprovedReceiptTests|Bc3ColorBlockTranscoderTests" -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar transcoder/bridge**

Se `color0>color1`, copiar oito bytes de cor. Caso contrário, trocar endpoints e remapear índices `0↔1, 2↔3`, produzindo DXT1 format13 opaco RGB-exato.

Bridge exige prior manifest SHA `6eda827ee1fb1bddb85a1aabeea1a0181722947139bc4d58776a529c15d14b15`, 193 paths, partição 155/32/6, raw hashes/alpha level1+ reconciliados, consumer receipt unchanged e registro de zero equivalências color-block entre os 155 raw/safe anteriores. As seis exceções exatas são `lambo_countach/int_seat_occ.vtf`, `lambo_countach/skin.vtf`, `mazda_miata/skin.vtf` e Toyota Supra `skin.vtf`, `skin2.vtf`, `skin3.vtf`, todas sob `materials/models/diggercars/`. Serializar canonicamente em destination inexistente, calcular o SHA-256 dos bytes finais e emitir path+hash; esse hash é input obrigatório do fingerprint e do build, não é inferido silenciosamente depois.

- [ ] **Step 4: GREEN e bridge real**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "Bc3ApprovedReceiptTests|Bc3ColorBlockTranscoderTests" -v minimal
dotnet run --project .\experiments\VtfUniversalCorpusDriver\VtfUniversalCorpusDriver.csproj -c Release -- bc3-bridge `
  --raw-root "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack" `
  --safe-root "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\source\addons\lvs_cars_pack_CORPUS_BC3_TO_BC1_SAFE_FINAL_V1" `
  --prior-manifest "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\vtf_novel_v2\corpus_bc3_to_bc1_v1.manifest.json" `
  --output "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\bc3-raw-to-approved-v1.json"
~~~

Expected: 155/32/6, mapping hash, fontes unchanged.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/Bc3ApprovedReceipt.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/Bc3RawBridgeStore.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/Bc3ColorBlockTranscoder.cs `
        experiments/VtfUniversal.Tests/Bc3ApprovedReceiptTests.cs `
        experiments/VtfUniversal.Tests/Bc3ColorBlockTranscoderTests.cs `
        experiments/VtfUniversalCorpusDriver/Program.cs
git commit -m "feat: bridge approved BC3 paths to raw VTFs"
~~~

### Task 13: Construir attempt matrix e seletor monotônico

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfSelector.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfSelectorTests.cs`

**Interfaces:**
- `BuildAttempts(entry,candidates,semantics) -> UniversalVtfAttemptMatrix`.
- `Select(matrix,runtimeCaps) -> UniversalVtfProfileSelection`.
- `UniversalVtfRuntimeCaps.LoadCanonical(path,expectedSha256)` verifica schema/version/hash e expande um cap para o perfil reprovado e todos os perfis mais soltos.
- Level1 obrigatório; posteriores param no primeiro fail.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public void LevelOneDeficitDoesNotStopHigherAttempts()
{
    var matrix=AttemptFixtures.LevelOneFailsQualityLevelTwoPasses();
    var s=UniversalVtfSelector.Select(matrix,UniversalVtfRuntimeCaps.Empty);
    Assert.Equal(2,s.Conservative.Level);
    Assert.Contains(s.Attempts[UniversalVtfProfileKind.Conservative],
        x=>x.Level==1 && x.Reason=="forced_floor_quality_deficit");
    Assert.Contains(s.Attempts[UniversalVtfProfileKind.Conservative],
        x=>x.Level==2 && x.WasConsidered);
}

[Fact]
public void ProfilesAreNested()
{
    var s=UniversalVtfSelector.Select(AttemptFixtures.Nested(),UniversalVtfRuntimeCaps.Empty);
    Assert.True(s.Conservative.Level<=s.Balanced.Level);
    Assert.True(s.Balanced.Level<=s.Aggressive.Level);
}


[Fact]
public void BalancedRuntimeCapAlsoCapsAggressiveButNotConservative()
{
    var uncapped=AttemptFixtures.SelectedLevels(conservative:1,balanced:3,aggressive:4);
    var caps=UniversalVtfRuntimeCaps.FromVerifiedEntries(new[]{
        new UniversalVtfRuntimeCap("materials/a.vtf",
            UniversalVtfProfileKind.Balanced,2,"manual-gmod-regression")});
    var s=UniversalVtfSelector.Select(uncapped,caps);
    Assert.Equal((1,2,2),(s.Conservative.Level,s.Balanced.Level,s.Aggressive.Level));
}

[Fact]
public void CapBelowUniversalFloorIsRejected() =>
    Assert.Throws<InvalidDataException>(()=>
        UniversalVtfRuntimeCaps.FromVerifiedEntries(new[]{
            new UniversalVtfRuntimeCap("materials/a.vtf",
                UniversalVtfProfileKind.Conservative,0,"invalid")}));
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfSelectorTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar seleção**

~~~csharp
internal sealed record UniversalVtfAttempt(
    int Level,bool StructurallyValid,UniversalVtfMetricSet Metrics,
    string StructuralReason);
internal sealed record UniversalVtfAttemptMatrix(
    string RelativePath,IReadOnlyList<UniversalVtfAttempt> Attempts);
internal sealed record UniversalVtfProfileAttempt(
    int Level,bool WasConsidered,UniversalVtfEvaluation Evaluation,string Reason);
internal sealed record UniversalVtfSelectedLevel(int Level,string Reason);
internal sealed record UniversalVtfProfileSelection(
    UniversalVtfSelectedLevel Conservative,
    UniversalVtfSelectedLevel Balanced,
    UniversalVtfSelectedLevel Aggressive,
    IReadOnlyDictionary<UniversalVtfProfileKind,IReadOnlyList<UniversalVtfProfileAttempt>> Attempts);
internal sealed record UniversalVtfRuntimeCap(
    string RelativePath,UniversalVtfProfileKind Profile,int MaximumLevel,string Evidence);
internal sealed record UniversalVtfRuntimeCaps(IReadOnlyList<UniversalVtfRuntimeCap> Caps)
{
    internal static UniversalVtfRuntimeCaps Empty { get; } = new(Array.Empty<UniversalVtfRuntimeCap>());
    internal int MaxLevel(string path,UniversalVtfProfileKind profile) =>
        Caps.Where(x=>(int)x.Profile<=(int)profile &&
                      x.RelativePath.Equals(path,StringComparison.OrdinalIgnoreCase))
            .Select(x=>x.MaximumLevel).DefaultIfEmpty(int.MaxValue).Min();
}

foreach(var profile in Enum.GetValues<UniversalVtfProfileKind>())
{
    var floor=matrix.Attempts.Single(x=>x.Level==1);
    if(!floor.StructurallyValid)
        throw new InvalidDataException($"mandatory_level1_structural_failure:{floor.StructuralReason}");
    var floorEvaluation=UniversalVtfMetricCalculator.EvaluateWorstPlane(
        floor.Metrics,policy.GetThresholds(profile));
    profileAttempts[profile].Add(new UniversalVtfProfileAttempt(
        1,true,floorEvaluation,
        floorEvaluation.Passed ? "forced_floor_pass" : "forced_floor_quality_deficit"));
    int selected=1;
    foreach(var attempt in matrix.Attempts.Where(x=>x.Level>=2).OrderBy(x=>x.Level))
    {
        var evaluation=UniversalVtfMetricCalculator.EvaluateWorstPlane(
            attempt.Metrics,policy.GetThresholds(profile));
        profileAttempts[profile].Add(ToProfileAttempt(attempt,evaluation));
        if(!attempt.StructurallyValid || !evaluation.Passed) break;
        selected=attempt.Level;
    }
    result[profile]=Math.Min(selected,runtimeCaps.MaxLevel(matrix.RelativePath,profile));
}
if(!(result[Conservative]<=result[Balanced] && result[Balanced]<=result[Aggressive]))
    throw new InvalidOperationException("profile_nesting_violation");
~~~

`BuildAttempts` abre o container candidato já finalizado, decodifica seu top level para cada `(frame,face,depthSlice)`, reconstrói cada plano contra o raw mip 0 correspondente e produz um único metric set sem thresholds. Level 1 estruturalmente válido é sempre registrado/selecionado, mesmo com déficit de qualidade, e isso jamais impede avaliar level 2. A partir de level 2, cada perfil avalia consecutivamente o mesmo metric set até sua primeira reprovação. Rotas conflitantes: maior pior margem; se nenhuma passa level1, menor pior deficit; tie por flag sRGB, depois numeric-linear. Guardar todas as rotas/métricas/reasons. Caps usam JSON canônico versionado e hashado, `MaximumLevel>=1`, paths únicos/case-normalizados; um cap se aplica ao perfil indicado e a todos os perfis mais soltos. Após caps, recomputar e exigir nesting; conflito bloqueia em vez de reescrever resultado.

- [ ] **Step 4: Rodar GREEN**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfSelectorTests -v minimal
~~~

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfSelector.cs `
        experiments/VtfUniversal.Tests/UniversalVtfSelectorTests.cs
git commit -m "feat: select monotonic universal VTF levels"
~~~

### Task 14: Manifesto completo e contabilidade sem dupla contagem

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfManifestWriter.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfManifestWriterTests.cs`

**Interfaces:**
- `WriteAsync(path,buildReceipt,token)` em JSON canônico.
- Uma row por VTF, inclusive byte-floor/generated/blocked.
- Views raw→final, safe→final e VTF raw/safe/final.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public void SeparatesSavings()
{
    var r=ManifestFixtures.ResizedAndBc3ToBc1(1000,250,125);
    Assert.Equal(750,r.ResolutionSavedBytes);
    Assert.Equal(125,r.FormatSavedBytes);
    Assert.Equal(875,r.TotalSavedBytes);
}

[Fact]
public void DuplicatePathFails() =>
    Assert.Throws<InvalidDataException>(() =>
        UniversalVtfManifestWriter.Validate(ManifestFixtures.WithDuplicatePath()));

[Fact]
public void RecordsEveryDecisionInputHash()
{
    var m=ManifestFixtures.Valid;
    AssertSha256(m.PolicyFingerprint);
    AssertSha256(m.BcBridgeSha256);
    Assert.True(m.RuntimeCapsSha256 is null || IsSha256(m.RuntimeCapsSha256));
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfManifestWriterTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar schema**

Cada row: classes/consumer proof, raw/final dims/formats, authored/generated, nível por perfil, attempt matrix, pior gate/plano, hashes/bytes, floor/blocker, BC receipt e savings. Header: policy fingerprint, fixture/tool hashes, `BcBridgeSha256` obrigatório e `RuntimeCapsSha256` nulo ou obrigatório conforme o build. Aggregate: bytes/MB/MiB/%, níveis/classes/famílias e três views. Validar rowCount/path uniqueness, level floor, nesting, hashes de inputs e soma física.

- [ ] **Step 4: GREEN/round-trip**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfManifestWriterTests -v minimal
~~~

Expected: PASS e serialize→deserialize→serialize byte-idêntico.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfManifestWriter.cs `
        experiments/VtfUniversal.Tests/UniversalVtfManifestWriterTests.cs
git commit -m "feat: report universal VTF decisions and savings"
~~~

### Task 15: Compor e publicar três árvores transacionalmente

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusBuilder.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusAuditor.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfCorpusBuilderTests.cs`
- Create: `experiments/VtfUniversal.Tests/UniversalVtfCorpusAuditorTests.cs`

**Interfaces:**
- `BuildAsync(UniversalVtfBuildRequest,progress,token)`.
- Auditor `Clean=false` impede todos os renames finais.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public async Task UsesSafeNonVtfAndRawVtf()
{
    using var c=SyntheticCorpus.TwoAuthorities();
    var r=await Builder.BuildAsync(c.Request);
    Assert.Equal(c.ExpectedTransformedRawVtfHash,Hash(r.Conservative.Path("materials/a.vtf")));
    Assert.Equal(c.SafeModelHash,Hash(r.Conservative.Path("models/a.mdl")));
}

[Fact]
public async Task MutationPublishesNothing()
{
    using var c=SyntheticCorpus.MutateDuringBuild();
    await Assert.ThrowsAsync<InvalidDataException>(()=>Builder.BuildAsync(c.Request));
    Assert.All(c.FinalDestinations,p=>Assert.False(Directory.Exists(p)));
    Assert.All(c.SourceSnapshots,s=>s.AssertUnchanged());
}


[Theory]
[InlineData("bridge")]
[InlineData("runtime-caps")]
public async Task WrongDeclaredInputHashPublishesNothing(string input)
{
    using var c=SyntheticCorpus.WithWrongDeclaredHash(input);
    await Assert.ThrowsAsync<InvalidDataException>(()=>Builder.BuildAsync(c.Request));
    Assert.All(c.FinalDestinations,p=>Assert.False(Directory.Exists(p)));
}

[Fact]
public async Task VerifiedRuntimeCapsAreAppliedAndRecorded()
{
    using var c=SyntheticCorpus.WithBalancedCap(level:2);
    var r=await Builder.BuildAsync(c.Request);
    var balanced=UniversalVtfManifestStore.Load(r.Balanced.ManifestPath);
    var aggressive=UniversalVtfManifestStore.Load(r.Aggressive.ManifestPath);
    Assert.Equal(2,balanced.Row("materials/a.vtf").SelectedLevel);
    Assert.Equal(2,aggressive.Row("materials/a.vtf").SelectedLevel);
    Assert.Equal(c.RuntimeCapsSha256,balanced.RuntimeCapsSha256);
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "UniversalVtfCorpusBuilderTests|UniversalVtfCorpusAuditorTests" -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar transação**

~~~csharp
internal sealed record UniversalVtfBuildRequest(
    string RawRoot,string SafeRoot,string OutputParent,string ReportRoot,
    string BcBridgePath,string BcBridgeSha256,
    string? RuntimeCapsPath,string? RuntimeCapsSha256,
    IReadOnlyList<UniversalVtfProfileKind> Profiles,
    string PolicyFingerprint);
internal sealed record UniversalVtfBuiltProfile(
    UniversalVtfProfileKind Profile,string Root,string ManifestPath,
    long Bytes,string TreeSha256);
internal sealed record UniversalVtfBuildResult(
    UniversalVtfBuiltProfile Conservative,
    UniversalVtfBuiltProfile Balanced,
    UniversalVtfBuiltProfile Aggressive);
~~~

1. Verify anchors; exigir `BcBridgePath`+`BcBridgeSha256`; exigir runtime caps path+hash ambos presentes ou ambos ausentes; recomputar todos os hashes antes de decidir.
2. Criar staging siblings/destinations inexistentes.
3. Copiar safe fisicamente, sem hardlinks.
4. Substituir somente os VTF paths declarados por raw-derived.
5. Carregar bridge por `LoadAndVerify`, recomputar o policy fingerprint com seu SHA e só então reaplicar BC rule; carregar caps por hash/schema/version e aplicá-los seletivamente.
6. Escrever manifests fora das árvores.
7. Recapturar fontes e exigir unchanged.
8. Auditar paths/container/resources/non-VTF/accounting/determinismo.
9. Renomear todos os staging somente se as três auditorias CLEAN; em falha preservar recovery e publicar zero finals.

- [ ] **Step 4: GREEN com fault injection**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "UniversalVtfCorpusBuilderTests|UniversalVtfCorpusAuditorTests" -v minimal
~~~

Expected: PASS para access denied, collision, mutation, cancellation, invalid output e audit rejection.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusBuilder.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusAuditor.cs `
        experiments/VtfUniversal.Tests/UniversalVtfCorpusBuilderTests.cs `
        experiments/VtfUniversal.Tests/UniversalVtfCorpusAuditorTests.cs
git commit -m "feat: build universal VTF corpora transactionally"
~~~

### Task 16: Auditor Python independente e fixture parity

**Files:**
- Create: `scripts/audit_universal_vtf_v1.py`
- Create: `tests/test_audit_universal_vtf_v1.py`
- Modify: `experiments/VtfUniversal.Tests/UniversalVtfMetricCalculatorTests.cs`

**Interfaces:**
- `python scripts/audit_universal_vtf_v1.py fixtures <json>`.
- `python scripts/audit_universal_vtf_v1.py corpus --manifest <json> --candidate <dir>`.
- Não importa assemblies C# nem aceita métricas C# como expected.

- [ ] **Step 1: Escrever RED**

~~~python
def test_tree_hash(tmp_path):
    (tmp_path/"a.txt").write_bytes(b"A")
    (tmp_path/"sub").mkdir()
    (tmp_path/"sub"/"b.bin").write_bytes(bytes([0,255]))
    assert audit.tree_inventory(tmp_path)["tree_sha256"] == (
        "75e21d8d75cff49a5579a92b2f881167a5cb57ee7fa08ba2602ee84458b424eb"
    )

def test_missing_manifest_file_fails(tmp_path):
    with pytest.raises(audit.AuditError,match="missing"):
        audit.audit_corpus(fixture_manifest("materials/missing.vtf"),tmp_path)
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
python -m pytest .\tests\test_audit_universal_vtf_v1.py -q
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar auditor standard-library**

Implementar tree hash, parser/ranges VTF 7.0–7.5, percentis/coverage/IoU/normal/SSIM reflect-101/route margins para fixtures, 1.152 rows, paths/hashes/bytes/dims/mips/resources, nesting/floor, receipt 193, format13/never20, non-VTF identity e accounting. Recalcular `BcBridgeSha256`, `RuntimeCapsSha256` quando presente e policy fingerprint; divergência de qualquer input falha. Para cada bloco BC3 convertido no corpus, decodificar RGB antes/depois e exigir igualdade texel a texel. FFT/CIEDE2000 das fixtures têm implementação matemática própria; NaN/path/reparse/source-authority incorreta falham.

- [ ] **Step 4: Rodar GREEN/parity**

~~~powershell
python -m pytest .\tests\test_audit_universal_vtf_v1.py -q
python .\scripts\audit_universal_vtf_v1.py fixtures `
 .\GmodAddonCompressor-master\GmodAddonCompressor\Resources\universal-vtf-v1-fixtures.json
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter UniversalVtfMetricCalculatorTests -v minimal
~~~

Expected: Python PASS/CLEAN; C# PASS; <=1e-12.

- [ ] **Step 5: Commit**

~~~powershell
git add scripts/audit_universal_vtf_v1.py tests/test_audit_universal_vtf_v1.py `
        experiments/VtfUniversal.Tests/UniversalVtfMetricCalculatorTests.cs
git commit -m "test: independently audit universal VTF policy"
~~~

### Task 17: Completar driver sem duplicar decisões

**Files:**
- Modify: `experiments/VtfUniversalCorpusDriver/Program.cs`
- Create: `experiments/VtfUniversal.Tests/VtfUniversalCorpusDriverTests.cs`
- Create: `docs/universal-vtf-v1.md`

**Interfaces:**
- Commands `toolchain`, `inventory`, `bc3-bridge`, `generated-smoke`, `build`, `audit`.
- `build --profiles all` chama uma única produção.
- `build` exige `--bc-bridge` e `--bc-bridge-sha256`; `--runtime-caps` e `--runtime-caps-sha256` são opcionais somente como par.
- Exit: 0 CLEAN, 2 args, 3 anchor/census, 4 transform, 5 audit, 130 cancel.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public async Task RejectsSameAuthorityAndDestination()
{
    int code=await Driver.RunAsync(new[]{"build","--raw-root","same",
        "--safe-root","same","--output-parent","same","--profiles","all"});
    Assert.Equal(2,code);
}

[Fact]
public async Task DelegatesOneRequest()
{
    var fake=new RecordingCorpusBuilder();
    int code=await Driver.RunAsync(DriverFixtures.ValidArgs,fake);
    Assert.Equal(0,code);
    Assert.Single(fake.Requests);
    Assert.Equal(3,fake.Requests[0].Profiles.Count);
}

[Theory]
[InlineData("--bc-bridge-sha256")]
[InlineData("--runtime-caps-sha256")]
public async Task RejectsPathWithoutItsHash(string missingHash)
{
    string[] args=DriverFixtures.ArgsMissing(missingHash);
    Assert.Equal(2,await Driver.RunAsync(args));
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter VtfUniversalCorpusDriverTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar CLI/documentação**

Program apenas parseia, valida pares path/hash, chama `UniversalVtfToolchain.Verify`, calcula `UniversalVtfPolicy.V1.ComputeFingerprint(toolchain,declaredBridgeSha256)`, cria `UniversalVtfBuildRequest` e transmite JSONL progress; o builder recalcula tudo antes de publicar. Não contém threshold/classifier/resize. `toolchain` e `generated-smoke` apenas chamam os serviços de produção já implementados nas Tasks 9 e 11. Documento registra anchors, comandos, schema, exit codes, work paths, cancelamento, determinismo, runtime caps e rollback.

- [ ] **Step 4: GREEN/help**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter VtfUniversalCorpusDriverTests -v minimal
dotnet run --project .\experiments\VtfUniversalCorpusDriver\VtfUniversalCorpusDriver.csproj -c Release -- --help
~~~

Expected: PASS e help completo.

- [ ] **Step 5: Commit**

~~~powershell
git add experiments/VtfUniversalCorpusDriver/Program.cs `
        experiments/VtfUniversal.Tests/VtfUniversalCorpusDriverTests.cs `
        docs/universal-vtf-v1.md
git commit -m "feat: expose the universal VTF corpus driver"
~~~

### Task 18: Integrar Maximum e retirar dependência produtiva do lab antigo

**Files:**
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Objects/MaximumVTFEdit.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/CompressAddonSystem.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Models/CompressPipelineOptions.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/GmodAddonCompressor.csproj`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Interfaces/ICompressBatch.cs`
- Create: `experiments/VtfUniversal.Tests/MaximumVtfIntegrationTests.cs`

**Interfaces:**
- `ICompressBatch.CompressBatchAsync(IReadOnlyList<FileInfo>,CancellationToken)`.
- Maximum não chama `VTFEdit` nem `VtfMaximumExperimentRunner`.
- `MaximumVtfProfile` default Balanced.

- [ ] **Step 1: Escrever RED**

~~~csharp
[Fact]
public async Task MaximumRunsOneBatchWithoutPerFileTimeout()
{
    var service=new RecordingBatchService();
    await CompressHarness.RunVtfBucket(service,12);
    Assert.Single(service.Calls);
    Assert.Equal(12,service.Calls[0].Count);
}

[Fact]
public void ProductionHasNoLegacyLabReference()
{
    string project=File.ReadAllText(RepoPaths.WpfProject);
    string facade=File.ReadAllText(RepoPaths.MaximumVtfEdit);
    Assert.DoesNotContain("VtfMaximumLab",project,StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("VtfMaximumLab",facade,StringComparison.OrdinalIgnoreCase);
}
~~~

- [ ] **Step 2: Rodar RED**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter MaximumVtfIntegrationTests -v minimal
~~~

Expected: FAIL.

- [ ] **Step 3: Implementar integração**

CompressAddonSystem entrega bucket inteiro a `ICompressBatch`; Standard/Magick mantêm loop antigo. Maximum cria snapshot externo antes de replace, executa profile, audita e só então substitui VTFs; report fica em `ToolPaths.WorkRoot`. Remover `Task.WhenAny(Delay 2min)` somente do batch. Atualizar routing summary. Depois de eliminar `using VtfMaximumLab.*`, remover wildcard links do WPF csproj; não restaurar o lab deletado.

~~~csharp
internal interface ICompressBatch
{
    Task CompressBatchAsync(
        IReadOnlyList<FileInfo> files,
        CancellationToken cancellationToken);
}
~~~

- [ ] **Step 4: GREEN completo/build**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release -v minimal
dotnet build .\GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj -c Release -r win-x64
rg -n "VtfMaximumLab" .\GmodAddonCompressor-master\GmodAddonCompressor `
  --glob '!bin/**' --glob '!obj/**'
if($LASTEXITCODE-eq 0){throw "Legacy lab source link/reference remains in production"}
if($LASTEXITCODE-gt 1){throw "rg failed while checking legacy references"}
~~~

Expected: PASS/build0; Standard/Magick unchanged; WPF independente dos 50 deletados.

- [ ] **Step 5: Commit**

~~~powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/Objects/MaximumVTFEdit.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Systems/CompressAddonSystem.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/Models/CompressPipelineOptions.cs `
        GmodAddonCompressor-master/GmodAddonCompressor/GmodAddonCompressor.csproj `
        GmodAddonCompressor-master/GmodAddonCompressor/Interfaces/ICompressBatch.cs `
        experiments/VtfUniversal.Tests/MaximumVtfIntegrationTests.cs
git commit -m "feat: route Maximum through universal VTF batch"
~~~

### Task 19: Construir e auditar três candidatos reais

**Files:**
- Runtime create: `_runtime_validation/universal_vtf_v1/<run-id>/`, fora do Git.
- Create: `docs/superpowers/results/2026-08-28-universal-vtf-v1-offline-results.md`

**Interfaces:**
- Produces três árvores/manifests, relatório agregado, JSONL e recovery.
- Não promove baseline.

- [ ] **Step 1: Suite fresca**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release -v minimal
python -m pytest .\tests\test_audit_universal_vtf_v1.py -q
dotnet build .\GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj -c Release -r win-x64
~~~

Expected: zero failures.

- [ ] **Step 2: Build em destination nova**

~~~powershell
$runRoot="C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\UNIVERSAL_VTF_V1_OFFLINE_CANDIDATES"
if(Test-Path -LiteralPath $runRoot){throw "Destination exists: $runRoot"}
$bridge="C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\bc3-raw-to-approved-v1.json"
$bridgeSha=(Get-FileHash -LiteralPath $bridge -Algorithm SHA256).Hash.ToLowerInvariant()

dotnet run --project .\experiments\VtfUniversalCorpusDriver\VtfUniversalCorpusDriver.csproj -c Release -- build `
 --raw-root "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack" `
 --safe-root "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\source\addons\lvs_cars_pack_CORPUS_BC3_TO_BC1_SAFE_FINAL_V1" `
 --bc-bridge $bridge --bc-bridge-sha256 $bridgeSha `
 --output-parent $runRoot --profiles all
~~~

Expected: três finais somente após CLEAN; rows=1152; level0=0; unsupported reducible=0.

- [ ] **Step 3: Auditor independente**

~~~powershell
Get-ChildItem -LiteralPath $runRoot -Filter "*.manifest.json" | ForEach-Object {
  python .\scripts\audit_universal_vtf_v1.py corpus --manifest $_.FullName `
    --candidate (Join-Path $runRoot $_.BaseName.Replace(".manifest",""))
  if($LASTEXITCODE-ne 0){throw "Audit failed: $($_.Name)"}
}
~~~

Expected: três CLEAN, non-VTF identical, nesting/accounting reconciliados.

- [ ] **Step 4: Repetição determinística**

Reexecutar em parent `_REPEAT`; comparar decision matrix, policy/fixture/tool hashes, cada VTF SHA, tree SHA e bytes. Timestamp/path absoluto ficam em envelope não-hashado.

- [ ] **Step 5: Registrar números**

Results doc inclui raw/safe/final bytes, bytes/MB/MiB/%, níveis 1/2/3/4+, classes, authored/generated, receipt193, failures, top savings, tree/manifest/policy hashes, paths e status `OFFLINE_CLEAN_MANUAL_GMOD_PENDING`.

- [ ] **Step 6: Commit apenas do relatório**

~~~powershell
git add docs/superpowers/results/2026-08-28-universal-vtf-v1-offline-results.md
git commit -m "docs: record universal VTF offline candidates"
~~~

### Task 20: Verificação final e handoff manual sem promoção

**Files:**
- Modify only with evidence: `docs/superpowers/results/2026-08-28-universal-vtf-v1-offline-results.md`
- Candidate trees remain immutable.

**Interfaces:**
- Produces amostra estratificada e rollback.
- Regressão cria runtime cap versionado por path/profile; não relaxa threshold.

- [ ] **Step 1: Verificação fresca**

~~~powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release -v minimal
python -m pytest .\tests -q
dotnet build .\GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj -c Release -r win-x64
git status --short
~~~

Expected: testes/build0; candidatos não staged.

- [ ] **Step 2: Gerar amostra do manifesto**

Cobrir cada classe, nível, sete NOMIP, regenerated thumbnails, maior saving/família, shared, vidro, interior, lights/gauges/wheels, skins, LOD e W123 cubemap/CRC. A amostra lê manifesto; não reclassifica.

- [ ] **Step 3: Entregar sem promover**

Informar candidato instalado, paths/hashes/tamanhos/economia, checklist close/medium/far/skins/interior/glass/lights/gauges/wheels/LOD e rollback para `CORPUS_BC3_TO_BC1_SAFE_FINAL_V1`.

- [ ] **Step 4: Caps somente após confirmação**

Para path reprovado, criar `runtime-caps-v1.json` canônico; calcular SHA-256 e passar ambos explicitamente no rebuild:

~~~powershell
$rawRoot="C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack"
$safeRoot="C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\source\addons\lvs_cars_pack_CORPUS_BC3_TO_BC1_SAFE_FINAL_V1"
$bridge="C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\bc3-raw-to-approved-v1.json"
$bridgeSha=(Get-FileHash -LiteralPath $bridge -Algorithm SHA256).Hash.ToLowerInvariant()
$caps="C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\runtime-caps-v1.json"
$capsSha=(Get-FileHash -LiteralPath $caps -Algorithm SHA256).Hash.ToLowerInvariant()
$cappedRunRoot="C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\UNIVERSAL_VTF_V1_CAPPED_CANDIDATES"
if(Test-Path -LiteralPath $cappedRunRoot){throw "Destination exists: $cappedRunRoot"}
dotnet run --project .\experiments\VtfUniversalCorpusDriver\VtfUniversalCorpusDriver.csproj -c Release -- build `
  --raw-root $rawRoot --safe-root $safeRoot `
  --bc-bridge $bridge --bc-bridge-sha256 $bridgeSha `
  --runtime-caps $caps --runtime-caps-sha256 $capsSha `
  --output-parent $cappedRunRoot --profiles all
~~~

Propagar aos perfis mais soltos, recomputar os três manifests e retestar os afetados. O novo manifesto precisa registrar exatamente `$capsSha`. Se exigir level0, parar para decisão explícita do usuário.

- [ ] **Step 5: Commit da evidência, nunca promoção**

~~~powershell
git add docs/superpowers/results/2026-08-28-universal-vtf-v1-offline-results.md
git commit -m "docs: record universal VTF manual validation handoff"
~~~

Milestone termina `MANUAL_GMOD_PENDING` ou com caps confirmados e reauditoria CLEAN. Promoção é decisão separada do usuário.
