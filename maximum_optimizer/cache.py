from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from .contracts import RegionKey
from .regions import SmdRegion
from .smd import SmdInfluence, SmdTriangle, SmdVertex


_KEY = re.compile(r"^[0-9a-f]{64}$")


def _vertex_payload(vertex: SmdVertex) -> dict[str, object]:
    return {
        "primary_bone": vertex.primary_bone,
        "position": list(vertex.position),
        "normal": list(vertex.normal),
        "uv": list(vertex.uv),
        "influences": [[value.bone, value.weight] for value in vertex.influences],
    }


def _region_payload(region: SmdRegion) -> dict[str, object]:
    return {
        "key": region.key.value,
        "material": region.material,
        "local_ordinal": region.local_ordinal,
        "triangle_ordinals": list(region.triangle_ordinals),
        "centroid": list(region.centroid),
        "bounds_min": list(region.bounds_min),
        "bounds_max": list(region.bounds_max),
        "bone_ids": list(region.bone_ids),
        "triangles": [
            {
                "material": triangle.material,
                "source_ordinal": triangle.source_ordinal,
                "vertices": [_vertex_payload(vertex) for vertex in triangle.vertices],
            }
            for triangle in region.triangles
        ],
    }


def _vector(values: object, width: int, label: str) -> tuple[float, ...]:
    if type(values) is not list or len(values) != width:
        raise ValueError(f"cached {label} is invalid")
    result = tuple(float(value) for value in values)
    return result


def _region_from_payload(payload: object) -> SmdRegion:
    fields = {
        "key", "material", "local_ordinal", "triangle_ordinals", "centroid",
        "bounds_min", "bounds_max", "bone_ids", "triangles",
    }
    if type(payload) is not dict or set(payload) != fields:
        raise ValueError("cached region fields are invalid")
    triangles_raw = payload["triangles"]
    if type(triangles_raw) is not list or not triangles_raw:
        raise ValueError("cached triangles are invalid")
    triangles = []
    for triangle_raw in triangles_raw:
        if type(triangle_raw) is not dict or set(triangle_raw) != {"material", "source_ordinal", "vertices"}:
            raise ValueError("cached triangle fields are invalid")
        vertices_raw = triangle_raw["vertices"]
        if type(vertices_raw) is not list or len(vertices_raw) != 3:
            raise ValueError("cached triangle vertices are invalid")
        vertices = []
        for vertex_raw in vertices_raw:
            if type(vertex_raw) is not dict or set(vertex_raw) != {"primary_bone", "position", "normal", "uv", "influences"}:
                raise ValueError("cached vertex fields are invalid")
            influences_raw = vertex_raw["influences"]
            if type(influences_raw) is not list or not influences_raw:
                raise ValueError("cached influences are invalid")
            influences = tuple(
                SmdInfluence(int(value[0]), float(value[1]))
                for value in influences_raw
                if type(value) is list and len(value) == 2
            )
            if len(influences) != len(influences_raw):
                raise ValueError("cached influence row is invalid")
            vertices.append(
                SmdVertex(
                    int(vertex_raw["primary_bone"]),
                    _vector(vertex_raw["position"], 3, "position"),  # type: ignore[arg-type]
                    _vector(vertex_raw["normal"], 3, "normal"),  # type: ignore[arg-type]
                    _vector(vertex_raw["uv"], 2, "UV"),  # type: ignore[arg-type]
                    influences,
                )
            )
        triangles.append(
            SmdTriangle(
                str(triangle_raw["material"]),
                tuple(vertices),  # type: ignore[arg-type]
                int(triangle_raw["source_ordinal"]),
            )
        )
    return SmdRegion(
        RegionKey(str(payload["key"])),
        str(payload["material"]),
        int(payload["local_ordinal"]),
        tuple(int(value) for value in payload["triangle_ordinals"]),
        tuple(triangles),
        _vector(payload["centroid"], 3, "centroid"),  # type: ignore[arg-type]
        _vector(payload["bounds_min"], 3, "bounds minimum"),  # type: ignore[arg-type]
        _vector(payload["bounds_max"], 3, "bounds maximum"),  # type: ignore[arg-type]
        tuple(int(value) for value in payload["bone_ids"]),
    )


class FileRegionCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if type(key) is not str or _KEY.fullmatch(key) is None:
            raise ValueError("cache key must be a lowercase SHA-256")
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> SmdRegion | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if type(payload) is not dict or set(payload) != {"schema", "cache_key", "region", "payload_sha256"}:
                return None
            region_bytes = json.dumps(payload["region"], ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
            if payload["schema"] != 1 or payload["cache_key"] != key or payload["payload_sha256"] != hashlib.sha256(region_bytes).hexdigest():
                return None
            return _region_from_payload(payload["region"])
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def put(self, key: str, region: SmdRegion) -> None:
        path = self._path(key)
        region_payload = _region_payload(region)
        region_bytes = json.dumps(region_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        payload = {
            "schema": 1,
            "cache_key": key,
            "region": region_payload,
            "payload_sha256": hashlib.sha256(region_bytes).hexdigest(),
        }
        encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temporary.write_bytes(encoded)
        os.replace(temporary, path)
