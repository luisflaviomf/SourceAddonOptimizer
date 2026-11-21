"""Cenários finais (proto.json + proto2.json): tamanho dos sons da bancada por opção e combinadas, com a regra de
qualidade aplicada arquivo por arquivo; e a lista dos piores casos de cada opção para os pares A/B.

Regras contra o arquivo de hoje (métricas sempre medidas contra o ORIGINAL):
  igual_ou_melhor  NMR médio, NMR p95 e NMR de quadro curto (médio e p95) não piores que os do arquivo de hoje
  transparente     passa em igual_ou_melhor, OU fica abaixo dos pisos (NMR <= -12, p95 <= -9, curto <= -10, curto p95 <= -7)
A regra do produto (SDR/SNR/LSD contra a referência manual) é a de O1.

python analyze2.py -> out/scenarios.json, out/ab_spec.json + tabelas
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import LAB, MiB

FLOORS = {"nmr": -12.0, "nmr95": -9.0, "nmr_s": -10.0, "nmr_s95": -7.0}
KEYS = ("nmr", "nmr95", "nmr_s", "nmr_s95")


def load():
    inv = json.loads((LAB / "out" / "inventory.json").read_text(encoding="utf-8"))
    refs = json.loads((LAB / "out" / "refs.json").read_text(encoding="utf-8"))
    r1 = json.loads((LAB / "out" / "proto.json").read_text(encoding="utf-8"))
    r2 = json.loads((LAB / "out" / "proto2.json").read_text(encoding="utf-8"))
    cands = {}
    for k, d in r1.items():
        if "error" in d or "cands" not in d:
            continue
        c = {n: dict(v) for n, v in d["cands"].items()}
        for n, v in r2.get(k, {}).get("cands", {}).items():
            c[n] = {**c.get(n, {}), **v}
        cands[k] = c
    # the product's rule uses the LSD of its own float32 FFT (proto3)
    p3 = LAB / "out" / "proto3.json"
    if p3.exists():
        for k, d in json.loads(p3.read_text(encoding="utf-8")).items():
            for n, v in d.items():
                if k in cands and n in cands[k]:
                    cands[k][n]["lsd"] = v
    return inv, refs, r1, cands


def g3(c, ref):
    return c["sdr"] >= ref["sdr"] and c["seg"] >= ref["seg"] and c["lsd"] <= ref["lsd"]


def no_worse(c, t):
    return all(k in c and c[k] <= t.get(k, -120.0) for k in KEYS)


def transparent(c, t):
    return all(k in c and c[k] <= max(t.get(k, -120.0), FLOORS[k]) for k in KEYS)


def absolute(c, scale=0.0):
    return all(k in c and c[k] <= FLOORS[k] + scale for k in KEYS)


def ref_kind(r):
    kinds = set(r.values())
    for k in ("literal", "radical", "padrao", "pasta"):
        if k in kinds:
            return k
    return "nenhum"


def main():
    inv, refs, r1, cands = load()
    total_cb = sum(r["cb"]["bytes"] for r in inv.values())
    wav = [k for k in cands if inv[k]["cb"]["ext"] == ".wav" and "cb" in cands[k] and "nmr_s" in cands[k]["cb"]]
    loops = [k for k in wav if inv[k]["cb"].get("loop")]
    shots = [k for k in wav if not inv[k]["cb"].get("loop")]
    oggs = [k for k in cands if inv[k]["cb"]["ext"] == ".ogg"]
    mp3s = [k for k in cands if inv[k]["cb"]["ext"] == ".mp3"]
    print("WAV com as duas passadas:", len(wav), "de", sum(1 for r in inv.values() if r["cb"]["ext"] == ".wav"))

    def plan(k):
        o = inv[k]["orig"]
        rates = [22050] if o["rate"] <= 22050 else [22050, 44100]
        chans = [o["channels"]]
        full = cands[k].get(f"pcm_{max(rates)}_1")
        if o["channels"] == 2 and full and full["sdr"] >= 40.0:
            chans.append(1)
        return [(r, c) for r in rates for c in chans]

    def best(k, names, ok):
        limit = inv[k]["cb"]["bytes"]
        pick = None
        for n in names:
            c = cands[k].get(n)
            if c is None or "bytes" not in c or c["bytes"] >= limit or not ok(c):
                continue
            if pick is None or c["bytes"] < pick[1]:
                pick = (n, c["bytes"])
        return pick

    sel = {}

    def option(name, keys, names_of, ok_of):
        s = {}
        for k in keys:
            p = best(k, names_of(k), ok_of(k))
            if p:
                s[k] = p
        sel[name] = s
        return s

    today = lambda k: cands[k]["cb"]
    adx_plan = lambda k: [f"adx_{r}_{c}" for r, c in plan(k)]
    # the perceptual metric does not see the stereo image: these options never use fewer channels than today's file
    same_ch = lambda k, n: int(n.rsplit("_", 1)[1]) >= inv[k]["cb"]["channels"]
    adx_all = lambda k: [n for n in cands[k] if n.startswith("adx_") and "11025" not in n and same_ch(k, n)]
    adn_all = lambda k: [n for n in cands[k] if n.startswith(("adx_", "adn")) and "11025" not in n and same_ch(k, n)]
    ogg_orig = lambda qs: (lambda k: [f"ogg_{inv[k]['orig']['rate']}_{inv[k]['orig']['channels']}_q{q}" for q in qs])
    ogg_any = lambda k: [n for n in cands[k] if n.startswith("ogg_") and "nmr_s" in cands[k][n]]

    # 1. same formats, the product's own rule, better encoder
    option("A1 ADPCM melhor · regra do produto", wav, adx_plan, lambda k: (lambda c, b=cands[k]["base"]: g3(c, b)))
    option("A2 ADPCM melhor · regra do produto + perceptual não pior", wav, adx_plan,
           lambda k: (lambda c, b=cands[k]["base"], t=today(k): g3(c, b) and no_worse(c, t)))
    # 2. same formats, perceptual rule against today's file
    option("B1 ADPCM melhor · perceptual não pior que hoje", wav, adx_all, lambda k: (lambda c, t=today(k): no_worse(c, t)))
    option("B2 ADPCM com ruído moldado · perceptual não pior que hoje", wav, adn_all,
           lambda k: (lambda c, t=today(k): no_worse(c, t)))
    option("B3 ADPCM com ruído moldado · transparente", wav, adn_all, lambda k: (lambda c, t=today(k): transparent(c, t)))
    # 3. Vorbis for the sounds without loop
    for label, keys in (("sem loop", shots), ("sem loop, caminho escrito inteiro no Lua",
                                               [k for k in shots if ref_kind(refs.get(k, {})) == "literal"])):
        option(f"C1 Vorbis layout original q4–6 · perceptual não pior [{label}]", keys, ogg_orig((4, 5, 6)),
               lambda k: (lambda c, t=today(k): no_worse(c, t)))
        option(f"C2 Vorbis layout original q4–6 · transparente [{label}]", keys, ogg_orig((4, 5, 6)),
               lambda k: (lambda c, t=today(k): transparent(c, t)))
        option(f"C3 Vorbis qualquer layout q2–6 · perceptual não pior [{label}]", keys, ogg_any,
               lambda k: (lambda c, t=today(k): no_worse(c, t)))
    option("C4 Vorbis layout original q4 fixo, sem regra [sem loop]", shots, ogg_orig((4,)), lambda k: (lambda c: True))
    option("C9 Vorbis nos loops (hipótese: o motor não repete OGG)", loops, ogg_orig((4, 5, 6)),
           lambda k: (lambda c, t=today(k): transparent(c, t)))
    # 4. the OGG / MP3 the assistant does not touch (reference = the file itself)
    lossy_ok = lambda a, b: (lambda k: (lambda c: c["nmr"] <= a and c["nmr95"] <= b))
    for thr in ((-12.0, -9.0), (-15.0, -12.0)):
        option(f"D1 OGG→OGG q3–6 · NMR ≤ {thr[0]:g} / p95 ≤ {thr[1]:g}", oggs, lambda k: [f"ogg_q{q}" for q in (3, 4, 5, 6)], lossy_ok(*thr))
        option(f"D2 MP3→MP3 V2–V6 · NMR ≤ {thr[0]:g} / p95 ≤ {thr[1]:g}", mp3s, lambda k: [f"mp3_V{v}" for v in (2, 4, 5, 6)], lossy_ok(*thr))
        option(f"D3 MP3→Vorbis q3–6 · NMR ≤ {thr[0]:g} / p95 ≤ {thr[1]:g}", mp3s, lambda k: [f"ogg_q{q}" for q in (3, 4, 5, 6)], lossy_ok(*thr))

    def saved(s):
        return sum(inv[k]["cb"]["bytes"] - b for k, (n, b) in s.items())

    def quality(s):
        a = np.array([cands[k].get("cb", {}).get("nmr", -120.0) for k in s]) if s else np.array([0.0])
        b = np.array([cands[k][n]["nmr"] for k, (n, _) in s.items()]) if s else np.array([0.0])
        return np.median(a), np.median(b), np.percentile(a, 95), np.percentile(b, 95), int((b > 0).sum())

    print("\n%-78s %5s %8s %8s | NMR mediana hoje→novo, p95 hoje→novo, novos > 0 dB" % ("opção", "arq", "tira MiB", "sons MiB"))
    for name, s in sel.items():
        q = quality(s)
        print("%-78s %5d %8.2f %8.2f | %6.1f → %6.1f, %6.1f → %6.1f, %d" % (name, len(s), saved(s) / MiB,
                                                                          (total_cb - saved(s)) / MiB, *q))

    def combine(*names):
        out = {}
        for nm in names:
            for k, p in sel[nm].items():
                if k not in out or p[1] < out[k][1]:
                    out[k] = p
        return out

    combos = {
        "1 · só o codificador (A2)": combine("A2 ADPCM melhor · regra do produto + perceptual não pior"),
        "2 · + regra perceptual nos WAV (B2)": combine("A2 ADPCM melhor · regra do produto + perceptual não pior",
                                                       "B2 ADPCM com ruído moldado · perceptual não pior que hoje"),
        "3 · + OGG e MP3 recodificados (D1, D2 em −12/−9)": combine(
            "A2 ADPCM melhor · regra do produto + perceptual não pior",
            "B2 ADPCM com ruído moldado · perceptual não pior que hoje",
            "D1 OGG→OGG q3–6 · NMR ≤ -12 / p95 ≤ -9", "D2 MP3→MP3 V2–V6 · NMR ≤ -12 / p95 ≤ -9"),
        "4 · + Vorbis nos sem loop de caminho escrito (C2 literal)": combine(
            "A2 ADPCM melhor · regra do produto + perceptual não pior",
            "B2 ADPCM com ruído moldado · perceptual não pior que hoje",
            "D1 OGG→OGG q3–6 · NMR ≤ -12 / p95 ≤ -9", "D2 MP3→MP3 V2–V6 · NMR ≤ -12 / p95 ≤ -9",
            "C2 Vorbis layout original q4–6 · transparente [sem loop, caminho escrito inteiro no Lua]"),
        "5 · + Vorbis em todos os sem loop (C2)": combine(
            "A2 ADPCM melhor · regra do produto + perceptual não pior",
            "B2 ADPCM com ruído moldado · perceptual não pior que hoje",
            "D1 OGG→OGG q3–6 · NMR ≤ -12 / p95 ≤ -9", "D2 MP3→MP3 V2–V6 · NMR ≤ -12 / p95 ≤ -9",
            "C2 Vorbis layout original q4–6 · transparente [sem loop]"),
    }
    print("\ncombinações (cada arquivo fica com a menor opção que passou)")
    for name, s in combos.items():
        by = Counter()
        for k, (n, b) in s.items():
            kind = ("loop" if inv[k]["cb"].get("loop") else "sem loop") if inv[k]["cb"]["ext"] == ".wav" else inv[k]["cb"]["ext"]
            by[kind] += inv[k]["cb"]["bytes"] - b
        print("%-60s tira %7.2f MiB → sons %7.2f MiB | %s" % (name, saved(s) / MiB, (total_cb - saved(s)) / MiB,
                                                             ", ".join("%s %.1f" % (a, v / MiB) for a, v in by.most_common())))
    out = {"total_cb": total_cb,
           "options": {n: {"saved": saved(s), "files": len(s), "sel": {k: p[0] for k, p in s.items()}} for n, s in sel.items()},
           "combos": {n: {"saved": saved(s), "files": len(s), "sel": {k: p[0] for k, p in s.items()}} for n, s in combos.items()}}
    (LAB / "out" / "scenarios.json").write_text(json.dumps(out), encoding="utf-8")

    # ---- A/B: worst cases of each recommended option (largest NMR of the new file, and largest worsening)
    def worst(s, n, by="new"):
        rows = []
        for k, (name, b) in s.items():
            c, t = cands[k][name], cands[k].get("cb", {})
            dur = inv[k]["orig"].get("duration") or 0
            if dur < 0.4:
                continue
            score = c["nmr"] if by == "new" else c["nmr"] - t.get("nmr", -120.0)
            rows.append((score, k, name))
        rows.sort(reverse=True)
        return rows[:n]

    def biggest(s, n):
        rows = sorted(((inv[k]["cb"]["bytes"] - b, k, name) for k, (name, b) in s.items()), reverse=True)
        return rows[:n]

    def cases(rows, why):
        return [{"key": k, "new": name, "why": why % v} for v, k, name in rows]

    spec = []
    s = sel["C2 Vorbis layout original q4–6 · transparente [sem loop]"]
    spec.append({"title": "Vorbis (.ogg) no lugar do WAV sem loop",
                 "note": "Vorbis no layout do original (taxa e canais do addon), q4 a q6, o menor que passa na regra. "
                         "Primeiro os piores pela métrica, depois os que mais encolhem.",
                 "cases": cases(worst(s, 8), "pior NMR do arquivo novo: %.1f dB") +
                          cases(worst(s, 4, "delta"), "maior piora contra hoje: %+.1f dB") +
                          cases(biggest(s, 4), "maior corte: %d bytes")})
    s = sel["A2 ADPCM melhor · regra do produto + perceptual não pior"]
    spec.append({"title": "MS-ADPCM com o codificador melhor (mesma regra de hoje)",
                 "note": "Mesmo formato que o produto já grava. Aqui o arquivo novo passa na regra do produto e não é "
                         "pior que o de hoje na métrica perceptual.",
                 "cases": cases(worst(s, 6), "pior NMR do arquivo novo: %.1f dB") + cases(biggest(s, 4), "maior corte: %d bytes")})
    s = {k: p for k, p in sel["B2 ADPCM com ruído moldado · perceptual não pior que hoje"].items()
         if k not in sel["A2 ADPCM melhor · regra do produto + perceptual não pior"] or
         p[1] < sel["A2 ADPCM melhor · regra do produto + perceptual não pior"][k][1]}
    spec.append({"title": "MS-ADPCM com ruído moldado (regra perceptual)",
                 "note": "Arquivos que só encolhem com a regra perceptual (não passam na regra de SDR do produto).",
                 "cases": cases(worst(s, 8), "pior NMR do arquivo novo: %.1f dB") + cases(biggest(s, 6), "maior corte: %d bytes")})
    s = sel["D1 OGG→OGG q3–6 · NMR ≤ -12 / p95 ≤ -9"]
    spec.append({"title": "OGG recodificado (Vorbis → Vorbis menor)", "note": "Referência = o próprio OGG do addon.",
                 "cases": cases(worst(s, 5), "pior NMR do arquivo novo: %.1f dB") + cases(biggest(s, 3), "maior corte: %d bytes")})
    s = sel["D2 MP3→MP3 V2–V6 · NMR ≤ -12 / p95 ≤ -9"]
    spec.append({"title": "MP3 recodificado (MP3 → MP3 menor)", "note": "Referência = o próprio MP3 do addon.",
                 "cases": cases(worst(s, 4), "pior NMR do arquivo novo: %.1f dB") + cases(biggest(s, 2), "maior corte: %d bytes")})
    # today's worst files by the perceptual metric, with the lab encoder at the same layout as "novo"
    rows = sorted(((cands[k]["cb"]["nmr"], k) for k in wav if inv[k]["cb"]["fmt"] == "MS-ADPCM"
                   and (inv[k]["orig"].get("duration") or 0) >= 0.4), reverse=True)[:8]
    spec.append({"title": "Os piores de HOJE pela métrica perceptual",
                 "note": "Arquivos em que o ADPCM de hoje deixa ruído acima do limiar. 'Novo' = mesmo formato e tamanho, "
                         "codificador de laboratório. Servem para conferir de ouvido se a métrica acerta.",
                 "cases": [{"key": k, "new": f"adx_{inv[k]['cb']['rate']}_{inv[k]['cb']['channels']}",
                            "why": "NMR de hoje: %.1f dB" % v} for v, k in rows]})
    (LAB / "out" / "ab_spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\npares A/B:", sum(len(s["cases"]) for s in spec), "casos em", len(spec), "grupos")


if __name__ == "__main__":
    main()
