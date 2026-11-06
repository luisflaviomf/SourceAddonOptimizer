# Verificação da apresentação

Escopo: artefatos visuais isolados, não integração no produto.

- Renderer WPF separado compilado: zero erros. O SDK avisa que .NET 6 está fora de suporte; nenhuma atualização do produto foi realizada nesta tarefa.
- Três alternativas × três estados renderizados em 1280 × 720: nove renderizações concluídas sem erro.
- Os mesmos nove estados também foram renderizados em 1024 × 720.
- Inspeção visual dos nove estados de referência e das três preparações em 1024 × 720 realizada.
- Contraste dos botões corrigido nos XAMLs; B compactado para manter as ações principais visíveis na referência.
- Painéis longos usam rolagem. Esses renders não constituem teste de teclado, leitor de tela, interação completa ou todas as combinações de DPI.
- A galeria apenas alterna imagens. Nenhum processamento, instalação, promoção de baseline ou alteração do aplicativo foi realizado.

O primeiro render A gerou uma imagem válida, mas o coletor de evidência a classificou como vazia porque usava IsVisible sem janela nativa. O coletor passou a verificar Visibility e dimensões no visual tree offscreen. O render inicial foi preservado em renders; a apresentação usa renders-final e revisões finais indicadas na galeria.

MainWindow.xaml, App.xaml e o executável publicado foram reautenticados com os mesmos SHA-256 registrados em AUDIT.md. O renderer possui projeto e saída próprios, sem referência ao projeto do produto.

Revisão final C: cancelamento e relatórios agora inteiros na área visível de referência. Os três estados foram renderizados novamente em ambas as larguras; imagens autoritativas C em renders-final/C-Bancada-r2. A/B permanecem em renders-final/A-Studio e renders-final/B-Fluxo. Os nove caminhos usados na galeria foram conferidos.

SHA-256 dos XAMLs que produziram as imagens finais:

- A-Studio.xaml: C8CC3191260BD839BD50D3E9C8F3C2D1A1B23D3D8E285A75F916418C06EEFF13
- B-Fluxo.xaml: 77BFB781EFE09B3D4B89A4BF22F156852F2D052127070B3E551BD79C9B012139
- C-Bancada.xaml: A3D1DE5880089541D15FF602597BE95D6FC935A8C3597BF7AE4DAFF90B8C395F
