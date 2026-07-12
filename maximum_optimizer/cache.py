from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_VALIDATED_LOCK_GUARD = threading.Lock()
_VALIDATED_LOCKS: dict[str, tuple[threading.RLock, int]] = {}


@contextmanager
def _validated_key_lock(digest: str):
    with _VALIDATED_LOCK_GUARD:
        lock, users = _VALIDATED_LOCKS.get(digest, (threading.RLock(), 0))
        _VALIDATED_LOCKS[digest] = (lock, users + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _VALIDATED_LOCK_GUARD:
            current_lock, users = _VALIDATED_LOCKS[digest]
            if current_lock is lock and users == 1:
                del _VALIDATED_LOCKS[digest]
            else:
                _VALIDATED_LOCKS[digest] = (current_lock, users - 1)


@contextmanager
def _cross_process_cache_lock(
    root: Path, digest: str, cancel_check: Callable[[], None],
):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        name = _validated_mutex_name(root, digest)
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "cannot create recovery cache mutex")
        acquired = False
        try:
            while not acquired:
                cancel_check()
                result = kernel32.WaitForSingleObject(handle, 30_000)
                if result in {0x00000000, 0x00000080}:
                    acquired = True
                elif result == 0x00000102:
                    continue
                elif result == 0xFFFFFFFF:
                    raise OSError(
                        ctypes.get_last_error(),
                        "failed acquiring recovery cache mutex",
                    )
                else:
                    raise OSError(result, "cannot acquire recovery cache mutex")
            yield
        finally:
            if acquired:
                kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        return
    import fcntl

    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".validated-cache.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _validated_mutex_name(root: Path, digest: str) -> str:
    root_identity = hashlib.sha256(
        (str(root.resolve()).casefold() + "\0" + digest).encode("utf-8")
    ).hexdigest()
    return "Local\\GmodAddonOptimizer-RecoveryCache-" + root_identity


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


def _flush_directory(path: Path) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
        kernel32.FlushFileBuffers.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateFileW(
            str(path), 0x80000000, 0x00000001 | 0x00000002 | 0x00000004,
            None, 3, 0x02000000, None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "cannot open directory for flush")
        try:
            if not kernel32.FlushFileBuffers(handle):
                # Windows does not support FlushFileBuffers for directory
                # handles on all filesystems. The final MoveFileExW below uses
                # WRITE_THROUGH, which is the durable directory-entry barrier.
                pass
        finally:
            kernel32.CloseHandle(handle)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _flush_tree(root: Path) -> None:
    directories: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        directories.append(parent)
        for name in directory_names:
            if _is_symlink(parent / name):
                raise ValueError("validated cache tree contains a symlink")
        for name in file_names:
            path = parent / name
            if _is_symlink(path):
                raise ValueError("validated cache tree contains a symlink")
            original_mode = path.stat().st_mode
            made_writable = os.name == "nt" and not (original_mode & stat.S_IWRITE)
            if made_writable:
                path.chmod(original_mode | stat.S_IWRITE)
            try:
                descriptor = os.open(
                    path, os.O_RDWR if os.name == "nt" else os.O_RDONLY
                )
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                if made_writable:
                    path.chmod(original_mode)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _flush_directory(directory)


def _durable_replace(source: Path, destination: Path) -> None:
    if os.name != "nt":
        os.replace(source, destination)
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.MoveFileExW.argtypes = (
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
    )
    kernel32.MoveFileExW.restype = ctypes.c_int
    MOVEFILE_REPLACE_EXISTING = 0x1
    MOVEFILE_WRITE_THROUGH = 0x8
    if not kernel32.MoveFileExW(
        str(source), str(destination),
        MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
    ):
        raise OSError(ctypes.get_last_error(), "durable cache rename failed")


def _device_id(path: Path) -> int:
    return os.stat(path).st_dev


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _is_symlink(path: Path) -> bool:
    return path.is_symlink()


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
    if _is_symlink(path):
        raise ValueError(f"refusing to remove symlink: {path}")
    if path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _raise_if_cache_symlink(final: Path) -> None:
    if _is_symlink(final):
        raise ValueError(f"cache final must not be a symlink: {final}")
    if not final.is_dir():
        return
    for child in (final / "payload", final / "complete.json"):
        if _is_symlink(child):
            raise ValueError(f"cache entry must not contain a symlink: {child}")


def _cleanup_after_commit(
    residue: Path,
    parent: Path,
    pending_prefix: str,
    replace: Callable[[os.PathLike[str] | str, os.PathLike[str] | str], None],
) -> None:
    try:
        _require_direct_child(residue, parent)
        pending = _unique_sibling(parent, pending_prefix)
        _require_direct_child(pending, parent)
        replace(residue, pending)
    except (OSError, ValueError):
        return
    try:
        _remove_direct_child(pending, parent)
    except (OSError, ValueError):
        return


class CandidateCache:
    def __init__(self, root: os.PathLike[str] | str):
        self.root = Path(root)

    def lookup(self, key: CacheKey) -> Path | None:
        final = self.root / key.digest
        if _is_symlink(final) or not final.is_dir():
            return None
        marker = final / "complete.json"
        payload = final / "payload"
        if _is_symlink(payload) or not payload.is_dir() or _is_symlink(marker):
            return None
        try:
            complete = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(complete, dict) or complete.get("digest") != key.digest:
            return None
        return final

    def _cleanup_pending(self, key: CacheKey) -> None:
        prefix = f"{key.digest}.cleanup-pending-"
        try:
            children = tuple(self.root.iterdir())
        except OSError:
            return
        for child in children:
            if not child.name.startswith(prefix) or _is_symlink(child):
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
        *,
        copy_function: Callable[[str, str], str | os.PathLike[str]] = shutil.copy2,
    ) -> Path:
        entry, _owned = self.store_with_ownership(
            key, source_dir, metadata, copy_function=copy_function
        )
        return entry

    def store_with_ownership(
        self,
        key: CacheKey,
        source_dir: os.PathLike[str] | str,
        metadata: object,
        *,
        copy_function: Callable[[str, str], str | os.PathLike[str]] = shutil.copy2,
    ) -> tuple[Path, bool]:
        source = Path(source_dir)
        if _is_symlink(source):
            raise ValueError(f"source_dir must not be a symlink: {source}")
        if not source.is_dir():
            raise ValueError(f"source_dir must be an existing directory: {source}")

        self.root.mkdir(parents=True, exist_ok=True)
        self._cleanup_pending(key)
        final = self.root / key.digest
        existing = self.lookup(key)
        if existing is not None:
            return existing, False
        _raise_if_cache_symlink(final)

        staging = _unique_sibling(
            self.root,
            f"{key.digest}.tmp-{os.getpid()}-",
        )
        staging.mkdir()
        shutil.copytree(source, staging / "payload", copy_function=copy_function)
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
            _cleanup_after_commit(
                quarantine,
                self.root,
                f"{key.digest}.cleanup-pending-",
                os.replace,
            )
        return final, True

    def store_validated(
        self,
        key: CacheKey,
        source_dir: os.PathLike[str] | str,
        metadata: object,
        *,
        finalize_staging: Callable[[Path], None],
        validate_existing: Callable[[Path], None],
        copy_function: Callable[[str, str], str | os.PathLike[str]] = shutil.copy2,
        cancel_check: Callable[[], None] = lambda: None,
    ) -> tuple[Path, bool]:
        """Publish a recovery entry only after private-staging semantic validation."""
        with _validated_key_lock(key.digest):
            with _cross_process_cache_lock(self.root, key.digest, cancel_check):
                return self._store_validated_locked(
                    key, source_dir, metadata,
                    finalize_staging=finalize_staging,
                    validate_existing=validate_existing,
                    copy_function=copy_function,
                )

    def _store_validated_locked(
        self,
        key: CacheKey,
        source_dir: os.PathLike[str] | str,
        metadata: object,
        *,
        finalize_staging: Callable[[Path], None],
        validate_existing: Callable[[Path], None],
        copy_function: Callable[[str, str], str | os.PathLike[str]] = shutil.copy2,
    ) -> tuple[Path, bool]:
        source = Path(source_dir)
        if _is_symlink(source) or not source.is_dir():
            raise ValueError("source_dir must be an existing non-symlink directory")
        self.root.mkdir(parents=True, exist_ok=True)
        self._cleanup_pending(key)
        final = self.root / key.digest
        existing = self.lookup(key)
        if existing is not None:
            validate_existing(existing)
            return existing, False
        _raise_if_cache_symlink(final)
        staging = _unique_sibling(self.root, f"{key.digest}.tmp-{os.getpid()}-")
        staging.mkdir()
        try:
            shutil.copytree(source, staging / "payload", copy_function=copy_function)
            _write_json(staging / "metadata.json", metadata)
            finalize_staging(staging)
            if (staging / "complete.json").exists():
                raise ValueError("validated cache finalizer published complete marker early")

            # Give a concurrent valid publisher precedence; never quarantine it.
            existing = self.lookup(key)
            if existing is not None:
                validate_existing(existing)
                _remove_direct_child(staging, self.root)
                return existing, False

            metadata_sha256 = hashlib.sha256(
                (staging / "metadata.json").read_bytes()
            ).hexdigest()
            integrity_path = staging / "maximum_integrity.json"
            if not integrity_path.is_file() or _is_symlink(integrity_path):
                raise ValueError("validated cache finalizer did not publish integrity")
            integrity_sha256 = hashlib.sha256(integrity_path.read_bytes()).hexdigest()
            _write_json(staging / "complete.json", {
                "schema": 2,
                "digest": key.digest,
                "metadata_sha256": metadata_sha256,
                "integrity_sha256": integrity_sha256,
            })
            _flush_tree(staging)
            quarantine: Path | None = None
            if _lexists(final):
                quarantine = _unique_sibling(self.root, f"{key.digest}.quarantine-")
                os.replace(final, quarantine)
            try:
                _durable_replace(staging, final)
                _flush_directory(self.root)
            except BaseException as promotion_error:
                if quarantine is not None:
                    try:
                        if _lexists(final):
                            _remove_direct_child(final, self.root)
                        os.replace(quarantine, final)
                    except BaseException as restore_error:
                        raise AtomicReplaceError(
                            final, quarantine, promotion_error, restore_error
                        ) from restore_error
                raise
            if quarantine is not None:
                _cleanup_after_commit(
                    quarantine, self.root,
                    f"{key.digest}.cleanup-pending-", os.replace,
                )
            return final, True
        except BaseException:
            if _lexists(staging):
                try:
                    _remove_direct_child(staging, self.root)
                except (OSError, ValueError):
                    pass
            raise

    def cleanup_incomplete(self) -> int:
        if not self.root.is_dir():
            return 0
        removed = 0
        for child in self.root.iterdir():
            if ".tmp-" not in child.name or _is_symlink(child) or not child.is_dir():
                continue
            _remove_direct_child(child, self.root)
            removed += 1
        return removed

    def invalidate(self, key: CacheKey) -> bool:
        """Remove only the exact invalid entry; never follow a reparse point."""
        final = self.root / key.digest
        _raise_if_cache_symlink(final)
        if not final.exists():
            return False
        _require_direct_child(final, self.root)
        _remove_direct_child(final, self.root)
        return True


def atomic_replace_tree(
    staging: os.PathLike[str] | str,
    destination: os.PathLike[str] | str,
    *,
    replace: Callable[[os.PathLike[str] | str, os.PathLike[str] | str], None] = os.replace,
) -> Path:
    """Promote staging; post-commit cleanup failures leave a recoverable residue."""
    staging_path = Path(staging)
    destination_path = Path(destination)

    if _is_symlink(staging_path):
        raise ValueError(f"staging must not be a symlink: {staging_path}")
    if _is_symlink(destination_path):
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
        _cleanup_after_commit(
            backup,
            destination_path.parent,
            f"{destination_path.name}.cleanup-pending-",
            replace,
        )
    return destination_path
