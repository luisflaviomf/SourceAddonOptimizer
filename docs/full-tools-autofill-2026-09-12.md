# Preenchimento automático de jogo e ferramentas

A Compressão completa agora preenche os caminhos ao abrir, após carregar as configurações. Antes, apenas lia FullCompressionEngine/Blender/StudioMdl e dependia de clique manual para detectar; não reaproveitava os caminhos das outras abas.

Prioridade: preservar caminhos próprios existentes; reutilizar configurações válidas de Exata/Modelos e localização do gmad; recorrer aos detectores já existentes. O compilador da engine selecionada tem prioridade quando o campo de compilador está ausente. O botão Detectar usa a mesma regra e não substitui caminhos existentes. Não há instalação, download, mudança de codec ou execução automática de compressão. A existência no disco não substitui o preflight de processamento. Instalações não encontradas continuam exigindo seleção manual.

Evidência: `.superpowers/full-autofill-20260912/`.

- `red.log`: falha reproduzida na inicialização — engine não preenchida.
- `green.log`: MainWindow real abriu com Garry's Mod, Blender 5.0 e studiomdl existentes; repetir detecção preservou caminhos.
- `smoke.log`: navegação, estados, inventário e preflight passaram.
- `tests.log`: 250 aprovados, 1 ignorado, 0 falhas.
- Revisão independente sem bloqueadores.
- `release.log`: release oficial concluído; EXE publicado iniciou InputIdle=true, sem saída prematura (PID diagnóstico 4140, encerrado após teste). Sem automação de cliques no EXE, pois native pipe Windows permanece indisponível.
- Preimages e EXE anterior preservados em `preimages/`.

EXE SHA-256: `E55B50A1F542855310E0BAEFFAD3046D69886FC3A86F1DC0A8AD011392B04567`.

ZIP de ferramentas/codecs inalterado: `03E2E8A59C2BD53AA79FE4FBA37BCD6D5BA6B74F3C2D4F1C407C472593933735`.
