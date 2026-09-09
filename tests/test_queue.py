"""Checks for the shared queue.

Run with:  python -m pytest tests/test_queue.py -q

No engine and no window: JobWorker is replaced with a stand-in, because what
matters here is the ordering and the cancelling, not the generating - that is
already covered elsewhere.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.jobs import JobResult
from app.queue import QueueItem, QueueManager, State


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class FakeWorker:
    """Stands in for JobWorker: does nothing until told how it ended."""

    made: list["FakeWorker"] = []

    def __init__(self, **kwargs):
        from PySide6.QtCore import QObject, Signal

        class Signals(QObject):
            progress = Signal(int, str)
            preview = Signal(bytes)
            file_ready = Signal(str)
            finished_ok = Signal(object)
            failed = Signal(str)

        self.kwargs = kwargs
        self.signals = Signals()
        self.progress = self.signals.progress
        self.preview = self.signals.preview
        self.file_ready = self.signals.file_ready
        self.finished_ok = self.signals.finished_ok
        self.failed = self.signals.failed
        self.started = False
        self.cancelled = False
        FakeWorker.made.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True
        self.failed.emit("Cancelled.")

    def isRunning(self):
        return self.started

    def wait(self, _ms=0):
        return True

    def succeed(self, **result):
        self.finished_ok.emit(JobResult(**result))


@pytest.fixture(autouse=True)
def fake_worker(monkeypatch):
    FakeWorker.made.clear()
    monkeypatch.setattr("app.queue.JobWorker", FakeWorker)
    # The cancelled-vs-failed check compares against this same constant.
    monkeypatch.setattr("app.queue.JobWorker.CANCELLED", "Cancelled.",
                        raising=False)
    yield


def make_item(mode="image", name="wf", prompt="x", tmp_path=None) -> QueueItem:
    workflow = type("W", (), {"name": name})()
    request = type("R", (), {"prompt": prompt})()
    return QueueItem(mode=mode, workflow=workflow, request=request,
                     output_dir=tmp_path or Path("."))


def test_the_first_item_starts_at_once(qt_app, tmp_path):
    q = QueueManager(client=None)
    item = q.add(make_item(tmp_path=tmp_path))
    assert item.state is State.RUNNING
    assert q.running is item
    assert FakeWorker.made[0].started


def test_they_run_one_at_a_time_in_order(qt_app, tmp_path):
    """There is one graphics card - two at once would only fight over it."""
    q = QueueManager(client=None)
    first = q.add(make_item(name="one", tmp_path=tmp_path))
    second = q.add(make_item(name="two", tmp_path=tmp_path))
    third = q.add(make_item(name="three", tmp_path=tmp_path))

    assert first.state is State.RUNNING
    assert second.state is third.state is State.WAITING
    assert len(FakeWorker.made) == 1, "started more than one at a time"

    FakeWorker.made[0].succeed(width=100, height=200, elapsed=3.0)
    assert first.state is State.DONE
    assert second.state is State.RUNNING
    assert third.state is State.WAITING


def test_cancelling_one_that_is_waiting_leaves_the_rest(qt_app, tmp_path):
    q = QueueManager(client=None)
    running = q.add(make_item(name="running", tmp_path=tmp_path))
    doomed = q.add(make_item(name="doomed", tmp_path=tmp_path))
    later = q.add(make_item(name="later", tmp_path=tmp_path))

    q.cancel(doomed)
    assert doomed.state is State.CANCELLED
    assert running.state is State.RUNNING, "cancelling a waiting item stopped the run"

    FakeWorker.made[0].succeed()
    assert later.state is State.RUNNING, "the queue skipped past the next item"


def test_cancelling_the_running_one_lets_the_next_start(qt_app, tmp_path):
    q = QueueManager(client=None)
    running = q.add(make_item(name="running", tmp_path=tmp_path))
    following = q.add(make_item(name="following", tmp_path=tmp_path))

    q.cancel(running)
    assert FakeWorker.made[0].cancelled
    assert running.state is State.CANCELLED
    assert following.state is State.RUNNING


def test_a_failure_does_not_stop_the_queue(qt_app, tmp_path):
    """One workflow with a missing model must not strand everything behind it."""
    q = QueueManager(client=None)
    bad = q.add(make_item(name="bad", tmp_path=tmp_path))
    good = q.add(make_item(name="good", tmp_path=tmp_path))

    FakeWorker.made[0].failed.emit("The AI engine finished but produced no files.")
    assert bad.state is State.FAILED
    assert bad.error
    assert good.state is State.RUNNING


def test_a_cancelled_run_is_not_recorded_as_a_failure(qt_app, tmp_path):
    q = QueueManager(client=None)
    item = q.add(make_item(tmp_path=tmp_path))
    FakeWorker.made[0].failed.emit("Cancelled.")
    assert item.state is State.CANCELLED
    assert not item.error


def test_emptied_fires_only_when_nothing_is_left(qt_app, tmp_path):
    q = QueueManager(client=None)
    fired = []
    q.emptied.connect(lambda: fired.append(True))

    q.add(make_item(tmp_path=tmp_path))
    q.add(make_item(tmp_path=tmp_path))
    FakeWorker.made[0].succeed()
    assert not fired, "fired while one was still to run"
    FakeWorker.made[1].succeed()
    assert len(fired) == 1


def test_busy_reports_what_is_outstanding(qt_app, tmp_path):
    q = QueueManager(client=None)
    assert not q.is_busy()
    q.add(make_item(tmp_path=tmp_path))
    q.add(make_item(tmp_path=tmp_path))
    assert q.is_busy()
    assert len(q.unfinished()) == 2

    FakeWorker.made[0].succeed()
    FakeWorker.made[1].succeed()
    assert not q.is_busy()
    assert len(q.unfinished()) == 0


def test_clearing_finished_keeps_what_is_still_to_do(qt_app, tmp_path):
    q = QueueManager(client=None)
    done = q.add(make_item(name="done", tmp_path=tmp_path))
    waiting = q.add(make_item(name="waiting", tmp_path=tmp_path))
    FakeWorker.made[0].succeed()

    q.clear_finished()
    assert done not in q.items
    assert waiting in q.items


def test_each_item_keeps_the_settings_it_was_added_with(qt_app, tmp_path):
    """Setting up the next item must not disturb one already waiting."""
    q = QueueManager(client=None)
    first = q.add(make_item(prompt="a fox in the snow", tmp_path=tmp_path))
    second = q.add(make_item(prompt="a fox running", tmp_path=tmp_path))

    second.request.prompt = "changed my mind"
    assert first.prompt == "a fox in the snow"


def test_the_summary_says_something_useful_at_each_stage(qt_app, tmp_path):
    q = QueueManager(client=None)
    item = q.add(make_item(prompt="a fox in the snow", tmp_path=tmp_path))
    item.message = "Rendering…"
    assert item.summary() == "Rendering…"

    FakeWorker.made[0].succeed(width=832, height=1248, elapsed=41.0)
    assert "832" in item.summary() and "1248" in item.summary()


# --- closing while work is outstanding --------------------------------------
def _window(qt_app, monkeypatch, tmp_path):
    from app.config import Config
    from app.ui.main_window import MainWindow

    cfg = Config(tmp_path / "settings.json")
    return MainWindow(cfg), cfg


def test_closing_is_refused_while_anything_is_unfinished(qt_app, monkeypatch,
                                                         tmp_path):
    """A viewer who has left a queue running must not lose it to a stray
    click on the X."""
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMessageBox

    window, _ = _window(qt_app, monkeypatch, tmp_path)
    window.queue.add(make_item(tmp_path=tmp_path))

    # Answer the warning with "Keep working", which is the default.
    def keep_working(box):
        for button in box.buttons():
            if box.buttonRole(button) == QMessageBox.RejectRole:
                box.setProperty("_clicked", button)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", keep_working)
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda self: self.property("_clicked"))

    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted(), "closed with work still queued"
    assert window.tabs.currentWidget() is window.queue_tab, \
        "should show what is holding it up"


def test_closing_is_allowed_once_the_queue_is_empty(qt_app, monkeypatch,
                                                    tmp_path):
    from PySide6.QtGui import QCloseEvent

    window, _ = _window(qt_app, monkeypatch, tmp_path)
    window.queue.add(make_item(tmp_path=tmp_path))
    FakeWorker.made[-1].succeed()

    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()


def test_close_when_empty_only_fires_on_the_last_item(qt_app, monkeypatch,
                                                      tmp_path):
    window, cfg = _window(qt_app, monkeypatch, tmp_path)
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))

    cfg.set("close_when_queue_empty", True)
    window.queue.add(make_item(name="first", tmp_path=tmp_path))
    window.queue.add(make_item(name="second", tmp_path=tmp_path))

    FakeWorker.made[-1].succeed()
    assert not closed, "closed while one was still to run"

    FakeWorker.made[-1].succeed()
    # The close is deferred a moment so the last result can be seen.
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(2500, loop.quit)
    loop.exec()
    assert closed, "the queue emptied but EasyAI stayed open"


def test_close_when_empty_does_nothing_when_switched_off(qt_app, monkeypatch,
                                                         tmp_path):
    window, cfg = _window(qt_app, monkeypatch, tmp_path)
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))

    cfg.set("close_when_queue_empty", False)
    window.queue.add(make_item(tmp_path=tmp_path))
    FakeWorker.made[-1].succeed()

    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(2000, loop.quit)
    loop.exec()
    assert not closed
