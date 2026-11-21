"""Terceira rodada no jogo: loops MS-ADPCM gravados PELO PRODUTO depois da correção (lab/cb_fix), contra o original.

Escolhe loops reais curtos da bancada que cobrem os casos do código C#: mono com bloco de 1024, 2048 e 512 bytes,
estéreo, marca em cue e em smpl, início em zero e depois de zero, número ímpar de amostras, e um que voltou para PCM.

python game_make3.py -> game/addon3/sound/sonslab/*, game/plan3.json
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import GROUPS, LAB, parse_wav, src_root

OUT = LAB / "game" / "addon3" / "sound" / "sonslab"
FIX = LAB / "cb_fix"


def loop_start(w):
    if w["smpl_loops"]:
        return w["smpl_loops"][0][0]
    if w["cue"]:
        return w["cue_pos"][0]
    return None


def main():
    shutil.rmtree(LAB / "game" / "addon3", ignore_errors=True)
    OUT.mkdir(parents=True)
    shutil.copy2(LAB / "game" / "addon" / "sound" / "sonslab" / "sync.wav", OUT / "sync.wav")
    rows = []
    for g in GROUPS:
        for p in sorted((FIX / g).rglob("*.wav")):
            w = parse_wav(p)
            if not w or not w["loop"]:
                continue
            rel = p.relative_to(FIX / g)
            o = parse_wav(src_root(g) / rel)
            s = loop_start(o)
            if s is None or w["frames"] / w["rate"] > 1.6 or w["frames"] / w["rate"] < 0.15:
                continue
            true_start = round(s * (w["rate"] / o["rate"])) if o["rate"] != w["rate"] else s
            if (w["frames"] - true_start) / w["rate"] < 0.04:
                continue
            was = parse_wav(LAB / "cb" / g / rel)
            rows.append({"group": g, "rel": rel.as_posix(), "fmt": w["fmt"], "ch": w["channels"], "rate": w["rate"],
                         "align": w["align"], "frames": w["frames"], "start": true_start, "smpl": bool(o["smpl_loops"]),
                         "was": was["fmt"], "bytes": p.stat().st_size})

    def pick(pred, n=1):
        got = [r for r in rows if pred(r) and r not in chosen]
        got.sort(key=lambda r: -r["bytes"])
        return got[:n]

    chosen = []
    adp = lambda r: r["fmt"] == "MS-ADPCM"
    wants = [
        ("mono 1024, inicio 0", lambda r: adp(r) and r["ch"] == 1 and r["align"] == 1024 and r["start"] == 0, 2),
        ("mono 1024, inicio > 0", lambda r: adp(r) and r["ch"] == 1 and r["align"] == 1024 and r["start"] > 0 and not r["smpl"], 2),
        ("mono 2048", lambda r: adp(r) and r["ch"] == 1 and r["align"] == 2048, 2),
        ("mono 512", lambda r: adp(r) and r["ch"] == 1 and r["align"] == 512, 2),
        ("mono smpl, inicio > 0", lambda r: adp(r) and r["ch"] == 1 and r["smpl"] and r["start"] > 0, 1),
        ("mono impar", lambda r: adp(r) and r["ch"] == 1 and r["frames"] % 2 == 1 and r["start"] > 0, 1),
        ("estereo, inicio 0", lambda r: adp(r) and r["ch"] == 2 and r["start"] == 0, 1),
        ("estereo, inicio > 0", lambda r: adp(r) and r["ch"] == 2 and r["start"] > 0, 2),
        ("voltou para PCM", lambda r: r["fmt"] == "PCM16" and r["was"] == "MS-ADPCM", 1),
    ]
    files, slots = [], []
    for label, pred, n in wants:
        for r in pick(pred, n):
            chosen.append(r)
            i = len(chosen)
            key = f"{r['group']}/{r['rel']}"
            base = {"kind": "realloop", "source": key, "case": label, "rate": r["rate"], "ch": r["ch"],
                    "loop_start": r["start"] / r["rate"], "length": r["frames"] / r["rate"], "align": r["align"]}
            shutil.copy2(src_root(r["group"]) / r["rel"], OUT / f"p{i}_orig.wav")
            shutil.copy2(FIX / r["group"] / r["rel"], OUT / f"p{i}_novo.wav")
            files.append({"file": f"sonslab/p{i}_orig.wav", "version": "orig", **base})
            files.append({"file": f"sonslab/p{i}_novo.wav", "version": "novo", **base})
            for v in ("orig", "novo"):
                slots.append({"id": len(slots) + 1, "file": f"sonslab/p{i}_{v}.wav", "api": "patch", "dur": 2.4, "gap": 0.4,
                              "shim": 0, "prefix": "", "group": "realloop"})
    plan = {"addon": str(LAB / "game" / "addon3"), "files": sorted({s["file"] for s in slots} | {"sonslab/sync.wav"}),
            "slots": slots, "tests": files, "endmovie": False}
    (LAB / "game" / "plan3.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print(len(chosen), "loops,", len(slots), "testes, %.0f s de jogo" % sum(s["dur"] + s["gap"] for s in slots))
    for r in chosen:
        print("  %-64s %s ch%d %d Hz bloco %d inicio %.3f s de %.3f s%s%s" % (
            (r["group"] + "/" + r["rel"])[-64:], r["fmt"], r["ch"], r["rate"], r["align"], r["start"] / r["rate"],
            r["frames"] / r["rate"], " smpl" if r["smpl"] else "", " impar" if r["frames"] % 2 else ""))


if __name__ == "__main__":
    main()
