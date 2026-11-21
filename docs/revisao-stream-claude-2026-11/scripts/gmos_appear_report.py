"""Summary of gmos_appear.py runs. python gmos_appear_report.py <run> [<run> ...]"""
import json
import sys
from pathlib import Path

LAB = Path(r"C:/GModLab/revisao/claude")
for run in sys.argv[1:]:
    r = json.loads((LAB / "clab" / run / "report.json").read_text(encoding="utf-8"))
    if "error" in r:
        print(run, r["error"])
        continue
    ms = r["models"] if isinstance(r["models"], list) else [r["models"][k] for k in sorted(r["models"], key=int)]
    ok = [m for m in ms if m.get("ready")]
    tot = sum(m["seconds"] for m in ok)
    idle = sum(m["idle_ft"] for m in ms) / len(ms)
    print(f"{run}: runtime {r.get('runtime')}, fps_max {r.get('fps_max'):.0f}, idle {1 / idle:.0f} fps; "
          f"{len(ok)}/{len(ms)} ready, total {tot:.2f} s, frames {sum(m['frames'] for m in ok)}, "
          f"worst frame {1000 * max(m['ft_max'] for m in ok):.0f} ms, frames over 1/60: {sum(m['over60'] for m in ok)}, "
          f"decode {sum(m['decode_s'] for m in ok):.2f} s, max decode in a frame {1000 * max(m['decode_max'] for m in ok):.1f} ms, "
          f"resumes per decode frame {sum(m['resumes'] for m in ok) / max(1, sum(m['decode_frames'] for m in ok)):.2f}")
    for m in ms:
        if m.get("ready"):
            print(f"   {m['path'][-42:]:42} {m['seconds']:6.2f} s {m['frames']:5d} frames  worst {1000 * m['ft_max']:5.0f} ms  "
                  f"mean {1000 * m['ft_mean']:5.1f} ms  over60 {m['over60']:4d}  decode {m['decode_s']:.2f} s")
