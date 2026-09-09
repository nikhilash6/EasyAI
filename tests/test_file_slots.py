"""Multiple picture / sound / video inputs per workflow.

The patterns here are taken from real workflow files: first-and-last-frame
video, a middle frame as well, numbered reference images, and a batch of ten
unlabelled reference pictures.

Run with:  python -m pytest tests/test_file_slots.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.workflows.manifest import (
    IMAGE_SLOTS, MAX_IMAGE_SLOTS, autodetect, slot_kind,
)
from app.workflows.patch import GenerationRequest, apply, missing_inputs


def node(class_type, inputs, title=None):
    n = {"class_type": class_type, "inputs": inputs}
    if title:
        n["_meta"] = {"title": title}
    return n


# --- fixtures modelled on real files --------------------------------------
@pytest.fixture
def first_last_frame():
    """LTX 2 FFLF: note the LAST frame node comes first in the file."""
    return {
        "47": node("LoadImage", {"image": "b.png"}, "LAST FRAME"),
        "45": node("LoadImage", {"image": "a.png"}, "FIRST FRAME"),
        "50": node("ImageResizeKJv2", {"image": ["45", 0]}),
        "51": node("ImageResizeKJv2", {"image": ["47", 0]}),
    }


@pytest.fixture
def three_frames():
    return {
        "98": node("LoadImage", {"image": "a.png"}, "First IMG"),
        "171": node("LoadImage", {"image": "m.png"}, "Middle IMG"),
        "106": node("LoadImage", {"image": "z.png"}, "End IMG"),
    }


@pytest.fixture
def numbered_refs():
    """Qwen Image Edit with five reference pictures."""
    return {
        "4": node("LoadImage", {"image": "5.png"}, "Load Image5"),
        "5": node("LoadImage", {"image": "4.png"}, "Load Image4"),
        "6": node("LoadImage", {"image": "3.png"}, "Load Image3"),
        "11": node("LoadImage", {"image": "1.png"}, "Load Image1"),
        "17": node("LoadImage", {"image": "2.png"}, "Load Image2"),
        "99": node("QwenImageIntegratedKSampler",
                   {"image": ["11", 0], "image2": ["17", 0], "image3": ["6", 0],
                    "image4": ["5", 0], "image5": ["4", 0]}),
    }


@pytest.fixture
def unlabelled_refs():
    """Flux 2 with ten reference images - no titles, identical consumers."""
    graph = {str(i): node("LoadImage", {"image": f"{i}.jpg"}) for i in range(1, 11)}
    for i in range(1, 11):
        graph[f"s{i}"] = node("ImageScaleToTotalPixels", {"image": [str(i), 0]})
    return graph


# --- ordering and labelling -----------------------------------------------
def test_first_and_last_frame_are_ordered_by_meaning_not_file_order(first_last_frame):
    """Node 47 is LAST but appears first; the words have to win."""
    m = autodetect(first_last_frame, mode="video")
    assert m.file_slots() == ["image_in", "image_in_2"]
    assert m.get("image_in").node == "45"
    assert m.get("image_in_2").node == "47"
    assert m.label_for("image_in") == "FIRST FRAME"
    assert m.label_for("image_in_2") == "LAST FRAME"


def test_middle_frame_sorts_between_first_and_last(three_frames):
    m = autodetect(three_frames, mode="video")
    assert [m.get(k).node for k in m.file_slots()] == ["98", "171", "106"]
    assert [m.label_for(k) for k in m.file_slots()] == \
        ["First IMG", "Middle IMG", "End IMG"]


def test_numbered_references_sort_numerically(numbered_refs):
    m = autodetect(numbered_refs, mode="image")
    assert [m.get(k).node for k in m.file_slots()] == ["11", "17", "6", "5", "4"]
    assert m.label_for("image_in") == "Load Image 1"
    assert m.label_for("image_in_5") == "Load Image 5"


def test_unlabelled_references_fall_back_to_position(unlabelled_refs):
    m = autodetect(unlabelled_refs, mode="image")
    assert len(m.file_slots()) == 10
    assert m.label_for("image_in") == "Picture 1"
    assert m.label_for("image_in_10") == "Picture 10"


def test_single_picture_is_not_numbered():
    m = autodetect({"1": node("LoadImage", {"image": "a.png"})}, mode="image")
    assert m.file_slots() == ["image_in"]
    assert m.label_for("image_in") == "Picture"


def test_more_loaders_than_slots_is_flagged():
    graph = {str(i): node("LoadImage", {"image": f"{i}.png"})
             for i in range(MAX_IMAGE_SLOTS + 3)}
    m = autodetect(graph, mode="image")
    assert len(m.file_slots()) == MAX_IMAGE_SLOTS
    assert "image_in" in m.ambiguous, "the user should be told some were dropped"


def test_consumer_input_name_labels_when_there_is_no_title():
    graph = {
        "1": node("LoadImage", {"image": "a.png"}),
        "2": node("LoadImage", {"image": "b.png"}),
        "3": node("WanImageToVideo", {"start_image": ["1", 0], "end_image": ["2", 0]}),
    }
    m = autodetect(graph, mode="video")
    assert m.label_for("image_in") == "Start image"
    assert m.label_for("image_in_2") == "End image"
    assert m.get("image_in").node == "1"


def test_pictures_sound_and_video_get_separate_families():
    graph = {
        "1": node("LoadImage", {"image": "a.png"}),
        "2": node("LoadAudio", {"audio": "a.mp3"}),
        "3": node("VHS_LoadVideo", {"video": "a.mp4"}),
    }
    m = autodetect(graph, mode="video")
    assert m.file_slots("image") == ["image_in"]
    assert m.file_slots("audio") == ["audio_in"]
    assert m.file_slots("video") == ["video_in"]
    assert slot_kind("image_in_4") == "image"


def test_linked_loader_input_is_ignored():
    """A LoadImage whose filename is wired from elsewhere is not a user input."""
    graph = {
        "1": node("LoadImage", {"image": ["9", 0]}),
        "2": node("LoadImage", {"image": "real.png"}),
    }
    m = autodetect(graph, mode="image")
    assert m.file_slots() == ["image_in"]
    assert m.get("image_in").node == "2"


# --- patching -------------------------------------------------------------
def test_each_slot_gets_its_own_file(first_last_frame):
    m = autodetect(first_last_frame, mode="video")
    request = GenerationRequest(
        prompt="x",
        uploaded={"image_in": "uploaded_first.png", "image_in_2": "uploaded_last.png"},
    )
    patched, report = apply(first_last_frame, m, request)

    assert patched["45"]["inputs"]["image"] == "uploaded_first.png"
    assert patched["47"]["inputs"]["image"] == "uploaded_last.png"
    assert "image_in" in report.applied and "image_in_2" in report.applied


def test_ten_reference_pictures_all_land(unlabelled_refs):
    m = autodetect(unlabelled_refs, mode="image")
    uploaded = {slot: f"up{i}.png" for i, slot in enumerate(m.file_slots(), start=1)}
    patched, _ = apply(unlabelled_refs, m, GenerationRequest(prompt="x", uploaded=uploaded))
    for i in range(1, 11):
        assert patched[str(i)]["inputs"]["image"] == f"up{i}.png"


def test_missing_picture_is_reported_per_slot(first_last_frame):
    m = autodetect(first_last_frame, mode="video")
    request = GenerationRequest(prompt="x", files={"image_in": __file__})
    problems = missing_inputs(m, request)
    assert len(problems) == 1
    assert "LAST FRAME" in problems[0]


def test_all_pictures_supplied_gives_no_complaint(first_last_frame):
    m = autodetect(first_last_frame, mode="video")
    request = GenerationRequest(prompt="x",
                                files={"image_in": __file__, "image_in_2": __file__})
    assert missing_inputs(m, request) == []


def test_convenience_image_path_maps_to_the_first_slot():
    request = GenerationRequest(prompt="x")
    request.image_path = "a.png"
    assert request.files["image_in"] == "a.png"
    assert request.image_path == "a.png"
    request.image_path = None
    assert "image_in" not in request.files


# --- round trip -----------------------------------------------------------
def test_labels_survive_save_and_load(tmp_path, first_last_frame):
    from app.workflows.manifest import Manifest

    m = autodetect(first_last_frame, mode="video", name="fflf")
    m.labels["image_in"] = "Opening shot"
    path = tmp_path / "fflf.manifest.json"
    m.save(path)

    again = Manifest.load(path)
    assert again.label_for("image_in") == "Opening shot"
    assert again.label_for("image_in_2") == "LAST FRAME"
    assert again.get("image_in_2").node == "47"
