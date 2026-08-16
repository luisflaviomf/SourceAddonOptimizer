from __future__ import annotations

import hashlib
import json
from pathlib import Path


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_bundle(root: Path, chunks: list[dict[str, object]]) -> Path:
    root.mkdir(parents=True)
    declared: list[dict[str, object]] = []
    for sequence, chunk in enumerate(chunks, start=1):
        name = f"chunk-{sequence:04d}.json"
        raw = json_bytes(chunk)
        (root / name).write_bytes(raw)
        declared.append(
            {
                "sequence": sequence,
                "file": name,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    first = chunks[0]
    index = {
        "schema": "sao-run-index-v1",
        "contract": first["contract"],
        "run_id": first["run_id"],
        "payload_schema": first["schema"],
        "created_utc": first["created_utc"],
        "producer": first["producer"],
        "environment": first["environment"],
        "chunk_count": len(chunks),
        "total_bytes": sum(int(item["bytes"]) for item in declared),
        "chunks": declared,
    }
    index_raw = json_bytes(index)
    (root / "run-index.json").write_bytes(index_raw)
    ready = {
        "schema": "sao-completion-v1",
        "run_id": first["run_id"],
        "index_sha256": hashlib.sha256(index_raw).hexdigest(),
    }
    (root / "complete.ready").write_bytes(json_bytes(ready))
    return root
