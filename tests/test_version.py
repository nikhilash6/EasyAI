"""Every program, and every .exe, says the same version.

Run with:  python -m pytest tests/test_version.py -q

The number lives in one place, app/__init__.py. Before 1.1.0 only EasyAI's
About box showed it - Setup and Studio showed nothing, and the .exe files
carried no version in Windows' Properties at all.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import VERSION_LABEL, __version__


def test_the_label_is_the_version():
    assert VERSION_LABEL == f"v{__version__}"
    assert all(part.isdigit() for part in __version__.split("."))


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_easyai_shows_it(qt_app, tmp_path):
    from app.config import Config
    from app.ui.main_window import MainWindow

    cfg = Config(tmp_path / "settings.json")
    cfg.set("auto_launch", False)
    assert VERSION_LABEL in MainWindow(cfg).windowTitle()


def test_setup_shows_it(qt_app):
    from setup.catalog import Catalog
    from setup.ui import SetupWindow

    assert VERSION_LABEL in SetupWindow(Catalog()).windowTitle()


def test_studio_shows_it(qt_app):
    from studio.ui import StudioWindow

    assert VERSION_LABEL in StudioWindow().windowTitle()


def test_every_exe_is_stamped_with_it():
    sys.path.insert(0, str(ROOT))
    from build_common import version_info

    info = version_info("EasyAI", "EasyAI - simple AI creation", "EasyAI.exe")
    numbers = tuple(int(p) for p in __version__.split(".")) + (0,)
    assert info.ffi.fileVersionMS >> 16 == numbers[0]
    assert info.ffi.fileVersionMS & 0xFFFF == numbers[1]
    assert info.ffi.fileVersionLS >> 16 == numbers[2]
    text = str(info)
    assert f"'FileVersion', '{__version__}'" in text
    assert f"'ProductVersion', '{__version__}'" in text


@pytest.mark.parametrize("spec", ["EasyAI.spec", "EasyAI Setup.spec", "EasyAI Studio.spec"])
def test_every_build_asks_for_it(spec):
    assert "version=version_info(" in (ROOT / spec).read_text(encoding="utf-8")
