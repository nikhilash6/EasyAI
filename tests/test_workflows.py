"""Format detection, manifest autodetect and graph patching.

Run with:  python -m pytest tests/test_workflows.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.workflows.loader import Format, classify, extract_graph, load_workflow
from app.workflows.manifest import Manifest, autodetect, manifest_path_for
from app.workflows.patch import GenerationRequest, apply


def node(class_type, inputs, title=None):
    n = {"class_type": class_type, "inputs": inputs}
    if title:
        n["_meta"] = {"title": title}
    return n


@pytest.fixture
def txt2img_graph():
    """A plain text-to-image graph with two encoders wired to a sampler."""
    return {
        "1": node("CheckpointLoaderSimple", {"ckpt_name": "flux2.safetensors"}),
        "2": node("CLIPTextEncode", {"text": "a cat", "clip": ["1", 1]},
                  title="CLIP Text Encode (Prompt)"),
        "3": node("CLIPTextEncode", {"text": "blurry", "clip": ["1", 1]},
                  title="CLIP Text Encode (Prompt)"),
        "4": node("EmptyLatentImage", {"width": 512, "height": 512, "batch_size": 1}),
        "5": node("KSampler", {"seed": 42, "steps": 20, "model": ["1", 0],
                               "positive": ["2", 0], "negative": ["3", 0],
                               "latent_image": ["4", 0]}),
        "6": node("VAEDecode", {"samples": ["5", 0], "vae": ["1", 2]}),
        "7": node("SaveImage", {"images": ["6", 0], "filename_prefix": "ComfyUI"}),
    }


@pytest.fixture
def subgraph_img2img():
    """Mirrors the real Flux Klein workflows: subgraph ids, a primitive prompt,
    and a size that is wired from GetImageSize rather than typed in."""
    return {
        "76": node("LoadImage", {"image": "photo.jpg"}),
        "122:120": node("UnetLoaderGGUF", {"unet_name": r"Flux 2\flux-2-klein-9b-Q8_0.gguf"}),
        "121": node("PrimitiveStringMultiline", {"value": "a portrait"},
                    title="String (Multiline)"),
        "122:107": node("CLIPTextEncode", {"text": ["121", 0], "clip": ["122:120", 0]},
                        title="CLIP Text Encode (Positive Prompt)"),
        "122:113": node("GetImageSize", {"image": ["76", 0]}),
        "122:112": node("EmptyFlux2LatentImage", {"width": ["122:113", 0],
                                                  "height": ["122:113", 1]}),
        "122:104": node("RandomNoise", {"noise_seed": 827927919139840}),
        "122:99": node("CFGGuider", {"positive": ["122:107", 0], "model": ["122:120", 0]}),
        "94": node("SaveImage", {"images": ["122:99", 0], "filename_prefix": "photobooth"}),
    }


# --- format classification -------------------------------------------------
def test_api_format_detected(txt2img_graph):
    assert classify(txt2img_graph) is Format.API


def test_ui_format_detected():
    assert classify({"nodes": [{"id": 1}], "links": []}) is Format.UI


def test_ui_with_embedded_api_detected(txt2img_graph):
    raw = {"nodes": [{"id": 1}], "links": [], "extra": {"prompt": txt2img_graph}}
    assert classify(raw) is Format.UI_WITH_API
    assert extract_graph(raw, Format.UI_WITH_API) == txt2img_graph


def test_ui_with_empty_prompt_is_plain_ui():
    assert classify({"nodes": [], "links": [], "extra": {"prompt": {}}}) is Format.UI


def test_garbage_is_invalid():
    assert classify([1, 2, 3]) is Format.INVALID
    assert classify({"templates": [{"data": "..."}]}) is Format.INVALID


# --- autodetect ------------------------------------------------------------
def test_detects_prompt_and_negative_from_graph_structure(txt2img_graph):
    """Both encoders share a title, so only the wiring can tell them apart."""
    m = autodetect(txt2img_graph)
    assert (m.get("prompt").node, m.get("prompt").input) == ("2", "text")
    assert (m.get("negative").node, m.get("negative").input) == ("3", "text")
    assert "prompt" not in m.ambiguous


def test_detects_size_seed_and_output(txt2img_graph):
    m = autodetect(txt2img_graph)
    assert (m.get("width").node, m.get("width").input) == ("4", "width")
    assert (m.get("height").node, m.get("height").input) == ("4", "height")
    assert (m.get("seed").node, m.get("seed").input) == ("5", "seed")
    assert (m.get("output").node, m.get("output").input) == ("7", "filename_prefix")
    assert m.can_set_size


def test_string_node_ids_survive(subgraph_img2img):
    m = autodetect(subgraph_img2img)
    assert m.get("seed").node == "122:104"      # not coerced to an int
    assert isinstance(m.get("seed").node, str)


def test_primitive_prompt_beats_the_encoder(subgraph_img2img):
    """The encoder's text is a link; the editable value lives in the primitive."""
    m = autodetect(subgraph_img2img)
    assert (m.get("prompt").node, m.get("prompt").input) == ("121", "value")


def test_linked_size_is_not_bindable(subgraph_img2img):
    """width/height are wired from GetImageSize, so there is nothing to set."""
    m = autodetect(subgraph_img2img)
    assert m.get("width") is None
    assert m.get("height") is None
    assert not m.can_set_size
    assert not m.can_set_ratio


def test_loader_and_engine_detected(subgraph_img2img):
    m = autodetect(subgraph_img2img)
    assert (m.get("image_in").node, m.get("image_in").input) == ("76", "image")
    assert m.engine == "flux2-klein"
    assert "image_in" in m.expose


def test_ratio_node_detected():
    graph = {"1": node("ResolutionSelector",
                       {"aspect_ratio": "16:9 (Widescreen)", "megapixels": 0.4})}
    m = autodetect(graph)
    assert m.get("ratio") is not None
    assert m.can_set_ratio


# --- patching --------------------------------------------------------------
def test_patch_writes_only_bound_inputs(txt2img_graph):
    m = autodetect(txt2img_graph)
    req = GenerationRequest(prompt="a dog", negative="ugly", ratio="2:3", seed=7)
    patched, report = apply(txt2img_graph, m, req)

    assert patched["2"]["inputs"]["text"] == "a dog"
    assert patched["3"]["inputs"]["text"] == "ugly"
    assert patched["5"]["inputs"]["seed"] == 7
    assert (patched["4"]["inputs"]["width"], patched["4"]["inputs"]["height"]) == (832, 1248)
    assert (report.width, report.height, report.seed) == (832, 1248, 7)
    # untouched inputs keep their values
    assert patched["5"]["inputs"]["steps"] == 20


def test_patch_never_mutates_the_original(txt2img_graph):
    before = json.dumps(txt2img_graph, sort_keys=True)
    apply(txt2img_graph, autodetect(txt2img_graph), GenerationRequest(prompt="x", ratio="1:1"))
    assert json.dumps(txt2img_graph, sort_keys=True) == before


def test_seed_is_randomised_when_not_given(txt2img_graph):
    m = autodetect(txt2img_graph)
    seeds = {apply(txt2img_graph, m, GenerationRequest(prompt="x"))[1].seed for _ in range(8)}
    assert len(seeds) > 1, "a fixed seed would give every user the same picture"


def test_patch_leaves_linked_inputs_alone(subgraph_img2img):
    m = autodetect(subgraph_img2img)
    # Force a width binding onto an input that is actually a link.
    m.bindings["width"] = type(m.get("image_in"))("122:112", "width")
    m.bindings["height"] = type(m.get("image_in"))("122:112", "height")
    patched, report = apply(subgraph_img2img, m, GenerationRequest(prompt="x", ratio="2:3"))

    assert patched["122:112"]["inputs"]["width"] == ["122:113", 0]   # link intact
    assert report.width is None
    assert any("connected to another node" in s for s in report.skipped)


def test_patch_reports_a_missing_node_instead_of_crashing(txt2img_graph):
    m = autodetect(txt2img_graph)
    m.bindings["prompt"] = type(m.get("prompt"))("999", "text")
    _, report = apply(txt2img_graph, m, GenerationRequest(prompt="x"))
    assert any("999" in s for s in report.skipped)


def test_uploaded_file_goes_to_the_bound_loader(subgraph_img2img):
    m = autodetect(subgraph_img2img)
    req = GenerationRequest(prompt="x", uploaded={"image_in": "uploaded.png"})
    patched, _ = apply(subgraph_img2img, m, req)
    assert patched["76"]["inputs"]["image"] == "uploaded.png"


#: The real option list ResolutionSelector accepts. Note the descriptive half
#: differs per entry, and 9:21 is not offered at all.
RESOLUTION_OPTIONS = [
    "1:1 (Square)", "2:3 (Portrait Photo)", "3:2 (Photo)",
    "3:4 (Portrait Standard)", "4:3 (Standard)",
    "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)",
]


def test_ratio_node_uses_the_nodes_own_wording():
    graph = {"1": node("ResolutionSelector", {"aspect_ratio": "16:9 (Widescreen)"})}
    m = autodetect(graph)
    patched, _ = apply(graph, m, GenerationRequest(prompt="x", ratio="9:16"),
                       ratio_options=RESOLUTION_OPTIONS)
    # Not "9:16 (Widescreen)" - that string is not in the list and would be
    # rejected. The node's own label for 9:16 must be used.
    assert patched["1"]["inputs"]["aspect_ratio"] == "9:16 (Portrait Widescreen)"


def test_ratio_the_node_cannot_do_is_left_alone():
    graph = {"1": node("ResolutionSelector", {"aspect_ratio": "16:9 (Widescreen)"})}
    m = autodetect(graph)
    patched, report = apply(graph, m, GenerationRequest(prompt="x", ratio="9:21"),
                            ratio_options=RESOLUTION_OPTIONS)
    assert patched["1"]["inputs"]["aspect_ratio"] == "16:9 (Widescreen)"
    assert any("could not be applied" in s for s in report.skipped)


def test_ratio_label_is_never_invented_without_the_list():
    """Offline we cannot know the wording, so the workflow keeps its own."""
    graph = {"1": node("ResolutionSelector", {"aspect_ratio": "16:9 (Widescreen)"})}
    m = autodetect(graph)
    patched, report = apply(graph, m, GenerationRequest(prompt="x", ratio="2:3"))
    assert patched["1"]["inputs"]["aspect_ratio"] == "16:9 (Widescreen)"
    assert any("could not be applied" in s for s in report.skipped)


def test_bare_ratio_node_can_be_set_without_the_list():
    graph = {"1": node("SomeRatioNode", {"aspect_ratio": "16:9"})}
    m = autodetect(graph)
    patched, _ = apply(graph, m, GenerationRequest(prompt="x", ratio="2:3"))
    assert patched["1"]["inputs"]["aspect_ratio"] == "2:3"


# --- manifest round trip ---------------------------------------------------
def test_manifest_survives_save_and_load(tmp_path, txt2img_graph):
    wf = tmp_path / "demo.json"
    wf.write_text(json.dumps(txt2img_graph), encoding="utf-8")

    loaded = load_workflow(wf, "image")
    assert loaded.loadable and loaded.fmt is Format.API
    assert manifest_path_for(wf).is_file(), "a manifest should be written on first load"

    reread = Manifest.load(manifest_path_for(wf))
    assert reread.get("prompt").node == "2"
    assert reread.engine == loaded.manifest.engine


def test_hand_edited_manifest_is_not_overwritten(tmp_path, txt2img_graph):
    wf = tmp_path / "demo.json"
    wf.write_text(json.dumps(txt2img_graph), encoding="utf-8")

    load_workflow(wf, "image")                       # writes the detected manifest
    m = Manifest.load(manifest_path_for(wf))
    m.bindings["prompt"] = type(m.get("prompt"))("3", "text")   # user corrects it
    m.autodetected = False
    m.save(manifest_path_for(wf))

    again = load_workflow(wf, "image")
    assert again.manifest.get("prompt").node == "3"
    assert again.manifest.autodetected is False


def test_ui_only_file_explains_how_to_fix_it(tmp_path):
    wf = tmp_path / "editor.json"
    wf.write_text(json.dumps({"nodes": [{"id": 1}], "links": []}), encoding="utf-8")
    loaded = load_workflow(wf, "image")
    assert not loaded.loadable
    assert "Export (API)" in loaded.error
    assert not manifest_path_for(wf).exists(), "unusable files get no manifest"
