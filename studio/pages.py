"""The maintenance pages: catalogue, links, languages.

Each is the same shape - a short explanation of what the job is for, a button,
and the log it produces - because each is a console tool underneath. The value
the window adds is knowing which tool to run, what state things are in before
you run it, and reading the result without a scrollback buffer.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from app.i18n import plural, t
from app.paths import PROJECT_ROOT
from studio.jobs import Job

TOOLS = PROJECT_ROOT / "tools"


class ToolPage(QWidget):
    """A heading, a state summary, one or more buttons, and a log."""

    def __init__(self, title: str, blurb: str, parent=None):
        super().__init__(parent)
        self.job: Job | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        heading = QLabel(title)
        heading.setObjectName("Big")
        layout.addWidget(heading)

        note = QLabel(blurb)
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.summary = QLabel("")
        self.summary.setObjectName("Mono")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.buttons = QHBoxLayout()
        self.buttons.addStretch(1)
        layout.addLayout(self.buttons)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

    def add_button(self, text: str, work, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        if primary:
            button.setObjectName("Primary")
        button.clicked.connect(lambda: self._run(work))
        self.buttons.insertWidget(self.buttons.count() - 1, button)
        return button

    def _run(self, work) -> None:
        if self.job and self.job.isRunning():
            return
        self.log.clear()
        self._set_busy(True)
        self.job = Job(work, self)
        self.job.line.connect(self.log.append)
        self.job.done.connect(self._finished)
        self.job.start()

    def _set_busy(self, busy: bool) -> None:
        for i in range(self.buttons.count()):
            widget = self.buttons.itemAt(i).widget()
            if widget:
                widget.setEnabled(not busy)

    def _finished(self, ok: bool, problem: str) -> None:
        self._set_busy(False)
        self.log.append("")
        self.log.append(t("Finished.") if ok
                        else t("Stopped: {problem}", problem=problem))
        self.refresh()

    def refresh(self) -> None:
        """Re-read whatever this page reports at the top. Overridden below."""


def _run_tool(module_name: str, argv: list[str]):
    """Import a tools/ script and call its main() with the given arguments."""
    def work():
        sys.path.insert(0, str(TOOLS))
        import importlib
        module = importlib.import_module(module_name)
        importlib.reload(module)
        saved = sys.argv
        sys.argv = [module_name] + argv
        try:
            raise SystemExit(module.main())
        finally:
            sys.argv = saved
    return work


class CataloguePage(ToolPage):
    """What EasyAI Setup will install, rebuilt from this machine."""

    def __init__(self, parent=None):
        super().__init__(
            t("Catalogue"),
            t("The list of everything EasyAI Setup installs, built from this "
              "computer. Rebuild it after adding a workflow or changing a "
              "model. ComfyUI must be running for a full rebuild."),
            parent)
        self.add_button(t("Update folders and mirrors"),
                        _run_tool("make_catalog", ["--mirrors-only"]))
        self.add_button(t("Rebuild everything"),
                        _run_tool("make_catalog", []), primary=True)
        self.refresh()

    def refresh(self) -> None:
        try:
            from setup.catalog import Catalog, human_bytes
            catalog = Catalog()
            groups = list(catalog.groups)
            total = sum(m.bytes for m in catalog.models.values())
            gated = len(catalog.gated(groups))
            mirrored = len(catalog.mirrored(groups))
            blocked = len(catalog.blocked(groups))
            bits = [
                t("ComfyUI {version}", version=catalog.comfyui.get("version", "?")),
                plural(len(catalog.models), "{n} model", "{n} models"),
                human_bytes(total),
                plural(len(catalog.nodes), "{n} add-on", "{n} add-ons"),
            ]
            if mirrored:
                bits.append(t("{n} mirrored", n=mirrored))
            if gated:
                bits.append(t("{n} need an account", n=gated))
            if blocked:
                bits.append(t("{n} with no link", n=blocked))
            self.summary.setText("   ·   ".join(bits))
        except Exception as e:                  # noqa: BLE001
            self.summary.setText(t("Could not read the catalogue: {problem}",
                                   problem=e))


class LinksPage(ToolPage):
    """Every download link, checked against the file on this machine."""

    def __init__(self, parent=None):
        super().__init__(
            t("Download links"),
            t("Checks that every link still works and still serves the same "
              "file. A link that resolves but serves a different build is the "
              "worst kind of fault: it downloads perfectly and then produces "
              "the wrong results."),
            parent)
        self.add_button(t("Check the original sources"),
                        _run_tool("verify_urls", []), primary=True)
        self.add_button(t("Check my mirror"),
                        _run_tool("verify_urls", ["--mirror"]))
        self.refresh()

    def refresh(self) -> None:
        try:
            from setup.catalog import Catalog
            catalog = Catalog()
            groups = list(catalog.groups)
            models = catalog.models_for(groups)
            self.summary.setText("   ·   ".join([
                plural(len(models), "{n} link to check", "{n} links to check"),
                t("{n} from my mirror", n=len(catalog.mirrored(groups))),
                t("{n} need an account", n=len(catalog.gated(groups))),
            ]))
        except Exception as e:                  # noqa: BLE001
            self.summary.setText(t("Could not read the catalogue: {problem}",
                                   problem=e))


class LanguagePage(ToolPage):
    """Keeping the translations level with the code."""

    def __init__(self, parent=None):
        super().__init__(
            t("Languages"),
            t("Finds text added to the programs since the translations were "
              "last updated. New text is added to each language file with an "
              "empty value, ready to be filled in."),
            parent)
        self.add_button(t("What is missing?"), _run_tool("make_lang", []))
        self.add_button(t("Add new text to the language files"),
                        _run_tool("make_lang", ["--write"]), primary=True)
        self.refresh()

    def refresh(self) -> None:
        try:
            import json

            from app import i18n
            bits = []
            for code, name in i18n.available().items():
                if code == "en":
                    continue
                raw = json.loads((i18n.LANG_DIR / f"{code}.json")
                                 .read_text(encoding="utf-8-sig"))
                entries = {k: v for k, v in raw.items() if not k.startswith("_")}
                done = sum(1 for v in entries.values() if v)
                bits.append(f"{name} {done}/{len(entries)}")
            self.summary.setText("   ·   ".join(bits) or t("English only"))
        except Exception as e:                  # noqa: BLE001
            self.summary.setText(t("Could not read the language files: {problem}",
                                   problem=e))
