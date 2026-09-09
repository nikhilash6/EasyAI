"""EasyAI Setup - installs the ComfyUI that EasyAI expects.

Start it with:   python EasyAISetup.py

This is a separate program from EasyAI. It never reads or writes EasyAI's
settings, and it only puts files inside the folder you choose.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from EasyAI import _make_console_utf8_safe  # noqa: E402  (shared, read-only helper)


def _report_fatal(error: BaseException) -> None:
    """Show a startup failure - the launcher uses pythonw, so there is no console."""
    import traceback

    details = "".join(traceback.format_exception(error))
    print(details, file=sys.stderr)

    message = (
        "EasyAI Setup could not start.\n\n"
        f"{type(error).__name__}: {error}\n\n"
        "If this keeps happening, run EasyAISetup.bat from a Command Prompt to "
        "see the full message."
    )
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        box = QMessageBox(QMessageBox.Critical, "EasyAI Setup", message)
        box.setDetailedText(details)
        box.exec()
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "EasyAI Setup", 0x10)
        except Exception:
            pass


def main() -> int:
    _make_console_utf8_safe()

    from PySide6.QtWidgets import QApplication

    from app import i18n
    from app.i18n import t
    from app.ui import theme
    from setup.catalog import CATALOG_PATH, Catalog
    from setup.ui import SetupWindow

    app = QApplication(sys.argv)
    app.setApplicationName("EasyAI Setup")
    app.setOrganizationName("EasyAI")
    i18n.start()
    theme.apply(app)

    if not CATALOG_PATH.is_file():
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(
            None, t("EasyAI Setup"),
            t("The install list is missing:\n{path}\n\n"
              "It ships with EasyAI - re-download the folder, or on a machine "
              "that already works run:\n    python tools/make_catalog.py",
              path=CATALOG_PATH))
        return 1

    window = SetupWindow(Catalog())
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
