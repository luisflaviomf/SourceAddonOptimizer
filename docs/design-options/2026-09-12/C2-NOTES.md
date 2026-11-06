# C2 — Bancada guiada

Revisão da opção C aprovada para apresentação, preservando grafite, areia e a bancada de arquivos. Arquivo novo: C2-Bancada-guiada.xaml. A/B/C e o produto existente permanecem intactos.

## Mudanças de organização

1. Entrada e saída saíram do inspector estreito e ocupam uma faixa larga no topo. Cada campo tem seu próprio Escolher… e rótulo explícito.
2. Título, orientação curta e sequência Preparar → Processar → Testar no jogo tornam a próxima etapa visível.
3. A barra inferior fixa contém a orientação e a ação principal do estado. Cancelamento, candidato e relatórios não dependem da rolagem do inspector.
4. A bancada de arquivos permanece central. Primeira abertura usa um estado vazio útil, sem inventário fictício. Os outros estados identificam os arquivos como ilustrativos.
5. O inspector virou Resumo do processamento. Mantém o perfil e explica que a redução nativa pode alterar qualidade, enquanto a etapa exata preserva o resultado dessa redução. Jogo/ferramentas e detalhes técnicos são expansíveis.
6. Outras ferramentas é um menu WPF com os nomes completos das sete áreas avulsas; atividade aparece recolhida na preparação e aberta na execução.

## Estados entregues

| Recurso | Comportamento visual |
| --- | --- |
| EmptyView | Primeira abertura; orientação para escolher origem; CTA Escolher addon original… |
| SetupView | Pastas ilustrativas preenchidas; revisão de pastas e ferramentas; CTA Comprimir addon completo |
| RunningView | Origem/destino bloqueados visualmente; etapa e progresso ilustrativos; atividade aberta; Solicitar cancelamento fixo |
| ResultView | Candidato ilustrativo; Validação no GMod pendente; Abrir candidato e Relatórios e logs fixos |
| MainView | Binding State aceita empty, setup, running, result; fallback setup |

Cada template inclui o cliente completo. Sem code-behind, conversores, bibliotecas externas ou processamento conectado. O host pode selecionar diretamente os templates.

## Dimensões e acabamento

Cliente de referência 1280 × 720; verificação complementar 1024 × 720. Não usa Viewbox: largura flexível, fontes preservadas e rolagem local.

Cabeçalho 58, rodapé de apresentação 26. Corpo: orientação 86, faixa de pastas 112, bancada flexível, atividade 36 recolhida ou 82 aberta, ação fixa 74. Resumo à direita 326. Campos, rótulos e instruções principais usam 14; títulos 17–24. Tipos de arquivo, colunas e identificação da prévia usam tipografia menor apenas como metadado.

Cores preservadas da C: fundo #202326, superfície #282C30, texto #F1F2F3, acento #D7B78B e texto sobre acento #28231C. Scrollbar de tema com largura 9. Botões preservam a correção de herança de Foreground.

## Semântica e limites

Original somente leitura; resultado em uma pasta nova. Perfil completo independente, distribuição exata obrigatória no final, cancelamento seguro e nenhuma instalação automática. A etapa exata é relativa ao intermediário após a redução nativa. Resultado continua OFFLINE_PENDING_GMOD e não aprova o candidato no jogo.

Nenhuma ferramenta é apresentada como detectada/validada sem execução. Os caminhos de Blender/studiomdl/jogo solicitam seleção ou detecção. O botão de detecção não instala ferramentas.

Os tratamentos da tabela são previstos e ilustrativos, não uma declaração de elegibilidade nem um relatório de execução. Economia aparece sem medição. Esta revisão não cria scan, processamento por arquivo, fila, tarefa paralela ou backend novo.

Expanders de configuração e menu são controles WPF nativos. Ações e links visuais de atividade permanecem desconectados; a prévia não comprime, abre pastas nem navega para telas secundárias. A implementação dessas ligações depende da futura aprovação de alteração no produto.

Tradeoff deliberado: a faixa de preparação reduz o número de arquivos visíveis. Em troca, entrada, destino, próximo passo e cancelamento ficam mais fáceis de descobrir. O resumo mantém rolagem para opções técnicas extensas.

