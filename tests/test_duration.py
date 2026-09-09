"""Video length detection.

Real workflows rarely expose a frame count. They compute one from a seconds
value, so the number worth putting in front of a user is upstream of the frame
count. The fixtures here mirror the two calculations actually in use.

Run with:  python -m pytest tests/test_duration.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.workflows.manifest import autodetect
from app.workflows.patch import GenerationRequest, apply


def node(class_type, inputs, title=None):
    n = {"class_type": class_type, "inputs": inputs}
    if title:
        n["_meta"] = {"title": title}
    return n


@pytest.fixture
def ltx_style():
    """LTX 2.5: frames = duration * fps + 1, over two labelled primitives."""
    return {
        "362": node("PrimitiveInt", {"value": 5}, "Duration"),
        "361": node("PrimitiveInt", {"value": 24}, "Frame Rate"),
        "378": node("ComfyMathExpression",
                    {"expression": "a * b + 1",
                     "values.a": ["362", 0], "values.b": ["361", 0]},
                    "Math Expression (length)"),
        "356": node("EmptyLTXVLatentVideo",
                    {"width": 768, "height": 512, "length": ["378", 1],
                     "batch_size": 1}),
        "9": node("SaveVideo", {"filename_prefix": "v", "video": ["356", 0]}),
    }


@pytest.fixture
def minimax_style():
    """MiniMax H3: a single Duration float inside a rounding expression."""
    return {
        "132": node("PrimitiveFloat", {"value": 5}, "Float (Duration)"),
        "131": node("ComfyMathExpression",
                    {"expression": "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17",
                     "values.a": ["132", 0]},
                    "Math Expression"),
        "136": node("MiniMaxH3ReferenceToVideo",
                    {"prompt": "a fox", "width": 1344, "height": 768,
                     "length": ["131", 1]}),
        "130": node("CreateVideo", {"fps": 24, "images": ["136", 0]}, "Create Video"),
        "92": node("SaveVideo", {"filename_prefix": "v", "video": ["130", 0]}),
    }


# --- finding the right number ---------------------------------------------
def test_ltx_duration_is_found_through_the_calculation(ltx_style):
    m = autodetect(ltx_style, mode="video")
    assert (m.get("length").node, m.get("length").input) == ("362", "value")
    assert m.length_unit == "seconds"


def test_frame_rate_is_not_mistaken_for_duration(ltx_style):
    """Both are plain ints in the same expression; only the titles separate them."""
    m = autodetect(ltx_style, mode="video")
    assert m.get("length").node != "361"
    assert m.fps == 24


def test_minimax_duration_is_found(minimax_style):
    m = autodetect(minimax_style, mode="video")
    assert (m.get("length").node, m.get("length").input) == ("132", "value")
    assert m.length_unit == "seconds"
    assert m.fps == 24


def test_image_from_batch_length_is_ignored():
    """ImageFromBatch.length means 'take N images', not 'make an N-frame video'."""
    graph = {
        "446": node("ImageFromBatch", {"batch_index": 0, "length": 1},
                    "Get Image from Batch"),
        "9": node("SaveVideo", {"filename_prefix": "v"}),
    }
    m = autodetect(graph, mode="video")
    assert not m.has("length")


def test_a_plain_frame_count_is_used_directly():
    graph = {
        "43": node("EmptyLTXVLatentVideo",
                   {"width": 768, "height": 512, "length": 97, "batch_size": 1}),
    }
    m = autodetect(graph, mode="video")
    assert (m.get("length").node, m.get("length").input) == ("43", "length")
    assert m.length_unit == "frames"


def test_length_is_offered_on_video_without_being_asked_for(ltx_style):
    m = autodetect(ltx_style, mode="video")
    assert m.is_exposed("length"), "how long the video is, is a basic question"


def test_length_stays_hidden_for_images():
    graph = {"1": node("EmptyLatentImage", {"width": 512, "height": 512, "length": 8})}
    m = autodetect(graph, mode="image")
    assert not m.is_exposed("length")


# --- writing it back ------------------------------------------------------
@pytest.mark.parametrize("seconds", [2, 5, 8, 15])
def test_seconds_are_written_to_the_duration_node(ltx_style, seconds):
    m = autodetect(ltx_style, mode="video")
    patched, _ = apply(ltx_style, m, GenerationRequest(prompt="x", length=seconds))
    assert patched["362"]["inputs"]["value"] == seconds
    # The frame rate and the calculation are left alone.
    assert patched["361"]["inputs"]["value"] == 24
    assert patched["378"]["inputs"]["expression"] == "a * b + 1"
    assert patched["356"]["inputs"]["length"] == ["378", 1]


def test_minimax_seconds_are_written(minimax_style):
    m = autodetect(minimax_style, mode="video")
    patched, _ = apply(minimax_style, m, GenerationRequest(prompt="x", length=12))
    assert patched["132"]["inputs"]["value"] == 12


def test_length_untouched_when_not_asked_for(ltx_style):
    m = autodetect(ltx_style, mode="video")
    patched, _ = apply(ltx_style, m, GenerationRequest(prompt="x"))
    assert patched["362"]["inputs"]["value"] == 5


# --- the on-screen control ------------------------------------------------
def _picker(manifest, warn_at=8, low=2, high=15):
    from PySide6.QtWidgets import QApplication

    from app.config import Config
    from app.ui.widgets import DurationPicker

    QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.set("video_length_min", low)
    cfg.set("video_length_max", high)
    cfg.set("video_length_warn", warn_at)
    cfg.set("video_length_default", 5)
    picker = DurationPicker()
    picker.configure(manifest, cfg)
    return picker


def test_picker_offers_two_to_fifteen_seconds(ltx_style):
    picker = _picker(autodetect(ltx_style, mode="video"))
    assert (picker.spin.minimum(), picker.spin.maximum()) == (2, 15)
    assert picker.spin.suffix().strip() == "seconds"


@pytest.mark.parametrize("seconds,expected", [(2, False), (8, False), (9, True), (15, True)])
def test_memory_caution_appears_past_the_threshold(ltx_style, seconds, expected):
    picker = _picker(autodetect(ltx_style, mode="video"))
    picker.spin.setValue(seconds)
    assert picker.is_warning() is expected
    if expected:
        assert "memory" in picker.warning.text().lower()


def test_seconds_go_straight_through_for_a_seconds_workflow(ltx_style):
    picker = _picker(autodetect(ltx_style, mode="video"))
    picker.spin.setValue(7)
    assert picker.value_for_workflow() == 7


def test_seconds_become_frames_for_a_frame_based_workflow():
    graph = {"43": node("EmptyLTXVLatentVideo",
                        {"width": 768, "height": 512, "length": 97})}
    m = autodetect(graph, mode="video")
    m.fps = 24
    picker = _picker(m)
    picker.spin.setValue(6)
    assert picker.value_for_workflow() == 144      # the user still typed 6 seconds
    assert picker.seconds() == 6


def test_frame_estimate_is_shown(ltx_style):
    picker = _picker(autodetect(ltx_style, mode="video"))
    picker.spin.setValue(5)
    assert "120 frames" in picker.detail.text()


# --- the repeat-result (seed lock) switch ---------------------------------
def _manifest_with_seed():
    return autodetect({"1": node("RandomNoise", {"noise_seed": 1})}, mode="video")


def _tab(tmp_path, mode="video"):
    from PySide6.QtWidgets import QApplication

    from app.comfy.client import ComfyClient
    from app.config import Config
    from app.modes import MODES
    from app.ui.tab_base import GenerateTab

    QApplication.instance() or QApplication([])
    cfg = Config(tmp_path / "settings.json")      # a fresh install
    return GenerateTab(MODES[mode], cfg, ComfyClient("127.0.0.1:1")), cfg


@pytest.fixture
def seeded_graph(ltx_style):
    graph = dict(ltx_style)
    graph["196"] = node("RandomNoise", {"noise_seed": 42})
    return graph


def test_unlocked_runs_get_a_fresh_seed_each_time(seeded_graph):
    """With no seed supplied, the patcher must roll a new one every time."""
    m = autodetect(seeded_graph, mode="video")
    seeds = {apply(seeded_graph, m, GenerationRequest(prompt="x"))[1].seed
             for _ in range(8)}
    assert len(seeds) > 1


def test_a_locked_seed_reproduces_the_same_run(seeded_graph):
    m = autodetect(seeded_graph, mode="video")
    reports = [apply(seeded_graph, m, GenerationRequest(prompt="x", seed=777))[1]
               for _ in range(3)]
    assert {r.seed for r in reports} == {777}


def test_locking_works_on_a_fresh_install(tmp_path):
    """Nothing has run yet, so there is no previous seed to repeat.

    The old code resolved that to None and quietly carried on randomising, so
    ticking the box appeared to do nothing at all.
    """
    tab, cfg = _tab(tmp_path)
    tab.seed_lock.setVisible(True)
    tab.seed_lock.setChecked(True)

    first = tab._locked_seed()
    assert first, "ticking the box must pin a real number"
    assert tab._locked_seed() == first, "and keep pinning the same one"
    assert cfg.get("locked_seeds")["video"] == first


def test_each_tab_keeps_its_own_seed(tmp_path):
    """A locked Video run must not inherit whatever an Image run left behind."""
    video, cfg = _tab(tmp_path, "video")
    video._remember_seed(1111)

    from app.modes import MODES
    from app.ui.tab_base import GenerateTab
    image = GenerateTab(MODES["image"], cfg, video.client)
    image._remember_seed(2222)

    assert cfg.get("locked_seeds") == {"video": 1111, "image": 2222}
    assert video._locked_seed() == 1111
    assert image._locked_seed() == 2222


def test_a_locked_run_does_not_move_the_remembered_seed(tmp_path):
    """Locked means locked - the next press has to repeat the same number."""
    tab, cfg = _tab(tmp_path)
    tab._manifest = _manifest_with_seed()
    tab.seed_lock.setChecked(True)
    pinned = tab._locked_seed()

    from app.jobs import JobResult
    tab._on_success(JobResult(files=[], seed=999999))

    assert cfg.get("locked_seeds")["video"] == pinned
    assert tab._locked_seed() == pinned


def test_an_unlocked_run_is_remembered_so_it_can_be_locked_afterwards(tmp_path):
    """The usual flow: like a result, then tick the box to keep exploring it."""
    tab, cfg = _tab(tmp_path)
    tab._manifest = _manifest_with_seed()
    tab.seed_lock.setChecked(False)

    from app.jobs import JobResult
    tab._on_success(JobResult(files=[], seed=4242))

    tab.seed_lock.setChecked(True)
    assert tab._locked_seed() == 4242


# --- the Set up… dialog ---------------------------------------------------
def _editor(tmp_path, graph, mode="video"):
    import json

    from PySide6.QtWidgets import QApplication

    from app.ui.manifest_editor import ManifestEditor
    from app.workflows.loader import load_workflow

    QApplication.instance() or QApplication([])
    p = tmp_path / "wf.json"
    p.write_text(json.dumps(graph), encoding="utf-8")
    return ManifestEditor(load_workflow(p, mode))


def test_setup_dialog_opens(tmp_path, minimax_style):
    """It crashed on every workflow: the label fallback ran for non-file rows too."""
    editor = _editor(tmp_path, minimax_style)
    assert editor.rows, "the dialog must list the bindings"
    editor.close()


def test_setup_dialog_shows_every_binding_and_the_file_slots(tmp_path, minimax_style):
    editor = _editor(tmp_path, minimax_style)
    for key in ("prompt", "negative", "width", "ratio", "seed", "length",
                "enhancer", "output", "image_in"):
        assert key in editor.rows, f"{key} should be editable"
    # File slots get a rename box; ordinary bindings do not.
    assert "image_in" in editor.label_edits
    assert "prompt" not in editor.label_edits
    editor.close()


def test_setup_dialog_only_offers_one_spare_file_slot(tmp_path, minimax_style):
    """Ten blank picture rows on a workflow that wants none would be noise."""
    from app.workflows.manifest import IMAGE_SLOTS

    editor = _editor(tmp_path, minimax_style)
    offered = [k for k in IMAGE_SLOTS if k in editor.rows]
    assert offered == ["image_in"], offered
    editor.close()


def test_setup_dialog_saves_a_correction(tmp_path, minimax_style):
    editor = _editor(tmp_path, minimax_style)
    editor.rows["prompt"].rows[0].node_combo.setCurrentIndex(1)
    editor.label_edits["image_in"].setText("Opening shot")
    editor._save()

    from app.workflows.manifest import Manifest, manifest_path_for
    saved = Manifest.load(manifest_path_for(editor.workflow.path))
    assert saved.autodetected is False, "a checked manifest is no longer a guess"
    assert saved.labels.get("image_in") == "Opening shot"


def test_guess_again_restores_detection(tmp_path, minimax_style):
    editor = _editor(tmp_path, minimax_style)
    original = editor.rows["prompt"].bindings()[0].node
    editor.rows["prompt"].rows[0].node_combo.setCurrentIndex(0)   # clear it
    assert editor.rows["prompt"].bindings() == []
    editor._redetect()
    assert editor.rows["prompt"].bindings()[0].node == original
    editor.close()


# --- naming a workflow ----------------------------------------------------
def test_renaming_stores_the_name_in_the_manifest(tmp_path, minimax_style):
    """The name must not touch the exported file, so a re-export keeps it."""
    import json

    from app.workflows.loader import load_workflow
    from app.workflows.manifest import Manifest, manifest_path_for

    p = tmp_path / "Minimax_h3_i2v Turbo - GarionHK.json"
    p.write_text(json.dumps(minimax_style), encoding="utf-8")

    wf = load_workflow(p, "video")
    assert wf.name == "Minimax_h3_i2v Turbo - GarionHK"

    wf.manifest.name = "Photo to video"
    wf.manifest.save(wf.manifest_path)

    again = load_workflow(p, "video")
    assert again.name == "Photo to video"
    assert p.name == "Minimax_h3_i2v Turbo - GarionHK.json", "the file is untouched"
    assert Manifest.load(manifest_path_for(p)).name == "Photo to video"


def test_a_renamed_workflow_keeps_its_name_after_re_export(tmp_path, minimax_style, ltx_style):
    """Re-exporting renumbers nodes; the friendly name should still survive."""
    import json

    from app.workflows.loader import load_workflow

    p = tmp_path / "wf.json"
    p.write_text(json.dumps(minimax_style), encoding="utf-8")
    wf = load_workflow(p, "video")
    wf.manifest.name = "My video style"
    wf.manifest.autodetected = False
    wf.manifest.save(wf.manifest_path)

    # The user re-exports: same filename, different node ids.
    p.write_text(json.dumps(ltx_style), encoding="utf-8")
    again = load_workflow(p, "video")
    assert again.name == "My video style"


def test_rename_falls_back_to_the_filename_when_cleared(tmp_path, minimax_style):
    import json

    from app.workflows.loader import load_workflow

    p = tmp_path / "some workflow.json"
    p.write_text(json.dumps(minimax_style), encoding="utf-8")
    wf = load_workflow(p, "video")
    wf.manifest.name = ""
    wf.manifest.save(wf.manifest_path)
    assert load_workflow(p, "video").name == "some workflow"
