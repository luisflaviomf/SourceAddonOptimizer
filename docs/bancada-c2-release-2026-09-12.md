# Bancada C2 — implementação e release

Implementada a C2 aprovada com navegação permanentemente visível: Compressão completa, Modelos, Mídia e recursos, Descompactar, Juntar addons, Mapas, Distribuição exata e Pipeline.

A compressão completa usa os controles/handlers existentes, campos largos, estados reais, resumo e ações fixas. As sete ferramentas secundárias preservam seu conteúdo funcional; foram alterados títulos, navegação e tema global, não redesenhados todos os seus formulários internos.

## Comportamento

- Inventário de origem somente leitura, assíncrono, até 1.000 arquivos, sem seguir reparse points descendentes. A lista não declara elegibilidade de codec; a validação real continua no runner. Itens inacessíveis ou removidos durante a leitura podem impedir a listagem, mas não alteram a origem nem o processamento.
- Estado de processamento bloqueia edição; cancelamento/falha nunca aparecem como candidato concluído.
- Mudar origem, saída, jogo ou ferramentas invalida o candidato anterior na interface.
- Resultado continua pendente de validação no GMod. Redução nativa não é apresentada como lossless.

## Evidência

Pasta: `.superpowers/bancada-c2-20260912/`.

- `tests-final.log`: 250 PASS, 1 SKIP, 0 FAIL.
- `smoke-final.log`: oito abas em 1280×720 e 1024×720, seis estados, inventário real e clique real de preflight rejeitando origem inexistente. Inclui regressão do recorte vertical do texto. Não executa codecs nem comprime addons.
- `smoke-final/`: renderizações da MainWindow real; execução com atividade aberta também verificada em 1024×720. Estados de sucesso/cancelamento são estimulados no harness, não apresentados como uma nova execução corpus-wide.
- `release.log`: release pelo `build_release_wpf.ps1`, concluído.
- Revisão independente: sete abas secundárias preservadas; controles nomeados/handlers mantidos; correções de scroll horizontal, contraste e recorte de caminhos verificadas.
- Executável publicado iniciado em processo de diagnóstico oculto: PID 35136, InputIdle=true, sem saída prematura; nenhum botão acionado. Somente esse processo de diagnóstico foi encerrado depois.
- Automação interativa desktop indisponível: native pipe ausente (os error 2). Portanto não foi realizada navegação interativa na janela do EXE publicado; a verificação de navegação foi na MainWindow real pelo harness WPF.

## Release e preservação

EXE: `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`

- Tamanho: 220.323.918 B.
- SHA-256: `F0198AA5F29D6DE2F153AEC872C9AE29575733E6981A8F2BFD588A266D6634CC`.
- ZIP de tools/codecs antes e depois: `03E2E8A59C2BD53AA79FE4FBA37BCD6D5BA6B74F3C2D4F1C407C472593933735` — idêntico.
- Rollback do publish anterior: `.superpowers/bancada-c2-20260912/publish/` (EXE + PDB).
- Preimages dos três arquivos existentes editados e ZIP anterior em `.superpowers/bancada-c2-20260912/preimages/`.
- Sem alterações em originais, baselines, addons instalados ou codecs congelados. Sem commit abrangente da árvore suja.

Permanecem avisos preexistentes do SDK: .NET 6 fora de suporte, advisories de Magick.NET e IL3000 no toolchain. Nenhuma atualização de dependência ou transformação de codec foi incluída nesta mudança de interface.

Para testar: abra o EXE publicado, confira as oito ferramentas na barra e use Compressão completa para escolher addon e uma saída nova. A listagem é informativa; o botão de compressão continua executando o fluxo aprovado, com validação offline seguida de teste no GMod.
