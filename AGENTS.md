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
   - `addonmerge` -> `batch_merge_addons.main(...)` (Juntar addons)
   - `exactpack` -> `exact_distribution.cli.main(...)` (distribuicao exata experimental)
   - `texscreen` -> `texlab.screen_compress.main(...)` (modo Tela de VTF)
   - `modscreen` -> `model_screen.cli.main(...)` (modo Tela de Models)
   - `pnglossless` -> `png_lossless.cli.main(...)` (PNG sem perda)
   - `mapscan` / `mapbuild` / `mappack` -> `batch_scan_map_bsp` / `batch_build_map_bsp` (aba Mapas: separar o pakfile de cada `.bsp`, gravar o `.bsp` comprimido, gerar o `.gma`)
   - fluxo padrao -> `build_optimized_addon.main(...)`
5. O WPF consome stdout/progresso/logs e atualiza a UI.

Pontos importantes:

- **Navegacao (desde 2026-11-08)**: o WPF abre no assistente **Otimizar** e tem um rail lateral Material 3 (paleta M3 escura em `App.xaml`) com 4 secoes: **Otimizar** (assistente), **Avançado** (sub-abas Modelos e Texturas e sons [antiga "Mídia e recursos"]), **Ferramentas** (Descompactar, Juntar addons, Mapas, Distribuição exata) e **Testar no jogo** (Companion). As abas antigas continuam no `TabControl` `MainTabs` (cabecalhos ocultos pelo estilo `ShellTabControl`); o rail e as sub-abas so trocam `MainTabs.SelectedItem` (`MainWindow.Wizard.cs`). Pipeline e Compressão completa sairam do menu (Luis, 2026-11-08): o Pipeline virou o perfil Personalizado do Otimizar e a Compressão completa (Models Safe com Blender + distribuicao exata obrigatoria) ficou obsoleta; as abas e o codigo continuam no projeto, sem entrada na navegacao.
- **Nomes e telas (2026-11-08, pedido do Luis)**: na interface o modo "Tela" (modelos e texturas) se chama **Padrão** e o antigo modo "Padrão/Padrao" de texturas (CompressModeIndex 0) se chama **Clássico**; nos textos técnicos deste arquivo "Tela" continua sendo o mesmo modo (`screen`, `modscreen`, `texscreen` nao mudaram). Todas as telas (Avançado › Modelos e Texturas e sons, Ferramentas, Testar no jogo) seguem o padrao do assistente: titulo + subtitulo, cartoes `M3Card`, secoes recolhiveis `M3Expander`, chips `ChoiceChip`, rodape com a acao principal (`FilledButton`), estilos `PageTitle`/`PageSubtitle`/`SectionHeader`/`FieldLabel`/`FieldHint`/`StatValue` em `App.xaml`. Em Modelos e Texturas a escolha principal e uma so, "Quanto reduzir" (chips ligados a `ModelsLevel`/`TexturesLevel` no contexto: escolher um nivel volta para o modo Padrao e aplica o perfil); Normal/Fidelidade/Maximum (Blender) ficam recolhidos em "Outros métodos, com Blender" e Clássico/Magick/Maximum/Magick+ em "Outros métodos" (abrem sozinhos e mostram "Usando X. Para voltar ao normal, escolha um nível acima." quando um deles esta ativo). Textos do usuario em PT-BR; o teste `tests/wpf_companion_contract` ainda confere alguns textos internos em ingles do Companion (mostrados so em "Detalhes da validação").
- O assistente **Otimizar** (Addon → Objetivo → Otimizar; `MainWindow.Wizard.cs`, `DataContexts/OptimizeWizardContext.cs`, `Systems/Optimizer/WizardProfiles.cs`, `Systems/Reporting/AddonInventory.cs`) tem objetivos Addon completo / Só modelos / Só texturas e sons e 4 perfis: **Sem perda** (Models Tela `exact`: modelos byte a byte + empacotamento exato; texscreen `exact`: so BC3->BC1 exato, sem reduzir e sem caminho regular; sons intocados), **Custo-benefício** (padrao: Models Tela `stream` + texscreen `stream` (Forte + texturas em stream a 34 dB; desde 2026-11-09 o metodo principal, pacote LVS 1375 -> 68 MiB) + WAV Adaptativo conservador com referencia 22050 Hz mono PCM16; referencia 44100 estereo deixa sons maiores) e **Máximo** (Models `stream` + texscreen `stream_max`: o mesmo stream a 31 dB + o mesmo WAV Adaptativo conservador do Custo-benefício, padrao de sons de todos os perfis por escolha do Luis) e **Personalizado** (chips na propria etapa Objetivo: Modelos Não mexer/Sem perda/Equilibrado/Forte/Forte + espelho/Stream, Texturas Não mexer/Sem perda/Equilibrado/Forte/Máximo/Stream (padrao)/Stream máximo, Sons Não mexer/Adaptativo/Manual com taxa, canais e formato, Recomprimir PNG sem perda (marcado por padrao) e Minificar Lua; `WizardProfiles.ForCustom`). Todos os perfis recomprimem os PNG sem perda (worker `pnglossless`, mesmos pixels; ver "PNG sem perda" abaixo). O perfil e aplicado as configuracoes compartilhadas so durante a execucao (`SaveSettings` nao grava nesse intervalo) e restaurado no fim; a execucao reaproveita o worker `modscreen` e `StartCompressProcessAsync` como o Pipeline. "Só texturas e sons" copia o addon para a pasta de saida antes de comprimir (o Compress normal trabalha no proprio addon). Saida: `<addon><sufixo de Modelos>` ao lado do addon. Recentes em `%LOCALAPPDATA%\GmodAddonOptimizer\wizard-recent.json`. Antes de comprimir, `Systems/Optimizer/WorkshopReferenceCleaner.cs` comenta na copia as linhas que so chamam `resource.AddWorkshop("id")` (a versao otimizada republicada nao pode fazer os jogadores de um servidor baixarem tambem o original, ex. LVS 3027256228 = 1,3 GB); chamadas no meio de outro codigo so geram aviso no log. No assistente, os VTF que o Tela nao trata (NOMIP, fora de DXT) ficam intocados (`CompressPipelineOptions.KeepScreenFallbacks`, ligado so durante o assistente): o resultado depende so do perfil, nunca das opcoes do Classico das abas avancadas, onde esses arquivos continuam indo para o caminho Classico. Os perfis `exact`/`max`/`stream` tambem aparecem no modo Avançado (Models Tela indice 4 "Sem perda"; Perfil Tela indices 2 "Máximo", 3 "Sem perda", 4 "Stream" e 5 "Stream máximo").
- A aba **Descompactar addons** ja existe no WPF e usa o worker `unpack`.
- A aba **Models** usa o worker empacotado para o pipeline de otimizar/recompilar modelos. Modo **Tela** (índice 3, worker `modscreen` em `model_screen/`): simplifica direto nos binários MDL/VVD/VTX, sem Blender/Crowbar/studiomdl; perfis `balanced` (Equilibrado, quase idêntico), `strong` (Forte, modelos ~50% menores, VVD sem tangentes) e `strong_mirror` (Forte + espelho: mesmos limites do Forte com o motor `model_screen/remesh.py` — cada peça simplificada inteira com costuras do meshoptimizer — e `model_screen/mirror.py` — o lado direito de modelos simétricos é desenhado por um strip extra que reusa os vértices do lado esquerdo através de ossos-espelho com `poseToBone` refletido; só espelha onde geometria e textura batem, todas as texturas do material contam, inclusive `$refracttinttexture`; procura materiais também nos addons vizinhos da pasta, ex. `lvs_framework`; LVS models −61% com aparência equivalente ao Forte; ver `docs/modlab-modelos-2026-11-06.md`); Forte + espelho é o padrão da interface (índice 2) e do `SourceAddonOptimizerCommandBuilder` (o WPF sempre passa `--profile`); perfil `stream` (índice 3, "Stream", sem o rótulo de experimental desde 2026-11-09; pacote `model_stream/`): models grandes rígidos guardam a geometria ORIGINAL comprimida (GMOS em `data_static/gmo/`) e `lua/autorun/client/gmo_stream_<addon>.lua` (runtime.lua + manifesto) a desenha com IMeshes no lugar do model, com probe de luz por entidade (`models/gmo_stream/probe_v1.mdl` + material com `$envmap env_cubemap`/bump/phong) e normais nos 2 bytes do motor; o .mdl fica com malha grossa ~2% (sombra, distância, reserva); rodas e models pequenos usam Forte + espelho; LVS: models 213 → ~48 MiB com qualidade igual ou melhor (ver `docs/modelos-stream-2026-11-08.md`); usa `tools/meshopt/meshopt_simplify.dll` (meshoptimizer v1.2 de `third_party/`); a passagem de costuras e os componentes rodam em Rust (`gmo_seams`/`gmo_components` em `tools/native/gmo_native.dll`, bit-idênticos ao Python que fica como referência/fallback; a DLL emula a ordem dos `set` do CPython 3.11). Models de primeira pessoa (`texlab/firstperson.py`: `SWEP.ViewModel` da Lua, `v_*`/`c_*`/mãos e o resto de `models/weapons/` que não é `w_*`) usam os limites da câmera do viewmodel (`simplify.first_person`, ~5x menos tolerância) e podem ir para o stream: o motor desenha o viewmodel sozinho, então o runtime (`REVISION` 3) liga no hook `PreDrawViewModel` um `RenderOverride` que desenha só o stream (nunca `DrawModel`, que dobrava os hooks de mãos), decodifica antes os viewmodels das armas carregadas e respeita `MaterialOverride`/`ModelMaterialOverride`/`MaterialOverrideByIndex` e a cor de quem chama. Models cuja aparência o jogo faz em tempo de execução ficam como o autor fez (nem stream, nem simplificação, só o empacotamento que mantém a imagem; `model_screen/runtime_materials.py`, regra geral lida dos arquivos, sem nome de addon): material com textura de render target (`_rt_...`, imagem da mira/câmera) ou superfície transparente/aditiva com proxy de material definido no Lua do addon ou vizinhos (`matproxy.Add`: vidro de luneta trocado pela imagem da mira, lente de red dot, contadores digitais). Na bancada pega 34 models de arma e nada mais (janelas Refract do LVS, pintura por proxy do simfphys e camuflagens são opacas ou sem Lua). Models com LODs (fixups no VVD, vários LODs no VTX) viram só o LOD0 (`binary.flatten_lods`) antes de simplificar, exceto no perfil `exact`; models só de animação (sem triângulos) são pulados, não falham. O stream guarda todos os candidatos de material (`a|b`) quando nenhum existe no addon ou vizinhos, e o runtime usa o primeiro que o jogo tem (pacotes de textura compartilhados). Marcas de bala em models em stream (runtime `REVISION` 4, 2026-11-11): o jogo guarda as marcas (decals) na malha de reserva do `.mdl`, que o stream nao desenha, entao elas sao feitas na geometria do stream. O addon otimizado leva `lua/autorun/server/gmo_stream_sv_<slug>.lua` (`model_stream/server.lua` + a lista dos models em stream; carregado uma vez por servidor): no gancho `PostEntityFireBullets` ele manda aos clientes que enxergam o ponto uma mensagem nao confiavel `gmo_stream_decal` (entidade, ponto, direcao, tipo de superficie), no maximo 12 por segundo por model atingido e 60 no total (`gmo_stream_marks 0` desliga no servidor). O cliente (`GMOS.AddDecal`) guarda o caminho da bala no espaco de cada peca desenhada, le de novo os triangulos do model do arquivo de stream na primeira marca (poucos ms por quadro; nada fica na memoria enquanto ninguem atira, e os triangulos saem 2 minutos depois da ultima marca), acha a superficie com um raio que percorre uma grade de celulas de 6 unidades, 8 unidades antes e depois do ponto avisado, recorta os triangulos sob um quadrado do tamanho do decal do jogo e desenha a malha por cima do stream com o material de decal do proprio jogo (`$modelmaterial`); ate 24 marcas por entidade e 256 no total, `gmo_stream_decals 0` desliga no cliente; impactos que o Lua do cliente pede com `util.Effect("Impact")` entram pelo mesmo caminho. So marcas de bala (sangue, explosoes e tinta nao). O resto do que a bala le (colisao `.phy`, caixas de acerto, superficie, bodygroups de dano do LVS) e identico ao original, medido tiro a tiro; a bala atravessar o vidro dos carros do LVS e acertar la dentro como metal e o comportamento do LVS original. Validar: Otimizar > addon com veiculos > no jogo, atirar na lataria de um veiculo em stream e ver os buracos de bala; em servidor dedicado, conferir o mesmo com um cliente conectado (`docs/teste-workshop-2026-11-11.md`; testes em `tests/test_model_stream.py`). Cada tira de triangulos tem de caber numa malha do jogo (65535 vertices): `mesh.Begin` com mais do que isso da erro e o jogo para de responder (um carro do Glide da bancada, 484 mil triangulos, teste de servidor de 2026-11-10). O encoder divide o pedaco cuja tira passa de `MAX_STRIP` (65534; triangulos pouco ligados viram ate 5 indices cada) e o runtime `REVISION` 3 corta em posicao par qualquer tira maior, entao streams gravados antes tambem funcionam com o runtime novo; `tests/test_model_stream.py` roda o `runtime.lua` em LuaJIT com o limite do jogo simulado. Ver `docs/bancada-2026-11-08.md`. Modos de recompilação: `normal`, `fidelity` e `maximum` (Models Maximum adaptive v2 em `maximum_optimizer/`, com meshoptimizer nativo via ctypes e perfil `maximum_optimizer/profiles/maximum-adaptive-v2.json`). O TRI_STRIP de VTX (`--vtx-tristrips`) roda no empacotamento de todos os modos, um arquivo por processo (mesmos bytes e relatório da versão sequencial). Também no empacotamento, por padrão, `vvd_engine_exact.py` troca normais/tangentes do VVD por valores "redondos" dentro do mesmo degrau do `PackNormal_UBYTE4` do motor: imagem idêntica no jogo, download da Workshop menor (`--no-vvd-exact-normals` desliga; ver `docs/modlab-modelos-2026-11-06.md`).
- A aba **Mídia e recursos** (Compress) tem os modos Padrao, Magick, Maximum (Universal VTF, perfil Balanced), Magick+ (Magick + repack exato BC3->BC1 quando o alpha comprovadamente nao e usado) e Tela (indice 4: worker `texscreen` em `texlab/screen_compress.py`, reducao por textura pela densidade na tela, mips menores originais exatos, alpha preservado; textura gravada com um nivel so leva NOLOD, que o jogo exige e poria sozinho imprimindo um aviso no console a cada carga; "Perfil Tela" Equilibrado (`q50`, padrao) ou Forte (`strong`: limiar 0,12, ate 8x por energia so se a textura nunca aparece esticada de perto, texturas `$detail` no maximo 4x, escritas pelo nome do arquivo (`TEXT_NAMES`) e texturas pequenas (2x cabe em 100 KB) em 2x; LVS 136 → 75 MiB, textos legiveis; em addon com models em stream a densidade e medida na geometria do stream via `model_stream/geometry.py`, nao na malha grossa). Texscreen v8: texturas usadas por models de primeira pessoa sao medidas na camera do viewmodel (FOV 62, distancia ao olho), sem 2x forcado, limiar do Equilibrado e no maximo 2x por energia; DXT sem mipmaps/NOMIP ("single") e RGB(A) sem compressao ("convert", vira DXT1/DXT5) usadas por models sao reconstruidas; NOMIP explicito e nomes de painel (`cockpit|dash|interior|display|radio|screen`) param no limite de escrita; texturas em `materials/models/` sem uso por nenhum model (do addon ou vizinhos) tem a reducao padrao de sem uso (2x), as outras sem uso (HUD, miras, sprites) continuam como antes; ver `docs/texlab-vtf-screen-aware-2026-11-06.md` e `docs/modelos-stream-2026-11-08.md`). Os trechos quentes do Tela rodam em Rust (`native/gmo_native` -> `tools/native/gmo_native.dll`, carregado por `texlab/native.py`, bit-identico ao numpy que fica como referencia/fallback; `GMO_NATIVE=0` desliga; ver `tools/native/README.md`). O numero de processos de modelos e da analise de texturas e automatico (`texlab/jobs.py`: CPUs logicas menos duas, limitado pela memoria livre e pelo que resta do limite de reserva do sistema; antes parava em 8); o resultado nao depende dele. Todo pool de processos do worker e criado por `texlab.jobs.process_pool`, que deixa a biblioteca numerica dos processos auxiliares em uma thread: o OpenBLAS do numpy reserva ~32 MB por thread em cada processo (660 MB por processo numa maquina de 20 threads, 12 GB com 18 processos, nunca usados), o que levou o sistema ao limite de memoria reservada e fez o simfphys falhar no assistente em 2026-11-11; com uma thread sao 49 MB por processo, mesmos bytes. A preparacao das imagens reduzidas roda nesses processos (`_prepare_one`; uma textura que falha num processo auxiliar e refeita sozinha no processo principal) e as chamadas do VTFCmd sao montadas pelo custo de cada imagem (`_encode_chunks`). A tela do assistente mostra o que a etapa de texturas esta fazendo (`ICompressStatus`, `CompressAddonSystem.e_StatusChanged`): antes ficava no ultimo `.wav` enquanto as texturas rodavam. O worker so pode importar os modulos numpy de `texlab` (nunca `render`, `evaluate`, `downscale`, `build_opt`, que usam torch/numba). WAV: "Perfil WAV" Manual (padrao) ou Adaptativo conservador (`Systems/Audio/*`, tambem na aba Mapas): usa as opcoes manuais como referencia e so troca por PCM16/MS-ADPCM 22050/44100 quando fica menor sem piorar SDR/SNR/LSD contra o original; loops (`cue `/`smpl`/`adtl`) preservados. No Adaptativo os sons vao em lote (`WAVEdit` como `ICompressBatchSelective`): os passos de ffmpeg de muitos sons (linha de base, candidatos, decodificacoes) entram no mesmo processo (`Systems/Audio/BatchedFfmpegAdaptiveAudioBackend.cs`). Abrir o ffmpeg custa ~150 ms e cada som abria ~8 vezes: 2 s de CPU por som, 42% do tempo da bancada. Cada saida leva os argumentos do comando de um som so (`FFMpegSystem.Build*`), entao os bytes sao os mesmos (os 3.150 sons da bancada identicos; sons 832 -> ~105 s); um processo que falha e refeito passo a passo com os comandos de um som. O Manual, MP3 e OGG continuam um arquivo por vez, ate 8 em paralelo. Candidatos reamostrados usam soxr com formato interno double (`internal_sample_fmt=dblp`, `FFMpegSystem.BuildAdaptiveResampleFilter`): com E/S int16 o libsoxr aplica dither TPDF próprio com semente de relógio e a saída variava ±2 LSB por execução; agora é byte a byte reproduzível (candidatos sem troca de taxa mantêm o filtro e os bytes antigos). Relatorio em `%LOCALAPPDATA%\GmodAddonOptimizer\work\audio`, nunca dentro do addon. Ver `docs/benchmarks/2026-08-23-adaptive-conservative-audio.md`.
- **Texturas Stream** (metodo principal desde 2026-11-09; texscreen perfis `stream` 34 dB e `stream_max` 31 dB (`STREAM_TARGETS`), Perfil Tela indices 4 e 5, chips "Stream"/"Stream máximo" em Texturas e no Personalizado, perfis Custo-benefício e Máximo do assistente; pacote `texture_stream/`): roda o Forte e depois troca cada VTF DXT1/DXT3/DXT5 ou sem compressao (desde 2026-11-11 a textura que o Forte reduz e que vira stream nao passa mais pelo DXT do VTFCmd: a imagem reduzida vai direto para o codificador do stream, com o cabecalho do arquivo que seria gravado, `screen_compress._stream_direct` + `texture_stream.convert(direct=...)` + `texlab.vtf.planned_reduced_vtf`; o VTFCmd so roda para o que continua VTF e nos perfis sem stream, que nao mudaram; etapa de texturas da bancada 388 -> 237 s, mapas de normal mais proximos do original, cor equivalente, bancada 728,3 -> 726,3 MiB; ver `docs/velocidade-2026-11-11.md`) (RGBA/BGRA/ABGR/RGB/BGR/BGRX 8 bits, que voltam no mesmo formato com alvo minimo de 40 dB; DXT3 volta DXT5) de quadro unico com lado >= 64 por um blob GMOT (`codec.py`: wavelet 5/3, YCoCg, normal map como X/Y normalizado, LZMA por pedaco, prévia em 512 no primeiro pedaco) em `data_static/gmot/<slug>/<i>.dat`, no maior passo que mantem 34 dB (+1,2 de margem) contra a entrada (`choose.py`; alpha so quando um VMT o le, alvo pelo peso no material; a transformada, a reconstrucao, o erro e a media de cada passo da busca, a quantizacao e os bytes das bandas e os planos de cor rodam em Rust, `gmo_ts_*` em `tstream.rs`, bit-identicos ao numpy, que fica como referencia e fallback; quando as duas resolucoes de croma mantem o alvo no mesmo passo fica a reduzida sem comprimir as duas para comparar, e o codificador LZMA de cada pedaco recebe um dicionario do tamanho do pedaco, com o mesmo fluxo de bytes e o cabecalho de 16 MB de sempre; na segunda rodada a conversao ficou 1,8x mais rapida com os mesmos blobs (medidas em seguida: ARC9 233 -> 132 s, m9k 87 -> 47 s; antes da primeira rodada o ARC9 levava 552 s); a compressao LZMA de cada pedaco e nivel 9 com `pb=0` (`codec.LZMA_SETTINGS`, escolha do Luis em 2026-11-10: 2,5x mais rapida que o 9 extremo de antes, mesma imagem, arquivos de stream 3,5% maiores, bancada 725,4 -> 728,3 MiB, conversao do ARC9 -29%; ver `docs/velocidade-2026-11-10.md`) com preenchimento de ruido nos 2 niveis finos, e deixa um VTF de 16 px no caminho original. `lua/autorun/!gmot_<slug>.lua` = manifesto JSON + `decoder.lua` + `runtime.lua` (nucleo compartilhado, o mais novo vale: `CORE_VERSION` 3 em `texture_stream/__init__.py`, subir a cada mudanca do runtime ou do decoder): no jogo decodifica para DXT (mesma VRAM), escreve GMA em `data/gmot/`, `game.MountGMA` e `IMaterial:SetTexture` para `materials/gmot/<slug>/<i>.vtf` (GMA montado nao substitui caminho existente). O manifesto liga cada textura a todo material cujo VMT a cita, por qualquer parametro (`texlab.materials.Material.all_textures()`: os de `TEXTURE_PARAMS` e os outros, como `$texture2`, `$refracttinttexture`, `$emissiveblendbasetexture`, inclusive dentro de blocos e com chave condicional; textura citada por parametro que a analise nao modela mantem o alfa inteiro), e os nomes sao lidos como o jogo le (`clean_path`/`MaterialResolver.texture`: barra repetida conta como uma, a extensao escrita no nome e descartada). O runtime so troca onde o jogo de fato tem a textura naquele parametro, e ainda pergunta a cada material que aparece na tela quais texturas ele tem (`look_inside`, poucos por quadro), o que cobre materiais de outros addons. Sem isso a Winchester 87 do M9K, cujo VMT escreve `models\\weapons\...` (o jogo chama a textura de `models//weapons/...`), ficava com a miniatura de 16 px, e na bancada 23 texturas nunca eram aplicadas e 75 so em parte (teste na Workshop de 2026-11-11, `docs/teste-workshop-2026-11-11.md`). Todas as texturas DXT entram (so as de model deixariam a bancada em 236 MiB); classe no manifesto: 0 model (arma/viewmodel/maos/inventario por quadro, entidades com sub-materiais, props estaticos e materiais do BSP), 1 efeito (pastas `effects/`, `particle(s)/`, `sprites/`, `decals/`..., shaders Sprite/SpriteCard/Refract, citados em `.pcf`; pre-carga 3 s depois do mapa; ceu `sv_skyname` e materiais das entidades do BSP pedidos quando o mapa carrega), 2 citada no Lua, 3 resto (pre-carga depois de 20 s); o runtime intercepta `Material`, `CreateMaterial`, `IMaterial:SetTexture`, `Entity:SetMaterial/SetSubMaterial` (so nomeiam: fila baixa, ate a previa), `render.SetMaterial`, os `MaterialOverride`, `surface.SetMaterial/SetTexture` (desenham: frente da fila, resolucao completa), `Entity:SetModel`, `ClientsideModel`, `ProjectedTexture:SetTexture` (farois/lanternas: a lampada fica registrada e e atualizada quando a textura chega), `CLuaEmitter:Add`, `game.AddDecal`; toda entidade tambem tem os materiais de `GetMaterials()` conferidos (model de outro addon com textura deste) e `SetTexture` aceita ITexture copiada de outro material. Tamanhos: previa 512 -> metade 1024 (texturas >= 2048) -> completa; so passa da previa o que o jogador segura, entidades com mais de ~200 px na tela (sobe quando se aproxima) e materiais desenhados pelo Lua; os planos da previa (ate 3) comecam as etapas seguintes. Ordem: o que o jogador segura (50% do quadro) > tela (30%) > nomeado pelo Lua > pre-carga (10%), interrompida quando chega algo da tela; teto `gmot_budget` (6 ms); enquanto a tela espera, o que o jogo tem acima de 60 fps tambem e usado. Mostrar tambem e ritmado: `Material()`/`SetTexture` carregam o arquivo dentro da chamada (25-58 ms no pior caso), entao as trocas saem uma por vez dentro da fatia do quadro e as de fundo descansam 9x o custo da ultima (aplicar o cache inteiro de uma vez travava 1,5 s). Cache entre sessoes: `data/gmot/cache.txt` + GMAs ate `gmot_cache_mb` (512, 0 desliga; LRU), GMAs pequenos juntados em blocos de 64 MB no inicio, montados antes do primeiro quadro (2a sessao: armas prontas em < 0,5 s); o runtime guarda a lista de arquivos que cada `game.MountGMA` devolve e nunca usa `file.Exists` no GAME (com dezenas de GMAs montados chegou a 4 ms por chamada). Slug = nome + hash do conteudo (pastas iguais de addons diferentes nunca colidem). `gmot_prefetch 0` desliga a pre-carga e `gmot_prefetch_mb` (256) limita quanto ela decodifica por sessao; `gmot_status` mostra estado, filas e pior quadro. Testes `tests/test_texture_stream.py` rodam `decoder.lua` e o runtime inteiro em LuaJIT via `lupa` com globais estritas (saida identica byte a byte ao espelho `codec.lua_vtf`, que faz a sintese na mesma ordem do Lua; segunda sessao saindo do cache; ritmo das trocas, farois, ceu/mapa e limite da pre-carga). O tour tem tres rodadas de referencia em `out/` (`tour_base` sem stream de texturas, `tour_s9b` primeira sessao, `tour_s9d` segunda) e a linha de cada fase traz `focus`: com a janela do jogo sem foco o jogo cai para ~20 fps e a rodada nao vale. Bancada no jogo: `C:\GModLab\bench\texstream\tour.py` (21 addons, armas, carros), `tsmp.py` (servidor dedicado + conteudo do `gmad.exe`, uma arma) e `tsmp_full.py` (servidor dedicado com a bancada inteira empacotada pelo `gmad.exe`: armas sacadas e disparadas, veiculos LVS/Glide/simfphys criados pelo servidor, PNG; ver `docs/teste-servidor-2026-11-10.md`). Ver `docs/texturas-stream-2026-11-09.md`.
- **Arquivos de stream e o `ignore` do `addon.json`** (`texlab/packing.py`, usado por `model_stream` e `texture_stream`): o gmad tira do pacote todo arquivo que casa com a lista `ignore` do `addon.json` do proprio addon (`*` atravessa pastas). Um addon com `"ignore": ["*.txt"]` (ARC9 Gunsmith Arms Source) era publicado sem nenhum arquivo de stream: no jogo, texturas de 16 px e malha grossa (teste multiplayer de 2026-11-09). Por isso os arquivos de `data_static/gmo/` e `data_static/gmot/<slug>/` usam a primeira extensao de `.dat`, `.txt`, `.json`, `.xml`, `.csv` (tipos que o gmad aceita em `data_static/`) que a lista do addon nao tira; quando a lista tira todas, o worker imprime um `[WARN]` pedindo para remover o padrao antes de publicar. Os runtimes leem o caminho do manifesto, entao addons convertidos antes (`.txt`) continuam funcionando; um addon convertido antes disso cujo `addon.json` ignora `*.txt` precisa ser otimizado de novo.
- **Juntar addons** (Ferramentas; worker `addonmerge` -> `batch_merge_addons.main(...)`, runner `Systems/Merge/AddonMergeRunner.cs`, tela `MainWindow.AddonMerge.cs`): junta as pastas de addon de uma pasta num addon so (padrao: `<pasta>_merged` dentro da propria pasta de origem; arquivo igual em dois addons vira um, arquivo diferente no mesmo caminho fica o do primeiro addon por nome). Regras desde 2026-11-09: o `addon.json` do resultado leva `"merged_from": [pastas de origem]`, que marca a pasta como resultado do Juntar (o gmad ignora a chave; o otimizador copia o `addon.json`, entao `<nome>_optimized` tambem fica marcado). So pastas com essa marca e nome comecando pelo nome do novo addon ficam fora da busca (antes bastava o nome: chamar o novo addon de `lvs` numa pasta com `lvs_framework`, `lvs_cars_pack`... achava 0 addons). A pasta de destino so e esvaziada se tiver a marca ou estiver vazia; senao o worker sai com erro 2 e a tela avisa antes de rodar (um novo addon com o nome de uma pasta de origem apagava essa pasta). O `addon.json` e gravado antes da copia, entao uma juncao interrompida pode ser refeita. A lista `ignore` e lida como o gmad le (`texlab.packing.ignored`: o padrao contra o caminho inteiro). A busca avisa quando duas pastas sao o mesmo addon (>= 50% dos caminhos iguais, ex. `<addon>` ao lado de `<addon>_optimized`). Para o otimizador, um addon juntado e as pastas de que ele veio nao sao vizinhos (`texlab.packing.copies_of_each_other`, usado por `texlab.density.sibling_roots` e `model_screen.cli._sibling_roots`): sem isso cada model era medido duas vezes. Testes: `tests/test_batch_merge_addons.py`. Validar: Ferramentas > Juntar addons > origem com pastas de addon > Procurar > Juntar, conferir o resumo e a pasta `<pasta>_merged`. Ver `docs/juntar-addons-2026-11-09.md`.
- **PNG sem perda** (desde 2026-11-10; worker `pnglossless` -> `png_lossless.cli.main(...)`, servico `Objects/LosslessPngEdit.cs` no sistema de Compress, ferramenta `tools/oxipng/oxipng.exe` 10.2.0 MIT empacotada em `_internal/`): recomprime cada `.png` (nivel 4; gAMA/cHRM/sRGB/iCCP/pHYs ficam, texto de editor/EXIF saem) e so troca o arquivo quando fica menor E decodifica nos mesmos pixels RGBA (conferido com Pillow, independente do oxipng). O oxipng pode guardar os mesmos pixels num tipo de cor menor (RGBA opaco vira RGB, ate 256 cores vira paleta, cinza vira cinza); so sao aceitos os formatos de `png_lossless.GAME_FORMATS`, os que o cliente do GMod leu com pixels identicos ao original RGBA (48 pares, 20 formatos, `ITexture:GetColor` + quadro capturado, 2026-11-10); um resultado em outro formato e refeito mantendo o formato de origem. PNG de 16 bits, animado ou que nao e PNG por dentro fica como esta. O worker nunca escreve dentro do arquivo do addon: prepara copias em `%LOCALAPPDATA%\GmodAddonOptimizer\work\pnglossless` e troca com `os.replace` (um original ligado por hard link nunca e alterado). A saida e a mesma byte a byte com qualquer numero de threads. Bancada juntada (2026-11-10, exe publicado): PNG 41,3 -> 23,0 MiB (2.193 de 2.195 arquivos, todos com os mesmos pixels), total 752,8 -> 725,4 MiB contando os `.xbox.vtx`, os outros 40.447 arquivos identicos byte a byte a rodada anterior; `tour.py` igual ao de antes. Ligado em todos os perfis do assistente (a linha de texturas termina em "· PNG sem perda"; no Personalizado, caixa "Recomprimir PNG sem perda", marcada por padrao; o resultado aparece em "Mostrar detalhes" como "PNG sem perda: a → b") e, em Avançado › Texturas e sons › Imagens PNG e JPG, pela caixa "PNG sem perda" (desligada por padrao: o tipo PNG marcado continua no Clássico, que reduz a imagem). `CompressAddonSystem` entrega a lista inteira aos servicos `ICompressBatch` de qualquer tipo (antes so VTF). O empacotamento de Models (`model_screen/cli.py`) tambem remove `*.xbox.vtx` junto com `*.dx80.vtx`/`*.sw.vtx`: e a variante do Xbox original, que o jogo no PC nunca le (so `.dx90.vtx`; m9k da bancada: 77 arquivos, 9,1 MiB). Testes: `tests/test_png_lossless.py`, `tests/test_model_screen.py`, `tests/wpf_optimizer_contract`. Validar: Otimizar > addon com PNG > rodar > "Mostrar detalhes" mostra a linha do PNG; abrir o jogo e conferir icones do menu de spawn e HUD. Ver `docs/arquivos-intocados-2026-11-10.md`.
- **Mapas** (Ferramentas; desde 2026-11-13; `MainWindow.Maps.cs` + `MainWindow.xaml.cs`, `Systems/Maps/MapProfiles.cs`, `MapStageOptimizationService.cs`, `MapBspBuildRunner.cs`; worker `mapscan`/`mapbuild`/`mappack`; `bsp_repack.py`): entrada `.gma`, `.bin` antigo (`.bin → .gma → pasta` pelo worker `unpack`, com `--plain-names` para a pasta ter o nome do addon e `addon.json` escrito do cabecalho do `.gma`) ou pasta; botao **Otimizar mapa** faz tudo: Preparar (separa o pakfile de cada `.bsp` v19/20 em `staging/<n>/pak_contents` + `stage_manifest.json`) → otimiza o conteudo com o perfil → `mapbuild` grava cada `.bsp` inteiro de novo (`bsp_repack.repack`: lumps em LZMA no cabecalho da Valve, exceto 0/35/40/43/44; entradas do pakfile em LZMA no formato das ferramentas da Valve, versao `09 26` e flags 0, o formato do `zipfile` do Python o GMod recusa; game lump com offsets absolutos corrigidos; `bsp_repack.verify` confere cada lump byte a byte e o pakfile na mesma ordem, senao o mapa fica como estava; CRC errado no zip original e game lumps ja comprimidos pela Valve sao aceitos) → arquivos fora do `.bsp` com o mesmo perfil → `mappack` gera `<pasta>.gma` com `gmad -warninvalid` (copia `maps/<mapa>.png` para `maps/thumb/`). Saida `<nome>_optimized\` + `<nome>_optimized.gma` ao lado do original; resultado visivel no quadro Andamento (antes = conteudo, para `.bin` o tamanho comprimido aparece ao lado). Perfis (`MapProfiles`): **Sem perda** (texscreen `exact`, sons intactos), **Custo-benefício** (`strong`, padrao) e **Máximo** (`max`), os dois com WAV Adaptativo conservador; todos com PNG sem perda e sem os `.dx80/.sw/.xbox/.360.vtx`; sem stream de texturas. Texturas que um mapa desenha no tamanho delas nunca mudam de tamanho (`texlab/size_bound.py`, usado pelo texscreen e pelo stream de texturas em qualquer addon com `maps/*.bsp`): `$basetexture` dos materiais dos lumps 43/44 (patches de cubemap do VBSP resolvidos), `infodecal`, `env_sprite`/`env_glow`; o jogo divide as coordenadas do mundo pelo tamanho da textura ao carregar o mapa (placa do `ttt_stadium` reduzida virou grade 2x2). Tambem `materials/skybox/` e `materials/maps/` intocados, recortes no maximo 2x. Bancada 145 mapas (worker, sem os sons): Sem perda 8,64 → 3,42 GiB (−60%), Custo-benefício 2,58 GiB (−70%), Máximo 2,46 GiB (−71,5%). O cliente do laboratorio tem o StormFox2 (clima/hora do dia): o brilho das capturas depende da hora da rodada, nao do arquivo; fixar a hora (`StormFox2.Time.Set`) e comparar a luz por `render.GetLightColor`. Testes `tests/test_map_compress.py`. Validar: Ferramentas › Mapas › um `.bin` antigo › Custo-benefício › Otimizar mapa; abrir o mapa da pasta `<nome>_optimized` no jogo e conferir placas/paredes sem repeticao, decalques e ceu. Ver `docs/mapas-2026-11-13.md`.
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
- Os runners do worker no WPF (modelos, mapas, juntar, descompactar) leem a saida em UTF-8: com a pagina de codigo padrao, pastas com acento ou cirilico viravam "Output addon:" quebrado e o assistente falhava

## Regras para implementar novas features

- Novas features de UI vao no WPF.
- Novas abas vao no WPF.
- Ajustes de UX, feedback, log, progresso e size report vao no WPF.
- Nova logica pesada, batch ou processamento em lote pode ir no backend Python e ser exposta ao WPF via runner.
- Se criar uma nova capacidade no worker, crie tambem a integracao correspondente no WPF em `Systems/...`.
- Nao reescrever o app sem necessidade. Preferir mudancas pequenas e seguras.

## Regras para evitar regressao

- Nao mexer no frontend Python antigo para features novas, salvo pedido explicito.
- Nao quebrar as abas existentes do WPF: o assistente Otimizar; em Avançado: Modelos e Texturas e sons (antiga Mídia e recursos); em Ferramentas: Descompactar, Juntar addons, Mapas e Distribuição exata; e Testar no jogo (Validação no jogo).
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
