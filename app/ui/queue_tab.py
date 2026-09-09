"""The Queue tab: what is lined up, what is running, and everything made.

Sits after the three mode tabs. It is not a Mode, so the window adds it
directly rather than through MODE_ORDER.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import plural, t
from app.modes import MODES
from app.queue import LABELS, State
from app.ui import theme
from app.ui.widgets import ResultGallery, Switch

#: A glance-able mark per state, so the list reads without being studied.
MARKS = {
    State.WAITING: "⏳",
    State.RUNNING: "▶",
    State.DONE: "✓",
    State.FAILED: "!",
    State.CANCELLED: "✕",
}

COLOURS = {
    State.WAITING: theme.MUTED,
    State.RUNNING: theme.ACCENT,
    State.DONE: theme.OK,
    State.FAILED: theme.BAD,
    State.CANCELLED: theme.DIM,
}


class QueueRow(QWidget):
    """One item: what it is, how far along, and a way to stop it."""

    cancel_requested = Signal(object)

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)

        self.mark = QLabel()
        self.mark.setFixedWidth(18)
        row.addWidget(self.mark)

        self.where = QLabel()
        self.where.setObjectName("Hint")
        self.where.setFixedWidth(96)
        row.addWidget(self.where)

        self.name = QLabel()
        self.name.setFixedWidth(150)
        row.addWidget(self.name)

        self.detail = QLabel()
        self.detail.setObjectName("Hint")
        row.addWidget(self.detail, 1)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setFixedWidth(120)
        self.bar.setTextVisible(False)
        row.addWidget(self.bar)

        self.stop = QPushButton("✕")
        self.stop.setFixedWidth(34)
        self.stop.setToolTip(t("Take this out of the queue"))
        self.stop.clicked.connect(lambda: self.cancel_requested.emit(self.item))
        row.addWidget(self.stop)

        self.refresh()

    def refresh(self) -> None:
        item = self.item
        self.mark.setText(MARKS.get(item.state, ""))
        self.mark.setStyleSheet(f"color:{COLOURS.get(item.state, theme.MUTED)};")
        self.where.setText(t(MODES[item.mode].label) if item.mode in MODES else item.mode)
        self.name.setText(item.workflow_name[:22])
        self.detail.setText(item.summary())

        running = item.state is State.RUNNING
        self.bar.setVisible(running)
        self.bar.setValue(item.percent)
        # A finished item has nothing to stop; the row stays for the record.
        self.stop.setVisible(not item.state.finished)
        self.setToolTip(f"{t(LABELS[item.state])}\n{item.prompt[:300]}")


class QueueTab(QWidget):
    """The whole tab: the queue on top, everything made underneath."""

    def __init__(self, manager, cfg, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.cfg = cfg
        self._rows: dict[int, QueueRow] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        self.heading = QLabel(t("QUEUE"))
        self.heading.setObjectName("Heading")
        head.addWidget(self.heading)
        self.count = QLabel("")
        self.count.setObjectName("Counter")
        head.addWidget(self.count)
        head.addStretch(1)
        self.clear_btn = QPushButton(t("Clear finished"))
        self.clear_btn.clicked.connect(self.manager.clear_finished)
        head.addWidget(self.clear_btn)
        layout.addLayout(head)

        self.list = QListWidget()
        self.list.setMinimumHeight(190)
        layout.addWidget(self.list, 1)

        self.empty = QLabel(t("Nothing queued. Press Create on any tab and it "
                              "will appear here."))
        self.empty.setObjectName("Hint")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)

        # Set up an evening's work, tick this, walk away. It pairs with the
        # existing "Close ComfyUI when EasyAI closes" setting, so the graphics
        # card is released too.
        #
        # It applies to this run only. Remembered, it would close every future
        # session the moment a job finished, which is not what anyone means by
        # ticking it once.
        self.close_when_done = Switch(t("Close EasyAI when the queue is empty"))
        self.close_when_done.setToolTip(t(
            "For this session only. EasyAI will say so and count down first, "
            "so you can stop it if you are still here."))
        self.close_when_done.setChecked(bool(cfg.get("close_when_queue_empty")))
        self.close_when_done.toggled.connect(
            lambda on: cfg.set("close_when_queue_empty", bool(on)))
        layout.addWidget(self.close_when_done)

        self.made_heading = QLabel(t("EVERYTHING MADE THIS SESSION"))
        self.made_heading.setObjectName("Heading")
        layout.addWidget(self.made_heading)

        self.gallery = ResultGallery()
        layout.addWidget(self.gallery, 1)

        manager.changed.connect(self.rebuild)
        manager.item_changed.connect(self._on_item_changed)
        manager.file_ready.connect(lambda item, path: self.gallery.add(path))
        self.rebuild()

    # -- the list ----------------------------------------------------------
    def rebuild(self) -> None:
        """Redraw the whole list. Short enough that partial updates would be
        more code than they are worth; per-item progress goes straight to the
        row it belongs to instead."""
        self.list.clear()
        self._rows.clear()
        for item in self.manager.items:
            row = QueueRow(item)
            row.cancel_requested.connect(self.manager.cancel)
            entry = QListWidgetItem()
            entry.setSizeHint(QSize(0, 46))
            entry.setFlags(Qt.NoItemFlags)
            self.list.addItem(entry)
            self.list.setItemWidget(entry, row)
            self._rows[item.id] = row

        waiting = len(self.manager.waiting())
        running = 1 if self.manager.running else 0
        bits = []
        if running:
            bits.append(t("1 running"))
        if waiting:
            bits.append(plural(waiting, "{n} waiting", "{n} waiting"))
        self.count.setText("   ·   ".join(bits))
        self.empty.setVisible(not self.manager.items)

    def _on_item_changed(self, item) -> None:
        row = self._rows.get(item.id)
        if row is not None:
            row.refresh()

    def retranslate(self) -> None:
        self.heading.setText(t("QUEUE"))
        self.clear_btn.setText(t("Clear finished"))
        self.empty.setText(t("Nothing queued. Press Create on any tab and it "
                             "will appear here."))
        self.close_when_done.setText(t("Close EasyAI when the queue is empty"))
        self.close_when_done.setToolTip(t(
            "For this session only. EasyAI will say so and count down first, "
            "so you can stop it if you are still here."))
        self.made_heading.setText(t("EVERYTHING MADE THIS SESSION"))
        self.gallery.retranslate()
        self.rebuild()
