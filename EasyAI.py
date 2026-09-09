"""EasyAI - a simple desktop front end for ComfyUI.

Start it with:   python EasyAI.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the app importable no matter where it is launched from (including from a
# PyInstaller bundle, where __file__ points inside the archive).
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _make_console_utf8_safe() -> None:
    """Stop diagnostic prints crashing on the Windows console.

    The default Windows code page is cp1252, which cannot encode the status
    symbols used in log lines; without this a print() can raise
    UnicodeEncodeError and take a worker thread down with it.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _report_fatal(error: BaseException) -> None:
    """Put a startup failure somewhere the user can actually see it.

    EasyAI.bat launches with pythonw, which has no console, so an exception
    here would otherwise be completely silent - the app simply would not
    appear, with nothing to explain why.
    """
    import traceback

    details = "".join(traceback.format_exception(error))
    print(details, file=sys.stderr)

    message = (
        "EasyAI could not start.\n\n"
        f"{type(error).__name__}: {error}\n\n"
        "If this keeps happening, run EasyAI.bat from a Command Prompt to see "
        "the full message."
    )
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        box = QMessageBox(QMessageBox.Critical, "EasyAI", message)
        box.setDetailedText(details)
        box.exec()
    except Exception:
        # Qt itself may be what failed; fall back to the Windows dialog.
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "EasyAI", 0x10)
        except Exception:
            pass


def main() -> int:
    _make_console_utf8_safe()

    from PySide6.QtWidgets import QApplication, QMessageBox

    from app import i18n
    from app.config import Config, ensure_folders
    from app.ui import theme
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("EasyAI")
    app.setOrganizationName("EasyAI")
    # Before any window is built, so the first thing shown is already in the
    # right language: the saved choice, or whatever Windows is set to.
    i18n.start()
    theme.apply(app)

    cfg = Config()
    ensure_folders(cfg)

    window = MainWindow(cfg)
    window.show()

    # First run: send the user straight to Settings so they can point EasyAI at
    # their ComfyUI before anything tries to use it.
    if not cfg.get("first_run_done"):
        from app.comfy.launcher import ComfyLauncher, default_comfyui_dir

        guess = default_comfyui_dir()
        cfg.set("comfyui_dir", guess)
        probe = ComfyLauncher(guess, cfg.get("comfyui_launcher"), window.client)
        options = probe.find_launchers()
        if options and cfg.get("comfyui_launcher") not in options:
            cfg.set("comfyui_launcher", options[0])

        QMessageBox.information(
            window, "Welcome to EasyAI",
            "Before you start, check that EasyAI knows where ComfyUI is.\n\n"
            f"It has guessed:\n{guess}\n\n"
            "The Settings window will open now — press Test now to confirm, "
            "then Save.")
        window._open_settings()
        cfg.set("first_run_done", True)
        cfg.save()

    window.start()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as fatal:      # noqa: BLE001 - last line of defence
        _report_fatal(fatal)
        raise SystemExit(1)
