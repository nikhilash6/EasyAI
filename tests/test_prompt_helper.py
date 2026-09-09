"""The Prompt Helper tab: a workflow whose result is words, not a file.

Modelled on the real Krea 2 enhancer - a string primitive, a system prompt
concatenated in front of it, a text-generating node, and a PreviewAny to show
the answer.

Run with:  python -m pytest tests/test_prompt_helper.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.comfy.client import ComfyClient
from app.modes import MODE_ORDER, MODES
from app.workflows.manifest import autodetect
from app.workflows.patch import GenerationRequest, apply


def node(class_type, inputs, title=None):
    n = {"class_type": class_type, "inputs": inputs}
    if title:
        n["_meta"] = {"title": title}
    return n


@pytest.fixture
def enhancer_graph():
    return {
        "3": node("PrimitiveStringMultiline", {"value": "a fox in the snow"},
                  "Text String (System Prompt)"),
        "2": node("StringConcatenate",
                  {"string_a": "You are an expert prompt engineer for "
                               "text-to-image models.",
                   "string_b": ["3", 0], "delimiter": ""},
                  "Concatenate Text"),
        "1": node("TextGenerate",
                  {"prompt": ["2", 0], "max_length": 512, "clip": ["8", 0]},
                  "Generate Text"),
        "4": node("PreviewAny", {"source": ["1", 0]}, "Preview as Text"),
        "8": node("CLIPLoader",
                  {"clip_name": r"Flux Krea 2\qwen3vl_4b_fp8_scaled.safetensors",
                   "type": "krea2"}, "Load CLIP"),
    }


# --- the mode -------------------------------------------------------------
def test_prompt_helper_is_a_tab():
    assert "prompt-enhancer" in MODE_ORDER
    mode = MODES["prompt-enhancer"]
    assert mode.produces_text
    assert not mode.uses_ratio


def test_the_other_modes_still_produce_files():
    for key in ("image", "video"):
        assert not MODES[key].produces_text


# --- detection ------------------------------------------------------------
def test_the_users_idea_is_the_prompt_not_the_system_text(enhancer_graph):
    """string_a holds the instructions; the editable idea is the primitive."""
    m = autodetect(enhancer_graph, mode="prompt-enhancer")
    assert (m.get("prompt").node, m.get("prompt").input) == ("3", "value")


def test_preview_any_does_not_look_like_a_wan_model(enhancer_graph):
    """"wan" is a substring of "previewany" - it used to match."""
    m = autodetect(enhancer_graph, mode="prompt-enhancer")
    assert m.engine == "krea2"


def test_patching_replaces_only_the_idea(enhancer_graph):
    m = autodetect(enhancer_graph, mode="prompt-enhancer")
    patched, _ = apply(enhancer_graph, m, GenerationRequest(prompt="a lighthouse"))
    assert patched["3"]["inputs"]["value"] == "a lighthouse"
    assert patched["2"]["inputs"]["string_a"].startswith("You are an expert")


# --- reading the result ---------------------------------------------------
class FakeClient(ComfyClient):
    def __init__(self, outputs):
        super().__init__("127.0.0.1:0")
        self._outputs = outputs

    def history(self, prompt_id):
        return {prompt_id: {"outputs": self._outputs}}


def test_text_is_read_from_a_preview_node():
    client = FakeClient({"4": {"text": ["A solitary fox standing alert."]}})
    assert client.collect_text("p") == ["A solitary fox standing alert."]


def test_blank_text_is_ignored():
    client = FakeClient({"4": {"text": ["   ", ""]}})
    assert client.collect_text("p") == []


def test_image_outputs_are_not_treated_as_text():
    client = FakeClient({"9": {"images": [{"filename": "a.png", "type": "output"}]}})
    assert client.collect_text("p") == []


def test_several_text_nodes_are_all_collected():
    client = FakeClient({"4": {"text": ["one"]}, "5": {"text": ["two"]}})
    assert sorted(client.collect_text("p")) == ["one", "two"]


# --- the tab --------------------------------------------------------------
def _tab(tmp_path):
    from PySide6.QtWidgets import QApplication

    from app.config import Config
    from app.ui.tabs import build_tab

    QApplication.instance() or QApplication([])
    cfg = Config(tmp_path / "settings.json")
    return build_tab("prompt-enhancer", cfg, ComfyClient("127.0.0.1:0"))


def test_tab_has_no_picture_preview(tmp_path):
    """The right column is a text box, so the shared preview must be optional."""
    tab = _tab(tmp_path)
    assert tab.preview is None
    assert hasattr(tab, "result_box")


def test_result_enables_copy_and_send(tmp_path):
    tab = _tab(tmp_path)
    assert not tab.copy_btn.isEnabled()
    tab._set_result("A solitary fox.")
    assert tab.copy_btn.isEnabled() and tab.send_btn.isEnabled()
    assert tab.result_box.toPlainText() == "A solitary fox."


def test_send_asks_for_the_chosen_tab(tmp_path):
    tab = _tab(tmp_path)
    tab._set_result("A solitary fox.")
    seen = []
    tab.send_to_requested.connect(lambda key, text: seen.append((key, text)))

    tab.send_combo.setCurrentIndex(tab.send_combo.findData("video"))
    tab._send_result()
    assert seen == [("video", "A solitary fox.")]


def test_send_offers_every_other_tab(tmp_path):
    tab = _tab(tmp_path)
    offered = [tab.send_combo.itemData(i) for i in range(tab.send_combo.count())]
    assert offered == ["image", "video"], "and never itself"


def test_nothing_is_sent_when_there_is_no_result(tmp_path):
    tab = _tab(tmp_path)
    seen = []
    tab.send_to_requested.connect(lambda *a: seen.append(a))
    tab._send_result()
    assert seen == []


def test_a_saved_prompt_can_be_reopened(tmp_path):
    tab = _tab(tmp_path)
    saved = tmp_path / "old.txt"
    saved.write_text("An earlier prompt.", encoding="utf-8")
    tab._show_saved(str(saved))
    assert tab.result_box.toPlainText() == "An earlier prompt."


def test_enhancer_switch_is_hidden_on_this_tab(tmp_path, enhancer_graph):
    """TextGenerate is an enhancer node, but here it IS the workflow."""
    from app.workflows.loader import load_workflow

    tab = _tab(tmp_path)
    p = tmp_path / "enh.json"
    p.write_text(json.dumps(enhancer_graph), encoding="utf-8")
    wf = load_workflow(p, "prompt-enhancer")
    assert wf.manifest.has("enhancer"), "the node is still detected"

    tab._on_workflow_picked(wf)
    assert not tab.enhance_check.isVisible(), "but the switch makes no sense here"
