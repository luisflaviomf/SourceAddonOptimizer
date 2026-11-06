# Interface atual — diagnóstico para redesign

Fonte: MainWindow.xaml e App.xaml atuais do WPF, não a GUI Python antiga.
Análise estática em 2026-09-12; não é uma alegação de inspeção visual da janela instalada.

## Problemas encontrados

- Oito destinos no mesmo TabControl: Compressão completa, Compress, Models,
  Descompactar addons, Juntar addons, Optimize Maps, Distribuição exata e Pipeline.
  A hierarquia não distingue operação principal, ferramentas especializadas e
  preparação do ambiente. Funções encadeadas aparecem ao lado de suas etapas.
- A tela Compress contém 74 TextBlocks e a tela Models, 51. Isso não significa
  que todos estejam visíveis ao mesmo tempo, mas mostra quanto texto e instrução
  compete com os controles. A tela Maps contém outros 59 blocos.
- O tema usa labels de 11, subtítulos de 10 e textos de ajuda de 12 unidades,
  embora a janela tenha 1280 × 720 como tamanho inicial. Caminhos compridos e
  terminologia técnica agravam a leitura.
- Há muitos painéis/bordas aninhados e pesos visuais semelhantes. O log da
  compressão completa tem 320 de altura reservados, mesmo antes de executar.
- Português e inglês são misturados nos destinos e comandos.
- Preparação, execução e conclusão aparecem como áreas simultâneas mesmo quando
  parte delas ainda não é relevante. Configuração de ferramentas divide espaço
  com a tarefa recorrente de selecionar e comprimir um addon.

## O que as alternativas devem melhorar

1. Uma entrada principal óbvia para a operação mais frequente.
2. Ferramentas especializadas agrupadas, sem apagar suas capacidades existentes.
3. Progressão entre preparar, acompanhar e inspecionar o resultado.
4. Logs completos disponíveis sob demanda, com status conciso sempre legível.
5. Tipografia e alinhamentos consistentes, foco de teclado e estados claros.
6. Preservar entrada somente leitura, saída nova, advertências de qualidade e
   distinção entre exportação offline e validação no GMod.

## Entrega de design e fidelidade

O Astra é responsável pelas três direções e seus dicionários XAML. O renderer
separado produz PNGs a partir dos controles WPF, não de uma imagem gerada por IA.
Esses XAML são a fonte visual reaproveitável para a alternativa que o usuário
escolher. A galeria HTML apenas permite comparar as imagens.

Não são três novos otimizadores, nem três versões já conectadas ao backend.
A implementação no produto permanece pendente da escolha. Não há promessa de
pixels idênticos em diferentes versões de Windows, fontes ou escalas de DPI;
a referência tem 1280 × 720 unidades de conteúdo a 96 DPI. A moldura do sistema
não faz parte da composição. Outras telas seguem o mapeamento documentado em SPEC.

## Preservação de referência

Hashes antes do trabalho de design:

- MainWindow.xaml: `9bcb122a2299197ae8c57e0e14ee086c8c20ea7b19a3f1153ab74444d611504c`
- App.xaml: `34a8b132fc738979afbbe9be57432cefd16d5031d6651beaff929cedb1cea317`
- EXE publicado: `d1703674fec632f577990342a0ac4b18bf0fc32489af6bf1d073fd38849495a8`

Nada nesta fase autoriza alteração do motor de compressão, políticas de VTX,
baselines, originais, artefatos congelados ou rollbacks.
