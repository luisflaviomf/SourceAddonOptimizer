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
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from .reporting import canonical_json


_SHA256 = re.compile(r"[0-9a-f]{64}")
_FAMILY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_LOCK_GUARD = threading.Lock()
_LOCKS: dict[str, tuple[threading.RLock, int]] = {}


def _is_reparse(path: Path) -> bool:
    try:
        value = path.lstat()
    except OSError:
        return True
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(canonical_json(payload), encoding="utf-8")


@contextmanager
def _identity_lock(digest: str):
    with _LOCK_GUARD:
        lock, users = _LOCKS.get(digest, (threading.RLock(), 0))
        _LOCKS[digest] = (lock, users + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _LOCK_GUARD:
            current, users = _LOCKS[digest]
            if current is lock and users == 1:
                del _LOCKS[digest]
            else:
                _LOCKS[digest] = (current, users - 1)


@dataclass(frozen=True)
class ReferenceBundleIdentity:
    family_id: str
    family_hash: str
    renderer_sha256: str
    profile_sha256: str
    dependency_digest: str
    material_roots_sha256: str
    vtfcmd_sha256: str

    def __post_init__(self) -> None:
        if _FAMILY_ID.fullmatch(self.family_id or "") is None:
            raise ValueError("reference bundle family_id is invalid")
        for name in (
            "family_hash",
            "renderer_sha256",
            "profile_sha256",
            "dependency_digest",
            "material_roots_sha256",
            "vtfcmd_sha256",
        ):
            if _SHA256.fullmatch(getattr(self, name) or "") is None:
                raise ValueError(f"reference bundle {name} is invalid")

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            canonical_json(asdict(self)).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class ReferenceBundle:
    identity: ReferenceBundleIdentity
    root: Path


def _inventory(root: Path) -> tuple[dict[str, object], ...]:
    if _is_reparse(root) or not root.is_dir():
        raise ValueError("reference bundle payload must be a regular directory")
    records: list[dict[str, object]] = []
    casefolded: set[str] = set()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if _is_reparse(path):
            raise ValueError("reference bundle payload contains a reparse point")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("reference bundle payload contains a non-regular file")
        relative = path.relative_to(root).as_posix()
        canonical = PurePosixPath(relative).as_posix()
        if canonical != relative or canonical.startswith("../") or canonical.startswith("/"):
            raise ValueError("reference bundle payload path is not canonical")
        folded = canonical.casefold()
        if folded in casefolded:
            raise ValueError("reference bundle payload has a case-colliding path")
        casefolded.add(folded)
        size, digest = _sha256_file(path)
        records.append({"path": canonical, "size": size, "sha256": digest})
    if not records:
        raise ValueError("reference bundle payload is empty")
    return tuple(records)


def _validated_bundle(
    entry: Path, identity: ReferenceBundleIdentity
) -> ReferenceBundle | None:
    if _is_reparse(entry) or not entry.is_dir():
        return None
    payload = entry / "payload"
    identity_path = entry / "identity.json"
    inventory_path = entry / "inventory.json"
    complete_path = entry / "complete.json"
    if any(_is_reparse(path) for path in (payload, identity_path, inventory_path, complete_path)):
        return None
    try:
        stored_identity = json.loads(identity_path.read_text(encoding="utf-8"))
        expected_inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        complete = json.loads(complete_path.read_text(encoding="utf-8"))
        actual_inventory = list(_inventory(payload))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if stored_identity != asdict(identity) or expected_inventory != actual_inventory:
        return None
    inventory_digest = hashlib.sha256(
        canonical_json(expected_inventory).encode("utf-8")
    ).hexdigest()
    if complete != {
        "schema": 1,
        "identity_digest": identity.digest,
        "inventory_sha256": inventory_digest,
    }:
        return None
    return ReferenceBundle(identity, payload)


class ReferenceBundleStore:
    def __init__(self, root: os.PathLike[str] | str) -> None:
        self.root = Path(root)

    def lookup(self, identity: ReferenceBundleIdentity) -> ReferenceBundle | None:
        if not isinstance(identity, ReferenceBundleIdentity):
            raise TypeError("reference bundle identity is invalid")
        return _validated_bundle(
            self.root / identity.family_id / identity.digest, identity
        )

    def publish(
        self,
        identity: ReferenceBundleIdentity,
        source: os.PathLike[str] | str,
    ) -> ReferenceBundle:
        if not isinstance(identity, ReferenceBundleIdentity):
            raise TypeError("reference bundle identity is invalid")
        source_root = Path(source)
        if _is_reparse(source_root) or not source_root.is_dir():
            raise ValueError("reference bundle source must be a regular directory")
        with _identity_lock(identity.digest):
            existing = self.lookup(identity)
            if existing is not None:
                return existing
            family_root = self.root / identity.family_id
            family_root.mkdir(parents=True, exist_ok=True)
            if _is_reparse(family_root):
                raise ValueError("reference bundle family root is unsafe")
            staging = family_root / f".pending-{uuid.uuid4().hex}"
            final = family_root / identity.digest
            quarantine: Path | None = None
            try:
                source_inventory = list(_inventory(source_root))
                staging.mkdir()
                shutil.copytree(source_root, staging / "payload", copy_function=shutil.copy2)
                inventory = list(_inventory(staging / "payload"))
                if inventory != source_inventory:
                    raise ValueError("reference bundle source changed while copying")
                _write_json(staging / "identity.json", asdict(identity))
                _write_json(staging / "inventory.json", inventory)
                _write_json(staging / "complete.json", {
                    "schema": 1,
                    "identity_digest": identity.digest,
                    "inventory_sha256": hashlib.sha256(
                        canonical_json(inventory).encode("utf-8")
                    ).hexdigest(),
                })
                if os.path.lexists(final):
                    if _is_reparse(final):
                        raise ValueError("reference bundle destination is unsafe")
                    quarantine = family_root / f".quarantine-{uuid.uuid4().hex}"
                    os.replace(final, quarantine)
                os.replace(staging, final)
                if quarantine is not None:
                    shutil.rmtree(quarantine)
            except BaseException:
                if os.path.lexists(staging) and not _is_reparse(staging):
                    shutil.rmtree(staging)
                if quarantine is not None and quarantine.exists() and not final.exists():
                    os.replace(quarantine, final)
                raise
            published = self.lookup(identity)
            if published is None:
                raise ValueError("published reference bundle failed verification")
            return published
