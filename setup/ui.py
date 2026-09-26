"""The EasyAI Setup window.

One screen, read top to bottom: where it goes, what to include, how big that
is, then Install. The size is deliberately the most prominent number on the
page - agreeing to a 198 GB download by accident would be a miserable surprise.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QTextEdit, QVBoxLayout,
    QWidget,
)

from app import VERSION_LABEL, i18n
from app.i18n import plural, t
from app.ui import theme
from app.ui.widgets import Switch, open_folder, open_in_explorer
from setup import existing, install_list
from setup.catalog import Catalog, human_bytes
from setup.download import Progress, format_eta, free_space
from setup.steps import COMFY_SUB, Installer


class InstallWorker(QThread):
    """Runs the install off the interface thread."""

    step = Signal(str)
    progress = Signal(object)
    finished_ok = Signal(object)

    def __init__(self, catalog, target, groups, token, civitai="",
                 update_existing=False, download_models=True, parent=None):
        super().__init__(parent)
        self._args = (catalog, target, groups, token, civitai)
        self._choices = {"update_existing": update_existing,
                         "download_models": download_models}
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
            **self._choices,
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
    def __init__(self, catalog: Catalog, loaded: "install_list.Loaded | None" = None):
        super().__init__()
        self.catalog = catalog
        #: The setup-settings.json this list came from, if it came from one.
        self.loaded = loaded
        self.worker: InstallWorker | None = None
        self._rebuilt = False
        #: What is in the chosen folder, refreshed as the folder changes.
        self.survey: existing.Survey | None = None
        #: ComfyUI's model folders per portable, so ticking a group card does
        #: not start a Python process each time.
        self._search_cache: dict[str, dict] = {}
        #: The folder the update switch last had its default set for, so a
        #: user's own choice is not reset by ticking a group.
        self._update_default_for = ""
        self._survey_timer = QTimer(self)
        self._survey_timer.setSingleShot(True)
        self._survey_timer.setInterval(350)
        self._survey_timer.timeout.connect(self._recalculate)

        self.setWindowTitle(f"{t('EasyAI Setup')}  ·  {VERSION_LABEL}")
        self.resize(880, 780)
        self.setMinimumSize(760, 640)
        self._build()
        self._recalculate()

    def _build(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        # Scrollable, so a small screen still reaches Install; the log keeps
        # the spare height on a big one.
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.NoFrame)
        scroller.setWidget(page)
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
            "models — all pinned to the versions EasyAI was built against. "
            "Point it at a ComfyUI you already have and it can update that "
            "instead.", version=self.catalog.comfyui["version"]))
        blurb.setObjectName("Hint")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        # The list of what gets installed, and where - editable beside Setup.
        list_row = QHBoxLayout()
        self.list_label = QLabel("")
        self.list_label.setObjectName("Mono")
        self.list_label.setWordWrap(True)
        list_row.addWidget(self.list_label, 1)
        self.list_open = QPushButton(t("Open the install list"))
        self.list_open.setToolTip(t(
            "setup-settings.json lists every model and workflow, where each "
            "comes from and where it goes. Edit it, then press Reload."))
        self.list_open.clicked.connect(self._open_list)
        list_row.addWidget(self.list_open)
        self.list_reload = QPushButton(t("Reload"))
        self.list_reload.clicked.connect(self._reload_list)
        list_row.addWidget(self.list_reload)
        layout.addLayout(list_row)

        self.list_note = QLabel("")
        self.list_note.setWordWrap(True)
        layout.addWidget(self.list_note)
        self.list_replace = QPushButton(t("Use this Setup's list instead"))
        self.list_replace.clicked.connect(self._replace_list)
        layout.addWidget(self.list_replace)
        self._show_list_state()

        # --- where ---------------------------------------------------------
        where = QLabel(t("WHERE TO PUT IT"))
        where.setObjectName("Heading")
        layout.addWidget(where)

        row = QHBoxLayout()
        self.folder = QLineEdit(str(Path.home() / "EasyAI-ComfyUI"))
        # Waits for typing to pause: each change looks inside the folder.
        self.folder.textChanged.connect(lambda _: self._survey_timer.start())
        row.addWidget(self.folder, 1)
        browse = QPushButton(t("Browse…"))
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self.space = QLabel("")
        self.space.setObjectName("Mono")
        layout.addWidget(self.space)

        # What is already in that folder, and whether to bring it up to date.
        self.found = QLabel("")
        self.found.setWordWrap(True)
        layout.addWidget(self.found)
        self.update_switch = Switch("")
        self.update_switch.setVisible(False)
        self.update_switch.toggled.connect(lambda _: self._recalculate(rescan=False))
        layout.addWidget(self.update_switch)

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

        self.models_switch = Switch(t("Download the models ComfyUI does not have yet"))
        self.models_switch.setChecked(True)
        self.models_switch.setToolTip(t(
            "Models already in ComfyUI are never downloaded again, wherever "
            "ComfyUI keeps them. Turn this off to install or update ComfyUI "
            "and the add-ons only."))
        self.models_switch.toggled.connect(lambda _: self._recalculate(rescan=False))
        layout.addWidget(self.models_switch)

        self.total = QLabel("")
        self.total.setObjectName("MonoAccent")
        self.total.setWordWrap(True)
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
        self.setCentralWidget(scroller)

        # Ticked last: it triggers _recalculate, which needs every label above.
        if self.cards and not self._rebuilt:
            self.cards[0].tick.setChecked(True)
        self._rebuilt = True

    # -- the install list ------------------------------------------------------
    def _show_list_state(self) -> None:
        loaded = self.loaded
        self.list_replace.setVisible(False)
        if loaded is None:
            self.list_label.setText(t("Install list: built into EasyAI Setup"))
            self.list_open.setVisible(False)
            self.list_reload.setVisible(False)
            self.list_note.setText("")
            return
        self.list_label.setText(t("Install list: {path}", path=loaded.path))
        notes = {
            "created": ("Good", t(
                "✓  Written out for the first time. Edit it to change what is "
                "installed and where, then press Reload.")),
            "refreshed": ("Good", t(
                "✓  Brought up to date with this EasyAI Setup's list.")),
            "unwritable": ("Warning", t(
                "⚠  Could not save the install list beside EasyAI Setup - the "
                "folder is read-only - so the built-in list is used. Move EasyAI "
                "Setup to a folder you can write to, to edit it.")),
        }
        kind, text = notes.get(loaded.status, ("Hint", ""))
        if loaded.status == "outdated":
            kind = "Warning"
            names = ", ".join(Path(n).stem for n in loaded.missing[:4])
            if len(loaded.missing) > 4:
                names += " …"
            text = (t("⚠  This list was made by an older EasyAI Setup and has "
                      "been edited, so it was kept as it is.")
                    + (" " + t("It is missing: {names}", names=names) if names else ""))
            self.list_replace.setVisible(True)
        self.list_note.setObjectName(kind)
        self.list_note.setText(text)
        self.list_note.style().unpolish(self.list_note)
        self.list_note.style().polish(self.list_note)

    def _open_list(self) -> None:
        if not self.loaded:
            return
        import os
        try:
            os.startfile(str(self.loaded.path))        # the user's own JSON editor
        except OSError:
            open_in_explorer(self.loaded.path)

    def _reload_list(self) -> None:
        try:
            loaded = install_list.load(self.loaded.path if self.loaded else None)
        except install_list.InstallListError as e:
            QMessageBox.warning(self, t("The install list has a problem"),
                                str(e) + "\n\n" + t("Nothing was changed - fix the "
                                                     "file and press Reload again."))
            return
        self.catalog, self.loaded = loaded.catalog, loaded
        self._rebuild()

    def _replace_list(self) -> None:
        backup = install_list.replace_with_builtin(self.loaded.path if self.loaded else None)
        if backup:
            QMessageBox.information(self, t("Install list replaced"), t(
                "Your edited list was kept as {name}, beside EasyAI Setup.",
                name=backup.name))
        self._reload_list()

    def _rebuild(self) -> None:
        """Rebuild the window, keeping everything the user has set."""
        keep_folder = self.folder.text()
        keep_hf, keep_cv = self.token.text(), self.civitai.text()
        keep_groups = self._selected()
        keep_update = self.update_switch.isChecked()
        keep_models = self.models_switch.isChecked()
        self._search_cache.clear()
        self._build()
        self.folder.setText(keep_folder)
        self.token.setText(keep_hf)
        self.civitai.setText(keep_cv)
        for card in self.cards:
            card.tick.setChecked(card.group_key in keep_groups)
        self.models_switch.setChecked(keep_models)
        self._recalculate()
        self.update_switch.setChecked(keep_update)

    def _change_language(self) -> None:
        """Rebuild the window in the newly chosen language.

        Rebuilding rather than walking the widgets and re-setting each text is
        both shorter and safer: there is no list of labels to forget to update
        when one is added later.
        """
        code = self.language.currentData()
        if not code or code == i18n.current():
            return

        i18n.load(code)
        i18n.remember(code)

        self.setWindowTitle(f"{t('EasyAI Setup')}  ·  {VERSION_LABEL}")
        self._rebuild()

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
            # Picking the portable folder itself means its parent.
            if Path(chosen).name.lower() == existing.COMFY_SUB.lower():
                chosen = str(Path(chosen).parent)
            self.folder.setText(chosen)

    def _recalculate(self, rescan: bool = True) -> None:
        groups = self._selected()
        target = Path(self.folder.text() or ".")
        if rescan or self.survey is None:
            portable = str(existing.portable_of(target))
            if portable not in self._search_cache:
                self._search_cache[portable] = existing.model_search_paths(Path(portable))
            self.survey = existing.survey(self.catalog, target, groups,
                                          search=self._search_cache[portable])
        survey = self.survey
        self._show_found(survey, str(target))

        download_models = self.models_switch.isChecked()
        needed = survey.download_bytes(download_models)
        free = free_space(target)

        labels = " + ".join(t(self.catalog.groups[g].label) for g in groups)
        if not groups:
            self.total.setText(t("Nothing chosen — tick at least one group above"))
        elif not download_models:
            self.total.setText(t("{labels} — models not downloaded, {size} to download",
                                 labels=labels, size=human_bytes(needed)))
        else:
            self.total.setText(t(
                "{labels} — {present} of {count} models already in ComfyUI, "
                "{missing} to download, {size} in all",
                labels=labels, present=len(survey.models_present),
                count=len(survey.models_present) + len(survey.models_missing)
                + len(survey.models_blocked),
                missing=len(survey.models_missing), size=human_bytes(needed)))
        self.space.setText(t("{size} free on that drive", size=human_bytes(free)))

        notes = []
        if free and needed and free < needed * 1.15:
            notes.append(t("⚠  Not enough room — this needs about {size}.",
                           size=human_bytes(needed * 1.15)))
        blocked = survey.models_blocked if download_models else []
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

    def _show_found(self, survey: existing.Survey, folder: str) -> None:
        """Say what is in the folder, and offer the update when it would help."""
        pinned = survey.pinned_version
        if not survey.comfy_found:
            self.found.setObjectName("Hint")
            self.found.setText(t("ComfyUI {version} will be installed here.", version=pinned))
            self.update_switch.setVisible(False)
        elif not survey.needs_update:
            self.found.setObjectName("Good")
            self.found.setText(t(
                "✓  ComfyUI {version} is already here, with EasyAI's add-ons at "
                "their tested versions.", version=survey.installed_version or pinned))
            self.update_switch.setVisible(False)
        else:
            installed = survey.installed_version or t("an unknown version")
            if survey.comfy_differs:
                self.found.setText(t(
                    "Found ComfyUI {installed} here. EasyAI is tested with {version}.",
                    installed=installed, version=pinned))
            else:
                self.found.setText(plural(
                    len(survey.packs_differ),
                    "Found ComfyUI {installed} here; {n} of EasyAI's add-ons is at "
                    "another version.",
                    "Found ComfyUI {installed} here; {n} of EasyAI's add-ons are at "
                    "other versions.", installed=installed))
            self.found.setObjectName("Warning")
            if survey.comfy_differs and survey.comfy_is_newer:
                self.update_switch.setText(t(
                    "Change ComfyUI from {installed} back to {version}, the version "
                    "EasyAI is tested with, along with its add-ons",
                    installed=installed, version=pinned))
            else:
                self.update_switch.setText(t(
                    "Update ComfyUI and EasyAI's add-ons to the tested versions "
                    "({version})", version=pinned))
            # A default only when the folder changes, never over the user's
            # own choice: on for an older ComfyUI, off for a newer one, whose
            # owner presumably chose it.
            if self._update_default_for != folder:
                self._update_default_for = folder
                self.update_switch.blockSignals(True)
                self.update_switch.setChecked(not survey.comfy_is_newer)
                self.update_switch.blockSignals(False)
            self.update_switch.setVisible(True)
        # The object name drives the colour, so the style has to be re-read.
        self.found.style().unpolish(self.found)
        self.found.style().polish(self.found)

    # -- running -----------------------------------------------------------
    def _start(self) -> None:
        groups = self._selected()
        target = Path(self.folder.text())
        self._recalculate()
        survey = self.survey
        # isHidden, not isVisible: Qt calls every widget invisible while its
        # window is off screen, and the choice must not depend on that.
        update = not self.update_switch.isHidden() and self.update_switch.isChecked()
        download_models = self.models_switch.isChecked()
        needed = survey.download_bytes(download_models)

        plan = []
        if not survey.comfy_found:
            plan.append(t("•  Install ComfyUI {version}", version=survey.pinned_version))
        elif update and survey.comfy_differs:
            plan.append(t("•  Move ComfyUI from {installed} to {version}",
                          installed=survey.installed_version or "?",
                          version=survey.pinned_version))
        elif survey.comfy_differs:
            plan.append(t("•  Leave ComfyUI {installed} as it is",
                          installed=survey.installed_version or "?"))
        if update and survey.packs_differ:
            plan.append(plural(len(survey.packs_differ),
                               "•  Bring {n} add-on to its tested version",
                               "•  Bring {n} add-ons to their tested versions"))
        if download_models:
            plan.append(plural(len(survey.models_missing),
                               "•  Download {n} model", "•  Download {n} models"))
        else:
            plan.append(t("•  Download no models"))

        if QMessageBox.question(
                self, t("Start?"),
                t("About to download {size} into:\n{folder}\n\n"
                  "This can take several hours. You can stop and re-run later — "
                  "it picks up where it left off.",
                  size=human_bytes(needed), folder=target)
                + "\n\n" + "\n".join(plan),
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        self._set_running(True)
        self.log.clear()
        self.worker = InstallWorker(self.catalog, target, groups,
                                    self.token.text().strip(),
                                    self.civitai.text().strip(),
                                    update_existing=update,
                                    download_models=download_models)
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
        self.update_switch.setEnabled(not running)
        self.models_switch.setEnabled(not running)

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
    catalog, loaded = open_install_list()
    window = SetupWindow(catalog, loaded)
    window.show()
    return app.exec()


def open_install_list(parent=None):
    """The install list to start with: setup-settings.json, or the built-in one.

    A file that cannot be used is reported, not ignored - but it must not stop
    Setup opening, so the built-in list is used until it is fixed.
    """
    try:
        loaded = install_list.load()
        return loaded.catalog, loaded
    except install_list.InstallListError as e:
        QMessageBox.warning(parent, t("The install list has a problem"),
                            str(e) + "\n\n" + t(
                                "EasyAI Setup will use its built-in list for now. "
                                "Fix the file and press Reload, or delete it to "
                                "start again."))
        return install_list.builtin_catalog(), None
