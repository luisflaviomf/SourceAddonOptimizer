from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Event


@dataclass(frozen=True)
class ProcessResult:
    command: tuple[str, ...]
    returncode: int
    elapsed_seconds: float
    log_path: Path


class ProcessCancelledError(RuntimeError):
    """Raised after a cancelled child process and its descendants are reaped."""


def _normalize_command(command: Sequence[str | os.PathLike[str]]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)):
        raise TypeError("command must be a sequence, not a shell string")
    normalized = tuple(os.fspath(item) for item in command)
    if not normalized or any(not isinstance(item, str) or not item for item in normalized):
        raise ValueError("command must contain non-empty string or path arguments")
    return normalized


def _quoted_command(command: tuple[str, ...]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def _write_log(log_path: Path, command: tuple[str, ...], stdout: str, stderr: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"Command: {_quoted_command(command)}\n\n"
        f"== stdout ==\n{stdout}"
        f"{'' if stdout.endswith(chr(10)) or not stdout else chr(10)}\n"
        f"== stderr ==\n{stderr}"
        f"{'' if stderr.endswith(chr(10)) or not stderr else chr(10)}",
        encoding="utf-8",
        errors="replace",
    )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def run_process(
    command: Sequence[str | os.PathLike[str]],
    cwd: str | os.PathLike[str],
    log_path: str | os.PathLike[str],
    cancel_event: Event,
) -> ProcessResult:
    normalized = _normalize_command(command)
    working_directory = Path(cwd).expanduser().resolve()
    destination = Path(log_path)
    if not working_directory.is_dir():
        raise ValueError(f"cwd is not an existing directory: {working_directory}")
    if cancel_event.is_set():
        raise ProcessCancelledError("process cancelled before launch")

    popen_options: dict[str, object] = {}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True

    started = time.monotonic()
    process = subprocess.Popen(
        list(normalized),
        cwd=working_directory,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_options,
    )
    stdout = ""
    stderr = ""
    cancelled = False
    try:
        while True:
            if cancel_event.is_set():
                cancelled = True
                _terminate_process_tree(process)
                break
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.poll() is None:
            stdout, stderr = process.communicate()
        elif cancelled:
            stdout, stderr = process.communicate()
    finally:
        if process.poll() is None:
            _terminate_process_tree(process)
            extra_stdout, extra_stderr = process.communicate()
            stdout += extra_stdout
            stderr += extra_stderr
        _write_log(destination, normalized, stdout, stderr)

    elapsed = time.monotonic() - started
    if cancelled:
        raise ProcessCancelledError(f"process cancelled: {_quoted_command(normalized)}")
    return ProcessResult(normalized, int(process.returncode), elapsed, destination)
