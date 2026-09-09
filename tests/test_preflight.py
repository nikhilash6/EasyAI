"""Pre-flight dependency checking.

Run with:  python -m pytest tests/test_preflight.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.comfy.objectinfo import (
    Capabilities, check, install_hint, pack_for, summarise,
)
from app.workflows.loader import load_workflow


def make_caps(nodes: dict) -> Capabilities:
    """Build a fake /object_info response.

    ``nodes`` maps class_type -> {field: [list_of_allowed_values]}.
    """
    raw = {}
    for class_type, fields in nodes.items():
        raw[class_type] = {
            "input": {"required": {k: [v] for k, v in fields.items()}}
        }
    return Capabilities(node_types=set(raw), raw=raw, available=True)


GRAPH = {
    "1": {"class_type": "UnetLoaderGGUF",
          "inputs": {"unet_name": r"Flux 2\flux-2-klein-9b-Q8_0.gguf"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["1", 0]}},
    "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
}

FULL_CAPS = make_caps({
    "UnetLoaderGGUF": {"unet_name": [r"Flux 2\flux-2-klein-9b-Q8_0.gguf", "other.gguf"]},
    "CLIPTextEncode": {},
    "SaveImage": {},
})


@pytest.fixture
def workflow(tmp_path):
    p = tmp_path / "flux.json"
    p.write_text(json.dumps(GRAPH), encoding="utf-8")
    return load_workflow(p, "image")


def test_everything_present_is_ready(workflow):
    check(workflow, FULL_CAPS)
    assert workflow.checked
    assert workflow.ready
    assert workflow.status_text() == "Ready"
    assert workflow.status_icon() == "✅"


def test_missing_custom_node_is_named_by_its_addon(workflow):
    caps = make_caps({"CLIPTextEncode": {}, "SaveImage": {}})   # no GGUF pack
    check(workflow, caps)
    assert not workflow.ready
    assert workflow.missing_nodes == ["ComfyUI-GGUF"]
    assert "ComfyUI-GGUF" in workflow.status_text()
    assert "Manager" in install_hint(workflow)


def test_missing_model_file_is_reported(workflow):
    caps = make_caps({
        "UnetLoaderGGUF": {"unet_name": ["something-else.gguf"]},
        "CLIPTextEncode": {}, "SaveImage": {},
    })
    check(workflow, caps)
    assert not workflow.ready
    assert workflow.missing_models == [r"Flux 2\flux-2-klein-9b-Q8_0.gguf"]
    assert "flux-2-klein-9b-Q8_0.gguf" in workflow.status_text()


def test_slash_direction_does_not_cause_a_false_alarm(workflow):
    """Workflows written on Windows use backslashes; the enum may not."""
    caps = make_caps({
        "UnetLoaderGGUF": {"unet_name": ["Flux 2/flux-2-klein-9b-Q8_0.gguf"]},
        "CLIPTextEncode": {}, "SaveImage": {},
    })
    check(workflow, caps)
    assert workflow.missing_models == []
    assert workflow.ready


def test_models_are_not_checked_when_the_node_itself_is_missing(workflow):
    """One clear cause beats two confusing ones."""
    caps = make_caps({"CLIPTextEncode": {}, "SaveImage": {}})
    check(workflow, caps)
    assert workflow.missing_nodes
    assert workflow.missing_models == []


def test_unreachable_server_leaves_the_workflow_unchecked(workflow):
    check(workflow, Capabilities(available=False, error="connection refused"))
    assert not workflow.checked
    assert workflow.status_text() == "Not checked yet"


def test_unusable_workflow_is_skipped(tmp_path):
    p = tmp_path / "editor.json"
    p.write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
    wf = check(load_workflow(p, "image"), FULL_CAPS)
    assert not wf.checked and not wf.ready


@pytest.mark.parametrize("class_type,expected", [
    ("UnetLoaderGGUF", "ComfyUI-GGUF"),
    ("VHS_VideoCombine", "ComfyUI-VideoHelperSuite"),
    ("LTXVScheduler", "ComfyUI-LTXVideo"),
    ("MiniMaxH3ReferenceToVideo", "comfyui-spectrum-minimax-h3"),
    ("ResolutionSelector", "controlaltai-nodes"),
    ("SomeNodeNobodyKnows", ""),
])
def test_node_pack_lookup(class_type, expected):
    assert pack_for(class_type) == expected


def test_summary_line(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(GRAPH), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")

    ok = check(load_workflow(good, "image"), FULL_CAPS)
    blocked = check(load_workflow(good, "image"), make_caps({"CLIPTextEncode": {}}))
    broken = check(load_workflow(bad, "image"), FULL_CAPS)

    assert summarise([ok, blocked, broken]) == \
        "1 ready, 1 needs something installed, 1 not usable"
    # Two blocked workflows take the plural verb - the count and the sentence
    # have to agree, which is also what makes the line translatable.
    assert summarise([ok, blocked, blocked]) == \
        "1 ready, 2 need something installed"


# --- input option lists ---------------------------------------------------
def test_v3_combo_options_are_read():
    """ComfyUI's newer nodes carry their choices in a dict, not a bare list.

    Reading only the old shape made every V3 input look like free text, so the
    missing-model check silently passed anything.
    """
    caps = Capabilities(
        node_types={"ResolutionSelector"},
        raw={"ResolutionSelector": {"input": {"required": {
            "aspect_ratio": ["COMBO", {"options": ["1:1 (Square)", "16:9 (Widescreen)"],
                                       "default": "1:1 (Square)"}],
            "megapixels": ["FLOAT", {"default": 1.0}],
        }}}},
        available=True,
    )
    assert caps.enum_options("ResolutionSelector", "aspect_ratio") == \
        ["1:1 (Square)", "16:9 (Widescreen)"]
    assert caps.enum_options("ResolutionSelector", "megapixels") is None
    assert caps.enum_options("ResolutionSelector", "nope") is None


def test_old_style_enum_still_works():
    caps = make_caps({"CheckpointLoaderSimple": {"ckpt_name": ["a.safetensors"]}})
    assert caps.enum_options("CheckpointLoaderSimple", "ckpt_name") == ["a.safetensors"]


def test_missing_model_is_caught_on_a_v3_style_input(tmp_path):
    p = tmp_path / "wf.json"
    p.write_text(json.dumps({
        "1": {"class_type": "NewStyleLoader", "inputs": {"ckpt_name": "gone.safetensors"}},
    }), encoding="utf-8")
    caps = Capabilities(
        node_types={"NewStyleLoader"},
        raw={"NewStyleLoader": {"input": {"required": {
            "ckpt_name": ["COMBO", {"options": ["present.safetensors"]}]}}}},
        available=True,
    )
    wf = check(load_workflow(p, "image"), caps)
    assert wf.missing_models == ["gone.safetensors"]
