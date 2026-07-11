# Modo Maximum para otimização de modelos

Data: 2026-07-11
Status: desenho aprovado; implementação ainda não iniciada

## 1. Resumo

O produto ganhará um terceiro modo de otimização de modelos, chamado `Maximum`, no aplicativo WPF principal. Esse modo buscará o menor tamanho compilado possível para a pasta `models/`, sem usar uma porcentagem fixa como objetivo e sem aceitar degradação estrutural ou visual acima dos limites configurados.

O `Maximum` trabalhará como uma busca adaptativa:

1. gera candidatos progressivamente menores;
2. repara aparência e atributos perdidos pela simplificação;
3. recompila cada candidato com StudioMDL;
4. mede os bytes reais dos arquivos Source compilados;
5. valida estrutura e fidelidade no pior caso observado;
6. continua reduzindo enquanto os gates forem aprovados;
7. quando ultrapassar o limite de qualidade, devolve detalhe somente às regiões danificadas e explora candidatos intermediários;
8. escolhe o menor candidato aprovado, usando maior fidelidade como desempate.

Os modos atuais não serão substituídos:

- `Normal`: fluxo rápido atual.
- `Fidelity`: busca limitada, priorizando qualidade e tempo previsível.
- `Maximum`: busca extensa, compilada e validada candidato a candidato, priorizando a maior redução segura.

Uma redução de 50% não é meta fixa nem limite. É uma referência mínima desejada para addons que permitam isso. Se um conjunto puder reduzir 70%, 80% ou 90% e ainda passar todos os gates, o modo deve continuar. Se um modelo complexo só puder reduzir menos sem violar fidelidade, o resultado deve refletir esse limite real em vez de forçar uma redução destrutiva.

## 2. Contexto e problema observado

O pipeline atual de modelos segue, em linhas gerais:

`MDL/VVD/VTX -> Crowbar -> SMD/QC -> Blender Decimate -> StudioMDL -> MDL/VVD/VTX`

Ele já produz reduções relevantes, mas usa principalmente a razão de faces/triângulos do Blender como proxy de tamanho. Essa aproximação é incompleta para modelos Source:

- o `.vvd` é fortemente influenciado pela quantidade de vértices compilados;
- os `.vtx` armazenam índices, strips e dados específicos por variante de hardware;
- UV seams, hard edges, materiais, normais e pesos podem transformar um vértice geométrico em vários wedges/vértices compilados;
- reduzir triângulos não implica redução proporcional dos bytes compilados;
- uma ponte externa que exporta geometria aparentemente menor pode aumentar wedges e terminar maior após StudioMDL.

Medições feitas durante a auditoria confirmaram o desvio entre a razão solicitada e o resultado compilado. Em três grupos avaliados, um decimate nominal de `0.50` reduziu os vértices LOD0 compilados aproximadamente entre 41% e 42%, não 50%. Na suíte maior, parte da redução total acima de 50% vinha da remoção de `.dx80.vtx`, e não somente da simplificação geométrica.

Portanto, a principal correção conceitual é: o otimizador não deve minimizar somente faces; ele deve minimizar os bytes reais do `models/` compilado, sob restrições explícitas de estrutura e fidelidade.

## 3. Objetivos

### 3.1 Objetivo principal

Minimizar o tamanho total compilado da família de modelos:

```text
minimize compiled_models_bytes(candidate)
subject to structural_gates(candidate) == PASS
       and fidelity_gates(candidate) == PASS
```

Quando dois candidatos tiverem tamanho praticamente equivalente, vence o de maior fidelidade. A faixa considerada equivalente será configurável e inicialmente pequena, para evitar trocar qualidade perceptível por economia irrelevante.

### 3.2 Objetivos complementares

- medir economia no resultado compilado, e não apenas no intermediário Blender/SMD;
- preservar skins, bodygroups, materiais, ossos, animações, attachments, hitboxes e física;
- detectar falhas locais que médias globais escondem;
- adaptar a estratégia à família do modelo e às regiões sensíveis;
- produzir um relatório auditável de onde vieram os bytes economizados;
- permitir cancelamento e retomada sem perder candidatos válidos já calculados;
- manter o original intacto até a promoção atômica do resultado final.

### 3.3 Não objetivos da primeira versão

- alterar o formato Source ou exigir modificação do motor do Garry's Mod;
- aplicar compressão de runtime do meshoptimizer que o Source não saiba decodificar;
- prometer uma redução universal de 50% para qualquer modelo;
- reescrever as abas atuais ou o frontend Python legado;
- substituir automaticamente colisões `.phy` por colisões degradadas;
- aceitar regressões estruturais para atingir uma porcentagem de marketing;
- considerar apenas a remoção de `.dx80.vtx` como ganho geométrico.

## 4. Decisões de produto

### 4.1 Local da funcionalidade

A opção `Maximum` será adicionada à aba de Models do WPF em:

`GmodAddonCompressor-master/GmodAddonCompressor/`

O processamento pesado continuará no backend Python, acionado pelo worker empacotado. O frontend Python em `gui/` permanece legado e não receberá essa funcionalidade.

### 4.2 Ativação e posicionamento

Na primeira versão, `Maximum` será marcado como experimental porque:

- pode levar substancialmente mais tempo que os modos atuais;
- executará várias compilações por família;
- os thresholds visuais ainda precisarão ser calibrados contra validação dentro do jogo;
- diferentes categorias de addon podem exigir perfis distintos.

O usuário não precisará escolher uma porcentagem de redução. Opcionalmente poderá ajustar um orçamento de busca ou perfil de fidelidade, mas a seleção padrão continuará sendo automática.

### 4.3 O que constitui uma família

Uma família é a unidade atômica de análise, otimização, validação e fallback. Ela agrupa o modelo raiz e todos os arquivos ou modelos dependentes necessários para preservar o comportamento, incluindo, quando aplicável:

- variantes ligadas pelo QC;
- meshes de bodygroups;
- LODs;
- arquivos de animação e modelos de referência;
- skins e ordem de materiais;
- arquivos compilados associados (`.mdl`, `.vvd`, variantes `.vtx`, `.ani`, `.phy`);
- dependências descobertas durante decompile/compile.

O inventário não dependerá apenas de nomes de arquivo. As relações extraídas do QC e dos artefatos compilados são a fonte principal; heurísticas de nome servem apenas como auxílio e devem aparecer no diagnóstico quando usadas.

## 5. Arquitetura

O backend do `Maximum` será dividido em componentes com contratos explícitos.

### 5.1 Inventory

Responsável por:

- localizar e agrupar famílias;
- medir o baseline original por extensão e por família;
- registrar triângulos, vértices geométricos, vértices compilados, materiais, meshes, bones, bodygroups, skins, hitboxes, attachments e animações;
- identificar regiões e atributos sensíveis;
- gerar uma assinatura estável das entradas para o cache.

Saída principal: `FamilyManifest` imutável, usado por todas as etapas seguintes.

### 5.2 Candidate Generator

Gera candidatos por estratégia e nível de agressividade. A primeira versão deverá suportar:

- Blender Decimate como controle e fallback;
- meshoptimizer nativo como simplificador principal quando os testes confirmarem o benefício;
- Open3D como adaptador opcional, somente se o benefício justificar o aumento do pacote e o custo operacional;
- candidatos híbridos por partição/região;
- candidatos intermediários de busca após o primeiro gate reprovado.

O meshoptimizer será usado apenas no processo offline. Recursos que exigem decoder em runtime não fazem parte do formato final.

### 5.3 Appearance Repair

Recebe um candidato simplificado e o aproxima visualmente do original sem restaurar indiscriminadamente toda a geometria. Técnicas previstas:

- projeção controlada dos vértices na superfície original;
- transferência e reconstrução de normais/tangentes;
- preservação ou restauração de UVs e limites de material;
- preservação e normalização de pesos ósseos;
- proteção de silhuetas, bordas, círculos, rodas, canos, antenas e peças finas;
- weighted/custom normals quando compatíveis com o pipeline Source;
- reintrodução seletiva de detalhe em regiões que reprovaram;
- bloqueio de borders e vértices críticos quando suportado pelo simplificador.

Reparo não significa suavizar tudo. Hard edges intencionais, seams e limites entre materiais devem ser preservados.

### 5.4 StudioMDL Compiler

Compila cada candidato em workspace isolado, captura diagnósticos e produz um conjunto completo de artefatos Source. Uma compilação com arquivos ausentes ou fallback implícito não é considerada sucesso.

### 5.5 Compiled Size Analyzer

Mede o que realmente será entregue:

- bytes por `.mdl`, `.vvd`, variante `.vtx`, `.ani` e `.phy`;
- bytes totais da família e de todo `models/`;
- vértices compilados por LOD quando o formato permitir a leitura;
- diferença entre economia lossless/empacotamento e economia geométrica;
- inflação causada por wedges, seams ou variantes de hardware;
- ganho marginal de cada candidato em relação ao melhor aprovado.

O seletor nunca usará apenas o tamanho de `.smd`, `.obj`, `.blend` ou a contagem de faces como verdade final.

### 5.6 Fidelity Validator

Executa gates estruturais e visuais descritos na seção 7. O resultado deve conter métricas globais e o pior caso por:

- ângulo;
- pose;
- submesh/material;
- região;
- tipo de erro.

### 5.7 Candidate Selector

Mantém a fronteira de Pareto tamanho/fidelidade, elimina dominados e decide:

- quando continuar reduzindo;
- quando fazer busca intermediária;
- quais regiões precisam recuperar detalhe;
- quando o ganho marginal não justifica novas tentativas;
- qual candidato aprovado será promovido.

### 5.8 Cache

Armazena manifestos, intermediários, compilações, métricas e previews. A chave deve incluir pelo menos:

- hash do conteúdo original da família;
- versão do pipeline;
- versões/configurações de Crowbar, Blender, StudioMDL e simplificador;
- estratégia e parâmetros do candidato;
- perfil e thresholds de validação.

Qualquer mudança relevante invalida apenas as entradas afetadas. Cancelar mantém entradas completas e consistentes; temporários incompletos não podem ser tratados como cache válido.

## 6. Fluxo detalhado do Maximum

### 6.1 Baseline

Para cada família:

1. inventariar os artefatos originais;
2. decompilar e validar a estrutura obtida;
3. renderizar/medir referências do original;
4. registrar tamanho compilado original;
5. opcionalmente recompilar o original decompilado para separar variação do toolchain de ganho real.

Se o roundtrip de controle já alterar estrutura ou exceder tolerâncias, a família não entra em busca destrutiva. Ela é preservada e recebe diagnóstico de incompatibilidade.

### 6.2 Classificação estrutural

Antes de escolher parâmetros, o pipeline classifica meshes e regiões por características extraídas da geometria e dos dados Source, não somente pelo nome:

- rígido versus skinned;
- plano versus curvo;
- interior versus silhueta;
- circular/revolução;
- fino/alongado;
- alta densidade de seams, materiais ou hard edges;
- influência de animação e deformação;
- tamanho projetado e relevância visual.

Essa classificação define pesos de atributos, locks e famílias iniciais de candidatos.

### 6.3 Busca progressiva

O primeiro conjunto cobre níveis moderados e agressivos usando mais de uma estratégia. Depois de compilados e validados:

- o candidato equivalente ao fluxo `Fidelity` atual faz parte do conjunto inicial, para que o `Maximum` nunca perca uma solução já conhecida quando os dois usam os mesmos gates;
- se o candidato mais agressivo passar, a busca avança para redução maior;
- se um candidato reprovar e houver um aprovado menos agressivo, a busca explora o intervalo entre eles;
- se a reprovação for localizada, cria-se um candidato híbrido que recupera detalhe somente nas regiões responsáveis;
- se a compilação ficar maior apesar de menor contagem geométrica, a estratégia é penalizada por inflação de wedges/índices;
- candidatos dominados por outro menor e mais fiel são descartados.

Não há parada automática em 50%. As condições de parada são:

- nenhum candidato adicional passa os gates;
- o intervalo de busca já está abaixo da granularidade útil;
- o ganho compilado marginal está abaixo do mínimo configurado;
- o orçamento de tempo/candidatos escolhido pelo usuário acabou;
- ocorreu cancelamento.

### 6.4 Reparar e recuperar detalhe

Quando uma reprovação visual é localizada:

1. o validador gera um mapa de erro por região e atributo;
2. o seletor identifica se a causa provável é silhueta, superfície, normal, UV, material, skinning ou topologia;
3. o reparador aplica a intervenção de menor custo esperada;
4. se o reparo de atributos não bastar, uma máscara local reduz a simplificação ou restaura geometria naquela região;
5. o candidato inteiro é recompilado e revalidado, porque mudanças locais podem alterar o tamanho compilado global.

O fluxo não assume que adicionar polígonos sempre melhora a imagem. A escolha será guiada pelas métricas e pelo tamanho final.

### 6.5 Promoção

Somente candidatos que passam todos os gates podem ser promovidos. A promoção para a saída final será atômica por pacote: o pipeline prepara todo o addon em staging, verifica integridade global e então substitui/move a saída final.

Se nenhuma variante otimizada de uma família passar:

- a família original é copiada integralmente;
- ela é marcada como `preservada`, não como `otimizada`;
- seus bytes não entram no total de economia geométrica;
- o pacote pode ser concluído se todas as famílias estiverem íntegras.

Essa preservação explícita é diferente de fallback oculto: aparece no status e no relatório, e o seletor não pode atribuir a ela os ganhos de um candidato que falhou.

## 7. Contrato de fidelidade

### 7.1 Gates estruturais obrigatórios

Todos são hard gates:

- StudioMDL conclui sem erro impeditivo;
- o conjunto esperado de arquivos compilados existe;
- o resultado pode ser decompilado/inspecionado quando a validação exigir;
- nenhum skin ou bodygroup desaparece;
- ordem e referência de materiais permanecem compatíveis;
- bones, hierarquia, pesos e limites suportados permanecem válidos;
- attachments, hitboxes e sequências/animações permanecem presentes;
- LODs e meshes necessários continuam associados corretamente;
- nenhuma rachadura/topologia aberta nova é introduzida em regiões que deveriam ser contínuas;
- não ocorre fallback oculto para arquivos do original;
- arquivos `.phy` e `.ani` são preservados ou regenerados somente quando houver uma estratégia explicitamente validada.

Qualquer falha reprova o candidato, independentemente do tamanho.

### 7.2 Gates visuais

A comparação usará múltiplas vistas e representações:

- render texturizado, para UV, materiais e aparência final;
- clay/iluminação controlada, para silhueta, superfície e normais;
- mapas de diferença e métricas geométricas;
- poses de animação representativas para meshes skinned;
- vistas específicas das regiões mais sensíveis detectadas.

Categorias mínimas de métricas:

- desvio de silhueta e bordas;
- distância da superfície simplificada à original e vice-versa;
- erro de normais/sombreamento;
- distorção e descontinuidade de UV;
- alteração de material/submesh;
- erro de skinning em poses amostradas;
- componentes desconectados, interseções ou peças finas perdidas;
- diferença perceptual de imagem, usada como sinal complementar.

O gate considera o pior ângulo, pior pose e pior região, não apenas a média. Médias continuam no relatório, mas não podem esconder uma roda deformada, um cano desaparecido ou um único bodygroup quebrado.

### 7.3 Thresholds

Os thresholds numéricos não serão inventados antes da calibração. A implementação deverá:

1. coletar métricas do original contra roundtrip de controle;
2. comparar candidatos conhecidos visualmente bons e ruins;
3. calibrar limites por categoria de modelo;
4. validar os limites com inspeção dentro do Garry's Mod;
5. versionar os perfis e registrá-los no relatório/cache.

Haverá limites absolutos para falhas óbvias e limites relativos normalizados pela escala, resolução e área projetada. O perfil padrão do `Maximum` busca grande redução, mas não enfraquece hard gates estruturais.

## 8. Interface WPF e observabilidade

### 8.1 Controles

A aba Models receberá a opção `Maximum (experimental)` ao lado dos modos atuais. A integração chamará o worker com um contrato equivalente a:

```text
--optimizer-mode maximum
```

Argumentos exatos serão definidos no plano de implementação preservando compatibilidade com chamadas atuais.

### 8.2 Progresso

A interface exibirá, quando disponível:

- família atual e total de famílias;
- etapa atual: inventário, simplificação, reparo, compilação, validação ou seleção;
- candidato atual e quantidade já testada;
- menor tamanho aprovado até o momento;
- redução total provisória;
- status dos gates;
- indicação de cache reutilizado;
- estimativa de progresso baseada no orçamento, sem prometer tempo exato quando a busca for adaptativa.

### 8.3 Resultado e relatório

O resumo final mostrará:

- bytes originais e finais de toda a pasta `models/`;
- porcentagem real de redução;
- detalhamento por `.mdl`, `.vvd`, variantes `.vtx`, `.ani` e `.phy`;
- economia lossless/empacotamento separada da economia geométrica;
- famílias otimizadas, preservadas e com erro;
- melhor estratégia/candidato por família;
- quantidade de candidatos e compilações;
- vértices/triângulos originais e finais quando disponíveis;
- pior métrica visual aprovada e margem até o limite;
- motivos de fallback ou preservação.

Não será exibido sucesso enganoso quando a economia vier apenas da remoção de arquivos opcionais ou quando uma família tiver sido copiada do original.

A comparação total usa o conteúdo efetivo de `models/` na entrada e na saída final, com as mesmas regras de inclusão. Workspaces, previews, cache e intermediários nunca entram no cálculo.

## 9. Segurança, falhas, cancelamento e retomada

- Entradas originais são somente leitura para o pipeline.
- Cada candidato usa workspace isolado.
- Falha de um candidato não contamina os seguintes.
- Falha de toda a busca de uma família restaura/copia o original daquela família.
- A saída final só é promovida após verificação global do pacote.
- Cancelamento termina processos filhos com segurança, preserva cache completo e remove/marca temporários incompletos.
- `Resume` reutiliza somente entradas cuja chave e integridade sejam válidas.
- O relatório distingue `otimizado`, `preservado`, `ignorado` e `falhou`.
- O pacote não pode ser marcado como completo se houver referência obrigatória ausente ou saída parcial silenciosa.
- Logs registrarão ferramenta, versão, parâmetros, código de saída e diagnóstico por candidato sem inundar a UI principal.

## 10. Estratégia de testes e validação

### 10.1 Testes automatizados de unidade

- parsing e agrupamento de famílias;
- leitura de tamanhos e contagens dos formatos compilados suportados;
- cálculo da fronteira de Pareto e desempate;
- classificação de regiões por propriedades geométricas;
- invalidação de cache;
- serialização de progresso e relatório;
- detecção de fallback/arquivo ausente;
- lógica de busca entre aprovado e reprovado;
- cancelamento e retomada.

### 10.2 Testes de integração

- decompile -> controle -> compile;
- candidato -> reparo -> compile -> análise -> validação;
- falha isolada de Blender, simplificador e StudioMDL;
- cache hit e cache invalidado;
- promoção atômica da saída;
- worker empacotado chamado pelo WPF;
- compatibilidade dos modos `Normal` e `Fidelity`.

### 10.3 Corpus de validação

O corpus deve incluir modelos reais variados:

- carros e rodas;
- veículos terrestres complexos;
- helicópteros/aeronaves;
- armas;
- attachments e peças finas;
- props rígidos;
- modelos com múltiplos materiais/skins/bodygroups;
- modelos skinned e animados;
- casos conhecidos que inflam wedges ou falham no roundtrip.

O conjunto será separado em calibração e validação para evitar ajustar thresholds apenas aos mesmos modelos usados no desenvolvimento.

### 10.4 Critérios de aceitação da primeira versão

- nenhum hard gate estrutural falha no corpus aceito;
- nenhum candidato usa fallback oculto;
- o relatório de bytes bate com os arquivos finais no disco;
- o `Maximum` nunca promove candidato reprovado;
- o `Maximum` encontra resultado igual ou menor que o melhor candidato aprovado do `Fidelity` quando recebe orçamento suficiente e ambos usam o mesmo perfil de gates;
- cancelamento e retomada não corrompem original, saída ou cache;
- `Normal` e `Fidelity` mantêm comportamento compatível;
- inspeção manual dentro do Garry's Mod confirma os casos representativos;
- o release WPF oficial é gerado por `build_release_wpf.ps1` e a opção funciona no executável publicado.

Os percentuais alcançados serão registrados por corpus, mas não usados como critério universal. A expectativa é ultrapassar 50% em famílias que tenham redundância suficiente, continuando até 70%, 80% ou 90% quando os gates permitirem.

## 11. Empacotamento e dependências

- O worker continuará sendo a fronteira entre WPF e ferramentas pesadas.
- Se o meshoptimizer for adotado, sua biblioteca/binário e licença serão incluídos no empacotamento do worker de forma reproduzível.
- O Open3D só será incluído após benchmark de ganho líquido, considerando também o aumento de tamanho e complexidade da distribuição.
- Alterações no worker exigem reconstrução explícita do executável PyInstaller para evitar artefato stale antes do release.
- O release final continuará sendo produzido por `build_release_wpf.ps1`.
- Versões efetivas das ferramentas serão registradas no cache e no relatório de diagnóstico.

## 12. Implantação incremental

### Fase 1: medição compilada e infraestrutura

- manifestos de família;
- analyzer de bytes/vértices compilados;
- contratos de candidato, cache e relatório;
- roundtrip de controle;
- integração básica do modo no worker e WPF.

### Fase 2: busca adaptativa

- múltiplos níveis com Blender como baseline;
- fronteira de Pareto;
- busca entre último aprovado e primeiro reprovado;
- preservação/fallback explícito por família.

### Fase 3: simplificação orientada por atributos e reparo

- integração offline do meshoptimizer;
- pesos/locks derivados da geometria e dos atributos Source;
- transferência de normais, UVs e pesos;
- mapas de erro e recuperação seletiva de detalhe.

### Fase 4: validação visual completa e calibração

- renders multiângulo e poses;
- gates de pior caso;
- corpus separado de validação;
- comparação manual no Garry's Mod;
- ajuste dos perfis versionados.

### Fase 5: endurecimento e saída do experimental

- benchmarks amplos de qualidade, tamanho e tempo;
- telemetria apenas local no relatório, salvo autorização futura explícita;
- resolução dos casos incompatíveis mais frequentes;
- documentação do usuário e critérios públicos dos perfis.

## 13. Riscos e mitigação

| Risco | Impacto | Mitigação |
|---|---|---|
| Menos triângulos, mas arquivo compilado maior | Anula o objetivo | Compilar e medir todo candidato; penalizar inflação de wedges |
| Média visual esconde defeito local | Modelo perceptivelmente quebrado | Gates por pior vista, pose, região e atributo |
| Threshold excessivamente permissivo | Redução grande e feia | Calibração com exemplos bons/ruins e validação in-game |
| Threshold excessivamente rígido | Redução pequena/placebo | Perfis por categoria e métricas normalizadas; busca Pareto |
| Heurística por nome classifica peça errada | Estratégia inadequada | Priorizar geometria/QC; registrar heurísticas auxiliares |
| Tempo de busca muito alto | Experiência impraticável | Cache, poda de dominados, orçamento configurável e Resume |
| Toolchain altera o controle | Falso ganho ou falsa perda | Roundtrip baseline e gate de compatibilidade |
| Dependência nativa aumenta o release | Distribuição mais pesada | Benchmark de benefício e inclusão seletiva |
| Candidato parcial vaza para a saída | Addon quebrado | Workspace isolado e promoção atômica |
| Economia de `.dx80.vtx` mascara geometria | Relatório enganoso | Separar ganhos lossless/arquivo opcional e geométricos |

## 14. Evidências que orientaram o desenho

- Nos grupos locais medidos, decimate nominal de 50% produziu redução compilada de vértices LOD0 em torno de 41% a 42%, mostrando que `ratio` não é um orçamento de bytes.
- As experiências existentes alcançaram aproximadamente 47% a 52% em algumas suítes, mas as variantes mais agressivas pioraram métricas visuais.
- Estratégias específicas para rodas e armas preservaram melhor a aparência, porém perderam parte da redução, reforçando a necessidade de busca regional em vez de um ratio global.
- Um roundtrip externo chegou a inflar modelos compilados em cerca de 31% por duplicação de wedges; após ajustes, tornou-se apenas marginalmente menor que o baseline. Isso confirma que o intermediário não pode ser usado como métrica final.
- A documentação e os headers do Source mostram que VVD/VTX têm estruturas e custos diferentes da mera lista de triângulos.
- Simplificação attribute-aware, locks de borda/vértice, erro alvo e regularização para skinning do meshoptimizer se alinham melhor ao problema, desde que o resultado final continue sendo compilado para formatos Source normais.

## 15. Questões deliberadamente adiadas para a implementação/calibração

Estas decisões não mudam o desenho e serão resolvidas por benchmark, não por preferência arbitrária:

- valores numéricos iniciais dos thresholds visuais por categoria;
- granularidade mínima da busca e ganho marginal de parada;
- conjunto exato de poses e câmeras por categoria;
- ABI/forma de integração do meshoptimizer com o worker;
- inclusão ou não do Open3D no release;
- orçamento padrão de candidatos/tempo;
- tolerância exata usada no desempate de tamanhos equivalentes.

Cada decisão deverá ter um teste ou benchmark reproduzível e será documentada no plano/implementação correspondente.

## 16. Definição de pronto do recurso

O modo `Maximum` estará pronto para deixar o estado experimental quando:

1. estiver integrado ao WPF publicado e ao worker empacotado;
2. medir e selecionar por bytes Source compilados;
3. executar busca adaptativa com recuperação seletiva de detalhe;
4. aplicar todos os hard gates estruturais e gates visuais de pior caso;
5. gerar relatório transparente por família e extensão;
6. suportar cancelamento, cache e retomada sem corrupção;
7. preservar famílias incompatíveis sem declarar falsa otimização;
8. passar testes automatizados, corpus de validação e inspeção dentro do Garry's Mod;
9. demonstrar, nos addons adequados, reduções substanciais que superem o patamar de 50% sempre que a fidelidade permita, sem limitar artificialmente resultados melhores.
