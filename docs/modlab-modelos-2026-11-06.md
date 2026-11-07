# Modelos: pesquisa de um caminho novo (06/11/2026)

Laboratório em `C:\GModLab\modlab` (scripts e dados). Pack: `lvs_cars_pack` inteiro
(163 modelos, 549 MB de modelos). Nada disso está no produto ainda.

## Onde estão os bytes

| Arquivo | Disco | Observação |
|---|---:|---|
| `.vvd` | 360 MB (66%) | 5,9 milhões de vértices × 64 bytes fixos (posição, normal, UV, pesos, tangente) |
| `.dx80.vtx` | 92 MB | o GMod roda em DX9 e não usa; o produto já apaga |
| `.dx90.vtx` | 92 MB | 1 grupo / 1 lista de triângulos por malha, sem LOD |
| `.mdl` + `.phy` | 4,5 MB | |

Todos os modelos têm 1 LOD; 96% dos vértices estão nas carrocerias, 3% nas rodas.

## O que NÃO dá ganho (medido)

- **Geometria nunca visível** (câmeras por fora em 256 direções × 3 distâncias + dos bancos
  reais do Lua do LVS; peças móveis e vidro não escondem nada): só 0,3% a 5% dos triângulos.
- **Unir vértices quase iguais**: 0,6% mesmo com 5° de tolerância nas normais.
- **Vértices removíveis sem perda exata** (áreas planas): 0,1%.
- **Vértices sem uso / triângulos degenerados**: 0,01% / 0,07%.
- **Tirar tangentes**: todos os modelos usam normal map ou phong.
- **Simplificar mantendo a aparência**: com normal dentro de 1° e UV dentro de meio texel,
  sobram 91–98% dos vértices; com 3°, 80–94%. Só a geometria permitiria 50–74%, mas a
  normal interpolada muda o reflexo (envmap/phong) — é isso que denuncia o carro simplificado.
  Os 15–26% dos modos atuais vêm de aceitar essa diferença.

## O que dá ganho: normais "exatas no motor"

O VertexLitGeneric (e Refract, Skin...) pede `VERTEX_FORMAT_COMPRESSED`: o motor empacota
cada normal e tangente em 2 bytes com `PackNormal_UBYTE4` (`src/public/mathlib/mathlib.h`,
Source SDK 2013) antes de mandar para a placa — 63 degraus por eixo, ~0,7°. Qualquer float
dentro do mesmo degrau vira os mesmos bytes na GPU.

`engine_exact.py` troca cada normal/tangente pelo float mais "redondo" (grades de 2^-5 a 2^-14)
que cai no mesmo degrau, com 10% de folga da borda; originais quase em cima de uma borda (onde
x87/FMA poderia escolher o degrau vizinho) ficam intactos. Posição, UV, pesos e o sinal da
tangente nunca mudam. Os floats ganham muitos zeros no fim e o LZMA da Workshop comprime
muito melhor.

Resultado (`lvs_cars_pack`, download estimado com LZMA por arquivo):

| Variante | Disco | Download |
|---|---:|---:|
| Original da Workshop | 1.375 MB | 321 MB |
| Texturas Tela + sem dx80 | 620 MB | 208 MB |
| **+ normais exatas** | 620 MB | **147 MB** |

Modelos: download 149 → 88 MB (-41%). No jogo (cliente isolado, 163 modelos × 2 vistas):
todas as diferenças restantes são ruído de captura (as mesmas que variam entre duas execuções
do pack original); as repetidas isoladamente saem idênticas pixel a pixel, inclusive o reflexo
cromado da roda do MG MGB que muda com qualquer outra quantização.

Limite: se uma configuração desligar a compressão de vértices (não visto em DX9), as normais
diferem no máximo ~0,45° do original.

Integrado no produto (07/11): `vvd_engine_exact.py`, passo padrão do empacotamento de Models
(todos os modos e a Compressão completa); `--no-vvd-exact-normals` desliga.

## Redução em disco (07/11): o que deu e por que não passa de ~35% mantendo a aparência

Pack H (`C:\GModLab\modlab\packH`, lvs_cars_pack inteiro):

| | Disco | Download |
|---|---:|---:|
| Original | 1.375 MB | 321 MB |
| Texturas Tela + sem dx80 + normais exatas | 620 MB | 147 MB |
| **+ simplificação de modelos (H)** | **518 MB** | **125 MB** |

Modelos: 549 → 355 MB em disco (−35% contando o dx80; 78% dos vértices ficam), 166 → 66 MB no
download. No jogo (163 modelos): FLIP médio 0,008 de perto e 0,011 de longe; nenhum erro novo.

Método (`cssimp.py` + `partsimp.py` + `srcmodel_io.py`, sem descompilar):
1. **Costuras consistentes**: pontos das bordas das ilhas (UV/quina) saem ao longo de cada costura
   (Douglas-Peucker) igualmente em todas as ilhas que a compartilham — nenhuma fresta pode abrir.
   Cantos (3+ ilhas, troca de osso) ficam. A tolerância de cada costura é ≤ 1% do tamanho dela
   própria: círculo pequeno (calota, furo de parafuso) continua redondo.
2. **Interior de cada ilha** com o meshoptimizer, bordas travadas, tolerância pela própria ilha.
3. Limites medidos: 2 px a 40 unidades (1080p), normal ≤ 20°, UV ≤ 1 texel da textura real do
   material (os vãos/linhas desenhados na textura sumiam com 4 texels), peças finas ≥ 20% da
   espessura, nada mais fino do que se vê a 16 unidades.

Por que não dá 70–90% sem mudar a aparência: os modelos do LVS são picados em milhares de ilhas
minúsculas (E46: 5.218 ilhas, mediana 8 vértices; Charger: 17.980, mediana 6; 45–78% dos vértices
na borda de uma ilha). A maioria das divisões muda normal **e** UV (quinas reais com UV própria).
Toda simplificação que respeita textura e sombreamento para nesse piso; afrouxar normais/UV
(testado: configuração "B") deixa manchas no reflexo e peças que somem. O próximo salto exige
**refazer a UV e assar texturas novas** para uma malha nova (LOD com bake) — projeto maior.

Defeitos vistos e corrigidos no caminho: contorno do capô do Charger (UV), calota da roda Alpina
virando polígono com frestas (tolerância pela peça inteira → passou a ser pela costura/ilha).

## No produto (07/11): modo **Tela** da aba Modelos

Worker `modscreen` (`model_screen/`), botão "Optimize Models" com o modo **Tela** marcado
(também vale para a aba Pipeline). Não usa Blender, Crowbar nem studiomdl: edita os binários.
Copia o addon para `<addon><sufixo>`, simplifica cada modelo, aplica normais exatas, apaga
dx80/sw.vtx e faz strips no dx90.vtx. Modelo que falhar fica exatamente como era.

| Perfil | O que muda | lvs_cars_pack: modelos | No jogo (FLIP médio) |
|---|---|---:|---:|
| **Equilibrado** | normal ≤ 20°, UV ≤ 1 texel real, 2 px a 40 u | 549 → **349 MB (−36%)** | 0,007 perto / 0,010 longe |
| **Forte** | normal ≤ 35°, UV ≤ 3 texels, 6 px a 40 u, **VVD sem tangentes** | 549 → **257 MB (−53%)** | 0,018 perto / 0,023 longe |

163 modelos, nenhuma falha, nenhum material com erro novo.

### Velocidade (07/11): Rust + paralelismo, saída idêntica

`lvs_cars_pack` inteiro, perfil Forte, `--jobs 8`. A pasta de saída inteira (3.392 arquivos) e
os três relatórios (`modscreen`, `vvd_engine_exact`, `vtx_tristrip`) saem byte a byte iguais
aos do código anterior:

| Etapa | Antes | Depois | O que mudou |
|---|---:|---:|---|
| Copiar o addon | 13–30 s | 2–4 s | cópia em threads; `models/` primeiro, o resto copia em segundo plano enquanto os modelos são simplificados (os materiais são lidos da origem, mesmos bytes) |
| Simplificar | 57 s | ~15–25 s | costuras (`_seams`) e componentes em Rust (`gmo_seams`, `gmo_components`); índice de materiais montado uma vez por processo (antes: uma vez por modelo); cabeçalho VTF sem ler o arquivo todo; ilhas sem `np.full` do tamanho do modelo; maiores modelos primeiro |
| Normais exatas | 8 s | ~3 s | arquivos em paralelo no mesmo pool |
| TRI_STRIP | 48 s | ~3–4 s | comparação de topologia em numpy, busca binária de blocos, arrays ctypes rápidos, relatório sem `asdict` (que copiava a lista de triângulos), arquivos em paralelo |
| **Total** | **127 s** | **26–35 s** | |

Tempo de CPU só de `simplify_model` nos 163 modelos: 512 s → 60 s (Forte), 530 s → 63 s
(Equilibrado), resultado idêntico nos dois perfis.

Como o Rust dá o mesmo resultado: a caminhada das costuras depende da ordem de iteração dos
`set` do Python, então `seams.rs` reproduz a tabela de set do CPython 3.11 (mesmos slots,
sondagem e redimensionamento; conferido contra sets reais). As contas seguem o numpy desta
máquina: produto escalar de 3 elementos sequencial sem FMA (ddot do OpenBLAS) e, no
Douglas-Peucker, `(k,3) @ (3,)` como `fma(a2,b2, fma(a0,b0, a1*b1))` (dgemv) — medido em 120 mil
casos. O caminho Python continua como referência e reserva (sem a DLL ou com `GMO_NATIVE=0`),
também idêntico nos 163 modelos. Observação: o numpy pode arredondar essas contas de outro jeito
em outra CPU (kernel do OpenBLAS); a DLL dá o resultado desta máquina em qualquer CPU.

`--jobs 0` (o padrão do WPF quando o usuário não escolheu) agora é automático (metade das CPUs
lógicas, até 8); antes virava 1 processo. O empacotamento dos outros modos (normal, fidelity,
maximum) também roda normais exatas e TRI_STRIP em paralelo (`--jobs`).

**Forte é o padrão** (07/11, escolhido depois do teste no jogo): a interface abre com Forte e o
`SourceAddonOptimizerCommandBuilder` só manda `balanced` quando Equilibrado é escolhido. Quem
já salvou um perfil nas configurações continua com o perfil salvo. O worker chamado direto, sem
`--profile`, continua `balanced`.

Tangentes: o motor tem `HasTangentData()` e calcula/usa outro referencial quando o VVD não traz
tangentes (25% do VVD). Na carroceria quase não muda; em lentes com normal map forte (setas
laterais) e na banda de rodagem de pneus o relevo fica mais "liso" — por isso só no Forte.

Por que não 70–90% sem estragar: piso das ilhas (acima) + LVS troca materiais de luz por Lua
(`SubMaterialID`) com a UV original, então refazer UV com bake de texturas quebraria as luzes.
O modo permissivo do meshoptimizer 1.2 (atravessar costuras) foi testado: mesma redução, porque
UVs de ilhas diferentes não podem se juntar sem distorcer a textura.

Testes: `tests/test_model_screen.py`, contrato C# (`tests/wpf_optimizer_contract`), harness de
interface `experiments/BancadaUiSmoke --tela <pasta>` (render da aba) e `--tela-run <addon>`
(botão real, worker real).

## Forte + espelho (07/11, noite): perfil `strong_mirror`

Pedido: modelos 70–80% menores em disco, ainda bonitos. Estudo completo em `C:\GModLab\modlab3`.

**Onde ficam os bytes (Forte, 260 MB):** VVD 78% (48 bytes fixos por vértice), VTX 20%, MDL+PHY 2%. Só
menos vértices reduz disco. Por que sobram: os carros têm ~1 vértice por triângulo, e as divisões são
quinas reais de ~90° (36–45%), bordas entre materiais (24–48%) e costuras de UV (13–29%). Mesmo com
tolerâncias muito frouxas sobram ~52% dos vértices: o piso é a estrutura, não a tolerância.

**Duas coisas novas:**
1. **Simplificação por peça inteira** (`model_screen/remesh.py`): cada peça fisicamente conectada vai
   inteira ao meshoptimizer; costuras de UV que importam, quinas, bordas de material e de osso são
   "seams" (os dois lados colapsam juntos, sem frestas; bordas deslizam sobre si mesmas). Costuras de UV
   de materiais cujas texturas não dependem de UV (cor lisa, máscara de "glitter" da pintura) somem.
   Mesmos limites do Forte (6 px a 40 u, normal 35°, UV 3 texels, redondos/finos protegidos).
2. **Espelhamento por osso** (`model_screen/mirror.py`): o lado direito não é guardado. Um strip extra
   no mesmo grupo do VTX reusa os vértices do lado esquerdo; as trocas de osso desse strip apontam para
   "ossos-espelho" (filhos do osso alvo, transformação local identidade, `poseToBone` = `poseToBone` do
   alvo × reflexão). Portas mapeiam LD → RD. O motor desenha certo (testado no jogo). Só espelha
   triângulos cujo parceiro existe no outro lado e cujas texturas mostram o mesmo nas duas UVs; todas
   as texturas do material contam (inclusive `$refracttinttexture`: a sirene vermelha/azul da viatura
   Monaco ficava errada antes dessa regra). Travas por posição (o meshoptimizer lê a trava de um único
   registro por posição): interface entre parte espelhada e própria, seus parceiros e o plano central.

`lvs_cars_pack` inteiro (163 modelos), no jogo contra o original (8 carros × 6 vistas; ruído de captura
0,004):

| | Disco (models) | Download | FLIP médio | Pior região |
|---|---:|---:|---:|---:|
| Original | 549 MB | 166 MB | — | — |
| Forte | 260 MB (−53%) | 49,6 MB | 0,037 | 0,24 |
| **Forte + espelho** | **213 MB (−61%)** | 42,4 MB | **0,038** | **0,17** |

O espelho tem teto na própria simetria: 40–56% dos vértices do lado direito não têm par no esquerdo
(interior assimétrico, triangulação diferente) e 11–16% estão em material separado de propósito
(setas esquerda/direita). Refazer UV e reassar as texturas de AO daria só ~−65%; passar disso exige
perda visível. Limite conhecido: marcas de tiro (decals) podem não aparecer nas partes espelhadas
(o motor calcula decals na CPU com o osso do VVD).

O perfil procura materiais também nos addons vizinhos da mesma pasta (ex. `lvs_framework` ao lado de
`lvs_cars_pack`), com prioridade menor; sem isso o pack ficaria em ~230 MB. Os perfis Equilibrado e
Forte não mudaram (mesma saída byte a byte). A busca de vizinhos de espelho usa grade em numpy
(`model_screen/gridsearch.py`), sem scipy no worker. Pack inteiro: ~55 s (8 processos).

Testes: `tests/test_model_screen_mirror.py` (busca em grade, inserção de ossos, E46 determinístico e
carregável, imagens espelhadas caindo sobre o lado direito real, regra da textura de refração, CLI de
ponta a ponta), contrato C# (perfil padrão `strong_mirror`).

## Ferramentas do laboratório

- `srcmodel_io.py`: reescreve MDL/VVD/VTX direto no binário (subconjunto de vértices + nova
  lista de triângulos por malha), sem descompilar; ida e volta idêntica nos 163 modelos.
- `visibility.py`: rasterizador de IDs (numba) para geometria visível.
- `simplify_pack.py`: simplificação com orçamento em pixels (meshoptimizer como motor de colapso).
- `engine_exact.py`, `quantize.py`, `entropy_*.py`, `download_size.py`.
