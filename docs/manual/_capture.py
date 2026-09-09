"""Captures numbered screenshots of every EasyAI screen listed in outline.md.

Launches the real EasyAI.py (and, separately, EasyAISetup.py). Window
discovery / existence / screenshot cropping uses pywinauto's win32 backend
(Qt dialogs are real top-level HWNDs, reliably enumerated by EnumWindows --
the UIA Desktop enumerator was found to silently miss owned Qt dialogs).
Clicking specific controls inside a window (buttons, tabs, menu items) uses
pywinauto's UIA backend, wrapped around that same HWND, because Qt exposes
its widgets to UI Automation even though they have no separate child HWNDs.
The actual pixel capture is done with pyautogui (Pillow-backed).

Every screenshot is a real capture of a window that was actually open on
screen -- nothing here is synthesized.

Run with:  python docs/manual/_capture.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pyautogui
from pywinauto import Application, Desktop

ROOT = Path(__file__).resolve().parents[2]
SHOTS = Path(__file__).resolve().parent / "screenshots"
LOG_PATH = Path(__file__).resolve().parent / "_capture.log"
SETTINGS_PATH = ROOT / "settings.json"
LANG_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "EasyAI" / "language.json"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

pyautogui.FAILSAFE = False

desktop = Desktop(backend="win32")

RESULTS: list[tuple[str, bool, str]] = []   # (name, ok, note/error)


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    log(f"{'OK ' if ok else 'FAIL'} - {name} {('- ' + note) if note else ''}")


# --------------------------------------------------------------------- setup
def backup_language() -> str | None:
    try:
        return LANG_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return None


def force_english_language() -> None:
    try:
        LANG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LANG_PATH.write_text(json.dumps({"language": "en"}), encoding="utf-8")
    except OSError as e:
        log(f"could not force language.json to en: {e}")


def restore_language(previous: str | None) -> None:
    try:
        if previous is None:
            if LANG_PATH.exists():
                LANG_PATH.unlink()
        else:
            LANG_PATH.write_text(previous, encoding="utf-8")
    except OSError as e:
        log(f"could not restore language.json: {e}")


def write_fresh_settings() -> None:
    """Point EasyAI at the repo's real ComfyUI dir + workflow folder, but with
    first_run_done cleared so the first-run Welcome/Settings flow shows up."""
    data = {
        "comfyui_dir": r"C:\AI ComfyUI - New Version\ComfyUI_windows_portable",
        "comfyui_launcher": "run_nvidia_gpu.bat",
        "comfyui_server": "127.0.0.1:8188",
        "auto_launch": True,
        "launch_timeout": 300,
        "job_timeout": 1800,
        "stop_engine_on_exit": True,
        "workflow_dir": str(ROOT / "workflows"),
        "output_dir": str(ROOT / "output"),
        "default_ratio": "2:3",
        "lock_seed": False,
        "locked_seeds": {},
        "batch_count": 1,
        "use_prompt_enhancer": True,
        "image_megapixels": 1.0,
        "video_megapixels": 0.5,
        "image_megapixels_warn": 1.5,
        "video_megapixels_warn": 1.0,
        "video_length_default": 5,
        "video_length_min": 2,
        "video_length_max": 15,
        "video_length_warn": 8,
        "language": "en",
        "theme": "dark",
        "window_geometry": "",
        "first_run_done": False,
    }
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ------------------------------------------------------------------- helpers
def wait_win(timeout=25, **kwargs):
    """Poll the win32 Desktop for a top-level window matching kwargs
    (title=, title_re=, class_name_re=...). Returns the win32 wrapper."""
    end = time.time() + timeout
    last_err = None
    while time.time() < end:
        try:
            w = desktop.window(**kwargs)
            if w.exists() and w.is_visible():
                return w
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.3)
    raise TimeoutError(f"window not found for {kwargs} ({last_err})")


def uia_of(win32_win):
    """Wrap a win32-discovered HWND with a UIA window, for control access."""
    hwnd = win32_win.handle
    app = Application(backend="uia").connect(handle=hwnd)
    return app.window(handle=hwnd)


def snap(win32_win, filename: str) -> Path:
    win32_win.set_focus()
    time.sleep(0.6)
    rect = win32_win.rectangle()
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    width, height = right - left, bottom - top
    SHOTS.mkdir(parents=True, exist_ok=True)
    out_path = SHOTS / filename
    img = pyautogui.screenshot(region=(left, top, width, height))
    img.save(str(out_path))
    return out_path


def click_in(uia_win, timeout=10, **kwargs) -> None:
    """Click a control, preferring the UIA Invoke pattern (a synthetic
    click_input can land on whatever is left behind if the control closes
    mid-click, e.g. clicking a dialog's OK button can click-through onto
    the window underneath)."""
    ctrl = uia_win.child_window(**kwargs)
    ctrl.wait("visible enabled", timeout=timeout)
    try:
        ctrl.invoke()
    except Exception:
        ctrl.click_input()


_watch_stop = threading.Event()


def _stray_dialog_watcher() -> None:
    """Runs for the life of the EasyAI process: the engine-start worker can
    report failure a couple of seconds *after* the startup splash is
    skipped, racing with whatever capture step runs next. Rather than trying
    to time that precisely, just keep sweeping it away in the background."""
    while not _watch_stop.is_set():
        try:
            dlg = desktop.window(title_re="The AI engine did not start.*")
            if dlg.exists() and dlg.is_visible():
                log(f"[watcher] dismissing stray dialog: {dlg.window_text()!r}")
                try:
                    uia_of(dlg).child_window(
                        title="OK", control_type="Button").invoke()
                except Exception as e:  # noqa: BLE001
                    log(f"[watcher] could not dismiss stray dialog: {e}")
        except Exception:
            pass
        _watch_stop.wait(0.5)


def start_watcher() -> None:
    _watch_stop.clear()
    t = threading.Thread(target=_stray_dialog_watcher, daemon=True)
    t.start()


def stop_watcher() -> None:
    _watch_stop.set()


# --------------------------------------------------------------- EasyAI flow
def run_easyai() -> None:
    log_file = open(Path(__file__).resolve().parent / "_easyai_stdout.log", "w",
                     encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "EasyAI.py"], cwd=str(ROOT),
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    log(f"Launched EasyAI.py pid={proc.pid}")
    start_watcher()

    try:
        _capture_first_run_and_settings()
        main_win = _capture_splash_and_main()
        if main_win is not None:
            _capture_tabs(main_win)
            _capture_manifest_editor(main_win)
            _capture_rename(main_win)
            _capture_help(main_win)
            _close_main_window(main_win)
    finally:
        stop_watcher()
        # Best-effort clean shutdown regardless of what failed above.
        try:
            if proc.poll() is None:
                proc.wait(timeout=15)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        log_file.close()


def _capture_first_run_and_settings() -> None:
    try:
        welcome = wait_win(title="Welcome to EasyAI", timeout=30)
        snap(welcome, "01_first-run-welcome.png")
        record("01 First-run Welcome", True)
        click_in(uia_of(welcome), title="OK", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("01 First-run Welcome", False, f"{type(e).__name__}: {e}")

    try:
        settings = wait_win(title="Settings", timeout=15)
        snap(settings, "02_settings.png")
        record("02 Settings", True)
        click_in(uia_of(settings), title="Save", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("02 Settings", False, f"{type(e).__name__}: {e}")


def _capture_splash_and_main():
    try:
        splash = wait_win(title="EasyAI", timeout=25)
        snap(splash, "03_startup-splash.png")
        record("03 Startup Splash", True)
        try:
            click_in(uia_of(splash), title="Carry on without it",
                     control_type="Button", timeout=5)
        except Exception:
            pass  # splash may already have finished (engine answered fast)
    except Exception as e:  # noqa: BLE001
        record("03 Startup Splash", False, f"{type(e).__name__}: {e}")

    try:
        main_win = wait_win(title="EasyAI — simple AI creation", timeout=30)
        time.sleep(1.5)  # let the background watcher catch any stray warning
        main_win.set_focus()
        snap(main_win, "04_main-window.png")
        record("04 Main Window", True)
        return main_win
    except Exception as e:  # noqa: BLE001
        record("04 Main Window", False, f"{type(e).__name__}: {e}")
        return None


def _select_tab(uwin, label_re: str) -> None:
    tab_item = uwin.child_window(title_re=label_re, control_type="TabItem")
    tab_item.wait("visible enabled", timeout=10)
    try:
        tab_item.select()
    except Exception:
        tab_item.click_input()
    time.sleep(0.6)


def _capture_tabs(win32_win) -> None:
    uwin = uia_of(win32_win)

    try:
        _select_tab(uwin, ".*Image.*")
        snap(win32_win, "05_image-tab-generate.png")
        record("05 Image tab", True)
    except Exception as e:  # noqa: BLE001
        record("05 Image tab", False, f"{type(e).__name__}: {e}")

    try:
        _select_tab(uwin, ".*Video.*")
        snap(win32_win, "06_video-tab-generate.png")
        record("06 Video tab", True)
    except Exception as e:  # noqa: BLE001
        record("06 Video tab", False, f"{type(e).__name__}: {e}")

    try:
        _select_tab(uwin, ".*Prompt Helper.*")
        snap(win32_win, "07_prompt-helper-tab-generate.png")
        record("07 Prompt Helper tab", True)
    except Exception as e:  # noqa: BLE001
        record("07 Prompt Helper tab", False, f"{type(e).__name__}: {e}")

    try:
        _select_tab(uwin, ".*Image.*")   # leave on Image tab for later steps
    except Exception:
        pass


def _capture_manifest_editor(win32_win) -> None:
    try:
        uwin = uia_of(win32_win)
        click_in(uwin, title_re="Set up.*", control_type="Button", timeout=10)
        editor = wait_win(title_re="Set up —.*", timeout=15)
        snap(editor, "08_manifest-editor.png")
        record("08 Manifest Editor (Set up…)", True)
        click_in(uia_of(editor), title="Cancel", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("08 Manifest Editor (Set up…)", False, f"{type(e).__name__}: {e}")


def _capture_rename(win32_win) -> None:
    try:
        win32_win.set_focus()
        time.sleep(0.3)
        win32_win.type_keys("{F2}")
        dlg = wait_win(title="Rename", timeout=10)
        snap(dlg, "09_rename-dialog.png")
        record("09 Rename dialog", True)
        click_in(uia_of(dlg), title="Cancel", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("09 Rename dialog", False, f"{type(e).__name__}: {e}")


def _open_help_item(win32_win, down_presses: int) -> None:
    """Open the Help menu and pick an item purely by keyboard.

    Qt's QMenu popups are real but titleless/ownerless-looking top-level
    windows that neither the win32 nor the UIA Desktop enumerators reliably
    surface as clickable targets, so mnemonics + arrow keys are the robust
    route: Alt+H opens "&Help", then Down N times + Enter picks item N.
    """
    win32_win.set_focus()
    time.sleep(0.3)
    win32_win.type_keys("%h", pause=0.05)
    time.sleep(0.4)
    for _ in range(down_presses):
        win32_win.type_keys("{DOWN}", pause=0.05)
        time.sleep(0.15)
    win32_win.type_keys("{ENTER}", pause=0.05)


def _capture_help(win32_win) -> None:
    try:
        _open_help_item(win32_win, 0)   # "How do I add a workflow?"
        dlg = wait_win(title="Adding a workflow", timeout=10)
        snap(dlg, "10_help-add-workflow.png")
        record("10 Help - How do I add a workflow?", True)
        click_in(uia_of(dlg), title="OK", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("10 Help - How do I add a workflow?", False, f"{type(e).__name__}: {e}")

    try:
        _open_help_item(win32_win, 1)   # "About EasyAI"
        dlg = wait_win(title="About EasyAI", timeout=10)
        snap(dlg, "11_help-about.png")
        record("11 Help - About EasyAI", True)
        click_in(uia_of(dlg), title="OK", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("11 Help - About EasyAI", False, f"{type(e).__name__}: {e}")


def _close_main_window(win32_win) -> None:
    try:
        win32_win.close()
    except Exception as e:  # noqa: BLE001
        log(f"main window close() raised: {e}")


# ---------------------------------------------------------------- Setup app
def run_easyai_setup() -> None:
    log_file = open(Path(__file__).resolve().parent / "_setup_stdout.log", "w",
                     encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "EasyAISetup.py"], cwd=str(ROOT),
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    log(f"Launched EasyAISetup.py pid={proc.pid}")
    try:
        win = wait_win(title="EasyAI Setup", timeout=25)
        snap(win, "12_easyai-setup.png")
        record("12 EasyAI Setup", True)
        try:
            win.close()
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        record("12 EasyAI Setup", False, f"{type(e).__name__}: {e}")
    finally:
        try:
            if proc.poll() is None:
                proc.wait(timeout=10)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        log_file.close()


# ----------------------------------------------------------------------- go
def main() -> None:
    LOG_PATH.write_text("", encoding="utf-8")
    SHOTS.mkdir(parents=True, exist_ok=True)
    write_fresh_settings()
    log("settings.json reset to fresh first-run state")
    prev_lang = backup_language()
    force_english_language()
    log("language.json forced to en for the capture session")

    try:
        run_easyai()
        time.sleep(1.0)
        run_easyai_setup()
    finally:
        restore_language(prev_lang)
        log("language.json restored")

    log("---- summary ----")
    for name, ok, note in RESULTS:
        log(f"{'OK ' if ok else 'FAIL'} {name} {note}")


if __name__ == "__main__":
    main()
