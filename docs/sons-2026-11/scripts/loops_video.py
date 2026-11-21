"""Vídeo com áudio dos loops MS-ADPCM gravados DENTRO DO JOGO (segunda rodada, game/run/g2): para cada som, o original,
o arquivo com o defeito (como o Custo-benefício gravava) e o corrigido, um depois do outro, com a forma de onda na tela.

python loops_video.py [saida.mp4] -> ab/loops_no_jogo.mp4
O áudio é a gravação do próprio motor, sem edição além de um ganho igual para as três versões de cada som.
"""
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
import game_analyze as ga
from sndlib import LAB

FS = 44100
FPS = 25
W, H = 1280, 720
RUN = LAB / "game" / "run" / "g2"
FFMPEG = "C:/ffmpeg/bin/ffmpeg.exe"

# order: the most obvious first
CASES = [
    ("simfphys/sound/gta4/vehicles/horns/vigero_horn.wav", "Buzina (simfphys, vigero_horn)",
     "Com o defeito: toca 0,22 s e para, em vez de continuar enquanto a buzina está apertada."),
    ("GLIDE/sound/glide/horns/car_horn_med_3.wav", "Buzina (Glide, car_horn_med_3)",
     "Com o defeito: recomeça no ponto errado e gagueja."),
    ("GLIDE/sound/glide/horns/police_horn_2.wav", "Buzina de polícia (Glide, police_horn_2)",
     "Com o defeito: um corte de 83 ms a cada volta."),
    ("simfphys/sound/gta3d/mci102_low.wav", "Motor (simfphys, mci102_low)",
     "Com o defeito: um corte de 45 ms a cada volta (a cada 0,55 s)."),
    ("LVS/sound/lvs/vehicles/bmw_r75/eng_loop.wav", "Motor (LVS, bmw_r75 eng_loop)",
     "Com o defeito: um corte de 45 ms a cada volta (a cada 1,1 s)."),
    ("simfphys/sound/simulated_vehicles/gta5_dukes/dukes_idle.wav", "Motor em marcha lenta (simfphys, dukes_idle)",
     "Com o defeito: um corte de 86 ms na emenda. Na gravação do original entrou outro som do mapa."),
]
VERSIONS = [("orig", "ORIGINAL", "o som do addon sem otimizar", (127, 178, 255)),
            ("hoje", "COM O DEFEITO", "como o otimizador grava hoje", (255, 122, 107)),
            ("novo", "CORRIGIDO", "com a correção dos loops", (111, 208, 140))]
BG, PANEL, TEXT, DIM = (22, 24, 28), (32, 36, 42), (230, 230, 230), (150, 156, 166)
INTRO, TITLE_PAUSE, GAP, OUTRO = 6.0, 1.6, 0.7, 3.0


def font(size, bold=False):
    return ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf", size)


def load_clips():
    plan = json.loads((LAB / "game" / "plan2.json").read_text(encoding="utf-8"))
    meta = {t["file"]: t for t in plan["tests"]}
    slots = {s["id"]: s for s in plan["slots"]}
    x, _ = ga.load_capture(RUN)
    t_sync, _ = ga.find_sync(x.mean(axis=1))
    events = json.loads((RUN / "log.json").read_text(encoding="utf-8"))["events"]
    clips = {}
    for ev in events:
        m = meta.get(ev["file"])
        if not m or m.get("kind") != "realloop" or ev["api"] == "bass":
            continue
        a = int((t_sync + ev["t"] - 0.05) * FS)
        clips.setdefault(m["source"], {})[m["version"]] = np.array(x[a:a + int((slots[ev["id"]]["dur"] + 0.15) * FS)],
                                                                   dtype=np.float64)
    out = []
    for src, title, note in CASES:
        vers = clips[src]
        peak = max(np.abs(v).max() for v in vers.values())
        rms = np.sqrt(np.mean(vers["orig"] ** 2))
        gain = min(0.70 / peak, 10 ** (-15 / 20) / rms)            # the same gain for the three versions
        out.append({"src": src, "title": title, "note": note, "gain": gain,
                    "clips": {k: vers[k] * gain for k, _, _, _ in VERSIONS}})
    return out


def timeline(cases):
    """[(start, end, case index or None, version index or None)] and the audio"""
    parts, marks, t = [np.zeros((int(INTRO * FS), 2))], [(0.0, INTRO, None, None)], INTRO
    for i, c in enumerate(cases):
        parts.append(np.zeros((int(TITLE_PAUSE * FS), 2)))
        marks.append((t, t + TITLE_PAUSE, i, None))
        t += TITLE_PAUSE
        for j, (key, _, _, _) in enumerate(VERSIONS):
            clip = c["clips"][key]
            fade = np.minimum(1.0, np.arange(len(clip))[::-1] / (0.01 * FS))[:, None]      # 10 ms at the cut only
            parts.append(clip * fade)
            marks.append((t, t + len(clip) / FS, i, j))
            t += len(clip) / FS
            parts.append(np.zeros((int(GAP * FS), 2)))
            marks.append((t, t + GAP, i, None))
            t += GAP
    parts.append(np.zeros((int(OUTRO * FS), 2)))
    marks.append((t, t + OUTRO, -1, None))
    return marks, np.concatenate(parts)


ROW_X, ROW_W, ROW_Y, ROW_H, ROW_STEP = 320, 920, 190, 130, 160


def wave_row(draw, mono, y, colour):
    n = len(mono) // ROW_W
    cols = mono[:n * ROW_W].reshape(ROW_W, n)
    lo, hi = cols.min(axis=1), cols.max(axis=1)
    mid, half = y + ROW_H // 2, ROW_H // 2 - 8
    scale = half / 0.75
    for k in range(ROW_W):
        draw.line([(ROW_X + k, mid - min(half, hi[k] * scale)), (ROW_X + k, mid - max(-half, lo[k] * scale))], fill=colour)


def case_frames(i, c, total):
    """one base picture per active row (None = nobody playing)"""
    frames = {}
    for active in (None, 0, 1, 2):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        d.text((40, 28), "%d de %d" % (i + 1, total), font=font(22), fill=DIM)
        d.text((40, 58), c["title"], font=font(38, True), fill=TEXT)
        d.text((40, 116), c["note"], font=font(23), fill=(159, 195, 255))
        for j, (key, label, sub, colour) in enumerate(VERSIONS):
            y = ROW_Y + j * ROW_STEP
            on = active == j
            d.rounded_rectangle([30, y, W - 30, y + ROW_H], radius=12, fill=(44, 50, 60) if on else PANEL,
                                outline=colour if on else None, width=3)
            shade = colour if (on or active is None) else tuple(int(v * 0.45) for v in colour)
            d.text((48, y + 28), label, font=font(24, True), fill=shade)
            d.text((48, y + 66), sub, font=font(17), fill=DIM)
            wave_row(d, c["clips"][key].mean(axis=1), y, shade)
        d.text((40, H - 46), "Gravado dentro do jogo pelo próprio motor (cliente do laboratório), 2,4 s por trecho. "
                             "Mesmo volume nas três versões.", font=font(18), fill=DIM)
        frames[active] = im
    return frames


def intro_frame():
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((60, 110), "Sons em loop: o defeito e a correção", font=font(50, True), fill=TEXT)
    lines = ["6 sons de veículos, gravados dentro do jogo.",
             "Cada som toca três vezes, uma depois da outra:"]
    y = 215
    for s in lines:
        d.text((60, y), s, font=font(30), fill=TEXT)
        y += 48
    y += 14
    for _, label, sub, colour in VERSIONS:
        d.text((90, y), label, font=font(30, True), fill=colour)
        d.text((360, y + 4), sub, font=font(27), fill=DIM)
        y += 56
    d.text((60, y + 34), "Ouça a emenda do loop: no trecho com o defeito o som corta, gagueja ou para.", font=font(27),
           fill=(159, 195, 255))
    return im


def outro_frame():
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((60, 250), "Fim", font=font(50, True), fill=TEXT)
    d.text((60, 340), "O defeito afeta os loops que o otimizador grava em MS-ADPCM: 594 sons na bancada.", font=font(27),
           fill=TEXT)
    d.text((60, 388), "A correção muda só a forma de escrever o arquivo; o som é o mesmo.", font=font(27), fill=TEXT)
    return im


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else LAB / "ab" / "loops_no_jogo.mp4"
    cases = load_clips()
    marks, audio = timeline(cases)
    wav_path = LAB / "work" / "loops_video_audio.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    bases = [case_frames(i, c, len(cases)) for i, c in enumerate(cases)]
    intro, outro = intro_frame(), outro_frame()
    total = len(audio) / FS
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
           "-r", str(FPS), "-i", "-", "-i", str(wav_path), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-shortest", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    k = 0
    for f in range(int(np.ceil(total * FPS))):
        t = f / FPS
        while k + 1 < len(marks) and t >= marks[k][1]:
            k += 1
        a, b, ci, vi = marks[k]
        if ci is None:
            im = intro
        elif ci == -1:
            im = outro
        elif vi is None:
            im = bases[ci][None]
        else:
            im = bases[ci][vi].copy()
            x = ROW_X + int((t - a) / (b - a) * ROW_W)
            y = ROW_Y + vi * ROW_STEP
            ImageDraw.Draw(im).line([(x, y + 6), (x, y + ROW_H - 6)], fill=(255, 255, 255), width=3)
        proc.stdin.write(im.tobytes())
    proc.stdin.close()
    proc.wait()
    print("video:", out, "%.1f s" % total, "%.1f MB" % (out.stat().st_size / 1e6))
    for c in cases:
        print("  %-62s ganho %.2f" % (c["src"][-62:], c["gain"]))


if __name__ == "__main__":
    main()
