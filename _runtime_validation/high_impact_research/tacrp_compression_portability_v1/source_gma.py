"""Deterministic streaming GMA v3 writer and independent file auditor."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
import tempfile
from typing import BinaryIO, Callable


GMA_TITLE = "tacrp_compression_portability_v1"
GMA_DESCRIPTION = '{"domain":"tacrp-compression-portability/support-gma/v1"}'
GMA_AUTHOR = "codex-tacrp-portability"
GMA_VERSION = 3
GMA_ADDON_VERSION = 1

MAX_ENTRIES = 2_048
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_PAYLOAD_BYTES + 2 * 1024 * 1024
MAX_PATH_BYTES = 240
CHUNK_BYTES = 1024 * 1024

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
# .dat is engine-whitelisted; legacy suffixes remain readable for rejected evidence.
_CODEC_SUFFIXES = (".dat", ".cvtfp2", ".cvvdp1", ".cvvdpt1")


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _reject_reparse_chain(path: Path, *, final_may_be_missing: bool = False) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for index, part in enumerate(absolute.parts[1:], 1):
        current /= part
        if final_may_be_missing and index == len(absolute.parts) - 1 and not current.exists():
            break
        try:
            if _is_reparse(current):
                raise ValueError(f"reparse path component: {current}")
        except FileNotFoundError as exc:
            raise ValueError(f"missing path component: {current}") from exc
    return absolute


def _validate_gma_path(path: object) -> tuple[str, bytes]:
    if not isinstance(path, str) or not path:
        raise ValueError("GMA path is empty or not a string")
    try:
        encoded = path.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("GMA path is not ASCII") from exc
    segments = path.split("/")
    unsafe = (
        len(encoded) > MAX_PATH_BYTES
        or path != path.lower()
        or "\\" in path
        or "//" in path
        or "\x00" in path
        or bool(re.search(r'[:<>"|?*]', path))
        or any(not item or item in {".", ".."} or item[0] in " ." or item[-1] in " ." for item in segments)
    )
    if unsafe:
        raise ValueError("GMA path is not canonical and safe")
    support_blob = path.startswith("data_static/") and path.endswith(_CODEC_SUFFIXES)
    if support_blob:
        suffix = next(item for item in _CODEC_SUFFIXES if path.endswith(item))
        support_blob = bool(_SHA256.fullmatch(segments[-1][: -len(suffix)]))
    allowed = (
        path.startswith("materials/") and path.endswith(".vtf")
        or path.startswith("models/") and path.endswith(".vvd")
        or support_blob
        or (
            path.startswith("lua/codex_tacrp_portability/")
            and path.endswith("_bootstrap_v1.lua")
        )
    )
    if not allowed:
        raise ValueError("GMA path is outside the supported cache/bootstrap namespaces")
    return path, encoded


def _content_address_digest(path: str) -> str | None:
    if not path.startswith("data_static/"):
        return None
    suffix = next((item for item in _CODEC_SUFFIXES if path.endswith(item)), None)
    return None if suffix is None else path.rsplit("/", 1)[-1][: -len(suffix)]


def _cstring(value: str) -> bytes:
    return value.encode("ascii") + b"\0"


_HEADER = b"".join(
    (
        b"GMAD",
        bytes((GMA_VERSION,)),
        struct.pack("<QQ", 0, 0),
        b"\0",
        _cstring(GMA_TITLE),
        _cstring(GMA_DESCRIPTION),
        _cstring(GMA_AUTHOR),
        struct.pack("<I", GMA_ADDON_VERSION),
    )
)


def gma_header(entries: Mapping[str, int]) -> bytes:
    """Return canonical GMA metadata, entry table, and zero table terminator."""
    if not isinstance(entries, Mapping) or not entries or len(entries) > MAX_ENTRIES:
        raise ValueError("GMA header entry count is out of bounds")
    normalized: list[tuple[str, bytes, int]] = []
    folded: set[str] = set()
    total = 0
    for raw_path, size in entries.items():
        path, encoded = _validate_gma_path(raw_path)
        if path.casefold() in folded:
            raise ValueError("GMA header paths duplicate or case-fold collide")
        folded.add(path.casefold())
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_ENTRY_BYTES:
            raise ValueError("GMA header payload size is out of bounds")
        total += size
        if total > MAX_PAYLOAD_BYTES:
            raise ValueError("GMA header aggregate payload size is out of bounds")
        normalized.append((path, encoded, size))
    normalized.sort(key=lambda item: item[1])
    parts = [_HEADER]
    for number, (_path, encoded, size) in enumerate(normalized, 1):
        parts.extend((struct.pack("<I", number), encoded + b"\0", struct.pack("<qI", size, 0)))
    parts.append(struct.pack("<I", 0))
    return b"".join(parts)


def _normalize_sources(entries: Mapping[str, Path]) -> tuple[tuple[str, bytes, Path, int, tuple[int, int]], ...]:
    if not isinstance(entries, Mapping) or not entries or len(entries) > MAX_ENTRIES:
        raise ValueError("GMA entry count is out of bounds")
    normalized: list[tuple[str, bytes, Path, int, tuple[int, int]]] = []
    folded: set[str] = set()
    total = 0
    for raw_path, raw_source in entries.items():
        path, encoded = _validate_gma_path(raw_path)
        if path.casefold() in folded:
            raise ValueError("GMA path duplicates or case-fold collides")
        folded.add(path.casefold())
        if not isinstance(raw_source, Path):
            raise ValueError("GMA source must be a pathlib.Path")
        source = _reject_reparse_chain(raw_source)
        if not source.is_file() or _is_reparse(source):
            raise ValueError("GMA source is not a regular file")
        before = source.stat()
        if before.st_nlink != 1:
            raise ValueError("GMA source is hardlinked")
        size = before.st_size
        if not 0 < size <= MAX_ENTRY_BYTES:
            raise ValueError("GMA entry payload size is out of bounds")
        total += size
        if total > MAX_PAYLOAD_BYTES:
            raise ValueError("GMA aggregate payload size is out of bounds")
        normalized.append((path, encoded, source, size, (before.st_size, before.st_mtime_ns)))
    normalized.sort(key=lambda item: item[1])
    return tuple(normalized)


def _normalize_expected(expected: Mapping[str, Mapping[str, object]] | None) -> dict[str, tuple[int, str]] | None:
    if expected is None:
        return None
    if not isinstance(expected, Mapping) or not expected or len(expected) > MAX_ENTRIES:
        raise ValueError("GMA expected entry count is out of bounds")
    result: dict[str, tuple[int, str]] = {}
    folded: set[str] = set()
    total = 0
    for raw_path, identity in expected.items():
        path, _encoded = _validate_gma_path(raw_path)
        if path.casefold() in folded:
            raise ValueError("GMA expected paths duplicate or case-fold collide")
        folded.add(path.casefold())
        if not isinstance(identity, Mapping) or set(identity) != {"size", "sha256"}:
            raise ValueError("GMA expected identity shape is invalid")
        size = identity["size"]
        digest = identity["sha256"]
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_ENTRY_BYTES:
            raise ValueError("GMA expected payload size is invalid")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("GMA expected payload SHA-256 is invalid")
        total += size
        if total > MAX_PAYLOAD_BYTES:
            raise ValueError("GMA expected aggregate payload size is out of bounds")
        result[path] = (size, digest)
    return result


def _write_segment(stream: BinaryIO, payload: bytes, archive_hash: object) -> None:
    stream.write(payload)
    archive_hash.update(payload)  # type: ignore[attr-defined]


def _stream_source(
    path: str,
    source: Path,
    size: int,
    snapshot: tuple[int, int],
    consume: Callable[[bytes], None],
) -> str:
    payload_hash = hashlib.sha256()
    observed = 0
    with source.open("rb") as source_stream:
        while True:
            chunk = source_stream.read(CHUNK_BYTES)
            if not chunk:
                break
            observed += len(chunk)
            payload_hash.update(chunk)
            consume(chunk)
    after = source.stat()
    if observed != size or (after.st_size, after.st_mtime_ns) != snapshot:
        raise ValueError(f"GMA source changed while streaming: {path}")
    return payload_hash.hexdigest()


class _ChunkFingerprint:
    def __init__(self, chunk_size: int) -> None:
        self.chunk_size = chunk_size
        self.overall = hashlib.sha256()
        self.pending = bytearray()
        self.chunks: list[str] = []
        self.size = 0

    def update(self, payload: bytes) -> None:
        self.overall.update(payload)
        self.size += len(payload)
        view = memoryview(payload)
        cursor = 0
        while cursor < len(view):
            take = min(self.chunk_size - len(self.pending), len(view) - cursor)
            self.pending.extend(view[cursor : cursor + take])
            cursor += take
            if len(self.pending) == self.chunk_size:
                self.chunks.append(hashlib.sha256(self.pending).hexdigest())
                self.pending.clear()

    def finish(self) -> tuple[int, str, list[str]]:
        if self.pending:
            self.chunks.append(hashlib.sha256(self.pending).hexdigest())
            self.pending.clear()
        return self.size, self.overall.hexdigest(), self.chunks.copy()


def fingerprint_gma_entries(
    entries: Mapping[str, Path],
    chunk_size: int = CHUNK_BYTES,
) -> dict[str, object]:
    """Fingerprint the exact would-be archive while streaming sources, without writing it."""
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or not 0 < chunk_size <= MAX_ENTRY_BYTES:
        raise ValueError("GMA fingerprint chunk size is out of bounds")
    normalized = _normalize_sources(entries)
    header = gma_header({path: size for path, _encoded, _source, size, _snapshot in normalized})
    fingerprint = _ChunkFingerprint(chunk_size)
    fingerprint.update(header)
    for path, _encoded, source, size, snapshot in normalized:
        payload_sha = _stream_source(path, source, size, snapshot, fingerprint.update)
        expected_sha = _content_address_digest(path)
        if expected_sha is not None and payload_sha != expected_sha:
            raise ValueError(f"GMA support blob content address mismatch: {path}")
    fingerprint.update(struct.pack("<I", 0))
    size, digest, chunks = fingerprint.finish()
    return {
        "size": size,
        "sha256": digest,
        "chunk_size": chunk_size,
        "chunks": chunks,
        "header_hex": header.hex(),
    }


def build_gma_file(entries: Mapping[str, Path], output: Path) -> dict[str, object]:
    """Stream path-sorted file entries into a new deterministic GMA v3 archive."""
    normalized = _normalize_sources(entries)
    if not isinstance(output, Path):
        raise ValueError("GMA output must be a pathlib.Path")
    output = _reject_reparse_chain(output, final_may_be_missing=True)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing existing GMA output: {output}")
    parent = _reject_reparse_chain(output.parent)
    if not parent.is_dir():
        raise ValueError("GMA output parent is not a directory")

    file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.tmp-", dir=parent)
    temporary = Path(temporary_name)
    archive_hash = hashlib.sha256()
    identities: dict[str, dict[str, object]] = {}
    try:
        with os.fdopen(file_descriptor, "wb") as stream:
            header = gma_header({path: size for path, _encoded, _source, size, _snapshot in normalized})
            _write_segment(stream, header, archive_hash)

            for path, _encoded, source, size, snapshot in normalized:
                payload_sha = _stream_source(
                    path,
                    source,
                    size,
                    snapshot,
                    lambda chunk: _write_segment(stream, chunk, archive_hash),
                )
                expected_sha = _content_address_digest(path)
                if expected_sha is not None and payload_sha != expected_sha:
                    raise ValueError(f"GMA support blob content address mismatch: {path}")
                identities[path] = {"size": size, "sha256": payload_sha}
            _write_segment(stream, struct.pack("<I", 0), archive_hash)
            stream.flush()
            os.fsync(stream.fileno())

        audit = audit_gma_file(temporary, identities)
        if audit["sha256"] != archive_hash.hexdigest():
            raise ValueError("GMA writer and independent auditor hashes differ")
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"refusing raced GMA output: {output}")
        os.replace(temporary, output)
        return audit
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class _StreamReader:
    def __init__(self, stream: BinaryIO, size: int) -> None:
        self.stream = stream
        self.size = size

    @property
    def offset(self) -> int:
        return self.stream.tell()

    def take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > self.size:
            raise ValueError("GMA is truncated")
        payload = self.stream.read(count)
        if len(payload) != count:
            raise ValueError("GMA is truncated")
        return payload

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.take(8))[0]

    def cstring(self, maximum: int) -> str:
        value = bytearray()
        for _ in range(maximum + 1):
            item = self.take(1)
            if item == b"\0":
                try:
                    return value.decode("ascii")
                except UnicodeDecodeError as exc:
                    raise ValueError("GMA string is not ASCII") from exc
            value.extend(item)
        raise ValueError("GMA string is unterminated or too long")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def audit_gma_file(
    path: Path,
    expected: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Independently stream-parse and authenticate one canonical GMA v3 file."""
    if not isinstance(path, Path):
        raise ValueError("GMA audit path must be a pathlib.Path")
    path = _reject_reparse_chain(path)
    if not path.is_file() or _is_reparse(path):
        raise ValueError("GMA audit input is not a regular file")
    before = path.stat()
    if before.st_nlink != 1 or not 0 < before.st_size <= MAX_ARCHIVE_BYTES:
        raise ValueError("GMA archive file is out of bounds or hardlinked")
    expected_identities = _normalize_expected(expected)
    archive_sha = _hash_file(path)

    with path.open("rb") as stream:
        reader = _StreamReader(stream, before.st_size)
        if reader.take(4) != b"GMAD" or reader.u8() != GMA_VERSION:
            raise ValueError("GMA magic or version mismatch")
        if reader.u64() != 0 or reader.u64() != 0:
            raise ValueError("GMA timestamps are not canonical")
        if reader.cstring(0) != "":
            raise ValueError("GMA required-content list is not empty")
        if (
            reader.cstring(64) != GMA_TITLE
            or reader.cstring(128) != GMA_DESCRIPTION
            or reader.cstring(64) != GMA_AUTHOR
            or reader.u32() != GMA_ADDON_VERSION
        ):
            raise ValueError("GMA metadata contract mismatch")

        table: list[tuple[str, int]] = []
        total_payload = 0
        previous_encoded: bytes | None = None
        folded: set[str] = set()
        while True:
            number = reader.u32()
            if number == 0:
                break
            if number != len(table) + 1 or len(table) >= MAX_ENTRIES:
                raise ValueError("GMA entry number or count is invalid")
            entry_path = reader.cstring(MAX_PATH_BYTES)
            entry_path, encoded = _validate_gma_path(entry_path)
            if previous_encoded is not None and encoded <= previous_encoded:
                raise ValueError("GMA table paths are not bytewise sorted and unique")
            if entry_path.casefold() in folded:
                raise ValueError("GMA table paths case-fold collide")
            folded.add(entry_path.casefold())
            previous_encoded = encoded
            size = reader.i64()
            if not 0 < size <= MAX_ENTRY_BYTES:
                raise ValueError("GMA table payload size is out of bounds")
            total_payload += size
            if total_payload > MAX_PAYLOAD_BYTES:
                raise ValueError("GMA table aggregate payload size is out of bounds")
            if reader.u32() != 0:
                raise ValueError("GMA table CRC is not zero")
            table.append((entry_path, size))
        if not table:
            raise ValueError("GMA table is empty")
        if reader.offset + total_payload + 4 != before.st_size:
            raise ValueError("GMA payload/footer extent is truncated or has trailing bytes")
        if expected_identities is not None and set(expected_identities) != {item[0] for item in table}:
            raise ValueError("GMA expected entry closed set mismatch")

        audited_entries: list[dict[str, object]] = []
        for entry_path, size in table:
            offset = reader.offset
            digest = hashlib.sha256()
            remaining = size
            while remaining:
                chunk = reader.take(min(CHUNK_BYTES, remaining))
                digest.update(chunk)
                remaining -= len(chunk)
            observed_sha = digest.hexdigest()
            content_address = _content_address_digest(entry_path)
            if content_address is not None and observed_sha != content_address:
                raise ValueError(f"GMA support blob content address mismatch: {entry_path}")
            if expected_identities is not None and expected_identities[entry_path] != (size, observed_sha):
                raise ValueError(f"GMA expected payload identity mismatch: {entry_path}")
            audited_entries.append(
                {"path": entry_path, "size": size, "sha256": observed_sha, "offset": offset}
            )
        if reader.u32() != 0:
            raise ValueError("GMA footer CRC is not zero")
        if reader.offset != before.st_size:
            raise ValueError("GMA has trailing bytes")

    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("GMA archive changed during audit")
    return {
        "size": before.st_size,
        "sha256": archive_sha,
        "entries": audited_entries,
        "payload_bytes": total_payload,
        "overhead_bytes": before.st_size - total_payload,
    }
