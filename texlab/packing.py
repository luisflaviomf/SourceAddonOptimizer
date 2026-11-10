"""What an addon's addon.json says about packing: the "ignore" list of the Workshop packer (gmad) and
the "merged_from" list of the addon merge.

The streams (model_stream, texture_stream) add data files to the addon. An addon whose addon.json says
"ignore": ["*.txt"] (read-me files; seen in the 2026-11-09 multiplayer test, ARC9 Gunsmith Arms Source)
was packed without them: the game then draws the coarse stand-in meshes and the 16 px placeholder
textures. So the stream files take a data_static file type that the addon's own list does not match.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

# data_static file types gmad accepts, in order of preference. ".dat" first: "*.txt" is a usual ignore
# pattern, and an addon.json written after the optimization (extracted .gma) cannot be read here
STREAM_EXTENSIONS = (".dat", ".txt", ".json", ".xml", ".csv")


def ignore_patterns(addon_root) -> tuple:
    """The "ignore" patterns of <addon>/addon.json (none when the file is missing or broken)."""
    try:
        meta = json.loads((Path(addon_root) / "addon.json").read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError):
        return ()
    ignore = meta.get("ignore") if isinstance(meta, dict) else None
    return tuple(p for p in ignore if isinstance(p, str)) if isinstance(ignore, list) else ()


def ignored(rel: str, patterns) -> bool:
    """gmad's test: every pattern against the whole path; '*' is any run of characters, folders
    included, '?' is one character. Case is ignored here (the stricter reading)."""
    rel = rel.replace("\\", "/").lower()
    for pattern in patterns:
        rx = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern.lower())
        if re.fullmatch(rx, rel, re.S):
            return True
    return False


@lru_cache(maxsize=None)
def stream_extension(addon_root: str, folder: str) -> tuple:
    """(extension, packed) for the stream files in <folder>/ of this addon: the first file type that its
    addon.json keeps. packed is False when the list leaves all of them out (the caller warns)."""
    patterns = ignore_patterns(addon_root)
    for ext in STREAM_EXTENSIONS:
        if not ignored(f"{folder}/0123456789abcdef{ext}", patterns):
            return ext, True
    return STREAM_EXTENSIONS[0], False


def merged_sources(addon_root) -> frozenset:
    """Lower-case folder names this addon was merged from (addon.json "merged_from", written by
    batch_merge_addons); empty for any other addon."""
    try:
        meta = json.loads((Path(addon_root) / "addon.json").read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError):
        return frozenset()
    names = meta.get("merged_from") if isinstance(meta, dict) else None
    return frozenset(n.lower() for n in names if isinstance(n, str) and n) if isinstance(names, list) else frozenset()


def copies_of_each_other(addon_root, other) -> bool:
    """A merged addon beside the folders it was made from (the merge writes next to its sources by
    default): they hold the same files, so neither is a neighbour of the other, nor of the other's
    optimized output ("<name><suffix>"). Looking there only measures every model twice."""
    a, b = Path(addon_root), Path(other)
    an, bn = a.name.lower(), b.name.lower()
    return any(bn.startswith(s) for s in merged_sources(a)) or any(an.startswith(s) for s in merged_sources(b))


IGNORED_WARNING = ('[WARN] addon.json "ignore" leaves the stream files ({folder}) out of the published addon: '
                   "remove that pattern before publishing")
