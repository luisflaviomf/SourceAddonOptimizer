# Juntar addons: revisão e bancada juntada (2026-11-09)

Pedido do Luis: juntar os addons de cada pasta da bancada num addon só (M9K, ARC9, GLIDE, LVS, simfphys),
conferir se o Juntar addons está certo e rápido, e otimizar a bancada pelos addons juntados.

## Como funciona

Ferramentas › Juntar addons. A origem é uma pasta que tem pastas de addon dentro (com `addon.json`, ou só com
`lua`, `materials`, `models`, `sound`…). O resultado é uma pasta nova, por padrão `<pasta>_merged` dentro da
própria origem, com a união dos arquivos:

- arquivo igual em dois addons vira um só;
- arquivo diferente no mesmo caminho: fica o do primeiro addon por nome (regra escolhida na tela), e o
  resumo lista quantos são;
- `addon.json` novo: tipo da maioria, as duas tags mais usadas, e da lista `ignore` só os padrões que não
  tiram nenhum arquivo do resultado.

Worker `addonmerge` (`batch_merge_addons.py`), runner `Systems/Merge/AddonMergeRunner.cs`, tela
`MainWindow.AddonMerge.cs`.

## Defeitos encontrados e corrigidos

1. **Nome do novo addon igual ao começo do nome das origens.** Com a saída dentro da origem (o padrão), a
   busca pulava toda pasta cujo nome começasse pelo nome do novo addon, para não juntar de novo o resultado
   de uma junção anterior. Chamar o novo addon de `lvs` na pasta do LVS achava **0 addons**; `arc9`, 0;
   `simfphys`, 3 de 9. Agora o `addon.json` do resultado leva `"merged_from": [pastas de origem]` e só fica
   de fora a pasta que tem essa marca. O gmad ignora a chave (pacote criado e reaberto igual). O otimizador
   copia o `addon.json`, então `<nome>_optimized` também fica marcado.
2. **A pasta de destino era apagada sem conferir.** O worker esvazia o destino antes de copiar. Um novo addon
   com o nome de uma pasta de origem apagaria esse addon. Agora só é esvaziada a pasta que tem a marca (ou
   vazia); senão a tela avisa antes de rodar e o worker sai com erro 2. O `addon.json` é gravado antes da
   cópia, então uma junção interrompida pode ser refeita.
3. **Lista `ignore` lida mais larga que a do gmad.** `readme.txt` tirava qualquer `readme.txt` de qualquer
   pasta e `docs` tirava a pasta inteira; o gmad compara o padrão com o caminho inteiro. Agora usa a mesma
   função do empacotamento dos streams (`texlab.packing.ignored`).
4. **`addon.json` salvo com BOM** não era lido (perdia tipo, tags e `ignore`).
5. **Duas cópias do mesmo addon na pasta** (`<addon>` ao lado de `<addon>_optimized`, ou duas versões): a
   junção misturava as duas sem dizer nada. A busca agora avisa quando duas pastas têm pelo menos metade dos
   caminhos iguais.
6. **Otimizador media cada model duas vezes.** O otimizador olha os addons vizinhos da pasta para saber
   quais models usam as texturas de um addon. O addon juntado fica ao lado das pastas de que veio, que têm
   os mesmos arquivos. Agora o addon juntado e as suas origens não são vizinhos um do outro
   (`texlab.packing.copies_of_each_other`).
7. **Tela:** estados em inglês (`merged`, `validated-local`…) passaram para português; aviso quando a busca
   não acha addon; depois de só procurar o resumo não mostra mais "Addon unido".

Saídas feitas por versões anteriores não têm a marca: para refazer por cima, apague a pasta ou escolha
outro nome.

## Velocidade

Pelo exe publicado, saída só em pasta (sem `.gma`), lendo da bancada no disco:

| Grupo | Addons | Arquivos | Tamanho | Procurar | Juntar |
|---|---:|---:|---:|---:|---:|
| GLIDE | 6 | 3.990 | 1.178 MiB | 10,7 s | 6,9 s |
| LVS | 3 | 5.641 | 1.770 MiB | 7,2 s | 9,0 s |
| arc9 | 3 | 9.297 | 2.125 MiB | 18,9 s | 14,2 s |
| m9k | 5 | 4.607 | 617 MiB | 11,1 s | 7,6 s |
| simfphys | 9 | 18.640 | 1.252 MiB | 38,1 s | 23,4 s |

26 addons e 6,8 GiB em cerca de 2 min e meio. O tempo acompanha o número de arquivos (simfphys), não o
tamanho. Não mexi na cópia nem no hash: o que sobra é custo do sistema de arquivos por arquivo. As pastas
de destino passaram a ser criadas uma vez cada, antes da cópia.

Conferência: cada pasta juntada foi comparada com a união das origens, arquivo por arquivo (nenhum faltando,
nenhum a mais, nenhum diferente). Conflitos reais na bancada: GLIDE 4 arquivos, arc9 18 (três models de
granada que dois addons trazem em versões diferentes), m9k 4.

## Bancada juntada, otimizada pelo assistente

Perfil Custo-benefício, exe publicado, um addon juntado por grupo:

| Grupo | Antes | Depois | Redução | Tempo |
|---|---:|---:|---:|---:|
| arc9 | 2.125 MiB | 249,7 MiB | 88% | 878 s |
| GLIDE | 1.178 MiB | 103,6 MiB | 91% | 226 s |
| LVS | 1.770 MiB | 127,5 MiB | 93% | 258 s |
| m9k | 617 MiB | 79,2 MiB | 87% | 278 s |
| simfphys | 1.252 MiB | 192,8 MiB | 85% | 337 s |
| **Total** | **6.943 MiB** | **752,8 MiB** | **89%** | **1.976 s (33 min)** |

O tamanho é o mesmo dos 21 addons otimizados separados (752,2 MiB na bancada anterior; por categoria a
diferença fica abaixo de 3 MiB). Juntar não muda a redução.

Tempo: não há medição dos 21 separados com esta versão. A referência anterior, em dois passos e com uma
versão mais antiga, somava 2.748 s (46 min). O trabalho é por arquivo, então juntar tira principalmente o
custo fixo de cada addon e o número de vezes que se roda o assistente (5 em vez de 21).

O que ocupa os 752,8 MiB:

| Tipo | Tamanho | Parte |
|---|---:|---:|
| Sons `.wav` | 210,9 MiB | 28% |
| Arquivos de model (`.mdl` `.vvd` `.vtx` `.phy`: os que não vão para o stream e a malha grossa dos que vão) | 219,9 MiB | 29% |
| Geometria dos models em stream | 107,2 MiB | 14% |
| Texturas em stream | 82,6 MiB | 11% |
| `.png` | 41,3 MiB | 5,5% |
| `.ogg` e `.mp3` | 55,2 MiB | 7% |
| Lua, `.vtf`, `.vmt` e o resto | 35,7 MiB | 5% |

## No jogo

`tour.py` com os cinco addons juntados (`out/tour_m1`, `tour_m3` primeira sessão; `tour_m2` segunda), contra
as rodadas dos 21 separados (`tour_s9b`, `tour_s9d`). Janela em foco em todas as fases, nenhuma textura
falhou, o console tem as mesmas 13 linhas de aviso dos addons originais.

| Fase | Separados, 1ª sessão | Juntados, 1ª sessão | Separados, 2ª | Juntados, 2ª |
|---|---|---|---|---|
| Arma 1 (quadro médio / pior) | 10,6 / 174 ms | 8,8 / 129 ms | 4,0 / 156 ms | 3,4 / 117 ms |
| Arma 2 | 12,9 / 113 ms | 9,7 / 84 ms | 4,4 / 112 ms | 5,1 / 87 ms |
| Carros longe | 20,3 / 696 ms | 15,8 / 573 ms | 11,7 / 738 ms | 8,7 / 566 ms |
| Carro perto | 16,4 / 228 ms | 10,3 / 159 ms | 9,3 / 229 ms | 8,2 / 166 ms |

Igual ou um pouco melhor; a diferença está dentro do que varia de uma rodada para outra.

A primeira rodada depois da otimização (`tour_m1`) teve travadas de 798 e 613 ms ao sacar as duas primeiras
armas. Repetida com as mesmas condições (`tour_m3`), 129 e 84 ms: era o disco lendo pela primeira vez os
arquivos recém-gravados, não a junção.

## Limites

- Juntar antes de otimizar. Juntar addons já otimizados funciona no nível dos arquivos, mas não remove
  duplicatas dos streams e deixa um manifesto por addon de origem.
- No mesmo caminho com conteúdo diferente só um arquivo sobrevive, como acontece no jogo com os dois addons
  instalados; a junção escolhe pelo nome do addon, o jogo pela ordem em que montou.
- Dois addons de origem em subpastas diferentes com o mesmo nome de pasta não são distinguidos nos
  relatórios.
