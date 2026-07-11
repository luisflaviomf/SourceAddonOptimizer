from __future__ import annotations

import re
from typing import Iterable


_REGION_KEY_RE = re.compile(r"^[^|/]+\|[^|/]+\|(?:0|[1-9][0-9]*)$")


def normalized_region_name(name: str) -> str:
    normalized = re.sub(r"\.\d{3}$", "", name.strip(), flags=re.IGNORECASE)
    normalized = re.sub(r"_OPT$", "", normalized, flags=re.IGNORECASE)
    return normalized.casefold()


def blender_suffix_number(name: str) -> int:
    match = re.search(r"\.(\d{3})$", name.strip())
    return int(match.group(1)) if match else 0


def region_keys(
    descriptions: Iterable[tuple[str, tuple[str, ...]]],
) -> dict[tuple[str, tuple[str, ...]], str]:
    descriptions = list(descriptions)
    if len(set(descriptions)) != len(descriptions):
        raise ValueError("ambiguous duplicate region description")
    grouped: dict[tuple[str, str], list[tuple[str, tuple[str, ...]]]] = {}
    for description in descriptions:
        name, materials = description
        signature = "+".join(material.casefold() for material in materials) or "none"
        grouped.setdefault((normalized_region_name(name), signature), []).append(description)
    result: dict[tuple[str, tuple[str, ...]], str] = {}
    for (base, signature), members in sorted(grouped.items()):
        for ordinal, description in enumerate(
            sorted(
                members,
                key=lambda item: (
                    blender_suffix_number(item[0]),
                    item[0].casefold(),
                    item[0],
                    item[1],
                ),
            )
        ):
            result[description] = f"{base}|{signature}|{ordinal}"
    if len(result) != len(descriptions):
        raise ValueError("ambiguous region key collision")
    return result


def parse_region_scope(scope: str) -> tuple[str, str] | None:
    if scope.count("/") != 1:
        return None
    region_key, pose = scope.rsplit("/", 1)
    if not _REGION_KEY_RE.fullmatch(region_key) or not re.fullmatch(r"[A-Za-z0-9_.-]+", pose):
        return None
    return region_key, pose


def is_region_key(value: str) -> bool:
    return _REGION_KEY_RE.fullmatch(value) is not None
