"""Where things live, whether running from source or from a built .exe.

Two different questions get two different answers, and conflating them is the
classic way to lose a user's work:

* **Files shipped with the program** - the language catalogues, the install
  catalogue, the licences, the icon. Read-only. Inside a one-file build these
  are unpacked into a temporary folder that Windows deletes on exit.

* **Files the user creates and keeps** - settings.json, their workflows, every
  picture they make. These must sit next to the .exe, because the temporary
  folder is gone the moment the program closes.

Running from source both are the project folder, which is why the distinction
never came up until the first build.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: The checked-out project, used whenever this is not a frozen build.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def frozen() -> bool:
    """True when running from a PyInstaller build rather than from source."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Read-only files that shipped inside the program."""
    if frozen():
        # _MEIPASS is the unpacked one-file bundle; for a one-folder build it
        # is absent and everything sits beside the executable instead.
        return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent)
    return PROJECT_ROOT


def data_dir() -> Path:
    """Files the user creates and expects to still be there tomorrow."""
    if frozen():
        return Path(sys.executable).parent
    return PROJECT_ROOT
