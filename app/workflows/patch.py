"""Writes the user's choices into a copy of the workflow graph.

Only inputs named by the manifest are touched, and only on a deep copy - the
loaded workflow is never mutated, so a failed run leaves nothing behind.

This replaces the old approach of scanning for a class_type and writing to every
node that matched, which quietly corrupted workflows with more than one
LoadImage.
"""
from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.i18n import t
from app.ratios import (
    clamp_megapixels, read_node_profile, resolve, snap_to_output_grid, solve_for,
)
from app.workflows.manifest import (
    FILE_SLOTS, Binding, Manifest, loader_consumers,
)

# ComfyUI seeds are unsigned 64-bit, but most nodes validate against this range.
MAX_SEED = 2 ** 53 - 1


class PatchError(Exception):
    """A binding pointed at a node or input that isn't in the graph."""


@dataclass
class GenerationRequest:
    """Everything the user chose for one run."""
    prompt: str = ""
    negative: str | None = None
    ratio: str | None = None
    #: A different model from the same folder, or None to keep the one the
    #: workflow names. Only ever a value the engine offered.
    model: str | None = None
    #: Output pixel budget in megapixels. None keeps the workflow's own.
    megapixels: float | None = None
    #: Local paths keyed by file slot, e.g. {"image_in": "first.png",
    #: "image_in_2": "last.png"}. A workflow can ask for several pictures.
    files: dict[str, str | Path] = field(default_factory=dict)
    length: int | None = None
    batch: int | None = None
    seed: int | None = None          # None means "pick a fresh one"
    #: None = leave the workflow as its author set it up; False = route around
    #: the prompt enhancer; True = use it.
    enhance: bool | None = None
    filename_prefix: str | None = None
    #: Filled in by the job runner after uploading, keyed by the same slots.
    uploaded: dict[str, str] = field(default_factory=dict)

    # Convenience for the single-input case and for older callers.
    @property
    def image_path(self):
        return self.files.get("image_in")

    @image_path.setter
    def image_path(self, value):
        self._set_slot("image_in", value)

    @property
    def audio_path(self):
        return self.files.get("audio_in")

    @audio_path.setter
    def audio_path(self, value):
        self._set_slot("audio_in", value)

    @property
    def video_path(self):
        return self.files.get("video_in")

    @video_path.setter
    def video_path(self, value):
        self._set_slot("video_in", value)

    def _set_slot(self, key: str, value) -> None:
        if value:
            self.files[key] = value
        else:
            self.files.pop(key, None)


@dataclass
class PatchReport:
    """What actually changed, for logging and for the progress panel."""
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    #: Whether the prompt enhancer ran, or None if the workflow has none.
    enhancer: bool | None = None
    megapixels: float | None = None
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _set(graph: dict, bindings, value, report: PatchReport,
         key: str, strict: bool = False) -> bool:
    """Write one value through every target of a binding key.

    Returns True if at least one write landed. Several targets is normal - a
    Flux 2 graph needs the same width in both the empty latent and the
    scheduler.
    """
    if bindings is None:
        return False
    if isinstance(bindings, Binding):
        bindings = [bindings]

    landed = False
    for binding in bindings:
        landed |= _set_one(graph, binding, value, report, key, strict)
    return landed


def _set_one(graph: dict, binding: Binding | None, value, report: PatchReport,
             key: str, strict: bool = False) -> bool:
    if binding is None:
        return False

    node = graph.get(binding.node)
    if not isinstance(node, dict):
        message = f"{key}: node {binding.node} is not in this workflow"
        if strict:
            raise PatchError(message)
        report.skipped.append(message)
        return False

    inputs = node.setdefault("inputs", {})
    if not binding.input:
        report.skipped.append(f"{key}: no input field set")
        return False
    if binding.input not in inputs:
        message = f"{key}: '{binding.input}' is not an input on node {binding.node}"
        if strict:
            raise PatchError(message)
        report.skipped.append(message)
        return False
    if isinstance(inputs[binding.input], list):
        # Wired from another node - overwriting would break the graph.
        report.skipped.append(
            f"{key}: '{binding.input}' is connected to another node, left alone")
        return False

    inputs[binding.input] = value
    report.applied.append(key)
    return True


def _ratio_head(option: str) -> str:
    """'9:16 (Portrait Widescreen)' -> '9:16'."""
    return option.split(" ", 1)[0].strip()


def match_ratio_option(ratio: str, options: list[str] | None, current: str) -> str | None:
    """Pick the node's own wording for a ratio, e.g. '2:3 (Portrait Photo)'.

    The label is never invented. These nodes accept a fixed list of strings and
    the descriptive half varies per entry - Square, Portrait Photo, Ultrawide -
    so guessing produces a value ComfyUI rejects. When the list is unknown, or
    the ratio simply isn't offered, None is returned and the node is left as the
    workflow author set it.
    """
    if options:
        for option in options:
            if _ratio_head(option) == ratio:
                return option
        return None

    # No list to check against: only safe if the node already holds a bare
    # ratio with no descriptive suffix.
    if current and _ratio_head(current) == current and ":" in current:
        return ratio
    return None


def prune_unused_files(graph: dict, manifest: Manifest, request: GenerationRequest,
                       report: PatchReport) -> None:
    """Remove the inputs for optional files the user chose not to supply.

    Declining to write a value is not enough. The workflow still holds the
    filename its author used - '李佳芯 Original.jpeg' - which exists on their
    machine and nobody else's, so the run would fail on a missing file. The
    input has to leave the graph entirely, which the node allows because these
    reference groups are declared optional.
    """
    touched_groups: set[tuple[str, str]] = set()

    for key in manifest.file_slots():
        if request.files.get(key) or not manifest.is_optional(key):
            continue

        for binding in manifest.all(key):
            for consumer_id, input_name in loader_consumers(graph, binding.node):
                consumer = graph.get(consumer_id)
                if not isinstance(consumer, dict):
                    continue
                if (consumer.get("inputs") or {}).pop(input_name, None) is not None:
                    if "." in input_name:
                        touched_groups.add((consumer_id, input_name.split(".", 1)[0]))

            # Drop the loader too, once nothing reads from it, so ComfyUI does
            # not try to open a file that is not there.
            if not loader_consumers(graph, binding.node):
                graph.pop(binding.node, None)

        report.skipped.append(f"{key}: not supplied, removed from the workflow")

    for consumer_id, group in sorted(touched_groups):
        _renumber_group(graph, consumer_id, group)


def _renumber_group(graph: dict, consumer_id: str, group: str) -> None:
    """Close the gaps in an auto-growing input group.

    These groups are numbered from a prefix - ref_image_0, ref_image_1 - and
    removing the middle one leaves a hole. ComfyUI accepts the hole, but the
    node reads the group by index, so renumbering keeps the remaining files in
    the positions the node expects rather than relying on that.
    """
    inputs = (graph.get(consumer_id) or {}).get("inputs") or {}
    members = [k for k in inputs if k.startswith(f"{group}.")]
    if not members:
        return

    def index_of(name: str) -> int:
        digits = re.findall(r"\d+", name.rsplit(".", 1)[-1])
        return int(digits[-1]) if digits else 0

    members.sort(key=index_of)
    values = [inputs.pop(name) for name in members]
    stem = re.sub(r"\d+$", "", members[0].split(".", 1)[1])
    for position, value in enumerate(values):
        inputs[f"{group}.{stem}{position}"] = value


def bypass_enhancer(graph: dict, manifest: Manifest, prompt_text: str,
                    report: PatchReport) -> bool:
    """Route the prompt around the enhancer node and drop it from the graph.

    An API workflow has no notion of a muted node - ComfyUI strips those at
    export - so switching the enhancer off means rewiring by hand: every input
    that reads from the enhancer is pointed at whatever fed the enhancer
    instead, and the node is then removed so its language model never loads.

    Returns True if the prompt was written inline while rewiring, in which case
    the caller must not write it again through a now-deleted node.
    """
    binding = manifest.get("enhancer")
    if binding is None:
        return False
    node = graph.get(binding.node)
    if not isinstance(node, dict):
        return False

    # What feeds the enhancer? A link if the prompt comes from another node,
    # otherwise the user's own words.
    upstream = (node.get("inputs") or {}).get(binding.input) if binding.input else None
    inlined = not isinstance(upstream, list)
    replacement = upstream if isinstance(upstream, list) else prompt_text

    rewired = 0
    for other_id, other in graph.items():
        if other_id == binding.node or not isinstance(other, dict):
            continue
        for field_name, value in list((other.get("inputs") or {}).items()):
            if isinstance(value, list) and value and str(value[0]) == binding.node:
                other["inputs"][field_name] = replacement
                rewired += 1

    graph.pop(binding.node, None)
    report.applied.append("enhancer:off")
    if rewired == 0:
        report.skipped.append(
            "enhancer: nothing was reading from it, so nothing needed rerouting")
    return inlined


def _target_size(patched: dict, manifest: Manifest, request: GenerationRequest,
                 report: PatchReport, ratio_options: list[str] | None,
                 ) -> tuple[tuple[int, int] | None, bool]:
    """Work out the pixels this run should produce, setting the chooser node.

    When the workflow has a size-chooser node, that node decides - so it is
    told the ratio and then asked, in effect, what it will produce. Otherwise
    the engine table answers.

    Returns (size, chooser_was_set).
    """
    if manifest.has("ratio"):
        binding = manifest.get("ratio")
        node = patched.get(binding.node) or {}
        current = str((node.get("inputs") or {}).get(binding.input, ""))
        option = match_ratio_option(request.ratio, ratio_options, current)
        if option is None:
            # Either the engine's node list was unreadable, or the chooser
            # genuinely has no such shape. Either way the picture comes out at
            # the workflow's own setting, which is not what was asked for -
            # so this has to reach the user, not just a log nobody sees.
            report.skipped.append(t(
                "The shape stayed at this workflow's own setting: {ratio} could "
                "not be applied. Check the AI engine is running, then press "
                "Refresh.", ratio=request.ratio))
            return None, False
        applied = _set(patched, [binding], option, report, "ratio")

        # read_node_profile sees the megapixels already written above.
        profile = read_node_profile(node)
        if profile:
            return (snap_to_output_grid(solve_for(request.ratio, *profile),
                                        manifest.engine), applied)

    size = resolve(request.ratio, manifest.engine, megapixels=request.megapixels)
    return snap_to_output_grid(size, manifest.engine), False


def apply(graph: dict, manifest: Manifest, request: GenerationRequest,
          ratio_options: list[str] | None = None) -> tuple[dict, PatchReport]:
    """Return a patched deep copy of the graph plus a report of what changed.

    ``ratio_options`` is the list a size-chooser node accepts, read from the
    running ComfyUI. Without it a ratio node is left alone rather than written
    with a made-up label.
    """
    patched = copy.deepcopy(graph)
    report = PatchReport()

    # --- prompt enhancer --------------------------------------------------
    # Done first, because switching it off can remove the very node the prompt
    # would otherwise be written into.
    prompt_already_written = False
    if manifest.has("enhancer"):
        if request.enhance is False:
            prompt_already_written = bypass_enhancer(
                patched, manifest, request.prompt or "", report)
            report.enhancer = False
        else:
            report.enhancer = True
            report.applied.append("enhancer:on")

    # --- text -------------------------------------------------------------
    if request.prompt is not None and not prompt_already_written:
        _set(patched, manifest.all("prompt"), request.prompt, report, "prompt")
    if request.negative is not None:
        _set(patched, manifest.all("negative"), request.negative, report, "negative")

    # --- input files (already uploaded, so these are ComfyUI-side names) ---
    # Each slot is a separate picture/sound, so each gets its own value.
    for key in FILE_SLOTS:
        name = request.uploaded.get(key)
        if name:
            _set(patched, manifest.all(key), name, report, key)

    # Anything optional the user skipped is taken out of the graph rather than
    # left pointing at a file only the workflow's author has.
    prune_unused_files(patched, manifest, request, report)

    # --- the model --------------------------------------------------------
    # Done before the size, because a workflow may size itself from whichever
    # model is loaded.
    if request.model and manifest.has("model"):
        _set(patched, manifest.all("model"), request.model, report, "model")

    # --- size -------------------------------------------------------------
    # A megapixels input means the workflow sizes itself from a pixel budget,
    # so hand it the number and let it do the arithmetic. Done first, so the
    # size worked out below reflects the new budget.
    if request.megapixels is not None and manifest.has("megapixels"):
        budget = clamp_megapixels(request.megapixels)
        if _set(patched, manifest.all("megapixels"), budget, report, "megapixels"):
            report.megapixels = budget

    if request.ratio:
        target, chooser_set = _target_size(
            patched, manifest, request, report, ratio_options)
        if target:
            # Write the result into every literal width/height as well. A
            # workflow can have both a chooser node and a node holding plain
            # numbers - Flux 2 keeps the latent size on the chooser but the
            # scheduler on literals - and if the two disagree the sampler is
            # scheduled for a different picture than it is asked to draw.
            wrote = _set(patched, manifest.all("width"), int(target[0]), report, "width")
            wrote |= _set(patched, manifest.all("height"), int(target[1]), report, "height")
            # Only claim a size when something will actually act on it. Width
            # and height wired from another node are left alone, and reporting
            # our number would promise a picture the workflow will not make.
            if chooser_set or wrote:
                report.width, report.height = int(target[0]), int(target[1])

    # --- seed -------------------------------------------------------------
    # A workflow ships with a fixed seed, so without this every user gets an
    # identical result. Randomise unless the user asked to lock it.
    seed = request.seed if request.seed is not None else random.randint(0, MAX_SEED)
    if _set(patched, manifest.all("seed"), int(seed), report, "seed"):
        report.seed = int(seed)

    # --- optional extras --------------------------------------------------
    if request.length is not None:
        _set(patched, manifest.all("length"), int(request.length), report, "length")
    if request.batch is not None:
        _set(patched, manifest.all("batch"), int(request.batch), report, "batch")
    if request.filename_prefix:
        _set(patched, manifest.all("output"), request.filename_prefix, report, "output")

    return patched, report


def missing_inputs(manifest: Manifest, request: GenerationRequest) -> list[str]:
    """User-facing complaints to raise before queueing anything."""
    problems = []
    if manifest.has("prompt") and not (request.prompt or "").strip():
        problems.append("Please type a prompt first.")

    supplied = 0
    for key in manifest.file_slots():
        label = manifest.label_for(key)
        path = request.files.get(key)
        if path:
            supplied += 1
            if not Path(path).is_file():
                problems.append(f"“{label}” no longer exists:\n{path}")
        elif not manifest.is_optional(key):
            # A required loader cannot be left empty - the filename baked into
            # the workflow points at the author's machine, not the user's.
            problems.append(f"Please choose a file for “{label}”.")

    # Reference workflows let you pick and choose, but running one with nothing
    # attached is almost never what someone meant, so ask for one.
    if supplied == 0 and manifest.has_optional_files:
        problems.append("Please attach at least one file to work from.")
    return problems
