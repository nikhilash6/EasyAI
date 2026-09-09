"""EasyAI Studio - the publishing tools.

Start it with:   python EasyAIStudio.py

The third program, and the only one your viewers never see. It packages new
workflows into EasyAI, keeps the catalogue and its download links honest, and
keeps the translations level with the code.

It expects the project folder, the model files and a running ComfyUI, which is
true on the machine that builds EasyAI and nowhere else.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from EasyAI import _make_console_utf8_safe  # noqa: E402  (shared, read-only)


def _report_fatal(error: BaseException) -> None:
    """Show a startup failure - the launcher uses pythonw, so there is no console."""
    import traceback

    details = "".join(traceback.format_exception(error))
    print(details, file=sys.stderr)

    message = (
        "EasyAI Studio could not start.\n\n"
        f"{type(error).__name__}: {error}\n\n"
        "If this keeps happening, run EasyAI Studio.bat from a Command Prompt "
        "to see the full message."
    )
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        box = QMessageBox(QMessageBox.Critical, "EasyAI Studio", message)
        box.setDetailedText(details)
        box.exec()
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "EasyAI Studio", 0x10)
        except Exception:
            pass


def main() -> int:
    _make_console_utf8_safe()

    from PySide6.QtWidgets import QApplication

    from app import i18n
    from app.ui import theme
    from studio.ui import StudioWindow

    app = QApplication(sys.argv)
    app.setApplicationName("EasyAI Studio")
    app.setOrganizationName("EasyAI")
    i18n.start()
    theme.apply(app)

    window = StudioWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as fatal:      # noqa: BLE001 - last line of defence
        _report_fatal(fatal)
        raise SystemExit(1)
