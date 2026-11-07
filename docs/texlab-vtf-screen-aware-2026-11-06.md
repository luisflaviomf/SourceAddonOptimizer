# Modo "Tela" — compressão de VTF guiada pela tela (06/11/2026)

Pesquisa nova (`texlab/`), feita do zero, e integrada ao WPF como quinto modo da
aba **Mídia e recursos**: Padrao, Magick, Maximum, Magick+, **Tela**.

## O que o modo faz

1. **Lê os modelos compilados do addon** (MDL/VVD/VTX, sem decompilar) e os VMTs.
   Para cada triângulo de cada textura calcula a densidade de texels por unidade
   de mundo (valores singulares do Jacobiano UV, com a escala do material).
2. **Escolhe a redução por textura** (2x, 4x ou 8x): toda textura DXT redutível cai
   2x (como o modo atual); cai 4x se a perda de detalhe visível, ponderada pela
   distância em que ela aparece na tela e normalizada pelo contraste local
   (detalhe em superfície escura conta mais), fica abaixo da perda típica do 2x
   (perfil q50); cai 8x só quando os níveis removidos comprovadamente nunca são
   amostrados, nem a 24 unidades em 1080p.
3. **Nível de topo novo por Lanczos do nível 0**; **todos os mips menores são os
   originais copiados bit a bit**. Texturas que quase nunca aparecem ampliadas
   reaproveitam o mip original sem recodificar.
4. **Formato pelo uso real do alpha**: DXT5→DXT1 sem perda só quando nenhum
   material lê o alpha, ou quando ele é 255 constante. Alpha de recorte e de
   transparência é preservado exatamente.
5. Formatos fora de DXT1/DXT5 e texturas NOMIP voltam ao caminho padrão.

Implementação: `texlab/screen_compress.py` (worker, comando `texscreen`, só numpy
e Pillow), `Objects/ScreenVTFEdit.cs` (WPF), modo `CompressPipelineMode.Screen`.

## Achado: o modo atual apaga máscaras de pintura

O caminho VTF atual converte para DXT1 texturas cujo alpha é a máscara de
`$blendtintbybasealpha` (pintura do LVS) ou de phong/envmap. Na amostra:
`kubel/kubelwagen_paint`, `mg_mgb/nodamage_lod0`, `mazda_miata/ext_rim`. O alpha
vira 255 em tudo. O modo Tela mantém DXT5. Comprovado pelos dados dos arquivos;
a prova visual com a entidade LVS pintada ainda não foi feita.

## Resultados (15 veículos LVS, 377 VTFs, 376,1 MB)

| Variante | Texturas | FLIP médio no jogo | Pior região (média) | Pior região (máx.) |
|---|---:|---:|---:|---:|
| Modo atual (2x, 8x8) | 105,5 MB (28,0%) | 0,0379 | 0,116 | 0,280 |
| **Modo Tela (produto)** | **66,6 MB (17,7%)** | **0,0335** | **0,110** | 0,288 |

Medição: cliente isolado do laboratório (`-noworkshop`), `gm_construct`, luz de
estúdio fixa, 19 modelos × 6 vistas, shaders reais. As métricas usam só as 89
capturas estáveis (o original contra ele mesmo varia nas outras 25: carregamento
de textura/materiais animados). Nenhum material/textura com erro. Tempo: 237 s
contra 157 s do modo atual. Execução ponta a ponta pelo `CompressAddonSystem`
com o worker extraído do ZIP embutido.

Com `mat_forceaniso 16` (3 modelos) o modo Tela também ganha no pior caso
(0,453 contra 0,540).

## Pack inteiro (lvs_cars_pack, 163 modelos, 1152 VTFs)

Rodado como no dia a dia (reparo de VMT + modo Tela pelo `CompressAddonSystem`)
numa cópia: texturas 798,5 → 135,7 MB; addon 1375 → 712 MB (modelos e sons
intactos). 502 s. Checagem: mesmos 3827 arquivos, não-VTF idênticos (exceto o
VMT reparado), 1145 texturas com mips menores idênticos ao original. No jogo, os
mesmos 3 materiais `___error` que o original já tem e nenhum novo.

Achado e corrigido nesse teste: o caminho antigo (Padrao/Magick e o caminho de
reserva do Tela) reduzia VTF de cor única para 1x1, e um topo DXT menor que um
bloco 4x4 aparece preto no jogo (carroceria do Mercedes W123). Agora o mínimo é
4x4 (`VTFEdit.SingleColorVtfBounds`).

Ruído de captura: materiais `Refract` (lanternas) leem a cópia do quadro
anterior; na primeira vista de cada modelo a captura varia entre execuções do
mesmo pack. Usar as vistas de perto (estáveis) para comparar: FLIP médio 0,0315,
pior região média 0,115 (147 capturas).

## O que foi tentado e descartado (com motivo medido)

- **"Só o invisível"** (mip nunca amostrado a 40 cm): quase nada é invisível de
  perto em 1440p; não bate o 2x.
- **Corte de mip sem recodificar no topo**: os mips de muitas texturas LVS foram
  gerados super-afiados pela ferramenta do autor (148 de 375 com PSNR < 30 dB
  contra uma redução limpa do nível 0); usar o mip 1 guardado como novo topo
  fica pior que o Lanczos.
- **Redução ciente da reconstrução bilinear (solver de mínimos quadrados)**:
  ganha +1 dB de PSNR na textura, mas no jogo empata na média e cria aliasing em
  malhas finas perto do limite de resolução (grade do Caterham). Mantido como
  `--method solver`, não é o padrão.
- **Alocação global por soma do erro na tela** (24,6 MB): melhor na média por
  pixel, mas apaga detalhes pequenos. Descartado.
- **Limiar sem normalizar contraste**: subestima malhas escuras (carbono, grade).

## Ferramentas de laboratório

- `texlab/sample.py`: monta a amostra a partir das entidades.
- `texlab/baseline/`: roda o caminho atual (2x, 8x8) ou, com `--screen`, o modo
  Tela pelo `CompressAddonSystem`.
- `texlab/render.py`, `evaluate.py`: renderizador CPU + FLIP offline.
- `texlab/ingame/`: sonda Lua + runner do cliente isolado, comparação FLIP e
  bisseção de texturas no jogo.

Dados: `C:\GModLab\texlab\v1\`.

## Limites

- Medido em veículos LVS; armas, props e mapas ainda não.
- O perfil q50 foi calibrado nessa amostra.
- O codificador DXT continua sendo o VTFCmd.
- Texturas não vistas por UV de modelo (HUD, Lua) recebem 2x como no modo atual.


## Velocidade (07/11): Rust + paralelismo, saida identica

`lvs_cars_pack` inteiro (1.152 VTFs), worker `texscreen --jobs 8`, mesmos bytes de antes
(2.740 arquivos de materials e relatorio comparados byte a byte):

| Etapa | Antes | Depois | O que mudou |
|---|---:|---:|---|
| Analise (plano) | 344 s | ~20 s | processos em paralelo (modelos e texturas no mesmo pool); DXT decode, band energy e `_sample_map` em Rust; cabecalho VTF sem ler o arquivo todo; mapa por texel so no metodo `solver` |
| VTFCmd | 141 s | ~26 s | varios VTFCmd ao mesmo tempo (CPUs logicas - 2), lotes menores |
| Preparar + gravar | 72 s | ~17 s | PNG temporario com compressao 1; gravacao em threads, validacao dos bytes antes de gravar (sem reler do disco) |
| **Total** | **557 s** | **~63 s** | worker empacotado: 64,6 s; botao Comprimir (Tela) na interface: 79 s |

Pico de memoria com 8 processos: 2,6 GB. O C# passa `--jobs` = metade das CPUs logicas, ate 8.
