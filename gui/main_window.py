import hashlib
import json
import random
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.paths import detect_blender, detect_studiomdl
from gui.process_runner import ProcessRunner
from gui.settings import (
    APP_NAME,
    DEFAULT_AUTOSMOOTH,
    DEFAULT_COMPRESS,
    DEFAULT_DECOMPILE_JOBS,
    DEFAULT_FORMAT,
    DEFAULT_JOBS,
    DEFAULT_MERGE,
    DEFAULT_OPEN_ON_FINISH,
    DEFAULT_OVERWRITE,
    DEFAULT_OVERWRITE_WORK,
    DEFAULT_PRESET,
    DEFAULT_RESUME_OPT,
    DEFAULT_STRICT,
    DEFAULT_SUFFIX,
    KEY_ADDON_PATH,
    KEY_AUTOSMOOTH,
    KEY_BLENDER_PATH,
    KEY_COMPRESS,
    KEY_DECOMPILE_JOBS,
    KEY_FORMAT,
    KEY_JOBS,
    KEY_MERGE,
    KEY_OPEN_ON_FINISH,
    KEY_OVERWRITE,
    KEY_OVERWRITE_WORK,
    KEY_PRESET,
    KEY_RESUME_OPT,
    KEY_STRICT,
    KEY_STUDIOMDL_PATH,
    KEY_SUFFIX,
    ORG_NAME,
)

PRESET_SAFE = "Seguro"
PRESET_AGGRESSIVE = "Agressivo"
PRESET_CUSTOM = "Personalizado"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GMod Addon Optimizer")
        self.resize(1280, 820)
        self._settings = QSettings(ORG_NAME, APP_NAME)
        self._runner = ProcessRunner(self)
        self._preview_runner = ProcessRunner(self)
        self._output_path = None
        self._preview_output_path = None
        self._preview_summary_path = None
        self._preview_car_dirs = []
        self._preview_car_mdls = []
        self._preview_base_auto = None
        self._preview_base_reason = ""
        self._preview_images = {}
        self._preview_angles = []
        self._preview_index = 0
        self._max_log_blocks = 20000
        self._preview_max_log_blocks = 15000
        self._syncing = False
        self._preset_applying = False
        self._loading_settings = False
        self._last_addon_path = ""

        self._build_ui()
        self._runner.log_line.connect(self._append_log)
        self._runner.progress_changed.connect(self._progress.setValue)
        self._runner.phase_changed.connect(self._phase_label.setText)
        self._runner.finished.connect(self._on_finished)
        self._runner.output_path.connect(self._on_output_path)
        self._preview_runner.log_line.connect(self._append_preview_log)
        self._preview_runner.progress_changed.connect(self._preview_progress.setValue)
        self._preview_runner.phase_changed.connect(self._preview_phase_label.setText)
        self._preview_runner.finished.connect(self._on_preview_finished)
        self._preview_runner.output_path.connect(self._on_preview_output_path)
        self._preview_runner.preview_summary.connect(self._on_preview_summary_path)
        self._load_settings()
        self._auto_detect_paths_if_empty()

    def _build_ui(self):
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_main_tab(), "Build")
        self._tabs.addTab(self._build_preview_tab(), "Teste rapido")

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.addWidget(self._tabs)
        self.setCentralWidget(central)

    def _build_main_tab(self) -> QWidget:
        splitter = QSplitter(self)
        splitter.setOrientation(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_contents = QWidget()
        self._form_layout = QVBoxLayout(scroll_contents)
        scroll.setWidget(scroll_contents)
        left_layout.addWidget(scroll)

        # Addon group
        addon_group = QGroupBox("Addon")
        addon_layout = QFormLayout(addon_group)
        self._addon_path = QLineEdit()
        self._addon_path.textChanged.connect(self._sync_preview_addon_from_main)
        self._addon_path.textChanged.connect(self._on_addon_changed)
        browse_addon = QPushButton("Procurar...")
        browse_addon.clicked.connect(self._browse_addon)
        addon_row = QHBoxLayout()
        addon_row.addWidget(self._addon_path)
        addon_row.addWidget(browse_addon)
        addon_layout.addRow("Pasta do addon", addon_row)

        self._suffix = QLineEdit()
        suffix_row = QHBoxLayout()
        suffix_row.addWidget(self._suffix)
        suffix_row.addWidget(self._help_button(self._suffix_help()))
        addon_layout.addRow("Suffix", suffix_row)
        self._form_layout.addWidget(addon_group)

        # Optimization group
        opt_group = QGroupBox("Otimizacao (Blender)")
        opt_layout = QFormLayout(opt_group)
        self._preset_combo = QComboBox()
        self._preset_combo.addItems([PRESET_SAFE, PRESET_AGGRESSIVE, PRESET_CUSTOM])
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        opt_layout.addRow("Preset", self._preset_combo)
        self._compress_slider = QSlider(Qt.Horizontal)
        self._compress_slider.setMinimum(0)
        self._compress_slider.setMaximum(100)
        self._compress_slider.valueChanged.connect(self._update_compress_label)
        self._compress_slider.valueChanged.connect(self._on_opt_param_changed)
        self._compress_slider.valueChanged.connect(self._sync_main_to_preview)
        self._compress_label = QLabel("")
        compress_row = QHBoxLayout()
        compress_row.addWidget(self._compress_slider)
        compress_row.addWidget(self._compress_label)
        compress_row.addWidget(self._help_button(self._compress_help()))
        opt_layout.addRow("Ratio", compress_row)

        self._merge = QDoubleSpinBox()
        self._merge.setDecimals(6)
        self._merge.setSingleStep(0.0001)
        self._merge.setRange(0.0, 1.0)
        self._merge.valueChanged.connect(self._on_opt_param_changed)
        self._merge.valueChanged.connect(self._sync_main_to_preview)
        merge_row = QHBoxLayout()
        merge_row.addWidget(self._merge)
        merge_row.addWidget(self._help_button(self._merge_help()))
        opt_layout.addRow("Merge", merge_row)

        self._autosmooth = QDoubleSpinBox()
        self._autosmooth.setDecimals(1)
        self._autosmooth.setRange(0.0, 180.0)
        self._autosmooth.setSingleStep(1.0)
        self._autosmooth.valueChanged.connect(self._on_opt_param_changed)
        self._autosmooth.valueChanged.connect(self._sync_main_to_preview)
        opt_layout.addRow("AutoSmooth", self._autosmooth)

        self._format = QComboBox()
        self._format.addItems(["smd", "dmx"])
        self._format.currentIndexChanged.connect(self._sync_main_to_preview)
        opt_layout.addRow("Format", self._format)

        self._jobs = QSpinBox()
        self._jobs.setRange(0, 32)
        self._jobs.setSingleStep(1)
        jobs_row = QHBoxLayout()
        jobs_row.addWidget(self._jobs)
        jobs_row.addWidget(self._help_button(self._jobs_help()))
        opt_layout.addRow("Jobs", jobs_row)

        self._decompile_jobs = QSpinBox()
        self._decompile_jobs.setRange(1, 16)
        self._decompile_jobs.setSingleStep(1)
        decomp_row = QHBoxLayout()
        decomp_row.addWidget(self._decompile_jobs)
        decomp_row.addWidget(self._help_button(self._decompile_jobs_help()))
        opt_layout.addRow("Decompile Jobs", decomp_row)
        self._form_layout.addWidget(opt_group)

        # Tools group
        tools_group = QGroupBox("Ferramentas")
        tools_layout = QFormLayout(tools_group)

        self._blender_path = QLineEdit()
        blender_detect = QPushButton("Auto-detect")
        blender_browse = QPushButton("Procurar...")
        blender_detect.clicked.connect(self._auto_detect_blender)
        blender_browse.clicked.connect(self._browse_blender)
        blender_row = QHBoxLayout()
        blender_row.addWidget(self._blender_path)
        blender_row.addWidget(blender_detect)
        blender_row.addWidget(blender_browse)
        tools_layout.addRow("Blender", blender_row)

        self._studiomdl_path = QLineEdit()
        studiomdl_detect = QPushButton("Auto-detect")
        studiomdl_browse = QPushButton("Procurar...")
        studiomdl_detect.clicked.connect(self._auto_detect_studiomdl)
        studiomdl_browse.clicked.connect(self._browse_studiomdl)
        studiomdl_row = QHBoxLayout()
        studiomdl_row.addWidget(self._studiomdl_path)
        studiomdl_row.addWidget(studiomdl_detect)
        studiomdl_row.addWidget(studiomdl_browse)
        tools_layout.addRow("StudioMDL", studiomdl_row)

        self._crowbar_status = QLabel("Bundled: OK")
        tools_layout.addRow("Crowbar", self._crowbar_status)
        self._form_layout.addWidget(tools_group)

        # Options group
        opts_group = QGroupBox("Opcoes")
        opts_layout = QVBoxLayout(opts_group)
        self._overwrite = QCheckBox("Overwrite addon final")
        self._overwrite_work = QCheckBox("Overwrite work/")
        self._strict = QCheckBox("Strict (abort on fail)")
        self._resume_opt = QCheckBox("Resume otimizacao (pular QCs ja otimizados)")
        self._open_on_finish = QCheckBox("Abrir pasta do addon final ao concluir")
        opts_layout.addWidget(self._overwrite)
        opts_layout.addWidget(self._overwrite_work)
        opts_layout.addWidget(self._strict)
        opts_layout.addWidget(self._resume_opt)
        opts_layout.addWidget(self._open_on_finish)
        self._form_layout.addWidget(opts_group)

        self._form_layout.addStretch(1)

        # Right side (logs)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._log.setFont(QFont("Consolas", 9))
        right_layout.addWidget(self._log)
        log_buttons = QHBoxLayout()
        self._log_clear = QPushButton("Limpar")
        self._log_clear.clicked.connect(self._log.clear)
        log_buttons.addWidget(self._log_clear)
        log_buttons.addStretch(1)
        right_layout.addLayout(log_buttons)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([520, 760])

        # Footer
        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        self._phase_label = QLabel("Idle")
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._status_label = QLabel("Idle")
        footer_layout.addWidget(self._phase_label)
        footer_layout.addWidget(self._progress, 1)
        footer_layout.addWidget(self._status_label)

        # Buttons
        buttons = QWidget()
        buttons_layout = QHBoxLayout(buttons)
        self._build_btn = QPushButton("Build / Otimizar")
        self._build_btn.clicked.connect(self._start_build)
        self._cancel_btn = QPushButton("Cancelar")
        self._cancel_btn.clicked.connect(self._runner.stop)
        self._cancel_btn.setEnabled(False)
        self._open_btn = QPushButton("Abrir pasta")
        self._open_btn.clicked.connect(self._open_output_folder)
        self._open_btn.setEnabled(False)
        buttons_layout.addWidget(self._build_btn)
        buttons_layout.addWidget(self._cancel_btn)
        buttons_layout.addWidget(self._open_btn)

        build_tab = QWidget()
        build_layout = QVBoxLayout(build_tab)
        build_layout.addWidget(splitter)
        build_layout.addWidget(footer)
        build_layout.addWidget(buttons)
        return build_tab

    def _build_preview_tab(self) -> QWidget:
        splitter = QSplitter(self)
        splitter.setOrientation(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_contents = QWidget()
        preview_form = QVBoxLayout(scroll_contents)
        scroll.setWidget(scroll_contents)
        left_layout.addWidget(scroll)

        # Addon group (preview)
        addon_group = QGroupBox("Addon")
        addon_layout = QFormLayout(addon_group)
        self._preview_addon_path = QLineEdit()
        self._preview_addon_path.textChanged.connect(self._on_preview_addon_changed)
        browse_addon = QPushButton("Procurar...")
        browse_addon.clicked.connect(self._browse_preview_addon)
        addon_row = QHBoxLayout()
        addon_row.addWidget(self._preview_addon_path)
        addon_row.addWidget(browse_addon)
        addon_layout.addRow("Pasta do addon", addon_row)

        self._preview_use_main = QCheckBox("Usar o addon da aba principal")
        self._preview_use_main.setChecked(True)
        self._preview_use_main.toggled.connect(self._toggle_preview_use_main)
        addon_layout.addRow("", self._preview_use_main)
        preview_form.addWidget(addon_group)

        # Car folder group
        model_group = QGroupBox("Carro")
        model_layout = QFormLayout(model_group)
        self._preview_car_combo = QComboBox()
        self._preview_car_combo.setEditable(True)
        self._preview_car_combo.currentTextChanged.connect(self._on_preview_car_changed)
        refresh_btn = QPushButton("Atualizar lista")
        refresh_btn.clicked.connect(self._refresh_preview_cars)
        random_btn = QPushButton("Aleatorio")
        random_btn.clicked.connect(self._pick_random_preview_car)
        model_row = QHBoxLayout()
        model_row.addWidget(self._preview_car_combo, 1)
        model_row.addWidget(refresh_btn)
        model_row.addWidget(random_btn)
        model_layout.addRow("Pasta do carro", model_row)

        self._preview_base_label = QLabel("Base: --")
        model_layout.addRow("Modelo base", self._preview_base_label)

        self._preview_base_override = QComboBox()
        self._preview_base_override.currentIndexChanged.connect(self._update_preview_base_label)
        model_layout.addRow("Override", self._preview_base_override)

        preview_form.addWidget(model_group)

        # Optimization group (preview)
        opt_group = QGroupBox("Otimizacao (Blender)")
        opt_layout = QFormLayout(opt_group)
        self._preview_compress_slider = QSlider(Qt.Horizontal)
        self._preview_compress_slider.setMinimum(0)
        self._preview_compress_slider.setMaximum(100)
        self._preview_compress_slider.valueChanged.connect(self._update_preview_compress_label)
        self._preview_compress_slider.valueChanged.connect(self._sync_preview_to_main)
        self._preview_compress_label = QLabel("")
        compress_row = QHBoxLayout()
        compress_row.addWidget(self._preview_compress_slider)
        compress_row.addWidget(self._preview_compress_label)
        compress_row.addWidget(self._help_button(self._compress_help()))
        opt_layout.addRow("Ratio", compress_row)

        self._preview_merge = QDoubleSpinBox()
        self._preview_merge.setDecimals(6)
        self._preview_merge.setSingleStep(0.0001)
        self._preview_merge.setRange(0.0, 1.0)
        self._preview_merge.valueChanged.connect(self._sync_preview_to_main)
        merge_row = QHBoxLayout()
        merge_row.addWidget(self._preview_merge)
        merge_row.addWidget(self._help_button(self._merge_help()))
        opt_layout.addRow("Merge", merge_row)

        self._preview_autosmooth = QDoubleSpinBox()
        self._preview_autosmooth.setDecimals(1)
        self._preview_autosmooth.setRange(0.0, 180.0)
        self._preview_autosmooth.setSingleStep(1.0)
        self._preview_autosmooth.valueChanged.connect(self._sync_preview_to_main)
        opt_layout.addRow("AutoSmooth", self._preview_autosmooth)

        self._preview_format = QComboBox()
        self._preview_format.addItems(["smd", "dmx"])
        self._preview_format.currentIndexChanged.connect(self._sync_preview_to_main)
        opt_layout.addRow("Format", self._preview_format)
        preview_form.addWidget(opt_group)

        preview_form.addStretch(1)

        # Right side (previews/logs)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self._preview_stats_label = QLabel("Original: -- tris -> Otimizado: -- tris")
        right_layout.addWidget(self._preview_stats_label)

        nav = QHBoxLayout()
        self._preview_prev_btn = QPushButton("Anterior")
        self._preview_prev_btn.clicked.connect(self._preview_prev_angle)
        self._preview_next_btn = QPushButton("Proximo")
        self._preview_next_btn.clicked.connect(self._preview_next_angle)
        self._preview_refresh_btn = QPushButton("Atualizar")
        self._preview_refresh_btn.clicked.connect(lambda: self._load_preview_summary(force_scan=True, show_error=True))
        self._preview_angle_label = QLabel("Angle: --")
        nav.addWidget(self._preview_prev_btn)
        nav.addWidget(self._preview_next_btn)
        nav.addStretch(1)
        nav.addWidget(self._preview_angle_label)
        nav.addStretch(1)
        nav.addWidget(self._preview_refresh_btn)
        right_layout.addLayout(nav)

        self._preview_tabs = QTabWidget()
        orig_tab = QWidget()
        orig_layout = QVBoxLayout(orig_tab)
        self._preview_orig_label = QLabel("Sem preview")
        self._preview_orig_label.setAlignment(Qt.AlignCenter)
        orig_layout.addWidget(self._preview_orig_label)

        opt_tab = QWidget()
        opt_layout = QVBoxLayout(opt_tab)
        self._preview_opt_label = QLabel("Sem preview")
        self._preview_opt_label.setAlignment(Qt.AlignCenter)
        opt_layout.addWidget(self._preview_opt_label)

        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self._preview_log = QPlainTextEdit()
        self._preview_log.setReadOnly(True)
        self._preview_log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._preview_log.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self._preview_log)
        log_buttons = QHBoxLayout()
        self._preview_log_clear = QPushButton("Limpar")
        self._preview_log_clear.clicked.connect(self._preview_log.clear)
        log_buttons.addWidget(self._preview_log_clear)
        log_buttons.addStretch(1)
        log_layout.addLayout(log_buttons)

        self._preview_tabs.addTab(orig_tab, "Original")
        self._preview_tabs.addTab(opt_tab, "Otimizado")
        self._preview_tabs.addTab(log_tab, "Logs")
        right_layout.addWidget(self._preview_tabs)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([520, 760])

        # Footer (preview)
        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        self._preview_phase_label = QLabel("Idle")
        self._preview_progress = QProgressBar()
        self._preview_progress.setRange(0, 100)
        self._preview_status_label = QLabel("Idle")
        footer_layout.addWidget(self._preview_phase_label)
        footer_layout.addWidget(self._preview_progress, 1)
        footer_layout.addWidget(self._preview_status_label)

        # Buttons (preview)
        buttons = QWidget()
        buttons_layout = QHBoxLayout(buttons)
        self._preview_run_btn = QPushButton("Rodar teste")
        self._preview_run_btn.clicked.connect(self._start_preview)
        self._preview_cancel_btn = QPushButton("Cancelar")
        self._preview_cancel_btn.clicked.connect(self._preview_runner.stop)
        self._preview_cancel_btn.setEnabled(False)
        self._preview_open_btn = QPushButton("Abrir pasta do teste")
        self._preview_open_btn.clicked.connect(self._open_preview_folder)
        self._preview_open_btn.setEnabled(False)
        buttons_layout.addWidget(self._preview_run_btn)
        buttons_layout.addWidget(self._preview_cancel_btn)
        buttons_layout.addWidget(self._preview_open_btn)

        preview_root = QWidget()
        preview_root_layout = QVBoxLayout(preview_root)
        preview_root_layout.addWidget(splitter)
        preview_root_layout.addWidget(footer)
        preview_root_layout.addWidget(buttons)
        return preview_root

    def _help_button(self, text: str) -> QToolButton:
        btn = QToolButton()
        btn.setText("?")
        btn.clicked.connect(lambda: QMessageBox.information(self, "Ajuda", text))
        return btn

    def _suffix_help(self) -> str:
        return (
            "Sufixo que sera adicionado ao nome da pasta do addon final.\n"
            "Ex.: meu_addon -> meu_addon_otimizado. O original nao e alterado."
        )

    def _compress_help(self) -> str:
        return "Define o ratio aplicado (0 = mais agressivo / 1 = sem decimate)."

    def _preset_values(self, preset: str) -> dict | None:
        if preset == PRESET_SAFE:
            return {"ratio": 0.75, "merge": 0.0, "autosmooth": 45.0}
        if preset == PRESET_AGGRESSIVE:
            return {"ratio": 0.50, "merge": 0.0, "autosmooth": 45.0}
        return None

    def _apply_preset(self, preset: str):
        values = self._preset_values(preset)
        if not values:
            return
        self._preset_applying = True
        ratio = values["ratio"]
        self._compress_slider.setValue(int(round(ratio * 100)))
        self._merge.setValue(values["merge"])
        self._autosmooth.setValue(values["autosmooth"])
        self._update_compress_label()
        self._preset_applying = False

    def _set_preset_combo(self, preset: str):
        idx = self._preset_combo.findText(preset)
        if idx < 0:
            idx = self._preset_combo.findText(PRESET_CUSTOM)
        self._preset_applying = True
        self._preset_combo.setCurrentIndex(idx)
        self._preset_applying = False

    def _on_preset_changed(self):
        if self._preset_applying or self._syncing or self._loading_settings:
            return
        preset = self._preset_combo.currentText()
        if preset != PRESET_CUSTOM:
            self._apply_preset(preset)

    def _on_opt_param_changed(self):
        if self._preset_applying or self._syncing or self._loading_settings:
            return
        if self._preset_combo.currentText() != PRESET_CUSTOM:
            self._set_preset_combo(PRESET_CUSTOM)

    def _merge_help(self) -> str:
        return "Merge by distance. 0 desliga totalmente. Use valores pequenos (ex: 0.0001) so se necessario."

    def _jobs_help(self) -> str:
        return "Numero de instancias do Blender em paralelo. 0 = auto (CPU-1, max 6)."

    def _decompile_jobs_help(self) -> str:
        return "Numero de instancias do Crowbar em paralelo. Use 2-4 para acelerar sem sobrecarregar o disco."

    def _browse_addon(self):
        path = QFileDialog.getExistingDirectory(self, "Selecionar addon")
        if path:
            self._addon_path.setText(path)
            if self._preview_use_main.isChecked():
                self._preview_addon_path.setText(path)
                self._refresh_preview_cars()

    def _sync_preview_addon_from_main(self):
        if self._preview_use_main.isChecked():
            self._preview_addon_path.setText(self._addon_path.text())
            self._refresh_preview_cars()

    def _addon_settings_key(self, addon_path: str) -> str:
        try:
            resolved = str(Path(addon_path).expanduser().resolve()).lower()
        except Exception:
            resolved = addon_path.strip().lower()
        digest = hashlib.sha1(resolved.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"addon/{digest}"

    def _load_addon_settings(self, addon_path: str):
        if not addon_path:
            return
        addon = Path(addon_path)
        if not addon.exists():
            return
        key = self._addon_settings_key(addon_path)
        if not self._settings.contains(f"{key}/preset"):
            return
        preset = self._settings.value(f"{key}/preset", "", str)
        if preset in (PRESET_SAFE, PRESET_AGGRESSIVE):
            self._set_preset_combo(preset)
            self._apply_preset(preset)
        else:
            self._set_preset_combo(PRESET_CUSTOM)
            if self._settings.contains(f"{key}/compress"):
                self._compress_slider.setValue(
                    int(float(self._settings.value(f"{key}/compress", self._compress_value())) * 100)
                )
            if self._settings.contains(f"{key}/merge"):
                self._merge.setValue(float(self._settings.value(f"{key}/merge", self._merge.value())))
            if self._settings.contains(f"{key}/autosmooth"):
                self._autosmooth.setValue(
                    float(self._settings.value(f"{key}/autosmooth", self._autosmooth.value()))
                )
            if self._settings.contains(f"{key}/format"):
                fmt = self._settings.value(f"{key}/format", self._format.currentText(), str)
                idx = self._format.findText(fmt)
                if idx >= 0:
                    self._format.setCurrentIndex(idx)
            self._update_compress_label()

    def _save_addon_settings(self, addon_path: str):
        if not addon_path:
            return
        addon = Path(addon_path)
        if not addon.exists():
            return
        key = self._addon_settings_key(addon_path)
        self._settings.setValue(f"{key}/preset", self._preset_combo.currentText())
        self._settings.setValue(f"{key}/compress", self._compress_value())
        self._settings.setValue(f"{key}/merge", self._merge.value())
        self._settings.setValue(f"{key}/autosmooth", self._autosmooth.value())
        self._settings.setValue(f"{key}/format", self._format.currentText())

    def _on_addon_changed(self):
        if self._loading_settings:
            return
        current = self._addon_path.text().strip()
        if self._last_addon_path and self._last_addon_path != current:
            self._save_addon_settings(self._last_addon_path)
        self._last_addon_path = current
        self._load_addon_settings(current)

    def _browse_blender(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecionar Blender", filter="blender.exe (*.exe)")
        if path:
            self._blender_path.setText(path)

    def _browse_studiomdl(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecionar studiomdl.exe", filter="studiomdl.exe (*.exe)")
        if path:
            self._studiomdl_path.setText(path)

    def _auto_detect_blender(self):
        p = detect_blender()
        if p:
            self._blender_path.setText(str(p))
        else:
            QMessageBox.warning(self, "Blender", "Nao foi possivel localizar o Blender automaticamente.")

    def _auto_detect_studiomdl(self):
        p = detect_studiomdl()
        if p:
            self._studiomdl_path.setText(str(p))
        else:
            QMessageBox.warning(self, "StudioMDL", "Nao foi possivel localizar o studiomdl.exe automaticamente.")

    def _auto_detect_paths_if_empty(self):
        if not self._blender_path.text().strip():
            self._auto_detect_blender()
        if not self._studiomdl_path.text().strip():
            self._auto_detect_studiomdl()
        self._update_crowbar_status()
        if self._preview_use_main.isChecked():
            self._preview_addon_path.setText(self._addon_path.text())
            self._refresh_preview_cars()

    def _update_crowbar_status(self):
        crowbar = self._find_crowbar()
        if crowbar and crowbar.exists():
            self._crowbar_status.setText(f"Bundled: OK ({crowbar.name})")
        else:
            self._crowbar_status.setText("Bundled: MISSING")

    def _find_crowbar(self) -> Path | None:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
        else:
            base = Path(__file__).resolve().parents[1]
        cand = base / "CrowbarCommandLineDecomp.exe"
        if cand.exists():
            return cand
        cand = base / "worker" / "CrowbarCommandLineDecomp.exe"
        return cand if cand.exists() else None

    def _load_settings(self):
        self._loading_settings = True
        self._addon_path.setText(self._settings.value(KEY_ADDON_PATH, "", str))
        self._suffix.setText(self._settings.value(KEY_SUFFIX, DEFAULT_SUFFIX, str))

        preset_value = ""
        if self._settings.contains(KEY_PRESET):
            preset_value = self._settings.value(KEY_PRESET, DEFAULT_PRESET, str)
        if preset_value in (PRESET_SAFE, PRESET_AGGRESSIVE):
            self._set_preset_combo(preset_value)
            self._apply_preset(preset_value)
        else:
            self._set_preset_combo(PRESET_CUSTOM)
            self._compress_slider.setValue(int(float(self._settings.value(KEY_COMPRESS, DEFAULT_COMPRESS)) * 100))
            self._merge.setValue(float(self._settings.value(KEY_MERGE, DEFAULT_MERGE)))
            self._autosmooth.setValue(float(self._settings.value(KEY_AUTOSMOOTH, DEFAULT_AUTOSMOOTH)))
            self._update_compress_label()

        fmt = self._settings.value(KEY_FORMAT, DEFAULT_FORMAT, str)
        idx = self._format.findText(fmt)
        if idx >= 0:
            self._format.setCurrentIndex(idx)
        self._jobs.setValue(int(self._settings.value(KEY_JOBS, DEFAULT_JOBS)))
        self._decompile_jobs.setValue(
            int(self._settings.value(KEY_DECOMPILE_JOBS, DEFAULT_DECOMPILE_JOBS))
        )
        self._overwrite.setChecked(bool(self._settings.value(KEY_OVERWRITE, DEFAULT_OVERWRITE, bool)))
        self._overwrite_work.setChecked(bool(self._settings.value(KEY_OVERWRITE_WORK, DEFAULT_OVERWRITE_WORK, bool)))
        self._strict.setChecked(bool(self._settings.value(KEY_STRICT, DEFAULT_STRICT, bool)))
        self._resume_opt.setChecked(bool(self._settings.value(KEY_RESUME_OPT, DEFAULT_RESUME_OPT, bool)))
        self._open_on_finish.setChecked(
            bool(self._settings.value(KEY_OPEN_ON_FINISH, DEFAULT_OPEN_ON_FINISH, bool))
        )
        self._blender_path.setText(self._settings.value(KEY_BLENDER_PATH, "", str))
        self._studiomdl_path.setText(self._settings.value(KEY_STUDIOMDL_PATH, "", str))
        self._loading_settings = False

        self._load_addon_settings(self._addon_path.text().strip())
        # Preview tab mirrors main settings by default.
        self._preview_addon_path.setText(self._addon_path.text())
        self._preview_compress_slider.setValue(self._compress_slider.value())
        self._preview_merge.setValue(self._merge.value())
        self._preview_autosmooth.setValue(self._autosmooth.value())
        self._preview_format.setCurrentText(self._format.currentText())
        self._update_preview_compress_label()
        self._toggle_preview_use_main(self._preview_use_main.isChecked())

    def _save_settings(self):
        self._settings.setValue(KEY_ADDON_PATH, self._addon_path.text().strip())
        self._settings.setValue(KEY_SUFFIX, self._suffix.text().strip())
        self._settings.setValue(KEY_COMPRESS, self._compress_value())
        self._settings.setValue(KEY_MERGE, self._merge.value())
        self._settings.setValue(KEY_AUTOSMOOTH, self._autosmooth.value())
        self._settings.setValue(KEY_FORMAT, self._format.currentText())
        self._settings.setValue(KEY_PRESET, self._preset_combo.currentText())
        self._settings.setValue(KEY_JOBS, self._jobs.value())
        self._settings.setValue(KEY_DECOMPILE_JOBS, self._decompile_jobs.value())
        self._settings.setValue(KEY_OVERWRITE, self._overwrite.isChecked())
        self._settings.setValue(KEY_OVERWRITE_WORK, self._overwrite_work.isChecked())
        self._settings.setValue(KEY_STRICT, self._strict.isChecked())
        self._settings.setValue(KEY_RESUME_OPT, self._resume_opt.isChecked())
        self._settings.setValue(KEY_OPEN_ON_FINISH, self._open_on_finish.isChecked())
        self._settings.setValue(KEY_BLENDER_PATH, self._blender_path.text().strip())
        self._settings.setValue(KEY_STUDIOMDL_PATH, self._studiomdl_path.text().strip())
        self._save_addon_settings(self._addon_path.text().strip())

    def _compress_value(self) -> float:
        return self._compress_slider.value() / 100.0

    def _ratio_from_compress(self) -> float:
        return self._compress_value()

    def _update_compress_label(self):
        compress = self._compress_value()
        self._compress_label.setText(f"{compress:.2f} (aplicado)")

    def _preview_compress_value(self) -> float:
        return self._preview_compress_slider.value() / 100.0

    def _update_preview_compress_label(self):
        compress = self._preview_compress_value()
        self._preview_compress_label.setText(f"{compress:.2f} (aplicado)")

    def _sync_main_to_preview(self):
        if self._syncing:
            return
        if not hasattr(self, "_preview_compress_slider"):
            return
        self._syncing = True
        self._preview_compress_slider.setValue(self._compress_slider.value())
        self._preview_merge.setValue(self._merge.value())
        self._preview_autosmooth.setValue(self._autosmooth.value())
        self._preview_format.setCurrentText(self._format.currentText())
        self._update_preview_compress_label()
        self._syncing = False

    def _sync_preview_to_main(self):
        if self._syncing:
            return
        if not hasattr(self, "_compress_slider"):
            return
        self._syncing = True
        self._compress_slider.setValue(self._preview_compress_slider.value())
        self._merge.setValue(self._preview_merge.value())
        self._autosmooth.setValue(self._preview_autosmooth.value())
        self._format.setCurrentText(self._preview_format.currentText())
        self._update_compress_label()
        self._syncing = False

    def _validate_inputs(self) -> list[str]:
        errors = []
        self._clear_field_errors()

        addon = Path(self._addon_path.text().strip())
        if not addon.exists() or not addon.is_dir():
            self._mark_error(self._addon_path)
            errors.append("Pasta do addon invalida.")

        suffix = self._suffix.text().strip()
        if not suffix:
            self._mark_error(self._suffix)
            errors.append("Suffix nao pode ser vazio.")

        blender = Path(self._blender_path.text().strip())
        if not blender.exists():
            self._mark_error(self._blender_path)
            errors.append("Blender nao encontrado.")

        studiomdl = Path(self._studiomdl_path.text().strip())
        if not studiomdl.exists():
            self._mark_error(self._studiomdl_path)
            errors.append("studiomdl.exe nao encontrado.")

        crowbar = self._find_crowbar()
        if not crowbar or not crowbar.exists():
            errors.append("CrowbarCommandLineDecomp.exe nao encontrado no bundle.")

        if getattr(sys, "frozen", False):
            worker = Path(self._worker_path())
            if not worker.exists():
                errors.append("Worker exe nao encontrado (GModAddonOptimizerWorker.exe).")

        return errors

    def _mark_error(self, widget):
        widget.setProperty("error", True)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _clear_field_errors(self):
        for w in [self._addon_path, self._suffix, self._blender_path, self._studiomdl_path]:
            w.setProperty("error", False)
            w.style().unpolish(w)
            w.style().polish(w)

    def _clear_preview_field_errors(self):
        for w in [self._preview_addon_path, self._preview_car_combo, self._preview_base_override, self._blender_path]:
            w.setProperty("error", False)
            w.style().unpolish(w)
            w.style().polish(w)

    def _toggle_preview_use_main(self, checked: bool):
        self._preview_addon_path.setEnabled(not checked)
        if checked:
            self._preview_addon_path.setText(self._addon_path.text())
            self._refresh_preview_cars()

    def _on_preview_addon_changed(self):
        if self._preview_use_main.isChecked():
            return
        self._refresh_preview_cars()

    def _browse_preview_addon(self):
        path = QFileDialog.getExistingDirectory(self, "Selecionar addon (preview)")
        if path:
            self._preview_addon_path.setText(path)

    def _refresh_preview_cars(self):
        addon = Path(self._preview_addon_path.text().strip())
        self._preview_car_combo.clear()
        self._preview_car_dirs = []
        if not addon.exists():
            return
        models_dir = addon / "models"
        if not models_dir.exists():
            return
        dirs = {}
        for mdl in models_dir.rglob("*.mdl"):
            if not mdl.is_file():
                continue
            dirs[mdl.parent.resolve()] = True
        for car_dir in sorted(dirs.keys(), key=lambda p: str(p).lower()):
            try:
                rel = car_dir.relative_to(models_dir).as_posix()
            except Exception:
                rel = str(car_dir)
            self._preview_car_combo.addItem(rel)
            self._preview_car_dirs.append((rel, car_dir))
        if self._preview_car_dirs:
            self._preview_car_combo.setCurrentIndex(0)
        self._update_preview_base_model()

    def _pick_random_preview_car(self):
        if not self._preview_car_dirs:
            self._refresh_preview_cars()
        if not self._preview_car_dirs:
            return
        rel, _path = random.choice(self._preview_car_dirs)
        idx = self._preview_car_combo.findText(rel)
        if idx >= 0:
            self._preview_car_combo.setCurrentIndex(idx)
        else:
            self._preview_car_combo.setEditText(rel)

    def _resolve_preview_car_dir(self) -> Path | None:
        text = self._preview_car_combo.currentText().strip()
        if not text:
            return None
        p = Path(text)
        if p.exists() and p.is_dir():
            return p.resolve()
        addon = Path(self._preview_addon_path.text().strip())
        if addon.exists():
            models_dir = addon / "models"
            if models_dir.exists():
                cand = (models_dir / text).resolve()
                if cand.exists():
                    return cand
        for rel, path in getattr(self, "_preview_car_dirs", []):
            if rel.lower() == text.lower():
                return path.resolve()
        return None

    def _list_mdls_in_dir(self, car_dir: Path) -> tuple[list[Path], bool]:
        mdls = sorted([p for p in car_dir.glob("*.mdl") if p.is_file()], key=lambda p: str(p).lower())
        if mdls:
            return mdls, False
        mdls = sorted([p for p in car_dir.rglob("*.mdl") if p.is_file()], key=lambda p: str(p).lower())
        return mdls, True

    def _choose_base_mdl(self, car_dir: Path, mdls: list[Path], recursive: bool) -> tuple[Path | None, str]:
        if not mdls:
            return None, "no_mdls"

        names = {p.name.lower(): p for p in mdls}
        folder_name = car_dir.name.lower()
        candidates = [
            "base.mdl",
            "body.mdl",
            "chassis.mdl",
            "car.mdl",
            f"{folder_name}.mdl",
            "model.mdl",
        ]
        for name in candidates:
            if name in names:
                reason = f"name:{name}"
                if recursive:
                    reason += " (recursive)"
                return names[name], reason

        skip_tokens = ["wheel", "wheelfr", "wheelbk", "rim", "tire", "tyre", "physics", "phys"]
        filtered = [p for p in mdls if not any(t in p.name.lower() for t in skip_tokens)]
        pool = filtered if filtered else mdls

        best = max(pool, key=lambda p: p.stat().st_size, default=None)
        reason = "largest"
        if filtered:
            reason = "largest (filtered)"
        if recursive:
            reason += " (recursive)"
        return best, reason

    def _on_preview_car_changed(self):
        self._update_preview_base_model()

    def _update_preview_base_model(self):
        car_dir = self._resolve_preview_car_dir()
        self._preview_base_override.blockSignals(True)
        self._preview_base_override.clear()
        self._preview_base_override.addItem("Auto")
        self._preview_car_mdls = []

        if not car_dir or not car_dir.exists():
            self._preview_base_auto = None
            self._preview_base_reason = ""
            self._preview_base_label.setText("Base: --")
            self._preview_base_override.blockSignals(False)
            return

        mdls, recursive = self._list_mdls_in_dir(car_dir)
        for mdl in mdls:
            self._preview_base_override.addItem(mdl.name, str(mdl))
            self._preview_car_mdls.append(mdl)

        base, reason = self._choose_base_mdl(car_dir, mdls, recursive)
        self._preview_base_auto = base
        self._preview_base_reason = reason
        self._update_preview_base_label()
        self._preview_base_override.blockSignals(False)

    def _update_preview_base_label(self):
        override_idx = self._preview_base_override.currentIndex()
        if override_idx > 0:
            name = self._preview_base_override.currentText()
            self._preview_base_label.setText(f"Base: {name} (override)")
            return
        if self._preview_base_auto:
            self._preview_base_label.setText(f"Base: {self._preview_base_auto.name} ({self._preview_base_reason})")
        else:
            self._preview_base_label.setText("Base: --")

    def _resolve_preview_model_path(self) -> Path | None:
        override_idx = self._preview_base_override.currentIndex()
        if override_idx > 0:
            data = self._preview_base_override.currentData()
            if data:
                p = Path(str(data))
                if p.exists():
                    return p.resolve()
        if self._preview_base_auto and self._preview_base_auto.exists():
            return self._preview_base_auto.resolve()
        car_dir = self._resolve_preview_car_dir()
        if not car_dir:
            return None
        mdls, recursive = self._list_mdls_in_dir(car_dir)
        base, _reason = self._choose_base_mdl(car_dir, mdls, recursive)
        return base.resolve() if base else None

    def _validate_preview_inputs(self) -> list[str]:
        errors = []
        self._clear_preview_field_errors()

        addon = Path(self._preview_addon_path.text().strip())
        if not addon.exists() or not addon.is_dir():
            self._mark_error(self._preview_addon_path)
            errors.append("Pasta do addon (preview) invalida.")

        blender = Path(self._blender_path.text().strip())
        if not blender.exists():
            self._mark_error(self._blender_path)
            errors.append("Blender nao encontrado.")

        crowbar = self._find_crowbar()
        if not crowbar or not crowbar.exists():
            errors.append("CrowbarCommandLineDecomp.exe nao encontrado no bundle.")

        if getattr(sys, "frozen", False):
            worker = Path(self._worker_path())
            if not worker.exists():
                errors.append("Worker exe nao encontrado (GModAddonOptimizerWorker.exe).")

        car_dir = self._resolve_preview_car_dir()
        if not car_dir or not car_dir.exists():
            self._mark_error(self._preview_car_combo)
            errors.append("Pasta do carro invalida ou nao encontrada.")

        mdl = self._resolve_preview_model_path()
        if not mdl or not mdl.exists():
            self._mark_error(self._preview_base_override)
            errors.append("Modelo .mdl invalido ou nao encontrado.")

        return errors

    def _build_preview_command(self) -> list[str]:
        addon = self._preview_addon_path.text().strip()
        car_dir = self._resolve_preview_car_dir()
        mdl = self._resolve_preview_model_path()
        ratio = self._ratio_from_compress()

        args = [
            self._worker_path(),
            "preview",
            "--addon",
            addon,
            "--ratio",
            f"{ratio:.4f}",
            "--merge",
            f"{self._merge.value():.6f}",
            "--autosmooth",
            f"{self._autosmooth.value():.1f}",
            "--format",
            self._format.currentText(),
            "--blender",
            self._blender_path.text().strip(),
        ]
        if car_dir:
            args.extend(["--car-dir", str(car_dir)])
        if mdl:
            args.extend(["--mdl", str(mdl)])
        if getattr(sys, "frozen", False):
            return args
        return [sys.executable, "-u"] + args

    def _start_preview(self):
        if self._preview_runner.is_running():
            return
        errors = self._validate_preview_inputs()
        if errors:
            QMessageBox.warning(self, "Erro", "\n".join(errors))
            return

        self._save_settings()
        self._preview_output_path = None
        self._preview_summary_path = None
        self._preview_open_btn.setEnabled(False)
        self._preview_progress.setValue(0)
        self._preview_phase_label.setText("Starting")
        self._preview_status_label.setText("Running")
        self._preview_stats_label.setText("Original: -- tris -> Otimizado: -- tris")
        self._preview_angles = []
        self._preview_index = 0
        self._preview_images = {}
        self._preview_angle_label.setText("Angle: --")
        self._preview_orig_label.setText("Sem preview")
        self._preview_orig_label.setPixmap(QPixmap())
        self._preview_opt_label.setText("Sem preview")
        self._preview_opt_label.setPixmap(QPixmap())
        self._preview_run_btn.setEnabled(False)
        self._preview_cancel_btn.setEnabled(True)

        cmd = self._build_preview_command()
        env = {"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        self._preview_runner.start(cmd, workdir=Path.cwd(), env=env)

    def _append_preview_log(self, line: str):
        self._preview_log.appendPlainText(line)
        if self._preview_log.document().blockCount() > self._preview_max_log_blocks:
            cursor = QTextCursor(self._preview_log.document())
            cursor.movePosition(QTextCursor.Start)
            for _ in range(self._preview_log.document().blockCount() - self._preview_max_log_blocks):
                cursor.select(QTextCursor.LineUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()

    def _on_preview_output_path(self, path: str):
        self._preview_output_path = path
        self._preview_open_btn.setEnabled(True)

    def _on_preview_summary_path(self, path: str):
        self._preview_summary_path = path

    def _on_preview_finished(self, exit_code: int):
        self._preview_cancel_btn.setEnabled(False)
        self._preview_run_btn.setEnabled(True)
        if exit_code == 0:
            self._preview_status_label.setText("OK")
            self._preview_progress.setValue(100)
            if not self._load_preview_summary(show_error=False):
                self._load_preview_summary(force_scan=True, show_error=False)
        else:
            self._preview_status_label.setText(f"FAIL ({exit_code})")

    def _open_preview_folder(self):
        if not self._preview_output_path:
            return
        p = Path(self._preview_output_path)
        if p.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _find_latest_preview_summary(self) -> Path | None:
        addon = Path(self._preview_addon_path.text().strip())
        if not addon.exists():
            return None
        addon_name = addon.name
        work_root = Path.cwd() / "work"
        base = work_root / f"{addon_name}_preview_tests"
        if not base.exists():
            return None
        candidates = list(base.glob("*/renders/preview_summary.json"))
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    def _load_preview_summary(self, force_scan: bool = False, show_error: bool = True) -> bool:
        summary_path = None
        if self._preview_summary_path and not force_scan:
            summary_path = Path(self._preview_summary_path)
        elif self._preview_output_path and not force_scan:
            summary_path = Path(self._preview_output_path) / "preview_summary.json"
        if not summary_path or not summary_path.exists():
            if force_scan:
                summary_path = self._find_latest_preview_summary()
        if not summary_path or not summary_path.exists():
            if show_error:
                QMessageBox.information(self, "Preview", "Nao encontrei preview_summary.json para carregar.")
            return False

        try:
            data = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return False

        before_tris = data.get("before", {}).get("tris")
        after_tris = data.get("after", {}).get("tris")
        if isinstance(before_tris, int) and isinstance(after_tris, int):
            self._preview_stats_label.setText(f"Original: {before_tris:,} tris -> Otimizado: {after_tris:,} tris")

        angles = data.get("angles") or []
        before_imgs = data.get("before", {}).get("images", {})
        after_imgs = data.get("after", {}).get("images", {})
        base = summary_path.parent

        self._preview_images = {}
        for angle in angles:
            b = before_imgs.get(angle)
            a = after_imgs.get(angle)
            if not b or not a:
                continue
            self._preview_images[angle] = {
                "before": (base / b).resolve(),
                "after": (base / a).resolve(),
            }

        self._preview_angles = angles
        self._preview_index = 0
        self._update_preview_images()
        return True

    def _update_preview_images(self):
        if not self._preview_angles:
            self._preview_angle_label.setText("Angle: --")
            self._preview_orig_label.setText("Sem preview")
            self._preview_opt_label.setText("Sem preview")
            return
        angle = self._preview_angles[self._preview_index]
        paths = self._preview_images.get(angle, {})
        before = paths.get("before")
        after = paths.get("after")
        self._preview_angle_label.setText(
            f"Angle: {angle} ({self._preview_index + 1}/{len(self._preview_angles)})"
        )
        if before and before.exists():
            pix = QPixmap(str(before))
            self._preview_orig_label.setPixmap(pix.scaled(720, 720, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._preview_orig_label.setText("")
        else:
            self._preview_orig_label.setText("Sem preview")
            self._preview_orig_label.setPixmap(QPixmap())

        if after and after.exists():
            pix = QPixmap(str(after))
            self._preview_opt_label.setPixmap(pix.scaled(720, 720, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._preview_opt_label.setText("")
        else:
            self._preview_opt_label.setText("Sem preview")
            self._preview_opt_label.setPixmap(QPixmap())

    def _preview_next_angle(self):
        if not self._preview_angles:
            return
        self._preview_index = (self._preview_index + 1) % len(self._preview_angles)
        self._update_preview_images()

    def _preview_prev_angle(self):
        if not self._preview_angles:
            return
        self._preview_index = (self._preview_index - 1) % len(self._preview_angles)
        self._update_preview_images()

    def _start_build(self):
        if self._runner.is_running():
            return
        errors = self._validate_inputs()
        if errors:
            QMessageBox.warning(self, "Erro", "\n".join(errors))
            return

        self._save_settings()
        self._output_path = None
        self._open_btn.setEnabled(False)
        self._progress.setValue(0)
        self._phase_label.setText("Starting")
        self._status_label.setText("Running")
        self._build_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        cmd = self._build_command()
        env = {"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        self._runner.start(cmd, workdir=Path.cwd(), env=env)

    def _build_command(self) -> list[str]:
        addon = self._addon_path.text().strip()
        suffix = self._suffix.text().strip()
        ratio = self._ratio_from_compress()

        args = [
            self._worker_path(),
            addon,
            "--suffix",
            suffix,
            "--ratio",
            f"{ratio:.4f}",
            "--merge",
            f"{self._merge.value():.6f}",
            "--autosmooth",
            f"{self._autosmooth.value():.1f}",
            "--format",
            self._format.currentText(),
            "--jobs",
            str(self._jobs.value()),
            "--decompile-jobs",
            str(self._decompile_jobs.value()),
            "--blender",
            self._blender_path.text().strip(),
            "--studiomdl",
            self._studiomdl_path.text().strip(),
        ]
        if self._overwrite.isChecked():
            args.append("--overwrite")
        if self._overwrite_work.isChecked():
            args.append("--overwrite-work")
        if self._strict.isChecked():
            args.append("--strict")
        if self._resume_opt.isChecked():
            args.append("--resume-opt")

        if getattr(sys, "frozen", False):
            return args
        return [sys.executable, "-u"] + args

    def _worker_path(self) -> str:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
            worker = base / "GModAddonOptimizerWorker.exe"
            if not worker.exists():
                worker = base / "worker" / "GModAddonOptimizerWorker.exe"
            return str(worker)
        return str(Path(__file__).resolve().parents[1] / "worker" / "worker_main.py")

    def _append_log(self, line: str):
        self._log.appendPlainText(line)
        if self._log.document().blockCount() > self._max_log_blocks:
            cursor = QTextCursor(self._log.document())
            cursor.movePosition(QTextCursor.Start)
            for _ in range(self._log.document().blockCount() - self._max_log_blocks):
                cursor.select(QTextCursor.LineUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()

    def _on_output_path(self, path: str):
        self._output_path = path
        self._open_btn.setEnabled(True)

    def _on_finished(self, exit_code: int):
        self._cancel_btn.setEnabled(False)
        self._build_btn.setEnabled(True)
        if exit_code == 0:
            self._status_label.setText("OK")
            self._progress.setValue(100)
            if self._open_on_finish.isChecked():
                self._open_output_folder()
        else:
            self._status_label.setText(f"FAIL ({exit_code})")

    def _open_output_folder(self):
        if not self._output_path:
            return
        p = Path(self._output_path)
        if p.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)
