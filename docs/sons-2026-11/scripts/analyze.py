"""Tamanho de cada opção na bancada inteira, contra o Custo-benefício de hoje, com a regra de qualidade aplicada
arquivo por arquivo (a menor opção que passa; se nenhuma passa, fica o arquivo de hoje).

Regras (sempre contra o ORIGINAL; "hoje" = arquivo do Custo-benefício):
  G  a do produto: SDR, SNR segmentar e LSD não piores que a referência manual (22050 Hz mono PCM16)
  E  estrita: as três métricas do produto E a perceptual (NMR médio e do percentil 95) não piores que o arquivo de hoje
  P  perceptual: NMR médio e percentil 95 não piores que o arquivo de hoje
  T  perceptual com piso: como P, mas o que fica abaixo de -12 dB (média) e -9 dB (p95) conta como transparente

python analyze.py -> out/analysis.json (escolhas por arquivo) + tabelas
"""
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import LAB, MiB

FLOOR, FLOOR95 = -12.0, -9.0


def load():
    inv = json.loads((LAB / "out" / "inventory.json").read_text(encoding="utf-8"))
    refs = json.loads((LAB / "out" / "refs.json").read_text(encoding="utf-8"))
    res = {}
    for p in glob.glob(str(LAB / "work" / "res" / "*.json")):
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        res[d["key"]] = d
    return inv, refs, res


def g3(c, ref):
    return c["sdr"] >= ref["sdr"] and c["seg"] >= ref["seg"] and c["lsd"] <= ref["lsd"]


def rule_p(c, ref, margin=0.0):
    return c["nmr"] <= ref["nmr"] + margin and c["nmr95"] <= ref["nmr95"] + margin


def rule_t(c, ref):
    return c["nmr"] <= max(ref["nmr"], FLOOR) and c["nmr95"] <= max(ref["nmr95"], FLOOR95)


def rule_e(c, ref):
    return g3(c, ref) and rule_p(c, ref)


RULES = {"E": rule_e, "P": rule_p, "T": rule_t}


def ref_kind(r):
    kinds = set(r.values())
    for k in ("literal", "radical", "padrao", "pasta"):
        if k in kinds:
            return k
    return "nenhum"


def pick(cands, names, ok, limit):
    """smallest candidate among names that passes ok and is smaller than limit bytes -> (name, bytes) or None"""
    best = None
    for n in names:
        c = cands.get(n)
        if c is None or c["bytes"] >= limit or not ok(c):
            continue
        if best is None or c["bytes"] < best[1]:
            best = (n, c["bytes"])
    return best


def main():
    inv, refs, res = load()
    total_cb = sum(r["cb"]["bytes"] for r in inv.values())
    missing = [k for k in inv if k not in res or "error" in res[k]]
    print("sons", len(inv), "com medida", len(inv) - len(missing), "sem medida", len(missing),
          "| Custo-beneficio hoje %.2f MiB" % (total_cb / MiB))
    wav = [k for k in inv if inv[k]["cb"]["ext"] == ".wav" and k not in missing]
    lossy = [k for k in inv if inv[k]["cb"]["ext"] != ".wav" and k not in missing]

    # ---- check of the port: the file the product chose must pass the product's own rule
    chosen = [k for k in wav if inv[k]["cb"]["fmt"] == "MS-ADPCM"]
    ok = sum(1 for k in chosen if g3(res[k]["cands"]["cb"], res[k]["cands"]["base"]))
    print("conferencia da regra do produto: %d de %d arquivos ADPCM de hoje passam na regra recalculada" % (ok, len(chosen)))

    options = {}

    def run_option(name, keys, names_of, ok_of, note=""):
        saved, n, sel = 0, 0, {}
        for k in keys:
            c = res[k]["cands"]
            cb = c["cb"] if "cb" in c else None
            limit = inv[k]["cb"]["bytes"]
            best = pick(c, names_of(k), ok_of(k), limit)
            if best:
                sel[k] = best[0]
                saved += limit - best[1]
                n += 1
        options[name] = {"saved": saved, "files": n, "sel": sel, "note": note, "scope": len(keys),
                         "scope_bytes": sum(inv[k]["cb"]["bytes"] for k in keys)}
        return options[name]

    def plan_layouts(k):
        o = inv[k]["orig"]
        rates = [22050] if o["rate"] <= 22050 else [22050, 44100]
        chans = [o["channels"]]
        c = res[k]["cands"]
        full = c.get(f"pcm_{max(rates)}_1")
        if o["channels"] == 2 and full and full["sdr"] >= 40.0:
            chans.append(1)
        if o["channels"] == 1:
            chans = [1]
        return [(r, ch) for r in rates for ch in chans]

    loops = [k for k in wav if inv[k]["cb"].get("loop")]
    shots = [k for k in wav if not inv[k]["cb"].get("loop")]
    literal = [k for k in shots if ref_kind(refs.get(k, {})) == "literal"]

    # O1: better MS-ADPCM encoder, the product's own rule (against the manual reference)
    for scope, keys in (("todos", wav), ("loops", loops), ("sem loop", shots)):
        run_option(f"O1 ADPCM melhor, regra do produto [{scope}]", keys,
                   lambda k: [f"adx_{r}_{c}" for r, c in plan_layouts(k)],
                   lambda k: (lambda c, b=res[k]["cands"]["base"]: g3(c, b)))
        run_option(f"O1e ADPCM melhor, regra do produto + NMR nao pior que hoje [{scope}]", keys,
                   lambda k: [f"adx_{r}_{c}" for r, c in plan_layouts(k)],
                   lambda k: (lambda c, b=res[k]["cands"]["base"], t=res[k]["cands"]["cb"]: g3(c, b) and rule_p(c, t)))
    # O2: ADPCM / PCM at any layout by the perceptual rules against today's file
    wav_names = lambda k: [n for n in res[k]["cands"] if n.startswith(("adx_", "pcm_"))]
    for rname in ("E", "P", "T"):
        for scope, keys in (("loops", loops), ("sem loop", shots)):
            run_option(f"O2 ADPCM/PCM qualquer layout, regra {rname} [{scope}]", keys, wav_names,
                       lambda k, f=RULES[rname]: (lambda c, t=res[k]["cands"]["cb"]: f(c, t)))
    # O4: Vorbis for the WAVs without loop
    ogg_names = lambda k: [n for n in res[k]["cands"] if n.startswith("ogg_")]
    mp3_names = lambda k: [n for n in res[k]["cands"] if n.startswith("mp3_")]
    for rname in ("E", "P", "T"):
        for scope, keys in (("sem loop", shots), ("sem loop, caminho literal", literal), ("loops (hipotese)", loops)):
            run_option(f"O4 Vorbis, regra {rname} [{scope}]", keys, ogg_names,
                       lambda k, f=RULES[rname]: (lambda c, t=res[k]["cands"]["cb"]: f(c, t)))
        run_option(f"O4m MP3, regra {rname} [sem loop]", shots, mp3_names,
                   lambda k, f=RULES[rname]: (lambda c, t=res[k]["cands"]["cb"]: f(c, t)))
    # fixed quality, original layout
    for q in (2, 3, 4, 5, 6):
        run_option(f"O4f Vorbis q{q} layout original, sem regra [sem loop]", shots,
                   lambda k, q=q: [f"ogg_{inv[k]['orig']['rate']}_{inv[k]['orig']['channels']}_q{q}"], lambda k: (lambda c: True))
        run_option(f"O4f Vorbis q{q} layout de hoje, sem regra [sem loop]", shots,
                   lambda k, q=q: [f"ogg_{inv[k]['cb']['rate']}_{inv[k]['cb']['channels']}_q{q}"], lambda k: (lambda c: True))
    # O5: the OGG and MP3 the assistant leaves alone, recoded at the same layout
    for ext, pre in ((".ogg", "ogg_q"), (".mp3", "mp3_V"), (".mp3", "ogg_q")):
        keys = [k for k in lossy if inv[k]["cb"]["ext"] == ext]
        for thr, thr95 in ((-9.0, -6.0), (-12.0, -9.0), (-15.0, -12.0)):
            run_option(f"O5 {ext} -> {pre}N, NMR <= {thr:g} e p95 <= {thr95:g}", keys,
                       lambda k, pre=pre: [n for n in res[k]["cands"] if n.startswith(pre)],
                       lambda k, a=thr, b=thr95: (lambda c: c["nmr"] <= a and c["nmr95"] <= b))
        for q in ((3, 4, 5, 6) if pre == "ogg_q" else (2, 4, 5, 6)):
            run_option(f"O5f {ext} -> {pre}{q}, sem regra", keys, lambda k, q=q, pre=pre: [f"{pre}{q}"], lambda k: (lambda c: True))

    print("\n%-74s %6s %9s %9s %9s" % ("opcao", "arq", "de (MiB)", "tira", "bancada"))
    for name, o in options.items():
        print("%-74s %6d %9.2f %9.2f %9.2f" % (name, o["files"], o["scope_bytes"] / MiB, o["saved"] / MiB,
                                             (total_cb - o["saved"]) / MiB))
    # quality of what was picked, against today's file (medians and worst)
    print("\nqualidade do escolhido (mediana | p95 do NMR medio; hoje -> novo)")
    for name, o in options.items():
        if not o["sel"]:
            continue
        a = np.array([res[k]["cands"]["cb"]["nmr"] if "cb" in res[k]["cands"] else -120 for k in o["sel"]])
        b = np.array([res[k]["cands"][n]["nmr"] for k, n in o["sel"].items()])
        worse = int(np.sum(b > a + 0.5))
        print("%-74s %6.1f -> %6.1f | %6.1f -> %6.1f | piores que hoje em >0,5 dB: %d" % (
            name, np.median(a), np.median(b), np.percentile(a, 95), np.percentile(b, 95), worse))
    out = {name: {"saved": o["saved"], "files": o["files"], "scope": o["scope"], "scope_bytes": o["scope_bytes"],
                  "sel": o["sel"]} for name, o in options.items()}
    (LAB / "out" / "analysis.json").write_text(json.dumps({"total_cb": total_cb, "options": out}), encoding="utf-8")


if __name__ == "__main__":
    main()
