import re
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal


class ProcessRunner(QObject):
    log_line = Signal(str)
    progress_changed = Signal(int)
    phase_changed = Signal(str)
    finished = Signal(int)
    output_path = Signal(str)
    preview_summary = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_output)
        self._proc.finished.connect(self._on_finished)
        self._buffer = ""
        self._step_index = None
        self._step_total = None
        self._phase = ""
        self._item_current = None
        self._item_total = None
        self._step_weights = {1: 0.15, 2: 0.55, 3: 0.25}
        self._packaging_weight = 0.05

        self._re_step = re.compile(r"^== Step (\d+)/(\d+): (.+) ==$")
        self._re_preview_step = re.compile(r"^== Preview Step (\d+)/(\d+): (.+) ==$")
        self._re_packaging = re.compile(r"^== Packaging: (.+) ==$")
        self._re_item = re.compile(r"^=== \((\d+)/(\d+)\) (MDL|QC):")
        self._re_found_mdl = re.compile(r"Found\s+(\d+)\s+\.mdl\s+file", re.IGNORECASE)
        self._re_found_qc = re.compile(r"(?:Found|Encontrados?)\s+(\d+)\s+.*QC", re.IGNORECASE)
        self._re_output = re.compile(r"^Output addon:\\s+(.+)$")
        self._re_preview_output = re.compile(r"^PREVIEW_OUTPUT_DIR:\\s+(.+)$")
        self._re_preview_summary = re.compile(r"^PREVIEW_SUMMARY:\\s+(.+)$")

    def is_running(self) -> bool:
        return self._proc.state() != QProcess.NotRunning

    def start(self, cmd: list[str], workdir: Path | None = None, env: dict | None = None):
        self._buffer = ""
        self._step_index = None
        self._step_total = None
        self._phase = ""
        self._item_current = None
        self._item_total = None
        self.progress_changed.emit(0)
        if workdir:
            self._proc.setWorkingDirectory(str(workdir))
        if env:
            qenv = QProcessEnvironment.systemEnvironment()
            for k, v in env.items():
                qenv.insert(k, v)
            self._proc.setProcessEnvironment(qenv)
        self._proc.start(cmd[0], cmd[1:])

    def stop(self):
        if not self.is_running():
            return
        self._proc.terminate()
        QTimer.singleShot(3000, self._kill_if_needed)

    def _kill_if_needed(self):
        if self.is_running():
            self._proc.kill()

    def _on_output(self):
        data = self._proc.readAllStandardOutput().data().decode("utf-8", errors="replace")
        if not data:
            return
        self._buffer += data
        lines = self._buffer.splitlines(True)
        if not lines:
            return
        if not lines[-1].endswith(("\n", "\r")):
            self._buffer = lines.pop()
        else:
            self._buffer = ""
        for raw in lines:
            line = raw.rstrip("\r\n")
            if line:
                self.log_line.emit(line)
                self._parse_progress(line)

    def _parse_progress(self, line: str):
        m = self._re_step.match(line)
        if m:
            self._step_index = int(m.group(1))
            self._step_total = int(m.group(2))
            self._phase = m.group(3).strip()
            self._item_current = None
            self._item_total = None
            self.phase_changed.emit(self._phase)
            self._emit_progress()
            return

        m = self._re_preview_step.match(line)
        if m:
            self._step_index = int(m.group(1))
            self._step_total = int(m.group(2))
            self._phase = m.group(3).strip()
            self._item_current = None
            self._item_total = None
            self.phase_changed.emit(self._phase)
            self._emit_progress()
            return

        m = self._re_packaging.match(line)
        if m:
            self._phase = m.group(1).strip()
            self.phase_changed.emit(self._phase)
            self._emit_progress(self._packaging_base())
            return

        m = self._re_item.match(line)
        if m:
            self._item_current = int(m.group(1))
            self._item_total = int(m.group(2))
            self._emit_progress()
            return

        m = self._re_found_mdl.match(line)
        if m:
            self._item_total = int(m.group(1))
            self._emit_progress()
            return

        m = self._re_found_qc.match(line)
        if m:
            self._item_total = int(m.group(1))
            self._emit_progress()
            return

        m = self._re_output.match(line)
        if m:
            self.output_path.emit(m.group(1).strip())
            return

        m = self._re_preview_output.match(line)
        if m:
            self.output_path.emit(m.group(1).strip())
            return

        m = self._re_preview_summary.match(line)
        if m:
            self.preview_summary.emit(m.group(1).strip())

    def _emit_progress(self, force_value: float | None = None):
        if force_value is not None:
            val = max(0, min(100, int(force_value * 100)))
            self.progress_changed.emit(val)
            return
        if not self._step_index or not self._step_total:
            return
        base = self._completed_weight()
        frac = 0.0
        if self._item_current and self._item_total:
            frac = min(1.0, self._item_current / float(self._item_total))
        weight = self._current_step_weight()
        overall = base + (weight * frac)
        val = max(0, min(100, int(overall * 100)))
        self.progress_changed.emit(val)

    def _current_step_weight(self) -> float:
        if self._step_total == 3 and self._step_index in self._step_weights:
            return self._step_weights[self._step_index]
        return 1.0 / float(self._step_total)

    def _completed_weight(self) -> float:
        if self._step_total == 3:
            total = 0.0
            for i in range(1, self._step_index):
                total += self._step_weights.get(i, 0.0)
            return total
        return (self._step_index - 1) / float(self._step_total)

    def _packaging_base(self) -> float:
        if self._step_total == 3:
            return min(1.0, sum(self._step_weights.values()))
        return 0.95

    def _on_finished(self, exit_code: int, _status):
        if self._buffer.strip():
            for raw in self._buffer.splitlines():
                line = raw.strip()
                if not line:
                    continue
                self.log_line.emit(line)
                self._parse_progress(line)
            self._buffer = ""
        self.finished.emit(exit_code)
