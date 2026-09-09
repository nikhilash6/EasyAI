"""Reading the prompt back out of a finished picture or video.

ComfyUI writes the graph it ran into every file it saves - a `prompt` text
chunk in a PNG, a block of JSON in the header of an MP4 - so the prompt is
never really lost, only unreadable. This module gets it back.

Nothing here touches the engine. A prompt can be read with ComfyUI closed,
which matters because looking through old results is exactly what someone does
before starting it up.

The one thing this must not do is guess when it does not have to. A graph can
hold several text boxes - `Krea 2 t2i` has three, one of them the prompt
enhancer's system instruction - and detection run from scratch picks the wrong
one. So a file is first matched to the workflow that made it, and that
workflow's saved manifest, which already knows which box is the prompt, is
believed over any fresh detection.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.jobs import safe_name

#: What is worth opening. PNG is where ComfyUI puts it; the others are tried
#: rather than refused, because a file that carries nothing simply reads as
#: "no prompt" and that is a better answer than "wrong sort of file".
IMAGE_EXT = {".png", ".webp", ".jpg", ".jpeg"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
READABLE_EXT = IMAGE_EXT | VIDEO_EXT

#: PNG text chunks ComfyUI writes. `prompt` is the API graph and the one that
#: matters; `workflow` is the editor's own format, kept as a fallback.
_PNG_KEYS = ("prompt", "workflow")

#: How much of a video to search. Every file tested carries the JSON about 4 KB
#: in, but a different encoder can put the metadata atom at the end, so both
#: ends are read. A megabyte each way costs nothing and covers both layouts.
_SCAN = 1024 * 1024

#: `2026-08-15_000247_Krea_2_t2i` or `..._Krea_2_t2i_02` for a batch.
_STAMPED = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_(.+?)(?:_\d{2})?$")

#: Below this, two graphs have nothing to do with each other. Deliberately
#: low: ComfyUI expands subgraphs when it submits, so a result's graph can
#: differ a good deal from the file that produced it and still be the same
#: workflow. The real protection is _usable() below, not this number.
_MIN_OVERLAP = 0.3

_decoder = json.JSONDecoder()


@dataclass
class PromptRead:
    """What was recovered from one file."""

    prompt: str
    #: The workflow it was matched to, empty when nothing matched.
    workflow_name: str = ""
    #: False when the prompt came from detection rather than from a workflow's
    #: own manifest - shown as a best guess rather than stated as fact.
    certain: bool = True


# --- getting the graph out of the file ------------------------------------
def _graph_from_image(path: Path) -> dict | None:
    try:
        from PIL import Image

        with Image.open(path) as picture:
            info = dict(picture.info)
    except Exception:
        return None
    for key in _PNG_KEYS:
        raw = info.get(key)
        if not isinstance(raw, str):
            continue
        try:
            graph = json.loads(raw)
        except ValueError:
            continue
        if isinstance(graph, dict) and graph:
            return graph
    return None


def _looks_like_graph(found) -> bool:
    """A graph is a dict of nodes, each carrying a class_type."""
    if not isinstance(found, dict) or len(found) < 2:
        return False
    nodes = [n for n in found.values()
             if isinstance(n, dict) and "class_type" in n]
    return len(nodes) >= 2


def _graph_from_bytes(blob: bytes) -> dict | None:
    """Find the first embedded JSON object that looks like a graph.

    Scanned with raw_decode rather than by counting braces: a prompt
    containing a closing brace would break brace counting, and prompts do
    contain them.
    """
    text = blob.decode("utf-8", "replace")
    at = 0
    while True:
        at = text.find('{"', at)
        if at < 0:
            return None
        try:
            found, _ = _decoder.raw_decode(text, at)
        except ValueError:
            at += 1
            continue
        if _looks_like_graph(found):
            return found
        at += 1


def _graph_from_video(path: Path) -> dict | None:
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            head = handle.read(_SCAN)
            tail = b""
            if size > _SCAN * 2:
                handle.seek(-_SCAN, 2)
                tail = handle.read(_SCAN)
    except OSError:
        return None
    return _graph_from_bytes(head) or (_graph_from_bytes(tail) if tail else None)


def graph_in(path: str | Path) -> dict | None:
    """The graph a file was made with, or None if it does not carry one."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXT:
        return _graph_from_video(path)
    if suffix in IMAGE_EXT:
        return _graph_from_image(path)
    return None


# --- matching the file to the workflow that made it -----------------------
def _signature(graph: dict) -> frozenset:
    return frozenset((node_id, (node or {}).get("class_type"))
                     for node_id, node in graph.items()
                     if isinstance(node, dict))


def _overlap(one: frozenset, other: frozenset) -> float:
    both = one | other
    return len(one & other) / len(both) if both else 0.0


def _usable(graph: dict, workflow, binding) -> str | None:
    """The prompt this binding points at, if it can be trusted here.

    The guard that keeps a loose signature match honest: the node must still
    exist in this file's graph, be the same kind of node as in the workflow,
    and hold actual text. Without it a stranger's picture could latch onto an
    unrelated workflow and report a filename as a prompt.
    """
    if binding is None:
        return None
    node = graph.get(binding.node)
    if not isinstance(node, dict):
        return None
    mine = (getattr(workflow, "graph", None) or {}).get(binding.node)
    if isinstance(mine, dict) and mine.get("class_type") != node.get("class_type"):
        return None
    value = (node.get("inputs") or {}).get(binding.input)
    return value if isinstance(value, str) and value.strip() else None


def _prompt_binding(workflow):
    manifest = getattr(workflow, "manifest", None)
    return manifest.get("prompt") if manifest is not None else None


def _by_filename(path: Path, workflows: list):
    """EasyAI names its results after the workflow, so this match is exact."""
    match = _STAMPED.match(path.stem)
    if not match:
        return None, None
    wanted = match.group(1).strip("_")
    for workflow in workflows:
        if safe_name(workflow.name).strip("_") == wanted:
            return workflow, _prompt_binding(workflow)
    return None, None


def _by_signature(graph: dict, workflows: list):
    """For a result whose workflow has since been renamed, or whose subgraphs
    ComfyUI expanded on the way in."""
    mine = _signature(graph)
    best, score = None, 0.0
    for workflow in workflows:
        if not getattr(workflow, "graph", None):
            continue
        rating = _overlap(mine, _signature(workflow.graph))
        if rating > score:
            best, score = workflow, rating
    if best is None or score < _MIN_OVERLAP:
        return None, None
    return best, _prompt_binding(best)


def _by_detection(graph: dict, mode: str, name: str) -> str | None:
    """Last resort, for a workflow this copy of EasyAI has never seen."""
    from app.workflows.manifest import autodetect

    try:
        manifest = autodetect(graph, mode=mode, name=name)
    except Exception:
        return None
    binding = manifest.get("prompt")
    if binding is None:
        return None
    node = graph.get(binding.node)
    if not isinstance(node, dict):
        return None
    value = (node.get("inputs") or {}).get(binding.input)
    return value if isinstance(value, str) and value.strip() else None


# --- the whole job --------------------------------------------------------
def read_prompt(path: str | Path,
                workflows: list | None = None) -> PromptRead | None:
    """The prompt a file was made with, or None if it does not carry one.

    Tried in order of how much can be trusted: the workflow named in the
    filename, then the workflow whose graph this most resembles, then
    detection. The first two answer from a saved manifest and are stated as
    fact; the third is marked as a guess.
    """
    path = Path(path)
    graph = graph_in(path)
    if not graph:
        return None
    workflows = workflows or []

    workflow, binding = _by_filename(path, workflows)
    if workflow is not None:
        text = _usable(graph, workflow, binding)
        if text:
            return PromptRead(text.strip(), workflow.name, certain=True)

    workflow, binding = _by_signature(graph, workflows)
    if workflow is not None:
        text = _usable(graph, workflow, binding)
        if text:
            return PromptRead(text.strip(), workflow.name, certain=True)

    mode = "video" if path.suffix.lower() in VIDEO_EXT else "image"
    guessed = _by_detection(graph, mode, path.stem)
    if guessed:
        return PromptRead(guessed.strip(), "", certain=False)
    return None


def recent_results(folders, limit: int = 80) -> list:
    """Everything readable in the results folders, newest first."""
    found = []
    for folder in folders:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            if item.is_file() and item.suffix.lower() in READABLE_EXT:
                try:
                    found.append((item.stat().st_mtime, item))
                except OSError:
                    continue
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in found[:limit]]
