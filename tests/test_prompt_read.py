"""Reading a prompt back out of a finished file.

Run with:  python -m pytest tests/test_prompt_read.py -q

The failure that matters is a confident wrong answer. A graph can hold several
text boxes - Krea 2 t2i has three, one of them the prompt enhancer's system
instruction - and a reader that detects from scratch shows that instruction to
the viewer as "your prompt". So most of what is tested here is the ordering
that stops it guessing while a saved manifest still knows the answer.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.prompts import (
    PromptRead, _graph_from_bytes, graph_in, read_prompt, recent_results,
)
from app.workflows.manifest import autodetect

REAL = "a fox asleep in the snow"
SYSTEM = ("You are an expert prompt engineer for text-to-image models. "
          "Your task is to expand the user's idea into a rich description.")


def three_text_graph(prompt: str = REAL) -> dict:
    """The shape that catches a naive reader: the enhancer's instruction sits
    on a lower node id than the prompt itself."""
    return {
        "10": {"class_type": "UNETLoader",
               "inputs": {"unet_name": "ZI\\z_image_turbo_bf16.safetensors"}},
        "18": {"class_type": "PrimitiveStringMultiline",
               "inputs": {"value": SYSTEM}},
        "19": {"class_type": "PrimitiveStringMultiline",
               "inputs": {"value": prompt}},
        "20": {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}},
    }


class FakeManifest:
    def __init__(self, node: str, field: str = "value"):
        self._binding = type("B", (), {"node": node, "input": field})()

    def get(self, key):
        return self._binding if key == "prompt" else None


class FakeWorkflow:
    def __init__(self, name, graph, node="19"):
        self.name = name
        self.graph = graph
        self.manifest = FakeManifest(node)


def png_with(tmp_path: Path, graph: dict | None, name: str = "a.png") -> Path:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    path = tmp_path / name
    meta = PngInfo()
    if graph is not None:
        meta.add_text("prompt", json.dumps(graph))
    Image.new("RGB", (8, 8), (30, 30, 30)).save(path, pnginfo=meta)
    return path


# --- getting the graph out --------------------------------------------------
def test_a_png_carrying_a_graph_gives_it_back(tmp_path):
    path = png_with(tmp_path, three_text_graph())
    assert graph_in(path) == three_text_graph()


def test_a_plain_png_reads_as_nothing_rather_than_raising(tmp_path):
    """A screenshot or a picture off the internet is the common case."""
    assert graph_in(png_with(tmp_path, None)) is None


def test_a_file_that_is_not_a_picture_at_all_is_survivable(tmp_path):
    broken = tmp_path / "b.png"
    broken.write_bytes(b"this is not a picture")
    assert graph_in(broken) is None


def test_an_unknown_extension_is_ignored(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("hello", encoding="utf-8")
    assert graph_in(other) is None


# --- the video scan ---------------------------------------------------------
def test_a_graph_embedded_in_a_blob_is_found():
    blob = b"\x00\x00ftypmp42" + json.dumps(three_text_graph()).encode() + b"\xff\xd8"
    assert _graph_from_bytes(blob) == three_text_graph()


def test_a_prompt_containing_a_brace_still_parses():
    """Brace counting would stop early here; raw_decode does not."""
    graph = three_text_graph("a sign reading } and { in neon")
    blob = b"junk" + json.dumps(graph).encode() + b"trailing"
    found = _graph_from_bytes(blob)
    assert found["19"]["inputs"]["value"] == "a sign reading } and { in neon"


def test_a_truncated_video_reads_as_nothing(tmp_path):
    half = json.dumps(three_text_graph())[:60].encode()
    path = tmp_path / "cut.mp4"
    path.write_bytes(b"\x00\x00ftypmp42" + half)
    assert graph_in(path) is None


def test_a_video_with_no_json_reads_as_nothing(tmp_path):
    path = tmp_path / "plain.mp4"
    path.write_bytes(b"\x00" * 4096)
    assert graph_in(path) is None


def test_a_lone_json_object_is_not_mistaken_for_a_graph():
    """Some encoders write small JSON blobs of their own."""
    assert _graph_from_bytes(b'{"encoder": "Lavf61.1.100"}') is None


# --- the ordering that matters ----------------------------------------------
def test_the_manifest_wins_over_detection(tmp_path):
    """The Krea case exactly: detection picks the system instruction, the
    workflow's manifest knows the prompt is the other node."""
    graph = three_text_graph()
    guessed = autodetect(graph, "image", "wf").get("prompt")
    assert guessed.node == "18", "the trap this test exists for has moved"

    path = png_with(tmp_path, graph, "2026-08-15_000247_Krea_2_t2i.png")
    found = read_prompt(path, [FakeWorkflow("Krea 2 t2i", graph)])
    assert found.prompt == REAL
    assert SYSTEM not in found.prompt
    assert found.certain


def test_a_renamed_workflow_is_still_matched_by_its_graph(tmp_path):
    """The filename says Z-Image Turbo; no workflow is called that any more."""
    graph = three_text_graph()
    path = png_with(tmp_path, graph,
                    "2026-08-17_152008_Z-Image_Turbo_-_Text_to_Image.png")
    found = read_prompt(path, [FakeWorkflow("ZIT - GarionhHK", graph)])
    assert found.prompt == REAL
    assert found.workflow_name == "ZIT - GarionhHK"
    assert found.certain


def test_an_expanded_subgraph_still_matches(tmp_path):
    """ComfyUI expands subgraphs when it submits, so the saved workflow and the
    graph in the file are alike rather than identical."""
    saved = three_text_graph()
    ran = dict(saved)
    ran["77"] = {"class_type": "PreviewImage", "inputs": {"images": ["10", 0]}}
    ran["78"] = {"class_type": "PreviewAny", "inputs": {"source": ["19", 0]}}

    path = png_with(tmp_path, ran, "made.png")
    found = read_prompt(path, [FakeWorkflow("Z-Image", saved)])
    assert found.prompt == REAL
    assert found.certain


def test_an_unknown_workflow_falls_through_to_detection(tmp_path):
    """A picture from someone else's ComfyUI still reads - as a guess."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "sd.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": REAL}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    found = read_prompt(png_with(tmp_path, graph, "stranger.png"), [])
    assert found is not None
    assert found.prompt == REAL
    assert not found.certain, "an unmatched workflow must not be stated as fact"
    assert found.workflow_name == ""


def test_a_file_with_no_prompt_reads_as_none(tmp_path):
    assert read_prompt(png_with(tmp_path, None), []) is None


# --- the guard on a loose match ---------------------------------------------
def test_a_match_is_refused_when_the_bound_node_has_gone(tmp_path):
    """Without this, an unrelated workflow could answer for any file."""
    graph = three_text_graph()
    stale = FakeWorkflow("Old", graph, node="999")
    found = read_prompt(png_with(tmp_path, graph, "x.png"), [stale])
    # It must fall through to detection rather than report nothing at all.
    assert found is not None
    assert not found.certain


def test_a_match_is_refused_when_the_node_is_a_different_kind(tmp_path):
    """Same id, different node - the graphs are not really the same workflow."""
    graph = three_text_graph()
    other = {"19": {"class_type": "LoadImage",
                    "inputs": {"value": "photo.jpg"}}}
    mismatched = FakeWorkflow("Different", other, node="19")
    found = read_prompt(png_with(tmp_path, graph, "y.png"), [mismatched])
    assert found is not None
    assert not found.certain


def test_a_wired_input_is_not_read_as_a_prompt(tmp_path):
    """A value computed by another node is a list, not the viewer's text."""
    graph = three_text_graph()
    graph["19"]["inputs"]["value"] = ["18", 0]
    workflow = FakeWorkflow("Wired", graph)
    found = read_prompt(png_with(tmp_path, graph, "z.png"), [workflow])
    assert found is None or found.prompt != ""


def test_an_empty_prompt_is_not_offered(tmp_path):
    graph = three_text_graph("   ")
    found = read_prompt(png_with(tmp_path, graph, "blank.png"),
                        [FakeWorkflow("Blank", graph)])
    assert found is None or found.prompt.strip() != ""


# --- the batch suffix -------------------------------------------------------
@pytest.mark.parametrize("name", [
    "2026-08-15_000247_Krea_2_t2i.png",
    "2026-08-15_000247_Krea_2_t2i_02.png",
])
def test_a_batch_number_does_not_break_the_filename_match(tmp_path, name):
    graph = three_text_graph()
    found = read_prompt(png_with(tmp_path, graph, name),
                        [FakeWorkflow("Krea 2 t2i", graph)])
    assert found.certain and found.workflow_name == "Krea 2 t2i"


def test_the_sanitiser_is_the_one_used_when_saving():
    """If these ever drifted apart, every reading would quietly become a
    guess instead of a certainty."""
    from app.jobs import _timestamp_stem, safe_name

    name = "Krea 2 t2i"
    assert _timestamp_stem(name).endswith(safe_name(name))


# --- listing what is there --------------------------------------------------
def test_recent_results_lists_newest_first(tmp_path):
    import time

    first = png_with(tmp_path, None, "one.png")
    time.sleep(0.01)
    second = png_with(tmp_path, None, "two.png")
    os.utime(second, (time.time() + 5, time.time() + 5))

    found = recent_results([tmp_path])
    assert found[0] == second and first in found


def test_recent_results_ignores_other_files_and_missing_folders(tmp_path):
    png_with(tmp_path, None, "keep.png")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    found = recent_results([tmp_path, tmp_path / "not-here"])
    assert [p.name for p in found] == ["keep.png"]
