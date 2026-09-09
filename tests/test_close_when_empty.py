"""Closing EasyAI once the queue is empty.

Run with:  python -m pytest tests/test_close_when_empty.py -q

Written after the real thing it prevents. The switch was ticked once, saved
into settings.json, and from then on every session closed itself the moment a
job finished. A prompt-enhancer run takes a few seconds, so the window simply
vanished a moment after the text appeared, with nothing said and no obvious
cause - it looked exactly like a crash.

Two rules keep that from happening again: the choice does not survive a
restart, and EasyAI says so before acting on it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.config import DEFAULTS, SESSION_ONLY, Config


# --- the choice is not remembered -------------------------------------------
def test_a_saved_true_does_not_survive_a_restart(tmp_path):
    """The exact settings.json that caused the report."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "close_when_queue_empty": True,
        "output_dir": "somewhere",
    }), encoding="utf-8")

    cfg = Config(path)
    assert cfg.get("close_when_queue_empty") is False
    # Everything else in the same file is still honoured.
    assert cfg.get("output_dir") == "somewhere"


def test_it_is_a_declared_setting(tmp_path):
    """It used to be absent from DEFAULTS, so `get` answered None - readable
    by accident rather than by design."""
    assert "close_when_queue_empty" in DEFAULTS
    assert DEFAULTS["close_when_queue_empty"] is False
    assert "close_when_queue_empty" in SESSION_ONLY


def test_every_session_only_key_has_a_default():
    """Resetting to a default that does not exist would raise on start-up."""
    for key in SESSION_ONLY:
        assert key in DEFAULTS, f"{key} is reset but never declared"


def test_it_still_works_within_the_one_session(tmp_path):
    """Forgetting it between runs must not stop it working during a run."""
    cfg = Config(tmp_path / "settings.json")
    cfg.set("close_when_queue_empty", True)
    assert cfg.get("close_when_queue_empty") is True


def test_saving_and_reloading_still_forgets_it(tmp_path):
    path = tmp_path / "settings.json"
    cfg = Config(path)
    cfg.set("close_when_queue_empty", True)
    cfg.save()

    assert Config(path).get("close_when_queue_empty") is False


def test_an_unrelated_setting_is_still_remembered(tmp_path):
    """The reset must be surgical - everything else still persists."""
    path = tmp_path / "settings.json"
    cfg = Config(path)
    cfg.set("close_when_queue_empty", True)
    cfg.set("image_megapixels", 1.75)
    cfg.save()

    again = Config(path)
    assert again.get("image_megapixels") == 1.75
    assert again.get("close_when_queue_empty") is False


# --- it announces itself ----------------------------------------------------
@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def window(tmp_path, qt_app):
    from app.ui.main_window import MainWindow

    cfg = Config(tmp_path / "settings.json")
    cfg.set("workflow_dir", str(tmp_path / "workflows"))
    cfg.set("output_dir", str(tmp_path / "output"))
    cfg.set("auto_launch", False)
    return MainWindow(cfg)


def test_nothing_happens_when_the_switch_is_off(tmp_path, qt_app):
    win = window(tmp_path, qt_app)
    asked = []
    win._start_closing_countdown = lambda: asked.append("asked")

    win._on_queue_empty()
    assert not asked, "an empty queue must be silent unless asked otherwise"


def test_it_asks_before_closing(tmp_path, qt_app):
    """The heart of the report: it used to close outright after 1.5 seconds."""
    win = window(tmp_path, qt_app)
    win.cfg.set("close_when_queue_empty", True)
    asked = []
    win._start_closing_countdown = lambda: asked.append("asked")

    win._on_queue_empty()
    assert asked == ["asked"]


def test_it_does_not_ask_twice(tmp_path, qt_app):
    win = window(tmp_path, qt_app)
    win.cfg.set("close_when_queue_empty", True)
    win._closing_for_good = True
    asked = []
    win._start_closing_countdown = lambda: asked.append("asked")

    win._on_queue_empty()
    assert not asked, "already on the way out"


def test_it_still_closes_when_nobody_is_there(tmp_path, qt_app, monkeypatch):
    """The point of the feature: line up an evening's work, walk away, and
    come back to a closed program and a free graphics card."""
    from app.ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "CLOSE_COUNTDOWN", 0)
    win = window(tmp_path, qt_app)
    win.cfg.set("close_when_queue_empty", True)
    closed = []
    win.close = lambda *a: closed.append("closed")

    win._on_queue_empty()
    assert closed == ["closed"], "an unattended countdown must run out and close"


def test_there_is_time_to_react():
    """A countdown too short to read and reach the button is no better than
    closing outright."""
    from app.ui.main_window import MainWindow

    assert MainWindow.CLOSE_COUNTDOWN >= 10
