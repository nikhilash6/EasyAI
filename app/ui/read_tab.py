"""The Read tab: what prompt made this picture or video?

Sits after the Queue. Like it, this is not a Mode, so the window adds it
directly rather than through MODE_ORDER.

It reads the file rather than the engine, so it works with ComfyUI closed -
which is when someone is most likely to be looking back through old results.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from app.i18n import t
from app.prompts import IMAGE_EXT, VIDEO_EXT, read_prompt, recent_results
from app.modes import MODES
from app.ui import theme
from app.ui.widgets import DropZone, PreviewPane, ResultGallery


class ReadTab(QWidget):
    """Drop something in, or click one of your results, and see its prompt."""

    #: (mode key, prompt) - the window already knows how to deliver this.
    send_to_requested = Signal(str, str)
    status_message = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._workflows: list = []
        self._prompt: str = ""

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(16)
        row.addLayout(self._build_left(), 3)
        row.addLayout(self._build_right(), 4)

    # ------------------------------------------------------------------ UI
    def _build_left(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(10)

        # A noun, not an instruction: DropZone also builds its file-dialog
        # title from this as "Choose a {label}".
        self.drop = DropZone(t("Picture or video"), IMAGE_EXT | VIDEO_EXT)
        self.drop.changed.connect(self._on_dropped)
        column.addWidget(self.drop)

        # ResultGallery carries its own "Your results" heading and folder
        # button, so a second heading here would only repeat it.
        self.gallery = ResultGallery()
        self.gallery.selected.connect(self._read)
        column.addWidget(self.gallery, 1)
        return column

    def _build_right(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(10)

        self.preview = PreviewPane()
        self.preview.clear_preview(
            t("Choose a picture or video to see the prompt that made it"))
        column.addWidget(self.preview, 3)

        self.source = QLabel("")
        self.source.setObjectName("Hint")
        column.addWidget(self.source)

        self.prompt_heading = QLabel(t("THE PROMPT"))
        self.prompt_heading.setObjectName("Heading")
        column.addWidget(self.prompt_heading)

        self.prompt_box = QPlainTextEdit()
        self.prompt_box.setReadOnly(True)
        self.prompt_box.setPlaceholderText(
            t("Nothing read yet — choose a file on the left."))
        column.addWidget(self.prompt_box, 2)

        buttons = QHBoxLayout()
        self.copy_btn = QPushButton(t("Copy"))
        self.copy_btn.clicked.connect(self._copy)
        buttons.addWidget(self.copy_btn)
        buttons.addStretch(1)

        self.use_image_btn = QPushButton(t("Use this in Image"))
        self.use_image_btn.setObjectName("Primary")
        self.use_image_btn.clicked.connect(lambda: self._send("image"))
        buttons.addWidget(self.use_image_btn)

        self.use_video_btn = QPushButton(t("Use this in Video"))
        self.use_video_btn.clicked.connect(lambda: self._send("video"))
        buttons.addWidget(self.use_video_btn)
        column.addLayout(buttons)

        self._set_have_prompt(False)
        return column

    # --------------------------------------------------------------- state
    def _set_have_prompt(self, have: bool) -> None:
        for button in (self.copy_btn, self.use_image_btn, self.use_video_btn):
            button.setEnabled(have)

    def showEvent(self, event) -> None:
        """Rebuild from disk each time the tab is opened.

        Results made since it was last looked at should be here, and a file
        deleted in Explorer should not still be listed.
        """
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        self._load_workflows()
        folders = [self.cfg.output_dir(mode) for mode in MODES]
        self.gallery.clear()
        found = recent_results(folders)
        # add() puts each at the top, so the oldest goes in first to leave the
        # newest at the top of the list.
        for path in reversed(found):
            self.gallery.add(path)
        self.gallery.set_folder(self.cfg.output_dir())
        self.gallery.empty_hint.setVisible(not found)

    def _load_workflows(self) -> None:
        """The workflows are what make a reading trustworthy rather than a
        guess, so they are reloaded rather than remembered from startup."""
        from app.workflows.loader import scan_all

        try:
            groups = scan_all(self.cfg.get("workflow_dir"),
                              auto_write_manifest=False)
        except Exception as e:      # a bad workflow folder must not break this
            print(f"[read] could not load the workflows: {e}")
            self._workflows = []
            return
        self._workflows = [w for group in groups.values() for w in group]

    # -------------------------------------------------------------- doing it
    def _on_dropped(self, path: str) -> None:
        if path:
            self._read(path)

    def _read(self, path: str) -> None:
        path = Path(path)
        if not path.is_file():
            self._show_nothing(t("That file is no longer there."))
            return

        if path.suffix.lower() in VIDEO_EXT:
            self.preview.show_video(path)
        else:
            self.preview.show_file(path)

        if not self._workflows:
            self._load_workflows()
        found = read_prompt(path, self._workflows)

        if found is None:
            self._show_nothing(t(
                "This file does not carry a prompt. Pictures lose it when they "
                "are re-saved, sent through a messaging app, or downloaded from "
                "most websites."))
            return

        self._prompt = found.prompt
        self.prompt_box.setPlainText(found.prompt)
        self._set_have_prompt(True)
        if found.certain and found.workflow_name:
            self.source.setText(t("from {name}", name=found.workflow_name))
            self.source.setStyleSheet(f"color:{theme.MUTED};")
        else:
            # Nothing here knows this workflow, so the prompt came from
            # detection. Say so rather than state it as fact.
            self.source.setText(
                t("best guess — this workflow is not one of yours"))
            self.source.setStyleSheet(f"color:{theme.WARN};")
        self.status_message.emit(t("Read the prompt from {file}.",
                                   file=path.name))

    def _show_nothing(self, message: str) -> None:
        self._prompt = ""
        self.prompt_box.setPlainText("")
        self.source.setText(message)
        self.source.setStyleSheet(f"color:{theme.MUTED};")
        self._set_have_prompt(False)

    def _copy(self) -> None:
        if not self._prompt:
            return
        QApplication.clipboard().setText(self._prompt)
        self.status_message.emit(t("Prompt copied."))

    def _send(self, mode_key: str) -> None:
        if self._prompt:
            self.send_to_requested.emit(mode_key, self._prompt)

    # -------------------------------------------------------------- language
    def retranslate(self) -> None:
        self.drop.retranslate()
        self.gallery.retranslate()
        self.prompt_heading.setText(t("THE PROMPT"))
        self.prompt_box.setPlaceholderText(
            t("Nothing read yet — choose a file on the left."))
        self.copy_btn.setText(t("Copy"))
        self.use_image_btn.setText(t("Use this in Image"))
        self.use_video_btn.setText(t("Use this in Video"))
        if not self._prompt:
            self.preview.clear_preview(
                t("Choose a picture or video to see the prompt that made it"))
