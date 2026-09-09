"""Running the long maintenance jobs without freezing the window.

Rebuilding the catalogue reads every add-on's source and hashes gigabytes;
checking the links makes forty network requests. Both take long enough that
doing them on the interface thread would look like a crash.
"""
from __future__ import annotations

import io
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Callable

from PySide6.QtCore import QThread, Signal


class Job(QThread):
    """Runs one function, streaming whatever it prints to the log.

    The maintenance tools are console programs that report by printing. Rather
    than rewrite them to emit signals - which would leave two ways of saying
    the same thing, and one of them untested - their output is captured and
    forwarded line by line.
    """

    line = Signal(str)
    done = Signal(bool, str)

    def __init__(self, work: Callable[[], object], parent=None):
        super().__init__(parent)
        self._work = work

    def run(self) -> None:
        stream = _Relay(self.line.emit)
        try:
            with redirect_stdout(stream), redirect_stderr(stream):
                self._work()
        except SystemExit as e:                 # the tools use SystemExit(code)
            stream.flush()
            code = e.code if isinstance(e.code, int) else 1
            self.done.emit(code == 0, "" if code == 0 else f"exit code {code}")
            return
        except Exception as e:                  # noqa: BLE001 - shown, not swallowed
            stream.flush()
            self.line.emit(traceback.format_exc())
            self.done.emit(False, f"{type(e).__name__}: {e}")
            return
        stream.flush()
        self.done.emit(True, "")


class _Relay(io.TextIOBase):
    """A file-like object that emits each completed line."""

    def __init__(self, emit: Callable[[str], None]):
        self._emit = emit
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""

    def isatty(self) -> bool:
        return False
