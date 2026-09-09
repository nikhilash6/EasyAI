"""The prompt-enhancer switch, and the staleness guard on embedded graphs.

Run with:  python -m pytest tests/test_enhancer.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.workflows.loader import Format, classify
from app.workflows.manifest import autodetect
from app.workflows.patch import GenerationRequest, apply


def node(class_type, inputs, title=None):
    n = {"class_type": class_type, "inputs": inputs}
    if title:
        n["_meta"] = {"title": title}
    return n


# --- fixtures -------------------------------------------------------------
@pytest.fixture
def enhancer_inline():
    """The user types straight into the enhancer, which feeds the encoder.

    This is how the ThinkingLLM enhancer is usually wired in an LTX workflow.
    """
    return {
        "1": node("ThinkingLLM_QwenVL_PromptEnhancer",
                  {"prompt_text": "a fox runs", "model_name": "qwen", "seed": 1}),
        "2": node("CLIPTextEncode", {"text": ["1", 0], "clip": ["5", 0]}),
        "3": node("CLIPTextEncode", {"text": "blurry", "clip": ["5", 0]}),
        "5": node("CLIPLoader", {"clip_name": "x.safetensors", "type": "ltxv"}),
        "6": node("LTXVConditioning", {"positive": ["2", 0], "negative": ["3", 0]}),
        "7": node("EmptyLTXVLatentVideo",
                  {"width": 768, "height": 512, "length": 97, "batch_size": 1}),
        "8": node("RandomNoise", {"noise_seed": 42}),
        "9": node("VHS_VideoCombine", {"filename_prefix": "LTX", "images": ["7", 0]}),
    }


@pytest.fixture
def enhancer_downstream():
    """A separate text node feeds the enhancer, which feeds the encoder."""
    return {
        "1": node("PrimitiveStringMultiline", {"value": "a fox runs"}),
        "2": node("ThinkingLLM_QwenVL_PromptEnhancer",
                  {"prompt_text": ["1", 0], "model_name": "qwen"}),
        "3": node("CLIPTextEncode", {"text": ["2", 0], "clip": ["5", 0]}),
        "5": node("CLIPLoader", {"clip_name": "x.safetensors", "type": "ltxv"}),
        "9": node("SaveVideo", {"filename_prefix": "LTX", "video": ["3", 0]}),
    }


# --- detection ------------------------------------------------------------
def test_enhancer_is_detected_and_is_also_the_prompt_entry(enhancer_inline):
    m = autodetect(enhancer_inline, mode="video")
    assert m.has("enhancer")
    assert m.get("enhancer").node == "1"
    assert m.get("enhancer").input == "prompt_text"
    # The user types into the enhancer, so that is where the prompt goes.
    assert (m.get("prompt").node, m.get("prompt").input) == ("1", "prompt_text")


def test_enhancer_detected_when_fed_from_another_node(enhancer_downstream):
    m = autodetect(enhancer_downstream, mode="video")
    assert m.get("enhancer").node == "2"
    # The editable text lives upstream, not on the enhancer.
    assert (m.get("prompt").node, m.get("prompt").input) == ("1", "value")


@pytest.mark.parametrize("class_type", [
    "ThinkingLLM_QwenVL_PromptEnhancer",
    "ThinkingLLM_QwenVL_GGUF_PromptEnhancer",
    "AILab_QwenVL_PromptEnhancer",
    "LTXVPromptEnhancer",
    "AdvPromptEnhancer",
    "Enhancer",
])
def test_known_enhancer_node_names(class_type):
    m = autodetect({"1": node(class_type, {"prompt": "hi"}),
                    "2": node("CLIPTextEncode", {"text": ["1", 0]})}, mode="video")
    assert m.has("enhancer"), f"{class_type} should be recognised"


def test_a_plain_workflow_has_no_enhancer():
    m = autodetect({"1": node("CLIPTextEncode", {"text": "hi"})}, mode="image")
    assert not m.has("enhancer")


def test_image_enhancers_are_not_mistaken_for_prompt_enhancers():
    """TopazImageEnhance and friends work on pictures, not words."""
    for class_type in ("TopazImageEnhance", "HitPawVideoEnhance",
                       "LTXVEnhanceAVideoKJ", "Flux2KleinTextEnhancer"):
        m = autodetect({"1": node(class_type, {"image": ["2", 0]})}, mode="image")
        assert not m.has("enhancer"), f"{class_type} is not a prompt enhancer"


# --- switching it on ------------------------------------------------------
def test_enhancer_on_leaves_the_node_in_place(enhancer_inline):
    m = autodetect(enhancer_inline, mode="video")
    patched, report = apply(enhancer_inline, m,
                            GenerationRequest(prompt="a cat sleeps", enhance=True))
    assert "1" in patched
    assert patched["1"]["inputs"]["prompt_text"] == "a cat sleeps"
    assert patched["2"]["inputs"]["text"] == ["1", 0]      # still routed through
    assert report.enhancer is True


def test_default_leaves_the_workflow_as_the_author_built_it(enhancer_inline):
    m = autodetect(enhancer_inline, mode="video")
    patched, report = apply(enhancer_inline, m, GenerationRequest(prompt="x"))
    assert "1" in patched
    assert report.enhancer is True


# --- switching it off -----------------------------------------------------
def test_enhancer_off_inlines_the_prompt_and_removes_the_node(enhancer_inline):
    m = autodetect(enhancer_inline, mode="video")
    patched, report = apply(enhancer_inline, m,
                            GenerationRequest(prompt="a cat sleeps", enhance=False))

    assert "1" not in patched, "the language model must not be loaded at all"
    assert patched["2"]["inputs"]["text"] == "a cat sleeps"
    assert report.enhancer is False
    # Everything else is untouched.
    assert patched["3"]["inputs"]["text"] == "blurry"
    assert patched["2"]["inputs"]["clip"] == ["5", 0]


def test_enhancer_off_reconnects_to_the_upstream_text_node(enhancer_downstream):
    m = autodetect(enhancer_downstream, mode="video")
    patched, _ = apply(enhancer_downstream, m,
                       GenerationRequest(prompt="a cat sleeps", enhance=False))

    assert "2" not in patched
    # The encoder now reads the primitive directly, and the primitive holds
    # the user's words.
    assert patched["3"]["inputs"]["text"] == ["1", 0]
    assert patched["1"]["inputs"]["value"] == "a cat sleeps"


def test_every_consumer_is_rewired_not_just_the_first():
    graph = {
        "1": node("LTXVPromptEnhancer", {"prompt_text": "x"}),
        "2": node("CLIPTextEncode", {"text": ["1", 0]}),
        "3": node("CLIPTextEncode", {"text": ["1", 0]}),
        "4": node("PreviewAny", {"source": ["1", 1]}),   # the RAW_TRACE output
    }
    m = autodetect(graph, mode="video")
    patched, _ = apply(graph, m, GenerationRequest(prompt="hello", enhance=False))
    assert "1" not in patched
    assert patched["2"]["inputs"]["text"] == "hello"
    assert patched["3"]["inputs"]["text"] == "hello"
    assert patched["4"]["inputs"]["source"] == "hello"


def test_original_graph_is_never_mutated_by_a_bypass(enhancer_inline):
    before = json.dumps(enhancer_inline, sort_keys=True)
    m = autodetect(enhancer_inline, mode="video")
    apply(enhancer_inline, m, GenerationRequest(prompt="x", enhance=False))
    assert json.dumps(enhancer_inline, sort_keys=True) == before


def test_bypass_still_sets_size_and_seed(enhancer_inline):
    m = autodetect(enhancer_inline, mode="video")
    patched, report = apply(enhancer_inline, m,
                            GenerationRequest(prompt="x", ratio="16:9",
                                              seed=7, enhance=False))
    assert patched["8"]["inputs"]["noise_seed"] == 7
    assert (patched["7"]["inputs"]["width"], patched["7"]["inputs"]["height"]) \
        == (report.width, report.height)


# --- the staleness guard --------------------------------------------------
def test_embedded_graph_from_another_workflow_is_rejected():
    """The real failure: an LTX 2.5 file carrying an old text-to-video graph."""
    raw = {
        "nodes": [
            {"id": 1, "type": "LoadImage", "mode": 0},
            {"id": 2, "type": "ResolutionSelector", "mode": 0},
            {"id": 3, "type": "SaveVideo", "mode": 0},
            {"id": 4, "type": "6e397a2b-68f7-48f6-8930-f3a5491a163c", "mode": 0},
        ],
        "links": [],
        "extra": {"prompt": {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
            "3": {"class_type": "VHS_VideoCombine", "inputs": {}},
        }},
    }
    assert classify(raw) is Format.UI_STALE_API


def test_matching_embedded_graph_is_accepted():
    raw = {
        "nodes": [
            {"id": 1, "type": "LoadImage", "mode": 0},
            {"id": 2, "type": "CLIPTextEncode", "mode": 0},
            {"id": 3, "type": "SaveImage", "mode": 0},
        ],
        "links": [],
        "extra": {"prompt": {
            "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "hi"}},
            "3": {"class_type": "SaveImage", "inputs": {}},
        }},
    }
    assert classify(raw) is Format.UI_WITH_API


def test_a_missing_loader_alone_is_enough_to_reject():
    """Most nodes line up, but the picture input is absent - not the same workflow."""
    raw = {
        "nodes": [
            {"id": 1, "type": "LoadImage", "mode": 0},
            {"id": 2, "type": "CLIPTextEncode", "mode": 0},
            {"id": 3, "type": "SaveImage", "mode": 0},
            {"id": 4, "type": "KSampler", "mode": 0},
        ],
        "links": [],
        "extra": {"prompt": {
            "2": {"class_type": "CLIPTextEncode", "inputs": {}},
            "3": {"class_type": "SaveImage", "inputs": {}},
            "4": {"class_type": "KSampler", "inputs": {}},
        }},
    }
    assert classify(raw) is Format.UI_STALE_API


def test_muted_nodes_do_not_count_against_a_good_file():
    raw = {
        "nodes": [
            {"id": 1, "type": "CLIPTextEncode", "mode": 0},
            {"id": 2, "type": "SomeDisabledNode", "mode": 4},   # bypassed
            {"id": 3, "type": "MarkdownNote", "mode": 0},       # never exported
        ],
        "links": [],
        "extra": {"prompt": {"1": {"class_type": "CLIPTextEncode", "inputs": {}}}},
    }
    assert classify(raw) is Format.UI_WITH_API


def test_stale_file_is_not_loadable_and_says_why(tmp_path):
    from app.workflows.loader import load_workflow

    p = tmp_path / "ltx.json"
    p.write_text(json.dumps({
        "nodes": [{"id": 1, "type": "LoadImage", "mode": 0}],
        "links": [],
        "extra": {"prompt": {"9": {"class_type": "CLIPTextEncode", "inputs": {}}}},
    }), encoding="utf-8")

    wf = load_workflow(p, "video")
    assert not wf.loadable
    assert "Export (API)" in wf.error
    assert "wrong thing" in wf.error
