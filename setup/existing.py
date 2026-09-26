"""What is already in a ComfyUI folder, so Setup updates instead of repeating.

Run against a folder with nothing in it, everything is "missing" and Setup
installs from scratch. Run against a viewer's existing ComfyUI, it answers the
three questions that decide what an install should do:

* **Which ComfyUI is this?** Read from ``comfyui_version.py`` and the git
  checkout, without running anything.
* **Are EasyAI's add-ons at the versions it was tested with?** A git pack's
  commit, or a registry pack's version from its ``pyproject.toml``.
* **Which models can ComfyUI already see?** ComfyUI searches more than one
  folder per kind - text encoders in both ``models/text_encoders`` and
  ``models/clip``, plus anything ``extra_model_paths.yaml`` adds - so looking
  only where Setup would have put a file calls a perfectly good 8.7 GB model
  missing and downloads it again. ComfyUI's own ``folder_paths`` is asked
  instead, through the portable's Python, which takes a tenth of a second and
  needs no running server.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from setup.catalog import Catalog, Model, NodePack

#: Where the extracted portable puts things.
COMFY_SUB = "ComfyUI_windows_portable"

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

#: Older folder names the catalogue still uses, and the kind ComfyUI files
#: them under now. "unet" and "clip" are searched as part of these.
LEGACY_KINDS = {"unet": "diffusion_models", "clip": "text_encoders"}

#: Run with the portable's own Python. Prints every model kind and the folders
#: ComfyUI searches for it, in the order it searches them.
_PROBE = r"""
import json, os, sys
comfy = sys.argv[1]
sys.path.insert(0, comfy)
os.chdir(comfy)
import folder_paths
extra = os.path.join(comfy, "extra_model_paths.yaml")
if os.path.isfile(extra):
    try:
        from utils.extra_config import load_extra_path_config
        load_extra_path_config(extra)
    except Exception:
        pass
print("EASYAI-PATHS" + json.dumps({k: folder_paths.get_folder_paths(k)
                                   for k in folder_paths.folder_names_and_paths}))
"""


# --- ComfyUI itself -----------------------------------------------------------
def portable_of(target: Path) -> Path:
    return Path(target) / COMFY_SUB


def python_of(portable: Path) -> Path:
    return Path(portable) / "python_embeded" / "python.exe"


def comfyui_version(comfy: Path) -> str | None:
    """The version ComfyUI reports about itself, e.g. "0.37.0"."""
    try:
        text = (Path(comfy) / "comfyui_version.py").read_text(encoding="utf-8")
    except OSError:
        return None
    found = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", text)
    return found.group(1) if found else None


def version_key(version: str | None) -> tuple:
    """Comparable form of "0.37.0"; unknown sorts lowest."""
    if not version:
        return ()
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", version))


def git_head(repo: Path) -> str | None:
    """The commit a checkout is on, read from the files - git not needed.

    Handles a detached head (what a pinned install is), a branch whose ref is
    a loose file, a branch only in packed-refs, and ``.git`` as a pointer file.
    """
    git = Path(repo) / ".git"
    try:
        if git.is_file():                       # "gitdir: <path>"
            pointed = git.read_text(encoding="utf-8").split(":", 1)[1].strip()
            git = (Path(repo) / pointed).resolve()
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, IndexError):
        return None
    if not head.startswith("ref:"):
        return head or None
    ref = head.split(":", 1)[1].strip()
    loose = git / ref
    try:
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    except OSError:
        return None
    return None


# --- add-ons ------------------------------------------------------------------
@dataclass
class PackState:
    """How one add-on folder is installed, if at all."""
    present: bool = False
    source: str = ""                # "git" or "cnr"
    commit: str = ""
    version: str = ""


def pack_state(folder: Path) -> PackState:
    folder = Path(folder)
    if not folder.is_dir():
        return PackState()
    if (folder / ".git").exists():
        return PackState(True, "git", commit=git_head(folder) or "")
    version = ""
    try:
        text = (folder / "pyproject.toml").read_text(encoding="utf-8")
        found = re.search(r"""(?m)^\s*version\s*=\s*["']([^"']+)["']""", text)
        version = found.group(1) if found else ""
    except OSError:
        pass
    return PackState(True, "cnr", version=version)


def pack_matches(pack: NodePack, state: PackState) -> bool:
    """Is this folder exactly what the catalogue pins?"""
    if not state.present or state.source != pack.source:
        return False
    if pack.source == "git":
        return bool(pack.commit) and state.commit.startswith(pack.commit[:12])
    return bool(pack.version) and state.version == pack.version


# --- models -------------------------------------------------------------------
def model_search_paths(portable: Path, timeout: int = 60) -> dict[str, list[Path]]:
    """Every model kind and the folders ComfyUI searches for it.

    Empty when the portable's Python cannot be run - a folder with no ComfyUI
    yet, or a broken one - and the caller falls back to the plain layout.
    """
    python = python_of(portable)
    comfy = Path(portable) / "ComfyUI"
    if not python.is_file() or not (comfy / "folder_paths.py").is_file():
        return {}
    try:
        result = subprocess.run([str(python), "-s", "-c", _PROBE, str(comfy)],
                                capture_output=True, text=True, timeout=timeout,
                                creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return {}
    for line in (result.stdout or "").splitlines():
        if line.startswith("EASYAI-PATHS"):
            try:
                raw = json.loads(line[len("EASYAI-PATHS"):])
            except ValueError:
                return {}
            return {kind: [Path(p) for p in paths] for kind, paths in raw.items()}
    return {}


def search_folders(model: Model, models_dir: Path,
                   search: dict[str, list[Path]]) -> list[Path]:
    """Where ComfyUI would look for this model, best first."""
    kind = LEGACY_KINDS.get(model.folder, model.folder)
    folders = list(search.get(kind) or search.get(model.folder) or [])
    own = Path(models_dir) / model.folder
    if own not in folders:
        folders.insert(0, own)
    return folders


def find_model(model: Model, models_dir: Path,
               search: dict[str, list[Path]]) -> Path | None:
    """The file ComfyUI would load for this model, if it has one already.

    Matched by the name the workflow uses, subfolder included, because that is
    exactly what ComfyUI resolves - a file of the same name elsewhere in the
    tree would not be found by the workflow either.
    """
    relative = Path(model.name.replace("\\", "/"))
    for folder in search_folders(model, models_dir, search):
        candidate = folder / relative
        if candidate.is_file():
            return candidate
    return None


# --- the whole picture ----------------------------------------------------------
@dataclass
class Survey:
    """Everything the Setup window needs to describe a folder before starting."""
    comfy_found: bool = False
    installed_version: str | None = None
    installed_commit: str | None = None
    pinned_version: str = ""
    pinned_commit: str = ""
    #: Add-ons EasyAI installs that are here but not at their pinned version.
    packs_differ: list[str] = field(default_factory=list)
    models_present: list[Model] = field(default_factory=list)
    models_missing: list[Model] = field(default_factory=list)
    models_blocked: list[Model] = field(default_factory=list)
    portable_bytes: int = 0

    @property
    def comfy_differs(self) -> bool:
        """This ComfyUI is not the pinned one."""
        if not self.comfy_found:
            return False
        if self.installed_commit and self.pinned_commit:
            return not self.installed_commit.startswith(self.pinned_commit[:12])
        return self.installed_version != self.pinned_version

    @property
    def comfy_is_newer(self) -> bool:
        return version_key(self.installed_version) > version_key(self.pinned_version)

    @property
    def needs_update(self) -> bool:
        """Anything an update would change."""
        return self.comfy_differs or bool(self.packs_differ)

    @property
    def missing_bytes(self) -> int:
        return sum(m.bytes for m in self.models_missing)

    def download_bytes(self, download_models: bool) -> int:
        total = 0 if self.comfy_found else self.portable_bytes
        if download_models:
            total += self.missing_bytes
        return total


def survey(catalog: Catalog, target: Path, groups: list[str],
           search: dict[str, list[Path]] | None = None) -> Survey:
    """Describe what installing these groups into ``target`` would involve.

    ``search`` can be passed in when the caller has already asked ComfyUI for
    its model folders, which saves a process start per tick of a group card.
    """
    portable = portable_of(target)
    comfy = portable / "ComfyUI"
    result = Survey(pinned_version=str(catalog.comfyui.get("version") or ""),
                    pinned_commit=str(catalog.comfyui.get("commit") or ""),
                    portable_bytes=int((catalog.comfyui.get("portable") or {}).get("bytes", 0)))

    result.comfy_found = python_of(portable).is_file() and comfy.is_dir()
    if result.comfy_found:
        result.installed_version = comfyui_version(comfy)
        result.installed_commit = git_head(comfy)
        for pack in catalog.nodes_for(groups):
            state = pack_state(comfy / "custom_nodes" / pack.name)
            if state.present and not pack_matches(pack, state):
                result.packs_differ.append(pack.name)

    if search is None:
        search = model_search_paths(portable) if result.comfy_found else {}
    models_dir = comfy / "models"
    for model in catalog.models_for(groups):
        # Also where setup-settings.json sends it, which ComfyUI may not have
        # been told about yet on a first run.
        if find_model(model, models_dir, search) or                 catalog.model_destination(model, models_dir, target).is_file():
            result.models_present.append(model)
        elif model.installable:
            result.models_missing.append(model)
        else:
            result.models_blocked.append(model)
    return result
