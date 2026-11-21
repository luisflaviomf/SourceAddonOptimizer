"""Pares A/B de ouvido: original x hoje (Custo-benefício) x novo, dos piores casos de cada opção.

python ab_make.py  (lê out/ab_spec.json: [{"title", "note", "cases": [{"key", "new", "why"}]}])
-> ab/index.html + ab/<n>/original.wav, hoje.wav, novo.wav (tudo decodificado para PCM16, que o navegador toca;
   o som é o mesmo que o jogo decodifica do arquivo de cada opção)
"""
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build
from sndlib import LAB

AB = LAB / "ab"

PAGE = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><title>Sons: pares A/B</title>
<style>
body{font:15px/1.45 system-ui,Segoe UI,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px;background:#16181c;color:#e6e6e6}
h1{font-size:22px} h2{font-size:18px;margin-top:36px;border-bottom:1px solid #444;padding-bottom:6px}
.note{color:#b8b8b8;margin:6px 0 14px}
.case{background:#20242a;border-radius:10px;padding:12px 14px;margin:12px 0}
.name{font-weight:600;word-break:break-all} .why{color:#9fc3ff;font-size:13px}
table{border-collapse:collapse;margin:8px 0;font-size:13px} td,th{padding:3px 10px;text-align:right;border-bottom:1px solid #333}
td:first-child,th:first-child{text-align:left}
.row{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-top:6px}
.row label{min-width:70px;font-weight:600} audio{height:34px;width:300px}
button{background:#3a6df0;color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;font-size:13px}
button.sec{background:#444} .blind{margin-top:8px;padding-top:8px;border-top:1px dashed #444}
.res{margin-left:10px;color:#ffd479}
</style></head><body>
<h1>Sons: pares de ouvido (original × hoje × novo)</h1>
<p class="note">Cada caso tem o som original do addon, o que o Custo-benefício grava hoje e o que a opção gravaria.
Use fones. "Teste cego" toca hoje e novo em ordem sorteada (X e Y) e só mostra qual é qual quando você clicar em Revelar.
NMR = ruído sobre o limiar de mascaramento (menor é melhor; acima de 0 dB o modelo considera audível em volume alto).
Os tamanhos são os do arquivo que vai no addon.</p>
__BODY__
<script>
document.querySelectorAll('.case').forEach(function(c){
  var flip=Math.random()<0.5, a=c.dataset.hoje, b=c.dataset.novo, pl=new Audio(), res=c.querySelector('.res');
  c.querySelector('.bx').onclick=function(){pl.src=flip?b:a;pl.play()};
  c.querySelector('.by').onclick=function(){pl.src=flip?a:b;pl.play()};
  c.querySelector('.bo').onclick=function(){pl.src=c.dataset.orig;pl.play()};
  c.querySelector('.bs').onclick=function(){pl.pause()};
  c.querySelector('.br').onclick=function(){res.textContent=flip?'X = novo, Y = hoje':'X = hoje, Y = novo'};
});
</script></body></html>"""


def kb(n):
    return f"{n / 1024:.1f} KB"


def main():
    spec = json.loads((LAB / "out" / "ab_spec.json").read_text(encoding="utf-8"))
    inv = json.loads((LAB / "out" / "inventory.json").read_text(encoding="utf-8"))
    res = json.loads((LAB / "out" / "proto.json").read_text(encoding="utf-8"))
    p2 = LAB / "out" / "proto2.json"
    res2 = json.loads(p2.read_text(encoding="utf-8")) if p2.exists() else {}
    AB.mkdir(parents=True, exist_ok=True)
    body, n = [], 0
    for sec in spec:
        body.append(f"<h2>{html.escape(sec['title'])}</h2><div class='note'>{html.escape(sec.get('note', ''))}</div>")
        for case in sec["cases"]:
            n += 1
            key, new = case["key"], case["new"]
            row = inv[key]
            d = AB / f"{n:03d}"
            d.mkdir(exist_ok=True)
            tmp = d / "tmp"
            tmp.mkdir(exist_ok=True)
            ext = lambda name: ".ogg" if name.startswith("ogg") else ".mp3" if name.startswith("mp3") else row["orig"]["ext"] if name in ("orig", "cb") else ".wav"
            files = {}
            for label, name in (("original", "orig"), ("hoje", "cb"), ("novo", new)):
                f = build.build_candidate(row, name, tmp / (label + ext(name)))
                files[label] = f.stat().st_size
                build.decode_pcm16(f, d / f"{label}.wav")
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
            c = dict(res.get(key, {}).get("cands", {}))
            for k2, v in res2.get(key, {}).get("cands", {}).items():
                c[k2] = {**c.get(k2, {}), **v}
            cb, nw = c.get("cb", {}), c.get(new, {})
            o = row["orig"]

            def m(v, k2, fmt="%.1f"):
                return fmt % v[k2] if k2 in v else "–"

            fmt_of = lambda r: f"{r['fmt']} {r['rate']} Hz {'mono' if r['channels'] == 1 else 'estéreo'}"
            table = (f"<table><tr><th></th><th>formato</th><th>tamanho</th><th>NMR</th><th>NMR p95</th><th>SDR</th><th>LSD</th></tr>"
                     f"<tr><td>original</td><td>{fmt_of(o)}</td><td>{kb(o['bytes'])}</td><td></td><td></td><td></td><td></td></tr>"
                     f"<tr><td>hoje</td><td>{fmt_of(row['cb'])}</td><td>{kb(row['cb']['bytes'])}</td><td>{m(cb, 'nmr')}</td>"
                     f"<td>{m(cb, 'nmr95')}</td><td>{m(cb, 'sdr')}</td><td>{m(cb, 'lsd')}</td></tr>"
                     f"<tr><td>novo</td><td>{html.escape(case.get('label', new))}</td><td>{kb(files['novo'])}</td><td>{m(nw, 'nmr')}</td>"
                     f"<td>{m(nw, 'nmr95')}</td><td>{m(nw, 'sdr')}</td><td>{m(nw, 'lsd')}</td></tr></table>")
            loop = " loop" if row["cb"].get("loop") else ""
            body.append(
                f"<div class='case' data-orig='{n:03d}/original.wav' data-hoje='{n:03d}/hoje.wav' data-novo='{n:03d}/novo.wav'>"
                f"<div class='name'>{n}. {html.escape(key)} · {o.get('duration', 0):.2f} s{' · loop' if loop else ''}</div>"
                f"<div class='why'>{html.escape(case.get('why', ''))}</div>{table}"
                f"<div class='row'><label>original</label><audio controls preload='none'{loop} src='{n:03d}/original.wav'></audio>"
                f"<label>hoje</label><audio controls preload='none'{loop} src='{n:03d}/hoje.wav'></audio>"
                f"<label>novo</label><audio controls preload='none'{loop} src='{n:03d}/novo.wav'></audio></div>"
                f"<div class='blind'><b>Teste cego:</b> <button class='bo sec'>original</button> <button class='bx'>X</button> "
                f"<button class='by'>Y</button> <button class='bs sec'>parar</button> <button class='br sec'>Revelar</button>"
                f"<span class='res'></span></div></div>")
            print(n, key, new, files, flush=True)
    (AB / "index.html").write_text(PAGE.replace("__BODY__", "\n".join(body)), encoding="utf-8")
    print("pagina:", AB / "index.html", n, "casos")


if __name__ == "__main__":
    main()
