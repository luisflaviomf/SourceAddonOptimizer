# Compressão completa

A primeira aba do **GMod Addon Optimizer WPF** reúne o fluxo de processamento
do produto, sem exigir que o usuário passe por Models, Compress ou Distribuição
exata. As abas anteriores continuam disponíveis e suas configurações não são
usadas como perfil implícito desta aba.

## Uso

1. Abra `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`.
2. Em **Compressão completa**, selecione a raiz de um addon nativo descompactado
   e uma pasta final nova. O nome final aceita `a-z`, números, `_` e `-`.
3. Informe a instalação do Garry's Mod, que contém a pasta `garrysmod`.
   Configure/detecte Blender e `studiomdl.exe` na própria aba uma vez. Addons
   sem MDL não precisam da etapa Blender.
4. Clique **Comprimir addon completo**. Progresso, log, economia e botões para
   abrir candidato/relatórios ficam na mesma aba.
5. Valide o candidato no jogo antes de distribuir. Ao copiar para `addons`,
   mantenha exatamente o nome da pasta final. Não monte simultaneamente o
   original e o candidato durante a validação.

Não passe um candidato já comprimido pelo fluxo novamente. Não há instalação,
ativação ou promoção automática de baseline.

## Perfil explícito `complete-safe-native-exact/v1`

- Cópia de staging com inventário SHA-256 antes/depois. Originais não são editados.
- Models: preset **Safe** já existente no produto (`normal`, ratio 0,75,
  merge 0, autosmooth 45, SMD); uma tarefa por etapa, strict, restauração de skins
  e TRI_STRIP. Sem políticas experimentais específicas de carros, planar,
  resume ou overwrite. Ausência de MDL é uma etapa não aplicável, não falha.
- VTF: pipeline genérico existente com política própria 2x/min8 e proporção
  preservada; mantém suas proteções de FX/alpha e casos fora de domínio.
- PNG: receita histórica 2x/min8, selecionada quando não aumenta bytes.
- WAV: resample somente PCM16/44100 mono/stereo para PCM16/22050, sem downmix.
  Exige conversão bem-sucedida, duração equivalente dentro de um sample de saída,
  canais preservados e prova dos loops/cues escalados e LISTadtl. Metadados fora
  do domínio ou arquivos acima de 64 MiB permanecem para o estágio exato.
- Distribuição exata obrigatória no final: VVD P1/portable, VTF P2/LZMA,
  MDL/DX90 VTX/WAV LZMA conforme elegibilidade e economia líquida, com os codecs
  congelados já integrados. DX80/SW não entram na distribuição final.
- Outros tipos permanecem nativos. Não é uma ativação dos adaptadores de pesquisa
  MDL H, VTX B/N/Q/V/Y, VVD G/K/O/X ou static-flex: eles não foram promovidos
  implicitamente ao produto. Não reproduz necessariamente o candidato TacRP
  de laboratório de 243.396.740 B.

**O fluxo inteiro não é lossless:** Models e redução de resolução/áudio podem
alterar qualidade. A distribuição exata reconstrói o intermediário selecionado,
não os bytes brutos anteriores à redução nativa. Economia varia por addon.

## Falhas e evidência

Falhas/cancelamento impedem a emissão do relatório de conclusão. Cancelamento
de mídia aguarda a operação corrente terminar, sem liberar a UI para outro
processamento sobre contextos compartilhados. Fechar a janela durante execução
solicita cancelamento e aguarda o encerramento seguro.

Relatórios em `%LOCALAPPDATA%/GmodAddonOptimizer/work/complete_<id>`:

- `complete.log`, `source-pins.json`, `final-pins.json`;
- `full-compression-report.json`, com perfil, bytes, tempo e hashes de evidência;
- `models-work/logs`, intermediários nativos e `exact/candidate-report.json`.

As exclusões já realizadas pelo estágio Models aparecem na diferença entre
entrada e intermediário; o relatório exato contabiliza apenas exclusões de sua
própria entrada. Não some os dois tipos de economia como se fossem independentes.

Saídas parciais/evidências não são apagadas automaticamente. Use uma nova pasta
para repetir uma execução rejeitada. Resultado offline é `OFFLINE_PENDING_GMOD`;
isso não substitui validação funcional/visual nem corrupt/cold/warm do candidato.
