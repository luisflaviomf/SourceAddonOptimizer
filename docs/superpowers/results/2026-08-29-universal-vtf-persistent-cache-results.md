# Universal VTF persistent cache — resultado

Status: **OFFLINE_CLEAN_MANUAL_GMOD_PENDING**

Data: 2026-08-29

## Resultado

O cache persistente por conteúdo foi implementado e validado sobre os 1.152 VTFs do corpus congelado. A execução pesada anterior não foi repetida: os resultados legados foram importados sob hashes explícitos, os valores legados `"NaN"` foram convertidos para `null` canônico apenas nos recibos novos e duas execuções em processos independentes obtiveram 100% de cache hit e zero recálculo de imagem.

- VTFs reaproveitados por execução quente: **1.152**
- VTFs recalculados: **0**
- Cache hit rate: **100%**
- Blobs finais únicos: **1.113**
- Objetos VTF no cache: **200.632.728 bytes**
- Cache completo: **224.951.202 bytes em 3.419 arquivos**
- Recibos de análise: **1.152**
- Recibos completos: **1.152**
- Quarentena: **0 arquivos**
- Staging residual: **0 entradas**

Nenhuma baseline foi substituída ou promovida.

## Autoridades preservadas

- Raw: `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack`
  - 3.827 arquivos
  - 1.441.918.651 bytes
  - tree SHA-256 `5f7b31c77e34608c4a40c6b945a7ad24b151be4ad067761c24aa1f93ed908758`
- Baseline segura: `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\source\addons\lvs_cars_pack_CORPUS_BC3_TO_BC1_SAFE_FINAL_V1`
  - 3.664 arquivos
  - 438.230.044 bytes
  - tree SHA-256 `8e60b8b46744a192fd5ab1ab77848ab9fd4123f1dd042c31d365e16505a698af`
- Produção legada preservada: `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\UNIVERSAL_VTF_V1_OFFLINE_CANDIDATES_DIRTY_NAN_MANIFEST`
- Recovery pré-cache preservado: `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\UNIVERSAL_VTF_V1_OFFLINE_CANDIDATES_ABORTED_PRE_CACHE_RESUME`
- Cache novo: `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\PERSISTENT_CACHE_SHA256_V1`

Hashes congelados usados no import:

- Policy: `de1fd1acd3434d34d6365aec224d56e85a4a319ff935ab708ffa93f87be14266`
- Fixture: `9adb529e565f9e1ba54f0833af21c59db8674b5bed286d9cac37c6f8ace13d7b`
- Toolchain: `415bd8ae0217aea0f85037eb5004fd3b03246229cfbff43ed28d12e086f0a4c6`
- Bridge: `9bf65c2b404365c23836b6eb2ff206a0fed6d8c217a71792406b775c8fb6dad1`
- Algoritmo/preparação do cache: `b7c2a1b5e89959dffb75645d7bec43427358ed8c7694d880442b3deee72d7ef4`
- Build log legado: `b8d26aa00828d948d5df25c46db22090e48d7fe69c78b40419407509f12c67b2`

## Import e resume

O importador valida, antes da publicação, hashes explícitos, raw/safe/bridge, as três árvores finais, layouts VTF, métricas compartilhadas, prova semântica e replay do seletor atual. Ele aceita named floats somente no leitor legado tipado; o loader normal continua rejeitando `"NaN"`.

Dois findings reais foram detectados e corrigidos sem recalcular imagens:

1. O build log legado estava em UTF-16LE com BOM, enquanto os manifestos estavam em UTF-8. Foi adicionada decodificação estrita por BOM, coberta por teste.
2. A validação da identidade rejeitava `scalarMask`, embora esse seja o lower-camel canônico do enum `ScalarMask`. A regra agora aceita somente os nomes canônicos derivados do enum e continua rejeitando valores inventados.

O segundo seed publicou 284 checkpoints antes do finding `scalarMask`. Eles foram preservados. O seed seguinte retomou no mesmo cache e terminou:

- Total: 1.152
- Importados/verificados: 1.152
- Recalculados: 0
- Blobs únicos: 1.113
- Tempo interno do import: 76,5589642 s
- Tempo wall do seed final/resume: 76,7423263 s
- Log CLEAN: `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\PERSISTENT_CACHE_SHA256_V1.seed.retry2.jsonl`
- SHA-256 do log: `51ed1a6f1a34ce21cabe427110abe031bb8a6f74b568554d1a3cad0fe79f13a6`

Os logs das duas recusas fail-closed também foram preservados:

- `PERSISTENT_CACHE_SHA256_V1.seed.jsonl` — `23a1603ec7e808a04afbf38c597f9fac6e4712e1ec7baeca671820fa3112b1fa`
- `PERSISTENT_CACHE_SHA256_V1.seed.retry1.jsonl` — `85601f12d35f20e08dfe53e0026b331fe367f22e7b31963e77dd63eeb00186e0`

## Benchmark

| Medida | Execução antiga | Warm 1 | Warm 2 |
|---|---:|---:|---:|
| Preparação/decisão VTF | 27.055,6239045 s | 2,2572807 s | 2,2599882 s |
| Build total interno | 27.055,6239045 s | 180,8094007 s | 221,6750856 s |
| Wall total observado | 27.055,6239045 s | 181,502986 s | 223,971911 s |
| Completed hits | — | 1.152 | 1.152 |
| Analysis hits | — | 0 | 0 |
| Misses | — | 0 | 0 |
| Recalculados | 1.152 | 0 | 0 |

Speedup conservador, usando o pior wall time quente: **120,799183×**.

Speedup da etapa pesada de análise/decisão, usando o pior preparation time quente: **11.971,577508×**.

O restante dos aproximadamente 3–4 minutos quentes é composição física de três árvores que somam cerca de 1,4 GB e auditoria integral por hash; não é PSNR, edge retention, alpha audit, análise de mipmaps ou decisão de resolução.

## Candidatos determinísticos

Os dois processos produziram os mesmos bytes de manifesto e os mesmos tree hashes. Nenhum dos seis manifestos contém o literal `"NaN"`.

| Perfil | Bytes | Tree SHA-256 | Manifest SHA-256 canônico |
|---|---:|---|---|
| Conservative | 471.027.900 | `ca20f2d97e397a2f421bcc81c812a491779c8e2c0014d34cd73c0107f4afac15` | `faf649a254a6ecc301923be44f2791bd9e656d71249f206c3d4843ee4dbfb396` |
| Balanced | 467.567.820 | `409ea63136540d6dcd6a6f69e03aa516ed55653bef5f22af53df83ab65da850e` | `e02eb9edfe04136dfb10f67ac5cd1d7b5ba481662fb4c995c86e460845f75aab` |
| Aggressive | 457.213.132 | `5751602cc4dd3a1ca0c3e21d4e4d92500ebe60fca77e28919de6bf0a19797573` | `bf1f1af7d8ec72e44bffccb46ddf35fd56459d413d607297dde3c52d3e556ce7` |

Run 1:

- `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\UNIVERSAL_VTF_V1_CACHE_WARM_RUN_1\universal-vtf-conservative`
- `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\UNIVERSAL_VTF_V1_CACHE_WARM_RUN_1\universal-vtf-balanced`
- `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\UNIVERSAL_VTF_V1_CACHE_WARM_RUN_1\universal-vtf-aggressive`
- Build log SHA-256: `b2606907f53b34ea4b46781ab8e128f3370165efab9c98607615f04adfc1739e`

Run 2:

- `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\UNIVERSAL_VTF_V1_CACHE_WARM_RUN_2\universal-vtf-conservative`
- `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\UNIVERSAL_VTF_V1_CACHE_WARM_RUN_2\universal-vtf-balanced`
- `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\_runtime_validation\universal_vtf_v1\UNIVERSAL_VTF_V1_CACHE_WARM_RUN_2\universal-vtf-aggressive`
- Build log SHA-256: `d00d32d11d387735bc035565def476ff677bf4ccf4cf891d938d2417caf09fed`

Run journals:

- Warm 1: `run-fca32f28bc6e44ccb4e8220cb7518e1b.jsonl` — `e9969b781bfe281feff1472f3f410410ceba392393f8a5569e86713062c41f3b`
- Warm 2: `run-e175718fe982468181942abed0484ae3.jsonl` — `06997c8f20ec186e48e92afb514a22ae1469aa7f4407014466d91e2e6b36e4bc`

## Auditorias

- C# unit/integration: **181/181**
- Python unit audit: **8/8**
- Build win-x64: **0 erros**; somente os avisos já conhecidos de EOL do .NET 6
- Auditor corpus C#: **6/6 CLEAN**
- Auditor independente Python: **6/6 CLEAN**
- Cada auditoria independente confirmou 1.152 linhas e 193 recibos BC por perfil.
- `git diff --check`: CLEAN

Os 24 logs de auditoria e stderr correspondentes estão ao lado dos candidatos sob `universal_vtf_v1`, com nomes `UNIVERSAL_VTF_V1_CACHE_WARM_RUN_<n>.audit.<perfil>.*` e `UNIVERSAL_VTF_V1_CACHE_WARM_RUN_<n>.python-audit.<perfil>.*`. Todos os stderr finais têm zero bytes.

## Handoff manual

Os candidatos não foram montados nem promovidos automaticamente. Para validação manual no GMod, usar preferencialmente o Run 2 e conferir, em cada perfil:

- vistas próxima, média e distante;
- skins e interiores;
- vidro e transparência;
- luzes, emissivos e gauges;
- rodas e materiais normais;
- transições de LOD.

Até confirmação manual, a baseline oficial continua sendo `CORPUS_BC3_TO_BC1_SAFE_FINAL_V1` de 438.230.044 bytes.
