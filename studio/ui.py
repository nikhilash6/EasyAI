"""The EasyAI Studio window.

A left-hand list of jobs and a page for each, rather than EasyAI Setup's single
column: this is a workbench you come back to for one task at a time, not a
sequence to work down. Same palette and type as the other two programs, so it
reads as part of the same family.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QStackedWidget, QVBoxLayout, QWidget,
)

from app import VERSION_LABEL, i18n
from app.i18n import t
from app.ui import theme
from studio.pages import CataloguePage, LanguagePage, LinksPage


class StudioWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{t('EasyAI Studio')}  ·  {VERSION_LABEL}")
        self.resize(1040, 760)
        self.setMinimumSize(880, 620)
        icon = theme.app_icon()
        if icon:
            self.setWindowIcon(icon)
        self._build()

    def _build(self) -> None:
        shell = QWidget()
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        # --- the left rail -------------------------------------------------
        rail = QWidget()
        rail.setFixedWidth(232)
        rail.setStyleSheet(f"background:{theme.SURFACE};")
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(14, 18, 14, 14)
        rail_layout.setSpacing(10)

        title = QLabel("EasyAI Studio")
        title.setObjectName("Big")
        rail_layout.addWidget(title)

        subtitle = QLabel(t("Publishing tools"))
        subtitle.setObjectName("Hint")
        rail_layout.addWidget(subtitle)
        rail_layout.addSpacing(8)

        self.nav = QListWidget()
        self.nav.setSpacing(2)
        rail_layout.addWidget(self.nav, 1)

        # Language sits at the bottom of the rail, as in the Setup window.
        caption = QLabel(t("Language"))
        caption.setObjectName("Heading")
        rail_layout.addWidget(caption)
        self.language = QComboBox()
        for code, name in i18n.available().items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(i18n.current())))
        self.language.currentIndexChanged.connect(self._change_language)
        rail_layout.addWidget(self.language)

        row.addWidget(rail)

        # --- the pages -----------------------------------------------------
        self.pages = QStackedWidget()
        row.addWidget(self.pages, 1)

        from setup.authoring_ui import AuthoringView
        self._add_page("\U0001F5C2", t("Add a workflow"), AuthoringView())
        self._add_page("\U0001F4E6", t("Catalogue"), CataloguePage())
        self._add_page("\U0001F517", t("Download links"), LinksPage())
        self._add_page("\U0001F310", t("Languages"), LanguagePage())

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.setCentralWidget(shell)

    def _add_page(self, icon: str, label: str, page: QWidget) -> None:
        item = QListWidgetItem(f"{icon}   {label}")
        item.setSizeHint(QSize(0, 40))
        self.nav.addItem(item)
        self.pages.addWidget(page)

    def _change_language(self) -> None:
        """Rebuild in the new language, keeping the page you were on."""
        code = self.language.currentData()
        if not code or code == i18n.current():
            return
        row = self.nav.currentRow()
        i18n.load(code)
        i18n.remember(code)
        self.setWindowTitle(f"{t('EasyAI Studio')}  ·  {VERSION_LABEL}")
        self._build()
        self.nav.setCurrentRow(max(0, row))


def run() -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    i18n.start()
    theme.apply(app)
    window = StudioWindow()
    window.show()
    return app.exec()
