"""Summary of an in-game GMOS bench report. python gmos_ingame_report.py <run>"""
import json
import sys
from pathlib import Path

r = json.loads((Path(r"C:/GModLab/revisao/claude/clab") / sys.argv[1] / "report.json").read_text(encoding="utf-8"))
if "error" in r:
    print(r["error"])
    sys.exit(1)
print("jit", r.get("jit"), r.get("status", [])[:1], "branch", r.get("branch"))
print("engine mesh calls, ns per vertex:", {k: round(v, 1) for k, v in r.get("micro", {}).items()})
for v, res in r["results"].items():
    items = res if isinstance(res, list) else [res[k] for k in sorted(res, key=int)]
    tot = sum(x["total"] for x in items)
    prof = {}
    for x in items:
        for k, t in x["prof"].items():
            prof[k] = prof.get(k, 0) + t
    print(f"{v}: total {tot:.3f} s  " + "  ".join(f"{k} {t:.3f}" for k, t in sorted(prof.items(), key=lambda kv: -kv[1])))
