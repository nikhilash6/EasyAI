"""The EasyAI Setup window.

One screen, read top to bottom: where it goes, what to include, how big that
is, then Install. The size is deliberately the most prominent number on the
page - agreeing to a 198 GB download by accident would be a miserable surprise.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from app import i18n
from app.i18n import plural, t
from app.ui import theme
from app.ui.widgets import Switch, open_folder
from setup.catalog import Catalog, human_bytes
from setup.download import Progress, format_eta, free_space
from setup.steps import COMFY_SUB, Installer


class InstallWorker(QThread):
    """Runs the install off the interface thread."""

    step = Signal(str)
    progress = Signal(object)
    finished_ok = Signal(object)

    def __init__(self, catalog, target, groups, token, civitai="", parent=None):
        super().__init__(parent)
        self._args = (catalog, target, groups, token, civitai)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        catalog, target, groups, token, civitai = self._args
        installer = Installer(
            catalog, target, groups, hf_token=token, civitai_token=civitai,
            on_step=self.step.emit,
            on_progress=self.progress.emit,
            should_stop=lambda: self._cancelled,
        )
        self.finished_ok.emit(installer.run())


class GroupCard(QFrame):
    """One tickable group, with what it costs."""

    changed = Signal()

    def __init__(self, group, catalog: Catalog, parent=None):
        super().__init__(parent)
        self.group = group
        self.setObjectName("Panel")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        self.tick = Switch(t(group.label))
        self.tick.toggled.connect(lambda _: self.changed.emit())
        layout.addWidget(self.tick, 1)

        blocked = [n for n in group.models if not catalog.models[n].installable]
        parts = [plural(len(group.models), "{n} model", "{n} models"),
                 plural(len(group.nodes), "{n} add-on", "{n} add-ons")]
        if blocked:
            parts.append(t("{n} to copy in by hand", n=len(blocked)))
        detail = QLabel(" · ".join(parts))
        detail.setObjectName("Hint")
        layout.addWidget(detail)

        # Only what will actually come down the wire, so this card and the
        # running total below it can never disagree.
        size = QLabel(human_bytes(catalog.download_bytes([group.key], include_base=False)))
        size.setObjectName("MonoAccent")
        layout.addWidget(size)

    @property
    def checked(self) -> bool:
        return self.tick.isChecked()


class SetupWindow(QMainWindow):
    def __init__(self, catalog: Catalog):
        super().__init__()
        self.catalog = catalog
        self.worker: InstallWorker | None = None
        self._rebuilt = False

        self.setWindowTitle(t("EasyAI Setup"))
        self.resize(880, 780)
        self.setMinimumSize(760, 640)
        self._build()
        self._recalculate()

    def _build(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        # Language sits above everything, because someone who cannot read the
        # window needs to find it without reading the window.
        top = QHBoxLayout()
        title = QLabel(t("Set up ComfyUI for EasyAI"))
        title.setObjectName("Big")
        top.addWidget(title, 1)

        self.language = QComboBox()
        for code, name in i18n.available().items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(
            max(0, self.language.findData(i18n.current())))
        self.language.currentIndexChanged.connect(self._change_language)
        top.addWidget(self.language)
        layout.addLayout(top)

        blurb = QLabel(t(
            "Installs ComfyUI {version}, the add-ons the workflows need, and the "
            "models — all pinned to the versions EasyAI was built against.",
            version=self.catalog.comfyui["version"]))
        blurb.setObjectName("Hint")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        # --- where ---------------------------------------------------------
        where = QLabel(t("WHERE TO PUT IT"))
        where.setObjectName("Heading")
        layout.addWidget(where)

        row = QHBoxLayout()
        self.folder = QLineEdit(str(Path.home() / "EasyAI-ComfyUI"))
        self.folder.textChanged.connect(self._recalculate)
        row.addWidget(self.folder, 1)
        browse = QPushButton(t("Browse…"))
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self.space = QLabel("")
        self.space.setObjectName("Mono")
        layout.addWidget(self.space)

        # --- what ----------------------------------------------------------
        what = QLabel(t("WHAT TO INCLUDE"))
        what.setObjectName("Heading")
        layout.addWidget(what)

        self.cards = []
        for key, group in self.catalog.groups.items():
            card = GroupCard(group, self.catalog)
            card.changed.connect(self._recalculate)
            card.group_key = key
            self.cards.append(card)
            layout.addWidget(card)

        self.total = QLabel("")
        self.total.setObjectName("MonoAccent")
        layout.addWidget(self.total)

        self.warnings = QLabel("")
        self.warnings.setObjectName("Warning")
        self.warnings.setWordWrap(True)
        layout.addWidget(self.warnings)

        self.good_news = QLabel("")
        self.good_news.setObjectName("Good")
        self.good_news.setWordWrap(True)
        layout.addWidget(self.good_news)

        # --- accounts ------------------------------------------------------
        # Only needed for the handful of models that sit behind a sign-in.
        # Typed here, used for this run, and never written to disk.
        self.token = self._token_row(layout, t("HuggingFace key"),
                                     t("only for models behind a licence — hf_…"))
        self.civitai = self._token_row(layout, t("Civitai key"),
                                       t("only for models that need a Civitai sign-in"))

        # --- go ------------------------------------------------------------
        self.install_btn = QPushButton(t("Install"))
        self.install_btn.setObjectName("Primary")
        self.install_btn.clicked.connect(self._start)
        layout.addWidget(self.install_btn)

        self.cancel_btn = QPushButton(t("Stop"))
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel)
        layout.addWidget(self.cancel_btn)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)

        self.current = QLabel("")
        self.current.setObjectName("Mono")
        layout.addWidget(self.current)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(150)
        layout.addWidget(self.log, 1)

        # One screen only. Packaging a workflow lives in EasyAI Studio, which
        # viewers never open - putting it behind a tab here would show every
        # beginner a control that is not for them.
        self.setCentralWidget(page)

        # Ticked last: it triggers _recalculate, which needs every label above.
        if self.cards and not self._rebuilt:
            self.cards[0].tick.setChecked(True)
        self._rebuilt = True

    def _change_language(self) -> None:
        """Rebuild the window in the newly chosen language.

        Rebuilding rather than walking the widgets and re-setting each text is
        both shorter and safer: there is no list of labels to forget to update
        when one is added later.
        """
        code = self.language.currentData()
        if not code or code == i18n.current():
            return
        keep_folder = self.folder.text()
        keep_hf, keep_cv = self.token.text(), self.civitai.text()
        keep_groups = self._selected()

        i18n.load(code)
        i18n.remember(code)

        self.setWindowTitle(t("EasyAI Setup"))
        self._build()
        self.folder.setText(keep_folder)
        self.token.setText(keep_hf)
        self.civitai.setText(keep_cv)
        for card in self.cards:
            card.tick.setChecked(card.group_key in keep_groups)
        self._recalculate()

    def _token_row(self, layout, label: str, hint: str) -> QLineEdit:
        row = QHBoxLayout()
        caption = QLabel(label)
        caption.setObjectName("Heading")
        caption.setMinimumWidth(130)
        row.addWidget(caption)
        field = QLineEdit()
        field.setEchoMode(QLineEdit.Password)
        field.setPlaceholderText(hint)
        row.addWidget(field, 1)
        layout.addLayout(row)
        return field

    # -- selection ---------------------------------------------------------
    def _selected(self) -> list[str]:
        return [c.group_key for c in self.cards if c.checked]

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, t("Where should it go?"),
                                                  self.folder.text())
        if chosen:
            self.folder.setText(chosen)

    def _recalculate(self) -> None:
        groups = self._selected()
        target = Path(self.folder.text() or ".")
        needed = self.catalog.download_bytes(groups)
        free = free_space(target)

        labels = " + ".join(t(self.catalog.groups[g].label) for g in groups)
        count = len([m for m in self.catalog.models_for(groups) if m.installable])
        self.total.setText(
            plural(count, "{labels} — {n} model, {size} to download",
                   "{labels} — {n} models, {size} to download",
                   labels=labels, size=human_bytes(needed))
            if groups else t("Nothing chosen — tick at least one group above"))
        self.space.setText(t("{size} free on that drive", size=human_bytes(free)))

        notes = []
        if free and needed and free < needed * 1.15:
            notes.append(t("⚠  Not enough room — this needs about {size}.",
                           size=human_bytes(needed * 1.15)))
        blocked = self.catalog.blocked(groups)
        if blocked:
            names = ", ".join(Path(m.name).name for m in blocked[:3])
            if len(blocked) > 3:
                names += " …"
            notes.append(plural(
                len(blocked),
                "⚠  {n} model with no download link — copy it in by hand "
                "afterwards: {names}",
                "⚠  {n} models with no download link — copy them in by hand "
                "afterwards: {names}", names=names))
        gated = self.catalog.gated(groups)
        if gated:
            # Name the site the key is actually for - saying "HuggingFace" for
            # a Civitai file sends someone to the wrong account page.
            sites = sorted({m.token_host for m in gated if m.token_host})
            where = " and ".join(sites) if sites else t("an account")
            notes.append(plural(
                len(gated),
                "⚠  {n} model needs a {where} key, and the licence accepted on "
                "the model's page.",
                "⚠  {n} models need a {where} key, and the licence accepted on "
                "the model's page.", where=where))
        self.warnings.setText("\n".join(notes))

        # Said plainly, because "no account needed" is the reason any of this
        # exists and it should be visible before the user starts worrying.
        mirrored = self.catalog.mirrored(groups)
        if mirrored and not gated:
            self.good_news.setText(plural(
                len(mirrored),
                "✓  No account needed — {n} model that would normally ask you to "
                "sign in comes straight from the EasyAI server.",
                "✓  No account needed — {n} models that would normally ask you to "
                "sign in come straight from the EasyAI server."))
        elif mirrored:
            self.good_news.setText(t(
                "✓  {n} of them come from the EasyAI server, so no account is "
                "needed for those.", n=len(mirrored)))
        else:
            self.good_news.setText("")

        self.install_btn.setEnabled(bool(groups))

    # -- running -----------------------------------------------------------
    def _start(self) -> None:
        groups = self._selected()
        target = Path(self.folder.text())
        needed = self.catalog.download_bytes(groups)

        if QMessageBox.question(
                self, t("Start the download?"),
                t("About to download {size} into:\n{folder}\n\n"
                  "This can take several hours. You can stop and re-run later — "
                  "it picks up where it left off.",
                  size=human_bytes(needed), folder=target),
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        self._set_running(True)
        self.log.clear()
        self.worker = InstallWorker(self.catalog, target, groups,
                                    self.token.text().strip(),
                                    self.civitai.text().strip())
        self.worker.step.connect(self._on_step)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.finished.connect(lambda: self._set_running(False))
        self.worker.start()

    def _cancel(self) -> None:
        if self.worker:
            self._on_step(t("Stopping after the current file…"))
            self.worker.cancel()

    def _set_running(self, running: bool) -> None:
        self.install_btn.setVisible(not running)
        self.cancel_btn.setVisible(running)
        self.bar.setVisible(running)
        for card in self.cards:
            card.setEnabled(not running)
        self.folder.setEnabled(not running)

    def _on_step(self, message: str) -> None:
        self.log.append(message)

    def _on_progress(self, progress: Progress) -> None:
        self.bar.setValue(progress.percent)
        speed = f"{human_bytes(progress.speed)}/s" if progress.speed else ""
        self.current.setText(t(
            "{name}   {done} of {total}   {speed}   {eta}",
            name=progress.name, done=human_bytes(progress.done),
            total=human_bytes(progress.total), speed=speed,
            eta=format_eta(progress.eta_seconds)))

    def _on_done(self, report) -> None:
        self.current.setText("")
        if "cancelled" in report.failed:
            QMessageBox.information(
                self, t("Stopped"),
                t("Stopped. Run this again when you are ready — anything already "
                  "downloaded is kept, and part-finished files carry on from where "
                  "they stopped."))
            return
        if report.failed:
            QMessageBox.warning(
                self, t("Finished, with some problems"),
                t("{done} items installed, but {failed} need attention:",
                  done=len(report.done), failed=len(report.failed))
                + "\n\n" + "\n".join(report.failed[:8]))
        else:
            comfy = Path(self.folder.text()) / COMFY_SUB
            if "easyai-settings" in report.done:
                # Setup has already written the path into EasyAI's own
                # settings, so there is nothing left to configure.
                QMessageBox.information(
                    self, t("Done"),
                    t("ComfyUI is installed, and EasyAI is set up to use "
                      "it.\n\nJust open EasyAI and start making things."))
            else:
                # Only reached if the settings could not be written. The
                # portable folder itself is what EasyAI needs - pointing it
                # at the folder holding that one finds no launcher.
                QMessageBox.information(
                    self, t("Done"),
                    t("ComfyUI is installed.\n\n"
                      "In EasyAI, open Settings and set the ComfyUI folder "
                      "to:\n\n{folder}\n\n"
                      "Then press Test now, and Save.", folder=comfy))
                open_folder(comfy)


def run() -> int:
    """Entry point used by EasyAISetup.py."""
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    i18n.start()
    theme.apply(app)
    window = SetupWindow(Catalog())
    window.show()
    return app.exec()
