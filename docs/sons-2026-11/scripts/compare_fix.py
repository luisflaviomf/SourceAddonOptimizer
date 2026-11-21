"""Compara os sons do Custo-benefício antes (lab/cb) e depois (lab/cb_fix) da correção dos loops MS-ADPCM no produto.

python compare_fix.py [tag_antes] [tag_depois]
- o que mudou de bytes e o que ficou idêntico (por ter ou não marca de loop);
- em todo loop MS-ADPCM novo: o reinício do motor (fórmula medida no jogo) cai no início verdadeiro do loop, e os
  dados terminam na última amostra;
- o mesmo cálculo para a saída de antes, para contar o que estava errado.
"""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import GROUPS, LAB, MiB, parse_wav, src_root

A = sys.argv[1] if len(sys.argv) > 1 else "cb"
B = sys.argv[2] if len(sys.argv) > 2 else "cb_fix"


def spb_of(align, ch):
    return 2 + (align - 7 * ch) * 2 // ch


def decodable(data_bytes, align, ch):
    n = data_bytes // align * spb_of(align, ch)
    rest = data_bytes % align
    if rest >= 7 * ch:
        n += 2 + (rest - 7 * ch) * 2 // ch
    return n


def loop_start(w):
    """the mark the engine keeps: sampler loop when present (written last), else the first cue"""
    if w["smpl_loops"]:
        return w["smpl_loops"][0][0]
    if w["cue"]:
        return w["cue_pos"][0]
    return None


def scaled(value, src_rate, rate):
    # as the product scales loop marks (Math.Round: halves go to the even sample)
    return round(value * (rate / src_rate)) if src_rate != rate else value


def check(path, orig):
    """for a looped MS-ADPCM file: (pad samples played each pass, restart error in samples)"""
    w = parse_wav(path)
    if w is None or w["fmt"] != "MS-ADPCM":
        return None
    mark = loop_start(w)
    true_start = loop_start(orig)
    if mark is None or true_start is None:
        return None
    ch, align = w["channels"], w["align"]
    want = scaled(true_start, orig["rate"], w["rate"])
    got = (mark // align) * spb_of(align, ch) + mark % align
    pad = decodable(w["data_bytes"], align, ch) - w["frames"]
    total = decodable(w["data_bytes"], align, ch)
    return {"pad": pad, "err": got - want, "beyond": got >= total and want < total, "align": align, "rate": w["rate"], "ch": ch}


def main():
    stats = Counter()
    size = Counter()
    fmt_change = Counter()
    worst = []
    blocks = Counter()
    for g in GROUPS:
        ra, rb = LAB / A / g, LAB / B / g
        for pa in sorted(ra.rglob("*.wav")):
            rel = pa.relative_to(ra)
            pb = rb / rel
            da, db = pa.read_bytes(), pb.read_bytes()
            size["antes"] += len(da)
            size["depois"] += len(db)
            orig = parse_wav(src_root(g) / rel)
            wa, wb = parse_wav(pa), parse_wav(pb)
            looped = wa["loop"]
            same = da == db
            stats[("com marca de loop" if looped else "sem marca de loop", "identico" if same else "mudou")] += 1
            if not same:
                fmt_change[(f"{wa['fmt']} {wa['rate']}/{wa['channels']}", f"{wb['fmt']} {wb['rate']}/{wb['channels']}",
                            "loop" if looped else "sem loop")] += 1
            for tag, path in (("antes", pa), ("depois", pb)):
                c = check(path, orig)
                if c is None:
                    continue
                stats[(tag, "loops MS-ADPCM")] += 1
                size[(tag, "loops MS-ADPCM")] += path.stat().st_size
                if c["pad"] > 1:
                    stats[(tag, "enchimento tocado a cada volta")] += 1
                if c["err"] != 0:
                    stats[(tag, "reinicio fora do lugar")] += 1
                    if c["beyond"]:
                        stats[(tag, "reinicio depois do fim (toca uma vez e para)")] += 1
                if tag == "depois":
                    blocks[(c["ch"], c["align"])] += 1
                    if c["pad"] == 1:
                        stats[(tag, "uma amostra a mais por volta (mono impar)")] += 1
                    if c["err"] != 0 or c["pad"] > 1:
                        worst.append((str(rel), c))
    print("bytes: antes %.3f MiB, depois %.3f MiB (%+.3f)" % (size["antes"] / MiB, size["depois"] / MiB,
                                                             (size["depois"] - size["antes"]) / MiB))
    for k in sorted(stats, key=str):
        print("  %-62s %d" % (" / ".join(k), stats[k]))
    print("loops MS-ADPCM: antes %.2f MiB, depois %.2f MiB" % (size[("antes", "loops MS-ADPCM")] / MiB,
                                                             size[("depois", "loops MS-ADPCM")] / MiB))
    print("mudancas de formato:")
    for k, v in fmt_change.most_common():
        print("  %-22s -> %-22s %-8s %d" % (*k, v))
    print("tamanho de bloco dos loops MS-ADPCM novos (canais, bytes):", dict(blocks))
    print("loops novos ainda errados:", len(worst), worst[:5])


if __name__ == "__main__":
    main()
