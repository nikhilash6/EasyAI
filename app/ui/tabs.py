"""The mode tabs.

GenerateTab already adapts itself from the Mode record and the workflow's
manifest, so most of these are pure declaration. Only the places where a mode
genuinely behaves differently are spelled out.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from app.i18n import t
from app.modes import MODE_ORDER, MODES
from app.ui.tab_base import GenerateTab
from app.ui.widgets import ResultGallery


class ImageTab(GenerateTab):
    def __init__(self, cfg, client, parent=None):
        super().__init__(MODES["image"], cfg, client, parent)


class VideoTab(GenerateTab):
    """Same screen, but video runs are long, so say so."""

    def __init__(self, cfg, client, parent=None):
        super().__init__(MODES["video"], cfg, client, parent)
        self.create_btn.setToolTip(t(
            "Video takes a lot longer than pictures — often several minutes."))


class PromptEnhancerTab(GenerateTab):
    """Turns a rough idea into the long, detailed prompt the models want.

    The result is words rather than a file, so the right-hand column is a text
    box with Copy and Send-to buttons instead of a picture preview and gallery.
    """

    #: (mode key, text) - asks the window to drop the result into another tab.
    send_to_requested = Signal(str, str)

    def __init__(self, cfg, client, parent=None):
        super().__init__(MODES["prompt-enhancer"], cfg, client, parent)
        self.prompt_label.setText(t("What do you want to describe?"))
        self.prompt_box.setMinimumHeight(110)
        self.create_btn.setText(t("Improve my prompt"))

        # This whole workflow is a prompt enhancer, so offering to switch the
        # enhancer off would be nonsense.
        self.enhance_check.setVisible(False)
        self.enhance_check.setChecked(True)

    def _build_right(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(8)

        self.heading = QLabel(t("The improved prompt"))
        self.heading.setObjectName("Heading")
        layout.addWidget(self.heading)

        self.result_box = QTextEdit()
        self.result_box.setReadOnly(True)
        self.result_box.setPlaceholderText(t(
            "Your longer, more detailed prompt will appear here.\n\n"
            "Copy it, or send it straight to the Image or Video tab."))
        layout.addWidget(self.result_box, 1)

        row = QHBoxLayout()
        self.copy_btn = QPushButton(t("Copy"))
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_result)
        row.addWidget(self.copy_btn)

        self.send_combo = QComboBox()
        for key in MODE_ORDER:
            if key != self.mode.key:
                self.send_combo.addItem(
                    t("Send to {tab}", tab=t(MODES[key].label)), key)
        self.send_combo.setEnabled(False)
        row.addWidget(self.send_combo, 1)

        self.send_btn = QPushButton(t("Go"))
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send_result)
        row.addWidget(self.send_btn)
        layout.addLayout(row)

        self.history = ResultGallery()
        self.history.selected.connect(self._show_saved)
        layout.addWidget(self.history, 1)
        # The shared code writes results into self.gallery; point it here.
        self.gallery = self.history
        return panel

    def retranslate(self) -> None:
        super().retranslate()
        # This tab renames two of the shared widgets and adds four of its own.
        self.prompt_label.setText(t("What do you want to describe?"))
        self.create_btn.setText(t("Improve my prompt"))
        self.heading.setText(t("The improved prompt"))
        self.result_box.setPlaceholderText(t(
            "Your longer, more detailed prompt will appear here.\n\n"
            "Copy it, or send it straight to the Image or Video tab."))
        self.copy_btn.setText(t("Copy"))
        self.send_btn.setText(t("Go"))
        for index in range(self.send_combo.count()):
            key = self.send_combo.itemData(index)
            self.send_combo.setItemText(
                index, t("Send to {tab}", tab=t(MODES[key].label)))

    # -- results -----------------------------------------------------------
    def _on_success(self, result) -> None:
        super()._on_success(result)
        if result.text:
            self._set_result(result.text)

    def _set_result(self, text: str) -> None:
        self.result_box.setPlainText(text)
        for widget in (self.copy_btn, self.send_combo, self.send_btn):
            widget.setEnabled(bool(text.strip()))

    def _show_saved(self, path: str) -> None:
        try:
            self._set_result(Path(path).read_text(encoding="utf-8"))
        except OSError:
            pass

    def _copy_result(self) -> None:
        QApplication.clipboard().setText(self.result_box.toPlainText())
        self.status_message.emit(t("Copied. Paste it into any prompt box."))

    def _send_result(self) -> None:
        text = self.result_box.toPlainText().strip()
        if text:
            self.send_to_requested.emit(self.send_combo.currentData(), text)

    # The picture preview does not exist on this tab.
    def _on_gallery_pick(self, path: str) -> None:
        self._show_saved(path)


TAB_CLASSES = {
    "image": ImageTab,
    "video": VideoTab,
    "prompt-enhancer": PromptEnhancerTab,
}


def build_tab(mode_key: str, cfg, client, parent=None) -> GenerateTab:
    return TAB_CLASSES[mode_key](cfg, client, parent)
