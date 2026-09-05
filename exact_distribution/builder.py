"""Generic deterministic exact candidate export (never installs candidates)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath

from .codecs import encode_options, verify_vendor
from .runtime import render_runtime


MARKER = "data_static/codex_exact_distribution/manifest.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(a: Path, b: Path) -> bool:
    try: a.relative_to(b); return True
    except ValueError: return False


def _checked_root(path: Path, *, existing: bool) -> Path:
    path = Path(path).absolute()
    probe = path if path.exists() else path.parent
    while True:
        if probe.is_symlink(): raise ValueError(f"reparse path refused: {path}")
        if probe == probe.parent: break
        probe = probe.parent
    if existing and not path.is_dir(): raise ValueError(f"directory missing: {path}")
    return path


def _inventory(source: Path) -> list[dict]:
    rows=[]; folded={}
    for path in sorted(source.rglob("*"), key=lambda p: p.relative_to(source).as_posix().encode()):
        if path.is_symlink(): raise ValueError(f"reparse source refused: {path}")
        if not path.is_file(): continue
        relative=path.relative_to(source).as_posix()
        key=relative.casefold()
        if key in folded: raise ValueError(f"case-fold collision: {folded[key]} and {relative}")
        folded[key]=relative
        data=path.read_bytes()
        rows.append({"source_path":relative,"size":len(data),"sha256":_sha(data)})
    return rows


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream: stream.write(data)


def build_candidate(source: Path, output: Path, work: Path, engine_root: Path, progress=None) -> dict:
    source=_checked_root(source,existing=True); engine_root=_checked_root(engine_root,existing=True)
    output=_checked_root(output,existing=False); work=_checked_root(work,existing=False)
    for a,b in ((output,source),(source,output),(work,source),(source,work),(output,work),(work,output)):
        if _inside(a,b): raise ValueError("source/work/output overlap refused")
    if output.exists(): raise FileExistsError(output)
    if work.exists(): raise FileExistsError(work)
    rows=_inventory(source)
    if any(r["source_path"].casefold()==MARKER for r in rows): raise ValueError("prepacked runtime refused")
    verify_vendor()
    excluded=[]; eligible=[]
    total=len(rows)
    for index,row in enumerate(rows,1):
        rel=row["source_path"]
        if progress: progress("inventory",index,total,rel)
        low=rel.lower()
        if low.endswith((".dx80.vtx",".sw.vtx")):
            excluded.append(row); continue
        try: low.encode("ascii"); safe=all(p not in ("",".","..") for p in PurePosixPath(low).parts)
        except UnicodeEncodeError: safe=False
        if not safe: row["decision"]="native_unsafe_path"; continue
        # Read original spelling, target only validated transports canonically.
        raw=(source/Path(*rel.split("/"))).read_bytes()
        try: options=encode_options(low,raw)
        except ValueError: options=[]
        if options:
            option=min(options,key=lambda x:(x["blob_size"],x["codec_domain"]))
            eligible.append((row,low,option))
    source_bytes=sum(r["size"] for r in rows); excluded_bytes=sum(r["size"] for r in excluded)
    # Estimate using the exact bytes that will be emitted, including contracts/runtime.
    selected=[]; blobs={}
    identity={"source":[(r["source_path"],r["size"],r["sha256"]) for r in rows],
              "excluded":[r["source_path"] for r in excluded]}
    cid=_sha(json.dumps(identity,sort_keys=True,separators=(",", ":")).encode())
    for row,target,option in eligible:
        entry={k:option[k] for k in option if k!="blob"}
        entry.update(source_path=row["source_path"],target_path=target,
                     blob_path=f"data_static/codex_exact_distribution/{cid}/blobs/{option['blob_sha256']}.dat")
        contract={"schema":"exact-distribution-runtime/v1","content_id":cid,"family":option["family"],"shard":0,"entries":[entry]}
        overhead=len(render_runtime(contract))+len(json.dumps(contract,sort_keys=True,separators=(",", ":")).encode())
        if row["size"]-option["blob_size"]>overhead:
            selected.append((row,target,option,entry)); blobs.setdefault(option["blob_sha256"],option["blob"])
    def materialize(use_transport: bool):
        output.mkdir()
        selected_paths={r[0]["source_path"] for r in selected} if use_transport else set()
        for row in rows:
            if row in excluded or row["source_path"] in selected_paths: continue
            raw=(source/Path(*row["source_path"].split("/"))).read_bytes()
            if (len(raw),_sha(raw))!=(row["size"],row["sha256"]): raise ValueError("input drift detected")
            _write(output/Path(*row["source_path"].split("/")),raw)
        if use_transport:
            grouped={}
            for _row,_target,opt,entry in selected: grouped.setdefault(opt["family"],[]).append(entry)
            for digest,data in sorted(blobs.items()): _write(output/f"data_static/codex_exact_distribution/{cid}/blobs/{digest}.dat",data)
            for family,entries in sorted(grouped.items()):
                entries.sort(key=lambda e:e["target_path"])
                contract={"schema":"exact-distribution-runtime/v1","content_id":cid,"family":family,"shard":0,"entries":entries}
                raw=json.dumps(contract,sort_keys=True,separators=(",", ":")).encode()+b"\n"
                _write(output/f"data_static/codex_exact_distribution/{cid}/{family}_0.json",raw)
                _write(output/f"lua/autorun/000000_exact_{cid[:12]}_{family}_0.lua",render_runtime(contract))
            manifest={"schema":"exact-distribution-candidate/v1","content_id":cid,"encoded":len(selected)}
            _write(output/MARKER,json.dumps(manifest,sort_keys=True,separators=(",", ":")).encode()+b"\n")
    materialize(bool(selected))
    candidate_bytes=sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
    baseline=source_bytes-excluded_bytes
    fallback=None
    if selected and candidate_bytes>=baseline:
        shutil.rmtree(output); selected.clear(); blobs.clear(); materialize(False)
        candidate_bytes=sum(p.stat().st_size for p in output.rglob("*") if p.is_file()); fallback="whole_candidate_no_net_gain"
    final=_inventory(source)
    if final!=rows: raise ValueError("input drift detected")
    report={"schema":"exact-distribution-report/v1","status":"OFFLINE_PENDING_GMOD","candidate":str(output),
            "content_id":cid,"source_bytes":source_bytes,"candidate_bytes":candidate_bytes,
            "encoded_count":len(selected),"native_count":len(rows)-len(excluded)-len(selected),
            "unique_blob_count":len(blobs),"excluded":{"count":len(excluded),"bytes":excluded_bytes,"paths":[r["source_path"] for r in excluded]},
            "fallback_reason":fallback,"byte_exact":True,"new_quality_loss":False,"installed":False}
    work.mkdir(); report_path=work/"candidate-report.json"; _write(report_path,json.dumps(report,sort_keys=True,separators=(",", ":")).encode()+b"\n")
    report["report_path"]=str(report_path.absolute())
    return report

