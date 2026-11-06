# Auditoria de alinhamento — Bancada C2

Data: 2026-09-12. Escopo: revisão, sem correção ou publicação do programa.

## Conclusão

A identidade C2 e a navegação com as oito ferramentas visíveis devem permanecer. O acabamento não está concluído: os layouts internos das ferramentas secundárias ainda têm restrições antigas que não se harmonizam com os novos estilos. Há cortes reais, além de problemas de proporção e organização. Recomenda-se uma rodada focada de correção visual, sem alterar processamento.

## Método e limites

- Renderização do MainWindow real com seus recursos WPF atuais: oito abas, áreas de 1280×720 e 1008×600, opções padrão/expandidas e posições superior/intermediária/inferior de rolagem: 96 capturas.
- Evidência: `../../.superpowers/bancada-c2-alignment-audit-20260912/`; medidas em `layout.json`; execução em `../../.superpowers/bancada-c2-alignment-audit-20260912.log`.
- Astra revisou especialmente Modelos, Mídia, Juntar e Mapas; revisão principal cobriu Completa, Descompactar, Exata e Pipeline.
- As capturas são diagnósticos do componente WPF, não capturas da janela publicada em uso. 1008×600 aproxima a área útil da janela mínima; não representa teste de DPI do Windows. Acessibilidade por teclado, leitores de tela e escalas 125–200% não foram certificadas.
- A interseção de bounds do diagnóstico não captura todos os recortes internos dos templates. Quando houver divergência, a imagem é evidência do corte, não o contador de largura visível.
- Conteúdo fora da primeira dobra mas acessível por rolagem é tratado como organização, não como controle inacessível.

## Achados por tela

| Tela | Achado | Prioridade / recomendação |
|---|---|---|
| Mídia e recursos | Na área estreita, Compress e parte de sua explicação são cortados horizontalmente; o campo de origem também fica pequeno. | Alta: reorganizar a linha conforme espaço disponível; garantir botão e rótulo completos. |
| Descompactar | Textos das opções de extração e de excluir o BSP ficam truncados horizontalmente na coluna Behavior. Open Output também perde o fim do texto. | Alta: permitir quebra dos rótulos, em especial o de exclusão; largura mínima baseada no conteúdo dos botões. |
| Modelos | Campos Blender/studiomdl caem para cerca de 69 px na área estreita. Botões laterais consomem a largura útil. Preset/Format têm aproximadamente 35 px de altura contra 51 px de Suffix/campos numéricos na mesma linha. | Média: reorganizar ferramentas e seletores; alinhar altura e eixo dos controles equivalentes. |
| Juntar addons | Origem fica separada de nome/destino/Scan; o próximo passo fica abaixo da primeira dobra, enquanto explicações ocupam bastante área. | Média: agrupar entrada, saída e ação; não esconder ferramenta nem remover avisos relevantes. |
| Mapas | Na área estreita, Cancel fica sozinho numa segunda linha; atalhos de saída formam outra linha. Scope/guardrails ocupam mais destaque que a ação seguinte. | Média: agrupar ações primárias e secundárias de forma previsível, preservando avisos e rolagem. |
| Compressão completa | Entradas e CTA estão coerentes. Com Atividade aberta na área estreita, o centro fica com aproximadamente 89 px de altura; resumo/configuração têm pouco espaço útil. | Média: reservar altura útil mínima para o trabalho; limitar o painel de atividade ou redistribuir espaço. Não remover inventário nem esconder ferramentas. |
| Distribuição exata | Campos de caminho e botões associados ficam maiores que os botões de ação; exportação e atalhos estão em blocos separados. Sem corte horizontal de ação confirmado nas capturas examinadas. | Baixa/média: uniformizar controles e aproximar ação/estado; manter todas as advertências de reconstrução e validação. |
| Pipeline | Select Addon estica para cerca de 68 px, enquanto Run Pipeline fica perto de 37 px. Relatórios vazios recebem muito espaço; ações e resultados ficam distantes. | Média: altura consistente e agrupamento de execução/estado, sem alterar a ordem funcional do pipeline. |

## Problemas transversais

1. **Alturas e proporções inconsistentes.** Campos de caminho com barras horizontais aumentam linhas; botões sem limite vertical acompanham o alongamento. O estilo global usa padding generoso e vários layouts foram dimensionados para outra composição. Não basta reduzir tudo indiscriminadamente: campos multilinha e logs precisam de estilos próprios.
2. **Larguras fixas e colunas rígidas.** Descompactar, por exemplo, divide Existing output/Behavior igualmente e usa rótulos de checkbox sem quebra; Open Output tem Width=100. Causas verificadas em MainWindow.xaml, próximos às linhas 1569–1600 e 1693–1698.
3. **Hierarquia desigual.** Em algumas abas selecionar uma pasta parece mais importante que executar. Outras dão grande destaque a caixas de relatório ainda vazias.
4. **Vocabulário misto.** Navegação em português, ferramentas internas com Select/Browse/Run/Root/Work e explicações em duas línguas. É uma inconsistência de compreensão, não defeito de codec. Recomenda-se tradução consistente, preservando nomes técnicos quando necessários.
5. **Espaçamento e grupos.** Padronizar ritmo entre rótulo/campo, campos relacionados, seções e ações; botões do mesmo grupo devem compartilhar altura e alinhamento. Evitar Cancel isolado por quebra acidental.

## O que manter

- Paleta escura e acento quente, estrutura Bancada, oito ferramentas sempre visíveis.
- Fluxo guiado e ação contextual da Compressão completa.
- Separação entre ações primárias, cancelamento e abertura de resultados.
- Avisos sobre originais, qualidade, reconstrução e necessidade de validar no jogo.
- Toda lógica, parâmetros, handlers e contratos de processamento.

## Rodada de polimento proposta

1. Corrigir cortes de botões e rótulos, começando por Mídia e Descompactar.
2. Padronizar uma altura de controle de linha única (referência: 40 px da Completa), com exceções explícitas para textos/logs; não forçar largura única para todos os botões.
3. Reorganizar linhas densas para dar espaço real aos caminhos e impedir quebras ruins de grupos.
4. Aproximar entrada/configuração/ação/estado nas ferramentas secundárias; reduzir repetição explicativa, sem enfraquecer avisos.
5. Conferir novamente todas as abas no mínimo e no tamanho normal, expanders, estados de execução/erro/resultado e caminhos longos; depois validar janela real em DPI ampliado quando o acesso nativo estiver disponível.

Os testes anteriores demonstraram funcionamento e alcance das telas, mas não bastavam para atestar alinhamento e legibilidade de todos os controles. Esta rodada acrescenta a inspeção que faltava, sem alegar que testes de backend detectam defeitos visuais.

## Preservação

Somente diagnóstico e documentação foram adicionados nesta revisão. Nenhuma correção de produção ou publicação de EXE foi feita. Hashes conferidos ao fim:

- MainWindow.xaml: `A2A19B213C27F89887D4CBC63CE07A0BDC9FE0C74E9DD86D4C7A1854DDB88027`
- App.xaml: `958B1CDF77417D8E8FF66E6C98C4A8046CBF3050246C3D66C0BED6E9D50FAD35`
- EXE publicado: `F0198AA5F29D6DE2F153AEC872C9AE29575733E6981A8F2BFD588A266D6634CC`

Originais, codecs, baselines, addons ativos e artefatos congelados não foram alvo de operações desta auditoria.
