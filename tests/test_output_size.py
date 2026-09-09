"""The shape chosen in the window must be the shape that comes out.

A picture generated at the wrong size looks like a working program, which is
why this is worth its own file: nothing errors, nothing is logged, the user
simply gets something they did not ask for.

Run with:  python -m pytest tests/test_output_size.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.ratios import PIXELS_PER_MEGAPIXEL
from app.workflows.loader import scan
from app.workflows.patch import GenerationRequest, apply as patch_apply

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / "workflows"

#: What ResolutionSelector really offers, read from a running ComfyUI 0.33.0.
OPTIONS = [
    "1:1 (Square)", "2:3 (Portrait Photo)", "3:2 (Photo)",
    "3:4 (Portrait Standard)", "4:3 (Standard)",
    "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)",
]

pytestmark = pytest.mark.skipif(not WORKFLOWS.is_dir(),
                                reason="no workflows on this machine")


def workflows(group: str):
    return [w for w in scan(WORKFLOWS, group, False, None) if w.manifest]


def chooser_size(inputs: dict) -> tuple[int, int] | None:
    """What ResolutionSelector will itself compute from its settings."""
    label, mp = inputs.get("aspect_ratio"), inputs.get("megapixels")
    multiple = int(inputs.get("multiple") or 8)
    if not isinstance(label, str) or not isinstance(mp, (int, float)):
        return None
    a, b = (float(x) for x in label.split()[0].split(":"))
    scale = (mp * PIXELS_PER_MEGAPIXEL / (a * b)) ** 0.5
    return (max(multiple, round(a * scale / multiple) * multiple),
            max(multiple, round(b * scale / multiple) * multiple))


@pytest.mark.parametrize("group", ["image", "video"])
@pytest.mark.parametrize("ratio", ["2:3", "16:9", "1:1", "9:16"])
def test_the_chooser_is_actually_told_the_ratio(group, ratio):
    """The bug this file exists for: Flux 2 Klein kept whatever shape it was
    saved with, because it has width and height inputs as well as a chooser
    and EasyAI took those to mean it could set the size itself. They belong to
    the scheduler and change nothing about the picture."""
    for wf in workflows(group):
        if not wf.manifest.has("ratio"):
            continue
        graph, _ = patch_apply(wf.graph, wf.manifest,
                               GenerationRequest(prompt="x", ratio=ratio,
                                                 megapixels=1.0),
                               ratio_options=OPTIONS)
        binding = wf.manifest.get("ratio")
        got = graph[binding.node]["inputs"][binding.input]
        assert got.split()[0] == ratio, (
            f"{wf.name}: asked for {ratio}, chooser left at {got!r}")


@pytest.mark.parametrize("group", ["image", "video"])
def test_the_size_shown_is_the_size_generated(group):
    """What the window reports and what the graph will make must agree."""
    for wf in workflows(group):
        if not wf.manifest.has("ratio"):
            continue
        for ratio, mp in (("2:3", 1.0), ("16:9", 1.0), ("1:1", 2.0)):
            graph, report = patch_apply(
                wf.graph, wf.manifest,
                GenerationRequest(prompt="x", ratio=ratio, megapixels=mp),
                ratio_options=OPTIONS)
            for node in graph.values():
                if node["class_type"] != "ResolutionSelector":
                    continue
                real = chooser_size(node["inputs"])
                if real is None:
                    continue
                assert report.width and report.height, (
                    f"{wf.name} {ratio}: nothing reported to the user")
                # LTX floors to its own grid afterwards, so the reported size
                # may be smaller - but never larger, and never a different
                # shape.
                assert report.width <= real[0] and report.height <= real[1], (
                    f"{wf.name} {ratio}: shows {report.width}x{report.height}, "
                    f"workflow makes {real[0]}x{real[1]}")


@pytest.mark.parametrize("group", ["image", "video"])
def test_the_megapixel_budget_reaches_the_chooser(group):
    for wf in workflows(group):
        if not (wf.manifest.has("ratio") and wf.manifest.has("megapixels")):
            continue
        graph, _ = patch_apply(wf.graph, wf.manifest,
                               GenerationRequest(prompt="x", ratio="1:1",
                                                 megapixels=1.5),
                               ratio_options=OPTIONS)
        binding = wf.manifest.get("megapixels")
        assert graph[binding.node]["inputs"][binding.input] == 1.5, wf.name


def test_a_workflow_with_both_a_chooser_and_literals_uses_the_chooser():
    """Flux 2 Klein exactly: the chooser feeds the latent, the width and
    height feed the scheduler. Both get written, from the same number."""
    klein = [w for w in workflows("image")
             if w.manifest.has("ratio") and w.manifest.can_set_size]
    if not klein:
        pytest.skip("no workflow of this shape installed")
    for wf in klein:
        graph, report = patch_apply(
            wf.graph, wf.manifest,
            GenerationRequest(prompt="x", ratio="9:16", megapixels=1.0),
            ratio_options=OPTIONS)

        binding = wf.manifest.get("ratio")
        assert graph[binding.node]["inputs"][binding.input].startswith("9:16")
        for slot in ("width", "height"):
            for b in wf.manifest.all(slot):
                value = graph[b.node]["inputs"][b.input]
                if isinstance(value, int):
                    expected = report.width if slot == "width" else report.height
                    assert value == expected, (
                        f"{wf.name}: {slot} literal disagrees with the chooser")


def test_a_skipped_ratio_is_reported_to_the_user():
    """Without the engine's node list the shape cannot be applied. The picture
    still generates, so this has to be said out loud."""
    for wf in workflows("image"):
        if not wf.manifest.has("ratio"):
            continue
        _, report = patch_apply(wf.graph, wf.manifest,
                                GenerationRequest(prompt="x", ratio="2:3"),
                                ratio_options=None)
        assert any("could not be applied" in s for s in report.skipped), wf.name
        return
