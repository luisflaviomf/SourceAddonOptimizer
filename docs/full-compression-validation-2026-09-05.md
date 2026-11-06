# Compressão completa — verificação de entrega

## Release atual

`GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`

- Tamanho: **220.306.510 B**.
- SHA-256: `d1703674fec632f577990342a0ac4b18bf0fc32489af6bf1d073fd38849495a8`.
- Build oficial: `.superpowers/full-compression-tab-20260905/release-final.log`, exit 0.
- ZIP do worker permanece idêntico: `03e2e8a59c2bd53aa79fe4fba37bcd6d5ba6b74f3c2d4f1c407c472593933735`.
- Os 17 pins de vendor/codec foram autenticados sem alteração. Não houve novo codec,
  runtime, transformação de baseline ou ativação de addon.

O executável publicado foi iniciado em janela oculta para diagnóstico: processo
154412, `WaitForInputIdle=true`, `HasExited=false`, `Responding=true`. Apenas esse
processo de teste, sem tarefa de compressão iniciada, foi encerrado após a checagem.
Isso prova inicialização, **não inspeção visual interativa**.

## Testes

`dotnet test experiments/VtfUniversal.Tests/VtfUniversal.Tests.csproj --no-build --no-restore`

Autoridade: `.superpowers/full-compression-tab-20260905/tests-authoritative.log`.
**246 PASS, 0 FAIL, 1 SKIP**, exit 0. O SKIP é o teste opt-in de addon real,
executado explicitamente duas vezes abaixo, não uma falha omitida.

Inclui carregamento do MainWindow/XAML compilado em STA, recursos e controles
novos/anteriores, seleção inicial da nova aba, sequência de etapas, preservação
do input, rejeição de drift/saída existente, cancelamento, falhas sem conclusão,
independência do perfil e restauração dos contextos. WAV inclui falha/timeout do
FFmpeg com saída parcial, perda/alteração de cue e conversão real PCM16/44100 mono
para 22050 com cue preservado e escalado.

A revisão independente encontrou um problema concreto preexistente no caminho
WAV do FFmpeg. Corrigido com o gate de exit code/timeout já usado por outros
formatos, mais validação do contrato WAV na nova aba. Rereview: sem findings
Critical/Important pendentes. Nenhuma mudança de arquitetura.

## Execuções reais do encadeamento

Usam `FullCompressionRunner` real, Blender/studiomdl reais, serviços de mídia do
produto e o worker extraído do ZIP publicado idêntico ao atual. Não são mocks.

| Amostra | Entrada | Intermediário nativo | Final | Economia total | Tempo |
|---|---:|---:|---:|---:|---:|
| Carros | 12.154.162 B | 5.162.356 B | 2.520.394 B | 9.633.768 B | 38,0 s |
| Armas | 106.635.770 B | 33.308.299 B | 14.925.166 B | 91.710.604 B | 153,8 s |

Entradas em `C:/GModLab/research-cache/product-exact-integration-20260905/inputs/`.
Cada execução autentica sua entrada novamente antes/depois e guarda inventários
do original e do candidato, report exato e hashes no relatório completo.

Autoridades:

- Carros: `C:/GModLab/research-cache/full-compression-tab-20260905/75766e0bace14b4ca7baf91a7992beb2/work/full-compression-report.json`.
- Armas: `C:/GModLab/research-cache/full-compression-tab-20260905/a0c8661b157a46bb9742fd2468a4b287/work/full-compression-report.json`.
- Logs: `.superpowers/full-compression-tab-20260905/smoke-cars.log` e `smoke-weapons.log`.

Estas são amostras delimitadas, não o corpus inteiro nem evidência visual de
qualidade. Models/texturas/áudio fazem redução nativa; somente o transporte exato
reconstrói seu intermediário byte a byte. **Não** equivalem à promoção de todos
os adaptadores estruturais de laboratório ou à reprodução do TacRP aprovado.

## Limites e preservação

- Não houve nova execução de GMod/corrupt/cold/warm dos candidatos destas amostras.
  Permanecem `OFFLINE_PENDING_GMOD`; a entrega é do fluxo WPF integrado.
- Não foi possível inspecionar/cllicar a interface publicada pela automação
  Windows: `failed to write kernel assets ... (os error 3)`, antes de inicializar
  `@oai/sky`. Não foi usado um bypass de automação. O teste WPF em processo e o
  diagnóstico de inicialização do EXE não substituem uma inspeção visual humana.
- Warnings existentes de net6 fora de suporte, advisories de Magick.NET e IL3000
  permanecem registrados nos logs; não foram confundidos com output limpo.
- Pointer oficial preservado: SHA-256
  `b492e061abca68aca76d902a6751d9f04a9417190576797437aacc7f9dc7b626`.
- Nenhum write foi direcionado a baselines, originais de Workshop, P0/P1,
  candidatos manuais instalados ou rollbacks aprovados.
- Executável/PDB anteriores preservados, cópias hash-verificadas, em
  `.superpowers/full-compression-tab-20260905/pre-release-publish/`.
- Preimages dos arquivos mistos alterados estão em
  `.superpowers/full-compression-tab-20260905/preimages/`; não foram sobrescritos
  os backups da fase anterior nem restauradas mudanças do usuário.
- A primeira checagem WPF concorrente foi impedida pelo lock de um teste real
  ainda em execução; aguardou-se seu fim. Uma asserção apontava para um botão
  antigo sem nome e foi corrigida. A seleção inicial foi explicitada no XAML
  após teste falhar. Logs anteriores foram preservados; a autoridade é o último
  log de testes e `release-final.log`.

Instruções do usuário: `docs/full-compression.md`.
