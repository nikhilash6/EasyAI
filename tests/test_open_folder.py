"""Opening folders and revealing files in Windows Explorer.

Explorer is fussy about how it is called, and getting it wrong does not fail -
it silently opens Documents instead, which looks like the button doing nothing.

Run with:  python -m pytest tests/test_open_folder.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import app.ui.widgets as widgets


@pytest.fixture
def commands(monkeypatch):
    """Record what would be handed to the shell."""
    seen = []

    class FakePopen:
        def __init__(self, cmd, *a, **k):
            seen.append(cmd)

    monkeypatch.setattr(widgets.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(widgets.sys, "platform", "win32")
    return seen


def test_reveal_keeps_the_switch_and_path_together(commands, tmp_path):
    """explorer needs '/select,<path>' as ONE argument.

    Passing them separately makes it ignore both and open Documents.
    """
    target = tmp_path / "a.png"
    target.write_bytes(b"x")
    widgets.open_in_explorer(target)

    assert len(commands) == 1
    assert commands[0] == f'explorer /select,"{target}"'
    assert "/select," in commands[0]
    assert not isinstance(commands[0], list), "a split argv loses the selection"


def test_open_folder_does_not_use_select(commands, tmp_path):
    """The results button wants the folder itself, not something inside it."""
    widgets.open_folder(tmp_path)
    assert commands == [f'explorer "{tmp_path}"']
    assert "/select" not in commands[0]


def test_a_deleted_file_falls_back_to_its_folder(commands, tmp_path):
    widgets.open_in_explorer(tmp_path / "gone.png")
    assert commands == [f'explorer "{tmp_path}"']


def test_opening_a_folder_creates_it_first(commands, tmp_path):
    """Nothing generated yet means the output folder may not exist."""
    fresh = tmp_path / "results" / "image"
    widgets.open_folder(fresh)
    assert fresh.is_dir()
    assert commands == [f'explorer "{fresh}"']


def test_the_results_button_works_before_anything_is_made(tmp_path):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    gallery = widgets.ResultGallery()
    assert not gallery.open_folder_btn.isEnabled()

    gallery.set_folder(tmp_path / "image")
    assert gallery.open_folder_btn.isEnabled()
    assert gallery.open_folder_btn.toolTip() == str(tmp_path / "image")


def test_a_generated_file_moves_the_button_to_its_folder(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    seen = []
    monkeypatch.setattr(widgets, "open_folder", lambda p: seen.append(Path(p)))

    gallery = widgets.ResultGallery()
    gallery.set_folder(tmp_path / "configured")
    made = tmp_path / "actual" / "out.png"
    made.parent.mkdir(parents=True)
    made.write_bytes(b"x")
    gallery.add(made)

    gallery._open_folder()
    assert seen == [made.parent], "it should open where the file actually went"
