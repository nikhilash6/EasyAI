"""File inputs a workflow will run without.

Modelled on the real MiniMax H3 Reference workflow: three picture loaders, one
audio loader, and one video loader that feeds two inputs at once (the footage
and its soundtrack). All five feed reference groups the node declares optional.

Run with:  python -m pytest tests/test_optional_inputs.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.comfy.objectinfo import Capabilities
from app.workflows.loader import load_workflow
from app.workflows.manifest import autodetect
from app.workflows.patch import GenerationRequest, apply, missing_inputs

REAL_FILE = str(Path(__file__).resolve())


def node(class_type, inputs, title=None):
    n = {"class_type": class_type, "inputs": inputs}
    if title:
        n["_meta"] = {"title": title}
    return n


@pytest.fixture
def reference_graph():
    return {
        "137": node("LoadImage", {"image": "author.jpeg"}),
        "619": node("LoadImage", {"image": "author.jpeg"}),
        "620": node("LoadImage", {"image": "author.jpeg"}),
        "621": node("LoadAudio", {"audio": "author.mov"}),
        "623": node("VHS_LoadVideo", {"video": "author.mov", "force_rate": 0}),
        "136": node("MiniMaxH3ReferenceToVideo", {
            "prompt": "a portrait", "width": 1344, "height": 768, "length": 124,
            "ref_image_size": "match",
            "ref_images.ref_image_0": ["137", 0],
            "ref_images.ref_image_1": ["619", 0],
            "ref_images.ref_image_2": ["620", 0],
            "ref_videos.ref_video_0": ["623", 0],
            "ref_video_audios.ref_video_audio_0": ["623", 2],
            "ref_audios.ref_audio_0": ["621", 0],
        }),
        "92": node("SaveVideo", {"filename_prefix": "v", "video": ["136", 0]}),
    }


@pytest.fixture
def caps():
    """MiniMaxH3ReferenceToVideo as the running ComfyUI describes it."""
    return Capabilities(
        node_types={"MiniMaxH3ReferenceToVideo", "LoadImage", "LoadAudio",
                    "VHS_LoadVideo", "SaveVideo"},
        raw={"MiniMaxH3ReferenceToVideo": {"input": {
            "required": {"prompt": ["STRING", {}], "width": ["INT", {}],
                         "height": ["INT", {}], "length": ["INT", {}],
                         "ref_image_size": ["COMBO", {"options": ["match"]}]},
            "optional": {"ref_images": ["COMFY_AUTOGROW_V3", {}],
                         "ref_videos": ["COMFY_AUTOGROW_V3", {}],
                         "ref_video_audios": ["COMFY_AUTOGROW_V3", {}],
                         "ref_audios": ["COMFY_AUTOGROW_V3", {}]},
        }}},
        available=True,
    )


@pytest.fixture
def manifest(reference_graph, caps):
    return autodetect(reference_graph, mode="video", caps=caps)


def run(graph, manifest, slots):
    request = GenerationRequest(
        prompt="a portrait",
        files={k: REAL_FILE for k in slots},
        uploaded={k: f"{k}.bin" for k in slots})
    patched, report = apply(graph, manifest, request)
    refs = {k: v for k, v in patched["136"]["inputs"].items() if k.startswith("ref_")}
    return patched, refs, report


# --- knowing which are optional -------------------------------------------
def test_autogrow_groups_are_recognised_as_optional(manifest):
    assert manifest.file_slots() == [
        "image_in", "image_in_2", "image_in_3", "audio_in", "video_in"]
    for key in manifest.file_slots():
        assert manifest.is_optional(key), key
    assert manifest.has_optional_files


def test_optionality_is_read_from_the_group_not_the_numbered_entry(caps):
    """The node declares 'ref_images'; the graph says 'ref_images.ref_image_1'."""
    assert caps.is_optional_input("MiniMaxH3ReferenceToVideo",
                                  "ref_images.ref_image_1")
    assert not caps.is_optional_input("MiniMaxH3ReferenceToVideo", "prompt")
    assert not caps.is_optional_input("MiniMaxH3ReferenceToVideo", "width")


def test_a_required_input_keeps_its_file_mandatory(caps):
    """A first-frame picture feeding a required input is not optional."""
    graph = {
        "1": node("LoadImage", {"image": "a.png"}),
        "2": node("WanImageToVideo", {"start_image": ["1", 0]}),
    }
    caps.raw["WanImageToVideo"] = {"input": {"required": {"start_image": ["IMAGE", {}]}}}
    caps.node_types.add("WanImageToVideo")

    m = autodetect(graph, mode="video", caps=caps)
    assert not m.is_optional("image_in")
    assert not m.has_optional_files


def test_nothing_is_optional_without_the_node_catalogue(reference_graph):
    """Offline we cannot tell, so nothing is dropped - the safe direction."""
    m = autodetect(reference_graph, mode="video", caps=None)
    assert m.optional_slots == []
    assert not m.has_optional_files


# --- pruning --------------------------------------------------------------
def test_one_picture_is_enough(reference_graph, manifest):
    patched, refs, _ = run(reference_graph, manifest, ["image_in"])
    assert refs == {"ref_image_size": "match", "ref_images.ref_image_0": ["137", 0]}
    for gone in ("619", "620", "621", "623"):
        assert gone not in patched, f"{gone} should have been removed"


def test_skipping_the_video_drops_both_of_its_inputs(reference_graph, manifest):
    """Node 623 feeds the footage and its soundtrack; they go together."""
    _, refs, _ = run(reference_graph, manifest, ["image_in"])
    assert "ref_videos.ref_video_0" not in refs
    assert "ref_video_audios.ref_video_audio_0" not in refs


def test_keeping_the_video_keeps_both(reference_graph, manifest):
    patched, refs, _ = run(reference_graph, manifest, ["video_in"])
    assert refs["ref_videos.ref_video_0"] == ["623", 0]
    assert refs["ref_video_audios.ref_video_audio_0"] == ["623", 2]
    assert "623" in patched


def test_audio_only(reference_graph, manifest):
    patched, refs, _ = run(reference_graph, manifest, ["audio_in"])
    assert refs == {"ref_image_size": "match", "ref_audios.ref_audio_0": ["621", 0]}
    assert list(patched) == ["621", "136", "92"]


def test_gaps_are_closed(reference_graph, manifest):
    """Supplying the first and third leaves ref_image_1 empty; renumber it."""
    _, refs, _ = run(reference_graph, manifest, ["image_in", "image_in_3"])
    assert refs["ref_images.ref_image_0"] == ["137", 0]
    assert refs["ref_images.ref_image_1"] == ["620", 0]
    assert "ref_images.ref_image_2" not in refs


def test_only_the_middle_picture_becomes_the_first(reference_graph, manifest):
    _, refs, _ = run(reference_graph, manifest, ["image_in_2"])
    assert refs["ref_images.ref_image_0"] == ["619", 0]
    assert len([k for k in refs if k.startswith("ref_images")]) == 1


def test_all_five_are_left_alone(reference_graph, manifest):
    patched, refs, _ = run(
        reference_graph, manifest,
        ["image_in", "image_in_2", "image_in_3", "audio_in", "video_in"])
    assert len(refs) == 7           # six references plus ref_image_size
    for kept in ("137", "619", "620", "621", "623"):
        assert kept in patched


def test_the_loaded_workflow_is_never_touched(reference_graph, manifest):
    before = json.dumps(reference_graph, sort_keys=True)
    run(reference_graph, manifest, ["image_in"])
    assert json.dumps(reference_graph, sort_keys=True) == before


def test_a_required_slot_is_not_pruned(caps):
    graph = {
        "1": node("LoadImage", {"image": "a.png"}),
        "2": node("WanImageToVideo", {"start_image": ["1", 0]}),
    }
    caps.raw["WanImageToVideo"] = {"input": {"required": {"start_image": ["IMAGE", {}]}}}
    caps.node_types.add("WanImageToVideo")
    m = autodetect(graph, mode="video", caps=caps)

    patched, _ = apply(graph, m, GenerationRequest(prompt="x"))
    assert "1" in patched, "a required loader must stay, even with no file"
    assert patched["2"]["inputs"]["start_image"] == ["1", 0]


# --- what the user is told ------------------------------------------------
def test_one_file_out_of_five_is_accepted(manifest):
    request = GenerationRequest(prompt="x", files={"audio_in": REAL_FILE})
    assert missing_inputs(manifest, request) == []


def test_nothing_attached_is_refused_once(manifest):
    problems = missing_inputs(manifest, GenerationRequest(prompt="x"))
    assert problems == ["Please attach at least one file to work from."]


def test_a_missing_file_on_disk_is_still_reported(manifest):
    request = GenerationRequest(prompt="x", files={"image_in": "C:/gone/none.png"})
    problems = missing_inputs(manifest, request)
    assert len(problems) == 1 and "no longer exists" in problems[0]


def test_required_slots_still_demand_a_file(caps):
    graph = {
        "1": node("LoadImage", {"image": "a.png"}),
        "2": node("WanImageToVideo", {"start_image": ["1", 0]}),
    }
    caps.raw["WanImageToVideo"] = {"input": {"required": {"start_image": ["IMAGE", {}]}}}
    caps.node_types.add("WanImageToVideo")
    m = autodetect(graph, mode="video", caps=caps)

    problems = missing_inputs(m, GenerationRequest(prompt="x"))
    assert any("Please choose a file" in p for p in problems)


# --- noticing new loaders in an edited workflow ---------------------------
def test_a_manifest_picks_up_loaders_added_later(tmp_path, reference_graph, caps):
    """Editing a workflow to add reference loaders used to change nothing: the
    original binding still resolved, so the manifest was never revisited."""
    one_loader = {k: v for k, v in reference_graph.items()
                  if k in ("137", "136", "92")}
    one_loader["136"] = json.loads(json.dumps(reference_graph["136"]))
    for extra in ("ref_images.ref_image_1", "ref_images.ref_image_2",
                  "ref_videos.ref_video_0", "ref_video_audios.ref_video_audio_0",
                  "ref_audios.ref_audio_0"):
        one_loader["136"]["inputs"].pop(extra)

    path = tmp_path / "ref.json"
    path.write_text(json.dumps(one_loader), encoding="utf-8")
    first = load_workflow(path, "video", caps=caps)
    assert first.manifest.file_slots() == ["image_in"]

    # The user adds the other four loaders in ComfyUI and re-exports.
    path.write_text(json.dumps(reference_graph), encoding="utf-8")
    again = load_workflow(path, "video", caps=caps)
    assert again.manifest.file_slots() == [
        "image_in", "image_in_2", "image_in_3", "audio_in", "video_in"]


def test_hand_edited_names_survive_that_refresh(tmp_path, reference_graph, caps):
    path = tmp_path / "ref.json"
    path.write_text(json.dumps(reference_graph), encoding="utf-8")

    wf = load_workflow(path, "video", caps=caps)
    wf.manifest.labels["image_in"] = "The face"
    wf.manifest.autodetected = False
    wf.manifest.save(wf.manifest_path)

    again = load_workflow(path, "video", caps=caps)
    assert again.manifest.label_for("image_in") == "The face"
