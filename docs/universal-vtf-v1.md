# Universal Adaptive VTF V1

Este documento descreve o driver de corpus da política Universal VTF V1. Ele é
um gerador/auditor offline e **não promove nenhuma árvore como baseline**. Toda
execução de produção parte das duas autoridades congeladas, publica três árvores
novas numa única transação e preserva os arquivos de origem.

## Autoridades congeladas

| Autoridade | Arquivos | Bytes | SHA-256 da árvore |
| --- | ---: | ---: | --- |
| `lvs_cars_pack_raw` | 3.827 | 1.441.918.651 | `5f7b31c77e34608c4a40c6b945a7ad24b151be4ad067761c24aa1f93ed908758` |
| `corpus_bc3_to_bc1_safe` | 3.664 | 438.230.044 | `8e60b8b46744a192fd5ab1ab77848ab9fd4123f1dd042c31d365e16505a698af` |

O censo VTF congelado contém 1.152 caminhos e 835.331.232 bytes na árvore
raw: 200 DXT1 e 952 DXT5; 1.145 cadeias authored e 7 texturas `NOMIP`.
O bridge BC3 aprovado precisa preservar a partição física congelada de 193
linhas: 155 `physical_opaque`, 32 `semantic_detail_rgb_only`, 6 bloqueadas e
0 ambíguas. O caminho e o SHA-256 do bridge são entradas explícitas; não existe
descoberta implícita de um manifesto anterior.

## Driver

Execute a partir da raiz do repositório:

```powershell
$driver = '.\experiments\VtfUniversalCorpusDriver\VtfUniversalCorpusDriver.csproj'
dotnet run --project $driver -c Release -- toolchain
dotnet run --project $driver -c Release -- inventory --raw-root <raw> --output <novo-json>
dotnet run --project $driver -c Release -- bc3-bridge --raw-root <raw> --safe-root <safe> --prior-manifest <manifest-aprovado> --output <novo-json>
dotnet run --project $driver -c Release -- generated-smoke --raw-root <raw>
dotnet run --project $driver -c Release -- build --raw-root <raw> --safe-root <safe> --bc-bridge <bridge-json> --bc-bridge-sha256 <sha256> --output-parent <diretorio-novo> --profiles all
dotnet run --project $driver -c Release -- audit --raw-root <raw> --safe-root <safe> --candidate-root <candidato> --manifest <manifest> --manifest-sha256 <sha256> --bc-bridge <bridge-json> --bc-bridge-sha256 <sha256>
```

`build` aceita `--report-root <diretorio-novo>` para separar relatórios e,
opcionalmente, o par inseparável `--runtime-caps <json>
--runtime-caps-sha256 <sha256>`. `audit` aceita o mesmo par. Caminho sem hash,
hash sem caminho, perfil diferente de `all`, destino existente ou sobreposição
entre raw/safe/destinos falham de forma fechada.

`build --profiles all` é deliberadamente uma única chamada ao builder. Ela
materializa e audita `universal-vtf-conservative`, `universal-vtf-balanced` e
`universal-vtf-aggressive` na mesma transação. Não há três invocações com estado
independente, nem decisões de threshold, classificação ou resize no CLI.

## Saída e schemas

O stdout é JSON Lines. Durante `build`, linhas de progresso têm:

```json
{"type":"progress","stage":"prepare|compose","completed":1,"total":1152,"relativePath":"materials/...vtf","profile":"balanced|null"}
```

A última linha usa `universal-vtf-build-result-v1` e registra fingerprint da
política, toolchain, bridge, caps e, para cada perfil, raiz, manifesto, hash do
manifesto, bytes e hash da árvore. Os manifestos canônicos usam
`universal-vtf-manifest-v1`; cada uma das 1.152 linhas contém autoridade raw,
safe/spatial/final, classificação semântica, tentativas por perfil, rota de
filtro, gates, formato, dimensões, hashes e contabilidade de bytes. O agregado
registra economias raw→final e safe→final, níveis, classes, famílias, bloqueios,
byte floors e recibos BC.

`audit` emite `universal-vtf-audit-result-v1`, com `status: CLEAN` somente se
árvores, path sets, manifesto, fingerprints, bridge/caps, formatos, receipts e
contabilidade forem consistentes. A auditoria Python independente deve ser
executada além do auditor C# antes de qualquer teste manual ou promoção.

## Códigos de saída

| Código | Significado |
| ---: | --- |
| 0 | CLEAN/sucesso |
| 2 | argumento, par path/hash, perfil ou isolamento inválido |
| 3 | anchor ou censo congelado divergente |
| 4 | falha de transformação/publicação |
| 5 | auditoria DIRTY ou inválida |
| 130 | cancelado por `Ctrl+C` |

## Determinismo, caps e cancelamento

O fingerprint de política é derivado dos fixtures canônicos, toolchain real e
SHA-256 declarado do bridge. Hashes são sempre recalculados antes da publicação;
declarar um hash não substitui a verificação. Timestamps do filesystem não fazem
parte da autoridade: bytes, caminhos relativos canônicos e hashes formam o
envelope determinístico. Uma repetição com as mesmas entradas deve produzir os
mesmos bytes de candidatos, manifestos e hashes de árvore.

Runtime caps usam `universal-vtf-runtime-caps-v1` e só são aceitos com o hash
correspondente. O hash fica propagado no fingerprint operacional e em todos os
manifestos. Sem caps, nenhum manifesto pode alegar caps verificados.

`Ctrl+C` sinaliza o mesmo `CancellationToken` para inventário, materialização,
composição e publicação. O processo retorna 130. Publicação só começa depois de
duas auditorias internas; uma falha durante publicação move de volta tudo que já
foi publicado.

## Work paths e recuperação

Dentro do `--output-parent` novo, o builder usa temporariamente:

```text
.universal-vtf-recovery-<guid>/
  raw-derived-candidates/
  manifests/
  universal-vtf-conservative.staging/
  universal-vtf-balanced.staging/
  universal-vtf-aggressive.staging/
```

Após CLEAN, os três `.staging` são publicados com os nomes definitivos e os
manifestos vão para `--report-root`. A recuperação é removida somente quando é
um diretório de propriedade comprovada do builder. Se uma falha impedir limpeza,
ela permanece disponível para diagnóstico; nunca se deve apagar raw, safe ou
uma baseline congelada para recuperar espaço.

## Rollback e promoção

Cada execução usa um `--output-parent` fisicamente novo. Para rollback, descarte
ou desmonte somente a árvore candidata e volte a montar a baseline safe
congelada. Preserve candidato, manifesto, SHA-256, bridge, caps, logs e relatório
de auditoria. CLEAN offline significa apenas `OFFLINE_CLEAN_MANUAL_GMOD_PENDING`:
nenhum comando deste driver substitui, altera ou promove automaticamente a
baseline aprovada.
