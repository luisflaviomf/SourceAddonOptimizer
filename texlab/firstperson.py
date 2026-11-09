"""Which models of an addon are seen in first person, and from how close.

Weapon view models are drawn from the player's eye with the view-model FOV: the gun sits 4 to 30 units
from the camera, against the 24 units (FOV 75) the screen-aware engines assume as the closest look at a
world model. Measured on the ARC9/M9K test bench (2026-11-08) that means 2 levels more texture detail on
screen and ~5x less geometric tolerance; treated as world models, first-person weapon textures went 8x.

Classes:
- "eye": authored in eye space (the model origin is the eye: SWEP.ViewModel models, v_*/c_* models,
  c_arms/hands bonemerged onto them); the distance of each triangle is its distance to the origin;
- "near": other first-person parts (weapon attachments, shells, magazines under models/weapons/ that are
  not world models w_*): a fixed NEAR_DISTANCE.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

VIEW_FOV_43 = 62.0          # GMod weapon_base SWEP.ViewModelFOV (horizontal, 4:3)
WORLD_FOV_43 = 75.0         # what the world-model camera of the engines assumes
EYE_MIN_DISTANCE = 4.0      # aiming down sights brings the rear sight this close to the eye
NEAR_DISTANCE = 10.0        # attachments and parts not authored in eye space

_VIEWMODEL_LUA = re.compile(r"""ViewModel\s*=\s*["']([^"']+?\.mdl)["']""", re.IGNORECASE)
_HANDS = re.compile(r"(^|/)(c_arms|chands|c_hands|v_hands)|/(arms|hands)/", re.IGNORECASE)


def focal_ratio() -> float:
    """Pixels per unit of the view-model camera over the world-model camera (same screen)."""
    return math.tan(math.radians(WORLD_FOV_43) / 2.0) / math.tan(math.radians(VIEW_FOV_43) / 2.0)


def classify(addon_root: Path) -> dict[str, str]:
    """{model path relative to the addon, lower case: "eye" | "near"} for the first-person models."""
    addon_root = Path(addon_root)
    out: dict[str, str] = {}
    lua = addon_root / "lua"
    if lua.is_dir():
        for f in lua.rglob("*.lua"):
            try:
                text = f.read_text(encoding="latin-1")
            except OSError:
                continue
            if "ViewModel" not in text and "viewmodel" not in text.lower():
                continue
            for m in _VIEWMODEL_LUA.finditer(text):
                out[m.group(1).replace("\\", "/").lower()] = "eye"
    models = addon_root / "models"
    if models.is_dir():
        for mdl in models.rglob("*.mdl"):
            rel = mdl.relative_to(addon_root).as_posix().lower()
            if rel in out:
                continue
            # name rules only inside models/weapons/ (vehicles and props have v_/c_ names too)
            if not rel.startswith("models/weapons/"):
                continue
            base = rel.rsplit("/", 1)[-1]
            if base.startswith("w_"):
                continue
            out[rel] = "eye" if base.startswith(("v_", "c_")) or _HANDS.search(rel) else "near"
    return out


def distances(kind: str, centroids) -> "np.ndarray":
    """Camera distance per triangle for a first-person model of class `kind`."""
    import numpy as np
    c = np.asarray(centroids, np.float64)
    if kind == "eye":
        return np.maximum(np.linalg.norm(c, axis=1), EYE_MIN_DISTANCE)
    return np.full(len(c), NEAR_DISTANCE)
