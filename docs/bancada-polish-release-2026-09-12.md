# Bancada C2 — polimento entregue

Implementação do polimento aprovado a partir da auditoria de 12/09/2026. Mudanças de produção restritas a App.xaml e MainWindow.xaml.

## Correções

- Botões e campos de uma linha com altura consistente; caminhos com tooltip completo e sem barra horizontal ocupando a linha. Campos continuam editáveis e com deslocamento de texto.
- Mídia: origem larga, duas colunas úteis, botão Comprimir e diagnóstico acessíveis na barra fixa.
- Modelos: preset/sufixo próximos da origem, ferramentas em linhas largas e painel de execução separado da rolagem de parâmetros.
- Descompactar: destino próximo da origem, opções longas quebrando linha, marca do checkbox centralizada e Open Output completo.
- Juntar: origem, nome e destino agrupados; ações fixas. Mapas: etapas e Cancelar agrupados na barra, escopo detalhado em expansão acessível.
- Exata e Pipeline: ações fixas; relatórios continuam alinhados pelo topo.
- Completa: mais espaço central em janela menor, mesmo com atividade aberta.
- Explicações com quebra de linha. Identidade visual e oito ferramentas visíveis preservadas. Tradução integral não foi incluída neste lote.

## Verificação

Evidência em `.superpowers/bancada-polish-20260912/`:

- RED inicial: `alignment-red.log`, 57 falhas de geometria; regressões adicionais reproduziram caminhos estreitos, legendas, explicações e marcas de checkbox.
- `alignment-final2.log`: PASS, oito abas em 1008×600 e 1280×720, opções expandidas. Confere geometria contra ancestrais, fonte efetiva de legendas, campos de ferramentas, rótulos de opções e viewport vertical do texto.
- `activity-check.log`: mesmo gate PASS com texto no painel compacto de atividade.
- `render-final2/`: 96 renderizações do MainWindow real — duas áreas, oito abas, padrão/expandido, topo/meio/fundo. Capturas anteriores preservadas como evidência intermediária.
- `smoke-final.log`: 16 combinações aba/tamanho, seis estados, inventário real somente leitura, preflight rejeitando origem inválida e invalidação do resultado ao editar.
- `tests-final.log`: **250 PASS, 1 SKIP, 0 FAIL**, total 251. O teste ignorado de cadeia real empacotada não foi apresentado como executado.
- Revisão independente: 144 controles nomeados preservados; parâmetros, defaults, handlers, bindings e bloqueios herdados conferidos contra preimages. Dois achados visuais finais corrigidos e conferidos novamente em código e imagem.
- `release.log`: release oficial por `build_release_wpf.ps1`, exit 0.
- EXE publicado iniciou em processo diagnóstico oculto: PID 33832, InputIdle=true, HasExited=false. Nenhuma ação de compressão acionada; somente esse processo foi encerrado após a verificação.

Limites: a automação nativa Windows continua indisponível (`native pipe`, os error 2). Portanto a navegação foi testada no componente WPF real, não por cliques na janela do EXE publicado. Escalas DPI do Windows e leitor de tela não foram certificados. As renderizações são diagnósticos WPF, não screenshots de desktop. Permanecem avisos preexistentes do SDK/dependências no build; não houve atualização de dependências.

## Artefato e preservação

EXE: `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`

- Tamanho: 220.326.990 B.
- SHA-256 EXE: `9DD840C40CDF5EA7C790EE4596C481269BE5BFA5516D14C0CB169A36FEB85626`.
- MainWindow.xaml: `3A8906DE0A31079D3A83DA7E411DA462B68ACB1D7EA09C2F71C591D6D418FA49`.
- App.xaml: `A93FAFEC963CE058744EBA166B028D67D6295981010C5183271CBBE3F69591C7`.
- Tools/codecs ZIP: `03E2E8A59C2BD53AA79FE4FBA37BCD6D5BA6B74F3C2D4F1C407C472593933735`, idêntico antes/depois.
- Rollback do EXE anterior e preimages dos dois XAML: `.superpowers/bancada-polish-20260912/preimages/`.
- Nenhuma transformação de addon, codec, baseline, pointer ou artefato congelado. Nenhum commit abrangente da árvore previamente suja.

Para testar: abrir o EXE publicado, alternar as oito abas, reduzir a janela e expandir as opções. A compressão mantém os mesmos comandos e parâmetros anteriores.
