from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from maximum_optimizer.benchmarking import canonical_json_bytes
from maximum_optimizer.dx90_runtime import (
    Dx90RuntimeError,
    build_runtime_plan,
    console_has_runtime_lua_error,
    parse_gmod_tasklist_pids,
    runtime_outputs_ready,
    stage_runtime_probe,
    steam_build_fingerprint,
    verify_capture_pairs,
    verify_animation_capture_pairs,
    verify_realm_reports,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _gmod_pids() -> tuple[int, ...]:
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq gmod.exe", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return parse_gmod_tasklist_pids(completed.stdout) if completed.returncode == 0 else ()


def _gmod_running() -> bool:
    return bool(_gmod_pids())


def _load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise Dx90RuntimeError(f"JSON object required: {path}")
    return raw


def _launch_hidden(arguments: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return subprocess.Popen(
        arguments,
        cwd=cwd,
        startupinfo=startupinfo,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        capture_output=True,
    )
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _terminate_pids(pids: set[int]) -> None:
    for pid in sorted(pids):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )


def run(args: argparse.Namespace) -> int:
    experiment_path = args.experiment.resolve(strict=True)
    candidates = args.candidates.resolve(strict=True)
    install = args.gmod_root.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.parent.resolve(strict=True) != output.parent:
        raise Dx90RuntimeError("output must be a new child of an existing exact parent")
    experiment = _load_json(experiment_path)
    manifest_sha = experiment.get("candidate_manifest", {}).get("sha256", "")
    run_id = f"proof-{manifest_sha[:16]}"
    plan = build_runtime_plan(experiment, candidates, run_id)
    if _gmod_running():
        raise Dx90RuntimeError("refusing to interfere with an existing gmod.exe process")
    console_paths = (
        install / "garrysmod/console.log",
        install / "garrysmod/console.txt",
    )
    if any(os.path.lexists(path) for path in console_paths):
        raise Dx90RuntimeError("a Garry's Mod condebug log already exists; refusing to overwrite user data")
    temp_output = output.parent / f".{output.name}.{run_id}.tmp"
    if os.path.lexists(temp_output):
        raise Dx90RuntimeError(f"temporary evidence path already exists: {temp_output}")
    temp_output.mkdir()

    appmanifest = install.parents[1] / "appmanifest_4000.acf"
    if not appmanifest.is_file():
        raise Dx90RuntimeError(f"Steam app manifest not found: {appmanifest}")
    appmanifest_text = appmanifest.read_text(encoding="utf-8", errors="strict")
    build_fingerprint = steam_build_fingerprint(appmanifest_text)
    appmanifest_sha256_before = _sha256(appmanifest)

    engine = install / "gmod.exe"
    script_path = Path(__file__).resolve(strict=True)
    launch_arguments = [
        str(engine),
        "-noaddons",
        "-noworkshop",
        "-disableluarefresh",
        "-insecure",
        "-windowed",
        "-noborder",
        "-w",
        "800",
        "-h",
        "600",
        "-novid",
        "-nosound",
        "-conclearlog",
        "-condebug",
        "-console",
        "-dev",
        "+map",
        "gm_flatgrass",
    ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "pending",
        "reason": None,
        "run_id": run_id,
        "target": "gmod_dynamic_runtime",
        "corpus_id": plan.corpus_id,
        "family_ids": list(plan.family_ids),
        "candidate_manifest_sha256": plan.manifest_sha256,
        "engine_executable_sha256": _sha256(engine),
        "engine_build_id": build_fingerprint["build_id"],
        "engine_build_sha256": build_fingerprint["sha256"],
        "engine_build_fingerprint": build_fingerprint,
        "appmanifest_sha256_before": appmanifest_sha256_before,
        "appmanifest_sha256_after": None,
        "appmanifest_changed_by_steam": None,
        "stable_build_identity_unchanged": None,
        "launcher_script_sha256": _sha256(script_path),
        "lua_sha256": None,
        "launch_arguments": launch_arguments[1:],
        "hidden_window": True,
        "timeout_seconds": args.timeout,
        "process_exit_code": None,
        "observed_gmod_pids": [],
        "runtime_processes_exited": False,
        "external_completion_termination": False,
        "timed_out": False,
        "elapsed_seconds": None,
        "candidate_artifacts": [
            {
                "family_id": item.family_id,
                "staged_path": item.staged_relative,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in plan.artifacts
        ],
        "server_report_sha256": None,
        "client_report_sha256": None,
        "console_sha256": None,
        "capture_metrics": [],
        "capabilities": [],
        "cleanup_verified": False,
    }
    staged = None
    process = None
    observed_pids: set[int] = set()
    runtime_console_error: str | None = None
    started = time.monotonic()
    try:
        staged = stage_runtime_probe(plan, install)
        report["lua_sha256"] = staged.lua_sha256
        process = _launch_hidden(launch_arguments, install)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            current_pids = set(_gmod_pids())
            observed_pids.update(current_pids)
            reports_ready = runtime_outputs_ready(plan, staged.data_root)
            live_console = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in console_paths
                if path.is_file()
            )
            if console_has_runtime_lua_error(live_console):
                runtime_console_error = "generated runtime Lua error detected in console"
                _terminate_pids(observed_pids | current_pids)
                time.sleep(0.5)
                break
            if reports_ready:
                # Garry's Mod blocks Lua's quit command in this configuration. The
                # complete immutable output set is the sentinel; terminate only the
                # PIDs observed after our no-existing-process precondition.
                report["external_completion_termination"] = True
                _terminate_pids(observed_pids | current_pids)
                time.sleep(0.5)
                break
            # The top-level launcher exits before bin/win64/gmod.exe appears. Allow
            # a bounded bootstrap grace period instead of treating exit 0 as runtime completion.
            if (
                process.poll() is not None
                and not current_pids
                and not reports_ready
                and time.monotonic() - started > 15
            ):
                break
            time.sleep(0.5)
        else:
            report["timed_out"] = True
        report["process_exit_code"] = process.poll()
        report["observed_gmod_pids"] = sorted(observed_pids)
        report["runtime_processes_exited"] = not bool(_gmod_pids())
        if report["timed_out"]:
            _terminate_pids(observed_pids | set(_gmod_pids()))
        report["elapsed_seconds"] = time.monotonic() - started

        raw_root = temp_output / "raw"
        raw_root.mkdir()
        if staged.data_root.is_dir():
            for source in staged.data_root.iterdir():
                if source.is_file():
                    shutil.copyfile(source, raw_root / source.name)
        for console_path in console_paths:
            if console_path.is_file():
                shutil.copyfile(console_path, raw_root / console_path.name)

        if report["timed_out"]:
            raise Dx90RuntimeError("Garry's Mod runtime probe timed out")
        if runtime_console_error:
            raise Dx90RuntimeError(runtime_console_error)
        if report["process_exit_code"] != 0:
            raise Dx90RuntimeError(f"Garry's Mod exited with code {report['process_exit_code']}")
        server_path = raw_root / "server.json"
        client_path = raw_root / "client.json"
        copied_logs = tuple(path for path in (raw_root / "console.log", raw_root / "console.txt") if path.is_file())
        if not server_path.is_file() or not client_path.is_file() or not copied_logs:
            raise Dx90RuntimeError("runtime did not produce both realm reports and complete console log")
        server = _load_json(server_path)
        client = _load_json(client_path)
        console_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace") for path in copied_logs
        )
        report["server_report_sha256"] = _sha256(server_path)
        report["client_report_sha256"] = _sha256(client_path)
        report["console_sha256"] = hashlib.sha256(console_text.encode("utf-8")).hexdigest()
        report["capture_metrics"] = {
            "rendering": list(verify_capture_pairs(plan.family_ids, raw_root)),
            "animation": list(verify_animation_capture_pairs(plan.family_ids, raw_root)),
        }
        report["capabilities"] = list(verify_realm_reports(plan, server, client, console_text))
        report["status"] = "proven"
    except Exception as exc:
        report["reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        if process is not None:
            _terminate_tree(process)
        _terminate_pids(observed_pids | set(_gmod_pids()))
        if staged is not None:
            staged.cleanup()
        for console_path in console_paths:
            if console_path.is_file():
                console_path.unlink()
        after_text = appmanifest.read_text(encoding="utf-8", errors="strict")
        after_fingerprint = steam_build_fingerprint(after_text)
        report["appmanifest_sha256_after"] = _sha256(appmanifest)
        report["appmanifest_changed_by_steam"] = (
            report["appmanifest_sha256_after"] != appmanifest_sha256_before
        )
        report["stable_build_identity_unchanged"] = after_fingerprint == build_fingerprint
        if not report["stable_build_identity_unchanged"]:
            report["status"] = "pending"
            report["reason"] = "Steam build/depot/branch identity changed during runtime proof"
        report["cleanup_verified"] = not any(
            os.path.lexists(path)
            for path in (
                install / "garrysmod/models/maximum_dx90_runtime" / run_id,
                install / "garrysmod/lua/autorun" / f"maximum_dx90_runtime_{run_id}.lua",
                install / "garrysmod/data/maximum_dx90_runtime" / run_id,
                *console_paths,
            )
        )
        if not report["cleanup_verified"]:
            report["status"] = "pending"
            report["reason"] = "cleanup verification failed; inspect reserved paths before another run"
        (temp_output / "runtime-proof.json").write_bytes(canonical_json_bytes(report))
        temp_output.rename(output)
    print(json.dumps({"status": report["status"], "reason": report["reason"], "output": str(output)}))
    return 0 if report["status"] == "proven" else 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run isolated DX90-only Garry's Mod proof")
    result.add_argument("--experiment", type=Path, required=True)
    result.add_argument("--candidates", type=Path, required=True)
    result.add_argument("--gmod-root", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--timeout", type=int, default=180)
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(run(parser().parse_args()))
    except (Dx90RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"DX90 runtime proof refused: {exc}", file=sys.stderr)
        raise SystemExit(2)
