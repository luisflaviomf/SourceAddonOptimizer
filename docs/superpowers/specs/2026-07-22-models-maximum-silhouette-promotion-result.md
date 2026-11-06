# Models Maximum v2: resultado da promoção controlada do kernel de silhueta

Data: 2026-07-22  
Branch: `feature/models-maximum-adaptive-v2`  
Estado: candidato final RC10 aprovado no gate técnico; sem merge na `main` e sem release oficial.

## Gate final RC10

O gate final foi repetido a partir de uma exportação limpa do commit de código
`83e3e748250278026c2f5941ecf677f6665f9bd4`. O DLL nativo Release x64 e o
worker PyInstaller foram reconstruídos do zero antes de executar
`build_release_wpf.ps1`. O pacote resultante passou pelos validadores de
manifesto, ABI, arquitetura e contrato de instalação do WPF.

Resultados de verificação:

- 23 testes nativos e 113 testes Python passaram;
- contrato WPF passou, inclusive 16 instalações concorrentes, pacote adulterado,
  extração interrompida, ZIP incompleto, manifesto inválido, DLL x86 e DLL falsa
  adjacente;
- o DLL é AMD64, exporta a ABI 1.0.0 e depende somente de `KERNEL32.dll`;
- o pacote tem 74 arquivos, nenhum PDB/cache/fonte C++/header e nenhum caminho de
  desenvolvimento;
- o WPF real executou Maximum em 18,589 s, selecionou o backend nativo nas nove
  chamadas, não usou fallback e manteve os hashes aprovados de MDL/VVD/DX90/PHY;
- uma execução em caminhos com `ação` e `空` terminou em 15,500 s;
- duas instâncias simultâneas do WPF permaneceram funcionais;
- a segunda execução não alterou a árvore instalada e não deixou diretórios
  `.partial` nem caches dentro dos tools.

O teste final de fallback usou o worker bruto sem o manifesto intencionalmente.
Ele terminou em 19,453 s com KD (`fallback_stage=initialize`). Um novo processo,
executando o pacote instalado válido, voltou ao backend nativo e terminou em
15,435 s. A etapa `adaptive-simplification` caiu de 9,220045 s para 5,738918 s:
3,481127 s a menos, ou -37,756% (1,607x). As duas lanes selecionaram
13.578 de 16.304 triângulos e produziram exatamente os mesmos quatro arquivos:

| Arquivo | SHA-256 |
|---|---|
| `wheel.dx90.vtx` | `e5794c1aca2da04c039d44d0d9d29eb20e23695b5453a5756c50f7598ee2d9fc` |
| `wheel.mdl` | `f7c8b07f9ab95f06f036d1581d55614bad46277bb5b388508655a5dbcd0c4a02` |
| `wheel.phy` | `fa3e392bdd115a77570f8f323fc5f8ae28590e7843744b0eaefcd116a905cb3c` |
| `wheel.vvd` | `8d1bbfd6d2209ae2a99ed4994813c0f00c02c6c2f73bbeb5c036e3264bfda5ee` |

Regressões reais curtas do mesmo gate, sempre sem DX80 e sem fallback nativo:

| Família | Tempo total | Triângulos | Bytes comparáveis | Redução |
|---|---:|---:|---:|---:|
| Pontiac wheel | 19,671 s | 16.304 -> 13.578 | 981.876 -> 835.224 | 14,94% |
| Toyota Supra | 192,822 s | 331.485 -> 259.143 | 18.052.152 -> 14.061.517 | 22,11% |
| Dodge Charger, skinning | 194,734 s | 299.415 -> 214.550 | 21.759.408 -> 16.125.385 | 25,89% |
| Caterham, 11 modelos | 42,591 s | 50.258 -> 36.912 | 4.120.885 -> 3.250.264 | 21,13% |

O gate de equivalência cross-model confirmou floats, decisões e payloads exatos
em Toyota (vidro/múltiplos materiais), Dodge (skinning/pesos mistos) e Pontiac.
No Dodge, a reprovação local de silhueta/material boundary também foi idêntica,
demonstrando que o fallback regional continuou restrito à região que falhou.

Durante a revisão de segurança foi encontrado um overflow possível na validação
de `row_stride * height` e do span da última vista. O commit `83e3e74` passou a
validar multiplicações e somas antes de qualquer leitura e adicionou um teste de
regressão. Todos os exports permanecem `noexcept`, inicializam/limpam a saída,
validam ponteiros, dimensões, capacidades e máscaras, e convertem exceções em
códigos de erro. Uma falha nativa parcial sempre descarta a tentativa completa e
recalcula a métrica exata pelo KD.

Limitações conhecidas do candidato:

- o publish é uma pasta/ZIP completo; o EXE isolado não é single-file;
- o alvo `net6.0-windows` está fora de suporte;
- `Magick.NET-Q16-AnyCPU` 14.10.4 mantém advisories NuGet preexistentes, incluindo
  um de severidade alta; isto não foi introduzido por Maximum v2;
- a saída de console do Blender ainda pode exibir mojibake cosmético em alguns
  caminhos Unicode, embora o job e o StudioMDL funcionem;
- `_nearest` não foi alterado e não faz parte desta promoção.

Artefato final auditado:

- pasta: `D:/gaco-max-v2-final-gate-20260722/candidate-publish-rc10-final`;
- executável: 232.960 bytes, SHA-256
  `6aa5a130a68af34484efbab8ba92b04c7d217da31b40b498488ce4384fb2592f`;
- ZIP interno: 31.298.155 bytes, SHA-256
  `09bccf7743e0ffbbbf6de93786b1b6f573bd31342e85571704a9ececac998bff`;
- DLL nativo: 218.112 bytes, SHA-256
  `3bea244de4962a350c274cb94f6a6d9e205618883557d5444596fd88ed44b490`;
- ZIP do publish: 268.584.961 bytes, SHA-256
  `7cd6a350bd6950f56fcf82e08216545c0ebffec17b024b8a76d30035066a87c6`.

As configurações e a instalação de tools preexistentes do usuário foram
restauradas após o gate. Nenhum merge, push, PR, publicação ou release oficial
foi executado.

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
