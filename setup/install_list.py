"""setup-settings.json - the install list, beside EasyAI Setup, that anyone can edit.

EasyAI Setup's list of what to install is built into the .exe, so changing it
used to mean rebuilding the program. This file is the same list written out
where it can be edited:

* **every model** - where it comes from (``from``, and ``mirror`` if there is
  one) and where it goes (its ``kind`` folder, or a full path in ``to``);
* **every workflow** - where it comes from (``built-in``, a file path or a web
  link) and which folder it goes into;
* **the two main folders** - where models go and where workflows go.

Setup writes the file out the first time it runs, then always reads it.

The one trap is an old file beside a newer Setup. The file would quietly hide
whatever the newer Setup added - a new workflow and its models would simply
never be offered. So the file records which built-in list it was made from:

* made from this Setup's list          -> used as it is;
* made from an older list, never edited -> replaced with the new list, silently;
* made from an older list, and edited   -> kept, because the edits are someone's
  work, with a warning in the window and a button to switch to the new list.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app import VERSION_LABEL
from app.paths import data_dir
from setup.catalog import CATALOG_PATH, DEFAULT_FOLDERS, Catalog

FILE_NAME = "setup-settings.json"
BACKUP_NAME = "setup-settings.old.json"

HOW_TO_USE = [
    "EasyAI Setup installs exactly what this file lists. Edit it, then press "
    "Reload in EasyAI Setup.",
    "\"from\" is where something comes from, \"to\" is where it goes.",
    "Write paths with forward slashes - \"D:/AI Models\". A single backslash "
    "is not allowed in this file.",
    "folders.models: where models go. Empty means the models folder of the "
    "ComfyUI being installed. A full path puts them there instead, and Setup "
    "tells ComfyUI to look there too.",
    "folders.workflows: where EasyAI's workflows go - a folder inside the "
    "install folder, or a full path. EasyAI is pointed at it.",
    "models: \"kind\" is the ComfyUI folder the model belongs in (unet, "
    "loras...). \"to\" is optional - a full path for this one model. "
    "\"mirror\" is tried before \"from\" when it is set.",
    "groups: which workflows, models and add-ons each tick box installs. A "
    "workflow's \"from\" is built-in, a file path (a relative path is read "
    "from beside this file), or a web link.",
    "Delete this file to go back to the list built into this EasyAI Setup.",
]


class InstallListError(Exception):
    """The file cannot be used - bad JSON, or entries that contradict each other."""


@dataclass
class Loaded:
    catalog: Catalog
    path: Path
    #: "created", "current", "refreshed", "outdated", or "unwritable".
    status: str
    #: What the newer built-in list has that this file does not, when outdated.
    missing: list[str]


def default_path() -> Path:
    """Beside EasyAI Setup.exe - or the project folder when run from source."""
    return data_dir() / FILE_NAME


def fingerprint(builtin: Path = CATALOG_PATH) -> str:
    return hashlib.sha256(Path(builtin).read_bytes()).hexdigest()[:16]


# --- the two shapes ---------------------------------------------------------
def document(raw: dict, built_in: str) -> dict:
    """The editable file, from the built-in catalogue."""
    models = {}
    for name, entry in raw.get("models", {}).items():
        models[name] = {
            "kind": entry.get("dir", "checkpoints"),
            "from": entry.get("url") or "",
            "mirror": entry.get("mirror") or "",
            "to": entry.get("to") or "",
            "bytes": int(entry.get("bytes") or 0),
            "sha256": entry.get("sha256") or "",
            "needs_account": bool(entry.get("gated")),
        }
    groups = {}
    for key, group in raw.get("groups", {}).items():
        groups[key] = {
            "label": group.get("label", key),
            "workflows": [{"file": item.get("file", item) if isinstance(item, dict) else item,
                           "from": (item.get("from") if isinstance(item, dict) else None)
                           or "built-in",
                           "to": (item.get("to") if isinstance(item, dict) else None) or key}
                          for item in (group.get("workflow_items") or group.get("workflows") or [])],
            "models": list(group.get("models", [])),
            "add_ons": list(group.get("nodes", [])),
        }
    return {
        "_how_to_use": HOW_TO_USE,
        "made_by": f"EasyAI Setup {VERSION_LABEL}",
        "built_in_list": built_in,
        "folders": {**DEFAULT_FOLDERS, **(raw.get("folders") or {})},
        "comfyui": raw["comfyui"],
        "groups": groups,
        "models": models,
        "add_ons": raw.get("nodes", {}),
    }


def to_raw(doc: dict) -> dict:
    """The catalogue shape Setup's installer reads, from the editable file."""
    models = {}
    for name, entry in (doc.get("models") or {}).items():
        models[name] = {
            "dir": entry.get("kind") or "checkpoints",
            "url": entry.get("from") or None,
            "mirror": entry.get("mirror") or None,
            "to": entry.get("to") or "",
            "bytes": int(entry.get("bytes") or 0),
            "sha256": entry.get("sha256") or "",
            "gated": bool(entry.get("needs_account")),
        }
    groups = {}
    for key, group in (doc.get("groups") or {}).items():
        items = [{"file": str(w.get("file") or ""), "from": str(w.get("from") or "built-in"),
                  "to": str(w.get("to") or key)}
                 for w in group.get("workflows") or [] if isinstance(w, dict)]
        groups[key] = {
            "label": group.get("label", key),
            "models": list(group.get("models") or []),
            "nodes": list(group.get("add_ons") or []),
            "workflows": [i["file"] for i in items],
            "workflow_items": items,
            "bytes": sum(models.get(n, {}).get("bytes", 0) for n in group.get("models") or []),
        }
    return {"comfyui": doc.get("comfyui") or {}, "folders": doc.get("folders") or {},
            "nodes": doc.get("add_ons") or {}, "models": models, "groups": groups}


def problems_in(raw: dict) -> list[str]:
    """Everything that would make an install go wrong, in plain words."""
    found = []
    if not (raw.get("comfyui") or {}).get("portable"):
        found.append("\"comfyui\" is missing its \"portable\" download.")
    for key, group in raw.get("groups", {}).items():
        for name in group.get("models", []):
            if name not in raw.get("models", {}):
                found.append(f"Group \"{key}\" lists the model \"{name}\", but it is not "
                             f"under \"models\".")
        for name in group.get("nodes", []):
            if name not in raw.get("nodes", {}):
                found.append(f"Group \"{key}\" lists the add-on \"{name}\", but it is not "
                             f"under \"add_ons\".")
        for item in group.get("workflow_items", []):
            if not item["file"]:
                found.append(f"Group \"{key}\" has a workflow with no \"file\".")
    return found


def _content_hash(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "_written_as"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()[:16]


def write(path: Path, doc: dict) -> None:
    doc = dict(doc)
    doc["_written_as"] = _content_hash(doc)
    Path(path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")


def _read(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        hint = ""
        if "escape" in e.msg.lower():
            hint = ("\n\nA path probably has a single backslash in it. Write it with "
                    "forward slashes, like \"D:/AI Models\".")
        raise InstallListError(f"{path.name} is not valid JSON - line {e.lineno}, "
                               f"column {e.colno}: {e.msg}.{hint}") from e
    except OSError as e:
        raise InstallListError(f"Could not read {path}: {e}") from e
    if not isinstance(doc, dict):
        raise InstallListError(f"{path.name} should hold one {{ ... }} object.")
    return doc


def _missing(doc: dict, builtin_raw: dict) -> list[str]:
    """Workflows the built-in list has that this file does not."""
    have = {w.get("file") for g in (doc.get("groups") or {}).values()
            for w in g.get("workflows") or [] if isinstance(w, dict)}
    return sorted({f for g in builtin_raw.get("groups", {}).values()
                   for f in g.get("workflows", []) if f not in have})


# --- the whole job --------------------------------------------------------------
def load(path: Path | None = None, builtin: Path = CATALOG_PATH) -> Loaded:
    """The install list to use, creating or refreshing the file as needed."""
    path = Path(path) if path else default_path()
    builtin_raw = json.loads(Path(builtin).read_text(encoding="utf-8"))
    current = fingerprint(builtin)
    status, missing = "current", []

    if not path.exists():
        doc = document(builtin_raw, current)
        try:
            write(path, doc)
            status = "created"
        except OSError:
            status = "unwritable"
    else:
        doc = _read(path)
        if doc.get("built_in_list") != current:
            if doc.get("_written_as") == _content_hash(doc):
                # Never edited: nothing to lose by bringing it up to date.
                doc = document(builtin_raw, current)
                try:
                    write(path, doc)
                    status = "refreshed"
                except OSError:
                    status = "unwritable"
            else:
                status = "outdated"
                missing = _missing(doc, builtin_raw)

    raw = to_raw(doc)
    found = problems_in(raw)
    if found:
        raise InstallListError(f"{path.name} has {len(found)} problem(s):\n\n"
                               + "\n".join("•  " + p for p in found[:10]))
    return Loaded(catalog=Catalog(path=builtin, raw=raw, list_path=path),
                  path=path, status=status, missing=missing)


def replace_with_builtin(path: Path | None = None, builtin: Path = CATALOG_PATH) -> Path | None:
    """Start the file again from this Setup's own list, keeping the old one."""
    path = Path(path) if path else default_path()
    backup = None
    if path.exists():
        backup = path.with_name(BACKUP_NAME)
        backup.unlink(missing_ok=True)
        path.rename(backup)
    builtin_raw = json.loads(Path(builtin).read_text(encoding="utf-8"))
    write(path, document(builtin_raw, fingerprint(builtin)))
    return backup


def builtin_catalog() -> Catalog:
    """The list inside this Setup, for when the file cannot be used."""
    return Catalog()
