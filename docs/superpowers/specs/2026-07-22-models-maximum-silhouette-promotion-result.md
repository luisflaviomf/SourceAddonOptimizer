# Models Maximum v2: resultado da promoção controlada do kernel de silhueta

Data: 2026-07-22  
Branch: `feature/models-maximum-adaptive-v2`  
Estado: candidato RC3 aprovado tecnicamente; sem merge na `main` e sem release oficial.

## Veredito

O kernel nativo exato de silhueta foi integrado ao Maximum, empacotado no ZIP
interno do WPF e exercitado pelo botão Models do WPF publicado. Ele conserva o
mesmo resultado do KD anterior e reduz materialmente o tempo da etapa adaptativa.

No A/B limpo, intercalado e da mesma sessão, a mediana aquecida da silhueta caiu
de 5,094586 s para 1,468585 s (3,469x; -71,17%). A etapa adaptativa completa caiu
de 9,257608 s para 5,477143 s (-40,84%) e o harness completo caiu de 11,785709 s
para 7,993818 s (-32,17%). O pico de memória aumentou 0,72% no harness isolado.

O teste final pelo WPF real terminou 17,3076 s depois do clique, incluindo
extração/worker/decompile/Blender/Maximum/render regional/StudioMDL/packaging. O
backend ficou nativo nas nove chamadas, sem fallback. A etapa adaptativa levou
5,782636 s.

Esta promoção acelera a validação; ela não altera a política de compressão nem
a qualidade. No Pontiac usado no teste WPF, original e resultado continuaram em
981.876 e 835.224 bytes comparáveis, respectivamente: 146.652 bytes ou 14,9359%
de redução. DX80 foi removido e excluído da métrica. O resultado compilado nativo
é byte a byte idêntico ao fallback KD.

## Integração promovida

- `meshopt_bridge.dll` mantém os exports anteriores e adiciona ABI explícita
  1.0.0, build `maximum-silhouette-raw-v1-20260722`, AMD64, oito vistas e
  capacidades/tamanhos/alinhamentos validados antes da primeira chamada.
- O worker Maximum recebe somente caminho absoluto declarado pelo manifesto.
  CWD, pasta do EXE, `PATH`, temporários genéricos e
  `MAXIMUM_SILHOUETTE_EXPERIMENT_DLL` não participam da resolução.
- Normal e Fidelity não importam nem inicializam o backend de silhueta.
- Qualquer erro nativo torna o backend indisponível pelo restante do processo,
  descarta a tentativa parcial e recalcula toda a métrica pelo KD exato.
- O manifesto 0.1.18 declara todos os arquivos, hashes, tamanhos, arquitetura,
  versão de API, build ID e contratos mínimos worker/WPF.
- O WPF extrai para
  `%LOCALAPPDATA%/GmodAddonOptimizer/tools/SourceAddonOptimizer/0.1.18/<zip-sha>/`,
  sob lock, primeiro em `.partial`, valida integralmente e publica por movimento
  atômico. Raízes anteriores não são sobrescritas.
- O DLL Release usa runtime estático; `dumpbin /dependents` mostrou apenas
  `KERNEL32.dll`.
- O relatório persistente `logs/maximum_silhouette_backend.json` registra DLL,
  API/build, chamadas, fases, memória e fallback.

## Defeito empacotado encontrado e corrigido

O primeiro teste completo do worker empacotado revelou que
`_attribute_contract_sha256()` lia `mesh_attributes.py` e `meshopt_bridge.py`
como arquivos, mas o PyInstaller os mantinha somente no PYZ. O Maximum falhava
antes da etapa adaptativa com `FileNotFoundError`.

A correção é deliberadamente pequena:

1. os dois fontes de contrato agora são `datas` explícitos do worker;
2. o validador do pacote rejeita qualquer manifesto que os omita;
3. dois testes reproduziram a falha antes da mudança e passaram depois;
4. o worker e o ZIP foram reconstruídos do zero;
5. o mesmo job empacotado foi repetido e concluiu nativamente.

O diretório bruto do PyInstaller, que propositalmente ainda não contém o
manifesto final, também foi executado: selecionou fallback KD, terminou o job e
produziu o mesmo resultado. O pacote válido é o ZIP montado/validado pelo build,
não `dist/GModAddonOptimizerWorker` isolado.

## Equivalência

| Verificação | Resultado |
|---|---:|
| Máscaras determinísticas/aleatórias | 10.000 exatas |
| Hash do contrato de máscaras | `c3370f5d06ac7922044a12ad3cc2e80b92960e7bd389c64da76650cde33419c9` |
| Toyota body/glass/material | floats, bytes e decisões exatos |
| Dodge com skinning/pesos mistos | floats, bytes e decisões exatos |
| Pontiac roda/região simples | floats, bytes e decisões exatos |
| A/B final, 30 processos | contrato idêntico entre lanes de cada estado |
| Job real KD versus nativo | relatório de seleção idêntico |
| MDL/VVD/DX90/PHY | arquivos byte a byte idênticos |
| Árvore compilada final | `2c8a4f16abd052d26ba67c3302c7d0fcc02f4cb464c5c6a4e574cab89cc7092e` |
| Bytes comparáveis | 835.224 em ambas as lanes |
| DX80 final | 0 em ambas as lanes |

O job real selecionou 13.578 de 16.304 triângulos, com quatro regiões mais leves
e quinze restauradas, um render regional e um compile StudioMDL. KD e nativo
tomaram exatamente as mesmas decisões.

## Benchmark A/B limpo desta promoção

Cada amostra é um processo novo. A ordem fria foi balanceada em cinco pares por
lane; a aquecida, em dez pares por lane. Nenhuma amostra foi removida. Uma série
anterior, contaminada por testes concorrentes, foi marcada explicitamente como
inválida e não entra nos números abaixo.

| Estado/métrica | KD mediana | Nativo mediana | Ganho |
|---|---:|---:|---:|
| Frio: silhueta | 5,171855 s | 1,422431 s | -72,50%; 3,636x |
| Frio: adaptativo | 9,867377 s | 5,927720 s | -39,93%; 1,665x |
| Frio: harness | 12,381564 s | 8,480373 s | -31,51%; 1,460x |
| Frio: pico RSS | 135.217.152 B | 136.757.248 B | +1,14% |
| Aquecido: silhueta | 5,094586 s | 1,468585 s | -71,17%; 3,469x |
| Aquecido: adaptativo | 9,257608 s | 5,477143 s | -40,84%; 1,690x |
| Aquecido: harness | 11,785709 s | 7,993818 s | -32,17%; 1,474x |
| Aquecido: pico RSS | 141.395.968 B | 142.413.824 B | +0,72% |

No job empacotado real, a etapa adaptativa caiu de 9,420 s no fallback KD para
5,900 s no nativo (-37,37%). Os tempos totais observados, 42,1 s e 15,8 s, não
são usados como ganho porque o primeiro compile aqueceu caches e variou de
5,512 s para 2,114 s. O A/B intercalado acima é a comparação válida.

Os 24,931 s e 8,832 s de investigações antigas nunca são divididos entre si.
O total anterior de 23,490 s tem escopo diferente. Não há evidência suficiente
para atribuir variações entre sessões a uma única causa ambiental.

Ambiente registrado: Windows 11 Pro build 26200, i7-13650HX (14C/20T), plano
Equilibrado, afinidade completa, prioridade Normal, CPython 3.11.9 AMD64. A
temperatura não estava disponível por sensor calibrado e foi marcada como tal.

## Teste WPF real

O RC3 foi aberto, a aba Models foi selecionada por UI Automation e
`Button_OptimizeModels` foi invocado. O WPF extraiu o hash correto e executou:

`C:/Users/luisf/AppData/Local/GmodAddonOptimizer/tools/SourceAddonOptimizer/0.1.18/9f4ee333.../SourceAddonOptimizerWorker.exe`

Resultado:

- 17,307563 s do clique ao relatório final;
- backend nativo 1.0.0, build correto, nove chamadas, zero fallback;
- DLL somente no caminho absoluto extraído/validado;
- 1.305,417 ms de preparação de máscaras;
- 100,116 ms dentro do kernel nativo;
- 5,782636 s na etapa adaptativa;
- um render regional e um compile StudioMDL;
- output íntegro e sem DX80.

O settings do usuário foi restaurado e verificado pelo SHA-256 original
`d2d2b6fd87f8cb8abc6c5c9eaeea760b7df2e9acd36100fa0f653e6f46f3eb73`.
Não restaram processos WPF, worker, Blender ou StudioMDL do teste.

## Testes e build

- Python: 106 testes e 19 subtests passaram.
- Contrato WPF: passou, incluindo adulteração, extração interrompida,
  concorrência, atualização/raiz antiga, ZIP incompleto, arquitetura errada e
  DLL falsa adjacente.
- Build WPF: zero erros. Permanecem avisos preexistentes de EOL do .NET 6 e
  advisories da versão atual de Magick.NET.
- DLL: AMD64, 217.600 bytes, SHA-256
  `caeeb112189aa1879e85af82c0b2a41fd9635d772b1043710a313997df8bc5fb`.
- ZIP interno e cópia auditável foram lidos diretamente do assembly e são
  byte a byte iguais.
- O executável oficial anterior não foi substituído; seu hash continua
  `95ca39846dfe3faa3378196b6ab283d5e27588c78d450ad4b6920405360e1d1b`.

## Candidato RC3

- Pasta completa:
  `D:/gaco-max-v2-silhouette-promotion-20260722/candidate-publish-rc3-fixed`
- Executável:
  `D:/gaco-max-v2-silhouette-promotion-20260722/candidate-publish-rc3-fixed/GmodAddonOptimizer.exe`
  - 232.960 bytes
  - SHA-256 `4a29618e5aacdcf13abfab906c47d9e37902b3306cf2fc860dd48742350c56e5`
- ZIP de tools incorporado/auditável:
  - 31.295.230 bytes
  - SHA-256 `9f4ee333704e1f2e2f5c143d687d11bb5520a8dac5fa4a219da6a288ef5cc147`
- ZIP da pasta publish completa:
  `D:/gaco-max-v2-silhouette-promotion-20260722/candidate-publish-rc3-fixed.zip`
  - 268.689.496 bytes
  - SHA-256 `e0502287f21c8dc981951a9f30de88550e92abfac57297f958843d837ada2c06`
- Manifesto de hashes:
  `D:/gaco-max-v2-silhouette-promotion-20260722/candidate-publish-rc3-fixed.hashes.json`

O publish deve ser usado como pasta completa ou pelo ZIP completo. O perfil
`win-x64-singlefile` não existe no projeto atual, portanto o EXE de 232.960
bytes não é um binário autônomo isolado. Isso é comportamento preexistente do
build, agora documentado em vez de ser apresentado como single-file.

## Evidência

- Raiz: `D:/gaco-max-v2-silhouette-promotion-20260722`
- 10.000 máscaras: `mask-equivalence-10000.json`
- Cross-model: `cross-equivalence.json`
- A/B limpo: `balanced-benchmark-final3-clean/run-index.json` e `runs/*.json`
- Ambiente: `benchmark-environment.json`
- WPF real: `wpf-rc3-result.json`
- Work WPF preservado: `wpf-rc3-work`
- Worker empacotado nativo: `packaged-worker-full-work-rc3-native`
- Candidato/hashes: `candidate-publish-rc3-fixed*`

## Limites e próximos passos

- `_nearest` não foi alterado.
- Nenhuma feature foi adicionada em `gui/`.
- O kernel não aumenta a compressão; preserva exatamente a saída e reduz tempo.
- A projeção/preparação das máscaras, não o código nativo, agora domina a
  silhueta. Qualquer otimização futura precisa manter a rasterização exata.
- O teste WPF final foi um Pontiac detalhado. A equivalência mais ampla usa os
  oráculos e os modelos Toyota/Dodge/Pontiac já congelados.
- Branch e worktree permanecem preservadas. Nenhum merge, push, PR ou release
  oficial foi feito.
