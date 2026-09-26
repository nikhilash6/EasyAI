"""Setup updating an existing ComfyUI, and only fetching models it lacks.

Run with:  python -m pytest tests/test_setup_update.py -q

The rules under test:

* An existing ComfyUI is moved to the pinned version only when the user asks,
  and then to exactly that version - never "latest".
* Its EasyAI add-ons move with it, so the result is the combination that was
  actually tested, not new ComfyUI on old add-ons.
* A model ComfyUI can already see is never downloaded again, wherever it
  keeps it - models/clip counts for a text encoder just as models/text_encoders
  does.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from setup import existing
from setup.catalog import Catalog, Model, NodePack
from setup.steps import Installer

PINNED = "73c9bad4d21e7addbe1d13bc92eee0f1431b017d"
OLD = "7fe8a6138504f90ff7be82f3babf416da32876b1"


# --- a small catalogue and a fake ComfyUI --------------------------------------
@pytest.fixture
def catalog(tmp_path) -> Catalog:
    raw = {
        "comfyui": {"version": "0.37.0", "commit": PINNED,
                    "portable": {"release": "v0.37.0", "bytes": 1_000_000,
                                 "url": "https://example/ComfyUI.7z", "asset": "ComfyUI.7z"}},
        "nodes": {
            "comfyui-easy-use": {"source": "cnr", "id": "comfyui-easy-use",
                                 "version": "1.4.1", "url": "https://example/easy.zip"},
            "ComfyUI-GGUF": {"source": "git", "url": "https://github.com/x/ComfyUI-GGUF",
                             "commit": "edd981b10e1234567890"},
        },
        "models": {
            "QWEN 2.1\\qwen3vl_8b.safetensors": {"dir": "text_encoders", "bytes": 100,
                                                "url": "https://example/te"},
            "ZI\\z_image.safetensors": {"dir": "diffusion_models", "bytes": 200,
                                       "url": "https://example/zi"},
            "Flux 2\\klein.gguf": {"dir": "unet", "bytes": 300, "url": "https://example/k"},
            "handmade.safetensors": {"dir": "loras", "bytes": 5, "url": None},
        },
        "groups": {"image": {"label": "Image",
                             "nodes": ["comfyui-easy-use", "ComfyUI-GGUF"],
                             "models": ["QWEN 2.1\\qwen3vl_8b.safetensors",
                                        "ZI\\z_image.safetensors", "Flux 2\\klein.gguf",
                                        "handmade.safetensors"]}},
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return Catalog(path)


def make_comfy(target: Path, version: str = "0.33.0", commit: str = OLD) -> Path:
    """A portable folder with just enough in it to be recognised."""
    portable = target / "ComfyUI_windows_portable"
    comfy = portable / "ComfyUI"
    (comfy / ".git").mkdir(parents=True)
    (comfy / ".git" / "HEAD").write_text(commit + "\n", encoding="utf-8")
    (comfy / "comfyui_version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (comfy / "requirements.txt").write_text("comfyui-frontend-package\n", encoding="utf-8")
    python = portable / "python_embeded"
    python.mkdir(parents=True)
    (python / "python.exe").write_bytes(b"")
    return comfy


def add_pack(comfy: Path, name: str, version: str = "", commit: str = "") -> Path:
    folder = comfy / "custom_nodes" / name
    folder.mkdir(parents=True)
    if commit:
        (folder / ".git").mkdir()
        (folder / ".git" / "HEAD").write_text(commit + "\n", encoding="utf-8")
    else:
        (folder / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8")
    return folder


def put_model(folder: Path, relative: str, size: int = 10) -> Path:
    path = folder / relative.replace("\\", "/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


# --- reading an install --------------------------------------------------------
def test_the_version_comes_from_comfyuis_own_file(tmp_path):
    comfy = make_comfy(tmp_path, "0.33.0")
    assert existing.comfyui_version(comfy) == "0.33.0"
    assert existing.comfyui_version(tmp_path / "nowhere") is None


@pytest.mark.parametrize("newer,older", [("0.37.0", "0.33.0"), ("0.10.0", "0.9.9"),
                                         ("1.0.0", "0.99.0")])
def test_versions_compare_as_numbers_not_text(newer, older):
    """As text, "0.10.0" sorts before "0.9.9"."""
    assert existing.version_key(newer) > existing.version_key(older)


def test_a_detached_head_is_read_without_git(tmp_path):
    comfy = make_comfy(tmp_path, commit=PINNED)
    assert existing.git_head(comfy) == PINNED


def test_a_branch_head_is_followed_to_its_commit(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    (repo / ".git" / "refs" / "heads" / "master").write_text(PINNED + "\n", encoding="utf-8")
    assert existing.git_head(repo) == PINNED


def test_a_branch_only_in_packed_refs_is_found(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    (repo / ".git" / "packed-refs").write_text(
        f"# pack-refs with: peeled\n{PINNED} refs/heads/master\n", encoding="utf-8")
    assert existing.git_head(repo) == PINNED


def test_a_registry_pack_reports_its_version(tmp_path):
    comfy = make_comfy(tmp_path)
    state = existing.pack_state(add_pack(comfy, "comfyui-easy-use", version="1.3.6"))
    assert (state.present, state.source, state.version) == (True, "cnr", "1.3.6")


def test_a_git_pack_reports_its_commit(tmp_path):
    comfy = make_comfy(tmp_path)
    state = existing.pack_state(add_pack(comfy, "ComfyUI-GGUF", commit="abc123"))
    assert (state.source, state.commit) == ("git", "abc123")


def test_a_pack_matches_only_its_exact_pin():
    cnr = NodePack(name="e", source="cnr", version="1.4.1")
    assert existing.pack_matches(cnr, existing.PackState(True, "cnr", version="1.4.1"))
    assert not existing.pack_matches(cnr, existing.PackState(True, "cnr", version="1.3.6"))
    git = NodePack(name="g", source="git", commit="edd981b10e1234567890")
    assert existing.pack_matches(git, existing.PackState(True, "git", commit="edd981b10e12aaaa"))
    # The same pack from the registry instead of git is not the pinned one.
    assert not existing.pack_matches(git, existing.PackState(True, "cnr", version="1.1.10"))


# --- finding models where ComfyUI looks -----------------------------------------
def search_for(models: Path) -> dict:
    """What ComfyUI's folder_paths reports for the standard layout."""
    return {"text_encoders": [models / "text_encoders", models / "clip"],
            "diffusion_models": [models / "unet", models / "diffusion_models"],
            "loras": [models / "loras"]}


def test_a_text_encoder_in_clip_is_found(tmp_path):
    """The reported case: the Qwen text encoder sits in models/clip."""
    models = tmp_path / "models"
    put_model(models / "clip", "QWEN 2.1\\qwen3vl_8b.safetensors")
    model = Model(name="QWEN 2.1\\qwen3vl_8b.safetensors", folder="text_encoders", url="u")
    assert existing.find_model(model, models, search_for(models)) is not None


def test_an_old_folder_name_searches_its_whole_kind(tmp_path):
    """The catalogue says "unet"; ComfyUI also reads diffusion_models for that."""
    models = tmp_path / "models"
    put_model(models / "diffusion_models", "Flux 2\\klein.gguf")
    model = Model(name="Flux 2\\klein.gguf", folder="unet", url="u")
    assert existing.find_model(model, models, search_for(models)) is not None


def test_the_subfolder_in_the_name_has_to_match(tmp_path):
    """A workflow asking for ZI\\z.safetensors cannot load one in another folder."""
    models = tmp_path / "models"
    put_model(models / "diffusion_models", "Other\\z_image.safetensors")
    model = Model(name="ZI\\z_image.safetensors", folder="diffusion_models", url="u")
    assert existing.find_model(model, models, search_for(models)) is None


def test_without_comfyuis_answer_the_plain_layout_is_used(tmp_path):
    models = tmp_path / "models"
    put_model(models / "diffusion_models", "ZI\\z_image.safetensors")
    model = Model(name="ZI\\z_image.safetensors", folder="diffusion_models", url="u")
    assert existing.find_model(model, models, {}) is not None


def test_no_python_means_no_search_paths(tmp_path):
    assert existing.model_search_paths(tmp_path / "nothing") == {}


# --- the survey ------------------------------------------------------------------
def test_an_empty_folder_needs_everything(tmp_path, catalog):
    survey = existing.survey(catalog, tmp_path / "new", ["image"])
    assert not survey.comfy_found and not survey.needs_update
    assert len(survey.models_missing) == 3
    assert [m.name for m in survey.models_blocked] == ["handmade.safetensors"]
    assert survey.download_bytes(True) == 1_000_000 + 100 + 200 + 300
    assert survey.download_bytes(False) == 1_000_000


def test_an_old_install_is_offered_the_update(tmp_path, catalog):
    comfy = make_comfy(tmp_path, "0.33.0", OLD)
    add_pack(comfy, "comfyui-easy-use", version="1.3.6")
    survey = existing.survey(catalog, tmp_path, ["image"], search={})
    assert survey.comfy_found and survey.comfy_differs and survey.needs_update
    assert not survey.comfy_is_newer
    assert survey.packs_differ == ["comfyui-easy-use"]
    # ComfyUI is already there, so the portable is not downloaded again.
    assert survey.download_bytes(False) == 0


def test_an_up_to_date_install_needs_nothing(tmp_path, catalog):
    comfy = make_comfy(tmp_path, "0.37.0", PINNED)
    add_pack(comfy, "comfyui-easy-use", version="1.4.1")
    add_pack(comfy, "ComfyUI-GGUF", commit="edd981b10e1234567890")
    survey = existing.survey(catalog, tmp_path, ["image"], search={})
    assert not survey.needs_update


def test_a_newer_install_is_recognised_as_newer(tmp_path, catalog):
    make_comfy(tmp_path, "0.38.0", "f" * 40)
    survey = existing.survey(catalog, tmp_path, ["image"], search={})
    assert survey.comfy_differs and survey.comfy_is_newer


def test_models_already_present_are_not_counted_to_download(tmp_path, catalog):
    comfy = make_comfy(tmp_path, "0.37.0", PINNED)
    models = comfy / "models"
    put_model(models / "clip", "QWEN 2.1\\qwen3vl_8b.safetensors")
    survey = existing.survey(catalog, tmp_path, ["image"], search=search_for(models))
    assert [m.name for m in survey.models_present] == ["QWEN 2.1\\qwen3vl_8b.safetensors"]
    assert survey.missing_bytes == 200 + 300


# --- the install: ComfyUI --------------------------------------------------------
class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def installer_for(catalog, target, monkeypatch, **options) -> tuple[Installer, dict]:
    installer = Installer(catalog, target, ["image"], **options)
    seen = {"switch": Recorder(), "pip": Recorder(), "fetch": Recorder(),
            "update_pack": Recorder(), "install_pack": Recorder()}
    monkeypatch.setattr(installer, "_switch_repo", seen["switch"])
    monkeypatch.setattr(installer, "_pip_requirements", seen["pip"])
    monkeypatch.setattr(installer, "_fetch_model", seen["fetch"])
    monkeypatch.setattr(installer, "_update_pack", seen["update_pack"])
    monkeypatch.setattr(installer, "_install_pack", seen["install_pack"])
    monkeypatch.setattr(existing, "model_search_paths", lambda portable, timeout=60: {})
    return installer, seen


def test_an_existing_comfyui_is_left_alone_unless_asked(tmp_path, catalog, monkeypatch):
    make_comfy(tmp_path, "0.33.0", OLD)
    installer, seen = installer_for(catalog, tmp_path, monkeypatch)
    installer.pin_comfyui_commit()
    assert seen["switch"].calls == []
    assert "pin" in installer.report.skipped


def test_asked_to_update_it_moves_to_the_pinned_commit(tmp_path, catalog, monkeypatch):
    make_comfy(tmp_path, "0.33.0", OLD)
    installer, seen = installer_for(catalog, tmp_path, monkeypatch, update_existing=True)
    installer.pin_comfyui_commit()
    (repo, commit), kwargs = seen["switch"].calls[0]
    assert commit == PINNED and kwargs["tag"] == "v0.37.0"
    # ...and its Python packages follow, recorded where ComfyUI's updater looks.
    (folder,), pip_kwargs = seen["pip"].calls[0]
    assert folder == installer.comfy
    assert pip_kwargs["remember"].name == "current_requirements.txt"
    assert "pin" in installer.report.done


def test_a_comfyui_this_run_unpacked_is_always_pinned(tmp_path, catalog, monkeypatch):
    make_comfy(tmp_path, "0.37.0", "a" * 40)
    installer, seen = installer_for(catalog, tmp_path, monkeypatch)
    installer._fresh = True
    installer.pin_comfyui_commit()
    assert seen["switch"].calls, "a fresh portable is ours to pin"


def test_nothing_happens_when_it_is_already_pinned(tmp_path, catalog, monkeypatch):
    make_comfy(tmp_path, "0.37.0", PINNED)
    installer, seen = installer_for(catalog, tmp_path, monkeypatch, update_existing=True)
    installer.pin_comfyui_commit()
    assert seen["switch"].calls == [] and seen["pip"].calls == []


def test_a_comfyui_without_git_cannot_be_moved(tmp_path, catalog, monkeypatch):
    comfy = make_comfy(tmp_path, "0.33.0", OLD)
    (comfy / ".git" / "HEAD").unlink()
    (comfy / ".git").rmdir()
    installer, _ = installer_for(catalog, tmp_path, monkeypatch, update_existing=True)
    with pytest.raises(OSError):
        installer.pin_comfyui_commit()


def test_the_update_never_asks_for_latest():
    """ComfyUI's own updater pulls master and the newest tag. Ours must only
    ever name the pinned commit."""
    from setup import steps

    assert "master" not in steps._SWITCH
    assert "latest" not in steps._SWITCH.lower()
    assert "GIT_CHECKOUT_SAFE" in steps._SWITCH
    assert "GIT_CHECKOUT_FORCE" not in steps._SWITCH


# --- the install: add-ons ---------------------------------------------------------
def test_outdated_add_ons_are_left_alone_unless_asked(tmp_path, catalog, monkeypatch):
    comfy = make_comfy(tmp_path)
    add_pack(comfy, "comfyui-easy-use", version="1.3.6")
    add_pack(comfy, "ComfyUI-GGUF", commit="0ld")
    installer, seen = installer_for(catalog, tmp_path, monkeypatch)
    installer.install_nodes()
    assert seen["update_pack"].calls == []
    assert {"comfyui-easy-use", "ComfyUI-GGUF"} <= set(installer.report.skipped)


def test_asked_to_update_outdated_add_ons_are_moved(tmp_path, catalog, monkeypatch):
    comfy = make_comfy(tmp_path)
    add_pack(comfy, "comfyui-easy-use", version="1.3.6")
    add_pack(comfy, "ComfyUI-GGUF", commit="edd981b10e1234567890")       # already right
    installer, seen = installer_for(catalog, tmp_path, monkeypatch, update_existing=True)
    installer.install_nodes()
    moved = [args[0].name for args, _ in seen["update_pack"].calls]
    assert moved == ["comfyui-easy-use"]


def test_a_replaced_add_on_is_restored_if_the_new_one_fails(tmp_path, catalog):
    comfy = make_comfy(tmp_path)
    folder = add_pack(comfy, "comfyui-easy-use", version="1.3.6")
    (folder / "user_config.json").write_text("{}", encoding="utf-8")
    installer = Installer(catalog, tmp_path, ["image"], update_existing=True)

    def broken(pack, destination):
        destination.mkdir()
        raise OSError("the registry is down")

    installer._install_pack = broken
    pack = catalog.nodes["comfyui-easy-use"]
    with pytest.raises(OSError):
        installer._update_pack(pack, folder, existing.pack_state(folder))
    assert (folder / "user_config.json").is_file(), "the old copy must come back"
    assert not list((comfy / "custom_nodes").glob("*.disabled"))


def test_a_replaced_add_on_leaves_no_copy_behind(tmp_path, catalog):
    comfy = make_comfy(tmp_path)
    folder = add_pack(comfy, "comfyui-easy-use", version="1.3.6")
    installer = Installer(catalog, tmp_path, ["image"], update_existing=True)

    def fine(pack, destination):
        destination.mkdir()
        (destination / "pyproject.toml").write_text(
            '[project]\nname = "comfyui-easy-use"\nversion = "1.4.1"\n', encoding="utf-8")

    installer._install_pack = fine
    installer._update_pack(catalog.nodes["comfyui-easy-use"], folder,
                           existing.pack_state(folder))
    assert existing.pack_state(folder).version == "1.4.1"
    assert [p.name for p in (comfy / "custom_nodes").iterdir()] == ["comfyui-easy-use"]


# --- the install: models ------------------------------------------------------------
def test_models_can_be_left_out_entirely(tmp_path, catalog, monkeypatch):
    make_comfy(tmp_path)
    installer, seen = installer_for(catalog, tmp_path, monkeypatch, download_models=False)
    installer.install_models()
    assert seen["fetch"].calls == []
    assert "models" in installer.report.skipped


def test_only_models_comfyui_cannot_see_are_fetched(tmp_path, catalog, monkeypatch):
    comfy = make_comfy(tmp_path)
    models = comfy / "models"
    put_model(models / "clip", "QWEN 2.1\\qwen3vl_8b.safetensors")
    installer, seen = installer_for(catalog, tmp_path, monkeypatch)
    monkeypatch.setattr(existing, "model_search_paths",
                        lambda portable, timeout=60: search_for(models))
    installer.install_models()

    fetched = [args[0].name for args, _ in seen["fetch"].calls]
    assert fetched == ["ZI\\z_image.safetensors", "Flux 2\\klein.gguf"]
    # New files go where the catalogue says, subfolder included.
    destination = seen["fetch"].calls[0][0][1]
    assert destination == models / "diffusion_models" / "ZI\\z_image.safetensors"


def test_a_model_present_elsewhere_is_not_reported_as_missing(tmp_path, catalog, monkeypatch):
    """A hand-copied model with no link is fine once ComfyUI can see it."""
    comfy = make_comfy(tmp_path)
    put_model(comfy / "models" / "loras", "handmade.safetensors")
    installer, _ = installer_for(catalog, tmp_path, monkeypatch)
    monkeypatch.setattr(existing, "model_search_paths",
                        lambda portable, timeout=60: search_for(comfy / "models"))
    installer.install_models()
    assert not [f for f in installer.report.failed if "handmade" in f]


def test_the_space_check_counts_only_what_will_download(tmp_path, catalog, monkeypatch):
    """An update with every model present needs almost no room."""
    comfy = make_comfy(tmp_path)
    for model in catalog.models.values():
        put_model(comfy / "models" / model.folder, model.name)
    installer, _ = installer_for(catalog, tmp_path, monkeypatch)
    monkeypatch.setattr("setup.steps.free_space", lambda target: 1)
    installer.check_space()          # would raise if it counted 600 bytes of models


# --- the catalogue generator ---------------------------------------------------------
def load_make_catalog():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import make_catalog
    return make_catalog


def test_an_official_link_replaces_an_unofficial_one():
    """flux2-vae.safetensors is also in an unrelated 3D project's repository."""
    mc = load_make_catalog()
    found = {}
    mc._offer(found, "flux2-vae.safetensors", "https://huggingface.co/VAST-AI/TripoSplat/x")
    mc._offer(found, "flux2-vae.safetensors", "https://huggingface.co/Comfy-Org/flux2-dev/x")
    assert "Comfy-Org" in found["flux2-vae.safetensors"]
    mc._offer(found, "flux2-vae.safetensors", "https://huggingface.co/Someone/else/x")
    assert "Comfy-Org" in found["flux2-vae.safetensors"]


def test_links_already_in_the_catalogue_are_kept(tmp_path, monkeypatch):
    mc = load_make_catalog()
    out = tmp_path / "catalog.json"
    out.write_text(json.dumps({"models": {"a.safetensors": {"url": "https://verified/a"},
                                          "b.safetensors": {"url": None}}}), encoding="utf-8")
    monkeypatch.setattr(mc, "OUT", out)
    assert mc.previous_links() == {"a.safetensors": "https://verified/a"}


# --- the screens ------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_middle_column_is_never_narrower_than_its_contents(qt_app):
    from PySide6.QtWidgets import QLabel
    from app.ui.widgets import ColumnScroll

    wide = QLabel("x" * 200)
    scroll = ColumnScroll()
    scroll.setWidget(wide)
    assert scroll.minimumSizeHint().width() >= wide.minimumSizeHint().width()


def test_create_stays_outside_the_scrolling_part(qt_app, tmp_path):
    from app.config import Config
    from app.ui.tabs import build_tab

    class Offline:
        server = "127.0.0.1:1"
        def is_alive(self): return False

    cfg = Config(tmp_path / "settings.json")
    cfg.set("output_dir", str(tmp_path / "output"))
    tab = build_tab("image", cfg, Offline())
    inner = tab.middle_scroll.widget()
    assert inner.isAncestorOf(tab.prompt_box)
    assert not inner.isAncestorOf(tab.create_btn)
    assert not inner.isAncestorOf(tab.status_label)


def setup_window(catalog, folder):
    from setup.ui import SetupWindow

    window = SetupWindow(catalog)
    window.folder.setText(str(folder))
    window._recalculate()
    return window


def test_setup_offers_the_update_for_an_old_install(qt_app, tmp_path, catalog, monkeypatch):
    monkeypatch.setattr(existing, "model_search_paths", lambda portable, timeout=60: {})
    make_comfy(tmp_path, "0.33.0", OLD)
    window = setup_window(catalog, tmp_path)
    assert not window.update_switch.isHidden()
    assert window.update_switch.isChecked(), "on by default for an older ComfyUI"
    assert "0.33.0" in window.found.text()


def test_setup_does_not_pre_tick_going_back_a_version(qt_app, tmp_path, catalog, monkeypatch):
    monkeypatch.setattr(existing, "model_search_paths", lambda portable, timeout=60: {})
    make_comfy(tmp_path, "0.38.0", "f" * 40)
    window = setup_window(catalog, tmp_path)
    assert not window.update_switch.isHidden()
    assert not window.update_switch.isChecked(), "a newer ComfyUI was someone's choice"


def test_setup_says_nothing_to_update_when_current(qt_app, tmp_path, catalog, monkeypatch):
    monkeypatch.setattr(existing, "model_search_paths", lambda portable, timeout=60: {})
    comfy = make_comfy(tmp_path, "0.37.0", PINNED)
    add_pack(comfy, "comfyui-easy-use", version="1.4.1")
    add_pack(comfy, "ComfyUI-GGUF", commit="edd981b10e1234567890")
    window = setup_window(catalog, tmp_path)
    assert window.update_switch.isHidden()


def test_setup_keeps_the_users_choice_when_a_group_is_ticked(qt_app, tmp_path, catalog, monkeypatch):
    monkeypatch.setattr(existing, "model_search_paths", lambda portable, timeout=60: {})
    make_comfy(tmp_path, "0.33.0", OLD)
    window = setup_window(catalog, tmp_path)
    window.update_switch.setChecked(False)
    window._recalculate()                 # what ticking a group card does
    assert not window.update_switch.isChecked()


def test_setup_passes_both_choices_to_the_install(qt_app, tmp_path, catalog, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    import setup.ui as ui

    monkeypatch.setattr(existing, "model_search_paths", lambda portable, timeout=60: {})
    make_comfy(tmp_path, "0.33.0", OLD)
    window = setup_window(catalog, tmp_path)
    window.models_switch.setChecked(False)

    made = {}

    class Worker:
        def __init__(self, *args, **kwargs):
            made.update(kwargs)
            self.step = self.progress = self.finished_ok = self.finished = self

        def connect(self, *_):
            pass

        def start(self):
            pass

    monkeypatch.setattr(ui, "InstallWorker", Worker)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    window._start()
    assert made == {"update_existing": True, "download_models": False}


# --- a dropped connection while fetching an add-on --------------------------------
def test_a_folder_git_wrote_read_only_files_into_is_removed(tmp_path):
    """shutil.rmtree gives up on git's read-only object files on Windows."""
    import stat
    from setup.steps import remove_tree

    objects = tmp_path / "pack" / ".git" / "objects" / "pack"
    objects.mkdir(parents=True)
    locked = objects / "pack-abc.pack"
    locked.write_bytes(b"x")
    os.chmod(locked, stat.S_IREAD)

    remove_tree(tmp_path / "pack")
    assert not (tmp_path / "pack").exists()


def test_one_dropped_fetch_is_retried_rather_than_giving_up(tmp_path, catalog):
    """A transfer that dies half-way left a .git behind, and the fallback clone
    then failed with a misleading 'destination path already exists'."""
    import subprocess

    installer = Installer(catalog, tmp_path, ["image"])
    calls = []

    def git(cwd, *args, check=True):
        calls.append(args[0])
        if args[0] == "fetch" and calls.count("fetch") == 1:
            (Path(cwd) / ".git" / "objects").mkdir(parents=True, exist_ok=True)
            (Path(cwd) / ".git" / "objects" / "tmp_pack").write_bytes(b"half")
            raise subprocess.SubprocessError("the connection was reset")
        (Path(cwd) / ".git").mkdir(exist_ok=True)
        return ""

    installer._git = git
    destination = tmp_path / "custom_nodes" / "ComfyUI-GGUF"
    installer._clone_at_commit(catalog.nodes["ComfyUI-GGUF"], destination)
    assert calls.count("fetch") == 2
    assert "clone" not in calls, "the retry should have been enough"
    assert not (destination / ".git" / "objects" / "tmp_pack").exists()


def test_the_first_error_is_reported_when_everything_fails(tmp_path, catalog):
    import subprocess

    installer = Installer(catalog, tmp_path, ["image"])

    def git(cwd, *args, check=True):
        if args[0] in ("fetch", "clone"):
            raise subprocess.SubprocessError(f"{args[0]} broke")
        return ""

    installer._git = git
    with pytest.raises(subprocess.SubprocessError) as caught:
        installer._clone_at_commit(catalog.nodes["ComfyUI-GGUF"],
                                   tmp_path / "custom_nodes" / "ComfyUI-GGUF")
    assert "fetch broke" in str(caught.value), "the real cause, not only the last one"
