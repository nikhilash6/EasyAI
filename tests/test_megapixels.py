"""The Image tab's megapixel control.

The expected values in `test_matches_the_real_resolution_selector` were measured
against the live ComfyUI node, not derived - the node's idea of a "megapixel" is
1024 x 1024, which is why 1:1 at 1.0 comes back as exactly 1024x1024.

Run with:  python -m pytest tests/test_megapixels.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.modes import MODES
from app.ratios import (
    MEGAPIXEL_MAX, MEGAPIXEL_MIN, RATIOS, clamp_megapixels, resolve, solve_for,
)
from app.workflows.manifest import autodetect
from app.workflows.patch import GenerationRequest, apply

RESOLUTION_OPTIONS = [
    "1:1 (Square)", "2:3 (Portrait Photo)", "3:2 (Photo)",
    "3:4 (Portrait Standard)", "4:3 (Standard)",
    "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)",
]


def node(class_type, inputs, title=None):
    n = {"class_type": class_type, "inputs": inputs}
    if title:
        n["_meta"] = {"title": title}
    return n


@pytest.fixture
def chooser_graph():
    """A workflow whose size comes from a ResolutionSelector."""
    return {
        "31": node("ResolutionSelector",
                   {"aspect_ratio": "16:9 (Widescreen)", "megapixels": 0.5,
                    "multiple": 32}, "Resolution Selector"),
        "22": node("EmptyLatentImage",
                   {"width": ["31", 0], "height": ["31", 1], "batch_size": 1}),
        "5": node("PrimitiveStringMultiline", {"value": "a fox"}),
        "9": node("SaveImage", {"filename_prefix": "x", "images": ["22", 0]}),
    }


@pytest.fixture
def literal_graph():
    """A workflow that holds plain width and height numbers."""
    return {
        "4": node("EmptyLatentImage", {"width": 1024, "height": 1024, "batch_size": 1}),
        "5": node("PrimitiveStringMultiline", {"value": "a fox"}),
        "9": node("SaveImage", {"filename_prefix": "x", "images": ["4", 0]}),
    }


# --- matching the node ----------------------------------------------------
@pytest.mark.parametrize("ratio,mp,multiple,expected", [
    # Measured from the live ResolutionSelector node.
    ("2:3", 0.5, 32, (576, 896)),
    ("2:3", 0.9, 32, (800, 1184)),
    ("2:3", 1.0, 32, (832, 1248)),
    ("2:3", 2.0, 32, (1184, 1760)),
    ("16:9", 0.5, 32, (960, 544)),
    ("16:9", 0.9, 32, (1280, 736)),
    ("16:9", 1.0, 32, (1376, 768)),
    ("16:9", 2.0, 32, (1920, 1088)),
    ("1:1", 0.5, 32, (736, 736)),
    ("1:1", 0.9, 32, (960, 960)),
    ("1:1", 1.0, 32, (1024, 1024)),
    ("1:1", 2.0, 32, (1440, 1440)),
    ("2:3", 1.0, 8, (840, 1256)),
    ("2:3", 1.0, 16, (832, 1248)),
])
def test_matches_the_real_resolution_selector(ratio, mp, multiple, expected):
    assert solve_for(ratio, mp, multiple) == expected


def test_a_megapixel_means_1024_squared_to_these_nodes():
    assert solve_for("1:1", 1.0, 32) == (1024, 1024)


# --- the range ------------------------------------------------------------
def test_range_is_half_to_two():
    assert (MEGAPIXEL_MIN, MEGAPIXEL_MAX) == (0.5, 2.0)


@pytest.mark.parametrize("given,expected", [
    (0.4, 0.4),      # a workflow's own value is honoured, not rounded up
    (0.5, 0.5), (1.0, 1.0), (2.0, 2.0),
    (99.0, 8.0), (-3, 0.05),
])
def test_only_nonsense_values_are_clamped(given, expected):
    """The offered range is a UI choice; the write path only guards against
    values that would break the run."""
    assert clamp_megapixels(given) == expected


@pytest.mark.parametrize("ratio", list(RATIOS))
@pytest.mark.parametrize("mp", [0.5, 1.0, 1.5, 2.0])
def test_engine_sizes_stay_on_the_grid_at_every_budget(ratio, mp):
    w, h = resolve(ratio, "flux2", mp)
    assert w % 16 == 0 and h % 16 == 0
    assert 0.6 < (w * h) / (mp * 1_000_000) < 1.4


@pytest.mark.parametrize("ratio", ["2:3", "3:4", "16:9", "1:1"])
def test_bigger_budget_gives_a_bigger_picture(ratio):
    small = resolve(ratio, "flux2", 0.5)
    large = resolve(ratio, "flux2", 2.0)
    assert large[0] > small[0] and large[1] > small[1]


def test_the_shape_is_kept_when_the_budget_changes():
    for mp in (0.5, 1.0, 1.5, 2.0):
        w, h = resolve("2:3", "flux2", mp)
        assert abs((w / h) / (2 / 3) - 1) < 0.02, f"{mp}MP -> {w}x{h}"


def test_no_budget_given_keeps_the_engine_default():
    assert resolve("1:1", "flux2") == (1024, 1024)
    assert resolve("2:3", "flux2") == (832, 1248)


# --- patching -------------------------------------------------------------
def test_chooser_node_is_given_the_budget(chooser_graph):
    m = autodetect(chooser_graph, mode="image")
    assert m.can_set_megapixels
    patched, report = apply(
        chooser_graph, m,
        GenerationRequest(prompt="x", ratio="2:3", megapixels=2.0),
        ratio_options=RESOLUTION_OPTIONS)

    assert patched["31"]["inputs"]["megapixels"] == 2.0
    assert patched["31"]["inputs"]["aspect_ratio"] == "2:3 (Portrait Photo)"
    assert (report.width, report.height) == (1184, 1760)
    assert report.megapixels == 2.0


def test_literal_sizes_are_recomputed_for_the_budget(literal_graph):
    m = autodetect(literal_graph, mode="image")
    assert m.can_set_megapixels and m.can_set_size

    patched, report = apply(literal_graph, m,
                            GenerationRequest(prompt="x", ratio="2:3", megapixels=0.5))
    assert (patched["4"]["inputs"]["width"], patched["4"]["inputs"]["height"]) \
        == (report.width, report.height)
    assert (report.width, report.height) == resolve("2:3", m.engine, 0.5)


def test_a_chooser_and_literal_sizes_are_kept_in_step():
    """Flux 2 keeps the latent size on the chooser and the scheduler on
    literals; if they disagree the sampler draws the wrong thing."""
    graph = {
        "15": node("ResolutionSelector",
                   {"aspect_ratio": "16:9 (Widescreen)", "megapixels": 2,
                    "multiple": 32}),
        "7": node("EmptyFlux2LatentImage",
                  {"width": ["15", 0], "height": ["15", 1], "batch_size": 1}),
        "8": node("Flux2Scheduler", {"steps": 4, "width": 1024, "height": 1024}),
        "9": node("SaveImage", {"filename_prefix": "x"}),
    }
    m = autodetect(graph, mode="image")
    patched, report = apply(graph, m,
                            GenerationRequest(prompt="x", ratio="2:3", megapixels=1.0),
                            ratio_options=RESOLUTION_OPTIONS)

    expected = solve_for("2:3", 1.0, 32)
    assert (patched["8"]["inputs"]["width"], patched["8"]["inputs"]["height"]) == expected
    assert (report.width, report.height) == expected


def test_no_budget_leaves_the_node_alone(chooser_graph):
    m = autodetect(chooser_graph, mode="image")
    patched, _ = apply(chooser_graph, m, GenerationRequest(prompt="x", ratio="2:3"),
                       ratio_options=RESOLUTION_OPTIONS)
    assert patched["31"]["inputs"]["megapixels"] == 0.5    # as the author set it


# --- where the control appears --------------------------------------------
def test_picture_and_video_tabs_offer_it():
    assert MODES["image"].uses_megapixels
    assert MODES["video"].uses_megapixels
    # The Prompt Helper produces words, so it has no pixels to count.
    assert not MODES["prompt-enhancer"].uses_megapixels


def test_video_warns_sooner_than_pictures():
    """A video pays the pixel cost on every frame."""
    from app.config import Config

    cfg = Config()
    assert cfg.get("video_megapixels_warn") < cfg.get("image_megapixels_warn")


def test_an_image_workflow_that_scales_by_pixels_can_still_use_it():
    """img2img sizes itself from the input picture, but the pixel count is
    still the user's to choose."""
    graph = {
        "76": node("LoadImage", {"image": "photo.jpg"}),
        "109": node("ImageScaleToTotalPixels",
                    {"megapixels": 1, "resolution_steps": 1, "image": ["76", 0]}),
        "9": node("SaveImage", {"filename_prefix": "x"}),
    }
    m = autodetect(graph, mode="image")
    assert m.can_set_megapixels
    assert not m.can_set_ratio, "the shape comes from the supplied picture"

    patched, report = apply(graph, m, GenerationRequest(prompt="x", megapixels=1.5))
    assert patched["109"]["inputs"]["megapixels"] == 1.5
    assert report.megapixels == 1.5


# --- the workflow's own value wins ----------------------------------------
def _picker(cfg, mode_key, current=None):
    from PySide6.QtWidgets import QApplication

    from app.ui.widgets import MegapixelPicker

    QApplication.instance() or QApplication([])
    picker = MegapixelPicker()
    picker.configure(cfg, mode_key, current=current)
    return picker


def test_a_workflow_keeps_its_own_budget(tmp_path):
    """Opening a 0.4 workflow and pressing Create must not silently make it 1.0."""
    from app.config import Config

    cfg = Config(tmp_path / "settings.json")
    assert _picker(cfg, "video", current=0.4).megapixels() == 0.4
    assert _picker(cfg, "image", current=0.5).megapixels() == 0.5


def test_the_remembered_value_is_used_when_a_workflow_has_none(tmp_path):
    from app.config import Config

    cfg = Config(tmp_path / "settings.json")
    cfg.set("image_megapixels", 1.4)
    assert _picker(cfg, "image").megapixels() == 1.4


@pytest.mark.parametrize("mode_key,value,expected", [
    ("image", 1.0, False), ("image", 1.5, False), ("image", 1.6, True),
    ("video", 0.9, False), ("video", 1.0, False), ("video", 1.2, True),
])
def test_warning_thresholds_differ_by_tab(tmp_path, mode_key, value, expected):
    from app.config import Config

    picker = _picker(Config(tmp_path / "settings.json"), mode_key)
    picker.spin.setValue(value)
    assert picker.is_warning() is expected


def test_video_warning_mentions_the_per_frame_cost(tmp_path):
    from app.config import Config

    picker = _picker(Config(tmp_path / "settings.json"), "video")
    picker.spin.setValue(2.0)
    assert "frame" in picker.detail.text().lower()


# --- what the pipeline does after the size is chosen ----------------------
@pytest.mark.parametrize("engine,chosen,expected", [
    # Measured by generating and reading the file back. LTX floors both sides
    # to a multiple of 64; MiniMax passes the chooser's answer through.
    ("ltx2.5", (1376, 768), (1344, 768)),
    ("ltx2.5", (960, 544), (960, 512)),
    ("ltx2.5", (1280, 736), (1280, 704)),
    ("ltx2.5", (1024, 1024), (1024, 1024)),
    ("minimax-h3", (1376, 768), (1376, 768)),
    ("flux2", (832, 1248), (832, 1248)),
])
def test_output_grid_matches_what_the_pipeline_produces(engine, chosen, expected):
    from app.ratios import snap_to_output_grid
    assert snap_to_output_grid(chosen, engine) == expected


def test_video_sizes_shown_for_ltx_are_on_its_grid():
    from app.ratios import snap_to_output_grid

    for ratio in RATIOS:
        for mp in (0.5, 0.9, 1.0, 2.0):
            w, h = snap_to_output_grid(solve_for(ratio, mp, 32), "ltx2.5")
            assert w % 64 == 0 and h % 64 == 0, f"{ratio}@{mp} -> {w}x{h}"
