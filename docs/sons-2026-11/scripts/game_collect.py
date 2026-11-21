"""Termina uma rodada do teste no jogo cujo game_run.py foi interrompido: espera o fim (data/sonslab/done.txt), fecha
só o jogo dessa rodada, guarda registro + gravação + trecho novo do console e põe o cliente como estava.

python game_collect.py <rodada> <pid do jogo> [espera maxima em s]
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from game_run import ADDON, CAP, CLIENT, GAME
from sndlib import LAB


def alive(pid):
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
    return str(pid) in r.stdout


def main():
    run, pid = sys.argv[1], int(sys.argv[2])
    limit = float(sys.argv[3]) if len(sys.argv) > 3 else 1800.0
    out = LAB / "game" / "run" / run
    addon = GAME / "addons" / ADDON
    data_dir = GAME / "data" / "sonslab"
    cap_files = [GAME / f"{CAP}.wav", GAME / "videos" / f"{CAP}.webm", CLIENT / f"{CAP}.wav"]
    console = GAME / "console.log"
    config = GAME / "cfg" / "config.cfg"
    backup = LAB / "game" / "backup"
    console_size = (backup / "console.log").stat().st_size if (backup / "console.log").exists() else 0
    t0 = time.time()
    done = data_dir / "done.txt"
    while not done.exists() and alive(pid) and time.time() - t0 < limit:
        time.sleep(3)
    time.sleep(3)
    print("done", done.exists(), "jogo ainda aberto", alive(pid), round(time.time() - t0), "s", flush=True)
    try:
        if alive(pid):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            for _ in range(30):
                if not alive(pid):
                    break
                time.sleep(1)
        time.sleep(2)
    finally:
        if data_dir.exists():
            for f in data_dir.iterdir():
                shutil.copy2(f, out / f.name)
        for p in cap_files:
            if p.exists():
                shutil.move(str(p), str(out / p.name))
        if console.exists():
            with open(console, "rb") as fh:
                fh.seek(console_size if console.stat().st_size >= console_size else 0)
                (out / "console_tail.log").write_bytes(fh.read())
        if (backup / "console.log").exists():
            shutil.copy2(backup / "console.log", console)
        shutil.copy2(backup / "config.cfg", config)
        for p in [addon, data_dir]:
            shutil.rmtree(p, ignore_errors=True)
        left = [str(p) for p in [addon, data_dir, *cap_files] if p.exists()]
        print("limpeza: sobrou", left or "nada", flush=True)
        print("saida", out, sorted(f.name for f in out.iterdir()), flush=True)


if __name__ == "__main__":
    main()
