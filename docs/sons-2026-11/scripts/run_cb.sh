#!/bin/bash
# Sons do Custo-benefício pela mesma via do produto (CompressAddonSystem + WAVEdit Adaptativo conservador,
# referência 22050 Hz mono PCM16), com o driver tests/audio_adaptive_corpus do repositório.
# MP3 e OGG não entram: o assistente deixa os dois como estão (CompressMP3 = CompressOGG = false).
REPO="C:/Users/luisf/Music/teste/.claude/worktrees/adoring-driscoll-1c99fb"
EXE="$REPO/tests/audio_adaptive_corpus/bin/Release/net6.0-windows/GmodAddonOptimizer.AudioAdaptive.Corpus.exe"
LAB="C:/GModLab/revisao/sons"
for g in ${1:-m9k GLIDE LVS simfphys arc9}; do
  src="C:/GModLab/bench/src/$g/${g}_merged"
  out="$LAB/cb/$g"
  rm -rf "$out"
  start=$(date +%s)
  "$EXE" "$src" "$src" "$out" > "$LAB/out/cb_$g.json" 2> "$LAB/out/cb_$g.err"
  echo "$g rc=$? $(( $(date +%s) - start )) s"
  rep=$(python -c "import json,sys;print(json.load(open(r'$LAB/out/cb_$g.json',encoding='utf-8-sig'))['AdaptiveReportPath'])")
  cp "$rep" "$LAB/out/adaptive_$g.json"
done
echo done
