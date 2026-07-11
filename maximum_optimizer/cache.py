from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class CacheKey:
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.digest, str) or _SHA256_RE.fullmatch(self.digest) is None:
            raise ValueError("digest must be a lowercase SHA-256 hex digest")

    @classmethod
    def build(
        cls,
        input_hash: str,
        candidate: dict[str, Any],
        tools: dict[str, Any],
        profile_version: str,
    ) -> CacheKey:
        payload = {
            "input": input_hash,
            "candidate": candidate,
            "tools": tools,
            "profile": profile_version,
        }
        try:
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise TypeError("cache key payload is not JSON serializable") from exc
        return cls(hashlib.sha256(raw.encode("utf-8")).hexdigest())


def _write_json(path: Path, payload: object) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())


def _device_id(path: Path) -> int:
    return os.stat(path).st_dev


def _unique_sibling(parent: Path, prefix: str) -> Path:
    while True:
        candidate = parent / f"{prefix}{uuid.uuid4().hex}"
        if not candidate.exists():
            return candidate


def _require_direct_child(path: Path, parent: Path) -> None:
    resolved_parent = parent.resolve(strict=True)
    if path.parent.resolve(strict=True) != resolved_parent:
        raise ValueError(f"refusing to remove path outside {parent}")


def _remove_direct_child(path: Path, parent: Path) -> None:
    _require_direct_child(path, parent)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


class CandidateCache:
    def __init__(self, root: os.PathLike[str] | str):
        self.root = Path(root)

    def lookup(self, key: CacheKey) -> Path | None:
        final = self.root / key.digest
        if not final.is_dir():
            return None
        marker = final / "complete.json"
        payload = final / "payload"
        if not payload.is_dir():
            return None
        try:
            complete = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(complete, dict) or complete.get("digest") != key.digest:
            return None
        return final

    def store(
        self,
        key: CacheKey,
        source_dir: os.PathLike[str] | str,
        metadata: object,
    ) -> Path:
        source = Path(source_dir)
        if not source.is_dir():
            raise ValueError(f"source_dir must be an existing directory: {source}")

        self.root.mkdir(parents=True, exist_ok=True)
        final = self.root / key.digest
        existing = self.lookup(key)
        if existing is not None:
            return existing

        staging = _unique_sibling(
            self.root,
            f"{key.digest}.tmp-{os.getpid()}-",
        )
        staging.mkdir()
        shutil.copytree(source, staging / "payload")
        _write_json(staging / "metadata.json", metadata)
        _write_json(staging / "complete.json", {"digest": key.digest})

        quarantine: Path | None = None
        if final.exists() or final.is_symlink():
            quarantine = _unique_sibling(
                self.root,
                f"{key.digest}.quarantine-",
            )
            os.replace(final, quarantine)

        try:
            os.replace(staging, final)
        except BaseException:
            if quarantine is not None and quarantine.exists():
                if final.exists() or final.is_symlink():
                    _remove_direct_child(final, self.root)
                os.replace(quarantine, final)
            raise

        if quarantine is not None:
            _remove_direct_child(quarantine, self.root)
        return final

    def cleanup_incomplete(self) -> int:
        if not self.root.is_dir():
            return 0
        removed = 0
        for child in self.root.iterdir():
            if ".tmp-" not in child.name or not child.is_dir() or child.is_symlink():
                continue
            _remove_direct_child(child, self.root)
            removed += 1
        return removed


def atomic_replace_tree(
    staging: os.PathLike[str] | str,
    destination: os.PathLike[str] | str,
    *,
    replace: Callable[[os.PathLike[str] | str, os.PathLike[str] | str], None] = os.replace,
) -> Path:
    staging_path = Path(staging)
    destination_path = Path(destination)

    if staging_path.resolve(strict=False) == destination_path.resolve(strict=False):
        raise ValueError("staging and destination must be different paths")
    if not staging_path.is_dir():
        raise ValueError(f"staging must be an existing directory: {staging_path}")
    if not destination_path.parent.is_dir():
        raise ValueError(
            f"destination parent must be an existing directory: {destination_path.parent}"
        )
    if destination_path.exists() and not destination_path.is_dir():
        raise ValueError(f"destination must be a directory when it exists: {destination_path}")
    if _device_id(staging_path) != _device_id(destination_path.parent):
        raise ValueError("staging and destination must be on the same volume")

    backup: Path | None = None
    if destination_path.exists():
        backup = _unique_sibling(
            destination_path.parent,
            f"{destination_path.name}.backup-",
        )
        replace(destination_path, backup)

    try:
        replace(staging_path, destination_path)
    except BaseException:
        if backup is not None and backup.exists():
            if destination_path.exists() or destination_path.is_symlink():
                _remove_direct_child(destination_path, destination_path.parent)
            replace(backup, destination_path)
        raise

    if backup is not None:
        _remove_direct_child(backup, destination_path.parent)
    return destination_path
