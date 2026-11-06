# Verificação C2

- Renderer isolado: build sem erros; permanece o aviso preexistente do SDK sobre .NET 6 fora de suporte.
- Empty/Setup/Running/Result renderizados em 1280×720 e 1024×720, oito renders por revisão, sem erros. Inspeção visual das oito telas; ações principais dentro da área visível.
- Revisão r2 altera somente o título da tabela do resultado para Recursos processados, evitando confundir recursos lógicos com arquivos físicos compactados. Renderizações finais em renders-c2/C2-r2 e renders-c2/fit1024-r2; resultado r2 reinspecionado.
- Galeria c2.html: cinco seletores executados em teste JavaScript com DOM simulado; imagens e links correspondentes existem. Não é teste de navegador nem de interação do produto.
- Contraste das cores preservadas: texto principal/fundo 14,09:1; secundário/superfície 6,77:1; texto do botão/acento 8,19:1. Não constitui auditoria completa de acessibilidade.
- MainWindow.xaml, App.xaml e EXE publicado mantêm os hashes de AUDIT.md; A/B/C mantêm os hashes de VERIFICATION.md. Nenhuma mudança de processamento, baseline ou addon.

SHA-256 do C2-Bancada-guiada.xaml usado nos renders finais:
59E2C9CFD94D0E7F9F012F3230BB72DE8BEBA316313D5BCDDC1B3A649FCE06A8

Artefatos de apresentação, com dados ilustrativos e backend desconectado. Aprovação do redesign no produto ainda pendente.
