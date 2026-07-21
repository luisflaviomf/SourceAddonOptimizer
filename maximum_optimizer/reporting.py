from __future__ import annotations

from pathlib import Path

from .contracts import SizeAccounting


def comparable_model_bytes(root: Path) -> SizeAccounting:
    comparable = 0
    dx80 = 0
    for path in sorted(Path(root).rglob("*"), key=lambda value: value.as_posix().casefold()):
        if not path.is_file():
            continue
        size = path.stat().st_size
        if path.name.casefold().endswith(".dx80.vtx"):
            dx80 += size
        else:
            comparable += size
    return SizeAccounting(comparable, dx80)


def reduction_percent(original: SizeAccounting, final: SizeAccounting) -> float:
    if original.comparable_bytes <= 0:
        return 0.0
    return (original.comparable_bytes - final.comparable_bytes) * 100.0 / original.comparable_bytes
