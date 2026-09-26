"""setup-settings.json - the editable install list beside EasyAI Setup.

Run with:  python -m pytest tests/test_install_list.py -q

Written after Qwen Image 2.1 was found missing from an install. Its workflow
had never been added to Setup's list, and the list was built into the .exe,
so nobody could see or fix that without rebuilding. The file puts every model
and workflow - where each comes from and where it goes - where it can be read
and edited, and these tests hold it to three things:

* it describes exactly what the built-in list does, until someone edits it;
* an edit really changes what is installed and where;
* an old file can never silently hide what a newer Setup added.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from setup import existing, install_list
from setup.catalog import Catalog
from setup.steps import Installer


def builtin_raw(extra_workflow: bool = False) -> dict:
    workflows = ["Krea 2 t2i.json"] + (["Qwen Image 2.1.json"] if extra_workflow else [])
    models = ["Flux Krea 2\\krea2.safetensors"] + (["QWEN 2.1\\qwen.safetensors"] if extra_workflow else [])
    return {
        "comfyui": {"version": "0.37.0", "commit": "73c9bad4",
                    "portable": {"url": "https://example/c.7z", "bytes": 10, "asset": "c.7z"}},
        "nodes": {"ComfyUI-GGUF": {"source": "cnr", "id": "ComfyUI-GGUF", "version": "1.1.10"}},
        "models": {
            "Flux Krea 2\\krea2.safetensors": {"dir": "diffusion_models", "bytes": 5,
                                               "url": "https://example/krea2"},
            "QWEN 2.1\\qwen.safetensors": {"dir": "diffusion_models", "bytes": 7,
                                           "url": "https://example/qwen"},
            "KNP.safetensors": {"dir": "loras", "bytes": 3, "url": "https://example/knp",
                                "mirror": "https://mirror/knp", "sha256": "abc", "gated": True},
        },
        "groups": {"image": {"label": "Image", "nodes": ["ComfyUI-GGUF"],
                             "models": models + ["KNP.safetensors"], "workflows": workflows}},
    }


@pytest.fixture
def builtin(tmp_path) -> Path:
    path = tmp_path / "bundle" / "catalog.json"
    path.parent.mkdir()
    path.write_text(json.dumps(builtin_raw()), encoding="utf-8")
    return path


@pytest.fixture
def list_path(tmp_path) -> Path:
    folder = tmp_path / "beside-setup"
    folder.mkdir()
    return folder / install_list.FILE_NAME


def edit(path: Path, change) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


# --- the file mirrors the built-in list -------------------------------------------
def test_the_first_run_writes_the_file_out(list_path, builtin):
    loaded = install_list.load(list_path, builtin)
    assert loaded.status == "created" and list_path.is_file()
    doc = json.loads(list_path.read_text(encoding="utf-8"))
    model = doc["models"]["KNP.safetensors"]
    assert model == {"kind": "loras", "from": "https://example/knp", "mirror": "https://mirror/knp",
                     "to": "", "bytes": 3, "sha256": "abc", "needs_account": True}
    assert doc["groups"]["image"]["workflows"] == [
        {"file": "Krea 2 t2i.json", "from": "built-in", "to": "image"}]
    assert doc["folders"] == {"models": "", "workflows": "EasyAI-workflows"}
    assert doc["_how_to_use"], "the file explains itself"


def test_it_describes_exactly_what_the_built_in_list_does(list_path, builtin):
    loaded = install_list.load(list_path, builtin)
    direct = Catalog(builtin)
    for name, model in direct.models.items():
        mine = loaded.catalog.models[name]
        assert (mine.url, mine.mirror, mine.folder, mine.bytes, mine.sha256, mine.gated) == \
               (model.url, model.mirror, model.folder, model.bytes, model.sha256, model.gated)
    assert loaded.catalog.nodes == direct.nodes
    assert loaded.catalog.comfyui == direct.comfyui
    assert loaded.catalog.groups["image"].models == direct.groups["image"].models


def test_the_real_built_in_list_round_trips(tmp_path):
    """Against the catalogue that actually ships, not a small fake."""
    loaded = install_list.load(tmp_path / install_list.FILE_NAME)
    direct = Catalog()
    assert set(loaded.catalog.models) == set(direct.models)
    assert all(loaded.catalog.models[k].url == m.url and loaded.catalog.models[k].folder == m.folder
               for k, m in direct.models.items())


def test_a_second_run_uses_the_file(list_path, builtin):
    install_list.load(list_path, builtin)
    assert install_list.load(list_path, builtin).status == "current"


# --- an old file beside a newer Setup ----------------------------------------------
def test_an_untouched_old_file_is_brought_up_to_date(list_path, builtin):
    """The reported case: a newer Setup adds Qwen, and the old file must not hide it."""
    install_list.load(list_path, builtin)
    builtin.write_text(json.dumps(builtin_raw(extra_workflow=True)), encoding="utf-8")

    loaded = install_list.load(list_path, builtin)
    assert loaded.status == "refreshed"
    assert "Qwen Image 2.1.json" in loaded.catalog.groups["image"].workflows


def test_an_edited_old_file_is_kept_but_flagged(list_path, builtin):
    install_list.load(list_path, builtin)
    edit(list_path, lambda d: d["folders"].update(models="D:/AI Models"))
    builtin.write_text(json.dumps(builtin_raw(extra_workflow=True)), encoding="utf-8")

    loaded = install_list.load(list_path, builtin)
    assert loaded.status == "outdated"
    assert loaded.missing == ["Qwen Image 2.1.json"]
    assert loaded.catalog.folders["models"] == "D:/AI Models", "the edit is someone's work"


def test_replacing_keeps_the_edited_copy(list_path, builtin):
    install_list.load(list_path, builtin)
    edit(list_path, lambda d: d["folders"].update(models="D:/AI Models"))
    backup = install_list.replace_with_builtin(list_path, builtin)
    assert backup.name == install_list.BACKUP_NAME
    assert json.loads(backup.read_text(encoding="utf-8"))["folders"]["models"] == "D:/AI Models"
    assert install_list.load(list_path, builtin).status == "current"


# --- mistakes in the file ----------------------------------------------------------
def test_a_single_backslash_is_explained(list_path, builtin):
    install_list.load(list_path, builtin)
    text = list_path.read_text(encoding="utf-8")
    list_path.write_text(text.replace('"models": ""', '"models": "D:\\AI Models"', 1),
                         encoding="utf-8")
    with pytest.raises(install_list.InstallListError) as caught:
        install_list.load(list_path, builtin)
    assert "forward slashes" in str(caught.value)


def test_a_group_naming_an_unknown_model_is_refused(list_path, builtin):
    install_list.load(list_path, builtin)
    edit(list_path, lambda d: d["groups"]["image"]["models"].append("nowhere.safetensors"))
    with pytest.raises(install_list.InstallListError) as caught:
        install_list.load(list_path, builtin)
    assert "nowhere.safetensors" in str(caught.value)


def test_a_folder_that_cannot_be_written_still_works(tmp_path, builtin):
    """Setup placed in a read-only folder falls back to its own list."""
    loaded = install_list.load(tmp_path / "missing-folder" / install_list.FILE_NAME, builtin)
    assert loaded.status == "unwritable"
    assert "Krea 2 t2i.json" in loaded.catalog.groups["image"].workflows


# --- edits change what happens -----------------------------------------------------
def test_moving_the_models_folder_moves_every_model(list_path, builtin, tmp_path):
    install_list.load(list_path, builtin)
    edit(list_path, lambda d: d["folders"].update(models=(tmp_path / "AI Models").as_posix()))
    catalog = install_list.load(list_path, builtin).catalog
    model = catalog.models["Flux Krea 2\\krea2.safetensors"]
    destination = catalog.model_destination(model, tmp_path / "ComfyUI" / "models", tmp_path)
    assert destination == tmp_path / "AI Models" / "diffusion_models" / "Flux Krea 2" / "krea2.safetensors"


def test_one_model_can_have_a_folder_of_its_own(list_path, builtin, tmp_path):
    install_list.load(list_path, builtin)
    edit(list_path, lambda d: d["models"]["KNP.safetensors"].update(to="E:/LoRAs"))
    catalog = install_list.load(list_path, builtin).catalog
    destination = catalog.model_destination(catalog.models["KNP.safetensors"],
                                            tmp_path / "ComfyUI" / "models", tmp_path)
    assert destination == Path("E:/LoRAs") / "KNP.safetensors"


# --- the installer follows the file --------------------------------------------------
def install_target(tmp_path) -> Path:
    comfy = tmp_path / "install" / "ComfyUI_windows_portable" / "ComfyUI"
    (comfy / "models").mkdir(parents=True)
    return tmp_path / "install"


def test_models_land_where_the_file_says_and_comfyui_is_told(list_path, builtin, tmp_path, monkeypatch):
    install_list.load(list_path, builtin)
    elsewhere = (tmp_path / "AI Models").as_posix()
    edit(list_path, lambda d: d["folders"].update(models=elsewhere))
    catalog = install_list.load(list_path, builtin).catalog
    target = install_target(tmp_path)

    installer = Installer(catalog, target, ["image"])
    monkeypatch.setattr(existing, "model_search_paths", lambda portable, timeout=60: {})
    written = []

    def fetch(model, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x" * model.bytes)
        written.append(destination)

    installer._fetch_model = fetch
    installer.install_models()
    assert all(str(p).startswith(str(tmp_path / "AI Models")) for p in written) and written

    yaml = (installer.comfy / "extra_model_paths.yaml").read_text(encoding="utf-8")
    assert "easyai_setup:" in yaml
    assert f"{elsewhere}/diffusion_models" in yaml and f"{elsewhere}/loras" in yaml

    # A second run fetches nothing: the files are where the list put them.
    written.clear()
    installer.install_models()
    assert written == []


def test_the_users_own_extra_paths_are_left_alone(list_path, builtin, tmp_path, monkeypatch):
    target = install_target(tmp_path)
    yaml_path = target / "ComfyUI_windows_portable" / "ComfyUI" / "extra_model_paths.yaml"
    mine = "my_models:\n    base_path: E:/Mine\n    loras: loras\n"
    yaml_path.write_text(mine, encoding="utf-8")

    install_list.load(list_path, builtin)
    edit(list_path, lambda d: d["folders"].update(models=(tmp_path / "AI").as_posix()))
    installer = Installer(install_list.load(list_path, builtin).catalog, target, ["image"])
    models = installer.catalog.models_for(["image"])

    installer.register_model_folders(models)
    installer.register_model_folders(models)          # twice: one block, not two
    text = yaml_path.read_text(encoding="utf-8")
    assert text.startswith(mine)
    assert text.count("easyai_setup:") == 1

    # Moving everything back into ComfyUI removes the block, and only the block.
    edit(list_path, lambda d: d["folders"].update(models=""))
    installer.catalog = install_list.load(list_path, builtin).catalog
    installer.register_model_folders(models)
    assert yaml_path.read_text(encoding="utf-8").strip() == mine.strip()


def test_nothing_is_registered_when_models_stay_in_comfyui(list_path, builtin, tmp_path):
    install_list.load(list_path, builtin)
    target = install_target(tmp_path)
    installer = Installer(install_list.load(list_path, builtin).catalog, target, ["image"])
    installer.register_model_folders(installer.catalog.models_for(["image"]))
    assert not (installer.comfy / "extra_model_paths.yaml").exists()


def test_workflows_come_from_where_the_file_says(list_path, builtin, tmp_path, monkeypatch):
    """Built in, a file beside the list, and a web link - each to its folder."""
    import setup.steps as steps

    bundled = tmp_path / "bundle-root" / "workflows" / "image"
    bundled.mkdir(parents=True)
    (bundled / "Krea 2 t2i.json").write_text("{}", encoding="utf-8")
    (bundled / "Krea 2 t2i.manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(steps, "ROOT", tmp_path / "bundle-root")

    (list_path.parent / "mine").mkdir()
    (list_path.parent / "mine" / "Qwen.json").write_text('{"q": 1}', encoding="utf-8")
    fetched = []
    monkeypatch.setattr(steps, "download",
                        lambda url, dest, **k: (fetched.append(url), Path(dest).write_text("{}")))

    install_list.load(list_path, builtin)

    def add(doc):
        doc["groups"]["image"]["workflows"] += [
            {"file": "Qwen.json", "from": "mine/Qwen.json", "to": "image"},
            {"file": "Web.json", "from": "https://example/Web.json", "to": "extra"},
        ]
        doc["folders"]["workflows"] = "My Workflows"
    edit(list_path, add)

    target = install_target(tmp_path)
    installer = Installer(install_list.load(list_path, builtin).catalog, target, ["image"])
    installer.copy_workflows()

    root = target / "My Workflows"
    assert (root / "image" / "Krea 2 t2i.json").is_file()
    assert (root / "image" / "Krea 2 t2i.manifest.json").is_file(), "its setup travels with it"
    assert json.loads((root / "image" / "Qwen.json").read_text()) == {"q": 1}
    assert (root / "extra" / "Web.json").is_file() and fetched == ["https://example/Web.json"]
    assert installer.report.failed == []


def test_a_missing_workflow_is_reported_and_the_rest_still_copy(list_path, builtin, tmp_path, monkeypatch):
    import setup.steps as steps

    bundled = tmp_path / "bundle-root" / "workflows" / "image"
    bundled.mkdir(parents=True)
    (bundled / "Krea 2 t2i.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(steps, "ROOT", tmp_path / "bundle-root")
    install_list.load(list_path, builtin)
    edit(list_path, lambda d: d["groups"]["image"]["workflows"].insert(
        0, {"file": "Gone.json", "from": "C:/nowhere/Gone.json", "to": "image"}))

    target = install_target(tmp_path)
    installer = Installer(install_list.load(list_path, builtin).catalog, target, ["image"])
    installer.copy_workflows()
    assert any("Gone.json" in f for f in installer.report.failed)
    assert (target / "EasyAI-workflows" / "image" / "Krea 2 t2i.json").is_file()


def test_easyai_is_pointed_at_the_workflows_folder_the_file_names(list_path, builtin, tmp_path, monkeypatch):
    import app.config as config

    install_list.load(list_path, builtin)
    edit(list_path, lambda d: d["folders"].update(workflows="My Workflows"))
    target = install_target(tmp_path)
    portable = target / "ComfyUI_windows_portable"
    (portable / "python_embeded").mkdir()
    (portable / "python_embeded" / "python.exe").write_bytes(b"")
    (target / "My Workflows" / "image").mkdir(parents=True)
    (target / "My Workflows" / "image" / "a.json").write_text("{}")
    settings = tmp_path / "easyai-settings.json"
    monkeypatch.setattr(config, "SETTINGS_PATH", settings)

    installer = Installer(install_list.load(list_path, builtin).catalog, target, ["image"])
    installer.configure_easyai()
    assert json.loads(settings.read_text(encoding="utf-8"))["workflow_dir"] == str(target / "My Workflows")


# --- the list that ships ---------------------------------------------------------------
def test_every_workflow_in_the_folder_is_in_setups_list():
    """The guard against the original mistake: a workflow that EasyAI shows
    but EasyAI Setup would never install."""
    catalog = Catalog()
    for group in catalog.groups.values():
        folder = ROOT / "workflows" / group.key
        on_disk = {p.name for p in folder.glob("*.json") if not p.name.endswith(".manifest.json")}
        assert on_disk == set(group.workflows), (
            f"{group.key}: in the folder but not in setup/catalog.json: "
            f"{sorted(on_disk - set(group.workflows))}; listed but missing: "
            f"{sorted(set(group.workflows) - on_disk)} - run tools/make_catalog.py")


def test_qwen_image_2_1_is_installed_with_the_image_group():
    catalog = Catalog()
    image = catalog.groups["image"]
    assert any("qwen_image_2_1_image_edit" in w for w in image.workflows)
    assert any("qwen_image_2_1_t2i" in w for w in image.workflows)
    for name in ("QWEN 2.1\\qwen_image_2.1_int8_convrot.safetensors",
                 "QWEN 2.1\\qwen3vl_8b_int8_convrot.safetensors",
                 "QWEN 2.1\\qwen_image_2.1_vae_bf16.safetensors"):
        assert name in image.models and catalog.models[name].url, name


# --- the window ------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_setup_shows_where_the_list_is(qt_app, list_path, builtin, monkeypatch):
    from setup.ui import SetupWindow

    monkeypatch.setattr(existing, "model_search_paths", lambda portable, timeout=60: {})
    loaded = install_list.load(list_path, builtin)
    window = SetupWindow(loaded.catalog, loaded)
    assert str(list_path) in window.list_label.text()
    assert window.list_replace.isHidden()


def test_setup_offers_the_new_list_when_an_edited_one_is_outdated(qt_app, list_path, builtin, monkeypatch):
    from setup.ui import SetupWindow

    monkeypatch.setattr(existing, "model_search_paths", lambda portable, timeout=60: {})
    install_list.load(list_path, builtin)
    edit(list_path, lambda d: d["folders"].update(models="D:/AI Models"))
    builtin.write_text(json.dumps(builtin_raw(extra_workflow=True)), encoding="utf-8")
    loaded = install_list.load(list_path, builtin)

    window = SetupWindow(loaded.catalog, loaded)
    assert not window.list_replace.isHidden()
    assert "Qwen Image 2.1" in window.list_note.text()


def test_reload_picks_up_an_edit(qt_app, list_path, builtin, monkeypatch):
    from setup.ui import SetupWindow

    monkeypatch.setattr(existing, "model_search_paths", lambda portable, timeout=60: {})
    monkeypatch.setattr(install_list, "default_path", lambda: list_path)
    real_load = install_list.load
    monkeypatch.setattr(install_list, "load", lambda path=None, b=builtin: real_load(path, b))
    loaded = install_list.load(list_path)
    window = SetupWindow(loaded.catalog, loaded)

    edit(list_path, lambda d: d["groups"]["image"].update(label="Pictures"))
    window._reload_list()
    assert window.catalog.groups["image"].label == "Pictures"
