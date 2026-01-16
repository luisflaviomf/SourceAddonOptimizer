import os
import re
import shutil
from pathlib import Path


def _first_existing(paths):
    for p in paths:
        if not p:
            continue
        path = Path(p)
        if path.exists():
            return path
    return None


def detect_blender() -> Path | None:
    env = _first_existing(
        [
            os.environ.get("BLENDER_EXE"),
            os.environ.get("BLENDER_PATH"),
            os.environ.get("BLENDER"),
        ]
    )
    if env:
        return env.resolve()

    which = shutil.which("blender") or shutil.which("blender.exe")  # type: ignore[name-defined]
    if which:
        return Path(which).resolve()

    base = Path(r"C:\Program Files\Blender Foundation")
    if base.exists():
        candidates = []
        for d in base.iterdir():
            if not d.is_dir():
                continue
            if not d.name.lower().startswith("blender"):
                continue
            exe = d / "blender.exe"
            if exe.exists():
                candidates.append(exe)
        if candidates:
            # Prefer highest version by name.
            def _ver_key(p: Path):
                m = re.search(r"blender\s*([0-9]+(?:\.[0-9]+)*)", p.parent.name.lower())
                if m:
                    parts = m.group(1).split(".")
                    return tuple(int(x) for x in parts)
                return (0,)

            candidates.sort(key=_ver_key, reverse=True)
            return candidates[0].resolve()

    return None


def _steam_root_candidates():
    env_pf86 = os.environ.get("PROGRAMFILES(X86)")
    env_pf = os.environ.get("PROGRAMFILES")
    candidates = [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ]
    if env_pf86:
        candidates.append(Path(env_pf86) / "Steam")
    if env_pf:
        candidates.append(Path(env_pf) / "Steam")
    return [p for p in candidates if p.exists()]


def _parse_libraryfolders(vdf_path: Path):
    libs = []
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return libs

    for line in text.splitlines():
        m = re.search(r'"path"\s+"([^"]+)"', line)
        if m:
            libs.append(m.group(1).replace("\\\\", "\\"))
            continue
        m = re.match(r'\s*"\d+"\s+"([^"]+)"', line)
        if m:
            libs.append(m.group(1).replace("\\\\", "\\"))
            continue
    return libs


def _find_studiomdl_in_root(root: Path) -> Path | None:
    cand = root / "bin" / "studiomdl.exe"
    return cand if cand.exists() else None


def detect_studiomdl() -> Path | None:
    env = _first_existing(
        [
            os.environ.get("STUDIOMDL_EXE"),
            os.environ.get("STUDIOMDL_PATH"),
        ]
    )
    if env:
        return env.resolve()

    gmod_env = os.environ.get("GARRYSMOD_DIR")
    if gmod_env:
        p = _find_studiomdl_in_root(Path(gmod_env))
        if p:
            return p.resolve()

    default = Path(
        r"C:\Program Files (x86)\Steam\steamapps\common\GarrysMod\bin\studiomdl.exe"
    )
    if default.exists():
        return default.resolve()

    for root in _steam_root_candidates():
        vdf = root / "steamapps" / "libraryfolders.vdf"
        libs = _parse_libraryfolders(vdf) if vdf.exists() else []
        for lib in libs:
            gmod = Path(lib) / "steamapps" / "common" / "GarrysMod"
            p = _find_studiomdl_in_root(gmod)
            if p:
                return p.resolve()

        # Also check the Steam root library itself.
        gmod = root / "steamapps" / "common" / "GarrysMod"
        p = _find_studiomdl_in_root(gmod)
        if p:
            return p.resolve()

    return None
