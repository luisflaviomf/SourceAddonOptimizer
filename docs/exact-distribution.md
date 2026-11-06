# Distribuição exata experimental (WPF)

A aba **Distribuição exata** exporta os bytes de um addon para uma nova pasta. Pode receber um original ou a saída de Models/Compress. Não altera configurações de qualidade, não instala o candidato e não abre o jogo.

1. Selecione a pasta de entrada, já descompactada. Entradas com GMA/BIN ou runtime já empacotado são recusadas.
2. Informe uma saída inexistente; o último nome da pasta aceita apenas `a-z`, `0-9`, `_` e `-`. As sugestões da UI atendem a esse contrato.
3. Selecione a instalação/diretório da engine Garry's Mod, usada para identificar sobreposições. Saída e work devem ficar fora da instalação. Uma entrada instalada pode ser lida sem alterações.
4. Exporte e consulte progresso, log, saída e `candidate-report.json`. O botão Cancelar encerra apenas o worker desta execução e seus filhos. Falha ou cancelamento deixa qualquer saída parcial sem aprovação; repita com outra pasta nova.

O resultado é `OFFLINE_PENDING_GMOD`: reconstrução exata verificada offline, ainda pendente de validação no runtime alvo. O relatório separa bytes da entrada, tamanho do candidato, economia líquida do codec e exclusões. A política deste projeto exclui `*.dx80.vtx` e `*.sw.vtx` da nova distribuição e preserva DX90; essa redução não conta como compressão. Arquivos sem ganho líquido permanecem nativos.

As famílias codificadas incluem bootstrap Lua e suporte à reconstrução/cache no GMod. A primeira carga pode custar tempo e disco. Valide reconstrução fria/quente, integridade, montagem e comportamento visual/funcional antes de ativar manualmente. Não modifique originais, baselines, candidatos aprovados ou rollbacks para aplicar a política.

Ao copiar o candidato para `garrysmod/addons`, preserve exatamente o nome final da pasta exportada. Esse nome faz parte do contrato/CID e dos caminhos de suporte do runtime; não renomeie a pasta do candidato.

No **Pipeline**, marque **Distribuição exata no final (experimental)** para encadear Models → Compress → exportação exata. A opção começa desativada; ela e o diretório da engine são persistidos. A exportação só inicia após sucesso das duas etapas nativas, cria uma pasta irmã nova e preserva o intermediário. “Abrir output” aponta para o candidato exato apenas quando o worker e seu relatório confirmam sucesso. O resumo guarda saídas nativa/exata e relatório separadamente. A redução de qualidade escolhida nas etapas anteriores permanece responsabilidade dessas etapas.

O work de cada exportação fica em `%LOCALAPPDATA%\GmodAddonOptimizer\work\exact_<id>`. O log persistente fica ao lado (`.log`) e é copiado para `wpf-worker.log` dentro do work quando essa pasta existe.

## Worker e release

```text
SourceAddonOptimizerWorker.exe exactpack <entrada> --output <nova_saida> --work <novo_work> --engine-root <engine>
```

O worker imprime `EXACT_PROGRESS: fase|indice|total|caminho`, `EXACT_REPORT: caminho` e `EXACT_OUTPUT: caminho`. O WPF usa argumentos estruturados e UTF-8, exige saída e relatório esperados e nunca interpreta apenas exit code 0 como aprovação de runtime.

Use `build_release_wpf.ps1` para o release oficial. O pacote inclui os fontes Python autenticados e templates como arquivos em `_internal`, pois o produtor verifica seus próprios bytes. A versão dos tools é 0.1.19; o instalador extrai por hash de conteúdo, então a versão extraída anterior fica disponível para o executável anterior. Antes de publicar/limpar, preserve o executável e ZIP anteriores. Validação do build não substitui testes do worker empacotado, inspeção da UI publicada e validação do GMod.
