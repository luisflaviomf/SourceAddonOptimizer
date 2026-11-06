"""Run the texlab in-game probe on the isolated lab client (single player, gm_construct).

The variant's models/materials and the LVS framework's are hard-linked into
temporary `zz_texlab_*` addon folders of the lab client, the probe captures PNGs
and a material report into garrysmod/data/texlab/<run>, then the folders are removed.
Nothing in the real Garry's Mod install is touched.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

CLIENT = Path(r"C:/GModLab/runtime/client-install")
FRAMEWORK = Path(r"C:/WorkshopDL/steamcmd/steamapps/workshop/content/4000/LVS/lvs_framework")
PROBE_TEMPLATE = Path(__file__).with_name("texlab_probe.lua.in")
PREFIX = "zz_texlab_"


def _link_tree(src: Path, dst: Path, subdirs=("materials", "models")) -> int:
    n = 0
    for sub in subdirs:
        base = src / sub
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                target = dst / p.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(p, target)
                n += 1
    return n


def _lua_models(models: list[dict]) -> str:
    items = []
    for m in models:
        color = "nil" if not m.get("color") else "{%d,%d,%d}" % tuple(m["color"])
        items.append('{mdl = "%s", skin = %d, color = %s, tag = "%s"}' % (m["mdl"], m.get("skin", 0), color, m["tag"]))
    return "{\n    " + ",\n    ".join(items) + "\n}"


def run(variant_root: Path, run_name: str, models: list[dict], out_dir: Path, timeout: int = 900,
        convars: tuple = ()) -> dict:
    addons = CLIENT / "garrysmod" / "addons"
    addons.mkdir(exist_ok=True)
    for old in addons.glob(PREFIX + "*"):
        shutil.rmtree(old)
    data_dir = CLIENT / "garrysmod" / "data" / "texlab" / run_name
    if data_dir.exists():
        shutil.rmtree(data_dir)
    valve_rc = CLIENT / "garrysmod" / "cfg" / "valve.rc"
    created_rc = False
    if not valve_rc.exists():
        # Source only executes +map from the command line through `stuffcmds` in valve.rc.
        valve_rc.parent.mkdir(parents=True, exist_ok=True)
        valve_rc.write_text("stuffcmds" + chr(10), encoding="ascii")
        created_rc = True
    try:
        _link_tree(FRAMEWORK, addons / (PREFIX + "framework"))
        _link_tree(Path(variant_root), addons / (PREFIX + "variant"))
        probe = addons / (PREFIX + "probe") / "lua" / "autorun" / "client" / "texlab_probe.lua"
        probe.parent.mkdir(parents=True, exist_ok=True)
        lua = PROBE_TEMPLATE.read_text(encoding="utf-8").replace("__MODELS__", _lua_models(models)).replace("__RUN__", run_name)
        probe.write_text(lua, encoding="utf-8")
        args = [str(CLIENT / "bin" / "win64" / "gmod.exe"), "-multirun", "-allowquit", "-windowed", "-noborder",
                "-x", "32000", "-y", "32000", "-w", "1600", "-h", "900", "-noworkshop", "-nomouse", "-novid",
                "-condebug", "+engine_no_focus_sleep", "0", "+mat_picmip", "0", *convars, "+map", "gm_construct"]
        t0 = time.time()
        proc = subprocess.Popen(args, cwd=str(CLIENT))
        report_path = data_dir / "report.json"
        try:
            while not report_path.exists():
                if proc.poll() is not None:
                    break
                if time.time() - t0 > timeout:
                    raise TimeoutError("probe timed out")
                time.sleep(2)
            time.sleep(2)
        finally:
            # Lua cannot run "quit": close every process of the lab client (and only it).
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"Name='gmod.exe'\" | Where-Object { $_.ExecutablePath -like '"
                            + str(CLIENT).replace("/", "\\") + "\\*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                           capture_output=True)
            proc.wait(timeout=60)
        if not report_path.exists():
            raise RuntimeError(f"probe produced no report (exit {proc.returncode}); see {CLIENT / 'garrysmod' / 'console.log'}")
        out_dir = Path(out_dir) / run_name
        if out_dir.exists():
            shutil.rmtree(out_dir)
        shutil.copytree(data_dir, out_dir)
        console = CLIENT / "garrysmod" / "console.log"
        if console.exists():
            shutil.copy2(console, out_dir / "console.log")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["seconds"] = time.time() - t0
        return report
    finally:
        for old in addons.glob(PREFIX + "*"):
            shutil.rmtree(old, ignore_errors=True)
        if created_rc:
            valve_rc.unlink(missing_ok=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--models", required=True, help="JSON file with the model list")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    models = json.loads(Path(args.models).read_text())
    report = run(Path(args.variant), args.run, models, Path(args.out))
    bad = [(m["mdl"], mt["name"]) for m in report["models"] for mt in m.get("materials", [])
           if mt.get("error") or (mt.get("base") or {}).get("error")]
    print(json.dumps({"models": len(report["models"]), "error_materials": bad[:20],
                      "seconds": round(report["seconds"])}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
