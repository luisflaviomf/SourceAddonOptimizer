# AGENTS.md

## Visao geral do repositorio

Este repositorio contem dois frontends e um backend Python compartilhado:

- **Produto principal atual**: aplicativo WPF em `GmodAddonCompressor-master/GmodAddonCompressor/`
- **Frontend legado**: GUI Python em `gui/`
- **Backend/worker Python**: scripts na raiz + entrypoint em `worker/worker_main.py`

Se houver qualquer duvida sobre onde implementar novas features, a resposta padrao e:

1. **UI nova vai no WPF**
2. **backend pesado continua no worker Python**
3. **nao implementar novas abas no frontend Python legado, salvo instrucao explicita do usuario**

## Qual e o produto principal

O aplicativo principal e real usado no dia a dia e o **WPF** em:

- `GmodAddonCompressor-master/GmodAddonCompressor/`

Este e o app que deve receber:

- novas abas
- novos controles de UI
- novas integracoes de fluxo
- novos resumos/status/progresso
- ajustes de UX
- integracao com worker/tools

O executavel final distribuivel do produto principal sai em:

- `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`

## O que e legado e o que e atual

### Atual

- `GmodAddonCompressor-master/GmodAddonCompressor/`
- `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml`
- `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml.cs`
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/*`
- `worker/worker_main.py`
- scripts Python de backend na raiz (`build_optimized_addon.py`, `batch_*`, `render_previews.py`)

### Legado

- `gui/`
- `pyinstaller/gui.spec`
- `dist/GModAddonOptimizer/`
- `build_release.cmd` na raiz, que hoje chama o build do frontend Python antigo
- `GmodAddonCompressor-master/build.bat`, que faz apenas publish do WPF e **nao** empacota os tools corretamente para release completo

Regra permanente:

- **Nao usar `gui/` para novas features**, a menos que o usuario peca explicitamente para mexer no frontend Python antigo.

## Estrutura dos diretorios importantes

- `GmodAddonCompressor-master/GmodAddonCompressor/`: app WPF principal
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/`: integracao WPF -> worker para models/pipeline
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Unpack/`: integracao WPF -> worker para descompactacao de addons
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Tools/`: extracao/versionamento dos tools empacotados
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Reporting/`: size report
- `worker/worker_main.py`: entrypoint do worker Python
- `pyinstaller/`: empacotamento PyInstaller do worker e do frontend Python legado
- `dist/GModAddonOptimizerWorker/`: saida do worker empacotado
- `batch_unpack_addons.py`: backend de descompactacao de `.gma` e `.bin`
- `build_optimized_addon.py`: backend principal de models/pipeline
- `build_release_wpf.ps1`: script correto de release do app principal

## Como funcionam WPF + worker Python

Arquitetura real:

1. O WPF e a interface principal.
2. O WPF nao implementa a logica pesada de models/unpack diretamente.
3. O WPF chama o worker Python empacotado (`SourceAddonOptimizerWorker.exe`).
4. O worker roteia comandos:
   - `unpack` -> `batch_unpack_addons.main(...)`
   - `exactpack` -> `exact_distribution.cli.main(...)` (distribuicao exata experimental)
   - `texscreen` -> `texlab.screen_compress.main(...)` (modo Tela de VTF)
   - `modscreen` -> `model_screen.cli.main(...)` (modo Tela de Models)
   - fluxo padrao -> `build_optimized_addon.main(...)`
5. O WPF consome stdout/progresso/logs e atualiza a UI.

Pontos importantes:

- A aba **Descompactar addons** ja existe no WPF e usa o worker `unpack`.
- A aba **Models** usa o worker empacotado para o pipeline de otimizar/recompilar modelos. Modo **Tela** (índice 3, worker `modscreen` em `model_screen/`): simplifica direto nos binários MDL/VVD/VTX, sem Blender/Crowbar/studiomdl; perfis `balanced` (Equilibrado, quase idêntico), `strong` (Forte, modelos ~50% menores, VVD sem tangentes) e `strong_mirror` (Forte + espelho: mesmos limites do Forte com o motor `model_screen/remesh.py` — cada peça simplificada inteira com costuras do meshoptimizer — e `model_screen/mirror.py` — o lado direito de modelos simétricos é desenhado por um strip extra que reusa os vértices do lado esquerdo através de ossos-espelho com `poseToBone` refletido; só espelha onde geometria e textura batem, todas as texturas do material contam, inclusive `$refracttinttexture`; procura materiais também nos addons vizinhos da pasta, ex. `lvs_framework`; LVS models −61% com aparência equivalente ao Forte; ver `docs/modlab-modelos-2026-11-06.md`); Forte + espelho é o padrão da interface (índice 2) e do `SourceAddonOptimizerCommandBuilder` (o WPF sempre passa `--profile`); perfil `stream` (índice 3, "Stream (experimental)", pacote `model_stream/`): models grandes rígidos guardam a geometria ORIGINAL comprimida (GMOS em `data_static/gmo/`) e `lua/autorun/client/gmo_stream_<addon>.lua` (runtime.lua + manifesto) a desenha com IMeshes no lugar do model, com probe de luz por entidade (`models/gmo_stream/probe_v1.mdl` + material com `$envmap env_cubemap`/bump/phong) e normais nos 2 bytes do motor; o .mdl fica com malha grossa ~2% (sombra, distância, reserva); rodas e models pequenos usam Forte + espelho; LVS: models 213 → ~48 MiB com qualidade igual ou melhor (ver `docs/modelos-stream-2026-11-08.md`); usa `tools/meshopt/meshopt_simplify.dll` (meshoptimizer v1.2 de `third_party/`); a passagem de costuras e os componentes rodam em Rust (`gmo_seams`/`gmo_components` em `tools/native/gmo_native.dll`, bit-idênticos ao Python que fica como referência/fallback; a DLL emula a ordem dos `set` do CPython 3.11). Modos de recompilação: `normal`, `fidelity` e `maximum` (Models Maximum adaptive v2 em `maximum_optimizer/`, com meshoptimizer nativo via ctypes e perfil `maximum_optimizer/profiles/maximum-adaptive-v2.json`). O TRI_STRIP de VTX (`--vtx-tristrips`) roda no empacotamento de todos os modos, um arquivo por processo (mesmos bytes e relatório da versão sequencial). Também no empacotamento, por padrão, `vvd_engine_exact.py` troca normais/tangentes do VVD por valores "redondos" dentro do mesmo degrau do `PackNormal_UBYTE4` do motor: imagem idêntica no jogo, download da Workshop menor (`--no-vvd-exact-normals` desliga; ver `docs/modlab-modelos-2026-11-06.md`).
- A aba **Mídia e recursos** (Compress) tem os modos Padrao, Magick, Maximum (Universal VTF, perfil Balanced), Magick+ (Magick + repack exato BC3->BC1 quando o alpha comprovadamente nao e usado) e Tela (indice 4: worker `texscreen` em `texlab/screen_compress.py`, reducao por textura pela densidade na tela, mips menores originais exatos, alpha preservado; "Perfil Tela" Equilibrado (`q50`, padrao) ou Forte (`strong`: limiar 0,12, ate 8x por energia so se a textura nunca aparece esticada de perto, texturas `$detail` no maximo 4x, escritas pelo nome do arquivo (`TEXT_NAMES`) e texturas pequenas (2x cabe em 100 KB) em 2x; LVS 136 → 75 MiB, textos legiveis; em addon com models em stream a densidade e medida na geometria do stream via `model_stream/geometry.py`, nao na malha grossa); ver `docs/texlab-vtf-screen-aware-2026-11-06.md` e `docs/modelos-stream-2026-11-08.md`). Os trechos quentes do Tela rodam em Rust (`native/gmo_native` -> `tools/native/gmo_native.dll`, carregado por `texlab/native.py`, bit-identico ao numpy que fica como referencia/fallback; `GMO_NATIVE=0` desliga; ver `tools/native/README.md`). O worker so pode importar os modulos numpy de `texlab` (nunca `render`, `evaluate`, `downscale`, `build_opt`, que usam torch/numba). WAV: "Perfil WAV" Manual (padrao) ou Adaptativo conservador (`Systems/Audio/*`, tambem na aba Mapas): usa as opcoes manuais como referencia e so troca por PCM16/MS-ADPCM 22050/44100 quando fica menor sem piorar SDR/SNR/LSD contra o original; loops (`cue `/`smpl`/`adtl`) preservados; até 8 sons em paralelo (metade das CPUs lógicas). Candidatos reamostrados usam soxr com formato interno double (`internal_sample_fmt=dblp`, `FFMpegSystem.BuildAdaptiveResampleFilter`): com E/S int16 o libsoxr aplica dither TPDF próprio com semente de relógio e a saída variava ±2 LSB por execução; agora é byte a byte reproduzível (candidatos sem troca de taxa mantêm o filtro e os bytes antigos). Relatorio em `%LOCALAPPDATA%\GmodAddonOptimizer\work\audio`, nunca dentro do addon. Ver `docs/benchmarks/2026-08-23-adaptive-conservative-audio.md`.
- A aba **Validação no jogo** (Companion) instala/remove o addon `companion/sourceaddonoptimizer_companion`, importa perfis de runtime opcionais para o Models Maximum e gera/importa planos de validacao. Codigo em `MainWindow.Companion.cs` e `Systems/Companion/*`.
- A aba **Distribuição exata** exporta um candidato sem nova perda de qualidade, via `Systems/Optimizer/ExactDistributionRunner.cs`.
- A aba **Compressão completa** tem entrada/saída/engine/ferramentas e perfil próprios: staging autenticado -> Models Safe -> VTF/PNG/WAV -> distribuição exata obrigatória. Usa `MainWindow.FullCompression.cs` e `Systems/Optimizer/FullCompression*.cs`, sem herdar o perfil das outras abas. Ver `docs/full-compression.md` para limites e instruções. Não confundir este fluxo com a promoção dos adaptadores estruturais do laboratório, nem chamar a redução nativa de byte-exact.
- A aba **Pipeline** encadeia Models -> Compress e pode executar a distribuicao exata no final (opcao persistida, padrao desativado). Falha/cancelamento de qualquer etapa impede sucesso geral; o intermediario nativo permanece separado.

## Linha de desenvolvimento

Desde 2026-11-06 existe uma unica linha: `publish/2026-03` (a `main` local aponta para o mesmo commit). Ela junta o trabalho de setembro (Universal VTF, distribuicao exata, Compressao completa, interface Bancada) com o que estava apenas na `main` (Models Maximum v2, Magick+, Companion). Tags `backup/pre-unify-20261106-*` guardam o estado anterior das duas linhas. Branches `spike/*` e `feature/*` antigos continuam separados e nao fazem parte do produto ate serem portados.

## Pipeline de build/release correto

### Release padrao do app principal

Use **este** script na raiz:

```powershell
.\build_release_wpf.ps1
```

Esse e o fluxo correto porque ele:

1. faz clean do build `win-x64`
2. chama `pyinstaller/package_wpf_tools.ps1`
3. gera/valida o ZIP embutido `SourceAddonOptimizer.win-x64.zip`
4. roda `dotnet publish` do WPF
5. verifica que o `.exe` final existe

### Quando o worker Python foi alterado

Se voce alterou codigo em:

- `worker/`
- `build_optimized_addon.py`
- `batch_*`
- outros scripts Python usados pelo worker

garanta que o worker empacotado foi atualizado antes do release final. `package_wpf_tools.ps1` reconstrui quando o executavel esta ausente ou anterior aos fontes rastreados, incluindo `exact_distribution/*.py`, pins e todos os arquivos vendor autenticados. Se houver timestamps restaurados ou suspeita de build stale, refaca o PyInstaller antes do release.

Distribuicao exata: `worker.spec` deve incluir fontes Python reais, templates Lua e `vendor-pins.json` como data no `_internal`, alem dos imports. Os hashes de produtor e `verify_vendor()` leem os arquivos a partir de `__file__`; apenas bytecode no PYZ nao basta. Nao modificar os vendors congelados. ToolVersion 0.1.19 (linha unificada); o instalador `SourceAddonOptimizerPackageInstaller` extrai por hash de conteudo, entao versoes anteriores continuam disponiveis para rollback. A versao precisa ser a mesma em `ToolPaths.cs`, `maximum_optimizer/tool_package_manifest.py`, `maximum_optimizer/silhouette_native.py`, `pyinstaller/New-SourceAddonOptimizerManifest.ps1` e nos testes de contrato. Ver `docs/exact-distribution.md`.

### Nao usar como release principal

Nao trate estes scripts como release oficial do produto principal:

- `build_release.cmd`
- `pyinstaller/build.ps1`
- `GmodAddonCompressor-master/build.bat`

Eles servem para builds parciais/legados e podem induzir erro de fluxo.

## Scripts importantes

- `build_release_wpf.ps1`: script principal e correto para gerar o `.exe` final do WPF
- `pyinstaller/package_wpf_tools.ps1`: empacota worker + Crowbar em `Resources/SourceAddonOptimizer.win-x64.zip`
- `pyinstaller/build.ps1`: build PyInstaller do worker e do frontend Python legado
- `build_release.cmd`: wrapper legado para build do frontend Python antigo
- `GmodAddonCompressor-master/build.bat`: publish simples do WPF, sem garantir o empacotamento correto dos tools

## Regras para implementar novas features

- Novas features de UI vao no WPF.
- Novas abas vao no WPF.
- Ajustes de UX, feedback, log, progresso e size report vao no WPF.
- Nova logica pesada, batch ou processamento em lote pode ir no backend Python e ser exposta ao WPF via runner.
- Se criar uma nova capacidade no worker, crie tambem a integracao correspondente no WPF em `Systems/...`.
- Nao reescrever o app sem necessidade. Preferir mudancas pequenas e seguras.

## Regras para evitar regressao

- Nao mexer no frontend Python antigo para features novas, salvo pedido explicito.
- Nao quebrar as abas existentes do WPF: Compressão completa, Modelos, Mídia e recursos, Descompactar, Juntar addons, Mapas, Distribuição exata, Pipeline e Validação no jogo.
- Preservar integracao com settings, logs e size reports existentes.
- Preservar extracao de tools em `%LOCALAPPDATA%`; nao voltar a depender da pasta do exe para runtime.
- Sempre preferir integracao incremental no WPF em vez de reestruturar arquitetura.

## Como validar manualmente mudancas

### Validacao minima para features WPF

1. Gerar release com `.\build_release_wpf.ps1`
2. Abrir o exe publicado:

```text
GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe
```

3. Confirmar que a aba alterada abre no exe publicado, nao apenas em build local
4. Confirmar que logs/progresso/status atualizam pela UI
5. Confirmar que as outras abas principais ainda abrem normalmente

### Pasta de teste util para unpack

Usar:

```text
C:\Users\luisf\Music\teste\addonsparateste
```

Estado conhecido:

- contem 3 arquivos `.gma`
- contem 3 arquivos `.bin`

### Validacao da aba Descompactar addons

1. Abrir a aba `Descompactar addons` no WPF publicado
2. Usar a pasta `addonsparateste`
3. Apontar `gmad.exe` do Garry's Mod
4. Rodar `Scan`
5. Rodar `Extract`
6. Confirmar que o lote continua mesmo se algum item falhar

## Armadilhas e conhecimentos importantes ja descobertos

- A UI principal **nao** e `gui/`; ela e o WPF em `GmodAddonCompressor-master/GmodAddonCompressor/`.
- Ja houve confusao em sessao anterior por implementar/explicar coisas no frontend errado. Evitar isso.
- O app final publicado depende da extracao correta dos tools em `%LOCALAPPDATA%\GmodAddonOptimizer\tools\...`.
- O work/log runtime do WPF fica em `%LOCALAPPDATA%\GmodAddonOptimizer\`.
- A aba `Descompactar addons` ja existe no WPF e usa o worker `unpack`.
- `package_wpf_tools.ps1` foi endurecido para evitar ZIP stale/quebrado:
  - cria ZIP temporario
  - valida entradas obrigatorias
  - falha explicitamente em caso de lock/erro
  - nao deve imprimir `OK` com artefato inconsistente
- Ja foi observado problema de recurso embutido stale no build `win-x64`; por isso o release correto faz clean especifico antes do publish.
- Mudancas devem ser minimas e seguras. Nao reescrever o app inteiro para adicionar feature pequena.

## Convencoes para futuras alteracoes

- Preferir nomes/coisas novas no contexto de `GmodAddonOptimizer`, mesmo que o projeto ainda tenha nomes historicos `GmodAddonCompressor` em caminhos/namespaces.
- Documentar novas etapas de build/release no proprio repo se o fluxo mudar.
- Se adicionar nova feature no worker, registrar:
  - comando/entrypoint
  - runner WPF correspondente
  - como validar manualmente
- Se alterar release, atualizar primeiro `build_release_wpf.ps1` e depois este arquivo, para manter a documentacao fiel ao codigo.

## Resumo rapido para agentes futuros

Se voce acabou de entrar neste repositorio:

1. Trabalhe no **WPF** em `GmodAddonCompressor-master/GmodAddonCompressor/`
2. Trate `gui/` como **legado**
3. Use `worker/worker_main.py` e os scripts Python da raiz como backend
4. Gere release com `.\build_release_wpf.ps1`
5. Valide no exe publicado, nao so por build/backend
