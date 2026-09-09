"""The redesigned controls.

Covers the pieces that are easy to break silently: the chip row rebuilding
cleanly, the slider staying in step with the value it reports, and the theme
resolving a usable font when the designed one is not installed.

Run with:  python -m pytest tests/test_redesign.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PySide6.QtWidgets import QApplication

from app.config import Config
from app.ui import theme
from app.ui.widgets import MegapixelPicker, RatioPicker, Switch


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


# --- theme -----------------------------------------------------------------
def test_a_font_is_always_resolved():
    """Manrope is not on a stock Windows box; something usable must be picked."""
    assert theme.UI_FONT and theme.MONO_FONT
    assert theme.UI_FONT != theme.MONO_FONT


def test_the_stylesheet_uses_the_resolved_fonts():
    assert f'"{theme.UI_FONT}"' in theme.STYLESHEET
    assert f'"{theme.MONO_FONT}"' in theme.STYLESHEET


def test_the_accent_is_the_designed_orange():
    assert theme.ACCENT == "#ff6b1a"
    assert theme.ACCENT in theme.STYLESHEET


# --- shape chips -----------------------------------------------------------
def test_every_ratio_gets_a_chip():
    picker = RatioPicker()
    picker.set_engine("flux2")
    assert [c.text() for c in picker._chips.values()] == \
        ["1:1", "4:3", "3:4", "3:2", "2:3", "16:9", "9:16", "21:9", "9:21"]


def test_only_one_chip_is_ever_checked():
    picker = RatioPicker()
    picker.set_engine("flux2")
    picker.set_ratio("9:16")
    checked = [r for r, c in picker._chips.items() if c.isChecked()]
    assert checked == ["9:16"]
    assert picker.current_ratio() == "9:16"


def test_rebuilding_leaves_no_ghost_chips():
    """The old chips have to be unparented, not just scheduled for deletion -
    otherwise they stay painted behind the new row."""
    picker = RatioPicker()
    picker.set_engine("flux2")
    picker.set_allowed(["1:1", "16:9"])
    live = [c for c in picker._chip_holder.children() if isinstance(c, type(
        next(iter(picker._chips.values()))))]
    assert len(live) == 2, [c.text() for c in live]


def test_restricting_the_list_keeps_a_valid_choice():
    picker = RatioPicker()
    picker.set_engine("flux2")
    picker.set_ratio("9:21")
    picker.set_allowed(["1:1", "16:9"])          # 9:21 is gone now
    assert picker.current_ratio() in ("1:1", "16:9")


def test_the_pixel_line_follows_the_chosen_shape():
    picker = RatioPicker()
    picker.set_engine("flux2")
    picker.set_ratio("2:3")
    assert picker.detail.text() == "832 × 1248 px"
    picker.set_ratio("1:1")
    assert picker.detail.text() == "1024 × 1024 px"


def test_a_disabled_picker_explains_itself():
    picker = RatioPicker()
    picker.set_engine("flux2")
    picker.set_enabled_with_reason(False, "size comes from your picture")
    assert picker.detail.text() == "size comes from your picture"
    assert not picker.isEnabled()


# --- detail slider ---------------------------------------------------------
def test_slider_and_reported_value_agree(tmp_path):
    picker = MegapixelPicker()
    picker.configure(Config(tmp_path / "s.json"), "image")
    picker.slider.setValue(150)
    assert picker.megapixels() == 1.5
    assert picker.value_label.text() == "1.5 MP"


def test_setting_a_value_moves_the_slider(tmp_path):
    picker = MegapixelPicker()
    picker.configure(Config(tmp_path / "s.json"), "image")
    picker.set_megapixels(0.7)
    assert picker.slider.value() == 70
    assert picker.megapixels() == 0.7


def test_the_hidden_spin_box_stays_in_step(tmp_path):
    """Kept for anything still driving the control through .spin."""
    picker = MegapixelPicker()
    picker.configure(Config(tmp_path / "s.json"), "image")
    picker.spin.setValue(1.8)
    assert picker.megapixels() == 1.8
    picker.slider.setValue(60)
    assert picker.spin.value() == pytest.approx(0.6)


def test_a_workflow_below_the_floor_widens_the_slider(tmp_path):
    picker = MegapixelPicker()
    picker.configure(Config(tmp_path / "s.json"), "video", current=0.4)
    assert picker.slider.minimum() == 40
    assert picker.megapixels() == 0.4


# --- switch ----------------------------------------------------------------
def test_the_switch_is_still_a_checkbox():
    switch = Switch("Repeat")
    assert not switch.isChecked()
    switch.setChecked(True)
    assert switch.isChecked()

    seen = []
    switch.toggled.connect(seen.append)
    switch.setChecked(False)
    assert seen == [False]


# --- the icon pack ---------------------------------------------------------
def test_the_icon_carries_every_size_windows_asks_for():
    """A single-resolution .ico looks rough in the taskbar; Windows wants a set."""
    from PIL import Image

    ico = theme.ICON_DIR / "EasyAI.ico"
    assert ico.is_file(), "run tools/make_icons.py"
    with Image.open(ico) as im:
        sizes = {w for w, _ in im.info["sizes"]}
    assert {16, 20, 24, 32, 48, 256}.issubset(sizes), sorted(sizes)


def test_the_icon_has_no_wasted_padding():
    """The master has a transparent margin; shipping it shrinks the artwork."""
    from PIL import Image

    with Image.open(theme.ICON_DIR / "EasyAI-256.png") as im:
        im = im.convert("RGBA")
        box = im.split()[3].getbbox()
    assert box[0] <= 2 and box[1] <= 2, f"left/top padding: {box}"
    assert box[2] >= im.width - 2, f"right padding: {box}"


def test_the_app_icon_loads():
    icon = theme.app_icon()
    assert not icon.isNull()
    widths = {s.width() for s in icon.availableSizes()}
    assert {16, 32, 256}.issubset(widths), sorted(widths)
