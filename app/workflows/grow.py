"""Giving a reference-picture group the slots its node can actually take.

Qwen Image 2.1's encoder accepts up to sixteen reference pictures through one
auto-growing input group - ``images.image_1``, ``images.image_2`` and so on -
but a workflow exported from ComfyUI wires only the one the author happened to
use. EasyAI would then offer a single picture slot, however many the node can
read.

This adds the rest: one more picture loader per extra member, copied from the
one already there and wired into the next name. It happens in memory when the
workflow loads - the file itself is never rewritten, so it can still be shared
or re-added through EasyAI Studio exactly as exported.

Only a group that its workflow wires a single picture into is grown. That is
how ComfyUI exports these groups by default, so it marks a workflow whose
author never chose a number. MiniMax H3's reference workflow wires three into a
group that takes nine; three is a choice, and it is left alone.

What was added is recorded in the manifest, so the same slots come back when
the workflow loads with ComfyUI closed - without it, a manifest written with
the engine running would point at nodes that did not exist offline.
"""
from __future__ import annotations

import copy
import re

#: Loader classes that read a picture, and the input holding its filename.
IMAGE_LOADERS = {"loadimage": "image"}

#: The most picture slots EasyAI has - IMAGE_SLOTS runs image_in to image_in_10.
MAX_PICTURES = 10


def _links(graph: dict) -> list[tuple[str, str, str]]:
    """(consumer id, input name, source id) for every wire in the graph."""
    found = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        for name, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and value and isinstance(value[0], (str, int)):
                found.append((str(node_id), name, str(value[0])))
    return found


def _is_picture_loader(node) -> bool:
    return (isinstance(node, dict)
            and str(node.get("class_type", "")).lower() in IMAGE_LOADERS)


def _picture_loader_count(graph: dict) -> int:
    return sum(1 for node in graph.values() if _is_picture_loader(node))


def new_node_id(consumer: str, name: str) -> str:
    """A stable id for an added loader, so every load produces the same graph.

    Colons are avoided: ComfyUI reads ``459:474`` as "node 474 inside
    subgraph 459", and an added node is not inside anything.
    """
    return "easyai_" + re.sub(r"[^A-Za-z0-9]+", "_", f"{consumer}_{name}")


def plan(graph: dict, caps, limit: int = MAX_PICTURES) -> list[dict]:
    """Which groups to grow, and into which names. Needs the running engine.

    Returns one entry per group::

        {"node": "459:474", "group": "images", "loader": "470",
         "names": ["image_1", ..., "image_10"]}

    ``names`` is the whole group as it should end up, first member included,
    so the manifest alone is enough to rebuild it offline.
    """
    if caps is None or not getattr(caps, "available", False):
        return []

    # Which group members each consumer already has wired to a picture loader.
    members: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for consumer, input_name, source in _links(graph):
        group, dot, member = input_name.partition(".")
        if not dot or not _is_picture_loader(graph.get(source)):
            continue
        members.setdefault((consumer, group), []).append((member, source))

    specs = []
    room = limit - _picture_loader_count(graph)
    for (consumer, group), wired in sorted(members.items()):
        if len(wired) != 1 or room <= 0:
            continue
        class_type = str((graph.get(consumer) or {}).get("class_type", ""))
        grow = caps.autogrow(class_type, group)
        if not grow:
            continue
        member, loader = wired[0]
        names = grow["names"]
        if member not in names:
            continue
        # Continue from the member already wired, in the node's own order.
        start = names.index(member)
        wanted = names[start:start + 1 + room]
        if len(wanted) < 2:
            continue
        specs.append({"node": consumer, "group": group, "loader": loader,
                      "names": wanted})
        room -= len(wanted) - 1
    return specs


def apply(graph: dict, specs: list[dict]) -> list[str]:
    """Add the loaders the plan calls for. Safe to repeat.

    Returns the ids of any loaders added this time.
    """
    added = []
    for spec in specs or []:
        consumer = graph.get(spec.get("node"))
        template = graph.get(spec.get("loader"))
        if not isinstance(consumer, dict) or not _is_picture_loader(template):
            continue
        inputs = consumer.setdefault("inputs", {})
        group = spec.get("group", "")
        for name in (spec.get("names") or [])[1:]:
            key = f"{group}.{name}"
            if key in inputs:
                continue
            node_id = new_node_id(spec["node"], name)
            if node_id not in graph:
                clone = copy.deepcopy(template)
                clone.setdefault("_meta", {})["title"] = (
                    (template.get("_meta") or {}).get("title") or "Load Image")
                graph[node_id] = clone
                added.append(node_id)
            inputs[key] = [node_id, 0]
    return added


def member_loaders(graph: dict, spec: dict) -> list[str]:
    """The loader feeding each member of a grown group, in the node's order."""
    inputs = (graph.get(spec.get("node")) or {}).get("inputs") or {}
    group = spec.get("group", "")
    loaders = []
    for name in spec.get("names") or []:
        value = inputs.get(f"{group}.{name}")
        if isinstance(value, list) and value:
            loaders.append(str(value[0]))
    return loaders


def grown_names(specs: list[dict], node: str, group: str) -> list[str] | None:
    """The full name list of a grown group, or None if it was not grown."""
    for spec in specs or []:
        if spec.get("node") == node and spec.get("group") == group:
            return list(spec.get("names") or [])
    return None
