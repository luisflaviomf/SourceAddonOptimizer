from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


def deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(item) for item in value)
    return value


def canonical_payload(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: canonical_payload(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): canonical_payload(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [canonical_payload(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((canonical_payload(item) for item in value), key=repr)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON payload contains a non-finite number")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported JSON payload value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_payload(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def atomic_write_json(path: Path, value: Any) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.tmp-", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(value))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except (AttributeError, OSError):
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return destination


def event_line(event: Mapping[str, Any]) -> str:
    return "MAXIMUM_EVENT " + canonical_json(event)
