"""Qwen Image 2.1: one encoder for both prompts, and up to ten references.

Run with:  python -m pytest tests/test_qwen_image.py -q

Three things failed on the first Qwen Image 2.1 workflow, and each is pinned
here against the graph's real shape:

* Its single ``TextEncodeQwenImage21`` takes ``prompt`` and
  ``negative_prompt`` and feeds the sampler's positive *and* negative. The
  role tracer let "negative" win for the whole node, so the user's prompt was
  filed as the negative and there was no prompt at all.
* The export wires one reference picture into an input group that takes
  sixteen, so EasyAI offered one slot. It now grows the group to ten.
* The model links sat inside a subgraph of ComfyUI's own template, where
  EasyAI Studio's harvester never looked.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.comfy.objectinfo import Capabilities
from app.workflows import grow
from app.workflows.manifest import Manifest, autodetect
from app.workflows.patch import GenerationRequest, apply as patch_apply

NAMES = [f"image_{i}" for i in range(1, 17)]


def qwen_graph() -> dict:
    """The exported workflow, trimmed to the nodes that matter."""
    return {
        "13": {"class_type": "ResolutionSelector",
               "inputs": {"aspect_ratio": "1:1 (Square)", "megapixels": 2, "multiple": 32}},
        "461": {"class_type": "SaveImageAdvanced",
                "inputs": {"filename_prefix": "Qwen_image_2.1", "images": ["459:457", 0]}},
        "470": {"class_type": "LoadImage", "inputs": {"image": "ali photo.jpg"},
                "_meta": {"title": "Load Image"}},
        "502": {"class_type": "PrimitiveStringMultiline",
                "inputs": {"value": "Medium close-up of <image 1> as Android 18. "
                                    "No mask, no helmet, no extra fingers."},
                "_meta": {"title": "Prompt"}},
        "459:453": {"class_type": "CLIPLoader",
                    "inputs": {"clip_name": "QWEN 2.1\\qwen3vl_8b_int8_convrot.safetensors",
                               "type": "qwen_image"}},
        "459:454": {"class_type": "VAELoader",
                    "inputs": {"vae_name": "QWEN 2.1\\qwen_image_2.1_vae_bf16.safetensors"}},
        "459:497": {"class_type": "UNETLoader",
                    "inputs": {"unet_name": "QWEN 2.1\\qwen_image_2.1_int8_convrot.safetensors"}},
        "459:456": {"class_type": "EmptyLatentImage",
                    "inputs": {"width": ["13", 0], "height": ["13", 1], "batch_size": 1}},
        "459:474": {"class_type": "TextEncodeQwenImage21",
                    "inputs": {"prompt": ["502", 0], "negative_prompt": "",
                               "resolution": 0, "clip": ["459:453", 0],
                               "images.image_1": ["470", 0], "vae": ["459:454", 0]}},
        "459:458": {"class_type": "KSampler",
                    "inputs": {"seed": 589274772259146, "steps": 25, "cfg": 1,
                               "model": ["459:497", 0],
                               "positive": ["459:474", 0], "negative": ["459:474", 1],
                               "latent_image": ["459:456", 0]}},
        "459:457": {"class_type": "VAEDecode",
                    "inputs": {"samples": ["459:458", 0], "vae": ["459:454", 0]}},
    }


def engine() -> Capabilities:
    """What the running ComfyUI says about the nodes involved."""
    raw = {
        "TextEncodeQwenImage21": {"input": {
            "required": {
                "clip": ["CLIP", {}],
                "prompt": ["STRING", {"multiline": True}],
                "negative_prompt": ["STRING", {"multiline": True}],
                "images": ["COMFY_AUTOGROW_V3", {"template": {
                    "input": {"required": {"image": ["IMAGE", {}]}},
                    "names": NAMES, "min": 0}}],
            },
            "optional": {"vae": ["VAE", {}]},
        }},
        "MiniMaxH3ReferenceToVideo": {"input": {"optional": {
            "ref_images": ["COMFY_AUTOGROW_V3", {"template": {
                "input": {"required": {"ref_image": ["IMAGE", {}]}},
                "prefix": "ref_image_", "min": 0, "max": 9}}],
        }}},
        "Needy": {"input": {"required": {
            "images": ["COMFY_AUTOGROW_V3", {"template": {"names": NAMES, "min": 2}}],
        }}},
    }
    return Capabilities(node_types=set(raw), raw=raw, available=True)


# --- the prompt -------------------------------------------------------------
def test_the_prompt_is_found_as_the_prompt():
    """It used to be filed as the negative, leaving no prompt at all."""
    manifest = autodetect(qwen_graph(), "image", "qwen")
    prompt = manifest.get("prompt")
    assert prompt is not None, "no prompt found - the reported bug"
    assert (prompt.node, prompt.input) == ("502", "value")


def test_the_negative_is_the_encoders_own_field():
    manifest = autodetect(qwen_graph(), "image", "qwen")
    negative = manifest.get("negative")
    assert (negative.node, negative.input) == ("459:474", "negative_prompt")


def test_a_negative_fed_from_its_own_node_is_still_negative():
    """The same encoder with both prompts coming from primitives."""
    graph = qwen_graph()
    graph["600"] = {"class_type": "PrimitiveStringMultiline",
                    "inputs": {"value": "blurry, watermark"}}
    graph["459:474"]["inputs"]["negative_prompt"] = ["600", 0]
    manifest = autodetect(graph, "image", "qwen")
    assert manifest.get("prompt").node == "502"
    assert manifest.get("negative").node == "600"


def test_separate_encoders_are_unaffected():
    """The usual shape: one CLIPTextEncode per conditioning."""
    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a fox"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry"}},
        "3": {"class_type": "KSampler",
              "inputs": {"positive": ["1", 0], "negative": ["2", 0], "seed": 1}},
    }
    manifest = autodetect(graph, "image", "wf")
    assert manifest.get("prompt").node == "1"
    assert manifest.get("negative").node == "2"


# --- what the engine allows -------------------------------------------------
def test_a_group_with_min_zero_needs_none_of_its_members():
    """Listed as required, but min 0 - leaving every picture out is fine."""
    caps = engine()
    assert caps.is_optional_input("TextEncodeQwenImage21", "images.image_1")
    assert caps.is_optional_input("TextEncodeQwenImage21", "images.image_10")
    # Whole required inputs are still required.
    assert not caps.is_optional_input("TextEncodeQwenImage21", "prompt")


def test_members_below_min_stay_required():
    caps = engine()
    assert not caps.is_optional_input("Needy", "images.image_1")
    assert not caps.is_optional_input("Needy", "images.image_2")
    assert caps.is_optional_input("Needy", "images.image_3")


def test_both_ways_of_describing_a_group_are_read():
    caps = engine()
    assert caps.autogrow("TextEncodeQwenImage21", "images")["names"][:2] == ["image_1", "image_2"]
    minimax = caps.autogrow("MiniMaxH3ReferenceToVideo", "ref_images")
    assert minimax["names"][0] == "ref_image_0" and len(minimax["names"]) == 9
    assert caps.autogrow("TextEncodeQwenImage21", "prompt") is None


# --- growing to ten -----------------------------------------------------------
def test_one_wired_picture_grows_to_ten():
    specs = grow.plan(qwen_graph(), engine())
    assert len(specs) == 1
    assert specs[0]["names"] == [f"image_{i}" for i in range(1, 11)]
    assert specs[0]["loader"] == "470"


def test_nothing_grows_without_the_engine():
    """Only the engine can say what a group accepts."""
    assert grow.plan(qwen_graph(), None) == []
    assert grow.plan(qwen_graph(), Capabilities(available=False)) == []


def test_a_group_the_author_filled_is_left_alone():
    """MiniMax H3's reference workflow wires three into a group of nine.
    Three is a choice, so its screen must not change."""
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": "b.png"}},
        "3": {"class_type": "LoadImage", "inputs": {"image": "c.png"}},
        "9": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "ref_images.ref_image_0": ["1", 0],
            "ref_images.ref_image_1": ["2", 0],
            "ref_images.ref_image_2": ["3", 0]}},
    }
    assert grow.plan(graph, engine()) == []


def test_other_pictures_count_against_the_ten():
    """EasyAI has ten picture slots in all, not ten per group."""
    graph = qwen_graph()
    for n in range(4):
        graph[f"extra{n}"] = {"class_type": "LoadImage", "inputs": {"image": f"{n}.png"}}
    specs = grow.plan(graph, engine())
    assert len(specs[0]["names"]) == 10 - 4


def test_growing_is_repeatable_and_never_touches_the_original():
    graph = qwen_graph()
    before = copy.deepcopy(graph)
    specs = grow.plan(graph, engine())

    grown = copy.deepcopy(graph)
    first = grow.apply(grown, specs)
    again = grow.apply(grown, specs)
    assert len(first) == 9 and again == []
    assert graph == before


def test_added_nodes_are_not_mistaken_for_subgraph_members():
    """ComfyUI reads '459:474' as node 474 inside subgraph 459."""
    graph = qwen_graph()
    grow.apply(graph, grow.plan(graph, engine()))
    added = [n for n in graph if n.startswith("easyai_")]
    assert added and all(":" not in n for n in added)


def test_the_manifest_offers_ten_optional_references_in_order():
    manifest = autodetect(qwen_graph(), "image", "qwen", engine())
    slots = manifest.file_slots("image")
    assert len(slots) == 10
    assert all(manifest.is_optional(k) for k in slots)
    assert [manifest.label_for(k) for k in slots] == [f"Reference {i}" for i in range(1, 11)]


def test_the_growth_survives_a_round_trip_through_the_file(tmp_path):
    manifest = autodetect(qwen_graph(), "image", "qwen", engine())
    path = tmp_path / "qwen.manifest.json"
    manifest.save(path)
    assert Manifest.load(path).grow == manifest.grow


def test_the_same_slots_come_back_with_comfyui_closed(tmp_path):
    """The manifest was written with the engine running; the next start may
    not have it. The slots must not vanish, nor the manifest be discarded."""
    from app.workflows.loader import load_workflow

    path = tmp_path / "Qwen.json"
    path.write_text(json.dumps(qwen_graph()), encoding="utf-8")

    online = load_workflow(path, "image", caps=engine())
    offline = load_workflow(path, "image", caps=None)
    assert offline.manifest.file_slots() == online.manifest.file_slots()
    assert len(offline.manifest.file_slots()) == 10
    assert set(offline.graph) == set(online.graph)


def test_an_existing_manifest_gains_the_slots(tmp_path):
    """A Qwen workflow added before this fix has a one-slot manifest."""
    from app.workflows.loader import load_workflow

    path = tmp_path / "Qwen.json"
    path.write_text(json.dumps(qwen_graph()), encoding="utf-8")
    old = autodetect(qwen_graph(), "image", "Qwen")      # engine-less, one slot
    old.save(tmp_path / "Qwen.manifest.json")
    assert len(old.file_slots()) == 1

    wf = load_workflow(path, "image", caps=engine())
    assert len(wf.manifest.file_slots()) == 10


def test_the_workflow_file_is_never_rewritten(tmp_path):
    from app.workflows.loader import load_workflow

    path = tmp_path / "Qwen.json"
    path.write_text(json.dumps(qwen_graph()), encoding="utf-8")
    before = path.read_bytes()
    load_workflow(path, "image", caps=engine())
    assert path.read_bytes() == before


# --- what reaches ComfyUI ---------------------------------------------------
def patched(count: int, negative: str | None = None) -> dict:
    graph = qwen_graph()
    specs = grow.plan(graph, engine())
    grow.apply(graph, specs)
    manifest = autodetect(graph, "image", "qwen", engine())
    manifest.grow = specs
    request = GenerationRequest(prompt="a fox", negative=negative)
    for index, key in enumerate(manifest.file_slots()[:count]):
        request.files[key] = f"pic{index}.png"
        request.uploaded[key] = f"uploaded{index}.png"
    result, _ = patch_apply(graph, manifest, request)
    return result


def references(graph: dict) -> dict:
    inputs = graph["459:474"]["inputs"]
    return {k: v for k, v in inputs.items() if k.startswith("images.")}


def test_no_pictures_leaves_no_reference_and_no_placeholder():
    """'ali photo.jpg' exists only on the author's machine."""
    graph = patched(0)
    assert references(graph) == {}
    assert not any(n.get("class_type") == "LoadImage" for n in graph.values())


def test_pictures_are_numbered_from_one():
    """Renumbering used to start at 0 - fine for MiniMax's ref_image_0, but
    image_0 is not a name Qwen's node has, and the run fails."""
    graph = patched(3)
    assert sorted(references(graph)) == ["images.image_1", "images.image_2", "images.image_3"]
    loaded = [graph[v[0]]["inputs"]["image"] for _, v in sorted(references(graph).items())]
    assert loaded == ["uploaded0.png", "uploaded1.png", "uploaded2.png"]


def test_empty_reference_slots_are_not_reported_as_problems():
    """Skipped items reach the user as warnings. Leaving eight of ten
    optional slots empty is the normal case, not eight problems."""
    graph = qwen_graph()
    specs = grow.plan(graph, engine())
    grow.apply(graph, specs)
    manifest = autodetect(graph, "image", "qwen", engine())
    request = GenerationRequest(prompt="a fox")
    request.files["image_in"] = "a.png"
    request.uploaded["image_in"] = "a.png"

    _, report = patch_apply(graph, manifest, request)
    assert not [s for s in report.skipped if "image_in" in s]
    assert sum(1 for a in report.applied if a.endswith(":left out")) == 9


def test_all_ten_pictures_reach_the_encoder():
    graph = patched(10)
    assert len(references(graph)) == 10
    assert "images.image_10" in references(graph)


def test_the_negative_is_written_into_the_encoder():
    graph = patched(0, negative="blurry, watermark")
    assert graph["459:474"]["inputs"]["negative_prompt"] == "blurry, watermark"
    assert graph["502"]["inputs"]["value"] == "a fox"


def test_minimax_still_counts_from_zero():
    """The renumbering change must not move MiniMax off ref_image_0."""
    from app.workflows.patch import _renumber_group

    graph = {"9": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
        "ref_images.ref_image_0": ["1", 0], "ref_images.ref_image_2": ["3", 0]}}}
    _renumber_group(graph, "9", "ref_images", first=0)
    assert sorted(graph["9"]["inputs"]) == ["ref_images.ref_image_0", "ref_images.ref_image_1"]


# --- EasyAI Studio ----------------------------------------------------------
def test_links_inside_a_template_subgraph_are_harvested():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import make_catalog

    template = {
        "nodes": [{"type": "0f9a-guid", "properties": {}}],
        "definitions": {"subgraphs": [{"nodes": [
            {"type": "UNETLoader", "properties": {"models": [{
                "name": "qwen_image_2.1_int8_convrot.safetensors",
                "url": "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/x.safetensors"}]}},
        ], "definitions": {"subgraphs": [{"nodes": [
            {"type": "VAELoader", "properties": {"models": [{
                "name": "nested.safetensors", "url": "https://example/nested"}]}},
        ]}]}}]},
    }
    names = [m["name"] for n in make_catalog._template_nodes(template)
             for m in (n.get("properties") or {}).get("models") or []]
    assert "qwen_image_2.1_int8_convrot.safetensors" in names
    assert "nested.safetensors" in names, "a subgraph inside a subgraph"


# --- the screen -------------------------------------------------------------
@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def image_tab(tmp_path, qt_app, monkeypatch):
    from app.config import Config
    from app.ui.tabs import build_tab
    from app.workflows.loader import load_workflow

    path = tmp_path / "Qwen.json"
    path.write_text(json.dumps(qwen_graph()), encoding="utf-8")
    wf = load_workflow(path, "image", caps=engine())

    cfg = Config(tmp_path / "settings.json")
    cfg.set("workflow_dir", str(tmp_path / "workflows"))
    cfg.set("output_dir", str(tmp_path / "output"))

    class Offline:
        server = "127.0.0.1:1"
        def is_alive(self): return False

    tab = build_tab("image", cfg, Offline())
    tab.show()
    tab._on_workflow_picked(wf)
    return tab, wf


def shown(tab) -> list[str]:
    return [k for k, z in tab.drop_zones.items() if not z.isHidden()]


def test_only_the_first_reference_slot_shows_at_first(image_tab):
    tab, _ = image_tab
    assert len(tab.drop_zones) == 10
    assert shown(tab) == ["image_in"]


def test_filling_a_slot_reveals_the_next(image_tab, tmp_path):
    tab, _ = image_tab
    tab.drop_zones["image_in"].set_path(str(tmp_path / "a.png"))
    assert shown(tab) == ["image_in", "image_in_2"]
    tab.drop_zones["image_in_2"].set_path(str(tmp_path / "b.png"))
    assert shown(tab) == ["image_in", "image_in_2", "image_in_3"]


def test_emptying_a_middle_slot_closes_the_gap(image_tab, tmp_path):
    """Otherwise the third picture would reach the node as <image 2>."""
    tab, _ = image_tab
    for key, name in (("image_in", "a"), ("image_in_2", "b"), ("image_in_3", "c")):
        tab.drop_zones[key].set_path(str(tmp_path / f"{name}.png"))

    tab.drop_zones["image_in_2"].set_path("")

    paths = [Path(tab.drop_zones[k].path()).name for k in ("image_in", "image_in_2")]
    assert paths == ["a.png", "c.png"]
    assert tab.drop_zones["image_in_3"].path() == ""
    assert shown(tab) == ["image_in", "image_in_2", "image_in_3"]


def test_the_negative_box_holds_the_workflows_own_text(image_tab):
    tab, wf = image_tab
    assert not tab.negative_box.isHidden()
    assert tab.negative_box.toPlainText() == ""          # Qwen's own is empty


def test_the_negative_box_text_is_sent(image_tab):
    tab, wf = image_tab
    tab.prompt_box.setPlainText("a fox")
    tab.negative_box.setPlainText("blurry")
    request = tab._collect_request(wf)
    assert request.negative == "blurry"
    assert request.prompt == "a fox"


def test_an_untouched_negative_box_sends_the_workflows_own_text(qt_app, tmp_path):
    """Anima ships "worst quality, low quality..." - leaving the box alone
    must keep exactly that, not blank it."""
    from app.config import Config
    from app.ui.tabs import build_tab
    from app.workflows.loader import load_workflow

    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a fox"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "worst quality, low quality"}},
        "3": {"class_type": "KSampler",
              "inputs": {"positive": ["1", 0], "negative": ["2", 0], "seed": 1}},
    }
    path = tmp_path / "Anima.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    wf = load_workflow(path, "image")

    class Offline:
        server = "127.0.0.1:1"
        def is_alive(self): return False

    cfg = Config(tmp_path / "settings.json")
    cfg.set("output_dir", str(tmp_path / "output"))
    tab = build_tab("image", cfg, Offline())
    tab.show()
    tab._on_workflow_picked(wf)

    assert tab.negative_box.toPlainText() == "worst quality, low quality"
    tab.prompt_box.setPlainText("a fox")
    assert tab._collect_request(wf).negative == "worst quality, low quality"


def test_no_negative_box_for_a_workflow_without_one(qt_app, tmp_path):
    from app.config import Config
    from app.ui.tabs import build_tab
    from app.workflows.loader import load_workflow

    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a fox"}},
        "3": {"class_type": "KSampler", "inputs": {"positive": ["1", 0], "seed": 1}},
    }
    path = tmp_path / "Plain.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    wf = load_workflow(path, "image")

    class Offline:
        server = "127.0.0.1:1"
        def is_alive(self): return False

    cfg = Config(tmp_path / "settings.json")
    cfg.set("output_dir", str(tmp_path / "output"))
    tab = build_tab("image", cfg, Offline())
    tab.show()
    tab._on_workflow_picked(wf)
    assert tab.negative_box.isHidden()
