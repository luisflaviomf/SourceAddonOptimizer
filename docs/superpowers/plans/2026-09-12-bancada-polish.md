# Bancada C2 — polimento aprovado

Objetivo: corrigir os defeitos da auditoria `docs/design-audit-2026-09-12/REPORT.md`, mantendo identidade, oito destinos visíveis e processamento inalterado.

## Responsabilidades e verificação

- [x] Preservar preimages de MainWindow.xaml, App.xaml e EXE em `.superpowers/bancada-polish-20260912/preimages/`.
- [x] Criar regressão WPF real em `experiments/BancadaUiSmoke/AlignmentChecks.cs`: botões dentro dos ancestrais horizontais, sem esticamento e com legendas legíveis. Observar RED (57 falhas de alinhamento; rodada adicional de legenda detectou Open Output).
- [ ] App.xaml: regularizar altura/alinhamento/padding de botões e campos, sem limitar logs como campos de uma linha.
- [ ] MainWindow.xaml (Astra): corrigir Mídia, distribuir caminhos, quebrar rótulos longos, agrupar ações e diminuir pressão vertical na Completa. Preservar nomes, bindings, handlers e defaults.
- [ ] Executar `dotnet run --project experiments/BancadaUiSmoke/BancadaUiSmoke.csproj --no-restore -- --alignment`.
- [ ] Repetir smoke funcional e auditoria renderizada em novas pastas de evidência; inspecionar telas e regiões alteradas.
- [ ] Revisão independente de preservação funcional e dos defeitos corrigidos.
- [ ] Executar testes do projeto e release oficial `build_release_wpf.ps1`; conferir tools ZIP inalterado e EXE novo, preservando rollback.
- [ ] Testar inicialização publicada e, se acesso nativo estiver disponível, navegação. Registrar limites sem alegar teste interativo não realizado.

Sem mudanças em codecs, worker, originais, addons ativos, baselines ou parâmetros de compressão. Sem commit amplo da árvore previamente suja. Tradução completa não pertence a este lote; somente rótulos necessários à clareza e ao encaixe.

## Fechamento

Todos os itens de implementação e verificação acima foram executados. Release, hashes, resultados e limites registrados em `docs/bancada-polish-release-2026-09-12.md`. A etapa condicional de navegação nativa não pôde ser realizada: helper Windows sem native pipe. Inicialização do EXE publicado, navegação do MainWindow real e demais gates passaram. Revisão independente encerrada após correção dos dois últimos achados visuais.
