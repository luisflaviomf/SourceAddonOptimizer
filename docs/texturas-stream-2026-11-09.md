# Texturas em stream (GMOT): texturas decodificadas pelo Lua no jogo (09/11/2026)

Pedido do Luis: texturas da bancada abaixo de 100 MB no disco, com qualidade boa. O DXT (4 ou 8 bits
por texel) é o piso do formato nativo; o Tela Forte já tirou o que dava de resolução (bancada v6:
837 MiB de VTF). Para ir além, a textura precisa ser guardada num formato menor e decodificada no
jogo. Laboratório: `C:\GModLab\bench\texstream` (README.md, scripts, capturas).

Desde a tarde de 09/11 o stream é o método principal: perfil **Custo-benefício** do assistente
(texscreen `stream`, 34 dB) e **Máximo** (`stream_max`, 31 dB); nas abas avançadas, "Stream" e
"Stream máximo" em Texturas › Quanto reduzir (Perfil Tela 4 e 5).

## Como funciona

1. **No otimizador** (`texture_stream/`, perfis `stream`/`stream_max` do worker `texscreen`): depois do
   Forte, cada VTF DXT1/DXT3/DXT5 ou sem compressão de quadro único com lado ≥ 64 vira:
   - um blob GMOT em `data_static/gmot/<slug>/<i>.dat` (`codec.py`; a extensão muda quando o
     `addon.json` do addon manda o empacotador ignorar `*.dat`, ver "Multiplayer e empacotamento"): wavelet 5/3, cor em YCoCg (croma
     em meia resolução quando fica menor), normal maps como vetores X/Y normalizados (Z reconstruído),
     quantização com zona morta, LZMA por pedaço (um por nível e banda: dá para parar em qualquer
     tamanho). O passo de quantização é o maior que mantém **34 dB** (31 no Stream máximo; +1,2 dB de
     margem para o DXT) contra o VTF de entrada (`choose.py`); o alpha só fica quando algum material o
     lê, com alvo pelo peso que ele tem na imagem (translúcido/selfillum 1, máscara de envmap pelo
     `$envmaptint`, phong 0,7...). Os dois níveis mais finos recebem **preenchimento de ruído**: por
     bloco de 8×8, a energia dos coeficientes zerados (3 bits); o decodificador devolve ruído dessa
     intensidade e o granulado volta.
   - um VTF de 16 px com mipmaps no caminho original (o que aparece no primeiro instante);
   - uma linha no manifesto de `lua/autorun/!gmot_<slug>.lua`: blob, materiais e parâmetros que usam
     a textura, models que a desenham e a "classe" (como o runtime descobre que ela é necessária).
   O slug junta o nome da pasta e um hash do conteúdo: dois addons diferentes com a mesma pasta de
   saída nunca dividem caminhos, e uma versão nova de um addon nunca usa o cache da antiga.
2. **No jogo** (`runtime.lua` + `decoder.lua`, embutidos no arquivo do addon; o núcleo mais novo entre
   os addons carregados vale para todos): quando algo precisa da textura, o Lua lê o blob, decodifica
   para o formato de antes (DXT, ou o mesmo sem compressão: mesma VRAM), escreve um GMA em
   `data/gmot/`, monta com `game.MountGMA` e aponta os materiais para `materials/gmot/<slug>/<i>.vtf`.

Fatos do GMod que definiram o desenho (provados no cliente do laboratório): material JPG/PNG fica sem
compressão na VRAM (5× mais); VTF escrito pelo Lua em `data/` só carrega o cabeçalho (magenta); GMA
montado de `data/` funciona (52 MB em ~75 ms) mas não substitui um caminho que um addon já tem, por
isso os nomes únicos + `IMaterial:SetTexture`.

### Como o runtime sabe o que decodificar e em que ordem

| Classe | Texturas | Como o runtime descobre |
|---|---|---|
| 0 model | usadas por um model do addon ou de um vizinho | arma ativa, viewmodel e mãos (com seus sub-materiais) e inventário a cada quadro; entidades (64 por quadro, com material e sub-materiais trocados pelo servidor); props estáticos e materiais do mapa lidos do BSP; `ClientsideModel`, `SetModel`; os materiais que a própria entidade lista (`GetMaterials`), mesmo quando o model não está no manifesto (model de outro addon com textura deste) |
| 1 efeito | `effects/`, `particle(s)/`, `sprites/`, `decals/`..., shaders Sprite/SpriteCard/Refract..., materiais citados em `.pcf` | pré-carga 3 s depois do mapa carregar (o motor desenha sem passar pelo Lua); antes disso, o que dá para saber: céu do mapa (`sv_skyname`), materiais citados nas entidades do BSP (cordas, sprites, overlays), partículas do Lua (`CLuaEmitter:Add`), decals (`game.AddDecal`) e faróis/lanternas (`ProjectedTexture:SetTexture`: a lâmpada fica registrada e é atualizada quando a textura chega) |
| 2 Lua | caminho citado no Lua do addon ou vizinhos (camuflagens, adesivos, menus) | `Material`, `CreateMaterial`, `IMaterial:SetTexture`, `Entity:SetMaterial/SetSubMaterial`, `surface.GetTextureID` só **nomeiam**: entram numa fila baixa, até a prévia; `render.SetMaterial`, os `MaterialOverride`, `surface.SetMaterial/SetTexture` **desenham**: vão para a frente, em resolução completa; pré-carga depois de 20 s |
| 3 resto | o que nada acima cita | as mesmas interceptações; pré-carga depois de 20 s |

Só com a classe 0 a bancada ficaria em 236 MiB (texturas citadas pelo Lua, efeitos e as sem uso
aparente somam 150 MiB de VTF); por isso tudo entra no stream.

Tamanhos, por textura grande (lado > 512): **prévia** em 512 (o pedaço 0 do blob) → **metade** (1024,
para texturas de 2048 ou mais: um quarto do trabalho da completa) → **completa**. Só passa da prévia o
que está grande na tela: o que o jogador segura, entidades com mais de ~200 px de diâmetro na tela
(uma textura de 2048 espalhada num model menor que ~256 px só é amostrada no nível de 512) e materiais
desenhados pelo Lua. Chegar perto de um carro sobe a resolução dele. Os planos da prévia ficam
guardados (até 3 texturas) e as etapas seguintes começam deles.

Ordem: o que o jogador segura (vai para a frente da fila e ganha 50% do quadro) > o resto da tela
(30%) > o que o Lua só nomeou > pré-carga (10%). O trabalho de baixo é interrompido no meio quando
chega algo da tela. Enquanto a tela espera, um jogo acima de 60 fps também cede o que tem de sobra
acima de 60 (PC rápido mostra antes e continua em 60 ou mais). Teto absoluto `gmot_budget` ms por
quadro (padrão 6).

Mostrar uma textura pronta também custa: `Material()` e `IMaterial:SetTexture` carregam o arquivo na
hora, dentro da chamada, e não dá para dividir (pior caso medido: 25 e 58 ms). Por isso as trocas
também passam pela fatia do quadro, uma chamada do motor por vez: as que a tela espera primeiro; as
que nada espera (nomeadas pelo Lua, pré-carga) descansam 9× o tempo que a última custou, ou seja, no
máximo 10% do tempo.

Pré-carga: no máximo `gmot_prefetch_mb` (padrão 256) de texturas decodificadas por sessão, das
menores para as maiores dentro de cada classe; com centenas de addons o resto espera algo nomear ou
mostrar. `gmot_prefetch 0` desliga. `gmot_status` lista o estado por addon, as filas, o que ficou de
fora da pré-carga e o pior quadro.

### Cache entre sessões

Os GMAs escritos ficam em `data/gmot/` e são listados em `data/gmot/cache.txt` (tamanho, último uso,
slugs, primeiro arquivo). No início: o que passar de `gmot_cache_mb` (padrão 512, 0 = decodificar
toda sessão) sai, do uso mais antigo para o mais novo; os GMAs pequenos que os addons carregados usam
são juntados em blocos de até 64 MB (cada GMA montado é mais um lugar onde toda busca de arquivo do
jogo procura); os úteis são montados e suas texturas aplicadas sem decodificar, no mesmo ritmo das
outras trocas (nada de aplicar centenas de uma vez: isso travava o jogo por mais de 1 s). O que o mapa
precisa (céu, materiais do BSP, props) sobe ainda atrás da tela de carregamento, até 0,75 s. O runtime guarda a
lista de arquivos que cada GMA montou (`game.MountGMA` devolve a lista) e nunca pergunta ao sistema de
arquivos do jogo se uma textura existe: com dezenas de GMAs montados, `file.Exists` chegou a 4 ms por
chamada. Depois de uma troca de mapa os GMAs do processo continuam montados e são reaproveitados.

## Resultados

### Tamanho (bancada v6 inteira, código do produto)

Texturas = VTF + VMT + blobs + runtime, depois do Tela Forte (bancada v6) e depois do stream a 34 dB
(`C:\GModLab\bench\texstream\bench_product.py`, 16 processos; o WPF usa até 8).

| Grupo | Antes (Forte) | Stream | Redução | Tempo do stream |
|---|---:|---:|---:|---:|
| GLIDE (6 addons) | 50,3 MiB | 7,9 MiB | 6,4× | 101 s |
| LVS (3) | 92,3 MiB | 14,2 MiB | 6,5× | 142 s |
| m9k (5) | 126,6 MiB | 12,9 MiB | 9,8× | 205 s |
| simfphys (4) | 63,1 MiB | 9,9 MiB | 6,4× | 73 s |
| ARC9 (3) | 506,5 MiB | 51,7 MiB | 9,8× | 518 s |
| **Total** | **838,8 MiB** | **96,6 MiB** | **8,7×** | 17 min |

O pacote LVS de carros inteiro (Custo-benefício: models em stream + texturas em stream + sons
adaptativos): 1375 MiB no original, 134 MiB antes do stream de texturas, **68 MiB** com ele.

### No jogo: tour com os 21 addons da bancada

`C:\GModLab\bench\texstream\tour.py`: os 21 addons convertidos carregados juntos no gm_construct;
seis armas sacadas uma depois da outra (10 s cada, ARC9 e M9K), três carros LVS longe, um perto, 30 s
parado. `tsmp.py`: o mesmo com servidor dedicado e o conteúdo empacotado pelo `gmad.exe`.

Medido em 09/11/2026 (i7-13650HX, RTX 4060 Laptop, 1080p, janela com foco), três rodadas do mesmo
roteiro: a bancada v6 sem o stream de texturas (texturas do Forte em VTF, models em stream), a primeira
sessão com o stream (`data/gmot` vazio) e a sessão seguinte (cache escrito pela primeira).

Quadro médio / pior quadro de cada fase:

| Fase | Sem stream de texturas | 1ª sessão | 2ª sessão (cache) |
|---|---|---|---|
| Início, parado | 2,0 ms / 45 ms | 3,1 ms / 106 ms | 2,5 ms / 72 ms |
| Arma 1: ARC9 AUG (15 texturas) | 3,7 / 468 | 10,6 / 174 | 4,0 / 156 |
| Arma 2: ARC9 AK-47 (18) | 3,3 / 407 | 12,9 / 113 | 4,4 / 112 |
| Arma 3: M9K ACR (8) | 2,3 / 45 | 4,2 / 40 | 3,2 / 49 |
| Arma 4: M9K AUG A3 (13) | 2,5 / 32 | 4,2 / 42 | 3,3 / 39 |
| Arma 5: ARC9 AWP (6) | 3,4 / 137 | 5,4 / 89 | 4,4 / 89 |
| Arma 6: M9K 1887 (2) | 2,2 / 21 | 3,2 / 62 | 3,0 / 38 |
| Três carros LVS longe | 10,6 / 2229 | 20,3 / 696 | 11,7 / 738 |
| Carro perto (34 texturas) | 9,5 / 1300 | 16,4 / 228 | 9,3 / 229 |
| Faróis e lanternas | 10,2 / 105 | 9,9 / 76 | 10,5 / 88 |
| Parado 30 s | 7,0 / 32 | 9,5 / 154 | 8,9 / 58 |
| Pico de memória do Lua | 279 MB | 319 MB | 310 MB |

Tempo até a textura aparecer (maior valor entre as texturas do model):

| Model | 1ª sessão: prévia | 1ª sessão: completa | 2ª sessão: completa |
|---|---|---|---|
| ARC9 AUG | 2,3 s | 6,6 s | 0,3 s |
| ARC9 AK-47 | 2,8 s | 6,7 s | 0,5 s |
| M9K ACR | 0,5 s | 4,2 s | 0,1 s |
| M9K AUG A3 | direto | 0,9 s | 0,1 s |
| ARC9 AWP | 2,3 s | 4,7 s | 0,2 s |
| M9K 1887 | direto | 1,1 s | 0,0 s |
| Carro LVS perto | 2,4 s | 9,4 s (mediana 9,0) | 3,0 s (mediana 0,7) |

- As travadas maiores da coluna sem stream são o jogo lendo os VTF inteiros no instante em que a arma
  ou o carro aparece (0,4 s numa arma, 2,2 s nos três carros). Com o stream o jogo lê ali os arquivos
  de 16 px e as texturas chegam em fatias: as piores travadas ficam menores que as do addon sem stream,
  na primeira sessão e nas seguintes. O que sobra (0,7 s no spawn dos carros, ~150 ms ao sacar uma
  arma ARC9) é o próprio jogo criando as entidades: o stream gastou 0 a 1 ms nesses quadros.
- O custo da primeira sessão é o quadro médio enquanto decodifica: 10 a 13 ms com uma arma ARC9 recém
  sacada (3,5 ms sem stream; nesta máquina, de ~280 para ~85 fps durante ~7 s), 16 a 20 ms com os carros
  (10 ms sem stream). A regra só usa o que sobra acima de 60 fps, mais 30% do quadro, até `gmot_budget`.
- Primeira sessão: 664 texturas decodificadas nos 125 s do roteiro (as que apareceram e a pré-carga);
  o resto fica para quando for usado ou para a pré-carga das sessões seguintes (153 na segunda).
- Faróis: as três lâmpadas com textura em stream apontaram para ela (`glide/effects/headlight_circle2`,
  `effects/flashlight/headlight_lowbeam`, `arc9_fas/flashlight_helsoldt`).
- Nenhuma falha de decodificação ou de montagem e nenhum erro de Lua no console nas três rodadas.
- A janela do jogo sem foco roda a ~20 fps (o próprio jogo se limita) e não serve de medida: a linha
  de cada fase no `log.txt` do tour traz `focus true/false`.

### Multiplayer e empacotamento

`tsmp.py`, 09/11/2026: servidor dedicado do laboratório com três addons ARC9 convertidos; o cliente
entra só com o conteúdo que o `gmad.exe` empacota (o que um inscrito da Workshop recebe), montado antes
de qualquer Lua de addon rodar.

- A primeira rodada achou um defeito de empacotamento: 13 texturas falharam com "blob not found". O
  `addon.json` do ARC9 Gunsmith Arms: Source tem `"ignore": [..., "*.json", "*.txt"]` (para os leia-me) e
  o gmad tira do pacote todo arquivo que casa com essa lista: os 554 arquivos de stream do addon (380
  texturas, 174 models) ficaram de fora. No jogo isso é a textura de 16 px e a malha grossa para sempre.
- Correção (`texlab/packing.py`, vale para os dois streams): os arquivos de stream usam a primeira
  extensão de `.dat`, `.txt`, `.json`, `.xml`, `.csv` (as que o gmad aceita em `data_static/`) que a
  lista `ignore` do próprio addon não tira. O padrão passou a ser `.dat`, porque `*.txt` é um padrão
  comum e um `addon.json` escrito depois da otimização não pode ser lido na conversão. Se a lista tira
  todas (ex.: `data_static/*`), o worker avisa com `[WARN] addon.json "ignore" leaves the stream files
  ... out of the published addon`. O runtime lê o caminho do manifesto: nada muda no jogo, e addons
  convertidos antes (blobs `.txt`) continuam funcionando ao lado dos novos.
- Depois da correção (addon convertido de novo pelo worker, os outros dois ainda com blobs `.txt`): os
  554 arquivos no pacote, nenhuma falha e nenhum erro de Lua nos consoles do cliente e do servidor.
  AUG (15 texturas) completa, pior quadro do runtime 25 ms; M4A1 do addon reconvertido (7 texturas)
  completa, pior quadro 140 ms uma vez (uma chamada única do jogo ao montar/trocar, ver "Custos e
  limites").
- Quem otimizou antes desta correção um addon cujo `addon.json` ignora `*.txt` precisa otimizar de novo
  antes de publicar.

## Custos e limites

- Primeira vez de cada model numa máquina: a textura de 16 px no primeiro instante, a prévia em até
  ~3 s, a completa em seguida (armas ARC9 com 15-18 texturas de 2048: completas em ~7 s; carro com 34
  texturas: ~9 s). Da segunda sessão em diante sai do cache: armas prontas em menos de 0,5 s, o carro
  em até 3 s (as trocas são ritmadas para não travar).
- O addon otimizado precisa ser o que o servidor roda: é o servidor que manda o Lua do runtime para os
  jogadores. Jogador com o addon otimizado num servidor que usa o original (sem o
  `lua/autorun/!gmot_*.lua`) fica com as texturas de 16 px.
- Travadas que o runtime não consegue evitar: uma chamada única de `Material()`/`SetTexture` do jogo
  logo depois de um spawn grande já levou 82 e 206 ms (numa textura de 128 px: é o jogo se pondo em dia,
  não o tamanho do arquivo), e a leitura de um blob do disco levou 134 ms uma vez. Cada uma aconteceu
  uma vez por sessão nas medições e fica abaixo das travadas do addon sem stream na mesma cena.
- Em máquina fraca (abaixo de 60 fps) o stream usa no máximo 30% do quadro e nunca mais que
  `gmot_budget` (6 ms): a 20 fps as texturas levam cerca de 3x mais para chegar.
- `data/gmot/` guarda as texturas decodificadas até `gmot_cache_mb` (512 MB) no PC do jogador; nada
  disso conta no tamanho do addon.
- CPU do Lua enquanto decodifica: 2048² DXT1 ~0,7 s, DXT5 ~0,8 s (LuaJIT), espalhado pelos quadros na
  fatia acima. Memória do Lua: o decodificador trabalha linha a linha, o pico da primeira sessão foi
  319 MB com os 21 addons carregados (279 MB na mesma cena sem o stream de texturas; antes do
  decodificador por linhas chegava a 481 MB); o cliente 32 bits rodou sem erro de memória.
- Ícones do menu de spawn gerados enquanto a textura ainda é a de 16 px ficam borrados no cache do
  jogo (o mesmo vale para a malha grossa do stream de models).
- Texturas sem compressão (RGBA8888, BGRA8888, ABGR8888, RGB888, BGR888, BGRX8888: HUD, miras, ícones)
  voltam no mesmo formato, com alvo mínimo de 40 dB (bordas duras que o DXT nunca borrou); DXT3 volta
  como DXT5 (ou DXT1 sem alpha lido).
- Fica VTF: IA88/I8/A8/HDR, texturas animadas, cubemaps e as menores que 64 px.

## Testes

`tests/test_texture_stream.py`: codec (ida e volta, prévia, metade, ruído, alpha, normal map, formatos
sem compressão, DXT3), conversão de um addon (classes, manifesto, placeholder, slug pelo conteúdo), o
`decoder.lua` rodando em LuaJIT (lupa) idêntico byte a byte ao espelho em Python (o espelho faz as
contas da síntese na mesma ordem do Lua), e o runtime inteiro com a API do GMod simulada e globais
estritas (ler uma global que ninguém definiu é erro: pega função local usada antes da definição):
decodifica, monta, troca a textura, intercepta `SetTexture`/`render.SetMaterial`, pré-carrega o efeito,
caminhão longe só na prévia e completo quando chega perto, e a segunda sessão saindo do cache sem
decodificar nada. Três testes cobrem o ritmo e as rotas novas: as texturas do cache aparecem poucas por
quadro e as de fundo espalhadas; faróis (`ProjectedTexture`), `SetTexture` com ITexture, material criado
depois e materiais que a própria entidade lista; céu, materiais citados pelo mapa e a pré-carga parando
em `gmot_prefetch_mb`, com a sessão seguinte mostrando o que o mapa usa antes do primeiro quadro.
