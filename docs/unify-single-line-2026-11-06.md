# Linha única — unificação de `main` e `publish/2026-03` (06/11/2026)

## Contexto

As duas linhas tinham divergido sem querer. A pasta `Music\teste` estava em
`publish/2026-03` desde 20/03. O trabalho de julho/agosto foi feito em worktrees
e juntado na `main` (enviada ao GitHub em 16/08). A partir de 28/08 o trabalho
voltou a ser commitado direto na `publish/2026-03`. Resultado:

- `main` tinha Models Maximum adaptive v2, Magick+ e Companion;
- `publish/2026-03` tinha Universal VTF, distribuição exata, Compressão completa,
  interface Bancada e TRI_STRIP de VTX (estes três últimos ainda sem commit).

Agora as duas apontam para o mesmo commit. A base de trabalho é a
`publish/2026-03`.

## O que foi feito

1. Commits do trabalho pendente: GUI legada (`113371f`), trabalho de setembro
   (`2dccadf`) e protocolo do Companion na `main` (`edc796b`).
2. Merge `00f958c` com estas decisões:
   - **Mídia e recursos → Maximum** continua sendo o Universal VTF. O runner de
     julho (`VtfMaximumLab`) não entra no produto. Só as partes de codec que o
     Magick+ usa (Candidates, Encoding, Inventory, Validation) são compiladas no
     WPF; o projeto do laboratório compila o resto sozinho.
   - **Magick+** passa a aparecer como quarto modo da aba Mídia e recursos.
   - **Modelos** ganha o modo Maximum e o cartão "Runtime evidence"; o Pipeline
     ganha o mesmo cartão.
   - Nova aba **Validação no jogo** (Companion) no visual Bancada.
   - Empacotamento: instalador por hash de conteúdo da `main`. O worker leva
     distribuição exata, `meshopt_stripify` e `maximum_optimizer` juntos.
     Versão dos tools: **0.1.19**.
   - O release volta a gerar um único exe autocontido.
   - `companion/**` fixado em LF. O ZIP do Companion foi regerado a partir do
     código commitado (o anterior tinha sido gerado com fins de linha misturados;
     o conteúdo Lua é o mesmo).
3. Ajuste visual: os botões da aba Validação no jogo crescem conforme o texto.

## Verificação

- Python: **289 aprovados, 1 ignorado**.
- `VtfUniversal.Tests`: **250 aprovados, 1 ignorado**. `VtfMaximumLab.Tests`: **60/60**.
- Contratos WPF do instalador de tools e do Companion: aprovados.
- Release `build_release_wpf.ps1`: exit 0 (`.superpowers/unify-20261106/release-r2.log`).
- Exe publicado iniciado: InputIdle, Responding, 9 abas lidas via UI Automation;
  Modelos mostra Normal/Fidelity/Maximum; Mídia lista Padrao/Magick/Maximum/Magick+;
  capturas em `.superpowers/unify-20261106/`.
- Worker extraído do ZIP publicado: `--help` com `normal,fidelity,maximum`,
  `--vtx-tristrips` e `--maximum-runtime-profile`; `exactpack` na amostra de
  carros: 12.154.162 B → 3.508.199 B, `byte_exact=true`.

## Artefatos

- EXE: `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`
  — 240.566.350 B — SHA-256 `87e11a6f43b839c0333c4ee7a08c8105550dc4c805e452fc3ff62dfcd870dbaf`.
- ZIP de tools: 31.673.549 B — SHA-256 `693d2cd25ec6d3fa6560aaa12a4a57f468e694057ce33a12e7db7e966992b2f8`.
- ZIP do Companion: SHA-256 `8814a96575834f76cb1a684f595716390300e4f5a456339c97f7db5357e50f82`.

## Rollback

- Tags `backup/pre-unify-20261106-publish` e `backup/pre-unify-20261106-main`.
- Exe, ZIP de tools e `worker.spec` anteriores em `.superpowers/unify-20261106/pre-release/`.
- Os tools 0.1.18 continuam extraídos em `%LOCALAPPDATA%\GmodAddonOptimizer\tools\SourceAddonOptimizer\0.1.18`.

## Limites

- Nenhuma compressão completa de addon foi executada com o exe novo, e não houve
  teste dentro do GMod: Models Maximum e Companion precisam de uma rodada real.
- O GitHub (`origin`) não foi atualizado; `origin/main` continua em `fdaeb00`.
- Os ramos `spike/*` e `feature/*` de agosto (áudio ADPCM, rgbcx, cutout de 1 bit,
  normalizador de VTF, VTF tiled P2 etc.) continuam separados e precisam ser
  portados individualmente.
- Avisos preexistentes continuam: .NET 6 fora de suporte, advisories do
  Magick.NET 14.10.4, IL3000.
