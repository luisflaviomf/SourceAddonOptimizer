# Teste na Workshop: Winchester borrada e tiros no LVS (2026-11-11)

O Luis publicou na Workshop o LVS e o M9K otimizados (perfil Custo-benefício, pastas
`C:\GModLab\workshop\lvs_otimizado` e `m9k_otimizado`), baixou pelo Steam e testou. Quase tudo certo; dois
relatos:

1. A Winchester 87 do M9K ficou toda borrada: a forma da arma certa, a textura não.
2. No LVS, atirando no carro: a bala atravessa o vidro e acerta o banco lá dentro como se fosse metal, e o
   chassi não fica com marcas de bala.

## 1. A textura que não carregava

### Causa

Os dois VMT da Winchester escrevem o caminho da textura com o separador repetido:

```
"$baseTexture" "models\\weapons\v_models\1887winchester\shot_m3super90"
```

O jogo aceita: abre o arquivo e chama a textura de `models//weapons/v_models/1887winchester/shot_m3super90`
(conferido no cliente de laboratório, com a barra dupla no nome). O nosso leitor comparava o texto ao pé da
letra, não achava o arquivo e concluía que nenhum material usava a textura. No manifesto do stream ela ficava
sem material nenhum: virava stream, mas o runtime não tinha onde aplicar o resultado, e a arma ficava com a
miniatura de 16 px para sempre. A análise também a tratava como textura sem uso e a reduzia pela metade.

### Não era só essa arma

O mesmo efeito (textura que vira stream e nunca é aplicada num material) aparecia por três caminhos. Na
bancada, 23 texturas só eram citadas por algum deles e 75 eram citadas em parte:

| Forma de citar a textura | Exemplos na bancada |
|---|---|
| Caminho escrito de um jeito que o jogo aceita: separador repetido, extensão no nome (`...1024.vmt`) | Winchester 87 e silenciador da TMP9 (M9K), 3 texturas do ARC9 |
| Parâmetro fora da nossa lista | `$texture2` (miras do M9K), `$refracttinttexture` (vidros e faróis do LVS, 31), `$emissiveblendbasetexture` (luzes do GLIDE, 39), `$dudvmap`, `$envmap` com textura própria |
| Dentro de um bloco ou com chave condicional | blocos `>=dx90`, proxies (camuflagem das mãos do ARC9 FAS) |

Havia um quarto caso que os arquivos do addon não mostram: um material de **outro** addon que usa uma
textura deste (pacotes de textura compartilhados). O manifesto só conhece os materiais que estavam ao lado
do addon na hora de otimizar.

### Correção

- **Nomes lidos como o jogo lê** (`texlab/materials.py`: `clean_path`, `normalize_texture_ref`,
  `MaterialResolver.texture`): qualquer barra, barras repetidas contam como uma, a extensão que vier no nome
  é descartada quando o arquivo existe sem ela. Vale para o texto dos VMT e para os nomes de material dentro
  dos modelos.
- **Todo valor de VMT que cita uma textura entra no manifesto** (`Material.other`,
  `Material.all_textures()`), seja qual for o parâmetro, inclusive dentro de blocos e com chave condicional.
  O runtime só troca a textura onde o jogo de fato tem aquela textura naquele parâmetro, então um valor que
  não era textura não causa nada. Textura citada por um parâmetro que a análise não modela mantém o alfa
  inteiro (`alpha_uses`, `alpha_unused_refs`).
- **O runtime olha dentro dos materiais que aparecem na tela** (`look_inside`, núcleo `CORE_VERSION` 3):
  pergunta ao jogo, uma vez por material e poucos por quadro, quais texturas ele tem; se alguma é nossa e o
  manifesto não sabia, passa a aplicar ali também. Cobre os materiais de outros addons. O `norm` do runtime
  também passou a juntar barras repetidas, porque é com elas que o jogo devolve o nome.

A redução dessas texturas não mudou de regra: as que agora têm material conhecido são medidas pelo modelo
que as mostra (a Winchester, arma de primeira pessoa, deixou de ser reduzida); as citadas só por parâmetro
fora da lista continuam com a redução padrão de textura sem uso.

### Conferência

- No jogo (cliente de laboratório), M9K otimizado de novo com o código corrigido: os dois materiais da
  Winchester mostram a textura do stream em 1024x1024, a arma aparece com a madeira e a gravação do original.
  Antes, `$basetexture` ficava em 16x16.
- Manifesto do M9K: texturas sem nenhum material, 18 → 13.
- 4 testes novos (`tests/test_texture_stream.py`): nomes lidos como o jogo lê, manifesto com todos os valores,
  runtime com o nome que o jogo dá (barra dupla), runtime achando a textura num material de outro addon.

- No jogo, com as pastas finais de envio (saída do exe publicado): Winchester em 1024x1024 aos 3 s; o
  silenciador da TMP9 (nome com `.vmt`) e o `$texture2` da mira (`scope/gdcw_noise`) agora chegam ao tamanho
  do stream, antes ficavam em 16x16; 194 texturas decodificadas, nenhuma falha, nenhum erro de Lua.
- Bancada juntada pelo exe publicado (`C:\GModLab\bench\mcb13`), contra a rodada anterior (`mcb11`):
  726,34 → 726,46 MiB (redução de 89,54%, igual). Dos 37.822 arquivos fora do stream, 13 mudaram (as
  miniaturas das texturas recodificadas). Dos 4.804 arquivos de stream de textura, 21 foram recodificados
  (alfa mantido, ou textura que deixou de ser reduzida como "sem uso"); 87 texturas ganharam materiais no
  manifesto; as sem nenhum material foram de 228 para 211 (as que sobram são as que nenhum VMT cita).
- Duas rodadas da bancada com o código corrigido (`mcb12`, `mcb13`): os 37.822 arquivos fora do stream e os
  4.804 de stream idênticos byte a byte.
- Tempo: a rodada levou 800 s, contra 668 s de manhã. Medido lado a lado no M9K, o código novo não ficou mais
  lento (etapa de texturas 41,1 → 38,0 s, etapa de modelos 23,6 → 20,3 s); a diferença é o estado da máquina
  na hora da rodada.
- 465 testes Python.

## 2. Tiros no LVS

### Como foi medido

O mesmo carro, os mesmos tiros, no LVS original e no otimizado, no cliente de laboratório (um jogador,
`gm_construct`): Dodge Monaco e Dodge Charger; .357 do jogo e a Winchester do M9K disparadas como um jogador
dispara; tiros no para-brisa, vidro traseiro, capô, portas, para-lama e porta-malas; posição de cada impacto,
superfície, vida do carro, estágio de cada peça do sistema de danos e capturas de tela antes e depois. Mais
288 raios atravessando o carro pelos três eixos.

### O que é igual

Tudo o que a bala lê:

- Os 264 arquivos de colisão (`.phy`) do LVS são idênticos byte a byte; caixas de acerto, propriedade de
  superfície e contorno dos 267 modelos também (só mudou a malha desenhada; 34 modelos ganharam o osso do
  espelho).
- Cada tiro para no mesmo ponto, na mesma superfície (`metalvehicle`), e tira a mesma vida do carro nos dois.
  Os 288 raios dão o mesmo resultado.
- O para-brisa racha e depois quebra na mesma sequência nos dois (o sistema de peças do LVS lê as caixas de
  acerto do modelo e troca o bodygroup; o stream acompanha a troca).

**A bala atravessar o vidro e acertar lá dentro como metal é o comportamento do LVS original.** O casco de
colisão desses carros não inclui os vidros: a bala passa pelo plano do para-brisa e para no casco, que é
todo de metal. O dano no vidro vem das caixas do sistema de peças, não do impacto. O LVS ainda refaz o tiro
atravessando o carro (para acertar quem está dentro ou atrás).

### O que é diferente: as marcas de bala

No Charger original, um dos três tiros de .357 na porta deixou a marca de bala; no otimizado, nenhum. É uma
perda real do stream de modelos, e vale para qualquer modelo em stream, não só para o LVS.

O jogo guarda as marcas (decals) na malha do modelo que está no `.mdl` e as desenha junto com ela. No modelo
em stream essa malha é a de reserva (2% dos triângulos) e quem aparece na tela é a geometria desenhada pelo
Lua, que o jogo não conhece.

No Monaco, nem o original mostra marca na lataria: o casco de colisão fica alguns centímetros para dentro e o
jogo não acha superfície perto do impacto.

### O que foi tentado

Desenhar, por cima do stream, as marcas que o próprio jogo guardou (a malha de reserva com um material
invisível, só as marcas visíveis). Não serve: a malha de reserva é grosseira demais, a marca fica fora da
superfície real e some ou flutua.

### A solução: marcas feitas na geometria do stream

Primeiro o Luis decidiu deixar como estava; depois pediu um teste rápido só no Charger para ver se a ideia
funcionava. Funcionou, e ele pediu para terminar. Ficou assim (runtime de modelos `REVISION` 4):

1. **O servidor avisa onde a bala parou.** Cada addon otimizado leva
   `lua/autorun/server/gmo_stream_sv_<slug>.lua` (`model_stream/server.lua` mais a lista dos modelos em
   stream do addon). Ele escuta o gancho `PostEntityFireBullets` e, quando a bala para num modelo em stream,
   manda uma mensagem pequena e não confiável (entidade, ponto, direção, tipo de superfície) só para quem
   enxerga o ponto. Limites: 12 mensagens por segundo por modelo atingido e 60 no total. `gmo_stream_marks 0`
   no servidor desliga. O arquivo é carregado uma vez, quantos addons otimizados o servidor tiver.
2. **O cliente acha a superfície de verdade.** Ao receber, guarda o caminho da bala no espaço de cada peça
   que está sendo desenhada (o carro pode andar enquanto isso). Os triângulos do modelo são lidos de novo do
   arquivo de stream na primeira marca daquele modelo, alguns milissegundos por quadro, e separados numa
   grade de células de 6 unidades. O raio percorre só as células que atravessa, 8 unidades antes e depois do
   ponto avisado, e fica com o triângulo mais próximo.
3. **A marca é recortada nos triângulos.** Os triângulos sob um quadrado do tamanho do decal do jogo (6,4
   unidades para buraco em metal) são cortados no quadrado e viram uma malha pequena, desenhada por cima do
   stream com o material de decal do próprio jogo (o `$modelmaterial` do decal, por exemplo
   `decals/metal/shot3`). A marca segue o osso da peça e some se a peça for trocada (bodygroup).

Custos e limites:

- Nada fica na memória enquanto ninguém atira. Os triângulos de um modelo ficam guardados por 2 minutos
  depois da última marca.
- Até 24 marcas por entidade e 256 no total; a mais antiga sai.
- `gmo_stream_decals 0` no cliente desliga.
- Só marcas de bala disparadas pelo jogo (`FireBullets`) e impactos que o Lua do cliente pede
  (`util.Effect("Impact")`). Sangue, explosões e tinta não entram.
- A marca aparece onde a bala passa pela lataria, mesmo quando a colisão do carro fica alguns centímetros
  para dentro; por isso aparece em mais tiros que no original.

Medido no Charger (296 mil triângulos, 188 peças), no cliente de laboratório:

| | Resultado |
|---|---|
| 3 tiros de .357 na porta, um jogador | 3 marcas (o original mostra 1) |
| 3 disparos da Winchester do M9K | mais 20 marcas |
| Servidor dedicado com o cliente conectado | 3 marcas de 3 balas, 20 de 24 chumbos (limite por segundo) |
| Leitura dos triângulos na primeira marca | 170 a 225 ms, espalhados por 41 a 54 quadros |
| Marca mais lenta | 5 a 7 ms (a primeira versão, sem a grade, levava 14 a 30 ms) |
| Erros de Lua | nenhum |

Testes novos em `tests/test_model_stream.py`: a marca numa folha plana é exatamente o quadrado do decal; os
triângulos lidos de novo são os do stream e o raio dá o mesmo resultado que testar todos os triângulos; o
arquivo do servidor é escrito, carrega como Lua e avisa só os modelos em stream, com os limites por segundo.

Com as pastas finais (saída do exe publicado), no cliente de laboratório: 3 marcas de 3 tiros de .357 e 23
depois da Winchester, nenhum erro; o gmad empacota o arquivo do servidor junto com o resto.

Bancada juntada pelo exe publicado (`C:\GModLab\bench\mcb14`): 726,46 → 726,67 MiB (o runtime de modelos
maior e o arquivo do servidor em cada addon). Em cada grupo só mudou o runtime de modelos e apareceu o arquivo
do servidor; no LVS e no M9K os outros arquivos foram conferidos byte a byte, nos demais pelo tamanho.

O simfphys falhou uma vez na etapa de texturas e sons e passou quando a rodada foi repetida; o worker
empacotado rodado sozinho no mesmo addon também passou. A máquina estava com 6 a 8 GB livres do limite de
reserva de memória (o resto ocupado por outros programas abertos), o mesmo quadro da falha da manhã descrita
em `docs/velocidade-2026-11-11.md`. A mensagem de erro do worker de texturas não aparece no log do
assistente, então desta vez a causa não ficou provada. Fica pendente fazer o assistente mostrar essa
mensagem.

### Observações do mesmo teste

- A textura do vidro rachado fica mais suave que a do original (foi reduzida como as outras; não é falha de
  carregamento).
- "Demora um pouquinho para aparecer": o carro é decodificado na primeira vez que entra na tela. As texturas
  ficam guardadas entre sessões; a geometria é decodificada a cada sessão.

## Ferramentas do laboratório

Na pasta de rascunho da sessão: `labrun.py` (instala pastas de addon no cliente de laboratório e roda um
script Lua), `probe1.lua` a `probe6.lua` (os testes acima), `mdl_phys_diff.py` (o que a bala lê de cada
modelo, original contra otimizado), `vmt_audit.py` (referências de textura que o programa não via),
`lab_addon.py` (cópia de um addon otimizado com os runtimes do repositório). Resultados em
`C:\GModLab\bench\texstream\out\probe_*`.
