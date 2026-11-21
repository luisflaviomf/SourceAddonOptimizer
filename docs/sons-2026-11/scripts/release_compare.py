"""Confere a saída do exe publicado (assistente, Só texturas e sons, Custo-benefício) contra lab/cb_fix, a saída do
código do produto pelo driver de teste, que foi a conferida no jogo.

python release_compare.py [pasta de saída] -> out/release_compare.json + resumo
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import LAB, parse_wav

CHECK = LAB / "release_check"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else next(p for p in sorted(CHECK.iterdir())
                                                           if p.is_dir() and p.name.startswith("bancada_sons") and p.name != "bancada_sons")
    owner = json.loads((CHECK / "owner.json").read_text(encoding="utf-8"))
    src = CHECK / "bancada_sons"
    same = diff = missing = 0
    size_src = size_out = size_fix = 0
    loops = {"MS-ADPCM": 0, "PCM16": 0, "outro": 0}
    loop_same = loop_total = 0
    differ = []
    for rel, group in sorted(owner.items()):
        a, b = out / rel, LAB / "cb_fix" / group / rel
        size_src += (src / rel).stat().st_size
        if not a.exists():
            missing += 1
            differ.append({"file": rel, "why": "faltou na saida"})
            continue
        size_out += a.stat().st_size
        size_fix += b.stat().st_size
        equal = a.stat().st_size == b.stat().st_size and sha(a) == sha(b)
        w = parse_wav(a)
        if w and w["loop"]:
            loop_total += 1
            loop_same += equal
            loops[w["fmt"] if w["fmt"] in loops else "outro"] += 1
        if equal:
            same += 1
        else:
            diff += 1
            wb = parse_wav(b)
            differ.append({"file": rel, "exe": [w and w["fmt"], w and w["rate"], w and w["channels"], a.stat().st_size],
                           "driver": [wb and wb["fmt"], wb and wb["rate"], wb and wb["channels"], b.stat().st_size]})
    others = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p.suffix.lower() != ".wav")
    res = {"output": str(out), "files": len(owner), "identical": same, "different": diff, "missing": missing,
           "loops": loop_total, "loops_identical": loop_same, "loops_by_format": loops,
           "mib_original": round(size_src / 2 ** 20, 2), "mib_exe": round(size_out / 2 ** 20, 2),
           "mib_driver": round(size_fix / 2 ** 20, 2), "other_files": others[:20], "differ": differ[:200]}
    (LAB / "out" / "release_compare.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print("saida do exe:", out)
    print("WAV: %d; identicos ao driver byte a byte: %d; diferentes: %d; faltando: %d" % (len(owner), same, diff, missing))
    print("loops: %d (identicos %d); por formato %s" % (loop_total, loop_same, loops))
    print("tamanho: original %.2f MiB -> exe %.2f MiB (driver %.2f MiB)" % (size_src / 2 ** 20, size_out / 2 ** 20, size_fix / 2 ** 20))
    print("outros arquivos na saida:", others[:10])
    for d in differ[:15]:
        print("  ", d)


if __name__ == "__main__":
    main()
