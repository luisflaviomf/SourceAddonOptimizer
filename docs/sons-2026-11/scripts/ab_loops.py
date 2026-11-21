"""Página de escuta dos loops MS-ADPCM gravados DENTRO DO JOGO (segunda rodada): original x como o Custo-benefício
grava hoje x escrita corrigida. Os trechos são cortados da gravação que o próprio motor fez (game/run/g2).

python ab_loops.py -> ab/loops.html + ab/loops/<n>_{original,hoje,corrigido}.wav
"""
import html
import json
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import game_analyze as ga
from sndlib import LAB

FS = 44100
RUN = LAB / "game" / "run" / "g2"
OUT = LAB / "ab" / "loops"

NOTES = {
    "simfphys/sound/gta3d/mci102_low.wav": "motor, mono, 0,55 s; hoje: buraco de 45 ms a cada volta",
    "LVS/sound/lvs/vehicles/bmw_r75/eng_loop.wav": "motor, mono, 1,11 s; hoje: buraco de 45 ms a cada volta",
    "GLIDE/sound/glide/horns/car_horn_med_3.wav": "buzina, mono; hoje: recomeça no ponto errado e gagueja",
    "simfphys/sound/gta4/vehicles/horns/vigero_horn.wav": "buzina, mono; hoje: toca 0,22 s e para",
    "simfphys/sound/simulated_vehicles/gta5_dukes/dukes_idle.wav": "motor, estéreo, 1,48 s; hoje: buraco de 86 ms na emenda "
                                                                   "(na gravação do original entrou outro som do mapa)",
    "GLIDE/sound/glide/horns/police_horn_2.wav": "buzina, estéreo; hoje: buraco de 83 ms a cada volta",
}


def wav(path, x):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    hdr = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, FS, FS * 4, 4, 16)
    path.write_bytes(hdr + b"data" + struct.pack("<I", len(pcm)) + pcm)


def main():
    plan = json.loads((LAB / "game" / "plan2.json").read_text(encoding="utf-8"))
    meta = {t["file"]: t for t in plan["tests"]}
    slots = {s["id"]: s for s in plan["slots"]}
    x, _ = ga.load_capture(RUN)
    t_sync, _ = ga.find_sync(x.mean(axis=1))
    events = json.loads((RUN / "log.json").read_text(encoding="utf-8"))["events"]
    OUT.mkdir(parents=True, exist_ok=True)
    groups = {}
    for ev in events:
        m = meta.get(ev["file"])
        if not m or m.get("kind") != "realloop" or ev["api"] == "bass":
            continue
        a = int((t_sync + ev["t"] - 0.05) * FS)
        seg = x[a:a + int((slots[ev["id"]]["dur"] + 0.15) * FS)] * 2.0        # the test played at volume 0.5
        groups.setdefault(m["source"], {})[m["version"]] = seg
    body = []
    for i, (src, vers) in enumerate(groups.items(), start=1):
        names = {"orig": "original", "hoje": "hoje", "novo": "corrigido"}
        row = []
        for v, label in names.items():
            f = OUT / f"{i}_{label}.wav"
            wav(f, vers[v])
            row.append(f"<label>{label}</label><audio controls preload='none' src='loops/{f.name}'></audio>")
        body.append(f"<div class='case'><div class='name'>{i}. {html.escape(src)}</div>"
                    f"<div class='why'>{html.escape(NOTES.get(src, ''))}</div><div class='row'>{''.join(row)}</div></div>")
    page = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><title>Loops MS-ADPCM no jogo</title>
<style>
body{font:15px/1.45 system-ui,Segoe UI,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px;background:#16181c;color:#e6e6e6}
h1{font-size:22px} .note{color:#b8b8b8;margin:6px 0 14px}
.case{background:#20242a;border-radius:10px;padding:12px 14px;margin:12px 0}
.name{font-weight:600;word-break:break-all} .why{color:#9fc3ff;font-size:13px;margin:4px 0 8px}
.row{display:flex;gap:14px;flex-wrap:wrap;align-items:center} .row label{min-width:70px;font-weight:600} audio{height:34px;width:280px}
a{color:#9fc3ff}
</style></head><body>
<h1>Loops MS-ADPCM gravados dentro do jogo</h1>
<p class="note">Cada trecho tem 2,4 s e foi gravado pelo próprio motor do jogo (cliente do laboratório), com o loop tocando
por <code>CreateSound</code>: o som original do addon, o arquivo que o Custo-benefício grava hoje (MS-ADPCM do ffmpeg) e o
mesmo som com a escrita corrigida (último bloco curto + marca compensada). Ouça a emenda: em "hoje" há um corte a cada
volta; nas buzinas mono o loop recomeça no ponto errado ou para. <a href="index.html">Voltar aos pares A/B</a></p>
__BODY__
</body></html>"""
    (LAB / "ab" / "loops.html").write_text(page.replace("__BODY__", "\n".join(body)), encoding="utf-8")
    idx = LAB / "ab" / "index.html"
    s = idx.read_text(encoding="utf-8")
    link = "<p class=\"note\"><b>Loops no jogo:</b> <a style=\"color:#9fc3ff\" href=\"loops.html\">original × hoje × corrigido, gravados dentro do jogo</a>.</p>"
    if "loops.html" not in s:
        s = s.replace("__LINK__", "").replace("</h1>", "</h1>\n" + link, 1)
        idx.write_text(s, encoding="utf-8")
    print("pagina:", LAB / "ab" / "loops.html", len(groups), "loops")


if __name__ == "__main__":
    main()
