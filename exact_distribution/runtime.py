"""Deterministic, content-isolated exact-distribution runtime renderer."""
from __future__ import annotations

import json
import re


_SHA = re.compile(r"[0-9a-f]{64}\Z")


def render_runtime(contract: dict) -> bytes:
    if not isinstance(contract, dict) or contract.get("schema") != "exact-distribution-runtime/v1":
        raise ValueError("invalid runtime contract schema")
    cid = contract.get("content_id")
    family = contract.get("family")
    shard = contract.get("shard")
    entries = contract.get("entries")
    if not isinstance(cid, str) or not _SHA.fullmatch(cid):
        raise ValueError("invalid content id")
    if not isinstance(family, str) or not re.fullmatch(r"[a-z0-9_]+", family):
        raise ValueError("invalid family")
    if not isinstance(shard, int) or isinstance(shard, bool) or shard < 0:
        raise ValueError("invalid shard")
    if not isinstance(entries, list) or len(entries) > 2048:
        raise ValueError("invalid entries")
    payload = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    namespace = f"codex_exact_distribution_{cid}_{family}_{shard}"
    # Runtime deliberately fails closed unless its authenticated host bootstrap is
    # available. The contract remains embedded and content-ID isolated.
    return (
        "if SERVER then AddCSLuaFile() return end\n"
        f"local namespace={json.dumps(namespace)}\n"
        f"local contract_json={json.dumps(payload)}\n"
        "local host=_G.CODEX_EXACT_DISTRIBUTION_HOST\n"
        "if type(host)~='table' or type(host.run)~='function' then error(namespace..':host') end\n"
        "host.run(namespace,contract_json)\n"
    ).encode("ascii")

