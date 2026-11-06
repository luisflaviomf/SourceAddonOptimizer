# Três opções de apresentação WPF — 12/09/2026

Esta entrega compara três organizações visuais para o GMod Addon Optimizer existente. São camadas de apresentação candidatas ao produto, em XAML WPF real, com decisões de tipografia, cores, espaçamento, controles e estados definidas. Não são desenhos a serem reinterpretados nem um frontend web.

Os dicionários estão deliberadamente desconectados do processamento. Botões de executar, selecionar pastas, navegação e abrir relatórios não chamam o backend. Expanders, edição dos campos, menus WPF e rolagem respondem como controles nativos. Os arquivos do aplicativo instalado e seu código de produção não foram alterados.

## Escopo visual entregue

Cada opção contém a janela cliente completa em três estados: preparar, executar e resultado da compressão completa. As outras sete áreas possuem localização definida e entrada visível na organização escolhida; suas telas internas não foram redesenhadas nesta rodada. A integração e a migração dessas telas somente começam depois da escolha do usuário.

- A-Studio.xaml: navegação lateral, formulário central e perfil contextual à direita.
- B-Fluxo.xaml: percurso guiado de três etapas, resumo lateral e ferramentas avulsas no rodapé.
- C-Bancada.xaml: menus de operação, árvore de arquivos, tabela central, inspector e atividade inferior.

Não há filas, execução paralela, histórico de tarefas, previsão de tempo, medição de economia, detecção de instalação ou aprovação de candidato simulados. Caminhos, arquivos e barras de progresso são explicitamente ilustrativos. O estado de resultado mantém OFFLINE_PENDING_GMOD.

## Contrato de renderização e integração

Todos os arquivos são ResourceDictionary autônomos, sem namespaces de aplicativo, event handlers, conversores, dependências externas ou code-behind. Usam Segoe UI e Consolas.

Chaves públicas:

| Recurso | Conteúdo |
| --- | --- |
| SetupView | Cliente completo em preparação |
| RunningView | Cliente completo em execução ilustrativa |
| ResultView | Cliente completo com validação GMod pendente |
| MainView | Seleciona o estado pelo binding State |
| Shell | Estrutura compartilhada de cada opção |
| SetupBody / RunningBody / ResultBody | Conteúdo de cada estado dentro do shell |

State aceita setup, running e result. Valor ausente ou desconhecido usa preparação. A prévia pode aplicar diretamente qualquer um dos três templates públicos a um ContentControl; não precisa instanciar o viewmodel atual do aplicativo. Ao incorporar uma opção, os templates e estilos podem ser reutilizados, substituindo dados ilustrativos por bindings e ligando comandos aos runners existentes.

Referência: cliente 1280 × 720 em unidades WPF. O layout usa Grid com colunas proporcionais, rolagem vertical local e ausência de Viewbox; reduzir a largura não reduz a tipografia por escala. Verificação secundária solicitada em 1024 × 720. O host de apresentação deve manter a área cliente separada de eventual chrome de janela.

## A — Studio

Direção: ferramenta de produção sóbria, com fundo azul grafite, superfícies discretas e acento menta. A pessoa escolhe o trabalho na lateral e permanece no mesmo espaço até o resultado.

Estrutura:

- Cabeçalho 66; lateral 194; rodapé 34.
- Área principal flexível; painel de perfil 274 com rolagem independente.
- Margem de trabalho 34 horizontal e 30 vertical.
- Lateral divide Criar candidato e Ferramentas. Engine e ferramentas também têm acesso persistente inferior.
- Formulário prioriza entrada e saída. Engine, Blender e studiomdl ficam no expander do próprio fluxo.
- Perfil completo está à direita, com sequência e esclarecimento sobre qualidade.
- Detalhes técnicos ficam recolhidos. Incluem identificador de perfil e política VTX.

O estado de execução substitui o formulário por etapa atual, progresso, caminhos e cancelamento seguro. O resultado substitui progresso por candidato, evidências e pendência no jogo. A navegação e o perfil permanecem estáveis.

Melhor para alternar frequentemente entre compressão completa e ferramentas específicas sem perder orientação.

## B — Fluxo

Direção: aplicativo claro e orientado a uma tarefa por vez, com fundo mineral, superfícies brancas e verde discreto. Três etapas de uso — preparar, processar, validar no jogo — descrevem o percurso da pessoa. Não alteram as quatro etapas técnicas do backend.

Estrutura:

- Cabeçalho 76; toolbox inferior 102; rodapé 29.
- Margens horizontais 40.
- Corpo principal flexível; resumo à direita 283 incluindo recuo interno.
- O formulário de origem/destino e ferramentas ocupa uma superfície branca única.
- A ação primária aparece depois do formulário.
- Modelos, mídia, descompactar, juntar, mapas e exata ficam disponíveis como ferramentas avulsas.
- Pipeline personalizado permanece no cabeçalho.
- Nenhum console permanente. Atividade e logs são acessados sob demanda.

Execução é uma tela de acompanhamento simples. Resultado conduz à validação no GMod sem indicar aprovação e oferece candidato e relatórios juntos.

Melhor para quem quer comprimir um addon completo e prefere instruções curtas com uma próxima ação evidente.

## C — Bancada

Direção: ferramenta de arquivos para uso frequente, em grafite neutro e acento areia. A densidade serve à inspeção, sem transformar a interface em terminal.

Estrutura:

- Cabeçalho 55; menus 36; rodapé 28.
- Barra de operação 70; atividade inferior 100.
- Árvore de origem 172; tabela de arquivos flexível; inspector 290.
- Tabela: nome flexível, tipo 56, tratamento 127.
- Linha de arquivo com preenchimento horizontal 14 e vertical 13; separadores simples.
- Inspector contém origem, pasta nova, engine/ferramentas e resumo do perfil.
- Menus reúnem operações; não há oito abas de tamanho concorrente.
- Atividade inferior mostra poucas linhas e acesso ao log completo.

A tabela demonstra a organização de um inventário, sem alegar que houve scan. Os tratamentos mostrados são políticas previstas, não resultados de elegibilidade. Uma futura integração deve preencher o inventário a partir de evidência real já disponível; não exigir um novo subsistema ou prometer processamento por arquivo. Selecionar uma linha não passa a autorizar uma nova operação granular.

Melhor para quem pensa no addon como árvore de recursos e precisa conferir origem, tratamento e evidência ao executar.

## Mapa das capacidades existentes

| Área atual | A — Studio | B — Fluxo | C — Bancada |
| --- | --- | --- | --- |
| Compressão completa | Criar candidato → Compressão completa | Fluxo principal de abertura | Processar → Compressão completa |
| Compress | Ferramentas → Texturas e áudio | Ferramentas avulsas → Mídia | Processar → Texturas e áudio |
| Models | Ferramentas → Modelos | Ferramentas avulsas → Modelos | Processar → Modelos |
| Descompactar addons | Ferramentas → Descompactar | Ferramentas avulsas → Descompactar | Arquivo → Descompactar GMA / BIN |
| Juntar addons | Ferramentas → Juntar addons | Ferramentas avulsas → Juntar addons | Arquivo → Juntar addons |
| Optimize Maps | Ferramentas → Mapas | Ferramentas avulsas → Mapas | Menu Mapas |
| Distribuição exata | Criar candidato → Distribuição exata | Ferramentas avulsas → Exata | Distribuição → Exportar candidato exato |
| Pipeline | Criar candidato → Pipeline personalizado | Cabeçalho → Pipeline personalizado | Processar → Pipeline personalizado |

Preservação de controles no futuro detalhamento das áreas:

- Compress: escolha de addon, seleção por formato VTF/WAV/MP3/OGG/JPG/PNG/LUA, limites e fator de imagem, proporção, parâmetros de áudio, proteção de loops, opção de minificação Lua, execução/cancelamento, tamanho, log e controles avançados existentes. O rótulo curto Mídia é uma entrada para a área inteira; a opção Lua continua acessível em Recursos avançados.
- Models: addon, caminhos de Blender e studiomdl, detectar/procurar ferramentas instaladas, presets e parâmetros existentes, otimizar/recompilar, progresso, saída/work/logs. Parâmetros avançados ficam em inspector/expander, sem serem removidos.
- Descompactar: raiz e destino, gmad detectar/procurar, scan do lote, modo para saídas existentes, opções de mapas/pak e a opção destrutiva de remover BSP permanecem explicitamente separadas, extração/cancelamento, resumo e logs.
- Juntar: origem, nome/destino de bundle, conflitos, prioridade, modo de pacote, gerar GMA opcional, gmad, scan/gerar/cancelar, diagnósticos e relatórios. O redesenho não escolhe silenciosamente uma nova política de conflitos.
- Mapas: scan + stage, opções de recursos do staging, optimize staging e build + inject continuam ações distintas; conservar gmad, progresso e saída/work/logs.
- Exata: addon de entrada ou usar addon atual, pasta final inexistente, engine, exportar/cancelar, work, candidato, relatório e log.
- Pipeline: Models → Compress fixos, distribuição exata opcional com padrão desativado, engine disponível no mesmo fluxo por acesso contextual, progresso, resumo, size report, saída/work/logs e copiar resumo.
- Configuração de ferramentas: sempre detectar/selecionar instalações existentes. Nenhum botão instala Blender, Source, Steam ou Garry's Mod.

## Semântica da compressão completa

O perfil continua independente das áreas avulsas: complete-safe-native-exact/v1.

1. Staging com inventários e evidência de preservação.
2. Models Safe existente: normal, ratio 0,75, merge 0, autosmooth 45, SMD; demais garantias conforme docs/full-compression.md.
3. VTF/PNG/WAV com as políticas e proteções nativas existentes.
4. Distribuição exata obrigatória ao final, relativa ao intermediário selecionado.

Os controles de preparação não sugerem que seja preciso configurar outras áreas primeiro. Blender só é necessário quando há modelos. Entrada é somente leitura e saída é pasta nova. Não se compacta novamente um candidato já comprimido.

A interface nunca chama o fluxo inteiro de lossless: redução de modelos, resolução e áudio pode alterar a qualidade. A etapa exata reconstrói o intermediário escolhido, não o addon anterior à redução nativa.

DX80 e SW são excluídos somente da seleção de novos candidatos; DX90 permanece. Registrar caminhos, quantidade e bytes economizados separadamente da compressão de codec. Não alterar originais, candidatos aprovados, frozen, baseline ou rollback.

No resultado, economia só aparece quando há medição real. As prévias usam travessão. Falha e cancelamento não recebem estado de conclusão; parcial/evidência não é apagado automaticamente. Cancelar aguarda o encerramento seguro. Não há instalação, ativação nem promoção automática.

A tela final deve manter Validação no GMod pendente e o código OFFLINE_PENDING_GMOD. Orientar a preservar o nome da pasta final e testar apenas o candidato, sem montar simultaneamente o original.

## Sistema visual fixado

| Token | A — Studio | B — Fluxo | C — Bancada |
| --- | --- | --- | --- |
| Fundo | #10151C | #F5F5F1 | #202326 |
| Superfície | #151C25 | #FFFFFF | #282C30 |
| Elevado | #1D2733 | #EDEFEA | #34393E |
| Texto | #F1F5F9 | #26382F | #F1F2F3 |
| Secundário | #A3B0C1 | #627268 | #ADB5BC |
| Linha | #2C3745 | #DCE2DA | #41484F |
| Acento | #8EDCC5 | #2D6850 | #D7B78B |
| Fundo do acento | #203C38 | #E4EEE7 | #453D32 |
| Texto sobre acento | #102C25 | #FFFFFF | #28231C |
| Pendente | #E8C27D | #8B641E | #E5C17F |

Segoe UI é a família da interface; Consolas se limita a extensão de arquivo e atividade técnica. Conteúdo e controles principais usam 13–14; títulos usam 20–30 conforme hierarquia. Metadados auxiliares usam 10–12 e não carregam instruções essenciais. Sem ícones de pacotes externos.

Botões têm padding 15 × 10, raio 5, hover com borda de acento, foco visível e opacidade 45% quando indisponíveis. A ação primária usa o acento e texto contrastante. TextBox usa padding 11 × 10. Scrollbars locais usam largura 9 e cor de linha da própria opção.

Ao conectar o processamento, desabilitar alterações de origem/destino/perfil enquanto o runner estiver ativo; preservar navegação contextual que não dispare uma execução concorrente. Os estados visuais demonstrados não implementam esse bloqueio por si só.

## Evidência e limites

A renderização WPF e a inspeção de 1280 × 720/1024 × 720 pertencem ao host isolado desta apresentação. Elas comprovam aparência e montagem dos templates, não funcionamento dos runners, integridade de addons nem compatibilidade de candidato no GMod.

Após a escolha, a implementação deve incorporar uma opção ao WPF existente, preservar handlers/settings/runners e verificar o executável oficial publicado com build_release_wpf.ps1. Nenhum build ou release de produção faz parte desta entrega de alternativas.

