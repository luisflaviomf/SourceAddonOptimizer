# Velocidade da otimização (2026-11-10)

Pedido do Luis: os sons demoravam demais, a conversão de texturas para stream também, e todas as etapas
deviam ficar o mais rápidas possível. Regra mantida: nenhum byte do resultado muda.

## Resultado

Bancada juntada (5 addons, 6.943 MiB), perfil Custo-benefício, pelo exe publicado:

| Grupo | Antes | Agora |
|---|---:|---:|
| arc9 | 878 s | 360 s |
| GLIDE | 226 s | 116 s |
| LVS | 258 s | 147 s |
| m9k | 278 s | 126 s |
| simfphys | 337 s | 193 s |
| **Total** | **1.976 s (33 min)** | **942 s (15,7 min)** |

Saída: 725,4 MiB, os 42.642 arquivos idênticos byte a byte aos da rodada anterior.

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

### Conversão de texturas para stream

Era 80% da etapa de texturas do ARC9. Numa textura 2048 a maior parte do tempo era a busca do passo de
quantização, que reconstrói a imagem várias vezes em numpy.

- A reconstrução (quantizar + wavelet inversa) e o erro contra a original rodam em Rust (`gmo_ts_recon`,
  `gmo_ts_sqerr_*` em `native/gmo_native/src/tstream.rs`). As funções devolvem o vetor que o numpy teria
  montado e a média continua no numpy.
- As duas buscas (croma inteira e croma reduzida) andam juntas e o plano de luminância é reconstruído uma
  vez por passo; um passo já medido não é medido de novo.
- O tamanho dos dois candidatos só é calculado (uma compressão cada) quando os dois atingem o alvo, que é
  quando ele decide; mapas de normal têm um candidato só.
- O codificador usa a transformada que a busca já calculou.

ARC9, mesmo ambiente: conversão 552 s → 199 s, os 1.041 blobs idênticos; etapa de texturas 640 s → 316 s.

### Processos e tela

- Modelos e análise de texturas usavam no máximo 8 processos. Agora é automático (`texlab/jobs.py`: CPUs
  lógicas menos duas, limitado pela memória livre). Modelos do simfphys: 106 s → 80 s, mesmos arquivos.
- O assistente mostra o que a etapa de texturas está fazendo ("Texturas: convertendo para stream 358 de
  1135 · …"). Antes a tela ficava no último `.wav` enquanto as texturas rodavam, e parecia que o tempo
  todo era de som.

## Onde o tempo está agora

Etapas do worker medidas sozinhas, máquina livre (os sons rodam junto com as texturas no assistente):

| Etapa | arc9 | GLIDE | LVS | m9k | simfphys |
|---|---:|---:|---:|---:|---:|
| Modelos: copiar o addon | 6 s | 2 s | 2 s | 3 s | 21 s |
| Modelos: simplificar e gerar streams | 30 s | 27 s | 32 s | 18 s | 74 s |
| Modelos: empacotamento | 4 s | 2 s | 2 s | 2 s | 11 s |
| Texturas: analisar | 21 s | 18 s | 24 s | 11 s | 26 s |
| Texturas: reduzir (inclui o VTFCmd) | 87 s | 19 s | 33 s | 16 s | 20 s |
| Texturas: gravar | 6 s | 1 s | 3 s | 1 s | 3 s |
| Texturas: converter para stream | 199 s | 31 s | 55 s | 70 s | 39 s |

Na conversão para stream, mais da metade do que sobra é a compressão LZMA final de cada textura (nível 9
extremo). Ela define os bytes do arquivo: não dá para acelerar sem mudar o resultado. Trocar por um nível
mais rápido deixaria os arquivos um pouco maiores; é uma decisão de tamanho contra tempo, não tomada aqui.

Ainda dá para ganhar sem mudar bytes: a transformada direta em Rust (7% da conversão), a fase "reduzir" e
a simplificação de modelos, que ainda não foram perfiladas a fundo.

## Conferência

- 433 testes Python, testes do Rust, 30 testes de áudio (2 novos: muitos sons juntos; um arquivo ilegível
  no meio) e os contratos WPF.
- `tests/test_native_texlab.py`: as funções em Rust contra o numpy, bit a bit, e `choose()` inteiro com e
  sem a biblioteca.
- O worker empacotado e o Python de desenvolvimento dão resultados diferentes em 28 texturas do ARC9 na
  fase "reduzir" (versões diferentes das bibliotecas numéricas). Comparações de antes e depois têm de usar
  o mesmo ambiente; a conferência final é sempre pelo exe publicado.
