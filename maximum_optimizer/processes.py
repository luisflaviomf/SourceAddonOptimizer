from __future__ import annotations

import os
import shlex
import signal
import subprocess
import tempfile
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
    log_path.write_text(
        f"Command: {_quoted_command(command)}\n\n"
        f"== stdout ==\n{stdout}"
        f"{'' if stdout.endswith(chr(10)) or not stdout else chr(10)}\n"
        f"== stderr ==\n{stderr}"
        f"{'' if stderr.endswith(chr(10)) or not stderr else chr(10)}",
        encoding="utf-8",
        errors="replace",
    )


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _WindowsJob:
        def __init__(self) -> None:
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
            self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            self._kernel32.SetInformationJobObject.argtypes = (
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            )
            self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
            self._kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
            self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            self._kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
            self._kernel32.TerminateJobObject.restype = wintypes.BOOL
            self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            self._kernel32.CloseHandle.restype = wintypes.BOOL

            self._handle = self._kernel32.CreateJobObjectW(None, None)
            if not self._handle:
                raise ctypes.WinError(ctypes.get_last_error())
            information = _ExtendedLimitInformation()
            information.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self._kernel32.SetInformationJobObject(
                self._handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                error = ctypes.WinError(ctypes.get_last_error())
                self.close()
                raise error

        def assign(self, process: subprocess.Popen[bytes]) -> None:
            if not self._kernel32.AssignProcessToJobObject(
                self._handle, wintypes.HANDLE(int(process._handle))
            ):
                raise ctypes.WinError(ctypes.get_last_error())

        def terminate(self) -> None:
            if self._handle:
                self._kernel32.TerminateJobObject(self._handle, 1)

        def close(self) -> None:
            if self._handle:
                self._kernel32.CloseHandle(self._handle)
                self._handle = None

else:
    _WindowsJob = None


def _terminate_process_tree(
    process: subprocess.Popen[bytes], windows_job: object | None
) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            pass
        if windows_job is not None:
            windows_job.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _reap_parent(process: subprocess.Popen[bytes], windows_job: object | None) -> None:
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process, windows_job)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _read_capture(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _unlink_capture(path: Path) -> None:
    deadline = time.monotonic() + 2
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


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
    destination.parent.mkdir(parents=True, exist_ok=True)

    windows_job = _WindowsJob() if os.name == "nt" else None
    stdout_fd, stdout_name = tempfile.mkstemp(prefix="stdout-", suffix=".tmp", dir=destination.parent)
    stderr_fd, stderr_name = tempfile.mkstemp(prefix="stderr-", suffix=".tmp", dir=destination.parent)
    stdout_path = Path(stdout_name)
    stderr_path = Path(stderr_name)
    stdout_file = os.fdopen(stdout_fd, "wb", buffering=0)
    stderr_file = os.fdopen(stderr_fd, "wb", buffering=0)
    process: subprocess.Popen[bytes] | None = None
    cancelled = False
    started = time.monotonic()
    try:
        popen_options: dict[str, object] = {}
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True
        process = subprocess.Popen(
            list(normalized),
            cwd=working_directory,
            shell=False,
            stdout=stdout_file,
            stderr=stderr_file,
            **popen_options,
        )
        if windows_job is not None:
            try:
                windows_job.assign(process)
            except Exception:
                _terminate_process_tree(process, windows_job)
                _reap_parent(process, windows_job)
                raise

        while True:
            if process.poll() is not None:
                break
            if cancel_event.is_set():
                cancelled = True
                _terminate_process_tree(process, windows_job)
                break
            time.sleep(0.05)
    finally:
        if process is not None:
            if process.poll() is None:
                _terminate_process_tree(process, windows_job)
            _reap_parent(process, windows_job)
        if windows_job is not None:
            windows_job.close()
        stdout_file.close()
        stderr_file.close()
        stdout = _read_capture(stdout_path)
        stderr = _read_capture(stderr_path)
        _write_log(destination, normalized, stdout, stderr)
        _unlink_capture(stdout_path)
        _unlink_capture(stderr_path)

    if process is None:
        raise RuntimeError("process failed to start")
    elapsed = time.monotonic() - started
    if cancelled:
        raise ProcessCancelledError(f"process cancelled: {_quoted_command(normalized)}")
    return ProcessResult(normalized, int(process.returncode), elapsed, destination)
