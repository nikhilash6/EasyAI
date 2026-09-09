"""The install itself, as a sequence of resumable steps.

Every step checks whether its work is already done before doing it, so an
install interrupted at 80% carries on from 80% rather than starting again. The
state file written at the end records what succeeded, which is what makes a
second run cheap.

Everything is written inside the destination folder the user chose, with one
deliberate exception: the last step writes EasyAI's settings.json so the viewer
does not finish a multi-hour install and then have to type a path in by hand.
That file is merged, never replaced.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.i18n import t
from setup.catalog import Catalog, Model, NodePack, human_bytes, token_host_for
from setup.download import Cancelled, DownloadError, download, free_space

from app.paths import resource_dir

#: The workflows and licences copied into a new install ship with the program.
ROOT = resource_dir()
STATE_FILE = "easyai-setup.json"

#: Where the extracted portable puts things.
COMFY_SUB = "ComfyUI_windows_portable"

_7ZIP_CANDIDATES = (
    Path(r"C:\Program Files\7-Zip\7z.exe"),
    Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
)

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class Report:
    """What an install did, for the log and the state file."""
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


class Installer:
    """Runs the steps for one selection of groups."""

    def __init__(self, catalog: Catalog, target: Path, groups: list[str],
                 hf_token: str = "", civitai_token: str = "",
                 on_step: Callable[[str], None] | None = None,
                 on_progress: Callable | None = None,
                 should_stop: Callable[[], bool] | None = None):
        self.catalog = catalog
        self.target = Path(target)
        self.groups = groups
        #: Kept in memory for the run only - never written to disk, because a
        #: token in a config file outlives the reason it was needed.
        self.tokens = {"HuggingFace": hf_token, "Civitai": civitai_token}
        self._say = on_step or (lambda m: None)
        self._progress = on_progress
        self._stop = should_stop or (lambda: False)
        self.report = Report()

    # -- paths -------------------------------------------------------------
    @property
    def portable(self) -> Path:
        return self.target / COMFY_SUB

    @property
    def comfy(self) -> Path:
        return self.portable / "ComfyUI"

    @property
    def python(self) -> Path:
        return self.portable / "python_embeded" / "python.exe"

    @property
    def models_dir(self) -> Path:
        return self.comfy / "models"

    # -- the run -----------------------------------------------------------
    def run(self) -> Report:
        for step in (self.check_space, self.install_comfyui, self.pin_comfyui_commit,
                     self.install_nodes, self.install_models, self.copy_workflows,
                     self.configure_easyai):
            if self._stop():
                self.report.failed.append("cancelled")
                return self.report
            try:
                step()
            except Cancelled:
                self.report.failed.append("cancelled")
                return self.report
            except (DownloadError, OSError, subprocess.SubprocessError) as e:
                self.report.failed.append(f"{step.__name__}: {e}")
                self._say(t("FAILED  {problem}", problem=e))
                return self.report
        self.write_state()
        return self.report

    # -- 1. room to work ---------------------------------------------------
    def check_space(self) -> None:
        needed = self.catalog.download_bytes(self.groups)
        # The portable expands to about three times its archive, and models are
        # written once, so headroom of the download plus a margin is enough.
        needed = int(needed * 1.15)
        free = free_space(self.target)
        self._say(t("Space needed about {needed}, free {free}",
                    needed=human_bytes(needed), free=human_bytes(free)))
        if free and free < needed:
            raise OSError(t(
                "Not enough room on that drive.\n\n"
                "This selection needs about {needed} and {free} is free. Pick "
                "another folder, or untick a group.",
                needed=human_bytes(needed), free=human_bytes(free)))

    # -- 2. ComfyUI portable ----------------------------------------------
    def install_comfyui(self) -> None:
        if (self.python).is_file():
            self._say(t("ComfyUI is already here - leaving it alone"))
            self.report.skipped.append("comfyui")
            return

        spec = self.catalog.comfyui["portable"]
        archive = self.target / spec["asset"]
        self._say(t("Downloading ComfyUI {version} ({size})",
                    version=self.catalog.comfyui["version"],
                    size=human_bytes(spec["bytes"])))
        download(spec["url"], archive, expected_bytes=spec.get("bytes", 0),
                 on_progress=self._progress, should_stop=self._stop)

        self._say(t("Unpacking ComfyUI - this takes a few minutes"))
        extract_7z(archive, self.target)
        if not self.python.is_file():
            raise OSError(t(
                "Unpacked, but {path} is missing - the archive may not be the "
                "Windows portable build.", path=self.python))
        archive.unlink(missing_ok=True)
        self.report.done.append("comfyui")

    # -- 3. pin the core --------------------------------------------------
    def pin_comfyui_commit(self) -> None:
        """Move ComfyUI onto the exact commit this catalogue was built from.

        The portable ships whatever the release tag was - there is no v0.33.0
        release, only v0.33.1 - so the core is checked out to the recorded
        commit to remove that difference.
        """
        commit = self.catalog.comfyui.get("commit")
        if not commit or not (self.comfy / ".git").is_dir():
            self._say(t("Skipping version pin (no git checkout in this build)"))
            self.report.skipped.append("pin")
            return
        if self._git(self.comfy, "rev-parse", "HEAD").strip().startswith(commit[:12]):
            self._say(t("ComfyUI already pinned to {commit}", commit=commit[:10]))
            self.report.skipped.append("pin")
            return
        self._say(t("Pinning ComfyUI to {commit}", commit=commit[:10]))
        self._git(self.comfy, "fetch", "--depth", "50", "origin", commit, check=False)
        self._git(self.comfy, "checkout", commit)
        self.report.done.append("pin")

    # -- 4. custom nodes ---------------------------------------------------
    def install_nodes(self) -> None:
        packs = self.catalog.nodes_for(self.groups)
        if not packs:
            self._say(t("No custom nodes needed for this selection"))
            return
        folder = self.comfy / "custom_nodes"
        folder.mkdir(parents=True, exist_ok=True)

        for pack in packs:
            if self._stop():
                raise Cancelled("nodes")
            destination = folder / pack.name
            if destination.is_dir():
                self._say("  " + t("{name} already installed", name=pack.name))
                self.report.skipped.append(pack.name)
                continue
            self._say("  " + t("installing {name}", name=pack.name))
            try:
                self._install_pack(pack, destination)
                self._pip_requirements(destination)
                self.report.done.append(pack.name)
            except (subprocess.SubprocessError, OSError, DownloadError) as e:
                # One awkward node pack should not sink the whole install.
                self._say("  !! " + t("{name} failed: {problem}",
                                      name=pack.name, problem=e))
                self.report.failed.append(f"{pack.name}: {e}")

    def _install_pack(self, pack: NodePack, destination: Path) -> None:
        if pack.source == "git" and pack.url:
            self._clone_at_commit(pack, destination)
        elif pack.source == "cnr" and pack.id:
            url = pack.url or registry_url(pack.id, pack.version)
            if not url:
                raise OSError(f"the registry has no {pack.name} {pack.version}")
            archive = destination.parent / f"{pack.name}-{Path(url).name}"
            try:
                download(url, archive, on_progress=self._progress, should_stop=self._stop)
            except DownloadError:
                # The recorded CDN link has moved; ask the registry again.
                fresh = registry_url(pack.id, pack.version)
                if not fresh or fresh == url:
                    raise
                download(fresh, archive, on_progress=self._progress,
                         should_stop=self._stop)
            # These archives hold the pack's files at the root, so the folder
            # unpacked into is what becomes the custom_nodes entry.
            unpack(archive, destination)
            archive.unlink(missing_ok=True)
        else:
            raise OSError(f"no source recorded for {pack.name}")

    def _clone_at_commit(self, pack: NodePack, destination: Path) -> None:
        """Fetch just the one commit this pack is pinned to.

        A shallow fetch of the exact SHA takes a fraction of a full clone -
        ComfyUI_LayerStyle alone is 517 files with years of history behind it -
        and it cannot land on the wrong revision the way cloning a branch and
        hoping the commit is in it can. If the host refuses SHA fetches, fall
        back to a full clone.
        """
        destination.mkdir(parents=True, exist_ok=True)
        try:
            self._git(destination, "init", "-q")
            self._git(destination, "remote", "add", "origin", pack.url)
            self._git(destination, "fetch", "--depth", "1", "origin", pack.commit)
            self._git(destination, "checkout", "-q", "FETCH_HEAD")
        except subprocess.SubprocessError:
            shutil.rmtree(destination, ignore_errors=True)
            self._git(destination.parent, "clone", pack.url, pack.name)
            self._git(destination, "checkout", pack.commit)

    def _pip_requirements(self, folder: Path) -> None:
        req = folder / "requirements.txt"
        if not req.is_file() or not self.python.is_file():
            return
        self._say("    " + t("installing its Python packages"))
        subprocess.run([str(self.python), "-m", "pip", "install", "-r", str(req)],
                       capture_output=True, timeout=1800, creationflags=_NO_WINDOW)

    # -- 5. models ---------------------------------------------------------
    def install_models(self) -> None:
        models = self.catalog.models_for(self.groups)
        usable = [m for m in models if m.installable]
        blocked = [m for m in models if not m.installable]
        total = sum(m.bytes for m in usable)
        self._say(t("{n} models to fetch, {size}",
                    n=len(usable), size=human_bytes(total)))

        for i, model in enumerate(usable, 1):
            if self._stop():
                raise Cancelled("models")
            destination = self.models_dir / model.relative_path
            if destination.is_file() and (not model.bytes
                                          or destination.stat().st_size == model.bytes):
                self._say(f"  [{i}/{len(usable)}] "
                          + t("{name} already here", name=model.filename))
                self.report.skipped.append(model.filename)
                continue
            self._say(f"  [{i}/{len(usable)}] {model.filename}  "
                      + human_bytes(model.bytes))
            try:
                self._fetch_model(model, destination)
                self.report.done.append(model.filename)
            except DownloadError as e:
                # One model that cannot be fetched - a missing key, a dead
                # link - must not throw away the other hundred gigabytes that
                # downloaded perfectly. Record it and carry on; the summary at
                # the end says what is missing and why.
                self._say("  !! " + t("{name} could not be downloaded",
                                      name=model.filename))
                self.report.failed.append(f"{model.filename}: {_first_line(e)}")

        for model in blocked:
            self._say("  !! " + t("no download link for {name} - copy it in by hand",
                                  name=model.name))
            self.report.failed.append(f"no link: {model.name}")

        self.write_licences()

    def _fetch_model(self, model: Model, destination: Path) -> None:
        """Try each source in turn, keeping the first that works.

        The mirror is tried first because it needs no account. If it has been
        moved, revoked or the server is down, the original is still there - so
        the worst case is the old "you need a key" message, never a dead end.
        """
        problems = []
        for label, url in model.sources:
            if problems:
                # Never resume one source's part file from another. The two
                # copies are the same length, so a half-and-half file would
                # pass the size check and only reveal itself as a corrupt model
                # much later. Starting over costs a download; getting this
                # wrong costs a mystery.
                part = destination.with_suffix(destination.suffix + ".part")
                part.unlink(missing_ok=True)
            try:
                download(url, destination, expected_bytes=model.bytes,
                         sha256=model.sha256 if label == "mirror" else "",
                         # Chosen from the URL being called, so the mirror never
                         # receives a HuggingFace or Civitai credential.
                         token=self.tokens.get(token_host_for(url), ""),
                         on_progress=self._progress, should_stop=self._stop)
                return
            except Cancelled:
                raise
            except DownloadError as e:
                problems.append(f"{label}: {e}")
                if label != model.sources[-1][0]:
                    self._say("      " + t(
                        "the mirror failed, trying the original source"))

        raise DownloadError(
            t("Could not get {name} from any source.", name=model.filename)
            + "\n\n" + "\n\n".join(problems))

    def write_licences(self) -> None:
        """Save the licences that come with anything installed from the mirror.

        The LTX-2.x Community License permits redistribution on the condition
        that recipients are given a copy of the Agreement (Section 3.2), so
        writing these is part of being allowed to host the files at all - not
        a courtesy.
        """
        mirrored = self.catalog.mirrored(self.groups)
        if not mirrored:
            return
        source = ROOT / "setup" / "licences"
        if not source.is_dir():
            return
        folder = self.target / "LICENSES"
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(source.glob("*")):
            if path.is_file():
                shutil.copy2(path, folder / path.name)
        self._say(t("Licences for the mirrored models written to {folder}",
                    folder=folder.name))

    # -- 6. the workflows --------------------------------------------------
    def copy_workflows(self) -> None:
        """Put EasyAI's workflows where a copy of EasyAI will find them."""
        source_root = ROOT / "workflows"
        if not source_root.is_dir():
            return
        destination_root = self.target / "EasyAI-workflows"
        for key in self.groups:
            source = source_root / key
            if not source.is_dir():
                continue
            destination = destination_root / key
            destination.mkdir(parents=True, exist_ok=True)
            for path in source.glob("*.json"):
                shutil.copy2(path, destination / path.name)
        self._say(t("Workflows copied to {folder}", folder=destination_root.name))
        self.report.done.append("workflows")

    # -- 7. point EasyAI at what we just installed -------------------------
    def configure_easyai(self) -> None:
        """Write EasyAI's settings so it opens ready to use.

        Without this the viewer finishes a multi-hour install and is then told
        to open Settings and paste in a path - the one step most likely to be
        got wrong, because EasyAI wants the portable folder itself rather than
        the folder holding it.

        This is the only thing EasyAI Setup writes outside its own target
        folder, and it is deliberate: settings.json sits beside the .exe files,
        which is where EasyAI reads it from. Existing settings are merged, not
        replaced, so nothing else the user has chosen is lost.
        """
        from app.comfy.launcher import ComfyLauncher
        from app.config import SETTINGS_PATH, Config

        if not self.python.is_file():
            self._say(t("Skipping EasyAI setup - ComfyUI is not installed here"))
            return

        cfg = Config(SETTINGS_PATH)
        was = str(cfg.get("comfyui_dir") or "")

        cfg.set("comfyui_dir", str(self.portable))
        launchers = ComfyLauncher(self.portable, "", None).find_launchers()
        if launchers:
            cfg.set("comfyui_launcher", launchers[0])

        # The workflows were copied in beside the install; point EasyAI at that
        # copy rather than leaving it looking at an empty folder.
        workflows = self.target / "EasyAI-workflows"
        if any(workflows.glob("*/*.json")):
            cfg.set("workflow_dir", str(workflows))

        # The welcome message exists to make someone set the ComfyUI folder.
        # That is now done, so showing it would ask for work already finished.
        cfg.set("first_run_done", True)
        cfg.save()

        self._say(t("EasyAI is now set to use this ComfyUI"))
        self._say(f"    {SETTINGS_PATH}")
        if was and was != str(self.portable):
            self._say(t("    it previously pointed at {old}", old=was))
        self.report.done.append("easyai-settings")

    # -- state -------------------------------------------------------------
    def write_state(self) -> None:
        state = {
            "comfyui": self.catalog.comfyui.get("version"),
            "commit": self.catalog.comfyui.get("commit"),
            "groups": self.groups,
            "installed": self.report.done,
            "skipped": self.report.skipped,
            "failed": self.report.failed,
        }
        (self.target / STATE_FILE).write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        self._say(t("Finished. {done} installed, {skipped} already present, "
                    "{failed} need attention.",
                    done=len(self.report.done), skipped=len(self.report.skipped),
                    failed=len(self.report.failed)))

    # -- helpers -----------------------------------------------------------
    def _git(self, cwd: Path, *args: str, check: bool = True) -> str:
        result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                                text=True, timeout=1800, creationflags=_NO_WINDOW)
        if check and result.returncode != 0:
            raise subprocess.SubprocessError(
                (result.stderr or result.stdout or "git failed").strip()[:300])
        return result.stdout


def _first_line(error: Exception) -> str:
    """The headline of a multi-paragraph download error, for the summary list."""
    return str(error).strip().splitlines()[0]


def unpack(archive: Path, destination: Path) -> None:
    """Unpack a downloaded node pack, going by content rather than by name.

    The registry serves comfyui_essentials as "node.tar.gz" with a
    Content-Type of application/gzip, and the file is a plain zip. Anything
    that trusts the extension gets it wrong, so the magic bytes decide.
    """
    import tarfile
    import zipfile

    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            z.extractall(destination)
        return
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            t.extractall(destination, filter="data")
        return
    raise OSError(t("{name} is not an archive this can open", name=archive.name))


def registry_url(node_id: str, version: str) -> str | None:
    """Where the registry currently serves this exact node version."""
    import requests

    try:
        r = requests.get(f"https://api.comfy.org/nodes/{node_id}/install",
                         params={"version": version}, timeout=30)
        if r.status_code == 200:
            return r.json().get("downloadUrl")
    except (requests.RequestException, ValueError):
        pass
    return None


def find_7zip() -> Path | None:
    found = shutil.which("7z") or shutil.which("7za")
    if found:
        return Path(found)
    for candidate in _7ZIP_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def extract_7z(archive: Path, destination: Path) -> None:
    """Unpack a .7z, preferring installed 7-Zip and falling back to py7zr.

    7-Zip is several times faster on a 2 GB archive, but it is not on every
    machine, so py7zr is the guarantee.
    """
    exe = find_7zip()
    if exe:
        result = subprocess.run([str(exe), "x", str(archive), f"-o{destination}", "-y"],
                                capture_output=True, text=True, creationflags=_NO_WINDOW)
        if result.returncode == 0:
            return
    try:
        import py7zr
    except ImportError:
        raise OSError(t(
            "Cannot unpack the ComfyUI archive.\n\n"
            "Either install 7-Zip from 7-zip.org, or run:\n"
            "    pip install py7zr"))
    with py7zr.SevenZipFile(archive, "r") as z:
        z.extractall(path=str(destination))
