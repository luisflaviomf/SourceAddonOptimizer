"""Como os addons da bancada nomeiam cada som: para saber o que quebra se a extensão mudar (.wav -> .ogg).

Para cada som de sound/, procura o caminho (sem "sound/", minúsculas, barras normais) em todo arquivo de texto do
grupo (Lua, txt, json, cfg...) e nos binários que guardam nomes de som (.mdl, .pcf, .bsp):
  literal   o caminho inteiro com a extensão aparece escrito
  radical   só o caminho sem a extensão aparece (a extensão é juntada pelo código)
  padrao    só o começo do nome aparece (nome + número montado pelo código, ex. "shot" .. i .. ".wav")
  pasta     só a pasta aparece (lista de arquivos, nome montado inteiro)
  nenhum    nada
e onde: lua / texto (scripts, json) / binario (mdl, pcf, bsp).

python refs.py -> out/refs.json + tabela
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import GROUPS, LAB, MiB, src_root

TEXT = {".lua", ".txt", ".json", ".cfg", ".res", ".vdf", ".ini", ".properties", ".dat", ".xml", ".csv", ".vcd"}
BINARY = {".mdl", ".pcf", ".bsp"}


def load(root):
    lua, text, binary = [], [], []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in TEXT:
            s = p.read_bytes().decode("latin1").lower().replace("\\\\", "/").replace("\\", "/")
            s = re.sub(r"/+", "/", s)
            (lua if ext == ".lua" else text).append(s)
        elif ext in BINARY:
            data = p.read_bytes()
            strs = b"\n".join(m.group() for m in re.finditer(rb"[\x20-\x7e]{6,260}", data))
            binary.append(strs.decode("latin1").lower().replace("\\", "/"))
    return "\n".join(lua), "\n".join(text), "\n".join(binary)


def classify(rel, blobs):
    """rel: path under sound/, lower case. -> {where: kind}"""
    stem = rel.rsplit(".", 1)[0]
    name = stem.rsplit("/", 1)[-1]
    folder = stem.rsplit("/", 1)[0] + "/" if "/" in stem else ""
    prefix = re.sub(r"[\d_\-]+$", "", name)
    out = {}
    for where, blob in blobs.items():
        if rel in blob:
            out[where] = "literal"
        elif stem in blob:
            out[where] = "radical"
        elif folder and len(prefix) >= 3 and (folder + prefix) in blob and prefix != name:
            out[where] = "padrao"
        elif folder and len(folder) > 4 and folder in blob:
            out[where] = "pasta"
    return out


def main():
    inv = json.loads((LAB / "out" / "inventory.json").read_text(encoding="utf-8"))
    refs = {}
    for g in GROUPS:
        lua, text, binary = load(src_root(g))
        blobs = {"lua": lua, "texto": text, "binario": binary}
        print(g, "lua %.1f MiB, texto %.1f MiB, binario(strings) %.1f MiB" % (len(lua) / MiB, len(text) / MiB, len(binary) / MiB),
              flush=True)
        for key, row in inv.items():
            if row["group"] != g:
                continue
            rel = row["rel"].lower()
            rel = rel[6:] if rel.startswith("sound/") else rel
            refs[key] = classify(rel, blobs)
    (LAB / "out" / "refs.json").write_text(json.dumps(refs), encoding="utf-8")

    order = ["literal", "radical", "padrao", "pasta"]

    def best(r):
        kinds = set(r.values())
        for k in order:
            if k in kinds:
                return k
        return "nenhum"

    acc = defaultdict(lambda: [0, 0])
    for key, r in refs.items():
        row = inv[key]
        loop = "loop" if row["cb"].get("loop") else "sem loop"
        where = "+".join(sorted(w for w, k in r.items() if k == best(r))) or "-"
        a = acc[(row["cb"]["ext"], loop, best(r), where)]
        a[0] += 1
        a[1] += row["cb"]["bytes"]
    print("\next, loop, melhor referencia, onde: arquivos, MiB no Custo-beneficio")
    for k in sorted(acc, key=lambda k: (k[0], k[1], -acc[k][1])):
        print(f"{str(k):62s} {acc[k][0]:5d} {acc[k][1] / MiB:8.2f}")


if __name__ == "__main__":
    main()
