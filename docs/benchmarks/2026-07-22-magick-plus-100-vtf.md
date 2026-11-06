# Magick+ — benchmark de 100 VTFs

## Veredito

O modo Magick+ mantém o pipeline e as configurações do Magick e acrescenta somente
um repack exato BC3/DXT5 -> BC1/DXT1 quando a análise do addon prova que o canal
alpha não é consumido. O bloco de cor BC3 já tem o layout BC1; portanto o processo
remove os 64 bits de alpha por bloco sem reencodar RGB.

Resultado aprovado para produção: 25,61% menor que Magick, com 1,11% de tempo
adicional no comparativo lado a lado e nenhuma diferença RGB decodificada.

## Amostra e configuração

- Addon: `lvs_cars_pack` (Workshop `3027256228`)
- Seleção determinística: 100 VTFs
- Seed: `maximum-forced-2x-v1`
- SHA-256 da seleção: `37402a28538e174a72de2961711121f29065f7764d672fbfb43f42f092684044`
- Configuração: redução 2x, limites 8x8, aspect ratio preservado
- Bytes VTF originais: 73.895.504
- DX80: não aplicável à amostra de VTFs; nenhum artefato DX80 foi incluído

## Resultados finais lado a lado

| Métrica | Magick | Magick+ | Diferença |
|---|---:|---:|---:|
| VTF final | 24.569.888 B | 18.278.392 B | -6.291.496 B (-25,61%) |
| Redução contra original | 66,75% | 75,26% | +8,51 p.p. |
| Tempo total | 107,183 s | 108,373 s | +1,190 s (+1,11%) |
| VTFs válidos | 100/100 | 100/100 | igual |
| Iguais byte a byte ao Magick | 100 | 97 | 3 repacks exatos |

Arquivos alterados pelo post-pass:

| VTF | Magick | Magick+ |
|---|---:|---:|
| `mazda_miata/int.vtf` | 1.398.336 B | 699.272 B |
| `toyota_supra/skin.vtf` | 5.592.640 B | 2.796.424 B |
| `toyota_supra/skin2.vtf` | 5.592.640 B | 2.796.424 B |

Para os três: RGB SSIM = 1, RGB PSNR = 100, FLIP médio/P95 = 0. O runtime
também compara os canais RGB decodificados de todos os mipmaps e rejeita o
candidato diante de qualquer diferença. Dimensões, versão VTF, mipmaps,
thumbnail, resources e flags não-alpha são validados antes da substituição.

Relatórios brutos:

- `D:\gaco_magick_control_current_100_20260722.full-compress-report.json`
- `D:\gaco_magick_plus_final_100_20260722.full-compress-report.json`
- `D:\gaco_adaptive_repack_100_20260722\adaptive-size-results.json`

## Abordagens descartadas

- Prefiltro HQ/perceptual: melhora pequena em parte das texturas, mas não reduz o
  tamanho de arquivos BC1/BC3 com a mesma resolução e formato.
- Redução 4x adaptativa: economizou mais, porém deixou de satisfazer a exigência
  de qualidade no mínimo igual ao Magick para toda a amostra.
- Candidatos anisotrópicos: acrescentaram apenas cerca de 1,87% de redução contra
  Magick além do experimento adaptativo, com custo de processamento desproporcional.
- BC7, Basis Universal e AVIF: não são formatos VTF Source 1/Garry's Mod drop-in.

## Base técnica

- Microsoft BC documentation: https://learn.microsoft.com/windows/win32/direct3d10/d3d10-graphics-programming-guide-resources-block-compression
- DirectXTex `texconv`: https://github.com/microsoft/DirectXTex/wiki/texconv
- Valve VTEX (Source 1): https://developer.valvesoftware.com/wiki/Vtex_%28Source_1%29
- Basis Universal: https://github.com/BinomialLLC/basis_universal
- Compressonator: https://github.com/GPUOpen-Tools/compressonator
- Perceptual downscaling: https://cgl.ethz.ch/publications/papers/paperOzt15b.php
- Content-adaptive image downscaling: https://johanneskopf.de/publications/downscaling/

