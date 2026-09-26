"""Packaging a new workflow so EasyAI Setup can install it for viewers.

Dropping a workflow into workflows/<group>/ already makes EasyAI itself use
it - loader.scan() finds it and manifest.autodetect() works out which inputs to
drive. What that does not do is tell EasyAI Setup where the models come from,
so a viewer who installs the group gets a workflow that cannot run.

This closes that gap: look at a workflow, work out every model and add-on it
needs, settle where each model belongs and where it can be downloaded from,
then write all of that into the catalogue.

It runs on the machine where the model already works, because that is the only
place the file's real size, folder and hash can be read rather than guessed.
No Qt here - the window in setup/ui.py is a thin layer over this.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import requests

from app.comfy import objectinfo
from app.comfy.objectinfo import Capabilities, FolderMap
from app.modes import MODE_ORDER, MODES
from app.paths import PROJECT_ROOT
from app.workflows.loader import Format, classify
from app.workflows.manifest import BINDING_KEYS, autodetect

CATALOG = PROJECT_ROOT / "setup" / "catalog.json"
OVERRIDES = PROJECT_ROOT / "setup" / "url_overrides.json"
MIRRORS = PROJECT_ROOT / "setup" / "mirror_links.json"
STAGING = PROJECT_ROOT / "mirror-staging"

#: Inputs that name a model file. Kept beside the generator's copy rather than
#: imported, because tools/ is not shipped with the built .exe.
MODEL_FIELDS = (
    "ckpt_name", "unet_name", "vae_name", "clip_name", "clip_name1", "clip_name2",
    "lora_name", "control_net_name", "model_name", "style_model_name", "gguf_name",
    "upscale_model_name", "text_encoder_name", "audio_encoder_name",
)

#: Last-resort folder guess, used only when ComfyUI cannot be asked. Flagged
#: wherever it is used, because this is exactly the guess that once put nine
#: models where ComfyUI never looks.
FIELD_DIR = {
    "ckpt_name": "checkpoints", "unet_name": "unet", "gguf_name": "unet",
    "vae_name": "vae", "clip_name": "clip", "clip_name1": "clip",
    "clip_name2": "clip", "text_encoder_name": "text_encoders",
    "lora_name": "loras", "control_net_name": "controlnet",
    "style_model_name": "style_models", "upscale_model_name": "upscale_models",
    "audio_encoder_name": "audio_encoders", "model_name": "checkpoints",
}


class AuthoringError(Exception):
    """Something that must be fixed before the workflow can be added."""


@dataclass
class ModelNeed:
    """One model a workflow refers to, and everything known about it."""
    name: str                       # as the workflow writes it
    class_type: str
    field: str
    folder: str = ""
    folder_guessed: bool = False    # true when ComfyUI could not be asked
    bytes: int = 0
    local: Path | None = None
    url: str = ""
    known: bool = False             # already in the catalogue
    gated: bool = False

    @property
    def filename(self) -> str:
        return Path(self.name).name

    @property
    def ready(self) -> bool:
        return bool(self.url) and bool(self.folder)


@dataclass
class NodeNeed:
    name: str                       # the add-on folder, or the class if unknown
    classes: list[str] = field(default_factory=list)
    known: bool = False
    source: str = ""                # git | cnr | "" when unresolved


@dataclass
class Analysis:
    """What adding this workflow would involve. Nothing is written yet."""
    path: Path
    group: str
    name: str = ""
    node_count: int = 0
    models: list[ModelNeed] = field(default_factory=list)
    nodes: list[NodeNeed] = field(default_factory=list)
    manifest_slots: list[str] = field(default_factory=list)
    missing_nodes: list[str] = field(default_factory=list)
    missing_models: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def new_models(self) -> list[ModelNeed]:
        return [m for m in self.models if not m.known]

    @property
    def needs_link(self) -> list[ModelNeed]:
        return [m for m in self.models if not m.url]

    @property
    def can_add(self) -> bool:
        return not self.problems and not self.needs_link


# --- reading the workflow --------------------------------------------------
def load_graph(path: Path) -> dict:
    """The API-format graph, or a plain explanation of why it is not one."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise AuthoringError(f"That file could not be read as JSON.\n\n{e}") from e

    kind = classify(raw)
    if kind is Format.API:
        return raw
    if kind in (Format.UI, Format.UI_WITH_API, Format.UI_STALE_API):
        raise AuthoringError(
            "That is a saved workflow, not an API workflow.\n\n"
            "In ComfyUI choose  Workflow → Export (API)  and pick the file that "
            "produces. A plain Save keeps the node positions rather than the "
            "graph EasyAI needs to run.")
    raise AuthoringError("That file is not a ComfyUI workflow.")


def models_in(graph: dict) -> list[tuple[str, str, str]]:
    """(filename, class_type, input) for every model the graph names."""
    found, seen = [], set()
    for node in graph.values():
        class_type = node.get("class_type", "")
        for field_name, value in (node.get("inputs") or {}).items():
            if field_name in MODEL_FIELDS and isinstance(value, str) and value.strip():
                if value not in seen:
                    seen.add(value)
                    found.append((value, class_type, field_name))
    return found


def find_local(models_root: Path, name: str) -> Path | None:
    """The real file on this machine, preferring an exact subfolder match."""
    wanted = Path(name)
    direct = models_root / name
    if direct.is_file():
        return direct
    matches = list(models_root.rglob(wanted.name))
    if not matches:
        return None
    tail = wanted.parts
    exact = [m for m in matches if m.parts[-len(tail):] == tail]
    return exact[0] if exact else matches[0]


# --- looking it over -------------------------------------------------------
def analyse(path: Path, group: str, client, caps: Capabilities,
            models_root: Path, catalog: dict | None = None,
            harvested: dict[str, str] | None = None,
            owner_of=None, core_classes=None) -> Analysis:
    """Everything adding this workflow would involve, without writing anything.

    ``owner_of`` and ``core_classes`` are passed in rather than imported so the
    console tool can supply the generator's versions and the tests can supply
    fakes; both need the ComfyUI install, which this module does not own.
    """
    if group not in MODE_ORDER:
        raise AuthoringError(
            f"EasyAI only handles {', '.join(MODES[m].label for m in MODE_ORDER)}.")

    graph = load_graph(path)
    result = Analysis(path=Path(path), group=group, name=Path(path).stem,
                      node_count=len(graph))
    catalog = catalog if catalog is not None else read_catalog()
    known_models = catalog.get("models", {})
    known_nodes = catalog.get("nodes", {})
    harvested = harvested or {}

    # What EasyAI will be able to drive. A workflow with no prompt is almost
    # certainly the wrong file.
    manifest = autodetect(graph, group, result.name, caps)
    # Only what was actually found - listing every key EasyAI knows made a
    # workflow with no prompt look as if it could drive audio and video too.
    result.manifest_slots = [k for k in BINDING_KEYS if manifest.has(k)]
    if not manifest.has("prompt"):
        result.warnings.append(
            "No prompt input was found, so the typing box will do nothing.")

    # Will it run here at all? If not, it certainly will not run for a viewer.
    if caps and caps.available:
        from app.workflows.loader import load_workflow
        probe = objectinfo.check(load_workflow(path, group, auto_write_manifest=False),
                                 caps)
        result.missing_nodes = list(probe.missing_nodes)
        result.missing_models = list(probe.missing_models)
        if probe.missing_nodes:
            result.problems.append(
                "This ComfyUI cannot run the workflow - add-ons missing: "
                + ", ".join(sorted(set(probe.missing_nodes))))

    # --- models -----------------------------------------------------------
    folders = FolderMap(client, caps) if (client and caps and caps.available) else None
    for name, class_type, field_name in models_in(graph):
        need = ModelNeed(name=name, class_type=class_type, field=field_name)
        entry = known_models.get(name)
        if entry:
            need.known = True
            need.folder = entry.get("dir", "")
            need.bytes = entry.get("bytes", 0)
            need.url = entry.get("url") or ""
            need.gated = bool(entry.get("gated"))

        need.local = find_local(models_root, name)
        if need.local:
            need.bytes = need.local.stat().st_size

        if not need.folder:
            folder = folders.folder_for(class_type, field_name, name) if folders else None
            if folder:
                need.folder = folder
            elif need.local:
                # Where it really sits, which is what the generator does.
                try:
                    relative = need.local.relative_to(models_root)
                    head = relative.parts[:-len(Path(name).parts)]
                    need.folder = "/".join(head)
                except ValueError:
                    need.folder = ""
            if not need.folder:
                need.folder = FIELD_DIR.get(field_name, "checkpoints")
                need.folder_guessed = True
                result.warnings.append(
                    f"{need.filename}: could not confirm which folder this "
                    f"belongs in, guessed models/{need.folder}. Check it before "
                    f"sharing.")

        if not need.url:
            need.url = harvested.get(need.filename.lower(), "")
        if not need.local:
            result.warnings.append(
                f"{need.filename} is not on this machine, so its size cannot be "
                f"recorded and a link cannot be checked against it.")
        result.models.append(need)

    # --- add-ons ----------------------------------------------------------
    classes = {n.get("class_type", "") for n in graph.values()}
    core = set(core_classes()) if core_classes else set()
    extra = {c for c in classes if c and c not in core}
    if extra and owner_of:
        owners = owner_of(extra)
        by_pack: dict[str, list[str]] = {}
        for class_type in sorted(extra):
            pack = owners.get(class_type)
            by_pack.setdefault(pack or class_type, []).append(class_type)
        for pack, members in sorted(by_pack.items()):
            entry = known_nodes.get(pack)
            unresolved = pack in members            # no owner found for it
            result.nodes.append(NodeNeed(
                name=pack, classes=members, known=entry is not None,
                source=(entry or {}).get("source", "")))
            if unresolved:
                result.problems.append(
                    f"No add-on claims {pack}. EasyAI Setup would have no way "
                    f"to install it, so viewers could not run this workflow.")
    return result


# --- checking a link -------------------------------------------------------
def check_link(url: str, expected_bytes: int, token: str = "") -> str:
    """Empty string when the link is good, otherwise why it is not.

    The size comparison is the point: a link that resolves but serves a
    different build downloads perfectly and then produces wrong output, which
    is far harder to notice than a broken link.
    """
    if not url.strip():
        return "No link given."
    headers = {"User-Agent": "EasyAISetup/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.head(url, headers=headers, allow_redirects=True, timeout=40)
    except requests.RequestException as e:
        return f"Could not reach it: {type(e).__name__}"
    if r.status_code in (401, 403):
        return "needs-account"
    if r.status_code >= 400:
        return f"The server answered HTTP {r.status_code}."
    size = int(r.headers.get("Content-Length") or 0)
    if expected_bytes and size and size != expected_bytes:
        return (f"That link is a different build: it serves {size:,} bytes, "
                f"the file here is {expected_bytes:,}.")
    return ""


# --- writing it in ---------------------------------------------------------
def read_catalog() -> dict:
    try:
        return json.loads(CATALOG.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"comfyui": {}, "groups": {}, "nodes": {}, "models": {}}


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def apply(result: Analysis, links: dict[str, str] | None = None,
          copy_workflow: bool = True) -> list[str]:
    """Write the workflow in and merge the catalogue. Returns what was done.

    New links also go to url_overrides.json and mirror staging is recorded in
    the report, because a later full regenerate rebuilds catalog.json from
    those side files - anything written only here would be quietly lost.
    """
    if result.problems:
        raise AuthoringError("\n\n".join(result.problems))
    links = links or {}
    done: list[str] = []

    # 1. The workflow itself, plus a thumbnail if one sits beside it.
    if copy_workflow:
        destination = PROJECT_ROOT / "workflows" / result.group / result.path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if result.path.resolve() != destination.resolve():
            shutil.copy2(result.path, destination)
            done.append(f"copied {result.path.name} into workflows/{result.group}")
        for suffix in (".png", ".jpg", ".jpeg", ".webp"):
            art = result.path.with_suffix(suffix)
            if art.is_file():
                shutil.copy2(art, destination.with_suffix(suffix))
                done.append(f"copied the thumbnail {art.name}")
                break

    # 2. Remember any link that was typed in, where a regenerate will find it.
    overrides = _read_json(OVERRIDES)
    for model in result.models:
        url = links.get(model.name) or model.url
        if url and not overrides.get(model.filename):
            overrides[model.filename] = url
    _write_json(OVERRIDES, overrides)

    # 3. Merge the catalogue, leaving existing entries exactly as they are.
    catalog = read_catalog()
    models = catalog.setdefault("models", {})
    for model in result.models:
        url = links.get(model.name) or model.url
        entry = models.setdefault(model.name, {})
        entry.setdefault("dir", model.folder)
        if model.bytes:
            entry["bytes"] = model.bytes
        if url and not entry.get("url"):
            entry["url"] = url
        if model.gated:
            entry["gated"] = True
        if not model.known:
            done.append(f"added {model.filename} -> models/{model.folder}")

    group = catalog.setdefault("groups", {}).setdefault(
        result.group, {"label": MODES[result.group].label, "workflows": [],
                       "models": [], "nodes": [], "bytes": 0})
    if result.path.name not in group.setdefault("workflows", []):
        group["workflows"].append(result.path.name)
    for model in result.models:
        if model.name not in group.setdefault("models", []):
            group["models"].append(model.name)
    for node in result.nodes:
        if node.name not in group.setdefault("nodes", []):
            group["nodes"].append(node.name)
    group["bytes"] = sum(models.get(n, {}).get("bytes", 0)
                         for n in group.get("models", []))

    _write_json(CATALOG, catalog)
    done.append("merged into setup/catalog.json")
    return done


def stage_for_mirror(result: Analysis) -> list[str]:
    """Copy anything needing an account into mirror-staging, with its hash.

    The upload itself stays manual - ownCloud share links are created by hand -
    so this prepares the files and says what to paste back afterwards.
    """
    from setup.download import file_sha256

    notes: list[str] = []
    mirrors = _read_json(MIRRORS)
    for model in result.models:
        if not model.gated or not model.local:
            continue
        if mirrors.get(model.filename):
            continue
        target = STAGING / Path(model.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.stat().st_size != model.bytes:
            shutil.copy2(model.local, target)
        notes.append(f"{model.filename}\n"
                     f"    staged  {target}\n"
                     f"    sha256  {file_sha256(target)}\n"
                     f"    upload it, then add its share link to "
                     f"setup/mirror_links.json")
    return notes
