"""Background workers so the window never freezes.

Two QThread workers, both of which only ever talk to the UI through signals:

* :class:`EngineWorker` - probes for ComfyUI and starts it if needed, so the
  splash screen can report progress instead of the app hanging on launch.
* :class:`JobWorker` - runs one generation: patch the graph, upload inputs,
  queue, follow the websocket, download the results.

Nothing here touches a widget directly.
"""
from __future__ import annotations

import datetime as _dt
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.comfy.client import ComfyClient, ComfyError, PromptRejected
from app.comfy.launcher import ComfyLauncher
from app.i18n import N, t
from app.modes import MODES
from app.workflows.loader import Workflow
from app.workflows.patch import GenerationRequest, apply as patch_apply


@dataclass
class JobResult:
    """What one finished generation produced."""
    files: list[Path] = field(default_factory=list)
    #: Set instead of files by workflows whose result is words, not a picture.
    text: str = ""
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    elapsed: float = 0.0
    #: Things the run could not honour - a shape the workflow does not offer,
    #: for instance. Shown to the user, because silently producing something
    #: other than what was asked for is the worst way to fail.
    notes: list[str] = field(default_factory=list)


class EngineWorker(QThread):
    """Gets ComfyUI running without blocking the UI."""

    status = Signal(str)
    done = Signal(bool, str)      # ok, message

    def __init__(self, launcher: ComfyLauncher, auto_launch: bool, parent=None):
        super().__init__(parent)
        self.launcher = launcher
        self.auto_launch = auto_launch
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            result = self.launcher.ensure_running(
                auto_launch=self.auto_launch,
                on_status=self.status.emit,
                should_stop=lambda: self._cancelled,
            )
            self.done.emit(result.ok, result.message)
        except Exception as e:                      # never let a thread die silently
            traceback.print_exc()
            self.done.emit(False, f"Could not start the AI engine:\n{e}")


class JobWorker(QThread):
    """Runs a single generation end to end."""

    #: percent (0-100), message
    progress = Signal(int, str)
    #: raw JPEG/PNG bytes of ComfyUI's live preview
    preview = Signal(bytes)
    #: one finished file, emitted as soon as it lands so the gallery fills in
    file_ready = Signal(str)
    finished_ok = Signal(object)      # JobResult
    failed = Signal(str)

    def __init__(self, client: ComfyClient, workflow: Workflow,
                 request: GenerationRequest, output_dir: Path,
                 timeout: int = 1800, ratio_options: list[str] | None = None,
                 parent=None):
        super().__init__(parent)
        self.client = client
        self.workflow = workflow
        self.request = request
        self.output_dir = Path(output_dir)
        self.timeout = timeout
        self.ratio_options = ratio_options
        self._cancelled = False
        self._prompt_id: str | None = None
        self._notes: list[str] = []

    # -- control -----------------------------------------------------------
    def cancel(self) -> None:
        """Ask ComfyUI to stop, then unwind."""
        self._cancelled = True
        if self._prompt_id:
            self.client.interrupt()

    #: The one failure message that is not an error. The interface needs to
    #: recognise it to stay quiet, so both ends translate this same constant
    #: rather than comparing two separately written sentences.
    CANCELLED = N("Cancelled.")

    def _stopped(self) -> bool:
        return self._cancelled

    # -- the run -----------------------------------------------------------
    def run(self) -> None:
        started = _dt.datetime.now()
        try:
            result = self._generate()
            if self._cancelled:
                self.failed.emit(t(self.CANCELLED))
                return
            result.elapsed = (_dt.datetime.now() - started).total_seconds()
            self.finished_ok.emit(result)
        except PromptRejected as e:
            # Already phrased for a human by the client.
            self.failed.emit(str(e))
        except ComfyError as e:
            self.failed.emit(str(e))
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(t("Something went wrong:\n{problem}", problem=e))

    def _generate(self) -> JobResult:
        manifest = self.workflow.manifest
        if manifest is None:
            raise ComfyError(t("This workflow has no setup information."))

        # 1. Upload every input file first, so their ComfyUI-side names can be
        #    patched into the graph. A workflow may take several pictures -
        #    first and last frame, or a row of reference images - and each one
        #    goes to its own slot.
        self.progress.emit(0, "Getting things ready…")
        slots = [k for k in manifest.file_slots() if self.request.files.get(k)]
        for index, key in enumerate(slots):
            if self._stopped():
                return JobResult()
            label = manifest.label_for(key)
            if len(slots) > 1:
                self.progress.emit(2, f"Sending {label} ({index + 1} of {len(slots)})…")
            else:
                self.progress.emit(2, f"Sending {label.lower()}…")
            self.request.uploaded[key] = self.client.upload_file(self.request.files[key])

        # 2. Patch a copy of the graph.
        graph, report = patch_apply(self.workflow.graph, manifest, self.request,
                                    ratio_options=self.ratio_options)
        for skipped in report.skipped:
            print(f"[job] skipped {skipped}")
        self._notes = list(report.skipped)

        # 3. Queue it.
        if self._stopped():
            return JobResult()
        self.progress.emit(4, "Sending to the AI engine…")
        prompt_id, client_id = self.client.queue(graph)
        self._prompt_id = prompt_id

        # 4. Follow along. The websocket is for responsiveness only - the
        #    history poll below decides whether we actually got anything.
        self.progress.emit(5, "Starting…")
        finished = False
        try:
            finished = self.client.listen(
                client_id, prompt_id,
                on_progress=lambda pct, msg: self.progress.emit(max(5, pct), msg),
                on_preview=self.preview.emit,
                should_stop=self._stopped,
                timeout=self.timeout,
            )
        except ImportError:
            print("[job] websocket-client not installed; falling back to polling")
        except ComfyError:
            raise
        except Exception as e:
            print(f"[job] websocket unavailable, polling instead: {e}")

        if self._stopped():
            return JobResult()

        mode = MODES[self.workflow.mode]

        # 5. Collect. A workflow that produces words has no files to download.
        if mode.produces_text:
            self.progress.emit(96, "Reading the result…")
            pieces = self.client.wait_for_text(
                prompt_id, finished=finished,
                timeout=self.timeout, should_stop=self._stopped)
            if self._stopped():
                return JobResult()
            if not pieces:
                raise ComfyError(t(
                    "The workflow finished but produced no text.\n\n"
                    "It needs a node that shows its result — a Preview Any or "
                    "Show Text node on the output."))

            text = "\n\n".join(p.strip() for p in pieces)
            saved = self._save_text(text)
            self.progress.emit(100, "Done")
            return JobResult(files=[saved] if saved else [], text=text,
                             seed=report.seed, notes=list(self._notes))

        self.progress.emit(96, "Collecting the results…")
        want = mode.output_keys
        results = self.client.wait_for_results(
            prompt_id, want=want, finished=finished,
            timeout=self.timeout, should_stop=self._stopped,
        )
        if self._stopped():
            return JobResult()
        if not results:
            raise ComfyError(t(
                "The AI engine finished but produced no files.\n\n"
                "The workflow may have no Save node, or it may have been "
                "stopped."))

        stem = _timestamp_stem(self.workflow.name)
        saved: list[Path] = []
        for i, item in enumerate(results):
            if self._stopped():
                break
            self.progress.emit(96 + int(4 * (i + 1) / len(results)),
                               f"Saving {i + 1} of {len(results)}…")
            name = stem if len(results) == 1 else f"{stem}_{i + 1:02d}"
            path = self.client.save_result(item, self.output_dir, stem=name)
            saved.append(path)
            self.file_ready.emit(str(path))

        self.progress.emit(100, "Done")
        return JobResult(files=saved, seed=report.seed,
                         width=report.width, height=report.height,
                         notes=list(self._notes))


    def _save_text(self, text: str) -> Path | None:
        """Keep a copy on disk, so good prompts are not lost when the box clears."""
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / f"{_timestamp_stem(self.workflow.name)}.txt"
            path.write_text(text, encoding="utf-8")
            self.file_ready.emit(str(path))
            return path
        except OSError as e:
            print(f"[job] could not save the text: {e}")
            return None


def safe_name(workflow_name: str) -> str:
    """A workflow name reduced to what may appear in a filename.

    Shared with app.prompts, which runs every known workflow through this to
    recognise which one made a given result. If the two ever disagreed, reading
    a prompt back would quietly stop finding its workflow.
    """
    return "".join(c if (c.isalnum() or c in "-_") else "_"
                   for c in workflow_name)[:40]


def _timestamp_stem(workflow_name: str) -> str:
    """EasyAI_2026-08-14_153012_Flux2Klein - sorts by time, says what made it."""
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return f"{stamp}_{safe_name(workflow_name)}".strip("_")
