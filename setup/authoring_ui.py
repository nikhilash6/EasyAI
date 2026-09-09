"""The "Add a workflow" screen.

A publisher-side view: it packages a workflow that already runs on this
machine so EasyAI Setup can install its models for everyone else. It is
deliberately blunt about what it cannot confirm, because the failures here are
quiet ones - a model recorded against the wrong folder downloads perfectly and
is simply never found.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from app.comfy import objectinfo
from app.comfy.client import ComfyClient
from app.config import Config
from app.i18n import t
from app.modes import MODE_ORDER, MODES
from setup import authoring


class LookWorker(QThread):
    """The inspection, off the interface thread - it reads every add-on's
    source and walks fifty model folders."""

    done = Signal(object, str)

    def __init__(self, path: Path, group: str, parent=None):
        super().__init__(parent)
        self.path, self.group = path, group

    def run(self) -> None:
        try:
            cfg = Config()
            client = ComfyClient(cfg.server)
            caps = objectinfo.fetch(client, refresh=True)
            if not caps.available:
                self.done.emit(None, t(
                    "ComfyUI is not running.\n\nIt has to be, because only the "
                    "engine can say which folder each model belongs in and "
                    "which nodes are add-ons rather than built in."))
                return

            import sys
            root = Path(__file__).resolve().parent.parent
            sys.path.insert(0, str(root / "tools"))
            import make_catalog

            result = authoring.analyse(
                self.path, self.group, client, caps,
                models_root=Path(make_catalog.MODELS_ROOT),
                harvested=make_catalog.harvest_urls(),
                owner_of=make_catalog.owner_of,
                core_classes=make_catalog.core_classes)
            self.done.emit(result, "")
        except authoring.AuthoringError as e:
            self.done.emit(None, str(e))
        except Exception as e:                      # noqa: BLE001
            self.done.emit(None, f"{type(e).__name__}: {e}")


class AuthoringView(QWidget):
    """Pick a workflow, see what it needs, add it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: authoring.Analysis | None = None
        self.worker: LookWorker | None = None
        self._link_boxes: dict[str, QLineEdit] = {}
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        title = QLabel(t("Add a workflow"))
        title.setObjectName("Big")
        layout.addWidget(title)

        blurb = QLabel(t(
            "Packages a workflow that already runs on this computer, so EasyAI "
            "Setup can download its models for everyone else. ComfyUI must be "
            "running."))
        blurb.setObjectName("Hint")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText(
            t("the workflow exported with Workflow → Export (API)"))
        row.addWidget(self.file_edit, 1)
        browse = QPushButton(t("Browse…"))
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        group_row = QHBoxLayout()
        caption = QLabel(t("Goes in"))
        caption.setObjectName("Heading")
        group_row.addWidget(caption)
        self.group_combo = QComboBox()
        for key in MODE_ORDER:
            self.group_combo.addItem(t(MODES[key].label), key)
        group_row.addWidget(self.group_combo)
        group_row.addStretch(1)
        self.look_btn = QPushButton(t("Have a look"))
        self.look_btn.clicked.connect(self._look)
        group_row.addWidget(self.look_btn)
        layout.addLayout(group_row)

        self.report = QTextEdit()
        self.report.setReadOnly(True)
        self.report.setMinimumHeight(240)
        layout.addWidget(self.report, 1)

        self.links_note = QLabel("")
        self.links_note.setObjectName("Warning")
        self.links_note.setWordWrap(True)
        layout.addWidget(self.links_note)

        self.add_btn = QPushButton(t("Add to EasyAI"))
        self.add_btn.setObjectName("Primary")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self._add)
        layout.addWidget(self.add_btn)

    # -- actions -----------------------------------------------------------
    def _browse(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, t("Which workflow?"), self.file_edit.text(),
            t("Workflow files (*.json)"))
        if chosen:
            self.file_edit.setText(chosen)
            self._guess_group(Path(chosen))

    def _guess_group(self, path: Path) -> None:
        """A default, not a decision - text-to-video and text-to-image graphs
        look much alike, so the choice stays with the user."""
        text = path.name.lower()
        for key, words in (("video", ("video", "v2v", "i2v", "t2v", "flf")),
                           ("prompt-enhancer", ("prompt", "enhanc"))):
            if any(w in text for w in words):
                index = self.group_combo.findData(key)
                if index >= 0:
                    self.group_combo.setCurrentIndex(index)
                return

    def _look(self) -> None:
        path = Path(self.file_edit.text().strip())
        if not path.is_file():
            QMessageBox.warning(self, t("Add a workflow"),
                                t("Pick a workflow file first."))
            return
        self.look_btn.setEnabled(False)
        self.add_btn.setEnabled(False)
        self.report.setPlainText(t("Looking at it…"))
        self.worker = LookWorker(path, self.group_combo.currentData(), self)
        self.worker.done.connect(self._looked)
        self.worker.finished.connect(lambda: self.look_btn.setEnabled(True))
        self.worker.start()

    def _looked(self, result, problem: str) -> None:
        self.result = result
        if result is None:
            self.report.setPlainText(problem)
            self.links_note.setText("")
            return

        lines = [f"{result.name}   —   {result.node_count} nodes", ""]
        lines.append(t("EasyAI can drive: {slots}",
                       slots=", ".join(result.manifest_slots) or t("nothing")))
        lines.append("")
        lines.append(t("Models ({total} found, {new} new)",
                       total=len(result.models), new=len(result.new_models)))
        for m in result.models:
            mark = "  new " if not m.known else "      "
            size = f"{m.bytes / 1024 ** 2:,.0f} MB" if m.bytes else "?"
            where = f"models/{m.folder}" + ("  (guessed)" if m.folder_guessed else "")
            link = t("NO LINK") if not m.url else m.url.split("/")[2]
            lines.append(f"{mark}{m.filename[:44]:<46} {where:<30} {size:>10}  {link}")

        if result.nodes:
            lines.append("")
            lines.append(t("Add-ons ({n})", n=len(result.nodes)))
            for n in result.nodes:
                lines.append(f"  {'new ' if not n.known else '    '}{n.name:<34} "
                             f"{n.source or t('NOT RESOLVED')}")
        for warning in result.warnings:
            lines.append("")
            lines.append("!  " + warning)
        for problem_text in result.problems:
            lines.append("")
            lines.append("STOPS THE ADD:  " + problem_text)

        self.report.setPlainText("\n".join(lines))

        if result.needs_link:
            names = ", ".join(m.filename for m in result.needs_link[:3])
            self.links_note.setText(t(
                "{n} model(s) have no download link, so viewers could not get "
                "them: {names}. Add each to setup/url_overrides.json, then look "
                "again.", n=len(result.needs_link), names=names))
        elif result.problems:
            self.links_note.setText("")
        else:
            self.links_note.setText("")
        self.add_btn.setEnabled(result.can_add)

    def _add(self) -> None:
        if not (self.result and self.result.can_add):
            return
        try:
            done = authoring.apply(self.result)
            staged = authoring.stage_for_mirror(self.result)
        except authoring.AuthoringError as e:
            QMessageBox.warning(self, t("Add a workflow"), str(e))
            return

        message = "\n".join(done)
        if staged:
            message += "\n\n" + t("Needs uploading to your server:") + "\n\n"
            message += "\n\n".join(staged)
        message += "\n\n" + t("Now run:  python tools/verify_urls.py")
        QMessageBox.information(self, t("Added"), message)
        self.add_btn.setEnabled(False)
