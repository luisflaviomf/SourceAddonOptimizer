"""Render the pinned portability runtime with product/CID isolation.

The asset GMA header and LZMA decoder below preserve the separate fullstack
exact_assets domain. Frozen files are read and authenticated, never modified.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct

from .codecs import GENERAL_LIMIT, LZMA_DOMAIN, P1_DOMAIN, PORTABLE_DOMAIN, VTF_DOMAIN, VTF_LIMIT, verify_vendor
from _runtime_validation.high_impact_research.tacrp_compression_portability_v1 import source_gma

NAMESPACE = "codex_exact_distribution"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TEMPLATES = Path(source_gma.__file__).parent / "runtime_templates"
ASSET_FAMILIES = {"lzmdl": ("models/", ".mdl"), "lzvtx": ("models/", ".dx90.vtx"),
                  "lzvtf": ("materials/", ".vtf"), "lzwav": ("sound/", ".wav")}


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def safe_target(path: str) -> bool:
    return (isinstance(path, str) and bool(re.fullmatch(r"[a-z0-9_./ -]+", path))
            and len(path) <= 240 and all(p and p not in (".", "..") and p[0] not in " ."
            and p[-1] not in " ." for p in path.split("/")))


def asset_header(entries: dict[str, int]) -> bytes:
    """Existing exact_assets header layout; same caps and canonical ordering."""
    if not entries or len(entries) > source_gma.MAX_ENTRIES:
        raise ValueError("asset GMA count")
    header = (b'GMAD\x03' + struct.pack('<QQ', 0, 0) + b'\0tacrp_exact_assets_v1\0'
              + b'{"domain":"source-asset-lzma-known-size-exact/v1"}\0codex\0' + struct.pack('<I', 1))
    total = 0
    for number, (path, size) in enumerate(sorted(entries.items()), 1):
        if not safe_target(path) or not any(path.startswith(p) and path.endswith(s) for p, s in ASSET_FAMILIES.values()):
            raise ValueError("unsafe asset path")
        if type(size) is not int or not 0 < size <= GENERAL_LIMIT:
            raise ValueError("asset size out of bounds")
        total += size
        if total > source_gma.MAX_PAYLOAD_BYTES:
            raise ValueError("asset GMA aggregate")
        header += struct.pack('<I', number) + path.encode('ascii') + b'\0' + struct.pack('<qI', size, 0)
    return header + b'\0' * 4


LUA_DECODER = r'''
return function(blob, entry)
    if entry.codec_domain ~= "source-asset-lzma-known-size-exact/v1" then error("asset:domain") end
    if type(blob) ~= "string" or #blob <= 13 then error("asset:header") end
    if string.sub(blob,1,5) ~= string.char(93,0,0,128,0) then error("asset:properties") end
    local size = 0
    for index = 13,6,-1 do size = size * 256 + string.byte(blob,index) end
    if size ~= entry.source_size or size < 1 or size > 67108864 then error("asset:size") end
    local source = util.Decompress(blob, size)
    if type(source) ~= "string" or #source ~= size or string.lower(util.SHA256(source)) ~= entry.source_sha256 then
        error("asset:reconstruction")
    end
    return source
end
'''


def contract_path(contract: dict) -> str:
    return f"data_static/{NAMESPACE}/{contract['content_id']}/{contract['family']}_contract.json"


def support_alias(contract: dict) -> str:
    return f"data_static/{NAMESPACE}/{contract['content_id']}/{contract['family']}_support.gma"


def _identity(value: dict, *, header=False) -> None:
    if not isinstance(value, dict) or type(value.get("size")) is not int or not 0 < value["size"] <= source_gma.MAX_ARCHIVE_BYTES:
        raise ValueError("runtime identity size")
    if not isinstance(value.get("sha256"), str) or not _SHA.fullmatch(value["sha256"]):
        raise ValueError("runtime identity hash")
    chunk_size = value.get("chunk_size")
    if type(chunk_size) is not int or not 0 < chunk_size <= GENERAL_LIMIT:
        raise ValueError("runtime chunk size")
    chunks = value.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != (value["size"] + chunk_size - 1) // chunk_size or any(
            not isinstance(digest, str) or not _SHA.fullmatch(digest) for digest in chunks):
        raise ValueError("runtime chunk coverage")
    if header and (not isinstance(value.get("header_hex"), str) or not re.fullmatch(r"(?:[0-9a-f]{2})+", value["header_hex"])):
        raise ValueError("runtime cache header")


def _validate(contract: dict) -> None:
    if not isinstance(contract, dict) or contract.get("schema") != "exact-distribution-runtime/v1":
        raise ValueError("invalid runtime contract schema")
    cid = contract.get("content_id")
    codec = contract.get("codec_family")
    shard = contract.get("shard")
    if not isinstance(cid, str) or not _SHA.fullmatch(cid):
        raise ValueError("invalid content id")
    if codec not in (*ASSET_FAMILIES, "vvd", "vtf") or type(shard) is not int or not 0 <= shard <= 9999:
        raise ValueError("invalid family/shard")
    if contract.get("family") != f"{codec}_{shard:04d}":
        raise ValueError("invalid family identity")
    folder = contract.get("addon_folder")
    if not isinstance(folder, str) or not re.fullmatch(r"[a-z0-9_-]+", folder):
        raise ValueError("invalid addon folder")
    if contract.get("support_path") != f"addons/{folder}/{support_alias(contract)}":
        raise ValueError("invalid support path")
    if contract.get("engine_preexisting") != {}:
        raise ValueError("engine overlaps must remain native")
    _identity(contract.get("cache"), header=True)
    _identity(contract.get("support"))
    entries = contract.get("entries")
    if not isinstance(entries, list) or not 0 < len(entries) <= source_gma.MAX_ENTRIES:
        raise ValueError("invalid entry count")
    paths = []
    total = 0
    for entry in entries:
        path = entry.get("target_path")
        prefix, suffix = ASSET_FAMILIES.get(codec, ("models/", ".vvd") if codec == "vvd" else ("materials/", ".vtf"))
        if not safe_target(path) or not path.startswith(prefix) or not path.endswith(suffix):
            raise ValueError("invalid target family/path")
        for key in ("source_sha256", "blob_sha256"):
            if not isinstance(entry.get(key), str) or not _SHA.fullmatch(entry[key]):
                raise ValueError("invalid entry hash")
        for key in ("source_size", "blob_size"):
            cap = VTF_LIMIT if key == "source_size" and codec == "vtf" else GENERAL_LIMIT
            if type(entry.get(key)) is not int or not 0 < entry[key] <= cap:
                raise ValueError("invalid entry size")
        domains = (P1_DOMAIN, PORTABLE_DOMAIN) if codec == "vvd" else (VTF_DOMAIN,) if codec == "vtf" else (LZMA_DOMAIN,)
        if entry.get("codec_domain") not in domains:
            raise ValueError("invalid codec domain")
        if entry.get("blob_path") != f"data_static/{NAMESPACE}/{cid}/{entry['blob_sha256']}.dat":
            raise ValueError("invalid blob path")
        paths.append(path)
        total += entry["source_size"]
    if paths != sorted(set(paths)) or total > source_gma.MAX_PAYLOAD_BYTES:
        raise ValueError("invalid target closed set/aggregate")
    sizes = {entry["target_path"]: entry["source_size"] for entry in entries}
    header = source_gma.gma_header(sizes) if codec in ("vvd", "vtf") else asset_header(sizes)
    if header.hex() != contract["cache"]["header_hex"] or len(header) + total + 4 != contract["cache"]["size"]:
        raise ValueError("invalid cache header binding")


def render_runtime(contract: dict) -> bytes:
    _validate(contract)
    verify_vendor()
    codec = contract["codec_family"]
    if codec == "vvd":
        decoder = (_TEMPLATES / "vvd_decoder.lua").read_text(encoding="utf-8")
        old = 'if active[1] ~= physical then error("vvd:lod0") end'
        portable = 'a.fixup_start, a.vertex_start, a.tangent_start)\n    local lanes = {}'
        if decoder.count(old) != 1 or decoder.count(portable) != 1:
            raise ValueError("unexpected shared-header boundary shape")
        decoder = decoder.replace(old, 'if fixups == 0 and active[1] ~= physical then error("vvd:lod0") end')
        decoder = decoder.replace(portable, 'a.fixup_start, a.vertex_start, a.tangent_start)\n    if active[1] ~= a.physical_count then error("vvd:lod0") end\n    local lanes = {}')
    elif codec == "vtf":
        decoder = (_TEMPLATES / "vtf_decoder.lua").read_text(encoding="utf-8")
    else:
        decoder = LUA_DECODER
    raw = json_bytes(contract)
    pointer = "{path=" + json.dumps(contract_path(contract)) + ",size=" + str(len(raw)) + ",sha256=" + json.dumps(hashlib.sha256(raw).hexdigest()) + "}"
    guard = ('if SERVER then AddCSLuaFile() return end' if codec in ("vtf", "lzvtf") else
             'if SERVER then AddCSLuaFile() if not game.IsDedicated() then return end end')
    output = (_TEMPLATES / "runtime.lua.in").read_text(encoding="utf-8")
    output = output.replace("codex_tacrp_portability", NAMESPACE)
    output = output.replace("CODEX_TACRP_PORTABILITY", "CODEX_EXACT_DISTRIBUTION_" + contract["content_id"])
    output = output.replace("tacrp-compression-portability-runtime-receipt/v1", "exact-distribution-runtime-receipt/v1")
    old_alias = '"data_static/' + NAMESPACE + '/" .. CONTRACT.family .. "_support.gma"'
    if output.count(old_alias) != 1:
        raise ValueError("unexpected support alias shape")
    output = output.replace(old_alias, json.dumps(support_alias(contract)))
    output = output.replace("@@REALM_GUARD@@", guard).replace("@@CONTRACT_POINTER@@", pointer).replace("@@DECODER@@", decoder)
    if "@@" in output:
        raise ValueError("unresolved runtime token")
    return output.encode("utf-8")
