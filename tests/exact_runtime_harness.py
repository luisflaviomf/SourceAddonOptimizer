"""Offline GMod boundary emulator; executes complete generated LuaJIT autoruns."""
import hashlib
import io
import json
import lzma
import struct
import time

from lupa.luajit21 import LuaRuntime


def unpack_gma(raw):
    stream = io.BytesIO(raw)
    def take(n):
        value = stream.read(n)
        assert len(value) == n
        return value
    def zstr():
        result = bytearray()
        while (value := take(1)) != b"\0":
            result.extend(value)
        return bytes(result)
    assert take(5) == b"GMAD\x03"
    take(16)
    assert zstr() == b""
    zstr(); zstr(); zstr(); take(4)
    table = []
    while struct.unpack("<I", take(4))[0]:
        path = zstr()
        size, crc = struct.unpack("<qI", take(12))
        assert crc == 0
        table.append((path, size))
    files = {path: take(size) for path, size in table}
    assert take(4) == b"\0" * 4 and not stream.read()
    return files


class RuntimeHarness:
    def __init__(self, candidates, data=None, extra_game=None, server=False, dedicated=False):
        self.lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
        self.data = {} if data is None else data
        self.game = dict(extra_game or {})
        self.writes = []
        self.mounts = []
        self.decompressions = 0
        for root in candidates:
            for path in root.rglob("*"):
                if path.is_file():
                    rel = path.relative_to(root).as_posix().lower().encode()
                    self.game[rel] = path.read_bytes()
                    self.game[b"addons/" + root.name.lower().encode() + b"/" + rel] = path.read_bytes()
        globals = self.lua.globals()
        globals.SERVER = server
        globals.py_open = self.open
        globals.py_exists = lambda path, realm: path in self.space(realm)
        globals.py_delete = lambda path: self.data.pop(path, None)
        globals.py_rename = self.rename
        globals.py_sha = lambda raw: hashlib.sha256(raw).hexdigest().encode()
        globals.py_json_read = lambda raw, *_: self.to_lua(json.loads(raw))
        globals.py_json_write = lambda table, *_: json.dumps(self.from_lua(table)).encode()
        globals.py_decompress = self.decompress
        globals.py_mount = self.mount
        globals.py_clock = time.monotonic
        globals.py_dedicated = lambda: dedicated
        self.lua.execute(b'''
            function AddCSLuaFile() end
            SysTime = function() return py_clock() end
            file = {
              Open=function(...) return py_open(...) end,
              Exists=function(...) return py_exists(...) end,
              CreateDir=function() end,
              Delete=function(...) py_delete(...) end,
              Rename=function(...) return py_rename(...) end
            }
            util = {
              SHA256=function(...) return py_sha(...) end,
              JSONToTable=function(...) return py_json_read(...) end,
              TableToJSON=function(...) return py_json_write(...) end,
              Decompress=function(...) return py_decompress(...) end
            }
            game = {MountGMA=function(...) return py_mount(...) end,
                    IsDedicated=function() return py_dedicated() end}
            print=function() end
        ''')

    def space(self, realm):
        assert realm in (b"GAME", b"DATA")
        return self.game if realm == b"GAME" else self.data

    def open(self, path, mode, realm):
        space = self.space(realm)
        if mode == b"rb":
            if path not in space:
                return None
            stream = io.BytesIO(space[path])
        else:
            assert mode == b"wb" and realm == b"DATA"
            self.writes.append(path)
            stream = io.BytesIO()
        handle = self.lua.table()
        handle[b"Read"] = lambda _, count: stream.read(int(count))
        handle[b"Size"] = lambda _: len(stream.getvalue())
        handle[b"Write"] = lambda _, raw: stream.write(raw)
        def close(_):
            if mode == b"wb":
                space[path] = stream.getvalue()
            stream.close()
        handle[b"Close"] = close
        return handle

    def rename(self, old, new):
        if old not in self.data or new in self.data:
            return False
        self.data[new] = self.data.pop(old)
        return True

    def decompress(self, blob, size):
        self.decompressions += 1
        # Bootil reads five property bytes and the explicit eight-byte size,
        # then passes the raw stream to its LZMA decoder (not liblzma ALONE).
        assert blob[:5] == bytes.fromhex("5d00008000")
        assert struct.unpack("<Q", blob[5:13])[0] == size
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[
            {"id": lzma.FILTER_LZMA1, "dict_size": 8388608, "lc": 3, "lp": 0, "pb": 2}])
        raw = decoder.decompress(blob[13:], max_length=int(size) + 1)
        assert decoder.eof and not decoder.unused_data and len(raw) == size
        return raw

    def mount(self, path):
        self.mounts.append(path)
        raw = self.data[path[5:]] if path.startswith(b"data/") else self.game[path]
        files = unpack_gma(raw)
        self.game.update(files)
        return True, self.to_lua(list(files))

    def to_lua(self, value):
        if isinstance(value, dict):
            return self.lua.table_from({k.encode(): self.to_lua(v) for k, v in value.items()})
        if isinstance(value, list):
            return self.lua.table_from([self.to_lua(v) for v in value])
        return value.encode() if isinstance(value, str) else value

    def from_lua(self, value):
        if isinstance(value, bytes):
            return value.decode()
        if hasattr(value, "items"):
            return {self.from_lua(k): self.from_lua(v) for k, v in value.items()}
        return value

    def run(self, candidate):
        for path in sorted((candidate / "lua/autorun").glob("*.lua")):
            self.lua.execute(path.read_bytes())

    def receipts(self):
        return [json.loads(raw) for path, raw in self.data.items() if path.endswith(b".receipt.json")]
