"""Pixel maths checks for app/ratios.py.

Run with:  python -m pytest tests/test_ratios.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.ratios import ENGINES, LADDERS, RATIOS, get_engine, resolve, solve

IMAGE_ENGINES = [k for k, e in ENGINES.items() if not e.is_video]
VIDEO_ENGINES = [k for k, e in ENGINES.items() if e.is_video]


@pytest.mark.parametrize("engine_key", IMAGE_ENGINES)
@pytest.mark.parametrize("ratio", list(RATIOS))
def test_image_sizes_are_on_the_grid(engine_key, ratio):
    engine = get_engine(engine_key)
    w, h = resolve(ratio, engine_key)
    assert w % engine.multiple == 0, f"{ratio}@{engine_key} -> {w} not /{engine.multiple}"
    assert h % engine.multiple == 0, f"{ratio}@{engine_key} -> {h} not /{engine.multiple}"
    assert engine.min_side <= w <= engine.max_side
    assert engine.min_side <= h <= engine.max_side


@pytest.mark.parametrize("engine_key", IMAGE_ENGINES)
@pytest.mark.parametrize("ratio", list(RATIOS))
def test_image_ratio_error_under_two_percent(engine_key, ratio):
    rw, rh = RATIOS[ratio]
    w, h = resolve(ratio, engine_key)
    err = abs((w / h) / (rw / rh) - 1.0)
    assert err < 0.02, f"{ratio}@{engine_key} -> {w}x{h} is {err:.3%} off"


@pytest.mark.parametrize("engine_key", IMAGE_ENGINES)
@pytest.mark.parametrize("ratio", list(RATIOS))
def test_image_area_near_budget(engine_key, ratio):
    engine = get_engine(engine_key)
    w, h = resolve(ratio, engine_key)
    ratio_of_target = (w * h) / (engine.megapixels * 1_000_000)
    # Extreme ratios can't hit the budget exactly on a coarse grid; 35% is the
    # widest drift the current tables produce.
    assert 0.65 < ratio_of_target < 1.35, f"{ratio}@{engine_key} -> {w}x{h}"


@pytest.mark.parametrize("engine_key", VIDEO_ENGINES)
@pytest.mark.parametrize("ratio", list(RATIOS))
def test_every_video_ratio_has_a_ladder_entry(engine_key, ratio):
    ladder = LADDERS.get(engine_key)
    assert ladder is not None, f"video engine {engine_key} has no ladder"
    assert ratio in ladder, f"{engine_key} ladder is missing {ratio}"
    w, h = ladder[ratio]
    assert w % 32 == 0 and h % 32 == 0, f"{engine_key} {ratio} -> {w}x{h} not /32"


@pytest.mark.parametrize("engine_key", VIDEO_ENGINES)
@pytest.mark.parametrize("ratio", list(RATIOS))
def test_video_sizes_within_engine_bounds(engine_key, ratio):
    engine = get_engine(engine_key)
    w, h = resolve(ratio, engine_key)
    assert engine.min_side <= w <= engine.max_side, f"{engine_key} {ratio} w={w}"
    assert engine.min_side <= h <= engine.max_side, f"{engine_key} {ratio} h={h}"


def test_known_good_flux_sizes():
    """The sizes agreed in the plan, pinned so a solver tweak can't drift them."""
    assert resolve("2:3", "flux2") == (832, 1248)
    assert resolve("3:2", "flux2") == (1248, 832)
    assert resolve("3:4", "flux2") == (864, 1152)
    assert resolve("4:3", "flux2") == (1152, 864)
    assert resolve("9:16", "flux2") == (720, 1280)
    assert resolve("16:9", "flux2") == (1280, 720)
    assert resolve("1:1", "flux2") == (1024, 1024)   # override, not solver


def test_portrait_and_landscape_are_mirrors():
    for engine_key in list(IMAGE_ENGINES) + list(VIDEO_ENGINES):
        for tall, wide in (("2:3", "3:2"), ("3:4", "4:3"), ("9:16", "16:9"), ("9:21", "21:9")):
            w1, h1 = resolve(tall, engine_key)
            w2, h2 = resolve(wide, engine_key)
            assert (w1, h1) == (h2, w2), f"{engine_key}: {tall} vs {wide} not mirrored"


def test_unknown_engine_falls_back_to_default():
    assert resolve("1:1", "no-such-model") == resolve("1:1", "_default")
    assert resolve("2:3", None) == resolve("2:3", "_default")


def test_unknown_ratio_falls_back_to_square():
    assert resolve("7:13", "flux2") == resolve("1:1", "flux2")


def test_solver_is_deterministic():
    for _ in range(3):
        assert solve("2:3", get_engine("flux2")) == (832, 1248)
