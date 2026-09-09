"""ComfyUI must not outlive EasyAI.

Leaving the engine running holds the whole graphics card, and a beginner has
no idea there is a second, windowless program to go and close. These check
both routes: the orderly one, and the one that survives EasyAI being killed.

Run with:  python -m pytest tests/test_engine_shutdown.py -q
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.comfy.launcher import ComfyLauncher, _kill_on_close_job
from app.config import DEFAULTS

windows_only = pytest.mark.skipif(sys.platform != "win32",
                                  reason="job objects are a Windows mechanism")


def _sleeper() -> subprocess.Popen:
    """A stand-in for ComfyUI: a child that will never exit on its own."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time\nwhile True: time.sleep(1)"],
        creationflags=0x08000000 if sys.platform == "win32" else 0,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                         capture_output=True, text=True, timeout=20).stdout
    return str(pid) in out


def test_closing_the_engine_is_on_by_default():
    """A viewer should not have to know this setting exists."""
    assert DEFAULTS["stop_engine_on_exit"] is True


def test_stop_ends_an_engine_we_started():
    launcher = ComfyLauncher(".", "run.bat", None)
    launcher.process = _sleeper()
    launcher.started_by_us = True
    pid = launcher.process.pid

    launcher.stop()
    launcher.process.wait(timeout=20)
    assert launcher.process.poll() is not None


def test_stop_leaves_alone_an_engine_we_did_not_start():
    """It may be someone's own session, with work queued in it."""
    launcher = ComfyLauncher(".", "run.bat", None)
    launcher.process = _sleeper()
    launcher.started_by_us = False          # we merely connected to it
    try:
        launcher.stop()
        time.sleep(1)
        assert launcher.process.poll() is None, "killed a server we did not start"
    finally:
        launcher.process.kill()


def test_changing_settings_keeps_the_power_to_close_it():
    """Settings builds a fresh launcher; without adopt() the running engine
    becomes unreachable and outlives EasyAI."""
    started = ComfyLauncher(".", "run.bat", None)
    started.process = _sleeper()
    started.started_by_us = True

    replacement = ComfyLauncher(".", "run.bat", None)
    assert replacement.stop() is None        # nothing to stop yet
    replacement.adopt(started)

    assert replacement.started_by_us
    assert replacement.process is started.process
    replacement.stop()
    started.process.wait(timeout=20)
    assert started.process.poll() is not None


def test_adopt_ignores_an_engine_the_other_did_not_start():
    other = ComfyLauncher(".", "run.bat", None)
    other.process = _sleeper()
    other.started_by_us = False
    try:
        fresh = ComfyLauncher(".", "run.bat", None)
        fresh.adopt(other)
        assert not fresh.started_by_us
        assert fresh.process is None
    finally:
        other.process.kill()


@windows_only
def test_the_job_object_is_available():
    job = _kill_on_close_job()
    assert job, "could not create the kill-on-close job"


@windows_only
def test_the_engine_is_put_into_the_job():
    """Silent failure here is invisible until someone finds ComfyUI still
    holding their graphics card. AssignProcessToJobObject needs
    PROCESS_TERMINATE as well as PROCESS_SET_QUOTA - asking for only the
    latter fails with access denied and looks like success."""
    launcher = ComfyLauncher(".", "run.bat", None)
    child = _sleeper()
    try:
        assert launcher._put_in_job(child) is True
    finally:
        child.kill()


@windows_only
def test_the_engine_dies_even_if_easyai_is_killed_outright():
    """The crash and Task-Manager case, where no cleanup code ever runs.

    A parent process is started that puts a child in the job, then the parent
    is killed without warning; the child must go with it.
    """
    here = Path(__file__).resolve().parent.parent
    script = (
        "import subprocess, sys, time\n"
        f"sys.path.insert(0, r'{here}')\n"
        "from app.comfy.launcher import ComfyLauncher\n"
        "child = subprocess.Popen([sys.executable, '-c',"
        " 'import time\\nwhile True: time.sleep(1)'], creationflags=0x08000000,"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "ComfyLauncher('.', 'run.bat', None)._put_in_job(child)\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", script],
                              stdout=subprocess.PIPE, text=True,
                              creationflags=0x08000000)
    try:
        child_pid = int(parent.stdout.readline().strip())
        assert _alive(child_pid)

        parent.kill()                       # no cleanup runs, as in a crash
        parent.wait(timeout=20)

        for _ in range(30):                 # the OS does this asynchronously
            if not _alive(child_pid):
                break
            time.sleep(0.5)
        assert not _alive(child_pid), "the engine outlived EasyAI being killed"
    finally:
        if parent.poll() is None:
            parent.kill()
