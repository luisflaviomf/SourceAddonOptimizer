# Auditoria visual Astra — GMod Addon Optimizer

Data: 12/09/2026. Escopo: revisão somente leitura do produto WPF atual. Este arquivo contém achados e propostas; não autoriza nem executa alterações no produto.

A navegação de oito ferramentas está visível, mas a organização das telas secundárias ainda precisa de revisão. Há um corte horizontal real na ação principal de Mídia em janela estreita, campos de ferramentas com apenas 69,3 unidades de largura, alturas incompatíveis na mesma linha e operações importantes distantes dos seus parâmetros. O smoke funcional anterior não verificava esses problemas.

## Evidência e cobertura

Fontes locais:

- `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml`.
- `GmodAddonCompressor-master/GmodAddonCompressor/App.xaml`.
- `.superpowers/bancada-c2-20260912/smoke-final/`: 16 imagens das oito abas em 1280×720 e 1024×720; seis estados adicionais da compressão completa.
- `.superpowers/bancada-c2-alignment-audit-20260912/`: capturas profundas e `layout.json` gerados pelo agente coordenador.

Cobertura desta revisão:

| Aba | Inspeção inicial | Inspeção profunda neste relatório |
| --- | --- | --- |
| Compressão completa | 1280×720, 1024×720 e seis estados | Coordenador aprofunda; aqui verificação do conjunto |
| Modelos | Ambos | Topo, meio e fundo; padrão e avançados abertos; 1280×720 e 1008×600 |
| Mídia e recursos | Ambos | Topo, meio e fundo; padrão e expandido; 1280×720 e 1008×600 |
| Descompactar | Ambos | Coordenador aprofunda |
| Juntar addons | Ambos | Topo, meio e fundo; padrão e expandido; 1280×720 e 1008×600 |
| Mapas | Ambos | Topo, meio e fundo; padrão e configurações abertas; 1280×720 e 1008×600 |
| Distribuição exata | Ambos | Coordenador aprofunda |
| Pipeline | Ambos | Coordenador aprofunda |

Nas quatro áreas aprofundadas, 48 amostras foram cobertas por inspeção visual e comparação: 36 imagens distintas examinadas, mais 12 pares padrão/expandido de Mídia e Juntar comprovadamente idênticos por SHA-256. Metadados de posição/tamanho foram consultados para sustentar os números abaixo.

1008×600 aproxima a área cliente disponível de uma janela mínima de 1024×640. Não representa um teste real de DPI alto. As medidas são unidades de layout WPF, arredondadas a uma casa decimal.

As capturas de estado são fixtures sem execução de codec. Por exemplo, `state-empty.png` mantém caminho/contagem do fixture enquanto o estado foi forçado a vazio. Isso limita conclusões sobre sincronização de dados; não foi classificado aqui como defeito comprovado do produto.

**Limitação dos metadados:** `visibleWidth` não basta para provar ausência de recorte por ancestrais. Em Mídia, o JSON declara `Button_Compress.visibleWidth=140`, mas a imagem mostra o botão cortado dentro do card. O relatório privilegia a imagem e o XAML neste caso.

**Rolagem não é recorte horizontal:** controle parcialmente fora da dobra pode ser alcançado rolando. Os achados sobre ações abaixo da dobra são problemas de organização/descoberta, não alegações de controle inacessível.

## Prioridades

- P1: prejudica diretamente reconhecer ou alcançar uma ação principal no tamanho suportado.
- P2: prejudica leitura, conferência, hierarquia ou previsibilidade de uso frequente.
- P3: acabamento e consistência, sem bloqueio observado.

## F01 — P1 — Mídia corta a ação principal horizontalmente

Evidência: `smoke-final/1024-tab-2.png`, `1008-600-tab2-default-0.png` e `1008-600-tab2-expanded-50.png`.

O botão Compress aparece incompleto à direita e sua legenda também termina abruptamente. Isso persiste ao rolar: não é apenas a dobra vertical. A origem ocupa 86,7 de largura no cenário 1008×600, enquanto Select Addon usa 110.

Local: MainWindow.xaml:870, região de `Button_Compress` em :879; grid de três colunas em :793. A ActionBar usa primeira coluna Auto, uma frase longa sem quebra no mesmo StackPanel do botão e uma segunda coluna de diagnósticos. O texto determina uma largura desejada maior que o card; o botão centraliza dentro dessa largura excedente. `CheckBox_DebugConsole` e Optional diagnostics, declarados a partir de :891, também não são visíveis no card nas capturas, mesmo em 1280.

Proposta: origem em faixa larga; ação principal em barra de largura disponível, sem Auto medido por prosa longa. Legenda com Wrap; diagnóstico em acesso separado/expansível. Mídia precisa de duas colunas úteis ou de seções empilhadas no tamanho mínimo, em vez das três atuais.

Aceite: texto completo e retângulo clicável do CTA dentro do card em 1008×600/1024×720; diagnóstico acessível; verificar os clips de todos os ancestrais, não somente os limites da janela.

## F02 — P2 — Campos de caminhos perdem quase toda a informação

Evidência mensurada:

| Tela/campo | 1280×720 | 1008×600 |
| --- | ---: | ---: |
| Modelos / Blender | 152,2 | 69,3 |
| Modelos / studiomdl | 152,2 | 69,3 |
| Mídia / origem | — | 86,7 |
| Mapas / gmad | — | 155,3 |

Modelos mostra apenas o início do drive/caminho no tamanho mínimo; dois botões Auto-detect/Browse ocupam a maior parte de cada meia coluna. O caminho não pode ser conferido visualmente sem navegar o texto. A origem de Mídia mostra pouquíssimos caracteres mesmo após a escolha.

Local: Modelos MainWindow.xaml:353 e :400, grades campo + Auto-detect + Browse dentro de duas colunas; Mídia :827; Mapas `TextBox_MapGmadPath`.

Proposta: uma linha completa por ferramenta, com caminho flexível e botões auxiliares compactos; em janela estreita, empilhar Blender e studiomdl. Mostrar nome do executável e permitir consultar/copiar caminho completo, sem usar a scrollbar horizontal como modo principal de identificação.

Isso não pede trocar os executáveis, detectar automaticamente nem validar algo que o backend ainda não verificou.

## F03 — P2 — Alturas de controles não seguem uma regra comum

Evidência mensurada em `layout.json`:

- Botões de ação Scan/Generate/Open: 36,62.
- CTA Compress: 40.
- Linhas de ferramentas em Modelos: TextBox e botões esticados a 51,29.
- Linhas de caminho com scrollbar horizontal em Mídia/Juntar/Mapas: botão Select/Browse/Use root esticado a 68,29.
- Modelos, Preset e Suffix começam no mesmo y=289,12: ComboBox com 34,62 e TextBox com 51,29, diferença de 16,67 nas bordas inferiores.
- No mesmo bloco de ajuste, Format tem 34,62 enquanto Ratio/Merge/AutoSmooth têm 51,29.

Evidência visual: `1280-720-tab1-expanded-50.png`, `1008-600-tab1-expanded-50.png`, `1280-720-tab4-expanded-50.png`.

Local: App.xaml:64 (ControlPadding), :250 (ButtonBaseStyle), :362 (TextBox), :427 (ComboBox); MainWindow.xaml:486–527 e campos de caminho citados.

Causa de layout: altura natural diferente dos templates e dos caminhos com scrollbar é transmitida aos botões vizinhos por Stretch; os formulários não estabelecem uma altura de linha comum.

Proposta: definir uma altura comum para campos de uma linha e seus botões, aproximadamente 40, alinhando também as bordas inferiores. Manter áreas multilinha de relatório fora dessa regra. Conferir o viewport interno de texto para não repetir o corte vertical já corrigido nos campos da compressão completa.

## F04 — P2 — Modelos separa os parâmetros do ato de executar

Na abertura, Optimize Models está no topo direito, mas Preset, Suffix e os ajustes não aparecem na primeira dobra. Ao rolar para conferi-los, o CTA e o estado saem da tela e quase toda a coluna direita fica vazia. O avançado aberto acentua essa divisão.

Evidência: `1280-720-tab1-default-0.png`, `1280-720-tab1-default-100.png`, `1008-600-tab1-expanded-100.png`.

Local: MainWindow.xaml:241, StackPanel esquerdo com cards; :683 `Button_OptimizeModels`, painel direito que participa da mesma rolagem externa.

Proposta: trazer preset e nome/sufixo da saída perto da origem; ferramentas em configuração contextual; deixar parâmetros menos comuns expansíveis; colocar executar e estado em faixa fixa ou painel que permaneça visível. Não é necessário eliminar nenhum ajuste.

Observação adicional: em `1008-600-tab1-default-0.png`, Open Output perde o final do rótulo dentro do botão. Os três botões de saída devem redistribuir-se ou usar rótulos curtos claros, com largura mínima suficiente.

## F05 — P2 — Juntar fragmenta origem, destino e ação inicial

A primeira tela oferece origem, política de conflitos e embalagem. Nome do novo addon, destino e Scan aparecem apenas na segunda linha de cards. O campo de origem fica fora da vista quando a pessoa chega ao destino. À esquerda há um grande card de resumo vazio antes de qualquer scan, e no fundo uma coluna quase vazia acompanha os diagnósticos da direita.

Evidência: `1280-720-tab4-default-0.png`, `1280-720-tab4-expanded-50.png`, `1008-600-tab4-expanded-100.png`.

Local: MainWindow.xaml:1772 (raiz da aba), :1990 `TextBox_AddonMergeBundleName`, :2003 `TextBox_AddonMergeOutputRoot`, :2028 `Button_AddonMergeScan`.

Proposta: origem + nome + destino na mesma região de preparação; conflitos como escolha próxima; embalagem GMA como opção expansível, conservando explicitamente o padrão atual. Scan/Generate/Cancel ficam juntos em barra estável. Resumo, inventário e diagnósticos ganham espaço após haver dados.

O recorte de Open Root/Open Merged na captura de 50% é apenas interseção com a dobra: esses botões são alcançáveis com rolagem e não foram classificados como inacessíveis.

## F06 — P2 — Mapas prioriza explicações antes da sequência de trabalho

Scope, Conservative v1 target e Rebuild guardrails consomem uma coluna de altura igual à configuração de origem; sobra grande área vazia. Scan + Stage não aparece na abertura em nenhum dos dois tamanhos. Abrir Map Asset Compression Settings empurra a sequência inteira ainda mais para baixo.

Evidência: `1280-720-tab5-default-0.png`, `1280-720-tab5-expanded-50.png`, `1008-600-tab5-expanded-50.png`.

Local: MainWindow.xaml:2162, card Current Scope; :2237 expander; :2452–2477 ações de etapas.

Proposta: origem e ferramenta primeiro; indicar explicitamente 1 Analisar e preparar, 2 Otimizar cópia, 3 Construir saída. Manter os handlers e seus pré-requisitos atuais. Resumo técnico do escopo em uma frase e detalhes expansíveis. Ações e cancelamento fora da rolagem longa das configurações.

A diferença é de descoberta e distância entre etapas, não uma falha de navegação: as capturas de meio mostram os botões completos.

## F07 — P2 — Rótulos de opções e menus ficam truncados em layouts estreitos

Mídia em 1008: Protect WAV loop behavior when possible e textos auxiliares terminam no limite da coluna sem quebra; o valor OGG q1 (aggressive) aparece como q1 (aggr…). Mapas: legenda do uso de gmad termina antes de explicar toda a condição. Modelos: Open Output perde parte do texto no tamanho mínimo.

Evidência: `1008-600-tab2-expanded-50.png`, `1008-600-tab2-expanded-100.png`, `1008-600-tab5-default-0.png`, `1008-600-tab1-default-0.png`.

Proposta: conteúdo de CheckBox em TextBlock com Wrap; linhas de ajuda limitadas à largura real; labels curtos e completos para os valores selecionados. Não depender de aumentar a janela para entender uma escolha de qualidade.

A truncagem das caixas de seleção de Descompactar também é visível no levantamento inicial, inclusive na opção de remover BSP; o coordenador detalha essa área.

## F08 — P2 — “Status: ready” de ferramentas é mais forte que a evidência da interface

Modelos mostra Status: ready sob Blender/studiomdl. Os triggers XAML em :386 e :426 só trocam para not set quando o caminho está vazio ou nulo; um caminho preenchido basta para a mensagem de pronto.

Não foi constatado que os caminhos atualmente escolhidos sejam inválidos. O defeito é semântico: o rótulo sugere validação, mas esse componente apenas expressa presença de texto.

Proposta: usar “Caminho informado” para esse estado. Reservar “Disponível/validado” para uma verificação real já existente, se houver; nenhuma instalação ou validação nova é necessária para tornar o texto honesto.

## F09 — P3 — Grades de ações mudam de forma e peso sem intenção consistente

Em Mapas 1280, Scan + Stage, Optimize Staging, Build + Inject e Cancel estão na mesma linha. Em 1008, Cancel quebra sozinho para a segunda, e Open Root/Work/Output viram a terceira. As larguras fixas de 118/130/120/100 mais margens ultrapassam o espaço útil.

Em Juntar, Browse e Use root têm 68,29 de altura; Scan e Generate logo abaixo têm 36,62. Em Modelos, Select Addon também pode ficar maior que a ação real de otimização.

Local: MainWindow.xaml:2452–2477 e :2009–2048. Evidência: `1008-600-tab5-default-50.png`, `1280-720-tab4-expanded-50.png`.

Proposta: manter cancelamento na mesma zona estável da operação ativa; botões auxiliares menores e neutros; somente o próximo passo primário recebe acento forte. Uma quebra planejada em duas linhas é aceitável; a posição do cancelar não deve depender apenas do WrapPanel.

## F10 — P3 — Texto técnico e cards repetidos ocupam espaço de decisão

Mídia repete a descrição do pipeline VTF em Compression mode, Routing e Unified VTF pipeline. Na abertura de 1280, o card de modo/roteamento ocupa aproximadamente y=244–509 antes dos controles de resolução; no tamanho mínimo, chega perto de y=262–575. A coluna de origem, em contrapartida, fica quase vazia ao rolar.

Modelos contém frases sobre como a própria UI deveria ser organizada, como “Keep the frequently used controls visible…”, que não ajudam a escolher um preset. Juntar e Mapas dedicam cards inteiros a instruções que poderiam ser uma introdução curta com detalhes sob demanda.

Local: MainWindow.xaml:1250, :1328 e introdução Configuration em Modelos.

Proposta: uma explicação curta junto da decisão e detalhes técnicos expansíveis. Não remover limites de segurança, efeitos de qualidade, conflitos ou requisitos; reduzir repetição e metatexto.

## F11 — P3 — Vocabulário e localização ainda misturam duas interfaces

A navegação e a compressão completa estão em português; as áreas avulsas continuam majoritariamente em inglês, incluindo Select Root, Browse, Open Work, Run, Scope e nomes de opções. Há também tooltips com sequências de encoding incorretas no XAML de Mídia, por exemplo “saÃ­das”.

Proposta: padronizar nomes de ações por efeito: Escolher addon, Escolher pasta, Detectar, Executar/Comprimir, Abrir saída, Relatórios e logs. Preservar nomes de formatos e parâmetros técnicos quando úteis. A localização deve ser tratada como um lote separado da correção de layout, com revisão de rótulos e atalhos.

## O que manter

- Oito destinos sempre visíveis e na mesma posição.
- Grafite/areia e texto principal claro.
- Separação explícita entre redução nativa e etapa exata na compressão completa.
- Pendência de validação GMod no resultado.
- Campos largos e barra de ação fixa da compressão completa como referência de consistência.
- Funções e controles existentes; esta revisão não pede criar backend, filas ou ferramentas novas.

## Direção de reorganização

| Área | Preparação | Opções | Ações/resultado |
| --- | --- | --- | --- |
| Modelos | Origem, preset e sufixo juntos | Ferramentas por linha; avançados expansíveis | Ação fixa; estado perto da ação |
| Mídia | Origem larga | Tipos e parâmetros em duas áreas úteis; texto técnico recolhido | CTA inteiro e diagnóstico acessível |
| Juntar | Origem, nome e destino na mesma região | Conflitos explícitos; GMA opcional | Scan → Generate próximos do resumo |
| Mapas | Origem e ferramenta | Configuração da cópia e guardrails sob demanda | Três etapas visíveis; cancelar estável |

Ordem proposta para uma futura implementação autorizada: corrigir F01; unificar medidas de linhas e impedir truncagem F02/F03/F07; reorganizar as quatro telas F04/F05/F06/F09; revisar semântica e linguagem F08/F10/F11.

Nenhum arquivo de produção foi alterado nesta auditoria.

