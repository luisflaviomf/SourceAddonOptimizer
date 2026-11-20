# Revisão GMOT/GMOS (Claude, 2026-11-20): scripts do laboratório

Cópia dos scripts e do protótipo usados em `docs/revisao-stream-claude-2026-11.md`. Fora do produto: nada
aqui é importado pelo worker ou pelo WPF. Os scripts esperam o laboratório em `C:\GModLab\revisao\claude\`
(fontes em `src\`, saídas em `cb\`, `max\`, `dump\`, `dumpimg\`, resultados em `out\`) e importam o código
do produto deste repositório (`texture_stream`, `model_stream`, `texlab`).

- `baseline.py`, `dump_inputs.py`: linha de base pelo worker e coleta das entradas do codificador.
- `inventory.py`, `gmot_planes.py`: inventário dos bytes.
- `gmotlib.py`, `cm_estimate.py`, `cm_real.py`, `cm_prior.py`, `lzma_variants.py`: camada de bits do GMOT.
- `cm_decoder.lua` (referência), `cm_decoder3.lua` (versão medida), `gen_cm_decoder.py`/`cm_decoder2.lua`
  (versão intermediária), `cm_lua_bench.py`: decodificador Lua e medição no LuaJIT (lupa).
- `rust_gmotcm/`: codificador/decodificador com contexto (cdylib; `cargo build --release`).
- `texeval.py`, `texexp.py`, `dxt_gain.py`, `alpha_study.py`, `refcodec.py`, `sidebyside.py`: qualidade,
  variantes com perda, DXT, alfa, régua AVIF/JPEG 2000, imagens.
- `gmoslib.py`, `gmos_exp.py`, `gmos_refs.py`, `gmos_entropy.py`, `gmos_lua_time.py`, `coarse_test.py`: GMOS.
