import sys
from pathlib import Path

# Ensure repo root is on sys.path when running as a script (python gui/main.py).
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def _load_qss(app: QApplication):
    qss_path = Path(__file__).resolve().parent / "resources" / "theme.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8", errors="ignore"))


def main():
    app = QApplication(sys.argv)
    icon_path = Path(__file__).resolve().parent / "resources" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    _load_qss(app)
    w = MainWindow()
    w.resize(1200, 720)
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
