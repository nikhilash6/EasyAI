"""Checks for packaging a new workflow into the catalogue.

The failure that matters most is the quiet one: a model recorded against the
wrong folder downloads perfectly and is never found, and a link that serves a
different build produces wrong output. Both are checked here.

Run with:  python -m pytest tests/test_authoring.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.comfy.objectinfo import Capabilities, FolderMap
from setup import authoring
from setup.authoring import AuthoringError, ModelNeed


# --- fakes -----------------------------------------------------------------
class FakeClient:
    """A ComfyUI that answers the two models routes."""

    def __init__(self, folders: dict[str, list[str]]):
        self._folders = folders

    def model_folders(self):
        return list(self._folders)

    def files_in_folder(self, folder):
        return list(self._folders.get(folder, []))


def caps_with(nodes: dict) -> Capabilities:
    return Capabilities(node_types=set(nodes), raw=nodes, available=True)


def combo(options):
    """The V3 input shape ComfyUI 0.33 uses."""
    return ["COMBO", {"options": list(options)}]


UNET_FILES = ["Flux 3\\flux3.safetensors", "other.safetensors"]
GGUF_FILES = ["Flux 2\\klein-Q8.gguf"]

NODES = {
    "UNETLoader": {"input": {"required": {"unet_name": combo(UNET_FILES)}}},
    "UnetLoaderGGUF": {"input": {"required": {"unet_name": combo(GGUF_FILES)}}},
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": combo([])}}},
}
FOLDERS = {
    "diffusion_models": UNET_FILES,
    "unet": GGUF_FILES,
    "loras": ["a.safetensors"],
    "checkpoints": [],
}


def api_graph():
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "Flux 3\\flux3.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }


# --- the folder question ---------------------------------------------------
def test_the_folder_comes_from_comfyui_not_the_field_name():
    """UNETLoader reads diffusion_models and UnetLoaderGGUF reads unet, and
    both call the field unet_name. Guessing from the field put nine models in
    folders ComfyUI never looks in."""
    folders = FolderMap(FakeClient(FOLDERS), caps_with(NODES))
    assert folders.folder_for("UNETLoader", "unet_name") == "diffusion_models"
    assert folders.folder_for("UnetLoaderGGUF", "unet_name") == "unet"
    # And the guess table would have said "unet" for both.
    assert authoring.FIELD_DIR["unet_name"] == "unet"


def test_an_unknown_loader_falls_back_rather_than_inventing():
    folders = FolderMap(FakeClient(FOLDERS), caps_with(NODES))
    assert folders.folder_for("SomeFutureLoader", "unet_name") is None


def test_a_file_in_exactly_one_folder_settles_it_without_an_option_list():
    folders = FolderMap(FakeClient(FOLDERS), caps_with({}))
    assert folders.folder_for("X", "y", "a.safetensors") == "loras"


def test_a_file_in_no_folder_is_not_guessed():
    folders = FolderMap(FakeClient(FOLDERS), caps_with({}))
    assert folders.folder_for("X", "y", "nowhere.safetensors") is None


# --- reading the file ------------------------------------------------------
def test_a_saved_workflow_is_refused_with_an_explanation(tmp_path):
    """The single most likely mistake: Save instead of Export (API)."""
    ui = tmp_path / "saved.json"
    ui.write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
    with pytest.raises(AuthoringError, match="Export \\(API\\)"):
        authoring.load_graph(ui)


def test_broken_json_says_so(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(AuthoringError, match="JSON"):
        authoring.load_graph(bad)


def test_an_api_workflow_loads(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(api_graph()), encoding="utf-8")
    assert len(authoring.load_graph(good)) == 3


def test_models_are_found_with_their_loader(tmp_path):
    found = authoring.models_in(api_graph())
    assert found == [("Flux 3\\flux3.safetensors", "UNETLoader", "unet_name")]


def test_finding_the_local_file_prefers_the_matching_subfolder(tmp_path):
    """qwen_image_vae.safetensors exists under several model folders; matching
    on the filename alone attributes the wrong copy."""
    root = tmp_path
    for sub in ("vae/Anima", "vae/Flux Krea 2"):
        (root / sub).mkdir(parents=True)
        (root / sub / "vae.safetensors").write_bytes(b"x")
    got = authoring.find_local(root, "Flux Krea 2\\vae.safetensors")
    assert got.parent.name == "Flux Krea 2"


# --- link checking ---------------------------------------------------------
def test_a_link_serving_a_different_build_is_rejected(monkeypatch):
    class Response:
        status_code = 200
        headers = {"Content-Length": "999"}

    monkeypatch.setattr(authoring.requests, "head", lambda *a, **k: Response())
    problem = authoring.check_link("https://x/y.safetensors", expected_bytes=1000)
    assert "different build" in problem


def test_a_link_of_the_right_size_passes(monkeypatch):
    class Response:
        status_code = 200
        headers = {"Content-Length": "1000"}

    monkeypatch.setattr(authoring.requests, "head", lambda *a, **k: Response())
    assert authoring.check_link("https://x/y.safetensors", 1000) == ""


def test_a_gated_link_is_reported_as_needing_an_account(monkeypatch):
    class Response:
        status_code = 401
        headers = {}

    monkeypatch.setattr(authoring.requests, "head", lambda *a, **k: Response())
    assert authoring.check_link("https://x/y", 10) == "needs-account"


def test_an_empty_link_is_not_accepted():
    assert authoring.check_link("   ", 10)


# --- merging ---------------------------------------------------------------
def test_merging_never_overwrites_a_verified_entry(tmp_path, monkeypatch):
    """A regenerate or a re-add must not throw away a link and hash that were
    checked against the real file."""
    catalog = {
        "groups": {}, "nodes": {},
        "models": {
            "Flux 3\\flux3.safetensors": {
                "dir": "diffusion_models", "bytes": 1000,
                "url": "https://verified/flux3.safetensors",
                "sha256": "abc", "mirror": "https://mirror/x",
            }
        },
    }
    monkeypatch.setattr(authoring, "CATALOG", tmp_path / "catalog.json")
    monkeypatch.setattr(authoring, "OVERRIDES", tmp_path / "overrides.json")
    authoring._write_json(authoring.CATALOG, catalog)

    result = authoring.Analysis(path=tmp_path / "wf.json", group="image", name="wf")
    result.models = [ModelNeed(name="Flux 3\\flux3.safetensors",
                               class_type="UNETLoader", field="unet_name",
                               folder="diffusion_models", bytes=1000,
                               url="https://somewhere-else/flux3.safetensors",
                               known=True)]
    authoring.apply(result, copy_workflow=False)

    after = json.loads(authoring.CATALOG.read_text(encoding="utf-8"))
    entry = after["models"]["Flux 3\\flux3.safetensors"]
    assert entry["url"] == "https://verified/flux3.safetensors"
    assert entry["sha256"] == "abc"
    assert entry["mirror"] == "https://mirror/x"


def test_a_new_link_is_written_where_a_regenerate_will_find_it(tmp_path, monkeypatch):
    """catalog.json is rebuilt from the side files, so a link recorded only in
    the catalogue would be lost the next time it is regenerated."""
    monkeypatch.setattr(authoring, "CATALOG", tmp_path / "catalog.json")
    monkeypatch.setattr(authoring, "OVERRIDES", tmp_path / "overrides.json")
    authoring._write_json(authoring.CATALOG, {"groups": {}, "nodes": {}, "models": {}})

    result = authoring.Analysis(path=tmp_path / "wf.json", group="image", name="wf")
    result.models = [ModelNeed(name="new.safetensors", class_type="UNETLoader",
                               field="unet_name", folder="diffusion_models",
                               bytes=50)]
    authoring.apply(result, links={"new.safetensors": "https://x/new.safetensors"},
                    copy_workflow=False)

    overrides = json.loads((tmp_path / "overrides.json").read_text(encoding="utf-8"))
    assert overrides["new.safetensors"] == "https://x/new.safetensors"


def test_a_problem_stops_the_add(tmp_path, monkeypatch):
    monkeypatch.setattr(authoring, "CATALOG", tmp_path / "catalog.json")
    result = authoring.Analysis(path=tmp_path / "wf.json", group="image", name="wf")
    result.problems.append("No add-on claims SomeNode.")
    with pytest.raises(AuthoringError, match="SomeNode"):
        authoring.apply(result, copy_workflow=False)


def test_the_group_totals_are_recalculated(tmp_path, monkeypatch):
    monkeypatch.setattr(authoring, "CATALOG", tmp_path / "catalog.json")
    monkeypatch.setattr(authoring, "OVERRIDES", tmp_path / "overrides.json")
    authoring._write_json(authoring.CATALOG, {"groups": {}, "nodes": {}, "models": {}})

    result = authoring.Analysis(path=tmp_path / "wf.json", group="image", name="wf")
    result.models = [
        ModelNeed(name="a.safetensors", class_type="X", field="unet_name",
                  folder="diffusion_models", bytes=100, url="https://x/a"),
        ModelNeed(name="b.safetensors", class_type="X", field="vae_name",
                  folder="vae", bytes=250, url="https://x/b"),
    ]
    authoring.apply(result, copy_workflow=False)

    after = json.loads(authoring.CATALOG.read_text(encoding="utf-8"))
    assert after["groups"]["image"]["bytes"] == 350


def test_only_the_three_supported_groups_are_accepted(tmp_path):
    good = tmp_path / "wf.json"
    good.write_text(json.dumps(api_graph()), encoding="utf-8")
    with pytest.raises(AuthoringError, match="only handles"):
        authoring.analyse(good, "music", None, Capabilities(), tmp_path)
