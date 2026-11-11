# Teste em servidor dedicado da bancada otimizada (2026-11-10)

Pedido do Luis: testar em servidor para ver se tudo funciona. O teste achou um defeito sério no stream de
modelos (o jogo do cliente parava de responder) e um aviso no console; os dois estão corrigidos e a
rodada final passou.

## O que o teste faz

`C:\GModLab\bench\texstream\tsmp_full.py <rodada> <pasta da bancada>`:

1. empacota cada addon otimizado com o `gmad.exe`, como a Workshop faz, e registra o que ele recusa ou
   deixa de fora;
2. sobe o servidor dedicado do laboratório com as pastas dos addons;
3. o cliente entra só com os pacotes (montados antes de qualquer Lua de addon), sem nenhuma pasta de
   addon; o Lua dos streams vem do servidor;
4. confere que todo arquivo de stream citado nos manifestos está nos pacotes;
5. saca e dispara 15 armas (as 7 do tour e 8 espalhadas pela lista do ARC9 e do M9K);
6. faz o servidor criar 18 veículos (8 do LVS, 5 do Glide, 5 do simfphys, como o menu de spawn cria) e
   confere no cliente a geometria em stream e as texturas de cada um;
7. carrega uma amostra dos PNG dos pacotes;
8. lê os consoles do cliente e do servidor atrás de erro de Lua e de conteúdo faltando.

## Rodada final (`out/mpfull4`, bancada `C:\GModLab\bench\mcb5`)

Os cinco addons juntados, otimizados de novo pelo exe publicado depois das correções: 6.943 → 725,5 MiB.

| Item | Resultado |
|---|---|
| Pacotes do `gmad.exe` | 5 de 5; nenhum arquivo recusado, nenhum deixado de fora pela lista `ignore` |
| Arquivos de stream citados nos manifestos | 4.815 de textura e 2.149 de modelo, todos nos pacotes |
| Armas | 15 de 15 na mão e disparadas, sem erro de Lua; geometria em stream pronta em 14 (1 é model comum) |
| Veículos | 18 de 18 no cliente, sem erro de Lua; geometria em stream pronta nos 18 |
| PNG | 263 de 263 carregados |
| Texturas em stream | 1.402 decodificadas, nenhuma falhou |
| Avisos de textura de um nível só no console | 4 linhas, de um arquivo original do simfphys (eram 22 na primeira rodada) |
| Erros de Lua | 1 no cliente, do M9K original (abaixo); nenhum do otimizador |

## Defeitos achados e corrigidos

### O jogo parava de responder com um carro do Glide

Ao criar o Opel Omega do Glide (484 mil triângulos), o cliente imprimia
`called mesh.Begin with a primitive count which results in too many vertices (81436, maximum=65535)` e
parava de responder; o servidor o derrubava por tempo esgotado. Reproduzido em single player: o jogo trava
dentro do motor, não no Lua (um vigia de Lua preso não disparou).

Causa: o stream de modelos guarda cada grupo em tiras de triângulos, uma malha do jogo por tira. O encoder
cortava o grupo a cada 20.000 triângulos, contando que a tira ficasse abaixo de 65.535 vértices. Triângulos
pouco ligados entre si viram até 5 índices cada, e um pedaço daquele carro deu 81.436.

- 1 model dos 2.156 em stream na bancada tinha pedaços assim (5 pedaços).
- Encoder (`model_stream/encoder.py`, `strips_for`): o pedaço cuja tira passa de 65.534 índices é dividido
  ao meio até caber. Os outros 2.155 streams saem byte a byte iguais.
- Runtime (`model_stream/runtime.lua`, `REVISION` 3): qualquer tira maior é cortada em posição par, cada
  parte começando nos dois últimos vértices da anterior. Um stream gravado pela versão antiga funciona com
  o runtime novo: o mesmo arquivo que travava agora aparece completo, sem erro.
- Testes: `tests/test_model_stream.py` roda o `runtime.lua` de verdade em LuaJIT, com o limite do jogo
  simulado, e compara os triângulos desenhados com os do stream (mesmos cantos e mesmo lado).

Um addon otimizado antes desta correção que tenha um model assim precisa ser otimizado de novo.

### Aviso no console para texturas de um nível só

O jogo exige as marcas NOLOD e NOMIP em textura gravada com um nível só; sem uma delas imprime
`Encountered VTF with 1 mip level and without NoLOD flag! Fixing..` (ou `NoMIP`) a cada carga e põe a
marca sozinho. Duas origens:

- o modo Tela gravava sem as marcas as texturas que ele reduzia para um nível só (5 arquivos do ARC9 e
  15 do M9K na bancada). Agora grava as duas (`texlab/screen_compress.py`, `_one_level_flags`);
- o stream reproduzia o cabeçalho de texturas que já eram de um nível só e sem marca no addon original
  (4 texturas de partícula do M9K). Agora a textura montada no jogo leva as duas marcas
  (`texture_stream/codec.py`).

A imagem no jogo é a mesma: são as marcas que o próprio jogo poria. Na rodada final sobram 4 linhas, de
`materials/effects/simfphys_armed/spark.vtf`, que é o arquivo original (pequeno demais para o stream e
sem redução a fazer).

## O que aparece e já é dos addons originais

Conferido nos arquivos, não numa rodada com os originais:

- `language.Add expects string as second argument!` em `m9k_mad_c4/shared.lua:171`: o arquivo é idêntico
  ao do M9K original.
- Três sons que o ARC9 cita e nenhum dos addons traz (`sightraise1.wav`, `sightlower1.wav`,
  `ak47_distant.wav`).
- 4 linhas do aviso de um nível só: uma textura original do simfphys (acima).
- O Opel Omega aparece rosa: os quatro materiais do model (`models/glide/exid/carmaterials`…) não estão em
  nenhum addon da bancada.

## Limites do teste

- Um cliente só, servidor em rede local. Os pacotes são os do `gmad.exe`, montados no cliente pelo roteiro;
  não é um download real da Workshop.
- Amostra: 15 de 216 armas e 18 de 607 veículos. O roteiro não entra nos veículos nem dirige.
- Som não é conferido além das linhas de arquivo faltando.
- As capturas dos veículos saíram com a câmera virada para cima (o recuo das armas disparadas antes), então
  mostram só a parte de cima de cada veículo; a conferência de geometria e textura é pelos estados do
  runtime, não pela imagem.
- A primeira rodada (`mpfull1`) não testou o Glide: ele esconde os veículos da lista de spawn do cliente e o
  roteiro não os achava. A segunda (`mpfull2`) parou no defeito acima. A terceira (`mpfull3`) passou com a
  correção do travamento e mostrou que o aviso de um nível só estava corrigido pela metade.
- O pior quadro do runtime de texturas foi 29 ms na terceira rodada e 179 ms na quarta (uma chamada do
  motor ao aplicar uma textura, logo depois de os arquivos serem gravados).
- A quinta rodada (`mpfull5`) repetiu o teste depois da troca do nível da compressão dos arquivos de stream
  de textura (LZMA nível 9 com `pb=0`, ver `docs/velocidade-2026-11-10.md`): 15 armas, 18 veículos, 263 PNG,
  1.403 texturas decodificadas, nenhuma falha, as mesmas linhas no console; pior quadro do runtime 72 ms.
- A sexta rodada (`mpfull6`, 2026-11-11) repetiu o teste com as texturas reduzidas indo direto para o stream
  (`docs/velocidade-2026-11-11.md`): 15 armas, 18 veículos, 263 PNG, 1.400 texturas decodificadas, nenhuma
  falha, as mesmas linhas no console; pior quadro do runtime 29 ms.
