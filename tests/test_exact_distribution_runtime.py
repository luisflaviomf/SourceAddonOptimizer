import json
import struct

import pytest
from lupa.luajit21 import LuaError

from exact_distribution.builder import build_candidate
from exact_distribution.runtime import render_runtime
from tests.exact_runtime_harness import RuntimeHarness, unpack_gma


def candidate(tmp_path, name="candidate", files=None):
    source = tmp_path / (name + "_source")
    engine = tmp_path / "engine"
    source.mkdir(); engine.mkdir(exist_ok=True)
    files = files or {"sound/example.wav": b"RIFF" + b"pattern" * 40000}
    for rel, raw in files.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    output = tmp_path / name
    report = build_candidate(source, output, tmp_path / (name + "_work"), engine)
    assert report["encoded_count"] == len(files)
    return output, report, files


def test_full_autorun_cold_identity_and_warm_zero_reconstruction(tmp_path):
    out, report, files = candidate(tmp_path)
    harness = RuntimeHarness([out])
    harness.run(out)
    for path, raw in files.items():
        assert harness.game[path.lower().encode()] == raw
    receipt, = harness.receipts()
    assert receipt["status"] == "READY" and receipt["mode"] == "cold"
    assert receipt["counts"]["decode_invocations"] == 1
    assert receipt["counts"]["reconstruction_writes"] == 1
    warm = RuntimeHarness([out], data=harness.data.copy())
    warm.run(out)
    receipt, = warm.receipts()
    assert receipt["mode"] == "warm"
    assert receipt["counts"]["blob_opens"] == receipt["counts"]["decode_invocations"] == receipt["counts"]["reconstruction_writes"] == 0
    assert warm.decompressions == 0
    assert all(path.endswith(b".receipt.json") for path in warm.writes)
    assert all(not key.lower().endswith((b".dx80.vtx", b".sw.vtx"))
               for path in out.rglob("*.gma") for key in unpack_gma(path.read_bytes()))


@pytest.mark.parametrize("corruption", ["contract", "support", "cache", "unknown_mount"])
def test_corrupt_or_conflicting_runtime_fails_closed(tmp_path, corruption):
    out, _, _ = candidate(tmp_path)
    harness = RuntimeHarness([out])
    if corruption == "cache":
        harness.run(out)
        data = harness.data.copy()
        key = next(key for key in data if key.endswith(b".gma"))
        data[key] = data[key][:-1] + b"X"
        harness = RuntimeHarness([out], data=data)
    elif corruption == "unknown_mount":
        harness.game[b"sound/example.wav"] = b"conflict"
    else:
        suffix = b"_contract.json" if corruption == "contract" else b"_support.gma"
        for key in list(harness.game):
            if key.endswith(suffix):
                harness.game[key] = harness.game[key][:-1] + b"X"
    with pytest.raises(LuaError):
        harness.run(out)
    assert b"sound/example.wav" not in harness.game or harness.game[b"sound/example.wav"] == b"conflict"
    assert not any(path.endswith(b".publishing.dat") for path in harness.data)


def test_two_candidates_coexist_and_output_folder_binds_identity(tmp_path):
    one, r1, _ = candidate(tmp_path, "one")
    two, r2, files = candidate(tmp_path, "two", {"sound/second.wav": b"RIFF" + b"pattern" * 40000})
    harness = RuntimeHarness([one, two])
    harness.run(one); harness.run(two)
    assert len(harness.receipts()) == 2
    assert {r["content_id"] for r in harness.receipts()} == {r1["content_id"], r2["content_id"]}
    assert harness.game[b"sound/second.wav"] == files["sound/second.wav"]
    three, r3, _ = candidate(tmp_path, "three")
    assert r3["content_id"] != r1["content_id"]


def large_vvd(padding=0, logical_less=False):
    count = 4096
    logical = count // 2 if logical_less else count
    fixup = struct.pack("<III", 0, 0, logical)
    vertex = 64 + len(fixup) + padding
    header = struct.pack("<4sIII8I4I", b"IDSV", 4, 7, 1,
                         logical, 0, 0, 0, 0, 0, 0, 0, 1, 64, vertex, vertex + count * 48)
    return header + fixup + b"\0" * padding + b"ABCD" * (12 * count) + b"EFGH" * (4 * count)


@pytest.mark.parametrize("padding,logical_less", [(0, True), (8, False)])
def test_frozen_vvd_decoder_consumes_metadata_and_shared_header_correction(tmp_path, padding, logical_less):
    raw = large_vvd(padding, logical_less)
    out, _, _ = candidate(tmp_path, files={"Models/Bar_Caddy.vvd": raw})
    harness = RuntimeHarness([out]); harness.run(out)
    assert harness.game[b"models/bar_caddy.vvd"] == raw
    contract_path, = out.rglob("*_contract.json")
    contract = json.loads(contract_path.read_bytes())
    contract["entries"][0]["codec_meta"]["physical_count"] += 1
    contract_path.write_bytes((json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n").encode())
    autorun, = (out / "lua/autorun").glob("*.lua")
    autorun.write_bytes(render_runtime(contract))
    harness = RuntimeHarness([out])
    with pytest.raises(LuaError, match="contract_binding"):
        harness.run(out)
    assert b"models/bar_caddy.vvd" not in harness.game


def test_runtime_rejects_malformed_contract():
    with pytest.raises(ValueError):
        render_runtime({})


def test_all_lzma_asset_families_reconstruct_and_dx90_is_retained(tmp_path):
    files = {"models/ship.mdl": b"IDST" + b"model" * 60000,
             "models/ship.dx90.vtx": b"VTX" + b"mesh" * 60000,
             "materials/ship.vtf": b"unsupported-vtf-header" + b"texture" * 60000,
             "sound/ship.wav": b"RIFF" + b"sound" * 60000}
    out, report, _ = candidate(tmp_path, files=files)
    harness = RuntimeHarness([out]); harness.run(out)
    assert {path: harness.game[path.encode()] for path in files} == files
    assert len(harness.receipts()) == 4
    assert all(row["status"] == "READY" for row in harness.receipts())
    assert report["dx90_coverage"] == {"source": 1, "candidate_native_or_reconstructed": 1}


def test_p2_full_runtime_reconstructs_real_vtf_and_warm_skips_decoder(tmp_path):
    import hashlib
    import random
    rng = random.Random(91)
    header = bytearray(80)
    header[:4] = b"VTF\0"
    struct.pack_into("<IIIHHIHH", header, 4, 7, 2, 80, 512, 512, 0, 1, 0)
    struct.pack_into("<I", header, 52, 13)
    header[56] = 1
    struct.pack_into("<i", header, 57, -1)
    struct.pack_into("<H", header, 63, 1)
    words = [hashlib.sha256(bytes([index])).digest()[:4] for index in range(256)]
    raw = bytes(header) + b"".join(words[rng.randrange(256)] + words[rng.randrange(256)] for _ in range(16384))
    out, _, _ = candidate(tmp_path, files={"materials/complex.vtf": raw})
    contract, = [json.loads(path.read_bytes()) for path in out.rglob("*_contract.json")]
    assert contract["codec_family"] == "vtf"
    harness = RuntimeHarness([out]); harness.run(out)
    assert harness.game[b"materials/complex.vtf"] == raw
    warm = RuntimeHarness([out], data=harness.data.copy()); warm.run(out)
    assert warm.receipts()[0]["counts"]["decode_invocations"] == 0


def test_support_alias_and_dedicated_server_reconstruction(tmp_path):
    out, _, files = candidate(tmp_path)
    harness = RuntimeHarness([out], server=True, dedicated=True)
    for key in list(harness.game):
        if key.startswith(b"addons/"):
            del harness.game[key]
    harness.run(out)
    assert harness.game[b"sound/example.wav"] == files["sound/example.wav"]
    assert harness.receipts()[0]["realm"] == "server"
    assert harness.mounts[0].startswith(b"data_static/codex_exact_distribution/")


@pytest.mark.parametrize("failure", ["blob", "mount_set", "post_mount"])
def test_runtime_identity_gates_after_support_mount(tmp_path, failure):
    out, _, _ = candidate(tmp_path)
    harness = RuntimeHarness([out])
    original_mount = harness.mount
    def altered_mount(path):
        ok, files = original_mount(path)
        if not path.startswith(b"data/") and failure == "blob":
            key = next(key for key in harness.game if key.endswith(b".dat"))
            harness.game[key] = b"X" + harness.game[key][1:]
        if path.startswith(b"data/"):
            if failure == "mount_set":
                files[1] = b"sound/wrong.wav"
            elif failure == "post_mount":
                harness.game[b"sound/example.wav"] = b"bad"
        return ok, files
    harness.lua.globals().py_mount = altered_mount
    with pytest.raises(LuaError, match={"blob": "blob:identity", "mount_set": "mount_closed_set", "post_mount": "post_mount:size"}[failure]):
        harness.run(out)
    assert harness.receipts()[0]["status"] == "FAILED"
