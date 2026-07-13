from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
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
    launch_private_process_job,
    owned_process_identity_matches,
    process_identities_by_name,
    runtime_outputs_ready,
    stage_runtime_probe,
    steam_build_fingerprint,
    seal_runtime_proof_report,
    verify_runtime_proof_bundle,
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


def _external_gmod_processes() -> tuple[dict[str, Any], ...]:
    return process_identities_by_name("gmod.exe")


def _terminate_owned_breakaways(
    identities: dict[tuple[int, float], dict[str, Any]],
    *,
    owner_marker: str,
    launched_after: float,
    allowed_executables: dict[str, str],
) -> bool:
    import psutil

    processes = []
    owned_parent_pids = {item["pid"] for item in identities.values()}
    for identity in identities.values():
        try:
            process = psutil.Process(identity["pid"])
            current = {
                "pid": process.pid, "parent_pid": process.ppid(),
                "create_time": process.create_time(), "exe_path": str(Path(process.exe()).resolve(strict=True)),
                "exe_sha256": _sha256(Path(process.exe()).resolve(strict=True)),
                "cmdline": list(process.cmdline()),
            }
            if (
                current["create_time"] != identity["create_time"]
                or not owned_process_identity_matches(
                    current, owner_marker=owner_marker, launched_after=launched_after,
                    allowed_executables=allowed_executables,
                    owned_parent_pids=owned_parent_pids,
                )
            ):
                raise Dx90RuntimeError("refusing to terminate changed breakaway process identity")
            process.terminate()
            processes.append(process)
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(processes, timeout=5.0)
    for process in alive:
        try:
            if process.create_time() != next(
                item["create_time"] for item in identities.values() if item["pid"] == process.pid
            ):
                raise Dx90RuntimeError("refusing to kill reused breakaway PID")
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=5.0)
    return not alive


def _load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise Dx90RuntimeError(f"JSON object required: {path}")
    return raw


def run(args: argparse.Namespace) -> int:
    experiment_path = args.experiment.resolve(strict=True)
    candidates = args.candidates.resolve(strict=True)
    install = args.gmod_root.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.parent.resolve(strict=True) != output.parent:
        raise Dx90RuntimeError("output must be a new child of an existing exact parent")
    experiment = _load_json(experiment_path)
    manifest_sha = experiment.get("candidate_manifest", {}).get("sha256", "")
    if _external_gmod_processes():
        raise Dx90RuntimeError("refusing to interfere with an existing gmod.exe process")
    console_paths = (install / "garrysmod/console.log", install / "garrysmod/console.txt")
    if any(os.path.lexists(path) for path in console_paths):
        raise Dx90RuntimeError("a Garry's Mod condebug log already exists; refusing to overwrite user data")
    appmanifest = install.parents[1] / "appmanifest_4000.acf"
    if not appmanifest.is_file():
        raise Dx90RuntimeError(f"Steam app manifest not found: {appmanifest}")
    appmanifest_text = appmanifest.read_text(encoding="utf-8", errors="strict")
    build_fingerprint = steam_build_fingerprint(appmanifest_text)
    appmanifest_sha256_before = _sha256(appmanifest)

    launcher = install / "gmod.exe"
    runtime_executable = install / "bin/win64/gmod.exe"
    if any(not path.is_file() or path.is_symlink() for path in (launcher, runtime_executable)):
        raise Dx90RuntimeError("exact launcher and bin/win64 runtime executables are required")
    launcher_sha256 = _sha256(launcher)
    runtime_executable_sha256 = _sha256(runtime_executable)
    proof_nonce = secrets.token_hex(32)
    process_owner_marker = "maximum_dx90_process_owner_" + proof_nonce
    run_id = f"proof-{manifest_sha[:12]}-{proof_nonce[:16]}"
    plan = build_runtime_plan(
        experiment,
        candidates,
        run_id,
        proof_nonce=proof_nonce,
        engine_build_sha256=build_fingerprint["sha256"],
        runtime_executable_sha256=runtime_executable_sha256,
    )
    temp_output = output.parent / f".{output.name}.{run_id}.tmp"
    if os.path.lexists(temp_output):
        raise Dx90RuntimeError(f"temporary evidence path already exists: {temp_output}")
    temp_output.mkdir()
    script_path = Path(__file__).resolve(strict=True)
    launcher_script_sha256 = _sha256(script_path)
    launch_arguments = [
        str(launcher),
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
        "+echo",
        process_owner_marker,
    ]
    report: dict[str, Any] = {
        "schema_version": 2,
        "status": "pending",
        "reason": None,
        "run_id": run_id,
        "proof_nonce": proof_nonce,
        "target": "gmod_dynamic_runtime",
        "corpus_id": plan.corpus_id,
        "family_ids": list(plan.family_ids),
        "candidate_manifest_sha256": plan.manifest_sha256,
        "launcher_executable_sha256": launcher_sha256,
        "runtime_executable_sha256": runtime_executable_sha256,
        "launcher_executable_sha256_after": None,
        "runtime_executable_sha256_after": None,
        "engine_build_id": build_fingerprint["build_id"],
        "engine_build_sha256": build_fingerprint["sha256"],
        "engine_build_fingerprint": build_fingerprint,
        "appmanifest_sha256_before": appmanifest_sha256_before,
        "appmanifest_sha256_after": None,
        "stable_build_identity_unchanged": None,
        "launcher_script_sha256": launcher_script_sha256,
        "lua_sha256": None,
        "launch_arguments": launch_arguments[1:],
        "hidden_window": True,
        "timeout_seconds": args.timeout,
        "process_exit_code": None,
        "observed_processes": [],
        "runtime_processes_exited": False,
        "external_completion_termination": False,
        "timed_out": False,
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
        "raw_inventory": [],
    }
    staged = None
    job = None
    observed_processes: dict[tuple[int, float], dict[str, Any]] = {}
    breakaway_processes: dict[tuple[int, float], dict[str, Any]] = {}
    runtime_console_error: str | None = None
    unowned_process_error: str | None = None
    try:
        staged = stage_runtime_probe(plan, install)
        report["lua_sha256"] = staged.lua_sha256
        launched_after = time.time() - 1.0
        job = launch_private_process_job(launch_arguments, install)
        allowed_paths = {
            str(launcher.resolve(strict=True)).replace("\\", "/").casefold(): launcher_sha256,
            str(runtime_executable.resolve(strict=True)).replace("\\", "/").casefold(): runtime_executable_sha256,
        }
        started = time.monotonic()
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            active_pids = set(job.active_pids())
            for identity in job.process_identities():
                expected_hash = allowed_paths.get(
                    identity["exe_path"].replace("\\", "/").casefold()
                )
                if expected_hash != identity["exe_sha256"]:
                    unowned_process_error = "private job contains an unexpected executable identity"
                    break
                observed_processes[(identity["pid"], identity["create_time"])] = identity
            external = tuple(
                item for item in _external_gmod_processes() if item["pid"] not in active_pids
            )
            pending_external = list(external)
            while pending_external:
                owned_parent_pids = active_pids | {
                    item["pid"] for item in observed_processes.values()
                }
                adopted = []
                for identity in pending_external:
                    if owned_process_identity_matches(
                        identity, owner_marker=process_owner_marker,
                        launched_after=launched_after, allowed_executables=allowed_paths,
                        owned_parent_pids=owned_parent_pids,
                    ):
                        key = (identity["pid"], identity["create_time"])
                        breakaway_processes[key] = identity
                        observed_processes[key] = identity
                        adopted.append(identity)
                if not adopted:
                    unowned_process_error = "unowned gmod.exe appeared during proof"
                    break
                pending_external = [item for item in pending_external if item not in adopted]
            if unowned_process_error:
                job.terminate()
                job.wait_empty(10.0)
                _terminate_owned_breakaways(
                    breakaway_processes, owner_marker=process_owner_marker,
                    launched_after=launched_after, allowed_executables=allowed_paths,
                )
                break
            reports_ready = runtime_outputs_ready(plan, staged.data_root)
            live_console = console_paths[0].read_text(encoding="utf-8", errors="replace") if console_paths[0].is_file() else ""
            if console_has_runtime_lua_error(live_console):
                runtime_console_error = "generated runtime Lua error detected in console"
                job.terminate()
                job.wait_empty(10.0)
                _terminate_owned_breakaways(
                    breakaway_processes, owner_marker=process_owner_marker,
                    launched_after=launched_after, allowed_executables=allowed_paths,
                )
                break
            if reports_ready:
                report["external_completion_termination"] = True
                job.terminate()
                job.wait_empty(10.0)
                _terminate_owned_breakaways(
                    breakaway_processes, owner_marker=process_owner_marker,
                    launched_after=launched_after, allowed_executables=allowed_paths,
                )
                break
            living_breakaways = tuple(
                item for item in _external_gmod_processes()
                if (item["pid"], item["create_time"]) in breakaway_processes
            )
            if not active_pids and not living_breakaways and not reports_ready and time.monotonic() - started > 15:
                break
            time.sleep(0.5)
        else:
            report["timed_out"] = True
        if job.active_pids():
            job.terminate()
        breakaways_exited = _terminate_owned_breakaways(
            breakaway_processes, owner_marker=process_owner_marker,
            launched_after=launched_after, allowed_executables=allowed_paths,
        )
        report["runtime_processes_exited"] = job.wait_empty(10.0) and breakaways_exited
        report["process_exit_code"] = job.root_exit_code()
        report["observed_processes"] = sorted((
            {key: value for key, value in item.items() if key != "cmdline"}
            for item in observed_processes.values()
        ), key=lambda item: (item["create_time"], item["pid"]))

        raw_root = temp_output / "raw"
        raw_root.mkdir()
        if staged.data_root.is_dir():
            for source in staged.data_root.iterdir():
                if source.is_file():
                    shutil.copyfile(source, raw_root / source.name)
        if console_paths[0].is_file():
            shutil.copyfile(console_paths[0], raw_root / "console.log")

        if report["timed_out"]:
            raise Dx90RuntimeError("Garry's Mod runtime probe timed out")
        if unowned_process_error:
            raise Dx90RuntimeError(unowned_process_error)
        if runtime_console_error:
            raise Dx90RuntimeError(runtime_console_error)
        if not report["runtime_processes_exited"]:
            raise Dx90RuntimeError("private runtime job did not exit completely")
        server_path = raw_root / "server.json"
        client_path = raw_root / "client.json"
        console_copy = raw_root / "console.log"
        if not server_path.is_file() or not client_path.is_file() or not console_copy.is_file():
            raise Dx90RuntimeError("runtime did not produce both realm reports and complete console log")
        server = _load_json(server_path)
        client = _load_json(client_path)
        console_text = console_copy.read_text(encoding="utf-8", errors="strict")
        report["server_report_sha256"] = _sha256(server_path)
        report["client_report_sha256"] = _sha256(client_path)
        report["console_sha256"] = _sha256(console_copy)
        report["capture_metrics"] = {
            "rendering": list(verify_capture_pairs(plan.family_ids, raw_root)),
            "animation": list(verify_animation_capture_pairs(plan.family_ids, raw_root)),
        }
        report["capabilities"] = list(verify_realm_reports(plan, server, client, console_text))
        report["status"] = "proven"
    except Exception as exc:
        report["reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        if job is not None:
            if job.active_pids():
                job.terminate()
            breakaways_exited = _terminate_owned_breakaways(
                breakaway_processes, owner_marker=process_owner_marker,
                launched_after=launched_after, allowed_executables=allowed_paths,
            )
            report["runtime_processes_exited"] = job.wait_empty(10.0) and breakaways_exited
            report["process_exit_code"] = job.root_exit_code()
            job.close()
        cleanup_error = None
        if staged is not None:
            try:
                staged.cleanup()
            except Exception as exc:
                cleanup_error = f"staged cleanup failed: {exc}"
        owner_marker = "maximum_dx90_console_owner_" + proof_nonce
        for console_path in console_paths:
            if not os.path.lexists(console_path):
                continue
            try:
                if (
                    console_path.is_symlink() or not console_path.is_file()
                    or owner_marker not in console_path.read_text("utf-8", errors="strict")
                    or _external_gmod_processes()
                ):
                    raise Dx90RuntimeError("refusing to delete unowned console log")
                console_path.unlink()
            except Exception as exc:
                cleanup_error = cleanup_error or str(exc)
        after_text = appmanifest.read_text(encoding="utf-8", errors="strict")
        after_fingerprint = steam_build_fingerprint(after_text)
        report["appmanifest_sha256_after"] = _sha256(appmanifest)
        report["launcher_executable_sha256_after"] = _sha256(launcher)
        report["runtime_executable_sha256_after"] = _sha256(runtime_executable)
        report["stable_build_identity_unchanged"] = after_fingerprint == build_fingerprint
        if (
            not report["stable_build_identity_unchanged"]
            or report["launcher_executable_sha256_after"] != launcher_sha256
            or report["runtime_executable_sha256_after"] != runtime_executable_sha256
        ):
            report["status"] = "pending"
            report["reason"] = "Steam or executable identity changed during runtime proof"
        report["cleanup_verified"] = not any(
            os.path.lexists(path)
            for path in (
                install / "garrysmod/models/maximum_dx90_runtime" / run_id,
                install / "garrysmod/lua/autorun" / f"maximum_dx90_runtime_{run_id}.lua",
                install / "garrysmod/data/maximum_dx90_runtime" / run_id,
                *console_paths,
            )
        ) and report["runtime_processes_exited"] is True and cleanup_error is None
        if not report["cleanup_verified"]:
            report["status"] = "pending"
            report["reason"] = cleanup_error or "cleanup verification failed; inspect reserved paths before another run"
        raw_root = temp_output / "raw"
        if raw_root.is_dir():
            report["raw_inventory"] = [
                {"path": path.name, "size_bytes": path.stat().st_size, "sha256": _sha256(path)}
                for path in sorted(raw_root.iterdir(), key=lambda item: item.name)
                if path.is_file() and not path.is_symlink()
            ]
        sealed = seal_runtime_proof_report(report)
        proof_path = temp_output / "runtime-proof.json"
        proof_path.write_bytes(canonical_json_bytes(sealed))
        proof_sha256 = _sha256(proof_path)
        if report["status"] == "proven":
            try:
                verify_runtime_proof_bundle(
                    temp_output,
                    plan,
                    expected_proof_sha256=proof_sha256,
                    expected_launcher_script_sha256=launcher_script_sha256,
                )
            except Exception as exc:
                report["status"] = "pending"
                report["reason"] = f"strict proof replay failed: {type(exc).__name__}: {exc}"
                sealed = seal_runtime_proof_report(report)
                proof_path.write_bytes(canonical_json_bytes(sealed))
                proof_sha256 = _sha256(proof_path)
        temp_output.rename(output)
    print(json.dumps({
        "status": report["status"], "reason": report["reason"],
        "output": str(output), "runtime_proof_sha256": proof_sha256,
    }))
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
