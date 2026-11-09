# Texturas em stream (GMOT): texturas decodificadas pelo Lua no jogo (09/11/2026)

Pedido do Luis: texturas da bancada abaixo de 100 MB no disco, com qualidade boa. O DXT (4 ou 8 bits
por texel) é o piso do formato nativo; o Tela Forte já tirou o que dava de resolução (bancada v6:
837 MiB de VTF). Para ir além, a textura precisa ser guardada num formato menor e decodificada no
jogo. Laboratório: `C:\GModLab\bench\texstream` (README.md, scripts, capturas).

## Como funciona

1. **No otimizador** (`texture_stream/`, perfil `stream` do worker `texscreen`): depois do Forte,
   cada VTF DXT1/DXT3/DXT5 ou sem compressão de quadro único com lado ≥ 64 vira:
   - um blob GMOT em `data_static/gmot/<slug>/<i>.txt` (`codec.py`): wavelet 5/3, cor em YCoCg (croma
     em meia resolução quando fica menor), normal maps como vetores X/Y normalizados (Z reconstruído),
     quantização com zona morta, LZMA por pedaço. O passo de quantização é o maior que mantém **34 dB**
     (+1,2 dB de margem para o DXT) contra o VTF de entrada (`choose.py`); o alpha só fica quando algum
     material o lê, com alvo pelo peso que ele tem na imagem (translúcido/selfillum 1, máscara de
     envmap pelo `$envmaptint`, phong 0,7...). Os dois níveis mais finos recebem **preenchimento de
     ruído**: por bloco de 8×8, a energia dos coeficientes zerados (3 bits); o decodificador devolve
     ruído dessa intensidade e o granulado volta.
   - um VTF de 16 px com mipmaps no caminho original (o que aparece nos primeiros segundos);
   - uma linha no manifesto de `lua/autorun/!gmot_<slug>.lua`: blob, materiais e parâmetros que usam
     a textura, models que a desenham e a "classe" (como o runtime descobre que ela é necessária).
2. **No jogo** (`runtime.lua` + `decoder.lua`, embutidos no arquivo do addon; o núcleo mais novo entre
   os addons carregados vale para todos): quando algo precisa da textura, o Lua lê o blob, decodifica
   para DXT (o mesmo formato e a mesma VRAM de antes), escreve um GMA em `data/gmot/`, monta com
   `game.MountGMA` e aponta os materiais para `materials/gmot/<slug>/<i>.vtf`. Texturas maiores que
   512 px ganham antes uma prévia em 512. Os GMAs ficam montados até o fim da sessão e são apagados na
   próxima; numa troca de mapa o que já foi montado é reaproveitado sem decodificar de novo.

Fatos do GMod que definiram o desenho (provados no cliente do laboratório): material JPG/PNG fica sem
compressão na VRAM (5× mais); VTF escrito pelo Lua em `data/` só carrega o cabeçalho (magenta); GMA
montado de `data/` funciona (52 MB em ~75 ms) mas não substitui um caminho que um addon já tem, por
isso os nomes únicos + `IMaterial:SetTexture`.

### Como o runtime sabe o que decodificar

| Classe | Texturas | Como o runtime descobre |
|---|---|---|
| 0 model | usadas por um model do addon ou de um vizinho | arma ativa, viewmodel, mãos e inventário a cada quadro; entidades (64 por quadro, com material e sub-materiais trocados pelo servidor); props estáticos e materiais do mapa lidos do BSP; `ClientsideModel`, `SetModel` |
| 1 efeito | `effects/`, `particle(s)/`, `sprites/`, `decals/`..., shaders Sprite/SpriteCard/Refract..., materiais citados em `.pcf` | pré-carga 3 s depois do mapa carregar (o motor desenha sem passar pelo Lua) |
| 2 Lua | caminho citado no Lua do addon ou vizinhos (camuflagens, adesivos, menus) | chamadas interceptadas: `Material`, `CreateMaterial`, `IMaterial:SetTexture`, `Entity:SetMaterial/SetSubMaterial`, `render.SetMaterial` e os `MaterialOverride`, `surface.SetMaterial/GetTextureID`; pré-carga depois de 20 s |
| 3 resto | o que nada acima cita | as mesmas interceptações; pré-carga depois de 20 s |

Só com a classe 0 a bancada ficaria em 236 MiB (texturas citadas pelo Lua, efeitos e as sem uso
aparente somam 150 MiB de VTF); por isso tudo entra no stream e a pré-carga cobre o que o Lua não vê.

Orçamento: no máximo `gmot_budget` ms por quadro (padrão 6), limitado a 30% do tempo do quadro quando
algo na tela espera e a 10% na pré-carga (interrompida quando algo da tela chega). `gmot_prefetch 0` desliga a pré-carga. `gmot_status` lista o
estado por addon.

## Resultados

### Tamanho (bancada v6 inteira, código do produto)

Texturas = VTF + VMT + blobs + runtime, depois do Tela Forte (bancada v6) e depois do stream
(`C:\GModLabench	exstreamench_product.py`, 16 processos; o WPF usa até 8).

| Grupo | Antes (Forte) | Stream | Redução | Tempo do stream |
|---|---:|---:|---:|---:|
| GLIDE (6 addons) | 50,3 MiB | 7,9 MiB | 6,4× | 101 s |
| LVS (3) | 92,3 MiB | 14,2 MiB | 6,5× | 142 s |
| m9k (5) | 126,6 MiB | 12,9 MiB | 9,8× | 205 s |
| simfphys (4) | 63,1 MiB | 9,9 MiB | 6,4× | 73 s |
| ARC9 (3) | 506,5 MiB | 51,7 MiB | 9,8× | 518 s |
| **Total** | **838,8 MiB** | **96,6 MiB** | **8,7×** | 17 min |

Composição dos 96,6 MiB (rodada anterior, sem os formatos sem compressão): blobs ~82, VTF que o codec
não pega (cubemaps 2,6, animadas 1,9, menores que 64 px 1,3, ilegíveis 1,3), VMT 2,0, runtime 1,9
(~45 KB por addon + manifesto), placeholders 1,7.

### No jogo (cliente do laboratório, x64, gm_construct, AUG da ARC9 com os 3 addons ARC9 em stream)

`C:\GModLab\bench\texstream\spike7.py`: dá a arma e captura em 0,3 / 1 / 2 / 4 / 8 / 15 s.

| | Sem stream (VTF) | Laboratório (antes) | Produto |
|---|---|---|---|
| Arma com textura (prévia) | 0 s | ~4 s | ~2 s |
| Metade das texturas completas | – | – | ~11 s |
| Quadro médio nos 15 s depois de sacar | 3,2 ms | 6,6 ms | 6,6 ms |
| p99 do quadro | 5,2 ms | 28 ms | 28 ms |
| Pior quadro | 179 ms | 178 ms | 149 ms |
| Pior tempo do runtime num quadro | – | – | 28 ms |
| Memória do Lua (pico) | 63 MB | – | 460 MB |

Os quadros acima de 100 ms nos três casos são o saque da arma e as próprias capturas de tela do teste
(`render.Capture` ~150 ms). Numa rodada apareceu um quadro de 432 ms no saque que não se repetiu.

O runtime do laboratório tinha um defeito: depois de montar um GMA, só voltava a aplicar as texturas
quando chegava trabalho novo; isso atrasava as prévias.

Decodificador (LuaJIT, 2048²): DXT1 ~1,0 s, DXT5 ~1,4 s (o bloco de alpha num laço separado tirou 1 s);
a prévia em 512 leva ~0,2 s. Cliente 32 bits: rodou sem erro de memória (pico de 103 MB do Lua, com a
CPU ocupada por outra tarefa; o x64 deixa o lixo acumular até ~460 MB antes de coletar).

## Custos e limites

- Primeira vez de cada model na sessão: a textura de 16 px até a prévia, depois a completa.
- `data/gmot/` guarda as texturas decodificadas da sessão (apagadas no início da próxima).
- CPU do Lua enquanto decodifica (uma 2048² DXT5 leva ~1,2 s, espalhado pelos quadros); memória
  temporária do Lua de ~200 MB numa 2048² (GMod 32 bits não testado).
- Ícones do menu de spawn gerados enquanto a textura ainda é a de 16 px ficam borrados no cache do
  jogo (o mesmo vale para a malha grossa do stream de models).
- Texturas sem compressão (RGBA8888, BGRA8888, ABGR8888, RGB888, BGR888, BGRX8888: HUD, miras, ícones)
  voltam no mesmo formato, com alvo mínimo de 40 dB (bordas duras que o DXT nunca borrou); DXT3 volta
  como DXT5 (ou DXT1 sem alpha lido).
- Fica VTF: IA88/I8/A8/HDR, texturas animadas, cubemaps e as menores que 64 px.

## Testes

`tests/test_texture_stream.py`: codec (ida e volta, prévia, ruído, alpha, normal map), conversão de um
addon (classes, manifesto, placeholder), o `decoder.lua` rodando em LuaJIT (lupa) idêntico byte a byte
ao espelho em Python, e o runtime inteiro com a API do GMod simulada (decodifica, monta, troca a
textura, intercepta `SetTexture`/`render.SetMaterial`, pré-carrega o efeito).
