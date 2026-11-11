# Velocidade das texturas, terceira rodada (2026-11-11)

Pedido do Luis: focar em acelerar o processamento das texturas ("não precisa chegar a 90%, acho que a gente
já chegou no limite ou quase isso"). Continua `docs/velocidade-2026-11-10.md`.

## Resultado

Bancada juntada (5 addons, 6.943 MiB), perfil Custo-benefício, pelo exe publicado:

| Grupo | 2ª rodada (10/11) | Agora |
|---|---:|---:|
| arc9 | 288 s | 195 s |
| GLIDE | 106 s | 109 s |
| LVS | 136 s | 116 s |
| m9k | 84 s | 75 s |
| simfphys | 171 s | 173 s |
| **Total** | **785 s (13,1 min)** | **668 s (11,1 min)** |

Saída: 728,33 → 726,34 MiB (redução de 89,51% para 89,54%). O GLIDE e o simfphys quase não mudam porque o
tempo deles está nos modelos e nos sons.

Desta vez o resultado muda: as texturas reduzidas que viram stream são codificadas a partir de outra imagem
(ver abaixo). Qualidade igual ou melhor, arquivos um pouco menores. O resto do addon sai idêntico byte a byte.

## O que mudou

### A textura reduzida vai direto para o stream

Nos perfis com stream (Custo-benefício e Máximo), a etapa "reduzir" preparava a imagem reduzida de cada
textura, mandava o VTFCmd comprimir em DXT e gravava um VTF. Logo em seguida a conversão para stream abria
esse VTF, decodificava o DXT e codificava de novo no formato do stream. O DXT era só um intermediário, e era
ele que custava: no ARC9, 43 dos 58 s da etapa eram o compressor DXT do VTFCmd (800 segundos de processador
somando os processos), que não dá para acelerar sem mudar os bytes porque o compressor é fechado.

Agora a imagem reduzida vai direto para o codificador do stream (`screen_compress._stream_direct`,
`texture_stream.convert(direct=...)`). O cabeçalho e o tamanho a bater são os do arquivo que seria gravado
(`texlab.vtf.planned_reduced_vtf`: mesmo cabeçalho, mesmas flags e mesmo número de níveis, com zeros no
lugar dos pixels). O VTFCmd só roda para o que continua VTF: texturas pequenas demais para o stream, as que o
codificador devolve porque o blob não ficaria menor, e os perfis sem stream, que não mudaram em nada (mesmos
bytes de antes).

Etapa de texturas inteira do ARC9, caminho antigo e novo alternados, duas vezes cada (Python de
desenvolvimento, cópia do addon original): 166 e 172 s → 117 e 118 s. Com os dois itens abaixo, 95 s.

### Dois desperdícios na conversão para stream

- **Releitura dos blobs.** Depois de os processos gravarem os blobs, o processo principal abria os mil
  arquivos de novo só para calcular o hash que entra no nome da pasta. Abrir um arquivo recém-gravado espera
  o antivírus: 12,5 s no ARC9. O hash agora vem do processo que gravou o blob (mesmo valor, mesmo nome).
- **Texturas com cadeia de mips feita pelo VTFCmd** (as que eram sem compressão) também vão direto. O VTFCmd
  sempre grava a cadeia completa até 1x1 (conferido nas 52 do ARC9), então o número de níveis é conhecido
  sem rodá-lo.

### Memória: 12 GB reservados à toa

Numa das rodadas da bancada o simfphys falhou ("Texturas e sons não terminaram"). Rodando sozinho, passava.
A causa: cada processo auxiliar que carrega o numpy reserva um buffer de uns 32 MB por thread da biblioteca
numérica (OpenBLAS), 660 MB por processo numa máquina de 20 threads, sem nunca usar. Dezoito processos
reservavam 12 GB. Com o resto da máquina ocupado (19 GB já reservados por outros programas, limite de 35 GB),
o sistema chegava ao limite e uma alocação falhava. Isso já acontecia antes desta rodada: o código antigo
tinha o mesmo pico (+15,9 GB reservados para +3,3 GB usados de verdade).

- Os processos auxiliares agora começam com a biblioteca numérica em uma thread
  (`texlab.jobs.process_pool`, usado por modelos, análise, preparação, conversão e empacotamento): 49 MB
  reservados por processo. Pico da etapa de texturas: +15,6 → +4,6 GB reservados, no ARC9 e no simfphys.
  A análise não ficou mais lenta e o plano sai idêntico; a bancada confirma os mesmos bytes.
- A contagem automática de processos olha também o que resta do limite de reserva do sistema, não só a
  memória física livre (`texlab.jobs.free_memory_gb`).
- Uma textura cuja preparação falha num processo auxiliar (falta de memória, processo que morreu) é refeita
  no processo principal, uma por vez, depois que os auxiliares terminam. Antes a etapa inteira falhava.

### Ajustes que não mudam bytes

- A preparação das imagens (decodificar, reduzir com Lanczos, gravar o PNG) saiu de threads para os processos
  da análise, que já estão abertos. As threads passavam a maior parte do tempo esperando umas pelas outras.
- As chamadas do VTFCmd são montadas pelo custo de cada imagem (número de pixels), a mais cara primeiro, em
  vez de blocos de tamanho igual na ordem da lista: uma chamada com dez texturas 2048 durava metade da fase
  inteira enquanto um terço dos processos ficava parado.

O ganho desses dois ficou pequeno (3 a 5 s em 58 no ARC9): quase todo o tempo é o compressor.

## Qualidade e tamanho do caminho direto

A meta de qualidade do stream é medida contra a imagem que entra no codificador. Antes entrava a imagem
reduzida depois de passar pelo DXT; agora entra a imagem reduzida em si.

Amostras, cada textura codificada pelos dois caminhos e o que o jogo decodifica comparado com a imagem
reduzida:

| Amostra | Arquivos de stream | Qualidade (cor) | Qualidade (mapas de normal) |
|---|---:|---:|---:|
| arc9, 58 texturas | −3,1% | 33,78 → 33,82 dB | 33,60 → 34,00 dB |
| LVS, 157 texturas | cor −6,6%, normal +5,8% | 33,94 → 33,92 dB | 33,79 → 34,20 dB |
| m9k, 37 texturas | cor −3,4%, normal +3,2% | 33,62 → 33,51 dB | 36,87 → 36,99 dB |

Bancada inteira, rodada anterior contra esta (as duas pelo exe publicado):

- Saída 728,33 → 726,34 MiB; arquivos de stream de textura 85,48 → 83,31 MiB (−2,5%).
- 4.801 texturas são stream nas duas rodadas: 754 blobs idênticos (texturas que não foram reduzidas) e 4.047
  recodificados. Umas 20 texturas trocaram de lado (viraram stream ou voltaram a ser VTF por poucos bytes).
- Dos 37.822 arquivos fora do stream, só mudaram as miniaturas de 16 px das texturas recodificadas e essas
  texturas que trocaram de lado. Nenhum arquivo de modelo, som, Lua ou PNG mudou.
- As 311 texturas em que o resultado novo mais difere do antigo, comparadas com a imagem reduzida: nos mapas
  de normal o novo está mais perto em 94 de 95 (média 27,5 → 28,4 dB); nas coloridas, equivalente (28,48 →
  28,42 dB). São as texturas difíceis, em que o próprio DXT final limita a qualidade.

No jogo (`tsmp_full.py mpfull6`, servidor dedicado, conteúdo empacotado pelo gmad): 15 armas, 18 veículos,
263 PNG, 1.400 texturas decodificadas, nenhuma falha, o mesmo console das rodadas anteriores, pior quadro do
runtime 29 ms.

## Onde o tempo está agora

Etapas do worker empacotado final medidas sozinhas:

| Etapa | arc9 | GLIDE | LVS | m9k | simfphys |
|---|---:|---:|---:|---:|---:|
| Modelos: copiar o addon | 7 s | 3 s | 3 s | 3 s | 28 s |
| Modelos: simplificar e gerar streams | 29 s | 22 s | 28 s | 14 s | 46 s |
| Modelos: empacotamento | 5 s | 2 s | 2 s | 2 s | 13 s |
| Texturas: analisar | 15 s | 13 s | 17 s | 6 s | 15 s |
| Texturas: preparar as imagens reduzidas | 17 s | 6 s | 13 s | 4 s | 7 s |
| Texturas: VTFCmd e gravação do que continua VTF | 3 s | 1 s | 2 s | 1 s | 2 s |
| Texturas: converter para stream | 56 s | 9 s | 17 s | 20 s | 13 s |
| **Texturas, etapa inteira** | 92 s | 29 s | 48 s | 31 s | 37 s |

Na medição de ontem a etapa de texturas inteira era 166, 45, 73, 48 e 55 s (388 s somando; agora 237 s):
"reduzir" (preparar + VTFCmd + gravar) era 89, 20, 36, 18 e 22 s e "converter para stream" 57, 9, 17, 21 e
12 s. Hoje o maior bloco de um addon grande é a conversão para stream (56 s no ARC9), seguida da
simplificação de modelos.

## O que ainda dá para ganhar

- A redução Lanczos (via Pillow) é o maior consumo da preparação: 70 dos 107 segundos de processador no ARC9.
  Uma versão em Rust teria de reproduzir o Pillow bit a bit.
- A análise lê muitos arquivos recém-copiados, e o antivírus pesa nessa leitura (12 s numa cópia antiga, 22 s
  numa recém-feita, mesmo código).
- Na conversão para stream o que sobra é a busca do passo e a compressão final, já tratadas ontem.

## Conferência

- 461 testes Python. Novos: o cabeçalho planejado igual ao do arquivo gravado, a seleção das texturas que vão
  direto, o perfil stream de ponta a ponta com o VTFCmd (uma textura vai direto, outra volta), a textura que
  o codificador devolve, a textura que falha num processo auxiliar e é refeita, os processos auxiliares com
  uma thread, as chamadas do VTFCmd cobrindo cada imagem uma vez.
- Perfil sem stream (Forte) no ARC9, numa pasta isolada: os mesmos bytes com o código já enviado, com o
  código final, com os processos auxiliares em uma ou em todas as threads, e com o worker empacotado.
- Armadilha do laboratório: o motor de texturas lê os addons das pastas vizinhas (modelos de outro addon que
  usam as texturas deste). Com várias cópias do mesmo addon na pasta de testes, 28 texturas do ARC9 mudavam
  de redução conforme as cópias presentes. Era isso, e não versões de biblioteca, que fazia o worker
  empacotado e o Python de desenvolvimento divergirem na anotação de ontem.
- Duas rodadas da bancada com o mesmo código (uma antes e outra depois da correção de memória): arc9, GLIDE,
  LVS e m9k idênticos byte a byte.
- O que o worker final grava nas etapas de modelos e texturas confere com a saída da bancada (linha
  "against" de `stages_all.py`).
