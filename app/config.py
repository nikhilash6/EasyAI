"""Persistent user settings.

Follows the pattern from PhotoBooth-WEBUI: a defaults dict merged with whatever
is on disk, so adding a new setting in code never breaks an existing
settings.json. Writes go through a temp file + os.replace so a crash mid-save
can't leave a truncated config behind.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.paths import data_dir

# Where the user's own things go: the project folder when running from source,
# the folder holding the .exe in a built copy. Never the unpacked bundle - that
# is a temporary folder, and settings written there would vanish on exit.
ROOT = data_dir()

DEFAULTS = {
    # --- ComfyUI backend ---
    "comfyui_dir": r"C:\AI ComfyUI - New Version\ComfyUI_windows_portable",
    "comfyui_launcher": "run_nvidia_gpu.bat",
    "comfyui_server": "127.0.0.1:8188",
    "auto_launch": True,
    "launch_timeout": 300,      # seconds to wait for the engine to come up
    "job_timeout": 1800,        # seconds a single generation may take
    #: On by default: if EasyAI started ComfyUI, EasyAI should take it away
    #: again. Leaving it running holds on to all of the graphics memory, and a
    #: beginner has no idea there is a second program to close.
    "stop_engine_on_exit": True,
    #: "Close EasyAI when the queue is empty", the switch on the Queue tab.
    #: Deliberately forgotten at the end of every run - see SESSION_ONLY.
    "close_when_queue_empty": False,

    # --- Folders ---
    "workflow_dir": str(ROOT / "workflows"),
    "output_dir": str(ROOT / "output"),

    # --- Generation defaults ---
    "default_ratio": "2:3",
    "lock_seed": False,
    #: Last seed used, per tab, so locking in one mode cannot inherit
    #: the number another mode happened to leave behind.
    "locked_seeds": {},
    #: A different model of the same family, per workflow name. Only ever a
    #: value the engine offered; the workflow file itself is never rewritten,
    #: so it can still be shared or re-added unchanged.
    "model_choice": {},
    "batch_count": 1,
    "use_prompt_enhancer": True,
    #: Output pixel budget in megapixels (0.5 - 2.0), used when a workflow
    #: does not carry one of its own. A workflow's own value always wins.
    "image_megapixels": 1.0,
    "video_megapixels": 0.5,
    #: Above these, warn about memory. Video is lower because the cost is
    #: paid per frame: 2 megapixels x 15 seconds is a very different job.
    "image_megapixels_warn": 1.5,
    "video_megapixels_warn": 1.0,

    # --- video length, in seconds ---
    "video_length_default": 5,
    "video_length_min": 2,
    "video_length_max": 15,
    #: Above this, warn that the card may run out of memory. Roughly where a
    #: 16 GB card starts to struggle with a 22B video model at 1 megapixel.
    "video_length_warn": 8,

    # --- UI ---
    "language": "en",
    "theme": "dark",
    "window_geometry": "",
    "first_run_done": False,
}

SETTINGS_PATH = ROOT / "settings.json"

#: Settings that are always back to their default at the next start, however
#: they were left.
#:
#: "Close EasyAI when the queue is empty" means "I am walking away from the
#: batch I have just lined up". Remembered, it becomes something else
#: entirely: every future run of EasyAI closes itself the moment one job
#: finishes, with no message and no clue why - which is exactly what it did
#: after a prompt-enhancer job that took a few seconds.
SESSION_ONLY = ("close_when_queue_empty",)


class Config:
    """Dict-backed settings with defaults, disk persistence and attribute-free access."""

    def __init__(self, path: Path | str = SETTINGS_PATH):
        self.path = Path(path)
        self.data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                # Merge rather than replace: unknown keys are kept, missing keys
                # fall back to the shipped default.
                self.data.update(stored)
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError) as e:
            print(f"[config] could not read {self.path}: {e} - using defaults")

        # Whatever an older settings.json says, a walk-away choice starts off.
        for key in SESSION_ONLY:
            self.data[key] = DEFAULTS[key]

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"[config] could not write {self.path}: {e}")

    # -- convenience -------------------------------------------------------
    def get(self, key: str, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def __getitem__(self, key: str):
        return self.get(key)

    def __setitem__(self, key: str, value) -> None:
        self.set(key, value)

    # -- derived paths -----------------------------------------------------
    @property
    def server(self) -> str:
        return str(self.get("comfyui_server", "127.0.0.1:8188")).strip().rstrip("/")

    @property
    def base_url(self) -> str:
        return f"http://{self.server}"

    def workflow_dir(self, mode: str | None = None) -> Path:
        d = Path(self.get("workflow_dir"))
        return d / mode if mode else d

    def output_dir(self, mode: str | None = None) -> Path:
        d = Path(self.get("output_dir"))
        return d / mode if mode else d

    def comfyui_launcher_path(self) -> Path:
        return Path(self.get("comfyui_dir")) / self.get("comfyui_launcher")


def ensure_folders(cfg: Config) -> None:
    """Create the workflow and output trees so the user has somewhere to drop files."""
    from app.modes import MODES

    for mode in MODES:
        cfg.workflow_dir(mode).mkdir(parents=True, exist_ok=True)
        cfg.output_dir(mode).mkdir(parents=True, exist_ok=True)
