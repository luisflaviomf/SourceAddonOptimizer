"""Confere a cópia em Python das métricas do produto contra o que o próprio produto registrou (relatórios do
Adaptativo conservador em out/adaptive_<grupo>.json) e a regra recalculada contra as escolhas reais."""
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import GROUPS, LAB

inv = json.loads((LAB / "out" / "inventory.json").read_text(encoding="utf-8"))
r1 = json.loads((LAB / "out" / "proto.json").read_text(encoding="utf-8"))
p3 = LAB / "out" / "proto3.json"
if p3.exists():
    for k, d in json.loads(p3.read_text(encoding="utf-8")).items():
        for n, v in d.items():
            if n in r1.get(k, {}).get("cands", {}):
                r1[k]["cands"][n]["lsd"] = v
rep = {}
for g in GROUPS:
    d = json.loads((LAB / "out" / f"adaptive_{g}.json").read_text(encoding="utf-8-sig"))
    for f in d["Files"]:
        rep[g + "/" + f["RelativePath"].replace("\\", "/")] = f["Result"]
bad, n, dif = [], 0, []
for k, r in inv.items():
    if r["cb"]["ext"] != ".wav" or r["cb"]["fmt"] != "MS-ADPCM" or "cands" not in r1.get(k, {}):
        continue
    c = r1[k]["cands"]
    if "cb" not in c or "base" not in c:
        continue
    cb, b = c["cb"], c["base"]
    n += 1
    R = rep[k]
    sc = [x for x in R["Candidates"] if x["Spec"] == R["SelectedSpec"] and x["Score"]]
    if sc:
        s = sc[0]["Score"]
        dif.append((cb["sdr"] - s["SdrDecibels"], cb["seg"] - s["SegmentalSnrDecibels"], cb["lsd"] - s["LogSpectralDistance"]))
    if not (cb["sdr"] >= b["sdr"] and cb["seg"] >= b["seg"] and cb["lsd"] <= b["lsd"]):
        bad.append((k, round(cb["sdr"] - b["sdr"], 2), round(cb["seg"] - b["seg"], 2), round(b["lsd"] - cb["lsd"], 3)))
dif = np.array(dif)
print("arquivos ADPCM de hoje:", n)
print("minha medida menos a do produto (SDR, SNR seg, LSD): mediana", np.median(dif, axis=0).round(4),
      "| maior diferenca", np.abs(dif).max(axis=0).round(3), "| p99", np.percentile(np.abs(dif), 99, axis=0).round(3))
print("nao passam na regra recalculada:", len(bad),
      Counter(("sdr " if x[1] < 0 else "") + ("seg " if x[2] < 0 else "") + ("lsd" if x[3] < 0 else "") for x in bad))
for x in bad[:10]:
    print("  ", x)
