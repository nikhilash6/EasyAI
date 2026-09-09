"""Finds workflow files, checks their format, and pairs them with a manifest.

ComfyUI saves two different JSON shapes and only one of them can be queued:

* **API format** - a flat dict of ``{node_id: {"class_type": ..., "inputs": ...}}``.
  This is what ``POST /prompt`` wants, and what *Workflow -> Export (API)* writes.
* **UI format** - ``{"nodes": [...], "links": [...]}``, what plain Save writes.
  ``/prompt`` rejects it.

Every workflow currently saved on this machine is UI format, so the failure has
to be explained rather than thrown. Two thirds of that is handled here:
UI files that carry an embedded ``extra.prompt`` graph (ComfyUI stores one when
the workflow has been run) are converted silently; the rest get a card that says
how to re-export.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.modes import MODES
from app.workflows.manifest import (
    _AUDIO_LOADERS, _IMAGE_LOADERS, _VIDEO_LOADERS, BINDING_KEYS,
    FILE_SLOTS, MANIFEST_SUFFIX, MANIFEST_VERSION, Manifest, autodetect,
    manifest_path_for,
)


class Format(Enum):
    API = "api"                 # ready to queue
    UI_WITH_API = "ui+api"      # UI file carrying a matching embedded API graph
    UI = "ui"                   # UI only - needs re-export
    UI_STALE_API = "ui+stale"   # embedded graph is left over from another workflow
    INVALID = "invalid"         # not a workflow at all


@dataclass
class Workflow:
    """One workflow file plus everything the UI needs to decide what to do with it."""
    path: Path
    mode: str
    name: str
    fmt: Format
    graph: dict = field(default_factory=dict)
    manifest: Manifest | None = None
    manifest_path: Path | None = None
    error: str = ""
    #: Filled in by the pre-flight check (app/comfy/objectinfo.py).
    missing_nodes: list[str] = field(default_factory=list)
    missing_models: list[str] = field(default_factory=list)
    checked: bool = False

    # -- readiness ---------------------------------------------------------
    @property
    def loadable(self) -> bool:
        return self.fmt in (Format.API, Format.UI_WITH_API) and bool(self.graph)

    @property
    def ready(self) -> bool:
        return self.loadable and not self.missing_nodes and not self.missing_models

    @property
    def needs_attention(self) -> bool:
        return bool(self.manifest and self.manifest.ambiguous)

    def status_text(self) -> str:
        """One line for the workflow card."""
        if not self.loadable:
            return self.error or "Cannot be used"
        if self.missing_nodes:
            return "Missing add-on: " + ", ".join(sorted(set(self.missing_nodes))[:2])
        if self.missing_models:
            return "Missing model: " + Path(self.missing_models[0]).name
        if not self.checked:
            return "Not checked yet"
        if self.needs_attention:
            return "Check the setup for this workflow"
        return "Ready"

    def status_icon(self) -> str:
        if not self.loadable or self.missing_nodes or self.missing_models:
            return "⛔"        # no entry
        if self.needs_attention:
            return "⚠"        # warning
        return "✅"            # check mark

    @property
    def thumbnail(self) -> Path | None:
        """<workflow>.png / .jpg / .webp sitting beside the json, if present."""
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = self.path.with_suffix(ext)
            if candidate.is_file():
                return candidate
        return None


# Node types in a UI file that never appear in an API graph, so they must not
# count when checking whether an embedded graph belongs to this workflow.
_UI_ONLY_TYPES = {"markdownnote", "note", "reroute", "primitivenode",
                  "getnode", "setnode"}
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

#: How much of the visible workflow the embedded graph must account for.
_MATCH_THRESHOLD = 0.6


def _visible_node_types(raw: dict) -> set[str]:
    """Real, active node types in the editor graph.

    Muted and bypassed nodes (mode 2 and 4) are dropped from an API export, and
    subgraph references are GUIDs that expand into their contents, so neither
    can be expected to appear verbatim in the embedded graph.
    """
    types = set()
    for node in raw.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if node.get("mode") in (2, 4):
            continue
        node_type = str(node.get("type") or "")
        if not node_type or _GUID.match(node_type):
            continue
        if node_type.lower() in _UI_ONLY_TYPES:
            continue
        types.add(node_type)
    return types


def _embedded_matches(raw: dict, embedded: dict) -> bool:
    """Does this embedded graph actually belong to this workflow?

    ComfyUI stores the last prompt it ran in ``extra.prompt``, and that copy is
    often left over from a different workflow the file was derived from. Running
    it would quietly produce the wrong thing - a first-and-last-frame workflow
    silently generating plain text-to-video - so the two are compared first.

    Two checks. The anchors are the decisive one: if the editor graph loads a
    picture and the embedded graph has no loader at all, they cannot be the same
    workflow, however much else lines up.
    """
    visible = _visible_node_types(raw)
    if not visible:
        return True          # nothing to check against; the graph is all we have

    embedded_types = {str(n.get("class_type") or "")
                      for n in embedded.values() if isinstance(n, dict)}

    anchors = {t for t in visible if _is_anchor(t)}
    if anchors and not anchors.issubset(embedded_types):
        return False

    matched = sum(1 for t in visible if t in embedded_types)
    return (matched / len(visible)) >= _MATCH_THRESHOLD


def _is_anchor(node_type: str) -> bool:
    """A node whose presence defines what a workflow is: an input or an output.

    These are never optional. A workflow that loads a picture and saves a video
    must show both in any graph that claims to be it.
    """
    low = node_type.lower()
    loads_a_file = low.startswith("load") or ".load" in low or "_load" in low
    saves_a_file = low.startswith("save") or "videocombine" in low
    return loads_a_file or saves_a_file


# --- format detection -----------------------------------------------------
def classify(raw: object) -> Format:
    if not isinstance(raw, dict):
        return Format.INVALID

    # API format: a non-empty dict where every value is a node.
    values = [v for v in raw.values()]
    if values and all(isinstance(v, dict) and "class_type" in v for v in values):
        return Format.API

    if isinstance(raw.get("nodes"), list):
        embedded = (raw.get("extra") or {}).get("prompt")
        if isinstance(embedded, dict) and embedded and \
                all(isinstance(v, dict) and "class_type" in v for v in embedded.values()):
            return (Format.UI_WITH_API if _embedded_matches(raw, embedded)
                    else Format.UI_STALE_API)
        return Format.UI

    return Format.INVALID


def extract_graph(raw: dict, fmt: Format) -> dict:
    if fmt is Format.API:
        return raw
    if fmt is Format.UI_WITH_API:
        return (raw.get("extra") or {}).get("prompt") or {}
    return {}


_UI_ADVICE = (
    "This is a ComfyUI editor file, not an API file.\n\n"
    "In ComfyUI open the workflow, then choose\n"
    "Workflow → Export (API)\n"
    "and save the result into this folder."
)

_STALE_ADVICE = (
    "This file has an old copy of a different workflow saved inside it, so "
    "EasyAI cannot use it safely — it would make the wrong thing.\n\n"
    "In ComfyUI open the workflow, then choose\n"
    "Workflow → Export (API)\n"
    "and save the result into this folder."
)


# --- loading --------------------------------------------------------------
def load_workflow(path: str | Path, mode: str, auto_write_manifest: bool = True,
                  caps=None) -> Workflow:
    """Read one workflow file and attach (or generate) its manifest."""
    path = Path(path)
    name = path.stem
    wf = Workflow(path=path, mode=mode, name=name, fmt=Format.INVALID)

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        wf.error = f"This file is not valid JSON (line {e.lineno})."
        return wf
    except OSError as e:
        wf.error = f"Could not read the file: {e}"
        return wf

    wf.fmt = classify(raw)
    if wf.fmt is Format.UI:
        wf.error = _UI_ADVICE
        return wf
    if wf.fmt is Format.UI_STALE_API:
        wf.error = _STALE_ADVICE
        return wf
    if wf.fmt is Format.INVALID:
        wf.error = "This does not look like a ComfyUI workflow."
        return wf

    wf.graph = extract_graph(raw, wf.fmt)
    if not wf.graph:
        wf.fmt = Format.INVALID
        wf.error = "The workflow has no nodes in it."
        return wf

    wf.manifest_path = manifest_path_for(path)
    wf.manifest = _load_or_create_manifest(wf, auto_write_manifest, caps)
    if wf.manifest.name:
        wf.name = wf.manifest.name
    return wf


def _load_or_create_manifest(wf: Workflow, auto_write: bool, caps=None) -> Manifest:
    """Read the sidecar, or detect one and write it once.

    A manifest is normally never regenerated, so hand corrections survive. The
    exception is one that plainly isn't about this graph any more - empty, or
    pointing at nodes that no longer exist. Re-exporting a workflow renumbers
    everything, and an editor file replaced by a proper API export shares no ids
    at all with what came before. Keeping such a manifest means the workflow
    silently loses its prompt and size controls.
    """
    stored: Manifest | None = None
    if wf.manifest_path and wf.manifest_path.is_file():
        try:
            stored = Manifest.load(wf.manifest_path)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[workflows] ignoring unreadable manifest {wf.manifest_path}: {e}")

    if stored is not None:
        if stored.describes(wf.graph):
            if not stored.name:
                stored.name = wf.name
            stored.mode = stored.mode or wf.mode
            # Re-merge when EasyAI has learned a new binding key, or when the
            # workflow itself has grown inputs the manifest never saw. Editing a
            # workflow to add three reference loaders and re-exporting used to
            # change nothing at all, because the original binding still resolved.
            if stored.version < MANIFEST_VERSION or _has_unbound_loaders(stored, wf.graph):
                _upgrade_manifest(stored, wf, auto_write, caps)
            return stored

        # Hand-edited manifests are never thrown away silently; the workflow is
        # flagged instead so the user can fix it in the editor.
        if not stored.autodetected and not stored.is_empty:
            broken = stored.broken_bindings(wf.graph)
            print(f"[workflows] {wf.path.name}: manifest no longer matches "
                  f"({', '.join(broken)}) - keeping your edits, needs checking")
            stored.ambiguous = sorted(set(stored.ambiguous) | set(broken))
            return stored

        print(f"[workflows] {wf.path.name}: manifest was out of date, detecting again")

    manifest = autodetect(wf.graph, mode=wf.mode, name=wf.name, caps=caps)
    if auto_write and wf.manifest_path:
        try:
            manifest.save(wf.manifest_path)
        except OSError as e:
            print(f"[workflows] could not write {wf.manifest_path}: {e}")
    return manifest


def scan(workflow_root: str | Path, mode: str, auto_write_manifest: bool = True,
         caps=None) -> list[Workflow]:
    """Every workflow in workflows/<mode>/, sorted for humans."""
    folder = Path(workflow_root) / mode
    if not folder.is_dir():
        return []

    found = [
        load_workflow(p, mode, auto_write_manifest, caps)
        for p in sorted(folder.glob("*.json"), key=_natural_key)
        if not p.name.endswith(MANIFEST_SUFFIX)
    ]
    # Usable workflows first, then by name - so a broken file never sits at the
    # top of the list a beginner sees.
    return sorted(found, key=lambda w: (not w.loadable, _natural_key(w.path)))


def scan_all(workflow_root: str | Path, auto_write_manifest: bool = True,
             caps=None) -> dict[str, list[Workflow]]:
    return {mode: scan(workflow_root, mode, auto_write_manifest, caps) for mode in MODES}


def _has_unbound_loaders(manifest: Manifest, graph: dict) -> bool:
    """Does the workflow load files the manifest does not know about?"""
    bound = {b.node for key in FILE_SLOTS for b in manifest.all(key)}
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        if _cls_is_loader(str(node.get("class_type") or "")) and str(node_id) not in bound:
            return True
    return False


def _cls_is_loader(class_type: str) -> bool:
    low = class_type.lower()
    return low in _IMAGE_LOADERS or low in _AUDIO_LOADERS or low in _VIDEO_LOADERS


def _upgrade_manifest(manifest: Manifest, wf: Workflow, auto_write: bool,
                      caps=None) -> None:
    """Fill in bindings that did not exist when this manifest was written.

    Only empty keys are touched. A workflow set up before the megapixels
    binding existed should gain the detail control, but nothing the user chose
    is allowed to move.
    """
    fresh = autodetect(wf.graph, mode=wf.mode, name=wf.name, caps=caps)
    added = []
    for key in BINDING_KEYS:
        if not manifest.all(key) and fresh.all(key):
            manifest.set(key, fresh.all(key))
            added.append(key)

    for key, label in (fresh.labels or {}).items():
        manifest.labels.setdefault(key, label)
    # Optional-ness is read from the running ComfyUI, so refresh it whenever we
    # have an answer rather than leaving a blank from an offline first load.
    if fresh.optional_slots:
        manifest.optional_slots = sorted(
            set(manifest.optional_slots or []) | set(fresh.optional_slots),
            key=FILE_SLOTS.index)
    if manifest.length_unit == "seconds" and fresh.length_unit != "seconds":
        manifest.length_unit = fresh.length_unit
    manifest.fps = manifest.fps or fresh.fps

    manifest.version = MANIFEST_VERSION
    if added:
        print(f"[workflows] {wf.path.name}: added {', '.join(added)} to its setup")
    if auto_write and wf.manifest_path:
        try:
            manifest.save(wf.manifest_path)
        except OSError as e:
            print(f"[workflows] could not update {wf.manifest_path}: {e}")


def _natural_key(path: Path):
    """Sort so workflow2 comes before workflow10."""
    import re
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(p) if p.isdigit() else p for p in parts]
