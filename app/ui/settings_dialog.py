"""Where ComfyUI lives, where files go, and whether to start the engine.

Wrong paths are the most common way this app breaks, so the ComfyUI section
tests itself: pick a folder, and it immediately says whether it found a start
file and whether a server is answering.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from app import i18n
from app.comfy.client import ComfyClient
from app.comfy.launcher import ComfyLauncher
from app.i18n import t
from app.ui import theme


class PathRow(QWidget):
    """A read-only path box with a Browse button."""

    def __init__(self, value: str, caption: str, parent=None):
        super().__init__(parent)
        self.caption = caption
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit(value)
        layout.addWidget(self.edit, 1)
        browse = QPushButton(t("Browse…"))
        browse.clicked.connect(self._browse)
        layout.addWidget(browse)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, self.caption, self.edit.text())
        if chosen:
            self.edit.setText(chosen)

    def value(self) -> str:
        return self.edit.text().strip()


class SettingsDialog(QDialog):
    def __init__(self, cfg, client: ComfyClient, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.client = client
        self.setWindowTitle(t("Settings"))
        self.resize(660, 620)
        self._build()
        self._refresh_engine_hint()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # --- engine ------------------------------------------------------
        engine_box = QGroupBox(t("The AI engine (ComfyUI)"))
        form = QFormLayout(engine_box)

        self.dir_row = PathRow(str(self.cfg.get("comfyui_dir")),
                               t("Where is ComfyUI installed?"))
        self.dir_row.edit.textChanged.connect(self._on_dir_changed)
        form.addRow(t("ComfyUI folder"), self.dir_row)

        self.launcher_combo = QComboBox()
        self.launcher_combo.setEditable(True)
        self.launcher_combo.setToolTip(t(
            "The .bat file that starts ComfyUI. On a portable install this is "
            "usually run_nvidia_gpu.bat."))
        form.addRow(t("Start file"), self.launcher_combo)

        self.server_edit = QLineEdit(self.cfg.server)
        self.server_edit.setToolTip(t(
            "Leave this alone unless ComfyUI runs on another PC or a different "
            "port."))
        self.server_edit.textChanged.connect(self._refresh_engine_hint)
        form.addRow(t("Address"), self.server_edit)

        self.auto_launch = QCheckBox(
            t("Start ComfyUI automatically when EasyAI opens"))
        self.auto_launch.setChecked(bool(self.cfg.get("auto_launch")))
        form.addRow("", self.auto_launch)

        self.stop_on_exit = QCheckBox(t("Close ComfyUI when EasyAI closes"))
        self.stop_on_exit.setToolTip(t(
            "Leaving it running keeps hold of the graphics card.\n\n"
            "Only ever closes a ComfyUI that EasyAI started. One you opened "
            "yourself is left alone."))
        self.stop_on_exit.setChecked(bool(self.cfg.get("stop_engine_on_exit")))
        form.addRow("", self.stop_on_exit)

        check_row = QHBoxLayout()
        self.engine_hint = QLabel("")
        self.engine_hint.setWordWrap(True)
        check_row.addWidget(self.engine_hint, 1)
        test_btn = QPushButton(t("Test now"))
        test_btn.clicked.connect(self._refresh_engine_hint)
        check_row.addWidget(test_btn)
        form.addRow("", self._wrap(check_row))
        outer.addWidget(engine_box)

        # --- folders -----------------------------------------------------
        folder_box = QGroupBox(t("Folders"))
        folder_form = QFormLayout(folder_box)
        self.workflow_row = PathRow(str(self.cfg.get("workflow_dir")),
                                    t("Where are the workflow files?"))
        folder_form.addRow(t("Workflows"), self.workflow_row)
        self.output_row = PathRow(str(self.cfg.get("output_dir")),
                                  t("Where should results be saved?"))
        folder_form.addRow(t("Results"), self.output_row)
        outer.addWidget(folder_box)

        # --- generation --------------------------------------------------
        gen_box = QGroupBox(t("Creating"))
        gen_form = QFormLayout(gen_box)

        self.ratio_combo = QComboBox()
        from app.ratios import RATIO_ORDER
        self.ratio_combo.addItems(RATIO_ORDER)
        index = self.ratio_combo.findText(str(self.cfg.get("default_ratio")))
        self.ratio_combo.setCurrentIndex(index if index >= 0 else 0)
        gen_form.addRow(t("Shape to start with"), self.ratio_combo)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(60, 14400)
        self.timeout_spin.setSingleStep(60)
        self.timeout_spin.setSuffix(t(" seconds"))
        self.timeout_spin.setValue(int(self.cfg.get("job_timeout") or 1800))
        self.timeout_spin.setToolTip(
            t("Give up on a job that takes longer than this."))
        gen_form.addRow(t("Give up after"), self.timeout_spin)

        self.launch_timeout_spin = QSpinBox()
        self.launch_timeout_spin.setRange(30, 1800)
        self.launch_timeout_spin.setSingleStep(30)
        self.launch_timeout_spin.setSuffix(t(" seconds"))
        self.launch_timeout_spin.setValue(int(self.cfg.get("launch_timeout") or 300))
        gen_form.addRow(t("Wait for the engine up to"), self.launch_timeout_spin)

        # Language lives here rather than in its own group: it is a one-line
        # choice, and burying it deeper would be unkind to anyone who has
        # landed in a language they cannot read.
        self.language_combo = QComboBox()
        for code, label in i18n.available().items():
            self.language_combo.addItem(label, code)
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(i18n.current())))
        self.language_combo.setToolTip(
            t("Changes the whole window as soon as you press Save."))
        gen_form.addRow(t("Language"), self.language_combo)
        outer.addWidget(gen_box)

        outer.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._on_dir_changed()

    @staticmethod
    def _wrap(layout) -> QWidget:
        holder = QWidget()
        holder.setLayout(layout)
        return holder

    # ------------------------------------------------------------- helpers
    def _on_dir_changed(self) -> None:
        """Repopulate the start-file list for whatever folder is selected."""
        current = self.launcher_combo.currentText() or str(self.cfg.get("comfyui_launcher"))
        probe = ComfyLauncher(self.dir_row.value(), current, self.client)
        found = probe.find_launchers()

        self.launcher_combo.blockSignals(True)
        self.launcher_combo.clear()
        self.launcher_combo.addItems(found)
        if current:
            index = self.launcher_combo.findText(current)
            if index >= 0:
                self.launcher_combo.setCurrentIndex(index)
            else:
                self.launcher_combo.setEditText(current)
        self.launcher_combo.blockSignals(False)
        self._refresh_engine_hint()

    def _refresh_engine_hint(self) -> None:
        messages: list[str] = []
        colour = theme.OK

        probe = ComfyLauncher(self.dir_row.value(),
                              self.launcher_combo.currentText(), self.client)
        problem = probe.validate()
        if problem:
            messages.append(problem.splitlines()[0])
            colour = theme.WARN

        server = self.server_edit.text().strip() or self.cfg.server
        if ComfyClient(server).is_alive(timeout=1.5):
            messages.append(t("ComfyUI is answering at {server}.", server=server))
        else:
            messages.append(
                t("Nothing is answering at {server} right now.", server=server))
            if not problem:
                colour = theme.WARN

        self.engine_hint.setText("  ".join(messages))
        self.engine_hint.setStyleSheet(f"color:{colour};")

    # -------------------------------------------------------------- saving
    def _save(self) -> None:
        self.cfg.set("comfyui_dir", self.dir_row.value())
        self.cfg.set("comfyui_launcher", self.launcher_combo.currentText().strip())
        self.cfg.set("comfyui_server", self.server_edit.text().strip() or "127.0.0.1:8188")
        self.cfg.set("auto_launch", self.auto_launch.isChecked())
        self.cfg.set("stop_engine_on_exit", self.stop_on_exit.isChecked())
        self.cfg.set("workflow_dir", self.workflow_row.value())
        self.cfg.set("output_dir", self.output_row.value())
        self.cfg.set("default_ratio", self.ratio_combo.currentText())
        self.cfg.set("job_timeout", self.timeout_spin.value())
        self.cfg.set("launch_timeout", self.launch_timeout_spin.value())
        self.cfg.set("first_run_done", True)

        # Remembered outside EasyAI's own settings, so EasyAI Setup opens in
        # the same language without either program reaching into the other's.
        # The window retranslates itself once this dialog closes, so there is
        # nothing to announce and no restart to ask for.
        chosen = self.language_combo.currentData()
        if chosen and chosen != i18n.current():
            i18n.load(chosen)
            i18n.remember(chosen)

        self.cfg.save()
        self.accept()
