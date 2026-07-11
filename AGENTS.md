# AGENTS.md

## Visao geral do repositorio

Este repositorio contem dois frontends e um backend Python compartilhado:

- **Produto principal atual**: aplicativo WPF em `GmodAddonCompressor-master/GmodAddonCompressor/`
- **Frontend legado**: GUI Python em `gui/`
- **Backend/worker Python**: scripts na raiz + entrypoint em `worker/worker_main.py`

Se houver qualquer duvida sobre onde implementar novas features, a resposta padrao e:

1. **UI nova vai no WPF**
2. **backend pesado continua no worker Python**
3. **nao implementar novas abas no frontend Python legado, salvo instrucao explicita do usuario**

## Qual e o produto principal

O aplicativo principal e real usado no dia a dia e o **WPF** em:

- `GmodAddonCompressor-master/GmodAddonCompressor/`

Este e o app que deve receber:

- novas abas
- novos controles de UI
- novas integracoes de fluxo
- novos resumos/status/progresso
- ajustes de UX
- integracao com worker/tools

O executavel final distribuivel do produto principal sai em:

- `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe`

## O que e legado e o que e atual

### Atual

- `GmodAddonCompressor-master/GmodAddonCompressor/`
- `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml`
- `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml.cs`
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/*`
- `worker/worker_main.py`
- scripts Python de backend na raiz (`build_optimized_addon.py`, `batch_*`, `render_previews.py`)

### Legado

- `gui/`
- `pyinstaller/gui.spec`
- `dist/GModAddonOptimizer/`
- `build_release.cmd` na raiz, que hoje chama o build do frontend Python antigo
- `GmodAddonCompressor-master/build.bat`, que faz apenas publish do WPF e **nao** empacota os tools corretamente para release completo

Regra permanente:

- **Nao usar `gui/` para novas features**, a menos que o usuario peca explicitamente para mexer no frontend Python antigo.

## Estrutura dos diretorios importantes

- `GmodAddonCompressor-master/GmodAddonCompressor/`: app WPF principal
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/`: integracao WPF -> worker para models/pipeline
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Unpack/`: integracao WPF -> worker para descompactacao de addons
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Tools/`: extracao/versionamento dos tools empacotados
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Reporting/`: size report
- `worker/worker_main.py`: entrypoint do worker Python
- `pyinstaller/`: empacotamento PyInstaller do worker e do frontend Python legado
- `dist/GModAddonOptimizerWorker/`: saida do worker empacotado
- `batch_unpack_addons.py`: backend de descompactacao de `.gma` e `.bin`
- `build_optimized_addon.py`: backend principal de models/pipeline
- `build_release_wpf.ps1`: script correto de release do app principal

## Como funcionam WPF + worker Python

Arquitetura real:

1. O WPF e a interface principal.
2. O WPF nao implementa a logica pesada de models/unpack diretamente.
3. O WPF chama o worker Python empacotado (`SourceAddonOptimizerWorker.exe`).
4. O worker roteia comandos:
   - `unpack` -> `batch_unpack_addons.main(...)`
   - fluxo padrao -> `build_optimized_addon.main(...)`
5. O WPF consome stdout/progresso/logs e atualiza a UI.

Pontos importantes:

- A aba **Descompactar addons** ja existe no WPF e usa o worker `unpack`.
- A aba **Models** usa o worker empacotado para o pipeline de otimizar/recompilar modelos.
- A aba **Pipeline** encadeia Models -> Compress no frontend WPF.

## Pipeline de build/release correto

### Release padrao do app principal

Use **este** script na raiz:

```powershell
.\build_release_wpf.ps1
```

Esse e o fluxo correto porque ele:

1. faz clean do build `win-x64`
2. chama `pyinstaller/package_wpf_tools.ps1`
3. gera/valida o ZIP embutido `SourceAddonOptimizer.win-x64.zip`
4. roda `dotnet publish` do WPF
5. verifica que o `.exe` final existe

### Quando o worker Python foi alterado

Se voce alterou codigo em:

- `worker/`
- `build_optimized_addon.py`
- `batch_*`
- outros scripts Python usados pelo worker

garanta que o worker empacotado foi atualizado antes do release final. Hoje o `package_wpf_tools.ps1` reconstrui o worker apenas se o executavel empacotado estiver ausente. Se houver suspeita de `dist/GModAddonOptimizerWorker/` stale, refaca o PyInstaller antes do release.

### Nao usar como release principal

Nao trate estes scripts como release oficial do produto principal:

- `build_release.cmd`
- `pyinstaller/build.ps1`
- `GmodAddonCompressor-master/build.bat`

Eles servem para builds parciais/legados e podem induzir erro de fluxo.

## Scripts importantes

- `build_release_wpf.ps1`: script principal e correto para gerar o `.exe` final do WPF
- `pyinstaller/package_wpf_tools.ps1`: empacota worker + Crowbar em `Resources/SourceAddonOptimizer.win-x64.zip`
- `pyinstaller/build.ps1`: build PyInstaller do worker e do frontend Python legado
- `build_release.cmd`: wrapper legado para build do frontend Python antigo
- `GmodAddonCompressor-master/build.bat`: publish simples do WPF, sem garantir o empacotamento correto dos tools

## Regras para implementar novas features

- Novas features de UI vao no WPF.
- Novas abas vao no WPF.
- Ajustes de UX, feedback, log, progresso e size report vao no WPF.
- Nova logica pesada, batch ou processamento em lote pode ir no backend Python e ser exposta ao WPF via runner.
- Se criar uma nova capacidade no worker, crie tambem a integracao correspondente no WPF em `Systems/...`.
- Nao reescrever o app sem necessidade. Preferir mudancas pequenas e seguras.

## Regras para evitar regressao

- Nao mexer no frontend Python antigo para features novas, salvo pedido explicito.
- Nao quebrar as abas existentes do WPF: `Compress`, `Models`, `Descompactar addons`, `Pipeline`.
- Preservar integracao com settings, logs e size reports existentes.
- Preservar extracao de tools em `%LOCALAPPDATA%`; nao voltar a depender da pasta do exe para runtime.
- Sempre preferir integracao incremental no WPF em vez de reestruturar arquitetura.

## Como validar manualmente mudancas

### Validacao minima para features WPF

1. Gerar release com `.\build_release_wpf.ps1`
2. Abrir o exe publicado:

```text
GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe
```

3. Confirmar que a aba alterada abre no exe publicado, nao apenas em build local
4. Confirmar que logs/progresso/status atualizam pela UI
5. Confirmar que as outras abas principais ainda abrem normalmente

### Pasta de teste util para unpack

Usar:

```text
C:\Users\luisf\Music\teste\addonsparateste
```

Estado conhecido:

- contem 3 arquivos `.gma`
- contem 3 arquivos `.bin`

### Validacao da aba Descompactar addons

1. Abrir a aba `Descompactar addons` no WPF publicado
2. Usar a pasta `addonsparateste`
3. Apontar `gmad.exe` do Garry's Mod
4. Rodar `Scan`
5. Rodar `Extract`
6. Confirmar que o lote continua mesmo se algum item falhar

## Armadilhas e conhecimentos importantes ja descobertos

- A UI principal **nao** e `gui/`; ela e o WPF em `GmodAddonCompressor-master/GmodAddonCompressor/`.
- Ja houve confusao em sessao anterior por implementar/explicar coisas no frontend errado. Evitar isso.
- O app final publicado depende da extracao correta dos tools em `%LOCALAPPDATA%\GmodAddonOptimizer\tools\...`.
- O work/log runtime do WPF fica em `%LOCALAPPDATA%\GmodAddonOptimizer\`.
- A aba `Descompactar addons` ja existe no WPF e usa o worker `unpack`.
- `package_wpf_tools.ps1` foi endurecido para evitar ZIP stale/quebrado:
  - cria ZIP temporario
  - valida entradas obrigatorias
  - falha explicitamente em caso de lock/erro
  - nao deve imprimir `OK` com artefato inconsistente
- Ja foi observado problema de recurso embutido stale no build `win-x64`; por isso o release correto faz clean especifico antes do publish.
- Mudancas devem ser minimas e seguras. Nao reescrever o app inteiro para adicionar feature pequena.

## Convencoes para futuras alteracoes

- Preferir nomes/coisas novas no contexto de `GmodAddonOptimizer`, mesmo que o projeto ainda tenha nomes historicos `GmodAddonCompressor` em caminhos/namespaces.
- Documentar novas etapas de build/release no proprio repo se o fluxo mudar.
- Se adicionar nova feature no worker, registrar:
  - comando/entrypoint
  - runner WPF correspondente
  - como validar manualmente
- Se alterar release, atualizar primeiro `build_release_wpf.ps1` e depois este arquivo, para manter a documentacao fiel ao codigo.

## Resumo rapido para agentes futuros

Se voce acabou de entrar neste repositorio:

1. Trabalhe no **WPF** em `GmodAddonCompressor-master/GmodAddonCompressor/`
2. Trate `gui/` como **legado**
3. Use `worker/worker_main.py` e os scripts Python da raiz como backend
4. Gere release com `.\build_release_wpf.ps1`
5. Valide no exe publicado, nao so por build/backend
