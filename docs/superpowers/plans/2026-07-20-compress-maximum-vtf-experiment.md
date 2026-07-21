# Compress Maximum VTF Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir um laboratório reproduzível que selecione 50 VTFs, reproduza o Compress atual em 2×/8×8, teste candidatos Maximum em resolução original/2×/4× e prove ou rejeite ganho mínimo de 10% com gates estruturais e visuais.

**Architecture:** Um console .NET 6 Windows referencia o WPF existente para reutilizar parser, planner semântico e pipeline atual. Componentes experimentais isolam inventário, amostragem, preparação, encoders, montagem VTF, métricas, seleção e relatórios; nenhuma alteração de UI ocorre nesta etapa.

**Tech Stack:** C# 10, .NET 6 Windows, Magick.NET, xUnit, VTFCmd embutido, DirectXTex/texconv, AMD Compressonator, FLIP de referência e PowerShell.

## Global Constraints

- Trabalhar no WPF principal e backend compartilhado; não modificar `gui/`.
- Não alterar o Maximum de Models.
- Preservar mudanças locais não relacionadas e excluir lixo, amostras e caches do commit.
- Nunca escrever no addon `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack`.
- Baseline: Standard, somente VTF, redução 2×, largura mínima 8, altura mínima 8 e proporção preservada.
- Maximum sempre avalia resolução original, 2× e 4× quando a estrutura permite.
- Nenhum candidato vence apenas por ser menor; todos os gates da especificação são obrigatórios.
- Preservar exatamente a versão VTF original. Não promover 7.1–7.4 para 7.5; candidatos 7.5 somente para originais 7.5 e após prova de carregamento/renderização no Garry's Mod instalado.
- Nenhuma integração WPF antes de Maximum ficar pelo menos 10% menor que `current-2x-8x8`.
- Não fazer commits intermediários. Commit e push somente depois da prova, integração e validação final.
- Executar sozinho, sem subagentes.

---

### Task 1: Infraestrutura de teste e acesso controlado aos internals

**Files:**
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/AssemblyInfo.cs`
- Create: `experiments/VtfMaximumLab/VtfMaximumLab.csproj`
- Create: `experiments/VtfMaximumLab/Program.cs`
- Create: `experiments/VtfMaximumLab.Tests/VtfMaximumLab.Tests.csproj`
- Create: `experiments/VtfMaximumLab.Tests/SmokeTests.cs`

**Interfaces:**
- Consumes: `GmodAddonCompressor.csproj`.
- Produces: executável `VtfMaximumLab` e suite xUnit com friend assemblies nomeadas.

- [ ] **Step 1: Escrever o teste RED de acesso ao parser atual**

```csharp
using GmodAddonCompressor.Systems.Vtf;

namespace VtfMaximumLab.Tests;

public sealed class SmokeTests
{
    [Fact]
    public void ExistingPlannerRejectsMissingVtf()
    {
        Assert.False(AddonVtfCompressionPlanner.TryReadMetadata("missing.vtf", out _));
    }
}
```

- [ ] **Step 2: Criar os csproj e confirmar falha por internal inacessível**

`VtfMaximumLab.Tests.csproj` referencia `Microsoft.NET.Test.Sdk` 17.8.0, `xunit` 2.6.6, `xunit.runner.visualstudio` 2.5.6 e projetos WPF/lab. Ambos novos projetos usam `net6.0-windows`.

Run:

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj -v minimal
```

Expected: FAIL com `AddonVtfCompressionPlanner is inaccessible due to its protection level`.

- [ ] **Step 3: Liberar somente as duas friend assemblies**

Adicionar a `AssemblyInfo.cs`:

```csharp
using System.Runtime.CompilerServices;

[assembly: InternalsVisibleTo("VtfMaximumLab")]
[assembly: InternalsVisibleTo("VtfMaximumLab.Tests")]
```

`Program.cs` aceita `--help` com exit code 0; qualquer comando desconhecido retorna 2 com mensagem de uso.

- [ ] **Step 4: Confirmar GREEN e build do laboratório**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj -v minimal
dotnet build .\experiments\VtfMaximumLab\VtfMaximumLab.csproj -c Release
```

Expected: 1 teste aprovado; ambos comandos com exit code 0.

### Task 2: Documento VTF estrutural e fixtures sintéticas

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Models/VtfDocumentInfo.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/VtfDocumentReader.cs`
- Create: `experiments/VtfMaximumLab.Tests/VtfFixtureBuilder.cs`
- Create: `experiments/VtfMaximumLab.Tests/VtfDocumentReaderTests.cs`

**Interfaces:**
- Produces: `VtfDocumentReader.TryRead(string, out VtfDocumentInfo, out string)`.
- `VtfDocumentInfo` contém versão, header, dimensões, flags, frames, faces, depth, formatos, mip count, thumbnail, recursos e intervalos por mip.

- [ ] **Step 1: Escrever testes RED para VTF 7.2, 7.5 e truncamento**

```csharp
[Fact]
public void ReadsLegacy72MipLayout()
{
    string path = VtfFixtureBuilder.WriteLegacy72(8, 8, 13, 4);
    Assert.True(VtfDocumentReader.TryRead(path, out var doc, out var error), error);
    Assert.Equal(4, doc.MipLevels.Count);
    Assert.Equal((8, 8), (doc.MipLevels[0].Width, doc.MipLevels[0].Height));
}

[Fact]
public void Reads75ResourceOffsetsWithoutAssumingAdjacentPayload()
{
    string path = VtfFixtureBuilder.WriteResource75(8, 8, 15, 4);
    Assert.True(VtfDocumentReader.TryRead(path, out var doc, out var error), error);
    Assert.True(doc.HighResDataOffset >= doc.HeaderSize);
}

[Fact]
public void RejectsTruncatedTopMip()
{
    string path = VtfFixtureBuilder.WriteTruncated();
    Assert.False(VtfDocumentReader.TryRead(path, out _, out var error));
    Assert.Contains("truncated", error, StringComparison.OrdinalIgnoreCase);
}
```

- [ ] **Step 2: Rodar RED**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter VtfDocumentReaderTests -v minimal
```

Expected: FAIL porque reader/modelos não existem.

- [ ] **Step 3: Implementar parser com aritmética checked**

```csharp
internal sealed record VtfMipLevelInfo(int Level, int Width, int Height, long Offset, int ByteCount);
internal sealed record VtfResourceInfo(int Tag, int Flags, int Data);
internal sealed record VtfDocumentInfo(
    int MajorVersion, int MinorVersion, int HeaderSize, int Width, int Height,
    int Flags, int Frames, int Faces, int Depth, int HighResFormat, int MipCount,
    int LowResFormat, int LowResWidth, int LowResHeight, long HighResDataOffset,
    IReadOnlyList<VtfMipLevelInfo> MipLevels, IReadOnlyList<VtfResourceInfo> Resources,
    long FileLength);
```

O parser calcula faces para `ENVMAP`, trata a face esférica adicional antiga, respeita resource dictionary em 7.3+ e rejeita overlap, overflow e EOF.

- [ ] **Step 4: Rodar GREEN e build WPF**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter VtfDocumentReaderTests -v minimal
dotnet build .\GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj -c Release
```

Expected: testes aprovados e build exit code 0.

### Task 3: Perfil semântico exposto sem duplicar o planner

**Files:**
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/AddonVtfCompressionPlanner.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Models/VtfSemanticProfile.cs`
- Create: `experiments/VtfMaximumLab.Tests/VtfSemanticProfileTests.cs`

**Interfaces:**
- Produces: `AddonVtfCompressionAnalysis.GetSemanticProfile(string?, string?)`.
- `VtfSemanticProfile` expõe usos de alpha, normal, cutout, translucência, vidro, emissivo, decal, efeito, alpha-test reference e evidências.

- [ ] **Step 1: Escrever RED com VMTs mínimos**

```csharp
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
}
```

- [ ] **Step 2: Rodar RED**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter VtfSemanticProfileTests -v minimal
```

Expected: FAIL porque a API não existe.

- [ ] **Step 3: Estender a análise atual**

Reutilizar `AddonVtfTextureSignals`; adicionar captura de `$selfillum`, `$decal`, `$alphatestreference` e evidências VMT/Lua/PCF. Não criar segundo parser KeyValues.

- [ ] **Step 4: Rodar GREEN completo**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj -v minimal
```

Expected: todos aprovados.

### Task 4: Inventário, seleção determinística e cópia isolada

**Files:**
- Create: `experiments/VtfMaximumLab/Inventory/VtfInventoryEntry.cs`
- Create: `experiments/VtfMaximumLab/Inventory/VtfInventoryScanner.cs`
- Create: `experiments/VtfMaximumLab/Sampling/VtfSampleManifest.cs`
- Create: `experiments/VtfMaximumLab/Sampling/VtfSampleSelector.cs`
- Create: `experiments/VtfMaximumLab/Sampling/VtfSampleTreeCopier.cs`
- Create: `experiments/VtfMaximumLab.Tests/VtfSampleSelectorTests.cs`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `VtfInventoryScanner.Scan`, `VtfSampleSelector.Select`, `VtfSampleTreeCopier.Copy`.
- Output: manifesto SHA-256 e árvores `original`, `current-2x-8x8`, `maximum`.

- [ ] **Step 1: Escrever RED para estabilidade e cobertura**

```csharp
[Fact]
public void SelectionIsStableAcrossInputOrderAndCoversRareTags()
{
    var entries = FixtureInventory.Mixed(80);
    var first = VtfSampleSelector.Select(entries, 50, VtfSampleSelector.DefaultSeed);
    var second = VtfSampleSelector.Select(entries.Reverse().ToArray(), 50, VtfSampleSelector.DefaultSeed);
    Assert.Equal(first.Items.Select(x => x.RelativePath), second.Items.Select(x => x.RelativePath));
    Assert.Contains(first.Items, x => x.Tags.Contains("normal"));
    Assert.Contains(first.Items, x => x.Tags.Contains("cutout"));
    Assert.Equal(50, first.Items.Count);
}
```

- [ ] **Step 2: Rodar RED**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter VtfSampleSelectorTests -v minimal
```

Expected: FAIL por tipos ausentes.

- [ ] **Step 3: Implementar scanner, set-cover e copiador**

Normalizar caminhos com `/`, ordenar pelo SHA-256 de `seed + "\n" + relativePath`, copiar VTF/VMT/referências e verificar hash. O copiador recusa destino dentro do original.

Adicionar a `.gitignore`:

```gitignore
/experiments/_work/
/experiments/_tools/
```

- [ ] **Step 4: Rodar GREEN e gerar a amostra**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter VtfSampleSelectorTests -v minimal
dotnet run --project .\experiments\VtfMaximumLab\VtfMaximumLab.csproj -- sample --addon "C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack" --out ".\experiments\_work\lvs-cars-vtf-maximum"
```

Expected: 50 itens, três árvores, manifesto e hashes do addon original inalterados.

### Task 5: Reproduzir o baseline atual com telemetria

**Files:**
- Create: `experiments/VtfMaximumLab/Baseline/CurrentCompressBaselineRunner.cs`
- Create: `experiments/VtfMaximumLab/Reporting/VtfRunRecord.cs`
- Create: `experiments/VtfMaximumLab.Tests/CurrentCompressBaselineRunnerTests.cs`
- Modify: `experiments/VtfMaximumLab/Program.cs`

**Interfaces:**
- Produces: `CurrentCompressBaselineRunner.RunAsync(string, CancellationToken)` e `current-results.json`.
- Consumes: `CompressAddonSystem`, `ImageContext` e árvore current.

- [ ] **Step 1: Escrever RED para configuração exata**

```csharp
[Fact]
public async Task ConfiguresTwoXWithEightPixelFloorsAndWaits()
{
    var fake = new RecordingCurrentCompressor();
    var runner = new CurrentCompressBaselineRunner(fake);
    await runner.RunAsync("tree", CancellationToken.None);
    Assert.Equal(2, fake.ResolutionFactor);
    Assert.Equal(8, fake.MinimumWidth);
    Assert.Equal(8, fake.MinimumHeight);
    Assert.True(fake.KeepAspectRatio);
}
```

- [ ] **Step 2: Rodar RED**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter CurrentCompressBaselineRunnerTests -v minimal
```

Expected: FAIL por runner ausente.

- [ ] **Step 3: Implementar runner e registros**

Configurar `ImageContext.Resolution=2`, `TaargetWidth=8`, `TargetHeight=8`, skips zero, `ReduceExactlyToLimits=false`, `KeepImageAspectRatio=true`; incluir somente VTF; aguardar completion; medir `Stopwatch`; ler metadata/tamanho antes e depois.

- [ ] **Step 4: Rodar GREEN e baseline real**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter CurrentCompressBaselineRunnerTests -v minimal
dotnet run --project .\experiments\VtfMaximumLab\VtfMaximumLab.csproj -- baseline --root ".\experiments\_work\lvs-cars-vtf-maximum"
```

Expected: 50 registros completos e 50 VTFs reabertos.

### Task 6: Matriz semântica e preparação de imagens

**Files:**
- Create: `experiments/VtfMaximumLab/Candidates/VtfCandidateSpec.cs`
- Create: `experiments/VtfMaximumLab/Candidates/VtfCandidateMatrixBuilder.cs`
- Create: `experiments/VtfMaximumLab/Images/MaximumImagePreprocessor.cs`
- Create: `experiments/VtfMaximumLab.Tests/VtfCandidateMatrixBuilderTests.cs`
- Create: `experiments/VtfMaximumLab.Tests/MaximumImagePreprocessorTests.cs`

**Interfaces:**
- Produces: specs com escala, dimensões, formato, encoder, alpha mode e mip policy; PNGs preparados.

- [ ] **Step 1: Escrever RED para formatos e escalas**

```csharp
[Fact]
public void GradualAlphaNeverOffersDxt1AndOffersThreeScales()
{
    var entry = FixtureInventory.GradualAlpha(1024, 512);
    var specs = VtfCandidateMatrixBuilder.Build(entry, new[] { "vtfcmd" });
    Assert.All(specs, x => Assert.Equal(VtfTargetFormat.Dxt5, x.Format));
    Assert.Equal(new[] { 1, 2, 4 }, specs.Select(x => x.ScaleDivisor).Distinct());
}
```

- [ ] **Step 2: Rodar RED**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter "VtfCandidateMatrixBuilderTests|MaximumImagePreprocessorTests" -v minimal
```

Expected: FAIL por tipos ausentes.

- [ ] **Step 3: Implementar matriz e filtros**

Usar RGB linear, alpha associate/disassociate, LanczosSharp, cobertura cutout por busca binária multiplicativa e normal filtering vetorial com renormalização. Alpha-máscara fica separado.

- [ ] **Step 4: Rodar GREEN**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter "VtfCandidateMatrixBuilderTests|MaximumImagePreprocessorTests" -v minimal
```

Expected: testes de opaca, vidro, cutout e normal aprovados.

### Task 7: Encoders, DDS e transplante seguro de payload BC

**Files:**
- Create: `experiments/VtfMaximumLab/Encoding/IVtfCandidateEncoder.cs`
- Create: `experiments/VtfMaximumLab/Encoding/VtfCmdCandidateEncoder.cs`
- Create: `experiments/VtfMaximumLab/Encoding/DdsBcReader.cs`
- Create: `experiments/VtfMaximumLab/Encoding/DdsCliCandidateEncoder.cs`
- Create: `experiments/VtfMaximumLab/Encoding/VtfBcPayloadTransplanter.cs`
- Create: `experiments/VtfMaximumLab/Tools/ExternalToolManifest.cs`
- Create: `experiments/VtfMaximumLab.Tests/DdsBcReaderTests.cs`
- Create: `experiments/VtfMaximumLab.Tests/VtfBcPayloadTransplanterTests.cs`
- Create: `scripts/setup_vtf_maximum_tools.ps1`

**Interfaces:**
- Produces: `IVtfCandidateEncoder.EncodeAsync(VtfCandidateEncodingRequest, CancellationToken)`.
- `DdsBcReader.Read` retorna mips BC; transplanter exige formato, dimensões, mip count e byte count iguais ao canônico.

- [ ] **Step 1: Escrever RED para ordem e rejeições**

```csharp
[Fact]
public void TransplantMapsDdsLargestFirstToVtfSmallestFirst()
{
    var dds = DdsFixture.Bc1(8, 8, 4);
    string canonical = VtfFixtureBuilder.WriteLegacy72(8, 8, 13, 4);
    VtfBcPayloadTransplanter.Replace(canonical, dds);
    Assert.Equal(dds.Mips[0].Bytes, ReadTopMip(canonical));
}

[Fact]
public void TransplantRejectsMismatchedMipCount()
{
    Assert.Throws<InvalidDataException>(() =>
        VtfBcPayloadTransplanter.Replace(
            VtfFixtureBuilder.WriteLegacy72(8, 8, 13, 4),
            DdsFixture.Bc1(8, 8, 1)));
}
```

- [ ] **Step 2: Rodar RED**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter "DdsBcReaderTests|VtfBcPayloadTransplanterTests" -v minimal
```

Expected: FAIL por adapters ausentes.

- [ ] **Step 3: Implementar leitores e adapters**

VTFCmd cria VTF canônico. Texconv/Compressonator criam DDS. O transplanter copia somente intervalos high-res validados; header, thumbnail, flags e recursos ficam no canônico.

- [ ] **Step 4: Criar setup idempotente**

O script baixa releases oficiais em `experiments/_tools`, valida SHA-256 do manifesto, registra versão/licença e falha em divergência. Não toca `gui/`, `dist/` ou addon.

- [ ] **Step 5: Rodar GREEN e round-trip real**

```powershell
.\scripts\setup_vtf_maximum_tools.ps1
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter "DdsBcReaderTests|VtfBcPayloadTransplanterTests" -v minimal
dotnet run --project .\experiments\VtfMaximumLab\VtfMaximumLab.csproj -- encoder-smoke --root ".\experiments\_work\lvs-cars-vtf-maximum"
```

Expected: três adapters geram VTFs reabríveis/decodificáveis; incompatibilidade vira rejeição, nunca overwrite.

### Task 8: Métricas, validação e seleção

**Files:**
- Create: `experiments/VtfMaximumLab/Validation/VtfStructuralValidator.cs`
- Create: `experiments/VtfMaximumLab/Metrics/RgbQualityMetrics.cs`
- Create: `experiments/VtfMaximumLab/Metrics/AlphaQualityMetrics.cs`
- Create: `experiments/VtfMaximumLab/Metrics/CutoutCoverageMetrics.cs`
- Create: `experiments/VtfMaximumLab/Metrics/NormalAngularMetrics.cs`
- Create: `experiments/VtfMaximumLab/Metrics/FlipMetricRunner.cs`
- Create: `experiments/VtfMaximumLab/Selection/VtfCandidateSelector.cs`
- Create: `experiments/VtfMaximumLab.Tests/VtfQualityGateTests.cs`
- Create: `experiments/VtfMaximumLab.Tests/VtfCandidateSelectorTests.cs`

**Interfaces:**
- Produces: `VtfCandidateAssessment`; selector recebe original, current e assessments.

- [ ] **Step 1: Escrever RED para gates e desempate**

```csharp
[Fact]
public void SmallerCandidateIsRejectedWhenRgbGateFails()
{
    var assessment = FixtureAssessment.Smaller(rgbSsim: 0.94, flipMean: 0.02, psnr: 35);
    Assert.False(VtfQualityGate.Accept(assessment).Accepted);
}

[Fact]
public void EqualSizePrefersHigherResolutionThenLowerError()
{
    var winner = VtfCandidateSelector.Select(FixtureAssessment.EqualSizePair());
    Assert.Equal(1, winner.ScaleDivisor);
}
```

- [ ] **Step 2: Rodar RED**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter "VtfQualityGateTests|VtfCandidateSelectorTests" -v minimal
```

Expected: FAIL por métricas/política ausentes.

- [ ] **Step 3: Implementar gates literais da especificação**

Implementar FLIP mean/P95, SSIM/PSNR linear, alpha SSIM/MAE/P99, cobertura/IoU e erro angular mean/P95/max. Filtrar inválidos/dominados, escolher menor e desempatar por resolução, erro, alterações e duração.

- [ ] **Step 4: Rodar GREEN completo**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj -v minimal
```

Expected: todos aprovados, nenhum teste ignorado.

### Task 9: Relatórios, previews e benchmark

**Files:**
- Create: `experiments/VtfMaximumLab/Reporting/VtfExperimentReport.cs`
- Create: `experiments/VtfMaximumLab/Reporting/VtfReportWriter.cs`
- Create: `experiments/VtfMaximumLab/Previews/VtfComparisonPanelWriter.cs`
- Create: `experiments/VtfMaximumLab/Experiment/VtfMaximumExperimentRunner.cs`
- Create: `experiments/VtfMaximumLab.Tests/VtfReportWriterTests.cs`
- Modify: `experiments/VtfMaximumLab/Program.cs`

**Interfaces:**
- Produces: report JSON, CSVs, previews, árvore maximum e `IntegrationGatePassed`.

- [ ] **Step 1: Escrever RED para totais**

```csharp
[Fact]
public void ReportCountsStatusesAndRealVtfBytes()
{
    var report = FixtureReport.OneOfEach();
    string json = VtfReportWriter.ToJson(report);
    Assert.Contains("\"preserved\": 1", json);
    Assert.Contains("\"reduced\": 1", json);
    Assert.Contains("\"rejected\": 1", json);
    Assert.Equal(report.Textures.Sum(x => x.MaximumBytes), report.MaximumBytes);
}
```

- [ ] **Step 2: Rodar RED**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj --filter VtfReportWriterTests -v minimal
```

Expected: FAIL por writer ausente.

- [ ] **Step 3: Implementar runner e painéis**

Gerar candidatos em temp dirs exclusivos, validar, selecionar, copiar atomicamente, registrar rejeições e duração. Painéis mostram original/current/Maximum, alpha, diferença, erros, cobertura e normal iluminada.

- [ ] **Step 4: Rodar GREEN e benchmark**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj -v minimal
dotnet run --project .\experiments\VtfMaximumLab\VtfMaximumLab.csproj -c Release -- experiment --root ".\experiments\_work\lvs-cars-vtf-maximum" --tools ".\experiments\_tools"
```

Expected: 50 decisões e nenhum aceite abaixo dos gates; hashes originais intactos.

### Task 10: Inspeção, hipóteses adicionais e decisão

**Files:**
- Modify when evidence requires: `experiments/VtfMaximumLab/Encoding/*`, `Images/*` e testes correspondentes
- Create ignored: `experiments/_work/lvs-cars-vtf-maximum/research-log.md`

**Interfaces:**
- Produces: decisão reproduzível do gate e hipóteses testadas.

- [ ] **Step 1: Inspecionar todos os 50 painéis**

Registrar pass/rejeição. Rejeitar halos, banding, perda semântica, cobertura instável, normal achatada/invertida e emissivo/decal alterado.

- [ ] **Step 2: Comparar encoders por classe**

Agrupar tamanho e métricas por encoder/formato/escala/semântica. Encoder em mesmo formato/resolução/mips só reivindica ganho de qualidade, nunca de tamanho.

- [ ] **Step 3: Se falhar, testar sequência fechada de hipóteses**

Na ordem: qualidade máxima de texconv/Compressonator; cluster-fit redistribuível; Lanczos linear versus blur-aware; cobertura por mip; normal vetorial/geodésico; remoção exclusiva de payload em `NOMIP`; thumbnail ausente somente após round-trip e teste no GMod. Cada mudança nasce de teste RED.

- [ ] **Step 4: Repetir árvores limpas após hipótese aceita**

```powershell
dotnet run --project .\experiments\VtfMaximumLab\VtfMaximumLab.csproj -c Release -- reset-derived --root ".\experiments\_work\lvs-cars-vtf-maximum"
dotnet run --project .\experiments\VtfMaximumLab\VtfMaximumLab.csproj -c Release -- baseline --root ".\experiments\_work\lvs-cars-vtf-maximum"
dotnet run --project .\experiments\VtfMaximumLab\VtfMaximumLab.csproj -c Release -- experiment --root ".\experiments\_work\lvs-cars-vtf-maximum" --tools ".\experiments\_tools"
```

Expected: original intact e relatório com versões/hashes.

- [ ] **Step 5: Aplicar o gate**

Se Maximum não for ≥10% menor que current ou qualquer aceito falhar, não modificar UI. Se passar, congelar relatório e escrever plano separado de integração baseado só nos vencedores.

### Task 11: Verificação experimental

**Files:**
- Inspect: todos os arquivos alterados
- Modify spec somente se resultados criarem protocolo versionado novo

**Interfaces:**
- Produces: evidência fresca para iniciar ou negar integração.

- [ ] **Step 1: Verificar placeholders, diff e escopo**

```powershell
rg -n -i "TBD|TODO|implement later|fill in details" .\experiments .\GmodAddonCompressor-master\GmodAddonCompressor\Systems\Vtf .\docs\superpowers
git diff --check
git status --short
```

Expected: sem placeholders, diff check limpo e mudanças preexistentes em `gui/` intactas.

- [ ] **Step 2: Rodar suite e builds frescos**

```powershell
dotnet test .\experiments\VtfMaximumLab.Tests\VtfMaximumLab.Tests.csproj -c Release -v minimal
dotnet build .\GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj -c Release
```

Expected: exit 0, zero testes falhos e build WPF bem-sucedido.

- [ ] **Step 3: Revalidar artefatos e hashes**

Executar `verify --root` no laboratório. Expected: 50/50 árvores reabrem, hashes original correspondem ao manifesto e totais JSON igualam disco.

- [ ] **Step 4: Registrar decisão sem commit prematuro**

Se gate falhar, continuar Task 10. Se passar, atualizar o plano corrente e criar `docs/superpowers/plans/2026-07-20-compress-maximum-vtf-integration.md`. Não fazer commit/push nesta etapa.
