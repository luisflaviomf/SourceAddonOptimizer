# Arquivos que o assistente deixava iguais: medição e redução (2026-11-10)

Pedido: reduzir no disco os tipos de arquivo que o assistente Otimizar (perfil Custo-benefício) não muda,
com regras gerais (propriedades medidas do arquivo, nunca nome de addon).

- Passo 1: medição, sem mudar o produto (da seção "Medição" em diante).
- Passo 2: o Luis escolheu implementar o PNG sem perda e a remoção dos `.xbox.vtx` (seção seguinte). OGG,
  MP3, animações de `.mdl` e `.phy` continuam como estavam.

Bancada: os cinco addons juntados (`GLIDE`, `LVS`, `arc9`, `m9k`, `simfphys`), 6.943 MiB antes e
752,8 MiB depois do assistente (`docs/juntar-addons-2026-11-09.md`). Todas as porcentagens "da bancada"
abaixo são sobre os 752,8 MiB.

## O que foi implementado (passo 2)

### Resultado na bancada

Assistente pelo exe publicado, perfil Custo-benefício, os cinco addons juntados:

| Grupo | Antes | Assistente em 2026-11-09 | Agora | PNG | `.xbox.vtx` | Tempo (antes → agora) |
|---|---:|---:|---:|---|---|---|
| arc9 | 2.125 MiB | 249,7 MiB | 236,0 MiB | 19,63 → 5,87 MiB | | 878 → 812 s |
| GLIDE | 1.178 MiB | 103,6 MiB | 101,9 MiB | 10,70 → 8,92 MiB | | 226 → 217 s |
| LVS | 1.770 MiB | 127,5 MiB | 125,9 MiB | 3,96 → 2,37 MiB | | 258 → 291 s |
| m9k | 617 MiB | 79,2 MiB | 70,1 MiB | não tem PNG | −9,10 MiB (77 arquivos) | 278 → 257 s |
| simfphys | 1.252 MiB | 192,8 MiB | 191,6 MiB | 7,07 → 5,87 MiB | | 337 → 329 s |
| **Total** | **6.943 MiB** | **752,8 MiB** | **725,4 MiB** | **41,35 → 23,04 MiB** | **−9,10 MiB** | **1.976 → 1.906 s** |

A redução da bancada vai de 89,2% para 89,6% (−27,4 MiB). O passo do PNG levou 115 s somados nos quatro
grupos, rodando junto com as texturas e os sons, e não alongou o total (a diferença de tempo entre as duas
rodadas é variação normal; no LVS um teste no jogo rodava ao mesmo tempo).

Conferência da saída (`mcb2`) contra a origem e contra a saída de 2026-11-09 (`mcb`):

- os 2.195 PNG decodificam nos mesmos pixels RGBA da origem; 2.193 ficaram menores, 2 já não tinham o que
  ganhar e ficaram como estavam; nenhum ficou maior;
- os 77 `.xbox.vtx` sumiram;
- os outros 40.447 arquivos são idênticos byte a byte aos da rodada anterior.

### PNG sem perda

Worker `pnglossless` (`png_lossless/`), chamado pelo serviço `Objects/LosslessPngEdit.cs`; ferramenta
`tools/oxipng/oxipng.exe` (oxipng 10.2.0, MIT), empacotada junto do worker.

- Cada `.png` é recomprimido (nível 4) e só troca de lugar com o original quando fica menor **e** decodifica
  nos mesmos pixels RGBA. Essa conferência é feita com o Pillow, sem confiar no oxipng: no teste, uma
  ferramenta falsa que muda os pixels tem todos os arquivos recusados.
- Os mesmos pixels podem ser guardados num tipo de cor menor: RGBA opaco vira RGB, até 256 cores vira
  paleta, cinza vira cinza. Só são aceitos os 20 formatos que o jogo leu igual ao original (abaixo); um
  resultado em outro formato é refeito mantendo o formato de origem.
- Ficam os blocos que tratam de cor (gAMA, cHRM, sRGB, iCCP, pHYs); saem texto do editor, EXIF e data.
- Não mexe em: PNG de 16 bits, PNG animado, arquivo `.png` que não é PNG por dentro, e qualquer arquivo em
  que um passo falhe.
- O arquivo do addon nunca é escrito por dentro: o resultado é preparado em
  `%LOCALAPPDATA%\GmodAddonOptimizer\work\pnglossless` e trocado de uma vez. Um original ligado por hard
  link (como a bancada) não é alterado.
- A saída é a mesma byte a byte com 1 ou 12 threads e entre o worker de desenvolvimento e o empacotado.

Onde aparece:

- Assistente: ligado em todos os perfis (a linha de texturas termina em "· PNG sem perda"). No
  Personalizado há a caixa "Recomprimir PNG sem perda", marcada por padrão. O resultado sai em "Mostrar
  detalhes", por exemplo `PNG sem perda: 19,6 MiB → 5,9 MiB · 1.403 de 1.403 arquivos menores, com os mesmos pixels`.
- Avançado › Texturas e sons › Imagens PNG e JPG: caixa "PNG sem perda", desligada por padrão. Sem ela, o
  tipo PNG marcado continua no caminho antigo, que reduz a imagem.

### O jogo lê os formatos novos?

Sim. Medido no cliente do laboratório, comparando cada arquivo recomprimido com o original carregado ao
lado, pixel por pixel (`ITexture:GetColor`) e num quadro capturado com os dois desenhados lado a lado:

| Teste | Pares | Pixels comparados | Diferentes | Quadro capturado |
|---|---:|---:|---:|---|
| 20 formatos de armazenamento, cliente de 64 bits | 48 | 186.271 | 0 | 48 de 48 células iguais |
| Os mesmos 20 formatos, cliente de 32 bits | 48 | 186.271 | 0 | 48 de 48 iguais |
| Amostra da saída do assistente na bancada (16 trocas de formato) | 170 | 98.835 | 0 | 170 de 170 iguais |

Os 20 formatos: RGBA 8, cinza + alpha 8, RGB 8 (com e sem cor transparente), cinza 1/2/4/8 bits (com e sem
cor transparente), paleta 1/2/4/8 bits (com e sem transparência). Nas imagens de 64×64 todos os pixels foram
comparados; nas maiores, uma amostra fixa (ler um pixel custa tempo proporcional ao tamanho da textura).
16 bits por canal não foi testado e nunca é gravado.

### `.xbox.vtx`

O empacotamento de Models (`model_screen/cli.py`) passou a tirar `*.xbox.vtx` junto com `*.dx80.vtx` e
`*.sw.vtx`. É a variante do model para o Xbox original; o jogo no PC lê só `.dx90.vtx`. Vale para todos os
perfis de Models do modo Padrão, inclusive o Sem perda. Na bancada só o m9k tinha esses arquivos.

### No jogo

`tour.py` com os cinco addons de `mcb2`, contra as rodadas de 2026-11-09 (`tour_m3` primeira sessão,
`tour_m2` segunda). Janela em foco em todas as fases, nenhuma textura falhou, as mesmas 13 linhas de aviso
no console. Quadro médio / pior:

| Fase | Antes, 1ª sessão | Agora, 1ª sessão | Antes, 2ª | Agora, 2ª |
|---|---|---|---|---|
| Arma 1 | 8,8 / 129 ms | 8,6 / 121 ms | 3,4 / 117 ms | 3,7 / 123 ms |
| Arma 2 | 9,7 / 84 ms | 9,1 / 70 ms | 5,1 / 87 ms | 4,2 / 79 ms |
| Arma 4 (`m9k_auga3`, que tinha `.xbox.vtx`) | 2,9 / 19 ms | 3,0 / 25 ms | 3,5 / 38 ms | 2,8 / 28 ms |
| Carros longe | 15,8 / 573 ms | 17,6 / 546 ms | 8,7 / 566 ms | 9,2 / 558 ms |
| Carro perto | 10,3 / 159 ms | 10,4 / 151 ms | 8,2 / 166 ms | 8,3 / 149 ms |

Dentro do que varia de uma rodada para outra. As capturas das armas do m9k e dos carros são as mesmas das
rodadas anteriores (diferença média abaixo de 1 em 255). Rodadas: `tour_p3` (primeira sessão) e `tour_p2`
(segunda); `tour_p1` foi a primeira leitura dos arquivos recém-gravados, mais lenta pelo disco, como já
tinha acontecido em 2026-11-09. O tour esconde o HUD, então os PNG foram conferidos pelo teste da tabela
acima, não pelas capturas do tour.

### O que custa

- Mais um executável no pacote de ferramentas (1,2 MB) e cerca de 15 s por addon com algumas centenas de
  PNG (70 s nos 1.403 do arc9), em paralelo com as texturas.
- 1.623 dos 2.195 PNG da bancada mudam de tipo de cor por dentro (os pixels são os mesmos). Quem abrir um
  desses arquivos num editor vai ver, por exemplo, "indexado" em vez de RGBA. Se isso incomodar, o worker
  aceita `--keep-format`, que só recomprime (25,6 MiB em vez de 23,0); não há botão para isso na interface.
- O teste no jogo foi feito no cliente do laboratório (versão atual, 64 e 32 bits). Uma versão futura do
  jogo que mude o leitor de PNG não está coberta.

Testes: `tests/test_png_lossless.py` (14), `tests/test_model_screen.py`, `tests/wpf_optimizer_contract`;
a suíte Python inteira passa (423, 1 pulado).

## Medição (passo 1): resumo das técnicas

Feitas no passo 2: a segunda linha do PNG e a do `.xbox.vtx`. As outras continuam só medidas.

| Tipo | Hoje | Técnica | Depois | Economia | Da bancada | Perda |
|---|---:|---|---:|---:|---:|---|
| `.png` | 41,35 MiB | Recomprimir mantendo o formato de cor | 25,59 | −15,8 MiB | 2,1% | nenhuma (pixels idênticos) |
| `.png` | 41,35 MiB | Recomprimir + formato de cor menor quando dá o mesmo pixel | 23,02 | −18,3 MiB | 2,4% | nenhuma (pixels idênticos); precisa de teste no jogo |
| `.xbox.vtx` | 9,10 MiB | Remover (o jogo no PC nunca lê) | 0 | −9,1 MiB | 1,2% | nenhuma |
| `.mdl` | 82,82 MiB | Animações iguais guardadas uma vez só | 73,17 | −9,65 MiB | 1,3% | nenhuma (animação decodificada idêntica) |
| `.ogg` | 32,15 MiB | Recodificar Vorbis q5, mesma taxa e canais | 12,64 | −19,5 MiB | 2,6% | com perda (proposta) |
| `.mp3` | 22,96 MiB | Recodificar a 80 kbps por canal | 11,03 | −11,9 MiB | 1,6% | com perda (proposta) |
| `.png` | 23,02 MiB (depois do sem perda) | Paleta de 256 cores | 7,28 | −15,7 MiB | 2,1% | com perda |
| `.mdl` | 73,17 MiB (depois do sem perda) | Metade dos quadros de animação | 56,8 | −16,3 MiB | 2,2% | com perda (não recomendo) |
| `.phy` | 20,39 MiB | Limitar cada peça de colisão a 16 pontos | 13,6 | −6,8 MiB | 0,9% | muda a colisão (não recomendo) |

Só com o que não tem perda (PNG, `.xbox.vtx`, animações): **752,8 → 715,7 MiB** (redução da bancada de 89,2%
para 89,7%). Somando OGG q5 e MP3 a 80 kbps por canal: **684,3 MiB (90,1%)**.

O que vale a pena, na ordem:

1. PNG sem perda: maior ganho sem custo de qualidade, 18 a 27 s para a bancada inteira.
2. Remover `.xbox.vtx`: uma linha no empacotamento que já tira `.dx80.vtx` e `.sw.vtx`.
3. Animações iguais nos `.mdl`: sem perda, mas exige reescrever o `.mdl` e corrigir os endereços internos;
   é o item mais trabalhoso e precisa de teste no jogo com as armas.
4. OGG e MP3: o maior ganho que sobra, com perda. Só proposta, decisão do Luis.

`.phy` não tem nada sem perda para tirar. O que há com perda em `.phy` e nas animações mexe em colisão e
movimento, e rende pouco para o risco.

## O que fica igual depois do assistente

Comparação por conteúdo (hash) entre `C:/GModLab/bench/src/<grupo>/<grupo>_merged` e
`C:/GModLab/bench/mcb/<grupo>/<grupo>_merged`:

| Extensão | Arquivos | Tamanho depois | Idênticos | Tamanho dos idênticos |
|---|---:|---:|---:|---:|
| `.wav` | 3.150 | 210,9 MiB | 223 | 31,2 MiB |
| `.mdl` | 3.984 | 82,8 MiB | 1.003 | 7,1 MiB |
| `.png` | 2.195 | 41,3 MiB | 2.195 | 41,3 MiB |
| `.ogg` | 523 | 32,2 MiB | 523 | 32,2 MiB |
| `.vtx` | 4.062 | 31,9 MiB | 99 | 9,1 MiB |
| `.mp3` | 591 | 23,0 MiB | 591 | 23,0 MiB |
| `.phy` | 3.181 | 20,4 MiB | 3.181 | 20,4 MiB |
| `.lua` | 2.068 | 20,4 MiB | 2.049 | 18,5 MiB |
| `.vtf` | 5.418 | 10,0 MiB | 81 | 5,9 MiB |
| `.vmt` | 6.467 | 2,0 MiB | 6.187 | 1,9 MiB |
| `.pcf`, `.ttf`, resto | 125 | 3,5 MiB | 125 | 3,5 MiB |

Os outros 274,6 MiB são os arquivos de stream (`.dat`, 189,8 MiB) e os `.vvd` (84,8 MiB), todos refeitos.

Duas correções ao que se supunha:

- Os `.mdl` não ficam idênticos: 2.981 dos 3.984 são reescritos (contagens de vértices, malha grossa do
  stream), mas o tamanho não muda (82,6 → 82,8 MiB), porque o que pesa neles é animação.
- Sobram 77 arquivos `.xbox.vtx` (9,1 MiB, todos no m9k). São a versão do model para o Xbox original; o jogo
  no PC só lê `.dx90.vtx`. O empacotamento tira `*.dx80.vtx` e `*.sw.vtx` (`model_screen/cli.py`), mas não
  este sufixo.

## PNG (41,35 MiB, 2.195 arquivos)

Nenhum no m9k. arc9 19,6 MiB, GLIDE 10,7, simfphys 7,1, LVS 4,0.

Do que são feitos:

- Formato: 2.038 RGBA de 8 bits (38,2 MiB), 130 RGB (3,1 MiB), 23 com paleta, 4 cinza com alpha. Nenhum
  de 16 bits, nenhum entrelaçado.
- 145 dos RGBA (8,4 MiB) têm o alpha todo opaco; 1.136 arquivos usam 256 cores ou menos.
- 41 arquivos (8,9 MiB) estão gravados praticamente sem compressão: ícones 256×256 de acessórios do ARC9
  com 259 KiB cada (o tamanho dos pixels crus).
- Metadados (texto do editor, perfil de cor): 0,5 MiB.
- Tamanhos: 256×256 em 1.213 arquivos (14,6 MiB), 128×128 em 611 (10,5 MiB), 300×300 em 112 (8,1 MiB),
  512 em 62, 1024 em 28, 2048 em 6.

Onde são usados e quanto cada técnica deixa (MiB):

| Uso | Arquivos | Tamanhos | Hoje | Mantendo formato | Formato menor | 256 cores | Metade |
|---|---:|---|---:|---:|---:|---:|---:|
| `entities/*.png`: ícones do menu de spawn (armas, entidades, veículos pelo nome da classe) | 767 | 128 e 256 | 12,64 | 9,49 | 9,18 | 2,11 | 8,48 |
| `gui/glide/**`: ícones da lista de veículos do Glide (`icon = "gui/glide/..."`) | 123 | 300 e 500 | 9,49 | 8,37 | 7,85 | 1,38 | 2,42 |
| `entities/attachs/*`: ícones de acessórios do ARC9 (`Material(..., "mips smooth")`) | 271 | 256 | 8,65 | 1,71 | 1,35 | 0,57 | 0,76 |
| `arc9/glyphs_*`: teclas e botões das dicas do HUD | 699 | 256 | 3,03 | 1,91 | 1,39 | 1,39 | 2,28 |
| `hud/arc9_csgo/**`: retículos e máscaras de mira (`ATT.RTScopeReticle`) | 22 | 512 e 1024 | 2,60 | 1,09 | 1,01 | 0,21 | 0,42 |
| `arc9/**`: interface do ARC9 | 152 | 128 a 2560 | 2,50 | 1,65 | 1,20 | 0,86 | 0,71 |
| `lvs/**`: miras, HUD e ícones de armas | 67 | 64 a 2048 | 2,17 | 1,19 | 0,90 | 0,69 | 0,40 |
| Outros (`icon16`, `models/`, etc.) | 94 | 16 a 512 | 0,26 | 0,18 | 0,15 | 0,07 | 0,12 |
| **Total** | **2.195** | | **41,35** | **25,59** | **23,02** | **7,28** | **15,59** |

Técnicas sem perda (oxipng 10.2, cópias fora da bancada, pixels conferidos arquivo por arquivo):

| Técnica | Tamanho | Economia | Tempo | Conferência |
|---|---:|---:|---:|---|
| Recomprimir, mesmo tipo de cor e mesmos bits, sem os metadados (`-o 4 --nx`) | 25,59 MiB | −38,1% | 27 s | 2.195 de 2.195 com RGBA idêntico |
| O mesmo + tipo de cor menor quando o pixel é o mesmo (RGBA opaco vira RGB, até 256 cores vira paleta, cinza vira cinza) | 23,02 MiB | −44,3% | 18 s | 2.195 de 2.195 com RGBA idêntico |
| O mesmo + limpar a cor dos pixels totalmente transparentes | 22,35 MiB | −45,9% | 18 s | pixels visíveis e alpha idênticos; a cor escondida muda em 1.092 arquivos |
| Nível 6 | 22,93 MiB | −44,6% | 128 s | |
| Zopfli | 22,74 MiB | −45,0% | 341 s | |

- O nível 4 é o ponto certo: nível 6 e Zopfli custam 7 a 19 vezes o tempo por 0,1 a 0,3 MiB.
- Limpar a cor escondida rende só 0,7 MiB e deixa de ser idêntico byte a byte nos pixels; com filtro
  bilinear a cor escondida pode aparecer na borda. Não vale.
- O "formato menor" muda o tipo de 1.622 arquivos (982 para paleta, 485 para cinza com alpha, 145 de RGBA
  para RGB, 10 outros). A bancada já tem 23 PNG com paleta e 4 em cinza com alpha feitos pelos autores, e o modo Magick do
  programa já grava PNG com paleta, mas isso tinha de ser confirmado no jogo antes de ligar (um PNG de cada
  tipo, comparar a imagem). Foi confirmado no passo 2: os 20 formatos são lidos com os mesmos pixels.

Com perda (estimativas, a julgar na tela como os perfis de textura):

- Paleta de 256 cores: 23,02 → 7,28 MiB. Medido com o quantizador do Pillow sem pontilhado (PSNR mediano
  39,5 dB, 5% piores abaixo de 36 dB); um quantizador melhor dá mais qualidade no mesmo tamanho. O risco é
  faixa visível em degradês: miras, vinhetas, sombras.
- Metade da resolução para lado ≥ 256: 23,02 → 15,59 MiB. O menu de spawn desenha os ícones a 128 px, então
  os 190 ícones `entities/*.png` de 256 perdem pouco; para o resto o tamanho na tela depende do Lua de cada
  addon e não dá para medir pelos arquivos.

O programa já tem compressão de PNG com perda nos modos Clássico e Magick (reduz e quantiza); o assistente
a desliga (`MainWindow.Wizard.cs`, `CompressPNG = false`).

## MDL (82,82 MiB, 3.984 arquivos)

arc9 52,1 MiB, simfphys 13,8, m9k 11,1, LVS 4,0, GLIDE 1,8. Versões 48 (3.198), 49 (705) e 44 (81).

| Parte do arquivo | Tamanho | Parte |
|---|---:|---:|
| Animações (descritores + dados) | 53,23 MiB | 64,3% |
| Ossos (ossos 6,7 MiB; tabela linear de ossos 3,4 MiB em 1.423 arquivos; segundo cabeçalho 1,0) | 11,32 MiB | 13,7% |
| Tabelas de bodygroups, models e malhas | 6,23 MiB | 7,5% |
| Sequências (descritores, eventos, pesos por osso) | 5,95 MiB | 7,2% |
| Materiais e skins | 1,75 MiB | 2,1% |
| Nomes (tabela de texto) | 1,67 MiB | 2,0% |
| Cabeçalho | 1,55 MiB | 1,9% |
| Hitboxes, attachments, keyvalues | 1,13 MiB | 1,4% |

Animação de vértice (flex): 0. Divisão por posição no arquivo, a partir dos endereços do cabeçalho.

- Concentrado: 321 arquivos têm metade ou mais em animação e somam 63,1 MiB (54,0 de animação). Os 100
  maiores somam 45,8 MiB. `models/weapons/csgo` 24,4 MiB, `models/weapons/arc9_fas` 20,9, `v_m249.mdl`
  sozinho 4,35. São os models de primeira pessoa com todas as animações da arma.
- 15.409 animações, 273 mil quadros; 48% dos quadros a 30 fps e quase todo o resto entre 30 e 41 fps.
- Dos 51,55 MiB de dados de animação: 44,4 MiB são as sequências de valores por osso e eixo (1,04 milhão),
  2,7 poses fixas, 2,3 ponteiros, 2,1 cabeçalhos por osso. Sem regras de IK, sem blocos externos (`.ani`).

Técnicas:

| Técnica | Dados de animação | Economia | Perda |
|---|---:|---:|---|
| Hoje | 51,55 MiB | | |
| Blocos de animação idênticos guardados uma vez (dois descritores apontam para o mesmo dado) | 43,03 MiB | −8,52 MiB | nenhuma |
| O mesmo + sequências de valores idênticas dentro do bloco guardadas uma vez | 41,90 MiB | −9,65 MiB | nenhuma |
| O mesmo + ignorar variações de até ±2 unidades | 39,25 MiB | −12,3 MiB | com perda, ganho pequeno |
| O mesmo + metade dos quadros | 25,57 MiB | −26,0 MiB | com perda |
| Recodificar as sequências | igual | 0 | (o codificador do studiomdl refeito dá os mesmos bytes em 10 MB conferidos) |
| Tirar a tabela linear de ossos | | −3,4 MiB | muda o caminho de código do motor em 1.423 models |

- Os blocos repetidos existem porque o mesmo arquivo de animação entra em várias sequências do model
  (`fire`, `fire_iron`...) e o compilador grava uma cópia para cada uma. 145 arquivos concentram 9,4 MiB do
  ganho; arc9 −8,9 MiB, m9k −0,65.
- O endereço do dado no descritor é um inteiro relativo sem limite, então apontar dois descritores para o
  mesmo bloco é o que o formato já permite. Dentro do bloco o ponteiro é de 16 bits e só para a frente; a
  simulação respeita isso e o limite não tira nada.
- Custo: o arquivo encolhe no meio, então tudo que vem depois dos dados muda de lugar e os endereços de
  ossos, attachments, hitboxes e descritores para a tabela de nomes têm de ser corrigidos. Arquivo com
  estrutura fora do esperado fica como está. Conferência: decodificar todas as animações antes e depois e
  comparar, e teste no jogo.
- Metade dos quadros corta movimentos de 1 ou 2 quadros (coice do tiro) em models vistos de muito perto.
  Não recomendo sem comparação na tela.
- Tabela linear de ossos: é uma segunda cópia dos ossos que o motor usa por velocidade; models antigos
  (versão 44) não têm e funcionam, mas tirar muda como 1.423 models são lidos por 3,4 MiB. Não recomendo.

## PHY (20,39 MiB, 3.181 arquivos)

simfphys 15,5 MiB (2.211 arquivos, a maioria destroços de veículos), LVS 2,1, GLIDE 1,9, m9k 0,5, arc9 0,4.
Sem concentração: o maior tem 57 KiB, a média é 6,4 KiB, os 100 maiores somam 2,6 MiB.

| Conteúdo | Quantidade | Tamanho |
|---|---:|---:|
| Triângulos das peças convexas (16 bytes cada) | 643.808 | 9,82 MiB |
| Pontos (16 bytes cada) | 358.309 | 5,47 MiB |
| Nós da árvore de colisão (28 bytes) | 43.077 | 1,15 MiB |
| Cabeçalhos das peças | 23.148 peças em 3.219 sólidos | 0,35 MiB |
| Texto (massa, material, juntas) | | 0,77 MiB |
| Cabeçalhos dos sólidos e envoltórias dos nós internos | | 2,83 MiB |

- Peças convexas: média de 15,5 pontos e 27,8 triângulos; 57% têm até 8 pontos, 78% até 16, 98% até 64.
- Pontos repetidos dentro do mesmo sólido: 0. Os pontos já são compartilhados entre as peças, então não
  há o que tirar sem perda.
- Com perda (estimativa pelo número de pontos): limitar cada peça a 32 pontos −4,1 MiB (2.705 peças), a 16
  −6,8 MiB (5.072), a 8 −9,5 MiB (9.948). Muda a forma de colisão de veículos, rodas e destroços. Não
  recomendo: menos de 1% da bancada.

## OGG (32,15 MiB, 523 arquivos, 13,0 min)

Todos Vorbis. O arc9 tem 30,7 MiB (405 arquivos, 386 kbps em média): sons do CS:GO em qualidade máxima.

| Taxa e canais | Arquivos | Tamanho | Duração | Bitrate médio |
|---|---:|---:|---:|---:|
| 44.100 Hz estéreo | 317 | 27,70 MiB | 9,3 min | 417 kbps |
| 44.100 Hz mono | 205 | 4,44 MiB | 3,7 min | 166 kbps |
| 22.050 Hz mono | 1 | 0,01 MiB | | 71 kbps |

- 517 dos 523 têm até 5 s (95% dos bytes): tiros, recargas, cliques.
- Custo fixo por arquivo: 1,89 MiB são as tabelas do codec (3,6 KiB por arquivo), 0,36 MiB de páginas,
  0,10 MiB de comentários. Não há metadado relevante para tirar sem perda.

Recodificar com a mesma taxa e os mesmos canais, trocando só quando fica pelo menos 5% menor:

| Qualidade Vorbis | Tamanho | Economia | Arquivos trocados | Distância espectral (mediana / 5% piores) |
|---|---:|---:|---:|---|
| q6 (~190 kbps estéreo) | 14,70 MiB | −17,5 MiB (−54%) | 405 | 0,9 / 3,7 dB |
| q5 (~160 kbps) | 12,64 MiB | −19,5 MiB (−61%) | 416 | 1,2 / 4,8 dB |
| q4 (~130 kbps) | 10,67 MiB | −21,5 MiB (−67%) | 420 | 1,4 / 6,4 dB |
| q3 (~110 kbps) | 10,04 MiB | −22,1 MiB (−69%) | 438 | 1,5 / 7,4 dB |

## MP3 (22,96 MiB, 591 arquivos, 14,9 min)

m9k 12,5 MiB (463 arquivos, 300 kbps em média: gravados a 320 kbps), simfphys 9,1 (rádio da polícia 7,5 MiB
a 145 kbps), LVS 0,7, GLIDE 0,6.

| Taxa e canais | Arquivos | Tamanho | Duração | Bitrate médio |
|---|---:|---:|---:|---:|
| 44.100 Hz mono | 228 | 11,95 MiB | 6,7 min | 248 kbps |
| 44.100 Hz estéreo | 213 | 7,58 MiB | 3,8 min | 278 kbps |
| 22.050 Hz mono | 135 | 3,12 MiB | 4,1 min | 106 kbps |
| 22.050 Hz estéreo e 11.025 Hz | 15 | 0,32 MiB | 0,3 min | |

- 543 dos 591 têm até 5 s (73% dos bytes).
- Etiquetas ID3: 0,03 MiB, nenhuma capa. Nada para tirar sem perda.

Recodificar em bitrate constante, mesma taxa e canais, trocando só quando fica pelo menos 5% menor:

| Bitrate por canal | Tamanho | Economia | Arquivos trocados | Distância espectral (mediana / 5% piores) |
|---|---:|---:|---:|---|
| 96 kbps (192 estéreo) | 12,72 MiB | −10,2 MiB (−45%) | 519 | 1,9 / 6,8 dB |
| 80 kbps (160 estéreo) | 11,03 MiB | −11,9 MiB (−52%) | 535 | 2,5 / 9,3 dB |
| 64 kbps (128 estéreo) | 8,95 MiB | −14,0 MiB (−61%) | 566 | 2,8 / 11,4 dB |
| 48 kbps (96 estéreo) | 6,72 MiB | −16,2 MiB (−71%) | 591 | 3,9 / 15,5 dB |

O que custa em OGG e MP3:

- É perda sobre perda: o som já passou por um codec e passa por outro. Em 160 kbps estéreo a diferença
  costuma ser inaudível em tiros e recargas, mas é uma mudança de qualidade, não uma reorganização.
- O MP3 recodificado começa 1.105 amostras depois (25 ms a 44.100 Hz, medido), o atraso do codificador.
  Dá para compensar cortando esse trecho antes de codificar; sem isso o som da arma atrasa 25 ms.
- A distância espectral é a mesma família de medida do perfil WAV adaptativo, mas aqui serve só de
  indicação: não substitui ouvir no jogo.
- Não mudar canais: som estéreo não é posicionado no espaço pelo motor, mono é; trocar muda o
  comportamento.
- O programa já recodifica MP3 e OGG nas abas avançadas (`MP3Edit`, `OGGEdit`); o assistente os desliga.
  Ligar no assistente é decisão do Luis (hoje todos os perfis usam só o WAV Adaptativo conservador).

## Fora dos cinco tipos

- `.xbox.vtx`: 9,1 MiB removíveis sem perda (acima).
- `.wav` idênticos: 223 arquivos, 31,2 MiB. Todos já estão em PCM16 22.050 Hz mono, o formato de
  referência do Adaptativo conservador; 101 deles (19,6 MiB) têm marcas de loop. O perfil não achou nada
  menor dentro da regra de qualidade.
- `.lua`: 18,5 MiB idênticos; "Minificar Lua" fica desligado no Custo-benefício. Não medido aqui.

## Como foi medido

Scripts Python só de leitura sobre `C:/GModLab/bench/mcb`; as técnicas rodaram em cópias numa pasta
temporária. PNG com oxipng 10.2 e Pillow 12.3; áudio com ffprobe/ffmpeg (libvorbis, libmp3lame); MDL e PHY
com leitores próprios das estruturas (`studiohdr_t` versões 44 a 49, superfície compacta do IVP). O leitor
de animação foi conferido recodificando 10 MB de sequências com o algoritmo do studiomdl: mesmos bytes.
Os scripts e os resultados por arquivo estão na pasta de notas da sessão
(`side-session-notes/.../untouched-files-2026-11-10`), fora do repositório.

Limites da medição:

- 3.225 blocos de animação vazios ou com um único registro não foram lidos pelo simulador; somam 0,2 MiB.
- A divisão do `.mdl` por parte é por posição no arquivo; as tabelas de malha e a de sequências podem
  estar trocadas em alguns KiB entre si.
- As estimativas com perda (paleta, metade da resolução, quadros, colisão) são de tamanho; nenhuma foi
  julgada na tela.
