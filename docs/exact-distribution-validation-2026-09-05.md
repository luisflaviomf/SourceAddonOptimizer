# Validação da integração de distribuição exata — 05/09/2026

## Escopo e estado

Implementação no WPF real, em `C:/Users/luisf/Music/teste`, com worker versionado 0.1.18. Opção experimental e desativada por padrão no Pipeline; nenhuma baseline promovida e nenhum candidato instalado automaticamente.

Release corrigido `release-build-r2.log`: exit 0. Executável em `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`, SHA-256 `9a7be037e6f5393744037b1af9d8277d537ee55f8632f6b4161e97d70018b047`. ZIP de tools SHA-256 `03e2e8a59c2bd53aa79fe4fba37bcd6d5ba6b74f3c2d4f1c407c472593933735`, idêntico ao pacote efetivamente testado; a última correção foi apenas WPF.

As amostras abaixo são assets reais de carros (Alfa Montreal), armas (Armalite) e props (GTAV bar). Não são uma validação funcional completa de todos os SWEPs/veículos. A aprovação manual anterior do TacRP permanece independente desta integração.

## Worker efetivamente empacotado

Extraído do ZIP de tools gerado pelo release oficial e executado fora do repositório. Os 17 fontes/templates congelados e os fontes do produtor foram comparados por SHA-256 dentro do pacote. Cada candidato inteiro foi comparado com a árvore usada no teste real do GMod.

| Amostra | Entrada (B) | Candidato (B) | Arquivos reconstruídos | Nativos preservados | DX80/SW excluídos |
|---|---:|---:|---:|---:|---:|
| Carros | 12.154.162 | 3.508.253 | 21 | 30 | 2 |
| Armas | 106.635.770 | 31.192.923 | 171 | 66 | 42 |
| Props | 29.267.152 | 10.299.980 | 898 | 356 | 548 |
| Total | 148.057.084 | 45.001.156 | 1.090 | 452 | 592 |

Economia total nas amostras: **103.055.928 B (69,61%)**. Desses bytes, **94.090.948 B** são economia líquida do transporte/codec, já incluindo suporte, contratos, runtime e manifest; **8.964.980 B** são exclusões DX80/SW. Os tamanhos incluem os pequenos addon.json dos recortes de teste. As dependências compartilhadas do cenário não fazem parte dessas entradas e não são contabilizadas como redução.

Resultado: **1.090/1.090 reconstruções independentes byte a byte e SHA**, 452 nativos inalterados e todas as três árvores empacotadas idênticas às árvores de teste. Nessas amostras, a seleção adaptativa escolheu 283 VVDs no domínio portátil e 807 assets no domínio LZMA exact. P1/P2 estão empacotados e cobertos pelos testes focados de decoder/runtime Lua, mas não foram os domínios escolhidos nessas três amostras; não se alega nova prova real-engine deles com esse recorte.

## GMod real, isolamento e referência

Evidência autoritativa do ciclo: `C:/GModLab/research-cache/product-exact-integration-20260905/live-dev-r3/result.json`.

| Etapa | Resultado client/server | Tempo do ciclo Lab |
|---|---|---:|
| Corrupt | 23/23 FAILED, fail-closed esperado; zero decode/reconstrução | 33,30 s |
| Cold | 23/23 READY | 43,97 s |
| Warm | 23/23 READY; zero decode e zero escrita de reconstrução/cache | 37,84 s |

Os tempos incluem o ciclo do laboratório, não apenas o codec. Warm ainda escreve receipts de evidência. Os receipts verificam ausência dos alvos antes da montagem, autenticação do suporte/cache, conjunto montado e SHA pós-mount. Nenhum fallback dos alvos transformados para originais/Workshop foi permitido.

Foram criados/carregados **22 modelos em cada realm** no cold e novamente no warm (44 probes por etapa). Validados modelo, raio/bounds e lista de materiais contra uma execução nativa dos mesmos bytes de entrada; no client, também a lista de materiais de erro. Isso não substitui inspeção visual humana, animações ou comportamento completo de armas.

Uma tentativa inicial revelou materiais compartilhados ausentes no recorte. Foram adicionadas 119 dependências de materiais nativas autenticadas, sem qualquer sobreposição com os alvos transformados. Uma segunda tentativa identificou um slot `___error` no carro. A referência nativa reproduziu o mesmo slot; o gate final exige igualdade com essa referência, sem ignorar diferenças novas. Tentativas e logs foram preservados. O modelo original também emite aviso de `$basetexture` repetido no material headlight_glass; não foi corrigido nem atribuído à compressão.

Referência nativa: `native-reference-r2/reference.json`, SHA-256 `1bdf7541bd42362e65dbfaccfbd7c8a5a65abff838b427aa2b1d85fed8b20881`, sob a mesma raiz de evidência. A referência está copiada no ciclo final e vinculada aos CIDs das três entradas/candidatos.

## Preservação e limites

Postflight de identidade, somente leitura: `protected-postflight.json` na raiz de evidência. Baseline oficial LVS de 235.261.911 B, baseline anterior de 329.100.690 B, P0, P1, pointer e TacRP manual de 243.396.740 B mantiveram os hashes aprovados. O addon manual continua em `C:/Program Files (x86)/Steam/steamapps/common/GarrysMod/garrysmod/addons/000_tacrp_junto_fullstack_v2`. O jogo do laboratório foi encerrado.

A versão anterior do programa (533 arquivos/283.627.565 B) e seu ZIP de tools estão preservados em `.superpowers/sdd/2026-09-05-adaptive-exact-distribution/pre-release-publish/` e `pre-release-tools.zip`.

Testes registrados: Python 62 PASS; .NET final **229/229 PASS** (`dotnet-final-r1.log`) após a correção de junction/log, incluindo os 25 testes focados. Revisões independentes verificaram os fixes de grafia de diretórios Windows e rejeição de junctions antes de gravações. A revisão ampla final não encontrou Critical/Important no escopo experimental/default-off. Há avisos preexistentes de .NET 6 fora de suporte, advisories Magick.NET e Assembly.Location em single-file; testes negativos LuaJIT produzem diagnóstico de exceção tratada. Não são saídas pristine e não houve migração de plataforma nesta etapa. Minor opcional documentado: uma falha de append/cópia do log persistente pode não gerar aviso na UI, sem comprometer os bytes do candidato; melhoria deixada para depois.

**Inspeção interativa da interface publicada: pendente.** A ferramenta suportada de automação Windows falhou ao inicializar e na única recuperação tentada. Não foi contornada por automação alternativa. Build/testes/worker/GMod não equivalem a ter inspecionado visualmente a UI. As instruções de uso estão em [exact-distribution.md](exact-distribution.md).

## Decisões de implementação

- Trabalhar no checkout não-main atual com preimages, preservando alterações locais; uma worktree de HEAD não conteria essas alterações. Custo se inadequado: separar/reaplicar os deltas preservados.
- Disponibilizar a nova etapa explicitamente como experimental, sem trocar silenciosamente políticas de qualidade. Custo: um opt-in extra até validação mais ampla.
- Preservar grafia física nativa e usar caminhos GAME canônicos para os arquivos codificados; rejeitar colisões. Custo se houver limitação de plataforma: manter o arquivo nativo e investigar sem sobrescrevê-lo.
- Aplicar o limite de 8 MiB ao VTF P2, mantendo 64 MiB no domínio LZMA de assets aprovado. Custo: maior uso temporário possível na reconstrução; os gates de identidade e tamanho permanecem ativos.

Não houve merge/push, limpeza de evidências/rollbacks ou promoção de baseline. Mudanças pré-existentes permanecem no checkout; deltas desta integração em arquivos mistos estão preservados para revisão contra preimages, não confundidos com mudanças anteriores.
