# Compress Maximum VTF — Design

**Data:** 2026-07-20
**Produto:** aplicativo WPF em `GmodAddonCompressor-master/GmodAddonCompressor/`
**Addon principal:** `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack`

## Objetivo

Criar e provar um modo experimental `Maximum` na aba Compress. Para cada VTF, o modo deve encontrar o menor arquivo compatível com Garry's Mod/Source que permaneça dentro de limites visuais e estruturais explícitos. O modo só será integrado ao WPF se superar materialmente o Compress atual na mesma amostra, sem aceitar arquivos inválidos ou candidatos abaixo dos limites de qualidade.

Standard e Magick devem continuar funcionando como hoje. O frontend Python legado em `gui/` e o Maximum de Models ficam fora do escopo.

## Estado medido antes do experimento

O addon principal contém:

- 1.152 VTFs, totalizando 835.331.232 bytes;
- 952 VTFs DXT5 e 200 VTFs DXT1;
- versões 7.1, 7.2, 7.4 e 7.5;
- 70 texturas com pelo menos uma dimensão de 2.048 pixels;
- 7 texturas com flag `NOMIP`;
- nenhuma animação, textura volumétrica ou cubemap detectada;
- 1.525 VMTs, 193 arquivos Lua e nenhum PCF.

O pipeline atual usa `VTFEdit`, `AddonVtfCompressionPlanner`, análise de VMT/Lua/PCF, roteamento DXT1/DXT5, resize alpha-aware e preservação do original quando não há ganho. Essas proteções serão reutilizadas e ampliadas, não removidas.

## Baseline

O baseline reproduz exatamente o Compress atual com:

- modo Standard;
- somente VTF habilitado para o benchmark de texturas;
- redução de resolução 2×;
- largura mínima 8;
- altura mínima 8;
- preservação de proporção habilitada;
- demais opções atuais mantidas no valor padrão.

As três árvores isoladas serão:

1. `original`: cópia byte a byte dos arquivos selecionados;
2. `current-2x-8x8`: resultado do pipeline atual;
3. `maximum`: resultado do seletor adaptativo.

O addon original nunca será modificado.

## Seleção determinística da amostra

A amostra terá 50 VTFs, ou todos os VTFs se o conjunto tiver menos de 50. O inventário registra caminho relativo, tamanho, versão, formato, dimensões, mipmaps, flags, frames, faces implícitas, profundidade e referências semânticas.

Cada textura recebe múltiplas tags:

- opaca;
- alpha gradual;
- cutout/alpha test;
- vidro;
- normal map;
- máscara de phong/envmap em alpha;
- emissiva/self-illum;
- decal;
- efeito, sprite, partícula ou referência Lua/PCF;
- resolução 2K ou maior;
- DXT1, DXT5 e outros formatos presentes;
- sem mipmaps;
- animação, cubemap ou volume, caso existam.

A seleção usa um greedy set-cover determinístico. Primeiro escolhe o menor hash SHA-256 de caminho relativo para cada tag ainda descoberta. Em seguida preenche até 50 itens pelo menor hash, ponderando classes semânticas raras, formato e faixas de resolução para impedir que texturas comuns dominem a amostra. A semente textual é `compress-maximum-vtf-v1`. O manifesto registra a regra, o hash e a razão de inclusão de cada arquivo.

São copiados somente os VTFs selecionados, seus VMTs relacionados e os Lua/PCF que comprovadamente os referenciem. A ausência de uma categoria no addon é registrada; não se fabrica um exemplar artificial para o benchmark principal.

## Pesquisa e candidatos

A fase experimental compara pelo menos:

- o encoder do VTFCmd atual;
- DirectXTex/texconv em BC1 e BC3;
- AMD Compressonator em BC1 e BC3, incluindo configurações de alta qualidade;
- um encoder cluster-fit ou equivalente redistribuível, se os três anteriores não produzirem um Pareto satisfatório.

Artigos sobre cluster fit, métricas perceptuais, alpha-tested mipmaps e normal maps orientam os experimentos. Técnicas que exigem mudanças de shader, formatos não suportados pelo Source 1 ou decodificação em runtime são rejeitadas, mesmo quando comprimem melhor.

Para cada textura, o Maximum avalia nesta ordem:

1. resolução original;
2. dimensões divididas por 2;
3. dimensões divididas por 4.

As dimensões são arredondadas para valores válidos para Source e nunca chegam a zero. As três resoluções são sempre avaliadas quando a estrutura do VTF permite; a ordem original, 2× e 4× serve para organizar progresso e relatórios, não para interromper a busca antecipadamente.

Os formatos candidatos dependem da semântica:

- opaca e sem alpha semanticamente usado: BC1/DXT1;
- cutout estritamente binário: BC1 one-bit alpha e BC3, com decisão pelas métricas de cobertura;
- alpha gradual, vidro, emissiva com alpha, máscaras e alpha semanticamente usado: BC3/DXT5;
- normal map: BC3/DXT5 como rota principal; BC1 só pode entrar no experimento quando não houver alpha semântico e os limites angulares e previews iluminados passarem;
- formatos especiais, HDR, cubemaps, animações, volumes ou recursos desconhecidos: preservar até existir round-trip comprovado para aquela estrutura.

O tamanho comparado é sempre o VTF final, incluindo cabeçalho, recursos, thumbnail e todos os mipmaps.

## Resize e mipmaps

RGB é filtrado em espaço linear e convertido de volta ao espaço esperado pelo VTF. Texturas com transparência visual usam alpha premultiplicado durante o filtro e voltam a alpha reto antes da codificação.

Cutouts preservam cobertura no limiar definido por `$alphatestreference`; quando ausente, usa-se 0,5. A cobertura é preservada separadamente em cada mip.

Normal maps são convertidos de RGB para vetores, filtrados como vetores e renormalizados. O erro é medido como ângulo entre vetores decodificados. Canais alpha usados como máscara são processados separadamente.

Mipmaps necessários são mantidos. Uma cadeia existente não é removida para economizar espaço. Texturas `NOMIP` não recebem payload de mipmaps inúteis. Thumbnail, reflectivity e recursos só podem ser removidos ou recalculados depois de round-trip, abertura pelas ferramentas e teste no jogo demonstrarem que a mudança não afeta aparência, streaming ou compatibilidade. Recursos desconhecidos fazem o candidato ser preservado ou rejeitado.

## Validação estrutural

Antes das métricas visuais, todo candidato deve:

- ser menor que a alternativa que pretende substituir;
- possuir assinatura e cabeçalho VTF válidos;
- ser reaberto pelo parser independente;
- ser exportado/decodificado com sucesso;
- manter exatamente a versão VTF original, flags semânticas, frames, faces, profundidade e número necessário de mipmaps;
- nunca promover VTF 7.1–7.4 para 7.5; uma saída 7.5 só pode substituir um original 7.5 e ainda precisa passar por carregamento/renderização no Garry's Mod instalado;
- manter recursos conhecidos necessários e não deslocar ou truncar payloads;
- produzir dimensões e formato exatamente iguais aos registrados no manifesto do candidato.

Uma falha estrutural rejeita o candidato antes da comparação visual.

## Métricas e limites de qualidade

Original, Compress atual e Maximum são comparados depois da decodificação dos VTFs finais. Candidatos reduzidos são ampliados somente para a comparação métrica, nunca para o arquivo final.

### RGB comum, emissivo e decal

O cálculo combina:

- FLIP médio e percentil 95;
- SSIM em luminância e RGB linear;
- PSNR RGB linear;
- inspeção visual lado a lado e em diferença amplificada.

Um candidato deve cumprir simultaneamente:

- FLIP médio ≤ 0,050;
- FLIP P95 ≤ 0,150;
- SSIM ≥ 0,950;
- PSNR ≥ 30 dB;
- em relação ao Compress atual, FLIP médio não pode aumentar mais que 0,005, SSIM não pode cair mais que 0,005 e PSNR não pode cair mais que 0,5 dB.

Os limites relativos não obrigam o Maximum a reproduzir um defeito do baseline: os limites absolutos continuam obrigatórios.

### Alpha gradual e máscaras

Alpha é avaliado separado do RGB. O candidato deve cumprir:

- SSIM de alpha ≥ 0,980;
- erro absoluto médio de alpha ≤ 0,020 na escala 0–1;
- percentil 99 do erro absoluto ≤ 0,080;
- nenhum canal alpha semanticamente usado pode ser descartado;
- diferença relativa ao Compress atual limitada a 0,005 de SSIM e 0,005 de erro médio.

### Cutouts

Para o limiar do VMT e também para 0,5, o candidato deve cumprir:

- erro de cobertura no mip principal ≤ 1 ponto percentual;
- erro de cobertura em cada mip ≤ 2 pontos percentuais;
- intersection-over-union da máscara binária ≥ 0,980 no mip principal;
- ausência de halos ou desaparecimento visível no preview sobre fundos claro, escuro e quadriculado.

### Normal maps

Depois de decodificar e renormalizar os vetores, o candidato deve cumprir:

- erro angular médio ≤ 3°;
- erro angular P95 ≤ 8°;
- erro angular máximo ≤ 25°;
- aumento sobre o Compress atual ≤ 0,5° na média e ≤ 1° no P95;
- nenhum sinal de inversão, achatamento ou quebra especular no preview iluminado.

### Congelamento dos gates

Os valores acima são definidos antes de observar quais candidatos vencem. O baseline pode motivar limites mais rigorosos, mas os limites não podem ser relaxados para aceitar um candidato menor. Qualquer revisão fica registrada como nova versão do protocolo e exige repetir todo o benchmark.

## Inspeção visual

Cada uma das 50 texturas recebe um painel com:

- original, Compress atual e Maximum;
- visualização 1:1 e zoom de regiões com maior erro;
- diferença amplificada;
- alpha em escala de cinza e sobre fundo quadriculado;
- cobertura por mip para cutouts;
- normal map como RGB e aplicado a uma superfície iluminada;
- formato, resolução, tamanho e métricas.

Todos os 50 painéis são inspecionados. A inspeção visual pode rejeitar um candidato aprovado numericamente, mas nunca aprovar um candidato que falhou nos limites numéricos ou estruturais.

## Seleção adaptativa

Cada candidato aprovado forma um ponto `(tamanho, qualidade, resolução)`. A seleção elimina pontos dominados e escolhe o menor VTF aprovado. Em empate de tamanho, escolhe:

1. maior resolução;
2. menor FLIP ou erro angular;
3. menos alterações de metadados;
4. encoder com menor tempo.

Se nenhum candidato passar, o arquivo original é preservado byte a byte. Um arquivo menor nunca vence somente por ser menor.

## Gate para integração

O Maximum só entra no WPF quando, na mesma amostra:

- o total de bytes VTF do Maximum for pelo menos 10% menor que o total do `current-2x-8x8`;
- nenhum candidato aceito falhar nos limites estruturais, numéricos ou visuais;
- todos os 50 VTFs finais puderem ser reabertos e decodificados;
- todos os materiais da amostra carregarem no Garry's Mod sem erro de VTF/material no console;
- a contagem de arquivos e referências relacionadas continuar consistente;
- o relatório for reproduzível a partir do manifesto e das versões registradas das ferramentas.

Se o ganho ficar abaixo de 10%, a hipótese não é considerada comprovada. Novos encoders, filtros ou políticas podem ser experimentados, mas a UI permanece inalterada até o gate ser atingido.

## Arquitetura experimental

O harness de benchmark fica separado da UI e produz:

- manifesto da amostra;
- inventário original;
- árvore baseline atual;
- matriz de candidatos Maximum;
- métricas por candidato;
- decisão e motivos de rejeição;
- JSON e CSV agregados e por textura;
- painéis e previews visuais;
- duração por etapa e total.

O harness usa interfaces separadas para inventário, análise semântica, encoder, montagem VTF, validação, métricas, seleção e relatório. Ferramentas de pesquisa e artefatos volumosos ficam fora do commit.

## Integração WPF condicionada ao gate

Depois da comprovação:

- `Maximum (experimental)` será o terceiro modo da aba Compress;
- Standard e Magick manterão rotas e valores atuais;
- somente VTF usará busca adaptativa; os demais tipos continuarão nas rotas existentes;
- os controles globais de resize não decidirão a resolução dos VTFs em Maximum;
- o texto da UI explicará que Maximum testa resolução original, 2× e 4×;
- o Maximum de Models não será alterado.

A UI mostrará:

- arquivo atual;
- resolução, formato e encoder do candidato;
- tamanho e redução;
- aceite ou motivo de rejeição;
- quantidade processada, preservada, reduzida e rejeitada;
- tempo decorrido;
- resumo agregado e caminho do relatório detalhado.

O paralelismo será limitado por CPU, memória e capacidade do encoder. Cada VTF terá diretório temporário exclusivo. O original só será substituído por operação atômica depois da validação completa. Timeout, exceção, cancelamento ou falha de validação preservam o original.

Somente a ferramenta vencedora, redistribuível e necessária em produção será empacotada no release. Todas as métricas usadas como gate de aceite no experimento também serão aplicadas em produção. Métricas adicionais usadas apenas para pesquisa podem ficar fora do executável, mas nunca podem ser removidas se sua ausência mudar uma decisão de aceite ou rejeição.

## Testes e validação final

Serão adicionados testes automatizados para:

- leitura de cabeçalho, recursos, mipmaps e payload;
- análise VMT/Lua/PCF e uso de alpha;
- seleção determinística da amostra;
- resize linear e premultiplicado;
- cobertura de cutout;
- renormalização e erro angular;
- validação estrutural;
- desempate e preservação quando não há vencedor;
- substituição atômica e limpeza de temporários;
- preservação do comportamento Standard e Magick.

A validação final inclui:

1. repetir o benchmark completo a partir de uma área limpa;
2. comparar hashes do manifesto e decisões;
3. executar todos os testes;
4. compilar o WPF;
5. gerar release com `./build_release_wpf.ps1`;
6. abrir o executável publicado;
7. validar Compress Standard, Magick e Maximum;
8. abrir as abas Models, Descompactar addons e Pipeline para regressão básica;
9. executar Maximum sobre a amostra isolada pelo executável publicado;
10. montar a amostra como addon de teste e carregá-la no Garry's Mod, registrando o console e rejeitando qualquer erro de leitura de VTF ou material;
11. capturar no renderizador Source um conjunto estratificado de materiais opacos, vidro, cutout, emissivo, decal e normal map para confrontar com os previews decodificados;
12. confirmar relatório, progresso, tempos e preservação do addon original.

## Versionamento e entrega

As mudanças locais existentes em `gui/` e arquivos soltos não relacionados serão preservadas e excluídas do commit. A especificação, implementação, testes e eventual ferramenta redistribuível só serão commitados e enviados depois que o gate de integração e a validação final passarem. O push usará a branch atual e não incluirá amostras, temporários, previews em massa, logs ou downloads de pesquisa.

O relatório final ao usuário informará números reais de tamanho original, Compress atual e Maximum; reduções; métricas; duração; contagens de preservados, reduzidos e rejeitados; localização da amostra e da árvore Maximum; hash do manifesto; commit e destino do push.
