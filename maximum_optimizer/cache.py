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


class AtomicReplaceError(RuntimeError):
    def __init__(
        self,
        destination: Path,
        recovery_path: Path,
        promotion_error: BaseException,
        restore_error: BaseException,
    ) -> None:
        self.destination = destination
        self.recovery_path = recovery_path
        self.promotion_error = promotion_error
        self.restore_error = restore_error
        super().__init__(
            f"promotion of {destination} failed and rollback also failed; "
            f"original data remains available at recovery path {recovery_path}"
        )


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


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _unique_sibling(parent: Path, prefix: str) -> Path:
    while True:
        candidate = parent / f"{prefix}{uuid.uuid4().hex}"
        if not _lexists(candidate):
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


def _raise_if_cache_symlink(final: Path) -> None:
    if final.is_symlink():
        raise ValueError(f"cache final must not be a symlink: {final}")
    if not final.is_dir():
        return
    for child in (final / "payload", final / "complete.json"):
        if child.is_symlink():
            raise ValueError(f"cache entry must not contain a symlink: {child}")


class CandidateCache:
    def __init__(self, root: os.PathLike[str] | str):
        self.root = Path(root)

    def lookup(self, key: CacheKey) -> Path | None:
        final = self.root / key.digest
        if final.is_symlink() or not final.is_dir():
            return None
        marker = final / "complete.json"
        payload = final / "payload"
        if payload.is_symlink() or not payload.is_dir() or marker.is_symlink():
            return None
        try:
            complete = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(complete, dict) or complete.get("digest") != key.digest:
            return None
        self._cleanup_quarantines(key)
        return final

    def _cleanup_quarantines(self, key: CacheKey) -> None:
        prefix = f"{key.digest}.quarantine-"
        try:
            children = tuple(self.root.iterdir())
        except OSError:
            return
        for child in children:
            if not child.name.startswith(prefix) or child.is_symlink():
                continue
            try:
                _require_direct_child(child, self.root)
                if _lexists(child):
                    _remove_direct_child(child, self.root)
            except (OSError, ValueError):
                continue

    def store(
        self,
        key: CacheKey,
        source_dir: os.PathLike[str] | str,
        metadata: object,
    ) -> Path:
        source = Path(source_dir)
        if source.is_symlink():
            raise ValueError(f"source_dir must not be a symlink: {source}")
        if not source.is_dir():
            raise ValueError(f"source_dir must be an existing directory: {source}")

        self.root.mkdir(parents=True, exist_ok=True)
        final = self.root / key.digest
        existing = self.lookup(key)
        if existing is not None:
            return existing
        _raise_if_cache_symlink(final)

        staging = _unique_sibling(
            self.root,
            f"{key.digest}.tmp-{os.getpid()}-",
        )
        staging.mkdir()
        shutil.copytree(source, staging / "payload")
        _write_json(staging / "metadata.json", metadata)
        _write_json(staging / "complete.json", {"digest": key.digest})

        quarantine: Path | None = None
        if _lexists(final):
            quarantine = _unique_sibling(
                self.root,
                f"{key.digest}.quarantine-",
            )
            os.replace(final, quarantine)

        try:
            os.replace(staging, final)
        except BaseException as promotion_error:
            if quarantine is not None:
                try:
                    if _lexists(final):
                        _remove_direct_child(final, self.root)
                    os.replace(quarantine, final)
                except BaseException as restore_error:
                    raise AtomicReplaceError(
                        final,
                        quarantine,
                        promotion_error,
                        restore_error,
                    ) from restore_error
            raise

        if quarantine is not None:
            try:
                _remove_direct_child(quarantine, self.root)
            except OSError:
                pass
        return final

    def cleanup_incomplete(self) -> int:
        if not self.root.is_dir():
            return 0
        removed = 0
        for child in self.root.iterdir():
            if ".tmp-" not in child.name or child.is_symlink() or not child.is_dir():
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
    """Promote staging, succeeding even if a residual backup cannot be removed."""
    staging_path = Path(staging)
    destination_path = Path(destination)

    if staging_path.is_symlink():
        raise ValueError(f"staging must not be a symlink: {staging_path}")
    if destination_path.is_symlink():
        raise ValueError(f"destination must not be a symlink: {destination_path}")
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
    except BaseException as promotion_error:
        if backup is not None:
            try:
                if _lexists(destination_path):
                    _remove_direct_child(destination_path, destination_path.parent)
                replace(backup, destination_path)
            except BaseException as restore_error:
                raise AtomicReplaceError(
                    destination_path,
                    backup,
                    promotion_error,
                    restore_error,
                ) from restore_error
        raise

    if backup is not None:
        try:
            _remove_direct_child(backup, destination_path.parent)
        except OSError:
            pass
    return destination_path
