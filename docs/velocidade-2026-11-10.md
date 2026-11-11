# Velocidade da otimização (2026-11-10)

Pedido do Luis: os sons demoravam demais, a conversão de texturas para stream também, e todas as etapas
deviam ficar o mais rápidas possível. Nas duas rodadas nenhum byte do resultado muda. No fim o Luis escolheu
trocar o nível da compressão final dos arquivos de stream de textura: mesma imagem, conversão mais rápida,
arquivos de stream 3,5% maiores (seção "A compressão LZMA final").

## Resultado

Bancada juntada (5 addons, 6.943 MiB), perfil Custo-benefício, pelo exe publicado:

| Grupo | Antes | 1ª rodada | 2ª rodada |
|---|---:|---:|---:|
| arc9 | 878 s | 360 s | 288 s |
| GLIDE | 226 s | 116 s | 106 s |
| LVS | 258 s | 147 s | 136 s |
| m9k | 278 s | 126 s | 84 s |
| simfphys | 337 s | 193 s | 171 s |
| **Total** | **1.976 s (33 min)** | **942 s (15,7 min)** | **785 s (13,1 min)** |

Saída: 725,4 MiB, os 42.642 arquivos idênticos byte a byte nas três rodadas. Com a troca do nível da
compressão final a saída passou a 728,3 MiB.

Na rodada em sequência o GLIDE marcou 161 s, com a etapa de modelos (que a 2ª rodada não tocou) também mais
lenta: foi a máquina naquele momento. Repetido sozinho deu 106 s, que é o número da tabela; com os 161 s o
total seria 840 s.

## O que mudou

### Sons (perfil Adaptativo)

Os sons tomavam cerca de 42% do tempo. Cada som gastava uns 2 s de processador, quase tudo para abrir o
ffmpeg: abrir o programa custa cerca de 150 ms nesta máquina, e cada som abria em média oito vezes (linha
de base, candidatos e a decodificação de cada um para medir a qualidade).

Agora os passos de muitos sons vão no mesmo processo (`Systems/Audio/BatchedFfmpegAdaptiveAudioBackend.cs`):
cada passo vira uma entrada e uma saída da mesma linha de comando, com os argumentos do comando de um som
só (`FFMpegSystem.Build*`). O fluxo de cada som pede juntos os passos que não dependem um do outro, então
são três rodadas por som. Um processo que falha (um arquivo ilegível derruba o comando inteiro) é refeito
passo a passo com os comandos antigos.

- 24.674 passos em 1.565 processos (eram 24.674 processos).
- Os 3.150 sons da bancada: 832 s → 105 s medidos sozinhos, todos idênticos.
- O que sobra é trabalho de verdade (reamostragem do soxr, medidas de qualidade): a máquina fica com os
  núcleos ocupados.

### Conversão de texturas para stream, 1ª rodada

Era 80% da etapa de texturas do ARC9. Numa textura 2048 a maior parte do tempo era a busca do passo de
quantização, que reconstrói a imagem várias vezes em numpy.

- A reconstrução (quantizar + wavelet inversa) e o erro contra a original rodam em Rust (`gmo_ts_recon`,
  `gmo_ts_sqerr_*` em `native/gmo_native/src/tstream.rs`).
- As duas buscas (croma inteira e croma reduzida) andam juntas e o plano de luminância é reconstruído uma
  vez por passo; um passo já medido não é medido de novo.
- O tamanho dos dois candidatos só é calculado (uma compressão cada) quando os dois atingem o alvo, que é
  quando ele decide; mapas de normal têm um candidato só.
- O codificador usa a transformada que a busca já calculou.

ARC9, mesmo ambiente: conversão 552 s → 199 s, os 1.041 blobs idênticos; etapa de texturas 640 s → 316 s.

### Conversão de texturas para stream, 2ª rodada

Pedido do Luis depois da 1ª: focar nesta etapa. Perfil de 41 texturas reais (m9k, um processo, 24,8 s)
depois da 1ª rodada: compressão LZMA final 28%, reconstrução 17%, estimativa de tamanho dos dois candidatos
15%, e boa parte do resto em numpy (quantizar, transformada direta, planos de cor).

- **Mais numpy em Rust**, tudo bit a bit igual: a transformada direta (`gmo_ts_dwt`, 171 → 33 ms num plano
  2048), a quantização com os bytes de cada banda e o mapa de ruído (`gmo_ts_put_band`), os planos Y/Co/Cg
  e as normais unitárias (`gmo_ts_ycocg`, `gmo_ts_unit_normals`). O codificador não monta de novo os planos
  que a busca já transformou.
- **Reconstrução numa passada por nível.** A primeira versão em Rust montava planos intermediários inteiros,
  como o numpy. Agora cada nível da wavelet inversa anda linha a linha sobre buffers que ficam no cache; o
  quantizador não tem desvio no sinal e o arredondamento não chama biblioteca. Plano 1024: 7,3 → 4,0 ms.
- **Média do erro em Rust** (`gmo_ts_mse_*`): segue a soma em pares do numpy (blocos de até 128 valores com
  oito somas parciais, metades cortadas em múltiplo de oito), então dá o mesmo float64 sem montar o vetor de
  erros.
- **Mesmo passo, croma reduzida.** Quando as duas resoluções de croma mantêm o alvo no mesmo passo, a
  reduzida codifica uma parte dos mesmos coeficientes e a estimativa de tamanho sempre a escolhia. Isso é o
  caso de 287 de 299 texturas com dois candidatos (amostra do m9k); a compressão dos dois para comparar só
  roda nas outras.
- **Dicionário LZMA do tamanho do pedaço.** O codificador limpava tabelas dimensionadas para 16 MB antes de
  cada pedaço (6 ms), e uma textura tem uns vinte pedaços, muitos de poucos bytes. Com o dicionário do tamanho
  do pedaço o fluxo comprimido é o mesmo (1.360 pedaços reais comparados) e o cabeçalho continua dizendo
  16 MB.

A amostra de 41 texturas: 24,8 → 10,6 s, mesmos bytes. Conversão inteira, código de antes e de agora medidos
em seguida no mesmo estado da máquina (Python de desenvolvimento):

| Addon | 1ª rodada | 2ª rodada |
|---|---:|---:|
| m9k (cópia antes do stream) | 86,8 s | 46,5 s |
| arc9 (cópia antes do stream) | 232,7 s | 132,0 s |

Os blobs saem idênticos nos dois lados. A medida nova do ARC9 rodou com 15 processos em vez de 18 (a
memória livre naquele momento decidiu), o que pesa contra o código novo nessa linha.

### Processos e tela

- Modelos e análise de texturas usavam no máximo 8 processos. Agora é automático (`texlab/jobs.py`: CPUs
  lógicas menos duas, limitado pela memória livre). Modelos do simfphys: 106 s → 80 s, mesmos arquivos.
- O assistente mostra o que a etapa de texturas está fazendo ("Texturas: convertendo para stream 358 de
  1135 · …"). Antes a tela ficava no último `.wav` enquanto as texturas rodavam, e parecia que o tempo
  todo era de som.

## Onde o tempo está agora

Etapas do worker empacotado final (com a compressão nível 9) medidas sozinhas; os sons rodam junto com as
texturas no assistente:

| Etapa | arc9 | GLIDE | LVS | m9k | simfphys |
|---|---:|---:|---:|---:|---:|
| Modelos: copiar o addon | 5 s | 3 s | 3 s | 3 s | 28 s |
| Modelos: simplificar e gerar streams | 26 s | 23 s | 32 s | 18 s | 49 s |
| Modelos: empacotamento | 5 s | 2 s | 2 s | 2 s | 14 s |
| Texturas: analisar | 16 s | 14 s | 17 s | 7 s | 17 s |
| Texturas: reduzir (inclui o VTFCmd) | 89 s | 20 s | 36 s | 18 s | 22 s |
| Texturas: gravar | 4 s | 2 s | 3 s | 2 s | 3 s |
| Texturas: converter para stream | 57 s | 9 s | 17 s | 21 s | 12 s |

A linha da conversão para stream nas três medições do worker empacotado:

| Conversão para stream | arc9 | GLIDE | LVS | m9k | simfphys | Soma |
|---|---:|---:|---:|---:|---:|---:|
| Depois da 1ª rodada | 199 s | 31 s | 55 s | 70 s | 39 s | 394 s |
| Depois da 2ª rodada (mesmos bytes) | 88 s | 12 s | 21 s | 27 s | 15 s | 163 s |
| Com a compressão nível 9 | 57 s | 9 s | 17 s | 21 s | 12 s | 116 s |

As três medições foram feitas em momentos diferentes e as linhas que não mudaram de código variaram até 30%
entre elas (simplificar modelos do simfphys: 37 s numa, 49 s na outra). A primeira foi com a máquina mais
carregada. As comparações justas são as medidas em seguida, nas seções da 2ª rodada e da decisão do nível.
Hoje o maior bloco de um addon grande é "reduzir" (89 s no ARC9), que inclui o VTFCmd.

## A compressão LZMA final: tamanho contra tempo

Depois da 2ª rodada, o maior pedaço da conversão era a compressão LZMA final de cada pedaço, então em nível
9 extremo: metade do tempo de processador nas texturas pequenas e médias. Ela é sem perda: qualquer ajuste dá a mesma
imagem no jogo, só muda o tamanho do arquivo. Medido em 250 blobs reais da bancada (os 70 maiores e 180 ao
acaso; 1.360 pedaços, 317 MiB descomprimidos, um processo):

| Ajuste | Tempo | Tamanho |
|---|---:|---:|
| 9 extremo (até a 2ª rodada) | 122,8 s | 23,93 MiB |
| 9 extremo, `pb=0` | 121,5 s | −0,56% |
| 9 com `nice_len` 128 | 86,5 s (1,4x) | +1,25% |
| 9 | 57 a 61 s (2,1x) | +3,05% |
| **9, `pb=0` (escolhido)** | 51,0 s (2,5x) | +2,65% |
| 5 | 41,9 s (2,9x) | +6,0% |
| 4 | 29,9 s (4,1x) | +10,8% |
| 3 | 19,2 s (6,4x) | +17,3% |
| 1 | 17,3 s (7,1x) | +19,9% |

- O tempo do "extremo" vem de um parâmetro só, `nice_len` 273: com ele a busca do melhor encaixe roda em
  toda repetição de até 273 bytes. Mexer na profundidade da busca (`depth` 64, 128, 256) com `nice_len` 273
  custa o mesmo que o extremo. Não há ajuste intermediário de graça.
- `pb=0` deixa os arquivos 0,5% menores sem custo de tempo (os coeficientes são bytes sem alinhamento de 4).

### Decisão do Luis (2026-11-10): nível 9 com `pb=0`

`texture_stream/codec.py::LZMA_SETTINGS`. O que deu na bancada inteira, pelo exe publicado:

- **Tamanho:** saída 725,43 → 728,33 MiB (+2,90 MiB, +0,40%); redução de 89,55% para 89,51%. Os arquivos de
  stream de textura foram de 82,58 para 85,48 MiB (+3,5%). A amostra previa +2,65% e a estimativa passada ao
  Luis antes da decisão foi de +2,2 MiB: saiu um terço acima.
- **Tempo da conversão**, compressão antiga e nova medidas em seguida, duas vezes cada (Python de
  desenvolvimento, cópia do addon antes do stream): m9k 46,8 e 52,4 s → 40,7 e 43,4 s (−15%); ARC9 130,2 e
  138,0 s → 93,0 e 97,5 s (−29%). A estimativa antes da decisão era de −43% no ARC9. A compressão ficou 2,5x
  mais rápida como medido, mas com 13 a 18 processos disputando a memória ela pesava menos no relógio do
  que no perfil de um processo só.
- **Só a camada LZMA mudou.** Dos 37.822 arquivos fora do stream, 37.821 idênticos byte a byte; cada blob,
  descomprimido pedaço a pedaço, dá os mesmos bytes de antes. A exceção é uma textura de 64 px do LVS
  (`models/diggercars/shared/net_b_nm`) que passou a virar stream porque agora fica 3 bytes menor que o VTF.
- **No jogo** (`tsmp_full.py`, servidor dedicado, conteúdo empacotado pelo gmad): 15 armas, 18 veículos,
  263 PNG, 1.403 texturas decodificadas, nenhuma falha; o console é o da rodada anterior (as linhas que
  sobram são dos addons originais). O decodificador do jogo lê `pb=0` do cabeçalho de cada pedaço.
- Duas conversões seguidas dão os mesmos bytes.

Os tempos do assistente nessa rodada (arc9 248 s, GLIDE 119 s, LVS 147 s, m9k 95 s, simfphys 269 s) não
servem de comparação: a máquina estava ocupada com outras coisas (o simfphys, cuja etapa de modelos não
mudou, levou 269 s contra 171 s).

Para voltar ao nível anterior basta `LZMA_SETTINGS = {"preset": 9 | lzma.PRESET_EXTREME}`; os arquivos já
convertidos continuam funcionando com qualquer um dos dois.

## O que ainda dá para ganhar sem mudar bytes

(Continuação em `docs/velocidade-2026-11-11.md`: a etapa "reduzir" deixou de passar pelo VTFCmd nas texturas
que viram stream, e a memória reservada pelos processos auxiliares caiu de 12 GB para menos de 1 GB.)

Pouco, na conversão para stream: transformada direta numa passada (2%), miniatura de 16 px em Rust (2%),
referência do erro em 8 bits (2%). As fases "reduzir" (inclui o VTFCmd) e a simplificação de modelos ainda
não foram perfiladas a fundo.

## Conferência

- 1ª rodada: 433 testes Python, testes do Rust, 30 testes de áudio (2 novos: muitos sons juntos; um arquivo
  ilegível no meio) e os contratos WPF. 2ª rodada: 453 testes Python e os do Rust (o C# não mudou).
- `tests/test_native_texlab.py`: cada função em Rust contra o numpy, bit a bit (transformada, reconstrução,
  erros, média para tamanhos dos dois lados dos limites da soma em pares, bytes das bandas, planos de cor),
  e `choose()` inteiro com e sem a biblioteca. `tests/test_texture_stream.py`: o blob com a biblioteca, sem
  ela e sem a transformada reaproveitada; o cabeçalho de cada pedaço LZMA e o fluxo igual ao do dicionário
  de 16 MB.
- Troca do nível da compressão: 453 testes Python, a comparação dos dois resultados da bancada (acima) e o
  teste em servidor dedicado.
- As duas regras que não são prova matemática (mesmo passo → croma reduzida; dicionário do tamanho do
  pedaço) foram conferidas na bancada inteira: os 4.815 blobs saíram idênticos pelo exe publicado. Num addon
  em que uma delas desse outro resultado, a diferença seria de alguns bytes no arquivo, com a mesma imagem.
- (Corrigido em 2026-11-11.) As 28 texturas do ARC9 que saíam diferentes entre o worker empacotado e o
  Python de desenvolvimento não eram diferença de biblioteca: o motor de texturas também lê os addons das
  pastas vizinhas, e as duas rodadas tinham vizinhos diferentes na pasta de testes. Com a pasta isolada os
  dois dão os mesmos bytes. Comparações têm de usar uma pasta sem vizinhos, ou com os mesmos; a conferência
  final continua sendo pelo exe publicado.
- Medidas de tempo feitas em momentos diferentes não se comparam: a mesma etapa, sem mudança de código,
  variou 30% entre duas sessões. Antes e depois têm de ser medidos em seguida.
