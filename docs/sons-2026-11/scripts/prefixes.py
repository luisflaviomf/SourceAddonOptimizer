"""Como o Lua da bancada chama os WAV sem loop de original estéreo: com prefixo de nome que muda a espacialização
(")" estéreo espacializado, "^" variante por distância) ou sem. No jogo, WAV e OGG estéreo só se comportam igual
quando o nome leva ")" ou "^".

python prefixes.py
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import analyze2
from sndlib import GROUPS, LAB, MiB, src_root

BS = chr(92)
CHARS = "*#@><^)}$!?&~"


def main():
    inv, refs, r1, cands = analyze2.load()
    sc = json.loads((LAB / "out" / "scenarios.json").read_text(encoding="utf-8"))
    c2 = sc["options"]["C2 Vorbis layout original q4–6 · transparente [sem loop]"]["sel"]
    lua = {}
    for g in GROUPS:
        parts = [p.read_bytes().decode("latin1").lower().replace(BS + BS, "/").replace(BS, "/")
                 for p in src_root(g).rglob("*.lua")]
        lua[g] = re.sub(r"/+", "/", "\n".join(parts))
    stats, size, gain, examples = Counter(), Counter(), Counter(), []
    for k, r in inv.items():
        if r["cb"]["ext"] != ".wav" or r["cb"].get("loop") or r["orig"]["channels"] != 2:
            continue
        rel = r["rel"].lower()
        rel = rel[6:] if rel.startswith("sound/") else rel
        text = lua[r["group"]]
        pre = Counter()
        for m in re.finditer(re.escape(rel), text):
            j = m.start()
            while j > 0 and text[j - 1] in CHARS:
                j -= 1
            prefix = text[j:m.start()]
            quote = text[j - 1] if j > 0 else ""
            pre[prefix if quote in "\"'" else "?" + prefix] += 1
        if not pre:
            cls = "sem referencia literal"
        elif all((")" in x or "^" in x) and not x.startswith("?") for x in pre):
            cls = "so com ) ou ^"
        elif any(")" in x or "^" in x for x in pre):
            cls = "misturado"
        else:
            cls = "sem prefixo de espaco"
        stats[cls] += 1
        size[cls] += r["cb"]["bytes"]
        if k in c2:
            gain[cls] += r["cb"]["bytes"] - cands[k][c2[k]]["bytes"]
        if len(examples) < 5 and cls in ("so com ) ou ^", "misturado"):
            examples.append((k[-60:], dict(pre)))
    print("WAV sem loop de original estereo, como o Lua os chama (arquivos, MiB hoje, ganho se virassem Vorbis pela C2):")
    for c in stats:
        print("  %-26s %4d arq %6.2f MiB | C2 tiraria %5.2f MiB" % (c, stats[c], size[c] / MiB, gain[c] / MiB))
    print(examples)
    allp = Counter()
    for g in GROUPS:
        for m in re.finditer(r"[\"']([*#@><^)}$!?&~]+)[a-z0-9_]+/[a-z0-9_/\-\. ]+\.(wav|ogg|mp3)", lua[g]):
            allp[(g, m.group(1), m.group(2))] += 1
    print("prefixos em nomes de som escritos no Lua:", allp.most_common(14))


if __name__ == "__main__":
    main()
